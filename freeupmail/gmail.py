"""Gmail over IMAP, using Gmail's extensions on top of imapmail: one scan of All Mail,
its categories (Promotions, Social...) and X-GM-MSGID ids, which survive moves."""

import re

from . import imapmail, store
from .imapmail import BATCH, FLAGS_RE, UID_RE, Log, find_folder, search_uids, select

HOST = imapmail.GOOGLE[0]
MSGID_RE = re.compile(rb"X-GM-MSGID (\d+)")
# Gmail's own classification, exposed through its search syntax.
LABEL_QUERIES = {
    "inbox": "in:inbox",
    "promotions": "category:promotions",
    "social": "category:social",
    "updates": "category:updates",
    "forums": "category:forums",
    "starred": "is:starred",
    "important": "is:important",
}


def scan_messages(imap, db, account: str, log: Log) -> None:
    select(imap, find_folder(imap, b"\\All"), readonly=True)
    uids = search_uids(imap, "ALL")
    log(f"all mail: {len(uids)} messages")
    labels: dict[bytes, list[str]] = {}
    for label, query in LABEL_QUERIES.items():
        for uid in search_uids(imap, "X-GM-RAW", f'"{query}"'):
            labels.setdefault(uid, []).append(label)

    rows = []
    for meta, raw in imapmail.fetch_headers(imap, uids, imapmail.HEADER_FIELDS, log, "X-GM-MSGID "):
        uid, msgid = UID_RE.search(meta), MSGID_RE.search(meta)
        if not (uid and msgid):
            continue
        flags = FLAGS_RE.search(meta)
        rows.append(imapmail.message_row(account, msgid[1].decode(), int(uid[1]), flags[1] if flags else b"",
                                         raw, labels.get(uid[1], [])))
    store.replace_messages(db, account, rows)
    log(f"stored {len(rows)} messages")


def scan_account(account: dict, db, log: Log = print) -> bool:
    return imapmail.run_scan(account, db, log, scan_messages)


def fetch_message(account: dict, msg_id: str) -> dict:
    """Full message by X-GM-MSGID (UIDs can go stale; the Gmail id never does)."""
    def find(imap, _):
        uids = search_uids(imap, "X-GM-MSGID", msg_id)
        return uids[0].decode() if uids else None

    try:
        raw = imapmail.fetch_raw(account, b"\\All", find)
    except RuntimeError as exc:
        return {"error": str(exc)}
    return imapmail.render_message(raw) if raw else {"error": imapmail.GONE}


def web_link(account: dict, rfc_id: str) -> str:
    addr = account["email"]
    if not rfc_id:
        return f"https://mail.google.com/mail/?authuser={addr}"
    return f"https://mail.google.com/mail/?authuser={addr}#search/rfc822msgid%3A{rfc_id}"


def _msgids(imap, uids: list) -> dict[str, str]:
    """{uid: X-GM-MSGID} for the uids still in the selected folder."""
    found = {}
    for i in range(0, len(uids), BATCH):
        chunk = ",".join(u.decode() if isinstance(u, bytes) else str(u) for u in uids[i : i + BATCH])
        _, data = imap.uid("FETCH", chunk, "(X-GM-MSGID)")
        for line in data:
            if isinstance(line, bytes):
                uid, msgid = UID_RE.search(line), MSGID_RE.search(line)
                if uid and msgid:
                    found[uid[1].decode()] = msgid[1].decode()
    return found


def trash(account: dict, items: dict[str, int], log: Log = print) -> dict[str, str]:
    """Move {msgid: uid} from All Mail to Trash. A message only moves when its
    X-GM-MSGID still matches the cached one. Returns {msgid: msgid} for what moved."""
    imap = imapmail.login(account)
    moved: dict[str, str] = {}
    try:
        select(imap, find_folder(imap, b"\\All"))
        trash_folder = find_folder(imap, b"\\Trash")
        uids = [str(u) for u in items.values()]
        for i in range(0, len(uids), BATCH):
            confirmed = {u: m for u, m in _msgids(imap, uids[i : i + BATCH]).items() if items.get(m) == int(u)}
            if confirmed:
                imapmail.move(imap, list(confirmed), trash_folder)
                moved.update({m: m for m in confirmed.values()})
            log(f"  trashed {len(moved)}/{len(uids)}")
    finally:
        imap.logout()
    return moved


def restore(account: dict, msg_ids: list[str], log: Log = print) -> dict[str, str]:
    """Move messages (by X-GM-MSGID) from Trash back to the Inbox. Returns {msgid: msgid}."""
    wanted = set(msg_ids)
    imap = imapmail.login(account)
    try:
        select(imap, find_folder(imap, b"\\Trash"))
        found = {u: m for u, m in _msgids(imap, search_uids(imap, "ALL")).items() if m in wanted}
        missing = len(wanted) - len(found)
        if missing:
            log(f"  {missing} no longer in Trash (emptied or deleted by hand)")
        restored: dict[str, str] = {}
        keys = list(found)
        for i in range(0, len(keys), BATCH):
            chunk = keys[i : i + BATCH]
            imapmail.move(imap, chunk, "INBOX")
            restored.update({found[u]: found[u] for u in chunk})
            log(f"  restored {len(restored)}/{len(found)}")
    finally:
        imap.logout()
    return restored
