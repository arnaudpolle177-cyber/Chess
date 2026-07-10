"""
explain.py
Génère une explication en langage clair pour le coup recommandé.
Deux modes :
- "local": règles simples basées sur python-chess (gratuit, hors-ligne)
- "api": appelle l'API Anthropic pour une explication plus riche (nécessite une clé API)
"""
import chess
import os

PIECE_NAMES_FR = {
    chess.PAWN: "pion",
    chess.KNIGHT: "cavalier",
    chess.BISHOP: "fou",
    chess.ROOK: "tour",
    chess.QUEEN: "dame",
    chess.KING: "roi",
}


def explain_move_local(board: chess.Board, move: chess.Move, pv_san):
    """
    Explication basée sur des règles simples : pas d'IA, juste
    des heuristiques (capture, échec, développement, centre, etc.)
    """
    reasons = []

    piece = board.piece_at(move.from_square)
    piece_name = PIECE_NAMES_FR.get(piece.piece_type, "pièce") if piece else "pièce"

    board_copy = board.copy()
    is_capture = board_copy.is_capture(move)
    board_copy.push(move)
    gives_check = board_copy.is_check()

    if is_capture:
        captured = board.piece_at(move.to_square)
        captured_name = PIECE_NAMES_FR.get(captured.piece_type, "pièce") if captured else "pièce"
        reasons.append(f"Ce coup capture {'un(e) ' + captured_name}.")

    if gives_check:
        reasons.append("Ce coup met le roi adverse en échec.")

    if board_copy.is_checkmate():
        reasons.append("C'est mat !")

    # Développement / centre (heuristique simple)
    to_file = chess.square_file(move.to_square)
    to_rank = chess.square_rank(move.to_square)
    is_central = to_file in (3, 4) and to_rank in (3, 4)
    if is_central and piece and piece.piece_type != chess.PAWN:
        reasons.append(f"Le {piece_name} prend une case centrale, ce qui augmente son influence.")

    if piece and piece.piece_type == chess.KNIGHT and board.fullmove_number <= 10:
        reasons.append("Développer les cavaliers tôt est une bonne pratique d'ouverture.")

    if not reasons:
        reasons.append("Ce coup améliore la position (meilleure évaluation selon le moteur).")

    if len(pv_san) > 1:
        suite = " ".join(pv_san[1:4])
        reasons.append(f"La suite logique pourrait être : {suite}...")

    return " ".join(reasons)


def explain_move_via_api(fen, move_san, pv_san, score_str, api_key=None):
    """
    Explication plus riche via l'API Anthropic (optionnel).
    Nécessite: pip install anthropic, et une clé API valide.
    """
    try:
        import anthropic
    except ImportError:
        return "[Module 'anthropic' non installé — utilise le mode local, ou fais 'pip install anthropic']"

    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return "[Pas de clé API Anthropic trouvée — utilise le mode local, ou définis ANTHROPIC_API_KEY]"

    client = anthropic.Anthropic(api_key=api_key)

    prompt = (
        f"Position d'échecs (FEN) : {fen}\n"
        f"Coup recommandé : {move_san}\n"
        f"Évaluation : {score_str}\n"
        f"Ligne principale : {' '.join(pv_san)}\n\n"
        "En 2-3 phrases simples et pédagogiques (niveau débutant/intermédiaire), "
        "explique pourquoi ce coup est bon. Pas de jargon inutile."
    )

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text
