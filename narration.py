"""
narration.py
Transforme (thème détecté, profil, coup choisi, justification) en un
message affichable -- toujours 2 blocs (observation + implication), dont
les LIBELLÉS varient selon le profil qui parle (voix ET fond changent, pas
juste le fond -- voir la conversation).

Critère directeur pour chaque gabarit : "un entraîneur humain dirait-il
vraiment ça, là, tout de suite ?" -- une observation courte et tranchée,
jamais un rapport d'analyse.

Rien n'est inventé : chaque phrase s'appuie sur une donnée réelle (case,
pièce, ampleur en pions, motif tactique détecté) fournie par
theme_detector.py / why_detector.py.

Plusieurs FORMULATIONS par (thème x profil) pour les thèmes les plus
fréquents en partie (BLUNDER, TACTICAL, STRATEGIC_ADVANTAGE,
EQUAL_POSITION) -- sans ça, le même thème répété plusieurs fois dans une
partie retombe toujours sur EXACTEMENT la même phrase, ce qui redonne vite
l'impression d'un moteur plutôt que d'un coach. Le choix de variante est
déterministe (dérivé de la position + du profil), pas aléatoire à chaque
appel -- une même position redonne toujours la même formulation, mais 2
positions différentes ont de bonnes chances de varier.
"""
import chess

from theme_detector import (
    BLUNDER, TACTICAL, ATTACK, DEFENSE, MISSED_OPPORTUNITY,
    ENDGAME, OPENING, STRATEGIC_ADVANTAGE, EQUAL_POSITION,
)

PIECE_NAMES_FR = {
    chess.PAWN: "pion", chess.KNIGHT: "cavalier", chess.BISHOP: "fou",
    chess.ROOK: "tour", chess.QUEEN: "dame", chess.KING: "roi",
}

# Icône (voir webview_ui.py, mêmes clés côté JS) par thème.
THEME_ICONS = {
    BLUNDER: "alert",
    TACTICAL: "bolt",
    ATTACK: "sword",
    DEFENSE: "shield",
    MISSED_OPPORTUNITY: "rewind",
    ENDGAME: "flag",
    OPENING: "book",
    STRATEGIC_ADVANTAGE: "trend",
    EQUAL_POSITION: "scale",
}

THEME_LABELS_FR = {
    BLUNDER: "Opportunité",
    TACTICAL: "Thème tactique",
    ATTACK: "Attaque",
    DEFENSE: "Défense",
    MISSED_OPPORTUNITY: "Occasion à saisir",
    ENDGAME: "Finale",
    OPENING: "Ouverture",
    STRATEGIC_ADVANTAGE: "Avantage stratégique",
    EQUAL_POSITION: "Position équilibrée",
}

# Libellé pédagogique du motif tactique détecté (voir why_detector.py) --
# utilisé pour NOMMER le concept, pas juste décrire l'effet. C'est ce qui
# rend la narration vraiment pédagogique plutôt que descriptive.
WHY_CONCEPT_NAME_FR = {
    "fork": "une fourchette",
    "pin": "un clouage",
    "undefended": "une pièce non défendue",
    "not_recaptured": "une pièce non défendue",
    "forced_sequence": "une séquence forcée",
    "open_file": "une colonne ouverte",
    "material_gain": "un gain de matériel net",
}


def _piece_name(board, square):
    piece = board.piece_at(square)
    return PIECE_NAMES_FR.get(piece.piece_type, "pièce") if piece else "pièce"


def _sq(square):
    return chess.square_name(square)


def _pawns(cp):
    return round(abs(cp) / 100, 1)


def _why_phrase(why_motif, why_detail, chosen, board):
    """Bout de phrase réutilisable citant la justification, si dispo (jamais inventé)."""
    move = chess.Move.from_uci(chosen["move_uci"])
    if why_motif == "fork":
        return f"le {_piece_name(board, move.from_square)} en {_sq(move.to_square)} attaque 2 pièces à la fois"
    if why_motif == "pin":
        return f"ce coup cloue une pièce adverse -- elle ne peut plus bouger sans exposer une pièce plus précieuse"
    if why_motif == "undefended":
        return f"la case {_sq(move.to_square)} n'est défendue par aucune pièce adverse"
    if why_motif == "not_recaptured":
        return "l'adversaire ne peut pas reprendre sur cette case"
    if why_motif == "forced_sequence":
        return "la séquence est forcée, il n'y a pas d'alternative sérieuse"
    if why_motif == "open_file":
        status = why_detail.get("file_status")
        label = "une colonne ouverte" if status == "open" else "une colonne semi-ouverte"
        return f"cette pièce prend {label}, un vrai atout positionnel"
    if why_motif == "material_gain":
        gain = why_detail.get("gain", 0)
        return f"le gain net avoisine {gain} point{'s' if gain > 1 else ''} de matériel sur la ligne"
    return None


def _concept_name(why_motif):
    return WHY_CONCEPT_NAME_FR.get(why_motif)


def _variant_index(board, profile_id, n_variants):
    """
    Choix déterministe (pas aléatoire à chaque appel) de la formulation à
    utiliser, dérivé de la position + du profil -- 2 appels sur LA MÊME
    position redonnent toujours la même formulation (utile pour la
    cohérence si plusieurs requêtes arrivent pour la même position), mais
    2 positions différentes varient naturellement.
    """
    if n_variants <= 1:
        return 0
    key = board.board_fen() + "|" + profile_id
    return hash(key) % n_variants


def _pick(variants, board, profile_id):
    idx = _variant_index(board, profile_id, len(variants))
    return variants[idx]


# ---------------------------------------------------------------------
# Gabarits : {theme: {profile: [liste de fonctions variantes]}}
# Chaque fonction : (theme_result, chosen, why_motif, why_detail, board) -> dict
# ---------------------------------------------------------------------

def _blunder_popular_1(t, c, wm, wd, board):
    why = _why_phrase(wm, wd, c, board)
    text2 = "Prends ce qui est à prendre avant qu'il ne réorganise sa défense."
    if why:
        text2 = f"Prends ce qui est à prendre : {why}."
    return {"label1": "Opportunité", "text1": "Ton adversaire vient de relâcher la pression.",
            "label2": "Comment en profiter", "text2": text2}


def _blunder_popular_2(t, c, wm, wd, board):
    pawns = _pawns(t.swing_cp) if t.swing_cp else None
    detail = f" (environ {pawns} pion{'s' if pawns and pawns > 1 else ''} d'un coup)" if pawns else ""
    return {"label1": "Erreur adverse", "text1": f"Le dernier coup lui coûte cher{detail}.",
            "label2": "Réaction", "text2": "Ne le laisse pas se rattraper -- joue le coup le plus concret disponible."}


def _blunder_tactical_1(t, c, wm, wd, board):
    concept = _concept_name(wm)
    text1 = "Une faiblesse nette vient d'apparaître dans son camp."
    if concept:
        text1 = f"Une faiblesse nette vient d'apparaître : {concept} possible."
    return {"label1": "Il vient de craquer", "text1": text1,
            "label2": "Fonce", "text2": "Frappe maintenant, avant qu'il ne referme la position."}


def _blunder_tactical_2(t, c, wm, wd, board):
    return {"label1": "Sang dans l'eau", "text1": "La position vient de basculer nettement en ta faveur.",
            "label2": "Exploite", "text2": "Cherche le coup le plus tranchant, pas le plus prudent."}


def _blunder_classical_1(t, c, wm, wd, board):
    return {"label1": "Principe", "text1": "Une erreur de l'adversaire se sanctionne immédiatement, sans détour.",
            "label2": "Application", "text2": "Vérifie la ligne, puis exécute-la sans hésiter."}


def _tactical_popular_1(t, c, wm, wd, board):
    return {"label1": "Moment clé", "text1": "Il y a un coup fort à jouer maintenant, pas juste un bon coup parmi d'autres.",
            "label2": "À faire", "text2": "Prends le temps de vérifier les captures et les échecs avant de jouer."}


def _tactical_popular_2(t, c, wm, wd, board):
    return {"label1": "Attention", "text1": "Un seul coup se détache vraiment des autres ici.",
            "label2": "À faire", "text2": "Compare-le sérieusement à tes premières idées avant de jouer autre chose."}


def _tactical_tactical_1(t, c, wm, wd, board):
    concept = _concept_name(wm)
    text2 = "Ouvre les lignes avant que l'adversaire ne puisse se réorganiser."
    if concept:
        text2 = f"{concept[0].upper()}{concept[1:]} en vue -- fonce."
    return {"label1": "Thème tactique", "text1": "La position est instable, un seul coup compte vraiment ici.",
            "label2": "Suite", "text2": text2}


def _tactical_tactical_2(t, c, wm, wd, board):
    return {"label1": "Ça se tend", "text1": "Les pièces sont assez proches pour qu'un calcul précis paie.",
            "label2": "Suite", "text2": "Ne joue pas le coup automatique -- cherche la complication."}


def _tactical_classical_1(t, c, wm, wd, board):
    return {"label1": "Nature de la position", "text1": "Position tactique typique : le calcul prime sur le plan général.",
            "label2": "Priorité", "text2": "Vérifie d'abord la sécurité du roi et les pièces non défendues."}


def _attack_popular_1(t, c, wm, wd, board):
    return {"label1": "Roi adverse exposé", "text1": "Le roi adverse manque de défenseurs autour de lui.",
            "label2": "Plan", "text2": "Continue d'amener tes pièces vers ce côté, l'avantage devrait se concrétiser."}


def _attack_tactical_1(t, c, wm, wd, board):
    return {"label1": "Le roi est la cible", "text1": "Chaque pièce en plus près de son roi rapproche la conclusion.",
            "label2": "Suite", "text2": "N'hésite pas à sacrifier du matériel si ça ouvre une ligne vers lui."}


def _attack_classical_1(t, c, wm, wd, board):
    return {"label1": "Principe", "text1": "Un roi affaibli justifie de concentrer les forces plutôt que de gagner du matériel.",
            "label2": "Suite logique", "text2": "Amène la pièce la moins active avant de forcer quoi que ce soit."}


def _defense_popular_1(t, c, wm, wd, board):
    return {"label1": "Ton roi est exposé", "text1": "L'adversaire a plus de pièces actives près de ton roi que toi.",
            "label2": "Priorité", "text2": "Consolide d'abord, cherche la contre-attaque une fois la position stabilisée."}


def _defense_tactical_1(t, c, wm, wd, board):
    return {"label1": "Danger réel", "text1": "Le roi n'a pas assez de défenseurs -- l'attaque adverse est concrète.",
            "label2": "Réaction", "text2": "Cherche un coup qui complique tout de suite, pas juste un coup qui défend."}


def _defense_classical_1(t, c, wm, wd, board):
    return {"label1": "Principe", "text1": "Face à une attaque, la priorité va toujours à la sécurité du roi.",
            "label2": "Méthode", "text2": "Élimine d'abord les menaces directes avant de penser au reste du plateau."}


def _missed_popular_1(t, c, wm, wd, board):
    pawns = _pawns(t.swing_cp) if t.swing_cp else None
    detail = f" (environ {pawns} pion{'s' if pawns and pawns > 1 else ''})" if pawns else ""
    text1 = f"Ton adversaire n'a pas trouvé la ligne la plus incisive{detail}."
    if t.opponent_better_move_san:
        text1 = f"Ton adversaire avait {t.opponent_better_move_san} de disponible et n'a pas joué ça{detail}."
    return {"label1": "Occasion pour toi", "text1": text1,
            "label2": "Maintenant", "text2": "C'est le moment de prendre l'initiative, avant qu'il ne se reprenne."}


def _missed_tactical_1(t, c, wm, wd, board):
    text1 = "Ton adversaire a joué la solution la plus calme, pas la plus dangereuse."
    if t.opponent_better_move_san:
        text1 = f"Il avait {t.opponent_better_move_san}, bien plus tranchant, et ne l'a pas joué."
    return {"label1": "Il a hésité", "text1": text1,
            "label2": "Fonce", "text2": "Ne lui laisse pas le temps de se reprendre -- pousse la position."}


def _missed_classical_1(t, c, wm, wd, board):
    text1 = "L'adversaire s'est éloigné du plan le plus solide."
    if t.opponent_better_move_san:
        text1 = f"{t.opponent_better_move_san} suivait mieux les principes -- il ne l'a pas joué."
    return {"label1": "Occasion pour toi", "text1": text1,
            "label2": "Méthode", "text2": "Continue à jouer solidement, l'avantage devrait grandir naturellement."}


def _endgame_popular_1(t, c, wm, wd, board):
    if t.passed_pawn_square is not None:
        return {"label1": "Priorité", "text1": f"Le pion en {_sq(t.passed_pawn_square)} n'a plus aucun pion adverse pour l'arrêter.",
                "label2": "Plan", "text2": "Pousse-le en le soutenant avec ton roi ou tes pièces."}
    return {"label1": "Priorité", "text1": "Les dames ne sont plus sur l'échiquier, le roi devient une pièce active.",
            "label2": "Plan", "text2": "Avance-le vers le centre, il peut participer sans risque désormais."}


def _endgame_tactical_1(t, c, wm, wd, board):
    return {"label1": "Peu de pièces, chaque coup compte", "text1": "En finale, une seule case perdue peut décider de la partie.",
            "label2": "Suite", "text2": "Cherche la ligne la plus forcée, pas la plus prudente."}


def _endgame_classical_1(t, c, wm, wd, board):
    if t.passed_pawn_square is not None:
        return {"label1": "Principe de finale", "text1": f"Un pion passé existe en {_sq(t.passed_pawn_square)} -- c'est l'atout principal de cette finale.",
                "label2": "Plan", "text2": "Amène ton roi devant lui avant de le pousser, c'est la méthode classique."}
    return {"label1": "Principe de finale", "text1": "Roi actif et opposition -- les 2 leviers classiques d'une finale sans pion passé.",
            "label2": "Plan", "text2": "Cherche à gagner l'opposition ou à créer un pion passé par une poussée de pions."}


def _opening_popular_1(t, c, wm, wd, board):
    return {"label1": "Principe", "text1": "Termine ton développement avant de chercher plus.",
            "label2": "Suite logique", "text2": "Roque puis connecte tes tours, le reste suivra naturellement."}


def _opening_tactical_1(t, c, wm, wd, board):
    return {"label1": "Encore tôt pour attaquer", "text1": "Toutes les pièces ne sont pas encore prêtes à se battre.",
            "label2": "Suite", "text2": "Développe la pièce la plus utile, garde l'idée d'attaque pour plus tard."}


def _opening_classical_1(t, c, wm, wd, board):
    return {"label1": "Principe", "text1": "Centre, développement, sécurité du roi -- dans cet ordre.",
            "label2": "Suite logique", "text2": "Ce coup avance l'un de ces 3 objectifs sans en sacrifier un autre."}


def _strategic_popular_1(t, c, wm, wd, board):
    return {"label1": "Avantage", "text1": "La position est nettement meilleure, sans qu'il y ait de coup immédiat à calculer.",
            "label2": "Plan", "text2": "Continue d'améliorer ta pièce la moins bien placée, l'avantage se maintient de lui-même."}


def _strategic_popular_2(t, c, wm, wd, board):
    pawns = _pawns(t.eval_cp)
    return {"label1": "Position favorable", "text1": f"L'avantage tourne autour de {pawns} pion{'s' if pawns > 1 else ''}, sans rien de forcé.",
            "label2": "Plan", "text2": "Pas besoin de précipiter les choses -- améliore ta position coup après coup."}


def _strategic_tactical_1(t, c, wm, wd, board):
    return {"label1": "Sous la surface", "text1": "L'avantage est réel, même sans motif tactique visible pour l'instant.",
            "label2": "Suite", "text2": "Cherche à créer une complication qui rendra la position plus dure à défendre."}


def _strategic_tactical_2(t, c, wm, wd, board):
    return {"label1": "Ça couve", "text1": "Rien d'immédiat, mais la tension va finir par se libérer quelque part.",
            "label2": "Suite", "text2": "Prépare le terrain plutôt que de forcer un coup qui n'est pas encore prêt."}


def _strategic_classical_1(t, c, wm, wd, board):
    if t.has_bishop_pair:
        return {"label1": "Nature de l'avantage", "text1": "Tu as la paire de fous -- un vrai atout à long terme, surtout si la position s'ouvre.",
                "label2": "Plan", "text2": "Cherche à ouvrir la position plutôt qu'à la refermer, ça les rend plus forts."}
    return {"label1": "Nature de l'avantage", "text1": "L'avantage tient à la position des pièces, pas à un gain de matériel.",
            "label2": "Plan", "text2": "Continue à améliorer la coordination avant de chercher à forcer quoi que ce soit."}


def _equal_popular_1(t, c, wm, wd, board):
    return {"label1": "Position équilibrée", "text1": "Rien ne se dégage clairement pour l'instant, la partie reste ouverte.",
            "label2": "Approche", "text2": "Choisis le plan le plus simple à exécuter, pas le plus ambitieux."}


def _equal_popular_2(t, c, wm, wd, board):
    return {"label1": "Statu quo", "text1": "Aucun camp n'a vraiment pris l'avantage jusqu'ici.",
            "label2": "Approche", "text2": "Joue le coup le plus solide, laisse l'adversaire prendre le risque en premier."}


def _equal_tactical_1(t, c, wm, wd, board):
    return {"label1": "Calme avant la tempête", "text1": "L'équilibre actuel ne va pas forcément durer.",
            "label2": "Suite", "text2": "C'est le bon moment pour préparer une complication future."}


def _equal_tactical_2(t, c, wm, wd, board):
    return {"label1": "Tension latente", "text1": "La position est équilibrée mais pas vraiment calme.",
            "label2": "Suite", "text2": "Cherche quel camp a le plus à gagner à faire monter la tension -- et fais-le si c'est toi."}


def _equal_classical_1(t, c, wm, wd, board):
    return {"label1": "Position équilibrée", "text1": "Aucun camp n'a d'avantage net, la structure de pions guide le plan.",
            "label2": "Suite logique", "text2": "Identifie la case faible dans le camp adverse et vise-la progressivement."}


TEMPLATES = {
    BLUNDER: {
        "popular": [_blunder_popular_1, _blunder_popular_2],
        "creative": [_blunder_tactical_1, _blunder_tactical_2],
        "classical": [_blunder_classical_1],
    },
    TACTICAL: {
        "popular": [_tactical_popular_1, _tactical_popular_2],
        "creative": [_tactical_tactical_1, _tactical_tactical_2],
        "classical": [_tactical_classical_1],
    },
    ATTACK: {
        "popular": [_attack_popular_1], "creative": [_attack_tactical_1], "classical": [_attack_classical_1],
    },
    DEFENSE: {
        "popular": [_defense_popular_1], "creative": [_defense_tactical_1], "classical": [_defense_classical_1],
    },
    MISSED_OPPORTUNITY: {
        "popular": [_missed_popular_1], "creative": [_missed_tactical_1], "classical": [_missed_classical_1],
    },
    ENDGAME: {
        "popular": [_endgame_popular_1], "creative": [_endgame_tactical_1], "classical": [_endgame_classical_1],
    },
    OPENING: {
        "popular": [_opening_popular_1], "creative": [_opening_tactical_1], "classical": [_opening_classical_1],
    },
    STRATEGIC_ADVANTAGE: {
        "popular": [_strategic_popular_1, _strategic_popular_2],
        "creative": [_strategic_tactical_1, _strategic_tactical_2],
        "classical": [_strategic_classical_1],
    },
    EQUAL_POSITION: {
        "popular": [_equal_popular_1, _equal_popular_2],
        "creative": [_equal_tactical_1, _equal_tactical_2],
        "classical": [_equal_classical_1],
    },
}


CAUTION_TEXT_FR = {
    "stalemate_risk": "Attention au pat : l'adversaire n'a presque plus de coups légaux -- vérifie que ton coup lui en laisse au moins un.",
}


def _suite_phrase(chosen, max_moves=3):
    """
    Résume la suite ENVISAGÉE par ce coup précis -- les coups qui suivent
    dans la ligne réellement calculée par le moteur (jamais inventés),
    SANS le coup lui-même (déjà visible via la flèche sur l'échiquier, pas
    la peine de le répéter en texte). None si la ligne est trop courte
    pour dire quoi que ce soit d'utile (ex: coup de livre, qui n'a pas de
    ligne calculée au-delà de lui-même).

    C'est ce qui permet au coach de répondre concrètement à "quelle suite
    ce coup envisage-t-il ?", pas juste "pourquoi ce coup".
    """
    pv = chosen.get("pv_san") or []
    follow_up = pv[1:1 + max_moves]
    if not follow_up:
        return None
    return " ".join(follow_up)


def generate_narration(theme_result, profile_id, chosen, why_motif, why_detail, board):
    """
    Retourne un dict prêt à afficher :
    {"theme_label", "theme_icon", "label1", "text1", "label2", "text2",
    "suite", "caution"} --
    "suite" (optionnel) : la ligne concrètement envisagée après ce coup
    (voir _suite_phrase) -- répond à "quelle suite ce coup a-t-il en tête",
    distinct du "pourquoi" déjà couvert par label2/text2.
    "caution" (optionnel) : avertissement transversal, indépendant du
    thème principal (ex: risque de pat en finale gagnante).
    """
    theme = theme_result.theme
    profile_templates = TEMPLATES.get(theme, TEMPLATES[EQUAL_POSITION])
    variants = profile_templates.get(profile_id, profile_templates["popular"])
    tpl_fn = _pick(variants, board, profile_id)
    body = tpl_fn(theme_result, chosen, why_motif, why_detail, board)
    result = {
        "theme_label": THEME_LABELS_FR.get(theme, theme),
        "theme_icon": THEME_ICONS.get(theme, "info"),
        **body,
    }
    suite = _suite_phrase(chosen)
    if suite:
        result["suite"] = suite
    if theme_result.caution:
        caution_text = CAUTION_TEXT_FR.get(theme_result.caution)
        if caution_text:
            result["caution"] = caution_text
    return result
