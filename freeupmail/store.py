"""Config loading, header decoding and the SQLite cache shared by all modules."""

import json
import os
import sqlite3
import sys
from email.errors import HeaderParseError
from email.header import decode_header, make_header
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from pathlib import Path


def _app_dir() -> Path:
    """Per-user data folder, so the app runs the same from a checkout, a .pyz or an .exe.
    FREEUPMAIL_HOME overrides it (e.g. to keep everything on a USB stick)."""
    if os.environ.get("FREEUPMAIL_HOME"):
        return Path(os.environ["FREEUPMAIL_HOME"])
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "FreeUpMail"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "FreeUpMail"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "freeupmail"


APP_DIR = _app_dir()
CONFIG_PATH = APP_DIR / "accounts.json"
DB_PATH = APP_DIR / "mail.db"

MESSAGE_COLUMNS = (
    "account", "msg_id", "uid", "from_addr", "from_name", "from_domain", "subject", "date",
    "seen", "list_unsub", "list_id", "labels", "unsub_url", "rfc_id",
)
SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    account TEXT NOT NULL,
    msg_id TEXT NOT NULL,
    uid INTEGER,
    from_addr TEXT,
    from_name TEXT,
    from_domain TEXT,
    subject TEXT,
    date TEXT,
    seen INTEGER,
    list_unsub INTEGER,
    list_id TEXT,
    labels TEXT,
    PRIMARY KEY (account, msg_id)
);
CREATE TABLE IF NOT EXISTS contacts (
    account TEXT NOT NULL,
    addr TEXT NOT NULL,
    sent_count INTEGER DEFAULT 1,
    PRIMARY KEY (account, addr)
);
CREATE INDEX IF NOT EXISTS ix_messages_from ON messages (account, from_addr);
CREATE TABLE IF NOT EXISTS sender_actions (
    account TEXT NOT NULL,
    from_addr TEXT NOT NULL,
    action TEXT NOT NULL,
    PRIMARY KEY (account, from_addr)
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS scans (account TEXT PRIMARY KEY, scanned_at TEXT, status TEXT);
-- Individual emails the user chose to keep regardless of the sender's action.
CREATE TABLE IF NOT EXISTS pinned (account TEXT NOT NULL, msg_id TEXT NOT NULL, PRIMARY KEY (account, msg_id));
-- Everything Free(up)Mail moved to Trash, so it can be restored.
CREATE TABLE IF NOT EXISTS trashed (
    account TEXT NOT NULL,
    msg_id TEXT NOT NULL,
    new_id TEXT,
    from_addr TEXT,
    from_name TEXT,
    subject TEXT,
    date TEXT,
    trashed_at TEXT,
    restored INTEGER DEFAULT 0,
    PRIMARY KEY (account, msg_id)
);
-- What Free(up)Mail may do in each mailbox (see perms.py). No row, no access.
CREATE TABLE IF NOT EXISTS grants (account TEXT NOT NULL, perm TEXT NOT NULL, PRIMARY KEY (account, perm));
-- Local .eml copy of every email taken before it was moved to Trash.
CREATE TABLE IF NOT EXISTS backups (
    account TEXT NOT NULL,
    msg_id TEXT NOT NULL,
    path TEXT NOT NULL,
    rfc_id TEXT,
    from_addr TEXT,
    from_name TEXT,
    subject TEXT,
    date TEXT,
    seen INTEGER,
    size INTEGER,
    saved_at TEXT,
    PRIMARY KEY (account, msg_id)
);
-- Your own rules (automation.py); data is the rule as JSON.
CREATE TABLE IF NOT EXISTS user_rules (id INTEGER PRIMARY KEY AUTOINCREMENT, position INTEGER DEFAULT 0, data TEXT NOT NULL);
-- Emails you restored, by Message-ID, so they stay protected even after their id changes.
CREATE TABLE IF NOT EXISTS rescued (account TEXT NOT NULL, rfc_id TEXT NOT NULL, PRIMARY KEY (account, rfc_id));
-- Counts changes to everything the per-sender totals are worked out from, so views.py can
-- keep them in memory between requests (your Delete/Keep choices are applied on top).
CREATE TABLE IF NOT EXISTS data_version (v INTEGER NOT NULL);
"""
DERIVED_FROM = ("messages", "contacts", "pinned", "rescued", "user_rules", "settings")
SCHEMA += "".join(
    f"CREATE TRIGGER IF NOT EXISTS bump_{table}_{op.lower()} AFTER {op} ON {table}"
    " BEGIN UPDATE data_version SET v = v + 1; END;\n"
    for table in DERIVED_FROM for op in ("INSERT", "UPDATE", "DELETE")
)
MIGRATIONS = {"unsub_url": "TEXT DEFAULT ''", "rfc_id": "TEXT DEFAULT ''"}


def load_config() -> dict:
    """{"imap": [{email, password, host, port}]}. Older files kept Gmail-only accounts
    under "gmail" with an "app_password"; those are read as imap.gmail.com accounts."""
    if not CONFIG_PATH.exists():
        return {"imap": []}
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    for old in config.pop("gmail", []):
        config.setdefault("imap", []).append({
            "email": old["email"], "password": old.get("password") or old["app_password"],
            "host": "imap.gmail.com", "port": 993,
        })
    config.setdefault("imap", [])
    return config


def save_config(config: dict) -> None:
    write_private(CONFIG_PATH, json.dumps(config, indent=2))


def write_private(path: Path, text: str) -> None:
    """Write a file only the current user can read (passwords and sign-in tokens)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if sys.platform != "win32":
        path.chmod(0o600)


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    # Write-ahead log: a commit costs one small sync instead of several (a cleanup commits
    # once per backed-up email), and the window keeps reading while a job writes.
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    existing = {row[1] for row in db.execute("PRAGMA table_info(messages)")}
    for column, decl in MIGRATIONS.items():
        if column not in existing:
            db.execute(f"ALTER TABLE messages ADD COLUMN {column} {decl}")
    if not db.execute("SELECT 1 FROM data_version").fetchone():
        with db:
            db.execute("INSERT INTO data_version VALUES (0)")
    if not db.execute("SELECT 1 FROM settings WHERE key = 'grants_migrated'").fetchone():
        # Accounts from before permissions existed keep what they were already doing:
        # listing and opening emails. Anything that changes a mailbox must be granted.
        with db:
            db.executemany("INSERT OR IGNORE INTO grants VALUES (?, ?)",
                           [(a, p) for (a,) in db.execute("SELECT account FROM scans") for p in ("scan", "read")])
            db.execute("INSERT INTO settings VALUES ('grants_migrated', '1')")
    return db


def replace_messages(db: sqlite3.Connection, account: str, rows: list[dict]) -> None:
    """Swap one account's cached messages for a fresh scan, in one transaction."""
    cols = ", ".join(MESSAGE_COLUMNS)
    marks = ", ".join("?" * len(MESSAGE_COLUMNS))
    with db:
        db.execute("DELETE FROM messages WHERE account = ?", (account,))
        db.executemany(
            f"INSERT OR REPLACE INTO messages ({cols}) VALUES ({marks})",
            [tuple(r.get(c, "") for c in MESSAGE_COLUMNS) for r in rows],
        )


def replace_contacts(db: sqlite3.Connection, account: str, counts: dict[str, int]) -> None:
    with db:
        db.execute("DELETE FROM contacts WHERE account = ?", (account,))
        db.executemany(
            "INSERT INTO contacts (account, addr, sent_count) VALUES (?, ?, ?)",
            [(account, a, n) for a, n in counts.items()],
        )


def record_scan(db: sqlite3.Connection, account: str, scanned_at: str, status: str) -> None:
    with db:
        db.execute("INSERT OR REPLACE INTO scans VALUES (?, ?, ?)", (account, scanned_at, status))


def get_settings(db: sqlite3.Connection, defaults: dict) -> dict:
    stored = dict(db.execute("SELECT key, value FROM settings"))
    return {k: stored.get(k, v) for k, v in defaults.items()}


def decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except (HeaderParseError, LookupError, UnicodeError):
        return value.strip()


def parse_from(value: str | None) -> tuple[str, str, str]:
    """Return (address, display name, domain), address lower-cased."""
    name, addr = parseaddr(decode(value))
    addr = addr.lower()
    return addr, name, addr.rpartition("@")[2]


def parse_recipients(*values: str | None) -> list[str]:
    pairs = getaddresses([decode(v) for v in values if v])
    return [addr.lower() for _, addr in pairs if "@" in addr]


def unsub_link(value: str | None) -> str:
    """Best link from a List-Unsubscribe header: https first, else mailto."""
    links = [part.strip().strip("<>") for part in (value or "").split(",")]
    for prefix in ("https://", "http://", "mailto:"):
        for link in links:
            if link.lower().startswith(prefix):
                return link
    return ""


def iso_date(value: str | None) -> str:
    try:
        return parsedate_to_datetime(value).isoformat()
    except (TypeError, ValueError, IndexError):
        return ""
