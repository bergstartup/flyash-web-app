"""Unit tests for the derivations, against a scratch database."""

import datetime as dt

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.db import models as m
from app.db.models import Base
from app.db.session import apply_views


@pytest.fixture()
def scratch(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path/'t.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as c:
        apply_views(c)
    Session = sessionmaker(bind=engine)
    with Session() as s:
        yield s, engine


def test_production_is_posted_after_the_days_sales(scratch):
    """Sheet5's convention: sales run in entry order, production closes the day."""
    db, engine = scratch
    p = m.Product(code="FAB", name="FAB", has_stock_ledger=1, ledger_sheet="Sheet5")
    db.add(p); db.flush()
    day = dt.date(2026, 7, 1)
    db.add(m.StockOpening(product_id=p.id, as_on=day, qty=1000))
    for seq, qty in ((1, 100), (2, 200)):
        s = m.Sale(sale_date=day, seq_no=seq, movement_type="SALE")
        db.add(s); db.flush()
        db.add(m.SaleLine(sale_id=s.id, line_no=1, product_id=p.id,
                          qty=qty, rate=6, amount=qty * 6))
    db.add(m.Production(prod_date=day, product_id=p.id, qty=500))
    db.commit()

    rows = db.execute(text("""
        SELECT kind, seq, opening, produced, total, issued, balance
        FROM stock_ledger ORDER BY movement_date, kind, seq""")).all()
    assert [r[0] for r in rows] == [0, 0, 1], "production must sort last"
    assert [r[2] for r in rows] == [1000, 900, 700]      # opening
    assert [r[6] for r in rows] == [900, 700, 1200]      # balance
    assert rows[-1][4] == 1200                           # total = opening + prodn


def test_attendance_rejects_a_third_of_a_shift(scratch):
    db, _ = scratch
    lab = m.Labour(name="X", wage_type="DAILY", rate=500)
    db.add(lab); db.flush()
    db.add(m.Attendance(att_date=dt.date(2026, 7, 1), labour_id=lab.id,
                        shift_units=0.3))
    with pytest.raises(Exception):
        db.commit()


def test_a_damage_cannot_carry_a_payment_mode(scratch):
    db, _ = scratch
    db.add(m.Sale(sale_date=dt.date(2026, 7, 1), seq_no=1,
                  movement_type="DAMAGE", payment_mode="CASH"))
    with pytest.raises(Exception):
        db.commit()


def test_lot_balance_is_derived_not_stored(scratch):
    db, _ = scratch
    mat = m.RawMaterial(name="CEMENT", unit="BAGS", is_lot_tracked=1)
    db.add(mat); db.flush()
    lot = m.MaterialLot(material_id=mat.id, label="L1")
    db.add(lot); db.flush()
    db.add(m.MaterialLotInflow(lot_id=lot.id, as_on=dt.date(2026, 7, 1), qty=100))
    db.flush()
    cons = m.MaterialConsumption(cons_date=dt.date(2026, 7, 2),
                                 material_id=mat.id, qty=12.5)
    db.add(cons); db.flush()
    db.add(m.ConsumptionAllocation(lot_id=lot.id, consumption_id=cons.id, qty=12.5))
    db.commit()
    assert db.execute(text(
        "SELECT qty_remaining FROM lot_balance")).scalar() == pytest.approx(87.5)


def test_a_lot_can_be_returned_to(scratch):
    """04 Jul returns 4.5 bags to a lot with no receipt behind it."""
    db, _ = scratch
    mat = m.RawMaterial(name="CEMENT", unit="BAGS", is_lot_tracked=1)
    db.add(mat); db.flush()
    lot = m.MaterialLot(material_id=mat.id, label="L1")
    db.add(lot); db.flush()
    db.add(m.MaterialLotInflow(lot_id=lot.id, as_on=dt.date(2026, 7, 1), qty=10))
    db.flush()
    cons = m.MaterialConsumption(cons_date=dt.date(2026, 7, 4),
                                 material_id=mat.id, qty=0)
    db.add(cons); db.flush()
    db.add(m.ConsumptionAllocation(lot_id=lot.id, consumption_id=cons.id, qty=-4.5))
    db.commit()
    assert db.execute(text(
        "SELECT qty_remaining FROM lot_balance")).scalar() == pytest.approx(14.5)


def test_an_allocation_needs_exactly_one_source(scratch):
    db, _ = scratch
    mat = m.RawMaterial(name="CEMENT", unit="BAGS")
    db.add(mat); db.flush()
    lot = m.MaterialLot(material_id=mat.id, label="L1")
    db.add(lot); db.flush()
    db.add(m.ConsumptionAllocation(lot_id=lot.id, qty=1))
    with pytest.raises(Exception):
        db.commit()


def test_fixed_wage_labour_appears_without_attendance(scratch):
    """M.DIRECTOR has duty in Sheet15 and no attendance column anywhere."""
    db, _ = scratch
    lab = m.Labour(name="M.DIRECTOR", wage_type="FIXED", rate=75000)
    db.add(lab); db.flush()
    db.add(m.SalarySettlement(period_year=2026, period_month=7,
                              labour_id=lab.id, duty_override=31))
    db.commit()
    row = db.execute(text(
        "SELECT duty, salary, total_payment FROM salary_month")).first()
    assert row == (31, 75000, 75000)


def test_a_lot_is_empty_until_its_stock_actually_arrives(scratch):
    """Sheet12's buckets fill over several days, and the by-lot balance for a
    day before an arrival must not count it."""
    db, _ = scratch
    mat = m.RawMaterial(name="CEMENT", unit="BAGS", is_lot_tracked=1)
    db.add(mat); db.flush()
    lot = m.MaterialLot(material_id=mat.id, label="M / N")
    db.add(lot); db.flush()
    db.add(m.MaterialLotInflow(lot_id=lot.id, as_on=dt.date(2026, 7, 1), qty=9))
    db.add(m.MaterialLotInflow(lot_id=lot.id, as_on=dt.date(2026, 7, 19), qty=10))
    cons = m.MaterialConsumption(cons_date=dt.date(2026, 7, 2),
                                 material_id=mat.id, qty=7.5)
    db.add(cons); db.flush()
    db.add(m.ConsumptionAllocation(lot_id=lot.id, consumption_id=cons.id, qty=7.5))
    db.commit()
    by_day = dict(db.execute(text(
        "SELECT cons_date, qty_remaining FROM lot_balance_daily")).all())
    assert by_day["2026-07-01"] == pytest.approx(9)
    assert by_day["2026-07-02"] == pytest.approx(1.5)
    # the 19th's ten bags are not there on the 2nd, and are on the 19th
    assert by_day["2026-07-19"] == pytest.approx(11.5)
    assert db.execute(text(
        "SELECT qty_remaining FROM lot_balance")).scalar() == pytest.approx(11.5)
