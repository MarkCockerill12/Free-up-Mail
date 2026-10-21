"""Which module speaks to which connected account (Outlook via Graph, Gmail, any other IMAP
server), behind one Mailbox class. Mailbox is the only way the rest of the app reaches a
mailbox, and each of its methods checks the account's permissions (perms.py) first."""

from . import gmail, imapmail, imapwrite, outlook, outlook_write, perms, store

DEMO_TEXT = "This is the demo with made-up emails, so there is no real mailbox to change."


def imap_accounts(only: str | None = None) -> list[dict]:
    return [a for a in store.load_config()["imap"] if not only or a["email"] == only]


def outlook_email() -> str:
    return (outlook.connected() or {}).get("email", "")


def kind(cfg: dict) -> str:
    return "gmail" if imapmail.host_of(cfg) == gmail.HOST else "imap"


def module(cfg: dict):
    """gmail for Google mailboxes (they have All Mail and stable ids), imapmail otherwise."""
    return gmail if kind(cfg) == "gmail" else imapmail


def writer(cfg: dict):
    """The module that changes an IMAP mailbox: gmail (its ids and labels), imapwrite otherwise."""
    return gmail if kind(cfg) == "gmail" else imapwrite


def accounts() -> dict[str, tuple[str, dict | None]]:
    """{email: (type, imap config or None)} for every connected account."""
    config = store.load_config()
    found: dict[str, tuple[str, dict | None]] = {a["email"]: (kind(a), a) for a in config["imap"]}
    if config.get("demo_outlook"):
        found[config["demo_outlook"]] = ("outlook", {"email": config["demo_outlook"], "demo": True})
    elif outlook_email():
        found[outlook_email()] = ("outlook", None)
    return found


def web_link(entry: tuple[str, dict | None] | None, rfc_id: str) -> str:
    """Link to the email in the provider's webmail; Outlook's comes with the preview instead."""
    if not entry or entry[0] == "outlook" or (entry[1] or {}).get("demo"):
        return ""
    return module(entry[1]).web_link(entry[1], rfc_id)


class Mailbox:
    """One connected account. Every method that reads or changes mail checks its permission."""

    def __init__(self, db, email: str):
        entry = accounts().get(email)
        if not entry:
            raise perms.NotAllowed(f"{email} is not connected.")
        self.db, self.email, (self.kind, self.cfg) = db, email, entry
        self.demo = bool((self.cfg or {}).get("demo"))

    def _need(self, *needed: str, change: bool = True) -> None:
        for perm in needed:
            perms.require(self.db, self.email, perm)
        if self.demo and change:
            raise perms.NotAllowed(DEMO_TEXT)

    def can(self, perm: str) -> bool:
        return perms.allowed(self.db, self.email, perm)

    def scan(self, log) -> bool:
        self._need("scan", change=False)
        if self.demo:
            log(f"{self.email}: demo account, nothing to scan")
            return True
        if self.kind == "outlook":
            outlook.scan(self.db, log)
            return True
        return module(self.cfg).scan_account(self.cfg, self.db, log)

    def fetch(self, msg_id: str) -> dict:
        self._need("read", change=False)
        if self.demo:
            from . import demo
            return demo.message(self.db, self.email, msg_id)
        if self.kind == "outlook":
            return outlook.fetch_message(msg_id)
        return module(self.cfg).fetch_message(self.cfg, msg_id)

    def _expected(self, items) -> dict[str, str]:
        """{msg_id: Message-ID} from the last scan, so IMAP servers can be re-checked."""
        rows = self.db.execute("SELECT msg_id, rfc_id FROM messages WHERE account = ?", (self.email,))
        return {m: r or "" for m, r in rows if m in items}

    def trash(self, items: dict[str, int], log, keep=None) -> dict[str, str]:
        """{msg_id: uid} to Trash; `keep` saves a backup of each first (needs "read")."""
        self._need("trash", *(("read",) if keep else ()))
        if self.kind == "outlook":
            return outlook_write.trash(list(items), log, keep)
        return writer(self.cfg).trash(self.cfg, items, log, keep, self._expected(items))

    def restore(self, trash_ids: list[str], log) -> dict[str, str]:
        self._need("trash")
        if self.kind == "outlook":
            return outlook_write.restore(trash_ids, log)
        return writer(self.cfg).restore(self.cfg, trash_ids, log)

    def upload(self, emails: list[tuple[bytes, int]], log) -> list[int]:
        """Positions of `emails` that were put back."""
        self._need("trash")
        if self.kind == "outlook":
            return outlook_write.upload(emails, log)
        return writer(self.cfg).upload(self.cfg, emails, log)

    def present(self, rfc_ids: list[str], log) -> set[str]:
        """Message-IDs already back in the mailbox (so a backup isn't uploaded twice)."""
        self._need("scan", change=False)
        if self.demo or not rfc_ids:
            return set()
        if self.kind == "outlook":
            return outlook_write.present(rfc_ids, log)
        return writer(self.cfg).present(self.cfg, rfc_ids, log)

    def organise(self, action: str, items: dict[str, int], folder: str, mark_read: bool, log) -> int:
        self._need("organise")
        if self.kind == "outlook":
            return outlook_write.organise(action, list(items), folder, mark_read, log)
        return writer(self.cfg).organise(self.cfg, action, items, folder, mark_read, log, self._expected(items))
