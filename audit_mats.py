"""
audit_mats.py
Diagnostic : le coach voit-il les mats forces, et propose-t-il le PLUS COURT ?

Rejoue des positions de mat connues dans le pipeline de production complet
(analyse moteur -> selection par profil -> narration) et compare, pour chaque
profil et chaque niveau, le coup propose au(x) coup(s) matant(s) reels calcules
independamment par python-chess.

Trois questions distinctes, souvent confondues :
  1. le MOTEUR voit-il le mat ?            (candidates[0] est-il matant ?)
  2. le PROFIL le garde-t-il ?             (select_move rend-il un coup matant ?)
  3. la NARRATION le dit-elle ?            (le texte annonce-t-il un mat ?)

Un ecart entre 1 et 2 signale un probleme de selection, pas de recherche.
"""
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import chess

import engine_analysis
import human_profile
import move_intent as mi
import narration_v2
import why_detector

ENGINE = r"C:\Users\Triv\Desktop\Vivado\test\Coach.EXE\CoachEchecs\stockfish.exe"

# (nom, fen, mat_en_n_coups_attendu). Positions classiques, verifiables a la main.
POSITIONS = [
    ("mat du couloir (mat en 1)", "6k1/5ppp/8/8/8/8/8/3R2K1 w - - 0 1", 1),
    ("mat de l'escalier (mat en 2)", "7k/8/8/8/8/8/6R1/5R1K w - - 0 1", 2),
    ("dame+roi contre roi (mat en 2)", "7k/8/6K1/8/8/8/8/6Q1 w - - 0 1", 2),
    ("mat du berger (mat en 1)", "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5Q2/PPPP1PPP/RNB1K1NR w KQkq - 0 1", 1),
    ("deux tours (mat en 2)", "6k1/8/8/8/8/8/1R6/R5K1 w - - 0 1", 2),
    # Mat etouffe : Cf7# est un mat IMMEDIAT (verifie a la main + par le
    # moteur) -- l'entree annoncait "mat en 2", une erreur du harnais lui-meme.
    ("mat etouffe (mat en 1)", "6rk/6pp/8/6N1/8/8/8/6K1 w - - 0 1", 1),
]

TIERS = (1, 2, 3)
PROFILS = ("popular", "creative", "classical")


def coups_matants(board):
    """Coups qui font mat IMMEDIATEMENT (verite terrain, sans moteur)."""
    trouves = []
    for m in board.legal_moves:
        board.push(m)
        if board.is_checkmate():
            trouves.append(board.san(chess.Move.null()) if False else m)
        board.pop()
    return trouves


def main():
    eng = engine_analysis.ChessCoachEngine(ENGINE, threads=2, hash_mb=256)
    echecs = []
    try:
        for nom, fen, mat_en in POSITIONS:
            board = chess.Board(fen)
            res, _ = eng.analyze_candidates(fen, multipv=6, depth=18)
            cands = res["candidates"]
            immediats = coups_matants(board)
            immediats_uci = {m.uci() for m in immediats}

            print(f"\n=== {nom}  (mat en {mat_en} attendu)")
            print(f"    candidats moteur : " + ", ".join(
                f"{c['move_san']}={c['score']}(loss {c['eval_loss']})" for c in cands[:4]))

            # Q1 : le moteur voit-il le mat ?
            top = cands[0]
            moteur_voit = "Mat" in str(top.get("score", ""))
            print(f"    Q1 moteur voit le mat : {'OUI' if moteur_voit else 'NON'} "
                  f"({top['move_san']} {top['score']})")
            if not moteur_voit:
                echecs.append(f"{nom} : le MOTEUR ne voit pas le mat (top={top['score']})")

            # Q2 : chaque profil / niveau propose-t-il le mat LE PLUS COURT ?
            # Verite terrain = le mat le plus court present dans les candidats
            # moteur (distance lue sur le score numerique, pas sur la chaine).
            distances = [d for d in (engine_analysis.mate_in_moves(c.get("cp")) for c in cands)
                         if d is not None and d > 0]
            plus_court = min(distances) if distances else None
            for tier in TIERS:
                for prof in PROFILS:
                    chosen = human_profile.select_move(cands, tier, prof, board=board)
                    if chosen is None:
                        echecs.append(f"{nom} [{prof}/tier{tier}] : aucun coup rendu")
                        continue
                    san = chosen["move_san"]
                    d = engine_analysis.mate_in_moves(chosen.get("cp"))
                    if plus_court is None:
                        ok, detail = False, "aucun mat dans les candidats moteur"
                    elif d is None or d <= 0:
                        ok, detail = False, "PAS une ligne matante"
                    elif d > plus_court:
                        ok = False
                        detail = (f"mat plus LENT que le plus court "
                                  f"(mat en {d} au lieu de {plus_court})")
                    else:
                        ok, detail = True, "mat le plus court"
                    if mat_en == 1 and ok and chosen["move_uci"] not in immediats_uci:
                        ok, detail = False, "PAS le mat immediat"
                    if not ok:
                        echecs.append(
                            f"{nom} [{prof}/tier{tier}] : propose {san} "
                            f"({chosen['score']}, loss {chosen['eval_loss']}) -- {detail}")
                        print(f"    Q2 {prof}/tier{tier} : {san} {chosen['score']} <-- {detail}")

            # Q3 : la narration annonce-t-elle le mat, sur le coup optimal ?
            chosen = cands[0]
            why_motif, why_detail = why_detector.detect_why(board, chosen)
            intent = mi.detect_move_intent(board, chosen, why_motif, why_detail)
            sel = narration_v2.build_selection(board, cands)
            out = narration_v2.render(sel, "popular", chosen=chosen,
                                      why_motif=why_motif, why_detail=why_detail,
                                      board=board)
            texte = (out.get("text") or "").strip()
            dit_mat = "mat" in texte.lower()
            print(f"    Q3 narration ({intent.kind}, mate_in={intent.mate_in}) : {texte[:90]}")
            # TOUT mat force doit etre annonce -- pas seulement le mat en 1
            # (l'ancien controle ne testait que mat_en == 1 et concluait "0
            # probleme" alors que trois positions de mat en 2 sortaient en
            # "rook_file"/"gives_check").
            if not dit_mat:
                echecs.append(f"{nom} : mat en {mat_en}, la narration ne dit pas 'mat' -- {texte!r}")
            if intent.kind != mi.MATE:
                echecs.append(f"{nom} : mat en {mat_en}, intent={intent.kind} au lieu de 'mate'")
            elif intent.mate_in != mat_en:
                echecs.append(f"{nom} : mat en {mat_en}, la narration annonce mate_in={intent.mate_in}")
    finally:
        eng.engine.quit()

    print("\n\n===== ECHECS =====")
    if not echecs:
        print("aucun")
    for e in echecs:
        print(" -", e)
    print(f"\n{len(echecs)} probleme(s)")


if __name__ == "__main__":
    main()
