"""HTTP-level smoke tests for the Phase 5 screens, against the July database."""

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@pytest.mark.parametrize("path", [
    "/power", "/power?date=2026-07-15&year=2026&month=7",
    "/ledger", "/ledger?year=2026&month=7",
])
def test_pages_render(path):
    r = client.get(path)
    assert r.status_code == 200
    assert "SMCP Kallakudi" in r.text


def test_the_power_page_shows_the_derived_movement():
    body = client.get("/power?year=2026&month=7").text
    assert "58,236.4" in body            # 1 July's KWH reading
    assert "0.9" in body                 # a power factor, derived


def test_the_power_page_flags_the_day_the_meter_went_unread():
    body = client.get("/power?year=2026&month=7").text
    # 30 July's movement spans two days, because 29 July reads LEAVE / SIR
    assert 'class="num bad">2<' in body


def test_the_ledger_shows_julys_derived_balance():
    body = client.get("/ledger?year=2026&month=7").text
    for figure in ("14,273", "1,107"):
        assert figure in body


def test_a_meter_reading_below_the_last_comes_back_as_a_message():
    r = client.post("/power", follow_redirects=False, data={
        "date": "2026-08-15", "kwh": "1", "kvah": "1"})
    assert r.status_code == 303
    assert "error=" in r.headers["location"]
