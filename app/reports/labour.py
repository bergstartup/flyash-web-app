"""Read paths for the labour screens."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import text
from sqlalchemy.orm import Session


def salary_month(db: Session, year: int, month: int) -> list:
    return db.execute(text("""
        SELECT labour_id, labour_name, wage_type, muster_wage, duty, salary,
               advance, balance, extra, total_payment, paid_on, authority,
               status, outstanding
        FROM salary_month
        WHERE period_year = :y AND period_month = :m
        ORDER BY total_payment DESC"""), {"y": year, "m": month}).all()


def extra_wage_adjustment_report(db: Session, year: int, month: int) -> list:
    """Per-labour size of the extra-wage gap this month.

    This is the report the build plan calls for: whatever lands in the
    MIGRATION ADJUSTMENT bucket is money paid for a category of extra work
    that has no register yet, and this is what tells the owner where to look.
    """
    return db.execute(text("""
        SELECT l.name, SUM(e.qty * e.rate) AS adjustment_amount,
               COUNT(*) AS entries
        FROM extra_work e
        JOIN extra_work_type t ON t.id = e.work_type_id
        JOIN labour l ON l.id = e.labour_id
        WHERE t.is_adjustment = 1
          AND strftime('%Y', e.work_date) = :y
          AND strftime('%m', e.work_date) = :m
        GROUP BY l.id, l.name
        ORDER BY adjustment_amount DESC"""),
        {"y": f"{year:04d}", "m": f"{month:02d}"}).all()


def extra_work_for_day(db: Session, day: dt.date) -> list:
    return db.execute(text("""
        SELECT e.id, l.name AS labour_name, t.name AS work_type_name,
               e.qty, e.rate, e.qty * e.rate AS amount, e.remarks
        FROM extra_work e
        JOIN labour l ON l.id = e.labour_id
        JOIN extra_work_type t ON t.id = e.work_type_id
        WHERE e.work_date = :d ORDER BY e.id"""), {"d": day}).all()


def advances_for_day(db: Session, day: dt.date) -> list:
    return db.execute(text("""
        SELECT a.id, l.name AS labour_name, a.amount, a.payment_mode,
               a.paid_to, a.remarks
        FROM advance a JOIN labour l ON l.id = a.labour_id
        WHERE a.adv_date = :d ORDER BY a.id"""), {"d": day}).all()


def days_with_attendance(db: Session, year: int, month: int) -> list[str]:
    rows = db.execute(text("""
        SELECT DISTINCT att_date FROM attendance
        WHERE strftime('%Y', att_date) = :y AND strftime('%m', att_date) = :m
        ORDER BY att_date DESC"""), {"y": f"{year:04d}", "m": f"{month:02d}"}).all()
    return [str(r[0])[:10] for r in rows]
