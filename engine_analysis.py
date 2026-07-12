"""
engine_analysis.py
Interroge Stockfish pour obtenir le meilleur coup et l'évaluation.
"""
import os
import time
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

    def analyze_candidates(self, fen, multipv=4, depth=18):
        """
        Retourne jusqu'à `multipv` coups candidats objectivement bons
        (MultiPV Stockfish), triés du meilleur au moins bon, chacun avec sa
        perte d'éval ("eval_loss", en centipawns) par rapport au meilleur.
        Utilisé par human_profile.py pour choisir LEQUEL de ces bons coups
        correspond à chaque profil/niveau -- contrairement à
        analyze_fen_progressive(), ceci ne varie jamais la profondeur pour
        "faire plus faible" : la profondeur ici sert uniquement à avoir une
        éval fiable de chaque candidat, pas à limiter la force.
        """
        board = chess.Board(fen)
        if board.is_game_over():
            return {"game_over": True, "result": board.result()}, board

        info_list = self.engine.analyse(board, chess.engine.Limit(depth=depth), multipv=multipv)
        if isinstance(info_list, dict):
            info_list = [info_list]

        candidates = []
        best_cp = None
        for info in info_list:
            pv = info.get("pv")
            if not pv:
                continue
            cp = info["score"].pov(board.turn).score(mate_score=100000)
            if best_cp is None:
                best_cp = cp
            move = pv[0]
            tmp_board = board.copy()
            tmp_board.push(move)
            pv_san = []
            san_board = board.copy()
            for mv in pv[:6]:
                pv_san.append(san_board.san(mv))
                san_board.push(mv)
            candidates.append({
                "move_uci": move.uci(),
                "move_san": board.san(move),
                "cp": cp,
                "eval_loss": max(0, best_cp - cp),
                "score": self._format_score(info["score"], board.turn),
                "is_capture": board.is_capture(move),
                "is_check": tmp_board.is_check(),
                "is_castle": board.is_castling(move),
                "to_square_central": chess.square_file(move.to_square) in (3, 4)
                                      and chess.square_rank(move.to_square) in (3, 4),
                "pv_san": pv_san,
            })
        return {"game_over": False, "candidates": candidates}, board

    def configure_as_elo_advisor(self):
        """
        Bascule ce moteur en 'conseiller Elo' PERMANENT : UCI_LimitStrength
        reste activé pour toute sa durée de vie, faible Threads/Hash (pas
        besoin de puissance pour un avis rapide à 150ms). Seul UCI_Elo est
        modifié entre 2 appels (jamais LimitStrength lui-même) -- c'est
        beaucoup plus sûr que de basculer LimitStrength ON/OFF à chaque
        appel sur le moteur PRINCIPAL (voir suggest_move ci-dessous et le
        commentaire dans web_bridge.py, _init_elo_advisor).
        """
        try:
            self.engine.configure({"Threads": 1, "Hash": 16, "UCI_LimitStrength": True})
        except chess.engine.EngineError as e:
            print(f"⚠ Impossible de configurer le moteur 'conseiller Elo' : {e}")

    def suggest_move(self, fen, elo, movetime_ms=150):
        """
        À utiliser UNIQUEMENT sur un moteur déjà configuré via
        configure_as_elo_advisor() (LimitStrength déjà actif en permanence).
        Ne fait que changer UCI_Elo puis lance une analyse courte -- ne
        touche JAMAIS LimitStrength ici (c'est ce qui rendait l'ancienne
        version instable : reconfigurer LimitStrength sur le moteur
        PRINCIPAL, potentiellement pendant qu'une autre recherche vient
        juste de se terminer dessus, pouvait corrompre son état interne et
        provoquer un vrai crash natif de Stockfish).
        """
        self.engine.configure({"UCI_Elo": max(1320, min(3190, elo))})
        info = self.engine.analyse(chess.Board(fen), chess.engine.Limit(time=movetime_ms / 1000))
        pv = info.get("pv")
        return pv[0].uci() if pv else None

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
        start_time = time.monotonic()  # [debug perf] pour mesurer le temps réel jusqu'à chaque palier

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
                    elapsed = time.monotonic() - start_time  # [debug perf]
                    print(f"[stockfish] palier {targets[next_idx]} atteint en {elapsed:.2f}s (depth réel Stockfish : {depth})")
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
