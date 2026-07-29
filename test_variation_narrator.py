"""Gardes anti-faits-faux sur variation_narrator.analyze_variation.

Les gabarits de narrate_variation parlent tous de "mes pieces" : un motif
declenche par un coup ADVERSE est un fait faux affiche a l'ecran. Ces trois
cas viennent d'un audit sur une vraie partie (Morphy-Opera) ou le coach
annoncait une "rupture de pion" sur Fc4 et Fg5, deux coups sans aucune
rupture dans la ligne.
"""
import chess

import variation_narrator as vn


def _facts(prefix_san, line_san):
    board = chess.Board()
    for san in prefix_san.split():
        board.push_san(san)
    cur, moves = board.copy(), []
    for san in line_san.split():
        m = cur.parse_san(san)
        moves.append(m)
        cur.push(m)
    return vn.analyze_variation(None, board.copy(), moves, compute_eval=False)


def test_developpement_calme_nest_pas_une_rupture():
    # Fc4 Fe7 O-O Cf6 Te1 O-O : aucune poussee de pion, aucune capture.
    f = _facts("e4 e5 Nf3 d6 d4 Bg4 dxe5 Bxf3 Qxf3 dxe5", "Bc4 Be7 O-O Nf6 Re1 O-O")
    assert f.motif != vn.BREAKTHROUGH, f.motif
    # ...et les coups NOIRS pres du roi noir ne sont pas ma pression.
    assert f.motif != vn.KING_PRESSURE, f.motif


def test_poussee_adverse_nest_pas_ma_rupture():
    f = _facts("e4 e5 Nf3 d6 d4 Bg4 dxe5 Bxf3 Qxf3 dxe5 Bc4 Nf6 Qb3 Qe7 Nc3 c6",
               "Bg5 b6 a4 h6 Be3 Nbd7")
    assert f.motif != vn.BREAKTHROUGH, f.motif


def test_ma_poussee_suivie_dune_capture_reste_une_rupture():
    # d4-d5 puis ...exd5 : la detection doit toujours fonctionner.
    f = _facts("e4 e5 Nf3 Nc6 Bc4 Bc5 c3 Nf6 d3 d6", "d4 exd4 cxd4 Bb6")
    assert f.motif == vn.BREAKTHROUGH, f.motif
    assert f.square == chess.D4


if __name__ == "__main__":
    test_developpement_calme_nest_pas_une_rupture()
    test_poussee_adverse_nest_pas_ma_rupture()
    test_ma_poussee_suivie_dune_capture_reste_une_rupture()
    print("ok")
