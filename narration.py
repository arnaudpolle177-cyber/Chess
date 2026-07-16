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

import opening_identity
import variation_narrator

from theme_detector import (
    BLUNDER, TACTICAL, ATTACK, DEFENSE, MISSED_OPPORTUNITY,
    ENDGAME, OPENING, INITIATIVE_SHIFT, STRATEGIC_ADVANTAGE, PAWN_STRUCTURE,
    PIECE_ACTIVITY_GAP, KING_SAFETY_WARNING, EQUAL_POSITION,
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
    INITIATIVE_SHIFT: "pulse",
    STRATEGIC_ADVANTAGE: "trend",
    PAWN_STRUCTURE: "target",
    PIECE_ACTIVITY_GAP: "move",
    KING_SAFETY_WARNING: "shield",
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
    INITIATIVE_SHIFT: "Dynamique de la partie",
    STRATEGIC_ADVANTAGE: "Avantage stratégique",
    PAWN_STRUCTURE: "Faiblesse à cibler",
    PIECE_ACTIVITY_GAP: "Avantage de mobilité",
    KING_SAFETY_WARNING: "Sécurité du roi",
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


_SIMPLIFICATION_ADVICE_TEXT = {
    "simplify": "Cherche à échanger les pièces quand l'occasion se présente -- ça réduit le contre-jeu adverse et rend l'avantage plus facile à concrétiser.",
    "keep_tension": "Évite les échanges pour l'instant -- garde les pièces sur l'échiquier tant que la dynamique actuelle joue en ta faveur.",
}


def _strategic_popular_1(t, c, wm, wd, board):
    plan = _SIMPLIFICATION_ADVICE_TEXT.get(t.simplification_advice,
        "Continue d'améliorer ta pièce la moins bien placée, l'avantage se maintient de lui-même.")
    return {"label1": "Avantage", "text1": "La position est nettement meilleure, sans qu'il y ait de coup immédiat à calculer.",
            "label2": "Plan", "text2": plan}


def _strategic_popular_2(t, c, wm, wd, board):
    pawns = _pawns(t.eval_cp)
    plan = _SIMPLIFICATION_ADVICE_TEXT.get(t.simplification_advice,
        "Pas besoin de précipiter les choses -- améliore ta position coup après coup.")
    return {"label1": "Position favorable", "text1": f"L'avantage tourne autour de {pawns} pion{'s' if pawns > 1 else ''}, sans rien de forcé.",
            "label2": "Plan", "text2": plan}


def _strategic_tactical_1(t, c, wm, wd, board):
    if t.simplification_advice == "keep_tension":
        return {"label1": "Sous la surface", "text1": "L'avantage est réel, même sans motif tactique visible pour l'instant.",
                "label2": "Suite", "text2": "La dynamique actuelle joue pour toi -- évite les échanges qui la calmeraient."}
    return {"label1": "Sous la surface", "text1": "L'avantage est réel, même sans motif tactique visible pour l'instant.",
            "label2": "Suite", "text2": "Cherche à créer une complication qui rendra la position plus dure à défendre."}


def _strategic_tactical_2(t, c, wm, wd, board):
    if t.simplification_advice == "keep_tension":
        return {"label1": "Ça couve", "text1": "Rien d'immédiat, mais la tension va finir par se libérer quelque part.",
                "label2": "Suite", "text2": "Garde les pièces sur l'échiquier -- ton initiative actuelle mérite d'être poussée plus loin."}
    return {"label1": "Ça couve", "text1": "Rien d'immédiat, mais la tension va finir par se libérer quelque part.",
            "label2": "Suite", "text2": "Prépare le terrain plutôt que de forcer un coup qui n'est pas encore prêt."}


_MATERIAL_IMBALANCE_TEXT = {
    "bishop_pair_open": {
        "text1": "Tu as la paire de fous dans une position déjà ouverte -- l'avantage est immédiatement exploitable.",
        "simplify": "Continue d'ouvrir les lignes et cherche à échanger les pièces mineures adverses -- tes fous prendront encore plus de valeur dans un plateau dégagé.",
        "keep_tension": "Continue d'ouvrir les lignes, mais garde les pièces sur l'échiquier pour l'instant -- la dynamique actuelle mérite d'être poussée avant de simplifier.",
    },
    "bishop_pair_closed": {
        "text1": "Tu as la paire de fous, mais la position reste fermée pour l'instant -- l'avantage est encore latent.",
        "simplify": "Cherche à ouvrir la position progressivement, c'est là qu'ils prendront toute leur valeur -- pas la peine de précipiter d'autres échanges avant ça.",
        "keep_tension": "Cherche à ouvrir la position progressivement, mais évite les échanges pour l'instant -- ta dynamique actuelle vaut mieux qu'une simplification prématurée.",
    },
    "knights_closed": {
        "text1": "Tes cavaliers sont mieux adaptés que les fous adverses tant que la position reste fermée.",
        "simplify": "Garde la structure fermée et cherche à échanger les pièces les moins actives -- ça conserve ton avantage tout en simplifiant la conversion.",
        "keep_tension": "Garde la structure fermée ET les pièces sur l'échiquier pour l'instant -- ta dynamique actuelle vaut mieux qu'une simplification prématurée.",
    },
    "rook_vs_minors": {
        "text1": "Tu as une tour contre des pièces mineures -- un déséquilibre qui favorise généralement la finale.",
        "simplify": "Cherche à simplifier vers une finale, la tour prend de la valeur quand le plateau se dégage.",
        "keep_tension": "Résiste à l'envie de simplifier tout de suite -- ta dynamique actuelle vaut la peine d'être poussée avant de viser la finale.",
    },
}


def _strategic_classical_1(t, c, wm, wd, board):
    imbalance = _MATERIAL_IMBALANCE_TEXT.get(t.material_imbalance_kind)
    if imbalance:
        # simplification_advice vaut toujours "simplify" ou "keep_tension"
        # dès que STRATEGIC_ADVANTAGE est actif (voir theme_detector.py,
        # _simplification_advice -- jamais None en pratique aujourd'hui),
        # mais le .get() avec repli reste une protection saine si ça change.
        plan = imbalance.get(t.simplification_advice, imbalance["simplify"])
        return {"label1": "Nature de l'avantage", "text1": imbalance["text1"], "label2": "Plan", "text2": plan}
    simplify_text = _SIMPLIFICATION_ADVICE_TEXT.get(t.simplification_advice)
    if simplify_text:
        return {"label1": "Nature de l'avantage", "text1": "L'avantage tient à la position des pièces, pas à un gain de matériel.",
                "label2": "Plan", "text2": simplify_text}
    return {"label1": "Nature de l'avantage", "text1": "L'avantage tient à la position des pièces, pas à un gain de matériel.",
            "label2": "Plan", "text2": "Continue à améliorer la coordination avant de chercher à forcer quoi que ce soit."}


_PAWN_WEAKNESS_LABEL_FR = {"doubled": "pion doublé", "isolated": "pion isolé"}


def _pawn_structure_popular_1(t, c, wm, wd, board):
    kind = _PAWN_WEAKNESS_LABEL_FR.get(t.pawn_weakness_kind, "faiblesse de pion")
    sq = _sq(t.pawn_weakness_square)
    return {"label1": "Cible", "text1": f"L'adversaire a un {kind} en {sq} -- une faiblesse qui ne va pas disparaître toute seule.",
            "label2": "Plan", "text2": "Pas besoin de te précipiter dessus : garde-le en tête et fais peser la pression au bon moment."}


def _pawn_structure_tactical_1(t, c, wm, wd, board):
    kind = _PAWN_WEAKNESS_LABEL_FR.get(t.pawn_weakness_kind, "faiblesse de pion")
    sq = _sq(t.pawn_weakness_square)
    return {"label1": "Point faible repéré", "text1": f"Ce {kind} en {sq} est une porte d'entrée pour construire une attaque.",
            "label2": "Suite", "text2": "Amène tes pièces vers cette case, la pression finira par payer."}


def _pawn_structure_classical_1(t, c, wm, wd, board):
    kind = _PAWN_WEAKNESS_LABEL_FR.get(t.pawn_weakness_kind, "faiblesse de pion")
    sq = _sq(t.pawn_weakness_square)
    return {"label1": "Faiblesse structurelle", "text1": f"Le {kind} adverse en {sq} est un objectif à long terme, typique du jeu positionnel.",
            "label2": "Plan", "text2": "Améliore tes pièces en gardant cette case en ligne de mire, sans rien précipiter."}


def _piece_activity_popular_1(t, c, wm, wd, board):
    return {"label1": "Activité", "text1": "Tes pièces contrôlent davantage de cases importantes que celles de l'adversaire, sans avantage matériel pour autant.",
            "label2": "Plan", "text2": "Cette activité peut créer des menaces avant même que le matériel ne bouge -- profite de cette avance."}


def _piece_activity_tactical_1(t, c, wm, wd, board):
    return {"label1": "Pièces en mouvement", "text1": "Tes pièces sont nettement plus mobiles que celles de l'adversaire, même si l'éval reste serrée.",
            "label2": "Suite", "text2": "Cherche à transformer cette avance de mobilité en vraie menace concrète."}


def _piece_activity_classical_1(t, c, wm, wd, board):
    return {"label1": "Avantage de mobilité", "text1": "Sans gain de matériel, tes pièces occupent déjà de meilleures cases que celles de l'adversaire.",
            "label2": "Plan", "text2": "Continue d'améliorer la coordination -- l'avantage de mobilité précède souvent l'avantage matériel."}


def _king_safety_warning_popular_1(t, c, wm, wd, board):
    sq = _sq(t.king_safety_warning_square)
    if t.king_safety_warning_is_mine:
        return {"label1": "Prudence", "text1": f"Ton roi en {sq} commence à manquer de protection -- ce n'est pas encore dangereux, mais ça mérite attention.",
                "label2": "Plan", "text2": "Pense à mettre ton roi en sécurité avant que l'adversaire puisse vraiment en profiter."}
    return {"label1": "Occasion qui se prépare", "text1": f"Le roi adverse en {sq} commence à manquer de protection.",
            "label2": "Plan", "text2": "Pas encore critique, mais garde cette faiblesse en tête pour plus tard."}


def _king_safety_warning_tactical_1(t, c, wm, wd, board):
    sq = _sq(t.king_safety_warning_square)
    if t.king_safety_warning_is_mine:
        return {"label1": "Signal d'alerte", "text1": f"Ton roi en {sq} reste exposé -- de quoi devenir un vrai problème si l'adversaire s'organise.",
                "label2": "Suite", "text2": "Trouve un moyen de sécuriser ton roi avant de continuer tes plans offensifs."}
    return {"label1": "Cible en préparation", "text1": f"Le roi adverse en {sq} n'est pas encore attaqué, mais la porte commence à s'ouvrir.",
            "label2": "Suite", "text2": "Prépare tes pièces pour être prêt à frapper dès que l'occasion se précise."}


def _king_safety_warning_classical_1(t, c, wm, wd, board):
    sq = _sq(t.king_safety_warning_square)
    if t.king_safety_warning_is_mine:
        return {"label1": "Principe", "text1": f"La sécurité du roi passe avant tout le reste -- ton roi en {sq} n'est pas encore roqué et le centre s'ouvre.",
                "label2": "Plan", "text2": "Termine ta mise en sécurité avant de te lancer dans un plan plus ambitieux."}
    return {"label1": "Principe", "text1": f"Le roi adverse en {sq} tarde à se mettre en sécurité, dans un centre qui s'ouvre.",
            "label2": "Plan", "text2": "Continue ton développement -- cette faiblesse risque de compter plus tard dans la partie."}


def _initiative_popular_1(t, c, wm, wd, board):
    if t.eval_cp > 0:
        return {"label1": "Ça ralentit", "text1": "Tu gardes l'avantage, mais l'élan des derniers coups faiblit.",
                "label2": "Réaction", "text2": "Crée rapidement une nouvelle menace avant que l'adversaire ne reprenne la main."}
    return {"label1": "Ça revient", "text1": "La position reste difficile, mais tu regagnes du terrain coup après coup.",
            "label2": "Suite", "text2": "Continue sur cette lancée -- l'adversaire commence à perdre son avance."}


def _initiative_tactical_1(t, c, wm, wd, board):
    if t.eval_cp > 0:
        return {"label1": "Fenêtre qui se referme", "text1": "L'initiative que tu avais construite commence à s'effriter.",
                "label2": "Fonce", "text2": "C'est le moment de forcer les choses, pas de temporiser."}
    return {"label1": "Contre-attaque en marche", "text1": "Tu étais sous pression, mais l'initiative commence à changer de camp.",
            "label2": "Suite", "text2": "Pousse cette dynamique -- l'adversaire n'a peut-être pas encore réalisé le changement."}


def _initiative_classical_1(t, c, wm, wd, board):
    if t.eval_cp > 0:
        return {"label1": "Principe", "text1": "Un avantage qui n'est pas entretenu a tendance à s'estomper -- c'est ce qui commence à se produire ici.",
                "label2": "Plan", "text2": "Trouve un plan actif plutôt que de laisser la position se stabiliser d'elle-même."}
    return {"label1": "Principe", "text1": "La dynamique de la partie est en train de basculer progressivement en ta faveur.",
            "label2": "Plan", "text2": "Continue sur cette voie avec des coups actifs, sans revenir à la prudence trop tôt."}


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


# ---------------------------------------------------------------------
# Variantes supplémentaires : 2 à 3 formulations par (thème x profil),
# même IDÉE mais vocabulaire, structure de phrase et nuance différents.
# Toujours fondées sur les mêmes données réelles que les variantes _1
# (aucun champ inventé). _pick (dérivé de la position) répartit le choix,
# ce qui évite que 2 positions proches retombent sur la même phrase.
# ---------------------------------------------------------------------

# --- BLUNDER ---
def _blunder_popular_3(t, c, wm, wd, board):
    why = _why_phrase(wm, wd, c, board)
    text2 = "Convertis tout de suite : ici le coup le plus direct est souvent le meilleur."
    if why:
        text2 = f"Il y a du concret à jouer : {why}."
    return {"label1": "Ouverture", "text1": "La porte vient de s'ouvrir dans son camp.",
            "label2": "Concrétise", "text2": text2}


def _blunder_tactical_3(t, c, wm, wd, board):
    concept = _concept_name(wm)
    text2 = "Cherche le coup qui punit le plus fort, quitte à donner du matériel."
    if concept:
        text2 = f"{concept[0].upper()}{concept[1:]} à exploiter -- ne te contente pas du coup tranquille."
    return {"label1": "Faille ouverte", "text1": "Son dernier coup laisse une brèche exploitable.",
            "label2": "Punis", "text2": text2}


def _blunder_classical_2(t, c, wm, wd, board):
    pawns = _pawns(t.swing_cp) if t.swing_cp else None
    text1 = "Une imprécision adverse se convertit avec méthode, pas dans la précipitation."
    if pawns:
        text1 = f"L'adversaire vient de céder environ {pawns} pion{'s' if pawns > 1 else ''} -- à transformer proprement."
    return {"label1": "Méthode", "text1": text1,
            "label2": "Marche à suivre", "text2": "Calcule la ligne jusqu'au bout, puis exécute-la sans te retourner."}


# --- TACTICAL ---
def _tactical_popular_3(t, c, wm, wd, board):
    return {"label1": "Un coup se détache", "text1": "Ici les coups ne se valent pas : il y en a un nettement au-dessus.",
            "label2": "À faire", "text2": "Ne joue pas d'instinct -- vérifie d'abord les captures, échecs et coups forçants."}


def _tactical_tactical_3(t, c, wm, wd, board):
    concept = _concept_name(wm)
    text1 = "La position réclame du calcul concret, pas un plan général."
    if concept:
        text1 = f"Il y a {concept} à concrétiser -- c'est le moment de calculer précisément."
    return {"label1": "Calcul", "text1": text1,
            "label2": "Suite", "text2": "Suis la variante forçante jusqu'au bout avant de la jouer."}


def _tactical_classical_2(t, c, wm, wd, board):
    return {"label1": "Type de position", "text1": "Position concrète : le calcul exact prime sur les principes généraux.",
            "label2": "Priorité", "text2": "Contrôle les pièces non défendues et les échecs avant de te décider."}


# --- ATTACK ---
def _attack_popular_2(t, c, wm, wd, board):
    where = f" (roi en {_sq(t.king_square)})" if t.king_square is not None else ""
    return {"label1": "Cible dégagée", "text1": f"Le roi adverse est le point faible de la position{where}.",
            "label2": "Plan", "text2": "Fais converger tes pièces vers lui, l'avantage se transformera de lui-même."}


def _attack_tactical_2(t, c, wm, wd, board):
    return {"label1": "À l'assaut", "text1": "Son roi est à découvert -- c'est là qu'il faut frapper.",
            "label2": "Suite", "text2": "Ouvre une ligne vers lui, même au prix d'un pion ou d'une pièce."}


def _attack_classical_2(t, c, wm, wd, board):
    return {"label1": "Principe d'attaque", "text1": "Un roi affaibli s'attaque avec le maximum de pièces, pas avec une seule.",
            "label2": "Suite logique", "text2": "Fais entrer ta pièce la moins active dans l'attaque avant de forcer."}


# --- DEFENSE ---
def _defense_popular_2(t, c, wm, wd, board):
    where = f" en {_sq(t.king_square)}" if t.king_square is not None else ""
    return {"label1": "Roi sous pression", "text1": f"Ton roi{where} est moins bien entouré que celui de l'adversaire.",
            "label2": "Priorité", "text2": "Ramène un défenseur avant de penser à quoi que ce soit d'autre."}


def _defense_tactical_2(t, c, wm, wd, board):
    return {"label1": "Ça chauffe", "text1": "L'attaque adverse sur ton roi est bien réelle, pas une menace en l'air.",
            "label2": "Réaction", "text2": "Trouve le coup qui casse l'attaque, ou qui contre-attaque plus vite qu'elle."}


def _defense_classical_2(t, c, wm, wd, board):
    return {"label1": "Principe", "text1": "Sous attaque, on traite d'abord le danger le plus direct avant tout projet actif.",
            "label2": "Méthode", "text2": "Neutralise la pièce adverse la plus menaçante, puis réorganise ta défense."}


# --- MISSED_OPPORTUNITY ---
def _missed_popular_2(t, c, wm, wd, board):
    text1 = "L'adversaire n'a pas exploité tout ce que sa position offrait."
    if t.opponent_better_move_san:
        text1 = f"{t.opponent_better_move_san} était plus fort pour lui -- il a laissé passer."
    return {"label1": "Il t'a laissé respirer", "text1": text1,
            "label2": "Maintenant", "text2": "Reprends la main tant que la fenêtre est ouverte."}


def _missed_tactical_2(t, c, wm, wd, board):
    text1 = "Il a choisi la continuation sage plutôt que la plus mordante."
    if t.opponent_better_move_san:
        text1 = f"{t.opponent_better_move_san} mettait bien plus de pression -- il ne l'a pas vu."
    return {"label1": "Occasion manquée par lui", "text1": text1,
            "label2": "Fonce", "text2": "Sois plus incisif que lui : force la position avant qu'il ne se recentre."}


def _missed_classical_2(t, c, wm, wd, board):
    text1 = "L'adversaire a dévié du plan le plus rigoureux."
    if t.opponent_better_move_san:
        text1 = f"{t.opponent_better_move_san} respectait mieux la logique de la position."
    return {"label1": "Marge à exploiter", "text1": text1,
            "label2": "Méthode", "text2": "Reprends un jeu solide : ton avantage doit croître naturellement."}


# --- ENDGAME ---
def _endgame_popular_2(t, c, wm, wd, board):
    if t.passed_pawn_square is not None:
        return {"label1": "Atout de finale", "text1": f"Ton pion en {_sq(t.passed_pawn_square)} a la voie libre vers la promotion.",
                "label2": "Plan", "text2": "Fais-le avancer, mais garde une pièce pour l'escorter."}
    return {"label1": "Le roi entre en jeu", "text1": "Sans les dames, ton roi peut enfin s'avancer sans danger.",
            "label2": "Plan", "text2": "Dirige-le vers le centre : en finale, c'est une pièce offensive."}


def _endgame_tactical_2(t, c, wm, wd, board):
    return {"label1": "Précision de finale", "text1": "Il reste peu de pièces : la moindre imprécision se paie cash.",
            "label2": "Suite", "text2": "Calcule à fond les courses de pions et l'activité du roi avant de jouer."}


def _endgame_classical_2(t, c, wm, wd, board):
    if t.passed_pawn_square is not None:
        return {"label1": "Technique", "text1": f"Le pion passé en {_sq(t.passed_pawn_square)} est ta ressource principale ici.",
                "label2": "Plan", "text2": "Soutiens-le avec le roi avant de le pousser -- ne l'avance jamais seul."}
    return {"label1": "Technique de finale", "text1": "Sans pion passé, tout se joue sur l'activité du roi et l'opposition.",
            "label2": "Plan", "text2": "Active ton roi et cherche à créer une faiblesse durable dans l'autre camp."}


# --- OPENING ---
def _opening_popular_2(t, c, wm, wd, board):
    return {"label1": "Encore en développement", "text1": "L'important pour l'instant, c'est de faire sortir tes pièces.",
            "label2": "Suite logique", "text2": "Développe une pièce inactive vers une bonne case, puis mets ton roi à l'abri."}


def _opening_tactical_2(t, c, wm, wd, board):
    return {"label1": "Patience", "text1": "L'attaque devra attendre : tes forces ne sont pas toutes entrées en jeu.",
            "label2": "Suite", "text2": "Sors une pièce de plus vers le centre, l'occasion offensive viendra ensuite."}


def _opening_classical_2(t, c, wm, wd, board):
    return {"label1": "Principes d'ouverture", "text1": "Occupe le centre, développe vite, mets le roi en sécurité.",
            "label2": "Suite logique", "text2": "Choisis le coup qui sert un de ces buts sans compromettre les deux autres."}


# --- INITIATIVE_SHIFT ---
def _initiative_popular_2(t, c, wm, wd, board):
    if t.eval_cp > 0:
        return {"label1": "L'élan retombe", "text1": "Tu es toujours mieux, mais tu poussais plus fort il y a quelques coups.",
                "label2": "Réaction", "text2": "Relance une menace concrète pour ne pas laisser l'adversaire souffler."}
    return {"label1": "Tu remontes", "text1": "La position reste inconfortable, mais la tendance s'inverse en ta faveur.",
            "label2": "Suite", "text2": "Garde le rythme, l'adversaire perd du terrain coup après coup."}


def _initiative_tactical_2(t, c, wm, wd, board):
    if t.eval_cp > 0:
        return {"label1": "Ne temporise pas", "text1": "Ton avance s'érode -- l'initiative se perd si on la laisse dormir.",
                "label2": "Fonce", "text2": "Cherche le coup qui remet la pression tout de suite."}
    return {"label1": "Le vent tourne", "text1": "Tu étais dominé, mais la dynamique bascule vers toi.",
            "label2": "Suite", "text2": "Accentue cette bascule avant qu'il ne réalise ce qui se passe."}


def _initiative_classical_2(t, c, wm, wd, board):
    if t.eval_cp > 0:
        return {"label1": "Principe", "text1": "Un avantage se cultive activement -- laissé tel quel, il tend à fondre.",
                "label2": "Plan", "text2": "Fixe-toi un plan actif clair plutôt que d'attendre que la position parle."}
    return {"label1": "Principe", "text1": "La partie se rééquilibre progressivement à ton profit.",
            "label2": "Plan", "text2": "Poursuis avec des coups actifs, ne retombe pas dans la passivité trop tôt."}


# --- STRATEGIC_ADVANTAGE ---
def _strategic_popular_3(t, c, wm, wd, board):
    plan = _SIMPLIFICATION_ADVICE_TEXT.get(t.simplification_advice,
        "Améliore patiemment tes pièces, l'avantage ne s'en ira pas tout seul.")
    return {"label1": "Tu tiens la position", "text1": "Tu es clairement mieux, sans qu'un coup précis soit à trouver dans l'immédiat.",
            "label2": "Plan", "text2": plan}


def _strategic_tactical_3(t, c, wm, wd, board):
    if t.simplification_advice == "keep_tension":
        return {"label1": "Pression qui monte", "text1": "L'avantage n'est pas qu'une affaire de matériel : ta dynamique compte autant.",
                "label2": "Suite", "text2": "Garde les pièces sur l'échiquier et continue de resserrer l'étau."}
    return {"label1": "Pression qui monte", "text1": "L'avantage est là, prêt à se transformer en jeu concret.",
            "label2": "Suite", "text2": "Provoque une complication que l'adversaire aura du mal à tenir."}


def _strategic_classical_2(t, c, wm, wd, board):
    imbalance = _MATERIAL_IMBALANCE_TEXT.get(t.material_imbalance_kind)
    if imbalance:
        plan = imbalance.get(t.simplification_advice, imbalance["simplify"])
        return {"label1": "D'où vient l'avantage", "text1": imbalance["text1"], "label2": "Plan", "text2": plan}
    simplify_text = _SIMPLIFICATION_ADVICE_TEXT.get(t.simplification_advice)
    if simplify_text:
        return {"label1": "Avantage positionnel", "text1": "Ton avantage repose sur la qualité de tes pièces, pas sur le matériel.",
                "label2": "Plan", "text2": simplify_text}
    return {"label1": "Avantage positionnel", "text1": "L'avantage est structurel -- il tient à la position, pas au compte de matériel.",
            "label2": "Plan", "text2": "Renforce la coordination de tes pièces avant de chercher à forcer."}


# --- PAWN_STRUCTURE ---
def _pawn_structure_popular_2(t, c, wm, wd, board):
    kind = _PAWN_WEAKNESS_LABEL_FR.get(t.pawn_weakness_kind, "faiblesse de pion")
    sq = _sq(t.pawn_weakness_square)
    return {"label1": "Point d'appui", "text1": f"Le {kind} adverse en {sq} est une cible qui restera là un moment.",
            "label2": "Plan", "text2": "Installe une pièce dessus ou devant, sans forcer : la pression fera le travail."}


def _pawn_structure_tactical_2(t, c, wm, wd, board):
    kind = _PAWN_WEAKNESS_LABEL_FR.get(t.pawn_weakness_kind, "faiblesse de pion")
    sq = _sq(t.pawn_weakness_square)
    return {"label1": "Brèche structurelle", "text1": f"Ce {kind} en {sq} est le genre de défaut autour duquel une attaque se construit.",
            "label2": "Suite", "text2": "Oriente tes pièces vers cette zone et fais monter la pression."}


def _pawn_structure_classical_2(t, c, wm, wd, board):
    kind = _PAWN_WEAKNESS_LABEL_FR.get(t.pawn_weakness_kind, "faiblesse de pion")
    sq = _sq(t.pawn_weakness_square)
    return {"label1": "Cible durable", "text1": f"Le {kind} en {sq} est une faiblesse permanente -- un objectif de jeu positionnel typique.",
            "label2": "Plan", "text2": "Empêche d'abord qu'il soit réparé, puis attaque-le avec assez de pièces."}


# --- PIECE_ACTIVITY_GAP ---
def _piece_activity_popular_2(t, c, wm, wd, board):
    pct = round((t.activity_ratio - 1) * 100) if t.activity_ratio else None
    extra = f" (près de {pct}% de cases utiles en plus)" if pct else ""
    return {"label1": "Tes pièces respirent", "text1": f"Tes pièces sont bien plus actives que celles de l'adversaire{extra}, à matériel égal.",
            "label2": "Plan", "text2": "Sers-toi de cette liberté pour créer des menaces avant qu'il ne se coordonne."}


def _piece_activity_tactical_2(t, c, wm, wd, board):
    return {"label1": "Domination d'espace", "text1": "Tes pièces occupent le terrain, les siennes sont à l'étroit.",
            "label2": "Suite", "text2": "Transforme cette supériorité de mobilité en menace concrète avant qu'il ne se libère."}


def _piece_activity_classical_2(t, c, wm, wd, board):
    return {"label1": "Mobilité supérieure", "text1": "À matériel égal, tes pièces contrôlent plus de cases importantes que les siennes.",
            "label2": "Plan", "text2": "Restreins encore ses pièces avant de convertir cette activité en avantage durable."}


# --- KING_SAFETY_WARNING ---
def _king_safety_warning_popular_2(t, c, wm, wd, board):
    sq = _sq(t.king_safety_warning_square)
    if t.king_safety_warning_is_mine:
        return {"label1": "À surveiller", "text1": f"Ton roi en {sq} n'est pas encore à l'abri, et le centre commence à s'ouvrir.",
                "label2": "Plan", "text2": "Mets-le en sécurité maintenant, avant que ça ne devienne un vrai souci."}
    return {"label1": "Occasion à venir", "text1": f"Le roi adverse en {sq} tarde à se mettre à l'abri.",
            "label2": "Plan", "text2": "Garde cette faiblesse en tête et prépare-toi à en profiter plus tard."}


def _king_safety_warning_tactical_2(t, c, wm, wd, board):
    sq = _sq(t.king_safety_warning_square)
    if t.king_safety_warning_is_mine:
        return {"label1": "Alerte", "text1": f"Ton roi en {sq} reste au centre alors que le jeu s'ouvre -- terrain glissant.",
                "label2": "Suite", "text2": "Sécurise-le vite avant de te lancer dans quoi que ce soit d'ambitieux."}
    return {"label1": "Proie potentielle", "text1": f"Le roi adverse en {sq} n'est pas encore attaqué, mais la cible se dessine.",
            "label2": "Suite", "text2": "Amène tes pièces en position pour frapper dès que le centre craque."}


def _king_safety_warning_classical_2(t, c, wm, wd, board):
    sq = _sq(t.king_safety_warning_square)
    if t.king_safety_warning_is_mine:
        return {"label1": "Principe", "text1": f"La sécurité du roi prime : le tien en {sq} n'est pas encore roqué dans un centre qui s'ouvre.",
                "label2": "Plan", "text2": "Achève ta mise à l'abri avant d'entamer un plan plus large."}
    return {"label1": "Principe", "text1": f"Le roi adverse en {sq} néglige sa sécurité dans un centre instable.",
            "label2": "Plan", "text2": "Poursuis ton développement : ce retard risque de lui coûter cher plus tard."}


# --- EQUAL_POSITION ---
def _equal_popular_3(t, c, wm, wd, board):
    return {"label1": "À égalité", "text1": "Ni toi ni l'adversaire n'avez pris l'ascendant pour le moment.",
            "label2": "Approche", "text2": "Améliore ta pièce la moins bien placée, sans chercher à forcer le destin."}


def _equal_tactical_3(t, c, wm, wd, board):
    return {"label1": "Équilibre instable", "text1": "C'est nivelé, mais la position a du potentiel de déséquilibre.",
            "label2": "Suite", "text2": "Cherche le coup qui pose le plus de problèmes concrets à l'adversaire."}


def _equal_classical_2(t, c, wm, wd, board):
    return {"label1": "Partie équilibrée", "text1": "Rien ne tranche : c'est la structure de pions qui doit guider ton plan.",
            "label2": "Suite logique", "text2": "Repère la case ou la colonne faible adverse et construis ton jeu autour."}


TEMPLATES = {
    BLUNDER: {
        "popular": [_blunder_popular_1, _blunder_popular_2, _blunder_popular_3],
        "creative": [_blunder_tactical_1, _blunder_tactical_2, _blunder_tactical_3],
        "classical": [_blunder_classical_1, _blunder_classical_2],
    },
    TACTICAL: {
        "popular": [_tactical_popular_1, _tactical_popular_2, _tactical_popular_3],
        "creative": [_tactical_tactical_1, _tactical_tactical_2, _tactical_tactical_3],
        "classical": [_tactical_classical_1, _tactical_classical_2],
    },
    ATTACK: {
        "popular": [_attack_popular_1, _attack_popular_2],
        "creative": [_attack_tactical_1, _attack_tactical_2],
        "classical": [_attack_classical_1, _attack_classical_2],
    },
    DEFENSE: {
        "popular": [_defense_popular_1, _defense_popular_2],
        "creative": [_defense_tactical_1, _defense_tactical_2],
        "classical": [_defense_classical_1, _defense_classical_2],
    },
    MISSED_OPPORTUNITY: {
        "popular": [_missed_popular_1, _missed_popular_2],
        "creative": [_missed_tactical_1, _missed_tactical_2],
        "classical": [_missed_classical_1, _missed_classical_2],
    },
    ENDGAME: {
        "popular": [_endgame_popular_1, _endgame_popular_2],
        "creative": [_endgame_tactical_1, _endgame_tactical_2],
        "classical": [_endgame_classical_1, _endgame_classical_2],
    },
    OPENING: {
        "popular": [_opening_popular_1, _opening_popular_2],
        "creative": [_opening_tactical_1, _opening_tactical_2],
        "classical": [_opening_classical_1, _opening_classical_2],
    },
    INITIATIVE_SHIFT: {
        "popular": [_initiative_popular_1, _initiative_popular_2],
        "creative": [_initiative_tactical_1, _initiative_tactical_2],
        "classical": [_initiative_classical_1, _initiative_classical_2],
    },
    STRATEGIC_ADVANTAGE: {
        "popular": [_strategic_popular_1, _strategic_popular_2, _strategic_popular_3],
        "creative": [_strategic_tactical_1, _strategic_tactical_2, _strategic_tactical_3],
        "classical": [_strategic_classical_1, _strategic_classical_2],
    },
    PAWN_STRUCTURE: {
        "popular": [_pawn_structure_popular_1, _pawn_structure_popular_2],
        "creative": [_pawn_structure_tactical_1, _pawn_structure_tactical_2],
        "classical": [_pawn_structure_classical_1, _pawn_structure_classical_2],
    },
    PIECE_ACTIVITY_GAP: {
        "popular": [_piece_activity_popular_1, _piece_activity_popular_2],
        "creative": [_piece_activity_tactical_1, _piece_activity_tactical_2],
        "classical": [_piece_activity_classical_1, _piece_activity_classical_2],
    },
    KING_SAFETY_WARNING: {
        "popular": [_king_safety_warning_popular_1, _king_safety_warning_popular_2],
        "creative": [_king_safety_warning_tactical_1, _king_safety_warning_tactical_2],
        "classical": [_king_safety_warning_classical_1, _king_safety_warning_classical_2],
    },
    EQUAL_POSITION: {
        "popular": [_equal_popular_1, _equal_popular_2, _equal_popular_3],
        "creative": [_equal_tactical_1, _equal_tactical_2, _equal_tactical_3],
        "classical": [_equal_classical_1, _equal_classical_2],
    },
}


CAUTION_TEXT_FR = {
    "stalemate_risk": "Attention au pat : l'adversaire n'a presque plus de coups légaux -- vérifie que ton coup lui en laisse au moins un.",
}


def compute_scenario_facts(chosen, board, engine, compute_eval=True, depth=variation_narrator.DEFAULT_EVAL_DEPTH):
    """
    Partie COÛTEUSE du scénario (voir variation_narrator.analyze_variation :
    jusqu'à 1 + MAX_PLY évaluations moteur légères) -- volontairement
    séparée du rendu texte : ces faits ne dépendent QUE du coup joué et de
    sa PV, jamais du profil qui parle. On peut donc les calculer une seule
    fois et les partager entre 2 profils qui choisissent le même coup sur la
    même position (voir web_bridge.py, _scenario_cache) au lieu de refaire
    les évals 3 fois.

    board : position AVANT le coup choisi (chosen) -- même `board` que reçu
    par generate_narration. La ligne analysée COMMENCE par le coup recommandé
    lui-même (pv_uci[0] = chosen) : on passe donc `board` tel quel à
    analyze_variation, sans rien pousser, pour que le scénario s'ancre sur ce
    que FAIT la flèche (voir le corps ci-dessous).
    engine : instance ChessCoachEngine, nécessaire pour la trajectoire
    d'éval -- si None, motifs structurels seuls (aucun appel moteur).
    depth : profondeur de l'éval à chaque étape de la ligne (voir
    variation_narrator.DEFAULT_EVAL_DEPTH si non précisé). Comme ce calcul
    tourne maintenant de façon asynchrone après l'affichage de la flèche
    (voir web_bridge._attach_scenario_async), rien n'empêche plus de passer
    la depth du niveau Elo actif -- l'appelant décide (web_bridge.py passe
    tier.random_depth() ; les autres appelants gardent le repli par défaut).

    Retourne un VariationFacts, ou None si la ligne est trop courte pour en
    tirer quoi que ce soit (ex: coup de livre sans PV calculée au-delà).
    """
    pv_uci = chosen.get("pv_uci") or []
    # La ligne narrée COMMENCE par le coup recommandé (pv_uci[0] = chosen), pas
    # par la réponse adverse : une version précédente sautait le coup proposé
    # ("déjà visible via la flèche") et racontait UNIQUEMENT la suite théorique,
    # ce qui déconnectait le scénario du coup affiché (ex: la flèche prend une
    # pièce mais la "suite" parle d'autre chose). En incluant le coup choisi, le
    # motif structurel (échange, rupture, pression) s'ancre sur CE que fait la
    # flèche. Il faut au moins le coup + une réponse pour raconter une suite.
    if len(pv_uci) < 2:
        return None
    try:
        pv_moves = [chess.Move.from_uci(u) for u in pv_uci]
    except (ValueError, KeyError):
        return None
    # analyze_variation attend la position AVANT le 1er coup de la ligne, avec
    # pv_moves[0] = le coup choisi (voir sa docstring). On part donc du `board`
    # tel quel (position avant le coup recommandé), sans pousser quoi que ce soit.
    return variation_narrator.analyze_variation(
        engine, board.copy(), pv_moves, compute_eval=compute_eval and engine is not None,
        depth=depth,
    )


def render_scenario(facts, profile_id):
    """
    Partie CHEAP du scénario : choisit la formulation selon le profil qui
    parle (voir variation_narrator.narrate_variation) -- aucun appel moteur,
    juste un lookup de gabarit. None si aucun fait de scénario (voir
    compute_scenario_facts).
    """
    if facts is None:
        return None
    return variation_narrator.narrate_variation(facts, profile_id)


def _scenario_phrase(chosen, profile_id, board, engine, compute_eval=True, depth=variation_narrator.DEFAULT_EVAL_DEPTH):
    """
    Scénario complet (faits coûteux + rendu texte) en un appel -- conservé
    pour l'usage inline de generate_narration (include_scenario=True). Le
    chemin décomposé (compute_scenario_facts + render_scenario) est préféré
    par web_bridge.py pour pouvoir mutualiser/décorréler le coût moteur.
    """
    facts = compute_scenario_facts(chosen, board, engine, compute_eval, depth=depth)
    return render_scenario(facts, profile_id)


def _opening_identity_body(opening_match):
    """
    Construit le corps {"label1", "text1"} à partir d'un match de la base
    ECO locale (voir opening_identity.py) -- remplace ENTIÈREMENT les
    gabarits génériques _opening_xxx_1 pour cette position, quel que soit
    le profil qui parle (le nom + les points forts/faibles d'une ouverture
    sont des faits, pas une question de style). Le texte pros_cons est
    rédigé à la main (voir eco_openings.json), jamais généré.

    PAS de label2/text2 ici (contrairement aux autres gabarits) : le champ
    "suite" (scénario, voir _scenario_phrase/variation_narrator.py) couvre
    déjà "que faire ensuite" pour ce coup précis -- un label2 générique du
    style "Suite logique" ferait doublon avec lui dans l'UI (voir
    webview_ui.py, renderDetail) chaque fois qu'un scénario est disponible.
    Si aucun scénario n'est disponible pour ce coup (ex: coup de livre sans
    ligne calculée au-delà), le bloc affiche alors juste le nom + pros_cons,
    sans texte de remplissage creux à la place.
    """
    return {
        "label1": f"{opening_match['name']} ({opening_match['eco']})",
        "text1": opening_match["pros_cons"],
    }


def _opening_tag(opening_match):
    """
    Bannière {"eco", "family", "variation"} affichée en PLUS du thème
    principal (voir generate_narration) quand le thème affiché n'est PAS
    OPENING lui-même -- reste visible tant que la position est dans une
    ligne ECO connue (livre local, transposition comprise), même après la
    phase d'ouverture (7 coups) et même si un thème tactique/stratégique a
    pris le dessus sur l'affichage principal. `family` = nom de la famille
    d'ouverture (avant le ":"), `variation` = ce qui suit (None si le nom
    ne comporte pas de variation nommée, ex: "Réti Opening" seul).
    """
    name = opening_match["name"]
    if ":" in name:
        family, variation = name.split(":", 1)
        family, variation = family.strip(), variation.strip()
    else:
        family, variation = name.strip(), None
    return {"eco": opening_match["eco"], "family": family, "variation": variation}


def generate_narration(theme_result, profile_id, chosen, why_motif, why_detail, board,
                        move_history=None, opening_book=None, engine=None, compute_scenario_eval=True,
                        include_scenario=True, scenario_depth=variation_narrator.DEFAULT_EVAL_DEPTH):
    """
    Retourne un dict prêt à afficher :
    {"theme_label", "theme_icon", "label1", "text1", "label2", "text2",
    "suite", "caution"} --
    "suite" (optionnel) : le SCÉNARIO en langage humain de la suite
    envisagée par ce coup (voir _scenario_phrase / variation_narrator.py)
    -- ne reproduit jamais la liste des coups en SAN, raconte l'IDÉE
    (échange, rupture, pression sur le roi, repositionnement...) et la
    tendance d'éval de la ligne. Distinct du "pourquoi" déjà couvert par
    label2/text2.
    "caution" (optionnel) : avertissement transversal, indépendant du
    thème principal (ex: risque de pat en finale gagnante).

    move_history / opening_book : historique des coups joués (voir
    web_bridge.py, BridgeState._move_history) et instance
    opening_identity.OpeningIdentity. Si un match est trouvé dans la base
    ECO locale : pour le thème OPENING, son nom et son texte pros_cons
    remplacent entièrement les gabarits génériques _opening_xxx_1 (mêmes
    gabarits utilisés en fallback si aucun match, ex: partie déjà sortie de
    la théorie connue) ; pour tout AUTRE thème, il alimente juste la
    bannière "opening_tag" (voir plus bas) en plus du contenu principal.
    engine : instance ChessCoachEngine (voir engine_analysis.py), passée
    au scénario pour calculer sa trajectoire d'éval (voir
    variation_narrator.EVAL_DEPTH) -- si None, motifs structurels seuls.
    compute_scenario_eval : coupe-circuit pour désactiver l'éval de la
    ligne sans toucher au reste (ex: test de performance) -- True par
    défaut.
    include_scenario : si False, ne calcule PAS le "suite" ici (chemin
    rapide) -- la partie coûteuse du scénario (jusqu'à 1 + MAX_PLY évals
    moteur) est alors laissée à l'appelant, qui peut la mutualiser entre
    profils et/ou la différer pour afficher la flèche sans attendre (voir
    web_bridge.py, compute_scenario_facts / _scenario_cache). True par
    défaut pour ne rien changer aux appels existants (tests, autres modes).
    scenario_depth : profondeur transmise à compute_scenario_facts si
    include_scenario=True (voir variation_narrator.DEFAULT_EVAL_DEPTH).

    Tous ces paramètres sont optionnels (défaut None/True) pour ne rien
    casser des autres appels existants (thèmes autres que OPENING, tests).

    "opening_tag" (optionnel, présent uniquement si theme != OPENING) :
    {"eco", "family", "variation"} -- même source que le bloc OPENING
    (opening_book.identify(), voir plus haut) mais affiché comme bannière
    SUPPLÉMENTAIRE plutôt que de remplacer le contenu du thème principal.
    Permet de garder "tu es dans telle ouverture/variation" visible même
    quand la partie a dépassé la phase d'ouverture (7 coups) mais reste
    dans une ligne connue (livre polyglot et/ou base ECO), et même quand
    un autre thème (tactique, stratégique...) est celui affiché.
    """
    theme = theme_result.theme

    # Calculé une seule fois, réutilisé par les deux branches ci-dessous :
    # thème OPENING lui-même (bloc complet, voir _opening_identity_body) OU
    # tout autre thème (juste la bannière opening_tag, voir _opening_tag).
    opening_match = None
    if opening_book is not None and move_history:
        opening_match = opening_book.identify(move_history, fen=board.fen())

    if theme == OPENING and opening_match is not None:
        result = {
            "theme_label": THEME_LABELS_FR.get(theme, theme),
            "theme_icon": THEME_ICONS.get(theme, "info"),
            **_opening_identity_body(opening_match),
        }
        if include_scenario:
            suite = _scenario_phrase(chosen, profile_id, board, engine, compute_scenario_eval, scenario_depth)
            if suite:
                result["suite"] = suite
        if theme_result.caution:
            caution_text = CAUTION_TEXT_FR.get(theme_result.caution)
            if caution_text:
                result["caution"] = caution_text
        return result

    profile_templates = TEMPLATES.get(theme, TEMPLATES[EQUAL_POSITION])
    variants = profile_templates.get(profile_id, profile_templates["popular"])
    tpl_fn = _pick(variants, board, profile_id)
    body = tpl_fn(theme_result, chosen, why_motif, why_detail, board)
    result = {
        "theme_label": THEME_LABELS_FR.get(theme, theme),
        "theme_icon": THEME_ICONS.get(theme, "info"),
        **body,
    }
    if opening_match is not None:
        result["opening_tag"] = _opening_tag(opening_match)
    if include_scenario:
        suite = _scenario_phrase(chosen, profile_id, board, engine, compute_scenario_eval, scenario_depth)
        if suite:
            result["suite"] = suite
    if theme_result.caution:
        caution_text = CAUTION_TEXT_FR.get(theme_result.caution)
        if caution_text:
            result["caution"] = caution_text
    return result
