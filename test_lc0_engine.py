"""
test_lc0_engine.py
Smoke test minimal : lc0 démarre (GPU si possible, sinon repli CPU -- voir
BridgeState._start_lc0_engine, le vrai code de prod, pas une réimplémentation
ici), charge les poids, et renvoie au moins un coup candidat valide sur la
position de départ. Lancer directement :
    python test_lc0_engine.py
"""
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from web_bridge import BridgeState

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LC0_GPU_PATH = os.path.join(BASE_DIR, "engines", "lc0", "gpu", "lc0.exe")
STARTPOS_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def demo():
    if not os.path.isfile(LC0_GPU_PATH):
        print(f"SKIP - binaire lc0 (GPU) introuvable : {LC0_GPU_PATH} "
              "(dépôt cloné sans les binaires lc0 ?)")
        return

    # BridgeState.__init__ exige un moteur "principal" valide (Stockfish/
    # Berserk) -- on réutilise lc0 lui-même à cette place ici, il parle UCI
    # comme n'importe quel autre moteur, largement suffisant pour ce smoke
    # test qui ne vérifie QUE le second moteur (lc0_engine).
    state = BridgeState(LC0_GPU_PATH, lc0_path=LC0_GPU_PATH, threads=1, hash_mb=1)
    try:
        assert state.lc0_engine is not None, "BridgeState._start_lc0_engine n'a démarré aucun lc0 (ni GPU ni repli CPU)"
        result, board = state.lc0_engine.analyze_candidates(STARTPOS_FEN, multipv=2, depth=6)
        assert result.get("game_over") is False, result
        candidates = result["candidates"]
        assert len(candidates) >= 1, "aucun candidat renvoyé par lc0"
        move_uci = candidates[0]["move_uci"]
        assert board.parse_uci(move_uci), f"coup invalide renvoyé par lc0 : {move_uci}"
        print(f"OK - lc0 a renvoyé {len(candidates)} candidat(s), premier coup : {move_uci}")
    finally:
        state.close()


if __name__ == "__main__":
    demo()
