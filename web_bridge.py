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
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import chess
import chess.engine

from engine_analysis import ChessCoachEngine, PROGRESSIVE_DEPTHS
from explain import explain_move_local, explain_move_via_api

DEFAULT_PORT = 8765


class BridgeState:
    """État partagé entre le serveur HTTP et le reste du programme."""

    def __init__(self, stockfish_path, explain_mode="local", on_update=None,
                 on_depth_update=None, threads=None, hash_mb=256, depth=None):
        self.stockfish_path = stockfish_path
        self.threads = threads
        self.hash_mb = hash_mb
        # Si --depth est passé explicitement en CLI, on retombe sur un seul
        # palier (comportement classique, 1 seule flèche). Sinon, mode
        # progressif par défaut (10/15/20).
        self.depths = (depth,) if depth is not None else PROGRESSIVE_DEPTHS
        self.engine = ChessCoachEngine(stockfish_path, threads=threads, hash_mb=hash_mb)
        self.explain_mode = explain_mode
        self.on_update = on_update            # callback(lines, explanation) -> messages ponctuels (skip/erreur/fin de partie)
        self.on_depth_update = on_depth_update  # callback(depth, entry) -> une ligne progressive
        # self.lock protège UNIQUEMENT les petites variables d'état
        # ci-dessous (jamais tenu pendant le calcul Stockfish ou l'écriture
        # réseau, contrairement à avant).
        self.lock = threading.Lock()
        # self.engine_lock garantit qu'un seul thread parle à Stockfish à la
        # fois (le moteur ne supporte qu'une recherche à la fois).
        self.engine_lock = threading.Lock()
        self.last_fen = None
        self.my_side = "w"
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

    def set_my_side(self, side):
        """side: 'w' ou 'b'."""
        with self.lock:
            self.my_side = side

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
        print(f"⚠ Le moteur Stockfish semble avoir crashé, redémarrage... ({reason})")
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
        print("✅ Stockfish redémarré.")

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
        except chess.engine.EngineError as e:
            # On garde la VRAIE raison (type + message de l'exception) dans
            # les logs -- avant, le message était toujours générique et ne
            # permettait pas de savoir POURQUOI Stockfish était mort.
            self._restart_engine(reason=f"{type(e).__name__}: {e}")
            yield from self.engine.analyze_fen_progressive(
                fen, depths=self.depths, on_analysis_started=self._register_active_analysis
            )
        finally:
            self._clear_active_analysis()

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
            except chess.engine.EngineError as e:
                self._restart_engine(reason=f"{type(e).__name__}: {e}")
                try:
                    entry = _run_once()
                except Exception as e2:
                    return {"error": f"Moteur Stockfish indisponible : {e2}", "depth": depth}
            except Exception as e:
                return {"error": f"Moteur Stockfish indisponible : {e}", "depth": depth}
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
        Relance l'analyse sur la dernière position reçue (utilisé par le
        bouton "Rafraîchir"). On consomme le générateur nous-mêmes pour que
        les callbacks (on_update / on_depth_update) soient appelés.
        """
        if self.last_fen is None:
            if self.on_update:
                self.on_update(None, "Aucune position reçue pour l'instant depuis ton site.")
            return
        for _ in self.handle_fen_stream(self.last_fen):
            pass

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
                depth = data.get("depth")  # optionnel : requête à un seul palier
            except Exception:
                self._send_single(400, {"error": "JSON invalide, champ \"fen\" attendu"})
                return

            if depth is not None:
                # Nouveau mode : une requête = un seul palier, réponse
                # classique en un bloc (pas de streaming -> pas de risque de
                # buffering côté navigateur).
                result = state.handle_single_depth(fen, int(depth))
                self._send_single(200, result)
            else:
                # Ancien mode streaming, conservé pour compatibilité (ex:
                # utilisé en interne par le bouton "Rafraîchir").
                self._stream(state.handle_fen_stream(fen))

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
                         port=DEFAULT_PORT, threads=None, hash_mb=256, depth=None):
    """
    Démarre le serveur en tâche de fond (thread daemon) et retourne
    (server, state). Appelle state.close() pour bien fermer Stockfish
    à la fin.
    """
    state = BridgeState(
        stockfish_path, explain_mode=explain_mode, on_update=on_update,
        on_depth_update=on_depth_update, threads=threads, hash_mb=hash_mb, depth=depth,
    )
    handler_cls = _make_handler(state)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"🌉 Pont navigateur démarré : http://127.0.0.1:{port}/fen")
    print("   Colle chess_coach_bridge.user.js dans Tampermonkey pour connecter ton site.")
    return server, state
