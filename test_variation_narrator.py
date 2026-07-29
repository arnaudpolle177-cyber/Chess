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


def test_une_prise_adverse_seule_nest_pas_un_echange():
    """Apres 1.e4 e5 2.Cf3 Cc6 3.Fc4 Cf6, la ligne O-O Cxe4 Te1 d5 :
    l'adversaire PREND, personne ne reprend sur e4. Le motif ECHANGE annoncait pourtant "un echange qui
    simplifie la position en ma faveur" -- alors que je viens de perdre un
    pion. C'est le faux positif qui sortait sur 28 lignes sur 80."""
    f = _facts("e4 e5 Nf3 Nc6 Bc4 Nf6", "O-O Nxe4 Re1 d5")
    assert f.motif != vn.EXCHANGE, f.motif
    assert f.material_delta < 0, f.material_delta  # la ligne coute bien du materiel


def test_prise_puis_reprise_sur_la_meme_case_est_un_echange():
    """1...Cxe5 2.Cxe5 : prise, reprise, MEME case -- la definition meme d'un
    echange. Sans cette moitie, la garde ci-dessus pourrait tout refuser."""
    f = _facts("e4 e5 Nf3 Nc6 Bc4 Nf6", "Nxe5 Nxe5 d4 Bd6")
    assert f.motif == vn.EXCHANGE, f.motif
    assert f.square == chess.E5, chess.square_name(f.square)


def test_aucun_gabarit_ne_juge_ni_ne_prete_dintention():
    """Garde de langue sur les 15 gabarits : ce module affiche du texte, et le
    projet interdit le jugement comparatif ("meilleures cases") comme
    l'intention pretee ("l'idee est de", que rien ne calcule)."""
    interdits = ("meilleur", "l'idée est de", "en ma faveur", "quand on tient")
    for (motif, voix), texte in vn._TEMPLATES.items():
        for mot in interdits:
            assert mot not in texte.lower(), f"{motif}/{voix} : « {mot} » dans {texte!r}"


def test_le_bilan_materiel_est_dit_quand_il_existe():
    facts = vn.VariationFacts(motif=vn.EXCHANGE, material_delta=3, eval_trend="stable")
    assert "3 points de matériel de plus" in vn.narrate_variation(facts, "popular")
    # ... et tu, sous le seuil : un point d'ecart sur une ligne tronquee n'est
    # pas un fait solide.
    facts = vn.VariationFacts(motif=vn.EXCHANGE, material_delta=1, eval_trend="stable")
    assert "point" not in vn.narrate_variation(facts, "popular")


if __name__ == "__main__":
    test_developpement_calme_nest_pas_une_rupture()
    test_poussee_adverse_nest_pas_ma_rupture()
    test_ma_poussee_suivie_dune_capture_reste_une_rupture()
    test_une_prise_adverse_seule_nest_pas_un_echange()
    test_prise_puis_reprise_sur_la_meme_case_est_un_echange()
    test_aucun_gabarit_ne_juge_ni_ne_prete_dintention()
    test_le_bilan_materiel_est_dit_quand_il_existe()
    print("ok")
