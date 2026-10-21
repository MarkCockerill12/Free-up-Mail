"""Move planned emails to Trash (recording each one) and restore them on request."""

from collections.abc import Callable
from datetime import UTC, datetime

from . import outlook, providers, store, views

Log = Callable[[str], None]


def _record_trashed(db, account: str, mapping: dict[str, str], stamp: str) -> None:
    """Copy moved messages into `trashed` (with their id inside Trash) and drop them from the cache."""
    with db:
        for old_id, new_id in mapping.items():
            db.execute(
                "INSERT OR REPLACE INTO trashed (account, msg_id, new_id, from_addr, from_name, subject,"
                " date, trashed_at, restored) SELECT account, msg_id, ?, from_addr, from_name, subject,"
                " date, ?, 0 FROM messages WHERE account = ? AND msg_id = ?",
                (new_id, stamp, account, old_id),
            )
        db.executemany(
            "DELETE FROM messages WHERE account = ? AND msg_id = ?", [(account, m) for m in mapping]
        )


def run_cleanup(account: str | None, log: Log) -> None:
    db = store.connect()
    plan = views.cleanup_plan(db, account)
    connected = providers.accounts()
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    for acc, items in plan.items():
        log(f"== {acc}: moving {len(items)} emails to Trash")
        kind, cfg = connected.get(acc, ("", None))
        # Each returns {id before: id that finds it in Trash}.
        if kind == "outlook":
            mapping = outlook.trash(list(items), log)
        elif cfg:
            mapping = providers.module(cfg).trash(cfg, items, log)
        else:
            log(f"skip {acc}: not connected")
            continue
        _record_trashed(db, acc, mapping, stamp)
        skipped = len(items) - len(mapping)
        log(f"{acc}: {len(mapping)} moved to Trash" + (f", {skipped} skipped (changed since scan)" if skipped else ""))


def run_restore(items: list[list[str]], log: Log) -> None:
    """items: [[account, msg_id], ...] from the `trashed` table. Restored emails are pinned
    so a later cleanup leaves them alone, then the account is rescanned."""
    db = store.connect()
    connected = providers.accounts()
    by_account: dict[str, list[str]] = {}
    for acc, msg_id in items:
        by_account.setdefault(acc, []).append(msg_id)
    for acc, msg_ids in by_account.items():
        log(f"== {acc}: restoring {len(msg_ids)} emails to the Inbox")
        kind, cfg = connected.get(acc, ("", None))
        if not kind:
            log(f"skip {acc}: not connected")
            continue
        in_trash = dict(db.execute(
            f"SELECT new_id, msg_id FROM trashed WHERE account = ? AND msg_id IN ({','.join('?' * len(msg_ids))})",
            (acc, *msg_ids),
        ).fetchall())
        if kind == "outlook":
            back = outlook.restore(list(in_trash), log)
        else:
            back = providers.module(cfg).restore(cfg, list(in_trash), log)
        done = {in_trash[t]: new for t, new in back.items()}  # original id -> id in Inbox
        with db:
            db.executemany("UPDATE trashed SET restored = 1 WHERE account = ? AND msg_id = ?",
                           [(acc, m) for m in done])
            db.executemany("INSERT OR IGNORE INTO pinned VALUES (?, ?)", [(acc, new) for new in done.values()])
        log(f"{acc}: {len(done)} restored and pinned (kept from future cleanups)")
        if kind == "outlook":
            outlook.scan(db, log)
        else:
            providers.module(cfg).scan_account(cfg, db, log)
