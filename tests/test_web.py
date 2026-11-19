"""The web skeleton and the actor seam."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.web.actor import COOKIE

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


@pytest.mark.parametrize("path", ["/", "/reconciliation", "/anomalies"])
def test_pages_render(path):
    r = client.get(path)
    assert r.status_code == 200
    assert "SMCP Kallakudi" in r.text


def test_dashboard_shows_derived_totals():
    # Pinned to July: the dashboard follows the latest month with data, so an
    # entry keyed for any later month would otherwise empty this page.
    body = client.get("/?year=2026&month=7").text
    assert "1,069,943" in body        # sales, derived from sale_line
    assert "296,742" in body          # wages, derived from attendance


def test_actor_defaults_and_persists():
    fresh = TestClient(app)
    assert "M.DIRECTOR" in fresh.get("/").text
    fresh.post("/actor", data={"name": "IYAPPAN"},
               headers={"referer": "/"}, follow_redirects=False)
    assert fresh.cookies.get(COOKIE) == "IYAPPAN"


def test_unknown_actor_falls_back_rather_than_failing():
    fresh = TestClient(app)
    r = fresh.post("/actor", data={"name": "NOBODY"},
                   headers={"referer": "/"}, follow_redirects=False)
    assert r.status_code == 303
    assert fresh.cookies.get(COOKIE) == "M.DIRECTOR"


def test_the_dashboard_offers_the_months_workbook():
    body = client.get("/?year=2026&month=7").text
    assert 'action="/export"' in body and 'value="2026-07"' in body


def test_the_workbook_downloads_with_the_registers_sheets():
    import io

    import openpyxl
    r = client.get("/export?month=2026-07")
    assert r.status_code == 200
    assert 'filename="JULY-2026.xlsx"' in r.headers["content-disposition"]
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames[0] == "Sheet1" and "Sheet20" in wb.sheetnames
    assert wb["Sheet5"].max_row > 100, "the brick ledger is filled, not a template"
