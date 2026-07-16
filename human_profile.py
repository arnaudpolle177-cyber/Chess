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
  lieu d'être fixé par la boucle des 3 flèches.
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
        les 3 profils -- donc les 3 flèches d'un même coup partagent
        toujours la même profondeur, seul le coup SUIVANT en tire une autre.
        """
        rng = rng or random
        return rng.randint(self.depth_min, self.depth_max)


ELO_TIERS = {
    1: EloTier(id=1, label="1800-2200", elo_min=1800, elo_max=2200, elo_reference=2000,
           multipv=4, depth_min=13, depth_max=15,
           max_eval_loss_cp=90, typical_eval_loss_cp=35),
2: EloTier(id=2, label="2300-2700", elo_min=2300, elo_max=2700, elo_reference=2500,
           multipv=5, depth_min=17, depth_max=20,
           max_eval_loss_cp=50, typical_eval_loss_cp=18),
3: EloTier(id=3, label="2800-3200", elo_min=2800, elo_max=3200, elo_reference=3000,
           multipv=5, depth_min=21, depth_max=24,
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
INACCURACY_CHANCE = {1: 0.40, 2: 0.30, 3: 0.15}

# RÉGLAGE PRÉCISION (2026-07-16, suite) : simulation à l'appui (voir la
# conversation), le 0.30 partagé ci-dessus produisait ~14 coups
# "inaccuracy" sur 49 pour popular -- beaucoup trop (cible : 2-3 sur 40).
# Override scopé UNIQUEMENT popular + tier 2 (ne touche pas
# INACCURACY_CHANCE, donc creative/classical et les 2 autres tiers gardent
# exactement le comportement précédent). Calibré par simulation pour viser
# un taux réel post-conversion (le tirage ne débouche pas toujours sur un
# coup classé "inaccuracy", voir _score_popular) proche de 2-3/40 coups.
POPULAR_TIER2_INACCURACY_CHANCE = 0.10

# RÉGLAGE PRÉCISION (2026-07-16, suite) : à l'autre bout du spectre --
# 1-2 coups "brillants" par partie (façon chess.com : LE coup objectivement
# meilleur, ET un sacrifice -- pas juste précis, contre-intuitif). Scopé
# popular+tier2 comme le reste de ce chantier. Contrairement à
# l'inexactitude, il n'y a pas de "conversion garantie" : ça ne peut se
# déclencher QUE si la position offre effectivement un sacrifice
# objectivement meilleur (voir _is_sacrifice_candidate) -- comme pour un
# vrai joueur, un coup brillant ne se force pas, il se présente ou non.
POPULAR_TIER2_BRILLIANT_CHANCE = 0.15


def _is_sacrifice_candidate(c):
    """
    Signature "sacrifice" -- même heuristique que _score_creative (pièce de
    plus grande valeur qui en prend une de moindre valeur) : donne du
    matériel pour une compensation qui n'est pas immédiatement évidente.
    Pas une vraie détection tactique (on n'a pas l'info "cette pièce est-
    elle reprise tout de suite"), mais un proxy raisonnable vu les données
    déjà disponibles sur chaque candidat.
    """
    if not c.get("is_capture"):
        return False
    cv, mv = c.get("captured_piece_value"), c.get("moving_piece_value")
    return cv is not None and mv is not None and mv > cv


def _apply_inaccuracy_roll(tier, elo_tier_id, rng, chance=None):
    """
    Tire au sort si CE coup est une "petite inexactitude" (voir
    INACCURACY_CHANCE, ou `chance` si fourni pour un override scopé --
    voir POPULAR_TIER2_INACCURACY_CHANCE). Si oui, retourne une version du
    niveau dont la cible de perte typique est poussée près du plafond ET
    dont le plafond lui-même (max_eval_loss_cp) est relevé pour CE coup
    précis -- de quoi laisser entrer dans la fenêtre un coup nettement
    moins bon qu'à l'accoutumée (une vraie imprécision au sens chess.com,
    ~50-80cp), sans jamais aller jusqu'au blunder.

    RÉGLAGE PRÉCISION (2026-07-16) : sans relèvement du plafond, une
    "imprécision" restait bornée à max_eval_loss_cp du tier -- au tier 2
    (max 50cp) elle atteignait à peine le seuil chess.com d'imprécision, au
    tier 3 (max 20cp) c'était mathématiquement impossible. On relève donc le
    plafond de 60% (facteur 1.6) le temps de ce seul coup : la fenêtre
    normale du niveau (donc la force réelle) est intacte sur tous les autres
    coups, seul ce coup "à passage à vide" peut piocher plus bas. 1.6×50 =
    80cp au tier 2 : une franche imprécision, jamais une gaffe (>150-200cp).
    """
    if chance is None:
        chance = INACCURACY_CHANCE.get(elo_tier_id, 0.2)
    if rng.random() >= chance:
        return tier, False
    boosted_max = round(tier.max_eval_loss_cp * 1.6)
    boosted_typical = round(boosted_max * 0.85)
    if boosted_typical <= tier.typical_eval_loss_cp:
        return tier, False  # rien à pousser de plus (cas dégénéré)
    return replace(tier, max_eval_loss_cp=boosted_max, typical_eval_loss_cp=boosted_typical), True


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



# Au-delà de ce nombre de demi-coups RÉELLEMENT joués (voir ply_count dans
# _game_phase), on ne considère plus qu'on est en "opening" même si le
# matériel reste élevé -- 14 demi-coups = 7 coups pleins, seuil choisi
# d'après l'usage réel (voir la conversation : "en vrai c'est souvent max
# 7 coups").
OPENING_MAX_PLY = 14


def _material_count(board):
    """Total des points de matériel sur l'échiquier (hors rois), les 2 camps confondus."""
    total = 0
    for piece_type, value in ((chess.PAWN, 1), (chess.KNIGHT, 3), (chess.BISHOP, 3), (chess.ROOK, 5), (chess.QUEEN, 9)):
        total += len(board.pieces(piece_type, chess.WHITE)) * value
        total += len(board.pieces(piece_type, chess.BLACK)) * value
    return total


def _game_phase(board, ply_count=None):
    """
    "opening" / "middlegame" / "endgame".

    Basé sur le matériel restant sur l'échiquier (pas du numéro de coup du
    FEN -- pas fiable ici, voir chess_coach_bridge.user.js qui reconstruit
    toujours un FEN avec compteurs à "0 1"). Départ = 78 points de matériel
    (hors rois).

    ply_count (optionnel) : nombre de demi-coups RÉELLEMENT joués depuis le
    début de la partie, déduit côté serveur par comparaison de FEN successifs
    (voir web_bridge.py, BridgeState._move_history) -- SOURCE FIABLE,
    contrairement au numéro de coup du FEN lui-même. Ajoute une 2e condition
    à la phase "opening" : au-delà de OPENING_MAX_PLY demi-coups, on ne
    reste plus en "opening" même si le matériel est encore élevé (partie
    calme, peu d'échanges) -- sans ça, une position du 18e coup avec juste
    une pièce mineure échangée restait signalée "ouverture" en boucle (bug
    observé en pratique : le coach donnait encore des conseils de
    développement en plein milieu de partie tactique). None (par défaut) =
    comportement au matériel seul, pour les appelants qui n'ont pas accès à
    cet historique.
    """
    if board is None:
        return "middlegame"
    material = _material_count(board)
    if material >= 60 and (ply_count is None or ply_count < OPENING_MAX_PLY):
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


def _score_popular(c, tier, is_inaccuracy=False):
    """
    Bleu -- PRAGMATIQUE : maximise les CHANCES DE GAIN PRATIQUES (WDL --
    Win/Draw/Loss), pas juste l'éval brute en centipawns, avec une cible de
    perte proche de la perte "typique" du niveau -- c'est le profil "sûr et
    efficace" : il absorbe ce que faisait avant le profil "solide" (retiré,
    jugé redondant), tout en gardant son signal principal propre (WDL, pas
    juste "perte d'éval minimale"). Pénalise en plus les captures qui
    perdent de l'éval (complication inutile, peu pragmatique).

    RÉGLAGE PRÉCISION (2026-07-16) : avant, ce profil jouait ~96-98% de
    précision (chess.com) -- trop "moteur" pour un humain. Trois causes,
    toutes corrigées ici :
    1. cible à 0.55×typical -> le profil visait PLUS précis que son propre
       niveau. Remontée à 0.9×typical : il joue enfin autour de la perte
       typique du tier, pas en dessous.
    2. bonus WDL à *30 -> écrasait la bande cible et ramenait quasi toujours
       le meilleur coup pratique (donc un top move). Réduit à *12 : départage
       encore les coups proches, sans dicter le choix.
    3. is_inaccuracy (voir _apply_inaccuracy_roll) : sur un coup tiré comme
       "inexact", on vise la perte typique DÉJÀ poussée vers le plafond par le
       roll ET on coupe le WDL -- la bande tire alors vraiment vers un coup
       nettement moins bon (une vraie imprécision), au lieu de rester bridée
       près de l'optimal. C'est ce qui manquait pour produire les ~2
       imprécisions/partie d'un vrai joueur.
    """
    # RÉGLAGE PRÉCISION (2026-07-16, suite) : cible normale (hors tirage)
    # abaissée au tier 2 -- 0.9x plaçait systématiquement la cible en
    # pleine zone "good" (16.2cp), ce qui concentrait mécaniquement la
    # distribution là plutôt que sur best/excellent. Le cap dur (voir
    # select_move) empêchant déjà toute dérive incontrôlée vers
    # l'imprécision, on peut se permettre de viser plus près de l'optimal
    # sans revenir au problème initial (le WDL, lui, reste à *12, pas de
    # retour au quasi-toujours-top-move). Le tirage explicite (is_inaccuracy)
    # garde 0.9x sur le tier déjà boosté -- inchangé, sinon le tirage
    # perdrait sa capacité à produire une vraie imprécision.
    target_mult = 0.9 if (is_inaccuracy or tier.id != 2) else 0.28
    target = tier.typical_eval_loss_cp * target_mult
    bonus = 0
    if not is_inaccuracy and c.get("win_prob") is not None:
        # Échelle empirique : +12 points pour un coup à 100% de chances de
        # gain plutôt que 0% -- assez pour départager des coups très proches
        # en éval sans écraser la bande cible (donc sans forcer le top move).
        # Coupé sur un coup "inexact" : on veut justement laisser passer un
        # coup moins sûr ce coup-là.
        bonus += c["win_prob"] * 12
    if c["is_capture"] and c["eval_loss"] > tier.typical_eval_loss_cp:
        bonus -= 12  # capture qui perd de l'éval = complication risquée, pas pragmatique
    # RÉGLAGE PRÉCISION (2026-07-16, suite) : bande resserrée au tier 2
    # (0.75 -> 1.3) -- simulation à l'appui, avec 0.75 le bruit du softmax
    # (humanity) suffisait à lui seul à faire sortir ~16% des coups
    # "normaux" (is_inaccuracy=False) dans la zone imprécision, juste
    # parce que les écarts entre candidats sont parfois grands dans les
    # positions tendues. Une bande plus raide concentre le choix autour de
    # la cible sans toucher au tirage explicite (seule source voulue
    # d'imprécision) ni à humanity/INACCURACY_CHANCE (partagés avec
    # creative/classical et les autres tiers). Autres tiers/profils : bande
    # inchangée (0.75).
    factor = 1.2 if tier.id == 2 else 0.75
    return bonus + _band_penalty(c["eval_loss"], target, factor)


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
    # RÉGLAGE PRÉCISION (2026-07-16, suite) : override de chance scopé
    # popular+tier2 (voir POPULAR_TIER2_INACCURACY_CHANCE) -- None pour
    # tout le reste, donc comportement INACCURACY_CHANCE inchangé ailleurs.
    inaccuracy_chance = POPULAR_TIER2_INACCURACY_CHANCE if (profile_id == "popular" and elo_tier_id == 2) else None
    tier, is_inaccuracy = _apply_inaccuracy_roll(tier, elo_tier_id, rng, chance=inaccuracy_chance)
    scoring_tier = tier
    # RÉGLAGE PRÉCISION (2026-07-16, suite) : cap d'éligibilité DUR, scopé
    # popular+tier2, appliqué uniquement HORS tirage explicite. Plutôt que
    # de compter sur la bande/le softmax pour décourager les gros écarts
    # (ce qui laissait filtrer ~50% des "inaccuracy" par pur bruit, voir
    # simulation), on interdit structurellement à un coup normal de
    # dépasser ~1.5x la cible -- un coup au-delà de ça ne peut plus sortir
    # QUE via le tirage explicite (qui, lui, élargit tier.max_eval_loss_cp
    # à 80cp -- voir _apply_inaccuracy_roll). Ne change rien pour
    # creative/classical ni les autres tiers (scoring_tier reste `tier`
    # partout ailleurs).
    if profile_id == "popular" and elo_tier_id == 2 and not is_inaccuracy:
        hard_cap = round(tier.typical_eval_loss_cp * 0.9 * 1.5)  # ~24cp au tier 2
        scoring_tier = replace(tier, max_eval_loss_cp=min(tier.max_eval_loss_cp, hard_cap))
    eligible = _eligible_candidates(candidates, scoring_tier)
    # Bruit softmax réduit, même scope (popular+tier2) : avec la bande
    # resserrée de _score_popular, ça finit d'éliminer les "inaccuracy"
    # accidentelles issues du seul tirage humanity (voir simulation) --
    # laisse le tirage explicite ci-dessus comme SEULE source
    # d'imprécision voulue. humanity global inchangé pour les autres
    # profils/tiers (web_bridge.py continue de leur passer la même valeur).
    effective_humanity = humanity * 0.6 if (profile_id == "popular" and elo_tier_id == 2) else humanity

    # RÉGLAGE PRÉCISION (2026-07-16, suite) : coup "brillant" -- ne
    # s'applique jamais en même temps que l'inexactitude (les deux visent
    # des choses contradictoires). Cherche parmi les candidats OBJECTIFS
    # (pas seulement `eligible`, mais ça revient au même ici puisque
    # eval_loss=0 passe toujours n'importe quel cap) celui à eval_loss nul
    # qui a une signature de sacrifice. S'il y en a un et que le tirage
    # passe, on le joue directement -- pas de softmax ici, un coup brillant
    # ne se dilue pas avec humanity comme un choix de style ordinaire.
    brilliant_candidate = None
    if profile_id == "popular" and elo_tier_id == 2 and not is_inaccuracy:
        if rng.random() < POPULAR_TIER2_BRILLIANT_CHANCE:
            brilliant_candidate = next(
                (c for c in candidates if c["eval_loss"] <= 0.01 and _is_sacrifice_candidate(c)),
                None,
            )

    if brilliant_candidate is not None:
        chosen = dict(brilliant_candidate)
        chosen["is_inaccuracy"] = False
        chosen["is_brilliant"] = True
        return chosen

    if profile_id == "popular":
        scored = [(c, _score_popular(c, tier, is_inaccuracy)) for c in eligible]
    elif profile_id == "creative":
        scored = [(c, _score_creative(c, tier)) for c in eligible]
    elif profile_id == "classical":
        phase = _game_phase(board)
        # "En avantage" : le meilleur candidat objectif (candidates[0],
        # toujours trié meilleur -> moins bon) a une éval nettement positive
        # pour le camp au trait -- sert au profil classique en finale
        # (simplifier pour convertir un avantage, voir _score_classical).
        # cp peut être None pour un coup issu du livre d'ouvertures (voir
        # opening_book.py -- pas de vraie éval Stockfish en mode livre,
        # c'est volontaire). .get("cp", 0) ne protège QUE contre une clé
        # absente, pas contre une valeur None déjà présente -- sans ce
        # filet explicite, None >= 150 plantait le serveur pour CHAQUE
        # coup de livre côté profil "classique" (même bug déjà corrigé
        # dans theme_detector.py, jamais reporté ici).
        top_cp = candidates[0].get("cp")
        is_ahead = top_cp is not None and top_cp >= 150
        scored = [(c, _score_classical(c, tier, phase, is_ahead)) for c in eligible]
    else:
        raise ValueError(f"Profil de jeu inconnu : {profile_id!r}")

    chosen = _softmax_pick(scored, effective_humanity, rng)
    if chosen is not None:
        chosen = dict(chosen)
        chosen["is_inaccuracy"] = is_inaccuracy
        chosen["is_brilliant"] = False
    return chosen
