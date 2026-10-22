"""Free(up)Mail's own window: a native window inside the app itself (WebView2 on Windows,
WebKit on macOS) when pywebview is bundled, as it is in the release builds. Nothing else
opens: no browser, no browser profile, no extensions. Closing it closes the app.

Running from source without pywebview installed, server.py falls back to opening the
page in an Edge/Chrome app window instead.
"""

import os
import threading

_window = None
_ready = threading.Event()


def available() -> bool:
    try:
        import webview  # noqa: F401
    except Exception:  # noqa: BLE001 - missing, or its native parts can't load: use the fallback
        return False
    return True


def run(url: str, bounds: tuple[int, int, int, int], storage: str, engine_args: str = "") -> bool:
    """Show the window at bounds (x, y, width, height) and block until it is closed.
    False if it could not be shown. engine_args trims the WebView2 engine (Windows only)."""
    global _window
    if engine_args:
        os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = engine_args
    import webview

    webview.settings["ALLOW_DOWNLOADS"] = True  # filter files, backups
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True  # unsubscribe / webmail links
    try:
        _window = webview.create_window(
            "Free(up)Mail", url, x=bounds[0], y=bounds[1], width=bounds[2], height=bounds[3], min_size=(760, 520),
            text_select=True, zoomable=True, background_color="#ECE9D8",
        )
        _window.events.shown += _ready.set
        webview.start(private_mode=False, storage_path=storage)  # keeps theme & layout between runs
    except Exception as exc:  # noqa: BLE001 - e.g. no WebView2 runtime: caller falls back
        print(f"native window unavailable: {exc!r}", flush=True)
        return _ready.is_set()  # it did open (and was closed) if "shown" fired
    return True


def close() -> None:
    """Close the window (which stops the app), e.g. to restart into an update."""
    if _window is not None:
        _window.destroy()


def bring_to_front() -> bool:
    """Called when Free(up)Mail is started again while already open."""
    if _window is None or not _ready.is_set():
        return False
    _window.restore()
    _window.show()
    _window.on_top = True  # Windows only raises a window above others this way
    _window.on_top = False
    return True
