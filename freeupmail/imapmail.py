"""Any mailbox over IMAP (Yahoo, iCloud, AOL, GMX, Fastmail, Zoho, your own domain...):
login, read-only scan, preview, move to Trash and restore. gmail.py builds on this
with Gmail's own extensions.

Message ids here are "imap:<UIDVALIDITY>:<UID>:<folder>". IMAP never reuses a UID
while the folder keeps its UIDVALIDITY, so an id can never point at a different email.
"""

import base64
import email
import email.policy
import imaplib
import re
import threading
from collections.abc import Callable
from datetime import UTC, datetime

from . import store

BATCH = 500
LIST_RE = re.compile(rb'\((?P<flags>[^)]*)\) (?:"(?:[^"\\]|\\.)*"|NIL) (?P<name>.+)$')
UID_RE = re.compile(rb"UID (\d+)")
FLAGS_RE = re.compile(rb"FLAGS \(([^)]*)\)")
NUM_RE = re.compile(rb"(UIDNEXT|UIDVALIDITY) (\d+)")
CID_RE = re.compile(r"cid:([^\"'\s)>]+)", re.IGNORECASE)
HEADER_FIELDS = "FROM SUBJECT DATE LIST-UNSUBSCRIBE LIST-ID MESSAGE-ID"
INLINE_IMAGE_LIMIT = 3_000_000
GONE = "This email is no longer in the mailbox (deleted or moved to Trash)."

# Well-known providers: (IMAP host, webmail, where to create an app password).
GOOGLE = ("imap.gmail.com", "https://mail.google.com/", "https://myaccount.google.com/apppasswords")
YAHOO = ("imap.mail.yahoo.com", "https://mail.yahoo.com/", "https://login.yahoo.com/account/security")
ICLOUD = ("imap.mail.me.com", "https://www.icloud.com/mail", "https://account.apple.com/account/manage")
AOL = ("imap.aol.com", "https://mail.aol.com/", "https://login.aol.com/account/security")
GMX = ("imap.gmx.net", "https://www.gmx.net/", "")
PROVIDERS = {
    "gmail.com": GOOGLE, "googlemail.com": GOOGLE,
    "yahoo.com": YAHOO, "ymail.com": YAHOO, "rocketmail.com": YAHOO,
    "icloud.com": ICLOUD, "me.com": ICLOUD, "mac.com": ICLOUD,
    "aol.com": AOL, "aim.com": AOL,
    "gmx.com": ("imap.gmx.com", "https://www.gmx.com/", ""), "gmx.net": GMX, "gmx.de": GMX,
    "web.de": ("imap.web.de", "https://web.de/", ""),
    "mail.com": ("imap.mail.com", "https://www.mail.com/", ""),
    "zoho.com": ("imap.zoho.com", "https://mail.zoho.com/", "https://accounts.zoho.com/home#security/app_password"),
    "fastmail.com": ("imap.fastmail.com", "https://app.fastmail.com/", "https://app.fastmail.com/settings/security/apps"),
    "yandex.com": ("imap.yandex.com", "https://mail.yandex.com/", ""),
    "yandex.ru": ("imap.yandex.ru", "https://mail.yandex.ru/", ""),
}
# These sign in through Microsoft instead (Outlook basic-auth IMAP is switched off).
MICROSOFT_DOMAINS = {"outlook.com", "hotmail.com", "live.com", "msn.com", "passport.com"}

# Folder roles by special-use flag, with the usual names for servers that don't flag them.
ROLE_NAMES = {
    b"\\Sent": {"sent", "sent items", "sent messages", "sent mail"},
    b"\\Trash": {"trash", "deleted", "deleted items", "deleted messages", "bin"},
    b"\\Junk": {"junk", "spam", "bulk mail", "junk e-mail", "junk email"},
    b"\\Drafts": {"drafts"},
}
# Not scanned: the roles above plus virtual views that would list emails twice.
SKIP_FLAGS = (b"\\Noselect", b"\\NonExistent", b"\\All", b"\\Flagged", b"\\Important", *ROLE_NAMES)

Log = Callable[[str], None]


# ---------- accounts ----------

def provider(addr: str) -> tuple[str, str, str] | None:
    domain = addr.rpartition("@")[2].lower()
    if domain.startswith("yahoo."):
        return YAHOO
    return PROVIDERS.get(domain)


def is_microsoft(addr: str) -> bool:
    domain = addr.rpartition("@")[2].lower()
    return domain.split(".")[0] in {d.split(".")[0] for d in MICROSOFT_DOMAINS}


def host_of(account: dict) -> str:
    known = provider(account["email"])
    return account.get("host") or (known[0] if known else "imap." + account["email"].rpartition("@")[2])


def login(account: dict) -> imaplib.IMAP4:
    host, port = host_of(account), int(account.get("port") or 993)
    if port == 993:
        imap = imaplib.IMAP4_SSL(host, port, timeout=60)
    else:
        imap = imaplib.IMAP4(host, port, timeout=60)
        imap.starttls()
    password = account["password"]
    if host == GOOGLE[0]:
        password = password.replace(" ", "")  # Google shows app passwords in groups of four
    imap.login(account.get("user") or account["email"], password)
    # Many servers (Gmail included) only list MOVE/UIDPLUS once logged in.
    _, data = imap.capability()
    imap.capabilities = tuple(data[-1].decode().upper().split())
    return imap


def test_login(account: dict) -> str:
    """'' on success, else the server's error text."""
    try:
        login(account).logout()
    except imaplib.IMAP4.error as exc:
        return exc.args[0].decode() if isinstance(exc.args[0], bytes) else str(exc)
    except OSError as exc:
        return f"could not reach {host_of(account)} ({exc}). Check the server name."
    return ""


# ---------- folders ----------

def plain(name: str) -> str:
    """Folder name as LIST printed it, without IMAP quoting."""
    if len(name) > 1 and name[0] == name[-1] == '"':
        return name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return name


def quote(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def select(imap: imaplib.IMAP4, folder: str, readonly: bool = False) -> int:
    """Open a folder; returns its UIDVALIDITY (0 if the server didn't say)."""
    typ, data = imap.select(quote(folder), readonly=readonly)
    if typ != "OK":
        raise RuntimeError(f"cannot open {folder}: {data}")
    _, values = imap.response("UIDVALIDITY")
    return int(values[-1]) if values and values[-1] else 0


def list_folders(imap: imaplib.IMAP4) -> list[tuple[bytes, str]]:
    _, lines = imap.list()
    out = []
    for line in lines:
        m = LIST_RE.match(line) if isinstance(line, bytes) else None
        if m:
            out.append((m["flags"], plain(m["name"].decode())))
    return out


def _role(folders: list[tuple[bytes, str]], flag: bytes) -> str | None:
    for flags, name in folders:
        if flag in flags:
            return name
    names = ROLE_NAMES.get(flag, set())
    for _, name in folders:
        lower = name.lower()
        if lower in names or re.split(r"[/.]", lower)[-1] in names:
            return name
    return None


def find_folder(imap: imaplib.IMAP4, flag: bytes, required: bool = True) -> str | None:
    name = _role(list_folders(imap), flag)
    if name is None and required:
        raise RuntimeError(f"No {flag.decode()[1:]} folder found")
    return name


def mail_folders(imap: imaplib.IMAP4) -> list[str]:
    """Folders worth cleaning: everything except Sent, Drafts, Trash, Junk and virtual views."""
    folders = list_folders(imap)
    skip = {_role(folders, flag) for flag in ROLE_NAMES}
    return [name for flags, name in folders
            if name not in skip and not any(f in flags for f in SKIP_FLAGS)]


def search_uids(imap: imaplib.IMAP4, *criteria: str) -> list[bytes]:
    _, data = imap.uid("SEARCH", *criteria)
    return data[0].split() if data and data[0] else []


def fetch_headers(imap: imaplib.IMAP4, uids: list, fields: str, log: Log, extra: str = ""):
    """Yield (meta, header bytes) per message; meta holds UID/FLAGS (+ any `extra` items)."""
    for i in range(0, len(uids), BATCH):
        chunk = ",".join(u.decode() if isinstance(u, bytes) else str(u) for u in uids[i : i + BATCH])
        _, data = imap.uid("FETCH", chunk, f"({extra}FLAGS BODY.PEEK[HEADER.FIELDS ({fields})])")
        for j, item in enumerate(data):
            if isinstance(item, tuple):
                nxt = data[j + 1] if j + 1 < len(data) else b""
                yield item[0] + (nxt if isinstance(nxt, bytes) else b""), item[1]
        log(f"  {min(i + BATCH, len(uids))}/{len(uids)}")


def message_ids(imap: imaplib.IMAP4, uids: list) -> dict[int, str]:
    """{uid: Message-ID} for the uids still in the selected folder."""
    found = {}
    for meta, raw in fetch_headers(imap, uids, "MESSAGE-ID", lambda _: None):
        uid = UID_RE.search(meta)
        if uid:
            found[int(uid[1])] = (email.message_from_bytes(raw)["Message-ID"] or "").strip().strip("<>")
    return found


def move(imap: imaplib.IMAP4, uids: list[int], dest: str) -> None:
    seq = ",".join(map(str, uids))
    if "MOVE" in imap.capabilities:
        typ, resp = imap.uid("MOVE", seq, quote(dest))
    else:
        typ, resp = imap.uid("COPY", seq, quote(dest))
        if typ == "OK":
            imap.uid("STORE", seq, "+FLAGS.SILENT", "(\\Deleted)")
            if "UIDPLUS" in imap.capabilities:  # never a plain EXPUNGE: it would purge other deleted mail too
                imap.uid("EXPUNGE", seq)
    if typ != "OK":
        raise RuntimeError(f"moving to {dest} failed: {resp}")


def make_id(validity: int, uid: int, folder: str) -> str:
    return f"imap:{validity}:{uid}:{folder}"


def parse_id(msg_id: str) -> tuple[int, int, str]:
    _, validity, uid, folder = msg_id.split(":", 3)
    return int(validity), int(uid), folder


# ---------- scan ----------

def message_row(account: str, msg_id: str, uid: int, flags: bytes, raw: bytes, labels: list[str]) -> dict:
    msg = email.message_from_bytes(raw)
    addr, name, domain = store.parse_from(msg["From"])
    return {
        "account": account, "msg_id": msg_id, "uid": uid,
        "from_addr": addr, "from_name": name, "from_domain": domain,
        "subject": store.decode(msg["Subject"]), "date": store.iso_date(msg["Date"]),
        "seen": int(b"\\Seen" in flags),
        "list_unsub": int(msg["List-Unsubscribe"] is not None),
        "list_id": store.decode(msg["List-Id"]),
        "labels": ",".join(labels),
        "unsub_url": store.unsub_link(msg["List-Unsubscribe"]),
        "rfc_id": (msg["Message-ID"] or "").strip().strip("<>"),
    }


def scan_contacts(imap, db, account: str, log: Log) -> None:
    sent = find_folder(imap, b"\\Sent", required=False)
    if not sent:
        log("no Sent folder found - people you email can't be recognised for this account")
        return
    select(imap, sent, readonly=True)
    uids = search_uids(imap, "ALL")
    log(f"sent mail: {len(uids)} messages")
    counts: dict[str, int] = {}
    for _, raw in fetch_headers(imap, uids, "TO CC BCC", log):
        msg = email.message_from_bytes(raw)
        for addr in store.parse_recipients(msg["To"], msg["Cc"], msg["Bcc"]):
            counts[addr] = counts.get(addr, 0) + 1
    counts.pop(account, None)
    store.replace_contacts(db, account, counts)


def scan_messages(imap, db, account: str, log: Log) -> None:
    rows = []
    for folder in mail_folders(imap):
        try:
            validity = select(imap, folder, readonly=True)
        except RuntimeError as exc:
            log(f"skip {exc}")
            continue
        uids = search_uids(imap, "ALL")
        log(f"{folder}: {len(uids)} messages")
        inbox = folder.upper() == "INBOX"
        for meta, raw in fetch_headers(imap, uids, HEADER_FIELDS, log):
            uid = UID_RE.search(meta)
            if not uid:
                continue
            flags = FLAGS_RE.search(meta)
            flags = flags[1] if flags else b""
            labels = (["inbox"] if inbox else []) + (["starred"] if b"\\Flagged" in flags else [])
            rows.append(message_row(account, make_id(validity, int(uid[1]), folder), int(uid[1]), flags, raw, labels))
    store.replace_messages(db, account, rows)
    log(f"stored {len(rows)} messages")


def run_scan(account: dict, db, log: Log, scan_fn) -> bool:
    log(f"== scanning {account['email']}")
    now = datetime.now(UTC).isoformat(timespec="seconds")
    try:
        imap = login(account)
    except (imaplib.IMAP4.error, OSError) as exc:
        log(f"LOGIN FAILED: {exc}")
        store.record_scan(db, account["email"], now, "login failed")
        return False
    try:
        scan_contacts(imap, db, account["email"], log)
        scan_fn(imap, db, account["email"], log)
    finally:
        imap.logout()
    store.record_scan(db, account["email"], now, "ok")
    return True


def scan_account(account: dict, db, log: Log = print) -> bool:
    return run_scan(account, db, log, scan_messages)


# ---------- preview ----------

_preview_conns: dict[str, list] = {}  # email -> [imap, selected folder key, uidvalidity]
_preview_lock = threading.Lock()


def _preview_conn(account: dict, folder: str | bytes, fresh: bool) -> tuple[imaplib.IMAP4, int]:
    """Cached read-only connection with `folder` (a name, or a special-use flag) selected."""
    entry = None if fresh else _preview_conns.get(account["email"])
    if entry is None:
        entry = _preview_conns[account["email"]] = [login(account), None, 0]
    imap = entry[0]
    if entry[1] != folder:
        name = find_folder(imap, folder) if isinstance(folder, bytes) else folder
        entry[1:] = [folder, select(imap, name, readonly=True)]
    return imap, entry[2]


def fetch_raw(account: dict, folder: str | bytes, find_uid) -> bytes | None:
    """Full message source; find_uid(imap, uidvalidity) -> uid or None. None if it's gone."""
    with _preview_lock:
        for fresh in (False, True):
            try:
                imap, validity = _preview_conn(account, folder, fresh)
                uid = find_uid(imap, validity)
                if uid is None:
                    return None
                _, data = imap.uid("FETCH", str(uid), "(BODY.PEEK[])")
                return next((item[1] for item in data if isinstance(item, tuple)), None)
            except (imaplib.IMAP4.abort, OSError):
                _preview_conns.pop(account["email"], None)
    raise RuntimeError("Could not fetch this email from the mail server.")


def _part_text(part) -> str:
    try:
        return part.get_content()
    except (LookupError, UnicodeError, AttributeError):
        payload = part.get_payload(decode=True) or b""
        return payload.decode("utf-8", "replace")


def render_message(raw: bytes) -> dict:
    """Pick the HTML (or text) body, inline cid: images, list attachments."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    html_part = msg.get_body(preferencelist=("html",))
    text_part = msg.get_body(preferencelist=("plain",))
    html = _part_text(html_part) if html_part else ""
    if html:
        images, budget = {}, INLINE_IMAGE_LIMIT
        for part in msg.walk():
            cid = (part.get("Content-ID") or "").strip("<> ")
            if cid and part.get_content_maintype() == "image":
                data = part.get_payload(decode=True) or b""
                if len(data) <= budget:
                    budget -= len(data)
                    images[cid] = f"data:{part.get_content_type()};base64,{base64.b64encode(data).decode()}"
        html = CID_RE.sub(lambda m: images.get(m[1], m[0]), html)
    return {
        "html": html,
        "text": _part_text(text_part) if text_part else "",
        "unsub": store.unsub_link(msg["List-Unsubscribe"]),
        "attachments": [a.get_filename() or "attachment" for a in msg.iter_attachments()],
    }


def fetch_message(account: dict, msg_id: str) -> dict:
    validity, uid, folder = parse_id(msg_id)
    try:
        raw = fetch_raw(account, folder, lambda _, v: uid if v == validity else None)
    except RuntimeError as exc:
        return {"error": str(exc)}
    return render_message(raw) if raw else {"error": GONE}


def web_link(account: dict, rfc_id: str) -> str:
    known = provider(account["email"])
    return known[1] if known else ""


# ---------- trash / restore ----------

def trash(account: dict, items: dict[str, int], log: Log = print) -> dict[str, str]:
    """Move {msg_id: uid} to Trash. Returns {msg_id: Message-ID} for what moved; the
    Message-ID is how restore finds it again inside Trash."""
    by_folder: dict[tuple[str, int], list[int]] = {}
    for msg_id in items:
        validity, uid, folder = parse_id(msg_id)
        by_folder.setdefault((folder, validity), []).append(uid)
    imap = login(account)
    moved: dict[str, str] = {}
    try:
        trash_folder = find_folder(imap, b"\\Trash")
        if "MOVE" not in imap.capabilities and "UIDPLUS" not in imap.capabilities:
            log("  this server can't move emails directly: they are copied to Trash and flagged deleted")
        for (folder, validity), uids in by_folder.items():
            if select(imap, folder) != validity:
                log(f"  {folder} changed since the last scan; {len(uids)} skipped (rescan first)")
                continue
            for i in range(0, len(uids), BATCH):
                found = message_ids(imap, uids[i : i + BATCH])  # only uids that still exist
                if found:
                    move(imap, list(found), trash_folder)
                    moved.update({make_id(validity, u, folder): rfc for u, rfc in found.items()})
                log(f"  trashed {len(moved)}/{len(items)}")
    finally:
        imap.logout()
    return moved


def restore(account: dict, rfc_ids: list[str], log: Log = print) -> dict[str, str]:
    """Move messages (by Message-ID) from Trash back to the Inbox.
    Returns {Message-ID: new id in the Inbox}."""
    wanted = {r for r in rfc_ids if r}
    imap = login(account)
    try:
        select(imap, find_folder(imap, b"\\Trash"))
        found = {u: rfc for u, rfc in message_ids(imap, search_uids(imap, "ALL")).items() if rfc in wanted}
        missing = len(wanted - set(found.values()))
        if missing:
            log(f"  {missing} no longer in Trash (emptied or deleted by hand)")
        if not found:
            return {}
        _, status = imap.status("INBOX", "(UIDNEXT UIDVALIDITY)")
        before = dict(NUM_RE.findall(status[0]))
        uids = list(found)
        for i in range(0, len(uids), BATCH):
            move(imap, uids[i : i + BATCH], "INBOX")
            log(f"  restored {min(i + BATCH, len(uids))}/{len(uids)}")
        validity = select(imap, "INBOX", readonly=True)
        start = int(before[b"UIDNEXT"]) if int(before[b"UIDVALIDITY"]) == validity else 1
        arrived = message_ids(imap, search_uids(imap, f"UID {start}:*"))
        return {rfc: make_id(validity, u, "INBOX") for u, rfc in arrived.items() if rfc in wanted}
    finally:
        imap.logout()
