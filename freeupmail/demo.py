"""Made-up mailboxes for `--demo`: try every screen without connecting a real account.
Demo accounts are marked in the config, and providers.Mailbox refuses to change them."""

import json
import random
from datetime import UTC, datetime, timedelta

from . import automation, perms, store

GMAIL, OUTLOOK, YAHOO = "alex.taylor@gmail.com", "alex.taylor@outlook.com", "alex.t@yahoo.com"
# (name, address, category, mailing list, weight per account (gmail, outlook, yahoo), subjects)
SENDERS = [
    ("ShopMart", "deals@shopmart.example", "promotions", True, (90, 40, 10), [
        "48-hour flash sale: up to 60% off", "Your weekend picks are here", "Last chance: free delivery ends tonight",
        "New arrivals you'll love", "Your ShopMart order receipt #20931"]),
    ("SocialSphere", "notify@socialsphere.example", "social", True, (110, 20, 0), [
        "Sam liked your photo", "You have 4 new friend suggestions", "Jordan mentioned you in a comment",
        "See what you missed this week"]),
    ("Daily Deals", "news@dailydeals.example", "promotions", True, (60, 50, 30), [
        "Today only: 70% off spa days", "Deals near you", "Your local deals for this week"]),
    ("Tech Weekly", "newsletter@techweekly.example", "updates", True, (70, 0, 12), [
        "Issue #212: the state of laptops in 2026", "This week in tech", "Five apps worth trying"]),
    ("Pizza Planet", "offers@pizzaplanet.example", "promotions", True, (0, 64, 0), [
        "2 for 1 pizza Tuesday", "Your free garlic bread is waiting", "Hungry? Here's 30% off"]),
    ("FitTrack", "hello@fittrack.example", "updates", True, (45, 0, 0), [
        "Your weekly activity summary", "You beat your step goal 4 times!", "New challenge: 10k a day"]),
    ("Travelly", "deals@travelly.example", "promotions", True, (30, 25, 0), [
        "Weekend breaks from £49", "Prices dropped for Lisbon", "Booking confirmed: Lisbon, 12-15 May"]),
    ("Streamflix", "info@streamflix.example", "updates", True, (0, 33, 0), [
        "New this week on Streamflix", "Because you watched Night Shift", "Your Streamflix receipt"]),
    ("Bank of Example", "alerts@bankofexample.example", "updates", False, (12, 14, 0), [
        "Your statement is ready", "Security alert: new device signed in", "Payment received - thank you"]),
    ("Parcel Express", "tracking@parcelexpress.example", "updates", False, (20, 22, 6), [
        "Your parcel is out for delivery", "Your parcel has been delivered", "Delivery update for order 55102"]),
    ("CloudDrive", "no-reply@clouddrive.example", "updates", False, (18, 10, 0), [
        "New sign-in to your CloudDrive account", "You're running out of storage", "Your files were backed up"]),
    ("Prize Centre", "winner-claim83x7b2k1q@freemail.example", "", False, (0, 18, 14), [
        "cl41mY0urPr1zeN0wFr33G1ft8812", "Congratulations! You have been selected"]),
    ("City Council", "events@citycouncil.example", "", False, (6, 0, 0), [
        "Bin collection changes over the holidays", "Have your say on the new cycle lanes"]),
    ("Jordan Lee", "jordan.lee@gmail.com", "", False, (14, 4, 0), [
        "Dinner on Friday?", "Photos from the weekend", "Re: holiday plans"]),
    ("Pat Taylor", "pat.taylor@yahoo.com", "", False, (6, 0, 10), [
        "Sunday lunch", "Did you get my parcel?", "Happy birthday love!"]),
    ("Maria Gomez", "maria@studio-north.example", "", False, (0, 9, 0), [
        "Draft designs for review", "Invoice for March", "Meeting notes"]),
]
CONTACTS = ["jordan.lee@gmail.com", "pat.taylor@yahoo.com", "maria@studio-north.example"]


def _rows() -> dict[str, list[dict]]:
    rnd = random.Random(7)
    now = datetime(2026, 10, 20, 9, 30, tzinfo=UTC)
    out: dict[str, list[dict]] = {GMAIL: [], OUTLOOK: [], YAHOO: []}
    for name, addr, cat, is_list, weights, subjects in SENDERS:
        for account, count in zip(out, weights, strict=True):
            for n in range(count):
                when = now - timedelta(days=rnd.random() ** 1.6 * 700, minutes=rnd.randint(0, 1400))
                labels = [x for x in (cat if account == GMAIL else "", "inbox" if rnd.random() < 0.6 else "") if x]
                if account == OUTLOOK and cat in ("promotions", "social"):
                    labels.append("other")
                if addr in CONTACTS and n == 0:
                    labels.append("starred")
                uid = len(out[account]) + 1
                out[account].append({
                    "account": account, "msg_id": f"demo-{uid}", "uid": uid, "from_addr": addr, "from_name": name,
                    "from_domain": addr.split("@")[1], "subject": subjects[n % len(subjects)],
                    "date": when.isoformat(), "seen": int(rnd.random() < 0.55), "list_unsub": int(is_list),
                    "list_id": "", "labels": ",".join(labels),
                    "unsub_url": f"https://{addr.split('@')[1]}/unsubscribe" if is_list else "",
                    "rfc_id": f"demo-{uid}-{account}",
                })
    return out


RULES = [
    {"name": "Receipts folder", "conditions": [{"field": "subject", "op": "contains", "value": "receipt"}],
     "action": "move", "folder": "Receipts", "auto": True},
    {"name": "Never delete the bank", "conditions": [{"field": "domain", "op": "is", "value": "bankofexample.example"}],
     "action": "keep"},
    {"name": "Social notifications out of the inbox", "match": "all",
     "conditions": [{"field": "category", "op": "is", "value": "social"}, {"field": "age", "op": "older", "value": "7"}],
     "action": "archive", "mark_read": True},
]


def create() -> None:
    store.save_config({"demo": True, "demo_outlook": OUTLOOK, "imap": [
        {"email": GMAIL, "password": "", "host": "imap.gmail.com", "demo": True},
        {"email": YAHOO, "password": "", "demo": True},
    ]})
    db = store.connect()
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    for account, rows in _rows().items():
        store.replace_messages(db, account, rows)
        store.replace_contacts(db, account, {c: 3 for c in CONTACTS})
        store.record_scan(db, account, stamp, "ok")
        granted = ["scan", "read"] if account == YAHOO else ["scan", "read", "trash", "organise"]
        perms.set_grants(db, account, granted + (["server_rules"] if account == OUTLOOK else []))
    for rule in RULES:
        automation.save(db, {"enabled": True, **rule})
    with db:
        db.execute("INSERT OR REPLACE INTO sender_actions VALUES (?, ?, 'KEEP')", (OUTLOOK, "info@streamflix.example"))
        for n, (subject, addr) in enumerate([("Mega clearance event", "deals@shopmart.example"),
                                             ("Final hours!", "news@dailydeals.example")]):
            db.execute("INSERT INTO trashed VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
                       (GMAIL, f"old-{n}", f"old-{n}", addr, addr.split("@")[1], subject, "2026-09-01T10:00:00+00:00", stamp))
            db.execute("INSERT INTO backups VALUES (?, ?, '', '', ?, '', ?, '', 1, 2048, ?)",
                       (GMAIL, f"old-{n}", addr, subject, stamp))


def message(db, account: str, msg_id: str) -> dict:
    row = db.execute("SELECT from_name, subject, unsub_url FROM messages WHERE account = ? AND msg_id = ?",
                     (account, msg_id)).fetchone()
    if not row:
        return {"error": "Not found in the demo."}
    name, subject, unsub = row
    hue = sum(map(ord, name)) % 360
    html = f"""<div style="max-width:560px;margin:0 auto;font-family:Segoe UI,Arial,sans-serif;color:#222">
      <div style="background:hsl({hue},60%,45%);color:#fff;padding:18px 22px;border-radius:8px 8px 0 0">
        <div style="font-size:20px;font-weight:bold">{name}</div></div>
      <div style="border:1px solid #ddd;border-top:none;padding:20px 22px;border-radius:0 0 8px 8px">
        <h2 style="margin-top:0">{subject}</h2>
        <p>This is a made-up email in the Free(up)Mail demo. In the real app you see the email exactly as
        it was sent, with remote images (which tell senders you opened it) blocked until you allow them.</p>
        <p style="text-align:center;margin:26px 0"><span style="background:hsl({hue},60%,45%);color:#fff;
          padding:10px 22px;border-radius:20px;font-weight:bold">View offer</span></p>
        <p style="color:#888;font-size:12px">You are receiving this because you signed up at {name}.</p>
      </div></div>"""
    return {"html": html, "text": "", "attachments": [], "unsub": unsub or "", "web_link": ""}


if __name__ == "__main__":
    print(json.dumps({a: len(r) for a, r in _rows().items()}))
