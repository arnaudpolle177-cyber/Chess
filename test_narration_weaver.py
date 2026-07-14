"""
test_narration_weaver.py
Tests de l'étape 4 (narration_weaver.py) -- le weaver.

Vérifie l'assemblage de bout en bout, avec de VRAIES briques passées par le
scoring/sélection (theme_scoring) : on ne teste pas le weaver en vase clos
mais sur le flux réel étape 2 -> étape 4.

Garanties :
  - le paragraphe final est UNE chaîne non vide, 2 à 4 phrases, ponctuée ;
  - le PLAN provient toujours du PRINCIPAL (pas d'un secondaire) ;
  - la donnée réelle d'une brique (case de faiblesse, SAN) se retrouve bien
    dans le texte tissé -> "rien d'inventé" survit à l'assemblage ;
  - un secondaire de relation FORTE est tissé inline (connecteur relationnel
    présent) ; un secondaire neutre devient une phrase à part ;
  - 0 secondaire produit un paragraphe valide (principal seul) ;
  - le caution transversal est renvoyé à part, PAS tissé dans le texte.
"""
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import chess

from theme_detector import (
    ThemeCandidate, theme_tier, theme_family,
    BLUNDER, TACTICAL, ATTACK, DEFENSE, MISSED_OPPORTUNITY,
    ENDGAME, OPENING, INITIATIVE_SHIFT, STRATEGIC_ADVANTAGE, PAWN_STRUCTURE,
    PIECE_ACTIVITY_GAP, KING_SAFETY_WARNING, EQUAL_POSITION,
)
import theme_scoring as ts
import fragment_library as fl
from fragment_library import FragmentContext
import narration_weaver as nw


_failures = []


def check(cond, msg):
    if not cond:
        _failures.append(msg)


def brick(theme, strength, **fields):
    f = dict(fields)
    f["_tier"] = theme_tier(theme)
    f["_family"] = theme_family(theme)
    return ThemeCandidate(theme, strength, f)


def _n_sentences(text):
    # compte les séparateurs ". " (point + espace) : robuste aux décimales
    # ("3.0 pions" -> le point est suivi d'un chiffre, pas d'un espace).
    return (text.count(". ") + 1) if text else 0


def test_lead_only_paragraph():
    lead, supports = ts.select_lead_and_support([brick(ATTACK, 300, king_square=chess.G8)])
    res = nw.weave(lead, supports, "popular", FragmentContext(eval_cp=300))
    check(res["text"], "paragraphe vide pour un principal seul")
    check(res["text"].endswith("."), f"paragraphe non ponctué -> {res['text']!r}")
    check(supports == [], "aucun secondaire attendu ici")
    # principal seul -> au moins 2 phrases (observation + plan)
    check(_n_sentences(res["text"]) >= 2, f"attendu >=2 phrases -> {res['text']!r}")


def test_plan_comes_from_lead():
    # STRATEGIC principal + PAWN_STRUCTURE secondaire (relation CAUSE).
    cands = [
        brick(STRATEGIC_ADVANTAGE, 300, material_imbalance_kind=None, simplification_advice="simplify"),
        brick(PAWN_STRUCTURE, 1.0, pawn_weakness_square=chess.C6, pawn_weakness_kind="isolated"),
    ]
    lead, supports = ts.select_lead_and_support(cands)
    ctx = FragmentContext(eval_cp=300)
    res = nw.weave(lead, supports, "popular", ctx)
    # le plan du principal doit être la DERNIÈRE phrase
    lead_plan = fl.fragments_for(lead, "popular", ctx)["plan"]
    # la clause de plan (minuscule) apparaît capitalisée en fin de texte
    check(lead_plan[1:] in res["text"], f"le plan du principal doit finir le paragraphe -> {res['text']!r}")


def test_strong_relation_woven_inline():
    # STRATEGIC (advantage) + PAWN_STRUCTURE (structure) = CAUSE -> inline ", car ".
    cands = [
        brick(STRATEGIC_ADVANTAGE, 300, material_imbalance_kind=None, simplification_advice="simplify"),
        brick(PAWN_STRUCTURE, 1.0, pawn_weakness_square=chess.C6, pawn_weakness_kind="isolated"),
    ]
    lead, supports = ts.select_lead_and_support(cands)
    res = nw.weave(lead, supports, "popular", FragmentContext(eval_cp=300))
    check("car" in res["text"], f"relation CAUSE -> connecteur 'car' inline attendu -> {res['text']!r}")
    check("c6" in res["text"], f"la donnée réelle (c6) doit survivre au tissage -> {res['text']!r}")


def test_neutral_relation_becomes_separate_sentence():
    # Paire RÉELLEMENT neutre : ATTACK (famille king) principal + TACTICAL
    # (famille tactics) secondaire. (king, tactics) n'est PAS dans _RELATIONS
    # -> NEUTRAL (seul (tactics, king) y figure, pas l'inverse). Les deux sont
    # tier strong, donc scores > plancher 120 ; ATTACK garde une strength un
    # peu plus haute pour rester principal.
    cands = [
        brick(ATTACK, 360, king_square=chess.G8),
        brick(TACTICAL, 300),
    ]
    lead, supports = ts.select_lead_and_support(cands)
    check(lead.theme == ATTACK, f"principal attendu ATTACK -> {lead.theme}")
    check(supports and supports[0].theme == TACTICAL, f"secondaire attendu TACTICAL -> {supports}")
    res = nw.weave(lead, supports, "popular", FragmentContext(eval_cp=360))
    # relation neutre -> le secondaire est introduit par un starter de PHRASE
    # (pas tissé inline dans la phrase du principal).
    starters = tuple(nw._SENTENCE_CONNECTORS.values())
    check(any(s in res["text"] for s in starters),
          f"secondaire neutre -> phrase à part attendue -> {res['text']!r}")


def test_caution_not_woven_but_returned():
    lead, supports = ts.select_lead_and_support([brick(ENDGAME, 500, passed_pawn_square=chess.D6)])
    res = nw.weave(lead, supports, "classical", FragmentContext(eval_cp=600),
                   caution_text="Attention au pat.")
    check(res["caution"] == "Attention au pat.", "le caution doit être renvoyé tel quel")
    check("pat" not in res["text"].lower(), f"le caution ne doit PAS être tissé dans le texte -> {res['text']!r}")


def test_missed_san_survives_weaving():
    lead, supports = ts.select_lead_and_support(
        [brick(MISSED_OPPORTUNITY, 90, swing_cp=90, opponent_better_move_san="Qh5")])
    res = nw.weave(lead, supports, "creative", FragmentContext(eval_cp=40))
    check("Qh5" in res["text"], f"le SAN réel doit survivre au tissage -> {res['text']!r}")


def test_all_voices_produce_text():
    cands = [
        brick(ATTACK, 300, king_square=chess.G8),
        brick(PIECE_ACTIVITY_GAP, 2.0, activity_ratio=2.0),  # ratio 2.0 -> score ~154 > plancher 120
    ]
    lead, supports = ts.select_lead_and_support(cands)
    check(supports and supports[0].theme == PIECE_ACTIVITY_GAP, f"secondaire ACTIVITY attendu -> {supports}")
    for voice in ("popular", "creative", "classical"):
        res = nw.weave(lead, supports, voice, FragmentContext(eval_cp=300))
        check(res["text"] and res["text"].endswith("."), f"{voice}: texte invalide -> {res['text']!r}")
        # ATTACK(king) + ACTIVITY = MEANS -> connecteur "heureusement" inline
        check("heureusement" in res["text"].lower(),
              f"{voice}: relation MEANS attendue tissée inline -> {res['text']!r}")


def test_lead_none_safe():
    res = nw.weave(None, [], "popular")
    check(res["text"] == "", "lead None -> texte vide sans exception")


def test_two_supports_max_flow():
    # principal + 2 secondaires de familles distinctes : paragraphe <= 4 phrases.
    cands = [
        brick(STRATEGIC_ADVANTAGE, 300, material_imbalance_kind=None, simplification_advice="simplify"),
        brick(PAWN_STRUCTURE, 1.0, pawn_weakness_square=chess.C6, pawn_weakness_kind="isolated"),
        brick(PIECE_ACTIVITY_GAP, 2.0, activity_ratio=2.0),  # score ~154 > plancher 120
    ]
    lead, supports = ts.select_lead_and_support(cands, max_supports=2)
    res = nw.weave(lead, supports, "classical", FragmentContext(eval_cp=300))
    n = _n_sentences(res["text"])
    check(2 <= n <= 4, f"paragraphe attendu 2..4 phrases, obtenu {n} -> {res['text']!r}")


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        try:
            t()
            print(f"  ok  {t.__name__}")
        except Exception as e:
            _failures.append(f"{t.__name__} a levé {type(e).__name__}: {e}")
            print(f" ERR {t.__name__}: {e}")
    print(f"\n{len(tests)} tests executes")
    if _failures:
        print(f"[ECHEC] {len(_failures)} probleme(s) :")
        for f in _failures:
            print(f"   - {f}")
        sys.exit(1)
    print("[OK] narration_weaver tisse un paragraphe coherent")


if __name__ == "__main__":
    _run()
