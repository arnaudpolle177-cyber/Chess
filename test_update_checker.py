"""
test_update_checker.py
Tests de update_checker.py : pas d'accès réseau réel -- urllib.request.
urlopen et version.get_local_version sont monkeypatchés.
"""
import io
import json
import os
import sys
import zipfile

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
        check(result["asset_url"] == "https://x/zip", "asset_url = asset .zip trouvé")
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


class _FakeStreamResponse:
    """Simule une réponse HTTP en streaming (voir update_checker.download_update -- .read(size) par chunks, pas .read() d'un coup)."""

    def __init__(self, payload_bytes, content_length=True):
        self._buf = io.BytesIO(payload_bytes)
        self.headers = {"Content-Length": str(len(payload_bytes))} if content_length else {}

    def read(self, size=-1):
        return self._buf.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_download_update_writes_bytes_and_reports_progress():
    orig_urlopen = update_checker.urllib.request.urlopen
    payload = os.urandom(update_checker._CHUNK_SIZE + 100)  # >1 chunk, force plusieurs tours de boucle
    update_checker.urllib.request.urlopen = lambda *a, **kw: _FakeStreamResponse(payload)
    progress_values = []
    try:
        tmp_path = update_checker.download_update("https://x/fake.zip", progress_cb=progress_values.append)
        try:
            with open(tmp_path, "rb") as f:
                written = f.read()
            check(written == payload, "le fichier téléchargé contient exactement les octets envoyés")
            check(len(progress_values) >= 2, "progress_cb appelé plusieurs fois (plusieurs chunks)")
            check(abs(progress_values[-1] - 1.0) < 1e-6, "dernière valeur de progression = 100%")
        finally:
            os.remove(tmp_path)
    finally:
        update_checker.urllib.request.urlopen = orig_urlopen


def test_download_update_cleans_up_temp_file_on_error():
    orig_urlopen = update_checker.urllib.request.urlopen

    class _Boom:
        def __enter__(self):
            raise OSError("connexion coupée")

        def __exit__(self, *a):
            return False

    update_checker.urllib.request.urlopen = lambda *a, **kw: _Boom()
    raised = False
    try:
        update_checker.download_update("https://x/fake.zip")
    except OSError:
        raised = True
    finally:
        update_checker.urllib.request.urlopen = orig_urlopen
    check(raised, "l'erreur réseau doit être remontée (pas avalée comme check_for_update)")


def test_extract_update_returns_coachechecs_subfolder():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("CoachEchecs/version.txt", "V9TEST")
        zf.writestr("CoachEchecs/CoachEchecs.exe", "faux exe")
    fd, zip_path = __import__("tempfile").mkstemp(suffix=".zip")
    os.close(fd)
    try:
        with open(zip_path, "wb") as f:
            f.write(buf.getvalue())
        extracted = update_checker.extract_update(zip_path)
        check(os.path.basename(extracted.rstrip("/\\")) == "CoachEchecs",
              f"dossier extrait = sous-dossier CoachEchecs, obtenu {extracted}")
        check(os.path.isfile(os.path.join(extracted, "version.txt")), "version.txt présent dans le dossier extrait")
    finally:
        os.remove(zip_path)


def main():
    for fn in (
        test_no_local_version_never_calls_network,
        test_same_tag_means_no_update,
        test_different_tag_means_update_available,
        test_network_error_never_raises,
        test_download_update_writes_bytes_and_reports_progress,
        test_download_update_cleans_up_temp_file_on_error,
        test_extract_update_returns_coachechecs_subfolder,
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
