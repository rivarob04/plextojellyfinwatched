# plextojellyfinwatched — JellyPlex-Watched with a GUI

A fork of [luigi311/JellyPlex-Watched](https://github.com/luigi311/JellyPlex-Watched) that adds a **web GUI** on top of the original sync engine. Keep every user's watched history in sync between Plex, Jellyfin and Emby — configured, tested and run from your browser instead of a YAML file and a terminal.

The sync engine (`src/`) is unchanged from upstream, so matching, mappings and rules behave exactly as documented below.

## The GUI

| Tab | What you can do |
| --- | --- |
| **Dashboard** | Start a sync (dry run or live), stop it, watch the live colour-coded log, see recent runs, test every server connection, and turn on a repeating schedule |
| **Servers** | Add/remove Plex, Jellyfin and Emby servers, and tick a **sync-direction grid** (row pushes to column) for one-way or two-way sync |
| **Settings & Filters** | Dry-run default, log level, threads, timeouts, GUID/path matching, and user / library / library-type white- and blacklists |
| **Mappings** | Link the same person or library when names differ between servers |
| **Sync Rules** | Per-user and per-library direction overrides |
| **YAML** | Edit the raw `config.yaml` directly — form and file stay interchangeable |
| **Marked** | Searchable list of every item that has been marked watched |

Every save is validated with the real settings loader first, and the previous file is kept as `config.yaml.bak`.

### Windows app (Windows 10/11)

Download the latest **JellyPlex-Watched-Setup-x.y.z.exe** from the
[Releases page](https://github.com/rivarob04/plextojellyfinwatched/releases) and run it.
No Python or Docker needed; it installs per-user (no admin prompt) and adds a Start menu shortcut.
A portable `.zip` is also available: unzip anywhere and run `JellyPlex-Watched.exe`.

- The app opens in its own window (Microsoft Edge WebView2, included with Windows 11 and current Windows 10). Without WebView2 it opens in your default browser instead.
- Config, logs, marked items and run history live in `%APPDATA%\JellyPlex-Watched` (Start menu → *Open config folder*). Uninstalling keeps them.
- Windows SmartScreen may warn on first launch because the app isn't code-signed: click **More info → Run anyway**.
- The schedule runs only while the app window is open.

Every push to `main` rebuilds the app and publishes it to the release named in `packaging/windows/VERSION`; bump that file to cut a new release.

### Run it locally

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/rivarob04/plextojellyfinwatched.git
cd plextojellyfinwatched
uv sync
uv run gui.py
```

Your browser opens at <http://127.0.0.1:8787>. Add your servers, click **Test connections**, **Save**, then **Start sync** with *Dry run* ticked to preview what would change. Untick it for a live run.

(Without uv: `pip install .` then `python gui.py`.)

### Run it in Docker / Dockhand

Every push to `main` builds a multi-arch image (amd64 + arm64) to
`ghcr.io/rivarob04/plextojellyfinwatched:latest`, and `docker-compose.yml` uses it.

**Dockhand** — create a stack either way:

- *From Git:* repository `https://github.com/rivarob04/plextojellyfinwatched`, branch `main`, compose path `docker-compose.yml`.
- *Pasted compose:* copy the contents of `docker-compose.yml`.

Then add stack environment variables:

| Variable | Required | Example |
| --- | --- | --- |
| `GUI_PASSWORD` | yes | a long password |
| `CONFIG_DIR` | recommended | `/opt/appdata/jellyplex-watched` (host folder for config, logs, history) |
| `PUID` / `PGID` | no | `1000` |
| `TZ` | no | `America/Toronto` |
| `GUI_PORT_HOST` | no | `8787` |

Deploy, open `http://<docker-host>:8787`, and sign in with any username plus `GUI_PASSWORD`. The container starts fine with no config — the GUI is where you create it.

**Plain Docker:** `GUI_PASSWORD=yourpassword docker compose up -d`. To build from source instead of pulling: `docker compose -f docker-compose.build.yml up -d --build`.

### GUI options (environment variables)

| Variable | Default | Purpose |
| --- | --- | --- |
| `YAML_FILE` | `config.yaml` | Config file the GUI edits and runs |
| `GUI_HOST` | `127.0.0.1` | Bind address; `0.0.0.0` to allow other devices |
| `GUI_PORT` | `8787` | Port |
| `GUI_PASSWORD` | *(none)* | Password prompt for the GUI. **Set this whenever the GUI is reachable from your network** — it displays your server tokens |
| `GUI_NO_BROWSER` | *(unset)* | `1` to skip opening a browser tab on start |
| `GUI_STATE_FILE` | next to the config | Where the schedule and run history are stored |
| `GUI_DATA_DIR` | *(unset; `/app/config` in Docker)* | Default folder for `log.log` and `mark.log` in new configs |

The schedule runs only while the GUI is running. The original command-line mode (`python main.py`) still works with the same `config.yaml`, so you can configure in the GUI and run headless if you prefer.

### How it works

`gui/app.py` is a small FastAPI server serving a single-page interface from `gui/static/`. Each validate, connection test and sync runs in a separate process through `gui/worker.py`, which calls the upstream `load_settings` and `main_loop` directly, so a stuck server can be stopped without taking the GUI down.

## Credits & license

All sync logic is the work of [Luigi311](https://github.com/luigi311) and the JellyPlex-Watched contributors. This fork is distributed under the same **GNU GPL v3** licence (see `LICENSE`).

---

# Original JellyPlex-Watched documentation

## Description

Keep in sync all your users watched history between jellyfin, plex and emby servers locally. This uses file names and provider ids to find the correct episode/movie between the two. This is not perfect but it works for most cases. You can use this for as many servers as you want by entering multiple options in the config.

## Features

### Plex

- \[x] Match via filenames
- \[x] Match via provider ids
- \[x] Map usernames
- \[x] Use single login
- \[x] One way/multi way sync
- \[x] Sync watched
- \[x] Sync in progress
- \[ ] Sync view dates

### Jellyfin

- \[x] Match via filenames
- \[x] Match via provider ids
- \[x] Map usernames
- \[x] Use single login
- \[x] One way/multi way sync
- \[x] Sync watched
- \[x] Sync in progress
- \[x] Sync view dates


### Emby

- \[x] Match via filenames
- \[x] Match via provider ids
- \[x] Map usernames
- \[x] Use single login
- \[x] One way/multi way sync
- \[x] Sync watched
- \[x] Sync in progress
- \[x] Sync view dates


## Configuration

Use [`sample.config.yaml`](sample.config.yaml) as the primary configuration
template. Copy it to `config.yaml`, replace the placeholder server URLs and
credentials, and remove or edit server entries that do not apply to your
setup.

Credentials may be stored in YAML or supplied through supported environment
variables; environment values override matching YAML values. YAML credentials
are plaintext, so keep `config.yaml` restricted to the application user and
never commit it. Docker secret files are not loaded automatically; inject
secret values through the supported environment variables or create a protected
YAML file.

### Legacy dotenv compatibility

Existing installations may continue using the unprefixed legacy `.env` format.
For a new legacy file, use a clearly labeled compatibility configuration such
as:

```dotenv
# Legacy compatibility example
PLEX_BASEURL=http://plex.example.com:32400
PLEX_TOKEN=replace-with-a-plex-token
JELLYFIN_BASEURL=http://jellyfin.example.com:8096
JELLYFIN_TOKEN=replace-with-a-jellyfin-token
SYNC_FROM_PLEX_TO_JELLYFIN=true
```

The YAML template is the recommended starting point for new installations;
legacy dotenv values remain supported for existing deployments and migration.

### Source-backed environment overrides

The normal entry point is `load_settings()`. New-style environment variables
use the `JPW_` prefix and Pydantic's JSON syntax for lists and objects:

```dotenv
JPW_DRYRUN=false
JPW_WHITELIST_USERS='["alice", "bob"]'
JPW_SERVER_TOKENS='{"plex-main":"replacement-token"}'
```

The same variables can be placed in the selected dotenv file. Existing
unprefixed legacy variables, including comma-separated lists, remain supported
for current deployments and migration.

For source-backed loading, values are applied from highest to lowest priority:

1. Explicit field values supplied to the internal source loader.
2. `JPW_`-prefixed process environment variables.
3. `JPW_`-prefixed values in the selected dotenv file.
4. Legacy process environment variables.
5. Legacy values in the selected dotenv file.
6. The selected YAML file.
7. Field defaults.

`AppSettings(...)` is the plain data model and validates only values supplied
directly to it. Use `load_settings()` when environment or YAML sources should
be read. `ENV_FILE` and `YAML_FILE` are path selectors, not `JPW_` fields; set
them in the process environment or pass `env_file=` and `yaml_file=` to
`load_settings()`.

An absent, empty, or valueless new-style entry inherits from the next source.
Use JSON `[]` when an empty list should replace a lower-priority list; a full
JSON server list replaces the lower-priority server definitions as a whole.
Malformed new-style JSON is rejected. Legacy empty and valueless entries are
unset after process/file precedence is resolved and do not fall back to the
file. Legacy boolean values accept `1/0`, `true/false`, `yes/no`, `on/off`,
`t/f`, and `y/n` case-insensitively with surrounding whitespace ignored;
other non-empty values are rejected. A prefixed dotenv value therefore takes
precedence over a legacy process value.

### Compatibility notes

New-style environment variables use the `JPW_` prefix. Keep the original
unprefixed spelling for legacy deployments, including its comma-separated
syntax. For example, `WHITELIST_USERS=alice,bob` remains valid legacy input,
while the new equivalent is
`JPW_WHITELIST_USERS='["alice", "bob"]'`. YAML field names without a legacy
environment spelling must use the prefixed form, such as
`JPW_BLACKLIST_LIBRARIES='["Movies"]'`.

When a legacy variable exists in both the process environment and the selected
dotenv file, the process value now wins. This source decision is made before
legacy aliases are translated, so a process `LOGFILE` or `LOG_FILE` value wins
over either spelling in the file; the existing alias preference still applies
within the winning source. The same rule applies to `MARKFILE`/`MARK_FILE` and
`DEBUG`/`DEBUG_LEVEL`.

An empty or valueless legacy process entry still takes precedence over the
file entry and is then treated as unset; it does not silently fall back to the
file. Legacy loading remains supported. Prefixing new variables and changing
legacy process/file precedence do not deprecate or remove the legacy runtime
path.

Legacy `USER_MAPPING` and `LIBRARY_MAPPING` entries are expanded onto every
configured server because the old format does not identify the owning server.
Generated migration entries carry `legacy: true`; if both names from one pair
are discovered on a server, that mapping is skipped and a warning identifies
the configuration to review. Replace the entry with server-scoped YAML aliases
before syncing those accounts or libraries.

### Credentials and named overrides

`JPW_SERVER_TOKENS` is a JSON object whose keys are exact configured server
names and whose values are non-empty replacement credentials. It patches the
effective YAML or environment server definitions in place, preserving each
server's URL, sync directions, mappings, and other settings. A Plex token
override selects token authentication and clears its username, password, and
server name fields. Unknown server names, malformed maps, empty credentials,
and a map that matches no configured server are rejected without exposing the
credential in the error.

The highest-priority `JPW_SERVER_TOKENS` map replaces lower-priority maps as a
whole before its entries are applied to the effective server list.

A legacy `PLEX_TOKEN` without `PLEX_BASEURL` is a token-only override only when
exactly one Plex server is already configured by a higher-priority source. A
complete legacy base URL/token configuration retains its indexed server-list
behavior.

### Rules and filters

Each server's `sync_to` list describes the direction “this server pushes to
those servers.” It enables sync for users and libraries that pass the normal
filters. Bidirectional sync requires both directions.

Explicit rules are exceptions for their exact `from` → `to` direction:

- A matching `user_sync_rules` entry allows that user despite user whitelists
  and blacklists. Library restrictions apply unless a library rule overrides them.
- A matching `library_sync_rules` entry allows that library despite
  library-name and library-type filters, including the mapped destination's
  type. User restrictions apply unless a user rule overrides them.
- Rules add exceptions to an existing `sync_to` direction; unmatched users
  and libraries keep the normal server defaults and filters.

For a direction absent from `sync_to`, either kind of rule can enable sync:

| Rules for that direction | Eligible history |
| --- | --- |
| User rules only | Matching users, with normally allowed libraries |
| Library rules only | Matching libraries, with normally allowed users |
| Both | Both permissions are added; matching exceptions can override both scopes' filters |
| Neither | No sync |

For example, an Alice user rule and a Movies library rule for the same
direction allow Alice's normally permitted libraries **plus Movies**, even if
Movies is otherwise filtered out. Other normally permitted users also sync
Movies. Their other libraries remain excluded unless another rule or `sync_to`
enables them. Adding a library rule never narrows the user rule, or vice versa.

Rules for other directions do not restrict this one. A wildcard rule uses
`users: ["*"]` or `libraries: ["*"]` as its only entry. It also covers unmapped
runtime identities and overrides the corresponding filters for all matches.

Without a matching exception, a non-empty whitelist takes precedence over its
blacklist. Otherwise blacklist matches are rejected. User filters resolve the
source server's canonical identity and aliases. Without a matching library
exception, configured type filters require both endpoint types to be known and
allowed before writing history. Mappings still resolve actual accounts and
libraries; unmapped identities use same-name matching only when
the target name is not owned by another mapping. Rules do not create missing
accounts or libraries, or enable unsupported media types.

For example, configure Plex with `sync_to: [jellyfin-main, emby-main]` and the
other two servers with `sync_to: []`. These rules allow `luigi311` to send
history back to Plex and onward to the other server, while other users retain
the normal Plex-outbound behavior:

```yaml
user_sync_rules:
  - users: [luigi311]
    from: jellyfin-main
    to: plex-main
  - users: [luigi311]
    from: emby-main
    to: plex-main
```

No library rules are needed for that example; normal library filters apply.
This changes earlier behavior: rules now override their own scope's filters,
and either rule type can enable a direction without a companion rule. To
exclude an identity covered by an explicit rule, remove or narrow that rule
as well as configuring the normal filters.

Each pass compares the fetched histories from all servers before applying any
updates. For each destination user and library, it selects the best permitted
movie or episode state using the existing completion, playback-position, and
viewing-date rules. Competing sources produce one winning update per matching
item; ties use a stable server/user/library name order. Matching retains alternate
provider IDs and filenames from permitted histories, even when their watch state
does not win, so a destination can match any known identifier for the item.
Missing or failed fetch scopes are excluded, while a successfully fetched empty
history can receive updates.

Comparisons use the same snapshot in dry-run and normal operation and follow
permitted paths across servers. With a chain such as A → B → C, A's history is
considered for both B and C in the same pass, even when B's fetched history is
empty. Every hop must pass its direction, user, and library rules; missing or
failed scopes cannot relay history. Cycles are visited once per original source.
For example, if A has watched 20 minutes of Cars, B has not watched it, and C
has completed it, both A and B receive the completed state in that pass when
C has a permitted path to each. This requires no intermediate successful write.

### Applying configuration changes

Settings are loaded once at startup and their lookup indexes are cached for
that process. Restart the application after changing the YAML, dotenv file, or
environment values; the regular loop does not live-reload configuration.

## Installation

### Baremetal

- [Install uv](https://docs.astral.sh/uv/getting-started/installation/)

- Copy the YAML template and edit it for your servers:

  ```bash
  cp sample.config.yaml config.yaml
  ```

- Run

  ```bash
  uv run main.py
  ```

  ```bash
  ENV_FILE="Test.env" uv run main.py
  ```

  The `ENV_FILE` example is for an existing legacy dotenv configuration. Use
  `YAML_FILE="other-config.yaml" uv run main.py` to select a different YAML
  file.

### Docker

- Build docker image

  ```bash
  docker build -f Dockerfile.alpine -t jellyplex-watched .
  # Debian-based slim image:
  docker build -f Dockerfile.slim -t jellyplex-watched:slim .
  ```

- or use pre-built image

  ```bash
  docker pull luigi311/jellyplex-watched:latest
  ```

#### With a YAML configuration

- Copy [`sample.config.yaml`](sample.config.yaml) to `config.yaml`, edit it,
  restrict it to your application user, and mount it into the container. The
  container runs as `PUID`/`PGID`; pass the matching host identity when using
  restrictive file permissions such as `0600`:

  ```bash
  cp sample.config.yaml config.yaml
  chmod 600 config.yaml
  docker run --rm -it \
    --env PUID="$(id -u)" \
    --env PGID="$(id -g)" \
    -v "$(pwd)/config.yaml:/app/config/config.yaml:ro" \
    luigi311/jellyplex-watched:latest
  ```

#### With a named credential override

To replace credentials for servers already defined in YAML, use a named JSON
map such as `JPW_SERVER_TOKENS='{"plex-main":"replacement-token"}'`. The
server name must match exactly; the replacement keeps its URL, sync directions,
and mappings. A Plex replacement selects token authentication and clears the
username/password/server name fields. A legacy `PLEX_TOKEN` without
`PLEX_BASEURL` is supported when exactly one YAML Plex server exists.

#### With a legacy .env file

- Create a legacy `.env` file using the compatibility example above and set the
  variables to match your setup.

- Run

  ```bash
   docker run --rm -it \
     --env PUID="$(id -u)" \
     --env PGID="$(id -g)" \
     -v "$(pwd)/.env:/app/.env" \
     luigi311/jellyplex-watched:latest
  ```

## Troubleshooting/Issues

- Jellyfin

  - Attempt to decode JSON with unexpected mimetype, make sure you enable remote access or add your docker subnet to lan networks in jellyfin settings

- Configuration
  - Do not use quotes around variables in docker compose
  - If you are not running all 3 supported servers, that is, Plex, Jellyfin, and Emby simultaneously, make sure to comment out the server url and token of the server you aren't using.

## Contributing

I am open to receiving pull requests. If you are submitting a pull request, please make sure run it locally for a day or two to make sure it is working as expected and stable.

## License

This is currently under the GNU General Public License v3.0.
