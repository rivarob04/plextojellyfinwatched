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
class Runner:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self.lines: deque[str] = deque(maxlen=5000)
        self.base = 0  # absolute index of self.lines[0]
        self.current: dict | None = None
        self.history: list[dict] = []
        self.schedule = {"enabled": False, "interval_minutes": 60, "dryrun": True}
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
        except (OSError, ValueError):
            pass

    def _save_state(self) -> None:
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            STATE_PATH.write_text(
                json.dumps({"schedule": self.schedule, "history": self.history[-50:]}),
                encoding="utf-8",
            )
        except OSError:
            pass

    # -- runs --------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, dryrun: bool | None, trigger: str) -> dict:
        with self.lock:
            if self.running:
                raise HTTPException(409, "A sync is already running")
            if not CONFIG_PATH.exists():
                raise HTTPException(400, "Save a configuration first")
            args = ["run", "--config", str(CONFIG_PATH)]
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
            }
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
                self.lines.append(line)
                if self.current:
                    if "| ERROR" in line or "Startup error" in line:
                        self.current["errors"] += 1
                    if "Marked" in line or "marked" in line:
                        self.current["marked"] += 1
        code = proc.wait()
        with self.lock:
            if self.current:
                if self.current["status"] == "stopping":
                    self.current["status"] = "stopped"
                else:
                    self.current["status"] = "success" if code == 0 else "failed"
                self.current["finished"] = datetime.now().isoformat(timespec="seconds")
                self.current["exit_code"] = code
                self.history.append(dict(self.current))
                self._save_state()

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
