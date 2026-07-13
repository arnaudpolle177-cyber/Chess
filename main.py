"""
main.py
Point d'entrée du Coach d'échecs.

Utilisation :
    python main.py --web-bridge    # lance le coach (mode navigateur, seul mode disponible)
    python main.py                 # menu interactif (typiquement via double-clic sur le .exe)

Options utiles :
    --stockfish PATH   chemin vers l'exécutable moteur (Stockfish, Berserk, ...) --
                        sinon variable STOCKFISH_PATH ou "stockfish" dans le PATH
    --explain-mode {local,api}   mode d'explication (défaut: local)

Le mode "capture d'écran" (calibration + reconnaissance d'image) a été
retiré du projet -- seul le mode navigateur (lecture directe du DOM via
chess_coach_bridge.user.js) est encore utilisé.
"""
import argparse
import os
import sys
import threading


def _make_process_dpi_aware():
    """
    CORRECTIF IMPORTANT : sous Windows, si l'affichage utilise une mise à
    l'échelle (125%, 150%... très courant sur les laptops), les
    bibliothèques d'interface graphique et Windows lui-même peuvent ne pas
    compter les pixels de la même façon selon que le processus est
    "DPI-aware" ou non -- ça peut se traduire par une fenêtre floue ou mal
    dimensionnée. En rendant le PROCESSUS DPI-aware avant de créer la
    moindre fenêtre, on évite ce genre de décalage. Doit être appelé tout
    en haut du programme, avant tout import/usage d'une bibliothèque
    d'interface graphique.
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

from engine_analysis import DEFAULT_DEPTH
from webview_ui import CoachWebview


class BrowserBridgeApp:
    """
    Mode 'navigateur' (seul mode du projet) : le plateau est lu directement
    dans le DOM de ta page (via le script Tampermonkey
    chess_coach_bridge.user.js), donc pas de capture d'écran ni de
    reconnaissance d'image. Ce mode démarre le serveur local que le JS
    contacte, et affiche les résultats dans une fenêtre pywebview (voir
    webview_ui.py) -- les flèches, elles, s'affichent directement sur ta
    page web, pas dans cette fenêtre.
    """

    def __init__(self, stockfish_path, explain_mode, port, threads=None, hash_mb=1024, depth=None):
        self.overlay = CoachWebview(
            on_refresh_click=self.trigger_refresh,
            on_toggle_side_click=self.toggle_side,
            on_elo_change=self.change_elo_tier,
        )
        self.server = None
        self.state = None
        self.stockfish_path = stockfish_path
        self.explain_mode = explain_mode
        self.port = port
        self.threads = threads
        self.hash_mb = hash_mb
        self.depth = depth

    def trigger_refresh(self):
        if self.state:
            threading.Thread(target=self.state.refresh_last_profiles, daemon=True).start()

    def change_elo_tier(self, tier_id):
        if self.state:
            self.state.set_elo_tier(tier_id)
            # Recalcule tout de suite avec le nouveau niveau plutôt que
            # d'attendre le prochain coup joué -- sans ça, bouger le slider
            # ne semblait rien faire tant qu'aucun coup n'était joué. DANS
            # UN THREAD séparé : ce callback est appelé directement depuis
            # le pont JS pendant qu'on fait glisser le slider -- lancer
            # l'analyse ici bloquerait/gèlerait toute la fenêtre pendant le
            # calcul.
            threading.Thread(target=self.state.refresh_last_profiles, daemon=True).start()

    def toggle_side(self):
        if not self.state:
            return
        new_side = "b" if self.state.my_side == "w" else "w"
        self.state.set_my_side(new_side)
        self.overlay.set_camp(new_side)
        self.overlay.show_status("Camp changé. En attente du prochain coup...")
        # Ré-évalue la dernière position connue avec le nouveau camp actif
        # (filtre normal) : affiche les flèches si c'est effectivement au
        # tour de ce camp, sinon le message "au tour de l'adversaire" --
        # plutôt que d'attendre le prochain coup joué.
        if self.state.last_fen is not None:
            threading.Thread(target=self.state.refresh_last_profiles, daemon=True).start()

    def run(self):
        from web_bridge import start_bridge_server
        self.server, self.state = start_bridge_server(
            self.stockfish_path,
            explain_mode=self.explain_mode,
            on_update=self._on_update,
            on_depth_update=self._on_depth_update,
            on_profile_update=self._on_profile_update,
            port=self.port,
            threads=self.threads,
            hash_mb=self.hash_mb,
            depth=self.depth,
        )
        self.overlay.show_status(
            f"En attente de ton site... vérifie que chess_coach_bridge.user.js "
            f"est bien activé dans Tampermonkey sur ta page de jeu (port {self.port})."
        )
        try:
            self.overlay.run()
        finally:
            if self.state:
                self.state.close()

    def _on_update(self, lines, explanation):
        # N'arrive plus qu'avec lines=None (messages ponctuels : au tour de
        # l'adversaire, partie terminée, erreur) -- les résultats d'analyse
        # progressive passent maintenant par _on_depth_update /
        # _on_profile_update ci-dessous.
        if lines is None:
            self.overlay.show_status(explanation)

    def _on_depth_update(self, depth, entry):
        # Ancien mode (profondeur brute), conservé pour compatibilité si
        # --depth est passé en CLI. Affiché comme un profil générique dans
        # la nouvelle fenêtre (pas vraiment son usage prévu, mais reste
        # fonctionnel pour du diagnostic).
        self.overlay.update_profile(f"depth{depth}", entry)

    def _on_profile_update(self, profile_id, entry):
        # Mode par défaut : une entrée par profil "humain" (voir
        # human_profile.py), indépendante des 2 autres.
        self.overlay.update_profile(profile_id, entry)


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
    double-cliquant sur le .exe).
    """
    while True:
        print("\n" + "=" * 50)
        print("  ♟  Coach d'échecs — Menu")
        print("=" * 50)
        print("  1. Lancer le coach")
        print("  2. Quitter")
        choice = input("\nTon choix (1-2) : ").strip()

        if choice == "1":
            sf_input = input(
                "Chemin vers le moteur (Stockfish, Berserk, ...) "
                "(laisse vide pour utiliser STOCKFISH_PATH ou le PATH système) : "
            ).strip()
            stockfish_path = resolve_stockfish_path(sf_input or None)
            print("Démarrage du coach...")
            print("N'oublie pas d'activer chess_coach_bridge.user.js dans Tampermonkey sur ta page de jeu si ce n'est pas déjà fait.")
            try:
                app = BrowserBridgeApp(
                    stockfish_path, explain_mode="local", port=8765,
                    threads=None, hash_mb=1024, depth=None,  # None = mode progressif 10/15/20
                )
                app.run()
            except Exception as e:
                print(f"⚠ Erreur pendant le lancement : {e}")

        elif choice == "2":
            print("À bientôt !")
            break

        else:
            print("Choix invalide, entre 1 ou 2.")


def main():
    parser = argparse.ArgumentParser(description="Coach d'échecs en temps réel")
    parser.add_argument("--stockfish", default=None, help="Chemin vers l'exécutable du moteur (Stockfish, Berserk, ...)")
    parser.add_argument("--explain-mode", choices=["local", "api"], default="local")
    parser.add_argument("--web-bridge", action="store_true",
                         help="Lance le coach (mode navigateur, seul mode disponible)")
    parser.add_argument("--bridge-port", type=int, default=8765)
    parser.add_argument("--depth", type=int, default=None,
                         help=f"Profondeur de recherche fixe (défaut: mode progressif par niveau Elo). "
                              f"Ex: {DEFAULT_DEPTH} pour une profondeur unique classique.")
    parser.add_argument("--threads", type=int, default=None,
                         help="Threads donnés au moteur (défaut: nb coeurs CPU - 1)")
    parser.add_argument("--hash", type=int, default=1024, dest="hash_mb",
                         help="Mémoire (Mo) pour la table de transposition du moteur (défaut: 1024)")
    args = parser.parse_args()

    if args.web_bridge:
        stockfish_path = resolve_stockfish_path(args.stockfish)
        app = BrowserBridgeApp(
            stockfish_path, args.explain_mode, args.bridge_port,
            threads=args.threads, hash_mb=args.hash_mb, depth=args.depth,
        )
        app.run()
        return

    # Aucun argument passé (typiquement : double-clic sur le .exe) -> menu interactif.
    if len(sys.argv) == 1:
        interactive_menu()
        return

    # Des arguments ont été passés mais pas --web-bridge : rien d'autre à
    # faire (le mode capture d'écran a été retiré), on rappelle juste
    # l'option disponible.
    print("Aucun mode reconnu. Utilise --web-bridge pour lancer le coach, ou lance sans argument pour le menu interactif.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n⚠ Erreur : {e}")
        _pause_avant_fermeture()
