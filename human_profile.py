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
    depth: int                  # profondeur d'analyse OBJECTIVE (précision de l'éval, pas la force jouée)
    max_eval_loss_cp: int       # tolérance MAXIMALE (centipawns) vs le meilleur coup -- jamais dépassée
    typical_eval_loss_cp: int   # perte "typique" ciblée à ce niveau, utilisée par certains profils


ELO_TIERS = {
    1: EloTier(id=1, label="1800-2200", elo_min=1800, elo_max=2200, elo_reference=2000,
               multipv=4, depth=14, max_eval_loss_cp=90, typical_eval_loss_cp=35),
    2: EloTier(id=2, label="2300-2700", elo_min=2300, elo_max=2700, elo_reference=2500,
               multipv=4, depth=18, max_eval_loss_cp=50, typical_eval_loss_cp=18),
    3: EloTier(id=3, label="2800-3200", elo_min=2800, elo_max=3200, elo_reference=3000,
               multipv=4, depth=22, max_eval_loss_cp=20, typical_eval_loss_cp=6),
}
DEFAULT_ELO_TIER = 2

# 0 = toujours le mieux noté par le profil (déterministe)
# 1 = quasi uniforme parmi les coups éligibles pour ce niveau
# Valeur modeste par défaut : la précision ne doit jamais être à 100%, sans
# pour autant rendre le coach imprévisible dès la phase 1.
DEFAULT_HUMANITY = 0.15


def _eligible_candidates(candidates, tier: EloTier):
    """
    Coups dont la perte d'éval reste dans la tolérance du niveau. Filet de
    sécurité : si AUCUN candidat n'entre dans la fenêtre (position très
    forcée où même le 2e coup perd beaucoup), on retombe sur le meilleur
    coup objectif plutôt que de proposer autre chose hors tolérance.
    """
    eligible = [c for c in candidates if c["eval_loss"] <= tier.max_eval_loss_cp]
    return eligible or candidates[:1]


def _score_solid(c, tier):
    """Vert : coups peu risqués, proches de l'optimal, pas de complications inutiles."""
    penalty = c["eval_loss"]
    if c["is_capture"] and c["eval_loss"] > tier.typical_eval_loss_cp:
        penalty += 15  # capture qui perd de l'éval = complication risquée, pas "solide"
    return -penalty


def _score_popular(c, tier, elo_suggestion_uci):
    """Bleu : ce qu'un moteur bridé à cet Elo jouerait naturellement (signal réaliste de fréquence de jeu à ce niveau)."""
    bonus = 40 if elo_suggestion_uci and c["move_uci"] == elo_suggestion_uci else 0
    return bonus - c["eval_loss"] * 0.6


def _score_creative(c, tier):
    """Rose : s'écarte un peu du choix "évident" tout en restant dans la tolérance -- capture, poussée centrale, coup moins attendu."""
    novelty = 0
    if c["eval_loss"] > 0:
        novelty += 10  # pas LE meilleur coup, un peu de personnalité
    if c["to_square_central"]:
        novelty += 8
    if c["is_capture"]:
        novelty += 6
    return novelty - c["eval_loss"] * 0.8


def _score_classical(c, tier, elo_suggestion_uci):
    """Noir & blanc : coup naturel/développement, proche du choix Elo-bridé, faible complexité tactique."""
    bonus = 25 if elo_suggestion_uci and c["move_uci"] == elo_suggestion_uci else 0
    simplicity = -5 if (c["is_capture"] or c["is_check"]) else 5
    return bonus + simplicity - c["eval_loss"] * 0.7


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
        scored = [(c, _score_creative(c, tier)) for c in eligible]
    elif profile_id == "classical":
        scored = [(c, _score_classical(c, tier, elo_suggestion_uci)) for c in eligible]
    else:
        raise ValueError(f"Profil de jeu inconnu : {profile_id!r}")

    return _softmax_pick(scored, humanity, rng)
