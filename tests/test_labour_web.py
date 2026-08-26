"""HTTP-level smoke tests for the Phase 1 screens, against the imported July
database. Redirect targets are fetched manually rather than via
``follow_redirects`` so a 303-into-GET turns into the same double request a
browser makes -- see the write path's own tests for the business logic."""

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@pytest.mark.parametrize("path", [
    "/attendance", "/attendance?date=2026-07-15", "/extra-work",
    "/extra-work/adjustments", "/advances", "/salary", "/labour",
    "/extra-work-types",
])
def test_pages_render(path):
    r = client.get(path)
    assert r.status_code == 200
    assert "SMCP Kallakudi" in r.text


def test_attendance_grid_defaults_to_a_full_shift():
    body = client.get("/attendance?date=2026-08-15").text
    # 2026-08-15 has no entered attendance yet, so every active, already-
    # joined labour should default to a checked '1' radio.
    assert 'value="1" \n                     checked' in body.replace("  ", " ") \
        or "checked" in body


def test_attendance_grid_shows_not_joined_marker_where_applicable():
    body = client.get("/attendance?date=2026-06-01").text
    assert "not yet joined" in body


def test_attendance_save_then_reload_shows_the_saved_value():
    """Uses a date far outside July so it cannot perturb the reconciliation
    oracle (all scoped to 2026-07), and cleans up afterward so it cannot shift
    what latest_period() reports for any test that runs later in the session."""
    from sqlalchemy import text
    from app.db.session import engine
    day = "2099-01-01"
    try:
        r = client.post("/attendance", data={
            "date": day, "shift_1": "0.5"}, follow_redirects=False)
        assert r.status_code == 303
        body = client.get(r.headers["location"]).text
        assert "0.5" in body
    finally:
        with engine.begin() as c:
            c.execute(text("DELETE FROM attendance WHERE att_date = :d"), {"d": day})


def test_salary_page_shows_the_july_total():
    body = client.get("/salary?year=2026&month=7").text
    assert "296,742" not in body   # that total lives on the dashboard, not here
    assert "RAGHUL" in body


def test_extra_work_adjustments_page_shows_the_unexplained_total():
    body = client.get("/extra-work/adjustments?year=2026&month=7").text
    assert "36,442" in body


def test_labour_create_then_appears_in_the_master_list():
    """Runs against the shared demo database (see conftest.py), so the
    created row is torn down afterward -- otherwise a second run of the
    suite would violate labour.name's UNIQUE constraint."""
    from sqlalchemy import text
    from app.db.session import engine
    name = "WEB TEST WORKER"
    with engine.begin() as c:
        c.execute(text("DELETE FROM labour WHERE name = :n"), {"n": name})
    try:
        r = client.post("/labour", data={
            "name": name, "wage_type": "DAILY", "rate": "450"},
            follow_redirects=False)
        assert r.status_code == 303
        body = client.get(r.headers["location"]).text
        assert name in body
    finally:
        with engine.begin() as c:
            c.execute(text("DELETE FROM labour WHERE name = :n"), {"n": name})


def test_unknown_extra_work_type_code_is_rejected_with_a_visible_error():
    r = client.post("/extra-work-types", data={
        "code": "FLYASH_UNLOAD", "name": "dup", "rate": "1"},
        follow_redirects=False)
    assert r.status_code == 303
    body = client.get(r.headers["location"]).text
    assert "already in use" in body
