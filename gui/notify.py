"""Run notifications: Discord, ntfy, a generic JSON webhook, and email."""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from typing import Any

import requests

DEFAULTS: dict[str, Any] = {
    "on_failure": True,
    "on_changes": True,
    "on_success": False,
    "include_dryrun": False,
    "discord_webhook": "",
    "ntfy_url": "",
    "ntfy_token": "",
    "webhook_url": "",
    "smtp_host": "",
    "smtp_port": 587,
    "smtp_security": "starttls",  # starttls | ssl | none
    "smtp_user": "",
    "smtp_password": "",
    "email_from": "",
    "email_to": "",
}

TIMEOUT = 15


def settings_with_defaults(saved: dict | None) -> dict:
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in (saved or {}).items() if k in DEFAULTS})
    return merged


def should_notify(cfg: dict, run: dict) -> bool:
    status = run.get("status")
    if status in ("failed", "stopped"):
        return bool(cfg["on_failure"])
    if run.get("dryrun") and not cfg["include_dryrun"]:
        return False
    if (run.get("summary") or {}).get("total", 0) > 0:
        return bool(cfg["on_changes"] or cfg["on_success"])
    return bool(cfg["on_success"])


def compose(run: dict) -> tuple[str, str, str]:
    """Return (title, body, level) for a finished run."""
    summary = run.get("summary") or {}
    total = summary.get("total", 0)
    dry = run.get("dryrun")
    mode = "Dry run" if dry else "Live run"
    status = run.get("status")

    if status == "failed":
        title, level = "JellyPlex-Watched: sync failed", "error"
    elif status == "stopped":
        title, level = "JellyPlex-Watched: sync stopped", "warning"
    elif total:
        verb = "would mark" if dry else "marked"
        title, level = f"JellyPlex-Watched: {verb} {total} item{'s' if total != 1 else ''}", "success"
    else:
        title, level = "JellyPlex-Watched: no changes", "info"

    lines = [f"{mode} · {run.get('trigger', 'manual')} · {run.get('duration', '?')}"]
    if run.get("errors"):
        lines.append(f"{run['errors']} error line(s) in the log")
    if run.get("test"):
        lines = ["This is a test notification from JellyPlex-Watched."]
    for row in (summary.get("by_user") or [])[:10]:
        parts = []
        if row.get("watched"):
            parts.append(f"{row['watched']} watched")
        if row.get("partial"):
            parts.append(f"{row['partial']} in progress")
        lines.append(f"• {row['user']} → {row['server']}: {', '.join(parts)}")
    extra = len(summary.get("by_user") or []) - 10
    if extra > 0:
        lines.append(f"…and {extra} more")
    return title, "\n".join(lines), level


def send_all(cfg: dict, run: dict) -> dict[str, str]:
    """Send to every configured channel. Returns {channel: "ok" | error}."""
    title, body, level = compose(run)
    results: dict[str, str] = {}
    senders = {
        "discord": (cfg["discord_webhook"], _discord),
        "ntfy": (cfg["ntfy_url"], _ntfy),
        "webhook": (cfg["webhook_url"], _webhook),
        "email": (cfg["smtp_host"] and cfg["email_to"], _email),
    }
    for name, (enabled, fn) in senders.items():
        if not enabled:
            continue
        try:
            fn(cfg, title, body, level, run)
            results[name] = "ok"
        except Exception as error:  # noqa: BLE001 - reported back to the GUI
            results[name] = str(error)[:300] or type(error).__name__
    return results


def _check(resp: requests.Response) -> None:
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")


def _discord(cfg, title, body, level, run) -> None:
    colour = {"error": 0xC2412D, "warning": 0xE2A64A, "success": 0x2F8A4A}.get(level, 0xE5A00D)
    payload = {
        "username": "JellyPlex-Watched",
        "embeds": [{"title": title, "description": body[:4000], "color": colour}],
    }
    _check(requests.post(cfg["discord_webhook"], json=payload, timeout=TIMEOUT))


def _ntfy(cfg, title, body, level, run) -> None:
    headers = {
        "Title": title.encode("utf-8"),
        "Priority": "high" if level == "error" else "default",
        "Tags": {"error": "x", "warning": "warning", "success": "white_check_mark"}.get(level, "tv"),
    }
    if cfg["ntfy_token"]:
        headers["Authorization"] = f"Bearer {cfg['ntfy_token']}"
    _check(requests.post(cfg["ntfy_url"], data=body.encode("utf-8"), headers=headers, timeout=TIMEOUT))


def _webhook(cfg, title, body, level, run) -> None:
    payload = {"title": title, "message": body, "level": level, "run": run}
    _check(requests.post(cfg["webhook_url"], json=payload, timeout=TIMEOUT))


def _email(cfg, title, body, level, run) -> None:
    msg = EmailMessage()
    msg["Subject"] = title
    msg["From"] = cfg["email_from"] or cfg["smtp_user"]
    msg["To"] = cfg["email_to"]
    msg.set_content(body)
    port = int(cfg["smtp_port"] or (465 if cfg["smtp_security"] == "ssl" else 587))
    context = ssl.create_default_context()
    if cfg["smtp_security"] == "ssl":
        server = smtplib.SMTP_SSL(cfg["smtp_host"], port, timeout=TIMEOUT, context=context)
    else:
        server = smtplib.SMTP(cfg["smtp_host"], port, timeout=TIMEOUT)
    with server:
        if cfg["smtp_security"] == "starttls":
            server.starttls(context=context)
        if cfg["smtp_user"]:
            server.login(cfg["smtp_user"], cfg["smtp_password"])
        server.send_message(msg)
