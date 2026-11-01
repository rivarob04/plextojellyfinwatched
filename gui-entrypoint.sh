#!/usr/bin/env sh
# Entrypoint for the GUI image. Unlike entrypoint.sh it does not need a valid
# config to start: the GUI is how you create one.
set -e

CONF_DIR="${GUI_DATA_DIR:-/app/config}"
mkdir -p "$CONF_DIR"
cp /app/sample.config.yaml "$CONF_DIR/sample.config.yaml" 2>/dev/null || true

if [ "$(id -u)" = '0' ]; then
    if [ ! "$(getent group "$PGID")" ]; then
        groupadd -g "$PGID" jellyplex_watched
    fi
    if [ ! "$(getent passwd "$PUID")" ]; then
        useradd --no-create-home -u "$PUID" -g "$PGID" jellyplex_watched
    fi
    chown -R "$PUID:$PGID" "$CONF_DIR" 2>/dev/null || chown "$PUID:$PGID" "$CONF_DIR"
    echo "Starting JellyPlex-Watched GUI as UID $PUID / GID $PGID on port ${GUI_PORT:-8787}"
    exec gosu "$PUID:$PGID" "$@"
fi

echo "Starting JellyPlex-Watched GUI on port ${GUI_PORT:-8787}"
exec "$@"
