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
        shutil.rmtree(home, ignore_errors=True)
        os.environ["FREEUPMAIL_HOME"] = str(home)
        from freeupmail import demo
        demo.create()
    from freeupmail.server import main
    main()
