"""Rule validation, matching and translation to Gmail / Outlook filters.
Run: python -m tests.test_automation
"""

from datetime import UTC, datetime

from freeupmail import automation, filters

NOW = datetime(2026, 10, 21, tzinfo=UTC)
ROW = {"account": "me@x.com", "from_addr": "deals@shop.example", "from_name": "Shop", "from_domain": "shop.example",
       "subject": "Your receipt #42", "date": "2026-10-01T09:00:00+00:00", "seen": 0, "list_unsub": 1,
       "labels": {"inbox", "promotions"}, "kind": "receipt"}


def rule(*conditions, match="all", action="move", **extra):
    return automation.validate({"name": "r", "match": match, "action": action, "folder": "Receipts",
                                "conditions": [dict(zip(("field", "op", "value"), c, strict=True)) for c in conditions],
                                **extra})


def check(name, got, want):
    assert got == want, f"{name}: got {got!r}, want {want!r}"


def main():
    for bad in ({"name": "", "conditions": [], "action": "move"},
                {"name": "x", "conditions": [], "action": "trash"},  # would match everything
                {"name": "x", "conditions": [{"field": "subject", "op": "contains", "value": ""}], "action": "read"},
                {"name": "x", "conditions": [{"field": "age", "op": "older", "value": "soon"}], "action": "read"},
                {"name": "x", "conditions": [{"field": "subject", "op": "contains", "value": "a"}], "action": "move"},
                {"name": "x", "conditions": [{"field": "subject", "op": "contains", "value": "a"}], "action": "explode"}):
        try:
            automation.validate(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad}")

    cases = [
        (rule(("subject", "contains", "RECEIPT")), True),
        (rule(("subject", "not_contains", "receipt")), False),
        (rule(("from", "is", "deals@shop.example")), True),
        (rule(("from", "contains", "shop")), True),
        (rule(("domain", "ends", ".example")), True),
        (rule(("category", "is", "promotions")), True),
        (rule(("category", "is_not", "promotions")), False),
        (rule(("kind", "is", "receipt")), True),
        (rule(("list", "yes", "")), True),
        (rule(("unread", "yes", "")), True),
        (rule(("starred", "yes", "")), False),
        (rule(("age", "older", "30")), False),
        (rule(("age", "newer", "30")), True),
        (rule(("subject", "contains", "receipt"), ("starred", "yes", "")), False),
        (rule(("subject", "contains", "receipt"), ("starred", "yes", ""), match="any"), True),
        (rule(("subject", "contains", "receipt"), accounts=["other@x.com"]), False),
    ]
    for n, (r, want) in enumerate(cases):
        check(f"match {n} {automation.describe(r)}", automation.matches(r, ROW, NOW), want)

    check("gmail move", filters.gmail_filter(rule(("subject", "contains", "receipt"), ("domain", "is", "shop.example"))),
          [("hasTheWord", "subject:(receipt) from:(@shop.example)"), ("label", "Receipts"), ("shouldArchive", "true")])
    check("gmail any", filters.gmail_filter(rule(("from", "contains", "a"), ("from", "contains", "b"), match="any",
                                                 action="trash"))[0], ("hasTheWord", "{from:(a) from:(b)}"))
    check("gmail junk", filters.gmail_filter(rule(("from", "contains", "a"), action="junk")), None)
    check("gmail age", filters.gmail_filter(rule(("age", "older", "3"), action="read")), None)
    check("outlook move", filters.outlook_rule(rule(("from", "contains", "shop"), ("subject", "not_contains", "invoice"))),
          {"name": "r", "conditions": {"senderContains": ["shop"]}, "exceptions": {"subjectContains": ["invoice"]},
           "actions": {}, "folder": "Receipts"})
    check("outlook any", filters.outlook_rule(rule(("from", "contains", "a"), ("from", "contains", "b"), match="any",
                                                   action="junk"))["conditions"], {"senderContains": ["a", "b"]})
    check("outlook all same field", filters.outlook_rule(rule(("from", "contains", "a"), ("from", "contains", "b"))), None)
    check("outlook star", filters.outlook_rule(rule(("from", "contains", "a"), action="star")), None)
    print("rules engine: all checks passed")


if __name__ == "__main__":
    main()
