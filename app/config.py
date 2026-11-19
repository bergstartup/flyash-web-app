"""Runtime configuration. Everything is a plain env var with a sane default."""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("SMCP_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = Path(os.getenv("SMCP_DB", DATA_DIR / "smcp.db"))
DATABASE_URL = f"sqlite+pysqlite:///{DB_PATH}"

# Source workbook used for the one-time migration and the export diff.
SOURCE_WORKBOOK = Path(os.getenv("SMCP_SOURCE_XLSX", BASE_DIR / "JULY.xlsx"))
EXCEL_TEMPLATE = BASE_DIR / "app" / "excel" / "template.xlsx"

# Where the month import keeps the workbooks it was given, and the snapshot it
# takes before each one. scripts/backup.sh writes to the same directory.
IMPORT_DIR = Path(os.getenv("SMCP_IMPORT_DIR", DATA_DIR / "imports"))
BACKUP_DIR = Path(os.getenv("SMCP_BACKUP_DIR", DATA_DIR / "backups"))

# Financial year start month for roll-ups. April = Indian FY. See plan Q9.
FY_START_MONTH = int(os.getenv("SMCP_FY_START_MONTH", "4"))

# Sheet1 column J compares CUMULATIVE power factor in the workbook, while the
# requirement note says daily. The file wins by default. See plan section 12.
PF_MOVEMENT_MODE = os.getenv("SMCP_PF_MOVEMENT", "CUMULATIVE")

# Who is using the app. No passwords -- the LAN-trust decision. The dropdown is
# populated from this list and stamps audit_log.changed_by.
ACTORS = [a.strip() for a in os.getenv(
    "SMCP_ACTORS", "M.DIRECTOR,PAULRAJ.T,IYAPPAN,OFFICE"
).split(",") if a.strip()]
