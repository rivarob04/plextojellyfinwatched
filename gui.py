"""Launch the JellyPlex-Watched web GUI.

    python gui.py                    # http://127.0.0.1:8787
    GUI_HOST=0.0.0.0 python gui.py   # listen on the network (set GUI_PASSWORD!)

Environment:
    YAML_FILE      config file to edit and run (default: config.yaml)
    GUI_HOST       bind address (default 127.0.0.1)
    GUI_PORT       port (default 8787)
    GUI_PASSWORD   if set, the GUI asks for this password (any username)
    GUI_NO_BROWSER set to 1 to skip opening a browser tab
"""

import os
import sys
import threading
import webbrowser

if __name__ == "__main__":
    if sys.version_info < (3, 12):
        print("This script requires Python 3.12 or higher")
        sys.exit(1)

    import uvicorn

    host = os.environ.get("GUI_HOST", "127.0.0.1")
    port = int(os.environ.get("GUI_PORT", "8787"))

    if host not in ("127.0.0.1", "localhost", "::1") and not os.environ.get(
        "GUI_PASSWORD"
    ):
        print(
            "WARNING: the GUI is reachable from the network without a password. "
            "It shows your server tokens, so set GUI_PASSWORD.",
            file=sys.stderr,
        )

    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}"
    print(f"JellyPlex-Watched GUI running at {url}")
    if os.environ.get("GUI_NO_BROWSER") != "1":
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    uvicorn.run("gui.app:app", host=host, port=port, log_level="warning")
