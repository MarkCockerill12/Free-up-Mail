"""Junk / Spam folders are scanned on every account type, scans say when they are incomplete,
failed scans are recorded, and the junk-senders rule. Run: python -m tests.test_junk
"""

import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

os.environ["FREEUPMAIL_HOME"] = tempfile.mkdtemp()  # before importing store: never touch real data

from freeupmail import automation, gmail, imapmail, jobs, junk, outlook, store, views
from tests.fakes import FakeGmail, FakeIMAP

ME, GM, OL = "me@yahoo.com", "me@gmail.com", "me@outlook.com"


def fresh_db():
    store.DB_PATH = Path(tempfile.mkdtemp()) / "test.db"
    return store.connect()


def labels_of(db, account):
    return {m: set(filter(None, lab.split(","))) for m, lab in db.execute(
        "SELECT msg_id, labels FROM messages WHERE account = ?", (account,))}


def test_imap_scan_reads_junk():
    db = fresh_db()
    srv = FakeIMAP({"INBOX": {1: "a@x", 2: "b@x"}, "Bulk Mail": {3: "j@x"}, "Trash": {9: "t@x"}, "Sent": {}})
    assert [(f, j) for f, j in imapmail.scan_folders(srv)] == [("INBOX", False), ("Bulk Mail", True)]
    assert imapmail.scan_messages(srv, db, ME, lambda _: None) == 0
    found = labels_of(db, ME)
    assert len(found) == 3 and sorted(map(sorted, found.values())) == [["inbox"], ["inbox"], ["junk"]], found


def test_fetch_complete_asks_again():
    srv = FakeIMAP({"INBOX": {1: "a@x", 2: "b@x", 3: "c@x"}})
    srv.select("INBOX")
    real, calls = srv.uid, []

    def flaky(cmd, *args):
        calls.append(args)
        reply = real(cmd, *args)
        if cmd == "FETCH" and len(calls) == 1:  # the first answer drops uid 2
            return reply[0], [x for x in reply[1] if b"UID 2" not in (x[0] if isinstance(x, tuple) else x)]
        return reply

    srv.uid = flaky
    log = []
    got, missing = imapmail.fetch_complete(srv, [b"1", b"2", b"3"], "MESSAGE-ID", log.append)
    assert missing == 0 and len(got) == 3 and any("asking again" in line for line in log), log

    srv.uid = lambda cmd, *a: ("OK", [])  # the server never answers
    got, missing = imapmail.fetch_complete(srv, [b"1", b"2"], "MESSAGE-ID", lambda _: None)
    assert missing == 2 and not got


def test_gmail_scan_and_trash_from_spam():
    db = fresh_db()
    srv = FakeGmail({"[Gmail]/All Mail": {1: "111"}, "[Gmail]/Spam": {5: "555", 6: "666"}, "[Gmail]/Trash": {}, "INBOX": {}})
    assert gmail.scan_messages(srv, db, GM, lambda _: None) == 0
    found = labels_of(db, GM)
    assert set(found) == {"111", "555", "666"} and "junk" not in found["111"] and found["555"] == {"junk"}, found

    imapmail.login = lambda account: srv
    moved = gmail.trash({"email": GM}, {"111": 1, "555": 5}, lambda _: None, None, None, {"555"})
    assert moved == {"111": "111", "555": "555"}, moved
    assert sorted(m["gm"] for m in srv.folders["[Gmail]/Trash"].values()) == ["111", "555"]
    assert [m["gm"] for m in srv.folders["[Gmail]/Spam"].values()] == ["666"] and not srv.folders["[Gmail]/All Mail"]
    # A Spam uid that is not the cached one is left alone (same check as All Mail).
    assert gmail.trash({"email": GM}, {"666": 99}, lambda _: None, None, None, {"666"}) == {}


class Graph:
    """Just enough of Microsoft Graph for outlook.scan: a folder tree and the messages in it."""

    def __init__(self, messages, reported=None):
        self.tree = {  # id -> (name, parent)
            "inbox": ("Inbox", ""), "junkemail": ("Junk Email", ""), "deleteditems": ("Deleted Items", ""),
            "sentitems": ("Sent Items", ""), "drafts": ("Drafts", ""), "outbox": ("Outbox", ""),
            "archive": ("Archive", ""), "bin-sub": ("Old bin", "deleteditems"), "junk-sub": ("Spam 2", "junkemail"),
        }
        self.messages = messages  # [(id, folder id)]
        self.reported = reported or {}

    def total(self, fid):
        return self.reported.get(fid, sum(1 for _, f in self.messages if f == fid))

    def graph(self, method, path, body=None):
        if path == "/$batch":
            return {"responses": [{"id": r["id"], "status": 200, "body": {}} for r in body["requests"]]}
        if (m := re.fullmatch(r"/me/mailFolders/([a-z]+)\?\$select=id", path)):
            return {"id": m[1]}
        if path.startswith("/me/mailFolders?") or "/childFolders?" in path:
            parent = "" if path.startswith("/me/mailFolders?") else re.search(r"mailFolders/([^/]+)/childFolders", path)[1]
            kids = [f for f, (_, p) in self.tree.items() if p == parent]
            return {"value": [{"id": f, "displayName": self.tree[f][0], "totalItemCount": self.total(f),
                               "childFolderCount": int(any(p == f for _, p in self.tree.values()))} for f in kids]}
        if path.startswith("/me/mailFolders/sentitems/messages"):
            return {"value": []}
        if path.startswith("/me/messages?"):
            return {"value": [{"id": mid, "parentFolderId": f, "subject": "s", "isRead": True,
                               "from": {"emailAddress": {"address": f"{mid}@x.com", "name": ""}},
                               "receivedDateTime": "2026-01-01T00:00:00Z", "internetMessageId": f"<{mid}>"}
                              for mid, f in self.messages]}
        raise AssertionError((method, path))


def test_outlook_scan_reads_junk_and_checks_counts():
    db = fresh_db()
    outlook.current = lambda: OL
    messages = [("m1", "inbox"), ("m2", "archive"), ("j1", "junkemail"), ("j2", "junk-sub"),
                ("d1", "deleteditems"), ("d2", "bin-sub")]
    fake = Graph(messages)
    outlook.graph = fake.graph
    log = []
    outlook.scan(db, log.append)
    found = labels_of(db, OL)
    assert set(found) == {"m1", "m2", "j1", "j2"}, found  # Deleted Items and its subfolders are skipped
    assert found["j1"] == found["j2"] == {"junk"} and found["m1"] == {"inbox"} and found["m2"] == set(), found
    assert db.execute("SELECT status FROM scans WHERE account = ?", (OL,)).fetchone()[0] == "ok", log

    # Outlook says there are 40 emails in the Inbox but the list only returned one: say so.
    outlook.graph = Graph(messages, {"inbox": 40}).graph
    log.clear()
    outlook.scan(db, log.append)
    status = db.execute("SELECT status FROM scans WHERE account = ?", (OL,)).fetchone()[0]
    assert status.startswith("incomplete:") and any("WARNING" in line for line in log), (status, log)


def test_failed_scans_are_recorded():
    db = fresh_db()
    store.record_scan(db, GM, "2026-11-04T11:00:00+00:00", "ok")
    store.record_scan_error(db, GM, "Outlook sign-in expired,\nreconnect")
    when, status = db.execute("SELECT scanned_at, status FROM scans WHERE account = ?", (GM,)).fetchone()
    assert when == "2026-11-04T11:00:00+00:00" and status == "error: Outlook sign-in expired, reconnect"
    store.record_scan_error(db, OL, "boom")  # never scanned before
    assert db.execute("SELECT scanned_at, status FROM scans WHERE account = ?", (OL,)).fetchone() == ("", "error: boom")


def test_automatic_scan_timing():
    db = fresh_db()
    assert jobs._due(db, GM, 15), "never scanned"
    store.record_scan(db, GM, "2020-01-01T00:00:00+00:00", "ok")
    assert jobs._due(db, GM, 15), "old"
    store.record_scan(db, GM, datetime.now(UTC).isoformat(timespec="seconds"), "ok")
    assert not jobs._due(db, GM, 15), "just scanned"
    store.record_scan(db, OL, "2020-01-01T00:00:00+00:00", "login failed")
    assert not jobs._due(db, OL, 15), "a wrong password is not retried automatically"
    store.record_scan_error(db, OL, "no connection")
    assert jobs._due(db, OL, 15), "a network error is"


def mail(account, msg_id, addr, subject, labels):
    return {"account": account, "msg_id": msg_id, "uid": 1, "from_addr": addr, "from_name": "", "from_domain": addr.split("@")[1],
            "subject": subject, "date": "2026-01-01T00:00:00+00:00", "seen": 1, "list_unsub": 0, "list_id": "",
            "labels": labels, "unsub_url": "", "rfc_id": msg_id}


def test_junk_rule_and_suggestions():
    db = fresh_db()
    store.replace_messages(db, GM, [
        mail(GM, "1", "scam@bad.example", "Verify your account password", "junk"),    # security-looking spam
        mail(GM, "2", "scam@bad.example", "Hello friend", "inbox"),                  # same sender, got into the inbox
        mail(GM, "3", "friend@pal.example", "Lunch?", "junk"),                       # a contact the filter caught
        mail(GM, "4", "mixed@shop.example", "Your receipt", "inbox"), mail(GM, "5", "mixed@shop.example", "Sale", "inbox"),
        mail(GM, "6", "mixed@shop.example", "Another", "junk"),                      # mostly legitimate
        mail(GM, "7", "other@x.example", "Hi", "inbox"),
    ])
    store.replace_contacts(db, GM, {"friend@pal.example": 3})
    senders = {s["from_addr"]: s for s in views.senders(db)}
    assert senders["scam@bad.example"]["action"] == "DELETE" and "Junk" in senders["scam@bad.example"]["reason"]
    assert senders["friend@pal.example"]["action"] == "KEEP"          # contacts stay safe
    assert senders["mixed@shop.example"]["action"] == "REVIEW"         # mostly legitimate: not judged by one junk mail
    plan = views.cleanup_plan(db)[GM]
    assert "1" in plan, "the Junk folder's 'verify password' mail is not protected as a security mail"
    assert "2" in plan and "3" not in plan and "7" not in plan

    status = junk.status({})
    # Junk senders: scam + mixed (the contact is left out). The rule bins 1, 2, 5 and 6; "Your receipt" (4) is protected.
    assert status["senders"] == 2 and status["would_trash"] == 4 and status["rule"] is None, status
    result = junk.create({"block": True})
    assert result["rule"] == result["id"] and result["blocked"] == 2, result
    actions = {(a, f): act for a, f, act in db.execute("SELECT * FROM sender_actions")}
    assert actions[(GM, "scam@bad.example")] == "DELETE" and (GM, "friend@pal.example") not in actions
    # Running it again changes nothing and never overrides a choice of yours.
    with db:
        db.execute("INSERT OR REPLACE INTO sender_actions VALUES (?, 'mixed@shop.example', 'KEEP')", (GM,))
    again = junk.create({"block": True})
    assert again["id"] == result["id"] and len([r for r in automation.load(db) if r["name"] == junk.RULE_NAME]) == 1
    assert {(a, f): act for a, f, act in db.execute("SELECT * FROM sender_actions")}[(GM, "mixed@shop.example")] == "KEEP"

    # Other rules never file or flag mail that is already in Junk.
    archive = automation.validate({"name": "a", "conditions": [{"field": "from", "op": "contains", "value": "scam"}],
                                   "action": "archive"})
    assert set(views.rule_plan(db, archive)[GM]) == {"2"}
    read = automation.validate({"name": "r", "conditions": [{"field": "junk", "op": "yes"}], "action": "read"})
    assert not views.rule_plan(db, read)


def main():
    test_imap_scan_reads_junk()
    test_fetch_complete_asks_again()
    test_gmail_scan_and_trash_from_spam()
    test_outlook_scan_reads_junk_and_checks_counts()
    test_failed_scans_are_recorded()
    test_automatic_scan_timing()
    test_junk_rule_and_suggestions()
    print("junk folders / complete scans: all checks passed")


if __name__ == "__main__":
    main()
