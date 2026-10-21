# Free(up)Mail

Clear years of junk out of every inbox you own at once, without losing the emails that matter.

Free(up)Mail reads all your mailboxes (Outlook, Gmail, Yahoo, iCloud, AOL or any other IMAP account), groups everything by sender, suggests what is junk and lets you review it before anything moves. It can then set up filters so your mail provider bins future junk by itself.

It runs entirely on your computer as a small desktop app with a Windows XP–style interface. There is no server, no account to make and no tracking. It only talks to your own mail providers.

## Download

Grab a single file from the [latest release](https://github.com/MarkCockerill12/Free-up-Mail/releases/latest):

| File | Runs on | Needs |
| --- | --- | --- |
| `FreeUpMail-windows.exe` | Windows 10/11 | nothing |
| `FreeUpMail-macos` | macOS | nothing |
| `FreeUpMail-linux` | Linux | nothing |
| `FreeUpMail.pyz` | any of the above | [Python 3.11+](https://www.python.org/downloads/) |

Double-click it. Free(up)Mail opens in its own window, and closing the window shuts it down completely.

- **Windows:** SmartScreen may warn about an unrecognised app because the exe isn't code-signed. Click **More info → Run anyway**.
- **macOS:** the first time, right-click the file and choose **Open**. If macOS still refuses, run `xattr -d com.apple.quarantine FreeUpMail-macos`, then `chmod +x FreeUpMail-macos`.
- **Linux:** run `chmod +x FreeUpMail-linux` first.

The app window uses Edge, Chrome, Chromium or Brave if one is installed. Otherwise it opens in your default browser.

## Connecting accounts

Open **Accounts** on the toolbar.

| Provider | How it connects |
| --- | --- |
| Outlook.com, Hotmail, Live, Microsoft 365 | **Connect Outlook**: sign in on Microsoft's own page with a one-time code. Only a sign-in token is stored, never your password. |
| Gmail / Google Workspace | Email + an [app password](https://myaccount.google.com/apppasswords) (2-Step Verification must be on). |
| Yahoo, iCloud, AOL, GMX, Zoho, Fastmail, Yandex… | Email + an app password from that provider's security settings. The server is filled in automatically. |
| Anything else (your own domain, work mail…) | Email + password; under **Server settings**, enter the IMAP server if `imap.<your domain>` isn't right. |

## What it does

- **Groups everything by sender or domain** across all accounts, with counts, unread mail, mailing-list detection and Gmail's categories.
- **Suggests Delete / Review / Keep** for every sender, using unsubscribe headers, bulk-mail addresses, Promotions/Social categories, spam patterns and Outlook's "Other" tab.
- **Review mode** lets you go through senders one at a time with D (delete), K (keep) and S (skip) while you look at their latest email.
- **Clean up** moves everything marked Delete to each account's Trash. **In Trash** lists what was moved and can put any of it back.
- **Filters for future mail:**
  - Outlook: creates the inbox rules for you.
  - Gmail: gives you a filter file to import.
  - Other providers: gives you a list of senders to paste into their blocked list.
- **Reading pane** with the full email. Remote images (tracking pixels) are blocked until you click to show them.

## Safety

Free(up)Mail is designed so a real email can't disappear by accident:

- **People you've emailed** from any of your accounts are never suggested for deletion, never put in filters, and are skipped when you delete a whole domain.
- **Kept even from deleted senders:**
  - security and sign-in alerts
  - receipts, refunds, payslips and bookings
  - delivery updates

  You can change these under **Protection**.
- Starred/flagged emails and emails you **pin** are always kept.
- Nothing is permanently deleted. Emails go to Trash, where providers usually keep them for about 30 days.
- Each email is re-checked against the server just before it moves. If anything changed since the scan, the email is skipped.
- Restored emails are pinned automatically, so a later cleanup won't catch them again.

## Your data

Everything lives in one folder on your computer:

- Windows: `%LOCALAPPDATA%\FreeUpMail`
- macOS: `~/Library/Application Support/FreeUpMail`
- Linux: `~/.local/share/freeupmail`

To use a different folder (a USB stick, say), set `FREEUPMAIL_HOME`.

The folder holds:
- `accounts.json`: your IMAP accounts and their app passwords, stored unencrypted, as most desktop mail clients store them. This file and the token file can only be read by your user account.
- `outlook_token.json`: the Microsoft sign-in token.
- `mail.db`: a cache of email headers. Message bodies are never stored; they are fetched when you open an email.

To remove an account, use **Accounts → Remove**, then revoke its app password with the provider. To remove Free(up)Mail completely, delete the folder.

## Running from source

The app uses only the Python standard library (3.11+), so there's nothing to install:

```
python -m freeupmail              # app window, quits when closed
python -m freeupmail --browser    # in your normal browser (Ctrl+C to stop)
python -m tests.test_rules
python -m tests.test_cleanup
```

On Windows, `FreeUpMail.bat` starts it without a console window.

To build the single-file versions:

```
pip install pyinstaller
python build.py          # dist/FreeUpMail.pyz and dist/FreeUpMail(.exe)
```

Publishing a GitHub release runs `.github/workflows/release.yml`, which builds Windows, macOS and Linux versions and attaches them to the release.
