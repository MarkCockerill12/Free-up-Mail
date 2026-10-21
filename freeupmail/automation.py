"""Your own rules: "emails matching these conditions get this action", e.g. move every
receipt to a Receipts folder, send a sender straight to Junk, or always keep anything
from your bank. This module is only the rule model (validation and matching); every
change to a mailbox goes through cleanup.run_rules, which checks permissions first.

A rule (stored as JSON):
  {"name": "Receipts", "enabled": true, "accounts": [] (= all), "match": "all" | "any",
   "conditions": [{"field": "subject", "op": "contains", "value": "receipt"}],
   "action": "move", "folder": "Receipts", "mark_read": false, "auto": false}
"""

import json
from datetime import UTC, datetime, timedelta

FIELDS = {
    "from": "text", "domain": "text", "subject": "text",
    "category": "choice", "kind": "choice",
    "list": "flag", "unread": "flag", "starred": "flag", "inbox": "flag",
    "age": "days",
}
OPS = {
    "text": ("contains", "not_contains", "is", "starts", "ends"),
    "choice": ("is", "is_not"),
    "flag": ("yes", "no"),
    "days": ("older", "newer"),
}
CHOICES = {
    "category": ("promotions", "social", "updates", "forums", "other"),
    "kind": ("security", "receipt", "shipping"),
}
ACTIONS = {
    "move": "Move to a folder", "archive": "Archive (out of the inbox)", "junk": "Move to Junk / Spam",
    "trash": "Move to Trash", "read": "Mark as read", "star": "Flag / star", "keep": "Always keep (never delete)",
}
# Which permission each action needs (perms.py).
ACTION_PERM = {"trash": "trash", "keep": None, **dict.fromkeys(("move", "archive", "junk", "read", "star"), "organise")}


def validate(rule: dict) -> dict:
    """A clean copy of `rule`, or ValueError with a message for the GUI."""
    name = str(rule.get("name") or "").strip()
    if not name:
        raise ValueError("Give the rule a name.")
    conditions = []
    for c in rule.get("conditions") or []:
        field, op, value = c.get("field"), c.get("op"), str(c.get("value") or "").strip()
        kind = FIELDS.get(field)
        if not kind or op not in OPS[kind]:
            raise ValueError(f"Unknown condition {field} {op}.")
        if kind == "text" and not value:
            raise ValueError(f"Fill in the text for the '{field}' condition.")
        if kind == "choice" and value not in CHOICES[field]:
            raise ValueError(f"Pick a {field}.")
        if kind == "days" and (not value.isdigit() or int(value) < 0):
            raise ValueError("Age needs a number of days.")
        conditions.append({"field": field, "op": op, "value": value})
    if not conditions:
        raise ValueError("Add at least one condition, so the rule can't match every email.")
    action = rule.get("action")
    if action not in ACTIONS:
        raise ValueError("Pick what the rule does.")
    folder = str(rule.get("folder") or "").strip()
    if action == "move" and not folder:
        raise ValueError("Type the folder (or Gmail label) to move emails to.")
    return {
        "name": name, "enabled": bool(rule.get("enabled", True)),
        "accounts": [str(a) for a in rule.get("accounts") or []],
        "match": "any" if rule.get("match") == "any" else "all",
        "conditions": conditions, "action": action, "folder": folder if action == "move" else "",
        "mark_read": bool(rule.get("mark_read")) and action in ("move", "archive", "junk"),
        "auto": bool(rule.get("auto")) and action != "keep",
    }


# ---------- storage ----------

def load(db) -> list[dict]:
    return [{**json.loads(data), "id": rid} for rid, data in db.execute("SELECT id, data FROM user_rules ORDER BY position, id")]


def save(db, rule: dict) -> int:
    clean = validate(rule)
    with db:
        if rule.get("id"):
            db.execute("UPDATE user_rules SET data = ? WHERE id = ?", (json.dumps(clean), int(rule["id"])))
            return int(rule["id"])
        return db.execute("INSERT INTO user_rules (data) VALUES (?)", (json.dumps(clean),)).lastrowid


def delete(db, rule_id: int) -> None:
    with db:
        db.execute("DELETE FROM user_rules WHERE id = ?", (int(rule_id),))


# ---------- matching ----------

def _text(field: str, row: dict) -> str:
    if field == "from":
        return f"{row['from_addr']} {row['from_name']}".lower()
    return str(row["from_domain" if field == "domain" else "subject"] or "").lower()


def _flag(field: str, row: dict) -> bool:
    if field == "list":
        return bool(row["list_unsub"])
    if field == "unread":
        return not row["seen"]
    return field in row["labels"]  # starred / inbox


def _age_days(date: str, now: datetime) -> float | None:
    try:
        when = datetime.fromisoformat(date)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return (now - when) / timedelta(days=1)


def condition_matches(c: dict, row: dict, now: datetime) -> bool:
    field, op, value = c["field"], c["op"], c["value"]
    kind = FIELDS[field]
    if kind == "text":
        text, value = _text(field, row), value.lower()
        if field == "from" and op == "is":  # exact address, not "name + address"
            return row["from_addr"] == value
        return {
            "contains": value in text, "not_contains": value not in text, "is": text == value,
            "starts": text.startswith(value), "ends": text.endswith(value),
        }[op]
    if kind == "choice":
        has = value in row["labels"] if field == "category" else row["kind"] == value
        return has if op == "is" else not has
    if kind == "flag":
        return _flag(field, row) == (op == "yes")
    age = _age_days(row["date"], now)
    if age is None:
        return False
    return age > int(value) if op == "older" else age <= int(value)


def matches(rule: dict, row: dict, now: datetime | None = None) -> bool:
    if rule["accounts"] and row["account"] not in rule["accounts"]:
        return False
    now = now or datetime.now(UTC)
    results = (condition_matches(c, row, now) for c in rule["conditions"])
    return any(results) if rule["match"] == "any" else all(results)


def describe(rule: dict) -> str:
    """One line for the rules list, e.g. 'subject contains "receipt" → Move to a folder: Receipts'."""
    words = {"contains": "contains", "not_contains": "doesn't contain", "is": "is", "starts": "starts with",
             "ends": "ends with", "is_not": "is not", "yes": "is", "no": "is not", "older": "older than",
             "newer": "newer than"}
    parts = []
    for c in rule["conditions"]:
        if FIELDS[c["field"]] == "flag":
            parts.append(f"{c['field']} {'yes' if c['op'] == 'yes' else 'no'}")
        else:
            suffix = " days" if c["field"] == "age" else ""
            parts.append(f'{c["field"]} {words[c["op"]]} "{c["value"]}"{suffix}')
    joiner = " or " if rule["match"] == "any" else " and "
    target = f": {rule['folder']}" if rule["action"] == "move" else ""
    return f"{joiner.join(parts)} → {ACTIONS[rule['action']]}{target}"
