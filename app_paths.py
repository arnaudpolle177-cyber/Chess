"""
app_paths.py
Fournit un dossier de base STABLE pour stocker les fichiers générés par
l'application (calibration, templates appris, etc.).

Problème réglé ici : quand le programme est compilé en .exe avec PyInstaller
en mode "onefile", __file__ pointe vers un dossier temporaire d'extraction
(sys._MEIPASS), qui est DIFFÉRENT à chaque lancement et supprimé à la
fermeture. Utiliser __file__ pour sauvegarder des données fait donc "perdre"
la calibration et les templates entre deux lancements du .exe.

La solution : si l'app est "frozen" (compilée), on utilise le dossier où se
trouve le .exe lui-même (sys.executable). Sinon (exécution via
`python main.py`), on garde le dossier du script comme avant.
"""
import os
import sys


def get_base_dir():
    if getattr(sys, "frozen", False):
        # Exécuté en tant que .exe compilé (PyInstaller) : on utilise le
        # dossier où se trouve le .exe, qui est stable entre les lancements.
        return os.path.dirname(sys.executable)
    # Exécuté en tant que script Python normal.
    return os.path.dirname(os.path.abspath(__file__))
