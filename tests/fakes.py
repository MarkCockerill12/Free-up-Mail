"""Fake mail servers for the tests: Gmail (X-GM extensions), a plain IMAP server without
MOVE, and Microsoft Graph. Each keeps every message's raw source so backups can be checked."""

import re

from freeupmail import imapmail


def raw_email(rfc: str, subject: str = "Hello") -> bytes:
    return (f"Message-ID: <{rfc}>\r\nFrom: Shop <news@shop.com>\r\nTo: me@example.com\r\n"
            f"Subject: {subject}\r\nDate: Mon, 01 Sep 2025 10:00:00 +0000\r\n\r\nBody of {rfc}\r\n").encode()


def _fetch_reply(uids, box, what, extra=lambda u: ""):
    out = []
    for u in uids:
        if u not in box:
            continue
        if "BODY.PEEK[]" in what:
            out += [(f"1 (UID {u} BODY[] {{99}}".encode(), box[u]["raw"]), b")"]
        elif "MESSAGE-ID" in what:
            out += [(f"1 (UID {u} FLAGS () BODY[HEADER.FIELDS (MESSAGE-ID)] {{40}}".encode(),
                     f"Message-ID: <{box[u]['rfc']}>\r\n\r\n".encode()), b")"]
        else:
            out.append(f"1 ({extra(u)}UID {u})".encode())
    return out


class FakeGmail:
    """Folders of {uid: {"gm": X-GM-MSGID, "rfc", "raw", "labels"}}."""

    capabilities = ("IMAP4REV1", "MOVE", "X-GM-EXT-1")

    def __init__(self, folders):
        self.folders = {name: {u: {"gm": gm, "rfc": f"{gm}@gmail", "raw": raw_email(f"{gm}@gmail"), "labels": set(),
                                   "flags": set()} for u, gm in msgs.items()} for name, msgs in folders.items()}
        self.current = None
        self.next_uid = 1000

    def list(self):
        names = [b'(\\HasNoChildren \\All) "/" "[Gmail]/All Mail"', b'(\\HasNoChildren \\Trash) "/" "[Gmail]/Trash"',
                 b'(\\HasNoChildren \\Junk) "/" "[Gmail]/Spam"', b'(\\HasNoChildren) "/" "INBOX"']
        names += [f'(\\HasNoChildren) "/" "{n}"'.encode() for n in self.folders if not n.startswith(("[", "INBOX"))]
        return "OK", names

    def create(self, name):
        self.folders.setdefault(imapmail.plain(name), {})
        return "OK", [b""]

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
            return "OK", _fetch_reply(uids, box, args[1], lambda u: f"X-GM-MSGID {box[u]['gm']} ")
        if cmd == "MOVE":
            dest = self.folders[imapmail.plain(args[1])]
            for u in map(int, args[0].split(",")):
                self.next_uid += 1
                dest[self.next_uid] = box.pop(u)
            return "OK", [b"done"]
        if cmd == "STORE":
            for u in map(int, args[0].split(",")):
                target = box[u]["labels"] if "X-GM-LABELS" in args[1] else box[u]["flags"]
                value = args[2].strip("()").strip('"')
                (target.add if args[1].startswith("+") else target.discard)(value)
            return "OK", [b""]
        raise AssertionError(cmd)

    def logout(self):
        pass


class FakeIMAP:
    """A plain IMAP server (no Gmail extensions, no MOVE): folders of {uid: {"rfc", "raw", "flags"}}."""

    capabilities = ("IMAP4REV1", "UIDPLUS")

    def __init__(self, folders):
        self.folders = {name: {"validity": n, "next": 100,
                               "msgs": {u: {"rfc": rfc, "raw": raw_email(rfc), "flags": set()} for u, rfc in msgs.items()}}
                        for n, (name, msgs) in enumerate(folders.items(), 7)}
        self.current = None
        self.appended = []

    def list(self):
        return "OK", [f'(\\HasNoChildren) "." {imapmail.quote(n)}'.encode() for n in self.folders]

    def create(self, name):
        self.folders[imapmail.plain(name)] = {"validity": 50, "next": 1, "msgs": {}}
        return "OK", [b""]

    def select(self, name, readonly=False):
        self.current = imapmail.plain(name)
        self._validity = self.folders[self.current]["validity"]
        return "OK", [b"1"]

    def response(self, _):
        return "UIDVALIDITY", [str(self._validity).encode()]

    def status(self, name, _):
        f = self.folders[name]
        return "OK", [f'{name} (UIDNEXT {f["next"]} UIDVALIDITY {f["validity"]})'.encode()]

    def append(self, mailbox, flags, when, raw):
        f = self.folders[mailbox]
        rfc = re.search(rb"Message-ID: <([^>]+)>", raw)[1].decode()
        f["msgs"][f["next"]] = {"rfc": rfc, "raw": raw, "flags": {"\\Seen"} if flags else set()}
        f["next"] += 1
        self.appended.append(rfc)
        return "OK", [b""]

    def uid(self, cmd, *args):
        box = self.folders[self.current]["msgs"]
        if cmd == "SEARCH":
            uids = sorted(box)
            if args[0] == "UID":
                start = int(args[1].split(":")[0])
                uids = [u for u in uids if u >= start] or uids[-1:]
            return "OK", [" ".join(map(str, uids)).encode()]
        if cmd == "FETCH":
            return "OK", _fetch_reply([int(x) for x in args[0].split(",")], box, args[1])
        if cmd == "COPY":
            dest = self.folders[imapmail.plain(args[1])]
            for u in map(int, args[0].split(",")):
                dest["msgs"][dest["next"]] = dict(box[u], flags=set(box[u]["flags"]))
                dest["next"] += 1
            return "OK", [b"done"]
        if cmd == "STORE":
            for u in map(int, args[0].split(",")):
                box[u]["flags"].add(args[2].strip("()"))
            return "OK", [b""]
        if cmd == "EXPUNGE":
            for u in map(int, args[0].split(",")):
                assert "\\Deleted" in box[u]["flags"], "expunged a message that was not flagged deleted"
                box.pop(u)
            return "OK", [b""]
        raise AssertionError(cmd)

    def logout(self):
        pass

    def contents(self, folder):
        return sorted(m["rfc"] for m in self.folders[folder]["msgs"].values())


class FakeGraph:
    """Microsoft Graph: folders of {id: raw}; moves give new ids like real Exchange."""

    def __init__(self, inbox: dict[str, bytes]):
        self.folders = {"inbox": dict(inbox), "deleteditems": {}, "archive": {}, "junkemail": {}}
        self.patched: dict[str, dict] = {}
        self.created: list[dict] = []

    def _find(self, mid):
        return next((f for f, msgs in self.folders.items() if mid in msgs), None)

    def graph(self, method, path, body=None):
        if path == "/$batch":
            responses = []
            for r in body["requests"]:
                mid = re.search(r"/me/messages/([^/?]+)", r["url"])[1]
                src = self._find(mid)
                if src is None:
                    responses.append({"id": r["id"], "status": 404})
                elif r["method"] == "PATCH":
                    self.patched.setdefault(mid, {}).update(r["body"])
                    responses.append({"id": r["id"], "status": 200, "body": {}})
                else:
                    dest = r["body"]["destinationId"]
                    self.folders.setdefault(dest, {})[f"new-{mid}"] = self.folders[src].pop(mid)
                    responses.append({"id": r["id"], "status": 201, "body": {"id": f"new-{mid}"}})
            return {"responses": responses}
        if method == "POST" and path == "/me/mailFolders/inbox/messages":
            self.created.append(body)
            return {"id": f"up-{len(self.created)}"}
        if method == "GET" and path.startswith("/me/mailFolders?"):
            return {"value": [{"id": f, "displayName": f} for f in self.folders]}
        if method == "POST" and path == "/me/mailFolders":
            self.folders[body["displayName"]] = {}
            return {"id": body["displayName"]}
        raise AssertionError((method, path))

    def graph_raw(self, path):
        mid = re.search(r"/me/messages/([^/]+)/\$value", path)[1]
        return self.folders[self._find(mid)][mid]
