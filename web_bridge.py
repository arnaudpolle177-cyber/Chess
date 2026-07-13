"""
web_bridge.py
Pont HTTP local entre ton site web (qui lit le plateau directement dans le
DOM en JavaScript, voir chess_coach_bridge.user.js) et le moteur Stockfish
côté Python.

Fonctionnement :
- Le JS de ta page fait un POST http://127.0.0.1:8765/fen avec {"fen": "..."}
  à chaque coup joué (le sien ou celui de l'adversaire).
- Ce serveur interroge Stockfish en streaming (un seul passage
  d'approfondissement itératif) et répond en NDJSON (une ligne JSON par
  palier de profondeur atteint : 10, puis 15, puis 20 par défaut), au fil de
  l'eau plutôt que d'attendre la fin complète de l'analyse.
- Si ce n'est pas le tour du camp choisi ("Changer de camp" dans la fenêtre
  Python), on ne calcule/n'affiche rien pour l'adversaire.
- Chaque palier reçu met aussi à jour, en direct, la ligne correspondante
  dans la fenêtre Tkinter (on_depth_update), et l'explication finale est
  ajoutée une fois le palier le plus profond atteint.

Annulation des analyses obsolètes (important) :
- Si une nouvelle position arrive alors qu'une analyse précédente tourne
  encore, on NE LA MET PAS EN ATTENTE derrière un verrou (ça faisait
  s'accumuler des threads bloqués au fil d'une partie -> plantage à la
  longue). On demande activement à Stockfish d'arrêter la recherche en
  cours (analysis.stop()) : l'ancienne analyse, devenue inutile de toute
  façon, se termine alors quasi instantanément et libère la place pour la
  nouvelle.
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import chess
import chess.engine

from engine_analysis import ChessCoachEngine, PROGRESSIVE_DEPTHS
from explain import explain_move_local, explain_move_via_api
import human_profile
import opening_book
import app_paths
import theme_detector
import why_detector
import narration

DEFAULT_PORT = 8765
# Aperçu rapide (depth 12, quasi instantané) : calculé UNE fois pour les 4
# profils d'un coup (comme le cache principal), affiché immédiatement côté
# navigateur pendant que la vraie analyse (au niveau Elo choisi, plus
# profonde) tourne derrière et vient remplacer l'affichage dès qu'elle est
# prête. Voir handle_quick_take() plus bas.
QUICK_DEPTH = 12
QUICK_MULTIPV = 3


class BridgeState:
    """État partagé entre le serveur HTTP et le reste du programme."""

    def __init__(self, stockfish_path, explain_mode="local", on_update=None,
                 on_depth_update=None, on_profile_update=None, threads=None,
                 hash_mb=1024, depth=None):
        self.stockfish_path = stockfish_path
        self.threads = threads
        self.hash_mb = hash_mb
        # Si --depth est passé explicitement en CLI, on retombe sur un seul
        # palier (comportement classique, 1 seule flèche). Sinon, mode
        # progressif par défaut (10/15/20).
        self.depths = (depth,) if depth is not None else PROGRESSIVE_DEPTHS
        self.engine = ChessCoachEngine(stockfish_path, threads=threads, hash_mb=hash_mb)
        # Livre d'ouvertures (optionnel) : cherché à côté de l'exécutable
        # (même dossier que stockfish.exe), fichier "opening_book.bin". Rien
        # ne casse si le fichier est absent -- voir opening_book.py.
        book_path = os.path.join(app_paths.get_base_dir(), "opening_book.bin")
        self.opening_book = opening_book.OpeningBook(book_path)
        self.explain_mode = explain_mode
        self.on_update = on_update            # callback(lines, explanation) -> messages ponctuels (skip/erreur/fin de partie)
        self.on_depth_update = on_depth_update  # callback(depth, entry) -> ancien mode (profondeur brute), conservé pour compatibilité
        self.on_profile_update = on_profile_update  # callback(profile_id, entry) -> nouveau mode (coach "humain")
        # self.lock protège UNIQUEMENT les petites variables d'état
        # ci-dessous (jamais tenu pendant le calcul Stockfish ou l'écriture
        # réseau, contrairement à avant).
        self.lock = threading.Lock()
        # self.engine_lock garantit qu'un seul thread parle à Stockfish à la
        # fois (le moteur ne supporte qu'une recherche à la fois).
        self.engine_lock = threading.Lock()
        self.last_fen = None
        # Séparé de self.last_fen : dernière position vue par
        # handle_single_profile SPÉCIFIQUEMENT (pas mise à jour par
        # handle_quick_take). BUG RÉEL corrigé ici : handle_quick_take met
        # self.last_fen à jour AVANT que les vraies requêtes de profil
        # n'arrivent (l'aperçu rapide part toujours en premier, par
        # design -- voir chess_coach_bridge.user.js). Si is_new_position
        # dans handle_single_profile comparait à self.last_fen, il ne
        # voyait alors quasiment plus JAMAIS une position comme "nouvelle"
        # (déjà marquée par l'aperçu rapide entre-temps) -- cassant en
        # silence le message "au tour de l'adversaire" ET le suivi d'éval
        # pour le coaching. self.last_fen reste utilisé tel quel pour les
        # vérifications de péremption (staleness), où on veut justement la
        # position la plus récente vue par N'IMPORTE QUEL gestionnaire.
        self._last_profile_fen = None
        self.my_side = "w"
        # Niveau Elo actif (voir human_profile.ELO_TIERS). Change le style
        # de jeu proposé par les 4 profils, PAS juste la profondeur -- voir
        # human_profile.py pour le détail.
        self.elo_tier_id = human_profile.DEFAULT_ELO_TIER
        # Cache des coups candidats (MultiPV) pour la DERNIÈRE position
        # analysée. Les 4 profils (vert/bleu/rose/N&B) arrivent en 4
        # requêtes HTTP séparées pour LA MÊME position -- sans ce cache,
        # chacune relancerait sa propre analyse MultiPV complète (4x le
        # travail du moteur pour rien, et 4x plus de risque de backlog si
        # les coups s'enchaînent vite). Protégé par engine_lock (jamais lu/
        # écrit hors de ce verrou).
        self._candidates_cache_key = None      # (fen, elo_tier_id)
        self._candidates_cache_value = None    # (result_dict, board)
        # Cache SÉPARÉ pour l'aperçu rapide (depth 12) -- ne doit surtout
        # pas se mélanger avec le cache principal ci-dessus (qui est à la
        # profondeur du niveau Elo choisi) : sinon la "vraie" requête
        # pourrait par erreur réutiliser le résultat rapide et peu profond.
        self._quick_cache_key = None           # fen
        self._quick_cache_value = None         # liste de candidats (depth 12)
        # Même principe pour le moteur PRINCIPAL (analyse multi-candidats) :
        # certaines positions très tactiques/déséquilibrées font planter la
        # recherche multi-lignes de Stockfish de façon reproductible (crash
        # observé en pratique, jamais lié au CPU). Une position qui a déjà
        # fait planter l'analyse complète (multipv=3) bascule directement
        # sur une analyse dégradée mais fiable (multipv=1, profondeur
        # réduite) au lieu de retenter la même config qui replanterait --
        # les 4 profils partagent alors le même coup pour cette position
        # précise, plutôt que de rester bloqués sans rien afficher.
        self._main_engine_degraded = set()
        # Référence vers l'analyse Stockfish actuellement en cours (objet
        # chess.engine.AnalysisResult), pour pouvoir l'interrompre depuis un
        # autre thread si une position plus récente arrive.
        self.active_analysis = None
        # Incrémenté à chaque nouvelle position prise en charge. Permet à un
        # générateur "en retard" (dont l'analyse a été annulée mais qui n'a
        # pas encore eu l'occasion de s'arrêter -- ex: en train d'écrire un
        # dernier résultat au moment de l'annulation) de se rendre compte
        # qu'il est obsolète et de s'arrêter immédiatement, SANS appeler
        # on_depth_update ni continuer à écrire sur la connexion HTTP. Sans
        # ça, des résultats d'une position déjà dépassée pouvaient encore
        # arriver et écraser/mélanger l'affichage avec la position actuelle
        # (c'était la cause du bug "seule la ligne depth 20 s'affiche").
        self.generation = 0

        # --- Coaching : mémoire d'éval avant/après (voir theme_detector.py) ---
        # Compteur incrémenté à CHAQUE nouvelle position (mon tour OU celui
        # de l'adversaire, contrairement à self.generation ci-dessus qui
        # n'est utilisé que par l'ancien mode streaming). Sert de garde-fou
        # anti-péremption pour _opponent_turn_eval ci-dessous : le calcul
        # léger tourne en tâche de fond (voir _track_opponent_eval) et peut
        # prendre du temps si le moteur est occupé sur une analyse lourde
        # -- sans ce compteur, un résultat arrivé en retard pourrait se
        # retrouver associé à la MAUVAISE transition de position (faussant
        # silencieusement la détection de blunder) si l'adversaire a joué
        # très vite ou si 2 coups se sont enchaînés entre-temps.
        self._position_seq = 0
        # Éval objective (candidates[0]["cp"], point de vue de mon camp) la
        # DERNIÈRE fois que c'était mon tour, AVANT que je ne rejoue --
        # sert à mesurer si MON dernier coup a perdu du terrain
        # (MISSED_OPPORTUNITY). Mis à jour à la fin de chaque analyse
        # complète pour mon tour.
        self._prev_my_eval_cp = None
        # Éval légère (une seule ligne, profondeur modeste) prise pendant
        # le tour de l'ADVERSAIRE (voir _track_opponent_eval) -- sert à
        # mesurer l'ampleur d'un éventuel blunder adverse une fois que
        # c'est de nouveau mon tour. Point de vue : camp adverse (celui au
        # trait au moment de la mesure). Stocké avec le numéro de séquence
        # de la position mesurée (voir _position_seq) -- jamais utilisé
        # seul, uniquement via self._opponent_turn_eval (tuple).
        self._opponent_turn_eval = None  # (position_seq, cp) | None
        # Thème détecté pour la DERNIÈRE position -- calculé UNE SEULE fois
        # par position (voir theme_detector.py : "même thème, 3
        # philosophies différentes", pas 3 détections indépendantes) et
        # réutilisé par les 3 requêtes de profil qui arrivent pour cette
        # même position.
        self._theme_cache_key = None    # fen
        self._theme_cache_value = None  # theme_detector.ThemeResult

    def _track_opponent_eval(self, fen, board, position_seq):
        """
        Appelé quand c'est le tour de l'ADVERSAIRE (voir handle_single_profile,
        branche "skip") : une évaluation LÉGÈRE (1 seule ligne, profondeur
        modeste -- pas les 4-5 candidats complets, pas la peine ici) pour
        pouvoir mesurer, une fois que c'est de nouveau mon tour, si son
        coup a changé l'éval de façon significative (BLUNDER, voir
        theme_detector.py). Best-effort : une erreur ici ne doit jamais
        bloquer l'affichage du message "au tour de l'adversaire".

        `position_seq` : capturé au moment de l'appel (voir _position_seq),
        pour que le consommateur (_update_eval_tracking_and_theme) puisse
        vérifier que ce résultat correspond bien à la position IMMÉDIATEMENT
        précédente, et pas à une mesure arrivée en retard sur une position
        déjà dépassée -- voir le commentaire sur _opponent_turn_eval dans
        __init__.
        """
        try:
            with self.engine_lock:
                result, _ = self.engine.analyze_candidates(fen, multipv=1, depth=12)
            if result.get("candidates"):
                self._opponent_turn_eval = (position_seq, result["candidates"][0]["cp"])
        except Exception:
            pass  # cosmétique (juste pour la narration) -- jamais bloquant

    def _update_eval_tracking_and_theme(self, fen, board, candidates, current_seq):
        """
        Appelé UNE SEULE fois par nouvelle position où c'est mon tour (sur
        cache miss de _candidates_cache, voir _run_candidates ci-dessous) :
        calcule le thème partagé de cette position (voir theme_detector.py
        -- un seul thème, réutilisé par les 3 profils) à partir de la
        mémoire d'éval, puis avance cette mémoire pour le prochain cycle.
        Best-effort : ne doit jamais empêcher l'affichage des flèches si
        ça échoue pour une raison quelconque.

        `current_seq` : numéro de séquence de CETTE position (voir
        _position_seq) -- sert à vérifier que _opponent_turn_eval
        correspond bien à la position IMMÉDIATEMENT précédente (seq - 1)
        avant de l'utiliser. Le suivi tournant en tâche de fond (voir
        _track_opponent_eval), un résultat en retard pourrait sinon être
        associé par erreur à la mauvaise transition de position (ex: coups
        joués très vite, ou moteur occupé sur une analyse lourde) --
        faussant silencieusement la détection de blunder. Si périmé, on
        l'ignore simplement (swing_cp reste None, pas de BLUNDER détecté
        pour ce coup-ci, plutôt qu'une détection basée sur de mauvaises
        données).
        """
        try:
            if not candidates:
                return
            current_eval = candidates[0]["cp"]  # point de vue de mon camp

            opponent_eval_cp = None
            stored = self._opponent_turn_eval
            if stored is not None:
                stored_seq, stored_cp = stored
                if stored_seq == current_seq - 1:
                    opponent_eval_cp = stored_cp
                # sinon : périmé (arrivé en retard ou plusieurs coups en
                # retard) -- on l'ignore silencieusement.

            swing_cp = None
            if opponent_eval_cp is not None:
                # opponent_eval_cp est du point de vue de l'adversaire
                # (c'était son tour au moment de la mesure) -> on le
                # convertit de mon point de vue en le négant, puis on
                # compare à l'éval actuelle.
                swing_cp = current_eval - (-opponent_eval_cp)

            my_move_quality_cp = None
            if opponent_eval_cp is not None and self._prev_my_eval_cp is not None:
                # Écart entre mon éval AVANT de jouer (prev_my_eval_cp) et
                # l'éval juste après mon coup, avant que l'adversaire ne
                # réponde (opponent_eval_cp, reconverti de mon point de
                # vue) -- mesure la qualité de MON dernier coup réellement
                # joué (pas forcément celui suggéré par un profil).
                my_move_quality_cp = (-opponent_eval_cp) - self._prev_my_eval_cp

            theme_result = theme_detector.detect_theme(
                board, candidates, swing_cp=swing_cp, my_move_quality_cp=my_move_quality_cp,
            )
            # Valeur écrite AVANT la clé (pas l'inverse) : si un autre thread
            # lit ce cache pile entre les 2 lignes, il verra soit l'ancienne
            # paire clé/valeur cohérente, soit la nouvelle -- jamais une
            # clé qui pointe déjà vers la nouvelle position alors que la
            # valeur est encore l'ancienne (ce qui aurait pu faire utiliser
            # le mauvais thème pour la mauvaise position).
            self._theme_cache_value = theme_result
            self._theme_cache_key = fen

            # Avance la mémoire pour le prochain cycle (mon prochain tour).
            self._prev_my_eval_cp = current_eval
            self._opponent_turn_eval = None
        except Exception as e:
            print(f"⚠ Détection de thème indisponible pour ce coup : {e}")

    def set_my_side(self, side):
        """side: 'w' ou 'b'."""
        with self.lock:
            self.my_side = side

    def set_elo_tier(self, tier_id):
        """tier_id : 1, 2 ou 3 (voir human_profile.ELO_TIERS)."""
        if tier_id not in human_profile.ELO_TIERS:
            return
        with self.lock:
            self.elo_tier_id = tier_id

    def _register_active_analysis(self, analysis):
        with self.lock:
            self.active_analysis = analysis

    def _clear_active_analysis(self):
        with self.lock:
            self.active_analysis = None

    def _cancel_current_analysis_if_any(self):
        """
        Demande à Stockfish d'arrêter la recherche en cours, si une analyse
        tourne déjà (elle est de toute façon obsolète : une position plus
        récente vient d'arriver). Ne bloque pas : c'est juste une demande.
        """
        with self.lock:
            analysis = self.active_analysis
        if analysis is not None:
            try:
                analysis.stop()
            except Exception:
                pass  # déjà arrêtée / moteur mort -> pas grave

    def _restart_engine(self, reason=""):
        """
        Redémarre Stockfish après un crash (processus tué, plantage interne,
        etc.). Sans ça, une seule mort du moteur rendait TOUT le pont
        inutilisable jusqu'au redémarrage complet du programme.

        IMPORTANT (perf) : cette méthode est appelée DEPUIS L'INTÉRIEUR de
        engine_lock (voir _progressive_with_auto_restart). Fermer proprement
        l'ancien moteur (self.engine.close()) peut bloquer une dizaine de
        secondes si le processus est déjà mort mais que la librairie attend
        quand même une réponse UCI avant d'abandonner -- pendant tout ce
        temps, engine_lock resterait tenu et TOUT le pont semblerait figé
        (c'était la cause du freeze de ~15s après un crash). On fait donc
        cette fermeture dans un thread séparé, SANS l'attendre : peu importe
        qu'elle prenne du temps, ça ne bloque plus rien d'autre.
        """
        print(f"⚠ Le moteur semble avoir crashé, redémarrage... ({reason})")
        self._clear_active_analysis()

        old_engine = self.engine

        def _cleanup_old_engine():
            try:
                old_engine.close()
            except Exception:
                pass  # déjà mort, pas grave -- on essaie juste par propreté

        threading.Thread(target=_cleanup_old_engine, daemon=True).start()

        self.engine = ChessCoachEngine(
            self.stockfish_path, threads=self.threads, hash_mb=self.hash_mb
        )
        print("✅ Moteur redémarré.")

    def _progressive_with_auto_restart(self, fen):
        """
        Générateur : comme analyze_fen_progressive, mais relance Stockfish
        UNE fois et reprend l'analyse depuis le début si le moteur meurt en
        cours de route (EngineError). Si ça replante une 2e fois, laisse
        l'exception remonter (pas de boucle infinie si Stockfish est
        introuvable/cassé). Enregistre aussi l'analyse en cours pour
        permettre son annulation externe (voir _cancel_current_analysis_if_any).
        """
        try:
            yield from self.engine.analyze_fen_progressive(
                fen, depths=self.depths, on_analysis_started=self._register_active_analysis
            )
        except Exception as e:  # pas seulement EngineError : python-chess peut aussi lever d'autres erreurs (ex: IllegalMoveError) sur une réponse moteur corrompue
            # On garde la VRAIE raison (type + message de l'exception) dans
            # les logs -- avant, le message était toujours générique et ne
            # permettait pas de savoir POURQUOI Stockfish était mort.
            self._restart_engine(reason=f"{type(e).__name__}: {e}")
            yield from self.engine.analyze_fen_progressive(
                fen, depths=self.depths, on_analysis_started=self._register_active_analysis
            )
        finally:
            self._clear_active_analysis()

    def handle_quick_take(self, fen):
        """
        Version rapide (depth 12, quasi instantanée) des 4 profils en UNE
        seule requête -- affichée immédiatement côté navigateur pendant que
        la vraie analyse (plus profonde, au niveau Elo choisi) tourne
        derrière. Ne calcule PAS l'avis Elo-bridé (pour rester rapide) --
        les profils "populaire"/"classique" s'en passent juste pour cet
        aperçu, ils l'auront dans la vraie réponse qui suit.
        """
        try:
            board = chess.Board(fen)
        except ValueError as e:
            return {"quick": True, "error": f"FEN invalide reçu du navigateur : {e}"}

        with self.lock:
            self.last_fen = fen  # l'aperçu rapide arrive en premier, avant les vraies requêtes profil
            my_side = self.my_side
            elo_tier_id = self.elo_tier_id

        side_to_move = "w" if board.turn else "b"
        if side_to_move != my_side:
            return {"quick": True, "skip": True}
        if board.is_game_over():
            return {"quick": True, "game_over": True, "result": board.result()}

        with self.engine_lock:
            if self._quick_cache_key == fen:
                candidates = self._quick_cache_value
            else:
                self._register_active_analysis(None)
                # Réutilise la même liste que l'analyse principale (voir
                # handle_single_profile) : si cette position est déjà
                # connue pour faire planter le mode natif, on passe direct
                # en mode sûr (recherches successives) plutôt que de la
                # retenter en natif pour rien.
                is_degraded = fen in self._main_engine_degraded

                def _run_quick(mpv, safe):
                    result, brd = self.engine.analyze_candidates(fen, multipv=mpv, depth=QUICK_DEPTH, safe_mode=safe)
                    return result

                try:
                    result = _run_quick(QUICK_MULTIPV, is_degraded)
                except Exception as e:  # pas seulement EngineError : python-chess peut aussi lever d'autres erreurs (ex: IllegalMoveError) sur une réponse moteur corrompue
                    self._restart_engine(reason=f"{type(e).__name__}: {e}")
                    if fen not in self._main_engine_degraded and len(self._main_engine_degraded) < 500:
                        self._main_engine_degraded.add(fen)
                    try:
                        result = _run_quick(1, True)  # repli direct sur le mode sûr après un crash
                    except Exception as e2:
                        return {"quick": True, "error": f"Moteur Stockfish indisponible : {e2}"}
                finally:
                    self._clear_active_analysis()

                if result.get("game_over"):
                    return {"quick": True, "game_over": True, "result": result["result"]}
                candidates = result["candidates"]
                self._quick_cache_key = fen
                self._quick_cache_value = candidates

        profiles_out = {}
        for profile_id in human_profile.PROFILE_IDS:
            chosen = human_profile.select_move(
                candidates, elo_tier_id, profile_id, board=board,
            )
            if chosen is not None:
                profiles_out[profile_id] = {
                    "move_uci": chosen["move_uci"],
                    "move_san": chosen["move_san"],
                    "score": chosen["score"],
                    "pv_san": chosen["pv_san"],
                }
        return {"quick": True, "profiles": profiles_out}

    def handle_single_profile(self, fen, profile_id):
        """
        Analyse la position pour UN SEUL profil de jeu ("popular",
        "creative", "classical" -- voir human_profile.py) au niveau Elo
        actuellement sélectionné, et retourne directement un dict (même
        pattern que handle_single_depth : une requête HTTP par flèche, pas
        de streaming).
        """
        try:
            board = chess.Board(fen)
        except ValueError as e:
            return {"error": f"FEN invalide reçu du navigateur : {e}", "profile": profile_id}

        with self.lock:
            is_new_position = fen != self._last_profile_fen
            self._last_profile_fen = fen
            self.last_fen = fen  # toujours mis à jour aussi (péremption, voir plus bas)
            my_side = self.my_side
            elo_tier_id = self.elo_tier_id
            if is_new_position:
                self._position_seq += 1
            current_seq = self._position_seq

        if is_new_position:
            self._cancel_current_analysis_if_any()

        side_to_move = "w" if board.turn else "b"
        if side_to_move != my_side:
            if is_new_position:
                if self.on_update:
                    camp = "Blancs" if my_side == "w" else "Noirs"
                    self.on_update(None, f"Au tour de l'adversaire (tu joues les {camp}).")
                # Suivi léger de l'éval pendant le tour adverse -- voir
                # theme_detector.py (BLUNDER). Une seule fois par position
                # (les 3 requêtes de profil arrivent toutes ici pour LA
                # MÊME position adverse) -- déclenché en tâche de fond pour
                # ne pas retarder la réponse HTTP (aucune flèche à
                # afficher de toute façon tant que c'est son tour).
                threading.Thread(
                    target=self._track_opponent_eval, args=(fen, board, current_seq), daemon=True,
                ).start()
            return {"skip": True, "profile": profile_id}

        if board.is_game_over():
            if is_new_position and self.on_update:
                self.on_update(None, f"Partie terminée : {board.result()}")
            return {"game_over": True, "result": board.result(), "profile": profile_id}

        tier = human_profile.ELO_TIERS[elo_tier_id]

        with self.engine_lock:
            with self.lock:
                if fen != self.last_fen:
                    return {"stale": True, "profile": profile_id}  # position dépassée entre-temps

            def _run_candidates():
                cache_key = (fen, elo_tier_id)
                if self._candidates_cache_key == cache_key:
                    # Déjà calculé pour un autre profil sur cette même
                    # position/niveau -- on réutilise, pas de nouvel appel
                    # Stockfish.
                    return self._candidates_cache_value

                # 1. Livre d'ouvertures d'abord (gratuit en performance : la
                #    position elle-même dit si on est encore "dans la
                #    théorie" -- aucun compteur de coups à tenir à jour, et
                #    ça marche pareil que ce soit MON coup ou celui d'un
                #    adversaire qui vient de jouer). Si la position n'y est
                #    pas (ou pas de livre chargé), bascule silencieusement
                #    sur Stockfish juste en dessous.
                book_entries = self.opening_book.lookup(board)
                if book_entries:
                    book_candidates = opening_book.candidates_from_book_entries(
                        board, book_entries, max_candidates=tier.multipv
                    )
                    if book_candidates:
                        result = {"game_over": False, "candidates": book_candidates}
                        self._candidates_cache_value = result  # valeur avant clé, même raison que le cache de thème plus haut
                        self._candidates_cache_key = cache_key
                        self._update_eval_tracking_and_theme(fen, board, book_candidates, current_seq)
                        return result

                # 2. Hors théorie (ou pas de livre) -> Stockfish comme avant.
                self._register_active_analysis(None)  # pas d'objet analysis() streamé ici, rien à annuler en cours de route
                is_degraded = fen in self._main_engine_degraded
                try:
                    result, brd = self.engine.analyze_candidates(
                        fen, multipv=tier.multipv, depth=tier.random_depth(), safe_mode=is_degraded,
                    )
                except Exception as e:  # pas seulement EngineError : python-chess peut aussi lever d'autres erreurs (ex: IllegalMoveError) sur une réponse moteur corrompue
                    self._restart_engine(reason=f"{type(e).__name__}: {e}")
                    if fen not in self._main_engine_degraded:
                        print(f"⚠ Position basculée en mode sûr (recherches successives) pour le moteur principal (crash) : {fen}")
                        if len(self._main_engine_degraded) < 500:  # borne de sécurité
                            self._main_engine_degraded.add(fen)
                    # On retente en mode SÛR (recherches successives,
                    # profondeur plus modeste) -- beaucoup moins de risque
                    # de crash que la recherche multi-lignes native qui
                    # vient de planter. Si même ÇA replante, l'exception
                    # remonte normalement jusqu'au bloc try/except autour
                    # de _run_candidates() (voir plus bas), qui retourne
                    # une erreur propre pour CETTE requête plutôt que de
                    # planter tout le serveur.
                    result, brd = self.engine.analyze_candidates(
                        fen, multipv=tier.multipv, depth=min(tier.random_depth(), 14), safe_mode=True,
                    )
                self._candidates_cache_value = result  # valeur avant clé, même raison que le cache de thème plus haut
                self._candidates_cache_key = cache_key
                self._update_eval_tracking_and_theme(fen, board, result["candidates"], current_seq)
                return result

            try:
                result = _run_candidates()
            except Exception as e:  # pas seulement EngineError : python-chess peut aussi lever d'autres erreurs (ex: IllegalMoveError) sur une réponse moteur corrompue
                self._restart_engine(reason=f"{type(e).__name__}: {e}")
                try:
                    result = _run_candidates()
                except Exception as e2:
                    return {"error": f"Moteur Stockfish indisponible : {e2}", "profile": profile_id}
            finally:
                self._clear_active_analysis()

        with self.lock:
            stale = fen != self.last_fen
        if stale:
            return {"stale": True, "profile": profile_id}
        if result.get("game_over"):
            return {"game_over": True, "result": result["result"], "profile": profile_id}

        chosen = human_profile.select_move(
            result["candidates"], elo_tier_id, profile_id, board=board,
        )
        if chosen is None:
            return {"error": "Aucun coup candidat trouvé pour cette position.", "profile": profile_id}

        entry = {
            "profile": profile_id,
            "move_uci": chosen["move_uci"],
            "move_san": chosen["move_san"],
            "score": chosen["score"],
            "pv_san": chosen["pv_san"],
        }

        # Narration : thème partagé (calculé une seule fois par position,
        # voir _update_eval_tracking_and_theme) + justification propre au
        # coup de CE profil (voir why_detector.py) + gabarit selon la
        # personnalité du profil (voir narration.py). Chaque profil a donc
        # sa propre narration -- contrairement à l'ancienne "explication"
        # calculée une seule fois pour le dernier profil de la boucle.
        try:
            theme_result = (
                self._theme_cache_value if self._theme_cache_key == fen
                else theme_detector.detect_theme(board, result["candidates"])
            )
            why_motif, why_detail = why_detector.detect_why(board, chosen)
            entry["narration"] = narration.generate_narration(
                theme_result, profile_id, chosen, why_motif, why_detail, board,
            )
        except Exception as e:
            print(f"⚠ Narration indisponible pour ce coup ({profile_id}) : {e}")

        if self.on_profile_update:
            self.on_profile_update(profile_id, dict(entry))

        return entry

    def handle_single_depth(self, fen, depth):
        """
        Analyse la position pour UN SEUL palier de profondeur, et retourne
        directement un dict (pas un générateur/stream). Le navigateur envoie
        maintenant une requête HTTP séparée par palier (10, puis 15, puis
        20) plutôt qu'une seule requête "streamée" : certains navigateurs/
        gestionnaires d'extensions ne délivrent PAS les données au fil de
        l'eau (onprogress) malgré un vrai streaming NDJSON côté serveur --
        ils attendent la fin complète de la connexion avant de tout donner
        d'un coup. En séparant en plusieurs requêtes HTTP indépendantes, on
        s'appuie sur une garantie beaucoup plus fiable : chaque requête se
        termine (et déclenche son propre callback JS) dès qu'ELLE est prête,
        indépendamment des autres -- ce n'est plus qu'un détail d'implé-
        mentation fragile d'un navigateur particulier.
        """
        try:
            board = chess.Board(fen)
        except ValueError as e:
            return {"error": f"FEN invalide reçu du navigateur : {e}", "depth": depth}

        with self.lock:
            is_new_position = fen != self.last_fen
            self.last_fen = fen
            my_side = self.my_side

        if is_new_position:
            # Une position DIFFÉRENTE de la précédente vient d'arriver :
            # toute recherche encore active pour l'ancienne position est
            # désormais inutile, on lui demande de s'arrêter. On ne fait PAS
            # ça pour les requêtes soeurs (10/15/20) d'une même position,
            # qui doivent au contraire se succéder tranquillement derrière
            # engine_lock.
            self._cancel_current_analysis_if_any()

        side_to_move = "w" if board.turn else "b"
        if side_to_move != my_side:
            if is_new_position and self.on_update:
                camp = "Blancs" if my_side == "w" else "Noirs"
                self.on_update(None, f"Au tour de l'adversaire (tu joues les {camp}).")
            return {"skip": True, "depth": depth}

        if board.is_game_over():
            if is_new_position and self.on_update:
                self.on_update(None, f"Partie terminée : {board.result()}")
            return {"game_over": True, "result": board.result(), "depth": depth}

        def _run_once():
            gen = self.engine.analyze_fen_progressive(
                fen, depths=(depth,), on_analysis_started=self._register_active_analysis
            )
            try:
                return next(gen, None)
            finally:
                gen.close()

        with self.engine_lock:
            with self.lock:
                if fen != self.last_fen:
                    return {"stale": True, "depth": depth}  # position dépassée entre-temps, pas la peine

            try:
                entry = _run_once()
            except Exception as e:  # pas seulement EngineError : python-chess peut aussi lever d'autres erreurs (ex: IllegalMoveError) sur une réponse moteur corrompue
                self._restart_engine(reason=f"{type(e).__name__}: {e}")
                try:
                    entry = _run_once()
                except Exception as e2:
                    return {"error": f"Moteur Stockfish indisponible : {e2}", "depth": depth}
            finally:
                self._clear_active_analysis()

        with self.lock:
            stale = fen != self.last_fen
        if stale or entry is None:
            return {"stale": True, "depth": depth}
        if entry.get("game_over"):
            return entry

        # Explication en langage clair uniquement pour le palier le plus
        # profond demandé (pas la peine de la recalculer 3x).
        if depth == max(self.depths):
            move_obj = chess.Move.from_uci(entry["move_uci"])
            if self.explain_mode == "api":
                explanation = explain_move_via_api(
                    fen, entry["move_san"], entry["pv_san"], entry["score"]
                )
            else:
                explanation = explain_move_local(board, move_obj, entry["pv_san"])
            entry = dict(entry)
            entry["explanation"] = explanation

        if self.on_depth_update:
            self.on_depth_update(entry["depth"], dict(entry))

        return entry

    def handle_fen_stream(self, fen):
        """
        Générateur consommé par le serveur HTTP pour écrire la réponse au
        fil de l'eau (NDJSON, une ligne JSON par item produit) :
        - soit un seul message {"error": ...}
        - soit un seul message {"game_over": True, "result": ...}
        - soit un seul message {"skip": True} (pas le tour du camp choisi)
        - soit une série de messages {"depth": 10/15/20, "move_uci": ...,
          "move_san": ..., "score": ..., "pv_san": [...]}
        """
        try:
            board = chess.Board(fen)
        except ValueError as e:
            yield {"error": f"FEN invalide reçu du navigateur : {e}"}
            return

        with self.lock:
            self.last_fen = fen
            my_side = self.my_side
            self.generation += 1
            my_gen = self.generation

        side_to_move = "w" if board.turn else "b"
        if side_to_move != my_side:
            # Pas mon tour : on ne calcule/n'affiche rien pour le camp
            # adverse. last_fen reste stocké pour le bouton "Rafraîchir".
            if self.on_update:
                camp = "Blancs" if my_side == "w" else "Noirs"
                self.on_update(None, f"Au tour de l'adversaire (tu joues les {camp}).")
            yield {"skip": True}
            return

        if board.is_game_over():
            if self.on_update:
                self.on_update(None, f"Partie terminée : {board.result()}")
            yield {"game_over": True, "result": board.result()}
            return

        # Une position plus récente vient d'arriver : si une ancienne
        # analyse tourne encore, on lui demande de s'arrêter tout de suite
        # au lieu d'attendre passivement derrière elle.
        self._cancel_current_analysis_if_any()

        with self.engine_lock:
            # Une position ENCORE plus récente est peut-être arrivée pendant
            # qu'on attendait le verrou moteur (plusieurs coups très rapides)
            # -> on abandonne avant même de commencer, inutile de calculer
            # pour une position déjà dépassée.
            with self.lock:
                if my_gen != self.generation:
                    return

            # IMPORTANT : générateur géré explicitement (pas juste "for entry
            # in self._progressive_with_auto_restart(fen)") pour pouvoir
            # appeler .close() nous-mêmes de façon garantie sur CHAQUE
            # chemin de sortie (via le "finally" plus bas), y compris un
            # `return` anticipé pour position obsolète. Sans ça, un `return`
            # en plein milieu de la boucle laisse la fermeture au ramasse-
            # miettes -- généralement rapide sur CPython, mais pas garanti,
            # et surtout pas synchrone avec la libération d'engine_lock :
            # Stockfish pouvait recevoir un nouveau "go" pour la position
            # suivante avant que le "stop" de l'ancienne recherche soit
            # vraiment traité, ce qui est une violation du protocole UCI et
            # une cause plausible des plantages observés.
            gen = self._progressive_with_auto_restart(fen)
            try:
                deepest_seen = None
                for entry in gen:
                    # Vérifié À CHAQUE palier, pas juste au début : une
                    # analyse déjà "annulée" (analysis.stop() appelé) peut
                    # continuer à produire 1-2 résultats de transition avant
                    # de s'arrêter vraiment. Sans ce contrôle, ces résultats
                    # obsolètes pouvaient s'afficher/se mélanger avec ceux de
                    # la position actuelle -> c'était la cause du bug où
                    # certaines lignes de profondeur ne s'affichaient jamais.
                    with self.lock:
                        if my_gen != self.generation:
                            return
                    deepest_seen = entry
                    if self.on_depth_update:
                        self.on_depth_update(entry["depth"], dict(entry))
                    yield entry
            except Exception as e:
                yield {"error": f"Moteur Stockfish indisponible : {e}"}
                return
            finally:
                gen.close()

            # Une fois le palier le plus profond atteint, on calcule
            # l'explication en langage clair une seule fois (pas à chaque
            # palier) et on la transmet accolée à l'entrée la plus profonde.
            with self.lock:
                stale = my_gen != self.generation
            if deepest_seen is not None and not stale:
                move_obj = chess.Move.from_uci(deepest_seen["move_uci"])
                if self.explain_mode == "api":
                    explanation = explain_move_via_api(
                        fen, deepest_seen["move_san"], deepest_seen["pv_san"], deepest_seen["score"]
                    )
                else:
                    explanation = explain_move_local(board, move_obj, deepest_seen["pv_san"])

                if self.on_depth_update:
                    final_entry = dict(deepest_seen)
                    final_entry["explanation"] = explanation
                    self.on_depth_update(deepest_seen["depth"], final_entry)

    def refresh_last(self):
        """
        LEGACY (ancien mode profondeur brute). Conservé seulement pour
        compatibilité --depth CLI. Le bouton "Rafraîchir" de l'UI utilise
        maintenant refresh_last_profiles() ci-dessous, PAS cette méthode :
        celle-ci ignore le niveau Elo et retombe toujours sur
        analyze_fen_progressive (profondeur fixe), ce qui la rend lente et
        incohérente avec le slider -- c'était justement la cause du bug où
        "Rafraîchir" semblait ignorer le niveau Elo choisi.
        """
        if self.last_fen is None:
            if self.on_update:
                self.on_update(None, "Aucune position reçue pour l'instant depuis ton site.")
            return
        for _ in self.handle_fen_stream(self.last_fen):
            pass

    def refresh_last_profiles(self):
        """
        Relance les 4 profils sur la dernière position reçue, au niveau Elo
        actuel -- c'est la vraie méthode derrière le bouton "Rafraîchir"
        dans main.py. Invalide d'abord le cache de candidats pour forcer un
        vrai recalcul (sinon, si rien n'a changé, on retomberait juste sur
        le cache existant sans se re-synchroniser après un souci moteur).
        """
        if self.last_fen is None:
            if self.on_update:
                self.on_update(None, "Aucune position reçue pour l'instant depuis ton site.")
            return
        with self.lock:
            self._candidates_cache_key = None
        for profile_id in human_profile.PROFILE_IDS:
            self.handle_single_profile(self.last_fen, profile_id)

    def close(self):
        try:
            self.engine.close()
        except Exception:
            pass


def _make_handler(state: BridgeState):
    class Handler(BaseHTTPRequestHandler):
        def _cors_headers(self):
            # Autorise les requêtes venant de n'importe quelle origine (ton
            # site tourne sur un domaine différent de "localhost").
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            # Requis par les navigateurs récents (Chrome/Edge) pour autoriser
            # une page web "publique" à contacter une adresse locale
            # (127.0.0.1) : sans ce header, la requête est bloquée en
            # silence par le navigateur, même si le serveur répond bien à
            # curl/Postman (règle de sécurité "Private Network Access").
            self.send_header("Access-Control-Allow-Private-Network", "true")

        def do_OPTIONS(self):
            self.send_response(204)
            self._cors_headers()
            self.end_headers()

        def do_POST(self):
            if self.path != "/fen":
                self.send_response(404)
                self._cors_headers()
                self.end_headers()
                return

            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
                fen = data["fen"]
                depth = data.get("depth")      # ancien mode : un seul palier de profondeur
                profile = data.get("profile")  # nouveau mode : un seul profil "humain"
                quick = data.get("quick", False)  # aperçu rapide (depth 12) des 4 profils d'un coup
            except Exception:
                self._send_single(400, {"error": "JSON invalide, champ \"fen\" attendu"})
                return

            if quick:
                result = state.handle_quick_take(fen)
                self._send_single(200, result)
            elif profile is not None:
                result = state.handle_single_profile(fen, profile)
                self._send_single(200, result)
            elif depth is not None:
                # Mode "un seul palier de profondeur", conservé pour
                # compatibilité (plus utilisé par défaut par le .user.js,
                # mais toujours fonctionnel si besoin).
                result = state.handle_single_depth(fen, int(depth))
                self._send_single(200, result)
            else:
                # Requête "brute" (ni profile, ni depth) : ne devrait plus
                # jamais arriver avec le .user.js à jour. On NE relance
                # PLUS silencieusement l'ancien mode streaming ici -- c'est
                # justement ce chemin (multipv=1, profondeur fixe) qui
                # provoquait des crashs répétés en tournant EN PARALLÈLE du
                # nouveau système de profils sur le même moteur partagé
                # (típicamente : 2 scripts actifs en même temps sur la
                # page, l'ancien chess_coach_bridge.js ET le nouveau
                # chess_coach_bridge.user.js). On log un avertissement
                # explicite pour le repérer facilement plutôt que de
                # laisser planter le moteur en silence.
                print(
                    "⚠ Requête /fen reçue SANS champ 'profile' ni 'depth' -- "
                    "vérifie qu'un ancien script (chess_coach_bridge.js) n'est "
                    "pas encore chargé sur la page en plus du .user.js Tampermonkey."
                )
                self._send_single(409, {
                    "error": "Requête sans 'profile' ni 'depth' -- ancien mode désactivé "
                             "(voir la console Python pour plus de détails)."
                })

        def _send_single(self, status, payload):
            """Réponse classique en un seul bloc (utilisée pour les erreurs de requête)."""
            try:
                self.send_response(status)
                self._cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode("utf-8"))
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                pass

        def _stream(self, generator):
            """
            Écrit chaque item du générateur comme une ligne JSON (NDJSON),
            au fil de l'eau. Pas de Content-Length (on ne connaît pas la
            taille finale à l'avance) : on ferme la connexion à la fin pour
            signaler la fin des données au navigateur.
            """
            try:
                self.send_response(200)
                self._cors_headers()
                self.send_header("Content-Type", "application/x-ndjson")
                self.close_connection = True
                self.end_headers()
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                generator.close()
                return

            for payload in generator:
                try:
                    self.wfile.write((json.dumps(payload) + "\n").encode("utf-8"))
                    self.wfile.flush()
                except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                    # Le navigateur a fermé la connexion en cours de route
                    # (ex: nouveau coup joué avant la fin de l'analyse, ou
                    # timeout côté client) -> on arrête le générateur, ce qui
                    # interrompt Stockfish en cours plutôt que de continuer à
                    # calculer pour rien.
                    generator.close()
                    return

        def log_message(self, format, *args):
            pass  # silence les logs HTTP dans la console

    return Handler


def start_bridge_server(stockfish_path, explain_mode="local", on_update=None, on_depth_update=None,
                         on_profile_update=None, port=DEFAULT_PORT, threads=None, hash_mb=1024, depth=None):
    """
    Démarre le serveur en tâche de fond (thread daemon) et retourne
    (server, state). Appelle state.close() pour bien fermer Stockfish
    à la fin.
    """
    state = BridgeState(
        stockfish_path, explain_mode=explain_mode, on_update=on_update,
        on_depth_update=on_depth_update, on_profile_update=on_profile_update,
        threads=threads, hash_mb=hash_mb, depth=depth,
    )
    handler_cls = _make_handler(state)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"🌉 Pont navigateur démarré : http://127.0.0.1:{port}/fen")
    print("   Colle chess_coach_bridge.user.js dans Tampermonkey pour connecter ton site.")
    return server, state
