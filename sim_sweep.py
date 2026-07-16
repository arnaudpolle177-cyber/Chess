"""
sim_sweep.py
Balayage (grid search) des 4 curseurs de "forme" de la distribution
popular+tier2, pour trouver le réglage qui colle le mieux à la cible de
distribution voulue -- au lieu de tâtonner les constantes à la main.

S'appuie sur sim_precision.py (mêmes jeux de candidats réalistes, même
classification en bandes chess.com). Pour chaque combinaison de curseurs, on
PATCHE les constantes du module human_profile (les fonctions les lisent via le
global du module au moment de l'appel -> le patch prend effet sans réécrire la
logique), on relance N coups, et on mesure l'écart à la cible.

Ce n'est PAS un test de force (l'Elo vient de la profondeur, hors sujet ici) --
juste la FORME de la distribution best/excellent/good/inaccuracy. Voir
sim_precision.py pour la limite méthodo (seuils de bande approximés en cp).

Usage : python sim_sweep.py [n_moves_par_combo] [seed] [top_k]
"""
import sys
import random

import human_profile as hp
import sim_precision as sim

# Cible en NOMBRE de coups sur une partie ~49 coups (voir la conversation).
# On compare la distribution simulée, ramenée à /49, à ces valeurs.
TARGET_PER49 = {
    "best": 27.0,
    "excellent": 12.0,
    "good": 4.5,
    "inaccuracy": 3.0,
}

# Grille de valeurs par curseur. Volontairement large sur BAND_FACTOR (le vrai
# levier "forme" : plus bas = plus étalé) et TARGET_MULT (position du pic).
GRID = {
    "POPULAR_TIER2_TARGET_MULT":   [0.28, 0.5, 0.7, 0.9, 1.1, 1.3],
    "POPULAR_TIER2_BAND_FACTOR":   [0.15, 0.25, 0.35, 0.5, 0.75, 1.2],
    "POPULAR_TIER2_HARD_CAP_MULT": [1.5, 2.0, 2.5, 3.0],
    "POPULAR_TIER2_HUMANITY_MULT": [0.6, 1.0, 1.4],
}


def score_distribution(counts, total):
    """
    Écart à la cible : somme des carrés des différences (en /49) sur les 4
    bandes qui nous intéressent. Plus bas = plus proche de la cible.
    """
    err = 0.0
    for band, target in TARGET_PER49.items():
        per49 = 49 * counts[band] / total if total else 0
        err += (per49 - target) ** 2
    return err


def run_one(n_moves, seed):
    counts, _, _ = sim.run(n_moves, seed)
    total = sum(counts.values())
    return counts, total


def main():
    n_moves = int(sys.argv[1]) if len(sys.argv) > 1 else 4000
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 12345
    top_k = int(sys.argv[3]) if len(sys.argv) > 3 else 8

    # Sauvegarde des valeurs d'origine pour les restaurer à la fin (ne pas
    # laisser le module dans un état patché si on l'importe ailleurs après).
    keys = list(GRID.keys())
    original = {k: getattr(hp, k) for k in keys}

    results = []
    total_combos = 1
    for k in keys:
        total_combos *= len(GRID[k])
    print(f"Balayage : {total_combos} combinaisons x {n_moves} coups (seed={seed})...\n")

    def recurse(i, current):
        if i == len(keys):
            for k, v in current.items():
                setattr(hp, k, v)
            counts, total = run_one(n_moves, seed)
            err = score_distribution(counts, total)
            results.append((err, dict(current), counts, total))
            return
        for v in GRID[keys[i]]:
            current[keys[i]] = v
            recurse(i + 1, current)

    try:
        recurse(0, {})
    finally:
        for k, v in original.items():
            setattr(hp, k, v)

    results.sort(key=lambda r: r[0])
    print(f"Cible /49 : {TARGET_PER49}\n")
    print(f"--- Top {top_k} réglages (écart le plus faible à la cible) ---\n")
    for err, combo, counts, total in results[:top_k]:
        per49 = {b: round(49 * counts[b] / total, 1) for b in TARGET_PER49}
        short = {k.replace("POPULAR_TIER2_", ""): v for k, v in combo.items()}
        print(f"écart={err:6.1f}  {short}")
        print(f"           best={per49['best']}  exc={per49['excellent']}  "
              f"good={per49['good']}  inacc={per49['inaccuracy']}\n")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
