"""Big cleanups: Gmail on several connections (one of which fails to sign in), Outlook moving
each chunk while the next is backed up (with a connection that drops part-way), and two
Outlook accounts side by side.
Run: python -m tests.test_parallel
"""

import os
import tempfile
import threading
from pathlib import Path

os.environ["FREEUPMAIL_HOME"] = tempfile.mkdtemp()  # before importing store: never touch real data

from freeupmail import (
    cleanup,
    gmail,
    imapmail,
    outlook,
    outlook_write,
    perms,
    providers,
    store,
)
from tests.fakes import FakeGmail, FakeGraph, raw_email

GMAIL, OUT1, OUT2 = "me@gmail.com", "one@outlook.com", "two@outlook.com"


def row(account, msg_id, uid):
    return {"account": account, "msg_id": msg_id, "uid": uid, "from_addr": "news@shop.com", "from_name": "",
            "from_domain": "shop.com", "subject": "Sale", "date": "2025-01-01T00:00:00+00:00", "seen": 1,
            "list_unsub": 1, "list_id": "", "labels": "inbox", "unsub_url": "", "rfc_id": f"{msg_id}@x"}


class Connection(FakeGmail):
    """One IMAP connection onto a mailbox shared with the others (the server is the lock)."""

    def __init__(self, server):
        self.folders, self.current, self.server = server.folders, None, server

    def uid(self, cmd, *args):
        with self.server.lock:
            self.next_uid = self.server.next_uid
            try:
                return super().uid(cmd, *args)
            finally:
                self.server.next_uid = self.next_uid


def setup_db():
    store.DB_PATH = Path(tempfile.mkdtemp()) / "test.db"
    db = store.connect()
    for acc in (GMAIL, OUT1, OUT2):
        perms.set_grants(db, acc, ["scan", "read", "trash"])
    return db


def test_gmail_parallel():
    server = FakeGmail({"[Gmail]/All Mail": {u: str(9000 + u) for u in range(1, 401)}, "[Gmail]/Trash": {}})
    server.lock, logins = threading.Lock(), []

    def login(account):
        logins.append(1)
        if len(logins) == 2:
            raise OSError("too many connections")
        return Connection(server)
    imapmail.login = login
    store.load_config = lambda: {"imap": [{"email": GMAIL, "password": "x", "host": "imap.gmail.com"}]}
    db = setup_db()
    store.replace_messages(db, GMAIL, [row(GMAIL, str(9000 + u), u) for u in range(1, 401)])
    log = []
    moved = cleanup.trash_messages(db, GMAIL, {str(9000 + u): u for u in range(1, 401)}, log.append)
    parts = -(-400 // gmail.TRASH_PER_CONNECTION)
    assert len(logins) == parts == 3, logins
    share = len(range(1, 401)[1::parts])  # the slice whose connection failed
    assert moved == 400 - share and len(server.folders["[Gmail]/Trash"]) == moved, (moved, share)
    assert any("couldn't connect" in line for line in log), log
    recorded = db.execute("SELECT COUNT(*) FROM trashed").fetchone()[0]
    backed = db.execute("SELECT COUNT(*) FROM backups").fetchone()[0]
    assert recorded == moved and backed >= moved, (recorded, backed, moved)


def test_outlook_chunks_and_two_accounts():
    one = FakeGraph({f"a{n}": raw_email(f"a{n}@x") for n in range(250)})
    two = FakeGraph({f"b{n}": raw_email(f"b{n}@x") for n in range(5)})
    by_account = {OUT1: one, OUT2: two}
    calls = {"batches": 0}

    def graph(method, path, body=None):
        box = by_account[outlook.current()]  # each call must know its account, even on worker threads
        if path == "/$batch" and box is one:
            calls["batches"] += 1
            if calls["batches"] == 9:  # chunks 1-2 are 10 batches: drop part-way through chunk 2
                raise OSError("connection reset")
        return box.graph(method, path, body)
    outlook.graph = graph
    outlook.graph_raw = lambda path: by_account[outlook.current()].graph_raw(path)
    outlook.accounts = lambda: [OUT1, OUT2]
    store.load_config = lambda: {"imap": []}
    assert set(providers.accounts()) == {OUT1, OUT2}
    db = setup_db()
    store.replace_messages(db, OUT1, [row(OUT1, f"a{n}", 0) for n in range(250)])
    store.replace_messages(db, OUT2, [row(OUT2, f"b{n}", 0) for n in range(5)])
    log = []
    moved = cleanup.trash_messages(db, OUT1, {f"a{n}": 0 for n in range(250)}, log.append)
    assert moved == 8 * 20 == len(one.folders["deleteditems"]), (moved, len(one.folders["deleteditems"]))
    assert any("stopped early" in line for line in log), log
    assert outlook_write.TRASH_CHUNK == 100 and len(one.folders["inbox"]) == 250 - moved
    assert cleanup.trash_messages(db, OUT2, {f"b{n}": 0 for n in range(5)}, log.append) == 5
    assert len(two.folders["deleteditems"]) == 5 and len(one.folders["deleteditems"]) == moved, "accounts mixed up"
    recorded = dict(db.execute("SELECT account, COUNT(*) FROM trashed GROUP BY account"))
    assert recorded == {OUT1: moved, OUT2: 5}, recorded


def main():
    test_gmail_parallel()
    test_outlook_chunks_and_two_accounts()
    print("parallel cleanups / several Outlook accounts: all checks passed")


if __name__ == "__main__":
    main()
