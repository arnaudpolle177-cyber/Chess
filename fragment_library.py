"""
fragment_library.py
Étape 3 du pipeline narration v2 (voir NARRATION_V2_PLAN.txt) : RÉSERVOIR DE
FRAGMENTS.

Au lieu de fournir un commentaire FINI par (thème x profil) -- ce que fait
narration.py aujourd'hui (TEMPLATES) -- ce module fournit, pour chaque
thème/brique, des FRAGMENTS COURTS réutilisables :

    { observation, cause, plan }   déclinés par VOIX (popular/creative/classical)

Le weaver (étape 4, narration_weaver.py) assemblera ensuite ces fragments
avec un CONNECTEUR choisi selon la relation entre le thème principal et un
thème secondaire, pour produire UNE seule pensée fluide de 2 à 4 phrases --
au lieu de coller deux commentaires indépendants.

------------------------------------------------------------------
CONTRAT DE FRAGMENT (important pour le weaver)
------------------------------------------------------------------
Chaque fragment est une CLAUSE, pas une phrase finie :
  - en MINUSCULE au début (sauf nom propre / notation SAN comme "Qh5"),
  - SANS ponctuation finale,
  - autoportante grammaticalement (un groupe verbal complet qu'on peut
    faire précéder d'un connecteur : "..., ce qui permet à <plan>").

C'est le WEAVER qui met la majuscule en tête de phrase, ajoute les
connecteurs et la ponctuation. Un fragment ne se termine donc jamais par un
point et ne commence jamais par une majuscule décorative -- sinon
l'assemblage produirait "Le roi adverse manque de défenseurs. Ce qui..."
(deux phrases bancales) au lieu d'une seule pensée tissée.

Les 3 clés :
  - observation : CE QUI EST VRAI dans la position (le constat brut).
                  TOUJOURS présent.
  - cause       : le détail concret qui FONDE l'observation (case précise,
                  ampleur en pions, motif tactique nommé...). Peut être None
                  si la brique n'a rien de plus précis à dire que son
                  observation -- le weaver s'en passe alors proprement.
  - plan        : QUE FAIRE (l'action recommandée). TOUJOURS présent. Dans
                  le commentaire final, le plan vient TOUJOURS du thème
                  PRINCIPAL (voir la roadmap) -- mais on le fournit pour
                  chaque brique car n'importe quelle brique peut être
                  principale selon la position.

------------------------------------------------------------------
CONTRAINTE ADN -- RIEN D'INVENTÉ
------------------------------------------------------------------
Chaque fragment s'appuie EXCLUSIVEMENT sur un champ RÉEL de la brique
(ThemeCandidate.fields, mêmes noms que ThemeResult) ou du contexte
(FragmentContext : coup joué, motif why détecté, éval). Aucun motif
tactique, aucune case, aucune ampleur n'est fabriqué. Quand une donnée
optionnelle manque (ex: opponent_better_move_san absent, why_motif None),
le fragment retombe sur une formulation plus générale mais toujours vraie,
jamais sur une invention.

------------------------------------------------------------------
ADDITIF ET NON BRANCHÉ
------------------------------------------------------------------
Rien n'appelle encore ce module en production. narration.py (TEMPLATES /
generate_narration) reste la source de l'affichage actuel. Le câblage se
fera à l'étape 5, une fois le weaver (étape 4) en place.
"""
from dataclasses import dataclass
from typing import Optional

import chess

from theme_detector import (
    BLUNDER, TACTICAL, ATTACK, DEFENSE, MISSED_OPPORTUNITY,
    ENDGAME, OPENING, INITIATIVE_SHIFT, STRATEGIC_ADVANTAGE, PAWN_STRUCTURE,
    PIECE_ACTIVITY_GAP, KING_SAFETY_WARNING, EQUAL_POSITION,
)

# Voix reconnues. "creative" correspond aux gabarits _tactical_ de
# narration.py (même profil, voir la conversation d'origine sur les 3
# philosophies). VOICE_FALLBACK est la voix utilisée si une brique ne
# décline pas la voix demandée (ne devrait pas arriver : toutes les briques
# ci-dessous couvrent les 3 voix, mais garde-fou robuste).
POPULAR = "popular"
CREATIVE = "creative"
CLASSICAL = "classical"
VOICES = (POPULAR, CREATIVE, CLASSICAL)
VOICE_FALLBACK = POPULAR


# ---------------------------------------------------------------------
# Petits helpers -- COPIE LOCALE VOLONTAIRE des helpers de narration.py.
# Décision de design : garder fragment_library totalement DÉCOUPLÉ de
# narration.py (qui importe variation_narrator + opening_identity, deux
# dépendances lourdes inutiles ici). Ces helpers font 2-3 lignes chacun et
# ne portent aucune donnée inventée -- juste du formatage de champs réels.
# Si narration.py devient à terme le réservoir de fragments (voir roadmap,
# §6 fichiers), ils fusionneront naturellement.
# ---------------------------------------------------------------------
PIECE_NAMES_FR = {
    chess.PAWN: "pion", chess.KNIGHT: "cavalier", chess.BISHOP: "fou",
    chess.ROOK: "tour", chess.QUEEN: "dame", chess.KING: "roi",
}

# Nom pédagogique du motif tactique (voir why_detector.py / narration.py
# WHY_CONCEPT_NAME_FR) -- COPIE LOCALE (même raison que ci-dessus). Sert à
# NOMMER le concept ("une fourchette") plutôt qu'à le décrire.
WHY_CONCEPT_NAME_FR = {
    "fork": "une fourchette",
    "pin": "un clouage",
    "undefended": "une pièce non défendue",
    "not_recaptured": "une pièce non défendue",
    "forced_sequence": "une séquence forcée",
    "open_file": "une colonne ouverte",
    "material_gain": "un gain de matériel net",
}

# Défaut "pion faible" (et non "faiblesse de pion") pour rester au MASCULIN :
# les fragments écrivent "un {kind}" / "le {kind} adverse", qui exige un nom
# masculin pour l'accord ("un pion faible", pas "un faiblesse de pion").
_PAWN_WEAKNESS_LABEL_FR = {"doubled": "pion doublé", "isolated": "pion isolé"}
_PAWN_WEAKNESS_LABEL_DEFAULT = "pion faible"


def _sq(square):
    """Nom de case algébrique ('e4'). None -> chaîne vide (jamais d'exception)."""
    return chess.square_name(square) if square is not None else ""


def _piece_name(board, square):
    piece = board.piece_at(square) if square is not None else None
    return PIECE_NAMES_FR.get(piece.piece_type, "pièce") if piece else "pièce"


def _pawns(cp):
    """Ampleur en pions (arrondie à 0.1) à partir de centipawns. None -> None."""
    return round(abs(cp) / 100, 1) if cp is not None else None


def _pawns_word(pawns):
    """'pion' / 'pions' selon l'ampleur (accord au pluriel au-delà de 1)."""
    return "pions" if pawns and pawns > 1 else "pion"


def _concept_name(why_motif):
    return WHY_CONCEPT_NAME_FR.get(why_motif)


# ---------------------------------------------------------------------
# Contexte de fragment
# ---------------------------------------------------------------------
@dataclass
class FragmentContext:
    """
    Tout ce dont les fragments ont besoin EN PLUS des champs de la brique
    elle-même. Séparé de la brique car ces données ne viennent pas de la
    détection de thème mais du coup joué / de l'analyse why :

    board       : position ACTUELLE (chess.Board) -- pour nommer une pièce
                  sur une case (rare : la plupart des fragments lisent des
                  cases déjà fournies par la brique).
    chosen      : dict du coup choisi (voir engine_analysis.analyze_candidates)
                  -- contient 'move_uci', utilisé par les fragments qui
                  citent le coup joué (motif why de type fork/undefended).
    why_motif   : identifiant du motif tactique détecté (voir why_detector.py)
                  ou None -- sert à NOMMER le concept quand il existe.
    why_detail  : dict de détails du motif (voir why_detector.py) ou None.
    eval_cp     : éval en centipawns du point de vue de mon camp (voir
                  ThemeResult.eval_cp) -- certains fragments (INITIATIVE_SHIFT)
                  changent selon que je suis en avantage ou non.

    Tous optionnels : un fragment qui a besoin d'un champ absent retombe sur
    sa formulation générale (jamais d'invention, jamais d'exception).
    """
    board: Optional[chess.Board] = None
    chosen: Optional[dict] = None
    why_motif: Optional[str] = None
    why_detail: Optional[dict] = None
    eval_cp: int = 0


def _f(observation, plan, cause=None):
    """Fabrique un dict de fragment normalisé (les 3 clés toujours présentes)."""
    return {"observation": observation, "cause": cause, "plan": plan}


# ---------------------------------------------------------------------
# Fragments par thème.
# Chaque fonction : (fields: dict, voice: str, ctx: FragmentContext) -> dict
#   fields = ThemeCandidate.fields de la brique (mêmes noms que ThemeResult).
# Retour = {observation, cause, plan} (clauses minuscules, sans point final).
# ---------------------------------------------------------------------

# --- BLUNDER -----------------------------------------------------------
def _frag_blunder(fields, voice, ctx):
    swing = fields.get("swing_cp")
    pawns = _pawns(swing)
    ampleur = None
    if pawns:
        ampleur = f"environ {pawns} {_pawns_word(pawns)} d'un coup"
    # cause : le motif why concret, s'il existe (jamais inventé).
    concept = _concept_name(ctx.why_motif)

    if voice == CREATIVE:
        obs = "ton adversaire vient de laisser une brèche exploitable"
        cause = concept if concept else ampleur
        plan = "frappe maintenant, avant qu'il ne referme la position"
        if concept:
            plan = f"exploite {concept} sans te contenter du coup tranquille"
        return _f(obs, plan, cause)
    if voice == CLASSICAL:
        obs = "l'adversaire vient de commettre une erreur nette"
        cause = ampleur
        plan = "calcule la ligne jusqu'au bout, puis exécute-la sans hésiter"
        return _f(obs, plan, cause)
    # popular
    obs = "ton adversaire vient de relâcher la pression"
    cause = ampleur
    plan = "prends ce qui est à prendre avant qu'il ne se réorganise"
    return _f(obs, plan, cause)


# --- TACTICAL ----------------------------------------------------------
def _frag_tactical(fields, voice, ctx):
    concept = _concept_name(ctx.why_motif)
    if voice == CREATIVE:
        obs = "la position est instable, un seul coup compte vraiment"
        cause = concept
        plan = "suis la variante forçante jusqu'au bout avant de la jouer"
        return _f(obs, plan, cause)
    if voice == CLASSICAL:
        obs = "c'est une position concrète, le calcul prime sur le plan général"
        cause = concept
        plan = "vérifie d'abord les pièces non défendues et les échecs"
        return _f(obs, plan, cause)
    # popular
    obs = "il y a un coup fort à jouer, pas juste un bon coup parmi d'autres"
    cause = concept
    plan = "prends le temps de vérifier les captures et les échecs avant de jouer"
    return _f(obs, plan, cause)


# --- ATTACK ------------------------------------------------------------
def _frag_attack(fields, voice, ctx):
    king_sq = fields.get("king_square")
    where = f"son roi en {_sq(king_sq)}" if king_sq is not None else "son roi"
    if voice == CREATIVE:
        obs = f"{where} est à découvert"
        plan = "ouvre une ligne vers lui, quitte à sacrifier du matériel"
        return _f(obs, plan, None)
    if voice == CLASSICAL:
        obs = f"{where} est affaibli"
        plan = "amène ta pièce la moins active dans l'attaque avant de forcer"
        return _f(obs, plan, None)
    # popular
    obs = f"{where} manque de défenseurs"
    plan = "fais converger tes pièces vers ce côté, l'avantage se concrétisera"
    return _f(obs, plan, None)


# --- DEFENSE -----------------------------------------------------------
def _frag_defense(fields, voice, ctx):
    king_sq = fields.get("king_square")
    where = f"ton roi en {_sq(king_sq)}" if king_sq is not None else "ton roi"
    if voice == CREATIVE:
        obs = f"l'attaque adverse sur {where} est bien réelle"
        plan = "cherche un coup qui casse l'attaque ou contre-attaque plus vite qu'elle"
        return _f(obs, plan, None)
    if voice == CLASSICAL:
        obs = f"{where} est sous attaque et la priorité va à sa sécurité"
        plan = "neutralise d'abord la pièce adverse la plus menaçante"
        return _f(obs, plan, None)
    # popular
    obs = f"{where} est moins bien entouré que celui de l'adversaire"
    plan = "consolide d'abord, cherche la contre-attaque une fois stabilisé"
    return _f(obs, plan, None)


# --- MISSED_OPPORTUNITY ------------------------------------------------
def _frag_missed(fields, voice, ctx):
    san = fields.get("opponent_better_move_san")
    swing = fields.get("swing_cp")
    pawns = _pawns(swing)
    ampleur = f"environ {pawns} {_pawns_word(pawns)}" if pawns else None
    if voice == CREATIVE:
        if san:
            obs = f"ton adversaire avait {san}, bien plus tranchant, et ne l'a pas joué"
        else:
            obs = "ton adversaire a choisi la continuation sage plutôt que la plus mordante"
        plan = "sois plus incisif que lui : force la position avant qu'il ne se recentre"
        return _f(obs, plan, ampleur)
    if voice == CLASSICAL:
        if san:
            obs = f"{san} suivait mieux la logique de la position, il ne l'a pas joué"
        else:
            obs = "l'adversaire s'est éloigné du plan le plus rigoureux"
        plan = "reprends un jeu solide, ton avantage doit croître naturellement"
        return _f(obs, plan, ampleur)
    # popular
    if san:
        obs = f"ton adversaire avait {san} de disponible et ne l'a pas joué"
    else:
        obs = "ton adversaire n'a pas trouvé la ligne la plus incisive"
    plan = "reprends la main tant que la fenêtre est ouverte"
    return _f(obs, plan, ampleur)


# --- ENDGAME -----------------------------------------------------------
def _frag_endgame(fields, voice, ctx):
    passed = fields.get("passed_pawn_square")
    has_passed = passed is not None
    if voice == CREATIVE:
        if has_passed:
            obs = f"ton pion passé en {_sq(passed)} a la voie libre vers la promotion"
            plan = "calcule sa course à fond avant de le pousser"
        else:
            obs = "il reste peu de pièces, la moindre imprécision se paie cash"
            plan = "calcule les courses de pions et l'activité du roi avant de jouer"
        return _f(obs, plan, None)
    if voice == CLASSICAL:
        if has_passed:
            obs = f"un pion passé existe en {_sq(passed)}, c'est l'atout principal de la finale"
            plan = "amène ton roi devant lui avant de le pousser, jamais seul"
        else:
            obs = "tout se joue sur l'activité du roi et l'opposition"
            plan = "active ton roi et cherche à créer une faiblesse durable"
        return _f(obs, plan, None)
    # popular
    if has_passed:
        obs = f"le pion en {_sq(passed)} n'a plus aucun pion adverse pour l'arrêter"
        plan = "pousse-le en le soutenant avec ton roi ou tes pièces"
    else:
        obs = "sans les dames, ton roi devient une pièce active"
        plan = "avance-le vers le centre, il peut participer sans risque désormais"
    return _f(obs, plan, None)


# --- OPENING -----------------------------------------------------------
# CONSCIENT DE LA POSITION : le plan ne recommande de ROQUER que si le
# roque est encore LÉGALEMENT possible (board.has_castling_rights). Sinon
# (droits perdus = déjà roqué, ou roi/tour déjà bougés), conseiller de
# roquer serait absurde -- on bascule sur "achève ton développement". Bug
# observé en pratique : "Roque puis connecte tes tours" affiché alors que
# le roi était déjà roqué (voir la conversation). ctx.board peut être None
# (fragments en formulation générale) -> repli prudent sur can_castle=True
# (le conseil de roque reste vrai par défaut en vraie ouverture).
def _frag_opening(fields, voice, ctx):
    can_castle = True
    if ctx is not None and ctx.board is not None:
        try:
            can_castle = ctx.board.has_castling_rights(ctx.board.turn)
        except Exception:
            can_castle = True
    if voice == CREATIVE:
        obs = "toutes tes pièces ne sont pas encore prêtes à se battre"
        plan = "développe la pièce la plus utile, garde l'idée d'attaque pour plus tard"
        return _f(obs, plan, None)
    if voice == CLASSICAL:
        obs = "l'ouverture obéit à trois priorités : centre, développement, sécurité du roi"
        if can_castle:
            plan = "choisis le coup qui sert un de ces buts sans compromettre les autres"
        else:
            plan = "ton roi est à l'abri, concentre-toi maintenant sur l'activité de tes pièces et le centre"
        return _f(obs, plan, None)
    # popular
    if can_castle:
        obs = "ton développement n'est pas terminé"
        plan = "roque puis connecte tes tours, le reste suivra naturellement"
    else:
        obs = "ton roi est déjà en sécurité, mais ton développement n'est pas tout à fait fini"
        plan = "amène ta dernière pièce inactive vers une bonne case et relie tes tours"
    return _f(obs, plan, None)


# --- INITIATIVE_SHIFT --------------------------------------------------
def _frag_initiative(fields, voice, ctx):
    # Le SENS du basculement dépend de l'éval (voir detect_theme point 6 /
    # narration _initiative_xxx) : en avantage -> je PERDS l'initiative ;
    # en désavantage -> je la REPRENDS. (La pente initiative_slope_cp elle-même
    # n'est pas citée dans le texte : sa valeur chiffrée n'apporte rien au
    # lecteur, seul son SIGNE -- déjà porté par l'éval -- compte.)
    winning = ctx.eval_cp > 0
    if voice == CREATIVE:
        if winning:
            obs = "l'initiative que tu avais construite commence à s'effriter"
            plan = "cherche le coup qui remet la pression tout de suite"
        else:
            obs = "tu étais sous pression mais l'initiative change de camp"
            plan = "accentue cette bascule avant qu'il ne réalise ce qui se passe"
        return _f(obs, plan, None)
    if voice == CLASSICAL:
        if winning:
            obs = "un avantage qui n'est pas entretenu tend à s'estomper, c'est ce qui commence ici"
            plan = "fixe-toi un plan actif clair plutôt que d'attendre"
        else:
            obs = "la dynamique de la partie bascule progressivement en ta faveur"
            plan = "poursuis avec des coups actifs, sans revenir trop tôt à la prudence"
        return _f(obs, plan, None)
    # popular
    if winning:
        obs = "tu gardes l'avantage mais l'élan des derniers coups faiblit"
        plan = "crée rapidement une nouvelle menace avant qu'il ne reprenne la main"
    else:
        obs = "la position reste difficile mais tu regagnes du terrain coup après coup"
        plan = "continue sur cette lancée, l'adversaire perd son avance"
    return _f(obs, plan, None)


# --- STRATEGIC_ADVANTAGE ----------------------------------------------
# Textes de simplification / déséquilibre matériel : réutilisent la même
# sémantique que narration.py (_SIMPLIFICATION_ADVICE_TEXT /
# _MATERIAL_IMBALANCE_TEXT), reformulés en CLAUSES de plan (minuscule, sans
# point). Rien d'inventé : material_imbalance_kind et simplification_advice
# sont des champs RÉELS de la brique.
_SIMPLIFY_PLAN = {
    "simplify": "cherche à échanger les pièces quand l'occasion se présente pour réduire son contre-jeu",
    "keep_tension": "évite les échanges tant que la dynamique actuelle joue pour toi",
}

_IMBALANCE_OBS = {
    "bishop_pair_open": "tu as la paire de fous dans une position déjà ouverte",
    "bishop_pair_closed": "tu as la paire de fous, mais la position reste fermée pour l'instant",
    "knights_closed": "tes cavaliers sont mieux adaptés que les fous adverses dans cette position fermée",
    "rook_vs_minors": "tu as une tour contre des pièces mineures, un déséquilibre qui favorise la finale",
}

_IMBALANCE_PLAN = {
    "bishop_pair_open": {
        "simplify": "continue d'ouvrir les lignes et échange les mineures adverses, tes fous n'en vaudront que plus",
        "keep_tension": "continue d'ouvrir les lignes mais garde les pièces, la dynamique mérite d'être poussée",
    },
    "bishop_pair_closed": {
        "simplify": "cherche à ouvrir la position progressivement, c'est là qu'ils prendront leur valeur",
        "keep_tension": "ouvre la position progressivement mais évite les échanges prématurés",
    },
    "knights_closed": {
        "simplify": "garde la structure fermée et échange les pièces les moins actives",
        "keep_tension": "garde la structure fermée et les pièces sur l'échiquier pour l'instant",
    },
    "rook_vs_minors": {
        "simplify": "cherche à simplifier vers une finale, la tour prend de la valeur quand le plateau se dégage",
        "keep_tension": "résiste à l'envie de simplifier tout de suite, pousse d'abord ta dynamique",
    },
}


def _frag_strategic(fields, voice, ctx):
    imbalance = fields.get("material_imbalance_kind")
    advice = fields.get("simplification_advice")
    pawns = _pawns(ctx.eval_cp)
    ampleur = f"un avantage d'environ {pawns} {_pawns_word(pawns)}" if pawns else None

    # Plan : priorité au plan de déséquilibre matériel s'il existe (plus
    # précis), sinon plan de simplification générique, sinon repli neutre.
    if imbalance and imbalance in _IMBALANCE_PLAN:
        plan = _IMBALANCE_PLAN[imbalance].get(advice, _IMBALANCE_PLAN[imbalance]["simplify"])
        obs = _IMBALANCE_OBS.get(imbalance, "ta position est nettement meilleure")
        # cause : la nature de l'avantage EST le déséquilibre -> pas de cause
        # séparée (elle ferait doublon avec l'observation).
        return _f(obs, plan, None)

    plan_default = _SIMPLIFY_PLAN.get(advice, "améliore patiemment ta pièce la moins bien placée")
    if voice == CREATIVE:
        obs = "l'avantage est réel, même sans motif tactique visible pour l'instant"
        return _f(obs, plan_default, ampleur)
    if voice == CLASSICAL:
        obs = "l'avantage tient à la qualité de tes pièces, pas au matériel"
        return _f(obs, plan_default, ampleur)
    # popular
    obs = "ta position est nettement meilleure, sans coup immédiat à calculer"
    return _f(obs, plan_default, ampleur)


# --- PAWN_STRUCTURE ----------------------------------------------------
# La case de la faiblesse est une DONNÉE-ANCRE (champ réel pawn_weakness_square) :
# elle vit dans l'OBSERVATION, pas dans cause -- sinon, tissée inline comme
# secondaire, la case disparaîtrait (le weaver ne garde que l'observation
# d'un secondaire). "Rien d'inventé" doit survivre à l'assemblage.
def _frag_pawn_structure(fields, voice, ctx):
    kind = _PAWN_WEAKNESS_LABEL_FR.get(fields.get("pawn_weakness_kind"), _PAWN_WEAKNESS_LABEL_DEFAULT)
    sq = _sq(fields.get("pawn_weakness_square"))
    cible = f"un {kind} adverse en {sq}" if sq else f"un {kind} adverse"
    if voice == CREATIVE:
        obs = f"{cible} est un défaut autour duquel construire une attaque"
        plan = "oriente tes pièces vers cette zone et fais monter la pression"
        return _f(obs, plan, None)
    if voice == CLASSICAL:
        obs = f"{cible} est une faiblesse structurelle permanente"
        plan = "empêche d'abord qu'elle soit réparée, puis attaque-la avec assez de pièces"
        return _f(obs, plan, None)
    # popular
    obs = f"l'adversaire a {cible} qui ne disparaîtra pas tout seul"
    plan = "garde cette faiblesse en tête et fais peser la pression au bon moment"
    return _f(obs, plan, None)


# --- PIECE_ACTIVITY_GAP ------------------------------------------------
def _frag_piece_activity(fields, voice, ctx):
    ratio = fields.get("activity_ratio")
    pct = round((ratio - 1) * 100) if ratio else None
    cause = f"près de {pct}% de cases utiles en plus" if pct else None
    if voice == CREATIVE:
        obs = "tes pièces sont nettement plus mobiles que celles de l'adversaire"
        plan = "transforme cette avance de mobilité en menace concrète"
        return _f(obs, plan, cause)
    if voice == CLASSICAL:
        obs = "à matériel égal, tes pièces occupent de meilleures cases que les siennes"
        plan = "restreins encore ses pièces avant de convertir cette activité"
        return _f(obs, plan, cause)
    # popular
    obs = "tes pièces contrôlent plus de cases importantes que celles de l'adversaire"
    plan = "sers-toi de cette liberté pour créer des menaces avant qu'il ne se coordonne"
    return _f(obs, plan, cause)


# --- KING_SAFETY_WARNING ----------------------------------------------
def _frag_king_safety_warning(fields, voice, ctx):
    mine = fields.get("king_safety_warning_is_mine", True)
    sq = _sq(fields.get("king_safety_warning_square"))
    if mine:
        where = f"ton roi en {sq}" if sq else "ton roi"
        if voice == CREATIVE:
            obs = f"{where} reste exposé alors que le jeu s'ouvre"
            plan = "sécurise-le vite avant de te lancer dans quoi que ce soit d'ambitieux"
        elif voice == CLASSICAL:
            obs = f"{where} n'est pas encore mis en sécurité dans un centre qui s'ouvre"
            plan = "achève ta mise à l'abri avant d'entamer un plan plus large"
        else:
            obs = f"{where} commence à manquer de protection"
            plan = "pense à le mettre en sécurité avant que l'adversaire n'en profite"
        return _f(obs, plan, None)
    # roi adverse
    where = f"le roi adverse en {sq}" if sq else "le roi adverse"
    if voice == CREATIVE:
        obs = f"{where} n'est pas encore attaqué mais la cible se dessine"
        plan = "amène tes pièces en position pour frapper dès que le centre craque"
    elif voice == CLASSICAL:
        obs = f"{where} néglige sa sécurité dans un centre instable"
        plan = "poursuis ton développement, ce retard risque de lui coûter cher"
    else:
        obs = f"{where} commence à manquer de protection"
        plan = "garde cette faiblesse en tête et prépare-toi à en profiter plus tard"
    return _f(obs, plan, None)


# --- EQUAL_POSITION ----------------------------------------------------
def _frag_equal(fields, voice, ctx):
    if voice == CREATIVE:
        obs = "l'équilibre actuel ne va pas forcément durer"
        plan = "cherche le coup qui pose le plus de problèmes concrets à l'adversaire"
        return _f(obs, plan, None)
    if voice == CLASSICAL:
        obs = "aucun camp n'a d'avantage net, la structure de pions guide le plan"
        plan = "repère la case faible adverse et construis ton jeu autour"
        return _f(obs, plan, None)
    # popular
    obs = "rien ne se dégage clairement, la partie reste ouverte"
    plan = "choisis le plan le plus simple à exécuter, pas le plus ambitieux"
    return _f(obs, plan, None)


# ---------------------------------------------------------------------
# Table de dispatch thème -> fonction de fragments.
# ---------------------------------------------------------------------
_FRAGMENT_FUNCS = {
    BLUNDER: _frag_blunder,
    TACTICAL: _frag_tactical,
    ATTACK: _frag_attack,
    DEFENSE: _frag_defense,
    MISSED_OPPORTUNITY: _frag_missed,
    ENDGAME: _frag_endgame,
    OPENING: _frag_opening,
    INITIATIVE_SHIFT: _frag_initiative,
    STRATEGIC_ADVANTAGE: _frag_strategic,
    PAWN_STRUCTURE: _frag_pawn_structure,
    PIECE_ACTIVITY_GAP: _frag_piece_activity,
    KING_SAFETY_WARNING: _frag_king_safety_warning,
    EQUAL_POSITION: _frag_equal,
}


def fragments_for(brick, voice, ctx=None):
    """
    Point d'entrée public : retourne le dict {observation, cause, plan} de la
    brique `brick` (un ThemeCandidate, voir theme_detector) dans la voix
    `voice`.

    brick : ThemeCandidate (a .theme et .fields). On lit .fields, qui porte
            les champs métier RÉELS posés par collect_theme_bricks (mêmes
            noms que ThemeResult) -- éventuellement _tier/_family (ignorés
            ici, ce sont des métadonnées de scoring, pas des champs de texte).
    voice : "popular" / "creative" / "classical". Inconnue -> VOICE_FALLBACK.
    ctx   : FragmentContext optionnel (coup joué, motif why, éval). Si None,
            un contexte vide est utilisé -- les fragments retombent alors sur
            leur formulation générale (jamais d'exception).

    Retour : {"observation": str, "cause": str|None, "plan": str} -- clauses
    minuscules sans ponctuation finale (voir CONTRAT DE FRAGMENT en tête).
    Thème inconnu -> fragments neutres (EQUAL_POSITION), jamais d'exception.
    """
    if ctx is None:
        ctx = FragmentContext()
    if voice not in VOICES:
        voice = VOICE_FALLBACK
    fn = _FRAGMENT_FUNCS.get(brick.theme, _frag_equal)
    fields = brick.fields if brick.fields else {}
    return fn(fields, voice, ctx)
