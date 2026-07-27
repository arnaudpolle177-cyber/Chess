"""
self_update.py
Applique une mise à jour DÉJÀ téléchargée/extraite (voir update_checker.
download_update/extract_update) : remplace les fichiers de l'installation
courante par les nouveaux et relance le coach -- déclenché par le bouton
"Mettre à jour" du bandeau (voir main.py, webview_ui.py).

Le process Python en cours NE PEUT PAS se remplacer lui-même sous Windows :
CoachEchecs.exe reste verrouillé (fichier ouvert) tant qu'il tourne. Solution
classique : écrire un petit script .bat qui ATTEND que ce process se
termine (poll sur le PID), copie les nouveaux fichiers par-dessus
l'installation actuelle, relance le nouvel exe, puis se supprime lui-même.
Le process Python lance ce script en tâche DÉTACHÉE (survit après la mort
du process Python) puis se termine immédiatement (os._exit -- pas de
nettoyage supplémentaire à faire ici, le script prend le relais).

UNIQUEMENT pour un build packagé (sys.frozen) -- rien à remplacer en dev
(`python main.py`).
"""
import os
import subprocess
import sys
import tempfile

import app_paths

# robocopy : /E (sous-dossiers y compris vides), /IS /IT (réécrit même les
# fichiers identiques/plus vieux -- une mise à jour DOIT écraser, pas
# "optimiser" en sautant des fichiers qui semblent pareils), /R:5 /W:1
# (5 tentatives, 1s d'attente -- CoachEchecs.exe peut rester verrouillé
# une fraction de seconde de plus après la fin du process, antivirus qui
# scanne le nouveau binaire, etc.).
#
# Journalisé dans %TEMP%\\coachechecs_update_log.txt (PAS supprimé, seul le
# script .bat l'est à la fin) : ce script tourne en tâche DÉTACHÉE, sans
# fenêtre -- avant ce journal, un échec (copie ratée, exe introuvable après
# coup) était totalement invisible, ni erreur ni trace nulle part. Boucle
# d'attente plafonnée à 30s (pas une attente infinie) : si le process
# Python ne s'est jamais vraiment terminé pour une raison quelconque, on
# tente quand même la copie plutôt que de rester bloqué pour toujours.
_BAT_TEMPLATE = """@echo off
set "LOG=%TEMP%\\coachechecs_update_log.txt"
echo ==== %date% %time% : debut mise a jour (attente PID {pid}) ==== > "%LOG%"
set /a _tries=0
:wait_loop
tasklist /FI "PID eq {pid}" 2>NUL | find "{pid}" >NUL
if not errorlevel 1 (
    set /a _tries+=1
    if %_tries% GEQ 30 (
        echo [%time%] Abandon de l'attente apres 30s, le process semble toujours actif. >> "%LOG%"
        goto do_copy
    )
    timeout /t 1 /nobreak >nul
    goto wait_loop
)
:do_copy
echo [%time%] Process termine, copie de "{src}" vers "{dst}"... >> "%LOG%"
robocopy "{src}" "{dst}" /E /IS /IT /R:5 /W:1 >> "%LOG%" 2>&1
echo [%time%] Code retour robocopy: %errorlevel% >> "%LOG%"
if exist "{exe}" (
    echo [%time%] Relance de "{exe}" >> "%LOG%"
    start "" "{exe}"
) else (
    echo [%time%] ERREUR : "{exe}" introuvable apres la copie -- mise a jour incomplete. >> "%LOG%"
)
echo ==== %time% : fin du script ==== >> "%LOG%"
del "%~f0"
"""


def _build_bat_script(pid, src, dst, exe):
    """Séparée de apply_update_and_restart pour être testable sans process réel (voir test_self_update.py)."""
    return _BAT_TEMPLATE.format(pid=pid, src=src, dst=dst, exe=exe)


def apply_update_and_restart(extracted_dir):
    """
    extracted_dir : dossier contenant les nouveaux fichiers (voir
    update_checker.extract_update -- CoachEchecs.exe + siblings, MÊME
    structure que l'installation actuelle). Ne fait rien et retourne False
    si le process n'est pas un build packagé. Sinon, ne retourne JAMAIS
    normalement (termine le process avant la fin de la fonction) --
    l'appelant ne doit rien exécuter après ce point.
    """
    if not getattr(sys, "frozen", False):
        return False

    install_dir = app_paths.get_base_dir()
    exe_path = sys.executable
    pid = os.getpid()

    fd, bat_path = tempfile.mkstemp(suffix=".bat", prefix="coachechecs_update_")
    os.close(fd)
    with open(bat_path, "w", encoding="utf-8") as f:
        f.write(_build_bat_script(pid, extracted_dir, install_dir, exe_path))

    # DETACHED_PROCESS (0x8) + CREATE_NEW_PROCESS_GROUP (0x200) : le script
    # doit survivre après que CE process se termine, sans rester rattaché à
    # sa console (qui disparaît avec lui).
    subprocess.Popen(
        ["cmd", "/c", bat_path],
        creationflags=0x00000008 | 0x00000200,
        close_fds=True,
        cwd=tempfile.gettempdir(),
    )
    os._exit(0)  # jamais de retour -- libère immédiatement le verrou sur CoachEchecs.exe
