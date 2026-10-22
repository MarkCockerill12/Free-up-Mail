"""Read models for the GUI: per-sender aggregates, email lists, cleanup and rule plans.

Every "will this be deleted?" answer goes through `_status`, so the GUI, the counts
and the actual cleanup can never disagree.
"""

import threading
from datetime import UTC, datetime

from . import automation, providers, rules, store

BULK_CATS = ("promotions", "social")
CATS = {"promotions", "social", "updates", "forums", "other"}
COLUMNS = ("account", "msg_id", "uid", "from_addr", "from_name", "from_domain", "subject", "date", "seen",
           "list_unsub", "labels", "unsub_url", "rfc_id")
SELECT = f"SELECT {', '.join(COLUMNS)} FROM messages"


class Context:
    def __init__(self, db):
        self.settings = store.get_settings(db, rules.DEFAULT_SETTINGS)
        # Anyone you have emailed from ANY account counts as a contact everywhere.
        self.contacts = {a for (a,) in db.execute("SELECT DISTINCT addr FROM contacts")}
        self.own = {a for (a,) in db.execute("SELECT DISTINCT account FROM messages")}
        self.pinned = set(db.execute("SELECT account, msg_id FROM pinned"))
        self.rescued = set(db.execute("SELECT account, rfc_id FROM rescued"))
        self.overrides = {
            (a, f): act for a, f, act in db.execute("SELECT account, from_addr, action FROM sender_actions")
        }
        self.rules = automation.load(db)
        self.keep_rules = [r for r in self.rules if r["enabled"] and r["action"] == "keep"]
        self.now = datetime.now(UTC)

    def is_pinned(self, row: dict) -> bool:
        """Pinned by you, or restored by you (matched by Message-ID, which survives moves)."""
        return (row["account"], row["msg_id"]) in self.pinned or (
            bool(row["rfc_id"]) and (row["account"], row["rfc_id"]) in self.rescued)

    def kept_by_rule(self, row: dict) -> bool:
        return any(automation.matches(r, row, self.now) for r in self.keep_rules)


# Every list, total and plan starts from all the messages and the per-sender totals. They
# only change when store.data_version does (or the hour turns: protection ages with time),
# so they're kept here between requests. Treat what comes out as read-only.
_cache: dict = {}
_cache_lock = threading.RLock()


def _cached(db, name: str, build):
    key = (db.execute("PRAGMA database_list").fetchone()[2], db.execute("SELECT v FROM data_version").fetchone()[0],
           datetime.now(UTC).strftime("%Y%m%d%H"))
    with _cache_lock:  # concurrent requests wait for one build instead of each doing it
        if _cache.get("key") != key:
            _cache.clear()
            _cache["key"] = key
        if name not in _cache:
            _cache[name] = build()
        return _cache[name]


def iter_rows(db, where: str = "", params: tuple = ()):
    """Messages as dicts with the extras matching needs: labels as a set, and the kind."""
    if not where:
        return iter(_cached(db, "rows", lambda: list(_read_rows(db))))
    return _read_rows(db, where, params)


def _read_rows(db, where: str = "", params: tuple = ()):
    for values in db.execute(f"{SELECT} {where} ORDER BY date DESC, msg_id", params):
        row = dict(zip(COLUMNS, values, strict=True))
        row["labels"] = set(row["labels"].split(",")) if row["labels"] else set()
        row["subject"] = row["subject"] or ""
        row["kind"] = rules.classify(row["subject"])
        yield row


def _status(action: str, kind: str, date: str, labels: set, pinned: bool, ctx: Context) -> str:
    if action != "DELETE":
        return action.lower()
    starred = "starred" in labels
    return "protected" if rules.is_kept(kind, date, ctx.settings, starred, pinned) else "delete"


def row_status(row: dict, action: str, ctx: Context) -> str:
    protected = ctx.is_pinned(row) or ctx.kept_by_rule(row)
    return _status(action, row["kind"], row["date"], row["labels"], protected, ctx)


def senders(db, ctx: Context | None = None, where: str = "", params: tuple = ()) -> list[dict]:
    """Per-sender aggregates. Each sender's suggestion depends only on its own emails, so
    `where` may narrow this to some accounts/senders without changing their answers."""
    ctx = ctx or Context(db)
    base = _aggregate(db, ctx, where, params) if where else _cached(db, "senders", lambda: _aggregate(db, ctx))
    result = []
    for key, s in base.items():
        override = ctx.overrides.get(key)
        action = override or s["suggested"]
        result.append({**s, "override": override, "action": action,
                       "would_delete": s["deletable"] if action == "DELETE" else 0})
    return result


def plan_counts(db) -> dict[str, int]:
    """{account: emails a cleanup would trash}: the same answers as cleanup_plan, from the
    per-sender totals instead of a pass over every email."""
    ctx = Context(db)
    out: dict[str, int] = {}
    for s in senders(db, ctx):
        if s["would_delete"] and s["from_addr"] not in ctx.own:
            out[s["account"]] = out.get(s["account"], 0) + s["would_delete"]
    return out


def _aggregate(db, ctx: Context, where: str = "", params: tuple = ()) -> dict[tuple[str, str], dict]:
    """Per-sender totals and suggestion, biggest first, before your own Delete/Keep choices."""
    out: dict[tuple[str, str], dict] = {}
    for row in iter_rows(db, where, params):
        account, addr, date = row["account"], row["from_addr"], row["date"]
        s = out.get((account, addr))
        if s is None:
            s = out[(account, addr)] = {
                "account": account, "from_addr": addr, "from_name": row["from_name"], "domain": row["from_domain"],
                "count": 0, "unread": 0, "unsub": 0, "inbox": 0, "spam_hits": 0, "deletable": 0,
                "kinds": {"security": 0, "receipt": 0, "shipping": 0, "starred": 0, "pinned": 0, "rule": 0},
                "cats": {}, "starred": False, "last_date": date[:10], "first_date": date[:10],
                "sample_subject": row["subject"], "unsub_url": row["unsub_url"] or "",
                "is_contact": addr in ctx.contacts,
            }
        labels, kind = row["labels"], row["kind"]
        pinned, by_rule = ctx.is_pinned(row), ctx.kept_by_rule(row)
        s["count"] += 1
        s["unread"] += not row["seen"]
        s["unsub"] += row["list_unsub"]
        s["first_date"] = date[:10] or s["first_date"]
        s["unsub_url"] = s["unsub_url"] or row["unsub_url"] or ""
        s["spam_hits"] += rules.looks_like_spam(addr, row["subject"])
        if kind:
            s["kinds"][kind] += 1
        s["kinds"]["starred"] += "starred" in labels
        s["kinds"]["pinned"] += pinned
        s["kinds"]["rule"] += by_rule
        s["deletable"] += _status("DELETE", kind, date, labels, pinned or by_rule, ctx) == "delete"
        s["starred"] |= "starred" in labels
        s["inbox"] += "inbox" in labels
        for cat in labels & CATS:
            s["cats"][cat] = s["cats"].get(cat, 0) + 1
    for s in out.values():
        s["promo_social"] = sum(s["cats"].get(c, 0) for c in BULK_CATS) > s["count"] / 2
        # Outlook's non-Focused "Other" tab also holds transactional mail: only a hint.
        s["mostly_other"] = s["cats"].get("other", 0) > s["count"] / 2
        s["spammy"] = s["spam_hits"] * 2 >= s["count"]
        s["suggested"], s["reason"] = rules.suggest(s, ctx.own)
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["count"]))


def emails(db, params: dict) -> dict:
    """Filtered, paged email list. params: account, q, from_addrs, status, kind, cat,
    unread, offset, limit."""
    if params.get("status") == "trashed":
        return _trashed(db, params)
    ctx = Context(db)
    account, q = params.get("account") or "", (params.get("q") or "").lower()
    from_addrs = set(params.get("from_addrs") or [])
    # Narrow in SQL first: one sender's emails shouldn't cost a pass over every mailbox.
    clauses, args = [], []
    if account:
        clauses.append("account = ?")
        args.append(account)
    if 0 < len(from_addrs) <= 500:
        clauses.append(f"from_addr IN ({', '.join('?' * len(from_addrs))})")
        args += sorted(from_addrs)
    where, args = (f"WHERE {' AND '.join(clauses)}" if clauses else ""), tuple(args)
    actions = {(s["account"], s["from_addr"]): s["action"] for s in senders(db, ctx, where, args)}
    status_filter, kind_filter, cat = params.get("status") or "", params.get("kind") or "", params.get("cat") or ""
    connected = providers.accounts()
    items = []
    # Per-status totals for the current filters (ignoring the status filter itself).
    counts = {"all": 0, "pinned": 0, "unread": 0}
    for row in iter_rows(db, where, args):
        acc, addr, name, subject = row["account"], row["from_addr"], row["from_name"], row["subject"]
        if account and acc != account:
            continue
        if from_addrs and addr not in from_addrs:
            continue
        if q and q not in f"{addr} {name} {subject}".lower():
            continue
        label_set = row["labels"]
        if cat and (cat not in label_set if cat != "none" else label_set & CATS):
            continue
        if params.get("unread") and row["seen"]:
            continue
        if params.get("inbox") and "inbox" not in label_set:
            continue
        if kind_filter and row["kind"] != kind_filter:
            continue
        pinned, by_rule = ctx.is_pinned(row), ctx.kept_by_rule(row)
        action = actions.get((acc, addr), "REVIEW")
        status = _status(action, row["kind"], row["date"], label_set, pinned or by_rule, ctx)
        counts[status] = counts.get(status, 0) + 1
        counts["pinned"] += pinned
        counts["unread"] += not row["seen"]
        counts["all"] += 1
        if status_filter == "pinned" and not pinned:
            continue
        if status_filter and status_filter != "pinned" and status != status_filter:
            continue
        items.append({
            "account": acc, "msg_id": row["msg_id"], "from_addr": addr, "from_name": name, "subject": subject,
            "date": row["date"][:16].replace("T", " "), "seen": bool(row["seen"]), "labels": ",".join(sorted(label_set)),
            "kind": row["kind"], "starred": "starred" in label_set, "pinned": pinned, "by_rule": by_rule,
            "action": action, "status": status, "web_link": providers.web_link(connected.get(acc), row["rfc_id"]),
        })
    counts["trashed"] = db.execute("SELECT COUNT(*) FROM trashed WHERE restored = 0").fetchone()[0]
    offset, limit = int(params.get("offset") or 0), int(params.get("limit") or 200)
    return {"total": len(items), "items": items[offset : offset + limit], "counts": counts}


def _trashed(db, params: dict) -> dict:
    """Everything Free(up)Mail moved to Trash and that hasn't been put back, with whether
    a local backup copy exists (it can be restored even after the Trash is emptied)."""
    account, q = params.get("account") or "", (params.get("q") or "").lower()
    items = []
    for acc, msg_id, addr, name, subject, date, trashed_at, has_backup in db.execute(
        "SELECT t.account, t.msg_id, t.from_addr, t.from_name, t.subject, t.date, t.trashed_at, b.path IS NOT NULL"
        " FROM trashed t LEFT JOIN backups b ON b.account = t.account AND b.msg_id = t.msg_id"
        " WHERE t.restored = 0 ORDER BY t.trashed_at DESC, t.date DESC"
    ):
        if (account and acc != account) or (q and q not in f"{addr} {name} {subject}".lower()):
            continue
        items.append({
            "account": acc, "msg_id": msg_id, "from_addr": addr, "from_name": name, "subject": subject,
            "date": (date or "")[:16].replace("T", " "), "trashed_at": trashed_at, "status": "trashed",
            "kind": rules.classify(subject or ""), "seen": True, "labels": "", "pinned": False,
            "backup": bool(has_backup),
        })
    offset, limit = int(params.get("offset") or 0), int(params.get("limit") or 200)
    return {"total": len(items), "items": items[offset : offset + limit]}


def cleanup_plan(db, account: str | None = None) -> dict[str, dict[str, int]]:
    """{account: {msg_id: uid}} for every message the current decisions would trash
    (uid is 0 for Outlook, which has none)."""
    ctx = Context(db)
    actions = {(s["account"], s["from_addr"]): s["action"] for s in senders(db, ctx)}
    plan: dict[str, dict[str, int]] = {}
    for row in iter_rows(db):
        acc = row["account"]
        if (account and acc != account) or row["from_addr"] in ctx.own:
            continue
        if row_status(row, actions.get((acc, row["from_addr"]), "REVIEW"), ctx) == "delete":
            plan.setdefault(acc, {})[row["msg_id"]] = row["uid"]
    return plan


def rule_plan(db, rule: dict, ctx: Context | None = None) -> dict[str, dict[str, int]]:
    """{account: {msg_id: uid}} that a rule would act on now. A rule that bins mail never
    touches protected email: contacts, your own addresses, starred/pinned/restored,
    "always keep" rules and the kinds kept under Protection. Junk counts as binning."""
    ctx = ctx or Context(db)
    plan: dict[str, dict[str, int]] = {}
    for row in iter_rows(db):
        if row["from_addr"] in ctx.own or not automation.matches(rule, row, ctx.now):
            continue
        action = rule["action"]
        # Junk counts as binning: spam folders empty themselves (Gmail after 30 days).
        if action in ("trash", "junk") and (
                row["from_addr"] in ctx.contacts or row_status(row, "DELETE", ctx) != "delete"):
            continue
        if (action == "read" and row["seen"]) or (action == "star" and "starred" in row["labels"]):
            continue
        if action == "archive" and "inbox" not in row["labels"]:
            continue  # archiving only ever takes emails out of the inbox
        plan.setdefault(row["account"], {})[row["msg_id"]] = row["uid"]
    return plan


def rule_preview(db, rule: dict) -> dict:
    ctx = Context(db)
    plan = rule_plan(db, rule, ctx)
    wanted = {(a, m) for a, items in plan.items() for m in items}
    sample = []
    for row in iter_rows(db):
        if (row["account"], row["msg_id"]) in wanted:
            sample.append({"account": row["account"], "from": row["from_name"] or row["from_addr"],
                           "subject": row["subject"], "date": row["date"][:10]})
            if len(sample) >= 30:
                break
    return {"total": len(wanted), "accounts": {a: len(i) for a, i in plan.items()}, "sample": sample}
