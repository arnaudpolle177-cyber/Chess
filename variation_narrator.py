"""
variation_narrator.py
Transforme une variante calculée par le moteur (PV -- Principal Variation)
en un "scénario" racontant l'IDÉE de la suite, pas la liste des coups.

Remplace _suite_phrase() dans narration.py (qui se contentait de joindre
les coups en SAN, ex: "Rc8 → Nd2 → Nxd3" -- affichait ce que le moteur
calcule, sans jamais expliquer ce que ça signifie pour un joueur humain).

Principe (2 sources d'information, combinées) :
1. MOTIFS STRUCTURELS -- détectés en rejouant la PV avec python-chess, sans
   aucun appel moteur supplémentaire : échanges de pièces, repositionnement
   d'une pièce sans capture, rupture de pion qui ouvre une ligne, pression
   croissante autour du roi adverse (coups qui rapprochent des pièces de
   lui). Ce sont des FAITS géométriques/matériels, jamais inventés.
2. TRAJECTOIRE D'ÉVAL -- une éval à chaque étape de la ligne (voir
   analyze_variation(depth=...)), pour dire si la position s'améliore
   progressivement, décolle d'un coup, ou reste stable. Historiquement
   bridée à une profondeur < niveau Elo choisi pour ne pas retarder la
   flèche affichée -- ce n'est plus nécessaire depuis que le scénario est
   calculé de façon ASYNCHRONE, après coup, sans bloquer l'affichage (voir
   web_bridge.py, _attach_scenario_async) : l'appelant peut donc passer la
   depth du niveau Elo actif directement (voir DEFAULT_EVAL_DEPTH plus bas
   pour le repli si aucune depth n'est précisée).

S'applique à N'IMPORTE QUELLE phase de partie et n'importe quel thème
détecté (BLUNDER, TACTICAL, ENDGAME, etc.) -- ce module ne connaît pas le
thème, il ne fait que raconter ce que fait la ligne de coups elle-même.
"""
from dataclasses import dataclass
from typing import List, Optional

import chess

DEFAULT_EVAL_DEPTH = 13  # repli si l'appelant ne précise pas de depth (voir analyze_variation)
MAX_PLY = 6      # nombre de demi-coups de la PV analysés (cohérent avec pv_san actuel)

PIECE_VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}

# Types de motifs détectables, par ordre de priorité si plusieurs matchent
# sur la même ligne (le plus "racontable" en premier).
EXCHANGE = "EXCHANGE"
BREAKTHROUGH = "BREAKTHROUGH"
KING_PRESSURE = "KING_PRESSURE"
REPOSITION = "REPOSITION"
QUIET_IMPROVE = "QUIET_IMPROVE"


@dataclass
class VariationFacts:
    motif: str
    # Détails utiles à narrate_variation() pour remplir les gabarits --
    # toujours des données réelles (case, pièce, ampleur), jamais du texte.
    square: Optional[int] = None           # case clé du motif (échange, rupture, case visée près du roi)
    piece_type: Optional[int] = None       # pièce principale impliquée dans le motif
    material_delta: int = 0                # gain/perte net de matériel sur la ligne (points, camp qui joue)
    eval_trend: str = "stable"             # "improving" / "spikes" / "stable" / "declining"
    eval_start_cp: Optional[int] = None
    eval_end_cp: Optional[int] = None


def _eval_at(engine, board, depth):
    """
    Éval (depth donnée par l'appelant, multipv=1) de la position actuelle, du
    point de vue du camp qui a choisi ce coup au départ (pas du camp au
    trait sur CETTE position intermédiaire -- voir _pov_cp). None si le
    moteur échoue (ex: position déjà terminée) -- traité comme "on ne sait
    pas", jamais comme une valeur numérique par défaut trompeuse.
    """
    if board.is_game_over():
        return None
    try:
        info = engine.engine.analyse(board, chess.engine.Limit(depth=depth))
        return info["score"]
    except Exception:
        return None


def _pov_cp(score, root_color):
    """Score en centipawns du point de vue de root_color, quel que soit le camp au trait sur cette position précise."""
    if score is None:
        return None
    return score.pov(root_color).score(mate_score=100000)


def _classify_trend(cps):
    """
    Classe la trajectoire d'éval (liste de cp, déjà du point de vue du
    camp qui joue la ligne, valeurs None filtrées en amont) :
    - "improving" : progression régulière et significative du début à la fin.
    - "spikes" : la plus grande partie du gain arrive d'un coup à un moment précis de la ligne.
    - "declining" : la ligne perd du terrain (rare pour une PV du moteur, mais possible en fin de ligne tronquée).
    - "stable" : peu de mouvement net.
    """
    if len(cps) < 2:
        return "stable"
    total_delta = cps[-1] - cps[0]
    if total_delta <= -40:
        return "declining"
    if total_delta < 40:
        return "stable"
    # Gain significatif : régulier (improving) ou concentré sur un seul
    # saut (spikes) ? On compare le plus gros saut ponctuel au gain total.
    biggest_jump = max((cps[i + 1] - cps[i] for i in range(len(cps) - 1)), default=0)
    if biggest_jump >= total_delta * 0.7:
        return "spikes"
    return "improving"


def analyze_variation(engine, board, pv_moves, compute_eval=True, depth=DEFAULT_EVAL_DEPTH):
    """
    board : position AVANT le 1er coup de la ligne (déjà le coup choisi par
    le profil, pas encore joué -- pv_moves[0] EST ce coup).
    pv_moves : liste de chess.Move (voir candidate["pv_uci"], à reconvertir
    en Move par l'appelant -- voir narrate_move_history ci-dessous pour
    l'usage typique depuis web_bridge.py).
    compute_eval : si False, saute complètement la trajectoire d'éval
    (motifs structurels seuls) -- utile pour tester/désactiver le coût
    moteur sans toucher au reste du code.
    depth : profondeur d'analyse à chaque étape de la ligne (voir
    DEFAULT_EVAL_DEPTH si non précisé). Depuis que le scénario est calculé
    de façon asynchrone (voir web_bridge._attach_scenario_async), rien
    n'empêche plus de passer la depth du niveau Elo actif ici -- l'appelant
    (narration.compute_scenario_facts) est celui qui décide.

    Retourne un VariationFacts (jamais None -- QUIET_IMPROVE en repli si
    rien de plus spécifique ne matche).
    """
    root_color = board.turn
    moves = pv_moves[:MAX_PLY]
    if not moves:
        return VariationFacts(motif=QUIET_IMPROVE)

    cur = board.copy()
    cps = []
    if compute_eval:
        start_score = _eval_at(engine, cur, depth)
        start_cp = _pov_cp(start_score, root_color)
        if start_cp is not None:
            cps.append(start_cp)

    moved_squares = {}  # square d'origine -> nb de fois qu'une pièce en est repartie (repositionnement)
    exchange_square = None
    exchange_delta = 0
    breakthrough_square = None
    breakthrough_piece = None
    king_pressure_hits = 0
    opp_king_square = board.king(not root_color)

    material_delta = 0

    for i, move in enumerate(moves):
        mover_color = cur.turn
        piece = cur.piece_at(move.from_square)
        piece_type = piece.piece_type if piece else None
        is_capture = cur.is_capture(move)
        captured = cur.piece_at(move.to_square)
        was_pawn_push_to_open_file = (
            piece_type == chess.PAWN
            and not is_capture
            and i + 1 < len(moves)
            and cur.is_capture(moves[i + 1])
        )

        cur.push(move)

        if is_capture:
            cap_value = PIECE_VALUES.get(captured.piece_type, 0) if captured else 1  # 1 = en passant
            signed = cap_value if mover_color == root_color else -cap_value
            material_delta += signed
            if exchange_square is None:
                exchange_square = move.to_square
                exchange_delta = signed

        if was_pawn_push_to_open_file and breakthrough_square is None:
            breakthrough_square = move.to_square
            breakthrough_piece = chess.PAWN

        if opp_king_square is not None and chess.square_distance(move.to_square, opp_king_square) <= 2:
            king_pressure_hits += 1

        if not is_capture and piece_type in (chess.KNIGHT, chess.BISHOP, chess.QUEEN, chess.ROOK):
            moved_squares[move.from_square] = moved_squares.get(move.from_square, 0) + 1

        if compute_eval:
            score = _eval_at(engine, cur, depth)
            cp = _pov_cp(score, root_color)
            if cp is not None:
                cps.append(cp)

    eval_trend = _classify_trend(cps) if len(cps) >= 2 else "stable"
    eval_start_cp = cps[0] if cps else None
    eval_end_cp = cps[-1] if cps else None

    # Priorité de détection : rupture > échange notable > pression sur le
    # roi > repositionnement > calme (repli). Une ligne peut matcher
    # plusieurs motifs à la fois (ex: une rupture QUI ouvre une attaque) --
    # on garde le plus "racontable" en tête, la trajectoire d'éval affine
    # ensuite le ton dans narrate_variation() quel que soit le motif choisi.
    if breakthrough_square is not None:
        return VariationFacts(
            motif=BREAKTHROUGH, square=breakthrough_square, piece_type=breakthrough_piece,
            material_delta=material_delta, eval_trend=eval_trend,
            eval_start_cp=eval_start_cp, eval_end_cp=eval_end_cp,
        )
    if exchange_square is not None and abs(exchange_delta) >= 1:
        return VariationFacts(
            motif=EXCHANGE, square=exchange_square, material_delta=material_delta,
            eval_trend=eval_trend, eval_start_cp=eval_start_cp, eval_end_cp=eval_end_cp,
        )
    if king_pressure_hits >= 2:
        return VariationFacts(
            motif=KING_PRESSURE, square=opp_king_square, material_delta=material_delta,
            eval_trend=eval_trend, eval_start_cp=eval_start_cp, eval_end_cp=eval_end_cp,
        )
    if moved_squares:
        return VariationFacts(
            motif=REPOSITION, material_delta=material_delta, eval_trend=eval_trend,
            eval_start_cp=eval_start_cp, eval_end_cp=eval_end_cp,
        )
    return VariationFacts(
        motif=QUIET_IMPROVE, material_delta=material_delta, eval_trend=eval_trend,
        eval_start_cp=eval_start_cp, eval_end_cp=eval_end_cp,
    )


# --- Gabarits (motif x profil), variantes multiples pour éviter la
# répétition -- même mécanique que narration.py (_pick y gère la
# sélection déterministe des variantes, réutilisée ici via narrate_variation
# côté appelant si besoin ; ici on reste volontairement simple : 1 variante
# par (motif, profil), le nombre de motifs x trend combinés donne déjà une
# bonne diversité sans dupliquer tout le mécanisme de _pick).

_TREND_SUFFIX = {
    "improving": " La position s'améliore progressivement à chaque coup de cette suite.",
    "spikes": " L'essentiel du gain arrive d'un coup, au moment clé de la séquence.",
    "declining": " Cette ligne reste à surveiller, l'avantage n'y est pas garanti.",
    "stable": "",
}

_TEMPLATES = {
    (EXCHANGE, "popular"): "L'idée est de provoquer un échange qui simplifie la position en ma faveur.",
    (EXCHANGE, "creative"): "Cette suite cherche l'échange pour ouvrir des lignes vers les pièces adverses restantes.",
    (EXCHANGE, "classical"): "L'échange proposé ici allège la position -- une technique classique quand on tient l'avantage.",

    (BREAKTHROUGH, "popular"): "Cette rupture de pion ouvre la position et donne plus d'activité à mes pièces.",
    (BREAKTHROUGH, "creative"): "La rupture centrale déstabilise la position adverse et ouvre des lignes d'attaque.",
    (BREAKTHROUGH, "classical"): "Cette poussée de pion ouvre la position selon les principes classiques -- plus d'espace pour les pièces.",

    (KING_PRESSURE, "popular"): "L'idée est de continuer à rapprocher mes pièces du roi adverse pour augmenter la pression.",
    (KING_PRESSURE, "creative"): "L'attaque continue en resserrant l'étau autour du roi adverse.",
    (KING_PRESSURE, "classical"): "Cette suite concentre les forces vers le roi adverse, en accord avec les principes d'attaque.",

    (REPOSITION, "popular"): "Cette suite replace mes pièces sur de meilleures cases avant de passer à l'action.",
    (REPOSITION, "creative"): "Je prépare mes pièces sur de meilleures cases pour préparer une complication à venir.",
    (REPOSITION, "classical"): "Cette manœuvre améliore la position des pièces avant de fixer un plan précis.",

    (QUIET_IMPROVE, "popular"): "Cette suite consolide la position sans rien précipiter.",
    (QUIET_IMPROVE, "creative"): "Rien d'immédiat ici, mais la position garde des ressources à exploiter plus tard.",
    (QUIET_IMPROVE, "classical"): "Cette suite améliore la position coup après coup, sans rien forcer.",
}


def narrate_variation(facts, profile_id):
    """
    facts : VariationFacts (voir analyze_variation).
    profile_id : "popular" / "creative" / "classical".

    Retourne le texte du scénario, jamais une liste de coups.
    """
    base = _TEMPLATES.get((facts.motif, profile_id)) or _TEMPLATES[(QUIET_IMPROVE, "popular")]
    suffix = _TREND_SUFFIX.get(facts.eval_trend, "")
    return base + suffix
