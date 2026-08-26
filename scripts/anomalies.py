"""Rediscover the error register from JULY.xlsx and store it."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import SOURCE_WORKBOOK
from app.db.session import SessionLocal
from app.importer.anomalies import record

if __name__ == "__main__":
    with SessionLocal() as db:
        found = record(db, SOURCE_WORKBOOK)
    for a in found:
        loc = f"{a.sheet} {a.cell}".strip()
        print(f"{a.code}  {loc:<22}  {a.summary}")
        if a.difference:
            print(f"      workbook={a.workbook_value}  derived={a.derived_value}"
                  f"  difference={a.difference}")
    codes = sorted({a.code for a in found})
    print(f"\n{len(found)} findings across {len(codes)} checks: {', '.join(codes)}")
