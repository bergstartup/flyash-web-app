"""Rediscover the error register from the source workbook.

Each check reads JULY.xlsx (values *and* formulas) and, where relevant, the
imported database, and reports where the register disagrees with itself. These
are not bugs in the app -- they are figures somebody has to adjudicate before
opening balances are set, which is why they land in a table rather than an
exception.

Every one of them is the same failure: a number that should have been derived
was typed instead.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Callable, Iterator

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import models as m
from app.importer import source as S
from app.importer.names import norm, resolve

CHECKS: list[Callable] = []


def check(fn: Callable) -> Callable:
    CHECKS.append(fn)
    return fn


def _a(code, sheet, cell, summary, workbook=None, derived=None, diff=None,
       severity="ERROR") -> m.ImportAnomaly:
    fmt = lambda v: None if v is None else (
        f"{v:,.10g}" if isinstance(v, (int, float)) else str(v))
    return m.ImportAnomaly(
        code=code, sheet=sheet, cell=cell, severity=severity, summary=summary,
        workbook_value=fmt(workbook), derived_value=fmt(derived),
        difference=fmt(diff))


# ---------------------------------------------------------------------------
# 1 & 2: stock ledgers that disagree with themselves
# ---------------------------------------------------------------------------

@check
def a01_sheet9_total_row(wbv, wbf, db) -> Iterator[m.ImportAnomaly]:
    ws = wbv["Sheet9"]
    printed_sales = S.num(ws.cell(18, 5).value) or 0.0
    printed_closing = S.num(ws.cell(18, 6).value) or 0.0
    running = sum(S.num(ws.cell(r, 5).value) or 0.0 for r in range(5, 18))
    derived = db.execute(text("""
        SELECT sales, closing FROM stock_month_summary s
        JOIN product p ON p.id = s.product_id WHERE p.ledger_sheet = 'Sheet9'
    """)).first()
    if derived and abs(printed_closing - derived[1]) > 0.01:
        yield _a("A01", "Sheet9", "E18/F18",
                 "Total row records 0 sold while the ledger rows show "
                 f"{running:,.0f} sold on 21 Jul. Printed closing stock is "
                 f"{printed_closing:,.0f}; the ledger itself says "
                 f"{derived[1]:,.0f}.",
                 workbook=printed_closing, derived=derived[1],
                 diff=printed_closing - derived[1])


@check
def a02_sheet8_missing_sale(wbv, wbf, db) -> Iterator[m.ImportAnomaly]:
    ws = wbv["Sheet8"]
    ledger, carry = {}, None
    for r in range(5, 36):
        carry = S.date_column(ws, r, 1, carry)
        qty = S.num(ws.cell(r, 5).value)
        if carry and qty:
            ledger[carry] = ledger.get(carry, 0.0) + qty
    register = {}
    for d, qty in db.execute(text("""
        SELECT s.sale_date, SUM(l.qty) FROM sale s
        JOIN sale_line l ON l.sale_id = s.id
        JOIN product p ON p.id = l.product_id
        WHERE p.ledger_sheet = 'Sheet8' GROUP BY s.sale_date""")):
        register[dt.date.fromisoformat(str(d))] = float(qty)
    for d in sorted(set(ledger) | set(register)):
        gap = round(register.get(d, 0.0) - ledger.get(d, 0.0), 2)
        if gap > 0.01:
            yield _a("A02", "Sheet8", d.isoformat(),
                     f"A {gap:,.0f}-unit 4\" solid sale is in the sales "
                     "register but was never posted to the stock ledger, so "
                     "closing stock is overstated.",
                     workbook=ledger.get(d, 0.0), derived=register.get(d, 0.0),
                     diff=gap)


# ---------------------------------------------------------------------------
# 3 & 4: cement
# ---------------------------------------------------------------------------

@check
def a03_cement_lot_split(wbv, wbf, db) -> Iterator[m.ImportAnomaly]:
    ws = wbv["Sheet12"]
    for r in range(5, 36):
        closing = S.num(ws.cell(r, 8).value)
        split = S.num(ws.cell(r, 18).value)
        if closing is None or split is None or abs(closing - split) < 0.01:
            continue
        d = S.date(ws.cell(r, 1).value)
        yield _a("A03", "Sheet12", f"H{r}/R{r}",
                 f"The by-lot split totals {split:,.1f} bags against a closing "
                 f"stock of {closing:,.1f} on {d}. The 31-bag 31 Jul lot was "
                 "entered a day early.",
                 workbook=split, derived=closing, diff=split - closing)


@check
def a04_fap_cement_mismatch(wbv, wbf, db) -> Iterator[m.ImportAnomaly]:
    s10 = S.num(wbv["Sheet10"].cell(27, 11).value)
    s12 = S.num(wbv["Sheet12"].cell(38, 4).value)
    if s10 is None or s12 is None or abs(s10 - s12) < 0.01:
        return
    yield _a("A04", "Sheet10/Sheet12", "K27 vs D38",
             f"FAP cement inward for July: the raw-material register says "
             f"{s10:,.0f} bags, the cement log says {s12:,.0f}.",
             workbook=s10, derived=s12, diff=s12 - s10)


# ---------------------------------------------------------------------------
# 5, 6, 7: wages and advances
# ---------------------------------------------------------------------------

def _labour_index(db) -> dict[str, tuple[int, str, str, float]]:
    rows = db.execute(text(
        "SELECT id, name, wage_type, rate FROM labour")).all()
    return {norm(r[1]): tuple(r) for r in rows}


@check
def a05_sheet3_regular_wage(wbv, wbf, db) -> Iterator[m.ImportAnomaly]:
    ws = wbv["Sheet3"]
    index = _labour_index(db)
    for col in range(2, 26, 2):
        name = S.text(ws.cell(4, col).value)
        typed = S.num(ws.cell(44, col).value)
        lab = resolve(name, index)
        if typed is None or lab is None or lab[2] != "DAILY":
            continue
        duty = db.execute(text(
            "SELECT COALESCE(SUM(shift_units),0) FROM attendance "
            "WHERE labour_id = :i"), {"i": lab[0]}).scalar()
        derived = float(duty or 0.0) * float(lab[3])
        if abs(typed - derived) < 0.01:
            continue
        yield _a("A05", "Sheet3", f"{_col(col)}44",
                 f"{lab[1]}: regular wage totals Rs {typed:,.0f}, but "
                 f"attendance x rate is Rs {derived:,.0f}.",
                 workbook=typed, derived=derived, diff=typed - derived)


@check
def a06_a07_advance_mismatch(wbv, wbf, db) -> Iterator[m.ImportAnomaly]:
    s4, s15 = wbv["Sheet4"], wbv["Sheet15"]
    index = _labour_index(db)
    columns: dict[int, tuple] = {}
    for col in range(2, 17):
        nm = S.text(s4.cell(2, col).value)
        if not nm or nm.upper() == "TOTAL":
            continue
        lab = resolve(nm, index)
        if lab:
            columns[lab[0]] = (col, nm)
    for r in range(4, 20):
        name = S.text(s15.cell(r, 2).value)
        lab = resolve(name, index)
        if lab is None:
            continue
        stated = S.num(s15.cell(r, 6).value) or 0.0
        entry = columns.get(lab[0])
        if entry is None:
            # Structural, not arithmetic: the person has no Sheet4 column at
            # all. M.DIRECTOR is the only case, and it is the same gap as C15.
            if stated:
                yield _a("A07b", "Sheet4/Sheet15", f"F{r}",
                         f"{lab[1]}: Rs {stated:,.0f} recorded as paid in "
                         "Sheet15, but this person has no column in the "
                         "advance register at all.",
                         workbook=None, derived=stated, diff=stated,
                         severity="INFO")
            continue
        col = entry[0]
        dated = sum(S.num(s4.cell(rr, col).value) or 0.0 for rr in range(4, 19))
        if abs(stated - dated) < 0.01:
            continue
        code = "A07" if dated == 0 else "A06"
        detail = ("The advance column exists but is empty, yet Sheet15 shows "
                  f"Rs {stated:,.0f} paid."
                  if dated == 0 else
                  f"Sheet4 column totals Rs {dated:,.0f}; Sheet15 records the "
                  f"advance as Rs {stated:,.0f}.")
        yield _a(code, "Sheet4/Sheet15", f"F{r}", f"{lab[1]}: {detail}",
                 workbook=dated, derived=stated, diff=stated - dated)


# ---------------------------------------------------------------------------
# 8: four receivable registers, four totals
# ---------------------------------------------------------------------------

@check
def a08_receivable_totals(wbv, wbf, db) -> Iterator[m.ImportAnomaly]:
    totals = {
        "Sheet7 delivery register": S.num(wbv["Sheet7"].cell(50, 7).value),
        "Sheet17 top block": S.num(wbv["Sheet17"].cell(11, 5).value),
        "Sheet17 grand total": S.num(wbv["Sheet17"].cell(74, 4).value),
        "Sheet18 grand total": S.num(wbv["Sheet18"].cell(33, 8).value),
    }
    values = [v for v in totals.values() if v is not None]
    if len(set(values)) <= 1:
        return
    spread = max(values) - min(values)
    listing = " | ".join(f"{k} Rs {v:,.0f}" for k, v in totals.items() if v)
    yield _a("A08", "Sheet7/17/18", "totals",
             "Four registers, four outstanding totals, none reconciling: "
             + listing,
             workbook=max(values), derived=min(values), diff=spread)


# ---------------------------------------------------------------------------
# 9, 10, 11: power and attendance
# ---------------------------------------------------------------------------

@check
def a09_power_gap(wbv, wbf, db) -> Iterator[m.ImportAnomaly]:
    ws = wbv["Sheet1"]
    for r in range(5, 39):
        d = S.date(ws.cell(r, 1).value)
        kwh_raw = ws.cell(r, 2).value
        if d is None or kwh_raw is None or S.num(kwh_raw) is not None:
            continue
        note = " / ".join(x for x in (S.raw_text(ws.cell(r, 2).value),
                                      S.raw_text(ws.cell(r, 3).value)) if x)
        yield _a("A09", "Sheet1", f"B{r}/C{r}",
                 f"Meter columns for {d} hold the text '{note}' instead of a "
                 "reading. The next day's delta silently spans two days.",
                 workbook=note, derived="no reading", diff=1)


@check
def a10_power_formula_offset(wbv, wbf, db) -> Iterator[m.ImportAnomaly]:
    ws = wbf["Sheet1"]
    for r in range(7, 39):
        f = ws.cell(r, 10).value
        if not isinstance(f, str) or not f.startswith("="):
            continue
        refs = [int(x) for x in re.findall(r"I(\d+)", f)]
        if len(refs) != 2:
            continue
        # J compares this row's cumulative PF against the previous DATA row.
        prev = _previous_data_row(ws, r)
        if refs[0] == r and refs[1] != prev:
            yield _a("A10", "Sheet1", f"J{r}",
                     f"PF-movement formula {f} reaches back to I{refs[1]} "
                     f"instead of I{prev}, skipping a row.",
                     workbook=f, derived=f"=I{r}-I{prev}", diff="1 row")


def _previous_data_row(ws, row: int) -> int:
    for r in range(row - 1, 4, -1):
        if S.num(ws.cell(r, 2).value) is not None or isinstance(
                ws.cell(r, 4).value, str):
            if ws.cell(r, 10).value is not None or ws.cell(r, 9).value is not None:
                return r
    return row - 1


@check
def a11_attendance_typed_total(wbv, wbf, db) -> Iterator[m.ImportAnomaly]:
    ws = wbv["Sheet2"]
    derived = db.execute(text("SELECT SUM(shift_units) FROM attendance")).scalar()
    derived = float(derived or 0.0)
    for r in range(36, 42):
        for c in range(1, 18):
            v = S.num(ws.cell(r, c).value)
            if v is None or v < 100:
                continue
            if abs(v - derived) < 0.01:
                continue
            if ws.cell(r, c).value is not None and not isinstance(
                    _formula(wbf["Sheet2"], r, c), str):
                yield _a("A11", "Sheet2", f"{_col(c)}{r}",
                         f"A hand-typed grand total of {v:,.10g} sitting beside "
                         f"the computed {derived:,.10g}.",
                         workbook=v, derived=derived, diff=v - derived)


def _formula(ws, r, c):
    return ws.cell(r, c).value


def _col(idx: int) -> str:
    import openpyxl
    return openpyxl.utils.get_column_letter(idx)


@check
def a12_duplicate_customers(wbv, wbf, db) -> Iterator[m.ImportAnomaly]:
    """Sheet13 is a customer master kept as a transaction log.

    Two kinds of repeat appear, and they need opposite treatment: the same
    person entered twice (collapse), and different people who share a first
    name (keep apart). Reporting both is the point -- merging the second kind
    would put one debtor's balance on another.
    """
    ws = wbv["Sheet13"]
    by_name: dict[str, list[int]] = {}
    for r in range(5, 82):
        name = S.text(ws.cell(r, 2).value)
        if name:
            by_name.setdefault(norm(name), []).append(r)
    for key, rows in by_name.items():
        if len(rows) < 2:
            continue
        records = {
            r: (norm(S.text(ws.cell(r, 3).value)), norm(S.text(ws.cell(r, 4).value)))
            for r in rows
        }
        cells = ", ".join(f"B{r}" for r in rows)
        spellings = sorted({S.text(ws.cell(r, 2).value) for r in rows})
        label = " / ".join(f"'{s}'" for s in spellings)
        if len(set(records.values())) == 1:
            yield _a("A12", "Sheet13", cells,
                     f"Customer entered {len(rows)} times as {label} with the "
                     "same village and contact. The master collapses these "
                     "into one record.",
                     workbook=len(rows), derived=1, diff=len(rows) - 1,
                     severity="WARN")
        else:
            places = " vs ".join(
                S.text(ws.cell(r, 3).value) or "?" for r in rows)
            yield _a("A13", "Sheet13", cells,
                     f"{len(rows)} different customers share the name {label} "
                     f"({places}) with different contact numbers. They are kept "
                     "apart; matching on the name alone would merge them.",
                     workbook=len(rows), derived=len(rows), diff=0,
                     severity="INFO")


@check
def a14_credit_sales_without_a_customer(wbv, wbf, db) -> Iterator[m.ImportAnomaly]:
    """C11 made concrete.

    Sheet6's consumer column is a free-text destination -- villages, people,
    landmarks -- so a credit sale records where the load went but not who owes
    for it. Those sales cannot enter a receivables ledger at all.
    """
    row = db.execute(text("""
        SELECT COUNT(*), COALESCE(SUM(l.amount), 0) FROM sale s
        JOIN sale_line l ON l.sale_id = s.id
        WHERE s.payment_mode = 'CREDIT' AND s.movement_type = 'SALE'
          AND s.customer_id IS NULL""")).first()
    if not row or not row[0]:
        return
    yield _a("A14", "Sheet6", "I column",
             f"{row[0]} credit sales worth Rs {row[1]:,.0f} name a destination "
             "but no customer, so they cannot be charged to anybody. Customer "
             "identity has to be captured at entry from now on.",
             workbook=row[0], derived=0, diff=row[1], severity="WARN")


# ---------------------------------------------------------------------------

def detect(db: Session, path) -> list[m.ImportAnomaly]:
    wbv = S.load(str(path), values=True)
    wbf = S.load(str(path), values=False)
    found: list[m.ImportAnomaly] = []
    for fn in CHECKS:
        found.extend(fn(wbv, wbf, db))
    return found


def record(db: Session, path) -> list[m.ImportAnomaly]:
    db.query(m.ImportAnomaly).delete()
    found = detect(db, path)
    db.add_all(found)
    db.commit()
    return found
