"""
engine_analysis.py
Interroge Stockfish pour obtenir le meilleur coup et l'évaluation.
"""
import os
import chess
import chess.engine

# Profondeur par défaut pour le mode capture d'écran classique (une seule
# analyse par position, pas de flèches progressives). Stockfish moderne
# (NNUE) atteint facilement 20-25+ en 1-2 secondes dès qu'il a plusieurs
# threads + un peu de mémoire (voir configure() ci-dessous).
DEFAULT_DEPTH = 20

# Paliers de profondeur par défaut pour le mode "3 flèches progressives"
# (vert/bleu/rouge), utilisé en mode navigateur (web_bridge.py). Un seul
# passage Stockfish (approfondissement itératif natif) suffit à produire
# les trois -- pas besoin de relancer l'analyse 3 fois, ce qui serait 3x
# plus lent pour rien.
PROGRESSIVE_DEPTHS = (10, 15, 20)


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

    def analyze_fen_progressive(self, fen, depths=PROGRESSIVE_DEPTHS, on_analysis_started=None):
        """
        Générateur : analyse la position en UN SEUL passage Stockfish
        (approfondissement itératif natif -- Stockfish calcule déjà depth 1,
        2, 3... jusqu'à la profondeur max en interne), et yield un résultat
        dès que chaque palier demandé (ex: 10, 15, 20) est atteint.

        on_analysis_started(analysis_obj) : callback optionnel appelé juste
        après le démarrage de la recherche, avec l'objet
        chess.engine.AnalysisResult. Permet à l'appelant de stocker une
        référence pour pouvoir interrompre cette recherche depuis un autre
        thread (analysis_obj.stop()) si elle devient obsolète (ex: une
        position plus récente vient d'arriver côté web_bridge.py) --
        évite d'accumuler des recherches Stockfish concurrentes/en attente.

        Chaque élément produit : {"depth", "move_uci", "move_san", "score",
        "pv_san"}. Si la partie est déjà terminée, yield un seul
        {"game_over": True, "result": ...} et s'arrête.
        """
        board = chess.Board(fen)
        if board.is_game_over():
            yield {"game_over": True, "result": board.result()}
            return

        targets = sorted(set(depths))
        max_depth = targets[-1]
        next_idx = 0

        # engine.analysis() (et non analyse()) : mode streaming, donne accès
        # à l'info UCI à CHAQUE profondeur traversée pendant la recherche,
        # sans jamais relancer le calcul depuis zéro.
        with self.engine.analysis(board, chess.engine.Limit(depth=max_depth)) as analysis:
            if on_analysis_started:
                on_analysis_started(analysis)
            for info in analysis:
                depth = info.get("depth")
                pv = info.get("pv")
                if depth is None or not pv:
                    continue
                # Une même profondeur peut être re-signalée (mise à jour de
                # la meilleure ligne) -- on ne yield qu'au moment où on
                # dépasse (ou atteint) le prochain palier demandé.
                while next_idx < len(targets) and depth >= targets[next_idx]:
                    yield self._format_progressive_entry(board, info, targets[next_idx])
                    next_idx += 1
                if next_idx >= len(targets):
                    break  # les 3 paliers sont produits, pas la peine de continuer la recherche

    def _format_progressive_entry(self, board, info, depth_label):
        pv = info["pv"]
        score_str = self._format_score(info["score"], board.turn)
        pv_san = []
        tmp_board = board.copy()
        for mv in pv[:6]:
            pv_san.append(tmp_board.san(mv))
            tmp_board.push(mv)
        return {
            "game_over": False,
            "depth": depth_label,
            "move_uci": pv[0].uci(),
            "move_san": board.san(pv[0]),
            "score": score_str,
            "pv_san": pv_san,
        }

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
