#!/bin/bash
set -euo pipefail

SOURCE_DIR="/var/backups/radar"
RECIPIENT="${RADAR_BACKUP_GPG_RECIPIENT:-}"
DEST="${RADAR_BACKUP_EXTERNAL_DIR:-}"

if [ -z "$RECIPIENT" ] || [ -z "$DEST" ]; then
    echo "Configure RADAR_BACKUP_GPG_RECIPIENT e RADAR_BACKUP_EXTERNAL_DIR." >&2
    exit 2
fi

command -v gpg >/dev/null
test -d "$DEST"
LATEST="$(find "$SOURCE_DIR" -maxdepth 1 -type f -name 'radar_backup_*.tar.gz'     -printf '%T@ %p\n' | sort -nr | head -1 | cut -d' ' -f2-)"
if [ -z "$LATEST" ] || [ ! -f "$LATEST" ]; then
    echo "Nenhum backup do Radar encontrado." >&2
    exit 3
fi

NAME="$(basename "$LATEST").gpg"
TMP="$DEST/.$NAME.tmp"
FINAL="$DEST/$NAME"

gpg --batch --yes --trust-model always     --compress-algo none     --recipient "$RECIPIENT"     --output "$TMP"     --encrypt "$LATEST"

test -s "$TMP"
chmod 600 "$TMP"
mv -f "$TMP" "$FINAL"

find "$DEST" -maxdepth 1 -type f -name 'radar_backup_*.tar.gz.gpg'     -mtime +30 -delete
echo "Backup externo criptografado: $FINAL"
