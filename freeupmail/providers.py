"""Which module speaks to which connected account: Outlook (Graph), Gmail or any other IMAP server."""

from . import gmail, imapmail, outlook, store


def imap_accounts(only: str | None = None) -> list[dict]:
    return [a for a in store.load_config()["imap"] if not only or a["email"] == only]


def outlook_email() -> str:
    return (outlook.connected() or {}).get("email", "")


def kind(cfg: dict) -> str:
    return "gmail" if imapmail.host_of(cfg) == gmail.HOST else "imap"


def module(cfg: dict):
    """gmail for Google mailboxes (they have All Mail and stable ids), imapmail otherwise."""
    return gmail if kind(cfg) == "gmail" else imapmail


def accounts() -> dict[str, tuple[str, dict | None]]:
    """{email: (type, imap config or None)} for every connected account."""
    found: dict[str, tuple[str, dict | None]] = {a["email"]: (kind(a), a) for a in imap_accounts()}
    if outlook_email():
        found[outlook_email()] = ("outlook", None)
    return found


def web_link(entry: tuple[str, dict | None] | None, rfc_id: str) -> str:
    """Link to the email in the provider's webmail; Outlook's comes with the preview instead."""
    if not entry or entry[0] == "outlook":
        return ""
    return module(entry[1]).web_link(entry[1], rfc_id)
