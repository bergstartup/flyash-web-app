"""The daily round: a live-derived checklist, not a stored one.

Unit tests exercise app.reports.daily against a scratch database so the
station logic is checked in isolation; the HTTP tests are smoke tests
against the imported July data, in the style of the other web tests.
"""

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import models as m
from app.db.models import Base
from app.db.session import apply_views
from app.main import app
from app.reports import daily

client = TestClient(app)


@pytest.fixture()
def scratch(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path/'t.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as c:
        apply_views(c)
    Session = sessionmaker(bind=engine)
    with Session() as s:
        yield s


def _by_key(stations, key):
    return next(s for s in stations if s.key == key)


def test_attendance_is_warn_when_someone_is_unmarked(scratch):
    db = scratch
    a = m.Labour(name="A", wage_type="DAILY", rate=500)
    b = m.Labour(name="B", wage_type="DAILY", rate=500)
    db.add_all([a, b]); db.flush()
    day = dt.date(2026, 7, 1)
    db.add(m.Attendance(att_date=day, labour_id=a.id, shift_units=1))
    db.commit()

    station = _by_key(daily.round_for(db, day).stations, "attendance")
    assert station.status == "warn"
    assert station.headline == "1 / 2 marked"
    assert station.missing == ["B"]


def test_attendance_is_ok_once_everyone_joined_is_marked(scratch):
    db = scratch
    a = m.Labour(name="A", wage_type="DAILY", rate=500)
    db.add(a); db.flush()
    day = dt.date(2026, 7, 1)
    db.add(m.Attendance(att_date=day, labour_id=a.id, shift_units=1))
    db.commit()

    station = _by_key(daily.round_for(db, day).stations, "attendance")
    assert station.status == "ok"
    assert not station.missing


def test_a_labourer_not_yet_joined_is_not_expected(scratch):
    db = scratch
    day = dt.date(2026, 7, 1)
    a = m.Labour(name="A", wage_type="DAILY", rate=500,
                join_date=dt.date(2026, 7, 5))
    db.add(a); db.commit()

    station = _by_key(daily.round_for(db, day).stations, "attendance")
    assert station.headline == "0 / 0 marked"
    assert station.status == "muted"


def test_production_never_nags_about_an_idle_product(scratch):
    """Two of three ledger products in the real register run on well under a
    third of the month's days -- an idle day is normal, not a gap."""
    db = scratch
    p = m.Product(code="FAB", name="FAB", has_stock_ledger=1, ledger_sheet="Sheet5")
    db.add(p); db.commit()

    station = _by_key(daily.round_for(db, dt.date(2026, 7, 1)).stations,
                      "production")
    assert station.status == "muted"
    assert station.headline == "0 / 1 ledger products ran"
    assert station.notes == ["Idle today: FAB"]


def test_a_power_reading_not_taken_is_bad(scratch):
    db = scratch
    day = dt.date(2026, 7, 2)
    db.add(m.PowerReading(reading_date=dt.date(2026, 7, 1), kwh=100, kvah=80))
    db.commit()

    station = _by_key(daily.round_for(db, day).stations, "power")
    assert station.status == "bad"
    assert station.headline == "Not taken"
    assert "2026-07-01" in station.notes[0]


def test_a_power_reading_taken_is_ok_and_shows_the_days_draw(scratch):
    db = scratch
    db.add(m.PowerReading(reading_date=dt.date(2026, 7, 1), kwh=100, kvah=80))
    db.add(m.PowerReading(reading_date=dt.date(2026, 7, 2), kwh=133, kvah=90))
    db.commit()

    station = _by_key(daily.round_for(db, dt.date(2026, 7, 2)).stations, "power")
    assert station.status == "ok"
    assert station.notes == ["33 kWh since 2026-07-01"]


def test_a_sale_missing_its_payment_mode_is_flagged(scratch):
    db = scratch
    day = dt.date(2026, 7, 1)
    db.add(m.Sale(sale_date=day, seq_no=1, movement_type="SALE"))
    db.commit()

    station = _by_key(daily.round_for(db, day).stations, "sales")
    assert station.status == "warn"
    assert station.notes == ["Trip #1 has no payment mode recorded"]


def test_a_below_reorder_material_is_surfaced(scratch):
    db = scratch
    db.add(m.RawMaterial(name="CEMENT", unit="BAGS", reorder_level=50))
    db.commit()

    station = _by_key(daily.round_for(db, dt.date(2026, 7, 1)).stations,
                      "materials")
    assert station.status == "warn"
    assert "CEMENT" in station.notes[0]


@pytest.mark.parametrize("path", [
    "/daily", "/daily?date=2026-07-01", "/daily?date=2026-07-15",
    "/daily?date=2026-07-29",  # the LEAVE day: reading_taken = 0
])
def test_the_page_renders(path):
    r = client.get(path)
    assert r.status_code == 200
    assert "Daily round" in r.text


def test_navigation_lights_up_overview_on_the_daily_page():
    body = client.get("/daily").text
    assert '<a href="/daily" class="on">Daily round</a>' in body
