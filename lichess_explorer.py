"""
lichess_explorer.py
Interroge l'API Lichess Opening Explorer (base "lichess", parties de tous
les joueurs -- pas seulement les GM) pour couvrir le nom d'ouverture ET une
suggestion de coup théorique au-delà de ce que couvrent le livre polyglot
local (opening_book.py, 30k positions figées) et la petite base ECO locale
(opening_identity.py / eco_openings.json, 39 lignes).

Design pensé pour ne JAMAIS ralentir une flèche (contrainte explicite :
la flèche théorique doit s'afficher vite ou ça ne sert à rien) :

- lookup(fen) ne fait JAMAIS d'appel réseau sur le fil qui répond au
  navigateur. Si la position est déjà en cache -> retour instantané.
  Sinon -> retour immédiat de None ET lancement d'un thread daemon qui va
  chercher la réponse en tâche de fond et la met en cache pour la
  PROCHAINE requête sur cette position (le navigateur revient de toute
  façon sur la même position plusieurs fois : 1 aperçu rapide + jusqu'à 3
  profils, voir web_bridge.py -- et une même ouverture revient d'une
  partie à l'autre, donc le cache reste utile après le 1er coup "à
  vide").
- Cache mémoire {fen: dict|None}, valable pour toute la durée du
  processus (pas de TTL : une ouverture connue le reste).
- Seuil de popularité (MIN_POPULARITY) : un coup trop rare dans la base
  est ignoré plutôt que suggéré comme "théorique".
"""
import json
import threading
import urllib.request
import urllib.parse

EXPLORER_URL = "https://explorer.lichess.ovh/lichess"
REQUEST_TIMEOUT = 4  # secondes -- tourne uniquement en tâche de fond, jamais sur le chemin de la réponse HTTP au navigateur
MIN_POPULARITY = 0.01  # le coup le plus joué doit représenter au moins 1% des parties de la position pour être retenu
MAX_CACHE_ENTRIES = 5000  # borne mémoire de sécurité (parties très longues / beaucoup de positions vues sur la durée)


class LichessExplorer:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self._cache = {}       # fen -> dict résultat, ou None si interrogé mais rien d'utile trouvé
        self._pending = set()  # fens actuellement en cours de récupération (évite de spammer 2x la même requête)
        self._lock = threading.Lock()

    def lookup(self, fen):
        """
        Retourne immédiatement :
        - le résultat en cache si déjà connu pour ce FEN (dict
          {"eco", "name", "top_move_uci", "top_move_san", "popularity"},
          ou None si la position a déjà été interrogée sans rien donner
          d'exploitable) ;
        - None si la position n'a encore jamais été vue -- une recherche
          part alors en tâche de fond, le résultat sera disponible au
          PROCHAIN appel pour ce même FEN.
        Ne bloque jamais l'appelant.
        """
        if not self.enabled or not fen:
            return None
        with self._lock:
            if fen in self._cache:
                return self._cache[fen]
            if fen in self._pending:
                return None
            self._pending.add(fen)
        threading.Thread(target=self._fetch, args=(fen,), daemon=True).start()
        return None

    def _fetch(self, fen):
        result = None
        try:
            result = self._query(fen)
        except Exception as e:
            # Best-effort total : pas de réseau, timeout, Lichess en
            # maintenance... rien de tout ça ne doit remonter -- le coach
            # retombe simplement sur les gabarits génériques / Stockfish
            # seul, exactement comme si l'explorer n'existait pas.
            print(f"⚠ Lichess Opening Explorer indisponible pour cette position : {e}")
        with self._lock:
            if len(self._cache) >= MAX_CACHE_ENTRIES:
                self._cache.clear()  # purge simple plutôt qu'un LRU -- ce cas reste rare en usage normal
            self._cache[fen] = result
            self._pending.discard(fen)

    def _query(self, fen):
        params = urllib.parse.urlencode({
            "variant": "standard",
            "fen": fen,
            "moves": 8,
            "topGames": 0,
            "recentGames": 0,
        })
        req = urllib.request.Request(
            f"{EXPLORER_URL}?{params}", headers={"User-Agent": "chess-coach/1.0"}
        )
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            data = json.load(resp)

        moves = data.get("moves") or []
        if not moves:
            return None  # position hors couverture Lichess (trop rare / déjà très exotique)

        def games(m):
            return m.get("white", 0) + m.get("draws", 0) + m.get("black", 0)

        total = sum(games(m) for m in moves) or 1
        best = max(moves, key=games)
        popularity = games(best) / total
        if popularity < MIN_POPULARITY:
            return None  # coup trop rare pour valoir une suggestion "théorique"

        opening = data.get("opening") or {}
        return {
            "eco": opening.get("eco"),
            "name": opening.get("name"),
            "top_move_uci": best.get("uci"),
            "top_move_san": best.get("san"),
            "popularity": round(popularity, 3),
        }

    def close(self):
        """Pour rester symétrique avec OpeningBook.close() -- rien à libérer ici (pas de connexion persistante)."""
        pass
