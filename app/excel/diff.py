"""Cell-by-cell comparison of a generated workbook against the original.

This is the only objective correctness oracle in the project. Values are
compared to 0.01, formulas after whitespace normalisation. Cells covered by an
entry in :data:`WHITELIST` are expected to differ, because the source workbook
is wrong there -- see the error register.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path

import openpyxl

TOLERANCE = 0.01

#: (sheet, cell) -> why the app deliberately disagrees with the register
WHITELIST: dict[tuple[str, str], str] = {
    ("Sheet2", "O40"): "A11 -- hand-typed 374 beside the computed 374.5",
    ("Sheet2", "I38"): "hand-adjusted SUM start row; the app emits one "
                       "consistent range per column",
    ("Sheet2", "J38"): "hand-adjusted SUM start row; the app emits one "
                       "consistent range per column",
    ("Sheet9", "E18"): "A01 -- total row records 0 sold against 100 in the ledger",
    ("Sheet9", "F18"): "A01 -- closing overstated by 100 units",
    ("Sheet1", "J38"): "A10 -- formula reaches back one row too far",
}

#: Sheets whose export is a master rather than a transaction replay. Their rows
#: are compared as a set of records, because the app deliberately collapses
#: duplicates that the register repeats -- see anomaly A12.
RECORD_MODE: dict[str, tuple[int, int, tuple[int, ...]]] = {
    # sheet -> (first data row, last data row, key columns)
    "Sheet13": (5, 81, (2, 3, 4)),
}


@dataclass
class Difference:
    sheet: str
    cell: str
    expected: object
    actual: object
    kind: str

    def __str__(self) -> str:
        return (f"{self.sheet}!{self.cell:<6} {self.kind:<8} "
                f"workbook={_show(self.expected)!r:<24} generated={_show(self.actual)!r}")


@dataclass
class SheetResult:
    sheet: str
    implemented: bool
    differences: list[Difference]
    compared: int = 0
    mode: str = "cells"
    collapsed: int = 0

    @property
    def ok(self) -> bool:
        return self.implemented and not self.differences


def _show(v):
    if isinstance(v, (dt.datetime, dt.date)):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, float) and v == int(v):
        return int(v)
    return v


def _norm_formula(v: str) -> str:
    return re.sub(r"\s+", "", v).upper()


def _comparable(v):
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, str):
        s = re.sub(r"\s+", " ", v).strip()
        return s or None
    return v


def compare_cell(expected, actual) -> str | None:
    """Return a difference kind, or None when the two agree."""
    e, a = _comparable(expected), _comparable(actual)
    if e is None and a is None:
        return None
    if isinstance(e, str) and e.startswith("=") or isinstance(a, str) and str(a).startswith("="):
        if isinstance(e, str) and isinstance(a, str):
            return None if _norm_formula(e) == _norm_formula(a) else "formula"
        return "formula"
    if isinstance(e, (int, float)) and isinstance(a, (int, float)):
        return None if abs(float(e) - float(a)) <= TOLERANCE else "value"
    if e is None or a is None:
        return "missing" if a is None else "extra"
    return None if str(e) == str(a) else "value"


def diff_workbooks(original: Path, generated: Path,
                   implemented: set[str]) -> list[SheetResult]:
    src = openpyxl.load_workbook(str(original))
    gen = openpyxl.load_workbook(str(generated))
    results = []
    for name in src.sheetnames:
        if name not in implemented:
            results.append(SheetResult(name, False, []))
            continue
        a, b = src[name], gen[name]
        if name in RECORD_MODE:
            results.append(_diff_records(name, a, b))
            continue
        rows = max(a.max_row, b.max_row)
        cols = max(a.max_column, b.max_column)
        diffs, compared = [], 0
        for r in range(1, rows + 1):
            for c in range(1, cols + 1):
                compared += 1
                kind = compare_cell(a.cell(r, c).value, b.cell(r, c).value)
                if kind is None:
                    continue
                ref = a.cell(r, c).coordinate
                if (name, ref) in WHITELIST:
                    continue
                diffs.append(Difference(name, ref, a.cell(r, c).value,
                                        b.cell(r, c).value, kind))
        results.append(SheetResult(name, True, diffs, compared))
    return results


def _key(value) -> str | None:
    """Canonical form for record comparison.

    Contact numbers are integers in the source sheet and text in the database,
    so both sides are reduced to a plain string before matching.
    """
    v = _comparable(value)
    if v is None:
        return None
    if isinstance(v, float) and v == int(v):
        v = int(v)
    return str(v).strip().upper() or None


def _records(ws, first: int, last: int, key_cols: tuple[int, ...]) -> list[tuple]:
    out = []
    for r in range(first, last + 1):
        row = tuple(_key(ws.cell(r, c).value) for c in key_cols)
        if any(v is not None for v in row):
            out.append(row)
    return out


def _diff_records(name: str, a, b) -> SheetResult:
    """Compare a master sheet as a set of records rather than by address."""
    first, last, keys = RECORD_MODE[name]
    src = _records(a, first, last, keys)
    gen = _records(b, first, last, keys)
    src_set, gen_set = set(src), set(gen)
    diffs = []
    for rec in sorted(gen_set - src_set, key=str):
        diffs.append(Difference(name, "-", None, rec, "extra"))
    missing = [r for r in src if r not in gen_set]
    collapsed = len(src) - len(src_set)
    for rec in sorted(set(missing), key=str):
        diffs.append(Difference(name, "-", rec, None, "missing"))
    return SheetResult(name, True, diffs, compared=len(src),
                       mode="records", collapsed=collapsed)
