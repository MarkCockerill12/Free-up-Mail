"""Regression cases for rules.py, taken from an audit of real subjects.

Run: python -m tests.test_rules
"""
from freeupmail import rules

CLASSIFY = {
    "Security alert": "security",
    "914730 is your Instagram code": "security",
    "Your bank account was unlinked from your account": "security",
    "Your phone number has been added to your account": "security",
    "New login to X from ChromeDesktop on Windows": "security",
    "New app(s) connected to your Microsoft account": "security",
    "Use code SAVE20 for 20% off": "",
    "Download our app for exclusive deals": "",
    "Designing your dream kitchen": "",
    "Acme Trading Limited payslip": "receipt",
    "You've authorised a payment to eBay Commerce UK Ltd": "receipt",
    "SAVE 40% ON MALWAREBYTES STANDARD THIS TAX SEASON": "",
    "Your online tax letters": "receipt",
    "Your Temu order has been refunded": "receipt",
    "We've delivered your AliExpress UK parcel": "shipping",
    "Your Temu package is arriving soon": "shipping",
    "Your order has been dispatched": "shipping",
    "Your bid is winning now and watch out for the competition": "",
    "Undeliverable: karen-millen/unsub": "",
    "Fwd: hurryUpYourfreeBottleOfferHasArrivedClaimnow7831123": "",
    "New arrivals just dropped - free shipping": "",
    "Hello from Deliveroo": "",
    "Thanks for your order!": "receipt",
}
SPAM = {
    ("randomname98@gmail.com", "freeElectricityNow71915"): True,
    ("x+abcdefghijklmn@gmail.com", "hello"): True,
    ("someone@ab12cd.onmicrosoft.com", "Invite"): True,
    ("instagram++aazqxsqwzzbgbo@support.facebook.com", ""): False,
    ("numbers-regulatory-review@twilio.com", "The Status of the Regulatory Bundle - BU67567ABCDEF123456789"): False,
    ("friend@gmail.com", "Lunch on Friday?"): False,
}
fails = 0
for subject, want in CLASSIFY.items():
    got = rules.classify(subject)
    if got != want:
        fails += 1
        print(f"FAIL classify {subject!r}: got {got!r} want {want!r}")
for (addr, subject), want in SPAM.items():
    if rules.looks_like_spam(addr, subject) != want:
        fails += 1
        print(f"FAIL spam {addr} {subject!r}: want {want}")
print(f"{len(CLASSIFY) + len(SPAM) - fails}/{len(CLASSIFY) + len(SPAM)} passed")
raise SystemExit(1 if fails else 0)
