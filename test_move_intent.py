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


# --- 4bis. MATE prime sur GIVES_CHECK --------------------------------------
def test_mate_prime_sur_tout():
    # Mat du couloir : Td8# -- c'est AUSSI un echec, la priorite doit
    # neanmoins ressortir MATE, jamais gives_check.
    board = chess.Board("6k1/5ppp/8/8/8/8/8/3R2K1 w - - 0 1")
    intent = mi.detect_move_intent(board, chosen("d1d8"))
    check(intent is not None, "un coup de mat doit produire un intent")
    check(intent.kind == mi.MATE,
          f"Td8# doit etre MATE, obtenu {intent.kind}")
    check(intent.forcing is True, "le mat est forcement forcant")


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


# --- 7bis. tags : l'échec d'une prise n'est plus perdu ---------------------
def test_prise_qui_donne_echec_porte_le_tag():
    # Fou blanc prend en b5 AVEC echec (roi noir en e8, diagonale a4-e8).
    board = chess.Board("4k3/8/8/1p6/8/8/8/4K2B w - - 0 1")
    board.set_piece_at(chess.A4, chess.Piece(chess.BISHOP, chess.WHITE))
    board.remove_piece_at(chess.H1)
    intent = mi.detect_move_intent(
        board, chosen("a4b5"))
    check(intent.kind == mi.CAPTURE_FREE,
          f"reste classe comme une prise, obtenu {intent.kind}")
    check("gives_check" in intent.tags,
          "l'echec ne doit pas etre perdu : tag gives_check attendu")


# --- 7ter. Intentions calmes : les coups tranquilles enfin classés ---------
def test_intentions_calmes():
    # Roque : le drapeau is_castle est déjà posé par engine_analysis, mais
    # detect_move_intent doit rester correct même sans lui (board fait foi).
    b = chess.Board("rnbqk2r/pppp1ppp/5n2/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 1")
    i = mi.detect_move_intent(b, chosen("e1g1"))
    check(i.kind == mi.CASTLE, f"O-O doit etre CASTLE, obtenu {i.kind}")

    # Développement : fou quittant la rangée de fond, sans capture.
    b = chess.Board("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 1")
    i = mi.detect_move_intent(b, chosen("f1c4"))
    check(i.kind == mi.DEVELOP, f"Fc4 doit etre DEVELOP, obtenu {i.kind}")

    # Tour sur colonne ouverte (colonne d vide de pions).
    b = chess.Board("4k3/ppp2ppp/8/8/8/8/PPP2PPP/3RK3 w - - 0 1")
    i = mi.detect_move_intent(b, chosen("d1d5"))
    check(i.kind == mi.ROOK_FILE, f"Td5 doit etre ROOK_FILE, obtenu {i.kind}")

    # Aucune de ces catégories -> QUIET résiduel (poussée de pion sur l'aile).
    b = chess.Board("4k3/ppp2ppp/8/8/8/8/PPP2PPP/4K3 w - - 0 1")
    i = mi.detect_move_intent(b, chosen("a2a3"))
    check(i.kind == mi.QUIET, f"a3 doit rester QUIET, obtenu {i.kind}")

    for kind in (mi.DEVELOP, mi.CASTLE, mi.ROOK_FILE, mi.REPOSITION):
        check(kind not in mi.FORCING_KINDS,
              f"{kind} est calme : ne doit PAS primer sur une tactique")


def test_statut_de_colonne_reporte():
    # Le statut de la colonne était calculé puis JETÉ : les fragments écrivaient
    # « colonne ouverte » même sur une colonne semi-ouverte. On le reporte.
    ouverte = chess.Board("4k3/ppp2ppp/8/8/8/8/PPP2PPP/3RK3 w - - 0 1")
    semi = chess.Board("4k3/ppp2ppp/3p4/8/8/8/PPP2PPP/3RK3 w - - 0 1")  # pion NOIR en d6
    i_ouverte = mi.detect_move_intent(ouverte, chosen("d1d5"))
    i_semi = mi.detect_move_intent(semi, chosen("d1d5"))
    check(i_ouverte.file_status == "open",
          f"colonne d vide des 2 camps -> 'open', obtenu {i_ouverte.file_status!r}")
    check(i_semi.kind == mi.ROOK_FILE and i_semi.file_status == "half_open",
          f"colonne d avec un pion adverse -> 'half_open', obtenu {i_semi.file_status!r}")


def test_capture_is_free_nomme_sa_preuve():
    # UNE SEULE SOURCE DE VÉRITÉ : _capture_is_free retourne le NOM de la preuve
    # et detect_move_intent le lit, au lieu de recopier ses conditions (une copie
    # qui divergeait aurait fait écrire « sans reprise » sur une prise reprenable).
    board = chess.Board("8/r7/8/8/4k3/8/4K3/R7 w - - 0 1")  # tour a7 non défendue
    move = chess.Move.from_uci("a1a7")
    check(mi._capture_is_free(board, move, ["a1a7"], 5, None) == "undefended",
          "case sans défenseur -> preuve 'undefended'")
    intent = mi.detect_move_intent(board, chosen("a1a7"))
    check(intent.capture_undefended is True and intent.capture_line_gain is False,
          "l'intent doit refléter EXACTEMENT la preuve retenue")

    defendue = chess.Board("8/8/4k3/3q4/8/8/8/3R2K1 w - - 0 1")  # d5 défendue par le roi
    d1d5 = chess.Move.from_uci("d1d5")
    check(mi._capture_is_free(defendue, d1d5, ["d1d5", "e6d5"], 4, None) == "line_gain",
          "case défendue mais bilan de ligne positif -> preuve 'line_gain'")
    check(mi._capture_is_free(defendue, d1d5, ["d1d5"], 9, None) is None,
          "PV sans reprise adverse et aucun motif -> AUCUNE preuve (None)")
    check(mi._capture_is_free(defendue, d1d5, ["d1d5"], 9, "fork") == "motif",
          "confirmation par why_motif seul -> preuve 'motif' (aucun gain recalculé)")


def test_repositionnement():
    # Cavalier déjà développé (pas sur la rangée de fond) qui change de poste,
    # sans capture -> REPOSITION.
    b = chess.Board("4k3/8/8/8/8/2N5/8/4K3 w - - 0 1")
    i = mi.detect_move_intent(b, chosen("c3d5"))
    check(i.kind == mi.REPOSITION, f"Cd5 doit etre REPOSITION, obtenu {i.kind}")
    check(not i.forcing, "reposition: non forçant")


def test_castle_qui_donne_echec_reste_gives_check():
    # Roque qui délivre échec au roi adverse (tour arrivant sur la colonne f,
    # roi noir en f8) : la priorité forçante GIVES_CHECK doit primer sur la
    # catégorie calme CASTLE.
    b = chess.Board("5k2/8/8/8/8/8/8/4K2R w K - 0 1")
    i = mi.detect_move_intent(b, chosen("e1g1"))
    check(i.kind == mi.GIVES_CHECK,
          f"roque + echec doit rester GIVES_CHECK, obtenu {i.kind}")
    check(i.forcing, "roque + echec : forçant")


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
               test_sacrifice, test_promotion, test_mate_prime_sur_tout,
               test_gives_check, test_quiet, test_malformed,
               test_prise_qui_donne_echec_porte_le_tag,
               test_intentions_calmes, test_statut_de_colonne_reporte,
               test_capture_is_free_nomme_sa_preuve, test_repositionnement,
               test_castle_qui_donne_echec_reste_gives_check,
               test_coherence_gate, test_render_forcing_end_to_end):
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
