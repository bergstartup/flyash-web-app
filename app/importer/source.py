"""Read-only access to the source workbook.

The July register mixes numbers, dates, ditto marks and free text in the same
columns, so every read goes through a coercion helper rather than trusting the
cell type.
"""

from __future__ import annotations

import datetime as dt
import re
from functools import lru_cache
from pathlib import Path

import openpyxl
from openpyxl.worksheet.worksheet import Worksheet

DITTO = {'"', "''", "”", "“", "''"}
NIL_TOKENS = {"NIL", "---", "--", "-", "/", ""}


@lru_cache(maxsize=4)
def load(path: str | Path, values: bool = True):
    """Load the workbook. ``values=True`` returns cached results, not formulas."""
    return openpyxl.load_workbook(str(path), data_only=values)


def num(value) -> float | None:
    """Coerce to a number, or None. Text such as LEAVE or NIL becomes None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if cleaned.upper() in NIL_TOKENS:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def text(value) -> str | None:
    """Collapse whitespace. Returns None for blanks and NIL-ish markers."""
    if value is None:
        return None
    if isinstance(value, (dt.datetime, dt.date)):
        return value.strftime("%Y-%m-%d")
    s = re.sub(r"\s+", " ", str(value)).strip()
    if not s or s.upper() in NIL_TOKENS:
        return None
    return s


def raw_text(value) -> str | None:
    """Like :func:`text` but keeps NIL markers, for round-trip fidelity."""
    if value is None:
        return None
    if isinstance(value, (dt.datetime, dt.date)):
        return value.strftime("%Y-%m-%d")
    s = re.sub(r"\s+", " ", str(value)).strip()
    return s or None


def date(value) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        s = value.strip()
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y"):
            try:
                return dt.datetime.strptime(s, fmt).date()
            except ValueError:
                continue
    return None


def is_ditto(value) -> bool:
    return isinstance(value, str) and value.strip() in DITTO


def date_column(ws: Worksheet, row: int, col: int = 1, carry: dt.date | None = None):
    """Resolve a date cell that may be a ditto mark repeating the row above."""
    cell = ws.cell(row, col).value
    d = date(cell)
    if d is not None:
        return d
    if is_ditto(cell) or cell is None:
        return carry
    return carry


def iter_rows(ws: Worksheet, first: int, last: int):
    for r in range(first, last + 1):
        yield r


def cell(ws: Worksheet, row: int, col: int):
    return ws.cell(row, col).value


def col_letter_to_index(letter: str) -> int:
    return openpyxl.utils.column_index_from_string(letter)
