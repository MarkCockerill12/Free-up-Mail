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
    filters,
    jobs,  # noqa: F401 - importing it adds its routes to api
    providers,
    store,
)

PORTS = range(8765, 8786)
# Read through importlib.resources so the page also loads from inside a .pyz or an .exe.
WEB_DIR = resources.files("freeupmail") / "web"
LOG_PATH = store.APP_DIR / "freeupmail.log"
WINDOW_PROFILE = store.APP_DIR / "app-window"
SESSION_PATH = store.APP_DIR / "session.json"
TOKEN = secrets.token_urlsafe(24)
CONTENT_TYPES = {".html": "text/html", ".js": "text/javascript", ".css": "text/css",
                 ".svg": "image/svg+xml", ".ico": "image/x-icon", ".png": "image/png"}
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


def open_window(url: str) -> subprocess.Popen | None:
    """Edge/Chrome in app mode with its own profile, so its process lives exactly as
    long as the window. Falls back to the default browser."""
    browser = next(filter(None, (shutil.which(b) or (b if Path(b).is_file() else None) for b in BROWSERS)), None)
    if not browser:
        webbrowser.open(url)
        return None
    return subprocess.Popen([
        browser, f"--app={url}", f"--user-data-dir={WINDOW_PROFILE}",
        "--no-first-run", "--no-default-browser-check", "--window-size=1500,950",
    ])


def wait_until_closed(window: subprocess.Popen | None, started: float) -> None:
    heart = api.HEART
    while True:
        time.sleep(2)
        if api.JOB.running:
            continue  # never stop in the middle of a scan or cleanup
        now = time.time()
        if not heart.last_ping:
            if now - started > 120:
                return  # window never connected
            continue
        if heart.last_bye > heart.last_ping and now - heart.last_bye > 5:
            return  # page closed and nothing reconnected (a reload pings again at once)
        # A minimised window may only ping once a minute; Edge can also linger in the
        # background after its window closes, so a live process only extends the wait.
        window_alive = window is not None and window.poll() is None
        limit = 180 if window_alive else 15 if window is not None else 100
        if now - heart.last_ping > limit:
            return


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
        if app_mode:
            open_window(running)
        elif "--no-browser" not in sys.argv:
            webbrowser.open(running)
        return
    server = bind()
    if server is None:
        _fatal(f"Free(up)Mail could not start: ports {PORTS.start}-{PORTS.stop - 1} are all in use.")
        return
    url = app_url(server.server_address[1], TOKEN)
    store.write_private(SESSION_PATH, json.dumps({"port": server.server_address[1], "token": TOKEN, "pid": os.getpid()}))
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} Free(up)Mail running on port {server.server_address[1]}", flush=True)
    try:
        if app_mode:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            wait_until_closed(open_window(url), time.time())
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} window closed, stopping", flush=True)
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
