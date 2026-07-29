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


# --- 6. Contention sur scenario_engine_lock : attente BORNÉE (voir
#        THREAT_LOCK_TIMEOUT_S), jamais un skip mis en cache comme un None
#        définitif, jamais une attente illimitée -------------------------
def test_threat_cache_contention():
    # Le verrou doit être tenu par un AUTRE thread : avec un acquire(timeout=)
    # (contrairement à l'ancien acquire(blocking=False)), le tenir dans CE
    # thread ferait bloquer l'appel pour de vrai pendant le timeout entier au
    # lieu d'échouer immédiatement -- un thread daemon + join(timeout=...)
    # fait échouer le test PROPREMENT (au lieu de suspendre la suite) si la
    # régression réapparaît.
    state = _FakeState()
    state.scenario_engine_lock.acquire()  # simule un profil frère / un scénario différé en cours
    try:
        board = chess.Board()
        result_holder = {}

        def call_from_other_thread():
            result_holder["result"] = state._opponent_threat(board.fen(), board)

        t = threading.Thread(target=call_from_other_thread, daemon=True)
        t0 = time.perf_counter()
        t.start()
        t.join(timeout=web_bridge.THREAT_LOCK_TIMEOUT_S + 2.0)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        check(not t.is_alive(), "l'appel n'est jamais revenu -- attente illimitée (régression)")
        check(result_holder.get("result") is None,
              f"verrou toujours occupé après le timeout -> doit rendre None, reçu {result_holder.get('result')!r}")
        check(elapsed_ms >= web_bridge.THREAT_LOCK_TIMEOUT_S * 1000 - 20,
              f"doit avoir réellement attendu ~THREAT_LOCK_TIMEOUT_S avant d'abandonner : {elapsed_ms:.1f}ms")
        check(state._threat_cache_key is None,
              "un skip par contention ne doit PAS être mis en cache (sinon il se fige pour cette position)")
    finally:
        state.scenario_engine_lock.release()

    # Le verrou est maintenant libre : un appel normal doit calculer et
    # remplir le cache (preuve que le skip précédent n'a pas poisonné l'état).
    board = chess.Board()
    result = state._opponent_threat(board.fen(), board)
    check(state._threat_cache_key == board.fen(), "une fois le verrou libre, le calcul doit se faire et être caché")


# --- 7. Contention BRÈVE : le second appelant attend, obtient un résultat
#        (pas None), et un cache-hit après acquisition ne re-sonde pas -----
def test_threat_lock_bounded_wait_and_recheck():
    state = _FakeState()
    board = chess.Board()
    calls = []
    real_opponent_threat = prophylaxis.opponent_threat

    def counting_opponent_threat(engine, board_arg, depth=10):
        calls.append(board_arg.fen())
        return real_opponent_threat(engine, board_arg, depth=depth)

    prophylaxis.opponent_threat = counting_opponent_threat
    try:
        state.scenario_engine_lock.acquire()  # simule un profil frère qui vient de prendre le verrou

        result_holder = {}

        def waiter():
            result_holder["result"] = state._opponent_threat(board.fen(), board)

        t = threading.Thread(target=waiter, daemon=True)
        t.start()
        time.sleep(0.05)  # laisse le waiter se mettre en attente sur le verrou
        # Le "profil frère" termine SA sonde et remplit le cache pour ce FEN
        # AVANT de relâcher le verrou -- c'est le cas visé par le re-check
        # après acquisition : le waiter ne doit pas resonder pour un résultat
        # déjà en cache.
        with state.lock:
            state._threat_cache_key = board.fen()
            state._threat_cache_value = {"already": "cached"}
        state.scenario_engine_lock.release()

        t.join(timeout=web_bridge.THREAT_LOCK_TIMEOUT_S + 2.0)
        check(not t.is_alive(), "le waiter n'est jamais revenu")
        check(result_holder.get("result") == {"already": "cached"},
              f"une contention BRÈVE doit rendre le résultat mis en cache par le tenant précédent, reçu {result_holder.get('result')!r}")
        check(len(calls) == 0,
              "le re-check du cache après acquisition doit éviter une sonde moteur redondante")
    finally:
        prophylaxis.opponent_threat = real_opponent_threat


def main():
    for fn in (test_blancs_trait_mat_donne, test_blancs_trait_mat_subi,
               test_noirs_trait_mat_donne_par_noirs, test_pas_de_mat,
               test_threat_cache_par_fen, test_threat_cache_contention,
               test_threat_lock_bounded_wait_and_recheck):
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
