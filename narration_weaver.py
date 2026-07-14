"""
narration_weaver.py
Étape 4 du pipeline narration v2 (voir NARRATION_V2_PLAN.txt) : LE WEAVER.

Prend le résultat de la sélection (1 thème PRINCIPAL + 0..2 SECONDAIRES, voir
theme_scoring.select_lead_and_support) et les FRAGMENTS de chaque brique
(voir fragment_library.fragments_for), et les tisse en UNE seule pensée
fluide de 2 à 4 phrases -- pas une liste de commentaires collés.

PRINCIPE (voir roadmap §3.3/3.4) :
  paragraphe = observation(principal)
             [+ connecteur(relation) + observation(secondaire)]*
             + plan(principal)

- Le PLAN vient TOUJOURS du principal : le paragraphe reste centré sur une
  seule idée directrice, même quand des secondaires l'enrichissent.
- Le CONNECTEUR entre principal et secondaire est choisi selon la RELATION
  sémantique entre leurs deux FAMILLES (table petite et éparse ci-dessous ;
  défaut = connecteur neutre "Par ailleurs"). C'est ce qui évite l'effet
  "deux commentaires juxtaposés" : le lien exprime POURQUOI le secondaire
  éclaire le principal (cause, conséquence, moyen, contraste, renforcement).
- Tout est décliné par VOIX (popular/creative/classical) via les fragments ;
  le weaver lui-même reste surtout structurel (les connecteurs varient peu
  par voix, volontairement -- la couleur vient des fragments).

ADDITIF ET NON BRANCHÉ : rien n'appelle encore ce module en production. Le
câblage dans web_bridge.py est l'étape 5. generate_narration (narration.py)
reste la façade de l'affichage actuel.

CONTRAINTE ADN : le weaver n'invente RIEN -- il ne fait qu'assembler des
fragments déjà ancrés sur des champs réels (voir fragment_library). Il
n'ajoute aucune donnée d'échecs, seulement des mots de liaison.
"""
from theme_detector import (
    theme_family,
    FAMILY_OPPONENT_MOVE, FAMILY_TACTICS, FAMILY_KING, FAMILY_ADVANTAGE,
    FAMILY_STRUCTURE, FAMILY_ACTIVITY, FAMILY_DYNAMICS, FAMILY_PHASE,
    FAMILY_NEUTRAL,
)
import fragment_library as fl


# ---------------------------------------------------------------------
# Types de relation (voir roadmap §3.3). Suffisants d'après la roadmap :
# cause / conséquence / moyen / contraste / renforcement, + neutre par défaut.
# ---------------------------------------------------------------------
CAUSE = "cause"              # le secondaire EXPLIQUE le principal ("...car...")
CONSEQUENCE = "consequence"  # le secondaire DÉCOULE du principal ("...ce qui fait que...")
MEANS = "means"              # le principal est un problème, le secondaire un MOYEN ("...heureusement...")
CONTRAST = "contrast"        # le secondaire NUANCE le principal ("...mais...")
REINFORCE = "reinforce"      # le secondaire RENFORCE le principal ("...et surtout...")
NEUTRAL = "neutral"          # aucun lien fort -> phrase à part ("Par ailleurs...")


# Table de relations, indexée par (famille_principal, famille_secondaire).
# VOLONTAIREMENT PETITE ET ÉPARSE : seules les paires qui ont un vrai lien
# pédagogique sont listées ; tout le reste retombe sur NEUTRAL (voir
# relation_between). Les familles viennent de theme_detector.THEME_FAMILY.
#
# Lecture des choix :
# - (advantage, structure) = CAUSE : "tu es mieux ... car sa structure est faible".
# - (advantage, activity)  = REINFORCE : l'activité renforce l'avantage général.
# - (advantage, dynamics)  = CONTRAST : mieux, MAIS l'élan est en train de bouger.
# - (structure, activity)  = CONSEQUENCE : la faiblesse ouvre le jeu à tes pièces.
# - (king, activity)       = MEANS : roi (à attaquer/défendre) + tes pièces = le moyen.
# - (king, structure)      = REINFORCE : cible de roi + faiblesse structurelle vont ensemble.
# - (tactics, king)        = MEANS : le coup tactique VISE le roi.
# - (opponent_move, *)     = CONSEQUENCE : l'erreur adverse a PRODUIT l'avantage / la cible.
# - (phase, advantage)     = REINFORCE : la finale/l'ouverture cadre l'avantage.
_RELATIONS = {
    (FAMILY_ADVANTAGE, FAMILY_STRUCTURE): CAUSE,
    (FAMILY_ADVANTAGE, FAMILY_ACTIVITY): REINFORCE,
    (FAMILY_ADVANTAGE, FAMILY_DYNAMICS): CONTRAST,
    (FAMILY_ADVANTAGE, FAMILY_KING): REINFORCE,
    (FAMILY_STRUCTURE, FAMILY_ACTIVITY): CONSEQUENCE,
    (FAMILY_STRUCTURE, FAMILY_ADVANTAGE): CAUSE,
    (FAMILY_KING, FAMILY_ACTIVITY): MEANS,
    (FAMILY_KING, FAMILY_STRUCTURE): REINFORCE,
    (FAMILY_KING, FAMILY_DYNAMICS): CONTRAST,
    (FAMILY_TACTICS, FAMILY_KING): MEANS,
    (FAMILY_TACTICS, FAMILY_ACTIVITY): MEANS,
    (FAMILY_OPPONENT_MOVE, FAMILY_ADVANTAGE): CONSEQUENCE,
    (FAMILY_OPPONENT_MOVE, FAMILY_KING): CONSEQUENCE,
    (FAMILY_OPPONENT_MOVE, FAMILY_STRUCTURE): CONSEQUENCE,
    (FAMILY_OPPONENT_MOVE, FAMILY_ACTIVITY): CONSEQUENCE,
    (FAMILY_PHASE, FAMILY_ADVANTAGE): REINFORCE,
    (FAMILY_PHASE, FAMILY_STRUCTURE): REINFORCE,
    (FAMILY_PHASE, FAMILY_ACTIVITY): REINFORCE,
    (FAMILY_DYNAMICS, FAMILY_ADVANTAGE): REINFORCE,
}


def relation_between(lead_theme, support_theme):
    """
    Type de relation entre le thème PRINCIPAL et un SECONDAIRE, d'après leurs
    familles (voir _RELATIONS). Défaut : NEUTRAL (aucun lien fort listé) --
    le secondaire sera alors une phrase à part introduite par un connecteur
    neutre, plutôt que tissé dans la phrase du principal.

    C'est aussi le crochet `relation_ok` attendu par
    theme_scoring.select_lead_and_support si on veut filtrer les secondaires
    "sans vraie relation" : voir relation_is_useful ci-dessous.
    """
    lf = theme_family(lead_theme)
    sf = theme_family(support_theme)
    return _RELATIONS.get((lf, sf), NEUTRAL)


def relation_is_useful(lead_theme, support_theme):
    """
    Crochet sémantique pour select_lead_and_support(relation_ok=...) : un
    secondaire n'est VRAIMENT utile que s'il entretient une relation listée
    (non NEUTRAL) avec le principal. Branché à l'étape 5, il applique la
    "consigne finale" de la roadmap : mieux vaut 0 secondaire qu'un
    secondaire de remplissage. Laissé optionnel (le filtre structurel
    famille+plancher fonctionne déjà sans lui) mais disponible dès
    maintenant.
    """
    return relation_between(lead_theme, support_theme) != NEUTRAL


# ---------------------------------------------------------------------
# Connecteurs.
# INLINE : attachent le PREMIER secondaire DANS la phrase du principal (une
#   seule pensée compound). Minuscules, commencent par la ponctuation de
#   liaison. Le fragment secondaire (clause minuscule) suit directement.
# SENTENCE : introduisent un secondaire comme une PHRASE À PART (2e
#   secondaire, ou secondaire de relation neutre). Capitalisés, suivis d'une
#   virgule puis de la clause.
# ---------------------------------------------------------------------
_INLINE_CONNECTORS = {
    CAUSE: ", car ",
    CONSEQUENCE: ", ce qui fait que ",
    MEANS: " ; heureusement, ",
    CONTRAST: ", mais ",
    REINFORCE: ", et surtout ",
}

_SENTENCE_CONNECTORS = {
    CAUSE: "En effet",
    CONSEQUENCE: "Du coup",
    MEANS: "Justement",
    CONTRAST: "En revanche",
    REINFORCE: "Et surtout",
    NEUTRAL: "Par ailleurs",
}


def _capitalize(clause):
    """Met la 1re lettre en majuscule sans toucher au reste (préserve un SAN
    déjà capitalisé plus loin dans la clause). Chaîne vide -> inchangée."""
    if not clause:
        return clause
    return clause[0].upper() + clause[1:]


def _lead_sentence(lead_frag, inline_support_frag, inline_relation):
    """
    Construit la 1re phrase : observation du principal, éventuellement
    enrichie SOIT d'un secondaire tissé inline (si un secondaire à relation
    forte existe), SOIT de la CAUSE du principal (sinon). On ne met jamais
    les deux -- garder la phrase respirable (voir fragment_library : la cause
    est une apposition, le secondaire inline est une proposition).
    """
    obs = lead_frag["observation"]
    if inline_support_frag is not None:
        conn = _INLINE_CONNECTORS.get(inline_relation, ", et ")
        return _capitalize(obs + conn + inline_support_frag["observation"])
    if lead_frag.get("cause"):
        return _capitalize(obs + " -- " + lead_frag["cause"])
    return _capitalize(obs)


def weave(lead, supports, voice, ctx=None, caution_text=None):
    """
    Tisse le commentaire final v2.

    lead     : ThemeCandidate principal (voir theme_scoring.select_lead_and_support).
    supports : liste de 0 à 2 ThemeCandidate secondaires (déjà filtrés :
               familles distinctes, au-dessus du plancher de score).
    voice    : "popular" / "creative" / "classical".
    ctx      : fragment_library.FragmentContext (coup joué, motif why, éval) --
               None accepté (fragments en formulation générale).
    caution_text : avertissement TRANSVERSAL déjà rendu en texte (ex: risque
               de pat), indépendant du thème -- NON tissé dans le paragraphe
               (voir roadmap §5 : le caution est à part). Renvoyé tel quel
               dans le résultat pour que l'UI l'affiche distinctement, comme
               aujourd'hui.

    Retour : dict
      {
        "text": <paragraphe de 2 à 4 phrases, ponctué>,
        "lead": <thème principal>,
        "supports": [<thèmes secondaires>...],
        "voice": <voix>,
        "caution": <caution_text ou None>,
      }
    Si lead est None (sélection vide -- ne devrait pas arriver via
    collect_theme_bricks qui garantit EQUAL_POSITION), retourne un dict avec
    text="" -- l'appelant décide du repli.
    """
    if lead is None:
        return {"text": "", "lead": None, "supports": [], "voice": voice, "caution": caution_text}

    supports = list(supports or [])
    lead_frag = fl.fragments_for(lead, voice, ctx)

    # Le 1er secondaire à relation FORTE (non neutre) est tissé inline ; sinon
    # il devient une phrase à part. Les secondaires suivants sont toujours des
    # phrases à part, avec leur propre connecteur relationnel.
    inline_support = None
    inline_relation = NEUTRAL
    sentence_supports = []
    for i, sup in enumerate(supports):
        rel = relation_between(lead.theme, sup.theme)
        if i == 0 and rel != NEUTRAL:
            inline_support = sup
            inline_relation = rel
        else:
            sentence_supports.append((sup, rel))

    inline_support_frag = fl.fragments_for(inline_support, voice, ctx) if inline_support else None

    sentences = [_lead_sentence(lead_frag, inline_support_frag, inline_relation)]

    for sup, rel in sentence_supports:
        sup_frag = fl.fragments_for(sup, voice, ctx)
        starter = _SENTENCE_CONNECTORS.get(rel, _SENTENCE_CONNECTORS[NEUTRAL])
        sentences.append(f"{starter}, {sup_frag['observation']}")

    # Le PLAN vient TOUJOURS du principal (idée directrice unique).
    sentences.append(_capitalize(lead_frag["plan"]))

    text = ". ".join(s.rstrip(" .") for s in sentences if s) + "."

    return {
        "text": text,
        "lead": lead.theme,
        "supports": [s.theme for s in supports],
        "voice": voice,
        "caution": caution_text,
    }
