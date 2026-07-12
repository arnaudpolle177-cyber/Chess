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
from dataclasses import dataclass
import math
import random


@dataclass(frozen=True)
class EloTier:
    id: int
    label: str
    elo_min: int
    elo_max: int
    elo_reference: int        # valeur envoyée à Stockfish (UCI_Elo) pour les profils "populaire"/"classique"
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
               multipv=4, depth_min=11, depth_max=13, max_eval_loss_cp=90, typical_eval_loss_cp=35),
    2: EloTier(id=2, label="2300-2700", elo_min=2300, elo_max=2700, elo_reference=2500,
               multipv=4, depth_min=15, depth_max=17, max_eval_loss_cp=50, typical_eval_loss_cp=18),
    3: EloTier(id=3, label="2800-3200", elo_min=2800, elo_max=3200, elo_reference=3000,
               # Plafonné à 19 (pas 20) : depth 20 en multipv=4 est nettement
               # plus lent pour un gain de précision marginal à ce niveau
               # déjà quasi-parfait -- pas un bon rapport temps/qualité.
               multipv=4, depth_min=18, depth_max=19, max_eval_loss_cp=20, typical_eval_loss_cp=6),
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


def _score_popular(c, tier, elo_suggestion_uci):
    """
    Bleu : CE QUE JOUERAIT UN MOTEUR BRIDÉ À CET ELO -- signal réaliste de
    fréquence de jeu à ce niveau. Signal PRINCIPAL propre à ce profil (les
    autres profils ne l'utilisent plus, pour éviter qu'ils convergent tous
    sur le même coup).
    """
    target = tier.typical_eval_loss_cp * 0.8
    bonus = 22 if elo_suggestion_uci and c["move_uci"] == elo_suggestion_uci else 0
    return bonus + _band_penalty(c["eval_loss"], target, 0.7)


def _score_creative(c, tier, elo_suggestion_uci):
    """
    Rose : s'écarte du choix "évident" (le meilleur ET celui du moteur
    bridé) -- capture, poussée centrale, coup moins attendu -- tout en
    restant dans la tolérance. Vise délibérément une perte un peu plus
    élevée que "popular"/"classical" (plus de personnalité, jamais au-delà
    du plafond du niveau).
    """
    target = tier.typical_eval_loss_cp * 1.3
    novelty = 0
    if c["eval_loss"] > 0:
        novelty += 8  # pas LE meilleur coup, un peu de personnalité
    if c["to_square_central"]:
        novelty += 8
    if c["is_capture"]:
        novelty += 6
    if elo_suggestion_uci and c["move_uci"] == elo_suggestion_uci:
        novelty -= 10  # justement PAS le choix "populaire" -- sinon ça reconverge avec ce profil
    return novelty + _band_penalty(c["eval_loss"], target, 0.5)


def _score_classical(c, tier):
    """
    Blanc : coup NATUREL/développement/textbook -- basé sur des traits
    INTRINSÈQUES au coup (développement de pièce mineure, poussée centrale,
    roque, absence de complication tactique), PAS sur l'avis Elo-bridé
    (contrairement à avant, où ce profil utilisait EXACTEMENT le même
    signal que "populaire" et convergeait presque toujours sur le même
    coup). C'est ce qui rend ce profil vraiment différent de "populaire".
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


def select_move(candidates, elo_tier_id, profile_id, elo_suggestion_uci=None,
                 humanity=DEFAULT_HUMANITY, rng=None):
    """
    candidates : liste de dicts (voir ChessCoachEngine.analyze_candidates),
    déjà analysés à pleine force.
    Retourne UN candidat (dict), ou None si `candidates` est vide.
    """
    if not candidates:
        return None
    tier = ELO_TIERS.get(elo_tier_id, ELO_TIERS[DEFAULT_ELO_TIER])
    eligible = _eligible_candidates(candidates, tier)
    rng = rng or random

    if profile_id == "solid":
        scored = [(c, _score_solid(c, tier)) for c in eligible]
    elif profile_id == "popular":
        scored = [(c, _score_popular(c, tier, elo_suggestion_uci)) for c in eligible]
    elif profile_id == "creative":
        scored = [(c, _score_creative(c, tier, elo_suggestion_uci)) for c in eligible]
    elif profile_id == "classical":
        scored = [(c, _score_classical(c, tier)) for c in eligible]
    else:
        raise ValueError(f"Profil de jeu inconnu : {profile_id!r}")

    return _softmax_pick(scored, humanity, rng)
