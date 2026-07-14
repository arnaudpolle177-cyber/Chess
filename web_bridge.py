"""
web_bridge.py
Pont HTTP local entre ton site web (qui lit le plateau directement dans le
DOM en JavaScript, voir chess_coach_bridge.user.js) et le moteur Stockfish
côté Python.

Fonctionnement :
- Le JS de ta page fait, à chaque coup joué (le sien ou celui de
  l'adversaire), un POST http://127.0.0.1:8765/fen : d'abord un aperçu
  rapide {"fen": ..., "quick": true} (depth 12, quasi instantané), puis une
  requête par profil de jeu {"fen": ..., "profile": "popular"|...} au niveau
  Elo choisi.
- Chaque requête est indépendante et se termine (et met à jour sa flèche)
  dès qu'elle est prête, sans dépendre d'un streaming au fil de l'eau côté
  navigateur -- voir handle_single_profile.
- Si ce n'est pas le tour du camp choisi ("Changer de camp" dans la fenêtre
  Python), on ne calcule/n'affiche rien pour l'adversaire.

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
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import chess
import chess.engine

from engine_analysis import ChessCoachEngine
from explain import explain_move_local, explain_move_via_api
import human_profile
import opening_book
import app_paths
import theme_detector
import why_detector
import narration
import narration_v2
import opening_identity

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
                 on_profile_update=None, threads=None,
                 hash_mb=1024):
        self.stockfish_path = stockfish_path
        self.threads = threads
        self.hash_mb = hash_mb
        self.engine = ChessCoachEngine(stockfish_path, threads=threads, hash_mb=hash_mb)
        # Livre d'ouvertures (optionnel) : cherché à côté de l'exécutable
        # (même dossier que stockfish.exe), fichier "opening_book.bin". Rien
        # ne casse si le fichier est absent -- voir opening_book.py.
        book_path = os.path.join(app_paths.get_base_dir(), "opening_book.bin")
        self.opening_book = opening_book.OpeningBook(book_path)
        # Base ECO locale (nom d'ouverture + points forts/faibles rédigés à
        # la main) -- distincte du livre polyglot ci-dessus (qui donne des
        # coups, jamais de noms). Voir opening_identity.py et narration.py.
        eco_path = os.path.join(app_paths.get_base_dir(), "eco_openings.json")
        self.opening_identity = opening_identity.OpeningIdentity(eco_path)
        self.explain_mode = explain_mode
        self.on_update = on_update            # callback(lines, explanation) -> messages ponctuels (skip/erreur/fin de partie)
        self.on_profile_update = on_profile_update  # callback(profile_id, entry) -> coach "humain"
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
        # Historique des coups joués depuis le début de la partie actuelle
        # (SAN), déduit position par position par comparaison de FEN
        # successifs -- le script JS n'envoie jamais le coup joué
        # explicitement, seulement le FEN brut (voir chess_coach_bridge.
        # user.js). Utilisé par opening_identity.py pour identifier
        # l'ouverture en cours. Reset dès qu'on détecte un retour à la
        # position de départ (voir _update_move_history ci-dessous).
        self._move_history = []
        self._move_history_board = None  # dernière position connue de l'historique (chess.Board)
        # Historique GLISSANT des évals "à mon tour" (voir theme_detector.py,
        # INITIATIVE_SHIFT) -- toujours du point de vue de my_side, jamais
        # remis à zéro par position_seq (contrairement à _opponent_turn_eval,
        # qui ne regarde qu'UN coup adverse) : cette liste couvre plusieurs
        # de mes propres tours pour détecter une TENDANCE, pas un instantané.
        # maxlen borne la mémoire automatiquement (deque) -- pas besoin de
        # trim manuel. Remise à zéro sur nouvelle partie (voir
        # _update_move_history) ET sur changement de camp (voir
        # set_my_side) -- une éval "de mon point de vue" n'a plus de sens
        # cohérent si le camp change en cours de route.
        self._initiative_history = deque(maxlen=theme_detector.INITIATIVE_WINDOW)
        # Numéro de séquence (voir _position_seq) du DERNIER ply déjà ajouté
        # à _initiative_history -- garde-fou anti double-comptage : un
        # Rafraîchir ou un changement d'Elo relance handle_single_profile sur
        # la MÊME position (même current_seq) en invalidant le cache de
        # candidats, ce qui re-déclenche _update_eval_tracking_and_theme. Sans
        # ce garde-fou, on réajoutait alors le même ply dans la fenêtre
        # glissante -> la régression linéaire croyait avoir 2-3 tours
        # distincts identiques et faussait silencieusement INITIATIVE_SHIFT
        # (pente artificiellement aplatie). L'append n'avance donc que sur un
        # ply réellement nouveau (current_seq différent).
        self._initiative_last_seq = None
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

        # --- Coaching : mémoire d'éval avant/après (voir theme_detector.py) ---
        # Compteur incrémenté à CHAQUE nouvelle position (mon tour OU celui
        # de l'adversaire). Sert de garde-fou
        # anti-péremption pour _opponent_turn_eval ci-dessous : le calcul
        # léger tourne en tâche de fond (voir _track_opponent_eval) et peut
        # prendre du temps si le moteur est occupé sur une analyse lourde
        # -- sans ce compteur, un résultat arrivé en retard pourrait se
        # retrouver associé à la MAUVAISE transition de position (faussant
        # silencieusement la détection de blunder) si l'adversaire a joué
        # très vite ou si 2 coups se sont enchaînés entre-temps.
        self._position_seq = 0
        # Éval légère (une seule ligne, profondeur modeste) prise pendant
        # le tour de l'ADVERSAIRE (voir _track_opponent_eval) -- sert à
        # mesurer l'ampleur d'un éventuel blunder/imprécision adverse une
        # fois que c'est de nouveau mon tour. Point de vue : camp adverse
        # (celui au trait au moment de la mesure). Contient aussi le
        # meilleur coup qu'IL avait de disponible à ce moment-là (notation
        # SAN, point de vue adverse) -- utilisé pour citer précisément ce
        # qu'il aurait pu jouer de plus fort (voir MISSED_OPPORTUNITY).
        # Volontairement PAS de suivi de "mon" propre coup manqué : le
        # coach affiche toujours le coup à jouer via les flèches, un joueur
        # qui les suit ne peut pas vraiment manquer son propre coup -- ce
        # suivi ne concerne donc que l'adversaire. Stocké avec le numéro
        # de séquence de la position mesurée (voir _position_seq) -- jamais
        # utilisé seul, uniquement via self._opponent_turn_eval (tuple).
        self._opponent_turn_eval = None  # (position_seq, cp, move_san) | None
        # Thème détecté pour la DERNIÈRE position -- calculé UNE SEULE fois
        # par position (voir theme_detector.py : "même thème, 3
        # philosophies différentes", pas 3 détections indépendantes) et
        # réutilisé par les 3 requêtes de profil qui arrivent pour cette
        # même position.
        self._theme_cache_key = None    # fen
        self._theme_cache_value = None  # theme_detector.ThemeResult
        # Narration v2 (voir narration_v2.py / NARRATION_V2_PLAN.txt) : cache
        # de la SÉLECTION (1 principal + 0..2 secondaires), profil-indépendante,
        # calculée une fois par position au même endroit que _theme_cache
        # ci-dessus et partagée par les 3 profils -- seul le tissage (render)
        # varie par profil. Coexiste avec _theme_cache pendant la transition :
        # generate_narration (façade v1) reste disponible en repli si la
        # sélection v2 manque (cache miss concurrent, cf. handle_single_profile).
        self._selection_cache = narration_v2.SelectionCache()

    def _track_opponent_eval(self, fen, board, position_seq):
        """
        Appelé quand c'est le tour de l'ADVERSAIRE (voir handle_single_profile,
        branche "skip") : une évaluation LÉGÈRE (1 seule ligne, profondeur
        modeste -- pas les 4-5 candidats complets, pas la peine ici) pour
        pouvoir mesurer, une fois que c'est de nouveau mon tour, si son
        coup a changé l'éval de façon significative (BLUNDER/
        MISSED_OPPORTUNITY, voir theme_detector.py). Best-effort : une
        erreur ici ne doit jamais bloquer l'affichage du message "au tour
        de l'adversaire".

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
                top = result["candidates"][0]
                self._opponent_turn_eval = (position_seq, top["cp"], top.get("move_san"))
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
        l'ignore simplement (swing_cp reste None, pas de thème basé sur
        l'éval adverse pour ce coup-ci, plutôt qu'une détection basée sur
        de mauvaises données).
        """
        try:
            if not candidates:
                return
            current_eval = candidates[0]["cp"]  # point de vue de mon camp
            if current_eval is None:
                # Coup de livre (voir opening_book.py) -- pas de vraie éval
                # ici, donc pas de swing_cp fiable à calculer non plus. On
                # n'ajoute PAS ce point à _initiative_history non plus
                # (donnée non fiable, fausserait la tendance) -- la fenêtre
                # glissante saute simplement ce point, comme un trou dans
                # la série plutôt qu'une valeur inventée.
                # Repli sur le thème "neutre" (eval_cp=0 par défaut dans
                # theme_detector.detect_theme) plutôt que de laisser une
                # comparaison numérique planter sur None (voir le
                # commentaire équivalent dans theme_detector.py).
                try:
                    theme_result = theme_detector.detect_theme(board, candidates, move_history=list(self._move_history))
                except Exception as e:
                    print(f"⚠ Détection de thème en erreur (coup de livre), repli neutre : {e}")
                    theme_result = theme_detector.ThemeResult(theme_detector.EQUAL_POSITION, 0)
                self._theme_cache_value = theme_result
                self._theme_cache_key = fen
                # Narration v2 : sélection sans signaux d'éval (coup de livre),
                # comme le thème v1 juste au-dessus. Best-effort.
                try:
                    self._selection_cache.set(fen, narration_v2.build_selection(board, candidates))
                except Exception as e:
                    print(f"⚠ Sélection narration v2 indisponible (coup de livre) : {e}")
                self._opponent_turn_eval = None
                return

            # N'avance la fenêtre glissante que sur un ply RÉELLEMENT nouveau
            # (voir _initiative_last_seq) : un Rafraîchir / changement d'Elo
            # re-déclenche cette fonction sur la même position (même
            # current_seq) et ne doit PAS réajouter le même point d'éval.
            if current_seq != self._initiative_last_seq:
                self._initiative_history.append(current_eval)
                self._initiative_last_seq = current_seq
            initiative_trend = theme_detector.compute_initiative_trend(list(self._initiative_history))

            opponent_eval_cp = None
            opponent_better_move_san = None
            stored = self._opponent_turn_eval
            if stored is not None:
                stored_seq, stored_cp, stored_move_san = stored
                if stored_seq == current_seq - 1:
                    opponent_eval_cp = stored_cp
                    opponent_better_move_san = stored_move_san
                # sinon : périmé (arrivé en retard ou plusieurs coups en
                # retard) -- on l'ignore silencieusement.

            swing_cp = None
            if opponent_eval_cp is not None:
                # opponent_eval_cp est du point de vue de l'adversaire
                # (c'était son tour au moment de la mesure) -> on le
                # convertit de mon point de vue en le négant, puis on
                # compare à l'éval actuelle.
                swing_cp = current_eval - (-opponent_eval_cp)

            try:
                theme_result = theme_detector.detect_theme(
                    board, candidates, swing_cp=swing_cp, opponent_better_move_san=opponent_better_move_san,
                    initiative_trend=initiative_trend, move_history=list(self._move_history),
                )
            except Exception as e:
                # Filet de sécurité : MÊME en cas d'erreur imprévue ici (pas
                # seulement le cas cp=None déjà géré plus haut), le cache
                # est mis à jour avec un thème neutre pour CETTE position --
                # jamais laissé tel quel. Sans ça, une seule position qui
                # fait planter detect_theme (pour n'importe quelle raison,
                # même une qu'on n'a pas encore rencontrée) figeait le
                # thème affiché sur une position bien plus ancienne, pour
                # TOUTES les positions suivantes de la partie -- exactement
                # le bug observé en pratique (thème "Finale" affiché en
                # plein début de partie, jamais mis à jour ensuite).
                print(f"⚠ Détection de thème en erreur, repli neutre pour cette position : {e}")
                theme_result = theme_detector.ThemeResult(theme_detector.EQUAL_POSITION, current_eval or 0)

            # Valeur écrite AVANT la clé (pas l'inverse) : si un autre thread
            # lit ce cache pile entre les 2 lignes, il verra soit l'ancienne
            # paire clé/valeur cohérente, soit la nouvelle -- jamais une
            # clé qui pointe déjà vers la nouvelle position alors que la
            # valeur est encore l'ancienne (ce qui aurait pu faire utiliser
            # le mauvais thème pour la mauvaise position).
            self._theme_cache_value = theme_result
            self._theme_cache_key = fen

            # Narration v2 : même position, mêmes signaux -> on calcule aussi
            # la SÉLECTION (principal + secondaires) et on la cache. Purement
            # additif : aucun appel moteur (collect_theme_bricks est du pur
            # python-chess), calcul best-effort qui ne doit jamais empêcher la
            # mise à jour du thème v1 ci-dessus si quelque chose casse ici.
            try:
                selection = narration_v2.build_selection(
                    board, candidates, swing_cp=swing_cp,
                    opponent_better_move_san=opponent_better_move_san,
                    initiative_trend=initiative_trend,
                )
                self._selection_cache.set(fen, selection)
            except Exception as e:
                print(f"⚠ Sélection narration v2 indisponible pour ce coup : {e}")

            # Avance la mémoire pour le prochain cycle.
            self._opponent_turn_eval = None
        except Exception as e:
            print(f"⚠ Détection de thème indisponible pour ce coup : {e}")

    def set_my_side(self, side):
        """side: 'w' ou 'b'."""
        with self.lock:
            self.my_side = side
            # Une éval "de mon point de vue" dans _initiative_history n'a
            # plus de sens cohérent si le camp change en cours de route --
            # sans ce reset, un changement de camp mi-partie ferait passer
            # une pente calculée pour Blancs comme si elle décrivait Noirs.
            self._initiative_history.clear()
            # Remis à None en même temps que le clear : sinon le prochain ply
            # (dont le current_seq peut coïncider avec _initiative_last_seq
            # d'avant le reset) serait sauté par le garde-fou anti
            # double-comptage, laissant la fenêtre vide un tour de trop.
            self._initiative_last_seq = None

    def set_elo_tier(self, tier_id):
        """tier_id : 1, 2 ou 3 (voir human_profile.ELO_TIERS)."""
        if tier_id not in human_profile.ELO_TIERS:
            return
        with self.lock:
            self.elo_tier_id = tier_id

    def _restart_engine(self, reason=""):
        """
        Redémarre Stockfish après un crash (processus tué, plantage interne,
        etc.). Sans ça, une seule mort du moteur rendait TOUT le pont
        inutilisable jusqu'au redémarrage complet du programme.

        IMPORTANT (perf) : cette méthode est appelée DEPUIS L'INTÉRIEUR de
        engine_lock (voir handle_single_profile). Fermer proprement
        l'ancien moteur (self.engine.close()) peut bloquer une dizaine de
        secondes si le processus est déjà mort mais que la librairie attend
        quand même une réponse UCI avant d'abandonner -- pendant tout ce
        temps, engine_lock resterait tenu et TOUT le pont semblerait figé
        (c'était la cause du freeze de ~15s après un crash). On fait donc
        cette fermeture dans un thread séparé, SANS l'attendre : peu importe
        qu'elle prenne du temps, ça ne bloque plus rien d'autre.
        """
        print(f"⚠ Le moteur semble avoir crashé, redémarrage... ({reason})")

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

    def _update_move_history(self, fen, board):
        """
        Déduit le coup joué entre la dernière position CONNUE de
        l'historique et `fen` (nouvelle position, déjà confirmée stable
        côté JS), en essayant chaque coup légal depuis cette ancienne
        position jusqu'à retrouver exactement le même plateau de pièces.
        Fiable tant qu'un seul coup a été joué entre 2 lectures (garanti
        par la détection de stabilité du script JS). Best-effort : ne doit
        jamais bloquer l'affichage des flèches si la déduction échoue pour
        une raison quelconque (ex: 1er coup de la partie, désynchronisation
        ponctuelle).
        """
        try:
            board_part_new = fen.split(" ", 1)[0]

            # Retour à la position de départ = nouvelle partie -> on repart
            # d'un historique vierge (même logique que resetTrackingState
            # côté JS, mais le serveur Python n'a aucun autre moyen de le
            # savoir).
            if board_part_new == chess.STARTING_BOARD_FEN:
                if self._move_history:
                    self._move_history = []
                self._move_history_board = None
                self._initiative_history.clear()
                self._initiative_last_seq = None  # cohérent avec le clear (voir set_my_side)
                return

            if self._move_history_board is None:
                # Rien à comparer (tout premier coup vu depuis le
                # démarrage du serveur, ou après un reset) -- on initialise
                # juste le point de départ, sans coup à déduire encore.
                self._move_history_board = board.copy()
                return

            old_board = self._move_history_board
            for legal_move in old_board.legal_moves:
                test_board = old_board.copy()
                test_board.push(legal_move)
                if test_board.board_fen() == board_part_new:
                    self._move_history.append(old_board.san(legal_move))
                    self._move_history_board = test_board
                    return

            # Aucun coup légal ne mène à cette position -- désynchronisation
            # (ex: 2 coups joués très vite entre 2 lectures). On
            # resynchronise silencieusement sur la position actuelle plutôt
            # que de laisser l'historique corrompu ou de planter.
            self._move_history_board = board.copy()
        except Exception:
            pass  # cosmétique (identification d'ouverture) -- jamais bloquant

    def handle_single_profile(self, fen, profile_id):
        """
        Analyse la position pour UN SEUL profil de jeu ("popular",
        "creative", "classical" -- voir human_profile.py) au niveau Elo
        actuellement sélectionné, et retourne directement un dict : une
        requête HTTP par flèche, pas de streaming.
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
            self._update_move_history(fen, board)

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
                # Filet de repli RARE (cache miss inattendu, ex: requêtes
                # concurrentes arrivant avant que _theme_cache_key soit
                # posé) -- LIMITATION CONNUE : n'a accès ni à swing_cp ni à
                # initiative_trend ici (contrairement au chemin normal, voir
                # _update_eval_tracking_and_theme), donc BLUNDER/
                # MISSED_OPPORTUNITY/INITIATIVE_SHIFT ne peuvent pas se
                # déclencher via ce chemin pour CETTE position précise --
                # dégradation ponctuelle et sans risque (retombe proprement
                # sur les thèmes suivants dans PRIORITY_ORDER), pas une
                # narration fausse. Volontairement pas corrigé : dupliquer
                # ici l'alimentation de _initiative_history risquerait un
                # double-ajout si ce chemin s'exécute en concurrence avec
                # le chemin normal, ce qui fausserait la pente calculée --
                # pire qu'une fonctionnalité simplement absente une fois.
                else theme_detector.detect_theme(board, result["candidates"], move_history=list(self._move_history))
            )
            why_motif, why_detail = why_detector.detect_why(board, chosen)
            # engine_lock requis ici : narration.generate_narration() peut
            # interroger le moteur pour la trajectoire d'éval du scénario
            # (voir variation_narrator.py) -- le moteur ne supporte qu'une
            # recherche à la fois, jamais d'appel moteur hors de ce verrou.
            with self.engine_lock:
                entry["narration"] = narration.generate_narration(
                    theme_result, profile_id, chosen, why_motif, why_detail, board,
                    move_history=list(self._move_history), opening_book=self.opening_identity,
                    engine=self.engine,
                )

            # Narration v2 (transition) : ajoute le PARAGRAPHE tissé (une seule
            # pensée de 2-4 phrases, principal + secondaires) au dict
            # d'affichage. Le front (webview_ui.py, renderDetail) l'utilise EN
            # PRIORITÉ s'il est présent, et retombe sinon sur
            # label1/text1/label2/text2 (façade v1 ci-dessus) -- transition
            # douce, réversible. Aucun appel moteur ici (build_selection/render
            # sont purs python-chess) -> volontairement HORS engine_lock.
            # Best-effort : si ça casse, la narration v1 reste affichée.
            try:
                selection = self._selection_cache.get(fen)
                if selection is None:
                    # Repli RARE (même cas de cache miss que le thème v1
                    # ci-dessus) : sans signaux swing/initiative, comme la façade.
                    selection = narration_v2.build_selection(board, result["candidates"])
                woven = narration_v2.render(
                    selection, profile_id, chosen=chosen,
                    why_motif=why_motif, why_detail=why_detail, board=board,
                )
                if woven.get("text"):
                    entry["narration"]["paragraph"] = woven["text"]
                    # Aligne l'en-tête (icône + libellé) sur le thème PRINCIPAL
                    # du paragraphe, pour que le titre colle à ce qui est écrit
                    # (le principal v2 = plus haut score, pas toujours identique
                    # au 1er thème de PRIORITY_ORDER retourné par detect_theme).
                    lead = woven.get("lead")
                    if lead:
                        entry["narration"]["theme_label"] = narration.THEME_LABELS_FR.get(
                            lead, entry["narration"].get("theme_label"))
                        entry["narration"]["theme_icon"] = narration.THEME_ICONS.get(
                            lead, entry["narration"].get("theme_icon"))
            except Exception as e:
                print(f"⚠ Paragraphe narration v2 indisponible ({profile_id}) : {e}")
        except Exception as e:
            print(f"⚠ Narration indisponible pour ce coup ({profile_id}) : {e}")

        if self.on_profile_update:
            self.on_profile_update(profile_id, dict(entry))

        return entry

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
                profile = data.get("profile")  # un seul profil "humain"
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
            else:
                # Requête "brute" (sans 'profile' ni 'quick') : ne devrait
                # plus jamais arriver avec le .user.js à jour. Typiquement le
                # signe qu'un ancien script (chess_coach_bridge.js) est encore
                # chargé sur la page en plus du .user.js Tampermonkey. On log
                # un avertissement explicite pour le repérer facilement plutôt
                # que de traiter la requête.
                print(
                    "⚠ Requête /fen reçue SANS champ 'profile' ni 'quick' -- "
                    "vérifie qu'un ancien script (chess_coach_bridge.js) n'est "
                    "pas encore chargé sur la page en plus du .user.js Tampermonkey."
                )
                self._send_single(409, {
                    "error": "Requête sans 'profile' ni 'quick' -- mode non reconnu "
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

        def log_message(self, format, *args):
            pass  # silence les logs HTTP dans la console

    return Handler


def start_bridge_server(stockfish_path, explain_mode="local", on_update=None,
                         on_profile_update=None, port=DEFAULT_PORT, threads=None, hash_mb=1024):
    """
    Démarre le serveur en tâche de fond (thread daemon) et retourne
    (server, state). Appelle state.close() pour bien fermer Stockfish
    à la fin.
    """
    state = BridgeState(
        stockfish_path, explain_mode=explain_mode, on_update=on_update,
        on_profile_update=on_profile_update,
        threads=threads, hash_mb=hash_mb,
    )
    handler_cls = _make_handler(state)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"🌉 Pont navigateur démarré : http://127.0.0.1:{port}/fen")
    print("   Colle chess_coach_bridge.user.js dans Tampermonkey pour connecter ton site.")
    return server, state
