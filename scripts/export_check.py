"""Generate July from the database and diff it against JULY.xlsx."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DATA_DIR, SOURCE_WORKBOOK
from app.db.session import engine
from app.excel.workbook import build_month
from app.excel.diff import diff_workbooks
from app.excel.writers import WRITERS

if __name__ == "__main__":
    out = DATA_DIR / "JULY-generated.xlsx"
    with engine.connect() as conn:
        status = build_month(conn, 2026, 7, out)
    results = diff_workbooks(SOURCE_WORKBOOK, out, set(WRITERS))
    todo = [r.sheet for r in results if not r.implemented]
    print(f"Generated {out.name}\n")
    total = 0
    for r in results:
        if not r.implemented:
            continue
        total += len(r.differences)
        mark = "PASS" if r.ok else "FAIL"
        print(f"[{mark}] {r.sheet}: {r.compared:,} cells compared, "
              f"{len(r.differences)} differences")
        for d in r.differences[:12]:
            print(f"        {d}")
        if len(r.differences) > 12:
            print(f"        ... and {len(r.differences) - 12} more")
    print(f"\n{len(WRITERS)} of 20 sheets implemented; {total} unexplained "
          f"differences.")
    print("Awaiting writers: " + ", ".join(todo))
