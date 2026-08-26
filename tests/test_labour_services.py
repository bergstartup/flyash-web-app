"""Service-layer tests for the Phase 1 write paths, against a scratch DB."""

import datetime as dt

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.db.models import Base
from app.db.session import apply_views
from app.services import advance as advance_svc
from app.services import attendance as attendance_svc
from app.services import extra_work as extra_work_svc
from app.services import extra_work_type as ewt_svc
from app.services import labour as labour_svc
from app.services import salary as salary_svc
from app.services.period import PeriodLocked, close


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path/'t.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as c:
        apply_views(c)
    with sessionmaker(bind=engine)() as s:
        yield s


@pytest.fixture()
def raghul(db):
    # An explicit join_date in the past -- defaulting to today() would make
    # this fixture's usability depend on the wall clock vs. the fixed 2026
    # dates the rest of the suite uses.
    return labour_svc.create(db, "OFFICE", name="RAGHUL", wage_type="DAILY",
                             rate=500, join_date=dt.date(2020, 1, 1))


# -- labour master ------------------------------------------------------------

def test_create_assigns_the_next_display_order(db):
    a = labour_svc.create(db, "OFFICE", name="A", wage_type="DAILY", rate=500)
    b = labour_svc.create(db, "OFFICE", name="B", wage_type="DAILY", rate=500)
    assert b.display_order > a.display_order


def test_full_aadhaar_is_rejected(db):
    with pytest.raises(ValueError):
        labour_svc.create(db, "OFFICE", name="A", wage_type="DAILY", rate=500,
                          aadhar_last4="12345678901")


def test_deactivate_is_soft_delete(db, raghul):
    labour_svc.deactivate(db, "OFFICE", raghul.id)
    assert raghul.is_active == 0
    assert raghul.exit_date is not None
    assert raghul.name == "RAGHUL"          # the record survives
    labour_svc.reactivate(db, "OFFICE", raghul.id)
    assert raghul.is_active == 1 and raghul.exit_date is None


# -- attendance -----------------------------------------------------------

def test_default_is_a_full_shift_for_a_joined_labour(db, raghul):
    roster = attendance_svc.roster_for_day(db, dt.date(2026, 8, 1))
    row = next(r for r in roster if r.labour.id == raghul.id)
    assert row.joined and row.shift_units == 1.0


def test_not_yet_joined_has_no_default(db):
    lab = labour_svc.create(db, "OFFICE", name="NEW", wage_type="DAILY", rate=500,
                            join_date=dt.date(2026, 8, 15))
    roster = attendance_svc.roster_for_day(db, dt.date(2026, 8, 1))
    row = next(r for r in roster if r.labour.id == lab.id)
    assert not row.joined and row.shift_units is None


def test_save_day_writes_one_row_per_entry(db, raghul):
    day = dt.date(2026, 8, 1)
    n = attendance_svc.save_day(db, "OFFICE", day, {raghul.id: 0.5})
    assert n == 1
    row = db.execute(text(
        "SELECT shift_units FROM attendance WHERE labour_id = :i"),
        {"i": raghul.id}).scalar()
    assert row == 0.5


def test_save_day_rejects_an_invalid_shift_value(db, raghul):
    with pytest.raises(ValueError):
        attendance_svc.save_day(db, "OFFICE", dt.date(2026, 8, 1), {raghul.id: 0.3})


def test_save_day_rejects_a_labour_who_has_not_joined(db):
    lab = labour_svc.create(db, "OFFICE", name="NEW", wage_type="DAILY", rate=500,
                            join_date=dt.date(2026, 8, 15))
    with pytest.raises(ValueError):
        attendance_svc.save_day(db, "OFFICE", dt.date(2026, 8, 1), {lab.id: 1})


def test_save_day_respects_the_period_lock(db, raghul):
    close(db, "OFFICE", 2026, 7)
    with pytest.raises(PeriodLocked):
        attendance_svc.save_day(db, "OFFICE", dt.date(2026, 7, 15), {raghul.id: 1})


# -- extra work -------------------------------------------------------------

def test_extra_work_snapshots_the_current_rate(db, raghul):
    wt = ewt_svc.create(db, "OFFICE", code="UNLOAD", name="Unloading",
                        unit="UNIT", rate=100, effective_from=dt.date(2026, 7, 1))
    entry = extra_work_svc.add(db, "OFFICE", work_date=dt.date(2026, 7, 15),
                               labour_id=raghul.id, work_type_id=wt.id, qty=3)
    assert entry.rate == 100

    ewt_svc.set_rate(db, "OFFICE", wt.id, 150, dt.date(2026, 8, 1))
    entry.__class__  # no-op; rate change must not touch the existing row
    db.refresh(entry)
    assert entry.rate == 100, "a later rate change must not rewrite a settled entry"


def test_extra_work_needs_a_rate_in_effect(db, raghul):
    wt = ewt_svc.create(db, "OFFICE", code="UNLOAD", name="Unloading",
                        unit="UNIT", rate=100, effective_from=dt.date(2026, 8, 1))
    with pytest.raises(ValueError):
        extra_work_svc.add(db, "OFFICE", work_date=dt.date(2026, 7, 15),
                           labour_id=raghul.id, work_type_id=wt.id, qty=3)


def test_extra_work_delete_removes_the_row(db, raghul):
    wt = ewt_svc.create(db, "OFFICE", code="UNLOAD", name="Unloading",
                        unit="UNIT", rate=100, effective_from=dt.date(2026, 7, 1))
    entry = extra_work_svc.add(db, "OFFICE", work_date=dt.date(2026, 7, 15),
                               labour_id=raghul.id, work_type_id=wt.id, qty=3)
    extra_work_svc.delete(db, "OFFICE", entry.id)
    assert db.execute(text("SELECT COUNT(*) FROM extra_work")).scalar() == 0


# -- advances ---------------------------------------------------------------

def test_digital_advance_autoposts_to_the_ledger(db, raghul):
    advance_svc.add(db, "OFFICE", adv_date=dt.date(2026, 7, 10), labour_id=raghul.id,
                    amount=3000, payment_mode="DIGITAL", paid_to="G-Pay")
    row = db.execute(text(
        "SELECT direction, entry_type, amount FROM digital_ledger")).first()
    assert row == ("OUT", "AUTO", 3000.0)


def test_cash_advance_does_not_touch_the_ledger(db, raghul):
    advance_svc.add(db, "OFFICE", adv_date=dt.date(2026, 7, 10), labour_id=raghul.id,
                    amount=3000, payment_mode="CASH")
    assert db.execute(text("SELECT COUNT(*) FROM digital_ledger")).scalar() == 0


def test_deleting_a_digital_advance_removes_its_ledger_entry(db, raghul):
    adv = advance_svc.add(db, "OFFICE", adv_date=dt.date(2026, 7, 10),
                          labour_id=raghul.id, amount=3000, payment_mode="DIGITAL")
    advance_svc.delete(db, "OFFICE", adv.id)
    assert db.execute(text("SELECT COUNT(*) FROM digital_ledger")).scalar() == 0
    assert db.execute(text("SELECT COUNT(*) FROM advance")).scalar() == 0


# -- salary settlement --------------------------------------------------------

def test_settle_is_idempotent_per_labour_per_month(db, raghul):
    salary_svc.settle(db, "OFFICE", period_year=2026, period_month=7,
                      labour_id=raghul.id, status="PENDING")
    salary_svc.settle(db, "OFFICE", period_year=2026, period_month=7,
                      labour_id=raghul.id, status="PAID", outstanding=0)
    n = db.execute(text("SELECT COUNT(*) FROM salary_settlement")).scalar()
    assert n == 1
    status = db.execute(text("SELECT status FROM salary_settlement")).scalar()
    assert status == "PAID"


# -- extra-work-type rates --------------------------------------------------

def test_rate_history_is_versioned_not_overwritten(db):
    wt = ewt_svc.create(db, "OFFICE", code="UNLOAD", name="Unloading",
                        unit="UNIT", rate=100, effective_from=dt.date(2026, 7, 1))
    ewt_svc.set_rate(db, "OFFICE", wt.id, 150, dt.date(2026, 8, 1))
    assert ewt_svc.current_rate(db, wt.id, dt.date(2026, 7, 15)) == 100
    assert ewt_svc.current_rate(db, wt.id, dt.date(2026, 8, 15)) == 150
