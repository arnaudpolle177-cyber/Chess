"""Prophylaxie : ce que le coup EMPECHE.

La moitie testee ici est la moitie PURE (prevented_by) -- aucun moteur. La
moitie moteur (opponent_threat) n'est testee que sur sa garde d'echec, le seul
point ou elle peut produire une position ILLEGALE : python-chess accepte
board.push(chess.Move.null()) en echec sans lever la moindre erreur et fabrique
silencieusement une position invalide (is_valid() -> False).
"""
import chess

import prophylaxis


def test_menace_annulee_par_capture_de_la_piece():
    # Cavalier noir en g4 pret a sauter en f2 (capture le pion) ; Bxg4
    # supprime la piece qui devait jouer la menace.
    # FEN corrigee : la FEN du brief ("... w kq ...") place un cavalier noir
    # en g4 alors que c'est aux BLANCS de jouer et son fou c4 ne peut pas
    # atteindre g4 -- ni "Nxf2" (coup noir) ni "Bxg4" n'y sont legaux. Position
    # minimale reconstruite avec la meme intention : Bh3 peut prendre Ng4, qui
    # menace Nxf2.
    board = chess.Board("4k3/8/8/8/6n1/7B/5P2/4K3 w - - 0 1")
    probe = board.copy()
    probe.push(chess.Move.null())
    threat = probe.parse_san("Nxf2")            # coup NOIR, la menace
    my_move = board.parse_san("Bxg4")            # je prends le cavalier
    out = prophylaxis.prevented_by(board, my_move, threat)
    assert out is not None
    assert out["reason"] == "captured"
    assert out["san"] == "Nxf2"


def test_menace_toujours_disponible_ne_reclame_rien():
    board = chess.Board("4k3/8/8/8/6n1/7B/5P2/4K3 w - - 0 1")
    probe = board.copy()
    probe.push(chess.Move.null())
    threat = probe.parse_san("Nxf2")
    my_move = board.parse_san("Kd1")             # ne touche pas au cavalier de g4
    assert prophylaxis.prevented_by(board, my_move, threat) is None


def test_menace_annulee_parce_que_la_case_est_occupee():
    """Le coup n'enleve pas la piece menacante, mais rend le coup illegal :
    reason doit valoir "blocked", pas "captured"."""
    # Dame noire en a7, menace Qxg1 le long de la diagonale a7-g1 (rien entre
    # les deux). Mon coup Ne3 interpose le cavalier c2 sur cette diagonale :
    # la dame est toujours vivante et son trajet vers g1 n'est plus legal.
    board = chess.Board("4k3/q7/8/8/8/8/2N5/4K1R1 w - - 0 1")
    probe = board.copy()
    probe.push(chess.Move.null())
    threat = probe.parse_san("Qxg1")
    my_move = board.parse_san("Ne3")
    out = prophylaxis.prevented_by(board, my_move, threat)
    assert out is not None and out["reason"] == "blocked"


def test_aucune_menace_aucun_plantage():
    board = chess.Board()
    assert prophylaxis.prevented_by(board, board.parse_san("e4"), None) is None


def test_en_echec_pas_de_coup_nul():
    # Roi blanc en echec : le coup nul est ILLEGAL. La detection doit rendre
    # None sans jamais interroger le moteur (engine=None le prouve : si la
    # garde sautait, on planterait sur None.engine).
    check_board = chess.Board("rnbqkbnr/ppp2ppp/8/3pp3/6P1/5P2/PPPPP2P/RNBQKBNR w KQkq - 0 1")
    check_board.push_san("a3")
    check_board.push_san("Qh4")
    assert check_board.is_check()
    assert prophylaxis.opponent_threat(None, check_board) is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
