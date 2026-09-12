"""Service-layer tests for the Phase 2 write paths, against a scratch DB."""

import datetime as dt

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, Customer, Receipt, StockOpening
from app.db.session import apply_views
from app.services import product as product_svc
from app.services import production as production_svc
from app.services import sale as sale_svc
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


@pytest.fixture()
def brick(db):
    """A ledger product opening the month with 1,000 on hand."""
    p = product_svc.create(db, "OFFICE", code="FAB", name="FLY ASH BRICKS",
                           default_rate=6, ledger_sheet="Sheet5")
    db.add(StockOpening(product_id=p.id, as_on=JULY, qty=1000))
    db.commit()
    return p


@pytest.fixture()
def cement(db):
    """A product that is sold but keeps no stock ledger."""
    return product_svc.create(db, "OFFICE", code="CEMENT", name="CEMENT",
                              default_rate=400)


# -- product master ---------------------------------------------------------

def test_a_ledger_sheet_renders_only_one_product(db, brick):
    with pytest.raises(ValueError, match="already renders"):
        product_svc.create(db, "OFFICE", code="OTHER", name="OTHER",
                           ledger_sheet="Sheet5")


def test_changing_a_rate_does_not_rewrite_a_sale_already_recorded(db, brick):
    sale, _ = sale_svc.create(db, "OFFICE", sale_date=JULY, payment_mode="CASH",
                              lines=[{"product_id": brick.id, "qty": 100,
                                      "rate": 6}])
    product_svc.set_rate(db, "OFFICE", brick.id, 9)
    assert sale.lines[0].rate == 6 and sale.lines[0].amount == 600


def test_deactivating_a_product_keeps_it_out_of_the_entry_screens(db, brick):
    product_svc.deactivate(db, "OFFICE", brick.id)
    assert brick.id not in [p.id for p in product_svc.listing(db)]
    assert brick.id in [p.id for p in product_svc.listing(db, include_inactive=True)]


# -- production -------------------------------------------------------------

def test_saving_a_day_twice_updates_rather_than_duplicates(db, brick):
    production_svc.save_day(db, "OFFICE", JULY, {brick.id: 500})
    production_svc.save_day(db, "OFFICE", JULY, {brick.id: 800})
    rows = production_svc.for_day(db, JULY)
    assert len(rows) == 1 and rows[0].qty == 800


def test_a_blank_removes_the_entry_rather_than_storing_a_nought(db, brick):
    production_svc.save_day(db, "OFFICE", JULY, {brick.id: 500})
    production_svc.save_day(db, "OFFICE", JULY, {brick.id: None})
    assert production_svc.for_day(db, JULY) == []


def test_production_defaults_to_posting_last_in_the_day(db, brick):
    production_svc.save_day(db, "OFFICE", JULY, {brick.id: 500})
    assert production_svc.for_day(db, JULY)[0].after_seq is None


def test_production_position_moves_it_among_the_days_sales(db, brick):
    sale_svc.create(db, "OFFICE", sale_date=JULY, payment_mode="CASH",
                    lines=[{"product_id": brick.id, "qty": 100, "rate": 6}])
    sale_svc.create(db, "OFFICE", sale_date=JULY, payment_mode="CASH",
                    lines=[{"product_id": brick.id, "qty": 200, "rate": 6}])
    [entry] = production_svc.save_day(db, "OFFICE", JULY, {brick.id: 500})

    kinds = [r[1] for r in production_svc.ledger(db, brick.id, JULY)]
    assert kinds == [0, 0, 1], "production posts last by default"

    production_svc.set_position(db, "OFFICE", entry.id, after_seq=1)
    ledger = production_svc.ledger(db, brick.id, JULY)
    assert [r[1] for r in ledger] == [0, 1, 0]
    # ... and the balance the second sale opens on reflects it
    assert ledger[2][6] == 1400   # 1000 - 100 + 500


def test_production_respects_the_period_lock(db, brick):
    close(db, "OFFICE", 2026, 7)
    with pytest.raises(PeriodLocked):
        production_svc.save_day(db, "OFFICE", JULY, {brick.id: 500})


# -- sales: rule 1, stock on hand -------------------------------------------

def test_a_sale_cannot_issue_stock_that_is_not_there(db, brick):
    with pytest.raises(sale_svc.StockShortfall, match="1,001 asked for"):
        sale_svc.create(db, "OFFICE", sale_date=JULY, payment_mode="CASH",
                        lines=[{"product_id": brick.id, "qty": 1001, "rate": 6}])


def test_the_days_production_counts_towards_what_can_be_sold(db, brick):
    production_svc.save_day(db, "OFFICE", JULY, {brick.id: 500})
    sale, _ = sale_svc.create(db, "OFFICE", sale_date=JULY, payment_mode="CASH",
                              lines=[{"product_id": brick.id, "qty": 1500,
                                      "rate": 6}])
    assert sale.id


def test_two_lines_of_one_product_are_checked_together(db, brick):
    with pytest.raises(sale_svc.StockShortfall):
        sale_svc.create(db, "OFFICE", sale_date=JULY, payment_mode="CASH",
                        lines=[{"product_id": brick.id, "qty": 600, "rate": 6},
                               {"product_id": brick.id, "qty": 600, "rate": 6}])


def test_a_product_with_no_ledger_is_not_stock_checked(db, cement):
    sale, _ = sale_svc.create(db, "OFFICE", sale_date=JULY, payment_mode="CASH",
                              lines=[{"product_id": cement.id, "qty": 9999,
                                      "rate": 400}])
    assert sale.id


# -- sales: the rest --------------------------------------------------------

def test_a_trip_carrying_two_products_is_one_sale_with_two_lines(db, brick, cement):
    sale, _ = sale_svc.create(
        db, "OFFICE", sale_date=JULY, payment_mode="CASH", destination="KVK",
        lines=[{"product_id": brick.id, "qty": 100, "rate": 6},
               {"product_id": cement.id, "qty": 2, "rate": 400}])
    assert len(sale.lines) == 2
    assert [line.line_no for line in sale.lines] == [1, 2]


def test_an_amount_that_is_not_qty_times_rate_is_flagged_as_overridden(db, brick):
    sale, _ = sale_svc.create(db, "OFFICE", sale_date=JULY, payment_mode="CASH",
                              lines=[{"product_id": brick.id, "qty": 100,
                                      "rate": 6, "amount": 500}])
    assert sale.lines[0].amount == 500 and sale.lines[0].amount_overridden == 1


def test_a_credit_sale_has_to_say_who_owes_the_money(db, brick):
    """A14: fifteen July credit sales name a destination and no customer."""
    with pytest.raises(ValueError, match="who owes"):
        sale_svc.create(db, "OFFICE", sale_date=JULY, payment_mode="CREDIT",
                        destination="KVK",
                        lines=[{"product_id": brick.id, "qty": 100, "rate": 6}])


def test_a_damage_moves_stock_and_carries_no_money(db, brick):
    with pytest.raises(ValueError, match="moves stock and nothing else"):
        sale_svc.create(db, "OFFICE", sale_date=JULY, movement_type="DAMAGE",
                        payment_mode="CASH",
                        lines=[{"product_id": brick.id, "qty": 16}])
    sale, _ = sale_svc.create(db, "OFFICE", sale_date=JULY,
                              movement_type="DAMAGE", destination="SIR",
                              lines=[{"product_id": brick.id, "qty": 16}])
    assert sale.movement_type == "DAMAGE"
    # it still comes off the stock
    assert sale_svc.available(db, brick.id, JULY) == 984


def test_a_damage_is_not_revenue(db, brick):
    sale_svc.create(db, "OFFICE", sale_date=JULY, movement_type="DAMAGE",
                    lines=[{"product_id": brick.id, "qty": 16}])
    assert db.execute(text("SELECT COUNT(*) FROM sales_register")).scalar() == 0


def test_seq_numbers_run_per_day(db, brick):
    first, _ = sale_svc.create(db, "OFFICE", sale_date=JULY, payment_mode="CASH",
                               lines=[{"product_id": brick.id, "qty": 10, "rate": 6}])
    second, _ = sale_svc.create(db, "OFFICE", sale_date=JULY, payment_mode="CASH",
                                lines=[{"product_id": brick.id, "qty": 10, "rate": 6}])
    later, _ = sale_svc.create(db, "OFFICE", sale_date=dt.date(2026, 7, 2),
                               payment_mode="CASH",
                               lines=[{"product_id": brick.id, "qty": 10, "rate": 6}])
    assert (first.seq_no, second.seq_no, later.seq_no) == (1, 2, 1)


def test_going_over_a_credit_limit_warns_rather_than_blocks(db, brick):
    customer = Customer(name="KVK", credit_limit=500)
    db.add(customer)
    db.commit()
    sale, warnings = sale_svc.create(
        db, "OFFICE", sale_date=JULY, payment_mode="CREDIT",
        customer_id=customer.id,
        lines=[{"product_id": brick.id, "qty": 200, "rate": 6}])
    assert sale.id, "a credit decision is the owner's, not the app's"
    assert warnings and "limit" in warnings[0]


def test_a_sale_with_money_collected_against_it_cannot_be_deleted(db, brick):
    sale, _ = sale_svc.create(db, "OFFICE", sale_date=JULY, payment_mode="CREDIT",
                              customer_id=_customer(db),
                              lines=[{"product_id": brick.id, "qty": 100, "rate": 6}])
    db.add(Receipt(receipt_date=JULY, sale_id=sale.id, amount=600))
    db.commit()
    with pytest.raises(sale_svc.HasReceipts):
        sale_svc.delete(db, "OFFICE", sale.id)


def test_deleting_a_sale_returns_the_stock(db, brick):
    sale, _ = sale_svc.create(db, "OFFICE", sale_date=JULY, payment_mode="CASH",
                              lines=[{"product_id": brick.id, "qty": 400, "rate": 6}])
    assert sale_svc.available(db, brick.id, JULY) == 600
    sale_svc.delete(db, "OFFICE", sale.id)
    assert sale_svc.available(db, brick.id, JULY) == 1000


def test_a_sale_respects_the_period_lock(db, brick):
    close(db, "OFFICE", 2026, 7)
    with pytest.raises(PeriodLocked):
        sale_svc.create(db, "OFFICE", sale_date=JULY, payment_mode="CASH",
                        lines=[{"product_id": brick.id, "qty": 10, "rate": 6}])


def _customer(db) -> int:
    customer = Customer(name="KVK")
    db.add(customer)
    db.commit()
    return customer.id
