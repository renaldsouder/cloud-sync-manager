#!/bin/sh
# Entrypoint Unraid : aligne l'identité du processus sur PUID/PGID/UMASK
# avant de céder la main à l'application (§5.3, SEC-004).
#
# Les valeurs par défaut 99:100 correspondent à nobody:users sur Unraid.
# Sans cet alignement, les fichiers descendus du Cloud seraient inaccessibles
# depuis les partages SMB (P13, garde-fou 7).
set -eu

PUID="${PUID:-99}"
PGID="${PGID:-100}"
UMASK="${UMASK:-000}"
CONFIG_DIR="${CSM_CONFIG_DIR:-/config}"

umask "$UMASK"

if ! getent group csm >/dev/null 2>&1; then
    groupadd --non-unique --gid "$PGID" csm
fi
if ! getent passwd csm >/dev/null 2>&1; then
    useradd --non-unique --uid "$PUID" --gid "$PGID" \
            --home-dir /app --no-create-home --shell /usr/sbin/nologin csm
fi

groupmod --non-unique --gid "$PGID" csm
usermod --non-unique --uid "$PUID" --gid "$PGID" csm

mkdir -p "$CONFIG_DIR"
# Seul l'appdata est réapproprié : jamais les shares montés, dont on ne doit
# pas toucher les droits.
chown -R "$PUID:$PGID" "$CONFIG_DIR" 2>/dev/null || true

echo "Cloud Sync Manager — UID=$PUID GID=$PGID UMASK=$UMASK appdata=$CONFIG_DIR"

exec gosu "$PUID:$PGID" "$@"
