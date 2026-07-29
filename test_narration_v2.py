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


def test_coup_calme_est_raconte_pas_la_position():
    # Coup calme (développement de fou, non forçant) : le paragraphe doit
    # citer la case d'arrivée du coup affiché (c4), pas uniquement le thème
    # de position -- c'est le bug corrigé par la tâche 6 (branche
    # `if intent.forcing` supprimée dans render()).
    board = chess.Board("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 1")
    candidates = [{
        "move_uci": "f1c4", "move_san": "Bc4", "cp": 30, "eval_loss": 0,
        "score": "+0.30", "pv_uci": ["f1c4"], "pv_san": ["Bc4"],
        "is_capture": False, "is_check": False, "is_castle": False,
        "is_king_move": False, "is_developing_minor": True,
        "is_pawn_center_push": False, "to_square_central": False,
        "win_prob": None, "moving_piece_value": 3, "captured_piece_value": None,
    }]
    out = nv2.narrate(board, candidates, "popular", chosen=candidates[0])
    check("c4" in out["text"],
          f"un coup calme doit citer sa case d'arrivee, obtenu {out['text']!r}")


def test_pas_deux_fois_le_meme_texte_de_suite():
    # Audit : 3 coups DEVELOP de suite sortaient le meme paragraphe mot pour
    # mot. recent_kinds doit faire devier la formulation quand le kind
    # courant a deja ete servi recemment pour ce profil.
    def cand(uci, san, developing):
        return [{
            "move_uci": uci, "move_san": san, "cp": 20, "eval_loss": 0,
            "score": "+0.20", "pv_uci": [uci], "pv_san": [san],
            "is_capture": False, "is_check": False, "is_castle": False,
            "is_king_move": False, "is_developing_minor": developing,
            "is_pawn_center_push": False, "to_square_central": False,
            "win_prob": None, "moving_piece_value": 3, "captured_piece_value": None,
        }]
    b1 = chess.Board("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 1")
    c1 = cand("f1c4", "Bc4", True)
    t1 = nv2.narrate(b1, c1, "popular", chosen=c1[0])["text"]

    b2 = chess.Board("rnbqkb1r/pppp1ppp/5n2/4p3/2B1P3/8/PPPP1PPP/RNBQK1NR w KQkq - 0 1")
    c2 = cand("g1f3", "Nf3", True)
    out2 = nv2.narrate(b2, c2, "popular", chosen=c2[0],
                        recent_kinds=["develop"])
    check(out2["text"] != t1,
          f"deux DEVELOP consecutifs ne doivent pas donner le meme texte : {t1!r}")


# --- Seuil d'explication ----------------------------------------------------
# La cause n'est rendue que quand le coup NE S'IMPOSE PAS de lui-meme. C'est ce
# qui distingue un commentaire explicatif d'un bavardage : sans ce garde-fou,
# une explication s'ajoute a chaque coup et le texte redevient du bruit.

def _render_avec_ecart(second_loss, prophylaxis=None):
    board = chess.Board("rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 0 1")
    move = board.parse_san("Bc4")
    chosen = {
        "move_uci": move.uci(), "move_san": "Bc4", "cp": 30, "eval_loss": 0,
        "score": "+0.30", "pv_uci": [move.uci()], "pv_san": ["Bc4"],
        "is_capture": False, "is_check": False, "is_castle": False,
        "is_king_move": False, "is_developing_minor": True,
        "is_pawn_center_push": False, "to_square_central": False,
        "win_prob": None, "moving_piece_value": 3, "captured_piece_value": None,
    }
    cands = [chosen, {"move_uci": "b1c3", "move_san": "Nc3", "cp": 30 - second_loss,
                      "eval_loss": second_loss, "pv_uci": ["b1c3"],
                      "is_check": False, "is_capture": False}]
    sel = nv2.build_selection(board, cands)
    return nv2.render(sel, "popular", chosen=chosen, board=board,
                      prophylaxis=prophylaxis)


def test_seuil_ecart_faible_explique():
    proph = {"san": "d5", "reason": "blocked"}
    out = _render_avec_ecart(10, prophylaxis=proph)
    check("d5" in out["text"], out["text"])


def test_seuil_ecart_large_nexplique_pas():
    proph = {"san": "d5", "reason": "blocked"}
    out = _render_avec_ecart(400, prophylaxis=proph)
    check("d5" not in out["text"], out["text"])
    check(bool(out["text"]), "le paragraphe doit rester complet, seule la cause tombe")


def _cand(board, uci, cp, loss, pv=None):
    """Candidat complet (les champs que collect_theme_bricks lit vraiment)."""
    mv = chess.Move.from_uci(uci)
    after = board.copy()
    after.push(mv)
    return {"move_uci": uci, "move_san": board.san(mv), "cp": cp, "eval_loss": loss,
            "pv_uci": pv or [uci], "is_check": after.is_check(),
            "is_capture": board.is_capture(mv), "is_castle": board.is_castling(mv)}


def _texte(fen, uci, cp, loss2, pv=None, alt="g1f1"):
    board = chess.Board(fen)
    top = _cand(board, uci, cp, 0, pv)
    sel = nv2.build_selection(board, [top, _cand(board, alt, cp - loss2, loss2)])
    return nv2.render(sel, "popular", chosen=top, board=board)["text"]


# --- Le GAIN : ce que le coup rapporte, quand le POURQUOI ne sert plus -----
def test_ecart_faible_explique_ecart_large_chiffre():
    """Les deux moitiés du même seuil : écart faible -> le lecteur a besoin du
    pourquoi ; écart large -> le coup se détache seul, il a besoin du combien.
    Jamais les deux, sinon le paragraphe enfle."""
    fen = "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 1"
    petit = _texte(fen, "e1g1", 30, 20, alt="d2d3")
    grand = _texte(fen, "e1g1", 30, 320, alt="d2d3")
    check("laissent filer" not in petit,
          f"écart faible : pas de chiffrage du gain -> {petit!r}")
    check("laissent filer au moins l'équivalent d'une pièce" in grand,
          f"écart large : le gain doit être chiffré en matériel -> {grand!r}")


def test_gain_materiel_de_ligne_prioritaire():
    """Coup CALME qui ramasse une tour trois demi-coups plus loin : c'est le
    matériel qu'on annonce, pas l'écart d'éval (plus concret)."""
    txt = _texte("3r2k1/pp3ppp/8/8/8/8/PP3PPP/R5K1 w - - 0 1", "a1d1", 300, 200,
                 pv=["a1d1", "g8h8", "d1d8"])
    check("tu ressors avec l'équivalent d'une tour de plus" in txt,
          f"le gain matériel de la ligne doit être dit -> {txt!r}")


def test_gain_materiel_faux_par_horizon_nest_pas_annonce():
    """Même ligne, mais la dame noire en c7 reprend en d8 juste au-delà de la
    PV fournie. Le bilan positif n'est qu'un artefact de troncature : on ne
    promet pas une tour. Symétrique exact des 50 faux sacrifices.

    La dame est en c7 et NON en d7 : en d7 elle bloquerait la colonne, Txd8
    serait illégal, la ligne s'arrêterait avant la prise et le test passerait
    au vert sans rien tester (vérifié -- première version de ce test)."""
    txt = _texte("3r2k1/ppq2ppp/8/8/8/8/PP3PPP/R5K1 w - - 0 1", "a1d1", 300, 200,
                 pv=["a1d1", "g8h8", "d1d8"])
    check("tu ressors" not in txt,
          f"aucun gain matériel promis quand la reprise est hors horizon -> {txt!r}")


def test_mat_ne_chiffre_pas_un_gain_absurde():
    """eval_loss vaut best_cp - cp et un mat est encodé ~99996 : sans
    exclusion, le coach annonçait « au moins l'équivalent d'une dame » sur un
    écart de 990 pions."""
    txt = _texte("6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1", "a1a8", 99999, 99000)
    check("laissent filer" not in txt, f"pas de chiffrage sur un mat -> {txt!r}")
    check("mat" in txt.lower(), f"le mat doit rester le sujet -> {txt!r}")


def test_gap_cp_lu_sur_le_second_candidat():
    board = chess.Board()
    cands = [{"move_uci": "e2e4", "move_san": "e4", "cp": 30, "eval_loss": 0},
             {"move_uci": "d2d4", "move_san": "d4", "cp": -20, "eval_loss": 50}]
    check(nv2.build_selection(board, cands).gap_cp == 50, "gap_cp doit lire eval_loss du 2e candidat")


def test_gap_cp_vaut_zero_sans_second_candidat():
    cands = [{"move_uci": "e2e4", "move_san": "e4", "cp": 30, "eval_loss": 0}]
    check(nv2.build_selection(chess.Board(), cands).gap_cp == 0, "gap_cp doit valoir 0 sans 2e candidat")


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
