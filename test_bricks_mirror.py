"""
test_bricks_mirror.py
Test de FIDÉLITÉ : le collecteur collect_theme_bricks (étape 1) doit être un
MIROIR EXACT des conditions de detect_theme (comportement d'affichage
actuel). Invariant vérifié :

    detect_theme(...).theme  ==  la brique de collect_theme_bricks(...) qui
    arrive en PREMIER dans PRIORITY_ORDER.

Pourquoi c'est un vrai test anti-bug : detect_theme s'arrête au 1er match
(early-return dans l'ordre de PRIORITY_ORDER) ; collect_theme_bricks teste
tout sans s'arrêter. Si les deux utilisent bien les MÊMES conditions, alors
le thème "gagnant" de detect_theme est forcément la brique la plus
prioritaire parmi celles collectées. Toute divergence = une condition
recopiée de travers dans le collecteur.

Exécute des POSITIONS RÉELLES (chess.Board) croisées avec des candidats
fabriqués (cp, eval_loss, is_check...) et des signaux (swing_cp,
initiative_trend). Aucun moteur Stockfish requis. Nécessite python-chess.
"""
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import chess

import theme_detector as td


_failures = []


def check(cond, msg):
    if not cond:
        print(f"  FAIL  {msg}")
        _failures.append(msg)


def make_candidates(cp, second_eval_loss=0, is_check=False, is_capture=False, n=3):
    """
    Fabrique une liste de candidats minimale au format attendu par
    detect_theme / collect_theme_bricks (voir engine_analysis.analyze_candidates
    pour le format réel : seules ces clés sont lues par la détection).
    """
    top = {
        "cp": cp, "eval_loss": 0, "is_check": is_check, "is_capture": is_capture,
    }
    second = {
        "cp": (cp - second_eval_loss) if cp is not None else None,
        "eval_loss": second_eval_loss, "is_check": False, "is_capture": False,
    }
    rest = [dict(second) for _ in range(max(0, n - 2))]
    return [top, second] + rest


# Positions réelles couvrant plusieurs phases / structures / sécurités de roi.
FENS = {
    "start":            chess.STARTING_FEN,
    "after_e4":         "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
    "italian":          "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 0 1",
    "midgame_open":     "r2q1rk1/pp2bppp/2n1bn2/2pp4/3P4/2N1PN2/PPQ1BPPP/R1B2RK1 w - - 0 1",
    "endgame_KP":       "8/8/8/4k3/8/4K3/4P3/8 w - - 0 1",
    "endgame_passed":   "8/2P5/8/4k3/8/8/6K1/8 w - - 0 1",
    "opp_doubled":      "r1bqkbnr/pp1p1ppp/2n5/2p1p3/4P3/2P2N2/PP1P1PPP/RNBQKB1R w KQkq - 0 1",
    "king_uncastled":   "r1bqk2r/pppp1ppp/2n2n2/2b1p3/2B1P3/2NP1N2/PPP2PPP/R1BQK2R w KQkq - 0 1",
}

# Combinaisons de signaux : (cp du top candidat, swing_cp, initiative_trend, second_eval_loss, is_check, is_capture)
SIGNALS = [
    (0, None, None, 0, False, False),
    (300, None, None, 0, False, False),
    (-300, None, None, 0, False, False),
    (120, None, None, 150, True, False),     # gros gap + échec -> TACTICAL possible
    (120, None, None, 150, False, True),     # gros gap + capture -> TACTICAL possible
    (200, 250, None, 0, False, False),       # swing >= 150 -> BLUNDER
    (200, 90, None, 0, False, False),        # swing dans bande MISSED
    (150, None, 60, 0, False, False),        # initiative montante, en avantage -> perte d'initiative
    (-150, None, -60, 0, False, False),      # initiative, en désavantage
    (90, None, None, 0, False, False),       # avantage modéré -> STRATEGIC possible
    (None, None, None, 0, False, False),     # coup de livre (cp=None) -> neutre
]


def priority_first(bricks):
    """La brique qui arrive en 1er dans PRIORITY_ORDER (== ce que detect_theme retourne)."""
    order = {t: i for i, t in enumerate(td.PRIORITY_ORDER)}
    return min(bricks, key=lambda b: order.get(b.theme, 9999))


def main():
    n_cases = 0
    for name, fen in FENS.items():
        board = chess.Board(fen)
        for (cp, swing, init, sel, chk, cap) in SIGNALS:
            n_cases += 1
            cands = make_candidates(cp, second_eval_loss=sel, is_check=chk, is_capture=cap)

            # 1) Les deux chemins ne doivent jamais lever d'exception.
            try:
                res = td.detect_theme(board, cands, swing_cp=swing,
                                      opponent_better_move_san="Qh5", initiative_trend=init)
            except Exception as e:
                check(False, f"[{name}/{cp},{swing},{init}] detect_theme a levé {type(e).__name__}: {e}")
                continue
            try:
                bricks = td.collect_theme_bricks(board, cands, swing_cp=swing,
                                                 opponent_better_move_san="Qh5", initiative_trend=init)
            except Exception as e:
                check(False, f"[{name}/{cp},{swing},{init}] collect_theme_bricks a levé {type(e).__name__}: {e}")
                continue

            # 2) Le collecteur ne renvoie jamais une liste vide.
            check(len(bricks) >= 1, f"[{name}/{cp},{swing},{init}] au moins 1 brique")

            # 3) INVARIANT DE FIDÉLITÉ : detect_theme == brique la plus prioritaire.
            first = priority_first(bricks)
            check(res.theme == first.theme,
                  f"[{name}/cp={cp},swing={swing},init={init}] "
                  f"detect_theme={res.theme} MAIS brique prioritaire={first.theme} "
                  f"(toutes: {[b.theme for b in bricks]})")

            # 4) Chaque brique porte bien son tier + sa famille (posés par le collecteur).
            for b in bricks:
                check("_tier" in b.fields and "_family" in b.fields,
                      f"[{name}] brique {b.theme} sans _tier/_family")

    print(f"\n{n_cases} cas testés (positions x signaux)")
    if _failures:
        print(f"[ECHEC] {len(_failures)} probleme(s) detecte(s)")
        sys.exit(1)
    print("[OK] collect_theme_bricks est un miroir fidele de detect_theme")
    sys.exit(0)


if __name__ == "__main__":
    main()
