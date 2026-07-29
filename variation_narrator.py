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

import engine_analysis  # uniquement pour MATE_SCORE (source unique de l'encodage mat)

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
    return score.pov(root_color).score(mate_score=engine_analysis.MATE_SCORE)


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
    last_capture_square = None   # case de la prise du demi-coup précédent...
    last_capture_by = None       # ... et couleur qui l'a jouée (voir ÉCHANGE plus bas)
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
        cur.push(move)

        # Rupture = MA poussée de pion qui provoque une capture au coup
        # suivant. Deux gardes indispensables, chacune corrigeant un faux
        # positif observé en production :
        #  - mover_color : tous les gabarits parlent de "mes pièces" ; une
        #    poussée ADVERSE n'est pas ma rupture ;
        #  - le test is_capture doit se faire APRÈS le push, dans la position
        #    où moves[i+1] est légal. python-chess calcule is_capture sur
        #    `from ^ to` intersecté avec les pièces adverses : évalué une
        #    position trop tôt, la case de DÉPART du coup suivant suffit à
        #    rendre True, et n'importe quelle poussée passait pour une rupture.
        was_pawn_push_to_open_file = (
            mover_color == root_color
            and piece_type == chess.PAWN
            and not is_capture
            and i + 1 < len(moves)
            and cur.is_capture(moves[i + 1])
        )

        if is_capture:
            cap_value = PIECE_VALUES.get(captured.piece_type, 0) if captured else 1  # 1 = en passant
            signed = cap_value if mover_color == root_color else -cap_value
            material_delta += signed
            # ÉCHANGE = une prise SUIVIE d'une reprise sur la MÊME case. Avant,
            # la première prise venue suffisait -- y compris une prise ADVERSE
            # (le test était `abs(delta) >= 1`), et le gabarit annonçait quand
            # même « un échange qui simplifie la position en ma faveur » alors
            # que je venais de perdre une pièce. Mesuré : le motif sortait sur
            # 28 lignes sur 80.
            if (exchange_square is None and last_capture_square == move.to_square
                    and last_capture_by is not None and last_capture_by != mover_color):
                exchange_square = move.to_square
                exchange_delta = signed
            last_capture_square = move.to_square
            last_capture_by = mover_color
        else:
            last_capture_square = None
            last_capture_by = None

        if was_pawn_push_to_open_file and breakthrough_square is None:
            breakthrough_square = move.to_square
            breakthrough_piece = chess.PAWN

        # Même raison que ci-dessus : seuls MES coups pressent le roi adverse
        # ou repositionnent MES pièces. Sans cette garde, un simple Fe7/Cf6
        # adverse près de son propre roi comptait comme ma pression.
        if (mover_color == root_color and opp_king_square is not None
                and chess.square_distance(move.to_square, opp_king_square) <= 2):
            king_pressure_hits += 1

        if (mover_color == root_color and not is_capture
                and piece_type in (chess.KNIGHT, chess.BISHOP, chess.QUEEN, chess.ROOK)):
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
    if exchange_square is not None:
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
    # « en ma faveur » et « quand on tient l'avantage » ont été retirés : rien
    # ne les calculait, et le motif sortait même quand la ligne PERDAIT du
    # matériel. Le bilan réel est ajouté par _MATERIAL_CLAUSE ci-dessous, qui
    # lui est compté.
    (EXCHANGE, "popular"): "Les pièces s'échangent dans cette suite, la position se simplifie.",
    (EXCHANGE, "creative"): "Cette suite passe par un échange, qui dégage des lignes vers les pièces restantes.",
    (EXCHANGE, "classical"): "Un échange intervient dans cette ligne, allégeant la position.",

    (BREAKTHROUGH, "popular"): "Cette rupture de pion ouvre la position et donne plus d'activité à mes pièces.",
    (BREAKTHROUGH, "creative"): "La rupture centrale déstabilise la position adverse et ouvre des lignes d'attaque.",
    (BREAKTHROUGH, "classical"): "Cette poussée de pion ouvre la position selon les principes classiques -- plus d'espace pour les pièces.",

    # Ce qui est compté ici, c'est que DEUX de mes coups au moins arrivent à
    # deux cases ou moins du roi adverse (king_pressure_hits) -- pas une
    # intention.
    (KING_PRESSURE, "popular"): "Dans cette suite, plusieurs de mes pièces viennent se poster près du roi adverse.",
    (KING_PRESSURE, "creative"): "L'attaque continue en resserrant l'étau autour du roi adverse.",
    (KING_PRESSURE, "classical"): "Cette suite concentre les forces vers le roi adverse, en accord avec les principes d'attaque.",

    # « de meilleures cases » est un jugement comparatif que rien ne mesure
    # (règle centrale du projet) -- et ce motif est le REPLI, donc il sortait
    # sur 47 lignes sur 80. On décrit ce qui est vrai : les pièces bougent,
    # aucune prise n'intervient.
    (REPOSITION, "popular"): "Cette suite déplace mes pièces sans qu'aucune prise n'intervienne.",
    (REPOSITION, "creative"): "Aucune prise dans cette ligne : les pièces manœuvrent, la tension reste entière.",
    (REPOSITION, "classical"): "Cette manœuvre réorganise les pièces, sans échange ni rupture.",

    (QUIET_IMPROVE, "popular"): "Cette suite consolide la position sans rien précipiter.",
    (QUIET_IMPROVE, "creative"): "Rien d'immédiat ici, mais la position garde des ressources à exploiter plus tard.",
    (QUIET_IMPROVE, "classical"): "Cette suite améliore la position coup après coup, sans rien forcer.",
}


# Bilan matériel de la ligne : COMPTÉ depuis toujours (material_delta), jamais
# dit. C'est pourtant le fait le plus concret de la carte, et il s'applique à
# TOUS les motifs -- d'où l'ajout ici plutôt que dans cinq gabarits. Pas de
# valeur chiffrée sous 2 : à 1 pion près, un décompte sur une ligne tronquée
# n'est pas un fait solide (voir move_intent, gardes d'horizon).
def _material_clause(material_delta):
    if material_delta >= 2:
        return f" Au bout de la ligne, {material_delta} points de matériel de plus pour moi."
    if material_delta <= -2:
        return f" Au bout de la ligne, {abs(material_delta)} points de matériel en moins pour moi."
    return ""


def narrate_variation(facts, profile_id):
    """
    facts : VariationFacts (voir analyze_variation).
    profile_id : "popular" / "creative" / "classical".

    Retourne le texte du scénario, jamais une liste de coups.
    """
    base = _TEMPLATES.get((facts.motif, profile_id)) or _TEMPLATES[(QUIET_IMPROVE, "popular")]
    return base + _material_clause(facts.material_delta) + _TREND_SUFFIX.get(facts.eval_trend, "")
