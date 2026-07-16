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

# Valeurs de pièces (convention standard) -- utilisées pour repérer les
# échanges déséquilibrés (esprit "sacrifice"/créatif) et les échanges "à
# volume égal" (technique classique de fin de partie).
PIECE_VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}


class ChessCoachEngine:
    def __init__(self, stockfish_path, threads=None, hash_mb=1024):
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

        # UCI_ShowWDL (Win/Draw/Loss en pour-mille) : option UCI standard
        # (pas propre à un moteur en particulier, contrairement à
        # UCI_LimitStrength/UCI_Elo) -- supportée par Stockfish, Berserk,
        # Lc0, et beaucoup d'autres. Utilisée par human_profile.py pour le
        # profil "populaire" : maximiser les chances de gain PRATIQUES
        # plutôt que suivre un avis Elo-bridé (qui n'existe plus, voir
        # l'historique -- UCI_Elo est propre à Stockfish).
        self.wdl_supported = "UCI_ShowWDL" in self.engine.options
        if self.wdl_supported:
            try:
                self.engine.configure({"UCI_ShowWDL": True})
            except chess.engine.EngineError as e:
                print(f"⚠ Impossible d'activer UCI_ShowWDL : {e}")
                self.wdl_supported = False
        else:
            print("ℹ Ce moteur ne fournit pas de statistiques Win/Draw/Loss -- le profil \"populaire\" s'appuiera uniquement sur la perte d'éval.")

    def analyze_candidates(self, fen, multipv=4, depth=18, safe_mode=False, is_stale=None):
        """
        Retourne jusqu'à `multipv` coups candidats objectivement bons,
        triés du meilleur au moins bon, chacun avec sa perte d'éval
        ("eval_loss", en centipawns) par rapport au meilleur. Utilisé par
        human_profile.py pour choisir LEQUEL de ces bons coups correspond à
        chaque profil/niveau.

        Deux modes :
        - safe_mode=False (par défaut) : recherche multi-lignes NATIVE de
          Stockfish (option UCI MultiPV), rapide (le travail est partagé
          entre les lignes). C'est le mode normal pour l'immense majorité
          des positions.
        - safe_mode=True : `multipv` recherches simple-ligne SUCCESSIVES,
          en excluant à chaque fois le(s) coup(s) déjà trouvé(s) via
          `root_moves` (équivalent UCI "searchmoves"). Plus lent (pas de
          travail partagé entre les recherches), mais évite complètement le
          code multi-PV natif de Stockfish -- utilisé UNIQUEMENT pour les
          positions déjà connues pour faire planter le mode natif (voir
          web_bridge.py, _main_engine_degraded). Inutile de payer ce coût
          de vitesse pour toutes les positions alors que la grande
          majorité n'a jamais posé de problème.
        """
        board = chess.Board(fen)
        if board.is_game_over():
            return {"game_over": True, "result": board.result()}, board

        if is_stale is not None and is_stale():
            return {"stale": True}, board

        if safe_mode:
            info_list = self._analyse_successive(board, multipv, depth, is_stale=is_stale)
            if info_list is None:
                return {"stale": True}, board
        else:
            info_list = self.engine.analyse(board, chess.engine.Limit(depth=depth), multipv=multipv)
            if isinstance(info_list, dict):
                info_list = [info_list]

        candidates = []
        best_cp = None
        for info in info_list:
            pv = info.get("pv")
            if not pv:
                continue
            move = pv[0]
            cp = info["score"].pov(board.turn).score(mate_score=100000)
            if best_cp is None:
                best_cp = cp

            tmp_board = board.copy()
            tmp_board.push(move)
            pv_san = []
            pv_uci = []
            san_board = board.copy()
            for mv in pv[:6]:
                pv_san.append(san_board.san(mv))
                pv_uci.append(mv.uci())
                san_board.push(mv)
            piece = board.piece_at(move.from_square)
            piece_type = piece.piece_type if piece else None
            from_rank = chess.square_rank(move.from_square)
            back_rank = 0 if board.turn == chess.WHITE else 7
            # Développe une pièce mineure (cavalier/fou) depuis sa case de
            # départ -- signal "classique/naturel" indépendant de tout avis
            # Elo, contrairement à avant où "populaire" et "classique"
            # s'ancraient sur EXACTEMENT le même signal et convergeaient
            # presque toujours sur le même coup.
            is_developing_minor = piece_type in (chess.KNIGHT, chess.BISHOP) and from_rank == back_rank
            is_pawn_center_push = piece_type == chess.PAWN and chess.square_file(move.to_square) in (3, 4)
            is_king_move = piece_type == chess.KING and not board.is_castling(move)

            # Valeurs de pièces -- utilisées pour repérer les échanges
            # déséquilibrés (esprit "sacrifice"/créatif) et les échanges "à
            # volume égal" (technique classique de fin de partie : échanger
            # quand on a l'avantage). None si ce n'est pas une capture.
            moving_piece_value = PIECE_VALUES.get(piece_type, 0)
            captured_piece = board.piece_at(move.to_square)
            captured_piece_value = PIECE_VALUES.get(captured_piece.piece_type, 0) if captured_piece else None
            if board.is_en_passant(move):
                captured_piece_value = 1

            # WDL (Win/Draw/Loss) du point de vue du camp qui joue ce coup,
            # si le moteur le fournit (voir __init__, UCI_ShowWDL) -- sert
            # de signal "chances de gain pratiques", distinct de l'éval
            # brute en centipawns. None si le moteur ne le fournit pas.
            win_prob = None
            wdl = info.get("wdl")
            if wdl is not None:
                pov_wdl = wdl.pov(board.turn)
                total = pov_wdl.wins + pov_wdl.draws + pov_wdl.losses
                if total > 0:
                    win_prob = pov_wdl.wins / total

            candidates.append({
                "move_uci": move.uci(),
                "move_san": board.san(move),
                "cp": cp,
                "eval_loss": max(0, best_cp - cp),
                "score": self._format_score(info["score"], board.turn),
                "is_capture": board.is_capture(move),
                "is_check": tmp_board.is_check(),
                "is_castle": board.is_castling(move),
                "is_king_move": is_king_move,
                "is_developing_minor": is_developing_minor,
                "is_pawn_center_push": is_pawn_center_push,
                "to_square_central": chess.square_file(move.to_square) in (3, 4)
                                      and chess.square_rank(move.to_square) in (3, 4),
                "win_prob": win_prob,
                "moving_piece_value": moving_piece_value,
                "captured_piece_value": captured_piece_value,  # None si pas une capture
                "pv_san": pv_san,
                "pv_uci": pv_uci,
            })

        candidates = self._dedupe_by_root_move(candidates)

        # Fallback enrichissement : en mode natif uniquement (jamais en
        # safe_mode, trop coûteux/risqué -- voir web_bridge.py,
        # _main_engine_degraded), si la déduplication laisse moins de 3
        # coups uniques et qu'on n'a pas déjà demandé le maximum, on
        # relance une analyse complète à multipv=6 pour donner aux profils
        # un vrai choix de style plutôt que 1-2 coups imposés. Récursif
        # une seule fois : le rappel passe déjà multipv=6, qui est le
        # plafond, donc pas de boucle infinie.
        if not safe_mode and len(candidates) < 3 and multipv < 6:
            return self.analyze_candidates(fen, multipv=6, depth=depth, safe_mode=False)

        return {"game_over": False, "candidates": candidates}, board

    @staticmethod
    def _dedupe_by_root_move(candidates):
        """
        Regroupe les candidats par coup racine (move_uci) et ne garde que
        la meilleure éval (cp le plus haut, du point de vue du camp au
        trait) pour chaque coup unique -- évite qu'une transposition plus
        loin dans la PV fasse artificiellement gonfler le nombre de choix
        distincts vus par human_profile.py (2 PV commençant par le même
        coup ne sont PAS 2 choix différents pour un joueur humain). L'ordre
        d'entrée (déjà trié meilleur -> moins bon) garantit qu'on garde la
        première occurrence rencontrée pour chaque coup.
        """
        seen = set()
        deduped = []
        for c in candidates:
            if c["move_uci"] in seen:
                continue
            seen.add(c["move_uci"])
            deduped.append(c)
        return deduped

    def _analyse_successive(self, board, multipv, depth, is_stale=None):
        """
        Génère `multipv` résultats d'analyse simple-ligne successifs (voir
        analyze_candidates, safe_mode=True), en excluant à chaque fois le
        coup déjà trouvé.

        `is_stale` (optionnel) est re-vérifié avant CHAQUE recherche
        simple-ligne : dès qu'un coup plus récent a changé la position
        pendant qu'on enchaînait les recherches, on arrête immédiatement
        au lieu de continuer à chercher des candidats pour une position
        qui n'est déjà plus affichée -- retourne None dans ce cas (à
        distinguer d'une liste vide, qui reste un résultat valide).
        """
        remaining_moves = list(board.legal_moves)
        n_wanted = min(multipv, len(remaining_moves))
        results = []
        for _ in range(n_wanted):
            if not remaining_moves:
                break
            if is_stale is not None and is_stale():
                return None
            info = self.engine.analyse(board, chess.engine.Limit(depth=depth), root_moves=remaining_moves)
            pv = info.get("pv")
            if not pv:
                break
            results.append(info)
            remaining_moves = [m for m in remaining_moves if m != pv[0]]
        return results

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
