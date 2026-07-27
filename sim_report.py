"""
sim_report.py
Diagnostic pour calibrer À LA MAIN les seuils de bande de game_report.py
(CATEGORY_BANDS_WIN_PCT_LOSS) -- même esprit que sim_precision.py/
sim_sweep.py (pas un optimiseur automatique, juste un instrument de mesure) :
simule des parties synthétiques à différents niveaux de "propreté" (peu ou
beaucoup de pertes de win% par coup) et affiche la distribution de labels +
l'accuracy obtenue, pour vérifier que ça correspond à l'intuition (une
partie quasi sans erreur -> accuracy 90+, une partie pleine de gaffes ->
accuracy basse) plutôt que de garder des seuils devinés sans jamais les
éprouver.

Limite méthodo (comme sim_precision.py) : les pertes de win% par coup sont
tirées d'une loi gaussienne repliée (approximation grossière d'un vrai
joueur), pas d'une vraie distribution de parties réelles -- suffisant pour
juger si les BANDES sont dans le bon ordre de grandeur, pas pour une
calibration scientifique fine.

Usage : python sim_report.py [n_moves_par_profil] [seed]
"""
import math
import random
import sys

import game_report as gr

# Doit rester cohérent avec la constante interne de game_report.win_percent
# (0.00368208) -- utilisée ici seulement pour INVERSER la formule (trouver
# le cp qui donne un win% cible), afin de fabriquer des entrées _ply_log
# synthétiques dont _win_pct_loss reproduit la perte voulue.
_WIN_PERCENT_K = 0.00368208


def _cp_for_win_percent(win_pct):
    win_pct = min(99.9, max(0.1, win_pct))
    return math.log(win_pct / (100 - win_pct)) / _WIN_PERCENT_K


# Profils de "propreté" simulés (moyenne, écart-type de la perte de win% par
# coup, en points de %) -- pas de vrais joueurs, juste des repères pour
# éprouver les seuils sur un éventail réaliste de parties.
PROFILES = {
    "quasi-parfait (GM)": (1.0, 1.5),
    "solide (club fort)": (3.0, 4.0),
    "amateur moyen": (7.0, 8.0),
    "debutant": (14.0, 12.0),
}


def _synthetic_ply(loss_pct):
    """Entrée _ply_log minimale : seule _win_pct_loss (donc eval_before_cp/
    eval_after_cp) compte pour classify_ply -- le reste est un remplissage
    neutre (jamais Book/Brilliant/Great/Miss ici, uniquement les bandes
    win%-loss qu'on veut calibrer)."""
    start_wp = 50.0
    end_wp = max(0.001, start_wp - loss_pct)
    is_best = loss_pct < 0.01
    return {
        "side": "w",
        "played_move_uci": "a2a3",
        "best_move_uci": "a2a3" if is_best else "e2e4",
        "eval_before_cp": round(_cp_for_win_percent(start_wp)),
        "eval_after_cp": round(_cp_for_win_percent(end_wp)),
        "is_book": False,
        "second_best_cp": None,
    }


def run(mean, std, n_moves, rng):
    ply_log = [_synthetic_ply(max(0.0, rng.gauss(mean, std))) for _ in range(n_moves)]
    return gr.build_report(ply_log, my_side="w")["w"]


def main():
    n_moves = int(sys.argv[1]) if len(sys.argv) > 1 else 4000
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 12345
    rng = random.Random(seed)

    print(f"Calibration game_report.py -- {n_moves} coups simulés par profil (seed={seed})\n")
    for name, (mean, std) in PROFILES.items():
        data = run(mean, std, n_moves, rng)
        total = sum(data["counts"].values())
        print(f"--- {name} (perte moyenne ~{mean}% +/- {std}%) -- accuracy = {data['accuracy']} ---")
        for label in ("Excellent", "Good", "Inaccuracy", "Mistake", "Blunder"):
            c = data["counts"][label]
            pct = 100 * c / total if total else 0
            per49 = 49 * c / total if total else 0
            print(f"  {label:<12}{c:>6}  {pct:5.1f}%  {per49:5.1f}/49")
        print()

    print(
        "Repère (chess.com, indicatif) : un joueur \"solide\" tourne "
        "généralement autour de 85-93 d'accuracy, un \"amateur moyen\" plutôt "
        "70-85, un \"débutant\" souvent sous 65. Si les chiffres ci-dessus "
        "s'en écartent nettement, ajuster CATEGORY_BANDS_WIN_PCT_LOSS dans "
        "game_report.py."
    )


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
