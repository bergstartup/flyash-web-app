"""Month-end close. Every service write checks the lock before touching a row."""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from app.db.models import PeriodLock
from app.services.audit import record


class PeriodLocked(Exception):
    """Raised when a write targets a settled month."""


def is_locked(db: Session, when: dt.date) -> bool:
    row = db.query(PeriodLock).filter_by(
        period_year=when.year, period_month=when.month).first()
    return bool(row and row.locked_at and not row.reopened_at)


def guard(db: Session, when: dt.date) -> None:
    if is_locked(db, when):
        raise PeriodLocked(
            f"{when:%B %Y} is closed. Reopen it with a reason before editing.")


def close(db: Session, actor: str, year: int, month: int) -> PeriodLock:
    row = db.query(PeriodLock).filter_by(
        period_year=year, period_month=month).first()
    if row is None:
        row = PeriodLock(period_year=year, period_month=month)
        db.add(row)
    row.locked_at = dt.datetime.now()
    row.locked_by = actor
    row.reopened_at = row.reopened_by = row.reopen_reason = None
    db.flush()
    record(db, actor, "period_lock", row.id, "CLOSE",
           new={"year": year, "month": month})
    db.commit()
    return row


def reopen(db: Session, actor: str, year: int, month: int, reason: str) -> PeriodLock:
    if not reason or not reason.strip():
        raise ValueError("Reopening a closed month requires a reason.")
    row = db.query(PeriodLock).filter_by(
        period_year=year, period_month=month).first()
    if row is None or not row.locked_at:
        raise ValueError(f"{year}-{month:02d} is not closed.")
    row.reopened_at = dt.datetime.now()
    row.reopened_by = actor
    row.reopen_reason = reason.strip()
    db.flush()
    record(db, actor, "period_lock", row.id, "REOPEN", new={"reason": reason})
    db.commit()
    return row
