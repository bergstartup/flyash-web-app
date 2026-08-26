"""Extra-work-type master.

Rates are versioned, never overwritten: adding a new rate leaves every past
transaction (which snapshots its own rate) untouched, and the master always
answers "what was the rate on this date" rather than only "what is it now".
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from app.db.models import ExtraWorkRate, ExtraWorkType
from app.services.audit import record


def list_types(db: Session, include_inactive: bool = False) -> list[ExtraWorkType]:
    q = db.query(ExtraWorkType)
    if not include_inactive:
        q = q.filter_by(is_active=1)
    return q.order_by(ExtraWorkType.name).all()


def current_rate(db: Session, work_type_id: int,
                 as_of: dt.date | None = None) -> float | None:
    as_of = as_of or dt.date.today()
    row = (db.query(ExtraWorkRate)
           .filter(ExtraWorkRate.work_type_id == work_type_id,
                   ExtraWorkRate.effective_from <= as_of)
           .order_by(ExtraWorkRate.effective_from.desc())
           .first())
    return row.rate if row else None


def create(db: Session, actor: str, *, code: str, name: str, unit: str,
           rate: float, effective_from: dt.date | None = None) -> ExtraWorkType:
    code, name = code.strip().upper(), name.strip()
    if not code or not name:
        raise ValueError("An extra-work type needs a code and a name.")
    if db.query(ExtraWorkType).filter_by(code=code).first():
        raise ValueError(f"Code '{code}' is already in use.")
    wt = ExtraWorkType(code=code, name=name, unit=unit, is_active=1)
    db.add(wt)
    db.flush()
    db.add(ExtraWorkRate(work_type_id=wt.id, rate=rate,
                         effective_from=effective_from or dt.date.today()))
    db.flush()
    record(db, actor, "extra_work_type", wt.id, "CREATE",
           new={"code": code, "name": name, "rate": rate})
    db.commit()
    return wt


def set_rate(db: Session, actor: str, work_type_id: int, rate: float,
            effective_from: dt.date) -> ExtraWorkRate:
    if rate <= 0:
        raise ValueError("Rate must be positive.")
    existing = (db.query(ExtraWorkRate)
               .filter_by(work_type_id=work_type_id, effective_from=effective_from)
               .first())
    if existing:
        existing.rate = rate
    else:
        db.add(ExtraWorkRate(work_type_id=work_type_id, rate=rate,
                             effective_from=effective_from))
    db.flush()
    record(db, actor, "extra_work_type", work_type_id, "SET_RATE",
           new={"rate": rate, "effective_from": str(effective_from)})
    db.commit()
    return existing
