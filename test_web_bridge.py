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
import threading
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import chess

import engine_analysis
import prophylaxis
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


# --- 5. Cache de menace (_opponent_threat) : 1 appel moteur par FEN --------
class _FakeState:
    """Juste assez d'attributs pour rejouer BridgeState._opponent_threat
    sans démarrer Stockfish (pas de moteur réel requis, voir prophylaxis.py :
    engine=None -> opponent_threat rend toujours None sans appel réseau/CPU)."""
    def __init__(self):
        self.lock = threading.Lock()
        self.scenario_engine_lock = threading.Lock()
        self.scenario_engine = None  # opponent_threat(None, ...) -> None, sans appel moteur
        self._threat_cache_key = None
        self._threat_cache_value = None

    _opponent_threat = web_bridge.BridgeState._opponent_threat


def test_threat_cache_par_fen():
    state = _FakeState()
    calls = []
    real_opponent_threat = prophylaxis.opponent_threat

    def counting_opponent_threat(engine, board, depth=10):
        calls.append(board.fen())
        return real_opponent_threat(engine, board, depth=depth)

    prophylaxis.opponent_threat = counting_opponent_threat
    try:
        board_a = chess.Board()
        board_b = chess.Board("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1")

        state._opponent_threat(board_a.fen(), board_a)
        state._opponent_threat(board_a.fen(), board_a)  # même FEN -> cache-hit, pas de 2e appel
        check(len(calls) == 1, f"même FEN doit rester en cache : {len(calls)} appels moteur attendus 1")

        state._opponent_threat(board_b.fen(), board_b)  # FEN différent -> recalcule
        check(len(calls) == 2, f"FEN différent doit recalculer : {len(calls)} appels moteur attendus 2")
        check(state._threat_cache_key == board_b.fen(), "la clé de cache doit suivre le dernier FEN interrogé")
    finally:
        prophylaxis.opponent_threat = real_opponent_threat


# --- 6. Contention sur scenario_engine_lock : jamais d'attente, jamais un
#        skip mis en cache comme un None définitif -----------------------
def test_threat_cache_contention():
    state = _FakeState()
    state.scenario_engine_lock.acquire()  # simule _attach_scenario_async en cours
    try:
        board = chess.Board()
        t0 = time.perf_counter()
        result = state._opponent_threat(board.fen(), board)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        check(result is None, f"verrou occupé -> doit rendre None immédiatement, reçu {result!r}")
        check(elapsed_ms < 50, f"acquire(blocking=False) ne doit jamais attendre : {elapsed_ms:.1f}ms")
        check(state._threat_cache_key is None,
              "un skip par contention ne doit PAS être mis en cache (sinon il se fige pour cette position)")
    finally:
        state.scenario_engine_lock.release()

    # Le verrou est maintenant libre : un appel normal doit calculer et
    # remplir le cache (preuve que le skip précédent n'a pas poisonné l'état).
    board = chess.Board()
    result = state._opponent_threat(board.fen(), board)
    check(state._threat_cache_key == board.fen(), "une fois le verrou libre, le calcul doit se faire et être caché")


def main():
    for fn in (test_blancs_trait_mat_donne, test_blancs_trait_mat_subi,
               test_noirs_trait_mat_donne_par_noirs, test_pas_de_mat,
               test_threat_cache_par_fen, test_threat_cache_contention):
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
