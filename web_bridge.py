"""
web_bridge.py
Pont HTTP local entre ton site web (qui lit le plateau directement dans le
DOM en JavaScript, voir chess_coach_bridge.user.js) et le moteur Stockfish
côté Python.

Remplace complètement la capture d'écran + reconnaissance d'image : le
navigateur connaît déjà la position exacte (c'est lui qui l'affiche), donc
il n'y a plus aucune erreur de reconnaissance possible.

Fonctionnement :
- Le JS de ta page fait un POST http://127.0.0.1:8765/fen avec {"fen": "..."}
  à chaque coup joué (le sien ou celui de l'adversaire).
- Ce serveur interroge Stockfish en streaming (un seul passage
  d'approfondissement itératif) et répond en NDJSON (une ligne JSON par
  palier de profondeur atteint : 10, puis 15, puis 20 par défaut), au fil de
  l'eau plutôt que d'attendre la fin complète de l'analyse.
- Le JS utilise chaque ligne pour dessiner/mettre à jour la flèche
  correspondante (vert = depth 10, bleu = depth 15, rouge = depth 20).
- Si ce n'est pas le tour du camp choisi ("Changer de camp" dans la fenêtre
  Python), on ne calcule/n'affiche rien pour l'adversaire.
- En parallèle, le dernier résultat (profondeur max atteinte) est aussi
  transmis à la petite fenêtre Tkinter existante (texte + explication).
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
                 threads=None, hash_mb=256, depth=None):
        self.stockfish_path = stockfish_path
        self.threads = threads
        self.hash_mb = hash_mb
        # Si --depth est passé explicitement en CLI, on retombe sur un seul
        # palier (comportement classique, 1 seule flèche). Sinon, mode
        # progressif par défaut (10/15/20).
        self.depths = (depth,) if depth is not None else PROGRESSIVE_DEPTHS
        self.engine = ChessCoachEngine(stockfish_path, threads=threads, hash_mb=hash_mb)
        self.explain_mode = explain_mode
        self.on_update = on_update  # callback(lines, explanation) -> ex: overlay.update_content
        self.lock = threading.Lock()
        self.last_fen = None
        # Camp pour lequel on veut des conseils ("w" ou "b"). Quand ce n'est
        # pas le tour de ce camp, on ignore l'analyse (pas la peine de
        # montrer les meilleurs coups pour l'adversaire, ni de solliciter
        # Stockfish pour rien).
        self.my_side = "w"

    def set_my_side(self, side):
        """side: 'w' ou 'b'."""
        with self.lock:
            self.my_side = side

    def _restart_engine(self):
        """
        Redémarre Stockfish après un crash (processus tué, plantage interne,
        etc.). Sans ça, une seule mort du moteur rendait TOUT le pont
        inutilisable jusqu'au redémarrage complet du programme.
        """
        print("⚠ Le moteur Stockfish semble avoir crashé, redémarrage...")
        try:
            self.engine.close()
        except Exception:
            pass  # déjà mort, pas grave
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
        introuvable/cassé).
        """
        try:
            yield from self.engine.analyze_fen_progressive(fen, depths=self.depths)
        except chess.engine.EngineError:
            self._restart_engine()
            yield from self.engine.analyze_fen_progressive(fen, depths=self.depths)

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
        with self.lock:
            try:
                board = chess.Board(fen)
            except ValueError as e:
                yield {"error": f"FEN invalide reçu du navigateur : {e}"}
                return

            self.last_fen = fen

            side_to_move = "w" if board.turn else "b"
            if side_to_move != self.my_side:
                # Pas mon tour : on ne calcule/n'affiche rien pour le camp
                # adverse. last_fen reste stocké pour le bouton "Rafraîchir".
                if self.on_update:
                    camp = "Blancs" if self.my_side == "w" else "Noirs"
                    self.on_update(None, f"Au tour de l'adversaire (tu joues les {camp}).")
                yield {"skip": True}
                return

            if board.is_game_over():
                if self.on_update:
                    self.on_update(None, f"Partie terminée : {board.result()}")
                yield {"game_over": True, "result": board.result()}
                return

            try:
                deepest_seen = None
                for entry in self._progressive_with_auto_restart(fen):
                    deepest_seen = entry
                    yield entry
            except Exception as e:
                yield {"error": f"Moteur Stockfish indisponible : {e}"}
                return

            # Une fois le palier le plus profond atteint, on calcule
            # l'explication en langage clair et on met à jour la fenêtre
            # Tkinter (une seule fois, pas à chaque palier -- inutile de
            # regénérer l'explication 3 fois pour la même position).
            if deepest_seen is not None:
                move_obj = chess.Move.from_uci(deepest_seen["move_uci"])
                if self.explain_mode == "api":
                    explanation = explain_move_via_api(
                        fen, deepest_seen["move_san"], deepest_seen["pv_san"], deepest_seen["score"]
                    )
                else:
                    explanation = explain_move_local(board, move_obj, deepest_seen["pv_san"])

                if self.on_update:
                    self.on_update([deepest_seen], explanation)

    def refresh_last(self):
        """
        Relance l'analyse sur la dernière position reçue (utilisé par le
        bouton "Rafraîchir"). Ne renvoie rien au navigateur (c'est un
        rafraîchissement de la fenêtre Tkinter uniquement) : on consomme le
        générateur nous-mêmes pour que on_update soit appelé.
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
            except Exception:
                self._send_single(400, {"error": "JSON invalide, champ \"fen\" attendu"})
                return

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


def start_bridge_server(stockfish_path, explain_mode="local", on_update=None, port=DEFAULT_PORT,
                         threads=None, hash_mb=256, depth=None):
    """
    Démarre le serveur en tâche de fond (thread daemon) et retourne
    (server, state). Appelle state.close() pour bien fermer Stockfish
    à la fin.
    """
    state = BridgeState(
        stockfish_path, explain_mode=explain_mode, on_update=on_update,
        threads=threads, hash_mb=hash_mb, depth=depth,
    )
    handler_cls = _make_handler(state)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"🌉 Pont navigateur démarré : http://127.0.0.1:{port}/fen")
    print("   Colle chess_coach_bridge.user.js dans Tampermonkey pour connecter ton site.")
    return server, state
