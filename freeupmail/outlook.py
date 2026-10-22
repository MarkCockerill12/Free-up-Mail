"""Outlook.com / Hotmail / Microsoft 365 via Microsoft Graph: device-code sign-in, read-only scan, preview,
move to Deleted Items / restore, and inbox rules.

Signs in through Microsoft's own public "Graph Command Line Tools" client (the one
Connect-MgGraph uses), so no app registration is needed.
"""

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime

from . import store

CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"
# "common" accepts personal and work/school accounts. Tokens remember the authority they
# came from; older ones were issued by "consumers".
AUTH = "https://login.microsoftonline.com/common/oauth2/v2.0"
LEGACY_AUTH = "https://login.microsoftonline.com/consumers/oauth2/v2.0"
GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = "offline_access User.Read Mail.ReadWrite MailboxSettings.ReadWrite"
TOKENS_PATH = store.APP_DIR / "outlook_tokens.json"  # {email: sign-in token}, one per account
OLD_TOKEN_PATH = store.APP_DIR / "outlook_token.json"  # from when only one account was possible
SKIP_FOLDERS = ("deleteditems", "junkemail", "sentitems", "drafts", "outbox")
INLINE_IMAGE_LIMIT = 3_000_000
CID_RE = re.compile(r"cid:([^\"'\s)>]+)", re.IGNORECASE)

Log = Callable[[str], None]


def _post_form(url: str, data: dict) -> dict:
    req = urllib.request.Request(url, urllib.parse.urlencode(data).encode())
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        return json.load(exc)


# ---------- accounts & sign-in ----------

# Which Outlook account the Graph calls on this thread are for (see use()).
_account: ContextVar[str] = ContextVar("outlook_account", default="")
_tokens_lock = threading.RLock()


def _tokens() -> dict[str, dict]:
    with _tokens_lock:
        if OLD_TOKEN_PATH.exists() and not TOKENS_PATH.exists():
            old = json.loads(OLD_TOKEN_PATH.read_text(encoding="utf-8"))
            store.write_private(TOKENS_PATH, json.dumps({old["email"]: old} if old.get("email") else {}))
            OLD_TOKEN_PATH.unlink()
        if not TOKENS_PATH.exists():
            return {}
        return json.loads(TOKENS_PATH.read_text(encoding="utf-8"))


def accounts() -> list[str]:
    """Every signed-in Outlook account."""
    return sorted(_tokens())


@contextmanager
def use(email: str):
    """Run the Graph calls inside this block as `email`."""
    reset = _account.set(email)
    try:
        yield
    finally:
        _account.reset(reset)


def bound(fn):
    """`fn` for a worker thread, which doesn't inherit the account chosen with use()."""
    email = current()

    def run(*args):
        with use(email):
            return fn(*args)
    return run


def current() -> str:
    email = _account.get()
    if email:
        return email
    known = accounts()
    if len(known) == 1:
        return known[0]
    raise RuntimeError("Outlook is not connected" if not known else "No Outlook account chosen for this call")


def connected() -> dict | None:
    """The current account's sign-in, if there is one."""
    try:
        return _tokens().get(current())
    except RuntimeError:
        return None


def disconnect(email: str) -> None:
    with _tokens_lock:
        tokens = _tokens()
        tokens.pop(email, None)
        store.write_private(TOKENS_PATH, json.dumps(tokens))


def _save_token(tok: dict, email: str) -> None:
    with _tokens_lock:
        tokens = _tokens()
        saved = tokens.get(email) or {"authority": AUTH}
        saved.update({
            "email": email,
            "access_token": tok["access_token"],
            "refresh_token": tok.get("refresh_token", saved.get("refresh_token")),
            "expires_at": time.time() + int(tok.get("expires_in", 3600)),
        })
        tokens[email] = saved
        store.write_private(TOKENS_PATH, json.dumps(tokens))


def start_sign_in() -> dict:
    """Returns Microsoft's device-code response (user_code, verification_uri, ...)."""
    flow = _post_form(f"{AUTH}/devicecode", {"client_id": CLIENT_ID, "scope": SCOPES})
    if "user_code" not in flow:
        raise RuntimeError(flow.get("error_description", str(flow)))
    return flow


def finish_sign_in(flow: dict, log: Log) -> str:
    """Waits for the user to approve; returns the signed-in address ("" if it failed).
    Another account is added alongside the ones already connected."""
    deadline = time.time() + int(flow.get("expires_in", 900))
    interval = int(flow.get("interval", 5))
    log(f"waiting for sign-in with code {flow['user_code']} at {flow['verification_uri']}")
    while time.time() < deadline:
        time.sleep(interval)
        tok = _post_form(f"{AUTH}/token", {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": CLIENT_ID,
            "device_code": flow["device_code"],
        })
        if "access_token" in tok:
            req = urllib.request.Request(f"{GRAPH}/me?$select=userPrincipalName,mail",
                                         headers={"Authorization": f"Bearer {tok['access_token']}"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                me = json.load(resp)
            email = (me.get("mail") or me.get("userPrincipalName") or "outlook").lower()
            _save_token(tok, email)
            log(f"signed in as {email}")
            return email
        error = tok.get("error")
        if error == "slow_down":
            interval += 5
        elif error != "authorization_pending":
            log(f"sign-in failed: {tok.get('error_description', tok)}")
            return ""
    log("sign-in code expired - click Connect Outlook again")
    return ""


_refresh_lock = threading.Lock()


def _access_token() -> str:
    with _refresh_lock:  # backups fetch in parallel: only one of them refreshes the sign-in
        return _fresh_token()


def _fresh_token() -> str:
    saved = connected()
    if not saved:
        raise RuntimeError("Outlook is not connected")
    if saved["expires_at"] - 60 > time.time():
        return saved["access_token"]
    tok = _post_form(f"{saved.get('authority', LEGACY_AUTH)}/token", {
        "grant_type": "refresh_token", "client_id": CLIENT_ID,
        "refresh_token": saved["refresh_token"], "scope": SCOPES,
    })
    if "access_token" not in tok:
        raise RuntimeError(f"Outlook sign-in expired, reconnect: {tok.get('error_description', tok)}")
    _save_token(tok, saved["email"])
    return tok["access_token"]


# ---------- Graph ----------

def graph(method: str, path: str, body: dict | None = None) -> dict:
    url = path if path.startswith("https://") else GRAPH + path
    for _ in range(5):
        req = urllib.request.Request(
            url, json.dumps(body).encode() if body is not None else None, method=method,
            headers={"Authorization": f"Bearer {_access_token()}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 503, 504):
                time.sleep(int(exc.headers.get("Retry-After", 5)))
                continue
            raise RuntimeError(f"Graph {method} {path} -> {exc.code}: {exc.read()[:300]!r}") from exc
    raise RuntimeError(f"Graph {method} {path}: throttled too long")


def graph_raw(path: str) -> bytes:
    """GET returning bytes (e.g. an email's MIME source from /$value)."""
    for _ in range(5):
        req = urllib.request.Request(GRAPH + path, headers={"Authorization": f"Bearer {_access_token()}"})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 503, 504):
                time.sleep(int(exc.headers.get("Retry-After", 5)))
                continue
            raise RuntimeError(f"Graph GET {path} -> {exc.code}") from exc
    raise RuntimeError(f"Graph GET {path}: throttled too long")


def pages(path: str):
    url: str | None = path
    while url:
        data = graph("GET", url)
        yield from data.get("value", [])
        url = data.get("@odata.nextLink")


def _addr(recipient: dict | None) -> tuple[str, str]:
    email_address = (recipient or {}).get("emailAddress") or {}
    return (email_address.get("address") or "").lower(), email_address.get("name") or ""


def _folder_id(name: str) -> str | None:
    try:
        return graph("GET", f"/me/mailFolders/{name}?$select=id")["id"]
    except RuntimeError:
        return None


def _quote(msg_id: str) -> str:
    return urllib.parse.quote(msg_id, safe="")


# ---------- scan ----------

def scan(db, log: Log) -> None:
    account = current()
    now = datetime.now(UTC).isoformat(timespec="seconds")
    log(f"== scanning {account}")
    counts: dict[str, int] = {}
    for msg in pages("/me/mailFolders/sentitems/messages?$select=toRecipients,ccRecipients,bccRecipients&$top=500"):
        for field in ("toRecipients", "ccRecipients", "bccRecipients"):
            for rcpt in msg.get(field) or []:
                addr, _ = _addr(rcpt)
                if addr and addr != account:
                    counts[addr] = counts.get(addr, 0) + 1
    log(f"sent mail: {len(counts)} people you have emailed")
    store.replace_contacts(db, account, counts)

    skip = {fid for fid in map(_folder_id, SKIP_FOLDERS) if fid}
    inbox = _folder_id("inbox")
    rows = []
    select = "id,from,subject,receivedDateTime,isRead,parentFolderId,flag,inferenceClassification,internetMessageId"
    for msg in pages(f"/me/messages?$select={select}&$orderby=receivedDateTime%20desc&$top=500"):
        if msg.get("parentFolderId") in skip:
            continue
        addr, name = _addr(msg.get("from"))
        labels = []
        if msg.get("parentFolderId") == inbox:
            labels.append("inbox")
        if (msg.get("flag") or {}).get("flagStatus") == "flagged":
            labels.append("starred")
        if msg.get("inferenceClassification") == "other":
            labels.append("other")
        rows.append({
            "account": account, "msg_id": msg["id"], "uid": 0, "from_addr": addr, "from_name": name,
            "from_domain": addr.rpartition("@")[2], "subject": msg.get("subject") or "",
            "date": msg.get("receivedDateTime") or "", "seen": int(bool(msg.get("isRead"))),
            "list_unsub": 0, "list_id": "", "labels": ",".join(labels), "unsub_url": "",
            "rfc_id": (msg.get("internetMessageId") or "").strip("<>"),
        })
        if len(rows) % 2000 == 0:
            log(f"  {len(rows)} messages")
    _detect_mailing_lists(rows, log)
    store.replace_messages(db, account, rows)
    store.record_scan(db, account, now, "ok")
    log(f"stored {len(rows)} messages")


def _detect_mailing_lists(rows: list[dict], log: Log) -> None:
    """Graph's message list cannot return List-Unsubscribe for personal accounts, so read
    the full headers of each sender's newest email (20 per batch) and flag that row."""
    latest: dict[str, dict] = {}
    for row in rows:  # newest first
        if row["from_addr"]:
            latest.setdefault(row["from_addr"], row)
    sample = list(latest.values())
    log(f"checking {len(sample)} senders for mailing-list headers")
    found = 0
    for i in range(0, len(sample), 20):
        chunk = sample[i : i + 20]
        requests = [{
            "id": str(n), "method": "GET",
            "url": f"/me/messages/{_quote(r['msg_id'])}?$select=internetMessageHeaders",
        } for n, r in enumerate(chunk)]
        for resp in graph("POST", "/$batch", {"requests": requests}).get("responses", []):
            if resp.get("status") != 200:
                continue
            headers = (resp.get("body") or {}).get("internetMessageHeaders") or []
            value = next((h["value"] for h in headers if h["name"].lower() == "list-unsubscribe"), None)
            if value is not None:
                row = chunk[int(resp["id"])]
                row["list_unsub"], row["unsub_url"] = 1, store.unsub_link(value)
                found += 1
        if (i // 20) % 15 == 0:
            log(f"  {min(i + 20, len(sample))}/{len(sample)} senders checked, {found} mailing lists")
    log(f"{found} senders are mailing lists")


# ---------- preview ----------

def fetch_message(msg_id: str) -> dict:
    try:
        msg = graph("GET", f"/me/messages/{_quote(msg_id)}?$select=body,webLink,internetMessageHeaders,hasAttachments")
    except RuntimeError as exc:
        if "404" in str(exc):
            return {"error": "This email is no longer in the mailbox (deleted or moved)."}
        raise
    body = msg.get("body") or {}
    html = body.get("content", "") if body.get("contentType") == "html" else ""
    text = body.get("content", "") if body.get("contentType") == "text" else ""
    headers = {h["name"].lower(): h["value"] for h in msg.get("internetMessageHeaders") or []}
    attachments: list[str] = []
    if msg.get("hasAttachments") or "cid:" in html:
        images, budget = {}, INLINE_IMAGE_LIMIT
        for att in pages(f"/me/messages/{_quote(msg_id)}/attachments"):
            if att.get("isInline") and att.get("contentId") and att.get("contentBytes"):
                if att.get("size", 0) <= budget:
                    budget -= att.get("size", 0)
                    images[att["contentId"].strip("<>")] = f"data:{att.get('contentType')};base64,{att['contentBytes']}"
            else:
                attachments.append(att.get("name") or "attachment")
        html = CID_RE.sub(lambda m: images.get(m[1], m[0]), html)
    return {
        "html": html, "text": text, "attachments": attachments,
        "unsub": store.unsub_link(headers.get("list-unsubscribe")),
        "web_link": msg.get("webLink", ""),
    }


# ---------- move / rules ----------
