"""The local server refuses API calls without this run's key or from a foreign Host,
and serves the page itself. Run: python -m tests.test_server
"""

import json
import os
import tempfile
import threading
import urllib.error
import urllib.request

os.environ["FREEUPMAIL_HOME"] = tempfile.mkdtemp()

from freeupmail import server


def call(port, path, headers=None, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", body, headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def main():
    httpd = server.bind()
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    key = {"X-FreeUpMail": server.TOKEN}
    try:
        assert call(port, "/")[0] == 200
        assert call(port, "/static/js/main.js")[0] == 200
        assert call(port, "/static/../server.py")[0] == 404
        assert call(port, "/api/state")[0] == 403, "no key"
        assert call(port, "/api/state", {"X-FreeUpMail": "1"})[0] == 403, "wrong key"
        assert call(port, "/api/state", {**key, "Host": f"evil.example:{port}"})[0] == 403, "foreign Host"
        status, body = call(port, "/api/state", key)
        assert status == 200 and json.loads(body)["accounts"] == [], body
        assert call(port, f"/api/bye?t={server.TOKEN}", body=b"{}")[0] == 200
        assert call(port, "/api/settings", key, json.dumps({"theme": "aero"}).encode())[0] == 200
        assert call(port, "/api/settings", body=json.dumps({"theme": "aero"}).encode())[0] == 403
        second = server.bind()  # e.g. another Windows user's copy: must get its own port
        assert second and second.server_address[1] != port
        second.server_close()
    finally:
        httpd.shutdown()
    print("server: all checks passed")


if __name__ == "__main__":
    main()
