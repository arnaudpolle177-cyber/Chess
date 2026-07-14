"""
test_theme_scoring.py
Tests unitaires du module PUR theme_scoring (étape 2, narration v2).

Autonome et sans dépendance externe (pas de pytest requis) : lance-le
directement avec `python test_theme_scoring.py`. Sortie "OK" + code de
sortie 0 si tout passe, sinon la 1re assertion qui casse s'affiche avec un
message explicite et le code de sortie est 1.

⚠ Ce fichier importe theme_scoring, qui importe theme_detector, qui importe
human_profile -> chess. Il faut donc `pip install python-chess` pour le
lancer (voir la démarche d'installation). Les tests eux-mêmes n'utilisent
NI moteur NI board : uniquement des ThemeCandidate fabriqués à la main.
"""
import sys

# Console Windows souvent en codec GBK/cp1252 -> force UTF-8 pour la sortie
# (accents/emoji) sans dépendre de la locale du terminal.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from theme_detector import (
    ThemeCandidate, theme_tier, theme_family,
    TIER_WEIGHT,
    BLUNDER, TACTICAL, ATTACK, DEFENSE, MISSED_OPPORTUNITY,
    ENDGAME, OPENING, INITIATIVE_SHIFT, STRATEGIC_ADVANTAGE,
    PAWN_STRUCTURE, PIECE_ACTIVITY_GAP, KING_SAFETY_WARNING, EQUAL_POSITION,
    BLUNDER_THRESHOLD_CP, STRATEGIC_EVAL_CP,
)
import theme_scoring as ts


_failures = []


def check(cond, msg):
    if cond:
        print(f"  ok  {msg}")
    else:
        print(f"  FAIL  {msg}")
        _failures.append(msg)


def brick(theme, strength, **extra_fields):
    """
    Fabrique un ThemeCandidate comme le ferait collect_theme_bricks :
    fields contient _tier/_family + d'éventuels champs métier.
    """
    fields = dict(extra_fields)
    fields["_tier"] = theme_tier(theme)
    fields["_family"] = theme_family(theme)
    return ThemeCandidate(theme, strength, fields)


# ---------------------------------------------------------------------
def test_tier_dominates_intensity():
    """Un thème d'un tier supérieur bat TOUJOURS un tier inférieur, même à
    intensité brute écrasante pour le second."""
    strong = brick(DEFENSE, 100)              # tier strong, intensité minimale (au plancher)
    enrich = brick(PIECE_ACTIVITY_GAP, 999)   # tier enrichment, intensité saturée
    check(ts.score_brick(strong) > ts.score_brick(enrich),
          "tier strong (même faible) > tier enrichment (même saturé)")


def test_intensity_orders_within_tier():
    """À tier égal, une intensité brute plus forte donne un meilleur score."""
    small = brick(BLUNDER, BLUNDER_THRESHOLD_CP + 10)
    big = brick(BLUNDER, 800)
    check(ts.score_brick(big) > ts.score_brick(small),
          "gros blunder > petit blunder (même tier)")


def test_intensity_clamped_0_99():
    """L'intensité normalisée reste dans [0, 99] même hors bornes."""
    at_floor = brick(STRATEGIC_ADVANTAGE, STRATEGIC_EVAL_CP)        # pile au plancher -> ~0
    huge = brick(STRATEGIC_ADVANTAGE, 100000)                       # bien au-delà de la saturation
    s_floor = ts.score_brick(at_floor) - TIER_WEIGHT["medium"]
    s_huge = ts.score_brick(huge) - TIER_WEIGHT["medium"]
    check(-0.001 <= s_floor <= 1.0, f"intensité au plancher ~0 (obtenu {s_floor:.3f})")
    check(s_huge <= ts.INTENSITY_MAX + 0.001, f"intensité saturée <= 99 (obtenu {s_huge:.3f})")


def test_equal_position_is_lowest():
    """EQUAL_POSITION (filet neutre) a le score le plus bas possible."""
    eq = brick(EQUAL_POSITION, 0.0)
    other = brick(KING_SAFETY_WARNING, 1.0)
    check(ts.score_brick(eq) < ts.score_brick(other),
          "EQUAL_POSITION < n'importe quel enrichissement présent")


def test_lead_is_highest_score():
    """Le principal est bien la brique au plus haut score."""
    cands = [
        brick(PAWN_STRUCTURE, 1.0),
        brick(ATTACK, 250),          # strong -> doit gagner
        brick(STRATEGIC_ADVANTAGE, 200),
    ]
    lead, _ = ts.select_lead_and_support(cands)
    check(lead.theme == ATTACK, f"principal = ATTACK (obtenu {lead.theme})")


def test_supports_different_families_only():
    """Deux briques de la même famille ne coexistent pas dans la sélection."""
    # STRATEGIC_ADVANTAGE (famille advantage) principal ; on met 2
    # enrichissements de familles différentes + 1 même famille que... eux.
    cands = [
        brick(STRATEGIC_ADVANTAGE, 300),      # advantage (lead)
        brick(PAWN_STRUCTURE, 1.0),           # structure
        brick(PIECE_ACTIVITY_GAP, 2.0),       # activity
        brick(KING_SAFETY_WARNING, 1.0),      # king
    ]
    lead, supports = ts.select_lead_and_support(cands, max_supports=2)
    fams = [theme_family(s.theme) for s in supports]
    check(lead.theme == STRATEGIC_ADVANTAGE, f"lead = STRATEGIC (obtenu {lead.theme})")
    check(len(supports) == 2, f"exactement 2 secondaires retenus (obtenu {len(supports)})")
    check(len(set(fams)) == len(fams), f"familles des secondaires toutes distinctes (obtenu {fams})")
    check(theme_family(lead.theme) not in fams, "aucun secondaire de la même famille que le principal")


def test_same_family_as_lead_excluded():
    """Un enrichissement de la MÊME famille que le principal est écarté."""
    # ATTACK (famille king) principal ; KING_SAFETY_WARNING est aussi king
    # -> doit être exclu malgré un score suffisant.
    cands = [
        brick(ATTACK, 300),                   # king (lead)
        brick(KING_SAFETY_WARNING, 1.0),      # king -> même famille -> exclu
        brick(PAWN_STRUCTURE, 1.0),           # structure -> ok
    ]
    lead, supports = ts.select_lead_and_support(cands)
    themes = [s.theme for s in supports]
    check(KING_SAFETY_WARNING not in themes, "KING_SAFETY_WARNING (même famille que ATTACK) exclu")
    check(PAWN_STRUCTURE in themes, "PAWN_STRUCTURE (autre famille) retenu")


def test_zero_supports_is_valid():
    """Si aucune autre famille n'apporte, 0 secondaire est un résultat valide."""
    cands = [
        brick(ATTACK, 300),                   # king (lead)
        brick(KING_SAFETY_WARNING, 1.0),      # king -> même famille -> exclu, rien d'autre
    ]
    lead, supports = ts.select_lead_and_support(cands)
    check(lead.theme == ATTACK, "principal présent")
    check(supports == [], f"0 secondaire (obtenu {[s.theme for s in supports]})")


def test_support_floor_filters_noise():
    """Un secondaire sous le plancher de score est écarté comme bruit."""
    # EQUAL_POSITION a un score ~100 (tier enrichment, intensité 0) < FLOOR=120.
    cands = [
        brick(STRATEGIC_ADVANTAGE, 300),      # advantage (lead)
        brick(EQUAL_POSITION, 0.0),           # score ~100 -> sous le plancher
    ]
    lead, supports = ts.select_lead_and_support(cands)
    check(EQUAL_POSITION not in [s.theme for s in supports],
          "EQUAL_POSITION sous le plancher -> pas retenu comme secondaire")


def test_max_supports_respected():
    """On ne dépasse jamais max_supports même si plein de familles matchent."""
    cands = [
        brick(STRATEGIC_ADVANTAGE, 300),      # advantage (lead)
        brick(PAWN_STRUCTURE, 1.0),           # structure
        brick(PIECE_ACTIVITY_GAP, 2.0),       # activity
        brick(INITIATIVE_SHIFT, 100),         # dynamics
        brick(ENDGAME, 1.0),                  # phase
    ]
    _, supports = ts.select_lead_and_support(cands, max_supports=2)
    check(len(supports) <= 2, f"jamais plus de max_supports (obtenu {len(supports)})")


def test_relation_ok_hook():
    """Le crochet sémantique relation_ok peut écarter un secondaire structurellement valide."""
    cands = [
        brick(STRATEGIC_ADVANTAGE, 300),      # advantage (lead)
        brick(PAWN_STRUCTURE, 1.0),           # structure -> structurellement ok...
    ]
    # ...mais on refuse toute relation -> 0 secondaire.
    _, supports = ts.select_lead_and_support(cands, relation_ok=lambda a, b: False)
    check(supports == [], "relation_ok=False écarte le secondaire (crochet étape 4 fonctionnel)")


def test_empty_input():
    """Entrée vide -> (None, []) sans exception."""
    lead, supports = ts.select_lead_and_support([])
    check(lead is None and supports == [], "entrée vide gérée proprement")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    print(f"theme_scoring : {len(tests)} tests\n")
    for t in tests:
        print(f"[{t.__name__}]")
        t()
    print()
    if _failures:
        print(f"[ECHEC] {len(_failures)} assertion(s) en echec")
        sys.exit(1)
    print("[OK] tous les tests passent")
    sys.exit(0)


if __name__ == "__main__":
    main()
