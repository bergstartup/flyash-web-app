"""Month-to-month carry-forward, and the opening figures entered at go-live."""

import datetime as dt

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.db.models import (Base, DigitalLedger, MaterialLot, MaterialLotInflow,
                           PowerReading, RawMaterial, StockOpening)
from app.db.session import apply_views
from app.reports import opening as opening_rpt
from app.services import material as material_svc
from app.services import opening as opening_svc
from app.services import product as product_svc
from app.services import production as production_svc
from app.services import sale as sale_svc
from app.services.period import PeriodLocked, close

JULY = dt.date(2026, 7, 1)
AUGUST = dt.date(2026, 8, 1)
OCTOBER = dt.date(2026, 10, 1)


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path/'t.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as c:
        apply_views(c)
    with sessionmaker(bind=engine)() as s:
        yield s


@pytest.fixture()
def brick(db):
    """Opens July with 1,000, makes 500 and sells 300: closes July on 1,200."""
    p = product_svc.create(db, "OFFICE", code="FAB", name="FLY ASH BRICKS",
                           default_rate=6, ledger_sheet="Sheet5")
    db.add(StockOpening(product_id=p.id, as_on=JULY, qty=1000))
    db.commit()
    production_svc.save_day(db, "OFFICE", dt.date(2026, 7, 10), {p.id: 500})
    sale_svc.create(db, "OFFICE", sale_date=dt.date(2026, 7, 20),
                    payment_mode="CASH",
                    lines=[{"product_id": p.id, "qty": 300, "rate": 6}])
    return p


@pytest.fixture()
def cement(db):
    mat = RawMaterial(name="CEMENT", unit="BAGS", is_lot_tracked=1)
    db.add(mat)
    db.commit()
    return mat


def _summary(db, product_id, period):
    return db.execute(text("""
        SELECT opening, production, sales, closing, is_entered
        FROM stock_month_summary
        WHERE product_id = :p AND period_start = :s"""),
        {"p": product_id, "s": period.isoformat()}).first()


# -- stock rolls itself -----------------------------------------------------

def test_a_month_with_no_opening_opens_on_the_last_ones_closing(db, brick):
    production_svc.save_day(db, "OFFICE", dt.date(2026, 8, 3), {brick.id: 100})
    opening, production, sales, closing, entered = _summary(db, brick.id, AUGUST)
    assert opening == pytest.approx(1200)
    assert closing == pytest.approx(1300)
    assert not entered


def test_the_ledger_rows_carry_across_the_month_too(db, brick):
    production_svc.save_day(db, "OFFICE", dt.date(2026, 8, 3), {brick.id: 100})
    first = db.execute(text("""
        SELECT opening, balance FROM stock_ledger
        WHERE product_id = :p AND period_start = :s"""),
        {"p": brick.id, "s": AUGUST.isoformat()}).first()
    assert tuple(first) == pytest.approx((1200, 1300))


def test_a_sale_can_draw_on_stock_carried_from_last_month(db, brick):
    """Before the carry-forward this was refused: August opened on nought."""
    sale, _ = sale_svc.create(db, "OFFICE", sale_date=dt.date(2026, 8, 2),
                              payment_mode="CASH",
                              lines=[{"product_id": brick.id, "qty": 1200,
                                      "rate": 6}])
    assert sale.id
    with pytest.raises(sale_svc.StockShortfall):
        sale_svc.create(db, "OFFICE", sale_date=dt.date(2026, 8, 2),
                        payment_mode="CASH",
                        lines=[{"product_id": brick.id, "qty": 1, "rate": 6}])


def test_it_carries_across_months_with_nothing_in_them(db, brick):
    production_svc.save_day(db, "OFFICE", dt.date(2026, 10, 2), {brick.id: 10})
    assert _summary(db, brick.id, OCTOBER)[0] == pytest.approx(1200)


# -- an entered opening wins ------------------------------------------------

def test_an_entered_opening_replaces_what_was_carried(db, brick):
    opening_svc.set_stock(db, "OFFICE", OCTOBER, brick.id, 5000)
    db.commit()
    opening, production, sales, closing, entered = _summary(db, brick.id, OCTOBER)
    assert (opening, closing, entered) == (5000, 5000, 1), \
        "a month with only an opening still shows, so it can be checked"
    assert sale_svc.available(db, brick.id, dt.date(2026, 10, 5)) == 5000


def test_the_month_after_an_entered_opening_carries_from_it(db, brick):
    opening_svc.set_stock(db, "OFFICE", OCTOBER, brick.id, 5000)
    db.commit()
    sale_svc.create(db, "OFFICE", sale_date=dt.date(2026, 10, 9),
                    payment_mode="CASH",
                    lines=[{"product_id": brick.id, "qty": 400, "rate": 6}])
    production_svc.save_day(db, "OFFICE", dt.date(2026, 11, 1), {brick.id: 1})
    assert _summary(db, brick.id, dt.date(2026, 11, 1))[0] == pytest.approx(4600)


def test_a_blank_goes_back_to_carrying(db, brick):
    opening_svc.set_stock(db, "OFFICE", OCTOBER, brick.id, 5000)
    opening_svc.set_stock(db, "OFFICE", OCTOBER, brick.id, None)
    db.commit()
    assert db.query(StockOpening).filter_by(as_on=OCTOBER).count() == 0
    assert sale_svc.available(db, brick.id, OCTOBER) == pytest.approx(1200)


def test_nought_is_an_opening_and_not_a_blank(db, brick):
    opening_svc.set_stock(db, "OFFICE", OCTOBER, brick.id, 0)
    db.commit()
    assert sale_svc.available(db, brick.id, OCTOBER) == 0


def test_the_screen_shows_carried_beside_entered(db, brick):
    opening_svc.set_stock(db, "OFFICE", OCTOBER, brick.id, 5000)
    db.commit()
    line = opening_rpt.for_month(db, OCTOBER).stock[0]
    assert (line.carried, line.entered) == (1200, 5000)


def test_an_opening_is_for_the_first_of_a_month(db, brick):
    with pytest.raises(ValueError, match="first of a month"):
        opening_svc.set_stock(db, "OFFICE", dt.date(2026, 10, 2), brick.id, 1)


def test_an_opening_respects_the_period_lock(db, brick):
    close(db, "OFFICE", 2026, 10)
    with pytest.raises(PeriodLocked):
        opening_svc.set_stock(db, "OFFICE", OCTOBER, brick.id, 1)


def test_stock_on_hand_cannot_be_negative(db, brick):
    with pytest.raises(ValueError, match="negative"):
        opening_svc.set_stock(db, "OFFICE", OCTOBER, brick.id, -1)


# -- the digital account ----------------------------------------------------

def _ledger(db, period):
    return db.execute(text("""
        SELECT opening, income, expense, closing FROM digital_ledger_month
        WHERE period_start = :s"""), {"s": period.isoformat()}).first()


def _post(db, day, direction, amount, entry_type="MANUAL"):
    db.add(DigitalLedger(txn_date=day, direction=direction, amount=amount,
                         entry_type=entry_type))
    db.commit()


def test_the_digital_account_carries_its_closing_forward(db):
    _post(db, JULY, "IN", 14273, "OPENING")
    _post(db, dt.date(2026, 7, 9), "OUT", 4273)
    _post(db, dt.date(2026, 8, 4), "IN", 500)
    assert tuple(_ledger(db, JULY)) == pytest.approx((14273, 0, 4273, 10000))
    assert tuple(_ledger(db, AUGUST)) == pytest.approx((10000, 500, 0, 10500))


def test_an_entered_balance_replaces_the_carried_one(db):
    _post(db, JULY, "IN", 14273, "OPENING")
    opening_svc.set_ledger(db, "OFFICE", OCTOBER, 2500)
    db.commit()
    _post(db, dt.date(2026, 10, 3), "OUT", 100)
    assert tuple(_ledger(db, OCTOBER)) == pytest.approx((2500, 0, 100, 2400))
    assert opening_rpt.for_month(db, OCTOBER).ledger.carried == 14273


def test_an_overdraft_opens_negative(db):
    opening_svc.set_ledger(db, "OFFICE", OCTOBER, -750)
    db.commit()
    assert _ledger(db, OCTOBER)[0] == pytest.approx(-750)
    assert opening_rpt.for_month(db, OCTOBER).ledger.entered == -750


def test_re_entering_the_balance_replaces_rather_than_adds(db):
    opening_svc.set_ledger(db, "OFFICE", OCTOBER, 2500)
    opening_svc.set_ledger(db, "OFFICE", OCTOBER, 3000)
    db.commit()
    assert db.query(DigitalLedger).count() == 1
    assert _ledger(db, OCTOBER)[0] == pytest.approx(3000)


# -- cement and its lots ----------------------------------------------------

def test_cement_carries_its_closing_forward(db, cement):
    ramco = RawMaterial(name="RAMCO CEMENT", unit="BAGS")
    db.add(ramco)
    db.commit()
    opening_svc.set_cement(db, "OFFICE", JULY, 100)
    db.commit()
    material_svc.receive(db, "OFFICE", receipt_date=dt.date(2026, 7, 5),
                         material_id=ramco.id, qty=40, rate=350)
    assert opening_rpt.cement_opening(db, AUGUST) == pytest.approx(140)
    assert opening_rpt.for_month(db, OCTOBER).cement.carried == pytest.approx(140)
    opening_svc.set_cement(db, "OFFICE", OCTOBER, 90)
    db.commit()
    assert opening_rpt.cement_opening(db, OCTOBER) == pytest.approx(90)


@pytest.fixture()
def july_lot(db, cement):
    """A July lot holding 100, 30 of it used."""
    lot = MaterialLot(material_id=cement.id, lot_date=JULY, label="1.7.26",
                      display_order=0)
    db.add(lot)
    db.flush()
    db.add(MaterialLotInflow(lot_id=lot.id, as_on=JULY, qty=100))
    db.commit()
    material_svc.consume(db, "OFFICE", cons_date=dt.date(2026, 7, 3),
                         material_id=cement.id, allocations={lot.id: 30})
    return lot


def test_a_lot_counted_on_the_day_holds_what_was_counted(db, july_lot):
    assert opening_rpt.for_month(db, OCTOBER).lots[0].carried == 70
    opening_svc.set_lot(db, "OFFICE", OCTOBER, july_lot.id, 0)
    db.commit()
    assert material_svc.remaining(db, july_lot.id, OCTOBER) == 0
    assert material_svc.remaining(db, july_lot.id, dt.date(2026, 9, 30)) == 70, \
        "the lot's history is not rewritten"
    line = opening_rpt.for_month(db, OCTOBER).lots[0]
    assert (line.carried, line.entered) == (70, 0), \
        "a lot zeroed on the day stays on screen"


def test_recounting_a_lot_replaces_the_count(db, july_lot):
    opening_svc.set_lot(db, "OFFICE", OCTOBER, july_lot.id, 0)
    opening_svc.set_lot(db, "OFFICE", OCTOBER, july_lot.id, 50)
    db.commit()
    assert material_svc.remaining(db, july_lot.id, OCTOBER) == 50
    opening_svc.set_lot(db, "OFFICE", OCTOBER, july_lot.id, None)
    db.commit()
    assert material_svc.remaining(db, july_lot.id, OCTOBER) == 70


def test_a_lot_the_app_never_saw_can_be_added(db, cement, july_lot):
    lot = opening_svc.add_lot(db, "OFFICE", OCTOBER, cement.id, "",
                              120, dt.date(2026, 9, 12))
    db.commit()
    assert lot.label == "12.09.26"
    assert material_svc.remaining(db, lot.id, OCTOBER) == 120
    assert material_svc.remaining(db, lot.id, dt.date(2026, 9, 30)) == 0
    with pytest.raises(ValueError, match="already a lot"):
        opening_svc.add_lot(db, "OFFICE", OCTOBER, cement.id, "12.09.26", 5)


# -- the meter --------------------------------------------------------------

def test_the_billing_cycle_start_is_its_own_row(db):
    opening_svc.set_eb_anchor(db, "OFFICE", dt.date(2026, 9, 16), 61000, 73500)
    db.commit()
    assert db.query(PowerReading).filter_by(is_eb_anchor=1).count() == 1
    db.add(PowerReading(reading_date=dt.date(2026, 9, 30), kwh=61500,
                        kvah=74000))
    db.commit()
    with pytest.raises(ValueError, match="already has a daily reading"):
        opening_svc.set_eb_anchor(db, "OFFICE", dt.date(2026, 9, 30), 1, 1)
