"""The updater: version checks, a download that doesn't match the release is refused and
changes nothing, a good one replaces the app and starts it, and the old copy is removed.
Run: python -m tests.test_update
"""

import hashlib
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["FREEUPMAIL_HOME"] = tempfile.mkdtemp()  # before importing store: never touch real data

from freeupmail import __version__, updater

NEW = b"the new app"


def fake_github(tag: str, digest: str):
    asset = {"name": updater.ASSETS[sys.platform], "size": len(NEW), "digest": f"sha256:{digest}",
             "browser_download_url": "https://github.com/x/releases/download/app"}
    release = {"tag_name": tag, "body": "notes", "html_url": "https://github.com/x/releases/tag", "assets": [asset]}

    def urlopen(req, timeout=0):
        url = req.full_url if hasattr(req, "full_url") else req
        return io.BytesIO(json.dumps(release).encode() if url == updater.LATEST else NEW)
    updater.urllib.request.urlopen = urlopen


def test_versions():
    assert updater._version("v1.10.0") > updater._version("1.9.9") > updater._version("1.9")
    assert updater._version("v2.0.0-beta") == (2, 0, 0) and updater._version("nonsense") == ()


def test_install():
    folder = Path(tempfile.mkdtemp())
    app = folder / ("FreeUpMail.exe" if sys.platform == "win32" else "FreeUpMail")
    app.write_bytes(b"the old app")
    sys.frozen, sys.executable = True, str(app)
    started, quit_calls, log = [], [], []
    updater.subprocess.Popen = lambda args, **kw: started.append(args)

    fake_github("v99.0.0", "0" * 64)  # what GitHub publishes doesn't match the download
    info = updater.check()
    assert info["newer"] and info["can_install"] and info["current"] == __version__, info
    try:
        updater.install(log.append, lambda: quit_calls.append(1))
        raise AssertionError("a mismatched download must be refused")
    except RuntimeError:
        pass
    assert app.read_bytes() == b"the old app" and sorted(p.name for p in folder.iterdir()) == [app.name]
    assert not started and not quit_calls

    fake_github("v99.0.0", hashlib.sha256(NEW).hexdigest())
    updater.install(log.append, lambda: quit_calls.append(1))
    assert app.read_bytes() == NEW and updater.old_copy(app).read_bytes() == b"the old app"
    assert started == [[str(app), "--after-update"]] and quit_calls == [1], (started, quit_calls)

    updater.remove_old_copy()  # what the new copy does when it starts
    for _ in range(20):
        if not updater.old_copy(app).exists():
            break
        time.sleep(0.1)
    assert not updater.old_copy(app).exists()

    fake_github(f"v{__version__}", hashlib.sha256(NEW).hexdigest())
    assert not updater.check()["newer"]
    del sys.frozen


def main():
    test_versions()
    test_install()
    print("updates: all checks passed")


if __name__ == "__main__":
    main()
