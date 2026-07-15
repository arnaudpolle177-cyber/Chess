"""
narration_v2.py
Étape 5 (partie NON invasive) du pipeline narration v2 -- L'ORCHESTRATEUR.

Assemble les 4 briques déjà construites et testées en un pipeline unique,
SANS toucher aux fichiers de production :

    collect_theme_bricks (theme_detector, étape 1)
        -> rank + select_lead_and_support (theme_scoring, étape 2)
            -> fragments_for (fragment_library, étape 3)
                -> weave (narration_weaver, étape 4)

Séparation clé (pour l'étape 6, cache) :
  - build_selection() ne dépend PAS du profil -> se calcule UNE fois par
    position et se met en cache (comme _theme_cache aujourd'hui, mais on
    cache la SÉLECTION principal+secondaires au lieu d'un seul thème).
  - render() dépend du profil -> se rejoue pour chacun des 3 profils, à
    partir de la même sélection cachée. Seul le tissage varie.

CÂBLÉ en production (2026-07-14) : web_bridge.handle_single_profile ajoute
entry["narration"]["paragraph"] = render(...)["text"] à côté de la façade v1
(generate_narration reste calculée en repli), et webview_ui.renderDetail
affiche ce paragraphe s'il est présent (Option B rétro-compat : sinon
fallback label1/text1/label2/text2). La SÉLECTION est cachée par position
(SelectionCache ci-dessous), seul render() se rejoue par profil. Retirer le
champ paragraph suffit à revenir intégralement à la narration v1.

CONTRAINTE ADN respectée de bout en bout : rien d'inventé (chaque fragment
ancré sur un champ réel de brique), hors-ligne, aucun appel moteur ajouté
(la détection de briques est du pur python-chess, comme detect_theme).
"""
from dataclasses import dataclass, field
from typing import List, Optional

import chess

import theme_detector as td
import theme_scoring as ts
import narration_weaver as nw
import move_intent as mi
from fragment_library import FragmentContext

# Champs de brique portant une CASE-CLÉ de position (voir ThemeResult) : c'est
# sur ces cases que se joue la "porte de cohérence" (le coup touche-t-il la
# zone du thème ?). Une brique peut n'en porter aucune (thème diffus comme
# STRATEGIC_ADVANTAGE) -> pas de case, la porte tranche alors sur la seule
# proximité au roi le cas échéant, sinon abandonne le thème.
_THEME_KEY_SQUARE_FIELDS = (
    "pawn_weakness_square",     # PAWN_STRUCTURE : le pion faible ciblé
    "king_square",              # ATTACK / DEFENSE : le roi concerné
    "king_safety_warning_square",  # KING_SAFETY_WARNING
    "passed_pawn_square",       # ENDGAME : le pion passé
)


def _theme_key_squares(candidate):
    """Cases-clés réelles portées par la brique (voir _THEME_KEY_SQUARE_FIELDS)."""
    if candidate is None or not candidate.fields:
        return []
    out = []
    for name in _THEME_KEY_SQUARE_FIELDS:
        sq = candidate.fields.get(name)
        if sq is not None:
            out.append(sq)
    return out


def _intent_is_coherent_with_theme(intent, theme_candidate):
    """
    Porte de COHÉRENCE (géométrique, pas par famille) : le coup recommandé
    touche-t-il la zone du thème de position ? Si oui, le thème garde du sens
    en secondaire (ex: "la dame reprend en e5, la faiblesse que tu ciblais") ;
    sinon il est hors-sujet et l'appelant l'abandonne (ex: fuir un échec n'a
    aucun rapport avec un pion isolé à l'autre bout).

    Vrai si la case d'arrivée OU de départ du coup est SUR une case-clé du
    thème, ou à distance <= 1 d'elle (le coup agit dans la zone). Un thème sans
    case-clé (avantage diffus) est jugé NON cohérent avec un coup forçant : on
    préfère un commentaire net sur le coup à un rappel positionnel vague.
    """
    if intent is None or theme_candidate is None:
        return False
    key_squares = _theme_key_squares(theme_candidate)
    if not key_squares:
        return False
    move_squares = [s for s in (intent.to_square, intent.from_square) if s is not None]
    for ks in key_squares:
        for ms in move_squares:
            if chess.square_distance(ks, ms) <= 1:
                return True
    return False


@dataclass
class Selection:
    """
    Résultat de la sélection PROFIL-INDÉPENDANT pour une position -- ce qui
    se met en cache (voir étape 6). Contient de quoi tisser n'importe quelle
    voix ensuite, sans re-détecter ni re-scorer.

    lead     : ThemeCandidate principal (jamais None tant que collect_theme_bricks
               a tourné -- il garantit au moins EQUAL_POSITION).
    supports : 0 à 2 ThemeCandidate secondaires déjà filtrés.
    eval_cp  : éval de la position du point de vue de mon camp (candidates[0].cp)
               -- profil-indépendant, nécessaire au tissage (sens de
               l'initiative, ampleur de l'avantage). 0 si indisponible.
    bricks   : toutes les briques collectées (debug / introspection ; pas
               requis par render()).
    """
    lead: Optional[td.ThemeCandidate]
    supports: List[td.ThemeCandidate]
    eval_cp: int = 0
    bricks: List[td.ThemeCandidate] = field(default_factory=list)


def build_selection(board, candidates, swing_cp=None, opponent_better_move_san=None,
                    initiative_trend=None, move_history=None, max_supports=2,
                    require_relation=False):
    """
    Étape position-level : détecte toutes les briques, les score, sélectionne
    1 principal + 0..2 secondaires. NE DÉPEND PAS du profil -> cacheable.

    Mêmes paramètres d'entrée que theme_detector.detect_theme (swing_cp,
    opponent_better_move_san, initiative_trend), pour un remplacement
    "iso-signaux" à l'étape de câblage.

    move_history : liste des coups SAN réellement joués (voir web_bridge.py,
        BridgeState._move_history) -- transmise telle quelle à
        collect_theme_bricks pour en déduire ply_count (source FIABLE du
        nombre de demi-coups, contrairement au compteur du FEN). Indispensable
        pour que le plafond de phase "opening" (voir human_profile._game_phase,
        OPENING_MAX_PLY) s'applique AUSSI dans le pipeline v2 -- sans elle, une
        partie calme du milieu de jeu resterait signalée "Ouverture". None =
        détection de phase au matériel seul (comme les appelants sans historique).

    require_relation : si True, un secondaire n'est retenu que s'il a une
        RELATION sémantique listée avec le principal (voir
        narration_weaver.relation_is_useful) -- application stricte de la
        "consigne finale" (mieux vaut 0 secondaire qu'un secondaire de
        remplissage). Si False (défaut), on garde le filtre STRUCTUREL seul
        (famille différente + plancher de score) et les secondaires sans
        relation forte apparaissent avec un connecteur neutre ("Par
        ailleurs"). C'est un curseur de style à régler ensemble -- laissé
        souple, pas figé.

    Retourne une Selection (lead jamais None en pratique).
    """
    eval_cp = 0
    if candidates:
        top_cp = candidates[0].get("cp")
        eval_cp = top_cp if top_cp is not None else 0

    bricks = td.collect_theme_bricks(
        board, candidates, swing_cp=swing_cp,
        opponent_better_move_san=opponent_better_move_san, initiative_trend=initiative_trend,
        move_history=move_history,
    )
    relation_ok = nw.relation_is_useful if require_relation else None
    lead, supports = ts.select_lead_and_support(
        bricks, max_supports=max_supports, relation_ok=relation_ok,
    )
    return Selection(lead=lead, supports=supports, eval_cp=eval_cp, bricks=bricks)


def render(selection, profile_id, chosen=None, why_motif=None, why_detail=None,
           board=None, caution_text=None):
    """
    Étape profil-level : tisse le paragraphe final pour un profil donné, à
    partir d'une Selection déjà calculée (voir build_selection). C'est la
    seule partie qui se rejoue par profil.

    profile_id : "popular" / "creative" / "classical".
    chosen / why_motif / why_detail : contexte du coup joué (voir
        why_detector.py) -- alimente les fragments qui citent le motif
        tactique concret (jamais inventé, None accepté).
    board : position actuelle (pour nommer une pièce sur une case, rare).
    caution_text : avertissement transversal DÉJÀ rendu en texte (ex: risque
        de pat) -- renvoyé à part, jamais tissé (voir narration_weaver.weave).

    Retourne le dict de narration_weaver.weave :
      {"text", "lead", "supports", "voice", "caution"}.
    Si la sélection est vide (ne devrait pas arriver), text="".
    """
    if selection is None or selection.lead is None:
        return {"text": "", "lead": None, "supports": [], "voice": profile_id, "caution": caution_text}
    ctx = FragmentContext(
        board=board, chosen=chosen, why_motif=why_motif, why_detail=why_detail,
        eval_cp=selection.eval_cp,
    )

    # Intention du COUP recommandé (voir move_intent) : quand le coup est
    # FORÇANT (fuite d'échec, prise nette, sacrifice, échec, promotion), il
    # DOIT primer sur le thème de position -- sinon on affiche "pion isolé"
    # alors que la flèche prend une pièce ou sauve le roi (bug observé). Le
    # thème de position n'est gardé en secondaire QUE s'il est cohérent avec le
    # coup (porte géométrique : le coup touche la zone du thème) -- ta consigne
    # "garder le thème positionnel quand il est cohérent avec une prise".
    # Calculé par profil (chosen diffère selon le profil) -> différencie enfin
    # les 3 profils et débloque le figement du thème de position.
    intent = None
    if board is not None and chosen is not None:
        try:
            intent = mi.detect_move_intent(board, chosen, why_motif, why_detail)
        except Exception:
            intent = None  # best-effort : jamais bloquant, on retombe sur le thème

    if intent is not None and intent.forcing:
        kept_theme = selection.lead if _intent_is_coherent_with_theme(intent, selection.lead) else None
        woven = nw.weave_intent(intent, kept_theme, profile_id, ctx, caution_text=caution_text)
        if woven.get("text"):
            return woven
        # Repli : si l'intention n'a produit aucun texte (kind sans fragment),
        # on retombe proprement sur le tissage de thème habituel ci-dessous.

    return nw.weave(selection.lead, selection.supports, profile_id, ctx, caution_text=caution_text)


def narrate(board, candidates, profile_id, swing_cp=None, opponent_better_move_san=None,
            initiative_trend=None, move_history=None, chosen=None, why_motif=None,
            why_detail=None, caution_text=None, require_relation=False):
    """
    Raccourci tout-en-un (sélection + tissage) -- pratique pour les tests et
    le chemin non caché. En production, PRÉFÉRER build_selection() une fois
    par position puis render() par profil, pour mutualiser la sélection
    entre les 3 profils (voir étape 6).
    """
    selection = build_selection(
        board, candidates, swing_cp=swing_cp,
        opponent_better_move_san=opponent_better_move_san, initiative_trend=initiative_trend,
        move_history=move_history, require_relation=require_relation,
    )
    return render(selection, profile_id, chosen=chosen, why_motif=why_motif,
                  why_detail=why_detail, board=board, caution_text=caution_text)


class SelectionCache:
    """
    Étape 6 (partie NON invasive) : cache de SÉLECTION par position, prêt à
    remplacer le couple _theme_cache_key / _theme_cache_value de
    web_bridge.py (qui cache aujourd'hui UN ThemeResult -- ici on cache la
    sélection principal+secondaires, profil-indépendante).

    Même invariant de cohérence thread que l'actuel _theme_cache (voir
    web_bridge.py, _update_eval_tracking_and_theme) : la VALEUR est écrite
    AVANT la CLÉ, pour qu'un lecteur concurrent voie soit l'ancienne paire
    cohérente, soit la nouvelle -- jamais une clé neuve pointant une valeur
    périmée. La protection réelle par lock reste à la charge de l'appelant
    (web_bridge tient déjà self.lock / engine_lock aux bons endroits) : ce
    cache ne fait pas de verrouillage lui-même, il se contente d'un ordre
    d'écriture sûr, exactement comme le code existant.

    Usage prévu (étape de câblage) :
        cache = SelectionCache()
        # une fois par position (cache miss) :
        sel = build_selection(board, candidates, swing_cp=..., ...)
        cache.set(fen, sel)
        # pour chacun des 3 profils :
        sel = cache.get(fen)  # None si périmé -> l'appelant recalcule
        entry = render(sel, profile_id, chosen=..., ...)
    """
    def __init__(self):
        self._key = None    # fen
        self._value = None  # Selection

    def get(self, fen):
        """Selection cachée pour cette fen, ou None si absente/périmée."""
        return self._value if self._key == fen else None

    def set(self, fen, selection):
        """Écrit valeur puis clé (ordre sûr, voir docstring de classe)."""
        self._value = selection
        self._key = fen

    def invalidate(self):
        """Force un recalcul au prochain accès (ex: Rafraîchir / nouvelle partie)."""
        self._key = None
        self._value = None
