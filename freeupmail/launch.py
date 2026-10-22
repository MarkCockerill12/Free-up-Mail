"""Entry point for `python -m freeupmail`, the .pyz and the .exe.

--demo runs against a fresh folder of made-up emails (nothing real is read or changed),
so it has to pick its data folder before anything else is imported."""

import os
import shutil
import sys
import tempfile
from pathlib import Path


def run() -> None:
    if "--demo" in sys.argv:
        home = Path(tempfile.gettempdir()) / "FreeUpMail-demo"
        os.environ["FREEUPMAIL_HOME"] = str(home)
        from freeupmail import server
        if not server.already_running():  # a demo that's open already is just brought forward
            shutil.rmtree(home, ignore_errors=True)
            from freeupmail import demo
            demo.create()
    if "--after-update" in sys.argv:  # started by the copy it replaced: let that one close first
        from freeupmail import server, updater
        updater.wait_for_old_copy(server.already_running)
    from freeupmail.server import main
    main()
