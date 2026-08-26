"""Salary settlement -- only the non-derived part of Sheet15.

Duty, salary, advance, balance, extra and total are all views over attendance,
advances and extra work. Only the payment mechanics are entered here.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from app.db.models import Labour, SalarySettlement
from app.services.audit import record


def settle(db: Session, actor: str, *, period_year: int, period_month: int,
          labour_id: int, paid_on: dt.date | None = None,
          authority: str | None = None, status: str | None = None,
          outstanding: float = 0.0) -> SalarySettlement:
    if db.get(Labour, labour_id) is None:
        raise ValueError(f"No such labour: {labour_id}")
    row = db.query(SalarySettlement).filter_by(
        period_year=period_year, period_month=period_month,
        labour_id=labour_id).first()
    if row is None:
        row = SalarySettlement(period_year=period_year, period_month=period_month,
                               labour_id=labour_id)
        db.add(row)
    old = {"paid_on": str(row.paid_on) if row.paid_on else None,
           "status": row.status, "outstanding": row.outstanding}
    row.paid_on = paid_on
    row.authority = authority
    row.status = status
    row.outstanding = outstanding
    db.flush()
    record(db, actor, "salary_settlement", row.id, "SETTLE",
           old=old, new={"paid_on": str(paid_on) if paid_on else None,
                        "status": status, "outstanding": outstanding})
    db.commit()
    return row
