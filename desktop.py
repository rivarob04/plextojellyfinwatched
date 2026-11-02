"""JellyPlex-Watched desktop app (Windows 10/11, also runs on macOS/Linux).

Starts the GUI server on a free local port and shows it in a native window
(Edge WebView2 on Windows). Configuration, logs and run history are stored in
%APPDATA%\\JellyPlex-Watched (or ~/.config/JellyPlex-Watched elsewhere).

    python desktop.py            # normal start
    python desktop.py --browser  # open in the default browser instead
    python desktop.py --smoke    # start, run one validation, exit (used by CI)
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

APP_NAME = "JellyPlex-Watched"


def data_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    path = base / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def free_port(preferred: int = 8787) -> int:
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return sock.getsockname()[1]
    raise RuntimeError("No free port available")


def has_webview2() -> bool:
    """True when the Edge WebView2 runtime is installed (Windows only)."""
    if os.name != "nt":
        return True
    import winreg

    guid = r"{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    keys = [
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{guid}"),
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{guid}"),
        (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\EdgeUpdate\Clients\{guid}"),
    ]
    for hive, path in keys:
        try:
            with winreg.OpenKey(hive, path) as key:
                version, _ = winreg.QueryValueEx(key, "pv")
                if version and version != "0.0.0.0":
                    return True
        except OSError:
            continue
    return False


def wait_until_up(url: str, timeout: float = 30) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url + "/healthz", timeout=2):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def smoke_test(url: str, folder: Path) -> int:
    """Exercise the API and a worker process; used by the Windows CI build."""
    report = {}
    try:
        with urllib.request.urlopen(url + "/api/config", timeout=10) as res:
            report["config"] = json.load(res)["path"]
        req = urllib.request.Request(
            url + "/api/validate",
            data=json.dumps(
                {
                    "data": {
                        "plex": [
                            {"name": "p", "baseurl": "http://127.0.0.1:1", "token": "x", "sync_to": ["j"]}
                        ],
                        "jellyfin": [
                            {"name": "j", "baseurl": "http://127.0.0.1:2", "token": "y", "sync_to": ["p"]}
                        ],
                    }
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as res:
            report["validate"] = json.load(res)
        ok = report["validate"].get("ok") is True
        with urllib.request.urlopen(url + "/api/notifications", timeout=10) as res:
            report["notifications"] = "on_failure" in json.load(res)
        if os.name == "nt":
            # The packaged app must carry the tray and window libraries.
            import pystray  # noqa: F401
            import webview  # noqa: F401
            from PIL import Image  # noqa: F401

            report["tray"] = True
            report["autostart_command"] = autostart_command()
        ok = ok and report["notifications"]
    except Exception as error:  # noqa: BLE001
        report["error"] = repr(error)
        ok = False
    (folder / "smoke-result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# Start with Windows (HKCU Run key) and a single running instance
# ---------------------------------------------------------------------------
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart_command() -> str | None:
    if os.name != "nt":
        return None
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimized'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return f'"{pythonw}" "{Path(__file__).resolve()}" --minimized'


def get_autostart() -> bool:
    if os.name != "nt":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except OSError:
        return False


def set_autostart(enabled: bool) -> None:
    if os.name != "nt":
        raise RuntimeError("Start with Windows is only available on Windows")
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass


def existing_instance(folder: Path) -> bool:
    """If the app is already running, bring its window forward and return True."""
    marker = folder / "instance.json"
    try:
        port = json.loads(marker.read_text(encoding="utf-8"))["port"]
        url = f"http://127.0.0.1:{port}"
        with urllib.request.urlopen(url + "/healthz", timeout=2):
            pass
        req = urllib.request.Request(url + "/api/desktop/show", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=3).close()
        return True
    except Exception:  # noqa: BLE001 - not running (or stale marker)
        return False


def tray_image():
    from PIL import Image, ImageDraw

    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([2, 2, size - 2, size - 2], radius=14, fill=(229, 160, 13, 255))
    draw.line([(17, 33), (28, 44), (48, 22)], fill=(26, 20, 0, 255), width=8, joint="curve")
    return img


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    # A windowed .exe has no console; give libraries somewhere to write.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

    folder = data_dir()
    smoke = "--smoke" in sys.argv
    if not smoke and existing_instance(folder):
        return 0

    os.environ.setdefault("YAML_FILE", str(folder / "config.yaml"))
    os.environ.setdefault("GUI_DATA_DIR", str(folder))
    os.environ.setdefault("GUI_WORK_DIR", str(folder))
    os.environ["GUI_DESKTOP"] = "1"
    os.environ.pop("GUI_PASSWORD", None)  # local-only, no prompt needed
    os.chdir(folder)

    import uvicorn

    from gui.app import DESKTOP_HOOKS, app, runner

    port = free_port()
    url = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", log_config=None)
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    if not wait_until_up(url):
        return 1

    if smoke:
        try:
            return smoke_test(url, folder)
        finally:
            runner.stop()
            server.should_exit = True

    try:
        (folder / "instance.json").write_text(json.dumps({"port": port}), encoding="utf-8")
    except OSError:
        pass

    state = {"window": None, "quitting": False, "tray": None, "told_tray": False}
    quit_event = threading.Event()

    def options() -> dict:
        saved = runner.extra.get("desktop", {})
        return {
            "close_to_tray": saved.get("close_to_tray", True),
            "start_minimized": saved.get("start_minimized", False),
            "start_with_windows": get_autostart(),
            "autostart_supported": os.name == "nt",
        }

    def set_options(new: dict) -> dict:
        runner.extra["desktop"] = {
            "close_to_tray": bool(new.get("close_to_tray", True)),
            "start_minimized": bool(new.get("start_minimized", False)),
        }
        runner._save_state()
        if os.name == "nt" and bool(new.get("start_with_windows")) != get_autostart():
            set_autostart(bool(new.get("start_with_windows")))
        return options()

    def show() -> None:
        window = state["window"]
        if window is not None:
            window.show()
            try:
                window.restore()
            except Exception:  # noqa: BLE001 - older pywebview
                pass
        else:
            import webbrowser

            webbrowser.open(url)

    def toast(title: str, message: str) -> None:
        icon = state["tray"]
        if icon is not None:
            try:
                icon.notify(message, title)
            except Exception:  # noqa: BLE001
                pass

    def on_run_finished(run: dict) -> None:
        cfg = runner.notifications
        status = run.get("status")
        total = (run.get("summary") or {}).get("total", 0)
        if status == "failed" and cfg.get("on_failure", True):
            toast("Sync failed", "Open JellyPlex-Watched to see the log.")
        elif status == "success" and total and (not run.get("dryrun") or cfg.get("include_dryrun")):
            from gui.notify import compose

            toast("Sync finished", compose(run)[0].replace("JellyPlex-Watched: ", "").capitalize() + ".")

    def run_now() -> None:
        try:
            runner.start(runner.schedule.get("dryrun", True), "tray")
        except Exception as error:  # noqa: BLE001
            toast("Could not start sync", str(getattr(error, "detail", error)))

    def quit_app() -> None:
        state["quitting"] = True
        quit_event.set()
        if state["tray"] is not None:
            state["tray"].stop()
        if state["window"] is not None:
            state["window"].destroy()

    DESKTOP_HOOKS.update(
        get_options=options,
        set_options=set_options,
        show=show,
        on_run_finished=on_run_finished,
    )

    # System tray icon (Windows; skipped quietly where unsupported).
    try:
        import pystray

        def status_text(_item) -> str:
            last = runner.history[-1] if runner.history else None
            if runner.running:
                return "Sync running…"
            if not last:
                return "No syncs yet"
            return f"Last sync: {last.get('status')} ({last.get('finished', '')[11:16]})"

        mode = lambda _i: "Run sync now (dry run)" if runner.schedule.get("dryrun", True) else "Run sync now (live)"  # noqa: E731
        icon = pystray.Icon(
            APP_NAME,
            tray_image(),
            APP_NAME,
            menu=pystray.Menu(
                pystray.MenuItem("Open", lambda: show(), default=True),
                pystray.MenuItem(mode, lambda: run_now()),
                pystray.MenuItem(status_text, None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit", lambda: quit_app()),
            ),
        )
        icon.run_detached()
        state["tray"] = icon
    except Exception:  # noqa: BLE001
        state["tray"] = None

    start_hidden = "--minimized" in sys.argv or options()["start_minimized"]
    try:
        if "--browser" not in sys.argv and has_webview2():
            try:
                import webview

                window = webview.create_window(
                    APP_NAME, url, width=1280, height=880, min_size=(900, 600),
                    hidden=start_hidden and state["tray"] is not None,
                )
                state["window"] = window

                def on_closing():
                    if state["quitting"] or state["tray"] is None or not options()["close_to_tray"]:
                        return True
                    window.hide()
                    if not state["told_tray"]:
                        state["told_tray"] = True
                        toast(APP_NAME, "Still running in the tray. Right-click the icon to quit.")
                    return False

                window.events.closing += on_closing
                webview.start()
                return 0
            except Exception:  # noqa: BLE001 - fall back to the browser
                state["window"] = None

        if not start_hidden:
            import webbrowser

            webbrowser.open(url)
        while thread.is_alive() and not quit_event.is_set():
            time.sleep(1)
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        state["quitting"] = True
        if state["tray"] is not None:
            try:
                state["tray"].stop()
            except Exception:  # noqa: BLE001
                pass
        runner.stop()
        server.should_exit = True
        try:
            (folder / "instance.json").unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
