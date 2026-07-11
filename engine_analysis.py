"""
engine_analysis.py
Interroge Stockfish pour obtenir le meilleur coup et l'évaluation.
"""
import os
import chess
import chess.engine

# Profondeur par défaut. Stockfish moderne (NNUE) atteint facilement
# 20-25+ en 1-2 secondes dès qu'il a plusieurs threads + un peu de mémoire
# (voir configure() ci-dessous) -- une profondeur plus haute = coups
# tactiques complexes mieux vus, donc force réelle plus proche du plein
# potentiel de Stockfish (largement au-dessus de 3000 Elo).
DEFAULT_DEPTH = 20


class ChessCoachEngine:
    def __init__(self, stockfish_path, threads=None, hash_mb=256):
        self.engine = chess.engine.SimpleEngine.popen_uci(stockfish_path)

        # Sans ça, Stockfish tourne sur 1 seul thread et une table de
        # transposition minuscule par défaut -> beaucoup plus lent pour
        # atteindre une bonne profondeur, donc plus faible "en pratique"
        # dans le temps qu'on lui laisse entre 2 analyses.
        if threads is None:
            cpu_count = os.cpu_count() or 4
            threads = max(1, cpu_count - 1)  # laisse un coeur libre pour le reste du programme
        try:
            self.engine.configure({"Threads": threads, "Hash": hash_mb})
        except chess.engine.EngineError as e:
            print(f"⚠ Impossible de configurer Threads/Hash sur ce Stockfish : {e}")

    def analyze_fen(self, fen, depth=DEFAULT_DEPTH, multipv=1):
        """
        Retourne une liste de dicts (une par ligne demandée) avec :
        - move (SAN)
        - move_uci
        - score (str, ex: "+0.35" ou "Mat en 3")
        - pv (liste de coups en SAN, la ligne principale)
        """
        board = chess.Board(fen)
        if board.is_game_over():
            return {"game_over": True, "result": board.result()}

        info = self.engine.analyse(
            board, chess.engine.Limit(depth=depth), multipv=multipv
        )
        if isinstance(info, dict):
            info = [info]

        lines = []
        for entry in info:
            pv = entry.get("pv", [])
            score_str = self._format_score(entry["score"], board.turn)

            pv_san = []
            tmp_board = board.copy()
            for mv in pv[:6]:  # 6 coups de profondeur affichée max
                pv_san.append(tmp_board.san(mv))
                tmp_board.push(mv)

            lines.append({
                "move_uci": pv[0].uci() if pv else None,
                "move_san": board.san(pv[0]) if pv else None,
                "score": score_str,
                "pv_san": pv_san,
            })

        return {"game_over": False, "lines": lines, "board": board}

    @staticmethod
    def _format_score(score, turn):
        pov_score = score.pov(turn)
        if pov_score.is_mate():
            return f"Mat en {abs(pov_score.mate())}"
        cp = pov_score.score()
        sign = "+" if cp >= 0 else ""
        return f"{sign}{cp / 100:.2f}"

    def close(self):
        self.engine.quit()
