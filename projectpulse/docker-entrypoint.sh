#!/bin/sh
# Make the mounted volume writable by the app, then stop being root.
#
# A Fly volume is mounted root:root, and this image runs as `pulse` (uid
# 10001) - so `/data` is unwritable by the process that has to write it, and
# the failure is a permission error at boot rather than anything about
# traceability. The container therefore starts as root, fixes the one
# directory it owns, and drops privileges before exec'ing the app. Nothing
# application-shaped runs as root.
#
# `setpriv` rather than `gosu` or `su-exec`: it is in util-linux, which the
# `python:*-slim` base already carries, so this needs no extra package. `su`
# is also there and is the wrong tool - it forks a shell, so the app would not
# be PID 1 and would stop receiving the signals Fly sends to stop a machine.
#
# Idempotent and safe without a volume: with no `/data` (a local
# `docker compose up`, or a Fly deploy before the mount exists) there is
# nothing to chown and the app starts exactly as it did before.
set -e

STATE_DIR="${PULSE_STATE_DIR:-/data}"
APP_UID=10001
APP_GID=10001

if [ "$(id -u)" = "0" ]; then
    if [ -d "$STATE_DIR" ]; then
        # Not `-R` unconditionally: on a volume holding real runs this walks
        # every artifact on every boot, and the only entries that can be
        # wrongly owned are ones root made - the mount point itself, and
        # anything written before this script existed. Chown the top level and
        # let the app own what it creates from here.
        chown "$APP_UID:$APP_GID" "$STATE_DIR" 2>/dev/null || {
            echo "[entrypoint] WARNING: could not chown $STATE_DIR." >&2
            echo "[entrypoint] Runs will fail to write. If this is a Fly" >&2
            echo "[entrypoint] volume, check it is actually mounted:" >&2
            echo "[entrypoint]   fly volumes list && fly ssh console -C 'ls -la /data'" >&2
        }
        # Subdirectories the app writes into, created here so the app never
        # has to create them at the root of a directory it does not own.
        for sub in runs repos; do
            if [ ! -d "$STATE_DIR/$sub" ]; then
                mkdir -p "$STATE_DIR/$sub" 2>/dev/null || true
            fi
            chown "$APP_UID:$APP_GID" "$STATE_DIR/$sub" 2>/dev/null || true
        done
    fi

    # ⚠️ `setpriv` changes the ids and *nothing else*, so `HOME` would stay
    # `/root` - which uid 10001 cannot read. `USER pulse` in the Dockerfile
    # used to set this for free, and dropping that directive silently took it
    # away. What broke: psycopg looks for an optional client certificate at
    # `$HOME/.postgresql/postgresql.crt`, and against a TLS Postgres (Neon)
    # the unreadable `/root` comes back as `Permission denied` rather than
    # "no such file", so the connection fails, `scripts.serve` exits 1 and the
    # machine reboots in a loop with no page ever served.
    export HOME=/home/pulse
    export USER=pulse LOGNAME=pulse

    # `--init-groups` so supplementary groups match the user rather than
    # staying root's, and `--inh-caps=-all` so nothing inherits a capability
    # root happened to hold.
    exec setpriv --reuid="$APP_UID" --regid="$APP_GID" --init-groups \
                 --inh-caps=-all -- "$@"
fi

# Already unprivileged - somebody set `USER` or `--user` themselves. Nothing
# to drop, and the chown above would have failed anyway.
exec "$@"
