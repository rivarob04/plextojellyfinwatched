"""Plex watchlist -> Jellyfin / Emby favorites.

Runs inside the worker process. For every linked Plex account it reads the
account's watchlist from plex.tv, matches each title to the target user's
Jellyfin/Emby library by IMDb / TMDB / TVDB id, and favorites it.

Only favorites this feature added are ever removed (when the title leaves the
watchlist); favorites a person set themselves are never touched. What the app
added is remembered in a small JSON state file next to the config.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

import requests

ID_KEYS = ("imdb", "tmdb", "tvdb")
TIMEOUT = 30


# ---------------------------------------------------------------------------
# Plex side
# ---------------------------------------------------------------------------
def parse_guids(raw_ids: list[str]) -> dict[str, str]:
    """['imdb://tt123', 'tmdb://45', 'tvdb://9'] -> {'imdb': 'tt123', ...}"""
    out: dict[str, str] = {}
    for gid in raw_ids:
        if "://" not in gid:
            continue
        scheme, value = gid.split("://", 1)
        scheme = scheme.lower()
        if scheme in ID_KEYS and value:
            out[scheme] = value.strip().lower()
    return out


def fetch_plex_watchlist(token: str) -> list[dict]:
    """Return [{title, year, type: movie|show, ids: {imdb,tmdb,tvdb}}]."""
    from plexapi.myplex import MyPlexAccount

    account = MyPlexAccount(token=token, timeout=TIMEOUT)
    items = account.watchlist()

    def describe(item) -> dict:
        try:
            ids = parse_guids([g.id for g in item.guids])  # loads details from plex.tv
        except Exception:  # noqa: BLE001 - keep the title even without ids
            ids = {}
        return {
            "title": item.title,
            "year": getattr(item, "year", None),
            "type": "show" if item.type == "show" else "movie",
            "ids": ids,
        }

    with ThreadPoolExecutor(max_workers=8) as pool:
        return [w for w in pool.map(describe, items) if w["type"] in ("movie", "show")]


def plex_account_name(token: str) -> str:
    resp = requests.get(
        "https://plex.tv/api/v2/user",
        headers={"Accept": "application/json", "X-Plex-Token": token,
                 "X-Plex-Client-Identifier": "jellyplex-watched"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("username") or data.get("title") or data.get("email") or "Plex account"


# ---------------------------------------------------------------------------
# Jellyfin / Emby side
# ---------------------------------------------------------------------------
class FavoritesTarget:
    """Thin wrapper over the engine's Jellyfin/Emby connection."""

    def __init__(self, conn) -> None:
        self.conn = conn
        self.base = conn.server_settings.baseurl.rstrip("/")
        self.headers = conn.headers
        self.session = conn.session
        self.is_jellyfin = conn.server_type == "Jellyfin"

    def _req(self, method: str, path: str, **kwargs) -> requests.Response:
        resp = self.session.request(
            method, self.base + path, headers=self.headers, timeout=TIMEOUT, **kwargs
        )
        return resp

    def user_id(self, username: str) -> str | None:
        for name, uid in self.conn.users.items():
            if name.lower() == username.lower():
                return uid
        return None

    def library_index(self, user_id: str) -> tuple[dict[tuple[str, str], dict], dict[str, dict]]:
        """Index the user's movies and series by provider id.

        Returns ({(provider, id): item}, {item_id: item}).
        """
        index: dict[tuple[str, str], dict] = {}
        by_id: dict[str, dict] = {}
        start, page = 0, 1000
        while True:
            resp = self._req(
                "GET",
                f"/Users/{user_id}/Items",
                params={
                    "Recursive": "true",
                    "IncludeItemTypes": "Movie,Series",
                    "Fields": "ProviderIds",
                    "StartIndex": start,
                    "Limit": page,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("Items", [])
            for item in items:
                entry = {
                    "id": item["Id"],
                    "name": item.get("Name"),
                    "type": item.get("Type"),
                    "favorite": bool((item.get("UserData") or {}).get("IsFavorite")),
                }
                by_id[item["Id"]] = entry
                for key, value in (item.get("ProviderIds") or {}).items():
                    k = key.lower()
                    if k in ID_KEYS and value:
                        index[(k, str(value).strip().lower())] = entry
            start += len(items)
            if not items or start >= data.get("TotalRecordCount", start):
                break
        return index, by_id

    def set_favorite(self, user_id: str, item_id: str, favorite: bool) -> None:
        method = "POST" if favorite else "DELETE"
        if self.is_jellyfin:
            # Jellyfin 10.9+ route, then the legacy one used by older versions.
            resp = self._req(method, f"/UserFavoriteItems/{item_id}", params={"userId": user_id})
            if resp.status_code in (404, 405):
                resp = self._req(method, f"/Users/{user_id}/FavoriteItems/{item_id}")
        else:
            resp = self._req(method, f"/Users/{user_id}/FavoriteItems/{item_id}")
            if resp.status_code == 405 and not favorite:
                resp = self._req("POST", f"/Users/{user_id}/FavoriteItems/{item_id}/Delete")
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code}")


def match(entry: dict, index: dict[tuple[str, str], dict]) -> dict | None:
    want_type = "Series" if entry["type"] == "show" else "Movie"
    for key in ID_KEYS:
        value = entry["ids"].get(key)
        if value:
            hit = index.get((key, value))
            if hit and hit["type"] == want_type:
                return hit
    return None


# ---------------------------------------------------------------------------
# One full pass
# ---------------------------------------------------------------------------
def run(
    links: list[dict],
    connections: dict[str, Any],
    state_path: Path,
    dryrun: bool,
    remove: bool,
    emit: Callable[[dict], None],
    log: Callable[[str, str], None],
    fetch: Callable[[str], list[dict]] | None = None,
) -> int:
    """Apply every link. Returns the number of errors."""
    fetch = fetch or fetch_plex_watchlist
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}

    errors = 0
    for link in links:
        if not link.get("enabled", True):
            continue
        label = link.get("plex_name") or "Plex account"
        try:
            watchlist = fetch(link["token"])
        except Exception as error:  # noqa: BLE001
            log("ERROR", f"Watchlist: could not read the Plex watchlist for {label}: {error}")
            errors += 1
            continue
        log("INFO", f"Watchlist: {label} has {len(watchlist)} movies and shows on their Plex watchlist")

        for target in link.get("targets") or []:
            server, username = target.get("server"), target.get("user")
            conn = connections.get(server)
            if not conn or not username:
                continue
            fav = FavoritesTarget(conn)
            uid = fav.user_id(username)
            if not uid:
                log("ERROR", f"Watchlist: user {username} not found on {server}")
                errors += 1
                continue
            try:
                index, by_id = fav.library_index(uid)
            except Exception as error:  # noqa: BLE001
                log("ERROR", f"Watchlist: could not read {username}'s library on {server}: {error}")
                errors += 1
                continue

            key = f"{link['id']}|{server}|{username.lower()}"
            owned: dict[str, dict] = state.get(key, {})
            wanted: set[str] = set()
            base = {"server_type": conn.server_type, "server": conn.server_name,
                    "user": username, "dryrun": dryrun, "source": label}

            for entry in watchlist:
                hit = match(entry, index)
                library = "Shows" if entry["type"] == "show" else "Movies"
                title = entry["title"] + (f" ({entry['year']})" if entry.get("year") else "")
                if not hit:
                    emit({**base, "kind": "missing", "library": library, "title": title,
                          "episode": None, "minutes": None})
                    continue
                wanted.add(hit["id"])
                if hit["favorite"]:
                    continue  # already a favorite (ours or theirs) – nothing to do
                try:
                    if not dryrun:
                        fav.set_favorite(uid, hit["id"], True)
                        owned[hit["id"]] = {"title": title}
                    emit({**base, "kind": "favorite", "library": library, "title": title,
                          "episode": None, "minutes": None})
                except Exception as error:  # noqa: BLE001
                    log("ERROR", f"Watchlist: could not favorite {title} for {username}: {error}")
                    errors += 1

            if remove:
                for item_id in list(owned):
                    if item_id in wanted:
                        continue
                    info = owned[item_id]
                    current = by_id.get(item_id)
                    try:
                        if current and current["favorite"] and not dryrun:
                            fav.set_favorite(uid, item_id, False)
                        if current and current["favorite"]:
                            emit({**base, "kind": "unfavorite", "library": "",
                                  "title": info.get("title") or current.get("name"),
                                  "episode": None, "minutes": None})
                        if not dryrun:
                            owned.pop(item_id, None)
                    except Exception as error:  # noqa: BLE001
                        log("ERROR", f"Watchlist: could not remove favorite {info.get('title')}: {error}")
                        errors += 1

            if not dryrun:
                state[key] = owned

    if not dryrun:
        try:
            state_path.write_text(json.dumps(state), encoding="utf-8")
        except OSError as error:
            log("ERROR", f"Watchlist: could not save state: {error}")
            errors += 1
    return errors
