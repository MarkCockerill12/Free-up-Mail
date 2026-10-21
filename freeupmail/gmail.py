"""Gmail over IMAP, using Gmail's extensions on top of imapmail: one scan of All Mail,
its categories (Promotions, Social...) and X-GM-MSGID ids, which survive moves."""

import imaplib
import re

from . import imapmail, imapwrite, store
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


def _confirmed(imap, items: dict[str, int], uids: list[str]) -> dict[str, str]:
    """{uid: msgid} for the uids whose X-GM-MSGID still matches the cached one."""
    return {u: m for u, m in _msgids(imap, uids).items() if items.get(m) == int(u)}


def trash(account: dict, items: dict[str, int], log: Log = print, keep=None, expected=None) -> dict[str, str]:
    """Move {msgid: uid} from All Mail to Trash. A message only moves when its X-GM-MSGID
    still matches the cached one and (with `keep`) its backup copy is saved.
    Returns {msgid: msgid} for what moved, also when the connection drops part-way."""
    imap = imapmail.login(account)
    moved: dict[str, str] = {}
    try:
        select(imap, find_folder(imap, b"\\All"))
        trash_folder = find_folder(imap, b"\\Trash")
        uids = [str(u) for u in items.values()]
        for i in range(0, len(uids), BATCH):
            confirmed = _confirmed(imap, items, uids[i : i + BATCH])
            ok = imapwrite.backed_up(imap, [int(u) for u in confirmed], lambda u, c=confirmed: c[str(u)], keep, log)
            if ok:
                imapwrite.move(imap, ok, trash_folder)
                moved.update({confirmed[str(u)]: confirmed[str(u)] for u in ok})
            log(f"  trashed {len(moved)}/{len(uids)}")
    except (imaplib.IMAP4.error, OSError, RuntimeError) as exc:
        log(f"  stopped early ({exc}); {len(moved)} were moved and are recorded")
    finally:
        imapwrite.logout(imap)
    return moved


def present(account: dict, rfc_ids: list[str], log: Log = print) -> set[str]:
    """Which of these Message-IDs are already back in the mailbox (outside Trash)."""
    found: set[str] = set()
    imap = imapmail.login(account)
    try:
        select(imap, find_folder(imap, b"\\All"), readonly=True)
        for rfc in rfc_ids:
            if rfc and '"' not in rfc and search_uids(imap, "X-GM-RAW", f'"rfc822msgid:{rfc}"'):
                found.add(rfc)
    finally:
        imapwrite.logout(imap)
    return found


upload = imapwrite.upload


def _label(name: str) -> str:
    return "(" + imapmail.quote(name) + ")"


def organise(account: dict, action: str, items: dict[str, int], folder: str = "",
             mark_read: bool = False, log: Log = print, expected=None) -> int:
    """Rule actions in Gmail terms: "move" adds the label and takes the email out of the
    Inbox (Gmail's own Move to), "archive" only takes it out of the Inbox."""
    imap = imapmail.login(account)
    done = 0
    try:
        label = imapwrite.ensure_folder(imap, folder) if action == "move" else ""
        junk = find_folder(imap, b"\\Junk") if action == "junk" else ""
        select(imap, find_folder(imap, b"\\All"))
        uids = [str(u) for u in items.values()]
        for i in range(0, len(uids), BATCH):
            confirmed = list(_confirmed(imap, items, uids[i : i + BATCH]))
            if not confirmed:
                continue
            seq = ",".join(confirmed)
            if mark_read or action == "read":
                imap.uid("STORE", seq, "+FLAGS.SILENT", "(\\Seen)")
            if action == "star":
                imap.uid("STORE", seq, "+FLAGS.SILENT", "(\\Flagged)")
            if label:
                imap.uid("STORE", seq, "+X-GM-LABELS", _label(label))
            if action in ("move", "archive"):
                imap.uid("STORE", seq, "-X-GM-LABELS", "(\\Inbox)")
            if junk:
                imapwrite.move(imap, confirmed, junk)
            done += len(confirmed)
        log(f"  {done} emails done")
    except (imaplib.IMAP4.error, OSError, RuntimeError) as exc:
        log(f"  stopped early ({exc}); {done} were done")
    finally:
        imapwrite.logout(imap)
    return done


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
            imapwrite.move(imap, chunk, "INBOX")
            restored.update({found[u]: found[u] for u in chunk})
            log(f"  restored {len(restored)}/{len(found)}")
    except (imaplib.IMAP4.error, OSError, RuntimeError) as exc:
        log(f"  stopped early ({exc}); {len(restored)} were restored")
    finally:
        imapwrite.logout(imap)
    return restored
