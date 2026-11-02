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


def _connect(kind: str, app_settings, server_settings):
    from src.emby import Emby
    from src.jellyfin import Jellyfin
    from src.plex import Plex

    cls = {"plex": Plex, "jellyfin": Jellyfin, "emby": Emby}[kind]
    return cls(app_settings=app_settings, server_settings=server_settings)


def _discover(kind: str, conn) -> tuple[list[str], list[dict]]:
    """Return (usernames, libraries) as the server reports them."""
    if kind == "plex":
        users = sorted(
            {(u.username or u.title) for u in conn.users if (u.username or u.title)},
            key=str.casefold,
        )
    else:
        users = sorted(conn.users.keys(), key=str.casefold)
    libraries = [
        {"name": name, "type": lib_type}
        for name, lib_type in sorted(conn.get_libraries().items(), key=lambda x: x[0].casefold())
    ]
    return users, libraries


def cmd_test(config: str) -> int:
    """Connect to every server on its own and list its users and libraries.

    Works even when the config as a whole is not valid yet (for example only
    one server added so far), so servers can be checked as they are added.
    """
    import yaml
    from loguru import logger
    from pydantic import ValidationError

    from src.settings import AppSettings, EmbySettings, JellyfinSettings, PlexSettings

    logger.remove()

    try:
        raw = yaml.safe_load(Path(config).read_text(encoding="utf-8")) or {}
    except Exception as error:  # noqa: BLE001
        _emit({"ok": False, "error": _startup_error(error), "results": []})
        return 1

    knobs = {
        k: raw[k]
        for k in ("request_timeout", "max_threads", "generate_guids", "generate_locations")
        if k in raw
    }
    try:
        app_settings = _load(config)
    except Exception:  # noqa: BLE001 - fall back to defaults for discovery
        app_settings = AppSettings.model_construct(**knobs)

    models = {"plex": PlexSettings, "jellyfin": JellyfinSettings, "emby": EmbySettings}
    results = []
    for kind, model in models.items():
        for item in raw.get(kind) or []:
            name = (item or {}).get("name") or "(unnamed)"
            entry = {"name": name, "type": kind, "ok": False, "detail": "",
                     "users": [], "libraries": []}
            try:
                server_settings = model.model_validate(item)
            except ValidationError as error:
                problems = "; ".join(
                    f"{'.'.join(str(p) for p in d['loc']) or 'server'}: {d['msg']}"
                    for d in error.errors(include_context=False, include_input=False)
                )
                entry["detail"] = "Config: " + problems
                results.append(entry)
                continue
            try:
                conn = _connect(kind, app_settings, server_settings)
                entry["detail"] = (
                    conn.info() if kind == "plex"
                    else f"{conn.server_name} {conn.server_version}"
                )
                entry["ok"] = True
            except Exception as error:  # noqa: BLE001
                entry["detail"] = _short_error(error)
                results.append(entry)
                continue
            try:
                entry["users"], entry["libraries"] = _discover(kind, conn)
            except Exception as error:  # noqa: BLE001
                entry["detail"] += f" (could not list users/libraries: {_short_error(error)})"
            results.append(entry)

    _emit({"ok": bool(results) and all(r["ok"] for r in results), "results": results})
    return 0


CHANGE_PREFIX = "@@JPW-CHANGE@@ "


def _capture_changes(dryrun: bool) -> None:
    """Report every item the engine marks (or would mark) to the GUI.

    The engine calls log_marked() once per changed item in both dry and live
    runs. We wrap it so each change is also printed as one JSON line the GUI
    turns into the preview table. On dry runs the mark file is left alone so
    it only ever lists real changes.
    """
    import src.functions
    import src.jellyfin_emby
    import src.plex

    original = src.functions.log_marked

    def wrapped(server_type, server_name, username, library, movie_show,
                episode=None, duration=None, mark_file=None, **kwargs):
        event = {
            "server_type": server_type,
            "server": server_name,
            "user": username,
            "library": library,
            "title": movie_show,
            "episode": episode,
            "minutes": round(duration / 60_000) if duration else None,
            "kind": "partial" if duration else "watched",
            "dryrun": dryrun,
        }
        print(CHANGE_PREFIX + json.dumps(event, ensure_ascii=False), flush=True)
        if not dryrun:
            extra = {"mark_file": mark_file} if mark_file is not None else {}
            original(server_type, server_name, username, library, movie_show,
                     episode, duration, **extra, **kwargs)

    for module in (src.plex, src.jellyfin_emby):
        module.log_marked = wrapped


def _run_watchlists(settings, spec_path: str, dryrun: bool) -> int:
    """Plex watchlist -> Jellyfin/Emby favorites. Returns the error count."""
    from loguru import logger

    from gui import watchlist
    from src.emby import Emby
    from src.jellyfin import Jellyfin
    from src.settings import EmbySettings, JellyfinSettings

    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    links = [link for link in spec.get("links", []) if link.get("enabled", True)]
    if not links:
        return 0
    wanted = {t.get("server") for link in links for t in link.get("targets") or []}

    connections = {}
    errors = 0
    for server in settings.all_servers:
        if server.name not in wanted:
            continue
        try:
            if isinstance(server, JellyfinSettings):
                connections[server.name] = Jellyfin(app_settings=settings, server_settings=server)
            elif isinstance(server, EmbySettings):
                connections[server.name] = Emby(app_settings=settings, server_settings=server)
        except Exception as error:  # noqa: BLE001
            logger.error(f"Watchlist: could not connect to {server.name}: {error}")
            errors += 1

    def log(level: str, message: str) -> None:
        (logger.error if level == "ERROR" else logger.info)(message)

    def emit(event: dict) -> None:
        print(CHANGE_PREFIX + json.dumps(event, ensure_ascii=False), flush=True)

    logger.info(f"Watchlist: syncing {len(links)} linked Plex account(s){' [DRYRUN]' if dryrun else ''}")
    errors += watchlist.run(
        links, connections, Path(spec["state_path"]), dryrun,
        bool(spec.get("remove", True)), emit, log,
    )
    logger.info("Watchlist: finished")
    return errors


def cmd_run(config: str, dryrun: str | None, job: str = "sync", watchlist_spec: str | None = None) -> int:
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
    failed = False
    if job in ("sync", "both"):
        _capture_changes(settings.dryrun)
        try:
            main_loop(settings, 100.0)
            logger.info("Sync pass finished")
        except Exception as error:  # noqa: BLE001
            logger.error(error)
            logger.error(traceback.format_exc())
            failed = True
    if job in ("watchlist", "both") and watchlist_spec:
        try:
            if _run_watchlists(settings, watchlist_spec, settings.dryrun):
                failed = failed or job == "watchlist"
        except Exception as error:  # noqa: BLE001
            logger.error(f"Watchlist: {error}")
            logger.error(traceback.format_exc())
            failed = True
    return 1 if failed else 0


def main() -> int:
    # Windows consoles/pipes default to a legacy code page; the GUI reads UTF-8.
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    parser = argparse.ArgumentParser(prog="gui.worker")
    parser.add_argument("command", choices=["validate", "test", "run"])
    parser.add_argument("--config", required=True)
    parser.add_argument("--dryrun", choices=["true", "false"])
    parser.add_argument("--job", choices=["sync", "watchlist", "both"], default="sync")
    parser.add_argument("--watchlist")
    args = parser.parse_args()

    if args.command == "validate":
        return cmd_validate(args.config)
    if args.command == "test":
        return cmd_test(args.config)
    return cmd_run(args.config, args.dryrun, args.job, args.watchlist)


if __name__ == "__main__":
    sys.exit(main())
