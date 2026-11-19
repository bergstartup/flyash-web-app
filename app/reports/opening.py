"""What a month opens with: the figure carried from the month before, and the
figure entered for it, side by side.

Every register rolls itself. A month with no opening of its own opens on the
closing of the one before, counted forward from the latest opening that was
entered -- ``stock_period_opening`` and ``digital_ledger_month`` do this in
SQL. An entered opening is for the day the carried figure is known to be
wrong: going live after months the app never saw, or a stock-take.

The "carried" figures here deliberately ignore anything entered for the month
itself, so the go-live screen can show what the app would have assumed next
to what the September book says.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models import PowerReading

CEMENT = "CEMENT"
RAMCO = "RAMCO CEMENT"
FAP = "FAP MIX CEMENT"


@dataclass
class Line:
    id: int
    name: str
    carried: float
    entered: float | None


@dataclass
class Opening:
    as_on: dt.date
    stock: list[Line] = field(default_factory=list)
    cement: Line | None = None
    lots: list[Line] = field(default_factory=list)
    ledger: Line | None = None
    last_reading: PowerReading | None = None
    eb_anchor: PowerReading | None = None


def month_start(value: dt.date) -> dt.date:
    if value.day != 1:
        raise ValueError("An opening is for the first of a month.")
    return value


def cement_opening(conn, period_start: dt.date, *,
                   before_only: bool = False) -> float:
    """Sheet12's opening for a month: the latest entered cement opening, plus
    what arrived (RAMCO and FAP mix), less what was used and sold since.

    ``before_only`` ignores an opening entered for the month itself, which is
    what the month would have carried without it.
    """
    op = "<" if before_only else "<="
    return conn.execute(text(f"""
        WITH anchor AS (
            SELECT MAX(o.as_on) AS on_ FROM material_opening o
            JOIN raw_material rm ON rm.id = o.material_id
            WHERE rm.name = :cement AND o.as_on {op} :start
        )
        SELECT
            COALESCE((SELECT o.qty FROM material_opening o
                       JOIN raw_material rm ON rm.id = o.material_id, anchor a
                       WHERE rm.name = :cement AND o.as_on = a.on_), 0)
          + COALESCE((SELECT SUM(r.qty) FROM material_receipt r
                       JOIN raw_material rm ON rm.id = r.material_id, anchor a
                       WHERE rm.name IN (:ramco, :fap) AND r.receipt_date < :start
                         AND (a.on_ IS NULL OR r.receipt_date >= a.on_)), 0)
          - COALESCE((SELECT SUM(c.qty) FROM material_consumption c
                       JOIN raw_material rm ON rm.id = c.material_id, anchor a
                       WHERE rm.name = :cement AND c.cons_date < :start
                         AND (a.on_ IS NULL OR c.cons_date >= a.on_)), 0)
          - COALESCE((SELECT SUM(l.qty) FROM sale s
                       JOIN sale_line l ON l.sale_id = s.id
                       JOIN product p ON p.id = l.product_id, anchor a
                       WHERE UPPER(p.name) = :cement AND s.sale_date < :start
                         AND (a.on_ IS NULL OR s.sale_date >= a.on_)), 0)
    """), {"cement": CEMENT, "ramco": RAMCO, "fap": FAP,
           "start": period_start.isoformat()}).scalar() or 0.0


def for_month(db: Session, as_on: dt.date) -> Opening:
    as_on = month_start(as_on)
    start = as_on.isoformat()
    out = Opening(as_on=as_on)

    for row in db.execute(text("""
        WITH anchor AS (
            SELECT p.id AS product_id,
                   (SELECT MAX(o.as_on) FROM stock_opening o
                     WHERE o.product_id = p.id AND o.as_on < :start) AS on_
            FROM product p
            WHERE p.has_stock_ledger = 1 AND p.is_active = 1
        )
        SELECT p.id, p.name,
               COALESCE((SELECT o.qty FROM stock_opening o
                          WHERE o.product_id = p.id AND o.as_on = a.on_), 0)
                 + COALESCE((SELECT SUM(m.produced - m.issued)
                              FROM stock_movement m
                              WHERE m.product_id = p.id AND m.d < :start
                                AND (a.on_ IS NULL OR m.d >= a.on_)), 0),
               (SELECT o.qty FROM stock_opening o
                 WHERE o.product_id = p.id AND o.as_on = :start)
        FROM anchor a JOIN product p ON p.id = a.product_id
        ORDER BY p.display_order, p.name"""), {"start": start}):
        out.stock.append(Line(*row))

    cement = db.execute(text("""
        SELECT rm.id, (SELECT o.qty FROM material_opening o
                        WHERE o.material_id = rm.id AND o.as_on = :start)
        FROM raw_material rm WHERE rm.name = :cement"""),
        {"cement": CEMENT, "start": start}).first()
    if cement is not None:
        out.cement = Line(cement[0], CEMENT,
                          cement_opening(db, as_on, before_only=True),
                          cement[1])

    # A lot is shown while it holds anything, or once something has been
    # entered for it on the day -- zeroing a lot must not make it vanish
    # before the operator can see that it took.
    for row in db.execute(text("""
        SELECT ml.id, rm.name || ' / ' || ml.label,
               COALESCE((SELECT SUM(i.qty) FROM material_lot_inflow i
                          WHERE i.lot_id = ml.id AND i.as_on < :start), 0)
                 - COALESCE((SELECT SUM(ca.qty) FROM consumption_allocation ca
                              JOIN material_consumption mc
                                ON mc.id = ca.consumption_id
                              WHERE ca.lot_id = ml.id
                                AND mc.cons_date < :start), 0) AS carried,
               (SELECT SUM(i.qty) FROM material_lot_inflow i
                 WHERE i.lot_id = ml.id AND i.as_on = :start
                   AND i.receipt_id IS NULL) AS counted
        FROM material_lot ml
        JOIN raw_material rm ON rm.id = ml.material_id
        WHERE rm.is_lot_tracked = 1
        ORDER BY rm.display_order, ml.display_order"""), {"start": start}):
        lot_id, name, carried, counted = row
        if abs(carried) > 1e-9 or counted is not None:
            entered = None if counted is None else carried + counted
            out.lots.append(Line(lot_id, name, carried, entered))

    ledger = db.execute(text("""
        WITH anchor AS (
            SELECT MAX(date(txn_date, 'start of month')) AS on_
            FROM digital_ledger
            WHERE entry_type = 'OPENING' AND txn_date < :start
        )
        SELECT
            COALESCE((SELECT SUM(CASE WHEN o.direction = 'IN'
                                      THEN o.amount ELSE -o.amount END)
                       FROM digital_ledger o, anchor a
                       WHERE o.entry_type = 'OPENING'
                         AND date(o.txn_date, 'start of month') = a.on_), 0)
              + COALESCE((SELECT SUM(CASE WHEN x.direction = 'IN'
                                          THEN x.amount ELSE -x.amount END)
                           FROM digital_ledger x, anchor a
                           WHERE x.entry_type <> 'OPENING'
                             AND x.txn_date < :start
                             AND (a.on_ IS NULL OR x.txn_date >= a.on_)), 0),
            (SELECT SUM(CASE WHEN o.direction = 'IN'
                             THEN o.amount ELSE -o.amount END)
              FROM digital_ledger o
              WHERE o.entry_type = 'OPENING'
                AND date(o.txn_date, 'start of month') = :start)
    """), {"start": start}).first()
    out.ledger = Line(0, "Digital account", ledger[0] or 0.0, ledger[1])

    out.last_reading = db.query(PowerReading).filter(
        PowerReading.reading_date < as_on,
        PowerReading.reading_taken == 1,
        PowerReading.is_eb_anchor == 0).order_by(
        PowerReading.reading_date.desc()).first()
    out.eb_anchor = db.query(PowerReading).filter(
        PowerReading.reading_date < as_on,
        PowerReading.is_eb_anchor == 1).order_by(
        PowerReading.reading_date.desc(), PowerReading.id.desc()).first()
    return out
