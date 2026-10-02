#!/bin/bash
set -euo pipefail

BASE="/opt/radar"
DEST="/var/backups/radar"
STAMP="$(date +'%Y-%m-%d_%H-%M-%S')"
TMP="$DEST/tmp_$STAMP"
FINAL="$DEST/radar_backup_$STAMP.tar.gz"

mkdir -p "$TMP"

echo "[$(date)] Iniciando backup Radar..."

for f in \
    .env \
    monitor_recuperacao.json \
    destinos_confirmados.json \
    kabum_feed_atual.csv.gz \
    17729-46967-pt_BR-Kabum_BR_Datafeed.csv.gz
do
    if [ -f "$BASE/$f" ]; then
        cp "$BASE/$f" "$TMP/"
    fi
done

python3 - <<PY
import sqlite3
from pathlib import Path

base = Path("$BASE")
dest = Path("$TMP")

for nome in [
    "publicacoes.sqlite3",
    "kabum_historico.sqlite3",
    "monitor_ofertas.session",
]:
    origem = base / nome
    destino = dest / nome

    if origem.exists():
        src = sqlite3.connect(str(origem))
        dst = sqlite3.connect(str(destino))
        src.backup(dst)
        dst.close()
        src.close()
        print("Backup SQLite OK:", nome)
PY

if [ -d "$BASE/imagens_ofertas" ]; then
    cp -a "$BASE/imagens_ofertas" "$TMP/"
fi

tar -C "$TMP" -czf "$FINAL" .

chmod 600 "$FINAL"
rm -rf "$TMP"

tar -tzf "$FINAL" >/dev/null

find "$DEST" \
    -type f \
    -name 'radar_backup_*.tar.gz' \
    -mtime +7 \
    -delete

echo "[$(date)] Backup concluído:"
echo "$FINAL"
du -h "$FINAL"
