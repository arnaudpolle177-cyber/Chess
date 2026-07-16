"""
sim_precision.py
Simulation de la DISTRIBUTION DE PRÉCISION (bandes chess.com) produite par
human_profile.select_move, pour calibrer le réglage "précision popular" sans
avoir à jouer des dizaines de vraies parties.

POURQUOI c'est valide sans Stockfish : la SÉLECTION ne dépend que des
eval_loss / win_prob / drapeaux des candidats et de la logique de select_move.
On n'a donc pas besoin d'un vrai moteur -- il suffit de fournir des jeux de
candidats RÉALISTES (mêmes ordres de grandeur qu'une vraie analyse MultiPV en
milieu de jeu) et de mesurer ce que select_move en fait sur des milliers de
coups. C'est exactement la logique de réglage qui est testée, pas la force
Stockfish (qui, elle, vient de la profondeur et n'est pas en jeu ici).

Ce n'est PAS un test de force : ça ne dit rien sur l'Elo réel (fixé par la
profondeur d'analyse). Ça mesure UNIQUEMENT la distribution best/excellent/
good/inaccuracy pour un flux de positions au profil/tier donné.

Usage : python sim_precision.py [n_moves] [seed]
"""
import sys
import random

import human_profile as hp

# Seuils de bande en perte d'éval (centipawns) -- approximation des catégories
# chess.com (qui, elles, raisonnent en chute de win% ; on reste en cp, plus
# direct ici et suffisant pour comparer AVANT/APRÈS un réglage). Bornes
# choisies pour coller au vocabulaire de l'utilisateur (best / très bien /
# good / imprécision).
BANDS = [
    ("best",       0,   10),   # ~coup optimal
    ("excellent",  10,  25),   # très bien
    ("good",       25,  50),   # bon
    ("inaccuracy", 50,  120),  # imprécision
    ("mistake",    120, 250),  # faute
    ("blunder",    250, 10_000),
]


def classify(eval_loss):
    for name, lo, hi in BANDS:
        if lo <= eval_loss < hi:
            return name
    return "blunder"


def _make_candidate(eval_loss, is_capture=False, moving=0, captured=None,
                    developing=False, castle=False, check=False, central=False):
    """Un candidat au format attendu par select_move (voir analyze_candidates)."""
    # win_prob : corrélé négativement à la perte d'éval (un moins bon coup a de
    # moins bonnes chances pratiques), avec un peu de bruit -- comme un vrai WDL.
    base = max(0.0, 0.62 - eval_loss / 400.0)
    return {
        "move_uci": "e2e4", "move_san": "e4",
        "cp": -eval_loss, "eval_loss": eval_loss,
        "score": "0.0",
        "is_capture": is_capture, "is_check": check, "is_castle": castle,
        "is_king_move": False, "is_developing_minor": developing,
        "is_pawn_center_push": False, "to_square_central": central,
        "win_prob": base,
        "moving_piece_value": moving,
        "captured_piece_value": captured,
        "pv_san": ["e4"], "pv_uci": ["e2e4"],
    }


def random_position_candidates(rng):
    """
    Jeu de candidats plausible pour UNE position de milieu de jeu, MultiPV 5.
    Le 1er candidat est toujours à eval_loss 0 (le meilleur objectif). Les
    suivants s'écartent selon un profil réaliste : le plus souvent des écarts
    modestes (plusieurs bons coups), parfois une position tranchée (un seul
    bon coup, le reste décroche vite). Une fraction des positions offre un
    sacrifice objectivement optimal (eval_loss 0 + pièce chère prend pièce
    moins chère) -> matière au "coup brillant".
    """
    n = 5
    # "Tranché" : un seul bon coup, chute rapide. "Riche" : plusieurs coups
    # proches. Mélange pour ne pas biaiser la mesure vers un seul type.
    sharp = rng.random() < 0.35
    losses = [0]
    for i in range(1, n):
        if sharp:
            step = rng.uniform(25, 90)
        else:
            step = rng.uniform(6, 30)
        losses.append(round(losses[-1] + step))

    cands = []
    sac_here = rng.random() < 0.12  # ~1 position sur 8 offre un sacrifice optimal
    for i, loss in enumerate(losses):
        if i == 0 and sac_here:
            cands.append(_make_candidate(0, is_capture=True, moving=9, captured=5))
        else:
            is_cap = rng.random() < 0.3
            cands.append(_make_candidate(
                loss,
                is_capture=is_cap,
                moving=rng.choice([1, 3, 3, 5]) if is_cap else 0,
                captured=rng.choice([1, 3, 3, 5]) if is_cap else None,
                developing=rng.random() < 0.2,
                central=rng.random() < 0.2,
            ))
    return cands


def run(n_moves, seed, profile_id="popular", elo_tier_id=2):
    rng = random.Random(seed)
    counts = {name: 0 for name, _, _ in BANDS}
    brilliant = 0
    flagged_inacc = 0
    for _ in range(n_moves):
        cands = random_position_candidates(rng)
        chosen = hp.select_move(cands, elo_tier_id, profile_id, rng=rng)
        if chosen is None:
            continue
        counts[classify(chosen["eval_loss"])] += 1
        if chosen.get("is_brilliant"):
            brilliant += 1
        if chosen.get("is_inaccuracy"):
            flagged_inacc += 1
    return counts, brilliant, flagged_inacc


def main():
    n_moves = int(sys.argv[1]) if len(sys.argv) > 1 else 20_000
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 12345

    counts, brilliant, flagged = run(n_moves, seed)
    total = sum(counts.values())
    print(f"\nProfil popular, tier 2 -- {total} coups simulés (seed={seed})\n")
    print(f"{'bande':<12}{'count':>8}{'%':>8}{'/49 coups':>12}")
    print("-" * 40)
    for name, _, _ in BANDS:
        c = counts[name]
        pct = 100 * c / total if total else 0
        per49 = 49 * c / total if total else 0
        print(f"{name:<12}{c:>8}{pct:>7.1f}%{per49:>11.1f}")
    print("-" * 40)
    # "Précision" chess.com approximée : part des coups best+excellent+good
    # (tout ce qui n'est ni imprécision ni pire) n'est PAS la vraie formule,
    # mais donne un repère AVANT/APRÈS comparable.
    clean = counts["best"] + counts["excellent"] + counts["good"]
    print(f"best+excellent+good : {100*clean/total:.1f}%")
    print(f"imprécisions+ : {100*(total-clean)/total:.1f}%")
    print(f"coups brillants (sacrifice optimal joué) : {brilliant} ({49*brilliant/total:.1f}/49)")
    print(f"coups tirés 'inexact' (drapeau) : {flagged} ({49*flagged/total:.1f}/49)")
    print("\nCible utilisateur (sur ~49 coups) : 27 best / 12 très bien / 4-5 good / 3 imprécisions\n")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
