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
    explain     : le commentaire a-t-il le DROIT d'expliquer ? Posé par
                  narration_v2.render selon l'écart entre les deux meilleurs
                  coups (voir le seuil, Task 4) : quand le coup s'impose de
                  lui-même, une explication est du bruit. False -> aucune
                  cause n'est ajoutée.

    Tous optionnels : un fragment qui a besoin d'un champ absent retombe sur
    sa formulation générale (jamais d'invention, jamais d'exception).
    """
    board: Optional[chess.Board] = None
    chosen: Optional[dict] = None
    why_motif: Optional[str] = None
    why_detail: Optional[dict] = None
    eval_cp: int = 0
    explain: bool = True


def _f(observation, plan, cause=None):
    """Fabrique un dict de fragment normalisé (les 3 clés toujours présentes)."""
    return {"observation": observation, "cause": cause, "plan": plan}


def _pick_variant(options, intent, ctx):
    """
    Choisit une formulation parmi `options` (liste non vide, de la plus
    naturelle à la moins), en s'écartant de celle déjà servie récemment pour
    ce même kind (voir narration_v2.render, recent_kinds). Déterministe :
    même position + même historique -> même texte (pas de random, sinon deux
    profils ou deux rafraîchissements donneraient des textes différents pour
    la même position).
    """
    recent = getattr(ctx, "recent_kinds", ()) if ctx else ()
    seen = sum(1 for k in recent if k == intent.kind)
    base = (intent.to_square or 0) % len(options)
    return options[(base + seen) % len(options)]


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

    # ORDRE : du plus décisif au plus général. Le carré tranche la partie (le
    # pion passe ou ne passe pas), l'opposition décide qui cède le terrain ;
    # le pion passé simple et le roi actif viennent après. Sans ces deux
    # premiers, la finale ne disposait que d'un signal et sortait les mêmes
    # deux paragraphes sur 96 positions mesurées.
    if has_passed and fields.get("outruns_king"):
        # « seul » n'est pas une précaution de style : _pawn_outruns_king ne
        # regarde QUE le roi adverse, jamais ses autres pièces.
        case = _sq(passed)
        if voice == CREATIVE:
            return _f(f"le roi adverse est sorti du carré du pion en {case}",
                      "pousse-le, la course est gagnée pour lui", None)
        if voice == CLASSICAL:
            return _f(f"le roi adverse ne peut plus rattraper seul le pion en {case}",
                      "pousse ce pion sans attendre, chaque temps compte", None)
        return _f(f"le roi adverse est trop loin pour arrêter seul le pion en {case}",
                  "pousse-le tout de suite, compte les cases jusqu'à la promotion", None)

    if fields.get("opposition"):
        # C'est à MOI de jouer (my_side = board.turn), donc l'opposition est
        # à l'adversaire : celui qui doit bouger la perd.
        if voice == CREATIVE:
            return _f("les rois se font face et c'est à toi d'avancer",
                      "cherche un coup de pion à jouer plutôt que de céder du terrain avec le roi", None)
        if voice == CLASSICAL:
            return _f("l'adversaire tient l'opposition, c'est à toi de rompre le face-à-face",
                      "cherche à reprendre l'opposition par un coup d'attente", None)
        return _f("les rois se font face à une case d'écart et c'est à toi de bouger",
                  "ton roi va devoir céder le passage : joue un autre coup si tu en as un", None)

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
# CONSCIENT DE LA POSITION : le conseil de roque distingue TROIS états, car le
# bug observé venait de les confondre --
#   "now"   : un coup de roque est LÉGAL tout de suite -> "roque maintenant" ;
#   "later" : le roque reste un objectif (droits encore là) mais les pièces
#             mineures bloquent encore les cases (coup 1-3 typique) -> il faut
#             DÉVELOPPER pour pouvoir roquer ensuite ; surtout PAS dire "roque
#             derrière toi", ce serait faux ;
#   "done"  : plus de droits de roque (déjà roqué OU roi/tour bougés) -> on ne
#             parle plus de roque du tout.
# Une version précédente ne testait QUE le coup de roque légal, fusionnant
# "later" et "done" : au coup 1-3 elle annonçait donc "le roque est derrière
# toi" alors que le joueur n'avait pas encore roqué. On lit maintenant AUSSI
# has_castling_rights pour séparer "pas encore" de "terminé".
# ctx.board peut être None (formulation générale) -> repli prudent sur "now".
_CASTLE_NOW, _CASTLE_LATER, _CASTLE_DONE = "now", "later", "done"


def _castle_state(ctx):
    if ctx is None or ctx.board is None:
        return _CASTLE_NOW  # repli : sans position, on garde le conseil de roque classique
    try:
        board = ctx.board
        if any(board.is_castling(m) for m in board.legal_moves):
            return _CASTLE_NOW
        if board.has_castling_rights(board.turn):
            return _CASTLE_LATER  # droits présents mais pas jouable là -> développer d'abord
        return _CASTLE_DONE
    except Exception:
        return _CASTLE_NOW


def _frag_opening(fields, voice, ctx):
    state = _castle_state(ctx)
    if voice == CREATIVE:
        obs = "toutes tes pièces ne sont pas encore prêtes à se battre"
        plan = "développe la pièce la plus utile, garde l'idée d'attaque pour plus tard"
        return _f(obs, plan, None)
    if voice == CLASSICAL:
        obs = "l'ouverture obéit à trois priorités : centre, développement, sécurité du roi"
        if state == _CASTLE_NOW:
            plan = "choisis le coup qui sert un de ces buts sans compromettre les autres"
        elif state == _CASTLE_LATER:
            plan = "développe tes pièces mineures pour dégager le roque, puis mets ton roi à l'abri"
        else:
            plan = "le roque n'est plus à l'ordre du jour, concentre-toi sur l'activité de tes pièces et le centre"
        return _f(obs, plan, None)
    # popular
    if state == _CASTLE_NOW:
        obs = "ton développement n'est pas terminé"
        plan = "roque puis connecte tes tours, le reste suivra naturellement"
    elif state == _CASTLE_LATER:
        obs = "tes pièces mineures ne sont pas encore toutes sorties"
        plan = "développe-les pour pouvoir roquer, puis relie tes tours"
    else:
        obs = "le roque est derrière toi, mais ton développement n'est pas tout à fait fini"
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


# =====================================================================
# FRAGMENTS D'INTENTION DE COUP (voir move_intent.py)
# =====================================================================
# Contrairement aux fragments de THÈME ci-dessus (qui décrivent la POSITION),
# ceux-ci décrivent CE QUE FAIT le coup recommandé -- la flèche affichée. Ils
# citent la pièce et la case RÉELLES du coup (jamais inventées : lues sur
# l'intent, lui-même dérivé de la position). Ils prennent la main quand le coup
# est FORÇANT (échec, prise, sacrifice...), cas où parler de structure de pions
# n'aurait aucun sens. Même contrat que les autres fragments : clause
# minuscule, sans ponctuation finale, {observation, cause, plan}.

def _piece_type_name(piece_type):
    """Nom FR d'un type de pièce (chess.PAWN..KING). None/inconnu -> 'pièce'."""
    return PIECE_NAMES_FR.get(piece_type, "pièce")


# Genre grammatical des noms de pièces -- "tour" et "dame" sont FÉMININS.
# Les fragments écrivaient "le {nom}" en dur, d'où "le tour adverse" observé
# en pratique. Le repli "pièce" est féminin lui aussi, donc cohérent.
_PIECE_IS_FEMININE = {chess.ROOK, chess.QUEEN}


def _piece_is_feminine(piece_type):
    """
    Genre grammatical du nom de la pièce -- SOURCE UNIQUE de l'accord. Les
    articles étaient déjà accordés, mais les ADJECTIFS et les PRONOMS restaient
    en dur dans les formulations ("la tour adverse n'est pas défendu",
    "prends cette tour, personne ne peut le reprendre"). Tout accord passe
    désormais par ici, sinon le bug revient à chaque nouvelle variante.
    """
    return piece_type in _PIECE_IS_FEMININE or _piece_type_name(piece_type) == "pièce"


def _piece_with_article(piece_type, definite=True):
    """
    Nom FR d'un type de pièce précédé de son article accordé :
    "la tour", "le cavalier", "une dame", "un fou". None/inconnu -> "la pièce".
    """
    name = _piece_type_name(piece_type)
    feminine = _piece_is_feminine(piece_type)
    if definite:
        return f"{'la' if feminine else 'le'} {name}"
    return f"{'une' if feminine else 'un'} {name}"


def _piece_demonstrative(piece_type):
    """"ce cavalier" / "cette tour" -- accord identique à _piece_with_article."""
    return f"{'cette' if _piece_is_feminine(piece_type) else 'ce'} {_piece_type_name(piece_type)}"


def _frag_check_escape(intent, voice, ctx):
    # Le roi était en échec : le coup le met à l'abri. On nomme la case
    # d'arrivée si on l'a. On distingue "le roi bouge" d'une parade (une autre
    # pièce s'interpose ou capture l'attaquant) via la pièce jouée.
    dest = _sq(intent.to_square)
    king_moves = intent.moved_piece == chess.KING
    if voice == CREATIVE:
        if king_moves:
            obs = f"ton roi est attaqué et file en {dest}" if dest else "ton roi est attaqué et doit filer"
        else:
            obs = "ton roi est attaqué, ce coup pare la menace"
        plan = "mets-le d'abord au calme, tu chercheras l'initiative une fois hors de danger"
        return _f(obs, plan, None)
    if voice == CLASSICAL:
        if king_moves:
            obs = f"ton roi est en échec et se réfugie en {dest}" if dest else "ton roi est en échec et doit se déplacer"
        else:
            obs = "ton roi est en échec, la priorité absolue est d'y répondre"
        plan = "assure la sécurité du roi avant toute autre considération"
        return _f(obs, plan, None)
    # popular
    if king_moves:
        obs = f"ton roi est en échec, il se met à l'abri en {dest}" if dest else "ton roi est en échec et doit bouger"
    else:
        obs = "ton roi est en échec, ce coup répond à la menace"
    plan = "sors d'abord de l'échec, le reste attendra"
    return _f(obs, plan, None)


def _frag_capture_free(intent, voice, ctx):
    # Prise classée CAPTURE_FREE par _capture_is_free, qui a TROIS chemins,
    # mais seuls DEUX sont des preuves matérielles CALCULÉES :
    #   - capture_undefended : case non défendue -> prise imprenable ;
    #   - capture_line_gain  : bilan de ligne strictement positif (PV avec
    #     la réponse adverse).
    # Le 3e chemin (confirmation par why_motif seul, ex. "fork") ne recalcule
    # AUCUN gain -- une prise défendue à valeur égale peut y passer si le
    # coup fourchette aussi une autre pièce. Dans ce cas ("proven" ci-dessous
    # False) on ne dit RIEN sur le matériel (ni "sans reprise", ni "gain
    # net") : on décrit la prise et, si connu, le motif réellement détecté
    # (cause = concept, jamais une pièce ou un gain fabriqués).
    dest = _sq(intent.to_square)
    prise = _piece_with_article(intent.captured_piece)
    par = _piece_with_article(intent.moved_piece)
    where = f"en {dest}" if dest else ""
    proven = intent.capture_undefended or intent.capture_line_gain
    concept = None if proven else _concept_name(intent.why_motif)

    demo = _piece_demonstrative(intent.captured_piece)
    # Accords en dur supprimés : le pronom COD et les adjectifs qui se
    # rapportent à la PIÈCE PRISE suivent son genre (voir _piece_is_feminine).
    fem = _piece_is_feminine(intent.captured_piece)
    pron = "la" if fem else "le"
    defendu = "défendue" if fem else "défendu"
    # « de » + article défini se CONTRACTE au masculin : "de le cavalier" est
    # fautif, il faut "du cavalier" ("de la tour" reste correct au féminin).
    # Sortait réellement à l'écran -- ni la garde d'accents ni celle d'accord
    # ne pouvaient le voir, aucune des deux ne regardait les prépositions.
    de_prise = f"de {prise}" if fem else f"du {_piece_type_name(intent.captured_piece)}"

    if voice == CREATIVE:
        # "sans compensation" affirme la même chose que "sans reprise" (la
        # pièce ne peut pas revenir dans le camp adverse) -- gardé par la
        # même preuve. "tourne largement à ton avantage" affirme un gain ->
        # exige capture_line_gain. Sans aucune des deux preuves, description
        # neutre de la prise, sans bilan matériel -- même règle pour la 2e
        # formulation (_pick_variant, voir anti-répétition).
        if intent.capture_undefended:
            options = [
                (f"{prise} adverse {where} tombe sans compensation".replace("  ", " ").rstrip(),
                 f"prends {demo}, puis enchaîne pendant que tu tiens l'avantage matériel"),
                (f"{prise} adverse {where} n'a personne pour {pron} reprendre".replace("  ", " ").rstrip(),
                 f"encaisse {demo}, l'initiative reste avec toi"),
            ]
        elif intent.capture_line_gain:
            options = [
                (f"l'échange {where} tourne largement à ton avantage".replace("  ", " ").rstrip(),
                 f"prends {demo}, puis enchaîne pendant que tu tiens l'avantage matériel"),
                (f"la séquence {where} laisse plus de matériel dans ton camp".replace("  ", " ").rstrip(),
                 f"engage l'échange, le bilan te reste favorable"),
            ]
        else:
            options = [
                (f"{par} prend {prise} {where}".replace("  ", " ").rstrip(),
                 f"prends {demo}, puis évalue calmement la suite"),
                (f"{par} se saisit {de_prise} {where}".replace("  ", " ").rstrip(),
                 f"prends {demo}, puis observe ce que ça donne"),
            ]
        obs, plan = _pick_variant(options, intent, ctx)
        return _f(obs, plan, concept)
    if voice == CLASSICAL:
        # "gain net de matériel" est un bilan -- ne l'affirmer que prouvé,
        # dans les deux formulations.
        if proven:
            options = [
                (f"{par} capture {prise} {where} avec un gain net de matériel".replace("  ", " ").rstrip(),
                 "encaisse le matériel, puis convertis proprement l'avantage"),
                (f"{par} capture {prise} {where}, le bilan matériel est acquis".replace("  ", " ").rstrip(),
                 "prends ce gain, puis joue simplement sur l'avantage acquis"),
            ]
        else:
            options = [
                (f"{par} capture {prise} {where}".replace("  ", " ").rstrip(),
                 "vérifie le motif tactique avant de t'engager dans la suite"),
                (f"{par} prend {prise} {where}, sans bilan matériel établi".replace("  ", " ").rstrip(),
                 "confirme le motif avant de poursuivre la ligne"),
            ]
        obs, plan = _pick_variant(options, intent, ctx)
        return _f(obs, plan, concept)
    # popular
    if intent.capture_undefended:
        options = [
            (f"{prise} adverse {where} n'est pas {defendu}".replace("  ", " ").rstrip(),
             f"prends {demo}, c'est du matériel gagné"),
            (f"{prise} adverse {where} est libre à prendre".replace("  ", " ").rstrip(),
             f"prends {demo}, personne ne peut {pron} reprendre"),
        ]
    elif intent.capture_line_gain:
        options = [
            (f"l'échange {where} tourne à ton avantage".replace("  ", " ").rstrip(),
             f"prends {demo}, c'est du matériel gagné"),
            (f"l'échange {where} te laisse en tête au niveau matériel".replace("  ", " ").rstrip(),
             f"prends {demo}, le bilan te reste favorable"),
        ]
    else:
        options = [
            (f"{prise} adverse {where} tombe".replace("  ", " ").rstrip(),
             f"prends {demo}, puis regarde ce que ça donne"),
            (f"{prise} adverse {where} est à portée".replace("  ", " ").rstrip(),
             f"prends {demo}, puis juge la suite calmement"),
        ]
    obs, plan = _pick_variant(options, intent, ctx)
    return _f(obs, plan, concept)


def _frag_sacrifice(intent, voice, ctx):
    # On donne du matériel (material_delta négatif, calculé sur la ligne) mais
    # le moteur recommande le coup : il y a une idée derrière (souvent
    # l'attaque). On reste factuel sur ce qu'on voit -- on ne PROMET pas un mat
    # qu'on n'a pas vérifié.
    #
    # Le sacrifice est toujours IMMÉDIAT ici : move_intent n'en déclare un que
    # si l'adversaire reprend sur la case d'arrivée dès sa réponse (voir
    # detect_move_intent, étape 2). La variante « sacrifice différé dans N
    # coups » a été SUPPRIMÉE avec le motif qui la produisait : elle ne sortait
    # que sur des artefacts d'horizon de PV (mesuré : 21 occurrences pour 0
    # vrai sacrifice, dont « 55.Rb3 met en route un sacrifice »).
    par = _piece_with_article(intent.moved_piece)
    dest = _sq(intent.to_square)
    where = f"en {dest}" if dest else ""

    if voice == CREATIVE:
        obs = f"ce coup sacrifie du matériel {where} pour ouvrir la position".replace("  ", " ").rstrip()
        plan = "lance la combinaison : ici l'activité vaut plus que les points"
        return _f(obs, plan, None)
    if voice == CLASSICAL:
        obs = f"{par} se donne {where} au profit de l'initiative".replace("  ", " ")
        plan = "calcule la suite jusqu'au bout avant de t'engager dans le sacrifice"
        return _f(obs, plan, None)
    # popular
    obs = f"ce coup abandonne du matériel volontairement {where}".replace("  ", " ").rstrip()
    plan = "ose le sacrifice, la compensation est bien réelle ici"
    return _f(obs, plan, None)


def _frag_gives_check(intent, voice, ctx):
    par = _piece_with_article(intent.moved_piece)
    dest = _sq(intent.to_square)
    where = f"en {dest}" if dest else ""
    if voice == CREATIVE:
        obs = f"ce coup donne échec {where} et force la réponse adverse".replace("  ", " ").rstrip()
        plan = "enchaîne les coups forçants tant que l'adversaire n'a pas le choix"
        return _f(obs, plan, None)
    if voice == CLASSICAL:
        obs = f"{par} donne échec {where}, un coup forçant".replace("  ", " ")
        plan = "vérifie chaque réponse à l'échec avant de poursuivre le plan"
        return _f(obs, plan, None)
    # popular
    obs = f"échec au roi {where}, l'adversaire doit réagir tout de suite".replace("  ", " ").rstrip()
    plan = "profite de l'échec pour garder la main sur la partie"
    return _f(obs, plan, None)


def _frag_promotion(intent, voice, ctx):
    dest = _sq(intent.to_square)
    where = f"en {dest}" if dest else ""
    if voice == CREATIVE:
        obs = f"ton pion va à dame {where}".replace("  ", " ").rstrip()
        plan = "promeus et bascule aussitôt vers l'attaque avec ta nouvelle pièce"
        return _f(obs, plan, None)
    if voice == CLASSICAL:
        obs = f"le pion atteint la dernière rangée {where} et se transforme".replace("  ", " ")
        plan = "promeus, puis exploite calmement la supériorité matérielle"
        return _f(obs, plan, None)
    # popular
    obs = f"ton pion arrive à promotion {where}".replace("  ", " ").rstrip()
    plan = "fais dame, c'est une pièce lourde de plus dans ton camp"
    return _f(obs, plan, None)


def _frag_capture_trade(intent, voice, ctx):
    # Échange à valeur ~égale : on décrit la prise sans dramatiser.
    prise = _piece_with_article(intent.captured_piece)
    par = _piece_with_article(intent.moved_piece)
    dest = _sq(intent.to_square)
    where = f"en {dest}" if dest else ""
    if voice == CREATIVE:
        obs = f"{par} prend {prise} {where} et relance la position".replace("  ", " ")
        plan = "engage l'échange, puis cherche à en tirer l'initiative"
        return _f(obs, plan, None)
    if voice == CLASSICAL:
        obs = f"{par} échange {prise} {where}".replace("  ", " ")
        plan = "réalise l'échange, il clarifie la position"
        return _f(obs, plan, None)
    # popular
    obs = f"{par} prend {prise} {where}".replace("  ", " ")
    plan = "fais l'échange, il simplifie la position sans rien concéder"
    return _f(obs, plan, None)


def _frag_mate(intent, voice, ctx):
    # Le coup MATE. Aucune nuance à apporter : c'est la fin de la partie, le
    # seul message utile est "joue-le". Deux cas distincts, jamais confondus :
    # le mat est-il DÉJÀ sur l'échiquier (mate_in == 1) ou au bout d'une
    # séquence forcée (mate_in > 1) ? On ne dit "fait mat" que dans le premier
    # cas -- sinon on annonce le compte, prouvé par le score du candidat (voir
    # move_intent.mate_in), jamais deviné.
    dest = _sq(intent.to_square)
    par = _piece_with_article(intent.moved_piece)
    where = f"en {dest}" if dest else ""
    n = intent.mate_in or 1
    if n > 1:
        if voice == CREATIVE:
            obs = f"{par} {where} lance le mat en {n} coups".replace("  ", " ")
            return _f(obs, "va au bout de la séquence, elle est forcée", None)
        if voice == CLASSICAL:
            obs = f"{par} {where} force le mat en {n} coups".replace("  ", " ")
            return _f(obs, "conduis la séquence jusqu'au mat, elle ne laisse aucune parade", None)
        obs = f"{par} {where} force le mat en {n} coups".replace("  ", " ")
        return _f(obs, "joue-le et enchaîne, la partie est gagnée", None)
    if voice == CREATIVE:
        obs = f"{par} {where} met le roi adverse échec et mat".replace("  ", " ")
        plan = "c'est fini, joue-le"
        return _f(obs, plan, None)
    if voice == CLASSICAL:
        obs = f"{par} {where} donne mat, la partie s'arrête ici".replace("  ", " ")
        plan = "joue ce coup, aucune autre considération n'a de valeur"
        return _f(obs, plan, None)
    # popular
    obs = f"{par} {where} fait mat".replace("  ", " ")
    plan = "joue-le, la partie est gagnée"
    return _f(obs, plan, None)


def _frag_develop(intent, voice, ctx):
    # Une mineure sort de la rangee de fond. Fait verifiable : la pièce et sa
    # case d'arrivee, lues sur l'intent. Au moins 2 formulations par voix
    # (choisies via _pick_variant) : a l'audit, 3 coups de développement de
    # suite sortaient le meme paragraphe mot pour mot.
    dest = _sq(intent.to_square)
    par = _piece_with_article(intent.moved_piece)
    where = f"en {dest}" if dest else ""
    if voice == CREATIVE:
        options = [
            (f"{par} entre dans la partie {where}".replace("  ", " ").rstrip(),
             "sors tes pièces d'abord, les idées viendront après"),
            (f"{par} rejoint le jeu {where}".replace("  ", " ").rstrip(),
             "chaque pièce sortie ajoute une option, continue"),
        ]
    elif voice == CLASSICAL:
        options = [
            (f"{par} se développe {where}".replace("  ", " ").rstrip(),
             "termine ton développement avant d'ouvrir le jeu"),
            (f"{par} quitte sa case de départ {where}".replace("  ", " ").rstrip(),
             "achève la mobilisation des pièces mineures avant tout plan"),
        ]
    else:
        # "puis roque" AFFIRME que le roque est encore possible : vérifié sur la
        # position (_castle_state lit has_castling_rights), sinon on conseillait
        # un coup illégal (observé sur une position sans aucun droit de roque).
        options = [
            (f"{par} sort {where}".replace("  ", " ").rstrip(),
             "développe, puis roque" if _castle_state(ctx) != _CASTLE_DONE
             else "développe, puis mets ton roi à l'abri derrière ses pions"),
            (f"{par} se met en jeu {where}".replace("  ", " ").rstrip(),
             "sors une pièce de plus avant de lancer une action"),
        ]
    obs, plan = _pick_variant(options, intent, ctx)
    return _f(obs, plan, None)


def _frag_castle(intent, voice, ctx):
    # Le roque : deux effets réels et simultanés (roi à l'abri, tour reliée).
    if voice == CREATIVE:
        return _f("ton roi se met à l'abri et ta tour rejoint le jeu",
                  "mets-toi en sécurité, tu attaqueras plus librement ensuite", None)
    if voice == CLASSICAL:
        return _f("le roque met le roi en sécurité et active la tour",
                  "sécurise le roi avant d'entamer une opération au centre", None)
    return _f("tu roques : roi à l'abri, tour connectée",
              "roque maintenant, c'est le bon moment", None)


def _has_other_rook(ctx):
    """
    Me reste-t-il une AUTRE tour que celle qui joue ? Compté sur la position
    (ctx.board, AVANT le coup : la tour qui joue y figure encore, donc >= 2).
    Sans position -> False : on ne conseille pas de doubler des tours qu'on
    n'a pas comptées (les plans "amène ta seconde tour" / "double tes tours"
    sortaient tels quels sur des finales à UNE seule tour).
    """
    board = getattr(ctx, "board", None) if ctx else None
    if board is None:
        return False
    try:
        return len(board.pieces(chess.ROOK, board.turn)) >= 2
    except Exception:
        return False


def _frag_rook_file(intent, voice, ctx):
    # Tour/dame arrivant sur une colonne ouverte ou semi-ouverte (fait calcule
    # par why_detector._open_file_status, voir move_intent). Le plan doit
    # s'accorder a la piece REELLEMENT jouee (intent.moved_piece) : une tour
    # sur colonne ouverte appelle a doubler les tours, une dame sur colonne
    # ouverte est plus exposee et sert plutot d'appui a une autre piece --
    # les deux plans ne sont pas interchangeables (voir audit, "les tours
    # aiment les colonnes ouvertes" affirme a tort sur une dame). Au moins
    # 2 formulations par voix, choisies via _pick_variant (ce coup revient
    # plusieurs fois par partie, une tour prenant souvent des colonnes
    # successives).
    dest = _sq(intent.to_square)
    par = _piece_with_article(intent.moved_piece)
    where = f"en {dest}" if dest else ""
    is_queen = intent.moved_piece == chess.QUEEN
    # STATUT RÉEL de la colonne (intent.file_status) : ROOK_FILE accepte AUSSI
    # les colonnes semi-ouvertes, et le texte écrivait "colonne ouverte" en dur
    # -- fait inventé, et conseil faux : sur une semi-ouverte il reste un pion
    # adverse sur la colonne, c'est LUI la cible.
    half = intent.file_status == "half_open"
    colonne = "colonne semi-ouverte" if half else "colonne ouverte"
    # "ta seconde tour" / "double tes tours" AFFIRME qu'une autre tour existe :
    # on ne le dit que si on l'a comptée sur la position (finales à une tour).
    double = _has_other_rook(ctx)

    if voice == CREATIVE:
        if is_queen:
            options = [
                (f"{par} prend la {colonne} {where}".replace("  ", " ").rstrip(),
                 f"reste vigilant, une dame avancée sur une {colonne} est une cible facile"),
                (f"{par} s'installe sur la {colonne} {where}".replace("  ", " ").rstrip(),
                 "prépare plutôt une autre pièce à profiter de cette colonne derrière la dame"),
            ]
        else:
            if half:
                plan_a = "pousse sur le pion adverse resté sur cette colonne, il ne peut pas fuir"
                plan_b = ("amène ta seconde tour derrière, ce pion ne tiendra pas à deux contre un"
                          if double else "fais de ce pion ta cible, la colonne s'ouvrira quand il tombera")
            else:
                plan_a = "une colonne ouverte, c'est une autoroute : occupe-la avant lui"
                plan_b = ("amène ta seconde tour derrière, la colonne devient une vraie autoroute"
                          if double else "garde cette colonne pour toi, il ne doit jamais te la disputer")
            options = [
                (f"{par} prend la {colonne} {where}".replace("  ", " ").rstrip(), plan_a),
                (f"{par} s'installe sur la {colonne} {where}".replace("  ", " ").rstrip(), plan_b),
            ]
    elif voice == CLASSICAL:
        if is_queen:
            options = [
                (f"{par} occupe la {colonne} {where}".replace("  ", " ").rstrip(),
                 "la dame y est exposée, prévois de la soutenir ou de la retirer si l'adversaire la conteste"),
                (f"{par} vient s'établir sur la {colonne} {where}".replace("  ", " ").rstrip(),
                 "utilise cette colonne pour amener une autre pièce, la dame n'y reste pas seule longtemps"),
            ]
        else:
            if half:
                plan_a = "exerce la pression sur le pion adverse de cette colonne avant tout autre plan"
                plan_b = ("double ensuite sur la colonne, la pression sur ce pion en sera doublée"
                          if double else "fixe ce pion, puis attaque-le avec une pièce de plus")
            else:
                plan_a = ("double ensuite sur la colonne pour en tirer profit"
                          if double else "occupe durablement la colonne et cherche la pénétration sur les rangées faibles")
                plan_b = ("amène l'autre tour sur la même colonne avant de poursuivre"
                          if double else "installe-toi sur la rangée de pénétration avant qu'il ne conteste la colonne")
            options = [
                (f"{par} occupe la {colonne} {where}".replace("  ", " ").rstrip(), plan_a),
                (f"{par} vient s'établir sur la {colonne} {where}".replace("  ", " ").rstrip(), plan_b),
            ]
    else:
        if is_queen:
            options = [
                (f"{par} se poste sur la {colonne} {where}".replace("  ", " ").rstrip(),
                 "surveille cette dame avancée, elle peut vite être attaquée"),
                (f"{par} se met sur la {colonne} {where}".replace("  ", " ").rstrip(),
                 "sers-t'en comme appui, mais ne la laisse pas avancer seule trop loin"),
            ]
        else:
            if half:
                plan_a = "vise le pion adverse qui reste sur cette colonne, il ne peut pas bouger"
                plan_b = ("double tes tours sur cette colonne, ce pion va souffrir"
                          if double else "garde la pression sur ce pion, c'est la cible de la colonne")
            else:
                plan_a = "les tours aiment les colonnes ouvertes, garde-la"
                plan_b = ("double tes tours sur cette colonne, c'est un bon plan"
                          if double else "reste sur cette colonne, elle t'ouvre le camp adverse")
            options = [
                (f"{par} se poste sur la {colonne} {where}".replace("  ", " ").rstrip(), plan_a),
                (f"{par} se met sur la {colonne} {where}".replace("  ", " ").rstrip(), plan_b),
            ]

    obs, plan = _pick_variant(options, intent, ctx)
    return _f(obs, plan, None)


def _frag_reposition(intent, voice, ctx):
    # Piece deja developpee qui change de poste, sans capture : on ne peut pas
    # affirmer POURQUOI sans detecteur dedie, donc on decrit le fait seul --
    # AUCUN jugement sur la case d'arrivee (pas de "mieux"/"meilleur"/"bon
    # poste") dans l'observation, dans AUCUNE des formulations (voir
    # test_reposition_isole). Au moins 2 formulations par voix, choisies via
    # _pick_variant pour ne pas repeter le meme texte a chaque reposition.
    dest = _sq(intent.to_square)
    par = _piece_with_article(intent.moved_piece)
    where = f"en {dest}" if dest else ""
    if voice == CREATIVE:
        options = [
            (f"{par} part chercher un autre poste {where}".replace("  ", " ").rstrip(),
             "améliore ta pièce la moins bien placée, c'est souvent le bon coup"),
            (f"{par} va voir ailleurs {where}".replace("  ", " ").rstrip(),
             "bouge ta pièce la moins utile, garde l'initiative dans le jeu"),
        ]
    elif voice == CLASSICAL:
        options = [
            (f"{par} se replace {where}".replace("  ", " ").rstrip(),
             "améliore la pièce la moins active avant de forcer le jeu"),
            (f"{par} se redéploie {where}".replace("  ", " ").rstrip(),
             "réoriente les pièces mal placées avant d'ouvrir les hostilités"),
        ]
    else:
        options = [
            (f"{par} change de poste {where}".replace("  ", " ").rstrip(),
             "repositionne, il n'y a rien de forcé ici"),
            (f"{par} bouge de case {where}".replace("  ", " ").rstrip(),
             "recycle cette pièce, le jeu n'est pas encore forcé"),
        ]
    obs, plan = _pick_variant(options, intent, ctx)
    return _f(obs, plan, None)


_INTENT_FUNCS = {
    "mate": _frag_mate,
    "check_escape": _frag_check_escape,
    "capture_free": _frag_capture_free,
    "sacrifice": _frag_sacrifice,
    "gives_check": _frag_gives_check,
    "promotion": _frag_promotion,
    "capture_trade": _frag_capture_trade,
    "develop": _frag_develop,
    "castle": _frag_castle,
    "rook_file": _frag_rook_file,
    "reposition": _frag_reposition,
}


# Ordre de priorité des explications : la prophylaxie d'abord (ce que le coup
# ENLÈVE à l'adversaire -- le seul « pourquoi » profond d'un coup calme), puis
# le contraste géométrique (ce qu'il CHANGE -- moins profond, toujours vrai).
# On n'en rend JAMAIS deux : la cause est une apposition, pas un paragraphe.
#
# new_squares est calculé (Task 2) mais volontairement PAS narré ici : « le
# fou contrôle 7 cases de plus » est un comptage vrai et parfaitement creux --
# le champ reste disponible pour un futur seuil ou une future formulation, il
# n'a pas sa place dans une phrase aujourd'hui.
def _explain_cause(intent, voice, ctx=None):
    """
    Clause « pourquoi ce coup », ou None. Initiale minuscule, sans ponctuation
    finale (le tissage compose « observation -- cause »).

    Chaque formulation ci-dessous est adossée à un fait CALCULÉ (voir
    MoveIntent : prophylaxis, new_attack_square, escapes_attack,
    becomes_defended). Aucun jugement comparatif : on dit ce qui est compté,
    jamais que c'est bien.

    Jamais de pronom faisant référence à une pièce nommée ailleurs (son genre
    dépendrait du type de pièce, invisible ici) : le sujet reste "la pièce"
    ou "la case", tous deux grammaticalement féminins, pour que les accords
    soient toujours corrects sans connaître le type de pièce.
    """
    if ctx is not None and not getattr(ctx, "explain", True):
        return None
    if intent is None:
        return None

    proph = getattr(intent, "prophylaxis", None)
    if proph and proph.get("san"):
        san = proph["san"]
        if proph.get("reason") == "captured":
            if voice == CREATIVE:
                return f"la pièce qui permettait {san} n'est plus là"
            if voice == CLASSICAL:
                return f"le coup supprime la pièce qui rendait {san} possible"
            return f"ce coup enlève {san} à l'adversaire"
        if voice == CREATIVE:
            return f"après ce coup, {san} ne passe plus"
        if voice == CLASSICAL:
            return f"le coup rend {san} impossible"
        return f"ce coup empêche {san}"

    # Les trois causes suivantes sont des CONTRASTES GÉOMÉTRIQUES (comptages
    # sur MoveIntent). Vraies, mais banales -- coupées quand l'écart d'éval
    # ne peut pas les filtrer (mode livre, voir narration_v2). Défaut True :
    # tout appelant sans ctx garde le comportement d'avant.
    if ctx is not None and not getattr(ctx, "explain_geometry", True):
        return None

    target = getattr(intent, "new_attack_square", None)
    if target is not None:
        case = _sq(target)
        if voice == CREATIVE:
            return f"la pièce adverse en {case} se retrouve sous le feu"
        if voice == CLASSICAL:
            return f"le coup crée une attaque sur {case}"
        return f"ce coup attaque la pièce en {case}"

    if getattr(intent, "escapes_attack", False):
        if voice == CREATIVE:
            return "la pièce s'éloigne de la menace qui pesait sur elle"
        if voice == CLASSICAL:
            return "le coup soustrait la pièce à l'attaque qu'elle subissait"
        return "la pièce n'est plus prise pour cible par la pièce qui la menaçait"

    if getattr(intent, "becomes_defended", False):
        if voice == CREATIVE:
            return "la pièce se pose sur une case couverte par tes autres pièces"
        if voice == CLASSICAL:
            return "la pièce arrive sur une case défendue, contrairement à sa case de départ"
        return "la pièce arrive sur une case défendue"

    return None


def fragments_for_intent(intent, voice, ctx=None):
    """
    Fragments {observation, cause, plan} décrivant le COUP recommandé (voir
    move_intent.MoveIntent), dans la voix demandée. Miroir de fragments_for()
    mais pour les intentions de coup, pas les thèmes de position.

    intent : MoveIntent. Un intent de kind "quiet" (non forçant) n'a pas de
             fragment dédié -> retourne None (l'appelant garde le thème de
             position). Un kind inconnu -> None aussi (jamais d'exception).
    voice  : "popular" / "creative" / "classical". Inconnue -> VOICE_FALLBACK.
    ctx    : FragmentContext optionnel, mais RÉELLEMENT UTILISÉ -- le passer
             change le texte :
               - ctx.recent_kinds alimente _pick_variant (anti-répétition) ;
                 sans ctx, les variantes ne tournent plus et le même paragraphe
                 ressort à chaque coup de même kind ;
               - ctx.board sert aux fragments qui doivent COMPTER quelque chose
                 avant de l'affirmer (droits de roque dans _frag_develop,
                 seconde tour dans _frag_rook_file). Sans board, ils retombent
                 sur la formulation qui n'affirme rien.
             ctx=None reste sûr (jamais d'exception, jamais d'invention), mais
             appauvrit le texte.

    Retour : dict fragment, ou None si aucune intention à narrer.
    """
    if intent is None:
        return None
    if voice not in VOICES:
        voice = VOICE_FALLBACK
    fn = _INTENT_FUNCS.get(intent.kind)
    if fn is None:
        return None  # "quiet" ou inconnu : pas d'intention marquante à raconter
    frag = fn(intent, voice, ctx)
    # La cause du fragment gagne si elle existe (_frag_capture_free pose le
    # concept why détecté, plus précis qu'un comptage géométrique). Sinon on
    # remplit ici, à l'UNIQUE point de passage des onze fragments d'intention,
    # plutôt que d'éparpiller la même logique dans chacun.
    if frag is not None and not frag.get("cause"):
        frag["cause"] = _explain_cause(intent, voice, ctx)
    return frag


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
