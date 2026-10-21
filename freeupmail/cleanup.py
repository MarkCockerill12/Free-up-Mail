"""Everything that changes a mailbox: cleanup (to Trash, with a backup first), restore
(from Trash, or from the backup once Trash was emptied) and running your rules.

Safety rules applied here, whatever the GUI sent:
- only messages from the last scan are touched, and each provider re-checks them on the server;
- the plan is recomputed from your decisions at the moment it runs;
- every mailbox call goes through providers.Mailbox, which checks the account's permissions;
- with backups on (the default) an email is only moved once its copy is saved on this computer.
"""

from collections.abc import Callable
from datetime import UTC, datetime

from . import automation, backup, perms, providers, store, views

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


def _known(db, account: str, items: dict[str, int]) -> dict[str, int]:
    """Only ids that are really in this account's last scan (never anything made up)."""
    cached = {m for (m,) in db.execute("SELECT msg_id FROM messages WHERE account = ?", (account,))}
    return {m: u for m, u in items.items() if m in cached}


def trash_messages(db, account: str, items: dict[str, int], log: Log) -> int:
    """Back up then move {msg_id: uid} to Trash. Returns how many moved."""
    items = _known(db, account, items)
    if not items:
        return 0
    box = providers.Mailbox(db, account)
    keep = backup.keeper(db, account) if backup.enabled(db) else None
    mapping = box.trash(items, log, keep)  # {id before: id that finds it in Trash}
    _record_trashed(db, account, mapping, datetime.now(UTC).isoformat(timespec="seconds"))
    skipped = len(items) - len(mapping)
    log(f"{account}: {len(mapping)} moved to Trash" + (f", {skipped} skipped" if skipped else "")
        + (" (each one backed up first)" if keep else ""))
    return len(mapping)


def run_cleanup(account: str | None, log: Log) -> None:
    db = store.connect()
    for acc, items in views.cleanup_plan(db, account).items():
        log(f"== {acc}: {len(items)} emails to move to Trash")
        try:
            trash_messages(db, acc, items, log)
        except perms.NotAllowed as exc:
            log(f"skipped: {exc}")


def _rescan(db, account: str, log: Log) -> None:
    box = providers.Mailbox(db, account)
    if box.can("scan"):
        box.scan(log)


def run_restore(items: list[list[str]], log: Log) -> None:
    """items: [[account, msg_id], ...] from the `trashed` table. Each email is moved back
    from Trash, or re-uploaded from its backup when it is no longer in Trash. Restored
    emails are protected from future cleanups (by Message-ID, which survives the move)."""
    db = store.connect()
    by_account: dict[str, list[str]] = {}
    for acc, msg_id in items:
        by_account.setdefault(acc, []).append(msg_id)
    for acc, msg_ids in by_account.items():
        log(f"== {acc}: restoring {len(msg_ids)} emails to the Inbox")
        try:
            box = providers.Mailbox(db, acc)
            rows = db.execute(
                f"SELECT t.msg_id, t.new_id, COALESCE(b.rfc_id, m.rfc_id, '') FROM trashed t"
                " LEFT JOIN backups b ON b.account = t.account AND b.msg_id = t.msg_id"
                " LEFT JOIN messages m ON m.account = t.account AND m.msg_id = t.msg_id"
                f" WHERE t.account = ? AND t.restored = 0 AND t.msg_id IN ({','.join('?' * len(msg_ids))})",
                (acc, *msg_ids),
            ).fetchall()
            in_trash = {new: old for old, new, _ in rows if new}
            back = box.restore(list(in_trash), log) if in_trash else {}
            done = {in_trash[t]: new for t, new in back.items()}  # original id -> id in Inbox
            from_backup = []
            for old, _, _ in rows:
                if old not in done and (copy := backup.load(db, acc, old)):
                    from_backup.append((old, copy))
            if from_backup:
                log(f"  {len(from_backup)} are no longer in Trash: putting them back from the backups")
                box.upload([c for _, c in from_backup], log)
                done.update({old: "" for old, _ in from_backup})
        except perms.NotAllowed as exc:
            log(f"skipped: {exc}")
            continue
        lost = len(rows) - len(done)
        rfc = {old: r for old, _, r in rows}
        with db:
            db.executemany("UPDATE trashed SET restored = 1 WHERE account = ? AND msg_id = ?", [(acc, m) for m in done])
            db.executemany("INSERT OR IGNORE INTO pinned VALUES (?, ?)", [(acc, new) for new in done.values() if new])
            db.executemany("INSERT OR IGNORE INTO rescued VALUES (?, ?)", [(acc, rfc[m]) for m in done if rfc.get(m)])
        log(f"{acc}: {len(done)} restored and protected from future cleanups"
            + (f"; {lost} could not be found in Trash and had no backup" if lost else ""))
        _rescan(db, acc, log)


def run_rules(rule_ids: list[int] | None, log: Log, accounts: set[str] | None = None, auto_only: bool = False) -> None:
    """Apply your rules to the emails already in the mailboxes. Each account needs the
    permission the action uses; rules that bin mail never touch protected email."""
    db = store.connect()
    chosen = [r for r in automation.load(db) if r["enabled"] and r["action"] != "keep"
              and (rule_ids is None or r["id"] in rule_ids) and (not auto_only or r["auto"])]
    touched: set[str] = set()
    for rule in chosen:
        plan = views.rule_plan(db, rule)
        if accounts is not None:
            plan = {a: i for a, i in plan.items() if a in accounts}
        log(f"== rule '{rule['name']}': {sum(map(len, plan.values()))} emails")
        for acc, items in plan.items():
            try:
                if rule["action"] == "trash":
                    trash_messages(db, acc, items, log)
                else:
                    providers.Mailbox(db, acc).organise(
                        rule["action"], _known(db, acc, items), rule["folder"], rule["mark_read"], log)
                touched.add(acc)
            except perms.NotAllowed as exc:
                log(f"skipped {acc}: {exc}")
    for acc in sorted(touched):
        _rescan(db, acc, log)  # moves give emails new ids; read them again
