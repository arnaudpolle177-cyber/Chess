"""
human_profile.py
Sélection de coup "humaine" : à partir de plusieurs coups candidats
objectivement bons (MultiPV Stockfish), choisit lequel proposer pour chaque
profil de jeu, à l'intérieur d'une fenêtre de tolérance calée sur un niveau
Elo cible.

Principe (voir la conversation avec l'utilisateur pour le détail) :
- Le niveau Elo ne change JAMAIS la profondeur d'analyse "pour faire plus
  faible" : les candidats sont toujours analysés à pleine force
  (engine_analysis.analyze_candidates). Le niveau Elo définit une fenêtre de
  tolérance de perte d'éval (EloTier.max_eval_loss_cp) : les coups en dehors
  de cette fenêtre sont écartés AVANT même de regarder les profils -- ça
  garantit qu'aucun profil, à aucun niveau, ne peut proposer un coup trop
  faible pour ce niveau (pas de "gros blunder volontaire").
- Chaque profil a sa propre fonction de score qui classe les coups DÉJÀ
  éligibles pour ce niveau. Les profils ne changent JAMAIS la force, juste
  LEQUEL des bons coups est mis en avant.
- Le paramètre `humanity` (0.0-1.0) contrôle, une fois le classement du
  profil établi, si on prend toujours le mieux noté (déterministe) ou si on
  pioche parmi les coups éligibles pondérés par leur score (tirage
  "humain", jamais 100% prévisible). Fixé à une valeur modeste par défaut
  pour cette phase 1 (pas encore de curseur dans l'UI), mais déjà branché
  pour ne rien avoir à redéfinir plus tard.

Architecture évolutive :
- Ajouter un niveau Elo -> ajouter une entrée dans ELO_TIERS.
- Ajouter un profil de style -> ajouter une fonction _score_xxx() + une
  entrée dans PROFILE_IDS/select_move(). Rien d'autre à changer côté
  web_bridge.py (qui appelle juste select_move(profile_id=...)).
- Futur sélecteur de style -> profile_id devient un choix utilisateur au
  lieu d'être fixé par la boucle des 4 flèches.
- Futur curseur "Humanité" -> humanity devient un paramètre venant de l'UI
  au lieu de DEFAULT_HUMANITY.
"""
from dataclasses import dataclass, replace
import math
import random

import chess


@dataclass(frozen=True)
class EloTier:
    id: int
    label: str
    elo_min: int
    elo_max: int
    elo_reference: int         # valeur "typique" du niveau -- non utilisée par le moteur (voir historique), gardée pour référence/affichage futur
    multipv: int               # nombre de coups candidats analysés
    depth_min: int              # profondeur d'analyse OBJECTIVE -- bornes d'un tirage aléatoire
    depth_max: int              # (voir random_depth ci-dessous), jamais une valeur fixe coup après coup
    max_eval_loss_cp: int       # tolérance MAXIMALE (centipawns) vs le meilleur coup -- jamais dépassée
    typical_eval_loss_cp: int   # perte "typique" ciblée à ce niveau, utilisée par certains profils

    def random_depth(self, rng=None):
        """
        Tire une profondeur au hasard dans [depth_min, depth_max] à chaque
        appel. Volontaire : une profondeur légèrement variable d'un coup à
        l'autre (au lieu d'une valeur fixe) casse la régularité "toujours
        exactement depth 16" qui, elle aussi, a un petit côté "on sent que
        c'est un ordinateur". Tiré UNE fois par position (pas par profil) :
        voir le cache dans web_bridge.py, qui appelle analyze_candidates()
        une seule fois par (position, tier) et réutilise le résultat pour
        les 4 profils -- donc les 4 flèches d'un même coup partagent
        toujours la même profondeur, seul le coup SUIVANT en tire une autre.
        """
        rng = rng or random
        return rng.randint(self.depth_min, self.depth_max)


ELO_TIERS = {
    1: EloTier(id=1, label="1800-2200", elo_min=1800, elo_max=2200, elo_reference=2000,
               multipv=4, depth_min=12, depth_max=14,
               max_eval_loss_cp=90, typical_eval_loss_cp=35),
    2: EloTier(id=2, label="2300-2700", elo_min=2300, elo_max=2700, elo_reference=2500,
               multipv=5, depth_min=16, depth_max=19,
               max_eval_loss_cp=50, typical_eval_loss_cp=18),
    3: EloTier(id=3, label="2800-3200", elo_min=2800, elo_max=3200, elo_reference=3000,
               # multipv=3, depth 20-22 : le mode natif (Berserk) s'est
               # avéré rapide en pratique même à ce niveau -- les anciennes
               # valeurs plus prudentes dataient de l'époque des recherches
               # successives forcées (beaucoup plus lentes, abandonnées
               # comme mode par défaut depuis). Le mode sûr (recherches
               # successives) reste disponible en repli automatique pour
               # les rares positions à problème -- voir web_bridge.py,
               # _main_engine_degraded.
               multipv=3, depth_min=20, depth_max=22,
               max_eval_loss_cp=20, typical_eval_loss_cp=6),
}
DEFAULT_ELO_TIER = 2

# 0 = toujours le mieux noté par le profil (déterministe)
# 1 = quasi uniforme parmi les coups éligibles pour ce niveau
# Remonté par rapport à la version précédente (0.15) : avec un scoring en
# "bande ciblée" (voir _band_penalty ci-dessous), un humanity trop bas
# retombait presque toujours sur le même candidat -> retrouvait le
# problème "coup d'ordinateur". 0.35 laisse une vraie variation coup après
# coup, tout en restant dans la fenêtre Elo (jamais un blunder).
DEFAULT_HUMANITY = 0.35

# Probabilité (par coup, par profil) qu'une "petite inexactitude" se
# déclenche -- pousse la cible de perte typique près du PLAFOND du niveau
# (jamais au-delà) pour CE coup précis, plutôt que la cible habituelle plus
# proche de l'optimal. Contrairement au mélange continu de humanity ci-
# dessus (qui donne un style globalement "un peu imprécis" tout le temps),
# ceci crée des MOMENTS DISTINCTS de moindre précision -- comme un vrai
# joueur qui a des passages à vide, pas une dilution constante. Plus élevé
# aux niveaux faibles (les joueurs moins forts ont plus souvent des
# inexactitudes), plus rare au niveau 3. Chaque profil tire indépendamment
# -- rien n'empêche qu'un seul des 4 arrive à ce moment-là.
INACCURACY_CHANCE = {1: 0.30, 2: 0.20, 3: 0.10}


def _apply_inaccuracy_roll(tier, elo_tier_id, rng):
    """
    Tire au sort si CE coup est une "petite inexactitude" (voir
    INACCURACY_CHANCE). Si oui, retourne une version du niveau dont la
    cible de perte typique est poussée près de max_eval_loss_cp -- jamais
    au-delà, donc jamais un vrai blunder, juste un coup nettement moins
    précis que la normale pour ce niveau.
    """
    chance = INACCURACY_CHANCE.get(elo_tier_id, 0.2)
    if rng.random() >= chance:
        return tier, False
    boosted_typical = round(tier.max_eval_loss_cp * 0.85)
    if boosted_typical <= tier.typical_eval_loss_cp:
        return tier, False  # déjà proche du plafond à ce niveau (ex: niveau 3), rien à pousser de plus
    return replace(tier, typical_eval_loss_cp=boosted_typical), True


def compute_tightening_factor(board):
    """
    FEATURE : resserre automatiquement la fenêtre de tolérance Elo dans les
    positions où un humain n'a de toute façon pas vraiment le choix (échec
    avec peu de réponses, très peu de coups légaux). Dans ces positions, même
    un joueur plus faible trouve généralement LE bon coup -- il n'y a pas de
    vraie marge d'erreur "humaine" à simuler, contrairement à une position
    calme avec plein d'options raisonnables.

    Retourne un facteur multiplicatif (0 < f <= 1) appliqué à
    max_eval_loss_cp ET typical_eval_loss_cp du niveau choisi -- 1.0 = aucun
    resserrement (position normale, plein de choix).
    """
    try:
        n_legal = board.legal_moves.count()
    except Exception:
        return 1.0

    if board.is_check():
        if n_legal <= 2:
            return 0.25  # échec avec 1-2 réponses possibles : quasi forcé
        if n_legal <= 4:
            return 0.5

    if n_legal <= 3:
        return 0.35  # très peu de coups légaux, échec ou non
    if n_legal <= 6:
        return 0.65

    return 1.0


def _tiered_for_position(tier: EloTier, board):
    """
    Applique compute_tightening_factor() au niveau Elo choisi pour CETTE
    position précise, sans jamais modifier ELO_TIERS lui-même (EloTier est
    immuable -- dataclasses.replace() crée une copie ajustée à la volée).
    """
    if board is None:
        return tier
    factor = compute_tightening_factor(board)
    if factor >= 1.0:
        return tier
    return replace(
        tier,
        max_eval_loss_cp=max(5, round(tier.max_eval_loss_cp * factor)),
        typical_eval_loss_cp=max(2, round(tier.typical_eval_loss_cp * factor)),
    )



def _material_count(board):
    """Total des points de matériel sur l'échiquier (hors rois), les 2 camps confondus."""
    total = 0
    for piece_type, value in ((chess.PAWN, 1), (chess.KNIGHT, 3), (chess.BISHOP, 3), (chess.ROOK, 5), (chess.QUEEN, 9)):
        total += len(board.pieces(piece_type, chess.WHITE)) * value
        total += len(board.pieces(piece_type, chess.BLACK)) * value
    return total


def _game_phase(board):
    """
    "opening" / "middlegame" / "endgame", à partir du matériel restant sur
    l'échiquier (pas du numéro de coup -- pas fiable ici, voir
    chess_coach_bridge.user.js qui reconstruit toujours un FEN avec
    compteurs à "0 1"). Départ = 78 points de matériel (hors rois).
    """
    if board is None:
        return "middlegame"
    material = _material_count(board)
    if material >= 60:
        return "opening"
    if material >= 24:
        return "middlegame"
    return "endgame"


# Alias public : utilisé aussi par theme_detector.py (détection du thème
# principal de la position, partagée entre les 3 profils).
game_phase = _game_phase


def _eligible_candidates(candidates, tier: EloTier):
    """
    Coups dont la perte d'éval reste dans la tolérance du niveau. Filet de
    sécurité : si AUCUN candidat n'entre dans la fenêtre (position très
    forcée où même le 2e coup perd beaucoup), on retombe sur le meilleur
    coup objectif plutôt que de proposer autre chose hors tolérance.
    """
    eligible = [c for c in candidates if c["eval_loss"] <= tier.max_eval_loss_cp]
    return eligible or candidates[:1]


def _band_penalty(eval_loss, target_cp, factor):
    """
    Pénalité en "bande ciblée" plutôt qu'en minimisation pure : au lieu de
    toujours préférer eval_loss=0 (ce qui donne systématiquement le coup
    Stockfish exact, donc un jeu perçu comme "d'ordinateur"), on pénalise
    l'ÉCART à une perte d'éval typique visée (target_cp). Un candidat qui
    perd exactement target_cp est donc mieux noté qu'un candidat parfait
    (0 perte) -- reflète qu'un joueur humain ne trouve pas systématiquement
    LE coup optimal, sans jamais dépasser tier.max_eval_loss_cp (déjà filtré
    par _eligible_candidates en amont).
    """
    return -abs(eval_loss - target_cp) * factor


def _score_popular(c, tier):
    """
    Bleu -- PRAGMATIQUE : maximise les CHANCES DE GAIN PRATIQUES (WDL --
    Win/Draw/Loss), pas juste l'éval brute en centipawns, avec une cible de
    perte resserrée (proche de l'optimal) -- c'est le profil "sûr et
    efficace" : il absorbe ce que faisait avant le profil "solide" (retiré,
    jugé redondant), tout en gardant son signal principal propre (WDL, pas
    juste "perte d'éval minimale"). Pénalise en plus les captures qui
    perdent de l'éval (complication inutile, peu pragmatique).
    """
    target = tier.typical_eval_loss_cp * 0.55
    bonus = 0
    if c.get("win_prob") is not None:
        # Échelle empirique : +30 points pour un coup à 100% de chances de
        # gain plutôt que 0% -- suffisant pour départager des coups très
        # proches en éval sans jamais l'emporter sur le plafond Elo (déjà
        # filtré en amont).
        bonus += c["win_prob"] * 30
    if c["is_capture"] and c["eval_loss"] > tier.typical_eval_loss_cp:
        bonus -= 12  # capture qui perd de l'éval = complication risquée, pas pragmatique
    return bonus + _band_penalty(c["eval_loss"], target, 0.75)


def _score_creative(c, tier):
    """
    Rose -- TACTIQUE/SACRIFICIEL : favorise les coups forcing (échecs), les
    captures qui donnent du matériel pour l'initiative (esprit sacrifice --
    pièce de plus grande valeur qui en prend une de moindre valeur), et
    s'écarte activement de ce qui ressemble à un choix "pragmatique" (WDL
    élevé) ou "classique" (développement calme, roque) -- laisse ce terrain
    aux 2 autres profils. Vise délibérément une perte un peu plus élevée
    (plus de personnalité), jamais au-delà du plafond du niveau.
    """
    target = tier.typical_eval_loss_cp * 1.3
    novelty = 0
    if c["eval_loss"] > 0:
        novelty += 6  # pas LE meilleur coup, un peu de personnalité
    if c["is_check"]:
        novelty += 10  # coup forcing -- esprit intuitif/attaquant
    if c["is_capture"]:
        novelty += 6
        cv, mv = c.get("captured_piece_value"), c.get("moving_piece_value")
        if cv is not None and mv is not None and mv > cv:
            novelty += 14  # sacrifie du matériel pour l'initiative -- signature "créative"
    if c["to_square_central"]:
        novelty += 6
    if c.get("win_prob") is not None:
        novelty -= c["win_prob"] * 10  # justement pas le choix le plus "sûr en pratique"
    if c.get("is_developing_minor") or c.get("is_castle"):
        novelty -= 6  # laisse ce terrain-là à "classique"
    return novelty + _band_penalty(c["eval_loss"], target, 0.5)


def _score_classical(c, tier, phase, is_ahead):
    """
    Blanc -- TEXTBOOK, sensible à la PHASE DE PARTIE (contrairement aux 2
    autres profils, qui gardent le même principe du début à la fin) :
    - Ouverture : développement de pièce mineure, poussée centrale, roque,
      coups calmes -- les principes classiques d'ouverture.
    - Milieu de partie : coups calmes, échanges "propres" (pièce prise de
      valeur égale ou supérieure -- pas un sacrifice), roque encore valorisé
      s'il n'a pas encore eu lieu.
    - Finale : activation du roi vers le centre (LA technique classique de
      fin de partie), échanger les pièces quand on a l'avantage (simplifier
      pour convertir), reste globalement sobre.
    """
    target = tier.typical_eval_loss_cp * 0.6
    naturalness = 0

    if phase == "opening":
        if c["is_developing_minor"]:
            naturalness += 14
        if c["is_pawn_center_push"]:
            naturalness += 10
        if c["is_castle"]:
            naturalness += 12
        if not c["is_capture"] and not c["is_check"]:
            naturalness += 6
    elif phase == "endgame":
        if c["is_king_move"]:
            naturalness += 14 if c["to_square_central"] else 6  # roi actif vers le centre
        if c["is_capture"] and is_ahead:
            naturalness += 10  # simplifier pour convertir un avantage -- technique classique
        if not c["is_capture"] and not c["is_check"]:
            naturalness += 4
    else:  # middlegame
        if not c["is_capture"] and not c["is_check"]:
            naturalness += 6
        cv, mv = c.get("captured_piece_value"), c.get("moving_piece_value")
        if c["is_capture"] and cv is not None and mv is not None and cv >= mv:
            naturalness += 6  # échange "propre", pas un sacrifice
        if c["is_castle"]:
            naturalness += 8  # roque encore valorisé s'il n'a pas eu lieu

    return naturalness + _band_penalty(c["eval_loss"], target, 0.9)


# Ordre = ordre d'affichage (bleu, rose, blanc). Ajouter un profil =
# ajouter son id ici + son cas dans select_move() ci-dessous.
PROFILE_IDS = ("popular", "creative", "classical")


def _softmax_pick(scored, humanity, rng):
    if len(scored) == 1 or humanity <= 0.0:
        return max(scored, key=lambda pair: pair[1])[0]
    temperature = max(0.05, humanity) * 40.0  # échelle empirique en "points de score"
    scores = [s for _, s in scored]
    top = max(scores)
    weights = [math.exp((s - top) / temperature) for s in scores]
    total = sum(weights)
    r = rng.random() * total
    upto = 0.0
    for (cand, _), w in zip(scored, weights):
        upto += w
        if upto >= r:
            return cand
    return scored[-1][0]


def select_move(candidates, elo_tier_id, profile_id,
                 humanity=DEFAULT_HUMANITY, rng=None, board=None):
    """
    candidates : liste de dicts (voir ChessCoachEngine.analyze_candidates),
    déjà analysés à pleine force.
    board : position actuelle (chess.Board), optionnel -- sert à resserrer
    automatiquement la fenêtre de tolérance dans les positions très forcées
    (voir compute_tightening_factor). Si omis, aucun resserrement.
    Retourne UN candidat (dict), ou None si `candidates` est vide.
    """
    if not candidates:
        return None
    tier = ELO_TIERS.get(elo_tier_id, ELO_TIERS[DEFAULT_ELO_TIER])
    tier = _tiered_for_position(tier, board)
    rng = rng or random
    # Tiré APRÈS le resserrement en position forcée : dans une position où
    # il n'y a de toute façon presque pas de choix, il n'y a pas de vraie
    # marge pour une "inexactitude" non plus (déjà cohérent avec
    # compute_tightening_factor -- boosted_typical ne peut jamais dépasser
    # le plafond, lui-même déjà resserré dans ces positions).
    tier, is_inaccuracy = _apply_inaccuracy_roll(tier, elo_tier_id, rng)
    eligible = _eligible_candidates(candidates, tier)

    if profile_id == "popular":
        scored = [(c, _score_popular(c, tier)) for c in eligible]
    elif profile_id == "creative":
        scored = [(c, _score_creative(c, tier)) for c in eligible]
    elif profile_id == "classical":
        phase = _game_phase(board)
        # "En avantage" : le meilleur candidat objectif (candidates[0],
        # toujours trié meilleur -> moins bon) a une éval nettement positive
        # pour le camp au trait -- sert au profil classique en finale
        # (simplifier pour convertir un avantage, voir _score_classical).
        is_ahead = candidates[0].get("cp", 0) >= 150
        scored = [(c, _score_classical(c, tier, phase, is_ahead)) for c in eligible]
    else:
        raise ValueError(f"Profil de jeu inconnu : {profile_id!r}")

    chosen = _softmax_pick(scored, humanity, rng)
    if chosen is not None:
        chosen = dict(chosen)
        chosen["is_inaccuracy"] = is_inaccuracy
    return chosen
