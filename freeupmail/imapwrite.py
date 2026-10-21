"""Changes to any IMAP mailbox: move to Trash (backing each email up first), restore,
re-upload from a backup, and the rule actions (folders, archive, junk, read, flag).
Reading lives in imapmail.py; providers.Mailbox checks permissions before any of this runs."""

import email
import email.utils
import imaplib

from . import imapmail
from .imapmail import (
    BATCH,
    NUM_RE,
    UID_RE,
    Log,
    find_folder,
    list_folders,
    make_id,
    message_ids,
    parse_id,
    quote,
    search_uids,
    select,
)

RAW_BATCH = 25  # whole emails per FETCH when taking backups


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


def fetch_raws(imap: imaplib.IMAP4, uids: list) -> dict[int, bytes]:
    """{uid: full email source} for the uids still in the selected folder."""
    raws: dict[int, bytes] = {}
    for i in range(0, len(uids), RAW_BATCH):
        chunk = ",".join(str(u) for u in uids[i : i + RAW_BATCH])
        _, data = imap.uid("FETCH", chunk, "(BODY.PEEK[])")
        for item in data:
            if isinstance(item, tuple):
                uid = UID_RE.search(item[0])
                if uid:
                    raws[int(uid[1])] = item[1]
    return raws


def backed_up(imap, uids: list[int], to_id, keep, log: Log) -> list[int]:
    """The uids whose backup copy was saved (all of them when backups are off)."""
    if keep is None:
        return uids
    raws = fetch_raws(imap, uids)
    ok = [u for u in uids if keep(to_id(u), raws.get(u))]
    if len(ok) < len(uids):
        log(f"  {len(uids) - len(ok)} could not be backed up, so they were left where they are")
    return ok


def _matching(imap, uids: list[int], to_id, expected: dict[str, str] | None, log: Log) -> dict[int, str]:
    """{uid: Message-ID} for the uids that still exist and still hold the email the scan saw
    (same Message-ID, when the scan recorded one)."""
    found = message_ids(imap, uids)
    if expected:
        changed = [u for u, rfc in found.items() if expected.get(to_id(u)) not in ("", None, rfc)]
        for u in changed:
            del found[u]
        if changed:
            log(f"  {len(changed)} changed since the last scan, so they were left alone")
    return found


def logout(imap) -> None:
    try:
        imap.logout()
    except (imaplib.IMAP4.error, OSError):
        pass  # the connection may already be gone


def _by_folder(items) -> dict[tuple[str, int], list[int]]:
    groups: dict[tuple[str, int], list[int]] = {}
    for msg_id in items:
        validity, uid, folder = parse_id(msg_id)
        groups.setdefault((folder, validity), []).append(uid)
    return groups


def trash(account: dict, items: dict[str, int], log: Log = print, keep=None,
          expected: dict[str, str] | None = None) -> dict[str, str]:
    """Move {msg_id: uid} to Trash. With `keep` (see backup.keeper) each email is saved
    first and only moved once its copy is on disk; `expected` ({msg_id: Message-ID} from
    the scan) makes sure each uid still holds that email. Returns {msg_id: Message-ID} for
    what moved (also when the connection drops part-way); the Message-ID is how restore
    finds it again inside Trash."""
    imap = imapmail.login(account)
    moved: dict[str, str] = {}
    try:
        trash_folder = find_folder(imap, b"\\Trash")
        if "MOVE" not in imap.capabilities and "UIDPLUS" not in imap.capabilities:
            log("  this server can't move emails directly: they are copied to Trash and flagged deleted")
        for (folder, validity), uids in _by_folder(items).items():
            if select(imap, folder) != validity:
                log(f"  {folder} changed since the last scan; {len(uids)} skipped (rescan first)")
                continue
            to_id = lambda u, v=validity, f=folder: make_id(v, u, f)
            for i in range(0, len(uids), BATCH):
                found = _matching(imap, uids[i : i + BATCH], to_id, expected, log)
                ok = backed_up(imap, list(found), to_id, keep, log)
                if ok:
                    move(imap, ok, trash_folder)
                    moved.update({to_id(u): found[u] for u in ok})
                log(f"  trashed {len(moved)}/{len(items)}")
    except (imaplib.IMAP4.error, OSError, RuntimeError) as exc:
        log(f"  stopped early ({exc}); {len(moved)} were moved and are recorded")
    finally:
        logout(imap)
    return moved


def upload(account: dict, emails: list[tuple[bytes, int]], log: Log = print) -> list[int]:
    """Put backed-up emails (raw source, seen flag) back into the Inbox, keeping their date.
    Returns the positions that were uploaded, so a failure part-way loses nothing."""
    imap = imapmail.login(account)
    done: list[int] = []
    try:
        for n, (raw, seen) in enumerate(emails):
            date = email.message_from_bytes(raw)["Date"]
            try:
                when = imaplib.Time2Internaldate(email.utils.parsedate_to_datetime(date))
            except (TypeError, ValueError, IndexError, OverflowError):
                when = None
            typ, resp = imap.append("INBOX", "(\\Seen)" if seen else None, when, raw)
            if typ == "OK":
                done.append(n)
            else:
                log(f"  one email could not be put back: {resp}")
    except (imaplib.IMAP4.error, OSError) as exc:
        log(f"  stopped early ({exc})")
    finally:
        logout(imap)
    log(f"  put back {len(done)} from backups")
    return done


def present(account: dict, rfc_ids: list[str], log: Log = print) -> set[str]:
    """Which of these Message-IDs are already back somewhere in the mailbox (e.g. you
    restored them yourself), so restoring from a backup won't make a duplicate."""
    wanted = [r for r in rfc_ids if r and '"' not in r and "\\" not in r]
    found: set[str] = set()
    if not wanted:
        return found
    imap = imapmail.login(account)
    try:
        for folder in imapmail.mail_folders(imap):
            try:
                select(imap, folder, readonly=True)
            except RuntimeError:
                continue
            for rfc in wanted:
                if rfc not in found and search_uids(imap, "HEADER", "Message-ID", f'"<{rfc}>"'):
                    found.add(rfc)
    finally:
        logout(imap)
    return found


def ensure_folder(imap: imaplib.IMAP4, name: str) -> str:
    """The folder called `name` (any letter case), created if it doesn't exist yet."""
    for _, existing in list_folders(imap):
        if existing.lower() == name.lower():
            return existing
    typ, resp = imap.create(quote(name))
    if typ != "OK":
        raise RuntimeError(f"could not create folder {name}: {resp}")
    return name


def _destination(imap, action: str, folder: str) -> str | None:
    if action == "move":
        return ensure_folder(imap, folder)
    if action == "archive":
        return find_folder(imap, b"\\Archive", required=False) or ensure_folder(imap, "Archive")
    if action == "junk":
        return find_folder(imap, b"\\Junk")
    return None


def organise(account: dict, action: str, items: dict[str, int], folder: str = "",
             mark_read: bool = False, log: Log = print, expected: dict[str, str] | None = None) -> int:
    """Rule actions on {msg_id: uid}: move / archive / junk / read / star. Returns how many."""
    imap = imapmail.login(account)
    done = 0
    try:
        dest = _destination(imap, action, folder)
        for (src, validity), uids in _by_folder(items).items():
            if dest and src.lower() == dest.lower():
                continue  # already there
            if select(imap, src) != validity:
                log(f"  {src} changed since the last scan; {len(uids)} skipped (rescan first)")
                continue
            to_id = lambda u, v=validity, f=src: make_id(v, u, f)
            for i in range(0, len(uids), BATCH):
                still = list(_matching(imap, uids[i : i + BATCH], to_id, expected, log))
                if not still:
                    continue
                seq = ",".join(map(str, still))
                if mark_read or action == "read":
                    imap.uid("STORE", seq, "+FLAGS.SILENT", "(\\Seen)")
                if action == "star":
                    imap.uid("STORE", seq, "+FLAGS.SILENT", "(\\Flagged)")
                if dest:
                    move(imap, still, dest)
                done += len(still)
        log(f"  {done} emails done")
    except (imaplib.IMAP4.error, OSError, RuntimeError) as exc:
        log(f"  stopped early ({exc}); {done} were done")
    finally:
        logout(imap)
    return done


def restore(account: dict, rfc_ids: list[str], log: Log = print) -> dict[str, str]:
    """Move messages (by Message-ID) from Trash back to the Inbox.
    Returns {Message-ID: new id in the Inbox}."""
    wanted = {r for r in rfc_ids if r}
    imap = imapmail.login(account)
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
        arrived = message_ids(imap, search_uids(imap, "UID", f"{start}:*"))
        return {rfc: make_id(validity, u, "INBOX") for u, rfc in arrived.items() if rfc in wanted}
    finally:
        logout(imap)
