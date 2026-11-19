"""Reset one month to what a register workbook says.

Everything the app holds for the month is cleared and read back from the
file: attendance, extra work, advances, salary settlements, sales, damages,
production, deliveries, material deliveries, cement use and lots, power
readings, the digital ledger, the pallet and bike registers -- and the
month's openings and the debtor snapshot at its end, both taken from the file.

Nothing outside the month is touched. Masters are matched by name and only
added to; a wage or a product that differs in the file is left as the app has
it, because changing a master would rewrite other months.

``preview`` does the whole import inside a transaction and rolls it back, so
what the operator confirms is exactly what will happen.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import MaterialLot
from app.importer import source as S
from app.importer.workbook import (CEMENT, LayoutError, WorkbookImporter,
                                   check_layout, next_month, workbook_month)
from app.reports import opening as opening_rpt
from app.services.audit import record
from app.services.period import guard

# What the screen calls each register, in the order the plant reads them.
# (label, rows held for the month, import stats keys that refill it)
REGISTERS = (
    ("Attendance", "attendance", ("attendance",)),
    ("Extra work", "extra_work",
     ("extra_work_unloading", "extra_work_adjustment")),
    ("Advances", "advance", ("advance",)),
    ("Salary settlements", "salary_settlement", ("salary_settlement",)),
    ("Sales and damages", "sale", ("sale", "damage")),
    ("Production", "production", ("production",)),
    ("Stock openings", "stock_opening", ("stock_opening",)),
    ("Delivery notes", "delivery", ("delivery",)),
    ("Material deliveries", "material_receipt", ("material_receipt",)),
    ("Cement used", "material_consumption", ("material_consumption",)),
    ("Cement lot movements", "material_lot_inflow", ("lot_inflow",)),
    ("Power readings", "power_reading", ("power_reading",)),
    ("Digital ledger", "digital_ledger", ("ledger_in", "ledger_out")),
    ("Debtor balances at month end", "receivable_opening",
     ("receivable_opening",)),
    ("Customer receipts", "receipt", ()),
    ("Pallet register", "pallet_movement", ("pallet_movement",)),
    ("Bike mileage", "bike_mileage", ("bike_mileage",)),
)


@dataclass
class Line:
    label: str
    before: int
    after: int


@dataclass
class MonthImport:
    month: dt.date
    lines: list[Line] = field(default_factory=list)
    created: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    outside_month: int = 0


def read_month(path: str | Path) -> dt.date:
    """The month a workbook is for, or a LayoutError saying why it is not a
    register."""
    try:
        wb = S.load(str(path), values=True)
    except Exception as exc:                       # noqa: BLE001
        raise LayoutError(
            "That file could not be opened as an Excel workbook (.xlsx).") from exc
    check_layout(wb)
    return workbook_month(wb)


def _clear(db: Session, start: dt.date, end: dt.date) -> dict[str, int]:
    """Delete what the month holds. Returns rows removed per table."""
    p = {"s": start.isoformat(), "e": end.isoformat(), "cement": CEMENT,
         "y": start.year, "m": start.month}
    in_month = "{col} >= :s AND {col} < :e"
    sales = f"SELECT id FROM sale WHERE {in_month.format(col='sale_date')}"
    receipts = ("SELECT id FROM material_receipt WHERE "
                + in_month.format(col="receipt_date"))
    removed: dict[str, int] = {}

    def run(sql: str, table: str | None = None) -> None:
        n = db.execute(text(sql), p).rowcount
        if table:
            removed[table] = removed.get(table, 0) + n

    # Cement drawn by the month's production or sold on the month's sales.
    run(f"""DELETE FROM consumption_allocation
            WHERE consumption_id IN (SELECT id FROM material_consumption
                                     WHERE {in_month.format(col='cons_date')})
               OR sale_line_id IN (SELECT id FROM sale_line
                                   WHERE sale_id IN ({sales}))""")
    run(f"DELETE FROM material_consumption WHERE {in_month.format(col='cons_date')}",
        "material_consumption")
    # A note or a receipt from another month keeps its row, without the link.
    run(f"""UPDATE delivery SET sale_id = NULL WHERE sale_id IN ({sales})""")
    run(f"""UPDATE receipt SET sale_id = NULL WHERE sale_id IN ({sales})""")
    run(f"DELETE FROM delivery WHERE {in_month.format(col='delivery_date')}",
        "delivery")
    # Collections in the month are inside the debtor snapshot the file
    # gives for its end, which supersedes them.
    run(f"DELETE FROM receipt WHERE {in_month.format(col='receipt_date')}",
        "receipt")
    run(f"DELETE FROM sale_line WHERE sale_id IN ({sales})")
    run(f"DELETE FROM sale WHERE {in_month.format(col='sale_date')}", "sale")
    run(f"DELETE FROM production WHERE {in_month.format(col='prod_date')}",
        "production")
    run("DELETE FROM stock_opening WHERE as_on = :s", "stock_opening")

    run(f"UPDATE material_lot SET receipt_id = NULL WHERE receipt_id IN ({receipts})")
    run(f"""UPDATE material_lot_inflow SET receipt_id = NULL
            WHERE receipt_id IN ({receipts})""")
    run(f"DELETE FROM material_lot_inflow WHERE {in_month.format(col='as_on')}",
        "material_lot_inflow")
    run(f"DELETE FROM material_receipt WHERE {in_month.format(col='receipt_date')}",
        "material_receipt")
    run("""DELETE FROM material_opening WHERE as_on = :s AND material_id IN
           (SELECT id FROM raw_material WHERE name = :cement)""")

    run(f"DELETE FROM attendance WHERE {in_month.format(col='att_date')}",
        "attendance")
    run(f"DELETE FROM extra_work WHERE {in_month.format(col='work_date')}",
        "extra_work")
    run(f"DELETE FROM advance WHERE {in_month.format(col='adv_date')}", "advance")
    run("""DELETE FROM salary_settlement
           WHERE period_year = :y AND period_month = :m""", "salary_settlement")

    run(f"DELETE FROM power_reading WHERE {in_month.format(col='reading_date')}",
        "power_reading")
    run(f"DELETE FROM digital_ledger WHERE {in_month.format(col='txn_date')}",
        "digital_ledger")
    run("DELETE FROM receivable_opening WHERE as_on = :e", "receivable_opening")
    run(f"DELETE FROM pallet_movement WHERE {in_month.format(col='move_date')}",
        "pallet_movement")
    run(f"DELETE FROM bike_mileage WHERE {in_month.format(col='log_date')}",
        "bike_mileage")
    db.flush()
    return removed


def _drop_empty_lots(db: Session) -> None:
    """A lot left with nothing ever in or out of it -- one the app made for a
    delivery that the file does not have -- is not a lot."""
    for lot in db.query(MaterialLot).all():
        used = db.execute(text("""
            SELECT EXISTS (SELECT 1 FROM material_lot_inflow WHERE lot_id = :l)
                OR EXISTS (SELECT 1 FROM consumption_allocation WHERE lot_id = :l)
        """), {"l": lot.id}).scalar()
        if not used:
            db.delete(lot)
    db.flush()


def _has_earlier_data(db: Session, start: dt.date) -> bool:
    return bool(db.execute(text("""
        SELECT EXISTS (SELECT 1 FROM sale WHERE sale_date < :s)
            OR EXISTS (SELECT 1 FROM attendance WHERE att_date < :s)
            OR EXISTS (SELECT 1 FROM production WHERE prod_date < :s)
    """), {"s": start.isoformat()}).scalar())


def _opening_warnings(db: Session, start: dt.date) -> list[str]:
    """Where the file's openings part from what the month before closed on."""
    if not _has_earlier_data(db, start):
        return []
    out = []
    figures = opening_rpt.for_month(db, start)
    prior = (start - dt.timedelta(days=1)).strftime("%B")
    lines = [*figures.stock, *([figures.cement] if figures.cement else []),
             *figures.lots, *([figures.ledger] if figures.ledger else [])]
    for line in lines:
        if line.entered is None:
            continue
        diff = line.entered - line.carried
        if abs(diff) > 0.005:
            out.append(
                f"{line.name}: the file opens {start:%B} on "
                f"{line.entered:,.2f}, but {prior} closed on "
                f"{line.carried:,.2f} ({diff:+,.2f}).")
    return out


def run(db: Session, actor: str, path: str | Path, month: dt.date, *,
        commit: bool) -> MonthImport:
    """Reset ``month`` to the workbook at ``path``.

    With ``commit=False`` nothing is kept: the import runs and is rolled
    back, and the result says what it would do.
    """
    start = month.replace(day=1)
    end = next_month(start)
    found = read_month(path)
    if found != start:
        raise ValueError(
            f"This workbook is for {found:%B %Y}, not {start:%B %Y}. "
            "Pick the month it is for, or upload the other file.")
    guard(db, start)

    before = _counts(db, start, end)
    try:
        _clear(db, start, end)
        importer = WorkbookImporter(db, path, start)
        stats = importer.run(commit=False)
        _drop_empty_lots(db)
    except IntegrityError as exc:
        db.rollback()
        raise ValueError(
            "The file holds a figure the register does not allow -- "
            f"{_reason(exc)}. Correct the workbook and upload it again.") from None
    except Exception:
        db.rollback()
        raise

    after = _counts(db, start, end)
    result = MonthImport(
        month=start,
        lines=[Line(label, before[table], after[table])
               for label, table, _keys in REGISTERS],
        created=list(importer.created),
        warnings=_opening_warnings(db, start) + importer.warnings,
        outside_month=stats.counts.get("row_outside_month", 0))

    if not commit:
        db.rollback()
        return result
    record(db, actor, "month_import", None, "IMPORT",
           new={"month": f"{start:%Y-%m}", "file": Path(path).name,
                "rows": {ln.label: ln.after for ln in result.lines},
                "created": [f"{k}: {n}" for k, n in result.created]})
    db.commit()
    return result


def _counts(db: Session, start: dt.date, end: dt.date) -> dict[str, int]:
    """Rows each register holds for the month."""
    p = {"s": start.isoformat(), "e": end.isoformat(),
         "y": start.year, "m": start.month}
    dated = {
        "attendance": "att_date", "extra_work": "work_date",
        "advance": "adv_date", "sale": "sale_date", "production": "prod_date",
        "delivery": "delivery_date", "material_receipt": "receipt_date",
        "material_consumption": "cons_date", "material_lot_inflow": "as_on",
        "power_reading": "reading_date", "digital_ledger": "txn_date",
        "receipt": "receipt_date", "pallet_movement": "move_date",
        "bike_mileage": "log_date",
    }
    out = {}
    for table, col in dated.items():
        out[table] = db.execute(text(
            f"SELECT COUNT(*) FROM {table} WHERE {col} >= :s AND {col} < :e"),
            p).scalar()
    out["salary_settlement"] = db.execute(text(
        "SELECT COUNT(*) FROM salary_settlement "
        "WHERE period_year = :y AND period_month = :m"), p).scalar()
    out["stock_opening"] = db.execute(text(
        "SELECT COUNT(*) FROM stock_opening WHERE as_on = :s"), p).scalar()
    out["receivable_opening"] = db.execute(text(
        "SELECT COUNT(*) FROM receivable_opening WHERE as_on = :e"), p).scalar()
    return out


def _reason(exc: IntegrityError) -> str:
    msg = str(exc.orig)
    if "ck_attendance_shift_units" in msg:
        return "an attendance mark other than 0, 0.5, 1, 1.5 or 2 on Sheet2"
    if "UNIQUE" in msg:
        return "the same entry twice (" + msg.split(":")[-1].strip() + ")"
    return msg


def backup(db: Session, dest_dir: Path, month: dt.date) -> Path:
    """A consistent copy of the database before it is changed. VACUUM INTO is
    safe against a live WAL database, which a file copy is not."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = dest_dir / f"smcp-{stamp}-before-{month:%Y-%m}-import.db"
    engine = db.get_bind()
    with engine.connect().execution_options(
            isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql("VACUUM INTO ?", (str(target),))
    return target

