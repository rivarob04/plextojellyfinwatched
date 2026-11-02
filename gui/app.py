"""FastAPI backend for the JellyPlex-Watched GUI."""

from __future__ import annotations

import base64
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from gui import notify

ROOT = Path(__file__).resolve().parent.parent
FROZEN = getattr(sys, "frozen", False)
# Directory the sync engine runs in (relative log/mark paths resolve here).
WORK_DIR = Path(os.environ.get("GUI_WORK_DIR", ROOT)).resolve()
# Hide the console window of child processes on Windows.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
STATIC = Path(__file__).resolve().parent / "static"

CONFIG_PATH = Path(os.environ.get("YAML_FILE", "config.yaml")).resolve()
STATE_PATH = Path(
    os.environ.get("GUI_STATE_FILE", CONFIG_PATH.parent / "gui_state.json")
).resolve()
DISCOVERY_PATH = STATE_PATH.with_name("gui_discovery.json")
RUNS_DIR = STATE_PATH.with_name("gui_runs")
WATCHLIST_STATE = STATE_PATH.with_name("gui_watchlist_state.json")
CHANGE_PREFIX = "@@JPW-CHANGE@@ "
DESKTOP = os.environ.get("GUI_DESKTOP") == "1"
GUI_PASSWORD = os.environ.get("GUI_PASSWORD", "")
# In Docker, keep logs next to the config so they survive container updates.
DATA_DIR = os.environ.get("GUI_DATA_DIR", "").rstrip("/")

DEFAULT_CONFIG: dict[str, Any] = {
    "dryrun": True,
    "debug_level": "INFO",
    "run_only_once": False,
    "sleep_duration": 3600,
    "log_file": f"{DATA_DIR}/log.log" if DATA_DIR else "log.log",
    "mark_file": f"{DATA_DIR}/mark.log" if DATA_DIR else "mark.log",
    "request_timeout": 300,
    "max_threads": 1,
    "generate_guids": True,
    "generate_locations": True,
    "blacklist_users": [],
    "whitelist_users": [],
    "blacklist_libraries": [],
    "whitelist_libraries": [],
    "blacklist_library_types": ["music", "photos"],
    "whitelist_library_types": ["movie", "show", "movies", "tvshows"],
    "plex": [],
    "jellyfin": [],
    "emby": [],
    "user_mappings": [],
    "library_mappings": [],
    "user_sync_rules": [],
    "library_sync_rules": [],
}


# --------------------------------------------------------------------------
# Config file helpers
# --------------------------------------------------------------------------
class _Dumper(yaml.SafeDumper):
    pass


def _str_presenter(dumper: yaml.SafeDumper, value: str):
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _str_presenter)


def dump_yaml(data: dict) -> str:
    header = (
        "# JellyPlex-Watched configuration — managed by the GUI.\n"
        "# Hand edits are fine; comments are not preserved when saving from forms.\n"
    )
    return header + yaml.dump(
        data, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=1000
    )


def read_config() -> tuple[dict, str, bool]:
    if not CONFIG_PATH.exists():
        return json.loads(json.dumps(DEFAULT_CONFIG)), "", False
    raw = CONFIG_PATH.read_text(encoding="utf-8")
    data = yaml.safe_load(raw) or {}
    if not isinstance(data, dict):
        raise HTTPException(400, "config.yaml does not contain a mapping")
    return data, raw, True


def write_config_text(text: str) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if CONFIG_PATH.exists():
        shutil.copy2(CONFIG_PATH, CONFIG_PATH.with_suffix(CONFIG_PATH.suffix + ".bak"))
    tmp = CONFIG_PATH.with_suffix(CONFIG_PATH.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, CONFIG_PATH)


def clean(data: Any) -> Any:
    """Drop empty optional server fields so the loader sees them as unset."""
    if isinstance(data, dict):
        return {k: clean(v) for k, v in data.items() if v is not None}
    if isinstance(data, list):
        return [clean(v) for v in data]
    return data


def normalise_servers(data: dict) -> dict:
    for kind in ("plex", "jellyfin", "emby"):
        servers = []
        for server in data.get(kind) or []:
            server = {k: v for k, v in server.items() if v not in ("", None)}
            servers.append(server)
        data[kind] = servers
    return clean(data)


# --------------------------------------------------------------------------
# Worker subprocesses
# --------------------------------------------------------------------------
def worker_cmd(*args: str) -> list[str]:
    if FROZEN:
        # Packaged Windows app: a separate console-mode worker executable
        # sits next to the GUI executable.
        exe = Path(sys.executable).with_name(
            "jpw-worker.exe" if os.name == "nt" else "jpw-worker"
        )
        return [str(exe), *args]
    return [sys.executable, "-u", "-m", "gui.worker", *args]


def run_worker_json(command: str, data: dict | None, timeout: int = 600) -> dict:
    """Run validate/test against `data` (unsaved edits) or the saved config."""
    path = CONFIG_PATH
    tmp_name = None
    if data is not None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".gui-check-", suffix=".yaml", dir=CONFIG_PATH.parent
        )
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(dump_yaml(normalise_servers(data)))
        path = Path(tmp_name)
    try:
        proc = subprocess.run(
            worker_cmd(command, "--config", str(path)),
            cwd=WORK_DIR,
            capture_output=True,
            creationflags=NO_WINDOW,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=_child_env(),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"{command} timed out after {timeout}s"}
    finally:
        if tmp_name:
            Path(tmp_name).unlink(missing_ok=True)

    lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
    for line in reversed(lines):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    tail = (proc.stderr or proc.stdout or "no output").strip()[-1500:]
    return {"ok": False, "error": tail}


def _child_env() -> dict:
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if not FROZEN:
        # The worker may run from the data folder, so point Python at the code.
        env["PYTHONPATH"] = os.pathsep.join(
            p for p in (str(ROOT), env.get("PYTHONPATH", "")) if p
        )
    env.pop("YAML_FILE", None)  # the worker always gets --config explicitly
    return env


# --------------------------------------------------------------------------
# Sync runner + scheduler
# --------------------------------------------------------------------------
def summarize(changes: list[dict]) -> dict:
    """Totals per user + destination server, for the preview and notifications."""
    groups: dict[tuple[str, str], dict] = {}
    missing = 0
    for c in changes:
        kind = c.get("kind") or "watched"
        if kind == "missing":
            missing += 1
            continue
        key = (c.get("user") or "?", c.get("server") or "?")
        row = groups.setdefault(
            key,
            {"user": key[0], "server": key[1], "watched": 0, "partial": 0,
             "favorite": 0, "unfavorite": 0, "libraries": set()},
        )
        row[kind if kind in row else "watched"] += 1
        row["libraries"].add(c.get("library") or "?")
    rows = sorted(groups.values(), key=lambda r: -(r["watched"] + r["partial"] + r["favorite"]))
    for row in rows:
        row["libraries"] = sorted(row["libraries"])
    return {
        "total": sum(r["watched"] + r["partial"] + r["favorite"] + r["unfavorite"] for r in rows),
        "watched": sum(r["watched"] for r in rows),
        "partial": sum(r["partial"] for r in rows),
        "favorite": sum(r["favorite"] for r in rows),
        "unfavorite": sum(r["unfavorite"] for r in rows),
        "missing": missing,
        "by_user": rows,
    }


def save_run(run: dict, changes: list[dict]) -> None:
    try:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        (RUNS_DIR / f"{run['id']}.json").write_text(
            json.dumps({"run": run, "changes": changes}, ensure_ascii=False), encoding="utf-8"
        )
        for old in sorted(RUNS_DIR.glob("*.json"))[:-30]:
            old.unlink(missing_ok=True)
    except OSError:
        pass


class Runner:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self.lines: deque[str] = deque(maxlen=5000)
        self.base = 0  # absolute index of self.lines[0]
        self.current: dict | None = None
        self.history: list[dict] = []
        self.schedule = {"enabled": False, "interval_minutes": 60, "dryrun": True}
        self.notifications: dict = notify.settings_with_defaults(None)
        self.extra: dict = {}  # other GUI preferences (plex client id, desktop opts…)
        self.changes: list[dict] = []
        self.next_run: float | None = None
        self._load_state()
        self._reschedule()
        threading.Thread(target=self._scheduler, daemon=True).start()

    # -- persistence -------------------------------------------------------
    def _load_state(self) -> None:
        try:
            state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            self.schedule.update(state.get("schedule", {}))
            self.history = state.get("history", [])[-50:]
            self.notifications = notify.settings_with_defaults(state.get("notifications"))
            self.extra = state.get("extra", {})
        except (OSError, ValueError):
            pass

    def _save_state(self) -> None:
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            STATE_PATH.write_text(
                json.dumps(
                    {
                        "schedule": self.schedule,
                        "history": self.history[-50:],
                        "notifications": self.notifications,
                        "extra": self.extra,
                    }
                ),
                encoding="utf-8",
            )
        except OSError:
            pass

    # -- runs --------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def watchlist_settings(self) -> dict:
        wl = self.extra.setdefault("watchlist", {})
        wl.setdefault("enabled", False)
        wl.setdefault("with_sync", True)
        wl.setdefault("remove", True)
        wl.setdefault("links", [])
        return wl

    def _write_watchlist_spec(self) -> str | None:
        wl = self.watchlist_settings()
        links = [link for link in wl["links"] if link.get("enabled", True) and link.get("targets")]
        if not wl["enabled"] or not links:
            return None
        spec = {"links": links, "remove": wl["remove"], "state_path": str(WATCHLIST_STATE)}
        path = STATE_PATH.with_name(f".gui-watchlist-{secrets.token_hex(6)}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(spec, fh)
        return str(path)

    def start(self, dryrun: bool | None, trigger: str, job: str | None = None) -> dict:
        with self.lock:
            if self.running:
                raise HTTPException(409, "A sync is already running")
            if not CONFIG_PATH.exists():
                raise HTTPException(400, "Save a configuration first")
            wl = self.watchlist_settings()
            if job is None:
                job = "both" if wl["enabled"] and wl["with_sync"] else "sync"
            spec = self._write_watchlist_spec() if job in ("both", "watchlist") else None
            if job == "watchlist" and not spec:
                raise HTTPException(400, "Turn on watchlist sync and link a Plex account with a target user first")
            if job == "both" and not spec:
                job = "sync"
            self.spec_path = spec
            args = ["run", "--config", str(CONFIG_PATH), "--job", job]
            if spec:
                args += ["--watchlist", spec]
            if dryrun is not None:
                args += ["--dryrun", "true" if dryrun else "false"]
            self.base += len(self.lines)
            self.lines.clear()
            self.current = {
                "id": int(time.time() * 1000),
                "started": datetime.now().isoformat(timespec="seconds"),
                "trigger": trigger,
                "dryrun": dryrun,
                "status": "running",
                "errors": 0,
                "marked": 0,
                "job": job,
            }
            self.changes = []
            self.proc = subprocess.Popen(
                worker_cmd(*args),
                cwd=WORK_DIR,
                creationflags=NO_WINDOW,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=_child_env(),
            )
            threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()
            return self.current

    def _pump(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            with self.lock:
                if line.startswith(CHANGE_PREFIX):
                    try:
                        change = json.loads(line[len(CHANGE_PREFIX):])
                    except ValueError:
                        continue
                    if len(self.changes) < 50000:
                        self.changes.append(change)
                    if self.current and change.get("kind") != "missing":
                        self.current["marked"] += 1
                        if self.current.get("dryrun") is None:
                            self.current["dryrun"] = change.get("dryrun")
                    continue
                self.lines.append(line)
                if self.current and ("| ERROR" in line or "Startup error" in line):
                    self.current["errors"] += 1
        code = proc.wait()
        if getattr(self, "spec_path", None):
            Path(self.spec_path).unlink(missing_ok=True)
            self.spec_path = None
        with self.lock:
            if not self.current:
                return
            if self.current["status"] == "stopping":
                self.current["status"] = "stopped"
            else:
                self.current["status"] = "success" if code == 0 else "failed"
            finished = datetime.now()
            self.current["finished"] = finished.isoformat(timespec="seconds")
            self.current["exit_code"] = code
            secs = int((finished - datetime.fromisoformat(self.current["started"])).total_seconds())
            self.current["duration"] = f"{secs // 60}m {secs % 60}s" if secs >= 60 else f"{secs}s"
            self.current["summary"] = summarize(self.changes)
            run = dict(self.current)
            changes = list(self.changes)
            self.history.append(run)
            self._save_state()
        save_run(run, changes)
        hook = DESKTOP_HOOKS.get("on_run_finished")
        if hook:
            try:
                hook(run)
            except Exception:  # noqa: BLE001
                pass
        if notify.should_notify(self.notifications, run):
            threading.Thread(
                target=notify.send_all, args=(self.notifications, run), daemon=True
            ).start()

    def stop(self) -> None:
        with self.lock:
            if not self.running or self.proc is None:
                return
            if self.current:
                self.current["status"] = "stopping"
            self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    def log_since(self, offset: int) -> dict:
        with self.lock:
            start = max(offset - self.base, 0)
            lines = list(self.lines)[start:]
            return {
                "offset": self.base + len(self.lines),
                "lines": lines,
                "reset": offset < self.base,
                "running": self.running,
                "current": self.current,
            }

    # -- schedule ----------------------------------------------------------
    def set_schedule(self, enabled: bool, interval_minutes: int, dryrun: bool) -> None:
        with self.lock:
            self.schedule = {
                "enabled": enabled,
                "interval_minutes": max(1, interval_minutes),
                "dryrun": dryrun,
            }
            self._save_state()
        self._reschedule()

    def _reschedule(self) -> None:
        if self.schedule["enabled"]:
            self.next_run = time.time() + self.schedule["interval_minutes"] * 60
        else:
            self.next_run = None

    def _scheduler(self) -> None:
        while True:
            time.sleep(5)
            if self.next_run and time.time() >= self.next_run:
                self._reschedule()
                if not self.running:
                    try:
                        self.start(self.schedule["dryrun"], "schedule")
                    except HTTPException:
                        pass


runner = Runner()

# --------------------------------------------------------------------------
# HTTP app
# --------------------------------------------------------------------------
app = FastAPI(title="JellyPlex-Watched GUI", docs_url=None, redoc_url=None)


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if GUI_PASSWORD and request.url.path != "/healthz":
        header = request.headers.get("authorization", "")
        ok = False
        if header.startswith("Basic "):
            try:
                _, _, password = base64.b64decode(header[6:]).decode().partition(":")
                ok = secrets.compare_digest(password, GUI_PASSWORD)
            except (ValueError, UnicodeDecodeError):
                ok = False
        if not ok:
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="JellyPlex-Watched"'},
            )
    return await call_next(request)


class ConfigBody(BaseModel):
    data: dict


class RawBody(BaseModel):
    raw: str


class RunBody(BaseModel):
    dryrun: bool | None = None


class ScheduleBody(BaseModel):
    enabled: bool
    interval_minutes: int = 60
    dryrun: bool = True


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/config")
def get_config():
    data, raw, exists = read_config()
    return {
        "data": data,
        "raw": raw or dump_yaml(data),
        "exists": exists,
        "path": str(CONFIG_PATH),
    }


@app.put("/api/config")
def put_config(body: ConfigBody):
    text = dump_yaml(normalise_servers(body.data))
    write_config_text(text)
    return {"ok": True, "raw": text}


@app.put("/api/config/raw")
def put_raw(body: RawBody):
    try:
        data = yaml.safe_load(body.raw) or {}
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        raise HTTPException(400, f"Invalid YAML{where}") from error
    if not isinstance(data, dict):
        raise HTTPException(400, "The YAML must be a mapping at the top level")
    write_config_text(body.raw)
    return {"ok": True, "data": data}


@app.post("/api/validate")
def validate(body: ConfigBody | None = None):
    return run_worker_json("validate", body.data if body else None, timeout=60)


def load_discovery() -> dict:
    try:
        return json.loads(DISCOVERY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


@app.post("/api/test")
def test_connections(body: ConfigBody | None = None):
    result = run_worker_json("test", body.data if body else None, timeout=900)
    found = load_discovery()
    stamp = datetime.now().isoformat(timespec="seconds")
    for entry in result.get("results", []):
        if entry.get("ok") and (entry.get("users") or entry.get("libraries")):
            found[entry["name"]] = {
                "type": entry.get("type"),
                "users": entry.get("users", []),
                "libraries": entry.get("libraries", []),
                "updated": stamp,
            }
    try:
        DISCOVERY_PATH.parent.mkdir(parents=True, exist_ok=True)
        DISCOVERY_PATH.write_text(json.dumps(found), encoding="utf-8")
    except OSError:
        pass
    result["discovery"] = found
    return result


@app.get("/api/discovery")
def discovery():
    return load_discovery()


@app.post("/api/run")
def start_run(body: RunBody):
    return runner.start(body.dryrun, "manual")


@app.post("/api/run/stop")
def stop_run():
    runner.stop()
    return {"ok": True}


@app.get("/api/run")
def run_status(offset: int = 0):
    return runner.log_since(offset)


# ---- run results -----------------------------------------------------------
@app.get("/api/run/changes")
def run_changes(id: int | None = None):
    with runner.lock:
        if runner.current and runner.current.get("status") in ("running", "stopping") and (
            id is None or id == runner.current["id"]
        ):
            return {"run": dict(runner.current), "changes": list(runner.changes), "live": True}
    files = sorted(RUNS_DIR.glob("*.json")) if RUNS_DIR.exists() else []
    if id is not None:
        files = [f for f in files if f.stem == str(id)]
    if not files:
        return {"run": None, "changes": []}
    try:
        return json.loads(files[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"run": None, "changes": []}


# ---- notifications ---------------------------------------------------------
class NotifyBody(BaseModel):
    settings: dict


@app.get("/api/notifications")
def get_notifications():
    return runner.notifications


@app.put("/api/notifications")
def put_notifications(body: NotifyBody):
    runner.notifications = notify.settings_with_defaults(body.settings)
    runner._save_state()
    return runner.notifications


@app.post("/api/notifications/test")
def test_notifications(body: NotifyBody):
    cfg = notify.settings_with_defaults(body.settings)
    sample = {
        "test": True,
        "status": "success",
        "dryrun": False,
        "trigger": "test",
        "duration": "0s",
        "summary": {
            "total": 3,
            "by_user": [{"user": "example", "server": "Example Server", "watched": 2, "partial": 1}],
        },
    }
    results = notify.send_all(cfg, sample)
    if not results:
        raise HTTPException(400, "No notification channel is filled in yet")
    return results


# ---- Plex sign-in (plex.tv PIN flow) ---------------------------------------
PLEX_PRODUCT = "JellyPlex-Watched"


def plex_headers(token: str | None = None) -> dict:
    client_id = runner.extra.get("plex_client_id")
    if not client_id:
        client_id = runner.extra["plex_client_id"] = "jpw-" + secrets.token_hex(12)
        runner._save_state()
    headers = {
        "Accept": "application/json",
        "X-Plex-Product": PLEX_PRODUCT,
        "X-Plex-Client-Identifier": client_id,
        "X-Plex-Device-Name": PLEX_PRODUCT,
    }
    if token:
        headers["X-Plex-Token"] = token
    return headers


def plex_call(method: str, url: str, **kwargs):
    import requests

    try:
        resp = requests.request(method, url, timeout=20, **kwargs)
    except requests.RequestException as error:
        raise HTTPException(502, f"Could not reach plex.tv: {error}") from error
    if resp.status_code >= 400:
        raise HTTPException(502, f"plex.tv answered HTTP {resp.status_code}")
    return resp.json()


@app.post("/api/plex/pin")
def plex_pin():
    from urllib.parse import urlencode

    headers = plex_headers()
    pin = plex_call("POST", "https://plex.tv/api/v2/pins?strong=true", headers=headers)
    query = urlencode(
        {
            "clientID": headers["X-Plex-Client-Identifier"],
            "code": pin["code"],
            "context[device][product]": PLEX_PRODUCT,
        }
    )
    return {"id": pin["id"], "code": pin["code"], "url": f"https://app.plex.tv/auth#?{query}"}


@app.get("/api/plex/pin/{pin_id}")
def plex_pin_status(pin_id: int, code: str = ""):
    pin = plex_call(
        "GET", f"https://plex.tv/api/v2/pins/{pin_id}", headers=plex_headers(), params={"code": code}
    )
    token = pin.get("authToken")
    if not token:
        return {"done": False}
    resources = plex_call(
        "GET",
        "https://plex.tv/api/v2/resources?includeHttps=1&includeRelay=0",
        headers=plex_headers(token),
    )
    servers = []
    for res in resources:
        if "server" not in (res.get("provides") or ""):
            continue
        options = []
        for conn in res.get("connections") or []:
            if conn.get("relay"):
                continue
            where = "Local" if conn.get("local") else "Remote"
            options.append({"label": f"{where} (secure)", "url": conn.get("uri"), "local": bool(conn.get("local"))})
            if conn.get("local") and conn.get("address") and conn.get("port"):
                options.append(
                    {
                        "label": "Local (plain IP)",
                        "url": f"http://{conn['address']}:{conn['port']}",
                        "local": True,
                    }
                )
        options.sort(key=lambda o: (not o["local"], "plain" not in o["label"]))
        servers.append(
            {
                "name": res.get("name"),
                "owned": bool(res.get("owned")),
                "token": res.get("accessToken") or token,
                "connections": options,
            }
        )
    return {"done": True, "servers": servers}


# ---- Plex watchlist -> favorites -----------------------------------------
def public_watchlist() -> dict:
    wl = runner.watchlist_settings()
    return {
        **{k: wl[k] for k in ("enabled", "with_sync", "remove")},
        "links": [
            {k: v for k, v in link.items() if k != "token"} | {"has_token": bool(link.get("token"))}
            for link in wl["links"]
        ],
    }


class WatchlistBody(BaseModel):
    enabled: bool = False
    with_sync: bool = True
    remove: bool = True
    links: list[dict] = []


@app.get("/api/watchlist")
def get_watchlist():
    return public_watchlist()


@app.put("/api/watchlist")
def put_watchlist(body: WatchlistBody):
    wl = runner.watchlist_settings()
    tokens = {link["id"]: link.get("token") for link in wl["links"]}
    links = []
    for link in body.links:
        if link.get("id") not in tokens:
            continue  # links are only created through Plex sign-in
        targets = [
            {"server": t.get("server", ""), "user": t.get("user", "")}
            for t in link.get("targets") or []
            if t.get("server") or t.get("user")
        ]
        links.append(
            {
                "id": link["id"],
                "plex_name": link.get("plex_name", ""),
                "enabled": bool(link.get("enabled", True)),
                "targets": targets,
                "token": tokens[link["id"]],
            }
        )
    wl.update(enabled=body.enabled, with_sync=body.with_sync, remove=body.remove, links=links)
    runner._save_state()
    return public_watchlist()


@app.get("/api/watchlist/link/{pin_id}")
def watchlist_link(pin_id: int, code: str = ""):
    """Finish a Plex sign-in and store it as a linked watchlist account."""
    pin = plex_call(
        "GET", f"https://plex.tv/api/v2/pins/{pin_id}", headers=plex_headers(), params={"code": code}
    )
    token = pin.get("authToken")
    if not token:
        return {"done": False}
    user = plex_call("GET", "https://plex.tv/api/v2/user", headers=plex_headers(token))
    name = user.get("username") or user.get("title") or user.get("email") or "Plex account"
    wl = runner.watchlist_settings()
    existing = next((link for link in wl["links"] if link.get("plex_name") == name), None)
    if existing:
        existing["token"] = token
        link = existing
    else:
        link = {"id": secrets.token_hex(6), "plex_name": name, "enabled": True,
                "targets": [], "token": token}
        wl["links"].append(link)
    runner._save_state()
    return {"done": True, "watchlist": public_watchlist(), "linked": name}


class WatchlistRunBody(BaseModel):
    dryrun: bool = True


@app.post("/api/watchlist/run")
def watchlist_run(body: WatchlistRunBody):
    return runner.start(body.dryrun, "manual", job="watchlist")


# ---- desktop app integration -----------------------------------------------
# desktop.py fills these in; in Docker they stay empty.
DESKTOP_HOOKS: dict = {}


class OpenBody(BaseModel):
    url: str


@app.get("/api/info")
def info():
    return {"desktop": DESKTOP, "tray": "set_options" in DESKTOP_HOOKS}


@app.post("/api/open")
def open_url(body: OpenBody):
    if not body.url.startswith("https://app.plex.tv/"):
        raise HTTPException(400, "Only Plex sign-in links can be opened")
    import webbrowser

    webbrowser.open(body.url)
    return {"ok": True}


class DesktopBody(BaseModel):
    close_to_tray: bool = True
    start_with_windows: bool = False
    start_minimized: bool = False


@app.post("/api/desktop/show")
def desktop_show():
    if "show" in DESKTOP_HOOKS:
        DESKTOP_HOOKS["show"]()
    return {"ok": True}


@app.get("/api/desktop")
def get_desktop():
    if "get_options" not in DESKTOP_HOOKS:
        raise HTTPException(404, "Desktop options are only available in the Windows app")
    return DESKTOP_HOOKS["get_options"]()


@app.put("/api/desktop")
def put_desktop(body: DesktopBody):
    if "set_options" not in DESKTOP_HOOKS:
        raise HTTPException(404, "Desktop options are only available in the Windows app")
    return DESKTOP_HOOKS["set_options"](body.model_dump())


@app.get("/api/schedule")
def get_schedule():
    return {**runner.schedule, "next_run": runner.next_run}


@app.put("/api/schedule")
def put_schedule(body: ScheduleBody):
    runner.set_schedule(body.enabled, body.interval_minutes, body.dryrun)
    return {**runner.schedule, "next_run": runner.next_run}


@app.get("/api/history")
def history():
    return list(reversed(runner.history))


@app.get("/api/marks")
def marks(limit: int = 500):
    data, _, _ = read_config()
    mark = Path(data.get("mark_file") or "mark.log")
    if not mark.is_absolute():
        mark = WORK_DIR / mark
    if not mark.exists():
        return {"path": str(mark), "lines": []}
    with mark.open(encoding="utf-8", errors="replace") as fh:
        lines = deque(fh, maxlen=limit)
    return {"path": str(mark), "lines": [ln.rstrip("\n") for ln in reversed(lines)]}


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
