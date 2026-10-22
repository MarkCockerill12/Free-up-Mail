"""Local GUI server and launcher.

FreeUpMail.exe / python -m freeupmail    own app window; quits when the window closes
python -m freeupmail --browser           serve + open the default browser (Ctrl+C to stop)
python -m freeupmail --no-browser        serve only (prints the address to open)
python -m freeupmail --demo              try it with made-up emails (nothing real is touched)

The server only listens on 127.0.0.1, and every /api call must carry a random key made
when the app starts (it reaches the window through the address it opens). Other users
and programs on the computer therefore can't read your mail through it.
"""

import ctypes
import hmac
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import (
    api,
    backup,
    filters,
    jobs,  # noqa: F401 - importing it adds its routes to api
    providers,
    store,
    updater,
    window,
)

PORTS = range(8765, 8786)
# Read through importlib.resources so the page also loads from inside a .pyz or an .exe.
WEB_DIR = resources.files("freeupmail") / "web"
LOG_PATH = store.APP_DIR / "freeupmail.log"
WINDOW_PROFILE = store.APP_DIR / "app-window"  # fallback Edge/Chrome window
WEBVIEW_DATA = store.APP_DIR / "window"  # the native window's own storage
SESSION_PATH = store.APP_DIR / "session.json"
TOKEN = secrets.token_urlsafe(24)
CONTENT_TYPES = {".html": "text/html", ".js": "text/javascript", ".css": "text/css",
                 ".svg": "image/svg+xml", ".ico": "image/x-icon", ".png": "image/png",
                 ".woff2": "font/woff2"}
# Chromium browsers can open a page as a standalone app window.
PROGRAM_DIRS = [os.environ.get(v, "") for v in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")]
BROWSERS = [
    *(str(Path(d, "Microsoft", "Edge", "Application", "msedge.exe")) for d in PROGRAM_DIRS if d),
    *(str(Path(d, "Google", "Chrome", "Application", "chrome.exe")) for d in PROGRAM_DIRS if d),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge", "brave-browser",
]
EDGE_FEATURES_OFF = ("msImplicitSignin", "msEdgeSyncConsent", "Translate", "OptimizationHints", "MediaRouter",
                     "msShoppingAssistant", "msEdgeCollections", "msWebAssist", "SpareRendererForSitePerProcess",
                     "AutofillServerCommunication")
# None of the browser's background services: they only cost memory in a single-page app.
# Software drawing (--disable-gpu) halves the engine's memory (measured ~410 -> ~210 MB)
# and is plenty fast for lists and emails.
LEAN_ARGS = ("--disable-gpu", "--disable-background-networking", "--disable-component-update", "--disable-default-apps",
             "--disable-breakpad", "--no-pings", "--renderer-process-limit=2",
             "--disable-features=" + ",".join(EDGE_FEATURES_OFF))
allowed_hosts: set[str] = set()


def app_url(port: int, token: str) -> str:
    # The key travels in the #fragment, which browsers never send to any server.
    return f"http://127.0.0.1:{port}/#t={token}"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def _send(self, body: bytes, ctype: str, status: int = 200, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, status: int = 200):
        self._send(json.dumps(data).encode(), "application/json", status)

    def _guard(self) -> bool:
        # DNS-rebinding / cross-site protection: local host names only.
        if self.headers.get("Host") not in allowed_hosts:
            self._json({"error": "bad host"}, 403)
            return False
        return True

    def _authorised(self, query: dict) -> bool:
        given = self.headers.get("X-FreeUpMail") or query.get("t") or ""
        if hmac.compare_digest(given.encode(), TOKEN.encode()):
            return True
        self._json({"error": "locked", "locked": True}, 403)
        return False

    def _call(self, handler, arg):
        try:
            result = handler(arg)
        except Exception as exc:  # noqa: BLE001 - report to the GUI instead of dropping the socket
            print(f"error in {urlparse(self.path).path}: {exc!r}", flush=True)
            return self._json({"error": str(exc)}, 500)
        is_error = isinstance(result, dict) and "error" in result
        self._json(result, 400 if is_error else 200)

    def do_GET(self):
        if not self._guard():
            return
        url = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path == "/":
            return self._file("index.html")
        if url.path.startswith("/static/"):
            return self._file(url.path.removeprefix("/static/"))
        if not self._authorised(query):
            return
        if url.path == "/api/filters":
            return self._filters(query.get("account", ""))
        handler = api.GET_ROUTES.get(url.path)
        if handler:
            return self._call(handler, query)
        self._json({"error": "not found"}, 404)

    def _filters(self, account: str):
        kind = providers.accounts().get(account, ("",))[0]
        if kind == "gmail":
            body, ctype, ext = filters.gmail_xml(store.connect(), account), "application/xml", "xml"
        else:
            body, ctype, ext = filters.address_list(store.connect(), account), "text/plain", "txt"
        name = f"blocked-senders-{account.split('@')[0]}.{ext}"
        self._send(body.encode(), ctype, 200, {"Content-Disposition": f'attachment; filename="{name}"'})

    def _file(self, name: str):
        parts = name.split("/")
        if any(p in ("", ".", "..") or "\\" in p for p in parts):
            return self._json({"error": "not found"}, 404)
        res = WEB_DIR.joinpath(*parts)
        if not res.is_file():
            return self._json({"error": "not found"}, 404)
        self._send(res.read_bytes(), CONTENT_TYPES.get(Path(name).suffix, "application/octet-stream"))

    def do_POST(self):
        if not self._guard():
            return
        url = urlparse(self.path)
        # The key is a custom header, so other web pages can't send it (CORS preflight);
        # only the page-close beacon passes it in the query instead.
        if not self._authorised({k: v[0] for k, v in parse_qs(url.query).items()}):
            return
        handler = api.POST_ROUTES.get(url.path)
        if not handler:
            return self._json({"error": "not found"}, 404)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            body = {}
        self._call(handler, body if isinstance(body, dict) else {})


class Server(ThreadingHTTPServer):
    """Binds a port only if nothing else is using it. (Python's default lets a server on
    Windows share a port another program already holds, so requests could reach the wrong app.)"""

    allow_reuse_address = sys.platform != "win32"
    allow_reuse_port = False
    daemon_threads = True

    def server_bind(self):
        if sys.platform == "win32":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


# ---------- launcher ----------

def _focus_running(url: str) -> bool:
    """Ask an already-open Free(up)Mail to bring its own window to the front."""
    base, key = url.split("/#t=")
    req = urllib.request.Request(f"{base}/api/focus", b"{}", {"X-FreeUpMail": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return bool(json.load(resp).get("ok"))
    except (OSError, ValueError):
        return False


def already_running() -> str | None:
    """Address (with key) of a Free(up)Mail this user already has open, if any."""
    try:
        session = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
        req = urllib.request.Request(f"http://127.0.0.1:{session['port']}/api/hello",
                                     headers={"X-FreeUpMail": session["token"]})
        with urllib.request.urlopen(req, timeout=2) as resp:
            if json.load(resp).get("app") == "freeupmail":
                return app_url(session["port"], session["token"])
    except (OSError, ValueError, KeyError):
        pass
    return None


def bind() -> Server | None:
    """First free port from PORTS (another app, or another user's Free(up)Mail, may hold 8765)."""
    for port in PORTS:
        try:
            server = Server(("127.0.0.1", port), Handler)
        except OSError:
            continue
        allowed_hosts.update({f"127.0.0.1:{port}", f"localhost:{port}"})
        return server
    return None


def work_area() -> tuple[int, int, int, int] | None:
    """(left, top, width, height) of the main screen minus the taskbar, in the scaled
    pixels browsers use for window sizes. None when unknown (not Windows)."""
    if sys.platform != "win32":
        return None
    try:
        user32 = ctypes.windll.user32
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))  # real pixels for this thread
        rect = (ctypes.c_long * 4)()
        user32.SystemParametersInfoW(0x30, 0, rect, 0)  # SPI_GETWORKAREA
        scale = user32.GetDpiForSystem() / 96
    except (AttributeError, OSError):
        return None
    left, top, right, bottom = (round(v / scale) for v in rect)
    return left, top, right - left, bottom - top


def window_bounds(area) -> tuple[int, int, int, int]:
    """(x, y, width, height): up to 1500x950, never bigger than the screen, centred."""
    if not area:
        return 60, 40, 1280, 800
    left, top, width, height = area
    w, h = min(1500, width - 40), min(950, height - 30)
    return left + (width - w) // 2, top + (height - h) // 2, w, h


def _fit_saved_window(area) -> None:
    """The app window remembers its last size; shrink it if it no longer fits the screen
    (e.g. a laptop after a big monitor). Sizes that fit are left alone."""
    prefs = WINDOW_PROFILE / "Default" / "Preferences"
    if not area or not prefs.is_file():
        return
    try:
        data = json.loads(prefs.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    left, top, width, height = area
    x, y, w, h = window_bounds(area)
    changed = False

    def fix(node):
        nonlocal changed
        if not isinstance(node, dict):
            return
        if {"left", "top", "right", "bottom"} <= node.keys():
            fits = (node["left"] >= left - 10 and node["top"] >= top - 10
                    and node["right"] <= left + width + 10 and node["bottom"] <= top + height + 10)
            if not fits and not node.get("maximized"):
                node.update({"left": x, "top": y, "right": x + w, "bottom": y + h})
                changed = True
            return
        for child in node.values():
            fix(child)

    fix(data.get("browser", {}).get("app_window_placement"))
    fix(data.get("browser", {}).get("window_placement"))
    if changed:
        prefs.write_text(json.dumps(data), encoding="utf-8")


def open_window(url: str) -> subprocess.Popen | None:
    """Edge/Chrome in app mode with its own profile, so its process lives exactly as
    long as the window. Falls back to the default browser."""
    browser = next(filter(None, (shutil.which(b) or (b if Path(b).is_file() else None) for b in BROWSERS)), None)
    if not browser:
        webbrowser.open(url)
        return None
    area = work_area()
    _fit_saved_window(area)
    x, y, w, h = window_bounds(area)
    return subprocess.Popen([
        browser, f"--app={url}", f"--user-data-dir={WINDOW_PROFILE}", f"--window-size={w},{h}",
        f"--window-position={x},{y}", "--no-first-run", "--no-default-browser-check",
        # A private window just for this app: no browser extensions (they could read your
        # email on screen), no sync and no automatic sign-in to a browser profile...
        "--disable-extensions", "--disable-sync",
        *LEAN_ARGS,
    ])


def wait_until_closed(window: subprocess.Popen | None, started: float) -> str:
    """Block until the app window is gone; returns why, for the log."""
    heart = api.HEART
    awake = tick = time.time()
    while True:
        time.sleep(2)
        now = time.time()
        if now - tick > 30:
            awake = now  # the computer slept: give the page time to wake up and ping again
        tick = now
        if api.QUIT.is_set():
            return "restarting for an update"
        if api.JOB.running:
            continue  # never stop in the middle of a scan or cleanup
        if not heart.last_ping:
            if now - started > 120:
                return "window never connected"
            continue
        if heart.last_bye > heart.last_ping and now - heart.last_bye > 5:
            return "window closed"  # and nothing reconnected (a reload pings again at once)
        # A minimised or idle window can go quiet for many minutes (browsers throttle or
        # freeze background pages), so while its browser runs only closing the page stops us.
        if window is not None and window.poll() is None:
            continue
        if now - max(heart.last_ping, awake) > 600:
            return "no window for 10 minutes"


def _tidy_backups() -> None:
    try:
        removed = backup.prune_by_setting(store.connect())
    except Exception as exc:  # noqa: BLE001 - never stop the app over housekeeping
        print(f"backup tidy failed: {exc!r}", flush=True)
        return
    if removed:
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} deleted {removed} backups past their keep time", flush=True)


def _fatal(message: str) -> None:
    print(message, flush=True)
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(None, message, "Free(up)Mail", 0x10)


def main() -> None:
    app_mode = not {"--browser", "--no-browser"} & set(sys.argv)
    if app_mode:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if sys.stdout:  # started from a terminal: say where the output goes
            print(f"Free(up)Mail is opening in its own window. Log: {LOG_PATH}", flush=True)
        sys.stdout = sys.stderr = open(LOG_PATH, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    running = already_running()
    if running:
        if app_mode and not _focus_running(running):
            open_window(running)
        elif "--no-browser" not in sys.argv:
            webbrowser.open(running)
        return
    server = bind()
    if server is None:
        _fatal(f"Free(up)Mail could not start: ports {PORTS.start}-{PORTS.stop - 1} are all in use.")
        return
    url = app_url(server.server_address[1], TOKEN)
    threading.Thread(target=_tidy_backups, daemon=True).start()
    updater.remove_old_copy()
    threading.Thread(target=lambda: (api.QUIT.wait(), server.shutdown()), daemon=True).start()
    store.write_private(SESSION_PATH, json.dumps({"port": server.server_address[1], "token": TOKEN, "pid": os.getpid()}))
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} Free(up)Mail running on port {server.server_address[1]}", flush=True)
    try:
        if app_mode:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            bounds = window_bounds(work_area())
            if window.available() and window.run(url, bounds, str(WEBVIEW_DATA), " ".join(LEAN_ARGS)):
                while api.JOB.running:  # closed mid-scan/cleanup: finish it quietly first
                    time.sleep(1)
                why = "window closed"
            else:
                why = wait_until_closed(open_window(url), time.time())
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {why}, stopping", flush=True)
            server.shutdown()
            return
        print(f"Open {url}", flush=True)
        if "--no-browser" not in sys.argv:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    finally:
        SESSION_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
