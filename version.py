"""
version.py
Version LOCALE du build en cours d'exécution -- PAS une constante à bumper
à la main : écrite automatiquement dans version.txt (à côté de l'exe, voir
app_paths.get_base_dir) par .github/workflows/build.yml au moment du
packaging, avec le TAG GIT exact utilisé pour cette release (ex: "V2LCO",
"v18"...). Comparée à la dernière Release GitHub par update_checker.py.

En dev (python main.py, pas de version.txt à côté du script) : get_local_
version() retourne None -- aucune vérification de mise à jour n'a de sens
pour un checkout source, voir update_checker.check_for_update.
"""
import os

import app_paths

VERSION_FILENAME = "version.txt"


def get_local_version():
    path = os.path.join(app_paths.get_base_dir(), VERSION_FILENAME)
    try:
        with open(path, "r", encoding="utf-8") as f:
            v = f.read().strip()
        return v or None
    except OSError:
        return None
