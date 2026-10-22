"""Server-side filters for future mail: Gmail filter XML export, Outlook inbox rules, and a
plain address list for providers without either (paste it into their blocked senders)."""

from xml.sax.saxutils import quoteattr

from . import automation, outlook, outlook_write, perms, rules, store, views

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


# ---------- your rules, translated for the providers ----------

def _term(value: str) -> str:
    return f'"{value}"' if " " in value else value


def gmail_filter(rule: dict) -> list[tuple[str, str]] | None:
    """Gmail filter properties for a rule, or None if Gmail filters can't express it
    (they only see new mail as it arrives: no age, unread or Free(up)Mail kinds)."""
    terms = []
    for c in rule["conditions"]:
        field, op, value = c["field"], c["op"], c["value"]
        if field in ("from", "domain", "subject"):
            key = "subject" if field == "subject" else "from"
            if field == "domain" and op in ("is", "ends"):
                value = "@" + value.lstrip("@")
            terms.append(f"{'-' if op == 'not_contains' else ''}{key}:({_term(value)})")
        elif field == "category":
            terms.append(f"{'-' if op == 'is_not' else ''}category:{value}")
        else:
            return None
    query = " ".join(terms) if rule["match"] == "all" else "{" + " ".join(terms) + "}"
    action = rule["action"]
    props = {
        "move": [("label", rule["folder"]), ("shouldArchive", "true")], "archive": [("shouldArchive", "true")],
        "trash": [("shouldTrash", "true")], "read": [("shouldMarkAsRead", "true")],
        "star": [("shouldStar", "true")], "keep": [("shouldNeverSpam", "true")],
    }.get(action)
    if props is None:
        return None  # Gmail filters cannot send mail to Spam
    if rule["mark_read"] and action != "read":
        props.append(("shouldMarkAsRead", "true"))
    return [("hasTheWord", query), *props]


def gmail_filter_protected(rule: dict, protect: str) -> list[tuple[str, str]] | None:
    """gmail_filter, plus your Protection words for rules that bin mail (as the app does)."""
    props = gmail_filter(rule)
    if props and rule["action"] == "trash" and protect:
        props.append(("doesNotHaveTheWord", protect))
    return props


OUTLOOK_KEYS = {"from": "senderContains", "domain": "senderContains", "subject": "subjectContains"}


def outlook_rule(rule: dict) -> dict | None:
    """An Outlook inbox rule for `rule` (see outlook_write.apply_rules), or None if Outlook rules
    can't express it."""
    conditions: dict[str, list[str]] = {}
    exceptions: dict[str, list[str]] = {}
    for c in rule["conditions"]:
        field, op, value = c["field"], c["op"], c["value"]
        if field in OUTLOOK_KEYS:
            key, target = OUTLOOK_KEYS[field], exceptions if op == "not_contains" else conditions
        elif field == "list":
            key, target, value = "headerContains", conditions if op == "yes" else exceptions, "List-Unsubscribe"
        else:
            return None
        target.setdefault(key, []).append(value)
    # Values in one list are OR'd and different lists AND'd: "all" needs one value per
    # list, "any" needs everything in a single list.
    if rule["match"] == "all" and any(len(v) > 1 for v in conditions.values()):
        return None
    if rule["match"] == "any" and (len(conditions) > 1 or exceptions):
        return None
    if not conditions:
        return None
    action = rule["action"]
    actions = {"read": {"markAsRead": True}, "trash": {"delete": True}}.get(action, {})
    folder = {"move": rule["folder"], "archive": "archive", "junk": "junkemail"}.get(action, "")
    if not actions and not folder:
        return None  # star / keep have no Outlook rule action
    if rule["mark_read"]:
        actions["markAsRead"] = True
    return {"name": rule["name"], "conditions": conditions, "exceptions": exceptions, "actions": actions, "folder": folder}


def exportable(rule: dict) -> dict:
    return {"gmail": gmail_filter(rule) is not None, "outlook": outlook_rule(rule) is not None}


def _own_rules(db, account: str) -> list[dict]:
    return [r for r in automation.load(db) if r["enabled"] and (not r["accounts"] or account in r["accounts"])]


def _entry(props: list[tuple[str, str]]) -> str:
    body = "".join(f"<apps:property name='{k}' value={quoteattr(v)}/>" for k, v in props)
    return f"<entry><category term='filter'></category><title>Mail Filter</title><content></content>{body}</entry>"


def gmail_xml(db, account: str) -> str:
    """Gmail's filter import file: your rules that Gmail can express, then the senders marked Delete."""
    protect = _protect_query(store.get_settings(db, rules.DEFAULT_SETTINGS))
    entries = [_entry(props) for r in _own_rules(db, account) if (props := gmail_filter_protected(r, protect))]
    addrs = deleted_senders(db, account)
    for chunk in _chunks(addrs):
        props = [("from", " OR ".join(chunk)), ("shouldTrash", "true")]
        if protect:
            props.append(("doesNotHaveTheWord", protect))
        entries.append(_entry(props))
    return (
        "<?xml version='1.0' encoding='UTF-8'?>"
        "<feed xmlns='http://www.w3.org/2005/Atom' xmlns:apps='http://schemas.google.com/apps/2006'>"
        f"<title>Mail Filters</title>{''.join(entries)}</feed>"
    )


def address_list(db, account: str) -> str:
    return "\n".join(deleted_senders(db, account)) + "\n"


def outlook_rules(db, account: str, log) -> None:
    perms.require(db, account, "server_rules")
    addrs = deleted_senders(db, account)
    words = protect_words(store.get_settings(db, rules.DEFAULT_SETTINGS))
    own = []
    for r in _own_rules(db, account):
        o = outlook_rule(r)
        if not o:
            continue
        if r["action"] == "trash" and words:  # binning rules keep your Protection words, as the app does
            o["exceptions"].setdefault("subjectContains", []).extend(words)
        own.append(o)
    log(f"== {account}: creating {len(own)} of your rules and rules for {len(addrs)} senders marked Delete")
    with outlook.use(account):
        outlook_write.apply_rules(addrs, protect_words(store.get_settings(db, rules.DEFAULT_SETTINGS)), log, own)
    log("rules saved in Outlook (Settings -> Mail -> Rules, named 'Free(up)Mail N')")
