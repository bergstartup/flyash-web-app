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
    body = client.get("/").text
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
