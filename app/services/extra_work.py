"""Extra work entry (Sheet14 and beyond).

The rate is always snapshotted from the work type's current rate at the time
of entry, never typed -- so a later rate change can never rewrite a settled
day's wage.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from app.db.models import ExtraWork, Labour, ExtraWorkType
from app.services.audit import record
from app.services.extra_work_type import current_rate
from app.services.period import guard


def for_day(db: Session, day: dt.date) -> list[ExtraWork]:
    return (db.query(ExtraWork).filter_by(work_date=day)
           .order_by(ExtraWork.id).all())


def add(db: Session, actor: str, *, work_date: dt.date, labour_id: int,
        work_type_id: int, qty: float, remarks: str | None = None) -> ExtraWork:
    guard(db, work_date)
    if qty == 0:
        raise ValueError("Quantity cannot be zero.")
    if db.get(Labour, labour_id) is None:
        raise ValueError(f"No such labour: {labour_id}")
    work_type = db.get(ExtraWorkType, work_type_id)
    if work_type is None:
        raise ValueError(f"No such extra-work type: {work_type_id}")
    rate = current_rate(db, work_type_id, work_date)
    if rate is None:
        raise ValueError(
            f"'{work_type.name}' has no rate in effect on {work_date}.")
    entry = ExtraWork(work_date=work_date, labour_id=labour_id,
                      work_type_id=work_type_id, qty=qty, rate=rate,
                      remarks=remarks)
    db.add(entry)
    db.flush()
    record(db, actor, "extra_work", entry.id, "CREATE", new={
        "date": str(work_date), "labour_id": labour_id, "qty": qty,
        "rate": rate, "amount": qty * rate})
    db.commit()
    return entry


def delete(db: Session, actor: str, entry_id: int) -> None:
    entry = db.get(ExtraWork, entry_id)
    if entry is None:
        return
    guard(db, entry.work_date)
    record(db, actor, "extra_work", entry.id, "DELETE",
           old={"date": str(entry.work_date), "labour_id": entry.labour_id,
                "qty": entry.qty, "rate": entry.rate})
    db.delete(entry)
    db.commit()
