"""Updates from the GitHub releases: check for a newer version and, for the standalone app,
download it, check it against the SHA-256 GitHub publishes for it, swap it in place of the
running file and start it. The new copy deletes the old one once that has closed.

Runs from source or the .pyz only get told where to download (a source folder may be
someone's own working copy, so it is never changed for them).
"""

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from . import __version__

REPO = "MarkCockerill12/Free-up-Mail"
LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
ASSETS = {"win32": "FreeUpMail-windows.exe", "darwin": "FreeUpMail-macos", "linux": "FreeUpMail-linux"}
HEADERS = {"User-Agent": f"FreeUpMail/{__version__}", "Accept": "application/vnd.github+json"}


def _version(tag: str) -> tuple[int, ...]:
    try:
        return tuple(int(p) for p in tag.lstrip("vV").split("-")[0].split("."))
    except ValueError:
        return ()


def running_app() -> Path | None:
    """The standalone app file this is running from (None from source or the .pyz)."""
    return Path(sys.executable) if getattr(sys, "frozen", False) else None


def asset_name() -> str:
    if running_app():
        return ASSETS.get(sys.platform, "")
    return "FreeUpMail.pyz" if str(sys.argv[0]).endswith(".pyz") else ""


def check() -> dict:
    """What the latest release is, and whether this copy can install it by itself."""
    req = urllib.request.Request(LATEST, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        release = json.load(resp)
    latest = release.get("tag_name", "")
    asset = next((a for a in release.get("assets", []) if a.get("name") == asset_name()), None)
    return {
        "current": __version__, "latest": latest.lstrip("vV"),
        "newer": _version(latest) > _version(__version__),
        "notes": (release.get("body") or "")[:3000], "page": release.get("html_url", ""),
        "can_install": bool(running_app() and asset and str(asset.get("digest", "")).startswith("sha256:")),
        "source": not running_app() and not asset_name(),
    }


def install(log, quit_app) -> None:
    """Download the newer app, verify it, put it where this one is and start it."""
    info = check()
    if not (info["newer"] and info["can_install"]):
        log("nothing to install")
        return
    req = urllib.request.Request(LATEST, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        asset = next(a for a in json.load(resp)["assets"] if a["name"] == asset_name())
    app = running_app()
    part = app.with_name(app.name + ".download")
    log(f"downloading Free(up)Mail {info['latest']} ({asset['size'] / 1e6:.1f} MB)")
    digest = hashlib.sha256()
    with urllib.request.urlopen(urllib.request.Request(asset["browser_download_url"], headers=HEADERS),
                                timeout=60) as resp, part.open("wb") as out:
        while chunk := resp.read(1 << 20):
            out.write(chunk)
            digest.update(chunk)
    if digest.hexdigest() != asset["digest"].split(":", 1)[1] or part.stat().st_size != asset["size"]:
        part.unlink(missing_ok=True)
        raise RuntimeError("the download didn't match the published release, so nothing was changed")
    old = old_copy(app)
    old.unlink(missing_ok=True)
    os.replace(app, old)  # a running program can be renamed (not deleted) on Windows
    try:
        os.replace(part, app)
    except OSError:
        os.replace(old, app)  # put this version back rather than leave nothing there
        raise
    if sys.platform != "win32":
        app.chmod(0o755)
    log(f"installed {info['latest']}; restarting")
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([str(app), "--after-update"], close_fds=True, creationflags=flags)
    quit_app()


def old_copy(app: Path) -> Path:
    return app.with_name(f"{app.stem}.old{app.suffix}")


def wait_for_old_copy(already_running) -> None:
    """Started by install(): let the old copy finish closing before opening."""
    deadline = time.time() + 30
    while already_running() and time.time() < deadline:
        time.sleep(0.5)


def remove_old_copy() -> None:
    """Delete the copy an update replaced (it may take a moment to close)."""
    app = running_app()
    if not app or not old_copy(app).exists():
        return

    def remove():
        for _ in range(60):
            try:
                old_copy(app).unlink(missing_ok=True)
                return
            except OSError:
                time.sleep(1)
    threading.Thread(target=remove, daemon=True).start()
