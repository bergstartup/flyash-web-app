"""Rebuild the database from JULY.xlsx. Destructive by design."""
import subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DB_PATH, SOURCE_WORKBOOK
from app.db.session import SessionLocal
from app.importer.july import import_july

if __name__ == "__main__":
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(DB_PATH) + suffix)
        if p.exists():
            p.unlink()
    subprocess.run(["./.venv/bin/alembic", "upgrade", "head"], check=True)
    with SessionLocal() as db:
        stats = import_july(db, SOURCE_WORKBOOK)
    print(f"Imported {SOURCE_WORKBOOK.name}:")
    print(stats)
