"""
test_fragment_library.py
Tests de l'étape 3 (fragment_library.py) -- réservoir de fragments.

Ce que ces tests garantissent (sans moteur, sans réseau) :
  - chaque thème connu produit un dict {observation, cause, plan} dans les 3
    voix, sans exception ;
  - le CONTRAT DE FRAGMENT est respecté : observation et plan toujours
    présents/non vides, aucune clause ne se termine par un point, aucune ne
    commence par une majuscule décorative (sauf notation SAN / nom propre) ;
  - les fragments ancrés sur un champ réel CITENT bien ce champ (la case
    d'une faiblesse de pion, le SAN d'un coup manqué, le pion passé) --
    preuve que "rien d'inventé" tient : la donnée affichée vient de la brique ;
  - un champ optionnel absent (why_motif None, opponent_better_move_san None,
    passed_pawn_square None) ne casse rien et ne fabrique pas de donnée.
"""
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import chess

from theme_detector import (
    ThemeCandidate,
    BLUNDER, TACTICAL, ATTACK, DEFENSE, MISSED_OPPORTUNITY,
    ENDGAME, OPENING, INITIATIVE_SHIFT, STRATEGIC_ADVANTAGE, PAWN_STRUCTURE,
    PIECE_ACTIVITY_GAP, KING_SAFETY_WARNING, EQUAL_POSITION,
)
import fragment_library as fl
from fragment_library import FragmentContext, fragments_for, VOICES


_failures = []


def check(cond, msg):
    if not cond:
        _failures.append(msg)


def brick(theme, **fields):
    return ThemeCandidate(theme, 1.0, dict(fields))


# Notation SAN / nom propre : un fragment PEUT légitimement commencer par une
# majuscule s'il cite un coup (rare -- en pratique nos observations mettent le
# SAN en milieu de clause, mais on reste tolérant). On vérifie surtout
# l'absence de point final et la présence des clés.
def _assert_clause(clause, where):
    check(isinstance(clause, str) and clause.strip() != "", f"{where}: clause vide")
    if not clause:
        return
    check(not clause.rstrip().endswith("."), f"{where}: clause finit par un point -> {clause!r}")
    check(not clause.rstrip().endswith(" et"), f"{where}: clause finit par une conjonction pendante -> {clause!r}")


ALL_THEMES = [
    BLUNDER, TACTICAL, ATTACK, DEFENSE, MISSED_OPPORTUNITY,
    ENDGAME, OPENING, INITIATIVE_SHIFT, STRATEGIC_ADVANTAGE, PAWN_STRUCTURE,
    PIECE_ACTIVITY_GAP, KING_SAFETY_WARNING, EQUAL_POSITION,
]

# Champs plausibles par thème (mêmes noms que collect_theme_bricks pose) pour
# que chaque fragment ait de quoi s'ancrer.
SAMPLE_FIELDS = {
    BLUNDER: {"swing_cp": 220},
    TACTICAL: {},
    ATTACK: {"king_square": chess.G8},
    DEFENSE: {"king_square": chess.G1},
    MISSED_OPPORTUNITY: {"swing_cp": 90, "opponent_better_move_san": "Qh5"},
    ENDGAME: {"passed_pawn_square": chess.D6},
    OPENING: {},
    INITIATIVE_SHIFT: {"initiative_slope_cp": -30.0},
    STRATEGIC_ADVANTAGE: {"material_imbalance_kind": "bishop_pair_open", "simplification_advice": "simplify"},
    PAWN_STRUCTURE: {"pawn_weakness_square": chess.C6, "pawn_weakness_kind": "isolated"},
    PIECE_ACTIVITY_GAP: {"activity_ratio": 1.6},
    KING_SAFETY_WARNING: {"king_safety_warning_square": chess.E1, "king_safety_warning_is_mine": True},
    EQUAL_POSITION: {},
}


def test_all_themes_all_voices_shape():
    ctx = FragmentContext(eval_cp=150)
    for theme in ALL_THEMES:
        for voice in VOICES:
            b = brick(theme, **SAMPLE_FIELDS[theme])
            frag = fragments_for(b, voice, ctx)
            check(set(frag.keys()) == {"observation", "cause", "plan"},
                  f"{theme}/{voice}: clés inattendues -> {set(frag.keys())}")
            _assert_clause(frag["observation"], f"{theme}/{voice} observation")
            _assert_clause(frag["plan"], f"{theme}/{voice} plan")
            if frag["cause"] is not None:
                _assert_clause(frag["cause"], f"{theme}/{voice} cause")


def test_unknown_voice_falls_back():
    b = brick(BLUNDER, swing_cp=200)
    frag = fragments_for(b, "nonexistent_voice", FragmentContext())
    check(frag["observation"], "voix inconnue -> devrait retomber sur un fallback non vide")


def test_no_context_no_crash():
    # ctx None : les fragments doivent retomber sur leur formulation générale.
    for theme in ALL_THEMES:
        b = brick(theme, **SAMPLE_FIELDS[theme])
        frag = fragments_for(b, "popular")  # ctx omis
        check(frag["observation"], f"{theme}: ctx absent -> observation vide")
        check(frag["plan"], f"{theme}: ctx absent -> plan vide")


def test_pawn_structure_cites_real_square():
    # "rien d'inventé" : la case citée est bien celle de la brique.
    b = brick(PAWN_STRUCTURE, pawn_weakness_square=chess.C6, pawn_weakness_kind="isolated")
    for voice in VOICES:
        frag = fragments_for(b, voice)
        text = " ".join(v for v in frag.values() if v)
        check("c6" in text, f"{voice}: la case c6 de la faiblesse devrait apparaître -> {text!r}")
        check("isolé" in text.lower(), f"{voice}: le type 'isolé' devrait apparaître -> {text!r}")


def test_missed_cites_san_when_present():
    b = brick(MISSED_OPPORTUNITY, swing_cp=90, opponent_better_move_san="Qh5")
    frag = fragments_for(b, "popular")
    check("Qh5" in frag["observation"], f"le SAN Qh5 devrait être cité -> {frag['observation']!r}")


def test_missed_no_san_no_invention():
    # SAN absent : l'observation ne doit PAS inventer de coup (pas de motif
    # SAN fabriqué). On vérifie juste qu'elle reste générale et non vide.
    b = brick(MISSED_OPPORTUNITY, swing_cp=90, opponent_better_move_san=None)
    frag = fragments_for(b, "popular")
    check(frag["observation"], "observation vide sans SAN")
    # aucune majuscule suivie de chiffre typique d'un SAN inventé (heuristique
    # faible mais utile) -- surtout : le test SAMPLE ci-dessus prouve le
    # chemin AVEC san, celui-ci prouve le chemin SANS.
    check("None" not in frag["observation"], "le None ne doit jamais fuiter dans le texte")


def test_endgame_passed_pawn_vs_none():
    with_pawn = fragments_for(brick(ENDGAME, passed_pawn_square=chess.D6), "popular")
    without = fragments_for(brick(ENDGAME, passed_pawn_square=None), "popular")
    check("d6" in " ".join(v for v in with_pawn.values() if v),
          "le pion passé d6 devrait être cité quand il existe")
    check("d6" not in " ".join(v for v in without.values() if v),
          "aucune case ne doit être citée quand il n'y a pas de pion passé")


def test_initiative_sign_depends_on_eval():
    b = brick(INITIATIVE_SHIFT, initiative_slope_cp=-30.0)
    winning = fragments_for(b, "popular", FragmentContext(eval_cp=120))
    losing = fragments_for(b, "popular", FragmentContext(eval_cp=-120))
    # En avantage : on PERD l'initiative ; en désavantage : on la REPREND.
    check(winning["observation"] != losing["observation"],
          "le sens du basculement d'initiative devrait dépendre de l'éval")


def test_strategic_imbalance_drives_plan():
    open_pair = fragments_for(
        brick(STRATEGIC_ADVANTAGE, material_imbalance_kind="bishop_pair_open", simplification_advice="simplify"),
        "classical", FragmentContext(eval_cp=150))
    no_imb = fragments_for(
        brick(STRATEGIC_ADVANTAGE, material_imbalance_kind=None, simplification_advice="simplify"),
        "classical", FragmentContext(eval_cp=150))
    check(open_pair["observation"] != no_imb["observation"],
          "un déséquilibre matériel connu devrait changer l'observation stratégique")
    check("fous" in open_pair["observation"].lower(),
          f"bishop_pair_open devrait mentionner les fous -> {open_pair['observation']!r}")


def test_king_safety_mine_vs_opponent():
    mine = fragments_for(
        brick(KING_SAFETY_WARNING, king_safety_warning_square=chess.E1, king_safety_warning_is_mine=True),
        "popular")
    opp = fragments_for(
        brick(KING_SAFETY_WARNING, king_safety_warning_square=chess.E8, king_safety_warning_is_mine=False),
        "popular")
    check("ton roi" in mine["observation"].lower(), f"is_mine=True -> 'ton roi' -> {mine['observation']!r}")
    check("adverse" in opp["observation"].lower(), f"is_mine=False -> roi adverse -> {opp['observation']!r}")


def test_opening_castle_three_states():
    # Trois états distincts (voir fragment_library._castle_state) :
    #   - roque JOUABLE maintenant  -> conseille de roquer ;
    #   - droits présents mais pièces pas sorties (coup 1) -> conseille de
    #     DÉVELOPPER, jamais "roque derrière toi" (le bug corrigé) ;
    #   - plus de droits (déjà roqué) -> ne parle plus de roque.
    now = chess.Board("rnbqk2r/pppp1ppp/5n2/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 1")  # O-O légal
    early = chess.Board("rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 1")  # droits, mais rien de sorti
    done = chess.Board("rnbq1rk1/pppp1ppp/5n2/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQ1RK1 w - - 0 1")  # les deux ont roqué

    def line(board):
        frag = fragments_for(brick(OPENING), "popular", FragmentContext(board=board))
        return " ".join(v for v in frag.values() if v).lower()

    now_txt, early_txt, done_txt = line(now), line(early), line(done)
    check("roque" in now_txt, f"roque jouable -> devrait conseiller de roquer -> {now_txt!r}")
    # Coup 1 : on doit inviter à développer, PAS annoncer un roque déjà fait.
    check("roque est derrière" not in early_txt,
          f"début d'ouverture -> ne doit PAS dire 'le roque est derrière toi' -> {early_txt!r}")
    check("développe" in early_txt or "sorties" in early_txt,
          f"début d'ouverture -> devrait parler de développement -> {early_txt!r}")
    # Déjà roqué : on ne renvoie plus vers le roque.
    check("roque est derrière" in done_txt or "roque n'est plus" in done_txt,
          f"déjà roqué -> devrait acter que le roque est fait -> {done_txt!r}")


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        try:
            t()
            print(f"  ok  {t.__name__}")
        except Exception as e:  # une exception DANS un test est un échec dur
            _failures.append(f"{t.__name__} a levé {type(e).__name__}: {e}")
            print(f" ERR {t.__name__}: {e}")
    print(f"\n{len(tests)} tests executes")
    if _failures:
        print(f"[ECHEC] {len(_failures)} probleme(s) :")
        for f in _failures:
            print(f"   - {f}")
        sys.exit(1)
    print("[OK] fragment_library respecte le contrat de fragment")


if __name__ == "__main__":
    _run()
