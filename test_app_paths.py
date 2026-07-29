"""
test_app_paths.py
Tests unitaires du module app_paths (dossier de base pour les fichiers
générés par l'application, en mode script comme en mode .exe compilé).

Autonome et sans dépendance externe : lance-le directement avec
`python test_app_paths.py`. Sortie "OK" + code de sortie 0 si tout passe,
sinon la 1re assertion qui casse s'affiche avec un message explicite et le
code de sortie est 1.
"""
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import app_paths


_failures = []


def check(cond, msg):
    if cond:
        print(f"  ok  {msg}")
    else:
        print(f"  FAIL  {msg}")
        _failures.append(msg)


def test_not_frozen_returns_script_dir():
    """Exécution normale (script) : le dossier de base suit app_paths.py."""
    had_frozen = hasattr(sys, "frozen")
    old_frozen = getattr(sys, "frozen", None)
    try:
        sys.frozen = False
        expected = os.path.dirname(os.path.abspath(app_paths.__file__))
        check(app_paths.get_base_dir() == expected,
              "sys.frozen=False -> dossier du script")
    finally:
        if had_frozen:
            sys.frozen = old_frozen
        else:
            del sys.frozen


def test_frozen_returns_executable_dir():
    """Exécution compilée (.exe) : le dossier de base suit sys.executable,
    pas __file__ (qui pointerait vers un dossier temporaire PyInstaller)."""
    had_frozen = hasattr(sys, "frozen")
    old_frozen = getattr(sys, "frozen", None)
    old_executable = sys.executable
    try:
        sys.frozen = True
        fake_exe = os.path.join("C:" + os.sep, "fake", "dist", "CoachEchecs.exe")
        sys.executable = fake_exe
        check(app_paths.get_base_dir() == os.path.dirname(fake_exe),
              "sys.frozen=True -> dossier de sys.executable")
    finally:
        sys.executable = old_executable
        if had_frozen:
            sys.frozen = old_frozen
        else:
            del sys.frozen


def test_frozen_attribute_absent_defaults_to_script_dir():
    """Sans l'attribut sys.frozen (cas normal hors PyInstaller), on retombe
    sur le dossier du script plutôt que de lever une exception."""
    had_frozen = hasattr(sys, "frozen")
    old_frozen = getattr(sys, "frozen", None)
    try:
        if had_frozen:
            del sys.frozen
        expected = os.path.dirname(os.path.abspath(app_paths.__file__))
        check(app_paths.get_base_dir() == expected,
              "pas d'attribut sys.frozen -> dossier du script")
    finally:
        if had_frozen:
            sys.frozen = old_frozen


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    print(f"app_paths : {len(tests)} tests\n")
    for t in tests:
        print(f"[{t.__name__}]")
        t()
    print()
    if _failures:
        print(f"[ECHEC] {len(_failures)} assertion(s) en echec")
        sys.exit(1)
    print("[OK] tous les tests passent")
    sys.exit(0)


if __name__ == "__main__":
    main()
