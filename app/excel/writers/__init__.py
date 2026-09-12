"""Sheet writer registry.

A sheet with no writer yet is reported by the diff harness as NOT IMPLEMENTED
rather than silently passing, so coverage is always visible.
"""

from __future__ import annotations

from app.excel.writers import (sheet02, sheet03, sheet04, sheet05, sheet06,
                               sheet08, sheet09, sheet13, sheet14, sheet15, sheet16)

WRITERS = {w.sheet: w for w in (
    sheet02.Sheet02Writer, sheet03.Sheet03Writer, sheet04.Sheet04Writer,
    sheet05.Sheet05Writer, sheet06.Sheet06Writer, sheet08.Sheet08Writer,
    sheet09.Sheet09Writer,
    sheet13.Sheet13Writer, sheet14.Sheet14Writer, sheet15.Sheet15Writer,
    sheet16.Sheet16Writer,
)}

ALL_SHEETS = [f"Sheet{i}" for i in range(1, 21)]
