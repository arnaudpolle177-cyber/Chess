"""
test_move_intent.py
Tests du détecteur d'intention de coup (move_intent.py) et de son câblage dans
la narration v2 (porte de cohérence géométrique -- narration_v2.render). Sans
moteur : candidats fabriqués à la main, positions FEN réelles.

Vérifie les incohérences observées en pratique et corrigées ici :
  - roi en échec -> CHECK_ESCAPE (jamais un commentaire de structure de pions) ;
  - prise nette / sacrifice / promotion / échec -> intent forçant correct ;
  - coup calme -> QUIET, non forçant (le thème de position reste maître) ;
  - porte de cohérence : le thème positionnel n'est gardé QUE s'il touche la
    zone du coup (prise sur la case du pion faible = gardé ; fuite d'échec
    hors-sujet = abandonné).
"""
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import chess

import move_intent as mi
import narration_v2 as nv2
import theme_detector as td


_failures = []


def check(cond, msg):
    if not cond:
        _failures.append(msg)


def chosen(move_uci, pv_uci=None):
    """Candidat minimal au format attendu par detect_move_intent."""
    return {"move_uci": move_uci, "pv_uci": pv_uci or [move_uci]}


# --- 1. CHECK_ESCAPE : roi en échec, on le met à l'abri --------------------
def test_check_escape():
    # Roi blanc en e1, échec par la tour noire sur la colonne e ; le roi peut
    # légalement s'écarter en f1 (hors de la colonne, non attaqué par la tour).
    board = chess.Board("k3r3/8/8/8/8/8/8/4K3 w - - 0 1")
    check(board.is_check(), "setup: le roi blanc doit être en échec")
    intent = mi.detect_move_intent(board, chosen("e1f1"))
    check(intent is not None, "check_escape: intent non None")
    check(intent.kind == mi.CHECK_ESCAPE, f"check_escape: kind={intent.kind}")
    check(intent.forcing, "check_escape: doit être forçant")
    check(intent.moved_piece == chess.KING, "check_escape: pièce jouée = roi")


# --- 2. CAPTURE_FREE : prise d'une pièce non défendue ----------------------
def test_capture_free():
    # Tour blanche a1 prend une tour noire non défendue en a7.
    board = chess.Board("8/r7/8/8/4k3/8/4K3/R7 w - - 0 1")
    intent = mi.detect_move_intent(board, chosen("a1a7"), why_motif="undefended")
    check(intent is not None and intent.kind == mi.CAPTURE_FREE,
          f"capture_free: kind={intent.kind if intent else None}")
    check(intent.forcing, "capture_free: forçant")
    check(intent.captured_piece == chess.ROOK, "capture_free: prend une tour")
    check(intent.material_delta > 0, "capture_free: gain matériel positif")


# --- 2bis. CAPTURE_FREE sur prise DÉFENDUE mais gagnante (régression) -------
def test_capture_free_defended_but_winning():
    # Tour d1 prend la dame d5 ; le roi e6 défend d5 (donc la case EST attaquée
    # -> l'ancienne logique "undefended" échouait), mais Rxd5 Kxd5 laisse +4
    # (dame 9 - tour 5). Doit sortir CAPTURE_FREE via le bilan de ligne, sans
    # dépendre de l'étiquette why_detector (qui aurait pu dire "material_gain").
    board = chess.Board("8/8/4k3/3q4/8/8/8/3R2K1 w - - 0 1")
    intent = mi.detect_move_intent(board, chosen("d1d5", pv_uci=["d1d5", "e6d5"]))
    check(intent is not None and intent.kind == mi.CAPTURE_FREE,
          f"capture_free défendue: kind={intent.kind if intent else None}")
    check(intent.forcing, "capture_free défendue: forçant")

    # Même prise mais SANS why_motif ni reprise dans la PV, et case défendue :
    # on ne doit PAS conclure au gain (line_delta ne verrait que ma prise) ->
    # simple échange. Garde-fou contre le faux positif décrit dans _capture_is_free.
    intent_short = mi.detect_move_intent(board, chosen("d1d5"))  # pv = [d1d5] seul
    check(intent_short is not None and intent_short.kind == mi.CAPTURE_TRADE,
          f"capture défendue sans reprise dans la PV: attendu trade, kind={intent_short.kind if intent_short else None}")


# --- 3. SACRIFICE : déficit matériel sur la ligne, mais coup recommandé ----
def test_sacrifice():
    # Fou blanc d3 prend le pion h7 (Bxh7), le roi reprend (Kxh7) : on donne un
    # fou (3) pour un pion (1) -> déficit net de 2 sur la ligne.
    board = chess.Board("rnbqkbnr/pppppp1p/8/8/8/3B4/PPPPPPPP/RNBQK1NR w KQkq - 0 1")
    intent = mi.detect_move_intent(board, chosen("d3h7", pv_uci=["d3h7", "h8h7"]))
    check(intent is not None and intent.kind == mi.SACRIFICE,
          f"sacrifice: kind={intent.kind if intent else None}")
    check(intent.forcing, "sacrifice: forçant")
    check(intent.material_delta < 0, f"sacrifice: material_delta négatif (={intent.material_delta})")


# --- 3bis. PAS un sacrifice : reprise coupée par l'horizon de la PV ---------
def test_not_sacrifice_recapture_beyond_horizon():
    # Échange parfaitement égal dont la PV s'arrête PILE sur la prise adverse :
    # Re1xe5 (prend le cavalier, +3), Re8xe5 (reprend ma tour, net -2). MA
    # reprise du pion d4 (dxe5, +5 -> net +3) tombe juste au-delà des 2 demi-
    # coups fournis. Sans la garde d'horizon, le bilan -2 faisait ressortir un
    # faux "sacrifice" -- exactement le symptôme observé en partie. Le pion d4
    # défend e5, donc la reprise coupée est bien réelle : intent NON sacrifice.
    board = chess.Board("k3r3/8/8/4n3/3P4/8/8/K3R3 w - - 0 1")
    intent = mi.detect_move_intent(board, chosen("e1e5", pv_uci=["e1e5", "e8e5"]))
    check(intent is not None and intent.kind != mi.SACRIFICE,
          f"horizon: ne doit PAS être un sacrifice (kind={intent.kind if intent else None})")


# --- 4. PROMOTION ----------------------------------------------------------
def test_promotion():
    board = chess.Board("8/P7/8/4k3/8/8/6K1/8 w - - 0 1")
    intent = mi.detect_move_intent(board, chosen("a7a8q"))
    check(intent is not None and intent.kind == mi.PROMOTION,
          f"promotion: kind={intent.kind if intent else None}")
    check(intent.forcing, "promotion: forçant")


# --- 5. GIVES_CHECK (sans prise nette) -------------------------------------
def test_gives_check():
    # Dame blanche d1 -> d8 donne échec au roi noir e8 (rien à prendre en d8).
    board = chess.Board("4k3/8/8/8/8/8/8/3QK3 w - - 0 1")
    intent = mi.detect_move_intent(board, chosen("d1d8"))
    check(intent is not None, "gives_check: intent non None")
    check(intent.gives_check, "gives_check: le coup donne bien échec")
    check(intent.kind == mi.GIVES_CHECK, f"gives_check: kind={intent.kind}")
    check(intent.forcing, "gives_check: forçant")


# --- 6. QUIET : coup calme, non forçant ------------------------------------
def test_quiet():
    board = chess.Board(chess.STARTING_FEN)
    intent = mi.detect_move_intent(board, chosen("e2e4"))
    check(intent is not None and intent.kind == mi.QUIET,
          f"quiet: kind={intent.kind if intent else None}")
    check(not intent.forcing, "quiet: NON forçant")


# --- 7. chosen mal formé -> None (jamais d'exception) ----------------------
def test_malformed():
    board = chess.Board(chess.STARTING_FEN)
    check(mi.detect_move_intent(board, None) is None, "malformed: chosen None -> None")
    check(mi.detect_move_intent(board, {"move_uci": "zzzz"}) is None, "malformed: uci illisible -> None")
    check(mi.detect_move_intent(board, chosen("e7e5")) is None, "malformed: coup illégal (mauvais camp) -> None")


# --- 8. Porte de cohérence géométrique (narration_v2) ----------------------
def _pawn_structure_brick(weak_square):
    return td.ThemeCandidate(
        td.PAWN_STRUCTURE, 1.0,
        {"pawn_weakness_square": weak_square, "pawn_weakness_kind": "isolated",
         "_tier": td.theme_tier(td.PAWN_STRUCTURE), "_family": td.theme_family(td.PAWN_STRUCTURE)},
    )


def test_coherence_gate():
    # Prise SUR la case du pion faible -> cohérent (thème gardé).
    e5 = chess.E5
    intent_on_weak = mi.MoveIntent(
        kind=mi.CAPTURE_FREE, forcing=True, from_square=chess.D3, to_square=e5,
        moved_piece=chess.BISHOP, captured_piece=chess.PAWN, material_delta=1,
    )
    brick = _pawn_structure_brick(e5)
    check(nv2._intent_is_coherent_with_theme(intent_on_weak, brick),
          "cohérence: prise sur la case du pion faible -> gardé")

    # Fuite d'échec à l'autre bout -> hors-sujet (thème abandonné).
    intent_far = mi.MoveIntent(
        kind=mi.CHECK_ESCAPE, forcing=True, from_square=chess.G1, to_square=chess.H1,
        moved_piece=chess.KING,
    )
    check(not nv2._intent_is_coherent_with_theme(intent_far, brick),
          "cohérence: fuite d'échec loin du pion faible -> abandonné")

    # Thème sans case-clé -> non cohérent avec un coup forçant.
    diffuse = td.ThemeCandidate(td.STRATEGIC_ADVANTAGE, 1.0,
                                {"_tier": td.theme_tier(td.STRATEGIC_ADVANTAGE),
                                 "_family": td.theme_family(td.STRATEGIC_ADVANTAGE)})
    check(not nv2._intent_is_coherent_with_theme(intent_on_weak, diffuse),
          "cohérence: thème diffus sans case-clé -> non gardé")


# --- 9. Bout-en-bout render : coup forçant -> texte centré sur le coup ------
def test_render_forcing_end_to_end():
    board = chess.Board("k3r3/8/8/8/8/8/8/4K3 w - - 0 1")  # roi blanc en échec (tour sur colonne e)
    candidates = [
        {"cp": -50, "eval_loss": 0, "is_check": False, "is_capture": False,
         "move_uci": "e1f1", "move_san": "Kf1"},
        {"cp": -80, "eval_loss": 30, "is_check": False, "is_capture": False,
         "move_uci": "e1d1", "move_san": "Kd1"},
    ]
    selection = nv2.build_selection(board, candidates)
    for profile in ("popular", "creative", "classical"):
        woven = nv2.render(selection, profile, chosen=candidates[0], board=board)
        check(bool(woven.get("text")), f"render forçant ({profile}): texte non vide")
        # Le texte doit parler du ROI (échec), jamais rester sur la structure.
        check("roi" in woven["text"].lower(),
              f"render forçant ({profile}): doit mentionner le roi -> {woven['text']!r}")


def main():
    for fn in (test_check_escape, test_capture_free, test_capture_free_defended_but_winning,
               test_sacrifice, test_promotion,
               test_gives_check, test_quiet, test_malformed, test_coherence_gate,
               test_render_forcing_end_to_end):
        try:
            fn()
        except Exception as e:
            _failures.append(f"{fn.__name__} a levé une exception : {e!r}")

    if _failures:
        print(f"ÉCHEC ({len(_failures)}) :")
        for f in _failures:
            print("  -", f)
        sys.exit(1)
    print("test_move_intent : OK")


if __name__ == "__main__":
    main()
