"""
theme_detector.py
Détecte le THÈME PRINCIPAL d'une position -- une seule fois par position,
PARTAGÉ entre les 3 profils (voir la conversation : "même thème, 3
philosophies différentes", pas 3 thèmes indépendants).

Le coach ne répond plus à "pourquoi ce coup ?" mais à "que se passe-t-il
réellement dans cette position ?" -- ce module répond à cette 2e question,
narration.py se charge ensuite de la raconter avec la voix de chaque
profil.

Toutes les conditions ci-dessous sont calculées à partir de données
RÉELLES (éval Stockfish, matériel, attaquants/défenseurs comptés via
python-chess) -- jamais inventées.
"""
from dataclasses import dataclass
from typing import Optional

import chess

from human_profile import game_phase as _game_phase  # réutilise la détection de phase déjà en place


# Ordre de priorité si plusieurs thèmes matchent en même temps : les
# événements ponctuels/rares passent avant les événements d'ambiance plus
# fréquents.
BLUNDER = "BLUNDER"
TACTICAL = "TACTICAL"
ATTACK = "ATTACK"
DEFENSE = "DEFENSE"
MISSED_OPPORTUNITY = "MISSED_OPPORTUNITY"
ENDGAME = "ENDGAME"
OPENING = "OPENING"
STRATEGIC_ADVANTAGE = "STRATEGIC_ADVANTAGE"
PAWN_STRUCTURE = "PAWN_STRUCTURE"
PIECE_ACTIVITY_GAP = "PIECE_ACTIVITY_GAP"
EQUAL_POSITION = "EQUAL_POSITION"

PRIORITY_ORDER = (
    BLUNDER, TACTICAL, ATTACK, DEFENSE, MISSED_OPPORTUNITY,
    ENDGAME, OPENING, STRATEGIC_ADVANTAGE, PAWN_STRUCTURE, PIECE_ACTIVITY_GAP, EQUAL_POSITION,
)

# Seuils (centipawns), ajustables si l'usage réel montre qu'ils déclenchent
# trop souvent/pas assez.
# Seuils (centipawns), ajustables si l'usage réel montre qu'ils déclenchent
# trop souvent/pas assez.
BLUNDER_THRESHOLD_CP = 150       # l'adversaire vient de perdre au moins 1.5 pion d'éval -- erreur nette
# MISSED_OPPORTUNITY : bande INTERMÉDIAIRE sous BLUNDER -- l'adversaire n'a
# pas complètement craqué, mais n'a pas non plus joué la ligne la plus
# incisive disponible. Volontairement PAS basé sur le coup du joueur
# lui-même : le coach affiche toujours le coup à jouer via les flèches, un
# joueur qui les suit ne peut pas vraiment "manquer" son propre coup -- ce
# thème parle donc de l'adversaire, pas de l'utilisateur.
MISSED_OPPORTUNITY_MIN_CP = 60
TACTICAL_GAP_CP = 100            # écart net entre le 1er et le 2e candidat
ATTACK_DEFENSE_EVAL_CP = 100     # avantage/désavantage net pour déclencher attaque/défense
STRATEGIC_EVAL_CP = 80           # avantage net mais sans motif tactique immédiat
EQUAL_EVAL_CP = 50               # position jugée équilibrée en dessous de ce seuil
# PIECE_ACTIVITY_GAP : différence RELATIVE (pas absolue -- le nombre total
# de cases contrôlées varie énormément selon la phase, voir la
# conversation) entre la mobilité pondérée des 2 camps, pour déclencher
# même sans avantage matériel/tactique.
ACTIVITY_GAP_RATIO = 1.4  # le camp actif contrôle au moins 40% de cases pondérées en plus que l'adversaire


@dataclass
class ThemeResult:
    theme: str
    # Contexte utile à narration.py pour remplir les gabarits sans avoir à
    # tout recalculer -- toujours des données réelles, jamais du texte.
    eval_cp: int
    swing_cp: Optional[int] = None       # ampleur du gain/de la perte détectée (BLUNDER / MISSED_OPPORTUNITY)
    king_square: Optional[int] = None    # roi concerné (ATTACK -> roi adverse, DEFENSE -> mon roi)
    phase: str = "middlegame"
    passed_pawn_square: Optional[int] = None  # un vrai pion passé de mon camp, si un existe (voir ENDGAME)
    has_bishop_pair: bool = False              # j'ai mes 2 fous ET l'adversaire non (voir STRATEGIC_ADVANTAGE)
    opponent_better_move_san: Optional[str] = None  # ce que l'adversaire aurait pu jouer de plus incisif (voir MISSED_OPPORTUNITY)
    pawn_weakness_square: Optional[int] = None  # pion adverse doublé/isolé à cibler (voir PAWN_STRUCTURE)
    pawn_weakness_kind: Optional[str] = None    # "doubled" ou "isolated" (voir PAWN_STRUCTURE)
    # Déséquilibre matériel qualitatif (voir _material_imbalance_kind) --
    # généralise l'ancien has_bishop_pair (bool) à plusieurs types de
    # déséquilibre, fusionné dans STRATEGIC_ADVANTAGE plutôt que d'être un
    # thème séparé (voir la conversation : évite la redondance, "la paire
    # de fous" ET "avantage stratégique" décrivaient déjà la même réalité).
    # Valeurs possibles : "bishop_pair_open" / "bishop_pair_closed" /
    # "knights_closed" / "rook_vs_minors" / None (pas de déséquilibre notable).
    material_imbalance_kind: Optional[str] = None
    activity_ratio: Optional[float] = None  # mobilité pondérée (mien / adverse), voir PIECE_ACTIVITY_GAP
    caution: Optional[str] = None  # avertissement transversal (ex: "stalemate_risk"), indépendant du thème principal


@dataclass
class ThemeCandidate:
    """
    Résultat BRUT d'une brique de détection individuelle -- "ce thème
    matche-t-il sur cette position, et avec quelle force" -- SANS décider
    s'il doit être affiché (c'est le rôle de detect_theme, qui applique la
    priorité, voir PRIORITY_ORDER).

    Introduit pour préparer la future fusion multi-thèmes (1 principal +
    jusqu'à 2 secondaires, voir la conversation) SANS changer le
    comportement actuel : detect_theme() continue à ne retourner qu'un
    seul ThemeResult pour l'instant, mais collecte déjà tous les
    ThemeCandidate qui matchent au passage (voir detect_theme,
    all_candidates) -- cette liste n'est pas encore utilisée pour fusionner
    des commentaires, elle est gardée en réserve pour la prochaine étape du
    projet, afin d'éviter une réécriture complète de l'architecture de
    détection à ce moment-là.

    strength : score de priorité/intensité, comparable UNIQUEMENT entre
    candidats du même "niveau" (voir PRIORITY_ORDER) -- pas une échelle
    universelle, juste de quoi trier plus tard.
    fields : dict de champs additionnels (mêmes noms que ThemeResult) à
    fusionner dans le ThemeResult final si ce candidat est retenu.
    """
    theme: str
    strength: float
    fields: dict


def _find_passed_pawn(board, color):
    """
    Retourne la case d'un pion passé de `color`, s'il en existe un, sinon
    None -- un pion est "passé" s'il n'y a AUCUN pion adverse sur sa
    colonne ni les colonnes adjacentes, en avant de lui. Calcul réel via
    python-chess, pas une estimation : sert à ce que la narration de
    finale (ENDGAME) puisse citer un pion passé PRÉCIS quand il y en a
    vraiment un, plutôt que de mentionner le concept dans le vide.
    """
    direction = 1 if color == chess.WHITE else -1
    for sq in board.pieces(chess.PAWN, color):
        file = chess.square_file(sq)
        rank = chess.square_rank(sq)
        blocked = False
        for f in (file - 1, file, file + 1):
            if f < 0 or f > 7:
                continue
            r = rank + direction
            while 0 <= r <= 7:
                other = board.piece_at(chess.square(f, r))
                if other and other.piece_type == chess.PAWN and other.color != color:
                    blocked = True
                    break
                r += direction
            if blocked:
                break
        if not blocked:
            return sq
    return None


def _king_safety_score(board, king_color):
    """
    Compte, sur les cases autour du roi de `king_color` (roi compris),
    combien sont attaquées par l'adversaire vs défendues par son propre
    camp -- un vrai calcul via python-chess (board.attackers()), pas une
    estimation. Retourne (cases_attaquées, cases_défendues).
    """
    king_sq = board.king(king_color)
    if king_sq is None:
        return 0, 0
    attacked = 0
    defended = 0
    king_file, king_rank = chess.square_file(king_sq), chess.square_rank(king_sq)
    for df in (-1, 0, 1):
        for dr in (-1, 0, 1):
            f, r = king_file + df, king_rank + dr
            if not (0 <= f <= 7 and 0 <= r <= 7):
                continue
            sq = chess.square(f, r)
            if board.attackers(not king_color, sq):
                attacked += 1
            if board.attackers(king_color, sq):
                defended += 1
    return attacked, defended


def _material_imbalance_kind(board, color):
    """
    Généralise l'ancienne _has_bishop_pair_advantage (bool) à plusieurs
    déséquilibres qualitatifs reconnus (voir la conversation) -- retourne
    un identifiant du déséquilibre trouvé EN FAVEUR de `color`, ou None.

    Le type de déséquilibre est croisé avec l'ouverture/fermeture de la
    position (via le nombre de pions encore sur leur colonne d'origine,
    signal simple de fermeture -- une position très fermée a peu
    d'échanges de pions, donc beaucoup de pions encore proches de leur
    case de départ) : la MÊME paire de fous n'a pas la même valeur
    pédagogique selon que la position est ouverte ou fermée (voir la
    conversation : "la paire de fous prendra de plus en plus de valeur SI
    la position s'ouvre" -- le conditionnel fait partie du message).

    Retourne un des identifiants suivants, ou None si rien de notable :
    - "bishop_pair_open"   : j'ai la paire de fous, position déjà ouverte -- l'avantage est déjà exploitable.
    - "bishop_pair_closed" : j'ai la paire de fous, position encore fermée -- l'avantage est latent, à activer en ouvrant le jeu.
    - "knights_closed"     : j'ai 2 cavaliers contre 2 fous adverses, position fermée -- MES pièces sont favorisées ici.
    - "rook_vs_minors"     : j'ai une tour + un pion de plus contre 2 pièces mineures adverses (échange déséquilibré classique).
    """
    my_bishops = len(board.pieces(chess.BISHOP, color))
    opp_bishops = len(board.pieces(chess.BISHOP, not color))
    my_knights = len(board.pieces(chess.KNIGHT, color))
    opp_knights = len(board.pieces(chess.KNIGHT, not color))

    # Signal de fermeture simple et bon marché : proportion de pions
    # encore sur leur colonne de départ (les 2 camps confondus) -- pas une
    # vraie analyse de chaînes de pions, juste un indicateur suffisant
    # pour trancher "plutôt ouvert" vs "plutôt fermé" sans calcul lourd.
    start_files_white = {sq for sq in chess.SQUARES if chess.square_rank(sq) == 1}
    start_files_black = {sq for sq in chess.SQUARES if chess.square_rank(sq) == 6}
    pawns_on_start = sum(1 for sq in board.pieces(chess.PAWN, chess.WHITE) if sq in start_files_white)
    pawns_on_start += sum(1 for sq in board.pieces(chess.PAWN, chess.BLACK) if sq in start_files_black)
    total_pawns = len(board.pieces(chess.PAWN, chess.WHITE)) + len(board.pieces(chess.PAWN, chess.BLACK))
    is_closed = total_pawns > 0 and (pawns_on_start / total_pawns) >= 0.6

    if my_bishops >= 2 and opp_bishops < 2:
        return "bishop_pair_closed" if is_closed else "bishop_pair_open"
    if my_knights >= 2 and opp_knights < 2 and opp_bishops >= 1 and is_closed:
        return "knights_closed"

    # Tour + pion contre 2 pièces mineures : comparaison BRUTE du nombre de
    # pièces (pas de comptage de valeur globale ici, ThemeDetector ne
    # connaît que le matériel, pas l'éval détaillée -- eval_cp suffit déjà
    # à indiquer QUI est mieux, ce déséquilibre explique juste POURQUOI).
    my_rooks = len(board.pieces(chess.ROOK, color))
    opp_minors = opp_bishops + opp_knights
    my_minors = my_bishops + my_knights
    if my_rooks >= 1 and opp_minors >= my_minors + 2:
        return "rook_vs_minors"

    return None


_PIECE_MOBILITY_TYPES = (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN)
_CENTRAL_SQUARES = {chess.D4, chess.D5, chess.E4, chess.E5, chess.C4, chess.C5, chess.F4, chess.F5}


def _activity_ratio(board, color):
    """
    Mobilité PONDÉRÉE de `color` / mobilité pondérée de l'adversaire (voir
    la conversation, PIECE_ACTIVITY_GAP) -- compte les cases contrôlées
    par les pièces mineures/lourdes (board.attacks(), déjà utilisé ailleurs
    dans le projet, coût nul en appels moteur), avec un bonus pour les
    cases centrales (une pièce qui contrôle des cases centrales pèse plus
    qu'une pièce qui contrôle autant de cases mais toutes en bord
    d'échiquier -- voir la conversation sur le risque de faux positif).
    Retourne None si l'adversaire a une mobilité nulle (division par 0,
    cas dégénéré qui ne devrait pas se produire hors position terminale).
    """
    def weighted_mobility(c):
        total = 0.0
        for piece_type in _PIECE_MOBILITY_TYPES:
            for sq in board.pieces(piece_type, c):
                for target in board.attacks(sq):
                    total += 1.5 if target in _CENTRAL_SQUARES else 1.0
        return total

    mine = weighted_mobility(color)
    theirs = weighted_mobility(not color)
    if theirs <= 0:
        return None
    return mine / theirs


# Avertissement pat (stalemate) : au moins 5 points de matériel d'avance
# en finale, ET l'adversaire n'a presque plus de coups légaux -- l'erreur
# classique du débutant qui gagne largement et pate l'adversaire par
# inadvertance. Stockfish lui-même n'y tombe jamais (un coup qui pate
# évalue à 0, donc déjà filtré par la fenêtre de tolérance), mais c'est un
# vrai réflexe à enseigner, indépendant du thème principal affiché.
STALEMATE_RISK_EVAL_CP = 500
STALEMATE_RISK_MAX_OPPONENT_MOVES = 3


def _stalemate_caution(board, my_side, eval_cp, phase):
    if phase != "endgame" or eval_cp < STALEMATE_RISK_EVAL_CP:
        return None
    try:
        tmp = board.copy()
        tmp.turn = not my_side  # compte la mobilité adverse -- approximation volontaire, juste pour un décompte de coups, pas pour valider une position
        n_moves = tmp.legal_moves.count()
        if 0 < n_moves <= STALEMATE_RISK_MAX_OPPONENT_MOVES:
            return "stalemate_risk"
    except Exception:
        pass
    return None


def _find_pawn_weakness(board, color):
    """
    Cherche une faiblesse de structure de pions DÉJÀ PRÉSENTE chez `color`
    sur la position actuelle -- peu importe qui l'a créée ni quand (voir
    la conversation : détection volontairement limitée au board actuel,
    pas au coup choisi par chaque profil, pour rester compatible avec le
    partage de thème entre les 3 profils, voir web_bridge.py
    _update_eval_tracking_and_theme).

    2 défauts détectés, par ordre de priorité (le premier trouvé gagne,
    parcours des colonnes de a à h pour un résultat déterministe) :
    - "doubled"  : 2+ pions de `color` sur la même colonne.
    - "isolated" : un pion de `color` sans AUCUN pion allié sur les
      colonnes adjacentes, pour le soutenir durablement.

    Retourne (square, kind) du pion concerné, ou (None, None) si aucune
    des deux faiblesses n'est présente. Un calcul réel via python-chess
    (colonnes comptées via chess.square_file), jamais une estimation.
    """
    pawns_by_file = {}
    for sq in board.pieces(chess.PAWN, color):
        pawns_by_file.setdefault(chess.square_file(sq), []).append(sq)

    for file in range(8):
        squares = pawns_by_file.get(file)
        if squares and len(squares) >= 2:
            return squares[0], "doubled"

    for file in range(8):
        squares = pawns_by_file.get(file)
        if not squares:
            continue
        has_neighbor = any(pawns_by_file.get(f) for f in (file - 1, file + 1) if 0 <= f <= 7)
        if not has_neighbor:
            return squares[0], "isolated"

    return None, None


def detect_theme(board, candidates, swing_cp=None, opponent_better_move_san=None):
    """
    board : position ACTUELLE (chess.Board), au trait de "mon" camp (my_side).
    candidates : liste triée meilleur -> moins bon (voir engine_analysis.analyze_candidates).
    swing_cp : écart d'éval en ma faveur depuis mon dernier tour, imputable
        au coup de l'adversaire (voir web_bridge.py, _track_opponent_eval)
        -- None si pas encore assez d'historique pour le calculer.
    opponent_better_move_san : le coup que l'adversaire avait de disponible
        à son tour précédent (avis du moteur à ce moment-là, voir
        web_bridge.py) -- utilisé pour MISSED_OPPORTUNITY, quand son coup
        réel était en dessous de ça sans être un franc blunder.

    Retourne un ThemeResult -- toujours un thème (EQUAL_POSITION au pire),
    jamais None.

    NOTE ARCHITECTURE (voir ThemeCandidate) : cette fonction collecte au
    passage tous les ThemeCandidate qui matchent dans `all_candidates`,
    mais continue à ne RETOURNER que le premier trouvé selon
    PRIORITY_ORDER (comportement inchangé) -- cette collecte prépare la
    future fusion multi-thèmes sans encore la faire.
    """
    my_side = board.turn
    top_cp = candidates[0]["cp"] if candidates else None
    # cp peut être None pour un coup issu du livre d'ouvertures (voir
    # opening_book.py -- pas de vraie éval Stockfish en mode livre, c'est
    # volontaire). Sans ce filet, une comparaison numérique (eval_cp >= X)
    # plantait avec un coup de livre -- et une fois planté, le thème ne se
    # mettait plus JAMAIS à jour pour les positions suivantes non plus
    # (voir web_bridge.py, _update_eval_tracking_and_theme : l'exception
    # est absorbée mais empêche le cache de thème d'avancer). 0 = repli
    # neutre ("on ne sait pas, on suppose une position à peu près égale").
    eval_cp = top_cp if top_cp is not None else 0
    phase = _game_phase(board)
    caution = _stalemate_caution(board, my_side, eval_cp, phase)
    all_candidates = []  # voir ThemeCandidate -- réserve pour la future fusion, pas encore utilisée pour l'affichage

    # 1. BLUNDER -- priorité maximale : l'adversaire vient de se tromper nettement.
    if swing_cp is not None and swing_cp >= BLUNDER_THRESHOLD_CP:
        all_candidates.append(ThemeCandidate(BLUNDER, swing_cp, {"swing_cp": swing_cp}))
        return ThemeResult(BLUNDER, eval_cp, swing_cp=swing_cp, phase=phase, caution=caution)

    # 2. TACTICAL -- un seul coup se démarque nettement des autres, et
    #    c'est un coup forcing (échec ou capture).
    if len(candidates) >= 2:
        gap = candidates[1]["eval_loss"]  # perte du 2e par rapport au 1er
        top = candidates[0]
        if gap >= TACTICAL_GAP_CP and (top["is_check"] or top["is_capture"]):
            all_candidates.append(ThemeCandidate(TACTICAL, gap, {}))
            return ThemeResult(TACTICAL, eval_cp, phase=phase, caution=caution)

    # 3. ATTACK / DEFENSE -- avantage net + roi (adverse ou le mien) exposé.
    opp_attacked, opp_defended = _king_safety_score(board, not my_side)
    if eval_cp >= ATTACK_DEFENSE_EVAL_CP and opp_attacked > opp_defended:
        all_candidates.append(ThemeCandidate(ATTACK, eval_cp, {"king_square": board.king(not my_side)}))
        return ThemeResult(ATTACK, eval_cp, king_square=board.king(not my_side), phase=phase, caution=caution)

    my_attacked, my_defended = _king_safety_score(board, my_side)
    if eval_cp <= -ATTACK_DEFENSE_EVAL_CP and my_attacked > my_defended:
        all_candidates.append(ThemeCandidate(DEFENSE, -eval_cp, {"king_square": board.king(my_side)}))
        return ThemeResult(DEFENSE, eval_cp, king_square=board.king(my_side), phase=phase, caution=caution)

    # 4. MISSED_OPPORTUNITY -- L'ADVERSAIRE n'a pas complètement craqué
    #    (sinon ce serait BLUNDER, déjà écarté au point 1), mais n'a pas
    #    non plus joué la ligne la plus incisive qu'il avait à ce moment-là
    #    -- il reste de la marge à exploiter maintenant. Volontairement PAS
    #    basé sur le coup du joueur lui-même (voir docstring plus haut).
    if swing_cp is not None and MISSED_OPPORTUNITY_MIN_CP <= swing_cp < BLUNDER_THRESHOLD_CP:
        all_candidates.append(ThemeCandidate(MISSED_OPPORTUNITY, swing_cp, {"swing_cp": swing_cp}))
        return ThemeResult(MISSED_OPPORTUNITY, eval_cp, swing_cp=swing_cp, phase=phase,
                            opponent_better_move_san=opponent_better_move_san, caution=caution)

    # 5. ENDGAME / OPENING -- phase de partie.
    if phase == "endgame":
        passed_sq = _find_passed_pawn(board, my_side)
        all_candidates.append(ThemeCandidate(ENDGAME, 1.0, {"passed_pawn_square": passed_sq}))
        return ThemeResult(ENDGAME, eval_cp, phase=phase, passed_pawn_square=passed_sq, caution=caution)
    if phase == "opening":
        all_candidates.append(ThemeCandidate(OPENING, 1.0, {}))
        return ThemeResult(OPENING, eval_cp, phase=phase, caution=caution)

    # 6. STRATEGIC_ADVANTAGE -- avantage net mais sans motif tactique
    #    immédiat. Inclut désormais le déséquilibre matériel qualitatif
    #    généralisé (voir _material_imbalance_kind -- fusionne l'ancien
    #    thème MATERIAL_IMBALANCE envisagé séparément, évite la redondance
    #    avec ce thème qui décrivait déjà la même réalité).
    if abs(eval_cp) >= STRATEGIC_EVAL_CP:
        imbalance = _material_imbalance_kind(board, my_side)
        all_candidates.append(ThemeCandidate(STRATEGIC_ADVANTAGE, abs(eval_cp), {"material_imbalance_kind": imbalance}))
        return ThemeResult(STRATEGIC_ADVANTAGE, eval_cp, phase=phase,
                            material_imbalance_kind=imbalance, caution=caution)

    # 7. PAWN_STRUCTURE -- faiblesse de structure ADVERSE à cibler (jamais
    #    les miennes, voir la conversation : ton offensif, "voici ce que tu
    #    peux viser", pas "attention à ta propre structure"). Position par
    #    ailleurs équilibrée (aucun des thèmes plus prioritaires n'a
    #    matché) -- un plan positionnel durable plutôt qu'une urgence.
    weakness_sq, weakness_kind = _find_pawn_weakness(board, not my_side)
    if weakness_sq is not None:
        all_candidates.append(ThemeCandidate(PAWN_STRUCTURE, 1.0, {
            "pawn_weakness_square": weakness_sq, "pawn_weakness_kind": weakness_kind,
        }))
        return ThemeResult(PAWN_STRUCTURE, eval_cp, phase=phase,
                            pawn_weakness_square=weakness_sq, pawn_weakness_kind=weakness_kind, caution=caution)

    # 8. PIECE_ACTIVITY_GAP -- avantage de mobilité marqué, MÊME sans
    #    avantage matériel/tactique (voir la conversation) -- position par
    #    ailleurs équilibrée en éval, mais un camp contrôle nettement plus
    #    de cases utiles que l'autre.
    ratio = _activity_ratio(board, my_side)
    if ratio is not None and ratio >= ACTIVITY_GAP_RATIO:
        all_candidates.append(ThemeCandidate(PIECE_ACTIVITY_GAP, ratio, {"activity_ratio": ratio}))
        return ThemeResult(PIECE_ACTIVITY_GAP, eval_cp, phase=phase, activity_ratio=ratio, caution=caution)

    # 9. Filet de sécurité : position jugée équilibrée.
    all_candidates.append(ThemeCandidate(EQUAL_POSITION, 0.0, {}))
    return ThemeResult(EQUAL_POSITION, eval_cp, phase=phase, caution=caution)
