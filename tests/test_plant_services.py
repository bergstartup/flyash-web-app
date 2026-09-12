"""Service-layer tests for the Phase 5 write paths, against a scratch DB."""

import datetime as dt

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, DigitalLedger
from app.db.session import apply_views
from app.services import power as power_svc
from app.services.period import PeriodLocked, close

JULY = dt.date(2026, 7, 1)


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path/'t.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as c:
        apply_views(c)
    with sessionmaker(bind=engine)() as s:
        yield s


# -- the meter --------------------------------------------------------------

def test_a_meter_cannot_go_backwards(db):
    """Rule 2. A reading below the last one is a keying error, not a fall."""
    power_svc.take_reading(db, "OFFICE", reading_date=JULY, kwh=58236.4,
                           kvah=70522.9)
    with pytest.raises(power_svc.MeterWentBackwards, match="below"):
        power_svc.take_reading(db, "OFFICE", reading_date=dt.date(2026, 7, 2),
                               kwh=58000, kvah=70600)


def test_a_day_with_no_reading_needs_a_reason(db):
    with pytest.raises(ValueError, match="needs a reason"):
        power_svc.take_reading(db, "OFFICE", reading_date=JULY, kwh=None,
                               kvah=None)


def test_a_day_nobody_read_the_meter_is_not_a_reading_of_zero(db):
    """29 July reads LEAVE / SIR, and the next day's movement spans both."""
    power_svc.take_reading(db, "OFFICE", reading_date=JULY, kwh=100, kvah=200)
    power_svc.take_reading(db, "OFFICE", reading_date=dt.date(2026, 7, 2),
                           kwh=None, kvah=None, remarks="LEAVE / SIR")
    power_svc.take_reading(db, "OFFICE", reading_date=dt.date(2026, 7, 3),
                           kwh=140, kvah=260)
    rows = {r[0]: r for r in power_svc.readings_for_month(db, 2026, 7)}
    assert "2026-07-02" not in rows, "a day with no reading has no movement"
    assert rows["2026-07-03"][3] == pytest.approx(40)   # 140 - 100
    assert rows["2026-07-03"][6] == 2, "the movement spans two days"


def test_re_reading_a_day_replaces_it(db):
    power_svc.take_reading(db, "OFFICE", reading_date=JULY, kwh=100, kvah=200)
    power_svc.take_reading(db, "OFFICE", reading_date=JULY, kwh=110, kvah=210)
    rows = power_svc.readings_for_month(db, 2026, 7)
    assert len(rows) == 1 and rows[0][1] == 110


def test_a_reading_respects_the_period_lock(db):
    close(db, "OFFICE", 2026, 7)
    with pytest.raises(PeriodLocked):
        power_svc.take_reading(db, "OFFICE", reading_date=JULY, kwh=1, kvah=2)


# -- the digital ledger -----------------------------------------------------

def test_a_hand_entered_line_can_be_removed(db):
    entry = power_svc.post(db, "OFFICE", txn_date=JULY, direction="OUT",
                           amount=1500, description="Petrol")
    power_svc.delete(db, "OFFICE", entry.id)
    assert power_svc.ledger_for_month(db, 2026, 7) == []


def test_a_line_that_posted_itself_belongs_to_what_posted_it(db):
    db.add(DigitalLedger(txn_date=JULY, direction="OUT", entry_type="AUTO",
                         amount=3000, description="Advance to RAGHUL",
                         source_table="advance", source_id=1))
    db.commit()
    entry = power_svc.ledger_for_month(db, 2026, 7)[0]
    with pytest.raises(ValueError, match="advance"):
        power_svc.delete(db, "OFFICE", entry.id)


def test_the_opening_balance_is_not_income(db):
    """C14: the Rs 14,273 June balance opens the month, it does not earn it."""
    db.add(DigitalLedger(txn_date=JULY, direction="IN", entry_type="OPENING",
                         amount=14273, description="JUNE BALANCE"))
    db.commit()
    power_svc.post(db, "OFFICE", txn_date=JULY, direction="IN", amount=8880)
    power_svc.post(db, "OFFICE", txn_date=JULY, direction="OUT", amount=1500)
    opening, income, expense, closing = power_svc.ledger_summary(db, 2026, 7)
    assert (opening, income, expense) == (14273, 8880, 1500)
    assert closing == pytest.approx(14273 + 8880 - 1500)


def test_a_ledger_line_must_carry_an_amount(db):
    with pytest.raises(ValueError, match="positive"):
        power_svc.post(db, "OFFICE", txn_date=JULY, direction="IN", amount=0)


def test_a_ledger_line_respects_the_period_lock(db):
    close(db, "OFFICE", 2026, 7)
    with pytest.raises(PeriodLocked):
        power_svc.post(db, "OFFICE", txn_date=JULY, direction="IN", amount=10)
