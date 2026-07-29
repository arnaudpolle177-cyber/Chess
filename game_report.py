"""
game_report.py
Rapport de fin de partie façon chess.com/lichess : accuracy % + compteurs de
coups par catégorie (Brilliant/Great/Book/Best/Excellent/Good/Inaccuracy/
Mistake/Miss/Blunder), pour les DEUX camps -- construit à partir de
web_bridge.BridgeState._ply_log (un dict par coup déjà joué, voir sa
docstring pour la structure exacte).

IMPORTANT -- ce que ce module N'EST PAS : une réplique de l'algorithme
chess.com (non public). Book/Best/Excellent/Good/Inaccuracy/Mistake/Blunder
et l'accuracy % reposent sur une formule win%-based publique (citée
ci-dessous, pas copiée d'un moteur propriétaire) et sont donc solides.
Brilliant/Great/Miss sont des heuristiques BEST-EFFORT (proxys raisonnables
vu les données disponibles, pas une détection tactique complète) --
documentées comme telles à chaque fonction, pas à considérer comme
"parfaites".
"""
import math

import chess

from engine_analysis import PIECE_VALUES

ALL_LABELS = (
    "Brilliant", "Great", "Book", "Best", "Excellent", "Good",
    "Inaccuracy", "Mistake", "Miss", "Blunder", "Unknown",
)

# Seuils de bande en PERTE DE WIN% (pas en cp -- voir win_percent ci-dessous),
# ordre de grandeur adapté de sim_precision.BANDS (qui, elle, raisonne en cp).
# PAS de bande "Best" ici : "Best" est déjà décidé par l'égalité stricte au
# meilleur coup (voir classify_ply) -- un coup DIFFÉRENT du meilleur mais à
# perte quasi nulle tombe dans "Excellent", jamais "Best" (réservé au coup
# réellement joué par le moteur). ponytail: seuils pas calibrés par
# simulation comme sim_sweep.py le fait pour les cp -- à ajuster
# empiriquement si l'usage réel montre des catégories trop larges/étroites.
CATEGORY_BANDS_WIN_PCT_LOSS = (
    ("Excellent", 0, 5),
    ("Good", 5, 10),
    ("Inaccuracy", 10, 20),
    ("Mistake", 20, 35),
    ("Blunder", 35, 101),
)

# Un coup qui aurait laissé un mat forcé en N -- voir engine_analysis.py,
# score(mate_score=100000) : un vrai avantage matériel ne dépasse jamais ça
# (même valeur/logique que web_bridge._MATE_CP_THRESHOLD).
_MATE_CP_THRESHOLD = 90000

# Écart (cp) entre le meilleur et le 2e meilleur candidat à partir duquel le
# coup optimal est considéré "unique" -- voir _is_great_heuristic.
_GREAT_SECOND_BEST_GAP_CP = 150

# Table de correspondance ACPL (perte moyenne en centipions par coup) -> Elo
# estimé, points d'ancrage COURAMMENT CITÉS dans le milieu échecs (forums
# lichess/chess.com, articles de vulgarisation) -- PAS la formule propriétaire
# de chess.com (jamais publiée), juste un repère indicatif interpolé
# linéairement entre ces points. ponytail: précision volontairement limitée
# (une estimation à +/-150 Elo est déjà utile pédagogiquement, inutile de
# viser plus fin sans données réelles pour calibrer).
_ACPL_ELO_ANCHORS = (
    (0, 3000), (10, 2700), (25, 2500), (50, 2200), (80, 1900),
    (120, 1600), (180, 1300), (250, 1000), (400, 700),
)


def estimate_elo_from_acpl(acpl):
    """
    Interpolation linéaire entre les points de _ACPL_ELO_ANCHORS, bornée aux
    extrémités (jamais > 3000 ni < 700 par cette table). None si acpl est
    None.
    """
    if acpl is None:
        return None
    if acpl <= _ACPL_ELO_ANCHORS[0][0]:
        return _ACPL_ELO_ANCHORS[0][1]
    if acpl >= _ACPL_ELO_ANCHORS[-1][0]:
        return _ACPL_ELO_ANCHORS[-1][1]
    for (x0, y0), (x1, y1) in zip(_ACPL_ELO_ANCHORS, _ACPL_ELO_ANCHORS[1:]):
        if x0 <= acpl <= x1:
            t = (acpl - x0) / (x1 - x0)
            return round(y0 + t * (y1 - y0))
    return _ACPL_ELO_ANCHORS[-1][1]  # inatteignable (bornes déjà gérées ci-dessus), filet de sécurité


def win_percent(cp):
    """
    Convertit une éval en centipawns (POV du camp qui vient de jouer) en un
    pourcentage de chances de gain 0..100. Formule publique (lichess),
    CITÉE pas copiée d'un moteur propriétaire :
    50 + 50 * (2 / (1 + exp(-0.00368208 * cp)) - 1).
    Un mat encodé (voir _MATE_CP_THRESHOLD) sature à 0 ou 100.
    """
    if cp is None:
        return None
    if abs(cp) >= _MATE_CP_THRESHOLD:
        return 100.0 if cp > 0 else 0.0
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)


def accuracy_from_win_percent_loss(loss_pct):
    """
    Formule publique (lichess), citée pas copiée d'un moteur propriétaire :
    103.1668 * exp(-0.04354 * loss_pct) - 3.1669, clampée [0, 100].
    """
    value = 103.1668 * math.exp(-0.04354 * loss_pct) - 3.1669
    return max(0.0, min(100.0, value))


def _win_pct_loss(entry):
    """
    Perte de win% du camp qui a joué CE coup, entre juste avant et juste
    après -- None si l'une des deux évals manque (coup de livre, dernier ply
    de la partie jamais backfillé, etc.).
    """
    before = win_percent(entry.get("eval_before_cp"))
    after_opp_pov = win_percent(entry.get("eval_after_cp"))
    if before is None or after_opp_pov is None:
        return None
    # eval_after_cp est du POV du camp qui vient de jouer (voir
    # web_bridge._update_move_history) -- déjà la bonne perspective, pas de
    # flip supplémentaire ici.
    return max(0.0, before - after_opp_pov)


def _cp_loss(entry):
    """
    Perte en CENTIPIONS BRUTS (pas en win%, contrairement à _win_pct_loss)
    du camp qui a joué CE coup -- sert uniquement à l'ACPL (voir
    estimate_elo_from_acpl), la table de correspondance Elo étant elle-même
    calibrée en cp, pas en win%. None si l'une des deux évals manque, ou si
    l'une encode un mat (voir _MATE_CP_THRESHOLD -- une valeur à 5-6 chiffres
    fausserait complètement une moyenne en cp, alors que win_percent() sature
    proprement ce cas pour _win_pct_loss).
    """
    before = entry.get("eval_before_cp")
    after = entry.get("eval_after_cp")
    if before is None or after is None:
        return None
    if abs(before) >= _MATE_CP_THRESHOLD or abs(after) >= _MATE_CP_THRESHOLD:
        return None
    return max(0.0, before - after)


def _band_for_loss(loss_pct):
    for label, lo, hi in CATEGORY_BANDS_WIN_PCT_LOSS:
        if lo <= loss_pct < hi:
            return label
    return "Blunder"


def _is_brilliant_heuristic(entry):
    """
    ponytail: proxy = coup optimal + sacrifice matériel (pièce qui prend en
    valant plus que ce qu'elle capture), même esprit que
    human_profile._is_sacrifice_candidate mais appliqué au coup RÉELLEMENT
    joué. Ceiling connu : ne détecte pas les brillances purement
    positionnelles (aucun sacrifice de matériel) -- documenté, pas un bug.
    """
    if not entry.get("fen_before") or not entry.get("played_move_uci"):
        return False
    try:
        board = chess.Board(entry["fen_before"])
        move = chess.Move.from_uci(entry["played_move_uci"])
    except ValueError:
        return False
    if not board.is_capture(move):
        return False
    moving = board.piece_at(move.from_square)
    moving_value = PIECE_VALUES.get(moving.piece_type, 0) if moving else 0
    if board.is_en_passant(move):
        captured_value = 1
    else:
        captured = board.piece_at(move.to_square)
        captured_value = PIECE_VALUES.get(captured.piece_type, 0) if captured else 0
    return moving_value > captured_value


def _is_great_heuristic(entry):
    """
    ponytail: proxy = coup optimal ET "unique" (2e meilleur candidat
    nettement pire). Seulement disponible pour les plies où le 2e candidat a
    été conservé (voir web_bridge._record_pending_ply_eval, second_best_cp
    -- multipv complet, donc uniquement les coups DE L'UTILISATEUR, jamais
    ceux de l'adversaire qui n'a qu'une éval single-PV). Sans cette donnée,
    dégrade silencieusement vers "Best" -- pas de faux positif inventé.
    """
    best_cp = entry.get("eval_before_cp")
    second_cp = entry.get("second_best_cp")
    if best_cp is None or second_cp is None:
        return False
    return (best_cp - second_cp) >= _GREAT_SECOND_BEST_GAP_CP


def _is_missed_mate(entry):
    """
    ponytail: sous-ensemble volontairement restreint du "Miss" chess.com --
    couvre le cas où un MAT FORCÉ était disponible avant ce coup et n'a pas
    été joué (eval_before_cp encode un mat, voir _MATE_CP_THRESHOLD) ET le
    coup joué n'est pas le meilleur coup. Voir _opponent_just_blundered pour
    le cas plus général (adversaire vient de blunder sans que ce soit
    forcément un mat).
    """
    cp = entry.get("eval_before_cp")
    if cp is None or abs(cp) < _MATE_CP_THRESHOLD:
        return False
    return entry.get("played_move_uci") != entry.get("best_move_uci")


def _opponent_just_blundered(ply_log, index):
    """
    True si le PLY PRÉCÉDENT (le coup de l'adversaire, juste avant celui-ci)
    est lui-même dans la bande Mistake/Blunder -- l'adversaire vient de
    perdre gros, réutilisé ici pour distinguer "Miss" (occasion manquée) de
    "Mistake"/"Blunder" ordinaire. Réutilise _band_for_loss/_win_pct_loss
    (même barème que pour CE coup) plutôt qu'un seuil séparé -- même esprit
    que theme_detector.MISSED_OPPORTUNITY_MIN_CP (la version narration en
    direct de cette idée, qui elle ne s'applique qu'à l'adversaire), mais ici
    dérivé du barème win% déjà en place pour rester cohérent avec le reste
    du rapport.
    """
    if index <= 0:
        return False
    prev_loss = _win_pct_loss(ply_log[index - 1])
    if prev_loss is None:
        return False
    return _band_for_loss(prev_loss) in ("Mistake", "Blunder")


def classify_ply(entry, ply_log, index):
    """
    Retourne un label parmi game_report.ALL_LABELS pour l'entrée
    ply_log[index]. `ply_log`/`index` servent uniquement à
    _opponent_just_blundered (regarde le ply précédent) -- voir le docstring
    du module pour le niveau de confiance de chaque label.
    """
    if entry.get("played_move_uci") is None:
        return "Unknown"
    if entry.get("is_book"):
        return "Book"

    is_best = (entry.get("best_move_uci") is not None
               and entry["played_move_uci"] == entry["best_move_uci"])
    if is_best:
        if _is_brilliant_heuristic(entry):
            return "Brilliant"
        if _is_great_heuristic(entry):
            return "Great"
        return "Best"

    loss_pct = _win_pct_loss(entry)
    if loss_pct is None:
        # Pas d'éval avant/après exploitable (dernier ply de la partie
        # jamais backfillé, coup de livre côté adverse non tagué is_book,
        # etc.) -- pas de crash, juste pas de bande fine.
        return "Unknown"

    label = _band_for_loss(loss_pct)
    if label in ("Mistake", "Blunder"):
        if _is_missed_mate(entry) or _opponent_just_blundered(ply_log, index):
            return "Miss"
    return label


def build_eval_curve(ply_log, my_side="w"):
    """
    Liste des évals APRÈS chaque coup, converties du POINT DE VUE DE
    `my_side` (PAS toujours Blancs -- contrairement à la barre d'avantage
    côté navigateur, web_bridge._objective_eval_white, qui elle reste
    toujours en absolu côté Blancs) : positif = tu es mieux, négatif =
    l'adversaire est mieux, quel que soit ton camp. Une valeur par ply où
    eval_after_cp est connu, DANS L'ORDRE DE LA PARTIE (les deux camps
    mélangés, contrairement à report["w"]/["b"]["moves"] qui sont séparés
    par camp). Sert de base à un petit graphe de tendance (voir
    webview_ui.py) -- pas de lissage/échantillonnage ici, juste les points
    bruts, peu coûteux même sur une longue partie (une partie de 80 coups =
    80 flottants).
    """
    curve = []
    for entry in ply_log:
        cp = entry.get("eval_after_cp")
        if cp is None:
            continue
        curve.append(cp if entry.get("side") == my_side else -cp)
    return curve


def build_report(ply_log, my_side="w"):
    """
    Agrège ply_log (voir web_bridge.BridgeState._ply_log) en un rapport par
    camp :
        {"w": {"accuracy": float|None, "acpl": float|None,
               "estimated_elo": int|None, "counts": {label: int, ...},
               "moves": [ply_log entries enrichies d'un champ "label"]},
         "b": {...},
         "eval_curve": [cp POV Blancs, ...] -- voir build_eval_curve}
    "estimated_elo" : estimation grossière (voir estimate_elo_from_acpl),
    PAS le "Game Rating" propriétaire de chess.com -- juste un repère
    indicatif basé sur l'ACPL.
    "moves" garde l'ORDRE de la partie -- sert au tableau ET au clic
    "ouvrir le puzzle" (index = position dans cette liste).
    Coût : O(n) sur ply_log déjà peuplé au fil de l'eau -- aucun recalcul
    moteur ici, pas de risque de coût sur une partie longue.
    """
    report = {
        side: {"accuracy": None, "acpl": None, "estimated_elo": None,
               "counts": {label: 0 for label in ALL_LABELS}, "moves": []}
        for side in ("w", "b")
    }
    losses_by_side = {"w": [], "b": []}
    cp_losses_by_side = {"w": [], "b": []}

    for index, entry in enumerate(ply_log):
        side = entry.get("side")
        if side not in ("w", "b"):
            continue
        label = classify_ply(entry, ply_log, index)
        enriched = dict(entry, label=label)
        report[side]["moves"].append(enriched)
        report[side]["counts"][label] += 1
        loss_pct = _win_pct_loss(entry)
        if loss_pct is not None:
            losses_by_side[side].append(loss_pct)
        cp_loss = _cp_loss(entry)
        if cp_loss is not None:
            cp_losses_by_side[side].append(cp_loss)

    for side in ("w", "b"):
        losses = losses_by_side[side]
        if losses:
            # Moyenne des accuracies PAR COUP (comme lichess), pas
            # accuracy(moyenne des pertes) -- cette 2e version se fait
            # écraser par un seul gros blunder (une moyenne de pertes très
            # étalée), alors que la moyenne d'accuracies individuelles
            # amortit mieux un seul mauvais coup au milieu d'une partie
            # sinon propre.
            per_move_accuracy = [accuracy_from_win_percent_loss(loss) for loss in losses]
            report[side]["accuracy"] = round(sum(per_move_accuracy) / len(per_move_accuracy), 1)
        cp_losses = cp_losses_by_side[side]
        if cp_losses:
            acpl = sum(cp_losses) / len(cp_losses)
            report[side]["acpl"] = round(acpl, 1)
            report[side]["estimated_elo"] = estimate_elo_from_acpl(acpl)

    report["eval_curve"] = build_eval_curve(ply_log, my_side)
    return report
