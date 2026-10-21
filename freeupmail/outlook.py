"""Outlook.com / Hotmail / Microsoft 365 via Microsoft Graph: device-code sign-in, read-only scan, preview,
move to Deleted Items / restore, and inbox rules.

Signs in through Microsoft's own public "Graph Command Line Tools" client (the one
Connect-MgGraph uses), so no app registration is needed.
"""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime

from . import store

CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"
# "common" accepts personal and work/school accounts. Tokens remember the authority they
# came from; older ones were issued by "consumers".
AUTH = "https://login.microsoftonline.com/common/oauth2/v2.0"
LEGACY_AUTH = "https://login.microsoftonline.com/consumers/oauth2/v2.0"
GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = "offline_access User.Read Mail.ReadWrite MailboxSettings.ReadWrite"
TOKEN_PATH = store.APP_DIR / "outlook_token.json"
SKIP_FOLDERS = ("deleteditems", "junkemail", "sentitems", "drafts", "outbox")
RULE_PREFIX = "Free(up)Mail"
OLD_RULE_PREFIXES = (RULE_PREFIX, "Mail Tidy")  # rules from before the rename are replaced too
RULE_CHUNK = 40
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


# ---------- sign-in ----------

def connected() -> dict | None:
    if not TOKEN_PATH.exists():
        return None
    return json.loads(TOKEN_PATH.read_text(encoding="utf-8"))


def disconnect() -> None:
    TOKEN_PATH.unlink(missing_ok=True)


def _save_token(tok: dict, email: str | None = None) -> None:
    saved = connected() or {"authority": AUTH}
    saved.update({
        "access_token": tok["access_token"],
        "refresh_token": tok.get("refresh_token", saved.get("refresh_token")),
        "expires_at": time.time() + int(tok.get("expires_in", 3600)),
    })
    if email:
        saved["email"] = email
    store.write_private(TOKEN_PATH, json.dumps(saved))


def start_sign_in() -> dict:
    """Returns Microsoft's device-code response (user_code, verification_uri, ...)."""
    flow = _post_form(f"{AUTH}/devicecode", {"client_id": CLIENT_ID, "scope": SCOPES})
    if "user_code" not in flow:
        raise RuntimeError(flow.get("error_description", str(flow)))
    return flow


def finish_sign_in(flow: dict, log: Log) -> bool:
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
            disconnect()  # a fresh sign-in replaces any older account's token
            _save_token(tok)
            me = graph("GET", "/me?$select=userPrincipalName,mail")
            email = (me.get("mail") or me.get("userPrincipalName") or "outlook").lower()
            _save_token(tok, email)
            log(f"signed in as {email}")
            return True
        error = tok.get("error")
        if error == "slow_down":
            interval += 5
        elif error != "authorization_pending":
            log(f"sign-in failed: {tok.get('error_description', tok)}")
            return False
    log("sign-in code expired - click Connect Outlook again")
    return False


def _access_token() -> str:
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
    _save_token(tok)
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
    account = connected()["email"]
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

def _move_batch(msg_ids: list[str], destination: str, log: Log, verb: str) -> dict[str, str]:
    """Move messages 20 per Graph batch. Returns {old id: new id} for those moved."""
    moved: dict[str, str] = {}
    for i in range(0, len(msg_ids), 20):
        chunk = msg_ids[i : i + 20]
        requests = [{
            "id": str(n), "method": "POST", "url": f"/me/messages/{_quote(mid)}/move",
            "body": {"destinationId": destination}, "headers": {"Content-Type": "application/json"},
        } for n, mid in enumerate(chunk)]
        result = graph("POST", "/$batch", {"requests": requests})
        for resp in result.get("responses", []):
            if resp.get("status") in (200, 201):
                moved[chunk[int(resp["id"])]] = (resp.get("body") or {}).get("id", "")
        if (i // 20) % 10 == 0 or i + 20 >= len(msg_ids):
            log(f"  {verb} {len(moved)}/{len(msg_ids)}")
    return moved


def trash(msg_ids: list[str], log: Log) -> dict[str, str]:
    return _move_batch(msg_ids, "deleteditems", log, "moved to Deleted Items")


def restore(trashed_ids: list[str], log: Log) -> dict[str, str]:
    return _move_batch(trashed_ids, "inbox", log, "restored")


def apply_rules(addrs: list[str], protect_words: list[str], log: Log) -> None:
    """Replace the 'Free(up)Mail N' inbox rules with ones moving these senders to Deleted Items."""
    for rule in pages("/me/mailFolders/inbox/messageRules"):
        if rule.get("displayName", "").startswith(OLD_RULE_PREFIXES):
            graph("DELETE", f"/me/mailFolders/inbox/messageRules/{rule['id']}")
    deleted = _folder_id("deleteditems")
    chunks = [addrs[i : i + RULE_CHUNK] for i in range(0, len(addrs), RULE_CHUNK)]
    for n, chunk in enumerate(chunks, 1):
        rule = {
            "displayName": f"{RULE_PREFIX} {n}",
            "sequence": 100 + n,
            "isEnabled": True,
            "conditions": {"senderContains": chunk},
            "actions": {"moveToFolder": deleted, "stopProcessingRules": True},
        }
        if protect_words:
            rule["exceptions"] = {"subjectContains": protect_words}
        graph("POST", "/me/mailFolders/inbox/messageRules", rule)
        log(f"  rule {n}/{len(chunks)} created ({len(chunk)} senders)")
