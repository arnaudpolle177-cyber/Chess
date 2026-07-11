"""
main.py
Point d'entrée du Coach d'échecs.

Utilisation :
    python main.py --calibrate     # (re)calibrer la zone de l'échiquier
    python main.py --learn         # apprendre les pièces (plateau en position de départ)
    python main.py                 # lancer le coach (overlay + analyse en direct)

Options utiles :
    --stockfish PATH   chemin vers l'exécutable stockfish (sinon variable STOCKFISH_PATH ou "stockfish" dans le PATH)
    --interval N       secondes entre 2 analyses automatiques (défaut: 3)
    --explain-mode {local,api}   mode d'explication (défaut: local)
"""
import argparse
import os
import sys
import threading
import time


def _make_process_dpi_aware():
    """
    CORRECTIF IMPORTANT : sous Windows, si l'affichage utilise une mise à
    l'échelle (125%, 150%... très courant sur les laptops), Tkinter et mss
    ne comptent pas les pixels de la même façon par défaut. Résultat : le
    rectangle dessiné pendant la calibration (via Tkinter) ne correspond
    plus exactement à la zone réellement capturée ensuite (via mss), avec
    un décalage qui s'accumule en s'éloignant du point d'ancrage -> le haut
    du plateau peut sembler à peu près bon tandis que le bas capture autre
    chose (fond de page, autre élément de l'interface).

    En rendant le PROCESSUS "DPI-aware" avant de créer la moindre fenêtre,
    Windows arrête de mentir à Tkinter sur la taille de l'écran, et les
    deux outils (Tkinter et mss) travaillent enfin dans le même référentiel
    de pixels physiques. Doit être appelé tout en haut du programme, avant
    tout import/usage de tkinter.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        # PROCESS_PER_MONITOR_DPI_AWARE (2) : le plus précis, gère aussi le
        # cas de plusieurs écrans avec des échelles différentes.
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            # Repli pour les versions de Windows plus anciennes
            # (Windows 7/8 sans shcore.dll).
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass  # tant pis, on continue sans (mieux qu'un crash)


_make_process_dpi_aware()

import chess

from capture_utils import run_calibration, load_board_config
from template_builder import build_templates_from_starting_position, load_templates
from board_reader import read_board_with_retries, save_debug_capture
from engine_analysis import ChessCoachEngine
from explain import explain_move_local, explain_move_via_api
from overlay_ui import CoachOverlay


class CoachApp:
    def __init__(self, stockfish_path, interval, explain_mode):
        self.stockfish_path = stockfish_path
        self.interval = interval
        self.explain_mode = explain_mode
        self.active_color = "w"  # camp pour lequel on demande le meilleur coup
        self.engine = None
        self.running = True

        board_region = load_board_config()
        self.overlay = CoachOverlay(
            on_refresh_click=self.trigger_refresh,
            on_toggle_side_click=self.toggle_side,
            board_region=board_region,
        )
        self._refresh_requested = threading.Event()
        self._refresh_requested.set()  # premier refresh immédiat

    def toggle_side(self):
        self.active_color = "b" if self.active_color == "w" else "w"
        self.trigger_refresh()

    def trigger_refresh(self):
        self._refresh_requested.set()

    def analysis_loop(self):
        self.engine = ChessCoachEngine(self.stockfish_path)
        try:
            while self.running:
                triggered = self._refresh_requested.wait(timeout=self.interval)
                self._refresh_requested.clear()
                self._run_one_analysis()
        finally:
            self.engine.close()

    def _run_one_analysis(self):
        try:
            result = read_board_with_retries(active_color=self.active_color, max_attempts=3)
            grid = result["grid"]
            min_score = result["min_score"]
            debug_info = result["debug_info"]
            fen = result["fen"]
            board = result["board"]

            if not result["valid"]:
                debug_dir = save_debug_capture(
                    debug_info, fen,
                    reason=f"Position invalide (après {result['attempts']} tentatives)"
                )
                self.overlay.show_error(
                    f"Position illisible ou invalide, même après {result['attempts']} "
                    "tentatives (vérifie la calibration / que le plateau est bien visible).\n"
                    f"Détails sauvegardés dans : {debug_dir}"
                )
                return

            result = self.engine.analyze_fen(fen, multipv=3)
            if result.get("game_over"):
                self.overlay.show_error(f"Partie terminée : {result['result']}")
                return

            best = result["lines"][0]

            if self.explain_mode == "api":
                explanation = explain_move_via_api(
                    fen, best["move_san"], best["pv_san"], best["score"]
                )
            else:
                move_obj = chess.Move.from_uci(best["move_uci"])
                explanation = explain_move_local(board, move_obj, best["pv_san"])

            self.overlay.update_content(
                lines=result["lines"],
                explanation=explanation,
            )

            if min_score < 0.45:
                self.overlay.append_warning(
                    "\n\n⚠ Reconnaissance incertaine sur au moins une case, "
                    "vérifie le résultat."
                )
        except Exception as e:
            debug_info_local = locals().get("debug_info")
            fen_local = locals().get("fen", "?")
            if debug_info_local is not None:
                try:
                    save_debug_capture(debug_info_local, fen_local, reason=f"Exception: {e}")
                except Exception:
                    pass  # ne pas planter sur le rapport de diagnostic lui-meme
            self.overlay.show_error(str(e))

    def run(self):
        thread = threading.Thread(target=self.analysis_loop, daemon=True)
        thread.start()
        self.overlay.run()
        self.running = False


class BrowserBridgeApp:
    """
    Mode 'navigateur' : le plateau est lu directement dans le DOM de ta
    page (via chess_coach_bridge.js), donc plus de capture d'écran ni de
    reconnaissance d'image. Ce mode se contente de démarrer le serveur
    local que le JS contacte, et affiche le texte/l'explication dans la
    même fenêtre Tkinter que le mode classique (les flèches, elles,
    s'affichent directement sur ta page web, pas ici).
    """

    def __init__(self, stockfish_path, explain_mode, port):
        self.overlay = CoachOverlay(
            on_refresh_click=lambda: None,  # rien à rafraîchir manuellement : c'est le JS qui pousse les mises à jour
            on_toggle_side_click=lambda: None,
            board_region=None,  # pas d'overlay bureau : les flèches sont dessinées dans la page
        )
        self.server = None
        self.state = None
        self.stockfish_path = stockfish_path
        self.explain_mode = explain_mode
        self.port = port

    def run(self):
        from web_bridge import start_bridge_server
        self.server, self.state = start_bridge_server(
            self.stockfish_path,
            explain_mode=self.explain_mode,
            on_update=self._on_update,
            port=self.port,
        )
        self.overlay.explanation_text.insert(
            "1.0",
            f"En attente de ton site...\n\n"
            f"Vérifie que chess_coach_bridge.js est bien chargé sur ta page "
            f"de jeu (port {self.port})."
        )
        try:
            self.overlay.run()
        finally:
            if self.state:
                self.state.close()

    def _on_update(self, lines, explanation):
        if lines is None:
            self.overlay.show_error(explanation)
        else:
            self.overlay.update_content(lines=lines, explanation=explanation)


def resolve_stockfish_path(cli_path):
    if cli_path:
        return cli_path
    env_path = os.environ.get("STOCKFISH_PATH")
    if env_path:
        return env_path
    return "stockfish"  # suppose que c'est dans le PATH


def _pause_avant_fermeture():
    """Évite que la fenêtre se ferme instantanément si lancée en double-clic."""
    input("\nAppuie sur Entrée pour quitter...")


def interactive_menu():
    """
    Menu affiché quand le programme est lancé sans argument (typiquement en
    double-cliquant sur le .exe). Reste ouvert et permet d'enchaîner
    calibration -> apprentissage -> lancement du coach sans jamais avoir à
    ouvrir un terminal séparé ni taper de commande.
    """
    while True:
        print("\n" + "=" * 50)
        print("  ♟  Coach d'échecs — Menu")
        print("=" * 50)
        config_ok = load_board_config() is not None
        templates_ok = bool(load_templates())
        print(f"  1. Calibrer l'échiquier          {'✅ déjà fait' if config_ok else '(à faire)'}")
        print(f"  2. Apprendre les pièces          {'✅ déjà fait' if templates_ok else '(à faire)'}")
        print("  3. Lancer le coach (capture d'écran)")
        print("  4. Lancer le coach (mode navigateur — recommandé, sans capture d'écran)")
        print("  5. Quitter")
        choice = input("\nTon choix (1-5) : ").strip()

        if choice == "1":
            try:
                config = run_calibration()
                print(f"✅ Calibration sauvegardée : {config}")
            except Exception as e:
                print(f"⚠ Erreur pendant la calibration : {e}")

        elif choice == "2":
            if load_board_config() is None:
                print("⚠ Fais d'abord l'étape 1 (calibration).")
                continue
            print("Affiche le plateau en position de DÉPART sur ton site, puis appuie sur Entrée.")
            input()
            try:
                paths = build_templates_from_starting_position()
                print(f"✅ {len(paths)} templates de pièces sauvegardés.")
            except Exception as e:
                print(f"⚠ Erreur pendant l'apprentissage : {e}")

        elif choice == "3":
            if load_board_config() is None:
                print("⚠ Fais d'abord l'étape 1 (calibration).")
                continue
            if not load_templates():
                print("⚠ Fais d'abord l'étape 2 (apprentissage des pièces).")
                continue
            sf_input = input(
                "Chemin vers stockfish.exe (laisse vide pour utiliser "
                "STOCKFISH_PATH ou le PATH système) : "
            ).strip()
            stockfish_path = resolve_stockfish_path(sf_input or None)
            print("Lancement du coach... (ferme la fenêtre 'coach' pour revenir ici)")
            try:
                app = CoachApp(stockfish_path, interval=3.0, explain_mode="local")
                app.run()
            except Exception as e:
                print(f"⚠ Erreur pendant le lancement du coach : {e}")

        elif choice == "4":
            sf_input = input(
                "Chemin vers stockfish.exe (laisse vide pour utiliser "
                "STOCKFISH_PATH ou le PATH système) : "
            ).strip()
            stockfish_path = resolve_stockfish_path(sf_input or None)
            print("Démarrage du mode navigateur...")
            print("N'oublie pas d'ajouter chess_coach_bridge.js sur ta page de jeu si ce n'est pas déjà fait.")
            try:
                app = BrowserBridgeApp(stockfish_path, explain_mode="local", port=8765)
                app.run()
            except Exception as e:
                print(f"⚠ Erreur pendant le lancement du mode navigateur : {e}")

        elif choice == "5":
            print("À bientôt !")
            break

        else:
            print("Choix invalide, entre un chiffre entre 1 et 5.")


def main():
    parser = argparse.ArgumentParser(description="Coach d'échecs en temps réel")
    parser.add_argument("--calibrate", action="store_true", help="Recalibrer la zone de l'échiquier")
    parser.add_argument("--learn", action="store_true", help="Apprendre les pièces (position de départ)")
    parser.add_argument("--stockfish", default=None, help="Chemin vers l'exécutable Stockfish")
    parser.add_argument("--interval", type=float, default=3.0, help="Secondes entre 2 analyses auto")
    parser.add_argument("--explain-mode", choices=["local", "api"], default="local")
    parser.add_argument("--web-bridge", action="store_true",
                         help="Mode navigateur : lit le plateau via chess_coach_bridge.js au lieu de la capture d'écran")
    parser.add_argument("--bridge-port", type=int, default=8765)
    args = parser.parse_args()

    if args.web_bridge:
        stockfish_path = resolve_stockfish_path(args.stockfish)
        app = BrowserBridgeApp(stockfish_path, args.explain_mode, args.bridge_port)
        app.run()
        return


    # Aucun argument passé (typiquement : double-clic sur le .exe) -> menu interactif.
    if len(sys.argv) == 1:
        interactive_menu()
        return

    if args.calibrate:
        config = run_calibration()
        print(f"✅ Calibration sauvegardée : {config}")
        return

    if args.learn:
        if load_board_config() is None:
            print("⚠ Aucune calibration trouvée, lance d'abord: python main.py --calibrate")
            return
        print("Assure-toi que le plateau affiche la position de DÉPART, puis appuie sur Entrée.")
        print("(3 captures successives seront prises avec une petite pause entre chaque, "
              "pour un apprentissage plus fiable — ne touche pas au plateau entre-temps.)")
        input()
        paths = build_templates_from_starting_position()
        print(f"✅ {len(paths)} templates de pièces sauvegardés.")
        return

    if load_board_config() is None:
        print("⚠ Aucune calibration trouvée. Lance d'abord : python main.py --calibrate")
        return
    if not load_templates():
        print("⚠ Aucun template de pièce. Lance d'abord : python main.py --learn")
        return

    stockfish_path = resolve_stockfish_path(args.stockfish)
    app = CoachApp(stockfish_path, args.interval, args.explain_mode)
    app.run()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n⚠ Erreur : {e}")
        _pause_avant_fermeture()
