"""
update_checker.py
Vérifie au démarrage si une Release plus récente du coach est disponible
sur GitHub, pour prévenir l'utilisateur du .exe compilé qu'une mise à jour
existe après un merge/tag côté dépôt -- voir version.py (version locale,
écrite par .github/workflows/build.yml) et main.py (appel au démarrage).

PAS de comparaison numérique de versions (semver) : l'historique de tags de
ce dépôt n'est pas cohérent ("V2LCO", "5.6", "v18"...), un tri/parsing
numérique serait fragile. On s'appuie plutôt sur la définition de GitHub
lui-même de "dernière release" (endpoint /releases/latest, = la plus
récemment PUBLIÉE, jamais une comparaison de numéros) -- fiable quel que
soit le schéma de nommage des tags. Une mise à jour est "disponible" si son
tag diffère simplement du tag local, rien de plus.

Best-effort total (hors ligne, GitHub indisponible, dépôt renommé...) : ne
lève jamais d'exception, ne bloque jamais le démarrage du coach -- retourne
juste "pas de mise à jour connue" dans le doute.
"""
import json
import urllib.error
import urllib.request

import version

GITHUB_REPO = "arnaudpolle177-cyber/Chess"
_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
_TIMEOUT_S = 5


def check_for_update(timeout_s=_TIMEOUT_S):
    """
    Retourne un dict :
        {"available": True, "latest_version": "V2LCO",
         "release_url": "https://github.com/.../releases/tag/V2LCO",
         "download_url": "...CoachEchecs-windows.zip"}
    ou {"available": False} -- que ce soit parce qu'aucune mise à jour
    n'existe, ou parce que la vérification a échoué pour une raison
    quelconque (jamais d'exception remontée à l'appelant).
    """
    local_version = version.get_local_version()
    if local_version is None:
        # Pas un build packagé (dev, `python main.py`) -- rien à comparer.
        return {"available": False}
    try:
        req = urllib.request.Request(
            _API_URL,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "CoachEchecs-update-check"},
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        latest_tag = (data.get("tag_name") or "").strip()
        if not latest_tag or latest_tag == local_version:
            return {"available": False}
        download_url = None
        for asset in data.get("assets", []):
            name = asset.get("name", "")
            if name.endswith(".zip"):
                download_url = asset.get("browser_download_url")
                break
        return {
            "available": True,
            "latest_version": latest_tag,
            "release_url": data.get("html_url"),
            "download_url": download_url or data.get("html_url"),
        }
    except Exception:
        return {"available": False}
