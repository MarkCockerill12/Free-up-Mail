"""Server-side filters for future mail: Gmail filter XML export, Outlook inbox rules, and a
plain address list for providers without either (paste it into their blocked senders)."""

from xml.sax.saxutils import quoteattr

from . import outlook, rules, store, views

MAX_FROM_CHARS = 900  # Gmail rejects very long criteria; split into several filters.
PROTECT_WORDS = {
    "security": ["security", "verify", "verification", "sign in", "sign-in", "login", "password", "code"],
    "receipt": ["receipt", "invoice", "payment", "refund", "payslip", "booking", "reservation", "order"],
    "shipping": ["delivery", "delivered", "dispatched", "shipped", "parcel", "tracking"],
}


def protect_words(settings: dict) -> list[str]:
    """Subject words that exempt future mail from the filters, per the protection settings."""
    words = []
    if settings["keep_security"] != "none":
        words += PROTECT_WORDS["security"]
    if settings["keep_receipts"] == "1":
        words += PROTECT_WORDS["receipt"]
    if settings["keep_shipping"] == "1":
        words += PROTECT_WORDS["shipping"]
    return words


def _protect_query(settings: dict) -> str:
    words = [f'"{w}"' if " " in w or "-" in w else w for w in protect_words(settings)]
    return f"subject:({' OR '.join(words)})" if words else ""


def deleted_senders(db, account: str) -> list[str]:
    """Addresses marked DELETE, never contacts or your own accounts (even if marked by hand)."""
    ctx = views.Context(db)
    return sorted(
        s["from_addr"] for s in views.senders(db, ctx)
        if s["account"] == account and s["action"] == "DELETE" and "@" in s["from_addr"]
        and s["from_addr"] not in ctx.contacts and s["from_addr"] not in ctx.own
    )


def _chunks(addrs: list[str]) -> list[list[str]]:
    chunks, current, size = [], [], 0
    for addr in addrs:
        if current and size + len(addr) + 4 > MAX_FROM_CHARS:
            chunks.append(current)
            current, size = [], 0
        current.append(addr)
        size += len(addr) + 4
    return chunks + [current] if current else chunks


def gmail_xml(db, account: str) -> str:
    addrs = deleted_senders(db, account)
    protect = _protect_query(store.get_settings(db, rules.DEFAULT_SETTINGS))
    entries = []
    for chunk in _chunks(addrs):
        props = [("from", " OR ".join(chunk)), ("shouldTrash", "true")]
        if protect:
            props.append(("doesNotHaveTheWord", protect))
        body = "".join(f"<apps:property name='{k}' value={quoteattr(v)}/>" for k, v in props)
        entries.append(
            "<entry><category term='filter'></category><title>Mail Filter</title>"
            f"<content></content>{body}</entry>"
        )
    return (
        "<?xml version='1.0' encoding='UTF-8'?>"
        "<feed xmlns='http://www.w3.org/2005/Atom' xmlns:apps='http://schemas.google.com/apps/2006'>"
        f"<title>Mail Filters</title>{''.join(entries)}</feed>"
    )


def address_list(db, account: str) -> str:
    return "\n".join(deleted_senders(db, account)) + "\n"


def outlook_rules(db, log) -> None:
    account = outlook.connected()["email"]
    addrs = deleted_senders(db, account)
    log(f"== {account}: creating rules for {len(addrs)} senders")
    outlook.apply_rules(addrs, protect_words(store.get_settings(db, rules.DEFAULT_SETTINGS)), log)
    log("rules saved in Outlook (Settings -> Mail -> Rules, named 'Free(up)Mail N')")
