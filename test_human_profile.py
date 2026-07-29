"""
test_human_profile.py
Tests de la sélection de coup par profil (human_profile.select_move), sans
moteur : candidats fabriqués à la main.

Couvre la règle non négociable des MATS : dès qu'un candidat encode un mat en
MA faveur, le coup proposé est le mat le plus COURT -- quel que soit le profil
et quel que soit le niveau, la tolérance du profil étant suspendue. Avant ce
correctif, un coup de distance au mat ne pesait que 1cp face à des
fenêtres de 20 à 90cp : les profils proposaient couramment un mat plus lent
(mat en 4 alors que mat en 2 existait), et _band_penalty, qui pénalise
volontairement eval_loss=0, poussait même activement vers le plus lent.
"""
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import engine_analysis
import human_profile as hp


_failures = []


def check(cond, msg):
    if not cond:
        _failures.append(msg)


def cand(uci, san, cp, eval_loss, **extra):
    """Candidat minimal au format engine_analysis.analyze_candidates."""
    c = {
        "move_uci": uci, "move_san": san, "cp": cp, "eval_loss": eval_loss,
        "is_capture": False, "is_check": False, "to_square_central": False,
        "is_developing_minor": False, "is_castle": False, "is_king_move": False,
        "is_pawn_center_push": False, "win_prob": None,
        "captured_piece_value": None, "moving_piece_value": 5,
    }
    c.update(extra)
    return c


def _mate_cp(moves):
    """cp encodant un mat en `moves` coups pour le camp au trait."""
    return engine_analysis.MATE_SCORE - moves


def test_le_mat_le_plus_court_gagne_pour_tous_les_profils_et_niveaux():
    # Trois mats de distances différentes + un coup normal. L'ordre moteur met
    # volontairement le plus court en 2e position : la sélection doit le
    # trouver sur la DISTANCE, pas sur le rang.
    candidats = [
        cand("a1a8", "Ta8", _mate_cp(4), 0),    # mat en 4
        cand("b2b8", "Tb8", _mate_cp(2), 1),    # mat en 2  <-- attendu
        cand("g1g2", "Rg2", _mate_cp(3), 2),    # mat en 3
        cand("a1a6", "Ta6", 900, 60),           # pas un mat du tout
    ]
    for tier in (1, 2, 3):
        for profil in hp.PROFILE_IDS:
            chosen = hp.select_move(candidats, tier, profil)
            check(chosen is not None and chosen["move_san"] == "Tb8",
                  f"[{profil}/tier{tier}] doit proposer le mat le plus court (Tb8), "
                  f"obtenu {chosen and chosen['move_san']}")


def test_choix_deterministe_a_distance_egale():
    # Deux mats de MÊME distance : toujours le même (l'ordre moteur tranche),
    # jamais un tirage -- la même position doit toujours redonner le même coup.
    candidats = [
        cand("b2b8", "Tb8", _mate_cp(2), 0),
        cand("a1a8", "Ta8", _mate_cp(2), 0),
    ]
    rendus = {hp.select_move(candidats, 2, "creative")["move_san"] for _ in range(20)}
    check(rendus == {"Tb8"}, f"choix non déterministe à distance égale : {rendus}")


def test_un_mat_subi_n_est_pas_un_mat_donne():
    # cp très NÉGATIF = mat SUBI. Le plus "court" de ces mats est le PIRE coup
    # possible pour moi : le confondre avec un mat donné ferait proposer un
    # coup parce qu'on se fait mater. Ici le court-circuit ne doit pas se
    # déclencher du tout -> la logique de profil normale reprend la main et
    # garde le meilleur coup objectif.
    candidats = [
        cand("g1g2", "Rg2", -_mate_cp(4), 0),   # je me fais mater en 4 (le moins pire)
        cand("g1h2", "Rh2", -_mate_cp(1), 500),  # je me fais mater tout de suite (hors fenêtre)
    ]
    for tier in (1, 2, 3):
        for profil in hp.PROFILE_IDS:
            chosen = hp.select_move(candidats, tier, profil, humanity=0.0)
            check(chosen is not None and chosen["move_san"] == "Rg2",
                  f"[{profil}/tier{tier}] mat SUBI traité comme un mat donné : "
                  f"{chosen and chosen['move_san']}")


def main():
    for fn in (test_le_mat_le_plus_court_gagne_pour_tous_les_profils_et_niveaux,
               test_choix_deterministe_a_distance_egale,
               test_un_mat_subi_n_est_pas_un_mat_donne):
        try:
            fn()
        except Exception as e:
            _failures.append(f"{fn.__name__} a levé une exception : {e!r}")

    if _failures:
        print(f"ÉCHEC ({len(_failures)}) :")
        for f in _failures:
            print("  -", f)
        sys.exit(1)
    print("test_human_profile : OK")


if __name__ == "__main__":
    main()
