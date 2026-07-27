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


def test_bat_script_contains_paths_no_tasklist():
    bat = self_update._build_bat_script(r"C:\extracted\CoachEchecs", r"C:\install\CoachEchecs", r"C:\install\CoachEchecs\CoachEchecs.exe")
    check("tasklist" not in bat.lower(),
          "PAS de tasklist -- diagnostiqué en pratique : ne produit aucune sortie en tâche détachée sans console")
    check("coachechecs_update_log.txt" in bat, "le script journalise dans un fichier (diagnostic possible après coup)")
    check('if exist "C:\\install\\CoachEchecs\\CoachEchecs.exe"' in bat,
          "vérifie que l'exe existe avant de tenter de le relancer")
    check("/R:20" in bat, "robocopy retente lui-même (pas de poll PID séparé) le temps que le verrou se libère")
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
        test_bat_script_contains_paths_no_tasklist,
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
