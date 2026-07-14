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

Ce module NE TOUCHE À RIEN en production : ni web_bridge.py, ni
webview_ui.py, ni generate_narration. Le vrai câblage (remplacer l'appel
generate_narration dans handle_single_profile, adapter le format d'affichage
de webview_ui.py qui attend aujourd'hui label1/text1/label2/text2 vers le
paragraphe unique de v2) est un CHANGEMENT DE CONTRAT D'AFFICHAGE à décider
et appliquer explicitement -- volontairement laissé hors de ce module pour
qu'il reste un diff petit et relisible.

CONTRAINTE ADN respectée de bout en bout : rien d'inventé (chaque fragment
ancré sur un champ réel de brique), hors-ligne, aucun appel moteur ajouté
(la détection de briques est du pur python-chess, comme detect_theme).
"""
from dataclasses import dataclass, field
from typing import List, Optional

import theme_detector as td
import theme_scoring as ts
import narration_weaver as nw
from fragment_library import FragmentContext


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
                    initiative_trend=None, max_supports=2, require_relation=False):
    """
    Étape position-level : détecte toutes les briques, les score, sélectionne
    1 principal + 0..2 secondaires. NE DÉPEND PAS du profil -> cacheable.

    Mêmes paramètres d'entrée que theme_detector.detect_theme (swing_cp,
    opponent_better_move_san, initiative_trend), pour un remplacement
    "iso-signaux" à l'étape de câblage.

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
    return nw.weave(selection.lead, selection.supports, profile_id, ctx, caution_text=caution_text)


def narrate(board, candidates, profile_id, swing_cp=None, opponent_better_move_san=None,
            initiative_trend=None, chosen=None, why_motif=None, why_detail=None,
            caution_text=None, require_relation=False):
    """
    Raccourci tout-en-un (sélection + tissage) -- pratique pour les tests et
    le chemin non caché. En production, PRÉFÉRER build_selection() une fois
    par position puis render() par profil, pour mutualiser la sélection
    entre les 3 profils (voir étape 6).
    """
    selection = build_selection(
        board, candidates, swing_cp=swing_cp,
        opponent_better_move_san=opponent_better_move_san, initiative_trend=initiative_trend,
        require_relation=require_relation,
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
