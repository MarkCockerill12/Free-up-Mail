"""Local backups: a copy of every email is saved as a plain .eml file before Free(up)Mail
moves it to Trash, so it can be put back even after the Trash has been emptied. Any mail
app (Outlook, Thunderbird, Apple Mail...) can also open these files directly."""

import hashlib
import re
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import store


def folder() -> Path:
    return store.APP_DIR / "backups"


def enabled(db) -> bool:
    return store.get_settings(db, {"backup": "1"})["backup"] == "1"


def _path(account: str, msg_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9@._-]", "_", account)
    name = hashlib.sha1(msg_id.encode()).hexdigest()[:24]
    return folder() / safe / datetime.now(UTC).strftime("%Y-%m") / f"{name}.eml"


def keeper(db, account: str):
    """A callback for the providers' trash(): saves one raw email, returns True once it is safely on disk."""
    meta = {
        row[0]: row[1:] for row in db.execute(
            "SELECT msg_id, rfc_id, from_addr, from_name, subject, date, seen FROM messages WHERE account = ?", (account,)
        )
    }
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    lock = threading.Lock()  # providers may back up from several connections at once

    def keep(msg_id: str, raw: bytes | None) -> bool:
        if not raw:
            return False
        path = _path(account, msg_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(raw)
        tmp.replace(path)  # never leave half-written files behind
        rfc_id, addr, name, subject, date, seen = meta.get(msg_id, ("", "", "", "", "", 1))
        with lock, db:
            db.execute(
                "INSERT OR REPLACE INTO backups VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (account, msg_id, str(path), rfc_id, addr, name, subject, date, seen, len(raw), stamp),
            )
        return True

    return keep


def load(db, account: str, msg_id: str) -> tuple[bytes, int] | None:
    """(raw email, seen flag) from the backup, or None when there is no readable copy."""
    row = db.execute("SELECT path, seen FROM backups WHERE account = ? AND msg_id = ?", (account, msg_id)).fetchone()
    if not row or not Path(row[0]).is_file():
        return None
    return Path(row[0]).read_bytes(), row[1] or 0


def stats(db) -> dict:
    count, size = db.execute("SELECT COUNT(*), COALESCE(SUM(size), 0) FROM backups").fetchone()
    return {"count": count, "bytes": size, "folder": str(folder())}


def prune_by_setting(db) -> int:
    """Apply "keep backups for N days". Runs when the app starts, so a computer that was off
    (or an app that was closed) catches up the next time it opens."""
    days = int(store.get_settings(db, {"backup_days": "0"})["backup_days"] or 0)
    return prune(db, days) if days > 0 else 0


def prune(db, days: int) -> int:
    """Delete backups older than `days` (only ever the local copies). Returns how many went."""
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
    old = db.execute("SELECT account, msg_id, path FROM backups WHERE saved_at < ?", (cutoff,)).fetchall()
    for _, _, path in old:
        Path(path).unlink(missing_ok=True)
    with db:
        db.executemany("DELETE FROM backups WHERE account = ? AND msg_id = ?", [(a, m) for a, m, _ in old])
    return len(old)
