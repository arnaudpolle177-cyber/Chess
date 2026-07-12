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


def _score_solid(c, tier):
    """
    Vert : coups sûrs, peu de complications, mais pas obligatoirement LE
    meilleur coup -- vise une perte d'éval modeste (proche de zéro sans y
    être collé) plutôt que le zéro absolu.
    """
    target = tier.typical_eval_loss_cp * 0.35
    penalty = _band_penalty(c["eval_loss"], target, 1.1)
    if c["is_capture"] and c["eval_loss"] > tier.typical_eval_loss_cp:
        penalty -= 15  # capture qui perd beaucoup d'éval = complication risquée, pas "solide"
    return penalty


def _score_popular(c, tier):
    """
    Bleu : maximise les CHANCES DE GAIN PRATIQUES (WDL -- Win/Draw/Loss),
    pas juste l'éval brute en centipawns. Signal PRINCIPAL propre à ce
    profil : un coup qui garde une position plus "convertible" en pratique
    (moins de risque de nulle, chances de gain solides) plutôt que le coup
    mathématiquement optimal mais délicat à jouer -- une différence
    reconnaissable de "comment les joueurs choisissent en pratique".

    (Avant, ce profil s'ancrait sur l'avis d'un Stockfish bridé en Elo
    -- UCI_LimitStrength/UCI_Elo, des extensions propres à Stockfish,
    absentes de la plupart des autres moteurs. Remplacé par un signal WDL,
    une option UCI standard supportée par la majorité des moteurs modernes,
    y compris Berserk.)
    """
    target = tier.typical_eval_loss_cp * 0.8
    win_bonus = 0
    if c.get("win_prob") is not None:
        # Échelle empirique : +30 points pour un coup à 100% de chances de
        # gain plutôt que 0% -- suffisant pour départager des coups très
        # proches en éval sans jamais l'emporter sur le plafond Elo (déjà
        # filtré en amont).
        win_bonus = c["win_prob"] * 30
    return win_bonus + _band_penalty(c["eval_loss"], target, 0.7)


def _score_creative(c, tier):
    """
    Rose : s'écarte du choix "évident" (le meilleur ET le plus "convertible"
    en pratique) -- capture, poussée centrale, coup moins attendu -- tout
    en restant dans la tolérance. Vise délibérément une perte un peu plus
    élevée que "popular"/"classical" (plus de personnalité, jamais au-delà
    du plafond du niveau).

    Volontairement PAS de bonus de cohérence de plan ici : ce profil vise
    justement à s'écarter de l'évident -- rester dans le même secteur du
    plateau à chaque coup irait à l'encontre de son principe.
    """
    target = tier.typical_eval_loss_cp * 1.3
    novelty = 0
    if c["eval_loss"] > 0:
        novelty += 8  # pas LE meilleur coup, un peu de personnalité
    if c["to_square_central"]:
        novelty += 8
    if c["is_capture"]:
        novelty += 6
    if c.get("win_prob") is not None:
        novelty -= c["win_prob"] * 10  # justement PAS le choix le plus "sûr en pratique" -- sinon ça reconverge avec "populaire"
    return novelty + _band_penalty(c["eval_loss"], target, 0.5)


def _score_classical(c, tier):
    """
    Blanc : coup NATUREL/développement/textbook -- basé sur des traits
    INTRINSÈQUES au coup (développement de pièce mineure, poussée centrale,
    roque, absence de complication tactique). Signal totalement indépendant
    des autres profils -- c'est ce qui le rend vraiment différent de
    "populaire" (WDL) et "solide" (perte d'éval minimale).
    """
    target = tier.typical_eval_loss_cp * 0.6
    naturalness = 0
    if c["is_developing_minor"]:
        naturalness += 14
    if c["is_pawn_center_push"]:
        naturalness += 10
    if c["is_castle"]:
        naturalness += 12
    if not c["is_capture"] and not c["is_check"]:
        naturalness += 6  # coup calme, pas de complication tactique forcée
    return naturalness + _band_penalty(c["eval_loss"], target, 0.9)


# Ordre = ordre d'affichage (vert, bleu, rose, noir&blanc). Ajouter un
# profil = ajouter son id ici + son cas dans select_move() ci-dessous.
PROFILE_IDS = ("solid", "popular", "creative", "classical")


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

    if profile_id == "solid":
        scored = [(c, _score_solid(c, tier)) for c in eligible]
    elif profile_id == "popular":
        scored = [(c, _score_popular(c, tier)) for c in eligible]
    elif profile_id == "creative":
        scored = [(c, _score_creative(c, tier)) for c in eligible]
    elif profile_id == "classical":
        scored = [(c, _score_classical(c, tier)) for c in eligible]
    else:
        raise ValueError(f"Profil de jeu inconnu : {profile_id!r}")

    chosen = _softmax_pick(scored, humanity, rng)
    if chosen is not None:
        chosen = dict(chosen)
        chosen["is_inaccuracy"] = is_inaccuracy
    return chosen
