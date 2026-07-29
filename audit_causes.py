"""
audit_causes.py
Les commentaires expliquent-ils, et disent-ils vrai ?

Rejoue une vraie partie dans le pipeline de PRODUCTION (analyse -> selection par
profil -> prophylaxie -> narration) et imprime, pour chaque coup blanc : le coup,
l'ecart avec le 2e candidat, le fait prophylactique detecte, et le paragraphe.

Trois choses a lire dans la sortie :
  1. les causes apparaissent-elles sur les coups a CHOIX SERRE (ecart faible) ?
  2. restent-elles absentes sur les coups EVIDENTS (ecart large) ?
  3. chaque cause est-elle VRAIE ? (le SAN cite doit etre un coup adverse
     reellement plus disponible -- verifie ici independamment)
"""
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import chess

import engine_analysis
import human_profile
import narration_v2
import prophylaxis
import why_detector

ENGINE = r"C:\Users\Triv\Desktop\Vivado\test\Coach.EXE\CoachEchecs\stockfish.exe"
GAME = """e4 e5 Nf3 d6 d4 Bg4 dxe5 Bxf3 Qxf3 dxe5 Bc4 Nf6 Qb3 Qe7 Nc3 c6 Bg5 b5
Nxb5 cxb5 Bxb5 Nbd7 O-O-O Rd8 Rxd7 Rxd7 Rd1 Qe6""".split()


def main():
    eng = engine_analysis.ChessCoachEngine(ENGINE, threads=2, hash_mb=256)
    board = chess.Board()
    faux = []
    try:
        for san in GAME:
            if board.turn == chess.WHITE:
                res, _ = eng.analyze_candidates(board.fen(), multipv=4, depth=14)
                cands = res["candidates"]
                chosen = human_profile.select_move(cands, 2, "popular", board=board)
                if chosen:
                    threat = prophylaxis.opponent_threat(eng, board)
                    move = chess.Move.from_uci(chosen["move_uci"])
                    proph = prophylaxis.prevented_by(board, move, threat)
                    sel = narration_v2.build_selection(board, cands)
                    wm, wd = why_detector.detect_why(board, chosen)
                    out = narration_v2.render(sel, "popular", chosen=chosen,
                                              why_motif=wm, why_detail=wd,
                                              board=board, prophylaxis=proph)
                    print(f"\n--- {board.fullmove_number}. {chosen['move_san']}"
                          f"   ecart={sel.gap_cp}cp  explique={sel.gap_cp <= narration_v2.EXPLAIN_GAP_MAX_CP}")
                    print(f"    menace adverse : {threat}   prophylaxie : {proph}")
                    print(f"    {out['text']}")

                    # Verification INDEPENDANTE du fait affiche : si le texte
                    # cite un SAN, ce coup doit vraiment etre devenu illegal.
                    if proph:
                        after = board.copy()
                        after.push(move)
                        after.push(chess.Move.null())
                        if any(after.san(m) == proph["san"] for m in after.legal_moves):
                            faux.append(f"{chosen['move_san']} : {proph['san']} est encore legal")
            board.push_san(san)
    finally:
        eng.engine.quit()

    print("\n===== FAITS FAUX =====")
    print("aucun" if not faux else "\n".join(" - " + f for f in faux))


if __name__ == "__main__":
    main()
