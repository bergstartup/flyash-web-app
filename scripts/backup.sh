#!/usr/bin/env bash
# Nightly consistent snapshot. VACUUM INTO is safe against a live WAL database,
# which a file copy is not.
set -euo pipefail
cd "$(dirname "$0")/.."

DB="${SMCP_DB:-data/smcp.db}"
DEST="${SMCP_BACKUP_DIR:-data/backups}"
KEEP="${SMCP_BACKUP_KEEP:-30}"
STAMP="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$DEST"
./.venv/bin/python - "$DB" "$DEST/smcp-$STAMP.db" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
con = sqlite3.connect(src)
con.execute("VACUUM INTO ?", (dst,))
con.close()
print(f"snapshot -> {dst}")
PY

# Keep the generated workbook alongside the snapshot.
if [ -f data/JULY-generated.xlsx ]; then
  cp data/JULY-generated.xlsx "$DEST/JULY-$STAMP.xlsx"
fi

ls -1t "$DEST"/smcp-*.db 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm --
echo "backups kept: $(ls -1 "$DEST"/smcp-*.db 2>/dev/null | wc -l) (limit $KEEP)"
