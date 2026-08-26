"""Print the July reconciliation table: derived value vs the workbook."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from app.db.session import engine
from app.reports.reconciliation import EXPECTED, measure

if __name__ == "__main__":
    with engine.connect() as conn:
        rows = measure(conn)
    w = max(len(r.label) for r in rows)
    ok = bad = 0
    for r in rows:
        status = "OK " if r.matches else "BAD"
        if r.matches: ok += 1
        else: bad += 1
        note = "" if r.matches else f"   <-- expected {r.expected:,.2f}"
        print(f"[{status}] {r.label:<{w}}  {r.actual:>14,.2f}{note}")
    print(f"\n{ok} matched, {bad} did not.")
    sys.exit(1 if bad else 0)
