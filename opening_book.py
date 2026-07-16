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

# Mêmes valeurs que engine_analysis.PIECE_VALUES -- dupliquées ici plutôt
# qu'importées pour garder ce module autonome (pas de dépendance vers
# engine_analysis.py juste pour une petite table de constantes).
_PIECE_VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}


class OpeningBook:
    def __init__(self, paths):
        """
        paths : un chemin (str) OU une liste de chemins vers un ou
        plusieurs livres polyglot (.bin). Avec plusieurs livres, leurs
        entrées sont FUSIONNÉES à chaque position (voir lookup) plutôt que
        de n'en garder qu'un seul -- le tri par poids déjà fait dans
        candidates_from_book_entries départage naturellement entre
        sources sans qu'on ait à en privilégier une arbitrairement.
        """
        if isinstance(paths, str):
            paths = [paths]
        self.readers = []
        for path in paths or []:
            if path and os.path.isfile(path):
                try:
                    self.readers.append(chess.polyglot.open_reader(path))
                    print(f"📖 Livre d'ouvertures chargé : {path}")
                except Exception as e:
                    print(f"⚠ Livre d'ouvertures illisible ({path}) : {e}. Ignoré.")
        if not self.readers:
            print(
                "ℹ Pas de livre d'ouvertures trouvé. Le coach utilisera Stockfish dès le "
                "1er coup -- rien de cassé, juste moins \"humain\" en ouverture. Voir README "
                "pour en ajouter un (optionnel)."
            )

    def lookup(self, board):
        """
        Retourne la liste FUSIONNÉE des entrées de TOUS les livres chargés
        pour cette position EXACTE (chacune avec .move et .weight), liste
        vide si aucun livre ne couvre cette position (= on considère qu'on
        est sorti de la théorie à partir d'ici). None si aucun livre n'est
        chargé du tout.
        """
        if not self.readers:
            return None
        entries = []
        for reader in self.readers:
            try:
                entries.extend(reader.find_all(board))
            except Exception as e:
                print(f"⚠ Erreur de lecture d'un livre d'ouvertures pour cette position : {e}")
        return entries

    def close(self):
        for reader in self.readers:
            try:
                reader.close()
            except Exception:
                pass


def candidates_from_book_entries(board, entries, max_candidates=4):
    """
    Convertit des entrées de livre polyglot en la même structure que
    ChessCoachEngine.analyze_candidates() (voir engine_analysis.py), pour
    pouvoir réutiliser TEL QUEL human_profile.select_move() sans aucune
    modification -- les 3 profils continuent de fonctionner exactement
    pareil, que le coup vienne du livre ou du moteur (Berserk).

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

        # Valeurs de pièces (esprit sacrifice) + case de départ du roi (pour
        # le profil "classique" en finale) -- MÊME calcul que
        # engine_analysis.analyze_candidates, indispensable ici aussi :
        # human_profile.py accède à c["is_king_move"] sans .get() en finale
        # (_score_classical), donc un candidat de livre auquel il manquerait
        # ce champ ferait planter la sélection de coup si jamais un livre
        # couvre encore une position déjà classée "finale" (rare mais pas
        # impossible avec des livres construits sur de vraies parties
        # longues).
        moving_piece_value = _PIECE_VALUES.get(piece_type, 0)
        captured_piece = board.piece_at(move.to_square)
        captured_piece_value = _PIECE_VALUES.get(captured_piece.piece_type, 0) if captured_piece else None
        if board.is_en_passant(move):
            captured_piece_value = 1
        is_king_move = piece_type == chess.KING and not board.is_castling(move)

        candidates.append({
            "move_uci": move.uci(),
            "move_san": board.san(move),
            "cp": None,  # pas d'éval Stockfish réelle en mode livre
            "eval_loss": eval_loss,
            "score": "Livre",  # affiché à la place d'un score centipawns
            "is_capture": board.is_capture(move),
            "is_check": tmp_board.is_check(),
            "is_castle": board.is_castling(move),
            "is_king_move": is_king_move,
            "is_developing_minor": piece_type in (chess.KNIGHT, chess.BISHOP) and from_rank == back_rank,
            "is_pawn_center_push": piece_type == chess.PAWN and chess.square_file(move.to_square) in (3, 4),
            "to_square_central": chess.square_file(move.to_square) in (3, 4)
                                  and chess.square_rank(move.to_square) in (3, 4),
            "win_prob": None,  # pas de WDL en mode livre (pas une éval moteur)
            "moving_piece_value": moving_piece_value,
            "captured_piece_value": captured_piece_value,  # None si pas une capture
            "pv_san": [board.san(move)],  # pas de ligne calculée en mode livre, juste le coup lui-même
            "pv_uci": [move.uci()],
        })
    return candidates
