"""
test_update_checker.py
Tests de update_checker.py : pas d'accès réseau réel -- urllib.request.
urlopen et version.get_local_version sont monkeypatchés.
"""
import io
import json
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import update_checker
import version

_failures = []


def check(cond, msg):
    if not cond:
        _failures.append(msg)


class _FakeResponse:
    def __init__(self, payload):
        self._data = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_no_local_version_never_calls_network():
    orig_local = version.get_local_version
    orig_urlopen = update_checker.urllib.request.urlopen
    called = []

    def fake_urlopen(*a, **kw):
        called.append(True)
        raise AssertionError("ne devrait jamais être appelé sans version locale")

    version.get_local_version = lambda: None
    update_checker.urllib.request.urlopen = fake_urlopen
    try:
        result = update_checker.check_for_update()
        check(result == {"available": False}, "sans version locale -> available False")
        check(not called, "pas d'appel réseau si pas de version locale (build dev)")
    finally:
        version.get_local_version = orig_local
        update_checker.urllib.request.urlopen = orig_urlopen


def test_same_tag_means_no_update():
    orig_local = version.get_local_version
    orig_urlopen = update_checker.urllib.request.urlopen
    version.get_local_version = lambda: "V2LCO"
    update_checker.urllib.request.urlopen = lambda *a, **kw: _FakeResponse(
        {"tag_name": "V2LCO", "html_url": "x", "assets": []})
    try:
        result = update_checker.check_for_update()
        check(result == {"available": False}, "même tag local/distant -> pas de mise à jour")
    finally:
        version.get_local_version = orig_local
        update_checker.urllib.request.urlopen = orig_urlopen


def test_different_tag_means_update_available():
    orig_local = version.get_local_version
    orig_urlopen = update_checker.urllib.request.urlopen
    version.get_local_version = lambda: "V1.LCO"
    update_checker.urllib.request.urlopen = lambda *a, **kw: _FakeResponse({
        "tag_name": "V2LCO",
        "html_url": "https://github.com/x/y/releases/tag/V2LCO",
        "assets": [{"name": "CoachEchecs-windows.zip", "browser_download_url": "https://x/zip"}],
    })
    try:
        result = update_checker.check_for_update()
        check(result["available"] is True, "tag différent -> mise à jour disponible")
        check(result["latest_version"] == "V2LCO", "latest_version = tag distant")
        check(result["download_url"] == "https://x/zip", "download_url = asset .zip trouvé")
    finally:
        version.get_local_version = orig_local
        update_checker.urllib.request.urlopen = orig_urlopen


def test_network_error_never_raises():
    orig_local = version.get_local_version
    orig_urlopen = update_checker.urllib.request.urlopen
    version.get_local_version = lambda: "V1.LCO"

    def boom(*a, **kw):
        raise OSError("pas de réseau")

    update_checker.urllib.request.urlopen = boom
    try:
        result = update_checker.check_for_update()
        check(result == {"available": False}, "erreur réseau -> available False, jamais d'exception")
    finally:
        version.get_local_version = orig_local
        update_checker.urllib.request.urlopen = orig_urlopen


def main():
    for fn in (
        test_no_local_version_never_calls_network,
        test_same_tag_means_no_update,
        test_different_tag_means_update_available,
        test_network_error_never_raises,
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
    print("test_update_checker : OK")


if __name__ == "__main__":
    main()
