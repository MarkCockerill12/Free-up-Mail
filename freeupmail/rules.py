"""Classification rules: what each message is, what to suggest per sender, what is kept.

Tuned against real subjects (see AiContext/Emails). Protection errs towards keeping:
a false "security"/"receipt" match only means a junk mail survives.
"""

import re
from datetime import UTC, datetime, timedelta

SECURITY_RE = re.compile(
    r"security|verif|password|passcode|\b(sign(ed)?|log(ged)?)[ -]?(in|on)\b"
    r"|2-step|two.?(step|factor)|\b2fa\b|\bmfa\b|one.?time (code|pass)|\botp\b"
    r"|(verification|security|login|confirmation|access|auth\w*) code|\bcode (is|:)|\d{4,8} is your"
    r"|new device|suspicious|unusual (activity|sign)|account (recovery|access|locked|suspended|disabled)"
    r"|\baccount\b.{0,30}\b(unlinked|deactivated|closed|compromised)"
    r"|(phone number|email address|recovery (email|phone)).{0,25}\b(added|changed|removed|updated)"
    r"|\bapp(\(s\)|s)?\b.{0,15}\b(connected|authori[sz]ed|access)",
    re.IGNORECASE,
)
# Strong receipt words win over delivery words ("refund for your parcel" is a receipt).
RECEIPT_RE = re.compile(
    r"receipt|invoice|refund|payslip|\bp60\b|\bp45\b|\bsalary\b"
    r"|payment (received|confirm|sent|to|from|of|failed|due)|you('ve| have)? (paid|sent|received|authori[sz]ed)"
    r"|order (confirm|#|no\b|number)|confirmed: your order|booking|reservation|e-?ticket"
    r"|\bstatement\b|tax (return|letter|code|refund|statement|year)|\bhmrc\b|self.assessment"
    r"|your (purchase|subscription|bill)\b|\bbill (is|due)",
    re.IGNORECASE,
)
SHIPPING_RE = re.compile(
    r"out for delivery|\bdelivered\b|delivery (update|attempt|scheduled|today|tomorrow|notification)"
    r"|\bdispatched\b|\bshipped\b|on (its|the) way|\bparcel\b|\bpackages?\b.{0,20}(update|arriv|deliver)"
    r"|tracking (number|update|info)|picked up|ready (for|to) (pick.?up|collect)|\barriv(ing|es|ed)\b"
    r"|in transit|now with (its|your|the)? ?\w* ?courier",
    re.IGNORECASE,
)
# Weaker order wording, only used when nothing above matched.
ORDER_RE = re.compile(r"your order|\bordered\b|order (update|has|was|is)|thanks for your order", re.IGNORECASE)

STRONG_BULK_ADDR_RE = re.compile(
    r"no.?reply|do.?not.?reply|newsletter|mailer|marketing|promo|offers|deals|news@|bounce|postmaster|mailer-daemon"
)
WEAK_BULK_ADDR_RE = re.compile(r"^(info|hello|team|notifications?|updates?|email|sender|support|contact|alerts?)@")
# Automated senders that relay mail from real people.
PERSON_RELAY_RE = re.compile(r"drive-shares|calendar-notification|comments-noreply@docs|@members\.ebay\.")
# One-off junk from throwaway addresses: random plus-tags, Microsoft trial tenants,
# and run-together subjects like "GetAFreeQuoteNowV2haemW".
SPAM_ADDR_RE = re.compile(
    r"\+[a-z0-9]{10,}@(gmail|googlemail|outlook|hotmail|live|yahoo|icloud|aol)\."
    r"|@[a-z0-9-]+\.onmicrosoft\.com$"
)
SPAM_SUBJECT_RE = re.compile(r"(?<![\w/])(?=[A-Za-z0-9]*[a-z])(?=[A-Za-z0-9]*[A-Z])[A-Za-z0-9]{20,}(?![\w/])")

ACTIONS = ("KEEP", "REVIEW", "DELETE")
DEFAULT_SETTINGS = {
    "keep_security": "all",  # all | 90d | none
    "keep_receipts": "1",
    "keep_shipping": "1",
    "backup": "1",  # save a local copy of every email before it goes to Trash
    "auto_rules": "1",  # after each scan, run the rules marked "run automatically"
    "theme": "xp",  # xp | win10 | aero | minimal | dark
    "welcome_done": "0",
}


def classify(subject: str) -> str:
    """'security' / 'receipt' / 'shipping' / '' for one subject line."""
    if SECURITY_RE.search(subject):
        return "security"
    if RECEIPT_RE.search(subject):
        return "receipt"
    if SHIPPING_RE.search(subject):
        return "shipping"
    if ORDER_RE.search(subject):
        return "receipt"
    return ""


def looks_like_spam(addr: str, subject: str) -> bool:
    return bool(SPAM_ADDR_RE.search(addr) or SPAM_SUBJECT_RE.search(subject))


def is_kept(kind: str, date: str, settings: dict, starred: bool = False, pinned: bool = False) -> bool:
    """True when a message survives even though its sender is marked DELETE."""
    if starred or pinned:
        return True
    if kind == "security":
        mode = settings["keep_security"]
        if mode == "all":
            return True
        if mode == "90d":
            try:
                sent = datetime.fromisoformat(date)
            except ValueError:
                return True
            if sent.tzinfo is None:
                sent = sent.replace(tzinfo=UTC)
            return datetime.now(UTC) - sent < timedelta(days=90)
        return False
    if kind == "receipt":
        return settings["keep_receipts"] == "1"
    if kind == "shipping":
        return settings["keep_shipping"] == "1"
    return False


def suggest(sender: dict, own: set[str]) -> tuple[str, str]:
    addr = sender["from_addr"]
    if addr in own:
        return "KEEP", "one of your own accounts"
    if sender["is_contact"]:
        return "KEEP", "you have emailed them"
    if sender["starred"]:
        return "KEEP", "you starred/flagged some"
    if PERSON_RELAY_RE.search(addr):
        return "KEEP", "relays mail from real people"
    if sender["unsub"]:
        return "DELETE", "mailing list (has unsubscribe link)"
    if STRONG_BULK_ADDR_RE.search(addr):
        return "DELETE", "automated no-reply/marketing address"
    if sender["promo_social"]:
        return "DELETE", "mostly in Gmail Promotions/Social"
    if sender["spammy"]:
        return "DELETE", "looks like spam (throwaway address or junk subject)"
    if sender.get("mostly_other"):
        return "REVIEW", "in Outlook's Other tab"
    if WEAK_BULK_ADDR_RE.search(addr):
        return "REVIEW", "automated-looking address"
    return "REVIEW", "not obviously bulk"
