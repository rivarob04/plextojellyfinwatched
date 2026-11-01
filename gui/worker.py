"""Subprocess entry point used by the GUI.

The GUI never imports the sync engine into its own process. Each action runs
here in a child process so a stuck request can be stopped, loguru's global
state stays isolated, and a crash cannot take the web server down.

    python -m gui.worker validate --config config.yaml
    python -m gui.worker test     --config config.yaml
    python -m gui.worker run      --config config.yaml [--dryrun true|false]

`validate` and `test` print one JSON document on the last line of stdout.
`run` streams the normal JellyPlex-Watched log output.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _startup_error(error: Exception) -> str:
    from main import _format_startup_error

    return _format_startup_error(error)


def _load(config: str):
    from src.settings import load_settings

    return load_settings(yaml_file=config, auto_migrate=False)


def _emit(payload: dict) -> None:
    print(json.dumps(payload), flush=True)


def cmd_validate(config: str) -> int:
    try:
        settings = _load(config)
    except Exception as error:  # noqa: BLE001 - reported to the GUI
        _emit({"ok": False, "error": _startup_error(error)})
        return 1

    _emit(
        {
            "ok": True,
            "servers": [s.name for s in settings.all_servers],
            "dryrun": settings.dryrun,
        }
    )
    return 0


def _short_error(error: Exception) -> str:
    text = str(error) or type(error).__name__
    lowered = text.lower()
    if "connection refused" in lowered:
        return "Connection refused — is the server running and the URL/port right?"
    if "name or service not known" in lowered or "nodename nor servname" in lowered:
        return "Host not found — check the base URL."
    if "timed out" in lowered:
        return "Timed out — the server did not answer."
    if "401" in lowered or "unauthorized" in lowered:
        return "Unauthorized — check the token / API key."
    if "certificate" in lowered or "ssl" in lowered:
        return "SSL/certificate error — check the URL or enable SSL bypass (Plex)."
    return text if len(text) <= 200 else text[:197] + "…"


def cmd_test(config: str) -> int:
    from loguru import logger

    logger.remove()

    try:
        settings = _load(config)
    except Exception as error:  # noqa: BLE001
        _emit({"ok": False, "error": _startup_error(error), "results": []})
        return 1

    from src.emby import Emby
    from src.jellyfin import Jellyfin
    from src.plex import Plex
    from src.settings import EmbySettings, JellyfinSettings, PlexSettings

    results = []
    for server in settings.all_servers:
        entry = {"name": server.name, "type": "", "ok": False, "detail": ""}
        try:
            if isinstance(server, PlexSettings):
                entry["type"] = "plex"
                conn = Plex(app_settings=settings, server_settings=server)
                entry["detail"] = conn.info()
            elif isinstance(server, JellyfinSettings):
                entry["type"] = "jellyfin"
                conn = Jellyfin(app_settings=settings, server_settings=server)
                entry["detail"] = f"{conn.server_name} {conn.server_version}"
            elif isinstance(server, EmbySettings):
                entry["type"] = "emby"
                conn = Emby(app_settings=settings, server_settings=server)
                entry["detail"] = f"{conn.server_name} {conn.server_version}"
            entry["ok"] = True
        except Exception as error:  # noqa: BLE001
            entry["detail"] = _short_error(error)
        results.append(entry)

    _emit({"ok": all(r["ok"] for r in results), "results": results})
    return 0


def cmd_run(config: str, dryrun: str | None) -> int:
    from loguru import logger

    from src.functions import configure_logger
    from src.main import main_loop

    try:
        settings = _load(config)
    except Exception as error:  # noqa: BLE001
        print(f"Startup error: {_startup_error(error)}", flush=True)
        return 1

    update: dict = {"run_only_once": True}
    if dryrun is not None:
        update["dryrun"] = dryrun.lower() == "true"
    settings = settings.model_copy(update=update)

    configure_logger(settings.log_file, settings.debug_level)
    try:
        main_loop(settings, 100.0)
    except Exception as error:  # noqa: BLE001
        logger.error(error)
        logger.error(traceback.format_exc())
        return 1
    logger.info("Sync pass finished")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="gui.worker")
    parser.add_argument("command", choices=["validate", "test", "run"])
    parser.add_argument("--config", required=True)
    parser.add_argument("--dryrun", choices=["true", "false"])
    args = parser.parse_args()

    if args.command == "validate":
        return cmd_validate(args.config)
    if args.command == "test":
        return cmd_test(args.config)
    return cmd_run(args.config, args.dryrun)


if __name__ == "__main__":
    sys.exit(main())
