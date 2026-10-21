"""Cleanup + restore end to end against fake Gmail / generic IMAP / Outlook servers and a temp database.

Run: python -m tests.test_cleanup
"""

import os
import re
import tempfile
from pathlib import Path

os.environ["FREEUPMAIL_HOME"] = tempfile.mkdtemp()  # before importing store: never touch real data

from freeupmail import cleanup, imapmail, outlook, store

GMAIL = "me@gmail.com"
YAHOO = "me@yahoo.com"
OUTLOOK = "me@outlook.com"


class FakeGmail:
    """Just enough IMAP for gmail.trash/restore: folders of {uid: X-GM-MSGID}."""

    capabilities = ("IMAP4REV1", "MOVE", "X-GM-EXT-1")

    def __init__(self, folders):
        self.folders = folders
        self.current = None
        self.next_uid = 1000

    def list(self):
        return "OK", [b'(\\HasNoChildren \\All) "/" "[Gmail]/All Mail"',
                      b'(\\HasNoChildren \\Trash) "/" "[Gmail]/Trash"', b'(\\HasNoChildren) "/" "INBOX"']

    def select(self, name, readonly=False):
        self.current = imapmail.plain(name)
        return "OK", [b"1"]

    def response(self, _):
        return "UIDVALIDITY", [None]

    def uid(self, cmd, *args):
        box = self.folders[self.current]
        if cmd == "SEARCH":
            return "OK", [" ".join(map(str, box)).encode()]
        if cmd == "FETCH":
            uids = [int(u) for u in args[0].split(",")]
            return "OK", [f"{n} (X-GM-MSGID {box[u]} UID {u})".encode() for n, u in enumerate(uids, 1) if u in box]
        if cmd == "MOVE":
            dest = self.folders[imapmail.plain(args[1])]
            for u in map(int, args[0].split(",")):
                self.next_uid += 1
                dest[self.next_uid] = box.pop(u)
            return "OK", [b"done"]
        raise AssertionError(cmd)

    def logout(self):
        pass


class FakeIMAP:
    """A plain IMAP server (no Gmail extensions, no MOVE): folders of {uid: Message-ID}."""

    capabilities = ("IMAP4REV1", "UIDPLUS")

    def __init__(self, folders):
        self.folders = {name: {"validity": n, "next": 100, "msgs": msgs} for n, (name, msgs) in enumerate(folders.items(), 7)}
        self.current = None
        self.flagged: set[int] = set()

    def list(self):
        return "OK", [b'(\\HasNoChildren) "." INBOX', b'(\\HasNoChildren) "." "Bulk Mail"',
                      b'(\\HasNoChildren) "." Trash', b'(\\HasNoChildren) "." Archive']

    def select(self, name, readonly=False):
        self.current = imapmail.plain(name)
        self._validity = self.folders[self.current]["validity"]
        return "OK", [b"1"]

    def response(self, _):
        return "UIDVALIDITY", [str(self._validity).encode()]

    def status(self, name, _):
        f = self.folders[name]
        return "OK", [f'{name} (UIDNEXT {f["next"]} UIDVALIDITY {f["validity"]})'.encode()]

    def uid(self, cmd, *args):
        box = self.folders[self.current]["msgs"]
        if cmd == "SEARCH":
            uids = sorted(box)
            if args[0] == "UID":
                start = int(args[1].split(":")[0])
                uids = [u for u in uids if u >= start] or uids[-1:]
            return "OK", [" ".join(map(str, uids)).encode()]
        if cmd == "FETCH":
            out = []
            for u in (int(x) for x in args[0].split(",")):
                if u in box:
                    out += [(f"1 (UID {u} FLAGS () BODY[HEADER.FIELDS (MESSAGE-ID)] {{40}}".encode(),
                             f"Message-ID: <{box[u]}>\r\n\r\n".encode()), b")"]
            return "OK", out
        if cmd == "COPY":
            dest = self.folders[imapmail.plain(args[1])]
            for u in map(int, args[0].split(",")):
                dest["msgs"][dest["next"]] = box[u]
                dest["next"] += 1
            return "OK", [b"done"]
        if cmd == "STORE":
            self.flagged |= set(map(int, args[0].split(",")))
            return "OK", [b""]
        if cmd == "EXPUNGE":
            for u in map(int, args[0].split(",")):
                assert u in self.flagged, "expunged a message that was not flagged"
                box.pop(u)
            return "OK", [b""]
        raise AssertionError(cmd)

    def logout(self):
        pass


def fake_graph(method, path, body=None):
    assert method == "POST" and path == "/$batch", path
    responses = []
    for r in body["requests"]:
        old = re.search(r"/me/messages/([^/]+)/move", r["url"])[1]
        responses.append({"id": r["id"], "status": 201, "body": {"id": f"new-{old}"}})
    return {"responses": responses}


def row(account, msg_id, uid, addr, subject, rfc_id=""):
    return {"account": account, "msg_id": msg_id, "uid": uid, "from_addr": addr, "from_name": "",
            "from_domain": addr.split("@")[1], "subject": subject, "date": "2025-01-01T00:00:00+00:00",
            "seen": 1, "list_unsub": 1, "list_id": "", "labels": "inbox", "unsub_url": "", "rfc_id": rfc_id}


def main():
    store.DB_PATH = Path(tempfile.mkdtemp()) / "test.db"
    gmail_box = FakeGmail({
        "[Gmail]/All Mail": {1: "111", 2: "222", 3: "333", 4: "999"},  # uid 4 changed since scan
        "[Gmail]/Trash": {}, "INBOX": {},
    })
    yahoo_box = FakeIMAP({
        "INBOX": {5: "a@shop", 6: "b@friend"},
        "Archive": {9: "c@shop"},
        "Trash": {}, "Bulk Mail": {},
    })
    imapmail.login = lambda account: gmail_box if account["email"] == GMAIL else yahoo_box
    imapmail.run_scan = lambda *a, **k: True
    outlook.connected = lambda: {"email": OUTLOOK}
    outlook.graph = fake_graph
    outlook.scan = lambda *a, **k: None
    store.load_config = lambda: {"imap": [{"email": GMAIL, "password": "x", "host": "imap.gmail.com"},
                                          {"email": YAHOO, "password": "x"}]}

    assert imapmail.mail_folders(yahoo_box) == ["INBOX", "Archive"], "Trash / Bulk Mail must not be scanned"
    inbox, archive = (f"imap:{yahoo_box.folders[f]['validity']}" for f in ("INBOX", "Archive"))

    db = store.connect()
    store.replace_messages(db, GMAIL, [
        row(GMAIL, "111", 1, "news@shop.com", "Big sale"),
        row(GMAIL, "222", 2, "news@shop.com", "Security alert"),     # protected kind
        row(GMAIL, "333", 3, "friend@gmail.com", "Lunch?"),         # sender kept
        row(GMAIL, "444", 4, "news@shop.com", "Another sale"),      # stale uid -> must not move
    ])
    store.replace_messages(db, YAHOO, [
        row(YAHOO, f"{inbox}:5:INBOX", 5, "news@shop.com", "Sale", "a@shop"),
        row(YAHOO, f"{inbox}:6:INBOX", 6, "friend@gmail.com", "Hi", "b@friend"),
        row(YAHOO, f"{archive}:9:Archive", 9, "news@shop.com", "Old sale", "c@shop"),
        row(YAHOO, "imap:1:7:Archive", 7, "news@shop.com", "Stale folder", "x@shop"),  # UIDVALIDITY changed
    ])
    store.replace_messages(db, OUTLOOK, [row(OUTLOOK, "o1", 0, "promo@deals.com", "50% off")])
    with db:
        for acc in (GMAIL, YAHOO):
            db.execute("INSERT INTO sender_actions VALUES (?, 'news@shop.com', 'DELETE')", (acc,))
            db.execute("INSERT INTO sender_actions VALUES (?, 'friend@gmail.com', 'KEEP')", (acc,))

    log = []
    cleanup.run_cleanup(None, log.append)
    assert sorted(gmail_box.folders["[Gmail]/Trash"].values()) == ["111"]
    assert sorted(gmail_box.folders["[Gmail]/All Mail"].values()) == ["222", "333", "999"]
    assert sorted(yahoo_box.folders["Trash"]["msgs"].values()) == ["a@shop", "c@shop"]
    assert list(yahoo_box.folders["INBOX"]["msgs"].values()) == ["b@friend"]
    assert not yahoo_box.folders["Archive"]["msgs"]
    trashed = dict(db.execute("SELECT msg_id, new_id FROM trashed"))
    assert trashed == {"111": "111", "o1": "new-o1", f"{inbox}:5:INBOX": "a@shop",
                       f"{archive}:9:Archive": "c@shop"}, trashed
    remaining = {m for (m,) in db.execute("SELECT msg_id FROM messages")}
    assert remaining == {"222", "333", "444", f"{inbox}:6:INBOX", "imap:1:7:Archive"}, remaining
    assert any("1 skipped" in line for line in log), log
    assert any("Archive changed since the last scan" in line for line in log), log

    cleanup.run_restore([[GMAIL, "111"], [OUTLOOK, "o1"], [YAHOO, f"{archive}:9:Archive"]], log.append)
    assert sorted(gmail_box.folders["INBOX"].values()) == ["111"]
    assert not gmail_box.folders["[Gmail]/Trash"]
    assert sorted(yahoo_box.folders["INBOX"]["msgs"].values()) == ["b@friend", "c@shop"]
    assert list(yahoo_box.folders["Trash"]["msgs"].values()) == ["a@shop"]
    pinned = set(db.execute("SELECT account, msg_id FROM pinned"))
    assert pinned == {(GMAIL, "111"), (OUTLOOK, "new-new-o1"), (YAHOO, f"{inbox}:100:INBOX")}, pinned
    assert db.execute("SELECT COUNT(*) FROM trashed WHERE restored = 0").fetchone()[0] == 1
    print("cleanup/restore: all checks passed")


if __name__ == "__main__":
    main()
