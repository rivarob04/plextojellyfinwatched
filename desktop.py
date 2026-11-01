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
    except Exception as error:  # noqa: BLE001
        report["error"] = repr(error)
        ok = False
    (folder / "smoke-result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if ok else 1


def main() -> int:
    # A windowed .exe has no console; give libraries somewhere to write.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

    folder = data_dir()
    os.environ.setdefault("YAML_FILE", str(folder / "config.yaml"))
    os.environ.setdefault("GUI_DATA_DIR", str(folder))
    os.environ.setdefault("GUI_WORK_DIR", str(folder))
    os.environ.pop("GUI_PASSWORD", None)  # local-only, no prompt needed
    os.chdir(folder)

    import uvicorn

    from gui.app import app, runner

    port = free_port()
    url = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", log_config=None)
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    if not wait_until_up(url):
        return 1

    try:
        if "--smoke" in sys.argv:
            return smoke_test(url, folder)

        if "--browser" not in sys.argv and has_webview2():
            try:
                import webview

                webview.create_window(
                    APP_NAME, url, width=1280, height=880, min_size=(900, 600)
                )
                webview.start()
                return 0
            except Exception:  # noqa: BLE001 - fall back to the browser
                pass

        import webbrowser

        webbrowser.open(url)
        while thread.is_alive():
            time.sleep(1)
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        runner.stop()
        server.should_exit = True


if __name__ == "__main__":
    sys.exit(main())
