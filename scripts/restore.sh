#!/usr/bin/env bash
# Restore a snapshot. Rehearse this on the plant machine -- an untested backup
# is not a backup.
set -euo pipefail
cd "$(dirname "$0")/.."

SNAPSHOT="${1:-}"
DB="${SMCP_DB:-data/smcp.db}"
if [ -z "$SNAPSHOT" ]; then
  echo "usage: scripts/restore.sh data/backups/smcp-YYYYmmdd-HHMMSS.db" >&2
  ls -1t data/backups/smcp-*.db 2>/dev/null | head -5 >&2 || true
  exit 2
fi
[ -f "$SNAPSHOT" ] || { echo "no such snapshot: $SNAPSHOT" >&2; exit 1; }

./.venv/bin/python - "$SNAPSHOT" <<'PY'
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
n = con.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
con.execute("PRAGMA integrity_check").fetchone()
print(f"snapshot verified: {n} tables, integrity ok")
PY

if [ -f "$DB" ]; then
  mv "$DB" "$DB.replaced-$(date +%Y%m%d-%H%M%S)"
fi
rm -f "$DB-wal" "$DB-shm"
cp "$SNAPSHOT" "$DB"
echo "restored $SNAPSHOT -> $DB"
