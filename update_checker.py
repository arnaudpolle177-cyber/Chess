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
import os
import tempfile
import urllib.error
import urllib.request
import zipfile

import version

GITHUB_REPO = "arnaudpolle177-cyber/Chess"
_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
_TIMEOUT_S = 5
_DOWNLOAD_TIMEOUT_S = 30
_CHUNK_SIZE = 256 * 1024


def check_for_update(timeout_s=_TIMEOUT_S):
    """
    Retourne un dict :
        {"available": True, "latest_version": "V2LCO",
         "release_url": "https://github.com/.../releases/tag/V2LCO",
         "asset_url": "...CoachEchecs-windows.zip" | None}
    ou {"available": False} -- que ce soit parce qu'aucune mise à jour
    n'existe, ou parce que la vérification a échoué pour une raison
    quelconque (jamais d'exception remontée à l'appelant).
    "asset_url" à None si la release n'a pas de zip attaché (release créée
    à la main sans build) -- l'appelant doit alors se rabattre sur
    "release_url" (mise à jour manuelle), l'auto-update (voir
    download_update/extract_update/self_update.py) n'a besoin QUE de
    asset_url.
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
        asset_url = None
        for asset in data.get("assets", []):
            if asset.get("name", "").endswith(".zip"):
                asset_url = asset.get("browser_download_url")
                break
        return {
            "available": True,
            "latest_version": latest_tag,
            "release_url": data.get("html_url"),
            "asset_url": asset_url,
        }
    except Exception as e:
        # Toujours best-effort (jamais bloquant, voir docstring), mais
        # visible dans la console (console=True, voir coach.spec) --
        # échec totalement silencieux avant ce print : un rate limit API
        # GitHub (ou tout autre souci réseau) faisait disparaître la
        # vérification sans aucune trace, impossible à diagnostiquer.
        print(f"⚠ Vérification de mise à jour indisponible : {e}")
        return {"available": False}


def download_update(asset_url, progress_cb=None, timeout_s=_DOWNLOAD_TIMEOUT_S):
    """
    Télécharge le zip de la release vers un fichier temporaire, EN
    STREAMING (le zip pèse 100+ Mo -- hors de question de tout charger en
    mémoire d'un coup). `progress_cb(fraction)` appelé périodiquement si
    fourni (0.0..1.0 ; 0.0 si la taille totale est inconnue -- certains
    CDN ne renvoient pas Content-Length) -- best-effort, une erreur dedans
    ne doit jamais interrompre le téléchargement.

    Retourne le chemin du fichier zip téléchargé. Lève une exception en
    cas d'échec (contrairement à check_for_update) : l'appelant (voir
    main.py, _apply_update_async) sait qu'il est dans un flux déclenché
    explicitement par l'utilisateur, une erreur doit lui être remontée
    plutôt qu'avalée silencieusement.
    """
    req = urllib.request.Request(asset_url, headers={"User-Agent": "CoachEchecs-update-check"})
    fd, tmp_path = tempfile.mkstemp(suffix=".zip", prefix="coachechecs_update_")
    os.close(fd)
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            downloaded = 0
            with open(tmp_path, "wb") as f:
                while True:
                    chunk = resp.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb:
                        try:
                            progress_cb(downloaded / total if total else 0.0)
                        except Exception:
                            pass
        return tmp_path
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def extract_update(zip_path):
    """
    Extrait le zip téléchargé dans un dossier temporaire dédié, retourne le
    chemin du sous-dossier "CoachEchecs" qu'il contient (voir
    .github/workflows/build.yml : "7z a -r CoachEchecs-windows.zip
    CoachEchecs" -- le zip a toujours cette structure). Lève une exception
    en cas d'échec, même logique que download_update ci-dessus.
    """
    dest_dir = tempfile.mkdtemp(prefix="coachechecs_update_extract_")
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(dest_dir)
    inner = os.path.join(dest_dir, "CoachEchecs")
    return inner if os.path.isdir(inner) else dest_dir
