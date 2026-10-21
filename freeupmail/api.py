"""JSON API behind the GUI: background jobs, heartbeat and route handlers."""

import threading
import time
import webbrowser

from . import cleanup, filters, imapmail, outlook, providers, rules, store, views


class Job:
    """One background task at a time (scan, cleanup, restore...) with a shared log."""

    def __init__(self):
        self.lock = threading.Lock()
        self.running = ""
        self.log: list[str] = []

    def write(self, line: str) -> None:
        self.log = (self.log + [line])[-300:]

    def start(self, name: str, fn) -> bool:
        with self.lock:
            if self.running:
                return False
            self.running = name
        self.write(f"--- {name}")

        def run():
            try:
                fn(self.write)
            except Exception as exc:  # noqa: BLE001 - surface any failure in the GUI log
                self.write(f"ERROR: {exc}")
            finally:
                self.running = ""
                self.write("--- done")

        threading.Thread(target=run, daemon=True).start()
        return True


class Heartbeat:
    """Open GUI pages ping regularly; the launcher stops the app once they go quiet."""

    def __init__(self):
        self.last_ping = 0.0
        self.last_bye = 0.0

    def ping(self) -> None:
        self.last_ping = time.time()

    def bye(self) -> None:
        self.last_bye = time.time()


JOB = Job()
HEART = Heartbeat()


def _start(name: str, fn) -> dict:
    return {"ok": True} if JOB.start(name, fn) else {"error": "Another task is still running."}


# ---------- GET ----------

def state(_: dict) -> dict:
    db = store.connect()
    counts = dict(db.execute("SELECT account, COUNT(*) FROM messages GROUP BY account"))
    scans = {a: (t, s) for a, t, s in db.execute("SELECT account, scanned_at, status FROM scans")}
    accounts = []
    for email, (kind, _) in providers.accounts().items():
        scanned_at, status = scans.get(email, ("", "not scanned"))
        accounts.append({"email": email, "type": kind, "messages": counts.get(email, 0),
                         "scanned_at": scanned_at, "status": status})
    trashed = db.execute("SELECT COUNT(*) FROM trashed WHERE restored = 0").fetchone()[0]
    return {
        "accounts": accounts,
        "settings": store.get_settings(db, rules.DEFAULT_SETTINGS),
        "trashed": trashed,
        "job": {"running": JOB.running, "log": JOB.log[-150:]},
    }


def senders_list(_: dict) -> list:
    return views.senders(store.connect())


def plan(_: dict) -> dict:
    return {acc: len(items) for acc, items in views.cleanup_plan(store.connect()).items()}


def message(query: dict) -> dict:
    account, msg_id = query.get("account", ""), query.get("msg_id", "")
    entry = providers.accounts().get(account)
    if not entry:
        return {"error": f"{account} is not connected"}
    if entry[0] == "outlook":
        return outlook.fetch_message(msg_id)
    result = providers.module(entry[1]).fetch_message(entry[1], msg_id)
    row = store.connect().execute(
        "SELECT rfc_id FROM messages WHERE account = ? AND msg_id = ?", (account, msg_id)
    ).fetchone()
    result["web_link"] = providers.web_link(entry, row[0] if row else "")
    return result


# ---------- POST ----------

def emails(body: dict) -> dict:
    return views.emails(store.connect(), body)


def actions(body: dict) -> dict:
    """body.changes: [[account, from_addr, action]]; action AUTO clears the override."""
    changes = body.get("changes") or []
    if any(c[2] not in (*rules.ACTIONS, "AUTO") for c in changes):
        return {"error": "bad action"}
    db = store.connect()
    with db:
        db.executemany("DELETE FROM sender_actions WHERE account = ? AND from_addr = ?",
                       [(a, f) for a, f, act in changes if act == "AUTO"])
        db.executemany("INSERT OR REPLACE INTO sender_actions VALUES (?, ?, ?)",
                       [(a, f, act) for a, f, act in changes if act != "AUTO"])
    return {"ok": True, "changed": len(changes)}


def pin(body: dict) -> dict:
    items = [tuple(i) for i in body.get("items") or []]
    db = store.connect()
    with db:
        if body.get("pinned", True):
            db.executemany("INSERT OR IGNORE INTO pinned VALUES (?, ?)", items)
        else:
            db.executemany("DELETE FROM pinned WHERE account = ? AND msg_id = ?", items)
    return {"ok": True}


def settings(body: dict) -> dict:
    db = store.connect()
    with db:
        for key, value in body.items():
            if key in rules.DEFAULT_SETTINGS:
                db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, str(value)))
    return {"ok": True}


def providers_info(_: dict) -> dict:
    """Known providers for the Add account form: domain -> server, app-password page."""
    known = {d: {"host": p[0], "help": p[2]} for d, p in imapmail.PROVIDERS.items()}
    return {"providers": known, "microsoft": sorted(imapmail.MICROSOFT_DOMAINS)}


def add_account(body: dict) -> dict:
    addr = body.get("email", "").strip().lower()
    if "@" not in addr or not body.get("password"):
        return {"error": "Enter the email address and its password."}
    if imapmail.is_microsoft(addr) and not body.get("host"):
        return {"error": "Outlook / Hotmail / Live addresses connect with the Connect Outlook button above."}
    account = {"email": addr, "password": body["password"]}
    if body.get("host"):
        account["host"] = body["host"].strip().lower()
        account["port"] = int(body.get("port") or 993)
    if body.get("user"):
        account["user"] = body["user"].strip()
    error = imapmail.test_login(account)
    if error:
        return {"error": f"The mail server rejected the login: {error}"}
    config = store.load_config()
    config["imap"] = [a for a in config["imap"] if a["email"] != addr] + [account]
    store.save_config(config)
    return {"ok": True}


def remove_account(body: dict) -> dict:
    ms = outlook.connected()
    if ms and ms.get("email") == body["email"]:
        outlook.disconnect()
        return {"ok": True}
    config = store.load_config()
    config["imap"] = [a for a in config["imap"] if a["email"] != body["email"]]
    store.save_config(config)
    return {"ok": True}


def scan(body: dict) -> dict:
    only = body.get("account")

    def run(log):
        db = store.connect()
        for acc in providers.imap_accounts(only):
            providers.module(acc).scan_account(acc, db, log)
        ms = outlook.connected()
        if ms and only in (None, "", ms["email"]):
            outlook.scan(db, log)

    return _start("scan", run)


def run_cleanup(body: dict) -> dict:
    if not body.get("confirm"):
        return {"error": "not confirmed"}
    return _start("cleanup", lambda log: cleanup.run_cleanup(body.get("account") or None, log))


def restore(body: dict) -> dict:
    items = body.get("items") or []
    if body.get("all"):
        db = store.connect()
        query = "SELECT account, msg_id FROM trashed WHERE restored = 0"
        params: tuple = ()
        if body.get("account"):
            query, params = query + " AND account = ?", (body["account"],)
        items = [list(r) for r in db.execute(query, params)]
    if not items:
        return {"error": "Nothing to restore."}
    return _start("restore", lambda log: cleanup.run_restore(items, log))


def outlook_connect(_: dict) -> dict:
    if JOB.running:
        return {"error": "Another task is still running."}
    try:
        flow = outlook.start_sign_in()
    except RuntimeError as exc:
        return {"error": str(exc)}

    def run(log):
        if outlook.finish_sign_in(flow, log):
            outlook.scan(store.connect(), log)

    JOB.start("Outlook sign-in", run)
    return {"user_code": flow["user_code"], "verification_uri": flow["verification_uri"]}


def outlook_rules(_: dict) -> dict:
    if not outlook.connected():
        return {"error": "Outlook is not connected"}
    return _start("Outlook rules", lambda log: filters.outlook_rules(store.connect(), log))


def open_link(body: dict) -> dict:
    """Open a link in the user's normal browser (signed in to their webmail), not the
    app window's separate profile."""
    url = str(body.get("url", ""))
    if not url.lower().startswith(("https://", "http://", "mailto:")):
        return {"error": "unsupported link"}
    webbrowser.open(url)
    return {"ok": True}


def ping(_: dict) -> dict:
    HEART.ping()
    return {"ok": True}


def bye(_: dict) -> dict:
    HEART.bye()
    return {"ok": True}


GET_ROUTES = {
    "/api/state": state,
    "/api/senders": senders_list,
    "/api/plan": plan,
    "/api/message": message,
    "/api/providers": providers_info,
}
POST_ROUTES = {
    "/api/emails": emails,
    "/api/actions": actions,
    "/api/pin": pin,
    "/api/settings": settings,
    "/api/accounts": add_account,
    "/api/accounts/remove": remove_account,
    "/api/scan": scan,
    "/api/cleanup": run_cleanup,
    "/api/restore": restore,
    "/api/outlook/connect": outlook_connect,
    "/api/outlook/rules": outlook_rules,
    "/api/open": open_link,
    "/api/ping": ping,
    "/api/bye": bye,
}
# sendBeacon cannot set custom headers; closing the app is harmless to allow.
HEADERLESS_POSTS = {"/api/bye"}
