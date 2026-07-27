"""
test_self_update.py
Tests de self_update.py : pas de process réel lancé (os._exit ni
subprocess.Popen ne sont jamais atteints ici) -- vérifie seulement la
génération du script .bat et la garde sys.frozen.
"""
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import self_update

_failures = []


def check(cond, msg):
    if not cond:
        _failures.append(msg)


def test_bat_script_contains_pid_wait_and_paths():
    bat = self_update._build_bat_script(1234, r"C:\extracted\CoachEchecs", r"C:\install\CoachEchecs", r"C:\install\CoachEchecs\CoachEchecs.exe")
    check('"PID eq 1234"' in bat, "le script attend la fin du PID donné")
    check("coachechecs_update_log.txt" in bat, "le script journalise dans un fichier (diagnostic possible après coup)")
    check('if exist "C:\\install\\CoachEchecs\\CoachEchecs.exe"' in bat,
          "vérifie que l'exe existe avant de tenter de le relancer")
    check("_tries" in bat, "boucle d'attente plafonnée, pas une attente infinie")
    check(r'"C:\extracted\CoachEchecs" "C:\install\CoachEchecs"' in bat, "robocopy copie du dossier extrait vers l'install")
    check(r'start "" "C:\install\CoachEchecs\CoachEchecs.exe"' in bat, "relance le bon exe")
    check("del \"%~f0\"" in bat, "le script se supprime lui-même à la fin")


def test_apply_update_noop_when_not_frozen():
    # sys.frozen n'existe pas dans ce test (exécution normale python) ->
    # apply_update_and_restart doit retourner False immédiatement, sans
    # jamais tenter de lancer un process ni appeler os._exit.
    check(not getattr(sys, "frozen", False), "précondition du test : pas un build packagé ici")
    result = self_update.apply_update_and_restart(r"C:\peu importe")
    check(result is False, "pas un build packagé -> apply_update_and_restart retourne False, ne fait rien")


def main():
    for fn in (
        test_bat_script_contains_pid_wait_and_paths,
        test_apply_update_noop_when_not_frozen,
    ):
        try:
            fn()
        except Exception as e:
            _failures.append(f"{fn.__name__} a levé une exception : {e!r}")

    if _failures:
        print(f"ÉCHEC ({len(_failures)}) :")
        for f in _failures:
            print("  -", f)
        sys.exit(1)
    print("test_self_update : OK")


if __name__ == "__main__":
    main()
