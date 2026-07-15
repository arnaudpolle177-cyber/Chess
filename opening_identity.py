"""
opening_identity.py
Identifie l'ouverture en cours (nom + points forts/faibles) à partir de
l'historique des coups joués depuis le début de la partie (voir
web_bridge.py, BridgeState._move_history).

Principe : chaque entrée de la base ECO locale (eco_openings.json) est une
séquence de coups en SAN ("1. e4 e5 2. Nf3 Nc6 3. Bc4" -> ["e4", "e5",
"Nf3", "Nc6", "Bc4"]). On cherche, parmi toutes les entrées dont la
séquence est un PRÉFIXE EXACT de l'historique joué, celle dont la séquence
est la PLUS LONGUE -- c'est la variante la plus précise qui corresponde
encore à la partie réelle. Si aucune entrée ne matche (l'historique s'est
déjà écarté de toutes les lignes connues), retourne None : narration.py
retombe alors sur les gabarits génériques existants (_opening_xxx_1).

Base 100% locale (fichier JSON, aucun appel réseau) -- format compatible
avec lichess-org/chess-openings (champs eco/name/pgn), plus un champ
`pros_cons` propre à ce projet (texte pédagogique rédigé à la main, pas
fourni par les bases ECO publiques).
"""
import json
import os
import re

_PGN_MOVE_RE = re.compile(r"\d+\.+\s*")  # supprime "1." / "12..." etc. d'une chaîne PGN


def _pgn_to_san_list(pgn):
    """
    Convertit une séquence PGN ("1. e4 e5 2. Nf3 Nc6") en liste de coups SAN
    (["e4", "e5", "Nf3", "Nc6"]) -- retire les numéros de coup, garde
    l'ordre.
    """
    cleaned = _PGN_MOVE_RE.sub("", pgn)
    return cleaned.split()


class OpeningIdentity:
    def __init__(self, path, explorer=None):
        self.entries = []  # liste de (san_list, eco, name, pros_cons), triée par longueur décroissante
        self.explorer = explorer  # LichessExplorer optionnel, voir lichess_explorer.py -- fallback si la base locale ne matche pas
        if not path or not os.path.isfile(path):
            print(
                f"ℹ Base d'ouvertures introuvable ({path or 'chemin non défini'}). "
                "L'identification par nom d'ouverture sera désactivée -- rien de cassé, "
                "le coach retombe sur les gabarits génériques en phase d'ouverture."
            )
            return
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            for entry in raw:
                san_list = _pgn_to_san_list(entry["pgn"])
                if not san_list:
                    continue
                self.entries.append((san_list, entry["eco"], entry["name"], entry["pros_cons"]))
            # Plus longue séquence d'abord : garantit qu'on retient la
            # variante la plus précise (celle qui matche le plus de coups
            # réellement joués), pas juste la première trouvée dans le
            # fichier.
            self.entries.sort(key=lambda e: len(e[0]), reverse=True)
            print(f"📚 Base d'ouvertures chargée : {len(self.entries)} entrées ({path})")
        except Exception as e:
            print(f"⚠ Base d'ouvertures illisible ({path}) : {e}. Le coach fonctionnera sans.")

    def identify(self, move_history, fen=None):
        """
        move_history : liste de coups SAN joués depuis le début de la
        partie (voir web_bridge.py, BridgeState._move_history).
        fen : position actuelle (optionnel) -- utilisée UNIQUEMENT pour le
        fallback Lichess ci-dessous, la base locale continue de matcher
        par historique de coups comme avant.

        Retourne un dict {"eco", "name", "pros_cons"} pour le meilleur
        match (préfixe exact le plus long) dans la base locale. Si rien ne
        matche (ligne absente des entrées locales, ou déjà hors théorie
        connue) ET qu'un explorer Lichess est configuré, tente un fallback
        en ligne (voir lichess_explorer.py) -- non bloquant : peut renvoyer
        None la première fois qu'une position est vue, le temps que la
        requête en tâche de fond remplisse le cache.
        """
        if move_history and self.entries:
            for san_list, eco, name, pros_cons in self.entries:
                if len(san_list) > len(move_history):
                    continue  # cette ligne va plus loin que ce qui a été joué, ne peut pas matcher un préfixe
                if move_history[:len(san_list)] == san_list:
                    return {"eco": eco, "name": name, "pros_cons": pros_cons}

        # Base locale muette sur cette position -- tente Lichess si
        # configuré (souvent déjà en cache si on est resté plusieurs coups
        # dans la même ouverture, voir lichess_explorer.py).
        if self.explorer is not None and fen:
            remote = self.explorer.lookup(fen)
            if remote and remote.get("name"):
                return {
                    "eco": remote.get("eco") or "?",
                    "name": remote.get("name"),
                    "pros_cons": "Ouverture identifiée via la base Lichess (statistiques de "
                                 "popularité) -- pas encore de commentaire détaillé rédigé pour "
                                 "cette ligne précise.",
                }
        return None
