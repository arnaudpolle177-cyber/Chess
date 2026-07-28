"""Audit multi-scenarios du pipeline de narration DE PRODUCTION.

Complete audit_narration.py (une seule partie d'attaque) par des positions
que cette partie ne couvre pas : finales, position fermee, defense sous
attaque, finale de dames, position egale sans jeu. Pour chaque scenario, on
joue a chaque ply le coup recommande par le pipeline (pas une partie figee),
et on imprime coup / intent / theme / why / paragraphe, plus des controles
automatiques de forme (FAUTES).

Le harnais ne doit jamais planter NI se bloquer : toute exception (position
sans coup legal, partie terminee, erreur du pipeline) est capturee,
journalisee, et le scenario suivant est enchaine. Un cas particulier observe
en pratique (charge machine forte, plusieurs moteurs Stockfish concurrents) :
chess.engine peut ne PAS lever d'exception mais rester bloque indefiniment
(une reponse UCI tardive arrive apres que le protocole a deja avance sur une
autre position -- la future asyncio interne ne se resout alors jamais). Un
simple try/except ne rattrape pas un blocage : chaque appel moteur passe donc
par un timeout explicite (_avec_timeout). Si le moteur ne repond pas dans le
delai, on considere qu'il est mort, on abandonne le scenario en cours et on
en RECREE un neuf avant d'enchainer le scenario suivant.
"""
import queue
import re
import sys
import threading
import traceback

sys.path.insert(0, r"C:\Users\Triv\Desktop\Vivado\test\Chess-main")
import chess
import engine_analysis, human_profile, why_detector, move_intent as mi
import narration_v2

ENGINE = r"C:\Users\Triv\Desktop\Vivado\test\Coach.EXE\CoachEchecs\stockfish.exe"
OUT_PATH = r"C:\Users\Triv\Desktop\Vivado\test\Chess-main\audit_scenarios_report.txt"

# ponytail: timeout heuristique -- distingue "tres lent car machine chargee"
# de "bloque pour de bon". A remonter si les analyses profondes en pratique
# depassent regulierement ce delai sur une machine chargee.
ENGINE_TIMEOUT_S = 60

SCENARIOS = [
    # ponytail: FEN du brief task-9 CORRIGEES -- les 3 finales etaient sans
    # roi noir ("8/.../6K1 w ...", etc.), donc une position ILLEGALE. Un
    # moteur nourri d'une position sans roi renvoie un score degenere (mate
    # sentinelle -> "avantage d'environ 1000.0 pions" observe en smoke-test),
    # et la narration le repete fidelement : ce n'etait pas un bug narration,
    # c'etait un bug de donnee d'entree du harnais. Roi noir ajoute en
    # symetrique de la position blanche. Deuxieme correction : la version
    # symetrique "naive" (pions f7g7h7 / f2g2h2 boites) offrait un mat au
    # dos immediat (Rxa8#/Qxd8#) des le premier coup -- le scenario s'arretait
    # avant de tester quoi que ce soit d'une finale. Luft ajoute (pion h
    # avance en h3/h6) pour que le roi ait une case de fuite.
    ("finale tours+pions", "r5k1/5pp1/7p/8/8/8/5PP1/R5K1 w - - 0 1", 8),
    ("finale roi+pions", "6k1/5ppp/8/8/8/8/5PPP/6K1 w - - 0 1", 8),
    ("position fermee", "r1bqk2r/pp2bppp/2n1pn2/2pp4/2PP4/2N1PN2/PP2BPPP/R1BQK2R w KQkq - 0 1", 10),
    ("defense sous attaque", "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5Q2/PPPP1PPP/RNB1K1NR b KQkq - 0 1", 8),
    ("finale de dames", "3q2k1/5pp1/7p/8/8/8/5PP1/3Q2K1 w - - 0 1", 6),
    ("position egale sans jeu", "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", 6),
]

FAUTES = []


def _avec_timeout(fn, timeout_s, *args, **kwargs):
    """Execute fn(*args, **kwargs) dans un thread demon, avec un timeout.

    Retourne (True, resultat) en cas de succes dans le delai. Retourne
    (False, None) en cas de timeout -- le thread est alors abandonne
    (demon : il ne bloque pas la sortie du process) car on n'a aucun moyen
    sur d'interrompre un appel moteur bloque au milieu d'un parsing UCI.
    Une exception levee par fn() a l'interieur du delai est re-levee ici
    (l'appelant garde son try/except habituel)."""
    resultat = queue.Queue(maxsize=1)

    def _run():
        try:
            resultat.put(("ok", fn(*args, **kwargs)))
        except Exception as e:
            resultat.put(("err", e))

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    try:
        kind, val = resultat.get(timeout=timeout_s)
    except queue.Empty:
        return False, None
    if kind == "err":
        raise val
    return True, val


def controler(nom, rows):
    for i, r in enumerate(rows):
        # ponytail: SAN peut se terminer par '+'/'#' (echec/mat) -- sans le
        # retrait, "Rxa8+"[-2:] = "8+" qui n'apparait jamais dans un texte
        # qui dit correctement "en a8" : faux positif systematique sur toute
        # capture/echec, observe au premier run complet. Le roque (O-O) n'a
        # de toute facon pas de "case" au sens de ce controle -- exempte
        # comme "quiet" au lieu de faux-positiver a chaque roque.
        case = r["coup"].rstrip("+#")[-2:]
        # 1. Le texte cite-t-il la piece ou la case du coup propose ?
        if (case not in r["texte"] and r["intent"] not in ("quiet", "castle")):
            FAUTES.append(f"[{nom}] {r['coup']} ({r['intent']}) : le texte ne cite pas {case}")
        # 2. Repetition immediate
        if i > 0 and r["texte"] == rows[i - 1]["texte"]:
            FAUTES.append(f"[{nom}] {r['coup']} : texte identique au coup precedent")
        # 3. Accord et coherence de vocabulaire
        # ponytail: "in" (substring) faisait un faux positif sur "une fou"
        # DANS "une fourchette" -- observe au run complet. \b force une
        # frontiere de mot des deux cotes.
        for faute in ("le tour", "le dame", "un tour", "une cavalier", "une fou"):
            if re.search(r"\b" + re.escape(faute) + r"\b", r["texte"]):
                FAUTES.append(f"[{nom}] {r['coup']} : accord fautif '{faute}'")
        # 4. Affirmation non prouvee
        if "sans reprise" in r["texte"] and r["why"] not in ("undefended", "not_recaptured"):
            FAUTES.append(f"[{nom}] {r['coup']} : 'sans reprise' affirme avec why={r['why']}")
        # 5. Contrat de clause casse par le tissage
        if r["texte"] and r["texte"][0].islower():
            FAUTES.append(f"[{nom}] {r['coup']} : paragraphe commence en minuscule")


def jouer_scenario(eng, nom, fen, n_coups, log):
    """Deroule jusqu'a n_coups plies en jouant a chaque fois le coup recommande
    par le pipeline -- quel que soit le camp au trait (analyse toujours la
    position COURANTE du board local, et pousse le coup obtenu sur ce meme
    board, donc l'alternance des camps est geree automatiquement).

    Ne leve jamais : toute anomalie est journalisee et interrompt seulement
    ce scenario (le scenario suivant est enchaine par l'appelant).

    Retourne (rows, moteur_mort) -- moteur_mort=True signale a l'appelant
    qu'un timeout moteur a eu lieu et que `eng` doit etre recree avant de
    lancer un autre scenario (voir ENGINE_TIMEOUT_S / _avec_timeout)."""
    rows = []
    try:
        board = chess.Board(fen)
    except Exception as e:
        log(f"[{nom}] FEN invalide : {e!r}")
        return rows, False

    history = []
    # ponytail: reproduit exactement web_bridge._recent_intent_kinds (voir
    # web_bridge.py:1305-1307) -- un tuple des 4 derniers "kind" par profil,
    # passe a narration_v2.render(recent_kinds=...). Sans ca l'anti-repetition
    # de production n'est jamais exerce et le harnais mesurerait des
    # repetitions qui n'existent pas en vrai (voir brief task-9).
    recent_kinds = []
    for ply in range(n_coups):
        if board.is_game_over():
            log(f"[{nom}] partie terminee ({board.result()}) apres {ply} coup(s) joue(s)")
            break
        try:
            ok, out_analyse = _avec_timeout(
                eng.analyze_candidates, ENGINE_TIMEOUT_S, board.fen(), multipv=4, depth=14)
            if not ok:
                log(f"[{nom}] TIMEOUT moteur au ply {ply} (fen={board.fen()}, >{ENGINE_TIMEOUT_S}s) : "
                    f"le moteur sous-jacent semble bloque, scenario abandonne")
                return rows, True
            res, _b = out_analyse
            cands = res.get("candidates", [])
            if not cands:
                log(f"[{nom}] aucun candidat renvoye par le moteur a {board.fen()}")
                break
            chosen = human_profile.select_move(cands, 2, "popular", board=board)
            if not chosen:
                log(f"[{nom}] aucun coup choisi par human_profile a {board.fen()}")
                break
            why_motif, why_detail = why_detector.detect_why(board, chosen)
            intent = mi.detect_move_intent(board, chosen, why_motif, why_detail)
            sel = narration_v2.build_selection(board, cands, move_history=list(history))
            out = narration_v2.render(sel, "popular", chosen=chosen,
                                       why_motif=why_motif, why_detail=why_detail,
                                       board=board, recent_kinds=tuple(recent_kinds))
            kind = (out.get("intent_kind")
                    or (out.get("lead") and str(out["lead"])) or "")
            recent_kinds.append(kind)
            recent_kinds = recent_kinds[-4:]
            mv = chess.Move.from_uci(chosen["move_uci"])
            if mv not in board.legal_moves:
                log(f"[{nom}] coup illegal renvoye par le pipeline au ply {ply} "
                    f"(fen={board.fen()}, uci={chosen['move_uci']}) : scenario abandonne")
                break
            san = board.san(mv)
            rows.append({
                "n": board.fullmove_number,
                "coup": san,
                "intent": (intent.kind if intent else "NONE"),
                "forcing": (intent.forcing if intent else False),
                "lead": sel.lead.theme if sel.lead else None,
                "why": why_motif,
                "texte": (out.get("text") or "").strip(),
            })
            board.push(mv)
            history.append(san)
        except Exception as e:
            log(f"[{nom}] EXCEPTION au ply {ply} (fen={board.fen()}) : {e!r}")
            log(traceback.format_exc())
            break
    return rows, False


def _nouveau_moteur(log):
    try:
        return engine_analysis.ChessCoachEngine(ENGINE, threads=2, hash_mb=256)
    except Exception as e:
        log(f"(erreur) impossible de demarrer/redemarrer le moteur : {e!r}")
        log(traceback.format_exc())
        return None


def main():
    lines = []

    def log(msg=""):
        lines.append(msg)

    eng = _nouveau_moteur(log)
    try:
        for nom, fen, n in SCENARIOS:
            log(f"\n===== SCENARIO : {nom} =====")
            if eng is None:
                log(f"[{nom}] pas de moteur disponible, scenario saute")
                continue
            try:
                rows, moteur_mort = jouer_scenario(eng, nom, fen, n, log)
            except Exception as e:
                log(f"[{nom}] SCENARIO EN ERREUR (non capturee dans jouer_scenario) : {e!r}")
                log(traceback.format_exc())
                rows, moteur_mort = [], False

            for r in rows:
                log(f"\n--- coup {r['n']}. {r['coup']}   intent={r['intent']}"
                    f"{' (forcant)' if r['forcing'] else ''}   theme={r['lead']}   why={r['why']}")
                log(f"    {r['texte']}")

            if rows:
                n_quiet = sum(1 for r in rows if r["intent"] == "quiet")
                log(f"\n[{nom}] taux quiet residuel : {n_quiet}/{len(rows)}")

            try:
                controler(nom, rows)
            except Exception as e:
                log(f"[{nom}] controle automatique en erreur : {e!r}")

            if moteur_mort:
                # Le moteur a laisse une requete pendante -- on ne le
                # referme pas proprement (quit() risquerait de se bloquer
                # aussi), on l'abandonne et on en recree un neuf.
                log(f"[{nom}] redemarrage du moteur pour le scenario suivant")
                eng = _nouveau_moteur(log)
    finally:
        if eng is not None:
            ok, _ = _avec_timeout(eng.engine.quit, 10)
            if not ok:
                log("(avertissement) le moteur ne s'est pas ferme dans le delai imparti")

    log("\n\n===== FAUTES DETECTEES (controles automatiques) =====")
    if FAUTES:
        for f in FAUTES:
            log(f)
    else:
        log("(aucune faute detectee par les controles automatiques)")

    report = "\n".join(lines)

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(report)

    # Ne jamais planter a l'affichage console (accents / codepage Windows) :
    # le rapport UTF-8 sur disque fait foi, la console est un confort.
    try:
        print(report)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "utf-8"
        print(report.encode(enc, errors="replace").decode(enc, errors="replace"))

    print(f"\n(rapport ecrit en UTF-8 dans {OUT_PATH})")


if __name__ == "__main__":
    main()
