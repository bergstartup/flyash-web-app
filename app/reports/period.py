"""Which period the app is currently operating in.

Nothing in the schema hardcodes "July 2026" -- it was simply the only month
with data when the register was imported. As real entries accumulate for
other months, screens that don't take an explicit year/month should follow
the data forward automatically rather than staying pinned to the import month.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import text
from sqlalchemy.orm import Session


def latest_period(db: Session) -> tuple[int, int]:
    """The most recent year/month with any transaction, or this month if the
    database is empty."""
    row = db.execute(text("""
        SELECT MAX(d) FROM (
            SELECT MAX(att_date)     AS d FROM attendance
            UNION ALL SELECT MAX(prod_date)    FROM production
            UNION ALL SELECT MAX(sale_date)    FROM sale
            UNION ALL SELECT MAX(adv_date)     FROM advance
            UNION ALL SELECT MAX(work_date)    FROM extra_work
            UNION ALL SELECT MAX(receipt_date) FROM material_receipt
            UNION ALL SELECT MAX(txn_date)     FROM digital_ledger
        )""")).scalar()
    if row is None:
        today = dt.date.today()
        return today.year, today.month
    d = dt.date.fromisoformat(str(row)[:10])
    return d.year, d.month
