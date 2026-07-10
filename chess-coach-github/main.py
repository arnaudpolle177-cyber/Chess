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
import threading
import time

import chess

from capture_utils import run_calibration, load_board_config
from template_builder import build_templates_from_starting_position, load_templates
from board_reader import read_board_to_grid, grid_to_fen
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

        self.overlay = CoachOverlay(
            on_refresh_click=self.trigger_refresh,
            on_toggle_side_click=self.toggle_side,
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
            grid, min_score = read_board_to_grid()
            fen = grid_to_fen(grid, active_color=self.active_color)
            board = chess.Board(fen)

            if not board.is_valid():
                self.overlay.show_error(
                    "Position illisible ou invalide (vérifie la calibration / "
                    "que le plateau est bien visible)."
                )
                return

            result = self.engine.analyze_fen(fen)
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
                move_san=best["move_san"],
                score_str=best["score"],
                pv_san=best["pv_san"],
                explanation=explanation,
            )

            if min_score < 0.45:
                self.overlay.explanation_text.insert(
                    "end",
                    "\n\n⚠ Reconnaissance incertaine sur au moins une case, "
                    "vérifie le résultat."
                )
        except Exception as e:
            self.overlay.show_error(str(e))

    def run(self):
        thread = threading.Thread(target=self.analysis_loop, daemon=True)
        thread.start()
        self.overlay.run()
        self.running = False


def resolve_stockfish_path(cli_path):
    if cli_path:
        return cli_path
    env_path = os.environ.get("STOCKFISH_PATH")
    if env_path:
        return env_path
    return "stockfish"  # suppose que c'est dans le PATH


def main():
    parser = argparse.ArgumentParser(description="Coach d'échecs en temps réel")
    parser.add_argument("--calibrate", action="store_true", help="Recalibrer la zone de l'échiquier")
    parser.add_argument("--learn", action="store_true", help="Apprendre les pièces (position de départ)")
    parser.add_argument("--stockfish", default=None, help="Chemin vers l'exécutable Stockfish")
    parser.add_argument("--interval", type=float, default=3.0, help="Secondes entre 2 analyses auto")
    parser.add_argument("--explain-mode", choices=["local", "api"], default="local")
    args = parser.parse_args()

    if args.calibrate:
        config = run_calibration()
        print(f"✅ Calibration sauvegardée : {config}")
        return

    if args.learn:
        if load_board_config() is None:
            print("⚠ Aucune calibration trouvée, lance d'abord: python main.py --calibrate")
            return
        print("Assure-toi que le plateau affiche la position de DÉPART, puis appuie sur Entrée.")
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
    main()
