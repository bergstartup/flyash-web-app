"""Service-layer tests for the Phase 4 write paths, against a scratch DB."""

import datetime as dt

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, MaterialLot, MaterialLotInflow
from app.db.session import apply_views
from app.services import material as material_svc
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
def cement(db):
    from app.db.models import RawMaterial
    mat = RawMaterial(name="CEMENT", unit="BAGS", is_lot_tracked=1,
                      reorder_level=50)
    db.add(mat)
    db.commit()
    return mat


@pytest.fixture()
def two_lots(db, cement):
    """Two lots holding 100 and 60, arriving on the 1st and the 5th."""
    made = []
    for order, (label, day, qty) in enumerate(
            [("OLD", JULY, 100.0), ("NEW", dt.date(2026, 7, 5), 60.0)]):
        lot = MaterialLot(material_id=cement.id, lot_date=day, label=label,
                          display_order=order)
        db.add(lot)
        db.flush()
        db.add(MaterialLotInflow(lot_id=lot.id, as_on=day, qty=qty))
        made.append(lot)
    db.commit()
    return made


def test_a_receipt_opens_a_lot_when_none_is_named(db, cement):
    material_svc.receive(db, "OFFICE", receipt_date=JULY, material_id=cement.id,
                         qty=80, rate=350)
    lots = material_svc.lots(db, cement.id)
    assert len(lots) == 1 and lots[0][4] == pytest.approx(80)


def test_a_receipt_lands_in_the_lot_it_names(db, cement, two_lots):
    material_svc.receive(db, "OFFICE", receipt_date=dt.date(2026, 7, 20),
                         material_id=cement.id, qty=40, rate=350,
                         lot_id=two_lots[0].id)
    assert material_svc.remaining(db, two_lots[0].id, dt.date(2026, 7, 20)) == 140
    # ... and not before it arrived
    assert material_svc.remaining(db, two_lots[0].id, dt.date(2026, 7, 19)) == 100


def test_an_amount_that_is_not_qty_times_rate_is_flagged(db, cement):
    receipt = material_svc.receive(db, "OFFICE", receipt_date=JULY,
                                   material_id=cement.id, qty=8.5, rate=4600,
                                   amount=39000)
    assert receipt.amount == 39000 and receipt.amount_overridden == 1


def test_consumption_draws_from_the_lots_it_names(db, cement, two_lots):
    """Not FIFO: 3 July draws from three lots at once, so the split is entered."""
    day = dt.date(2026, 7, 6)
    cons = material_svc.consume(db, "OFFICE", cons_date=day,
                                material_id=cement.id,
                                allocations={two_lots[0].id: 10,
                                             two_lots[1].id: 5.5})
    assert cons.qty == pytest.approx(15.5), "the total is the sum of the split"
    assert material_svc.remaining(db, two_lots[0].id, day) == pytest.approx(90)
    assert material_svc.remaining(db, two_lots[1].id, day) == pytest.approx(54.5)


def test_a_lot_cannot_give_up_more_than_it_holds(db, cement, two_lots):
    with pytest.raises(material_svc.LotShortfall, match="OLD"):
        material_svc.consume(db, "OFFICE", cons_date=dt.date(2026, 7, 6),
                             material_id=cement.id,
                             allocations={two_lots[0].id: 101})


def test_a_lot_cannot_be_drawn_before_it_arrives(db, cement, two_lots):
    with pytest.raises(material_svc.LotShortfall, match="NEW"):
        material_svc.consume(db, "OFFICE", cons_date=dt.date(2026, 7, 2),
                             material_id=cement.id,
                             allocations={two_lots[1].id: 10})


def test_a_lot_can_be_returned_to(db, cement, two_lots):
    """04 Jul returns 4.5 bags to a lot; a negative draw is not a shortfall."""
    day = dt.date(2026, 7, 6)
    material_svc.consume(db, "OFFICE", cons_date=day, material_id=cement.id,
                         allocations={two_lots[0].id: 10, two_lots[1].id: -4.5})
    assert material_svc.remaining(db, two_lots[1].id, day) == pytest.approx(64.5)


def test_nothing_consumed_is_rejected(db, cement, two_lots):
    with pytest.raises(ValueError, match="Nothing consumed"):
        material_svc.consume(db, "OFFICE", cons_date=JULY,
                             material_id=cement.id,
                             allocations={two_lots[0].id: 0})


def test_a_lot_of_another_material_is_refused(db, cement, two_lots):
    from app.db.models import RawMaterial
    other = RawMaterial(name="POWDER", unit="UNIT")
    db.add(other)
    db.commit()
    with pytest.raises(ValueError, match="does not hold this material"):
        material_svc.consume(db, "OFFICE", cons_date=JULY,
                             material_id=other.id,
                             allocations={two_lots[0].id: 1})


def test_below_reorder_reports_what_needs_buying(db, cement, two_lots):
    assert material_svc.below_reorder(db) == []
    material_svc.consume(db, "OFFICE", cons_date=dt.date(2026, 7, 6),
                         material_id=cement.id,
                         allocations={two_lots[0].id: 100, two_lots[1].id: 55})
    [(name, level, on_hand)] = material_svc.below_reorder(db)
    assert name == "CEMENT" and level == 50 and on_hand == pytest.approx(5)


def test_materials_respect_the_period_lock(db, cement, two_lots):
    close(db, "OFFICE", 2026, 7)
    with pytest.raises(PeriodLocked):
        material_svc.receive(db, "OFFICE", receipt_date=JULY,
                             material_id=cement.id, qty=1, rate=1)
    with pytest.raises(PeriodLocked):
        material_svc.consume(db, "OFFICE", cons_date=JULY,
                             material_id=cement.id,
                             allocations={two_lots[0].id: 1})
