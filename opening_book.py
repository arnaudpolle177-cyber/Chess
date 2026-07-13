"""
opening_book.py
Lecture d'un livre d'ouvertures au format polyglot (.bin), construit à
partir de vraies parties (ex: gm2001.bin -- parties de grands maîtres).

Principe : un livre polyglot est indexé par POSITION (pas par numéro de
coup), donc la détection "suis-je encore dans la théorie ?" est automatique
et gratuite -- pas de compteur de coups à maintenir :
- Si la position actuelle a une entrée dans le livre -> on est dedans,
  on répond depuis le livre (quasi instantané, aucun appel Stockfish).
- Sinon -> on n'y est plus (l'adversaire a joué quelque chose hors théorie,
  ou on est simplement sorti de la couverture du livre) -> on bascule
  silencieusement sur le système Stockfish habituel (candidats + profils),
  exactement comme si le livre n'existait pas.

python-chess fournit déjà tout le nécessaire (parsing du format .bin +
hachage Zobrist polyglot) via chess.polyglot -- pas besoin de réinventer le
format ici.
"""
import os
import chess
import chess.polyglot


class OpeningBook:
    def __init__(self, path):
        self.path = path
        self.reader = None
        if path and os.path.isfile(path):
            try:
                self.reader = chess.polyglot.open_reader(path)
                print(f"📖 Livre d'ouvertures chargé : {path}")
            except Exception as e:
                print(f"⚠ Livre d'ouvertures illisible ({path}) : {e}. Le coach fonctionnera sans.")
        else:
            print(
                f"ℹ Pas de livre d'ouvertures trouvé ({path or 'chemin non défini'}). "
                "Le coach utilisera Stockfish dès le 1er coup -- rien de cassé, juste moins "
                "\"humain\" en ouverture. Voir README pour en ajouter un (optionnel)."
            )

    def lookup(self, board):
        """
        Retourne la liste des entrées du livre pour cette position EXACTE
        (chacune avec .move et .weight), ou une liste vide si la position
        n'est pas dans le livre (= on considère qu'on est sorti de la
        théorie à partir d'ici). None si aucun livre n'est chargé.
        """
        if self.reader is None:
            return None
        try:
            return list(self.reader.find_all(board))
        except Exception as e:
            print(f"⚠ Erreur de lecture du livre d'ouvertures pour cette position : {e}")
            return []

    def close(self):
        if self.reader is not None:
            try:
                self.reader.close()
            except Exception:
                pass


def candidates_from_book_entries(board, entries, max_candidates=4):
    """
    Convertit des entrées de livre polyglot en la même structure que
    ChessCoachEngine.analyze_candidates() (voir engine_analysis.py), pour
    pouvoir réutiliser TEL QUEL human_profile.select_move() sans aucune
    modification -- les 4 profils continuent de fonctionner exactement
    pareil, que le coup vienne du livre ou de Stockfish.

    Le "eval_loss" ici est SYNTHÉTIQUE (basé sur le poids relatif du coup
    dans le livre, pas sur une vraie évaluation Stockfish) : un coup de
    livre est par définition une ligne jouée et connue, donc on lui donne
    une perte faible dans tous les cas -- juste assez pour que les profils
    puissent préférer les coups les plus fréquents sans les rendre 100%
    interchangeables entre eux.
    """
    if not entries:
        return []
    entries = sorted(entries, key=lambda e: e.weight, reverse=True)[:max_candidates]
    max_weight = max((e.weight for e in entries), default=1) or 1

    candidates = []
    for e in entries:
        move = e.move
        if move not in board.legal_moves:
            continue  # sécurité : ignore une entrée corrompue/incompatible
        relative_weight = e.weight / max_weight  # 1.0 pour le coup le plus joué, plus petit sinon
        eval_loss = 0 if relative_weight >= 0.999 else min(40, round((1 - relative_weight) * 60))

        tmp_board = board.copy()
        tmp_board.push(move)
        piece = board.piece_at(move.from_square)
        piece_type = piece.piece_type if piece else None
        from_rank = chess.square_rank(move.from_square)
        back_rank = 0 if board.turn == chess.WHITE else 7

        candidates.append({
            "move_uci": move.uci(),
            "move_san": board.san(move),
            "cp": None,  # pas d'éval Stockfish réelle en mode livre
            "eval_loss": eval_loss,
            "score": "Livre",  # affiché à la place d'un score centipawns
            "is_capture": board.is_capture(move),
            "is_check": tmp_board.is_check(),
            "is_castle": board.is_castling(move),
            "is_developing_minor": piece_type in (chess.KNIGHT, chess.BISHOP) and from_rank == back_rank,
            "is_pawn_center_push": piece_type == chess.PAWN and chess.square_file(move.to_square) in (3, 4),
            "to_square_central": chess.square_file(move.to_square) in (3, 4)
                                  and chess.square_rank(move.to_square) in (3, 4),
            "pv_san": [board.san(move)],  # pas de ligne calculée en mode livre, juste le coup lui-même
            "pv_uci": [move.uci()],
        })
    return candidates
