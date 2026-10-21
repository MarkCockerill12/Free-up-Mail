"""Cleanup, backups, restore and rules end to end against fake Gmail / plain IMAP / Outlook
servers and a temporary data folder. Run: python -m tests.test_cleanup
"""

import os
import tempfile
from pathlib import Path

os.environ["FREEUPMAIL_HOME"] = tempfile.mkdtemp()  # before importing store: never touch real data

from freeupmail import (
    automation,
    backup,
    cleanup,
    imapmail,
    outlook,
    perms,
    store,
)
from tests.fakes import FakeGmail, FakeGraph, FakeIMAP, raw_email

GMAIL, YAHOO, OUTLOOK = "me@gmail.com", "me@yahoo.com", "me@outlook.com"


def row(account, msg_id, uid, addr, subject, rfc_id="", labels="inbox"):
    return {"account": account, "msg_id": msg_id, "uid": uid, "from_addr": addr, "from_name": "",
            "from_domain": addr.split("@")[1], "subject": subject, "date": "2025-01-01T00:00:00+00:00",
            "seen": 1, "list_unsub": 1, "list_id": "", "labels": labels, "unsub_url": "", "rfc_id": rfc_id}


def setup():
    gm = FakeGmail({"[Gmail]/All Mail": {1: "111", 2: "222", 3: "333", 4: "999"}, "[Gmail]/Trash": {},
                    "[Gmail]/Spam": {}, "INBOX": {}})
    ya = FakeIMAP({"INBOX": {5: "a@shop", 6: "b@friend", 8: "r@shop"}, "Archive": {9: "c@shop"},
                   "Trash": {}, "Bulk Mail": {}})
    ms = FakeGraph({"o1": raw_email("o1@outlook"), "o2": raw_email("o2@outlook")})
    imapmail.login = lambda account: gm if account["email"] == GMAIL else ya
    imapmail.run_scan = lambda *a, **k: True
    outlook.connected = lambda: {"email": OUTLOOK}
    outlook.graph, outlook.graph_raw = ms.graph, ms.graph_raw
    outlook.scan = lambda *a, **k: None
    store.load_config = lambda: {"imap": [{"email": GMAIL, "password": "x", "host": "imap.gmail.com"},
                                          {"email": YAHOO, "password": "x"}]}
    store.DB_PATH = Path(tempfile.mkdtemp()) / "test.db"
    db = store.connect()
    inbox, archive = (f"imap:{ya.folders[f]['validity']}" for f in ("INBOX", "Archive"))
    store.replace_messages(db, GMAIL, [
        row(GMAIL, "111", 1, "news@shop.com", "Big sale"),
        row(GMAIL, "222", 2, "news@shop.com", "Security alert"),     # protected kind
        row(GMAIL, "333", 3, "friend@gmail.com", "Lunch?"),         # sender kept
        row(GMAIL, "444", 4, "news@shop.com", "Another sale"),      # stale uid -> must not move
    ])
    store.replace_messages(db, YAHOO, [
        row(YAHOO, f"{inbox}:5:INBOX", 5, "news@shop.com", "Sale", "a@shop"),
        row(YAHOO, f"{inbox}:6:INBOX", 6, "friend@gmail.com", "Hi", "b@friend"),
        row(YAHOO, f"{inbox}:8:INBOX", 8, "shop@receipts.com", "Your receipt", "r@shop"),
        row(YAHOO, f"{archive}:9:Archive", 9, "news@shop.com", "Old sale", "c@shop"),
        row(YAHOO, "imap:1:7:Archive", 7, "news@shop.com", "Stale folder", "x@shop"),  # UIDVALIDITY changed
    ])
    store.replace_messages(db, OUTLOOK, [row(OUTLOOK, "o1", 0, "promo@deals.com", "50% off"),
                                         row(OUTLOOK, "o2", 0, "shop@receipts.com", "Your receipt")])
    store.replace_contacts(db, GMAIL, {"friend@gmail.com": 2})
    with db:
        for acc in (GMAIL, YAHOO):
            db.execute("INSERT INTO sender_actions VALUES (?, 'news@shop.com', 'DELETE')", (acc,))
        db.execute("INSERT INTO sender_actions VALUES (?, 'promo@deals.com', 'DELETE')", (OUTLOOK,))
    return db, gm, ya, ms, inbox, archive


def test_permissions_block_everything(db, gm, ya, ms):
    log = []
    cleanup.run_cleanup(None, log.append)
    assert len(gm.folders["[Gmail]/All Mail"]) == 4 and not ya.contents("Trash") and not ms.folders["deleteditems"]
    assert sum("does not have permission" in line for line in log) == 3, log
    for acc in (GMAIL, YAHOO, OUTLOOK):
        perms.set_grants(db, acc, ["scan", "trash"])  # trash but not read: backups can't be taken
    log.clear()
    cleanup.run_cleanup(None, log.append)
    assert not ya.contents("Trash") and not db.execute("SELECT COUNT(*) FROM trashed").fetchone()[0], log


def test_cleanup_with_backups(db, gm, ya, ms, inbox, archive):
    for acc in (GMAIL, YAHOO, OUTLOOK):
        perms.set_grants(db, acc, ["scan", "read", "trash", "organise"])
    log = []
    cleanup.run_cleanup(None, log.append)
    assert [m["gm"] for m in gm.folders["[Gmail]/Trash"].values()] == ["111"]
    assert sorted(m["gm"] for m in gm.folders["[Gmail]/All Mail"].values()) == ["222", "333", "999"]
    assert ya.contents("Trash") == ["a@shop", "c@shop"]
    assert ya.contents("INBOX") == ["b@friend", "r@shop"] and not ya.contents("Archive")
    assert list(ms.folders["deleteditems"]) == ["new-o1"]
    trashed = dict(db.execute("SELECT msg_id, new_id FROM trashed"))
    assert trashed == {"111": "111", "o1": "new-o1", f"{inbox}:5:INBOX": "a@shop", f"{archive}:9:Archive": "c@shop"}
    copies = dict(db.execute("SELECT msg_id, path FROM backups"))
    assert set(copies) == set(trashed), copies
    assert all(Path(p).read_bytes().startswith(b"Message-ID:") for p in copies.values())
    assert backup.load(db, OUTLOOK, "o1")[0] == raw_email("o1@outlook")
    assert any("1 skipped" in line for line in log) and any("Archive changed since the last scan" in line for line in log)


def test_restore(db, gm, ya, ms, inbox, archive):
    ya.folders["Trash"]["msgs"].clear()  # the user emptied Yahoo's Trash: only the backups are left
    log = []
    cleanup.run_restore([[GMAIL, "111"], [OUTLOOK, "o1"], [YAHOO, f"{inbox}:5:INBOX"], [YAHOO, f"{archive}:9:Archive"]],
                        log.append)
    assert [m["gm"] for m in gm.folders["INBOX"].values()] == ["111"]
    assert "new-new-o1" in ms.folders["inbox"]
    assert sorted(ya.appended) == ["a@shop", "c@shop"], log
    assert db.execute("SELECT COUNT(*) FROM trashed WHERE restored = 0").fetchone()[0] == 0
    rescued = set(db.execute("SELECT account, rfc_id FROM rescued"))
    assert {(YAHOO, "a@shop"), (YAHOO, "c@shop")} <= rescued, rescued


def test_rules(db, gm, ya, ms, inbox):
    receipts = automation.save(db, {"name": "Receipts", "conditions": [{"field": "subject", "op": "contains", "value": "receipt"}],
                                    "action": "move", "folder": "Receipts", "mark_read": True})
    bin_friend = automation.save(db, {"name": "Bin friend", "conditions": [{"field": "from", "op": "contains", "value": "friend"}],
                                      "action": "trash"})
    perms.set_grants(db, OUTLOOK, ["scan", "read", "trash"])  # no "organise" for Outlook
    log = []
    cleanup.run_rules([receipts, bin_friend], log.append)
    assert ya.contents("Receipts") == ["r@shop"] and all("\\Seen" in m["flags"] for m in ya.folders["Receipts"]["msgs"].values())
    assert "new-o2" not in ms.folders.get("Receipts", {}) and any("does not have permission to move" in line for line in log), log
    # friend@gmail.com is someone you've emailed, so a trash rule must never bin them.
    assert "b@friend" in ya.contents("INBOX") and all(m["gm"] != "333" for m in gm.folders["[Gmail]/Trash"].values())


def test_backup_retention(db):
    keep_days = store.get_settings(db, {"backup_days": "0"})["backup_days"]
    assert keep_days == "0" and backup.prune_by_setting(db) == 0, "default keeps every backup"
    old_path = db.execute("SELECT path FROM backups WHERE msg_id = 'o1'").fetchone()[0]
    with db:
        db.execute("UPDATE backups SET saved_at = '2020-01-01T00:00:00+00:00' WHERE msg_id = 'o1'")
        db.execute("INSERT OR REPLACE INTO settings VALUES ('backup_days', '30')")
    before = db.execute("SELECT COUNT(*) FROM backups").fetchone()[0]
    assert backup.prune_by_setting(db) == 1 and not Path(old_path).exists()
    assert db.execute("SELECT COUNT(*) FROM backups").fetchone()[0] == before - 1


def main():
    db, gm, ya, ms, inbox, archive = setup()
    test_permissions_block_everything(db, gm, ya, ms)
    test_cleanup_with_backups(db, gm, ya, ms, inbox, archive)
    test_restore(db, gm, ya, ms, inbox, archive)
    test_rules(db, gm, ya, ms, inbox)
    test_backup_retention(db)
    print("cleanup/backups/restore/rules: all checks passed")


if __name__ == "__main__":
    main()
