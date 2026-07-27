"""
test_game_report.py
Tests de game_report.py : formule d'accuracy, classification par coup,
agrégation par camp. Sans moteur -- ply_log fabriqué à la main.
"""
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import game_report as gr

_failures = []


def check(cond, msg):
    if not cond:
        _failures.append(msg)


def entry(**kwargs):
    base = {
        "ply": 0, "side": "w", "san": "e4",
        "fen_before": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        "fen_after": "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
        "played_move_uci": "e2e4", "best_move_uci": "e2e4",
        "eval_before_cp": 20, "eval_after_cp": 15,
        "is_book": False, "second_best_cp": None,
    }
    base.update(kwargs)
    return base


def test_accuracy_formula_known_points():
    check(abs(gr.accuracy_from_win_percent_loss(0) - 100) < 0.5,
          "accuracy(perte=0) doit être proche de 100")
    check(gr.accuracy_from_win_percent_loss(100) < 5,
          "accuracy(perte=100) doit être proche de 0")
    lo = gr.accuracy_from_win_percent_loss(5)
    hi = gr.accuracy_from_win_percent_loss(30)
    check(lo > hi, "une perte de win% plus grande doit donner une accuracy plus basse")


def test_win_percent_symmetric_and_bounded():
    check(gr.win_percent(0) == 50.0, "win_percent(0) doit être 50")
    check(gr.win_percent(100000) == 100.0, "mat encodé positif -> 100%")
    check(gr.win_percent(-100000) == 0.0, "mat encodé négatif -> 0%")
    check(gr.win_percent(None) is None, "win_percent(None) doit rester None")


def test_classify_ply_best_move():
    e = entry(played_move_uci="e2e4", best_move_uci="e2e4", eval_before_cp=20, eval_after_cp=15)
    check(gr.classify_ply(e) == "Best", "coup == best_move_uci (non sacrifice) -> Best")


def test_classify_ply_book_priority():
    e = entry(is_book=True, played_move_uci="d2d4", best_move_uci="e2e4")
    check(gr.classify_ply(e) == "Book", "is_book=True prioritaire sur tout le reste")


def test_classify_ply_blunder():
    e = entry(played_move_uci="a2a3", best_move_uci="e2e4",
              eval_before_cp=200, eval_after_cp=-1000)
    label = gr.classify_ply(e)
    check(label == "Blunder", f"grosse perte de win% attendue -> Blunder, obtenu {label}")


def test_classify_ply_inaccuracy_small_loss():
    e = entry(played_move_uci="a2a3", best_move_uci="e2e4",
              eval_before_cp=30, eval_after_cp=20)
    label = gr.classify_ply(e)
    check(label in ("Excellent", "Good", "Inaccuracy"),
          f"petite perte de win% attendue -> pas Mistake/Blunder, obtenu {label}")


def test_classify_ply_unknown_move_never_crashes():
    e = entry(played_move_uci=None, best_move_uci=None)
    check(gr.classify_ply(e) == "Unknown", "played_move_uci=None -> Unknown, jamais de crash")


def test_classify_ply_great_needs_second_best_gap():
    e = entry(played_move_uci="e2e4", best_move_uci="e2e4",
              eval_before_cp=300, second_best_cp=100)
    check(gr.classify_ply(e) == "Great", "gros écart avec le 2e candidat -> Great")

    e2 = entry(played_move_uci="e2e4", best_move_uci="e2e4",
                eval_before_cp=300, second_best_cp=290)
    check(gr.classify_ply(e2) == "Best", "2e candidat proche -> pas Great, juste Best")

    e3 = entry(played_move_uci="e2e4", best_move_uci="e2e4",
                eval_before_cp=300, second_best_cp=None)
    check(gr.classify_ply(e3) == "Best", "pas de 2e candidat connu -> dégrade vers Best, pas de faux Great")


def test_classify_ply_brilliant_sacrifice():
    # Position où les Noirs peuvent prendre le cavalier (Cf3) avec leur
    # dame en h5xf3 -- sacrifice net (dame prend cavalier).
    fen = "rnb1kbnr/pppp1ppp/8/4p2q/8/5N2/PPPPPPPP/RNBQKB1R b KQkq - 2 3"
    e = entry(fen_before=fen, played_move_uci="h5f3", best_move_uci="h5f3",
              eval_before_cp=50, eval_after_cp=40)
    check(gr.classify_ply(e) == "Brilliant",
          "dame prend cavalier (perd de la valeur) sur le coup optimal -> Brilliant")


def test_classify_ply_missed_mate_is_miss():
    e = entry(played_move_uci="a2a3", best_move_uci="e2e4", eval_before_cp=99998)
    check(gr.classify_ply(e) == "Miss",
          "mat forcé disponible et non joué -> Miss")


def test_build_report_counts_both_sides():
    # fen_before réel de chaque ply (voir web_bridge._update_move_history) --
    # nécessaire pour que _is_brilliant_heuristic (side-to-move cohérent
    # avec le coup) donne un résultat sensé.
    fen0 = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    fen1 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
    fen2 = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"
    fen3 = "rnbqkbnr/pppp1ppp/8/4p3/4P3/P7/1PPP1PPP/RNBQKBNR b KQkq - 0 2"
    ply_log = [
        entry(ply=0, side="w", fen_before=fen0, played_move_uci="e2e4", best_move_uci="e2e4",
              eval_before_cp=20, eval_after_cp=15),
        entry(ply=1, side="b", fen_before=fen1, played_move_uci="e7e5", best_move_uci="e7e5",
              eval_before_cp=-15, eval_after_cp=-20),
        entry(ply=2, side="w", fen_before=fen2, played_move_uci="a2a3", best_move_uci="g1f3",
              eval_before_cp=20, eval_after_cp=-1000),
        entry(ply=3, side="b", fen_before=fen3, played_move_uci="d7d5", best_move_uci="d7d5",
              eval_before_cp=280, eval_after_cp=275),
    ]
    report = gr.build_report(ply_log, my_side="w")
    check(len(report["w"]["moves"]) == 2, "2 plies pour les Blancs")
    check(len(report["b"]["moves"]) == 2, "2 plies pour les Noirs")
    check(report["w"]["counts"]["Best"] == 1, "1 Best côté Blancs (e2e4)")
    check(report["w"]["counts"]["Blunder"] == 1, "1 Blunder côté Blancs (a2a3)")
    check(report["b"]["counts"]["Best"] == 2, "2 Best côté Noirs")
    check(report["w"]["accuracy"] is not None, "accuracy Blancs calculée")
    check(report["b"]["accuracy"] is not None, "accuracy Noirs calculée")
    check(report["b"]["accuracy"] > report["w"]["accuracy"],
          "les Noirs (2 Best) doivent avoir une meilleure accuracy que les Blancs (1 Blunder)")


def test_build_report_empty_log():
    report = gr.build_report([], my_side="w")
    check(report["w"]["accuracy"] is None, "pas de coup -> accuracy None, pas une exception")
    check(report["w"]["counts"]["Best"] == 0, "pas de coup -> compteurs à 0")


def main():
    for fn in (
        test_accuracy_formula_known_points,
        test_win_percent_symmetric_and_bounded,
        test_classify_ply_best_move,
        test_classify_ply_book_priority,
        test_classify_ply_blunder,
        test_classify_ply_inaccuracy_small_loss,
        test_classify_ply_unknown_move_never_crashes,
        test_classify_ply_great_needs_second_best_gap,
        test_classify_ply_brilliant_sacrifice,
        test_classify_ply_missed_mate_is_miss,
        test_build_report_counts_both_sides,
        test_build_report_empty_log,
    ):
        try:
            fn()
        except Exception as e:
            _failures.append(f"{fn.__name__} a levé une exception : {e!r}")

    if _failures:
        print(f"ÉCHEC ({len(_failures)}) :")
        for f in _failures:
            print("  -", f)
        sys.exit(1)
    print("test_game_report : OK")


if __name__ == "__main__":
    main()
