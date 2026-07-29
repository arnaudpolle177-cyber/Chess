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
    # La brique porte le SAN anglais de python-chess ; l'affichage le
    # francise (voir fragment_library._san_fr). Ce qui doit survivre au
    # tissage, c'est la DONNÉE de la brique -- ici sous sa forme affichée.
    check("Dh5" in res["text"], f"le SAN réel doit survivre au tissage -> {res['text']!r}")


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


def test_echec_secondaire_apparait_dans_le_texte():
    import move_intent
    board = chess.Board("4k3/8/8/1p6/B7/8/8/4K3 w - - 0 1")
    intent = move_intent.detect_move_intent(
        board, {"move_uci": "a4b5", "pv_uci": ["a4b5"]})
    out = nw.weave_intent(intent, None, "popular")
    check("échec" in out["text"].lower(),
          f"l'echec du coup doit apparaitre, texte obtenu : {out['text']!r}")


def test_la_cause_prime_sur_le_secondaire_inline_et_ne_le_perd_pas():
    """Régression : _lead_sentence préférait le thème inline, la cause du coup
    disparaissait alors du paragraphe (mesuré ~10-25% des positions qui en
    portent une). La cause passe désormais devant -- et le secondaire évincé
    doit repartir en phrase à part, pas au fond du seau."""
    lead, supports = ts.select_lead_and_support([
        brick(STRATEGIC_ADVANTAGE, 300, eval_cp=300),
        brick(PAWN_STRUCTURE, 200, weak_square=chess.D5),
    ])
    check(supports, "ce cas doit produire un secondaire (sinon il ne teste rien)")
    res = nw.weave(lead, supports, "popular", FragmentContext(eval_cp=300),
                   lead_cause="ce coup empêche Cd4")
    check("ce coup empêche Cd4" in res["text"],
          f"la cause doit survivre au secondaire -> {res['text']!r}")
    sup_obs = fl.fragments_for(supports[0], "popular",
                               FragmentContext(eval_cp=300))["observation"]
    check(sup_obs.rstrip(" .") in res["text"],
          f"le secondaire évincé de l'inline doit rester une phrase -> {res['text']!r}")


def test_cause_du_coup_calme_atteint_le_paragraphe():
    """Régression : pour un intent QUIET (pion, roi non-roquant), aucun
    fragment d'intention n'existe -> narration_v2 retombait sur le tissage de
    thème, qui n'a jamais vu le MoveIntent. La cause du coup était donc
    structurellement inatteignable sur 30-40% d'une partie."""
    import narration_v2 as nv2
    board = chess.Board("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
    cands = [{"move_uci": "e2e4", "move_san": "e4", "cp": 30, "eval_loss": 0},
             {"move_uci": "d2d4", "move_san": "d4", "cp": 20, "eval_loss": 10}]
    sel = nv2.build_selection(board, cands)
    out = nv2.render(sel, "popular", board=board, chosen=cands[0],
                     prophylaxis={"san": "Fb4", "reason": "blocked"})
    check("Fb4" in out["text"],
          f"la cause prophylactique d'un coup calme doit apparaitre -> {out['text']!r}")


def test_mode_livre_garde_la_prophylaxie_et_coupe_le_geometrique():
    """Régression : en mode livre, eval_loss est synthétique (<=40) et le
    seuil EXPLAIN_GAP_MAX_CP ne filtre plus rien. Le fait moteur (prophylaxie)
    reste, le comptage géométrique tombe."""
    import narration_v2 as nv2
    import fragment_library as flib
    # Après 1.e4 e5 : Cf3 attaque le pion e5 -> new_attack_square posé, donc
    # une cause géométrique EXISTE réellement ici (sans quoi le test ne
    # testerait rien -- vérifié en cassant la garde).
    board = chess.Board("rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2")
    chosen = {"move_uci": "g1f3", "move_san": "Nf3", "cp": None, "eval_loss": 0, "score": "Livre"}
    book = [chosen,
            {"move_uci": "f1c4", "move_san": "Bc4", "cp": None, "eval_loss": 20, "score": "Livre"}]
    engine_like = [dict(chosen, cp=30), dict(book[1], cp=10)]

    sel_engine = nv2.build_selection(board, engine_like)
    check(not sel_engine.book, "des candidats avec cp ne sont pas du livre")
    out_engine = nv2.render(sel_engine, "popular", board=board, chosen=chosen)
    check("e5" in out_engine["text"],
          f"hors livre, la cause géométrique doit sortir -> {out_engine['text']!r}")

    sel = nv2.build_selection(board, book)
    check(sel.book, "des candidats sans cp doivent être reconnus comme livre")
    out = nv2.render(sel, "popular", board=board, chosen=chosen)
    check("e5" not in out["text"],
          f"en livre, la cause géométrique doit tomber -> {out['text']!r}")
    check(bool(out["text"]), "le paragraphe doit rester complet, seule la cause tombe")

    # la prophylaxie, elle, est un fait moteur : elle survit au mode livre
    out_proph = nv2.render(sel, "popular", board=board, chosen=chosen,
                           prophylaxis={"san": "Fb4", "reason": "blocked"})
    check("Fb4" in out_proph["text"],
          f"la prophylaxie doit survivre au mode livre -> {out_proph['text']!r}")


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
