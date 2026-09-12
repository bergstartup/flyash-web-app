"""The daily round: what today's entry still needs, station by station.

Nothing here is stored -- every figure is worked out from the same tables
the entry screens themselves write to, for whichever day is asked for. A
station either has a clear day's worth of work (attendance, production and
the power reading: one row expected per labour, per ledger product, or per
day) or it doesn't (extra work, materials, consumption and the digital
ledger are all optional, so they only ever report what happened, never what
is "missing").

Status is one of four, each with its own pill colour on the page:
  ok    -- everything expected for the day is there
  warn  -- either partly done, or done but worth a second look
  bad   -- something is expected and nothing has been entered
  muted -- no fixed expectation; this is a headcount, not a checklist item
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models import Attendance, Labour, PowerReading, Product, Production
from app.services import material as material_svc
from app.services import power as power_svc


@dataclass
class Station:
    key: str
    label: str
    href: str
    status: str
    headline: str
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _attendance(db: Session, day: dt.date) -> Station:
    expected = (db.query(Labour).filter_by(is_active=1)
               .filter((Labour.join_date.is_(None)) | (Labour.join_date <= day))
               .order_by(Labour.display_order, Labour.id).all())
    entered_ids = {row.labour_id for row in
                  db.query(Attendance).filter_by(att_date=day).all()}
    missing = [lab.name for lab in expected if lab.id not in entered_ids]
    total = len(expected)
    done = total - len(missing)
    status = ("muted" if total == 0 else
              "ok" if not missing else
              "bad" if done == 0 else "warn")
    return Station("attendance", "Attendance", "/attendance", status,
                   f"{done} / {total} marked", missing)


def _extra_work(db: Session, day: dt.date) -> Station:
    n = db.execute(text(
        "SELECT COUNT(*) FROM extra_work WHERE work_date = :d"),
        {"d": day.isoformat()}).scalar()
    return Station("extra-work", "Extra work", "/extra-work", "muted",
                   f"{n} {'entry' if n == 1 else 'entries'}")


def _materials(db: Session, day: dt.date) -> Station:
    n = db.execute(text(
        "SELECT COUNT(*) FROM material_receipt WHERE receipt_date = :d"),
        {"d": day.isoformat()}).scalar()
    low = material_svc.below_reorder(db)
    notes = [f"{name} is below reorder level ({on_hand:g} of {level:g})"
             for name, level, on_hand in low]
    return Station("materials", "Material deliveries", "/materials",
                   "warn" if notes else "muted",
                   f"{n} {'delivery' if n == 1 else 'deliveries'}", notes=notes)


def _consumption(db: Session, day: dt.date) -> Station:
    n = db.execute(text(
        "SELECT COUNT(*) FROM material_consumption WHERE cons_date = :d"),
        {"d": day.isoformat()}).scalar()
    return Station("consumption", "Consumption", "/consumption", "muted",
                   f"{n} {'entry' if n == 1 else 'entries'}")


def _sales(db: Session, day: dt.date) -> Station:
    n = db.execute(text(
        "SELECT COUNT(*) FROM sale WHERE sale_date = :d"),
        {"d": day.isoformat()}).scalar()
    unpriced = db.execute(text("""
        SELECT seq_no FROM sale
        WHERE sale_date = :d AND movement_type = 'SALE' AND payment_mode IS NULL
        ORDER BY seq_no"""), {"d": day.isoformat()}).scalars().all()
    notes = [f"Trip #{seq} has no payment mode recorded" for seq in unpriced]
    return Station("sales", "Sales & dispatch", "/sales",
                   "warn" if notes else "muted",
                   f"{n} {'trip' if n == 1 else 'trips'}", notes=notes)


def _production(db: Session, day: dt.date) -> Station:
    """Unlike attendance, a ledger product with nothing to show for the day
    is normal, not a gap: two of the three ledger products run on well under
    a third of July's days. So this only ever reports, it never nags."""
    ledger = (db.query(Product).filter_by(is_active=1, has_stock_ledger=1)
             .order_by(Product.display_order, Product.id).all())
    ran = {row.product_id for row in
          db.query(Production).filter_by(prod_date=day).all()}
    total = len(ledger)
    done = sum(1 for p in ledger if p.id in ran)
    idle = [p.name for p in ledger if p.id not in ran]
    return Station("production", "Production", "/production", "muted",
                   f"{done} / {total} ledger products ran",
                   notes=[f"Idle today: {', '.join(idle)}"] if idle else [])


def _power(db: Session, day: dt.date) -> Station:
    today = db.query(PowerReading).filter_by(reading_date=day).first()
    taken = bool(today and today.reading_taken)
    previous = power_svc.previous_reading(db, day)
    notes = []
    if taken and previous and today.kwh is not None and previous.kwh is not None:
        notes.append(f"{today.kwh - previous.kwh:g} kWh since {previous.reading_date}")
    elif not taken and previous:
        notes.append(f"Last reading was {previous.reading_date}: "
                    f"{previous.kwh:g} kWh, {previous.kvah:g} kVAh")
    return Station("power", "Power reading", "/power",
                   "ok" if taken else "bad",
                   "Taken" if taken else "Not taken", notes=notes)


def _ledger(db: Session, day: dt.date) -> Station:
    row = db.execute(text("""
        SELECT COUNT(*), COALESCE(SUM(CASE WHEN direction='IN' THEN amount ELSE 0 END), 0),
               COALESCE(SUM(CASE WHEN direction='OUT' THEN amount ELSE 0 END), 0)
        FROM digital_ledger WHERE txn_date = :d"""), {"d": day.isoformat()}).first()
    n, in_amt, out_amt = row
    headline = f"{n} {'entry' if n == 1 else 'entries'}"
    if n:
        headline += f" (Rs {in_amt:,.0f} in, Rs {out_amt:,.0f} out)"
    return Station("ledger", "Digital ledger", "/ledger", "muted", headline)


def _as_needed(db: Session, day: dt.date) -> list[Station]:
    advances = db.execute(text(
        "SELECT COUNT(*) FROM advance WHERE adv_date = :d"),
        {"d": day.isoformat()}).scalar()
    pending_salary = db.execute(text("""
        SELECT COUNT(*) FROM salary_month
        WHERE period_year = :y AND period_month = :m AND outstanding > 0"""),
        {"y": day.year, "m": day.month}).scalar()
    return [
        Station("advances", "Advances", "/advances", "muted",
               f"{advances} {'draw' if advances == 1 else 'draws'} today"),
        Station("salary", "Salary", "/salary", "muted",
               f"{pending_salary} pending this month" if pending_salary
               else "Nothing pending this month"),
    ]


def _closing(db: Session) -> Station:
    open_findings = db.execute(text(
        "SELECT COUNT(*) FROM import_anomaly WHERE resolved = 0")).scalar()
    return Station("closing", "Error register", "/anomalies",
                  "warn" if open_findings else "ok",
                  f"{open_findings} open" if open_findings else "Clear")


@dataclass
class DailyRound:
    day: dt.date
    stations: list[Station]
    as_needed: list[Station]
    closing: Station


def round_for(db: Session, day: dt.date) -> DailyRound:
    stations = [
        _attendance(db, day),
        _extra_work(db, day),
        _materials(db, day),
        _consumption(db, day),
        _sales(db, day),
        _production(db, day),
        _power(db, day),
        _ledger(db, day),
    ]
    return DailyRound(day, stations, _as_needed(db, day), _closing(db))
