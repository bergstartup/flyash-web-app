"""Power meter readings (Sheet1) and the digital account ledger (Sheet19).

A meter only ever goes up, so a reading below the one before it is a keying
error and is refused -- validation rule 2. The anchor is the exception: it
restates where the EB meter stood and is not measured against anything.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models import DigitalLedger, PowerReading
from app.services.audit import record
from app.services.period import guard

DIRECTIONS = ("IN", "OUT")


class MeterWentBackwards(Exception):
    """Rule 2: a meter reading cannot be lower than the one before it."""


def previous_reading(db: Session, before: dt.date) -> PowerReading | None:
    return db.query(PowerReading).filter(
        PowerReading.reading_date < before,
        PowerReading.reading_taken == 1).order_by(
        PowerReading.reading_date.desc()).first()


def readings_for_month(db: Session, year: int, month: int) -> list:
    return db.execute(text("""
        SELECT reading_date, kwh, kvah, daily_kwh, daily_kvah, daily_pf,
               days_spanned, cum_kwh, cum_kvah, cum_pf
        FROM power_daily
        WHERE strftime('%Y', reading_date) = :y
          AND strftime('%m', reading_date) = :m
        ORDER BY reading_date"""),
        {"y": f"{year:04d}", "m": f"{month:02d}"}).all()


def take_reading(db: Session, actor: str, *, reading_date: dt.date,
                 kwh: float | None, kvah: float | None,
                 remarks: str | None = None) -> PowerReading:
    """Record the day's meter. Leave both figures out to record a day the
    reading was never taken -- 29 July reads LEAVE / SIR -- which is not the
    same as a reading of zero, and the next day's movement spans both."""
    guard(db, reading_date)
    taken = kwh is not None and kvah is not None
    if not taken and not remarks:
        raise ValueError("A day with no reading needs a reason.")
    if taken:
        if kwh < 0 or kvah < 0:
            raise ValueError("A meter reading cannot be negative.")
        earlier = previous_reading(db, reading_date)
        if earlier is not None:
            if kwh < earlier.kwh or kvah < earlier.kvah:
                raise MeterWentBackwards(
                    f"{reading_date:%d %b %Y}: {kwh:,.1f}/{kvah:,.1f} is below "
                    f"{earlier.reading_date:%d %b}'s {earlier.kwh:,.1f}/"
                    f"{earlier.kvah:,.1f}.")
    row = db.query(PowerReading).filter_by(reading_date=reading_date).first()
    if row is None:
        row = PowerReading(reading_date=reading_date)
        db.add(row)
    old = {"kwh": row.kwh, "kvah": row.kvah}
    row.kwh, row.kvah = kwh, kvah
    row.reading_taken = int(taken)
    row.remarks = remarks
    db.flush()
    record(db, actor, "power_reading", row.id, "READING",
           old=old, new={"kwh": kwh, "kvah": kvah, "remarks": remarks})
    db.commit()
    return row


# -- the digital ledger -----------------------------------------------------

def ledger_for_month(db: Session, year: int, month: int) -> list[DigitalLedger]:
    return db.query(DigitalLedger).filter(
        text("strftime('%Y', txn_date) = :y AND strftime('%m', txn_date) = :m")
    ).params(y=f"{year:04d}", m=f"{month:02d}").order_by(
        DigitalLedger.txn_date, DigitalLedger.id).all()


def ledger_summary(db: Session, year: int, month: int):
    return db.execute(text("""
        SELECT opening, income, expense, closing FROM digital_ledger_month
        WHERE period_start = :start"""),
        {"start": dt.date(year, month, 1).isoformat()}).first()


def post(db: Session, actor: str, *, txn_date: dt.date, direction: str,
         amount: float, description: str | None = None) -> DigitalLedger:
    """A hand-entered ledger line -- fuel, a bank charge, a payment with no
    other record. Entries that belong to something else post themselves: a
    digital labour advance already writes its own."""
    guard(db, txn_date)
    if direction not in DIRECTIONS:
        raise ValueError(f"Direction must be one of {DIRECTIONS}.")
    if amount <= 0:
        raise ValueError("Amount must be positive.")
    entry = DigitalLedger(txn_date=txn_date, direction=direction,
                          entry_type="MANUAL", description=description,
                          amount=amount)
    db.add(entry)
    db.flush()
    record(db, actor, "digital_ledger", entry.id, "CREATE", new={
        "date": str(txn_date), "direction": direction, "amount": amount,
        "description": description})
    db.commit()
    return entry


def delete(db: Session, actor: str, entry_id: int) -> None:
    """Only a hand-entered line can be removed here. One that posted itself
    belongs to the advance or sale behind it, and is removed with that."""
    entry = db.get(DigitalLedger, entry_id)
    if entry is None:
        return
    guard(db, entry.txn_date)
    if entry.entry_type != "MANUAL":
        raise ValueError(
            f"This line came from {entry.source_table or 'another record'}. "
            "Remove it there and it will go from here too.")
    record(db, actor, "digital_ledger", entry.id, "DELETE",
           old={"date": str(entry.txn_date), "amount": entry.amount})
    db.delete(entry)
    db.commit()
