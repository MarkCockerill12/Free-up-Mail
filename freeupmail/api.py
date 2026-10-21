"""JSON API behind the GUI: background jobs, heartbeat and the read/account routes.
Routes that start mailbox jobs (scan, cleanup, restore, rules) live in jobs.py."""

import os
import subprocess
import sys
import threading
import time
import webbrowser

from . import (
    automation,
    backup,
    filters,
    imapmail,
    perms,
    providers,
    rules,
    store,
    views,
)


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
            except perms.NotAllowed as exc:
                self.write(f"STOPPED: {exc}")
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


def start(name: str, fn) -> dict:
    return {"ok": True} if JOB.start(name, fn) else {"error": "Another task is still running."}


# ---------- GET ----------

def hello(_: dict) -> dict:
    return {"app": "freeupmail"}


def state(_: dict) -> dict:
    db = store.connect()
    counts = dict(db.execute("SELECT account, COUNT(*) FROM messages GROUP BY account"))
    scans = {a: (t, s) for a, t, s in db.execute("SELECT account, scanned_at, status FROM scans")}
    grants = perms.all_grants(db)
    accounts = []
    for email, (kind, cfg) in providers.accounts().items():
        scanned_at, status = scans.get(email, ("", "not scanned"))
        accounts.append({"email": email, "type": kind, "messages": counts.get(email, 0), "scanned_at": scanned_at,
                         "status": status, "perms": grants.get(email, []), "demo": bool((cfg or {}).get("demo"))})
    trashed = db.execute("SELECT COUNT(*) FROM trashed WHERE restored = 0").fetchone()[0]
    return {
        "accounts": accounts,
        "perm_labels": perms.PERMS,
        "settings": store.get_settings(db, rules.DEFAULT_SETTINGS),
        "trashed": trashed,
        "backups": backup.stats(db),
        "demo": bool(store.load_config().get("demo")),
        "data_folder": str(store.APP_DIR),
        "job": {"running": JOB.running, "log": JOB.log[-150:]},
    }


def senders_list(_: dict) -> list:
    return views.senders(store.connect())


def plan(_: dict) -> dict:
    return {acc: len(items) for acc, items in views.cleanup_plan(store.connect()).items()}


def message(query: dict) -> dict:
    db = store.connect()
    account, msg_id = query.get("account", ""), query.get("msg_id", "")
    try:
        box = providers.Mailbox(db, account)
        result = box.fetch(msg_id)
    except perms.NotAllowed as exc:
        return {"error": str(exc), "permission": "read"}
    row = db.execute("SELECT rfc_id FROM messages WHERE account = ? AND msg_id = ?", (account, msg_id)).fetchone()
    result.setdefault("web_link", providers.web_link(providers.accounts().get(account), row[0] if row else ""))
    return result


def providers_info(_: dict) -> dict:
    """Known providers for the Add account form: domain -> server, app-password page."""
    known = {d: {"host": p[0], "help": p[2]} for d, p in imapmail.PROVIDERS.items()}
    return {"providers": known, "microsoft": sorted(imapmail.MICROSOFT_DOMAINS)}


def rules_info(_: dict) -> dict:
    db = store.connect()
    own = [{**r, "summary": automation.describe(r), "exports": filters.exportable(r)} for r in automation.load(db)]
    return {"rules": own, "fields": automation.FIELDS, "ops": automation.OPS, "choices": automation.CHOICES,
            "actions": automation.ACTIONS, "action_perm": automation.ACTION_PERM}


# ---------- POST: reading & decisions ----------

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


# ---------- POST: accounts & permissions ----------

def _clean_perms(values) -> list[str]:
    return [p for p in values or [] if p in perms.PERMS]


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
    perms.set_grants(store.connect(), addr, _clean_perms(body.get("perms", perms.DEFAULT_NEW)))
    return {"ok": True}


def remove_account(body: dict) -> dict:
    db = store.connect()
    if providers.outlook_email() == body["email"]:
        from . import outlook
        outlook.disconnect()
    else:
        config = store.load_config()
        config["imap"] = [a for a in config["imap"] if a["email"] != body["email"]]
        store.save_config(config)
    perms.forget(db, body["email"])
    return {"ok": True}


def set_grants(body: dict) -> dict:
    account = body.get("account", "")
    if account not in providers.accounts():
        return {"error": f"{account} is not connected"}
    perms.set_grants(store.connect(), account, _clean_perms(body.get("perms")))
    return {"ok": True}


# ---------- POST: rules ----------

def save_rule(body: dict) -> dict:
    try:
        return {"ok": True, "id": automation.save(store.connect(), body)}
    except ValueError as exc:
        return {"error": str(exc)}


def delete_rule(body: dict) -> dict:
    automation.delete(store.connect(), body.get("id", 0))
    return {"ok": True}


def preview_rule(body: dict) -> dict:
    try:
        rule = automation.validate(body)
    except ValueError as exc:
        return {"error": str(exc)}
    return views.rule_preview(store.connect(), rule)


# ---------- POST: misc ----------

def _open_path(path: str) -> None:
    if sys.platform == "win32":
        os.startfile(path)
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])


def open_folder(body: dict) -> dict:
    """Show the data or backups folder in the file manager."""
    path = backup.folder() if body.get("what") == "backups" else store.APP_DIR
    path.mkdir(parents=True, exist_ok=True)
    _open_path(str(path))
    return {"ok": True}


def prune_backups(body: dict) -> dict:
    days = int(body.get("days") or 0)
    if days < 1:
        return {"error": "Pick how old backups must be before they are deleted."}
    return {"ok": True, "deleted": backup.prune(store.connect(), days)}


def open_link(body: dict) -> dict:
    """Open a link in the user's normal browser (signed in to their webmail), not the
    app window's separate profile."""
    url = str(body.get("url", ""))
    if not url.lower().startswith(("https://", "http://", "mailto:")):
        return {"error": "unsupported link"}
    webbrowser.open(url)
    return {"ok": True}


def focus(_: dict) -> dict:
    from . import window
    return {"ok": window.bring_to_front()}


def ping(_: dict) -> dict:
    HEART.ping()
    return {"ok": True}


def bye(_: dict) -> dict:
    HEART.bye()
    return {"ok": True}


GET_ROUTES = {
    "/api/hello": hello,
    "/api/state": state,
    "/api/senders": senders_list,
    "/api/plan": plan,
    "/api/message": message,
    "/api/providers": providers_info,
    "/api/rules": rules_info,
}
POST_ROUTES = {
    "/api/emails": emails,
    "/api/actions": actions,
    "/api/pin": pin,
    "/api/settings": settings,
    "/api/accounts": add_account,
    "/api/accounts/remove": remove_account,
    "/api/grants": set_grants,
    "/api/rules/save": save_rule,
    "/api/rules/delete": delete_rule,
    "/api/rules/preview": preview_rule,
    "/api/backups/prune": prune_backups,
    "/api/open-folder": open_folder,
    "/api/open": open_link,
    "/api/focus": focus,
    "/api/ping": ping,
    "/api/bye": bye,
}
