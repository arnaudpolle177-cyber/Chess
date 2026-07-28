"""
test_fragment_library.py
Tests de l'étape 3 (fragment_library.py) -- réservoir de fragments.

Ce que ces tests garantissent (sans moteur, sans réseau) :
  - chaque thème connu produit un dict {observation, cause, plan} dans les 3
    voix, sans exception ;
  - le CONTRAT DE FRAGMENT est respecté : observation et plan toujours
    présents/non vides, aucune clause ne se termine par un point, aucune ne
    commence par une majuscule décorative (sauf notation SAN / nom propre) ;
  - les fragments ancrés sur un champ réel CITENT bien ce champ (la case
    d'une faiblesse de pion, le SAN d'un coup manqué, le pion passé) --
    preuve que "rien d'inventé" tient : la donnée affichée vient de la brique ;
  - un champ optionnel absent (why_motif None, opponent_better_move_san None,
    passed_pawn_square None) ne casse rien et ne fabrique pas de donnée.
"""
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import chess

from theme_detector import (
    ThemeCandidate,
    BLUNDER, TACTICAL, ATTACK, DEFENSE, MISSED_OPPORTUNITY,
    ENDGAME, OPENING, INITIATIVE_SHIFT, STRATEGIC_ADVANTAGE, PAWN_STRUCTURE,
    PIECE_ACTIVITY_GAP, KING_SAFETY_WARNING, EQUAL_POSITION,
)
import fragment_library
import fragment_library as fl
from fragment_library import FragmentContext, fragments_for, VOICES


_failures = []


def check(cond, msg):
    if not cond:
        _failures.append(msg)


def _frag_string_constants():
    """(nom de fonction _frag_*, ligne, chaîne littérale) lues dans la SOURCE.

    AST plutôt qu'exécution : couvre AUSSI les branches que les autres tests
    n'empruntent jamais (c'était justement le cas de « se redeploie »).
    """
    import ast
    source = open(fragment_library.__file__, encoding="utf-8").read()
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.FunctionDef) and node.name.startswith("_frag_")):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                yield node.name, sub.lineno, sub.value


def test_texte_affiche_toujours_accentue():
    """
    Garde de non-régression BIDIRECTIONNELLE sur les accents du texte AFFICHÉ.

    1. Accents MANQUANTS -- les fragments d'intention avaient été écrits sans
       accents (« Developpe, puis roque », « roi a l'abri », « se redeploie »)
       parce que la consigne « messages de commit sans accent » avait été
       appliquée par erreur au texte de l'interface.
    2. Accents EN TROP (sur-correction) -- le chercher/remplacer qui a rétabli
       les accents a aussi transformé le VERBE « force » en participe
       « forcé » (« forcé la position », « une séquence qui forcé un
       sacrifice »). Une garde qui ne cherchait que les accents manquants
       validait donc ses propres dégâts, toujours au vert.
    """
    import re

    # Formes NUES de mots qui portent toujours un accent en français. On liste
    # la forme fautive, pas la correcte : une occurrence = un oubli.
    nues = re.compile(
        r"\b("
        r"apres|acheve|activite|ameliore|amelioration|arrivee|cle|connectee|"
        r"deja|depart|deplace|deploie|developpe|developpement|echange|"
        r"element|forcee|hostilites|idee|idees|materiel|menacee|operation|"
        r"piece|pieces|placee|placees|redeploie|reoriente|securite|securise|"
        r"strategie|verifie"
        r")\b"
    )
    # Sur-corrections : un participe passé suivi d'un DÉTERMINANT est en
    # réalité un verbe conjugué (« force la position », « donne échec et
    # force la réponse »). Même piège pour « sécurisé le roi », « développé
    # tes pièces » : c'est l'impératif qui était visé.
    surcorrige = re.compile(
        r"\b(forcé|sécurisé|développé|amélioré|vérifié|achevé|réorienté)\s+"
        r"(la|le|les|un|une|des|ton|ta|tes|cette|ce)\b"
    )
    for fname, lineno, valeur in _frag_string_constants():
        for mot in sorted(set(nues.findall(valeur))):
            check(False,
                  f"{fname} (ligne {lineno}) : « {mot} » sans accent "
                  f"dans un texte affiché -- {valeur!r}")
        for mot, suite in surcorrige.findall(valeur):
            check(False,
                  f"{fname} (ligne {lineno}) : « {mot} {suite} » -- participe "
                  f"passé accentué là où le VERBE est attendu (sur-correction "
                  f"d'accents) -- {valeur!r}")


def brick(theme, **fields):
    return ThemeCandidate(theme, 1.0, dict(fields))


# Notation SAN / nom propre : un fragment PEUT légitimement commencer par une
# majuscule s'il cite un coup (rare -- en pratique nos observations mettent le
# SAN en milieu de clause, mais on reste tolérant). On vérifie surtout
# l'absence de point final et la présence des clés.
def _assert_clause(clause, where):
    check(isinstance(clause, str) and clause.strip() != "", f"{where}: clause vide")
    if not clause:
        return
    check(not clause.rstrip().endswith("."), f"{where}: clause finit par un point -> {clause!r}")
    check(not clause.rstrip().endswith(" et"), f"{where}: clause finit par une conjonction pendante -> {clause!r}")


ALL_THEMES = [
    BLUNDER, TACTICAL, ATTACK, DEFENSE, MISSED_OPPORTUNITY,
    ENDGAME, OPENING, INITIATIVE_SHIFT, STRATEGIC_ADVANTAGE, PAWN_STRUCTURE,
    PIECE_ACTIVITY_GAP, KING_SAFETY_WARNING, EQUAL_POSITION,
]

# Champs plausibles par thème (mêmes noms que collect_theme_bricks pose) pour
# que chaque fragment ait de quoi s'ancrer.
SAMPLE_FIELDS = {
    BLUNDER: {"swing_cp": 220},
    TACTICAL: {},
    ATTACK: {"king_square": chess.G8},
    DEFENSE: {"king_square": chess.G1},
    MISSED_OPPORTUNITY: {"swing_cp": 90, "opponent_better_move_san": "Qh5"},
    ENDGAME: {"passed_pawn_square": chess.D6},
    OPENING: {},
    INITIATIVE_SHIFT: {"initiative_slope_cp": -30.0},
    STRATEGIC_ADVANTAGE: {"material_imbalance_kind": "bishop_pair_open", "simplification_advice": "simplify"},
    PAWN_STRUCTURE: {"pawn_weakness_square": chess.C6, "pawn_weakness_kind": "isolated"},
    PIECE_ACTIVITY_GAP: {"activity_ratio": 1.6},
    KING_SAFETY_WARNING: {"king_safety_warning_square": chess.E1, "king_safety_warning_is_mine": True},
    EQUAL_POSITION: {},
}


def test_all_themes_all_voices_shape():
    ctx = FragmentContext(eval_cp=150)
    for theme in ALL_THEMES:
        for voice in VOICES:
            b = brick(theme, **SAMPLE_FIELDS[theme])
            frag = fragments_for(b, voice, ctx)
            check(set(frag.keys()) == {"observation", "cause", "plan"},
                  f"{theme}/{voice}: clés inattendues -> {set(frag.keys())}")
            _assert_clause(frag["observation"], f"{theme}/{voice} observation")
            _assert_clause(frag["plan"], f"{theme}/{voice} plan")
            if frag["cause"] is not None:
                _assert_clause(frag["cause"], f"{theme}/{voice} cause")


def test_unknown_voice_falls_back():
    b = brick(BLUNDER, swing_cp=200)
    frag = fragments_for(b, "nonexistent_voice", FragmentContext())
    check(frag["observation"], "voix inconnue -> devrait retomber sur un fallback non vide")


def test_no_context_no_crash():
    # ctx None : les fragments doivent retomber sur leur formulation générale.
    for theme in ALL_THEMES:
        b = brick(theme, **SAMPLE_FIELDS[theme])
        frag = fragments_for(b, "popular")  # ctx omis
        check(frag["observation"], f"{theme}: ctx absent -> observation vide")
        check(frag["plan"], f"{theme}: ctx absent -> plan vide")


def test_pawn_structure_cites_real_square():
    # "rien d'inventé" : la case citée est bien celle de la brique.
    b = brick(PAWN_STRUCTURE, pawn_weakness_square=chess.C6, pawn_weakness_kind="isolated")
    for voice in VOICES:
        frag = fragments_for(b, voice)
        text = " ".join(v for v in frag.values() if v)
        check("c6" in text, f"{voice}: la case c6 de la faiblesse devrait apparaître -> {text!r}")
        check("isolé" in text.lower(), f"{voice}: le type 'isolé' devrait apparaître -> {text!r}")


def test_missed_cites_san_when_present():
    b = brick(MISSED_OPPORTUNITY, swing_cp=90, opponent_better_move_san="Qh5")
    frag = fragments_for(b, "popular")
    check("Qh5" in frag["observation"], f"le SAN Qh5 devrait être cité -> {frag['observation']!r}")


def test_missed_no_san_no_invention():
    # SAN absent : l'observation ne doit PAS inventer de coup (pas de motif
    # SAN fabriqué). On vérifie juste qu'elle reste générale et non vide.
    b = brick(MISSED_OPPORTUNITY, swing_cp=90, opponent_better_move_san=None)
    frag = fragments_for(b, "popular")
    check(frag["observation"], "observation vide sans SAN")
    # aucune majuscule suivie de chiffre typique d'un SAN inventé (heuristique
    # faible mais utile) -- surtout : le test SAMPLE ci-dessus prouve le
    # chemin AVEC san, celui-ci prouve le chemin SANS.
    check("None" not in frag["observation"], "le None ne doit jamais fuiter dans le texte")


def test_endgame_passed_pawn_vs_none():
    with_pawn = fragments_for(brick(ENDGAME, passed_pawn_square=chess.D6), "popular")
    without = fragments_for(brick(ENDGAME, passed_pawn_square=None), "popular")
    check("d6" in " ".join(v for v in with_pawn.values() if v),
          "le pion passé d6 devrait être cité quand il existe")
    check("d6" not in " ".join(v for v in without.values() if v),
          "aucune case ne doit être citée quand il n'y a pas de pion passé")


def test_initiative_sign_depends_on_eval():
    b = brick(INITIATIVE_SHIFT, initiative_slope_cp=-30.0)
    winning = fragments_for(b, "popular", FragmentContext(eval_cp=120))
    losing = fragments_for(b, "popular", FragmentContext(eval_cp=-120))
    # En avantage : on PERD l'initiative ; en désavantage : on la REPREND.
    check(winning["observation"] != losing["observation"],
          "le sens du basculement d'initiative devrait dépendre de l'éval")


def test_strategic_imbalance_drives_plan():
    open_pair = fragments_for(
        brick(STRATEGIC_ADVANTAGE, material_imbalance_kind="bishop_pair_open", simplification_advice="simplify"),
        "classical", FragmentContext(eval_cp=150))
    no_imb = fragments_for(
        brick(STRATEGIC_ADVANTAGE, material_imbalance_kind=None, simplification_advice="simplify"),
        "classical", FragmentContext(eval_cp=150))
    check(open_pair["observation"] != no_imb["observation"],
          "un déséquilibre matériel connu devrait changer l'observation stratégique")
    check("fous" in open_pair["observation"].lower(),
          f"bishop_pair_open devrait mentionner les fous -> {open_pair['observation']!r}")


def test_king_safety_mine_vs_opponent():
    mine = fragments_for(
        brick(KING_SAFETY_WARNING, king_safety_warning_square=chess.E1, king_safety_warning_is_mine=True),
        "popular")
    opp = fragments_for(
        brick(KING_SAFETY_WARNING, king_safety_warning_square=chess.E8, king_safety_warning_is_mine=False),
        "popular")
    check("ton roi" in mine["observation"].lower(), f"is_mine=True -> 'ton roi' -> {mine['observation']!r}")
    check("adverse" in opp["observation"].lower(), f"is_mine=False -> roi adverse -> {opp['observation']!r}")


def test_opening_castle_three_states():
    # Trois états distincts (voir fragment_library._castle_state) :
    #   - roque JOUABLE maintenant  -> conseille de roquer ;
    #   - droits présents mais pièces pas sorties (coup 1) -> conseille de
    #     DÉVELOPPER, jamais "roque derrière toi" (le bug corrigé) ;
    #   - plus de droits (déjà roqué) -> ne parle plus de roque.
    now = chess.Board("rnbqk2r/pppp1ppp/5n2/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 1")  # O-O légal
    early = chess.Board("rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 1")  # droits, mais rien de sorti
    done = chess.Board("rnbq1rk1/pppp1ppp/5n2/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQ1RK1 w - - 0 1")  # les deux ont roqué

    def line(board):
        frag = fragments_for(brick(OPENING), "popular", FragmentContext(board=board))
        return " ".join(v for v in frag.values() if v).lower()

    now_txt, early_txt, done_txt = line(now), line(early), line(done)
    check("roque" in now_txt, f"roque jouable -> devrait conseiller de roquer -> {now_txt!r}")
    # Coup 1 : on doit inviter à développer, PAS annoncer un roque déjà fait.
    check("roque est derrière" not in early_txt,
          f"début d'ouverture -> ne doit PAS dire 'le roque est derrière toi' -> {early_txt!r}")
    check("développe" in early_txt or "sorties" in early_txt,
          f"début d'ouverture -> devrait parler de développement -> {early_txt!r}")
    # Déjà roqué : on ne renvoie plus vers le roque.
    check("roque est derrière" in done_txt or "roque n'est plus" in done_txt,
          f"déjà roqué -> devrait acter que le roque est fait -> {done_txt!r}")


def test_piece_article_accord():
    import chess
    check(fragment_library._piece_with_article(chess.ROOK) == "la tour",
          "tour est FEMININ : 'la tour', jamais 'le tour'")
    check(fragment_library._piece_with_article(chess.QUEEN) == "la dame",
          "dame est FEMININ")
    check(fragment_library._piece_with_article(chess.KNIGHT) == "le cavalier",
          "cavalier est masculin")
    check(fragment_library._piece_with_article(chess.ROOK, definite=False) == "une tour",
          "article indefini feminin")
    check(fragment_library._piece_with_article(None) == "la piece"
          or fragment_library._piece_with_article(None) == "la pièce",
          "type inconnu -> repli feminin coherent avec 'piece'")


def test_capture_free_accorde_et_coherent():
    import chess, move_intent
    # Tour noire en d7 non défendue, tour blanche en d1 la prend.
    board = chess.Board("7k/3r4/8/8/8/8/8/3RK3 w - - 0 1")
    intent = move_intent.detect_move_intent(
        board, {"move_uci": "d1d7", "pv_uci": ["d1d7"]})
    for voice in ("popular", "creative", "classical"):
        frag = fragment_library.fragments_for_intent(intent, voice)
        blob = " ".join(v for v in frag.values() if v)
        check("le tour" not in blob, f"[{voice}] accord : jamais 'le tour'")
        check("pièce" not in blob or "tour" in blob,
              f"[{voice}] on nomme la piece reellement prise, pas 'piece'")


def test_sans_reprise_seulement_si_case_non_defendue():
    import chess, move_intent
    # Dame en d7 DEFENDUE par le roi e8, prise par la tour d1 : la prise
    # reste gagnante a l'echange (+9 -5 = +4) mais la piece EST reprenable
    # -> interdit d'ecrire "sans reprise" (note : avec une DAME comme
    # attaquante au lieu d'une tour, le bilan de ligne serait negatif et
    # l'intent tomberait en SACRIFICE, pas CAPTURE_FREE -- d'ou la tour).
    board = chess.Board("4k3/3q4/8/8/8/8/8/3RK3 w - - 0 1")
    intent = move_intent.detect_move_intent(
        board, {"move_uci": "d1d7", "pv_uci": ["d1d7", "e8d7"]})
    if intent.kind == move_intent.CAPTURE_FREE:
        check(intent.capture_undefended is False,
              "case defendue par le roi : capture_undefended doit etre False")
        for voice in ("popular", "creative", "classical"):
            frag = fragment_library.fragments_for_intent(intent, voice)
            blob = " ".join(v for v in frag.values() if v)
            check("sans reprise" not in blob,
                  f"[{voice}] 'sans reprise' est faux ici : {blob!r}")
        # "sans compensation" (voix creative) affirme la meme chose que "sans
        # reprise" -- egalement faux sur une prise defendue mais gagnante.
        creative_frag = fragment_library.fragments_for_intent(intent, "creative")
        creative_blob = " ".join(v for v in creative_frag.values() if v)
        check("sans compensation" not in creative_blob,
              f"[creative] 'sans compensation' est faux ici : {creative_blob!r}")


def test_capture_free_sans_preuve_materielle_ne_ment_pas():
    import chess, move_intent
    # Cavalier c3 prend un cavalier e4 DEFENDU par le pion d5, valeur egale
    # (3 pour 3) -> line_delta == 0 apres reprise, donc NI capture_undefended
    # (case defendue) NI capture_line_gain (bilan de ligne nul, pas positif).
    # Seul le 3e chemin de _capture_is_free (why_motif="fork", qui ne
    # recalcule aucun gain) fait ressortir CAPTURE_FREE : aucun fragment ne
    # doit alors affirmer un gain materiel ou une prise imprenable.
    board = chess.Board("4k3/8/8/3p4/4n3/2N5/8/4K3 w - - 0 1")
    intent = move_intent.detect_move_intent(
        board, {"move_uci": "c3e4", "pv_uci": ["c3e4", "d5e4"]}, why_motif="fork")
    check(intent.kind == move_intent.CAPTURE_FREE,
          f"setup: attendu capture_free, obtenu {intent.kind}")
    check(intent.capture_undefended is False,
          "case defendue par le pion d5 : capture_undefended doit etre False")
    check(intent.capture_line_gain is False,
          "echange a valeur egale (line_delta == 0) : capture_line_gain doit etre False")
    for voice in ("popular", "creative", "classical"):
        frag = fragment_library.fragments_for_intent(intent, voice)
        blob = " ".join(v for v in frag.values() if v)
        check("sans reprise" not in blob and "sans compensation" not in blob,
              f"[{voice}] aucune preuve d'imprenabilite : {blob!r}")
        check("gain net" not in blob and "tourne" not in blob.lower(),
              f"[{voice}] aucune preuve de gain materiel, ne pas l'affirmer : {blob!r}")


def test_fragment_mate_dans_les_3_voix():
    import chess, move_intent
    board = chess.Board("6k1/5ppp/8/8/8/8/8/3R2K1 w - - 0 1")
    intent = move_intent.detect_move_intent(
        board, {"move_uci": "d1d8", "pv_uci": ["d1d8"]})
    for voice in ("popular", "creative", "classical"):
        frag = fragment_library.fragments_for_intent(intent, voice)
        check(frag is not None, f"[{voice}] MATE doit avoir un fragment")
        blob = " ".join(v for v in frag.values() if v).lower()
        check("mat" in blob, f"[{voice}] le texte doit dire que c'est mat")


def test_fragments_calmes_nomment_le_coup():
    import chess, move_intent
    cas = [
        ("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 1", "f1c4", "c4"),
        ("rnbqk2r/pppp1ppp/5n2/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 1", "e1g1", None),
        ("4k3/ppp2ppp/8/8/8/8/PPP2PPP/3RK3 w - - 0 1", "d1d5", "d5"),
    ]
    for fen, uci, case in cas:
        board = chess.Board(fen)
        intent = move_intent.detect_move_intent(board, {"move_uci": uci, "pv_uci": [uci]})
        for voice in ("popular", "creative", "classical"):
            frag = fragment_library.fragments_for_intent(intent, voice)
            check(frag is not None,
                  f"[{voice}] {uci} ({intent.kind}) doit avoir un fragment, pas None")
            blob = " ".join(v for v in frag.values() if v)
            check(blob.strip() != "", f"[{voice}] {uci} : fragment vide")
            if case:
                check(case in blob,
                      f"[{voice}] {uci} : le texte doit citer la case {case}, obtenu {blob!r}")
            check(blob[0].islower() or blob[0].isdigit(),
                  f"[{voice}] {uci} : contrat de clause, minuscule initiale")
            check(not blob.rstrip().endswith("."),
                  f"[{voice}] {uci} : contrat de clause, pas de point final")


def test_reposition_isole():
    # Cavalier DEJA developpe (c3, hors rangee de fond) qui saute vers une
    # case vide e4 : pas de capture, pas d'echec, pas de colonne ouverte en
    # jeu (piece = cavalier, la branche ROOK_FILE ne s'applique qu'aux
    # tours/dames). C'est le seul chemin de la cascade qui reste : REPOSITION.
    import chess, move_intent
    board = chess.Board("4k3/8/8/8/8/2N5/8/4K3 w - - 0 1")
    intent = move_intent.detect_move_intent(
        board, {"move_uci": "c3e4", "pv_uci": ["c3e4"]})
    check(intent.kind == move_intent.REPOSITION,
          f"position mal choisie : attendu REPOSITION, obtenu {intent.kind}")
    if intent.kind != move_intent.REPOSITION:
        return  # les verifications suivantes n'ont de sens que sur REPOSITION
    for voice in ("popular", "creative", "classical"):
        frag = fragment_library.fragments_for_intent(intent, voice)
        check(frag is not None, f"[{voice}] REPOSITION doit avoir un fragment, pas None")
        blob = " ".join(v for v in frag.values() if v)
        check(blob.strip() != "", f"[{voice}] REPOSITION : fragment vide")
        check("e4" in blob, f"[{voice}] le texte doit citer la case e4, obtenu {blob!r}")
        check(blob[0].islower() or blob[0].isdigit(),
              f"[{voice}] contrat de clause, minuscule initiale")
        check(not blob.rstrip().endswith("."),
              f"[{voice}] contrat de clause, pas de point final")
        # Garde contre une reintroduction : sans detecteur dedie, on ne peut
        # pas affirmer que la case d'arrivee vaut MIEUX que l'origine -- voir
        # commentaire de _frag_reposition. Aucun terme comparatif dans le
        # registre factuel (observation).
        obs_lower = frag["observation"].lower()
        for terme in ("mieux", "meilleur", "meilleure", "pire"):
            check(terme not in obs_lower,
                  f"[{voice}] l'observation ne doit pas comparer les cases (terme {terme!r}) -> {frag['observation']!r}")


def test_rook_file_dame_ne_parle_pas_de_tour():
    # ROOK_FILE se declenche pour une tour OU une dame arrivant sur une
    # colonne ouverte/semi-ouverte (voir move_intent.ROOK_FILE). Defaut
    # constate en pratique : le plan disait "les tours aiment les colonnes
    # ouvertes" meme quand c'est une DAME qui a joue -- fait invente sur une
    # piece qui n'a pas bouge. Ici la dame joue d1-d5 sur colonne d ouverte :
    # aucune voix ne doit mentionner "tour" dans le fragment.
    import chess, move_intent
    board = chess.Board("4k3/ppp2ppp/8/8/8/8/PPP2PPP/3QK3 w - - 0 1")
    intent = move_intent.detect_move_intent(
        board, {"move_uci": "d1d5", "pv_uci": ["d1d5"]})
    check(intent.kind == move_intent.ROOK_FILE,
          f"setup : attendu rook_file, obtenu {intent.kind}")
    if intent.kind != move_intent.ROOK_FILE:
        return
    for voice in ("popular", "creative", "classical"):
        frag = fragment_library.fragments_for_intent(intent, voice)
        check(frag is not None, f"[{voice}] ROOK_FILE doit avoir un fragment")
        blob = " ".join(v for v in frag.values() if v)
        check("tour" not in blob.lower(),
              f"[{voice}] la dame a joue, aucun texte ne doit parler de 'tour' -> {blob!r}")
        check("dame" in blob.lower(),
              f"[{voice}] la piece reellement jouee (dame) doit etre nommee -> {blob!r}")
        check("d5" in blob, f"[{voice}] la case d'arrivee doit etre citee -> {blob!r}")


def test_rook_file_tour_conseille_bien_doubler():
    # Cas miroir : une TOUR sur colonne ouverte -- le conseil legitime de
    # doubler les tours doit rester present, on ne doit pas avoir tout
    # neutralise en corrigeant le cas dame.
    import chess, move_intent
    board = chess.Board("4k3/ppp2ppp/8/8/8/8/PPP2PPP/3RK3 w - - 0 1")
    intent = move_intent.detect_move_intent(
        board, {"move_uci": "d1d5", "pv_uci": ["d1d5"]})
    check(intent.kind == move_intent.ROOK_FILE,
          f"setup : attendu rook_file, obtenu {intent.kind}")
    if intent.kind != move_intent.ROOK_FILE:
        return
    for voice in ("popular", "creative", "classical"):
        frag = fragment_library.fragments_for_intent(intent, voice)
        blob = " ".join(v for v in frag.values() if v)
        check("dame" not in blob.lower(),
              f"[{voice}] la tour a joue, aucun texte ne doit parler de 'dame' -> {blob!r}")
        check("tour" in blob.lower(),
              f"[{voice}] la piece reellement jouee (tour) doit etre nommee -> {blob!r}")


def test_rook_file_a_plusieurs_formulations():
    # Meme audit : rook_file n'avait AUCUNE variante alors que la tour prend
    # souvent plusieurs colonnes successives dans une partie. On verifie que
    # _pick_variant fait bien varier le texte via recent_kinds (comme pour
    # develop/reposition), de facon deterministe.
    import chess, move_intent
    board = chess.Board("4k3/ppp2ppp/8/8/8/8/PPP2PPP/3RK3 w - - 0 1")
    intent = move_intent.detect_move_intent(
        board, {"move_uci": "d1d5", "pv_uci": ["d1d5"]})
    if intent.kind != move_intent.ROOK_FILE:
        return
    def ctx_avec_historique(kinds):
        ctx = FragmentContext()
        ctx.recent_kinds = tuple(kinds)
        return ctx

    for voice in ("popular", "creative", "classical"):
        frag_frais = fragment_library.fragments_for_intent(
            intent, voice, ctx_avec_historique(()))
        frag_repete = fragment_library.fragments_for_intent(
            intent, voice, ctx_avec_historique((intent.kind,)))
        check(frag_frais != frag_repete,
              f"[{voice}] rook_file doit varier sa formulation via _pick_variant")
        # determinisme : deux appels avec le meme historique -> meme texte
        frag_repete_bis = fragment_library.fragments_for_intent(
            intent, voice, ctx_avec_historique((intent.kind,)))
        check(frag_repete == frag_repete_bis,
              f"[{voice}] meme historique -> doit redonner le meme texte")


def test_accord_genre_dans_les_fragments_dintention():
    # Garde d'ACCORD (le bug que _piece_with_article ne couvrait pas) : l'article
    # etait accorde, mais les ADJECTIFS et PRONOMS restaient en dur -- « la tour
    # adverse n'est pas defendu », « prends cette tour, personne ne peut le
    # reprendre ». On balaie les intentions qui NOMMENT une piece, pour une piece
    # feminine (tour, dame) et une masculine (cavalier, fou), dans les 3 voix.
    import re
    import move_intent as mi

    faux_si_feminin = [
        r"\ble (tour|dame)\b", r"\bce (tour|dame)\b", r"\bun (tour|dame)\b",
        r"\bdéfendu\b", r"\ble reprendre\b",
    ]
    faux_si_masculin = [
        r"\bla (cavalier|fou)\b", r"\bcette (cavalier|fou)\b", r"\bune (cavalier|fou)\b",
        r"\bdéfendue\b", r"\bla reprendre\b",
    ]
    kinds = ("capture_free", "capture_trade", "mate", "sacrifice", "gives_check",
             "develop", "rook_file", "reposition")
    # Les 3 etats de preuve de capture_free : chaque branche a ses propres
    # formulations, donc ses propres accords a verifier.
    preuves = ({}, {"capture_undefended": True}, {"capture_line_gain": True})

    for genre, pieces, motifs in (("feminin", (chess.ROOK, chess.QUEEN), faux_si_feminin),
                                  ("masculin", (chess.KNIGHT, chess.BISHOP), faux_si_masculin)):
        for piece in pieces:
            for kind in kinds:
                for preuve in preuves:
                    intent = mi.MoveIntent(
                        kind=kind, forcing=True, from_square=chess.D1, to_square=chess.D4,
                        moved_piece=piece, captured_piece=piece, **preuve)
                    for voice in VOICES:
                        frag = fragment_library.fragments_for_intent(intent, voice)
                        if frag is None:
                            continue
                        blob = " ".join(v for v in frag.values() if v)
                        for motif in motifs:
                            m = re.search(motif, blob)
                            check(m is None,
                                  f"[{voice}/{kind}/{genre}] accord casse "
                                  f"({m.group(0) if m else ''}) -> {blob!r}")


def test_colonne_semi_ouverte_nest_pas_dite_ouverte():
    # « colonne ouverte » etait ecrit en dur alors que ROOK_FILE accepte AUSSI
    # les colonnes semi-ouvertes : fait invente, reproduit sur cette position
    # (pion NOIR en d6, aucun pion blanc sur la colonne d -> half_open).
    import move_intent
    board = chess.Board("4k3/ppp2ppp/3p4/8/8/8/PPP2PPP/3RK3 w - - 0 1")
    intent = move_intent.detect_move_intent(board, {"move_uci": "d1d5", "pv_uci": ["d1d5"]})
    check(intent.kind == move_intent.ROOK_FILE, f"setup : attendu rook_file, obtenu {intent.kind}")
    check(intent.file_status == "half_open",
          f"setup : colonne d semi-ouverte attendue, obtenu {intent.file_status!r}")
    for voice in VOICES:
        for historique in ((), (intent.kind,)):  # les DEUX variantes
            ctx = FragmentContext(board=board)
            ctx.recent_kinds = historique
            frag = fragment_library.fragments_for_intent(intent, voice, ctx)
            blob = " ".join(v for v in frag.values() if v)
            check("colonne ouverte" not in blob,
                  f"[{voice}] colonne SEMI-ouverte annoncee « ouverte » -> {blob!r}")
            check("semi-ouverte" in blob,
                  f"[{voice}] le statut reel de la colonne doit etre dit -> {blob!r}")


def test_pas_de_seconde_tour_inventee():
    # Finale a UNE seule tour : aucun plan ne doit supposer qu'il en existe une
    # autre (« amene ta seconde tour », « double tes tours », « l'autre tour »).
    import move_intent
    board = chess.Board("4k3/ppp2ppp/8/8/8/8/PPP2PPP/3RK3 w - - 0 1")
    intent = move_intent.detect_move_intent(board, {"move_uci": "d1d5", "pv_uci": ["d1d5"]})
    check(intent.kind == move_intent.ROOK_FILE, f"setup : attendu rook_file, obtenu {intent.kind}")
    check(len(board.pieces(chess.ROOK, chess.WHITE)) == 1, "setup : une seule tour blanche")
    for voice in VOICES:
        for historique in ((), (intent.kind,)):
            ctx = FragmentContext(board=board)
            ctx.recent_kinds = historique
            frag = fragment_library.fragments_for_intent(intent, voice, ctx)
            blob = " ".join(v for v in frag.values() if v).lower()
            for invente in ("seconde tour", "l'autre tour", "double tes tours",
                            "double ensuite", "tes tours"):
                check(invente not in blob,
                      f"[{voice}] une seule tour sur l'echiquier, « {invente} » est invente -> {blob!r}")

    # Cas miroir : avec DEUX tours, le conseil de doubler redevient legitime.
    board2 = chess.Board("4k3/ppp2ppp/8/8/8/8/PPP2PPP/3RK2R w - - 0 1")
    intent2 = move_intent.detect_move_intent(board2, {"move_uci": "d1d5", "pv_uci": ["d1d5"]})
    ctx2 = FragmentContext(board=board2)
    ctx2.recent_kinds = (intent2.kind,)
    blob2 = " ".join(v for v in fragment_library.fragments_for_intent(intent2, "popular", ctx2).values() if v)
    check("tours" in blob2.lower(),
          f"deux tours : le plan de doublement doit rester possible -> {blob2!r}")


def test_develop_ne_conseille_pas_un_roque_impossible():
    # « developpe, puis roque » sans AUCUN droit de roque = conseil d'un coup
    # illegal. Position sans droits (champ de roque « - » dans la FEN).
    import move_intent
    board = chess.Board("4k3/8/8/8/8/8/PPP2PPP/R3KBNR w - - 0 1")
    check(not board.has_castling_rights(chess.WHITE), "setup : aucun droit de roque")
    intent = move_intent.detect_move_intent(board, {"move_uci": "f1c4", "pv_uci": ["f1c4"]})
    check(intent.kind == move_intent.DEVELOP, f"setup : attendu develop, obtenu {intent.kind}")
    for voice in VOICES:
        for historique in ((), (intent.kind,)):
            ctx = FragmentContext(board=board)
            ctx.recent_kinds = historique
            frag = fragment_library.fragments_for_intent(intent, voice, ctx)
            blob = " ".join(v for v in frag.values() if v).lower()
            check("roque" not in blob,
                  f"[{voice}] plus de droit de roque : ne pas le conseiller -> {blob!r}")


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        try:
            t()
            print(f"  ok  {t.__name__}")
        except Exception as e:  # une exception DANS un test est un échec dur
            _failures.append(f"{t.__name__} a levé {type(e).__name__}: {e}")
            print(f" ERR {t.__name__}: {e}")
    print(f"\n{len(tests)} tests executes")
    if _failures:
        print(f"[ECHEC] {len(_failures)} probleme(s) :")
        for f in _failures:
            print(f"   - {f}")
        sys.exit(1)
    print("[OK] fragment_library respecte le contrat de fragment")


if __name__ == "__main__":
    _run()
