"""What Free(up)Mail may do in each mailbox. Nothing is listed, opened or changed unless
the matching permission was granted for that account, and every provider call that
reads or changes mail checks it here first (not just the buttons in the GUI)."""

PERMS = {
    "scan": "List emails (sender, subject and date only) so they can be sorted",
    "read": "Open full emails: the preview pane, and the backup copy saved before deleting",
    "trash": "Move emails to Trash, and back again when you restore them",
    "organise": "Move emails to folders, archive, mark as read or flag them (your rules)",
    "server_rules": "Create filter rules inside the mailbox so it sorts new mail by itself (Outlook)",
}
DEFAULT_NEW = ("scan",)  # pre-ticked when adding an account; everything else is opt-in


class NotAllowed(Exception):
    """A mailbox action the user has not granted."""


def granted(db, account: str) -> set[str]:
    return {p for (p,) in db.execute("SELECT perm FROM grants WHERE account = ?", (account,))}


def all_grants(db) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for account, perm in db.execute("SELECT account, perm FROM grants ORDER BY perm"):
        out.setdefault(account, []).append(perm)
    return out


def allowed(db, account: str, perm: str) -> bool:
    return db.execute("SELECT 1 FROM grants WHERE account = ? AND perm = ?", (account, perm)).fetchone() is not None


def require(db, account: str, perm: str) -> None:
    if not allowed(db, account, perm):
        raise NotAllowed(
            f"{account}: Free(up)Mail does not have permission to {PERMS[perm][0].lower() + PERMS[perm][1:]}. "
            "You can allow it under Accounts → Permissions."
        )


def set_grants(db, account: str, perms: list[str]) -> None:
    unknown = set(perms) - set(PERMS)
    if unknown:
        raise ValueError(f"unknown permission {sorted(unknown)}")
    with db:
        db.execute("DELETE FROM grants WHERE account = ?", (account,))
        db.executemany("INSERT INTO grants VALUES (?, ?)", [(account, p) for p in perms])


def grant(db, account: str, perm: str) -> None:
    if perm not in PERMS:
        raise ValueError(f"unknown permission {perm}")
    with db:
        db.execute("INSERT OR IGNORE INTO grants VALUES (?, ?)", (account, perm))


def forget(db, account: str) -> None:
    with db:
        db.execute("DELETE FROM grants WHERE account = ?", (account,))
