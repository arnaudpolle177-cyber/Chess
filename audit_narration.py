"""Rejoue une vraie partie dans le pipeline de narration DE PRODUCTION et
imprime, position par position : coup propose / intent / theme / paragraphe.
But : mesurer ou et pourquoi le commentaire decroche de la fleche."""
import sys
sys.path.insert(0, r"C:\Users\Triv\Desktop\Vivado\test\Chess-main")
import chess
import engine_analysis, human_profile, why_detector, move_intent as mi
import narration_v2

ENGINE = r"C:\Users\Triv\Desktop\Vivado\test\Coach.EXE\CoachEchecs\stockfish.exe"

# Partie d'attaque classique (Morphy, Opera 1858) : melange de coups calmes,
# developpement, prises et sacrifices -- exactement le spectre qui nous interesse.
GAME = """e4 e5 Nf3 d6 d4 Bg4 dxe5 Bxf3 Qxf3 dxe5 Bc4 Nf6 Qb3 Qe7 Nc3 c6 Bg5 b5
Nxb5 cxb5 Bxb5 Nbd7 O-O-O Rd8 Rxd7 Rxd7 Rd1 Qe6 Bxd7 Nxd7 Qb8 Nxb8 Rd8""".split()

eng = engine_analysis.ChessCoachEngine(ENGINE, threads=2, hash_mb=256)
board = chess.Board()
history = []
rows = []

for ply, san in enumerate(GAME):
    if board.turn == chess.WHITE:  # on n'audite qu'un camp, ca suffit
        try:
            res, _b = eng.analyze_candidates(board.fen(), multipv=4, depth=14)
            cands = res["candidates"]
            chosen = human_profile.select_move(cands, 2, "popular", board=board)
            if chosen:
                why_motif, why_detail = why_detector.detect_why(board, chosen)
                intent = mi.detect_move_intent(board, chosen, why_motif, why_detail)
                sel = narration_v2.build_selection(board, cands, move_history=list(history))
                out = narration_v2.render(sel, "popular", chosen=chosen,
                                          why_motif=why_motif, why_detail=why_detail,
                                          board=board)
                mv = chess.Move.from_uci(chosen["move_uci"])
                rows.append({
                    "n": board.fullmove_number,
                    "coup": board.san(mv),
                    "intent": (intent.kind if intent else "NONE"),
                    "forcing": (intent.forcing if intent else False),
                    "lead": sel.lead.theme if sel.lead else None,
                    "why": why_motif,
                    "texte": (out.get("text") or "").strip(),
                })
        except Exception as e:
            import traceback; traceback.print_exc()
            rows.append({"n": board.fullmove_number, "coup": "?", "intent": f"ERREUR {e!r}",
                         "forcing": False, "lead": None, "why": None, "texte": ""})
    board.push_san(san)
    history.append(san)

eng.engine.quit()

for r in rows:
    print(f"\n--- coup {r['n']}. {r['coup']}   intent={r['intent']}"
          f"{' (forcant)' if r['forcing'] else ''}   theme={r['lead']}   why={r['why']}")
    print(f"    {r['texte']}")

print("\n\n===== SYNTHESE =====")
from collections import Counter
print("intents :", Counter(r["intent"] for r in rows))
print("forcants :", sum(1 for r in rows if r["forcing"]), "/", len(rows))
