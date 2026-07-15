"""
lichess_explorer.py
Interroge l'API Lichess Opening Explorer (base "lichess", parties de tous
les joueurs -- pas seulement les GM) pour couvrir le nom d'ouverture ET une
suggestion de coup théorique au-delà de ce que couvrent le livre polyglot
local (opening_book.py, 30k positions figées) et la petite base ECO locale
(opening_identity.py / eco_openings.json, 39 lignes).

⚠ AUTHENTIFICATION REQUISE (depuis début mars 2026, changement Lichess) :
Lichess a fermé l'accès anonyme à ce endpoint suite à des attaques DDoS
répétées ("Using the opening explorer now requires being logged in because
we can't defend anon requests against DDoS"). Il faut donc un token d'accès
personnel Lichess (gratuit, aucun scope particulier requis) :
  1. Se connecter sur https://lichess.org (créer un compte si besoin,
     gratuit).
  2. Générer un token ici : https://lichess.org/account/oauth/token
     -- ne cocher AUCUN scope (lecture seule de l'explorer, pas besoin
     de permissions sur le compte).
  3. Fournir ce token au coach par L'UNE des deux méthodes suivantes :
     - variable d'environnement LICHESS_API_TOKEN, OU
     - fichier texte "lichess_token.txt" (le token, rien d'autre dedans)
       placé à côté de opening_book.bin / eco_openings.json.
Sans token configuré, ce module se désactive proprement dès le démarrage
(un seul message explicatif, aucun appel réseau tenté, aucun spam
d'erreurs 401) -- le coach fonctionne alors exactement comme avant
(livre local + base ECO locale uniquement, sans le repli Lichess).

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
import os
import threading
import urllib.request
import urllib.parse
import urllib.error

import app_paths

EXPLORER_URL = "https://explorer.lichess.org/lichess"  # hostname officiel actuel (l'ancien explorer.lichess.ovh reste répandu dans d'anciens exemples mais n'est plus celui documenté par Lichess)
REQUEST_TIMEOUT = 4  # secondes -- tourne uniquement en tâche de fond, jamais sur le chemin de la réponse HTTP au navigateur
MIN_POPULARITY = 0.01  # le coup le plus joué doit représenter au moins 1% des parties de la position pour être retenu
MAX_CACHE_ENTRIES = 5000  # borne mémoire de sécurité (parties très longues / beaucoup de positions vues sur la durée)


def _load_token():
    """
    Cherche un token d'accès personnel Lichess, dans l'ordre :
    1. variable d'environnement LICHESS_API_TOKEN,
    2. fichier "lichess_token.txt" à côté des autres fichiers du coach
       (opening_book.bin, eco_openings.json -- voir app_paths.get_base_dir()).
    Retourne None si rien n'est configuré (le module se désactive alors
    proprement, voir LichessExplorer.__init__).
    """
    env_token = os.environ.get("LICHESS_API_TOKEN")
    if env_token and env_token.strip():
        return env_token.strip()
    token_path = os.path.join(app_paths.get_base_dir(), "lichess_token.txt")
    if os.path.isfile(token_path):
        try:
            with open(token_path, encoding="utf-8") as f:
                token = f.read().strip()
            if token:
                return token
        except Exception as e:
            print(f"⚠ Token Lichess illisible ({token_path}) : {e}.")
    return None


class LichessExplorer:
    def __init__(self, enabled=True):
        self.token = _load_token() if enabled else None
        self.enabled = enabled and self.token is not None
        if enabled and self.token is None:
            print(
                "ℹ Pas de token Lichess configuré -- le repli \"base Lichess\" pour le nom "
                "d'ouverture et la flèche théorique est désactivé (rien de cassé, le coach "
                "utilise juste le livre local + la base ECO locale comme avant). Pour "
                "l'activer : génère un token gratuit sur "
                "https://lichess.org/account/oauth/token (aucun scope à cocher), puis "
                "mets-le soit dans la variable d'environnement LICHESS_API_TOKEN, soit dans "
                "un fichier lichess_token.txt à côté de opening_book.bin."
            )
        elif self.enabled:
            print("♟ Token Lichess détecté -- repli \"base Lichess\" activé pour le nom d'ouverture et la flèche théorique.")
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
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                # Token absent/invalide/révoqué côté Lichess -- ce n'est
                # PAS transitoire, ça va échouer pareil pour toutes les
                # positions suivantes. Autant se désactiver proprement
                # plutôt que de spammer la même erreur à chaque nouvelle
                # position de la partie.
                with self._lock:
                    self.enabled = False
                print(
                    f"⚠ Lichess a refusé le token fourni (HTTP {e.code}) -- le repli \"base "
                    "Lichess\" est désactivé pour le reste de la session. Vérifie que le "
                    "token dans LICHESS_API_TOKEN / lichess_token.txt est valide et non "
                    "expiré (voir https://lichess.org/account/oauth/token)."
                )
            else:
                print(f"⚠ Lichess Opening Explorer indisponible pour cette position : HTTP {e.code}")
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
            f"{EXPLORER_URL}?{params}",
            headers={
                "User-Agent": "chess-coach/1.0",
                "Authorization": f"Bearer {self.token}",
            },
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
