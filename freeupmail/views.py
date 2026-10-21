"""Read models for the GUI: per-sender aggregates, email lists, cleanup plans.

Every "will this be deleted?" answer goes through `_status`, so the GUI, the counts
and the actual cleanup can never disagree.
"""

from . import providers, rules, store

BULK_CATS = ("promotions", "social")
CATS = {"promotions", "social", "updates", "forums", "other"}


class Context:
    def __init__(self, db):
        self.settings = store.get_settings(db, rules.DEFAULT_SETTINGS)
        # Anyone you have emailed from ANY account counts as a contact everywhere.
        self.contacts = {a for (a,) in db.execute("SELECT DISTINCT addr FROM contacts")}
        self.own = {a for (a,) in db.execute("SELECT DISTINCT account FROM messages")}
        self.pinned = set(db.execute("SELECT account, msg_id FROM pinned"))
        self.overrides = {
            (a, f): act for a, f, act in db.execute("SELECT account, from_addr, action FROM sender_actions")
        }


def _status(action: str, kind: str, date: str, labels: set, pinned: bool, ctx: Context) -> str:
    if action != "DELETE":
        return action.lower()
    starred = "starred" in labels
    return "protected" if rules.is_kept(kind, date, ctx.settings, starred, pinned) else "delete"


def senders(db, ctx: Context | None = None) -> list[dict]:
    ctx = ctx or Context(db)
    out: dict[tuple[str, str], dict] = {}
    rows = db.execute(
        "SELECT account, msg_id, from_addr, from_name, from_domain, subject, date, seen, list_unsub,"
        " labels, unsub_url FROM messages ORDER BY date DESC"
    ).fetchall()
    for account, msg_id, addr, name, domain, subject, date, seen, unsub, labels, unsub_url in rows:
        s = out.get((account, addr))
        if s is None:
            s = out[(account, addr)] = {
                "account": account, "from_addr": addr, "from_name": name, "domain": domain,
                "count": 0, "unread": 0, "unsub": 0, "inbox": 0, "spam_hits": 0, "deletable": 0,
                "kinds": {"security": 0, "receipt": 0, "shipping": 0, "starred": 0, "pinned": 0},
                "cats": {}, "starred": False, "last_date": date[:10], "first_date": date[:10],
                "sample_subject": subject, "unsub_url": unsub_url or "",
                "is_contact": addr in ctx.contacts,
            }
        label_set = set(labels.split(",")) if labels else set()
        pinned = (account, msg_id) in ctx.pinned
        kind = rules.classify(subject)
        s["count"] += 1
        s["unread"] += not seen
        s["unsub"] += unsub
        s["first_date"] = date[:10] or s["first_date"]
        s["unsub_url"] = s["unsub_url"] or unsub_url or ""
        s["spam_hits"] += rules.looks_like_spam(addr, subject)
        if kind:
            s["kinds"][kind] += 1
        s["kinds"]["starred"] += "starred" in label_set
        s["kinds"]["pinned"] += pinned
        s["deletable"] += _status("DELETE", kind, date, label_set, pinned, ctx) == "delete"
        s["starred"] |= "starred" in label_set
        s["inbox"] += "inbox" in label_set
        for cat in label_set & CATS:
            s["cats"][cat] = s["cats"].get(cat, 0) + 1
    result = []
    for key, s in out.items():
        s["promo_social"] = sum(s["cats"].get(c, 0) for c in BULK_CATS) > s["count"] / 2
        # Outlook's non-Focused "Other" tab also holds transactional mail: only a hint.
        s["mostly_other"] = s["cats"].get("other", 0) > s["count"] / 2
        s["spammy"] = s["spam_hits"] * 2 >= s["count"]
        s["suggested"], s["reason"] = rules.suggest(s, ctx.own)
        s["override"] = ctx.overrides.get(key)
        s["action"] = s["override"] or s["suggested"]
        s["would_delete"] = s["deletable"] if s["action"] == "DELETE" else 0
        result.append(s)
    return sorted(result, key=lambda r: -r["count"])


def emails(db, params: dict) -> dict:
    """Filtered, paged email list. params: account, q, from_addrs, status, kind, cat,
    unread, offset, limit."""
    if params.get("status") == "trashed":
        return _trashed(db, params)
    ctx = Context(db)
    actions = {(s["account"], s["from_addr"]): s["action"] for s in senders(db, ctx)}
    account, q = params.get("account") or "", (params.get("q") or "").lower()
    from_addrs = set(params.get("from_addrs") or [])
    status_filter, kind_filter, cat = params.get("status") or "", params.get("kind") or "", params.get("cat") or ""
    connected = providers.accounts()
    items = []
    # Per-status totals for the current filters (ignoring the status filter itself).
    counts = {"all": 0, "pinned": 0, "unread": 0}
    for acc, msg_id, addr, name, subject, date, seen, labels, rfc_id in db.execute(
        "SELECT account, msg_id, from_addr, from_name, subject, date, seen, labels, rfc_id"
        " FROM messages ORDER BY date DESC"
    ):
        if account and acc != account:
            continue
        if from_addrs and addr not in from_addrs:
            continue
        if q and q not in f"{addr} {name} {subject}".lower():
            continue
        label_set = set(labels.split(",")) if labels else set()
        if cat and (cat not in label_set if cat != "none" else label_set & CATS):
            continue
        if params.get("unread") and seen:
            continue
        if params.get("inbox") and "inbox" not in label_set:
            continue
        kind = rules.classify(subject)
        if kind_filter and kind != kind_filter:
            continue
        pinned = (acc, msg_id) in ctx.pinned
        action = actions.get((acc, addr), "REVIEW")
        status = _status(action, kind, date, label_set, pinned, ctx)
        counts[status] = counts.get(status, 0) + 1
        counts["pinned"] += pinned
        counts["unread"] += not seen
        counts["all"] += 1
        if status_filter == "pinned" and not pinned:
            continue
        if status_filter and status_filter != "pinned" and status != status_filter:
            continue
        items.append({
            "account": acc, "msg_id": msg_id, "from_addr": addr, "from_name": name, "subject": subject,
            "date": date[:16].replace("T", " "), "seen": bool(seen), "labels": labels, "kind": kind,
            "starred": "starred" in label_set, "pinned": pinned, "action": action, "status": status,
            "web_link": providers.web_link(connected.get(acc), rfc_id),
        })
    offset, limit = int(params.get("offset") or 0), int(params.get("limit") or 200)
    return {"total": len(items), "items": items[offset : offset + limit], "counts": counts}


def _trashed(db, params: dict) -> dict:
    account, q = params.get("account") or "", (params.get("q") or "").lower()
    items = []
    for acc, msg_id, addr, name, subject, date, trashed_at in db.execute(
        "SELECT account, msg_id, from_addr, from_name, subject, date, trashed_at FROM trashed"
        " WHERE restored = 0 ORDER BY trashed_at DESC, date DESC"
    ):
        if (account and acc != account) or (q and q not in f"{addr} {name} {subject}".lower()):
            continue
        items.append({
            "account": acc, "msg_id": msg_id, "from_addr": addr, "from_name": name, "subject": subject,
            "date": (date or "")[:16].replace("T", " "), "trashed_at": trashed_at, "status": "trashed",
            "kind": rules.classify(subject or ""), "seen": True, "labels": "", "pinned": False,
        })
    offset, limit = int(params.get("offset") or 0), int(params.get("limit") or 200)
    return {"total": len(items), "items": items[offset : offset + limit]}


def cleanup_plan(db, account: str | None = None) -> dict[str, dict[str, int]]:
    """{account: {msg_id: uid}} for every message the current decisions would trash
    (uid is 0 for Outlook, which has none)."""
    ctx = Context(db)
    actions = {(s["account"], s["from_addr"]): s["action"] for s in senders(db, ctx)}
    plan: dict[str, dict[str, int]] = {}
    for acc, msg_id, uid, addr, subject, date, labels in db.execute(
        "SELECT account, msg_id, uid, from_addr, subject, date, labels FROM messages"
    ):
        if (account and acc != account) or addr in ctx.own:
            continue
        label_set = set(labels.split(",")) if labels else set()
        pinned = (acc, msg_id) in ctx.pinned
        status = _status(actions.get((acc, addr), "REVIEW"), rules.classify(subject), date, label_set, pinned, ctx)
        if status == "delete":
            plan.setdefault(acc, {})[msg_id] = uid
    return plan
