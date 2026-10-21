"""Changes to an Outlook mailbox through Microsoft Graph: move to Deleted Items (backing
each email up first), restore, re-upload from a backup, rule actions and inbox rules.
Reading and sign-in live in outlook.py; providers.Mailbox checks permissions first."""

import base64
import email
import email.policy
import email.utils
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from . import outlook
from .outlook import Log

UPLOAD_ATTACHMENT_LIMIT = 3_000_000  # Graph takes attachments inline up to ~3 MB per request
PARALLEL = 4  # Exchange allows 4 concurrent requests per mailbox
RULE_PREFIX = "Free(up)Mail"
OLD_RULE_PREFIXES = (RULE_PREFIX, "Mail Tidy")  # rules from before the rename are replaced too
RULE_CHUNK = 40


def _move_batch(msg_ids: list[str], destination: str, log: Log, verb: str) -> dict[str, str]:
    """Move messages 20 per Graph batch. Returns {old id: new id} for those moved, also
    when the connection fails part-way. Requests Microsoft asks to slow down are retried."""
    moved: dict[str, str] = {}
    try:
        for i in range(0, len(msg_ids), 20):
            pending = msg_ids[i : i + 20]
            for attempt in range(4):
                requests = [{
                    "id": str(n), "method": "POST", "url": f"/me/messages/{outlook._quote(mid)}/move",
                    "body": {"destinationId": destination}, "headers": {"Content-Type": "application/json"},
                } for n, mid in enumerate(pending)]
                result = outlook.graph("POST", "/$batch", {"requests": requests})
                retry = []
                for resp in result.get("responses", []):
                    mid = pending[int(resp["id"])]
                    if resp.get("status") in (200, 201):
                        moved[mid] = (resp.get("body") or {}).get("id", "")
                    elif resp.get("status") in (429, 503, 504):
                        retry.append(mid)
                if not retry:
                    break
                pending = retry
                time.sleep(2 * (attempt + 1))
            if (i // 20) % 10 == 0 or i + 20 >= len(msg_ids):
                log(f"  {verb} {len(moved)}/{len(msg_ids)}")
    except (RuntimeError, OSError) as exc:
        log(f"  stopped early ({exc}); {len(moved)} were done and are recorded")
    return moved


def _mime(msg_id: str) -> bytes | None:
    try:
        return outlook.graph_raw(f"/me/messages/{outlook._quote(msg_id)}/$value")
    except (RuntimeError, OSError):
        return None  # not backed up, so it isn't moved either


def trash(msg_ids: list[str], log: Log, keep=None) -> dict[str, str]:
    """Move to Deleted Items. With `keep` (see backup.keeper) each email's MIME source is
    saved first and only the ones safely backed up are moved."""
    if keep is not None:
        ok: list[str] = []
        with ThreadPoolExecutor(PARALLEL) as pool:
            for n, (mid, raw) in enumerate(zip(msg_ids, pool.map(_mime, msg_ids), strict=True), 1):
                if keep(mid, raw):
                    ok.append(mid)
                if n % 200 == 0:
                    log(f"  backed up {len(ok)}/{len(msg_ids)}")
        if len(ok) < len(msg_ids):
            log(f"  {len(msg_ids) - len(ok)} could not be backed up, so they were left where they are")
        msg_ids = ok
    return _move_batch(msg_ids, "deleteditems", log, "moved to Deleted Items")


def _recipients(value: str | None) -> list[dict]:
    return [{"emailAddress": {"name": n, "address": a}} for n, a in email.utils.getaddresses([value or ""]) if a]


def _message_json(raw: bytes, seen: int) -> dict:
    """A Graph message from a backed-up .eml. MIME uploads always land as drafts, so the
    message is rebuilt with the 'sent' flags and original dates set instead."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    html_part = msg.get_body(preferencelist=("html",))
    text_part = msg.get_body(preferencelist=("plain",))
    body = {"contentType": "html", "content": html_part.get_content()} if html_part else {
        "contentType": "text", "content": text_part.get_content() if text_part else ""}
    try:
        sent = email.utils.parsedate_to_datetime(msg["Date"]).astimezone(UTC).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, IndexError):
        sent = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    sender = _recipients(msg["From"])
    data = {
        "subject": str(msg["Subject"] or ""), "body": body, "toRecipients": _recipients(msg["To"]),
        "ccRecipients": _recipients(msg["Cc"]),
        "singleValueExtendedProperties": [
            {"id": "Integer 0x0E07", "value": "1" if seen else "4"},  # PR_MESSAGE_FLAGS: received, not a draft
            {"id": "SystemTime 0x0E06", "value": sent},  # delivery time
            {"id": "SystemTime 0x0039", "value": sent},  # sent time
        ],
    }
    if sender:
        data["from"] = data["sender"] = sender[0]
    budget, attachments = UPLOAD_ATTACHMENT_LIMIT, []
    for part in msg.walk():
        if part.is_multipart() or part in (html_part, text_part):
            continue
        payload = part.get_payload(decode=True) or b""
        if not part.get_filename() and not part.get("Content-ID"):
            continue
        if len(payload) > budget:
            continue  # too big to upload this way; the .eml backup still has it
        budget -= len(payload)
        attachments.append({
            "@odata.type": "#microsoft.graph.fileAttachment", "name": part.get_filename() or "attachment",
            "contentType": part.get_content_type(), "contentBytes": base64.b64encode(payload).decode(),
            "isInline": bool(part.get("Content-ID")), "contentId": (part.get("Content-ID") or "").strip("<>") or None,
        })
    if attachments:
        data["attachments"] = attachments
    return data


def upload(emails: list[tuple[bytes, int]], log: Log) -> list[int]:
    """Put backed-up emails (raw source, seen flag) back into the Inbox. Returns the
    positions that were uploaded, so a failure part-way loses nothing."""
    done: list[int] = []
    for n, (raw, seen) in enumerate(emails):
        try:
            outlook.graph("POST", "/me/mailFolders/inbox/messages", _message_json(raw, seen))
        except (RuntimeError, OSError) as exc:
            log(f"  one email could not be put back: {exc}")
            continue
        done.append(n)
    log(f"  put back {len(done)} from backups")
    return done


def present(rfc_ids: list[str], log: Log) -> set[str]:
    """Which of these Message-IDs are already back in the mailbox (outside Deleted Items)."""
    found: set[str] = set()
    deleted = outlook._folder_id("deleteditems")
    for rfc in rfc_ids:
        if not rfc or "'" in rfc:
            continue
        query = urllib.parse.quote(f"internetMessageId eq '<{rfc}>'")
        hits = outlook.graph("GET", f"/me/messages?$filter={query}&$select=id,parentFolderId").get("value", [])
        if any(h.get("parentFolderId") != deleted for h in hits):
            found.add(rfc)
    return found


def _folder_by_name(name: str) -> str:
    """Id of the top-level folder called `name`, created if missing."""
    for folder in outlook.pages("/me/mailFolders?$select=id,displayName&$top=200"):
        if folder.get("displayName", "").lower() == name.lower():
            return folder["id"]
    return outlook.graph("POST", "/me/mailFolders", {"displayName": name})["id"]


def _patch_batch(msg_ids: list[str], body: dict) -> None:
    for i in range(0, len(msg_ids), 20):
        requests = [{
            "id": str(n), "method": "PATCH", "url": f"/me/messages/{outlook._quote(mid)}",
            "body": body, "headers": {"Content-Type": "application/json"},
        } for n, mid in enumerate(msg_ids[i : i + 20])]
        outlook.graph("POST", "/$batch", {"requests": requests})


def organise(action: str, msg_ids: list[str], folder: str = "", mark_read: bool = False, log: Log = print) -> int:
    """Rule actions: move / archive / junk / read / star. Returns how many."""
    if mark_read or action == "read":
        _patch_batch(msg_ids, {"isRead": True})
    if action == "star":
        _patch_batch(msg_ids, {"flag": {"flagStatus": "flagged"}})
    destination = {"archive": "archive", "junk": "junkemail"}.get(action)
    if action == "move":
        destination = _folder_by_name(folder)
    if destination:
        return len(_move_batch(msg_ids, destination, log, "moved"))
    log(f"  {len(msg_ids)} emails done")
    return len(msg_ids)


def restore(trashed_ids: list[str], log: Log) -> dict[str, str]:
    return _move_batch(trashed_ids, "inbox", log, "restored")


def apply_rules(addrs: list[str], protect_words: list[str], log: Log, own_rules: list[dict] = ()) -> None:
    """Replace the 'Free(up)Mail ...' inbox rules: your own rules first (see filters.outlook_rule;
    their "folder" is a folder name or a well-known one like "archive"), then rules moving
    the senders marked Delete to Deleted Items."""
    for rule in outlook.pages("/me/mailFolders/inbox/messageRules"):
        if rule.get("displayName", "").startswith(OLD_RULE_PREFIXES):
            outlook.graph("DELETE", f"/me/mailFolders/inbox/messageRules/{rule['id']}")
    for n, own in enumerate(own_rules, 1):
        actions = dict(own["actions"])
        if own.get("folder"):
            well_known = own["folder"] in ("archive", "junkemail")
            actions["moveToFolder"] = outlook._folder_id(own["folder"]) if well_known else _folder_by_name(own["folder"])
        rule = {"displayName": f"{RULE_PREFIX}: {own['name']}"[:250], "sequence": 10 + n, "isEnabled": True,
                "conditions": own["conditions"], "actions": {**actions, "stopProcessingRules": True}}
        if own.get("exceptions"):
            rule["exceptions"] = own["exceptions"]
        outlook.graph("POST", "/me/mailFolders/inbox/messageRules", rule)
        log(f"  your rule '{own['name']}' created")
    deleted = outlook._folder_id("deleteditems")
    chunks = [addrs[i : i + RULE_CHUNK] for i in range(0, len(addrs), RULE_CHUNK)]
    for n, chunk in enumerate(chunks, 1):
        rule = {
            "displayName": f"{RULE_PREFIX} {n}",
            "sequence": 100 + n,
            "isEnabled": True,
            "conditions": {"senderContains": chunk},
            "actions": {"moveToFolder": deleted, "stopProcessingRules": True},
        }
        if protect_words:
            rule["exceptions"] = {"subjectContains": protect_words}
        outlook.graph("POST", "/me/mailFolders/inbox/messageRules", rule)
        log(f"  rule {n}/{len(chunks)} created ({len(chunk)} senders)")
