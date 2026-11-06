"""One click for junk: a rule that bins everything from anyone who has mail in a Junk / Spam
folder (re-checked after every scan), and optionally marks those senders Delete so the Gmail
filter file / Outlook rules built from "senders marked Delete" block their future mail too.

The rule is an ordinary rule (Rules window): it never touches people you've emailed, your own
addresses, starred / pinned / restored mail, and it goes through the same permission checks."""

from . import api, automation, store, views

RULE_NAME = "Junk folder senders"
RULE = {
    "name": RULE_NAME, "enabled": True, "match": "all", "accounts": [], "action": "trash", "auto": True,
    "conditions": [{"field": "junk_sender", "op": "yes", "value": ""}],
}


def junk_senders(db) -> list[tuple[str, str]]:
    """(account, address) of every sender with mail in a Junk folder, minus contacts and your own addresses."""
    ctx = views.Context(db)
    found = db.execute("SELECT DISTINCT account, from_addr FROM messages WHERE labels LIKE '%junk%' AND from_addr != ''")
    return sorted((a, f) for a, f in found if f not in ctx.contacts and f not in ctx.own)


def _existing(db) -> dict | None:
    return next((r for r in automation.load(db) if r["name"] == RULE_NAME), None)


def status(_: dict) -> dict:
    db = store.connect()
    rule = _existing(db)
    plan = views.rule_plan(db, automation.validate(RULE))
    in_junk = db.execute("SELECT COUNT(*) FROM messages WHERE labels LIKE '%junk%'").fetchone()[0]
    return {"rule": rule["id"] if rule else None, "enabled": bool(rule and rule["enabled"]),
            "junk_emails": in_junk, "senders": len(junk_senders(db)),
            "would_trash": sum(map(len, plan.values())), "accounts": {a: len(i) for a, i in plan.items()}}


def create(body: dict) -> dict:
    """Create (or switch back on) the junk-senders rule. With `block`, also mark the senders Delete."""
    db = store.connect()
    rule = _existing(db)
    rule_id = automation.save(db, {**RULE, "id": rule["id"] if rule else None})
    blocked = 0
    if body.get("block"):
        overridden = {(a, f) for a, f in db.execute("SELECT account, from_addr FROM sender_actions")}
        todo = [(a, f) for a, f in junk_senders(db) if (a, f) not in overridden]  # never undo a choice of yours
        with db:
            db.executemany("INSERT INTO sender_actions VALUES (?, ?, 'DELETE')", todo)
        blocked = len(todo)
    return {"ok": True, "id": rule_id, "blocked": blocked, **status({})}


api.GET_ROUTES["/api/junk"] = status
api.POST_ROUTES["/api/junk/rule"] = create
