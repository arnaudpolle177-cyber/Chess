"""
why_detector.py
Justification VÉRIFIABLE d'un coup choisi, à partir de sa ligne principale
réelle (pv_uci -- voir engine_analysis.py) -- jamais un motif tactique
inventé (clouage, fourchette... nécessiteraient un vrai détecteur de motifs,
un chantier séparé). Ici, uniquement ce qui se lit directement dans la PV
ou se calcule via les attaquants/défenseurs réels (python-chess).

Retourne un des motifs suivants (ou None si aucun ne s'applique clairement) :
- "fork"              : la pièce jouée attaque 2+ pièces adverses de valeur à la fois
- "pin"                : le coup cloue une pièce adverse qui ne l'était pas avant
- "undefended"       : la case d'arrivée n'a aucun défenseur adverse
- "material_gain"    : gain de matériel net sur la ligne calculée
- "forced_sequence"  : chaque coup de la ligne est une capture ou un échec
- "not_recaptured"   : après ma capture, l'adversaire ne reprend pas sur la case
"""
import chess

PIECE_VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}


def _material_diff_over_pv(board, pv_uci, my_side):
    """Gain matériel net pour `my_side` en jouant toute la ligne, en points de matériel standard."""
    tmp = board.copy()
    gain = 0
    for uci in pv_uci:
        move = chess.Move.from_uci(uci)
        mover_is_me = tmp.turn == my_side
        captured = tmp.piece_at(move.to_square)
        if captured:
            value = PIECE_VALUES.get(captured.piece_type, 0)
            gain += value if mover_is_me else -value
        elif tmp.is_en_passant(move):
            gain += 1 if mover_is_me else -1
        tmp.push(move)
    return gain


def _is_forced_sequence(board, pv_uci):
    """Chaque coup de la ligne (les 2 camps) est une capture ou donne échec."""
    if not pv_uci:
        return False
    tmp = board.copy()
    for uci in pv_uci:
        move = chess.Move.from_uci(uci)
        is_capture = tmp.is_capture(move)
        tmp.push(move)
        if not (is_capture or tmp.is_check()):
            return False
    return True


def _is_undefended(board, move):
    """La case d'arrivée du coup n'a aucun défenseur adverse -- calculé AVANT de jouer le coup."""
    mover = board.turn
    defenders = board.attackers(not mover, move.to_square)
    return len(defenders) == 0


def _is_recaptured(board, pv_uci):
    """
    Si le 1er coup de la ligne est une capture, est-ce que le coup suivant
    (l'adversaire) reprend sur la même case ? Retourne None si le 1er coup
    n'est pas une capture (motif non pertinent ici).
    """
    if len(pv_uci) < 2:
        return None
    tmp = board.copy()
    first = chess.Move.from_uci(pv_uci[0])
    if not tmp.is_capture(first):
        return None
    target_square = first.to_square
    tmp.push(first)
    second = chess.Move.from_uci(pv_uci[1])
    return second.to_square == target_square


def _is_fork(board, move):
    """
    Après ce coup, la pièce qui vient de bouger attaque-t-elle au moins 2
    pièces adverses de valeur (mineure ou plus) simultanément ? Calculé via
    board.attacks() (API native python-chess), pas une estimation.
    """
    tmp = board.copy()
    tmp.push(move)
    piece_square = move.to_square
    targets = 0
    for sq in tmp.attacks(piece_square):
        target = tmp.piece_at(sq)
        if target and target.color != tmp.piece_at(piece_square).color:
            if target.piece_type == chess.KING or PIECE_VALUES.get(target.piece_type, 0) >= 3:
                targets += 1
    return targets >= 2


def _is_pin(board, move):
    """
    Ce coup cloue-t-il une pièce adverse qui ne l'était pas avant (une
    pièce qui, si elle bougeait, exposerait une pièce plus précieuse
    derrière elle -- typiquement le roi) ? Utilise board.is_pinned(), une
    méthode native python-chess, pas une estimation maison.
    """
    opponent = not board.turn
    before_pinned = {sq for sq in chess.SQUARES
                      if board.piece_at(sq) and board.piece_at(sq).color == opponent and board.is_pinned(opponent, sq)}
    tmp = board.copy()
    tmp.push(move)
    for sq in chess.SQUARES:
        piece = tmp.piece_at(sq)
        if piece and piece.color == opponent and sq not in before_pinned and tmp.is_pinned(opponent, sq):
            return True
    return False


def detect_why(board, chosen):
    """
    board : position AVANT le coup choisi.
    chosen : dict candidat (voir engine_analysis.analyze_candidates), doit
    contenir move_uci et pv_uci.
    Retourne (motif: str|None, détail: dict) -- détail contient les
    données chiffrées utiles à narration.py (jamais de texte tout fait).

    Ordre : du motif le plus spécifique/pédagogique (nomme un vrai concept
    tactique) au plus générique.
    """
    move = chess.Move.from_uci(chosen["move_uci"])
    pv_uci = chosen.get("pv_uci") or [chosen["move_uci"]]
    my_side = board.turn

    if _is_fork(board, move):
        return "fork", {}

    if _is_pin(board, move):
        return "pin", {}

    if chosen.get("is_capture") and _is_undefended(board, move):
        return "undefended", {}

    recaptured = _is_recaptured(board, pv_uci)
    if recaptured is False:
        return "not_recaptured", {}

    if _is_forced_sequence(board, pv_uci):
        return "forced_sequence", {}

    gain = _material_diff_over_pv(board, pv_uci, my_side)
    if gain >= 2:  # au moins l'équivalent de 2 pions gagnés sur la ligne
        return "material_gain", {"gain": gain}

    return None, {}
