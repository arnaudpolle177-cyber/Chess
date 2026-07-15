"""
move_intent.py
Classifie CE QUE FAIT le coup recommandé (la flèche affichée), à partir de la
seule position réelle + du coup choisi -- jamais un motif inventé. C'est la
brique qui manquait : jusqu'ici la narration décrivait la POSITION (thème le
mieux scoré, partagé par les 3 profils et figé tant que la structure ne
bougeait pas), sans jamais expliquer le COUP proposé. D'où les incohérences
observées en pratique : les 3 flèches prenaient une pièce mais le commentaire
parlait encore d'un pion isolé ; le roi était en échec et le coach parlait
structure de pions.

Ici, on regarde le coup lui-même. Toutes les catégories reposent sur des
FAITS python-chess (échec avant/après, capture, valeur du matériel échangé,
promotion) et sur le motif `why` DÉJÀ calculé (why_detector.py) -- rien n'est
fabriqué. Quand aucune intention marquante ne se dégage (coup calme,
positionnel), on retourne un intent NON forçant : l'appelant garde alors le
thème de position (comportement historique préservé pour les coups tranquilles,
qui sont justement ceux où un commentaire positionnel a du sens).

Le découpage FORÇANT / NON FORÇANT porte la décision de l'appelant
(narration_v2.render) : un coup forçant PREND LA MAIN sur le thème de
position (on raconte le coup, pas la structure), un coup calme non.
"""
from dataclasses import dataclass
from typing import Optional

import chess

# Valeurs standard -- mêmes que why_detector / variation_narrator (source
# recopiée volontairement : ces trois modules doivent rester autonomes et
# testables sans s'importer mutuellement pour une simple table de constantes).
PIECE_VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}

# --- Catégories d'intention -------------------------------------------
# FORÇANTES (le coup fait quelque chose qui prime sur le thème de position) :
CHECK_ESCAPE = "check_escape"    # mon roi était en échec -> le coup le met à l'abri
CAPTURE_FREE = "capture_free"    # prise d'une pièce que l'adversaire ne peut pas reprendre (gain net)
SACRIFICE = "sacrifice"          # on donne plus qu'on ne prend, mais le moteur recommande quand même
GIVES_CHECK = "gives_check"      # le coup met le roi adverse en échec
PROMOTION = "promotion"          # le coup promeut un pion
CAPTURE_TRADE = "capture_trade"  # prise à valeur ~équilibrée (échange) -- forçant "léger"
# NON FORÇANTE :
QUIET = "quiet"                  # coup calme / positionnel -> laisse parler le thème de position

FORCING_KINDS = frozenset({CHECK_ESCAPE, CAPTURE_FREE, SACRIFICE, GIVES_CHECK, PROMOTION, CAPTURE_TRADE})

# Motifs why (why_detector.py) qui, s'ils sont présents, CONFIRMENT une prise
# nette. Gardés comme signal d'appoint uniquement : la classification ne
# DÉPEND PLUS d'eux (voir _capture_is_free). why_detector applique son propre
# ordre de priorité (fork > pin > undefended > not_recaptured > ...), si bien
# qu'une prise pourtant gagnante pouvait ressortir sous "fork"/"material_gain"
# et donc rater cette liste -- la prise gratuite retombait alors en simple
# échange. On prouve désormais le gain nous-mêmes, à partir de faits de la
# position, et ces motifs ne font que renforcer une preuve déjà établie.
_FREE_CAPTURE_MOTIFS = frozenset({"undefended", "not_recaptured", "material_gain", "fork", "pin"})


def _capture_is_free(board, move, pv_uci, line_delta, why_motif):
    """
    Une capture est-elle NETTE (l'adversaire ne peut pas rétablir le matériel) ?
    Deux preuves CALCULÉES, indépendantes de l'étiquette why_detector :

      1. case d'arrivée NON défendue par l'adversaire (board.attackers) : la
         pièce prise ne peut pas être reprise -> gain garanti, même si la PV
         est trop courte pour le montrer ;
      2. bilan matériel NET de la ligne strictement positif (line_delta > 0),
         MAIS seulement si la ligne contient la réponse adverse (len >= 2) :
         sans reprise dans la PV, line_delta ne refléterait que MA prise et
         ferait passer un mauvais échange (Txd5 repris par un pion) pour un
         gain -- on ne s'y fie donc que si l'adversaire a bien joué sa réponse.

    why_motif ne sert que de confirmation supplémentaire (jamais de condition
    nécessaire) -- voir _FREE_CAPTURE_MOTIFS. None-safe.
    """
    opponent = not board.turn
    if not board.attackers(opponent, move.to_square):
        return True  # rien ne défend la case -> prise imprenable
    if line_delta > 0 and len(pv_uci) >= 2:
        return True  # gain matériel net sur une ligne qui inclut la reprise adverse
    return why_motif in _FREE_CAPTURE_MOTIFS


@dataclass
class MoveIntent:
    """
    Ce que fait le coup recommandé. Toutes les cases/pièces sont RÉELLES
    (lues sur la position avant le coup) -- de quoi écrire un fragment
    concret ("la dame reprend le fou en e5", "le roi se met à l'abri en h1")
    sans rien inventer.

    kind        : une des constantes ci-dessus.
    forcing     : True si le coup doit primer sur le thème de position.
    from_square : case de départ du coup (int chess, ou None).
    to_square   : case d'arrivée du coup.
    moved_piece : type de pièce jouée (chess.PAWN..KING) ou None.
    captured_piece : type de pièce prise (ou None si pas une capture).
    material_delta : gain net de matériel du coup SEUL, en points, du point de
                     vue de mon camp (négatif = je donne du matériel = sacrifice).
    gives_check : le coup donne-t-il échec au roi adverse.
    """
    kind: str
    forcing: bool
    from_square: Optional[int] = None
    to_square: Optional[int] = None
    moved_piece: Optional[int] = None
    captured_piece: Optional[int] = None
    material_delta: int = 0
    gives_check: bool = False


def _immediate_material_delta(board, move):
    """
    Gain net de matériel du COUP SEUL (pas de la ligne entière), du point de
    vue du camp qui joue. Capture standard = +valeur prise ; en passant = +1 ;
    promotion = +(valeur promue - 1). Ne tient PAS compte d'une reprise
    éventuelle -- c'est le rôle du motif `why` (undefended/not_recaptured) de
    dire si la pièce est vraiment gagnée. None-safe. Toujours >= 0 (un coup
    seul ne peut pas PERDRE de matériel -- la perte n'apparaît qu'à la reprise,
    donc sur la LIGNE, voir _material_delta_over_pv).
    """
    delta = 0
    captured = board.piece_at(move.to_square)
    if captured is not None:
        delta += PIECE_VALUES.get(captured.piece_type, 0)
    elif board.is_en_passant(move):
        delta += 1
    if move.promotion:
        delta += PIECE_VALUES.get(move.promotion, 0) - 1  # le pion (1) devient la pièce promue
    return delta


def _material_delta_over_pv(board, pv_uci):
    """
    Bilan matériel NET pour le camp qui joue le 1er coup, en rejouant toute la
    ligne (mêmes règles que why_detector._material_diff_over_pv : captures +
    en passant + promotions). C'est le SEUL moyen fiable de repérer un
    sacrifice : le coup seul gagne toujours (>= 0), c'est la REPRISE adverse
    quelques demi-coups plus loin qui révèle qu'on a donné du matériel. Un
    résultat négatif = on finit la ligne en déficit matériel = sacrifice
    (l'éval reste bonne, sinon le moteur ne le recommanderait pas). pv_uci
    trop court ou illisible -> 0 (on ne conclut pas à un sacrifice sans
    preuve). None-safe.
    """
    if not pv_uci:
        return 0
    my_side = board.turn
    tmp = board.copy()
    gain = 0
    for uci in pv_uci:
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            break
        if move not in tmp.legal_moves:
            break  # ligne désynchronisée -- on s'arrête sur ce qu'on a pu valider
        mover_is_me = tmp.turn == my_side
        captured = tmp.piece_at(move.to_square)
        if captured is not None:
            value = PIECE_VALUES.get(captured.piece_type, 0)
            gain += value if mover_is_me else -value
        elif tmp.is_en_passant(move):
            gain += 1 if mover_is_me else -1
        if move.promotion:
            promo_gain = PIECE_VALUES.get(move.promotion, 0) - 1
            gain += promo_gain if mover_is_me else -promo_gain
        tmp.push(move)
    return gain


def detect_move_intent(board, chosen, why_motif=None, why_detail=None):
    """
    board : position AVANT le coup (chess.Board, même que reçu par la
        narration).
    chosen : dict candidat (voir engine_analysis.analyze_candidates) -- doit
        contenir 'move_uci'. pv_uci utilisé si présent (reprise adverse).
    why_motif / why_detail : motif tactique DÉJÀ détecté (why_detector.py) --
        signal d'APPOINT seulement pour confirmer une prise nette ; la
        classification prouve désormais le gain elle-même (case non défendue
        ou bilan de ligne positif -- voir _capture_is_free), donc elle ne
        DÉPEND plus de ce motif. Jamais recalculé ici.

    Retourne un MoveIntent, ou None si le coup est illisible (chosen mal formé)
    -- l'appelant retombe alors sur le thème de position, jamais sur un crash.

    Priorité de classement (du plus marquant au plus neutre) :
      1. mon roi en échec AVANT le coup      -> CHECK_ESCAPE
      2. je donne du matériel net            -> SACRIFICE
      3. promotion                            -> PROMOTION
      4. capture non reprenable (gain net)   -> CAPTURE_FREE
      5. le coup donne échec                  -> GIVES_CHECK
      6. capture à valeur équilibrée          -> CAPTURE_TRADE
      7. sinon                                -> QUIET (non forçant)
    """
    if not chosen:
        return None
    try:
        move = chess.Move.from_uci(chosen["move_uci"])
    except (ValueError, KeyError, TypeError):
        return None
    if move not in board.legal_moves:
        return None  # désync improbable -- pas d'intent plutôt qu'un raisonnement faux

    moved = board.piece_at(move.from_square)
    moved_piece = moved.piece_type if moved else None
    captured = board.piece_at(move.to_square)
    captured_piece = captured.piece_type if captured else None
    is_capture = board.is_capture(move)
    if is_capture and captured_piece is None and board.is_en_passant(move):
        captured_piece = chess.PAWN  # en passant : la case d'arrivée est vide mais on prend bien un pion
    immediate_delta = _immediate_material_delta(board, move)
    pv_uci = chosen.get("pv_uci") or [chosen.get("move_uci")]
    line_delta = _material_delta_over_pv(board, pv_uci)
    gives_check = board.gives_check(move)
    was_in_check = board.is_check()

    def _mk(kind, forcing, delta):
        return MoveIntent(
            kind=kind, forcing=forcing,
            from_square=move.from_square, to_square=move.to_square,
            moved_piece=moved_piece, captured_piece=captured_piece,
            material_delta=delta, gives_check=gives_check,
        )

    # 1. Sortir d'un échec prime sur tout : c'est le BUT du coup, aucune
    #    observation de structure n'a de sens tant que le roi est attaqué.
    if was_in_check:
        return _mk(CHECK_ESCAPE, True, immediate_delta)

    # 2. Sacrifice : sur TOUTE la ligne, on finit en déficit matériel (la
    #    reprise adverse prend plus qu'on n'a gagné), mais le moteur recommande
    #    le coup -> il y a une compensation, à raconter. Se détecte sur la PV,
    #    jamais sur le coup seul (qui gagne toujours >= 0). On expose le déficit
    #    (line_delta négatif) comme material_delta pour que le fragment cite
    #    l'ampleur réelle du sacrifice.
    if line_delta < 0:
        return _mk(SACRIFICE, True, line_delta)

    # 3. Promotion (sans perte de matériel) : événement majeur, on le dit.
    if move.promotion:
        return _mk(PROMOTION, True, immediate_delta)

    # 4. Capture nette : je prends une pièce que l'adversaire ne peut pas
    #    reprendre (case non défendue) OU la ligne se solde par un gain net.
    #    Prouvé sur la position, sans dépendre de l'étiquette why_detector
    #    (voir _capture_is_free) : c'est le correctif du cas "prise gagnante
    #    classée fork/material_gain qui retombait en simple échange".
    if is_capture and _capture_is_free(board, move, pv_uci, line_delta, why_motif):
        return _mk(CAPTURE_FREE, True, immediate_delta)

    # 5. Le coup donne échec (sans être une prise nette déjà traitée).
    if gives_check:
        return _mk(GIVES_CHECK, True, immediate_delta)

    # 6. Capture "ordinaire" (échange) : forçant léger -- on décrit la prise
    #    plutôt que de rester sur le thème de position, mais sans dramatiser.
    if is_capture:
        return _mk(CAPTURE_TRADE, True, immediate_delta)

    # 7. Coup calme : aucune intention marquante -> on laisse le thème de
    #    position parler (le commentaire positionnel a du sens ici).
    return _mk(QUIET, False, immediate_delta)
