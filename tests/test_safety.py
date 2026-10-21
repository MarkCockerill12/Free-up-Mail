"""Failure cases: changed emails, dropped connections, uploads that fail part-way, emails
already restored by hand, and rules that must not bin protected mail.
Run: python -m tests.test_safety
"""

import os
import tempfile
from pathlib import Path

os.environ["FREEUPMAIL_HOME"] = tempfile.mkdtemp()  # before importing store: never touch real data

from freeupmail import automation, cleanup, imapmail, perms, store, views
from tests.fakes import FakeIMAP

YAHOO = "me@yahoo.com"


def row(msg_id, uid, addr, subject, rfc_id, labels="inbox"):
    return {"account": YAHOO, "msg_id": msg_id, "uid": uid, "from_addr": addr, "from_name": "",
            "from_domain": addr.split("@")[1], "subject": subject, "date": "2025-01-01T00:00:00+00:00",
            "seen": 1, "list_unsub": 1, "list_id": "", "labels": labels, "unsub_url": "", "rfc_id": rfc_id}


def setup(folders):
    box = FakeIMAP(folders)
    imapmail.login = lambda account: box
    imapmail.run_scan = lambda *a, **k: True
    store.load_config = lambda: {"imap": [{"email": YAHOO, "password": "x"}]}
    store.DB_PATH = Path(tempfile.mkdtemp()) / "test.db"
    db = store.connect()
    perms.set_grants(db, YAHOO, ["scan", "read", "trash", "organise"])
    ids = {f: f"imap:{box.folders[f]['validity']}" for f in box.folders}
    return db, box, ids


def test_changed_and_dropped():
    db, box, ids = setup({"INBOX": {5: "a@shop", 6: "now-something-else"}, "Archive": {9: "c@shop"}, "Trash": {}})
    store.replace_messages(db, YAHOO, [
        row(f"{ids['INBOX']}:5:INBOX", 5, "news@shop.com", "Sale", "a@shop"),
        row(f"{ids['INBOX']}:6:INBOX", 6, "news@shop.com", "Sale 2", "b@shop"),  # uid 6 now holds another email
        row(f"{ids['Archive']}:9:Archive", 9, "news@shop.com", "Old sale", "c@shop"),
    ])
    with db:
        db.execute("INSERT INTO sender_actions VALUES (?, 'news@shop.com', 'DELETE')", (YAHOO,))
    box.fail_after_moves = 1  # the connection drops after the first folder's move
    log = []
    cleanup.run_cleanup(None, log.append)
    assert box.contents("Trash") == ["a@shop"], box.contents("Trash")
    assert "now-something-else" in box.contents("INBOX"), "a changed email must never be moved"
    assert any("changed since the last scan" in line for line in log), log
    assert any("stopped early" in line for line in log), log
    trashed = {m for (m,) in db.execute("SELECT msg_id FROM trashed")}
    assert trashed == {f"{ids['INBOX']}:5:INBOX"}, f"what moved before the drop must be recorded: {trashed}"


def test_restore_edge_cases():
    db, box, ids = setup({"INBOX": {1: "a@shop", 2: "b@shop", 3: "c@shop"}, "Trash": {}})
    store.replace_messages(db, YAHOO, [row(f"{ids['INBOX']}:{u}:INBOX", u, "news@shop.com", "Sale", r)
                                       for u, r in ((1, "a@shop"), (2, "b@shop"), (3, "c@shop"))])
    with db:
        db.execute("INSERT INTO sender_actions VALUES (?, 'news@shop.com', 'DELETE')", (YAHOO,))
    cleanup.run_cleanup(None, lambda _: None)
    assert box.contents("Trash") == ["a@shop", "b@shop", "c@shop"]
    # Trash emptied; a@shop was already put back by hand; the server refuses c@shop.
    box.folders["Trash"]["msgs"].clear()
    box.append("INBOX", None, None, b"Message-ID: <a@shop>\r\nSubject: x\r\n\r\nx")
    box.appended.clear()
    box.refuse.add("c@shop")
    log = []
    cleanup.run_restore([[YAHOO, f"{ids['INBOX']}:{u}:INBOX"] for u in (1, 2, 3)], log.append)
    assert box.appended == ["b@shop"], f"only b@shop needed uploading: {box.appended}"
    left = {m for (m,) in db.execute("SELECT msg_id FROM trashed WHERE restored = 0")}
    assert left == {f"{ids['INBOX']}:3:INBOX"}, f"the failed upload must stay restorable: {left}"
    assert any("already back in the mailbox" in line for line in log), log


def test_rules_never_bin_protected():
    db, _box, ids = setup({"INBOX": {1: "a"}, "Lists": {2: "b"}, "Trash": {}, "Junk": {}})
    inbox, lists = ids["INBOX"], ids["Lists"]
    store.replace_messages(db, YAHOO, [
        row(f"{inbox}:1:INBOX", 1, "shop@deals.com", "Your receipt", "a"),           # receipt: protected
        row(f"{inbox}:3:INBOX", 3, "friend@mail.com", "Deals tonight?", "f"),       # a contact
        row(f"{inbox}:4:INBOX", 4, "shop@deals.com", "Deals: 50% off", "d"),        # fair game
        row(f"{lists}:2:Lists", 2, "shop@deals.com", "Deals digest", "b", labels=""),  # not in the inbox
    ])
    store.replace_contacts(db, YAHOO, {"friend@mail.com": 1})
    junk = automation.validate({"name": "j", "conditions": [{"field": "subject", "op": "contains", "value": "deal"}],
                                "action": "junk"})
    junk_plan = set(views.rule_plan(db, junk).get(YAHOO, {}))
    assert junk_plan == {f"{inbox}:4:INBOX", f"{lists}:2:Lists"}, junk_plan
    receipts = automation.validate({"name": "r", "conditions": [{"field": "from", "op": "contains", "value": "deals"}],
                                    "action": "junk"})
    assert f"{inbox}:1:INBOX" not in views.rule_plan(db, receipts).get(YAHOO, {}), "receipts must not be junked"
    archive = automation.validate({"name": "a", "conditions": [{"field": "from", "op": "contains", "value": "deals"}],
                                   "action": "archive"})
    assert f"{lists}:2:Lists" not in views.rule_plan(db, archive).get(YAHOO, {}), "archive only takes mail out of the inbox"


def main():
    test_changed_and_dropped()
    test_restore_edge_cases()
    test_rules_never_bin_protected()
    print("safety: all checks passed")


if __name__ == "__main__":
    main()
