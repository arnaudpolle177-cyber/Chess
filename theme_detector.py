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
EQUAL_POSITION = "EQUAL_POSITION"

PRIORITY_ORDER = (
    BLUNDER, TACTICAL, ATTACK, DEFENSE, MISSED_OPPORTUNITY,
    ENDGAME, OPENING, STRATEGIC_ADVANTAGE, EQUAL_POSITION,
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
    caution: Optional[str] = None  # avertissement transversal (ex: "stalemate_risk"), indépendant du thème principal


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


def _has_bishop_pair_advantage(board, color):
    """
    Vrai si `color` possède ses 2 fous ET que l'adversaire n'a pas les
    siens -- avantage positionnel classique (asymétrique : les 2 camps
    ayant leurs 2 fous chacun n'est pas un avantage différenciant).
    """
    mine = len(board.pieces(chess.BISHOP, color))
    theirs = len(board.pieces(chess.BISHOP, not color))
    return mine >= 2 and theirs < 2


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
    """
    my_side = board.turn
    eval_cp = candidates[0]["cp"] if candidates else 0
    phase = _game_phase(board)
    caution = _stalemate_caution(board, my_side, eval_cp, phase)

    # 1. BLUNDER -- priorité maximale : l'adversaire vient de se tromper nettement.
    if swing_cp is not None and swing_cp >= BLUNDER_THRESHOLD_CP:
        return ThemeResult(BLUNDER, eval_cp, swing_cp=swing_cp, phase=phase, caution=caution)

    # 2. TACTICAL -- un seul coup se démarque nettement des autres, et
    #    c'est un coup forcing (échec ou capture).
    if len(candidates) >= 2:
        gap = candidates[1]["eval_loss"]  # perte du 2e par rapport au 1er
        top = candidates[0]
        if gap >= TACTICAL_GAP_CP and (top["is_check"] or top["is_capture"]):
            return ThemeResult(TACTICAL, eval_cp, phase=phase, caution=caution)

    # 3. ATTACK / DEFENSE -- avantage net + roi (adverse ou le mien) exposé.
    opp_attacked, opp_defended = _king_safety_score(board, not my_side)
    if eval_cp >= ATTACK_DEFENSE_EVAL_CP and opp_attacked > opp_defended:
        return ThemeResult(ATTACK, eval_cp, king_square=board.king(not my_side), phase=phase, caution=caution)

    my_attacked, my_defended = _king_safety_score(board, my_side)
    if eval_cp <= -ATTACK_DEFENSE_EVAL_CP and my_attacked > my_defended:
        return ThemeResult(DEFENSE, eval_cp, king_square=board.king(my_side), phase=phase, caution=caution)

    # 4. MISSED_OPPORTUNITY -- L'ADVERSAIRE n'a pas complètement craqué
    #    (sinon ce serait BLUNDER, déjà écarté au point 1), mais n'a pas
    #    non plus joué la ligne la plus incisive qu'il avait à ce moment-là
    #    -- il reste de la marge à exploiter maintenant. Volontairement PAS
    #    basé sur le coup du joueur lui-même (voir docstring plus haut).
    if swing_cp is not None and MISSED_OPPORTUNITY_MIN_CP <= swing_cp < BLUNDER_THRESHOLD_CP:
        return ThemeResult(MISSED_OPPORTUNITY, eval_cp, swing_cp=swing_cp, phase=phase,
                            opponent_better_move_san=opponent_better_move_san, caution=caution)

    # 5. ENDGAME / OPENING -- phase de partie.
    if phase == "endgame":
        passed_sq = _find_passed_pawn(board, my_side)
        return ThemeResult(ENDGAME, eval_cp, phase=phase, passed_pawn_square=passed_sq, caution=caution)
    if phase == "opening":
        return ThemeResult(OPENING, eval_cp, phase=phase, caution=caution)

    # 6. STRATEGIC_ADVANTAGE -- avantage net mais sans motif tactique immédiat.
    if abs(eval_cp) >= STRATEGIC_EVAL_CP:
        return ThemeResult(STRATEGIC_ADVANTAGE, eval_cp, phase=phase,
                            has_bishop_pair=_has_bishop_pair_advantage(board, my_side), caution=caution)

    # 7. Filet de sécurité : position jugée équilibrée.
    return ThemeResult(EQUAL_POSITION, eval_cp, phase=phase, caution=caution)
