"""
test_narration_v2.py
Test d'INTÉGRATION de bout en bout du pipeline narration v2
(narration_v2.py) sur de VRAIES positions (chess.Board) -- sans moteur
Stockfish (candidats fabriqués à la main au format engine_analysis).

Ce test est le garde-fou de l'assemblage complet :
    collect_theme_bricks -> score/select -> fragments -> weave

Garanties :
  - sur un échantillon de positions x signaux, le pipeline ne lève JAMAIS
    d'exception et produit toujours un paragraphe non vide, ponctué ;
  - la SÉLECTION est profil-indépendante : build_selection une fois, les 3
    profils rendus dessus partagent le même principal + secondaires (base du
    cache de l'étape 6) ;
  - les 3 voix produisent des textes cohérents (et généralement distincts) ;
  - narrate() (raccourci) == build_selection()+render() (cohérence API) ;
  - le principal choisi par le pipeline correspond bien à la brique la plus
    prioritaire (cohérence avec detect_theme via le test miroir déjà en place).
"""
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import chess

import narration_v2 as nv2
import theme_detector as td


_failures = []


def check(cond, msg):
    if not cond:
        _failures.append(msg)


def make_candidates(cp, second_eval_loss=0, is_check=False, is_capture=False, n=3):
    top = {"cp": cp, "eval_loss": 0, "is_check": is_check, "is_capture": is_capture,
           "move_uci": "e2e4", "move_san": "e4"}
    second = {"cp": (cp - second_eval_loss) if cp is not None else None,
              "eval_loss": second_eval_loss, "is_check": False, "is_capture": False,
              "move_uci": "d2d4", "move_san": "d4"}
    rest = [dict(second) for _ in range(max(0, n - 2))]
    return [top, second] + rest


FENS = {
    "start": chess.STARTING_FEN,
    "italian": "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 0 1",
    "midgame_open": "r2q1rk1/pp2bppp/2n1bn2/2pp4/3P4/2N1PN2/PPQ1BPPP/R1B2RK1 w - - 0 1",
    "endgame_KP": "8/8/8/4k3/8/4K3/4P3/8 w - - 0 1",
    "endgame_passed": "8/2P5/8/4k3/8/8/6K1/8 w - - 0 1",
    "king_uncastled": "r1bqk2r/pppp1ppp/2n2n2/2b1p3/2B1P3/2NP1N2/PPP2PPP/R1BQK2R w KQkq - 0 1",
}

SIGNALS = [
    (0, None, None, 0, False, False),
    (300, None, None, 0, False, False),
    (-300, None, None, 0, False, False),
    (120, None, None, 150, True, False),
    (200, 250, None, 0, False, False),
    (150, None, 60, 0, False, False),
    (-150, None, -60, 0, False, False),
    (90, None, None, 0, False, False),
    (None, None, None, 0, False, False),
]

VOICES = ("popular", "creative", "classical")


def test_no_crash_and_nonempty_paragraph():
    for name, fen in FENS.items():
        board = chess.Board(fen)
        for (cp, swing, init, sel, chk, cap) in SIGNALS:
            cands = make_candidates(cp, second_eval_loss=sel, is_check=chk, is_capture=cap)
            for voice in VOICES:
                try:
                    res = nv2.narrate(board, cands, voice, swing_cp=swing,
                                      initiative_trend=init, opponent_better_move_san="Qh5")
                except Exception as e:
                    _failures.append(f"[{name}/cp={cp}/{voice}] EXCEPTION {type(e).__name__}: {e}")
                    continue
                text = res.get("text", "")
                check(bool(text) and text.endswith("."),
                      f"[{name}/cp={cp}/{voice}] paragraphe invalide -> {text!r}")
                # 2 à 4 phrases. On compte les séparateurs ". " (point + espace) :
                # robuste aux décimales comme "3.0 pions" (point suivi d'un
                # chiffre, jamais d'un espace -> non compté).
                n_sent = text.count(". ") + 1
                check(2 <= n_sent <= 4,
                      f"[{name}/cp={cp}/{voice}] {n_sent} phrases (attendu 2..4) -> {text!r}")


def test_selection_is_profile_independent():
    board = chess.Board(FENS["midgame_open"])
    cands = make_candidates(300)
    selection = nv2.build_selection(board, cands, initiative_trend=None)
    lead_theme = selection.lead.theme
    support_themes = [s.theme for s in selection.supports]
    for voice in VOICES:
        res = nv2.render(selection, voice, board=board)
        check(res["lead"] == lead_theme,
              f"{voice}: principal doit être partagé -> {res['lead']} vs {lead_theme}")
        check(res["supports"] == support_themes,
              f"{voice}: secondaires doivent être partagés -> {res['supports']} vs {support_themes}")


def test_voices_generally_differ():
    board = chess.Board(FENS["italian"])
    cands = make_candidates(120, second_eval_loss=150, is_check=True)
    selection = nv2.build_selection(board, cands)
    texts = {v: nv2.render(selection, v, board=board)["text"] for v in VOICES}
    # au moins 2 formulations distinctes parmi les 3 (les voix ne sont pas
    # censées être identiques mot pour mot)
    check(len(set(texts.values())) >= 2,
          f"les voix devraient différer -> {texts}")


def test_narrate_matches_two_step():
    board = chess.Board(FENS["king_uncastled"])
    cands = make_candidates(90)
    one_shot = nv2.narrate(board, cands, "classical")
    selection = nv2.build_selection(board, cands)
    two_step = nv2.render(selection, "classical", board=board)
    check(one_shot["text"] == two_step["text"],
          f"narrate() doit égaler build_selection()+render() -> {one_shot['text']!r} vs {two_step['text']!r}")


def test_lead_matches_priority_first():
    # Cohérence avec detect_theme : le principal du pipeline doit être la
    # brique la plus prioritaire dans PRIORITY_ORDER PARMI CELLES DU MÊME
    # TIER LE PLUS ÉLEVÉ. Comme le scoring domine par tier puis intensité,
    # le principal est la brique de plus haut tier ; en cas d'égalité de
    # tier, l'intensité tranche (pas forcément l'ordre PRIORITY_ORDER). On
    # vérifie donc juste que le principal a le tier maximal présent.
    board = chess.Board(FENS["midgame_open"])
    cands = make_candidates(300)
    selection = nv2.build_selection(board, cands)
    tiers_present = {td.theme_tier(b.theme) for b in selection.bricks}
    lead_tier = td.theme_tier(selection.lead.theme)
    weight = td.TIER_WEIGHT
    check(weight[lead_tier] == max(weight[t] for t in tiers_present),
          f"le principal doit être du tier le plus élevé présent -> lead={selection.lead.theme}({lead_tier})")


def test_require_relation_prunes_or_keeps():
    # Avec require_relation=True, on ne garde que des secondaires à relation
    # sémantique listée -> le nombre de secondaires ne peut que DIMINUER ou
    # rester égal par rapport au filtre structurel seul.
    board = chess.Board(FENS["midgame_open"])
    cands = make_candidates(300)
    loose = nv2.build_selection(board, cands, require_relation=False)
    strict = nv2.build_selection(board, cands, require_relation=True)
    check(len(strict.supports) <= len(loose.supports),
          f"require_relation ne doit jamais AJOUTER de secondaire -> {len(strict.supports)} > {len(loose.supports)}")


def test_selection_cache_roundtrip():
    board = chess.Board(FENS["midgame_open"])
    cands = make_candidates(300)
    cache = nv2.SelectionCache()
    fen = board.fen()
    check(cache.get(fen) is None, "cache vide -> None")
    sel = nv2.build_selection(board, cands)
    cache.set(fen, sel)
    check(cache.get(fen) is sel, "get doit rendre la sélection mise en cache")
    check(cache.get("autre fen") is None, "clé différente -> None (pas de faux positif)")
    # les 3 profils partagent la MÊME sélection cachée
    themes = {v: nv2.render(cache.get(fen), v, board=board)["lead"] for v in VOICES}
    check(len(set(themes.values())) == 1, f"principal partagé via cache -> {themes}")
    cache.invalidate()
    check(cache.get(fen) is None, "invalidate -> None")


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
    print("[OK] pipeline narration v2 coherent de bout en bout")


if __name__ == "__main__":
    _run()
