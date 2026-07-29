"""Simule des parties COMPLETES (ouverture -> finale) dans le pipeline de
production, coup par coup et pour les deux camps, et imprime chaque
paragraphe du coach avec de quoi juger sa pedagogie.

Difference avec audit_narration.py (une partie humaine rejouee, un seul
camp, une seule voix, PAS de prophylaxie) : ici les parties sont jouees par
les profils eux-memes, donc elles atteignent VRAIMENT les finales -- la zone
ou les commentaires sont soupconnes d'etre creux -- et le chemin reproduit
web_bridge.handle_single_profile, prophylaxie et recent_kinds compris.

Lancer au premier plan avec un timeout genereux (plusieurs minutes) :
    PYTHONIOENCODING=utf-8 python audit_parties.py [nb_parties] [plies_max]
"""
import random
import sys
from collections import Counter

sys.path.insert(0, r"C:\Users\Triv\Desktop\Vivado\test\Chess-main")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import chess

import engine_analysis
import human_profile
import why_detector
import prophylaxis
import narration_v2
import move_intent as mi

ENGINE = r"C:\Users\Triv\Desktop\Vivado\test\Coach.EXE\CoachEchecs\stockfish.exe"

DEPTH = 14          # milieu de la fourchette du tier 1 en production (13-15)
MULTIPV = 4
N_GAMES = int(sys.argv[1]) if len(sys.argv) > 1 else 4
MAX_PLIES = int(sys.argv[2]) if len(sys.argv) > 2 else 90


def phase_of(board):
    """Phase GROSSIERE pour ventiler les stats. Volontairement independante
    de human_profile._game_phase : on veut juger le texte par zone de partie,
    pas re-tester la detection de phase."""
    men = len(board.piece_map())
    if board.fullmove_number <= 10:
        return "ouverture"
    return "finale" if men <= 12 else "milieu"


def play_and_narrate(eng, proph_eng, seed):
    """Joue une partie (les deux camps pilotes par human_profile) en narrant
    chaque position AVANT le coup. Retourne la liste des lignes d'audit."""
    rng = random.Random(seed)
    board = chess.Board()
    history = []
    recent_kinds = []
    rows = []

    while not board.is_game_over(claim_draw=True) and len(history) < MAX_PLIES:
        res, _b = eng.analyze_candidates(board.fen(), multipv=MULTIPV, depth=DEPTH)
        cands = res.get("candidates") or []
        if not cands:
            break
        # Niveau alterne par partie pour varier la qualite du jeu (et donc
        # les themes : une partie de tier 1 produit de vraies fautes).
        tier = 1 + (seed % 3)
        chosen = human_profile.select_move(cands, tier, "popular", rng=rng, board=board)
        if not chosen:
            break

        why_motif, why_detail = why_detector.detect_why(board, chosen)
        selection = narration_v2.build_selection(
            board, cands, move_history=list(history))

        proph = None
        try:
            threat = prophylaxis.opponent_threat(proph_eng, board)
            if threat is not None:
                proph = prophylaxis.prevented_by(
                    board, chess.Move.from_uci(chosen["move_uci"]), threat)
        except Exception as e:
            print(f"prophylaxie indisponible : {e}")

        out = narration_v2.render(
            selection, "popular", chosen=chosen, why_motif=why_motif,
            why_detail=why_detail, board=board,
            recent_kinds=tuple(recent_kinds[-4:]), prophylaxis=proph)

        kind = out.get("intent_kind") or (out.get("lead") and str(out["lead"])) or ""
        recent_kinds.append(kind)

        mv = chess.Move.from_uci(chosen["move_uci"])
        # Recalcul de l'intent pour le DIAGNOSTIC seul (pur python, aucun
        # appel moteur) : narration_v2.render ne rend que le kind, pas le
        # bilan materiel ni la PV qui l'ont produit.
        dbg = mi.detect_move_intent(board, chosen, why_motif, why_detail)
        rows.append({
            "partie": seed,
            "n": board.fullmove_number,
            "trait": "B" if board.turn == chess.WHITE else "N",
            "coup": board.san(mv),
            "phase": phase_of(board),
            "intent": out.get("intent_kind") or "-",
            "theme": str(selection.lead.theme) if selection.lead else None,
            "livre": selection.book,
            "gap": selection.gap_cp,
            "proph": (proph or {}).get("san"),
            "fen": board.fen(),
            "pv": " ".join(chosen.get("pv_uci") or []),
            "delta": dbg.material_delta if dbg else None,
            "sac_ply": getattr(dbg, "sacrifice_ply", None),
            "texte": (out.get("text") or "").strip(),
        })
        board.push(mv)
        history.append(rows[-1]["coup"])  # SAN calcule AVANT le push

    return rows, board


def main():
    eng = engine_analysis.ChessCoachEngine(ENGINE, threads=2, hash_mb=256)
    # Moteur SEPARE pour la prophylaxie, comme en production (scenario_engine)
    # -- une sonde de coup nul au milieu de l'analyse principale corromprait
    # l'etat du moteur partage.
    proph_eng = engine_analysis.ChessCoachEngine(ENGINE, threads=1, hash_mb=64)

    all_rows = []
    try:
        for seed in range(1, N_GAMES + 1):
            rows, board = play_and_narrate(eng, proph_eng, seed)
            all_rows += rows
            print(f"\n\n########## PARTIE {seed} -- {len(rows)} positions, "
                  f"issue : {board.result(claim_draw=True)} "
                  f"({'mat' if board.is_checkmate() else 'arret/nulle'})")
            for r in rows:
                print(f"\n[{r['phase']:9s}] {r['n']}{'.' if r['trait']=='B' else '...'} "
                      f"{r['coup']:6s} intent={r['intent']:14s} theme={r['theme']} "
                      f"gap={r['gap']}{' LIVRE' if r['livre'] else ''}"
                      f"{'  proph=' + r['proph'] if r['proph'] else ''}")
                if r["intent"] == "sacrifice":
                    print(f"   DIAG delta={r['delta']} sac_ply={r['sac_ply']} pv={r['pv']}")
                    print(f"   DIAG fen={r['fen']}")
                print(f"   {r['texte']}")
    finally:
        eng.engine.quit()
        proph_eng.engine.quit()

    print("\n\n===== SYNTHESE =====")
    print(f"positions narrees : {len(all_rows)}")
    for phase in ("ouverture", "milieu", "finale"):
        rows = [r for r in all_rows if r["phase"] == phase]
        if not rows:
            continue
        textes = [r["texte"] for r in rows]
        # Le chiffre qui compte : combien de paragraphes DIFFERENTS le lecteur
        # voit reellement. 30 positions pour 5 textes = du bruit, pas du coaching.
        print(f"\n--- {phase} : {len(rows)} positions, "
              f"{len(set(textes))} paragraphes distincts "
              f"({100*len(set(textes))//max(1,len(rows))}%)")
        print("   intents :", Counter(r["intent"] for r in rows).most_common(6))
        print("   themes  :", Counter(r["theme"] for r in rows).most_common(6))
        avec_cause = sum(1 for t in textes if " -- " in t)
        print(f"   causes rendues : {avec_cause}/{len(rows)}"
              f"   prophylaxies : {sum(1 for r in rows if r['proph'])}")
        for txt, n in Counter(textes).most_common(3):
            if n > 1:
                print(f"   x{n} : {txt[:150]}")

    # Phrases (pas paragraphes) les plus rabachees, toutes phases confondues :
    # c'est la que se voit le tic de langage.
    phrases = Counter()
    for r in all_rows:
        for s in r["texte"].split(". "):
            s = s.strip().rstrip(".")
            if s:
                phrases[s] += 1
    print("\n--- phrases les plus repetees (toutes phases) :")
    for s, n in phrases.most_common(12):
        print(f"   x{n:3d}  {s[:130]}")


if __name__ == "__main__":
    main()
