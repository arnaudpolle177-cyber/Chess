"""
theme_scoring.py
Étape 2 du pipeline narration v2 (voir NARRATION_V2_PLAN.txt) : à partir de
la liste de briques produite par theme_detector.collect_theme_bricks(),
attribue à chacune un SCORE DE PERTINENCE GLOBAL, les classe, puis
sélectionne 1 thème PRINCIPAL + jusqu'à 2 thèmes SECONDAIRES compatibles.

Module VOLONTAIREMENT PUR : aucune dépendance à chess, au moteur, ni même à
un `board`. Il ne manipule que des ThemeCandidate (dataclass simple). C'est
ce qui le rend trivial à tester unitairement sur des entrées fabriquées à
la main, sans Stockfish ni position réelle -- l'étape idéale pour poser des
tests, comme prévu dans la roadmap.

⚠ ADDITIF ET NON BRANCHÉ : rien n'appelle encore ce module en production.
detect_theme() et la narration actuelle sont inchangées. Le câblage se fera
à l'étape 5.

Ce que ce module fait / ne fait PAS :
- FAIT : score = poids_du_tier + intensité_normalisée(0..99). Le poids du
  tier DOMINE toujours l'intensité (un thème "très forte priorité" passe
  devant n'importe quel thème d'enrichissement, quel que soit son signal
  brut -- exactement la priorisation demandée).
- FAIT : anti-redondance STRUCTURELLE à la sélection : deux briques de la
  même famille sémantique (voir theme_detector.THEME_FAMILY) ne peuvent pas
  être toutes deux retenues -- on garde la mieux scorée.
- NE FAIT PAS : le filtrage SÉMANTIQUE "ce secondaire améliore-t-il vraiment
  la compréhension ?" (consigne finale de la roadmap). Ça dépend de la
  RELATION entre principal et secondaire (cause/conséquence/moyen/...), qui
  vit dans la table de relations de l'étape 4 (weaver). Ici on ne fait que
  le filtre structurel (famille différente + score plancher) ; le point
  d'accroche pour brancher le filtre sémantique est marqué EXPLICITEMENT
  plus bas (voir select_lead_and_support, paramètre `relation_ok`).
"""
from theme_detector import (
    TIER_WEIGHT, theme_tier, theme_family,
    BLUNDER, TACTICAL, ATTACK, DEFENSE, MISSED_OPPORTUNITY,
    ENDGAME, OPENING, INITIATIVE_SHIFT, STRATEGIC_ADVANTAGE,
    PAWN_STRUCTURE, PIECE_ACTIVITY_GAP, KING_SAFETY_WARNING, EQUAL_POSITION,
    BLUNDER_THRESHOLD_CP, MISSED_OPPORTUNITY_MIN_CP, TACTICAL_GAP_CP,
    ATTACK_DEFENSE_EVAL_CP, STRATEGIC_EVAL_CP, INITIATIVE_SLOPE_CP,
    ACTIVITY_GAP_RATIO,
)


# ---------------------------------------------------------------------
# Normalisation de l'intensité (strength brut -> 0..99)
# ---------------------------------------------------------------------
# Chaque brique a un `strength` sur une échelle DIFFÉRENTE (centipawns pour
# BLUNDER/STRATEGIC, ratio pour PIECE_ACTIVITY_GAP, constante 1.0 pour les
# thèmes "présence/absence" comme ENDGAME). Pour les comparer AU SEIN d'un
# même tier, on ramène chaque strength sur une échelle commune 0..99 via un
# couple (plancher, saturation) :
#   - plancher   = le seuil de DÉCLENCHEMENT du thème (en dessous, il ne
#                  matche même pas -> l'intensité normalisée part de ~0 au
#                  seuil, cohérent).
#   - saturation = la valeur au-delà de laquelle "plus fort" n'ajoute plus
#                  d'information pédagogique utile (un blunder de 9 pions
#                  n'est pas "3x plus important à commenter" qu'un blunder
#                  de 3 pions -- les deux sont juste "gros").
# Les planchers réutilisent les constantes de theme_detector (source unique
# de vérité, pas de nombre recopié). Les saturations sont des choix de
# design assumés, ajustables -- regroupés ici pour être faciles à régler.

_SATURATION = {
    BLUNDER: 900,               # ~9 pions d'un coup : écrasant
    TACTICAL: 600,              # écart 1er/2e candidat
    ATTACK: 600,
    DEFENSE: 600,
    MISSED_OPPORTUNITY: BLUNDER_THRESHOLD_CP,  # borné par le haut de sa bande (150)
    INITIATIVE_SHIFT: 150,      # pente cp/coup ; au-delà c'est déjà un basculement franc
    STRATEGIC_ADVANTAGE: 600,
    PIECE_ACTIVITY_GAP: 2.5,    # ratio de mobilité pondérée (mien/adverse)
}

_FLOOR = {
    BLUNDER: BLUNDER_THRESHOLD_CP,
    TACTICAL: TACTICAL_GAP_CP,
    ATTACK: ATTACK_DEFENSE_EVAL_CP,
    DEFENSE: ATTACK_DEFENSE_EVAL_CP,
    MISSED_OPPORTUNITY: MISSED_OPPORTUNITY_MIN_CP,
    INITIATIVE_SHIFT: INITIATIVE_SLOPE_CP,
    STRATEGIC_ADVANTAGE: STRATEGIC_EVAL_CP,
    PIECE_ACTIVITY_GAP: ACTIVITY_GAP_RATIO,
}

# Intensité fixe pour les thèmes "présence/absence" dont le strength est une
# constante 1.0 (ENDGAME, OPENING, PAWN_STRUCTURE, KING_SAFETY_WARNING) :
# ils n'ont pas d'échelle continue, leur pertinence est binaire. Valeur
# médiane volontaire -- ils ne dominent pas artificiellement leur tier, et
# le tier reste le facteur discriminant principal.
_CONSTANT_INTENSITY = 50.0

INTENSITY_MAX = 99.0  # borne haute (le tier_weight commence à 100, jamais de chevauchement)


def _normalized_intensity(candidate):
    """
    Ramène candidate.strength sur 0..INTENSITY_MAX selon le couple
    (plancher, saturation) du thème. Thèmes à strength constant -> intensité
    fixe médiane. EQUAL_POSITION (filet neutre) -> 0. Jamais d'exception :
    un thème inconnu retombe proprement sur l'intensité constante.
    """
    theme = candidate.theme
    if theme == EQUAL_POSITION:
        return 0.0
    floor = _FLOOR.get(theme)
    ceil = _SATURATION.get(theme)
    if floor is None or ceil is None:
        # Thème "présence/absence" (ou inconnu) : pas d'échelle continue.
        return _CONSTANT_INTENSITY
    if ceil <= floor:
        return _CONSTANT_INTENSITY  # garde-fou : spec incohérente -> médiane plutôt que division absurde
    frac = (candidate.strength - floor) / (ceil - floor)
    frac = max(0.0, min(1.0, frac))  # clamp
    return frac * INTENSITY_MAX


def _tier_of(candidate):
    """
    Tier d'une brique : lit d'abord `_tier` posé par collect_theme_bricks
    (source directe), sinon retombe sur theme_tier(theme) -- robuste aux
    ThemeCandidate venant d'une autre source (ex: all_candidates de
    detect_theme, qui ne posent pas `_tier`).
    """
    tier = candidate.fields.get("_tier") if candidate.fields else None
    return tier or theme_tier(candidate.theme)


def _family_of(candidate):
    """Famille d'une brique : `_family` posé par collect_theme_bricks, sinon theme_family(theme)."""
    fam = candidate.fields.get("_family") if candidate.fields else None
    return fam or theme_family(candidate.theme)


def score_brick(candidate):
    """
    Score de pertinence GLOBAL d'une brique : poids_du_tier +
    intensité_normalisée(0..99). Le poids du tier (1000 / 500 / 100) domine
    toujours l'intensité (< 100), donc l'ordre entre tiers est garanti et
    l'intensité ne fait que départager À L'INTÉRIEUR d'un tier.

    Retourne un float. Pur : ne dépend que du ThemeCandidate.
    """
    tier_weight = TIER_WEIGHT.get(_tier_of(candidate), TIER_WEIGHT["enrichment"])
    return tier_weight + _normalized_intensity(candidate)


def rank_bricks(candidates):
    """
    Trie les briques par score décroissant. Tri STABLE : à score égal,
    l'ordre d'origine (celui de collect_theme_bricks, qui suit
    PRIORITY_ORDER) est préservé -- ça donne un départage déterministe et
    lisible plutôt qu'un ordre arbitraire. Ne modifie pas la liste d'entrée.

    Retourne une nouvelle liste de (candidate, score), du plus au moins
    pertinent.
    """
    scored = [(c, score_brick(c)) for c in candidates]
    scored.sort(key=lambda pair: pair[1], reverse=True)  # sort() est stable
    return scored


# Score plancher qu'un SECONDAIRE doit dépasser pour valoir la peine d'être
# mentionné. Fixé juste au-dessus du plancher d'un tier d'enrichissement
# (100 + intensité) : un enrichissement à intensité quasi nulle n'apporte
# rien, on l'écarte. Volontairement bas -- le vrai tri "est-ce utile ?" est
# surtout sémantique (relation, étape 4) ; ici on ne coupe que le bruit.
SUPPORT_SCORE_FLOOR = 120.0


def select_lead_and_support(candidates, max_supports=2, relation_ok=None):
    """
    Sélectionne 1 thème PRINCIPAL + jusqu'à `max_supports` SECONDAIRES à
    partir de la liste de briques (voir theme_detector.collect_theme_bricks).

    Règles (voir NARRATION_V2_PLAN.txt, section 3.2) :
    - Le PRINCIPAL est la brique au score le plus élevé.
    - Un SECONDAIRE est retenu seulement si TOUTES ces conditions tiennent :
        (a) son score >= SUPPORT_SCORE_FLOOR (pas du bruit) ;
        (b) sa famille est DIFFÉRENTE de celle du principal ET de celles des
            secondaires déjà retenus (anti-redondance : deux briques de la
            même famille racontent la même idée) ;
        (c) relation_ok(lead_theme, support_theme) est vrai -- CROCHET pour
            le filtre SÉMANTIQUE de l'étape 4 (le secondaire enrichit-il
            vraiment le principal, ou est-ce du remplissage ?). Par défaut
            (relation_ok=None) ce filtre est neutre (toujours vrai) : à ce
            stade du pipeline, seul le filtre structurel est actif.
    - 0 secondaire est un résultat PARFAITEMENT VALIDE (consigne finale :
      mieux vaut un excellent commentaire à une seule idée qu'un commentaire
      artificiellement enrichi).

    Ne retourne JAMAIS None pour le principal tant que `candidates` est non
    vide (collect_theme_bricks garantit au moins EQUAL_POSITION). Si
    `candidates` est vide, retourne (None, []) -- l'appelant décide quoi
    faire (ne devrait pas arriver via collect_theme_bricks).

    Retourne (lead_candidate, [support_candidate, ...]).
    Pur : ne dépend que des ThemeCandidate.
    """
    if not candidates:
        return None, []

    ranked = rank_bricks(candidates)
    lead, _lead_score = ranked[0]
    lead_family = _family_of(lead)

    supports = []
    used_families = {lead_family}
    for cand, sc in ranked[1:]:
        if len(supports) >= max_supports:
            break
        if sc < SUPPORT_SCORE_FLOOR:
            continue  # bruit -- pas assez pertinent pour être mentionné
        fam = _family_of(cand)
        if fam in used_families:
            continue  # même idée qu'une brique déjà retenue (anti-redondance)
        if relation_ok is not None and not relation_ok(lead.theme, cand.theme):
            continue  # crochet sémantique (étape 4) : ce secondaire n'enrichit pas vraiment le principal
        supports.append(cand)
        used_families.add(fam)

    return lead, supports
