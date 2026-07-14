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
INITIATIVE_SHIFT = "INITIATIVE_SHIFT"
STRATEGIC_ADVANTAGE = "STRATEGIC_ADVANTAGE"
PAWN_STRUCTURE = "PAWN_STRUCTURE"
PIECE_ACTIVITY_GAP = "PIECE_ACTIVITY_GAP"
KING_SAFETY_WARNING = "KING_SAFETY_WARNING"
EQUAL_POSITION = "EQUAL_POSITION"

PRIORITY_ORDER = (
    BLUNDER, TACTICAL, ATTACK, DEFENSE, MISSED_OPPORTUNITY,
    ENDGAME, OPENING, INITIATIVE_SHIFT, STRATEGIC_ADVANTAGE, PAWN_STRUCTURE,
    PIECE_ACTIVITY_GAP, KING_SAFETY_WARNING, EQUAL_POSITION,
)

# =====================================================================
# NARRATION v2 -- MÉTADONNÉES DES BRIQUES (voir NARRATION_V2_PLAN.txt)
# =====================================================================
# Tables PUREMENT ADDITIVES : elles ne sont PAS utilisées par detect_theme
# (comportement d'affichage actuel strictement inchangé). Elles servent au
# futur pipeline multi-thèmes -- collect_theme_bricks() en bas de ce fichier
# -> scoring (theme_scoring.py, à venir) -> sélection -> weaver. Tant que ce
# pipeline n'est pas branché dans web_bridge.py, rien de tout ceci n'a
# d'effet sur le coach en fonctionnement.

# --- Tiers de priorité -------------------------------------------------
# Le poids de base d'un tier DOMINE l'intensité brute (voir la roadmap :
# scoring = poids_tier + intensité_normalisée) : un thème "très forte
# priorité" passe donc toujours devant un thème d'enrichissement, quel que
# soit son signal. La normalisation de l'intensité (0..99) est le travail
# de l'étape 2 (scoring), PAS d'ici -- collect_theme_bricks se contente de
# préserver le signal brut dans ThemeCandidate.strength (comme aujourd'hui).
TIER_STRONG = "strong"
TIER_MEDIUM = "medium"
TIER_ENRICHMENT = "enrichment"

TIER_WEIGHT = {TIER_STRONG: 1000, TIER_MEDIUM: 500, TIER_ENRICHMENT: 100}

THEME_TIER = {
    BLUNDER: TIER_STRONG,
    TACTICAL: TIER_STRONG,
    ATTACK: TIER_STRONG,
    DEFENSE: TIER_STRONG,
    STRATEGIC_ADVANTAGE: TIER_MEDIUM,
    ENDGAME: TIER_MEDIUM,
    OPENING: TIER_MEDIUM,
    MISSED_OPPORTUNITY: TIER_MEDIUM,
    PAWN_STRUCTURE: TIER_ENRICHMENT,
    PIECE_ACTIVITY_GAP: TIER_ENRICHMENT,
    KING_SAFETY_WARNING: TIER_ENRICHMENT,
    INITIATIVE_SHIFT: TIER_ENRICHMENT,
    EQUAL_POSITION: TIER_ENRICHMENT,  # filet neutre : présent seulement si rien d'autre, intensité 0
}

# --- Familles sémantiques ---------------------------------------------
# Deux briques de la MÊME famille racontent au fond la même idée et ne
# doivent pas coexister dans le commentaire final (anti-redondance, gérée
# à l'étape de sélection). Ex. attendu par la roadmap : PAWN_STRUCTURE et
# STRATEGIC_ADVANTAGE ne doivent pas "expliquer deux fois que la structure
# est meilleure".
#
# ⚠ TAXONOMIE PROVISOIRE -- à affiner ensemble à l'étape 2/3 (sélection).
# Placée ici pour que le collecteur soit déjà exploitable, mais les
# regroupements exacts (ex: faut-il fusionner "advantage" et "structure" ?)
# sont un choix de design à valider, pas une vérité figée.
FAMILY_OPPONENT_MOVE = "opponent_move"  # qualité du dernier coup adverse
FAMILY_TACTICS = "tactics"              # coup concret / calcul forcé
FAMILY_KING = "king"                    # sécurité d'un roi (le mien ou l'adverse)
FAMILY_ADVANTAGE = "advantage"          # avantage d'évaluation général
FAMILY_STRUCTURE = "structure"          # structure de pions
FAMILY_ACTIVITY = "activity"            # activité / mobilité des pièces
FAMILY_DYNAMICS = "dynamics"            # tendance dans le temps (initiative)
FAMILY_PHASE = "phase"                  # ambiance de phase (ouverture / finale)
FAMILY_NEUTRAL = "neutral"              # rien ne se dégage

THEME_FAMILY = {
    BLUNDER: FAMILY_OPPONENT_MOVE,
    MISSED_OPPORTUNITY: FAMILY_OPPONENT_MOVE,
    TACTICAL: FAMILY_TACTICS,
    ATTACK: FAMILY_KING,
    DEFENSE: FAMILY_KING,
    KING_SAFETY_WARNING: FAMILY_KING,
    STRATEGIC_ADVANTAGE: FAMILY_ADVANTAGE,
    PAWN_STRUCTURE: FAMILY_STRUCTURE,
    PIECE_ACTIVITY_GAP: FAMILY_ACTIVITY,
    INITIATIVE_SHIFT: FAMILY_DYNAMICS,
    ENDGAME: FAMILY_PHASE,
    OPENING: FAMILY_PHASE,
    EQUAL_POSITION: FAMILY_NEUTRAL,
}


def theme_tier(theme):
    """Tier de priorité d'un thème (voir THEME_TIER) -- défaut enrichissement si inconnu."""
    return THEME_TIER.get(theme, TIER_ENRICHMENT)


def theme_family(theme):
    """Famille sémantique d'un thème (voir THEME_FAMILY) -- défaut : sa propre valeur (famille singleton) si inconnu."""
    return THEME_FAMILY.get(theme, theme)


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
# KING_SAFETY_WARNING : signal PRÉVENTIF, testé seulement si ATTACK/DEFENSE
# n'a pas matché (sinon redondance directe, voir la conversation) --
# combine 2 conditions pour éviter de recréer un simple ATTACK/DEFENSE
# affaibli : le roi doit être structurellement pas encore mis en sécurité
# (voir _king_not_castled_yet) ET son king_safety_score doit déjà montrer
# un léger déséquilibre (entre 0 et le seuil ATTACK_DEFENSE_EVAL_CP actuel,
# jamais au-dessus -- sinon ce serait déjà ATTACK/DEFENSE).
KING_SAFETY_WARNING_MIN_ATTACKED_DELTA = 1  # au moins 1 case de plus attaquée que défendue autour du roi
KING_SAFETY_WARNING_MAX_MOVE_NUMBER = 15    # au-delà, le roque manqué n'est plus un signal fiable (partie déjà engagée dans un plan différent)
# INITIATIVE_SHIFT : fenêtre glissante des dernières évals "à mon tour"
# (voir web_bridge.py, BridgeState._initiative_history) -- détecte une
# TENDANCE sur plusieurs coups, pas un instantané. 4 points plutôt que 3 :
# filtre mieux le bruit normal d'éval (une seule variation isolée d'un
# tour à l'autre ne suffit pas à faire basculer la pente) sans rendre le
# thème trop lent à réagir.
INITIATIVE_WINDOW = 4
INITIATIVE_SLOPE_CP = 25  # pente minimale (cp par coup, régression linéaire) pour parler de tendance, pas de bruit
# Bande d'éval où la tendance devient intéressante à signaler : pas déjà
# extrême (ATTACK/DEFENSE aurait pris le dessus avant, voir l'ordre de
# priorité) et pas totalement neutre (sous ce seuil, "je perds l'avantage"
# n'a pas vraiment de sens -- il n'y avait pas d'avantage à perdre).
INITIATIVE_EVAL_MIN_ABS_CP = 20
# SIMPLIFICATION : enrichissement du texte STRATEGIC_ADVANTAGE (PAS un
# thème séparé, voir la conversation) -- consulte initiative_trend, déjà
# calculé à ce stade, pour trancher si l'avantage gagnerait à être
# simplifié (position stable, rien de dynamique en cours) ou au contraire
# mérite de garder la tension (initiative montante, mieux vaut ne pas
# désamorcer l'élan). Même seuil que INITIATIVE_SLOPE_CP pour rester
# cohérent avec ce qui déclencherait INITIATIVE_SHIFT si l'éval était
# dans la bonne bande -- "keep_tension" ne se déclenche QUE si la pente
# est déjà notable, pas sur un bruit résiduel.


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
    # "simplify" / "keep_tension" / None -- voir SIMPLIFICATION dans la
    # conversation : enrichissement de STRATEGIC_ADVANTAGE, jamais un
    # thème séparé (le "faut-il simplifier" est une CONCLUSION tirée d'un
    # thème déjà actif, pas un fait détecté indépendamment sur le board).
    simplification_advice: Optional[str] = None
    activity_ratio: Optional[float] = None  # mobilité pondérée (mien / adverse), voir PIECE_ACTIVITY_GAP
    king_safety_warning_square: Optional[int] = None  # roi concerné par l'avertissement préventif (voir KING_SAFETY_WARNING)
    king_safety_warning_is_mine: bool = True  # True = mon roi (à protéger), False = roi adverse (à cibler bientôt)
    initiative_slope_cp: Optional[float] = None  # pente cp/coup sur la fenêtre glissante (voir INITIATIVE_SHIFT) -- positif = je prends l'initiative, négatif = je la perds
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


def _king_not_castled_yet(board, color):
    """
    Vrai si le roi de `color` est encore sur sa case de départ (e1/e8) --
    signal simple et bon marché de "n'a pas encore roqué", pas une preuve
    absolue de danger en soi (un roi qui reste au centre volontairement,
    dans un centre fermé, n'est pas en danger -- voir _is_center_open
    ci-dessous, combiné dans _king_safety_warning).
    """
    start_square = chess.E1 if color == chess.WHITE else chess.E8
    king_sq = board.king(color)
    return king_sq == start_square


def _is_center_open(board):
    """
    Signal simple de centre ouvert/semi-ouvert : au moins une des colonnes
    centrales (d, e) n'a plus de pion d'un des deux camps -- pas une vraie
    analyse de chaînes de pions, juste suffisant pour distinguer "un roi
    non roqué au centre ouvert est en danger" de "un roi non roqué dans un
    centre totalement verrouillé ne l'est pas" (voir la conversation).
    """
    for file in (chess.square_file(chess.D1), chess.square_file(chess.E1)):
        white_pawn = any(
            chess.square_file(sq) == file for sq in board.pieces(chess.PAWN, chess.WHITE)
        )
        black_pawn = any(
            chess.square_file(sq) == file for sq in board.pieces(chess.PAWN, chess.BLACK)
        )
        if not white_pawn or not black_pawn:
            return True
    return False


def _king_safety_warning(board, color, eval_cp_for_color, phase, ply_count=None):
    """
    Signal PRÉVENTIF pour `color` (voir la conversation, KING_SAFETY_WARNING)
    -- combine 4 conditions, toutes nécessaires, pour rester spécifique et
    éviter les faux positifs (roi volontairement resté au centre dans une
    position fermée, ou milieu de partie déjà avancé où le roque n'est
    plus le sujet) :
    1. Le roi n'a pas encore roqué (toujours sur sa case de départ).
    2. On est encore en ouverture/tout début de milieu de partie (phase
       != "endgame", et nombre de coups joués sous un plafond -- voir
       KING_SAFETY_WARNING_MAX_MOVE_NUMBER).
    3. Le centre est au moins partiellement ouvert (sinon le roi non
       roqué n'est en réalité pas en danger).
    4. Un léger déséquilibre de king_safety_score existe déjà ET l'éval
       reste dans une bande MODÉRÉE (0 à ATTACK_DEFENSE_EVAL_CP, jamais
       au-dessus -- sinon ATTACK/DEFENSE aurait déjà matché avant ce point
       de la priorité ; en dessous de 0 dans le mauvais sens, ce n'est pas
       non plus le sujet -- voir eval_cp_for_color).

    eval_cp_for_color : eval_cp DÉJÀ orienté du point de vue de `color`
    (positif = favorable à `color`) -- l'appelant doit passer eval_cp pour
    my_side et -eval_cp pour l'adversaire (voir detect_theme).
    ply_count : nombre de demi-coups réellement joués (voir detect_theme,
        move_history) -- remplace board.fullmove_number, qui vaut TOUJOURS
        1 dans ce projet (le FEN reconstruit côté navigateur ne porte
        jamais le vrai numéro de coup, voir chess_coach_bridge.user.js) et
        rendait donc ce garde-fou totalement inopérant (jamais dépassé,
        quelle que soit la longueur réelle de la partie). None (par
        défaut) = ancien comportement (garde-fou toujours franchi, comme
        avant ce correctif) si l'appelant n'a pas l'historique.

    Retourne True si l'avertissement doit se déclencher pour `color`.
    """
    if phase == "endgame":
        return False
    if ply_count is not None and ply_count > KING_SAFETY_WARNING_MAX_MOVE_NUMBER * 2:
        return False
    if not (0 <= eval_cp_for_color < ATTACK_DEFENSE_EVAL_CP):
        return False
    if not _king_not_castled_yet(board, color):
        return False
    if not _is_center_open(board):
        return False
    attacked, defended = _king_safety_score(board, color)
    return (attacked - defended) >= KING_SAFETY_WARNING_MIN_ATTACKED_DELTA


def compute_initiative_trend(eval_history):
    """
    Régression linéaire simple (pente cp/coup) sur `eval_history` -- liste
    de cp DÉJÀ du point de vue de my_side (voir web_bridge.py,
    BridgeState._initiative_history), ordonnée du plus ancien au plus
    récent. Aucune dépendance externe (pas de numpy) : formule directe des
    moindres carrés sur un indice 0..n-1.

    Retourne la pente (float, positif = tendance à la hausse pour moi,
    négatif = tendance à la baisse), ou None si l'historique est trop
    court pour qu'une pente ait un sens (voir INITIATIVE_WINDOW -- il faut
    au moins 2 points, mais en pratique on attend surtout d'avoir la
    fenêtre pleine pour un signal fiable, filtré par INITIATIVE_SLOPE_CP
    dans detect_theme).
    """
    n = len(eval_history)
    if n < 2:
        return None
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(eval_history) / n
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, eval_history))
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0:
        return None
    return numerator / denominator


def _simplification_advice(initiative_trend):
    """
    Enrichissement de STRATEGIC_ADVANTAGE (voir la conversation) --
    consulte initiative_trend (déjà calculé par l'appelant, aucun nouveau
    signal moteur) pour trancher entre 2 conseils, sans prétendre à une
    analyse plus fine que ce que ces 2 signaux permettent honnêtement :

    - "keep_tension" : ma tendance d'initiative est déjà nettement
      montante (pente >= INITIATIVE_SLOPE_CP) -- je suis en train de
      construire quelque chose d'actif, simplifier casserait cet élan.
    - "simplify" : pas de tendance montante marquée -- l'avantage est
      stable, rien d'urgent en cours, la simplification est un plan
      raisonnable par défaut.
    - None : jamais retourné actuellement (couverture volontairement
      binaire pour rester honnête sur ce que 2 signaux peuvent vraiment
      trancher) -- réservé si une 3e nuance s'avère utile plus tard.
    """
    if initiative_trend is not None and initiative_trend >= INITIATIVE_SLOPE_CP:
        return "keep_tension"
    return "simplify"


def detect_theme(board, candidates, swing_cp=None, opponent_better_move_san=None, initiative_trend=None,
                  move_history=None):
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
    initiative_trend : pente cp/coup de la fenêtre glissante des dernières
        évals "à mon tour" (voir web_bridge.py, compute_initiative_trend /
        _initiative_history) -- None si pas encore assez d'historique.
        Utilisé pour INITIATIVE_SHIFT.
    move_history : liste des coups SAN joués depuis le début de la partie
        (voir web_bridge.py, BridgeState._move_history) -- source FIABLE du
        nombre de demi-coups réellement joués, contrairement au numéro de
        coup du FEN (toujours "1", voir chess_coach_bridge.user.js). Utilisé
        pour plafonner la phase "opening" (voir human_profile._game_phase,
        OPENING_MAX_PLY) et le signal préventif KING_SAFETY_WARNING. None
        (par défaut) = repli sur l'ancien comportement (matériel seul).

    Retourne un ThemeResult -- toujours un thème (EQUAL_POSITION au pire),
    jamais None.

    NOTE ARCHITECTURE (voir ThemeCandidate) : cette fonction collecte au
    passage tous les ThemeCandidate qui matchent dans `all_candidates`,
    mais continue à ne RETOURNER que le premier trouvé selon
    PRIORITY_ORDER (comportement inchangé) -- cette collecte prépare la
    future fusion multi-thèmes sans encore la faire.
    """
    ply_count = len(move_history) if move_history is not None else None
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
    phase = _game_phase(board, ply_count)
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

    # 6. INITIATIVE_SHIFT -- tendance sur plusieurs coups (voir
    #    compute_initiative_trend / web_bridge.py, _initiative_history),
    #    PAS un instantané. Se déclenche dans les 2 sens : je perds
    #    l'initiative (pente négative alors que j'étais/suis en avantage)
    #    OU je la reprends (pente positive alors que j'étais/suis en
    #    désavantage). Bande d'éval modérée (voir INITIATIVE_EVAL_MIN_ABS_CP)
    #    : pas déjà extrême (ATTACK/DEFENSE aurait pris le dessus avant) et
    #    pas totalement neutre (rien à perdre/gagner si l'éval est ~0).
    if initiative_trend is not None and abs(eval_cp) >= INITIATIVE_EVAL_MIN_ABS_CP:
        losing_initiative = eval_cp > 0 and initiative_trend <= -INITIATIVE_SLOPE_CP
        gaining_initiative = eval_cp < 0 and initiative_trend >= INITIATIVE_SLOPE_CP
        if losing_initiative or gaining_initiative:
            all_candidates.append(ThemeCandidate(INITIATIVE_SHIFT, abs(initiative_trend), {
                "initiative_slope_cp": initiative_trend,
            }))
            return ThemeResult(INITIATIVE_SHIFT, eval_cp, phase=phase,
                                initiative_slope_cp=initiative_trend, caution=caution)

    # 7. STRATEGIC_ADVANTAGE -- avantage net mais sans motif tactique
    #    immédiat. Inclut désormais le déséquilibre matériel qualitatif
    #    généralisé (voir _material_imbalance_kind -- fusionne l'ancien
    #    thème MATERIAL_IMBALANCE envisagé séparément, évite la redondance
    #    avec ce thème qui décrivait déjà la même réalité) ET un avis de
    #    simplification (voir _simplification_advice -- "faut-il chercher
    #    à échanger les pièces ou garder la tension", conclusion tirée
    #    d'initiative_trend, pas un nouveau signal détecté sur le board).
    if abs(eval_cp) >= STRATEGIC_EVAL_CP:
        imbalance = _material_imbalance_kind(board, my_side)
        simplify_advice = _simplification_advice(initiative_trend)
        all_candidates.append(ThemeCandidate(STRATEGIC_ADVANTAGE, abs(eval_cp), {
            "material_imbalance_kind": imbalance, "simplification_advice": simplify_advice,
        }))
        return ThemeResult(STRATEGIC_ADVANTAGE, eval_cp, phase=phase,
                            material_imbalance_kind=imbalance, simplification_advice=simplify_advice, caution=caution)

    # 8. PAWN_STRUCTURE -- faiblesse de structure ADVERSE à cibler (jamais
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

    # 9. PIECE_ACTIVITY_GAP -- avantage de mobilité marqué, MÊME sans
    #    avantage matériel/tactique (voir la conversation) -- position par
    #    ailleurs équilibrée en éval, mais un camp contrôle nettement plus
    #    de cases utiles que l'autre.
    ratio = _activity_ratio(board, my_side)
    if ratio is not None and ratio >= ACTIVITY_GAP_RATIO:
        all_candidates.append(ThemeCandidate(PIECE_ACTIVITY_GAP, ratio, {"activity_ratio": ratio}))
        return ThemeResult(PIECE_ACTIVITY_GAP, eval_cp, phase=phase, activity_ratio=ratio, caution=caution)

    # 10. KING_SAFETY_WARNING -- signal PRÉVENTIF, testé seulement ici (donc
    #    APRÈS ATTACK/DEFENSE au point 3, qui a priorité si la situation
    #    est déjà critique -- voir la conversation, évite la redondance).
    #    Mon roi d'abord (plus directement actionnable par l'utilisateur),
    #    puis le roi adverse (occasion à repérer, moins urgent).
    if _king_safety_warning(board, my_side, eval_cp, phase, ply_count):
        my_king_sq = board.king(my_side)
        all_candidates.append(ThemeCandidate(KING_SAFETY_WARNING, 1.0, {
            "king_safety_warning_square": my_king_sq, "king_safety_warning_is_mine": True,
        }))
        return ThemeResult(KING_SAFETY_WARNING, eval_cp, phase=phase,
                            king_safety_warning_square=my_king_sq, king_safety_warning_is_mine=True, caution=caution)
    if _king_safety_warning(board, not my_side, -eval_cp, phase, ply_count):
        opp_king_sq = board.king(not my_side)
        all_candidates.append(ThemeCandidate(KING_SAFETY_WARNING, 1.0, {
            "king_safety_warning_square": opp_king_sq, "king_safety_warning_is_mine": False,
        }))
        return ThemeResult(KING_SAFETY_WARNING, eval_cp, phase=phase,
                            king_safety_warning_square=opp_king_sq, king_safety_warning_is_mine=False, caution=caution)

    # 11. Filet de sécurité : position jugée équilibrée.
    all_candidates.append(ThemeCandidate(EQUAL_POSITION, 0.0, {}))
    return ThemeResult(EQUAL_POSITION, eval_cp, phase=phase, caution=caution)


# =====================================================================
# NARRATION v2 -- COLLECTEUR DE BRIQUES (étape 1, voir NARRATION_V2_PLAN.txt)
# =====================================================================
def collect_theme_bricks(board, candidates, swing_cp=None,
                          opponent_better_move_san=None, initiative_trend=None, move_history=None):
    """
    Version "briques" de detect_theme : teste TOUTES les conditions de
    thème et retourne la LISTE de toutes celles qui matchent (chacune un
    ThemeCandidate enrichi de son tier + sa famille), au lieu de s'arrêter
    au premier match selon PRIORITY_ORDER.

    C'est la première étape du pipeline narration v2 (détecter tous les
    thèmes -> scorer -> sélectionner 1 principal + 0..2 secondaires ->
    narrer). Cette fonction ne fait QUE la détection : ni scoring global,
    ni sélection, ni texte -- ces étapes viennent après (theme_scoring.py,
    weaver), et rien n'appelle encore collect_theme_bricks en production.

    ⚠ ADDITIF ET NON BRANCHÉ : detect_theme() reste la source de vérité de
    l'affichage actuel et n'est pas modifiée. Les conditions ci-dessous
    sont un MIROIR EXACT de celles de detect_theme (mêmes seuils, mêmes
    helpers, même sémantique de champs) -- à ceci près qu'il n'y a aucun
    `return` anticipé : on empile toutes les briques valides.

    Points de divergence VOLONTAIRES avec detect_theme, tous dus au fait
    qu'on ne s'arrête plus au premier match :
    - BLUNDER et MISSED_OPPORTUNITY sont mutuellement exclusifs par
      construction (bandes de swing_cp disjointes), donc au plus un des
      deux matche -- pas de risque de doublon "opponent_move".
    - ENDGAME/OPENING (bande de phase) et STRATEGIC/PAWN/ACTIVITY/KING
      peuvent désormais matcher SIMULTANÉMENT (detect_theme les rendait
      exclusifs via l'early-return). C'est justement le but : plusieurs
      observations coexistent, la sélection tranchera ensuite lesquelles
      garder. La cohabitation OPENING/ENDGAME entre elles reste impossible
      (phase unique).
    - ATTACK/DEFENSE et KING_SAFETY_WARNING peuvent tous coexister ici
      (même famille FAMILY_KING) : l'anti-redondance par famille, à
      l'étape de sélection, se chargera de n'en garder qu'un.

    Retourne une liste de ThemeCandidate (jamais vide : EQUAL_POSITION est
    ajouté en dernier ressort si rien d'autre ne matche, comme le filet de
    detect_theme). L'ordre de la liste suit PRIORITY_ORDER (ordre d'ajout),
    mais n'a AUCUNE valeur décisionnelle ici -- c'est le scoring de l'étape
    2 qui ordonnera vraiment.
    """
    my_side = board.turn
    top_cp = candidates[0]["cp"] if candidates else None
    eval_cp = top_cp if top_cp is not None else 0  # cp=None sur coup de livre -> neutre (voir detect_theme)
    ply_count = len(move_history) if move_history is not None else None
    phase = _game_phase(board, ply_count)

    bricks = []

    def _add(theme, strength, fields):
        # Enrichit chaque brique de son tier + sa famille dès la collecte,
        # pour que les étapes suivantes (scoring/sélection) n'aient pas à
        # re-consulter les tables. On stocke ça dans `fields` (dict libre,
        # voir ThemeCandidate) sous des clés préfixées `_` pour les
        # distinguer des vrais champs de ThemeResult qui, eux, seront
        # fusionnés dans le résultat final.
        enriched = dict(fields)
        enriched["_tier"] = theme_tier(theme)
        enriched["_family"] = theme_family(theme)
        bricks.append(ThemeCandidate(theme, strength, enriched))

    # 1. BLUNDER
    if swing_cp is not None and swing_cp >= BLUNDER_THRESHOLD_CP:
        _add(BLUNDER, swing_cp, {"swing_cp": swing_cp})

    # 2. TACTICAL
    if len(candidates) >= 2:
        gap = candidates[1]["eval_loss"]
        top = candidates[0]
        if gap >= TACTICAL_GAP_CP and (top["is_check"] or top["is_capture"]):
            _add(TACTICAL, gap, {})

    # 3. ATTACK / DEFENSE
    opp_attacked, opp_defended = _king_safety_score(board, not my_side)
    if eval_cp >= ATTACK_DEFENSE_EVAL_CP and opp_attacked > opp_defended:
        _add(ATTACK, eval_cp, {"king_square": board.king(not my_side)})
    my_attacked, my_defended = _king_safety_score(board, my_side)
    if eval_cp <= -ATTACK_DEFENSE_EVAL_CP and my_attacked > my_defended:
        _add(DEFENSE, -eval_cp, {"king_square": board.king(my_side)})

    # 4. MISSED_OPPORTUNITY
    if swing_cp is not None and MISSED_OPPORTUNITY_MIN_CP <= swing_cp < BLUNDER_THRESHOLD_CP:
        _add(MISSED_OPPORTUNITY, swing_cp, {
            "swing_cp": swing_cp, "opponent_better_move_san": opponent_better_move_san,
        })

    # 5. ENDGAME / OPENING (phase -- exclusives entre elles, pas des autres)
    if phase == "endgame":
        _add(ENDGAME, 1.0, {"passed_pawn_square": _find_passed_pawn(board, my_side)})
    elif phase == "opening":
        _add(OPENING, 1.0, {})

    # 6. INITIATIVE_SHIFT
    if initiative_trend is not None and abs(eval_cp) >= INITIATIVE_EVAL_MIN_ABS_CP:
        losing_initiative = eval_cp > 0 and initiative_trend <= -INITIATIVE_SLOPE_CP
        gaining_initiative = eval_cp < 0 and initiative_trend >= INITIATIVE_SLOPE_CP
        if losing_initiative or gaining_initiative:
            _add(INITIATIVE_SHIFT, abs(initiative_trend), {"initiative_slope_cp": initiative_trend})

    # 7. STRATEGIC_ADVANTAGE (+ material_imbalance_kind + simplification_advice)
    if abs(eval_cp) >= STRATEGIC_EVAL_CP:
        _add(STRATEGIC_ADVANTAGE, abs(eval_cp), {
            "material_imbalance_kind": _material_imbalance_kind(board, my_side),
            "simplification_advice": _simplification_advice(initiative_trend),
        })

    # 8. PAWN_STRUCTURE (faiblesse adverse)
    weakness_sq, weakness_kind = _find_pawn_weakness(board, not my_side)
    if weakness_sq is not None:
        _add(PAWN_STRUCTURE, 1.0, {
            "pawn_weakness_square": weakness_sq, "pawn_weakness_kind": weakness_kind,
        })

    # 9. PIECE_ACTIVITY_GAP
    ratio = _activity_ratio(board, my_side)
    if ratio is not None and ratio >= ACTIVITY_GAP_RATIO:
        _add(PIECE_ACTIVITY_GAP, ratio, {"activity_ratio": ratio})

    # 10. KING_SAFETY_WARNING (mon roi d'abord, puis l'adverse)
    if _king_safety_warning(board, my_side, eval_cp, phase, ply_count):
        _add(KING_SAFETY_WARNING, 1.0, {
            "king_safety_warning_square": board.king(my_side), "king_safety_warning_is_mine": True,
        })
    elif _king_safety_warning(board, not my_side, -eval_cp, phase, ply_count):
        _add(KING_SAFETY_WARNING, 1.0, {
            "king_safety_warning_square": board.king(not my_side), "king_safety_warning_is_mine": False,
        })

    # 11. Filet neutre : seulement si AUCUNE autre brique n'a matché (comme
    #     detect_theme, EQUAL_POSITION n'a de sens qu'en dernier ressort).
    if not bricks:
        _add(EQUAL_POSITION, 0.0, {})

    # NOTE (câblage étape 5) : `caution` (risque de pat, voir
    # _stalemate_caution) n'est PAS porté par les briques -- c'est un
    # avertissement TRANSVERSAL, indépendant du thème retenu. Le futur
    # pipeline devra le calculer une seule fois à part (comme aujourd'hui
    # dans detect_theme) et l'attacher au commentaire final, pas à une
    # brique en particulier.
    return bricks
