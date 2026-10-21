"""Routes that start background jobs touching mailboxes: scan, cleanup, restore, rules,
Outlook sign-in and Outlook server rules. Permissions are enforced again inside
providers.Mailbox; the checks here only give a quicker, clearer answer."""

from . import api, cleanup, filters, outlook, perms, providers, store


def scan(body: dict) -> dict:
    only = body.get("account")

    def run(log):
        db = store.connect()
        scanned = set()
        for email in providers.accounts():
            if only and email != only:
                continue
            box = providers.Mailbox(db, email)
            if not box.can("scan"):
                log(f"{email}: skipped - listing emails is not allowed for this account (Accounts → Permissions)")
                continue
            if box.scan(log):
                scanned.add(email)
        if scanned and store.get_settings(db, {"auto_rules": "1"})["auto_rules"] == "1":
            cleanup.run_rules(None, log, accounts=scanned, auto_only=True)

    return api.start("scan", run)


def run_cleanup(body: dict) -> dict:
    if not body.get("confirm"):
        return {"error": "not confirmed"}
    db = store.connect()
    for account in body.get("allow") or []:  # ticked in the Clean up window
        if account in providers.accounts():
            perms.grant(db, account, "trash")
            if body.get("allow_read"):
                perms.grant(db, account, "read")
    return api.start("cleanup", lambda log: cleanup.run_cleanup(body.get("account") or None, log))


def restore(body: dict) -> dict:
    items = body.get("items") or []
    if body.get("all"):
        db = store.connect()
        query = "SELECT account, msg_id FROM trashed WHERE restored = 0"
        params: tuple = ()
        if body.get("account"):
            query, params = query + " AND account = ?", (body["account"],)
        items = [list(r) for r in db.execute(query, params)]
    if not items:
        return {"error": "Nothing to restore."}
    return api.start("restore", lambda log: cleanup.run_restore(items, log))


def run_rules(body: dict) -> dict:
    ids = [int(i) for i in body.get("ids") or []] or None
    return api.start("rules", lambda log: cleanup.run_rules(ids, log))


def outlook_connect(body: dict) -> dict:
    if api.JOB.running:
        return {"error": "Another task is still running."}
    if store.load_config().get("demo"):
        return {"error": providers.DEMO_TEXT}
    try:
        flow = outlook.start_sign_in()
    except RuntimeError as exc:
        return {"error": str(exc)}
    wanted = [p for p in body.get("perms") or perms.DEFAULT_NEW if p in perms.PERMS]

    def run(log):
        if outlook.finish_sign_in(flow, log):
            db = store.connect()
            email = providers.outlook_email()
            perms.set_grants(db, email, wanted)
            if "scan" in wanted:
                providers.Mailbox(db, email).scan(log)

    api.JOB.start("Outlook sign-in", run)
    return {"user_code": flow["user_code"], "verification_uri": flow["verification_uri"]}


def outlook_rules(_: dict) -> dict:
    if not outlook.connected():
        return {"error": "Outlook is not connected"}
    db = store.connect()
    if not perms.allowed(db, outlook.connected()["email"], "server_rules"):
        return {"error": "Creating rules in Outlook is not allowed yet. Tick it under Accounts → Permissions."}
    return api.start("Outlook rules", lambda log: filters.outlook_rules(db, log))


ROUTES = {
    "/api/scan": scan,
    "/api/cleanup": run_cleanup,
    "/api/restore": restore,
    "/api/rules/run": run_rules,
    "/api/outlook/connect": outlook_connect,
    "/api/outlook/rules": outlook_rules,
}
api.POST_ROUTES.update(ROUTES)
