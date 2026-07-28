"""
test_web_bridge.py
Test de _objective_eval_white (web_bridge.py) -- la SEULE valeur objective
visible par l'utilisateur (barre d'avantage du navigateur). Corrigé pour ne
plus diviser la distance de mat par deux (affichait "mat en 2" pour un mat
en 4). Sans moteur : on fabrique directement le dict candidat et un board,
comme fait engine_analysis.analyze_candidates -- aucun Stockfish démarré.

Couvre les 4 chemins de signe : "mate" est TOUJOURS du point de vue des
BLANCS, quel que soit le camp au trait sur la position analysée.
"""
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import chess

import engine_analysis
import web_bridge

_failures = []


def check(cond, msg):
    if not cond:
        _failures.append(msg)


def cand(cp):
    return {"cp": cp}


# --- 1. Blancs au trait, mat DONNE par les Blancs (mat en 4) ---------------
def test_blancs_trait_mat_donne():
    board = chess.Board()  # position de départ, Blancs au trait
    cp = engine_analysis.MATE_SCORE - 4  # mat en 4, POV du camp au trait (Blancs)
    out = web_bridge._objective_eval_white(cand(cp), board)
    check(out["mate"] == 4, f"blancs donnent mat en 4 : mate={out['mate']} attendu 4")


# --- 2. Blancs au trait, mat SUBI par les Blancs (mat en 4) -----------------
def test_blancs_trait_mat_subi():
    board = chess.Board()
    cp = -(engine_analysis.MATE_SCORE - 4)  # mat subi en 4, POV Blancs (au trait)
    out = web_bridge._objective_eval_white(cand(cp), board)
    check(out["mate"] == -4, f"blancs se font mater en 4 : mate={out['mate']} attendu -4")


# --- 3. Noirs au trait, mat DONNE par les Noirs (mat en 4) ------------------
def test_noirs_trait_mat_donne_par_noirs():
    board = chess.Board("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR b KQkq - 0 1")
    cp = engine_analysis.MATE_SCORE - 4  # POV du camp au trait = Noirs : ils matent
    out = web_bridge._objective_eval_white(cand(cp), board)
    # Le champ est du point de vue des BLANCS -> negatif, ce sont eux qui se
    # font mater.
    check(out["mate"] == -4, f"noirs donnent mat en 4 : mate={out['mate']} attendu -4")


# --- 4. Position sans mat force ---------------------------------------------
def test_pas_de_mat():
    board = chess.Board()
    out = web_bridge._objective_eval_white(cand(50), board)
    check(out["mate"] is None, f"pas de mat : mate={out['mate']} attendu None")
    check(out["cp"] == 50, f"pas de mat : cp={out['cp']} attendu 50 (Blancs au trait)")


def main():
    for fn in (test_blancs_trait_mat_donne, test_blancs_trait_mat_subi,
               test_noirs_trait_mat_donne_par_noirs, test_pas_de_mat):
        try:
            fn()
        except Exception as e:
            _failures.append(f"{fn.__name__} a levé une exception : {e!r}")

    if _failures:
        print(f"ÉCHEC ({len(_failures)}) :")
        for f in _failures:
            print("  -", f)
        sys.exit(1)
    print("test_web_bridge : OK")


if __name__ == "__main__":
    main()
