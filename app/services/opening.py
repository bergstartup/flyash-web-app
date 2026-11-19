"""Entering a month's opening figures -- the go-live, or a stock-take.

Nothing here is needed month to month: every register carries its own
closing forward (see ``app/reports/opening.py``). An entered opening is for
the day the carried figure is known to be wrong -- going live on 1 October
when August and September were never entered, or a count that disagrees with
the books.

Each figure is entered, or left blank to go back to carrying. A blank is not
a nought: 0 bricks on hand is a real opening and is stored as one.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from app.db.models import (DigitalLedger, MaterialLot, MaterialLotInflow,
                           MaterialOpening, PowerReading, Product, RawMaterial,
                           StockOpening)
from app.reports.opening import CEMENT, for_month, month_start
from app.services.audit import record
from app.services.material import _next_lot_order
from app.services.period import guard

OPENING_NOTE = "OPENING BALANCE"


def set_stock(db: Session, actor: str, as_on: dt.date, product_id: int,
              qty: float | None) -> None:
    as_on = month_start(as_on)
    guard(db, as_on)
    product = db.get(Product, product_id)
    if product is None or not product.has_stock_ledger:
        raise ValueError("That product keeps no stock ledger.")
    if qty is not None and qty < 0:
        raise ValueError(f"{product.name}: stock on hand cannot be negative.")
    row = db.query(StockOpening).filter_by(
        product_id=product_id, as_on=as_on).first()
    old = None if row is None else row.qty
    if old == qty:
        return
    if qty is None:
        db.delete(row)
    elif row is None:
        row = StockOpening(product_id=product_id, as_on=as_on, qty=qty)
        db.add(row)
    else:
        row.qty = qty
    db.flush()
    record(db, actor, "stock_opening", None if qty is None else row.id,
           "OPENING", old={"product": product.name, "as_on": as_on, "qty": old},
           new={"product": product.name, "as_on": as_on, "qty": qty})


def set_cement(db: Session, actor: str, as_on: dt.date,
               qty: float | None) -> None:
    as_on = month_start(as_on)
    guard(db, as_on)
    if qty is not None and qty < 0:
        raise ValueError("Cement on hand cannot be negative.")
    cement = db.query(RawMaterial).filter_by(name=CEMENT).first()
    if cement is None:
        raise ValueError("There is no CEMENT material to open.")
    row = db.query(MaterialOpening).filter_by(
        material_id=cement.id, as_on=as_on).first()
    old = None if row is None else row.qty
    if old == qty:
        return
    if qty is None:
        db.delete(row)
    elif row is None:
        row = MaterialOpening(material_id=cement.id, as_on=as_on, qty=qty)
        db.add(row)
    else:
        row.qty = qty
    db.flush()
    record(db, actor, "material_opening", None if qty is None else row.id,
           "OPENING", old={"as_on": as_on, "qty": old},
           new={"as_on": as_on, "qty": qty})


def set_lot(db: Session, actor: str, as_on: dt.date, lot_id: int,
            qty: float | None) -> None:
    """Say what is standing in a lot on the day.

    The lot's history is not rewritten. The difference between what it
    carried and what was counted lands as one receipt-less inflow on the day
    -- negative when the lot holds less than the books say -- so the lot
    balance views need nothing new, and blanking the figure takes it back
    out.
    """
    as_on = month_start(as_on)
    guard(db, as_on)
    lot = db.get(MaterialLot, lot_id)
    if lot is None:
        raise ValueError(f"No such lot: {lot_id}")
    if qty is not None and qty < 0:
        raise ValueError(f"{lot.label}: a lot cannot hold less than nothing.")
    line = next((x for x in for_month(db, as_on).lots if x.id == lot_id), None)
    carried = line.carried if line else 0.0
    count = db.query(MaterialLotInflow).filter_by(
        lot_id=lot_id, as_on=as_on, receipt_id=None).first()
    old = None if count is None else carried + count.qty
    if old is None and qty is None:
        return
    if old is not None and qty is not None and abs(old - qty) < 1e-9:
        return
    delta = None if qty is None else qty - carried
    if delta is None or abs(delta) < 1e-9:
        if count is not None:
            db.delete(count)
    elif count is None:
        count = MaterialLotInflow(lot_id=lot_id, as_on=as_on, qty=delta)
        db.add(count)
    else:
        count.qty = delta
    db.flush()
    record(db, actor, "material_lot_inflow", lot_id, "OPENING",
           old={"lot": lot.label, "as_on": as_on, "standing": old},
           new={"lot": lot.label, "as_on": as_on, "standing": qty})


def add_lot(db: Session, actor: str, as_on: dt.date, material_id: int,
            label: str, qty: float, lot_date: dt.date | None = None) -> MaterialLot:
    """A lot standing on the day that the app has never seen -- one filled in
    August or September, before it went live."""
    as_on = month_start(as_on)
    guard(db, as_on)
    material = db.get(RawMaterial, material_id)
    if material is None or not material.is_lot_tracked:
        raise ValueError("That material is not kept in lots.")
    label = (label or "").strip().upper()
    if not label:
        label = f"{lot_date:%d.%m.%y}" if lot_date else ""
    if not label:
        raise ValueError("Name the lot, or give the date it was filled.")
    if len(label) > 20:
        raise ValueError("A lot name is at most 20 characters.")
    if qty <= 0:
        raise ValueError("A standing lot has to hold something.")
    if db.query(MaterialLot).filter_by(material_id=material_id,
                                       label=label).first():
        raise ValueError(f"There is already a lot called {label}. Enter what "
                         "is standing in it on its own row.")
    lot = MaterialLot(material_id=material_id, lot_date=lot_date, label=label,
                      display_order=_next_lot_order(db, material_id))
    db.add(lot)
    db.flush()
    db.add(MaterialLotInflow(lot_id=lot.id, as_on=as_on, qty=qty))
    db.flush()
    record(db, actor, "material_lot", lot.id, "OPENING",
           new={"material": material.name, "label": label, "as_on": as_on,
                "qty": qty})
    return lot


def set_ledger(db: Session, actor: str, as_on: dt.date,
               amount: float | None) -> None:
    """The digital account's balance on the day. Negative is an overdraft,
    and is kept as an OUT so the sign survives."""
    as_on = month_start(as_on)
    guard(db, as_on)
    rows = db.query(DigitalLedger).filter(
        DigitalLedger.entry_type == "OPENING",
        DigitalLedger.txn_date >= as_on,
        DigitalLedger.txn_date < _next_month(as_on)).all()
    old = sum(r.amount if r.direction == "IN" else -r.amount for r in rows) \
        if rows else None
    if old == amount:
        return
    for r in rows:
        db.delete(r)
    if amount is not None:
        db.add(DigitalLedger(
            txn_date=as_on, direction="IN" if amount >= 0 else "OUT",
            entry_type="OPENING", amount=abs(amount),
            description=f"{as_on - dt.timedelta(days=1):%B} BALANCE".upper()))
    db.flush()
    record(db, actor, "digital_ledger", None, "OPENING",
           old={"as_on": as_on, "amount": old},
           new={"as_on": as_on, "amount": amount})


def set_eb_anchor(db: Session, actor: str, reading_date: dt.date,
                  kwh: float, kvah: float) -> PowerReading:
    """Where the EB meter stood when the current billing cycle began. The
    cumulative columns on the power page count from here."""
    guard(db, reading_date)
    if kwh < 0 or kvah < 0:
        raise ValueError("A meter reading cannot be negative.")
    row = db.query(PowerReading).filter_by(reading_date=reading_date).first()
    if row is not None and not row.is_eb_anchor:
        raise ValueError(
            f"{reading_date:%d %b %Y} already has a daily reading. The billing "
            "cycle's start is its own row -- give the date the EB bill starts "
            "from.")
    old = None if row is None else {"kwh": row.kwh, "kvah": row.kvah}
    if row is None:
        row = PowerReading(reading_date=reading_date, is_eb_anchor=1,
                           reading_taken=1, remarks="EB BILLING CYCLE START")
        db.add(row)
    row.kwh, row.kvah = kwh, kvah
    db.flush()
    record(db, actor, "power_reading", row.id, "EB_ANCHOR", old=old,
           new={"date": reading_date, "kwh": kwh, "kvah": kvah})
    return row


def _next_month(day: dt.date) -> dt.date:
    return (day.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
