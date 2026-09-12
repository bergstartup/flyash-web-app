"""Cell-by-cell comparison of a generated workbook against the original.

This is the only objective correctness oracle in the project. Values are
compared to 0.01, formulas after whitespace normalisation. Cells covered by an
entry in :data:`WHITELIST` are expected to differ, because the source workbook
is wrong there -- see the error register.
"""

from __future__ import annotations

import datetime as dt
import difflib
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
    ("Sheet3", "V29"): "A05 -- a day's wage typed for MANJULA on 20 Jul while "
                       "the muster records her absent; the whole 5,800 vs "
                       "5,400 gap is this one cell",
    ("Sheet3", "P44"): "hand-adjusted SUM start row for a labour who joined "
                       "late; the app emits one consistent range per column",
    ("Sheet3", "Q44"): "hand-adjusted SUM start row for a labour who joined "
                       "late; the app emits one consistent range per column",
    ("Sheet3", "R44"): "hand-adjusted SUM start row for a labour who joined "
                       "late; the app emits one consistent range per column",
    ("Sheet3", "S44"): "hand-adjusted SUM start row for a labour who joined "
                       "late; the app emits one consistent range per column",
    ("Sheet14", "L5"): "per-day SUM stops at column I, omitting BIRU and "
                       "VIKRAM; the app sums the whole roster",
    ("Sheet14", "L6"): "per-day SUM stops at column I, omitting BIRU and "
                       "VIKRAM; the app sums the whole roster",
    ("Sheet14", "L7"): "per-day SUM stops at column I, omitting BIRU and "
                       "VIKRAM; the app sums the whole roster",
    ("Sheet14", "L8"): "per-day SUM stops at column I, omitting BIRU and "
                       "VIKRAM; the app sums the whole roster",
    ("Sheet15", "F15"): "A06 -- MANJULA's advance is 6,000 in the advance "
                        "register and 5,400 here; the register is the source",
    ("Sheet15", "F18"): "A07 -- IYAPPAN has no advance entries at all, yet "
                        "12,000 is recorded as paid",
    ("Sheet15", "F19"): "A07b -- M.DIRECTOR has no advance column, yet 75,000 "
                        "is recorded as paid",
    ("Sheet9", "E18"): "A01 -- total row records 0 sold against 100 in the ledger",
    ("Sheet9", "F18"): "A01 -- closing overstated by 100 units",
    ("Sheet1", "J38"): "A10 -- formula reaches back one row too far",
}

# Sheet15's S.No is hand-typed: it repeats 7 for UGAN and so runs one short of
# the roster it numbers. The app numbers the rows it actually lays out.
WHITELIST.update({
    ("Sheet15", f"A{row}"): "hand-typed S.No repeats 7 for UGAN and runs one "
                            "short; the app numbers the roster sequentially"
    for row in range(12, 20)
})

# The four opening cells Sheet5 carried as a formula instead of a figure. The
# app writes the ledger's own opening everywhere, as the register does in its
# other 177 opening cells.
WHITELIST.update({
    ("Sheet5", cell): "opening carried as a formula here and as a figure in "
                      "the sheet's other 177 opening cells"
    for cell in ("C6", "C7", "C8", "C14")
})

# The ledger sheets and Sheet6 spell the same vehicle or village differently.
# One master record wins, and it is the one the sales register named -- the
# same collapse Sheet13 makes for customers (anomaly A12). Sheet5 H25 is the
# clearest case: the master carries Sheet6's DHESHIGA TRACTOT, a typo for the
# DHESHIGA TRACTOR the ledger sheet spells correctly.
WHITELIST.update({
    (sheet, cell): "the ledger sheet and Sheet6 spell this differently; the "
                   "master keeps one record, under Sheet6's spelling"
    for sheet, cell in [
        ("Sheet5", "H19"), ("Sheet5", "H25"), ("Sheet5", "I30"),
        ("Sheet5", "I35"), ("Sheet5", "I54"), ("Sheet5", "H113"),
        ("Sheet5", "H115"), ("Sheet5", "H150"), ("Sheet5", "I157"),
        ("Sheet5", "I183"), ("Sheet8", "H25"), ("Sheet8", "H26"),
    ]
})

# Five July days ran a second production shift. Rule 5 keeps one production
# entry per product per day, by decision, so the app posts the day's total on
# the DUTY row and the AFTER 5 PM row has nothing to correspond to. The day's
# closing stock is unaffected -- only the split is lost.
_SHIFT = ("a second production shift, summed into the day's single entry "
          "under validation rule 5")
WHITELIST.update({("Sheet5", where): _SHIFT for where in (
    "AFTER 5 PM 2400 0", "AFTER 5 PM 2000 0", "AFTER 5 PM 300 0",
    "2026-07-17 1000 0",
    "A70", "D70", "J70", "A81", "D81", "J81", "A130", "D130", "J130",
    "A143", "D143", "J143", "D109", "C110", "C111", "J111",
)})

# Three days where Sheet5 lists the day's sales in a different order from
# Sheet6, and one where Sheet8 does. Same sales, same closing stock; only the
# openings between them move. Sheet6's S.No is the order of record.
_ORDER = ("Sheet6 and the ledger sheet list this day's sales in different "
          "orders; Sheet6's S.No is the order of record")
WHITELIST.update({("Sheet5", where): _ORDER for where in (
    "2026-07-05 0 2000", "2026-07-29 0 500",
    "2026-07-30 0 400", "2026-07-30 0 350", "2026-07-30 0 750",
    "B30", "C30", "A173", "B173", "C173",
    "A180", "B180", "C180", "B181", "C181",
)})
WHITELIST.update({("Sheet8", where): _ORDER for where in (
    "2026-07-30 0 35", "A29", "B29", "B31", "B32", "B33",
)})

# A02: a 450-unit sale that reached the sales register and never reached the
# stock ledger, so the register's closing stock is 450 too high.
WHITELIST[("Sheet8", "2026-07-30 0 450")] = (
    "A02 -- a sale Sheet6 records and Sheet8 never posted")

#: Sheets whose export is a master rather than a transaction replay. Their rows
#: are compared as a set of records, because the app deliberately collapses
#: duplicates that the register repeats -- see anomaly A12.
RECORD_MODE: dict[str, tuple[int, int, tuple[int, ...]]] = {
    # sheet -> (first data row, last data row, key columns)
    "Sheet13": (5, 81, (2, 3, 4)),
}

#: Sheets whose data block is compared as a *sequence* of movements rather than
#: address by address, because the two sides legitimately differ in how many
#: rows they post:
#:
#: * a day split into a DUTY and an AFTER 5 PM production shift is one entry
#:   here, by decision, and two rows in the register;
#: * the register drops movements it forgot to post -- anomaly A02 is a
#:   450-unit sale that reached Sheet6 and never reached Sheet8.
#:
#: Either one shifts every row below it, which would drown the real differences
#: in hundreds of off-by-one ones. Rows are aligned on what moved, then
#: compared cell by cell; rows that align nowhere are reported as a missing or
#: extra movement. Headers above the block and the footer below it are still
#: compared by address.
SEQUENCE_MODE: dict[str, tuple[int, int, int, tuple[int, ...]]] = {
    # sheet -> (first data row, last data row, last column, key columns)
    "Sheet5": (5, 187, 10, (4, 6)),
    "Sheet8": (5, 35, 8, (3, 5)),
    "Sheet9": (5, 17, 8, (3, 5)),
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
        if name in SEQUENCE_MODE:
            results.append(_diff_sequence(name, a, b))
            continue
        results.append(_diff_cells(name, a, b))
    return results


def _diff_cells(name: str, a, b, skip: range | None = None) -> SheetResult:
    rows = max(a.max_row, b.max_row)
    cols = max(a.max_column, b.max_column)
    diffs, compared = [], 0
    for r in range(1, rows + 1):
        if skip is not None and r in skip:
            continue
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
    return SheetResult(name, True, diffs, compared)


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


# ---------------------------------------------------------------------------
# Sequence comparison, for the ledgers whose row counts legitimately differ
# ---------------------------------------------------------------------------

DITTO_MARKS = {'"', "''", "''"}


def _rebase(value, row: int):
    """Make a self-referencing formula comparable at a different row.

    ``=B29+C29`` on row 29 and ``=B30+C30`` on row 30 say the same thing. Only
    references to the formula's own row are blanked; a reference to any other
    row is real information and is left alone.
    """
    if not isinstance(value, str) or not value.startswith("="):
        return value
    return re.sub(rf"([A-Z]{{1,3}}){row}\b", r"\1#", value.upper())


def _movement_rows(ws, first: int, last: int, cols: int,
                   key_cols: tuple[int, ...]) -> list[tuple]:
    """Rows of a ledger block as (row number, alignment key, values).

    The date column dittos, so it is carried forward before being used: two
    rows only align if they are the same movement on the same day.
    """
    out, carried = [], None
    for r in range(first, last + 1):
        values = [ws.cell(r, c).value for c in range(1, cols + 1)]
        if all(v is None for v in values):
            continue
        head = _comparable(values[0])
        if head is not None and head not in DITTO_MARKS:
            carried = head
        key = (carried,) + tuple(_key(ws.cell(r, c).value) for c in key_cols)
        out.append((r, key, values))
    return out


def _summarise(key) -> str:
    day = key[0]
    if isinstance(day, (dt.date, dt.datetime)):
        day = day.strftime("%Y-%m-%d")
    return " ".join(str(v) for v in (day,) + tuple(key[1:]) if v is not None)


def _diff_sequence(name: str, a, b) -> SheetResult:
    first, last, cols, key_cols = SEQUENCE_MODE[name]
    src = _movement_rows(a, first, last, cols, key_cols)
    gen = _movement_rows(b, first, last, cols, key_cols)

    # Everything outside the movement block -- headers and the footer -- still
    # lives at a fixed address and is compared there.
    result = _diff_cells(name, a, b, skip=range(first, last + 1))
    diffs = result.differences
    compared = result.compared

    matcher = difflib.SequenceMatcher(
        None, [r[1] for r in src], [r[1] for r in gen], autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            pairs = zip(src[i1:i2], gen[j1:j2])
        else:
            pairs = zip(src[i1:i2], gen[j1:j2])
            # A row that aligns nowhere is whitelisted by what moved, not by
            # where it sat: the row number shifts with every divergence above
            # it, and the movement is the durable fact.
            for row, key, _values in src[i1 + (j2 - j1):i2]:
                if (name, _summarise(key)) in WHITELIST:
                    continue
                diffs.append(Difference(name, f"row {row}", _summarise(key),
                                        None, "missing"))
            for row, key, _values in gen[j1 + (i2 - i1):j2]:
                if (name, _summarise(key)) in WHITELIST:
                    continue
                diffs.append(Difference(name, f"row {row}", None,
                                        _summarise(key), "extra"))
        for (src_row, _sk, src_values), (gen_row, _gk, gen_values) in pairs:
            for c, (expected, actual) in enumerate(
                    zip(src_values, gen_values), start=1):
                compared += 1
                kind = compare_cell(_rebase(expected, src_row),
                                    _rebase(actual, gen_row))
                if kind is None:
                    continue
                ref = a.cell(src_row, c).coordinate
                if (name, ref) in WHITELIST:
                    continue
                diffs.append(Difference(name, ref, expected, actual, kind))
    return SheetResult(name, True, diffs, compared, mode="movements")
