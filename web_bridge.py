"""
web_bridge.py
Pont HTTP local entre ton site web (qui lit le plateau directement dans le
DOM en JavaScript, voir chess_coach_bridge.js) et le moteur Stockfish côté
Python.

Remplace complètement la capture d'écran + reconnaissance d'image : le
navigateur connaît déjà la position exacte (c'est lui qui l'affiche), donc
il n'y a plus aucune erreur de reconnaissance possible.

Fonctionnement :
- Le JS de ta page fait un POST http://127.0.0.1:8765/fen avec {"fen": "..."}
  à chaque coup joué (le sien ou celui de l'adversaire).
- Ce serveur interroge Stockfish (multipv=3) et répond en JSON avec les 3
  meilleurs coups + une explication en langage clair pour le meilleur.
- Le JS utilise cette réponse pour dessiner des flèches directement sur
  l'échiquier de la page.
- En parallèle, si demandé, le résultat est aussi transmis à la petite
  fenêtre Tkinter existante (texte + explication), pour avoir les deux.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import chess

from engine_analysis import ChessCoachEngine
from explain import explain_move_local, explain_move_via_api

DEFAULT_PORT = 8765


class BridgeState:
    """État partagé entre le serveur HTTP et le reste du programme."""

    def __init__(self, stockfish_path, explain_mode="local", on_update=None):
        self.engine = ChessCoachEngine(stockfish_path)
        self.explain_mode = explain_mode
        self.on_update = on_update  # callback(lines, explanation) -> ex: overlay.update_content
        self.lock = threading.Lock()
        self.last_fen = None

    def handle_fen(self, fen):
        with self.lock:
            try:
                board = chess.Board(fen)
            except ValueError as e:
                return {"error": f"FEN invalide reçu du navigateur : {e}"}

            self.last_fen = fen
            result = self.engine.analyze_fen(fen, multipv=3)

            if result.get("game_over"):
                payload = {"game_over": True, "result": result["result"]}
                if self.on_update:
                    self.on_update(None, f"Partie terminée : {result['result']}")
                return payload

            lines = result["lines"]
            best = lines[0]

            if self.explain_mode == "api":
                explanation = explain_move_via_api(
                    fen, best["move_san"], best["pv_san"], best["score"]
                )
            else:
                move_obj = chess.Move.from_uci(best["move_uci"])
                explanation = explain_move_local(board, move_obj, best["pv_san"])

            if self.on_update:
                self.on_update(lines, explanation)

            return {"game_over": False, "lines": lines, "explanation": explanation}

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
                self.send_response(400)
                self._cors_headers()
                self.end_headers()
                self.wfile.write(b'{"error": "JSON invalide, champ \\"fen\\" attendu"}')
                return

            result = state.handle_fen(fen)

            self.send_response(200)
            self._cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode("utf-8"))

        def log_message(self, format, *args):
            pass  # silence les logs HTTP dans la console

    return Handler


def start_bridge_server(stockfish_path, explain_mode="local", on_update=None, port=DEFAULT_PORT):
    """
    Démarre le serveur en tâche de fond (thread daemon) et retourne
    (server, state). Appelle state.close() pour bien fermer Stockfish
    à la fin.
    """
    state = BridgeState(stockfish_path, explain_mode=explain_mode, on_update=on_update)
    handler_cls = _make_handler(state)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"🌉 Pont navigateur démarré : http://127.0.0.1:{port}/fen")
    print("   Colle chess_coach_bridge.js dans ta page (ou via une extension "
          "type Tampermonkey) pour connecter ton site.")
    return server, state
