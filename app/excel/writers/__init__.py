"""Sheet writer registry.

A sheet with no writer yet is reported by the diff harness as NOT IMPLEMENTED
rather than silently passing, so coverage is always visible.
"""

from __future__ import annotations

from app.excel.writers import sheet02, sheet13

WRITERS = {w.sheet: w for w in (sheet02.Sheet02Writer, sheet13.Sheet13Writer)}

ALL_SHEETS = [f"Sheet{i}" for i in range(1, 21)]
