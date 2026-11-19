"""HTTP-level tests for the month import screen, against the July database.

Nothing here confirms an import: checking a file must leave the database as
it found it.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import SOURCE_WORKBOOK
from app.db.session import engine
from app.main import app
from app.web.routers import month_import as month_router

client = TestClient(app)
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture(autouse=True)
def uploads(tmp_path, monkeypatch):
    """Keep the uploads these tests make out of data/imports."""
    monkeypatch.setattr(month_router, "IMPORT_DIR", tmp_path)


def _sales() -> int:
    with engine.connect() as c:
        return c.execute(text("SELECT COUNT(*) FROM sale")).scalar()


def test_the_screen_renders():
    body = client.get("/import?month=2026-07").text
    assert "Import a month from Excel" in body
    assert 'value="2026-07"' in body


def test_checking_julys_file_shows_what_would_change_and_changes_nothing():
    before = _sales()
    with open(SOURCE_WORKBOOK, "rb") as f:
        r = client.post("/import", data={"month": "2026-07"},
                        files={"file": ("JULY.xlsx", f, XLSX)})
    assert r.status_code == 200
    assert "will replace July 2026" in r.text
    assert "Replace July 2026 with this file" in r.text
    assert _sales() == before


def test_a_file_for_another_month_comes_back_as_a_message():
    with open(SOURCE_WORKBOOK, "rb") as f:
        r = client.post("/import", data={"month": "2026-08"},
                        files={"file": ("JULY.xlsx", f, XLSX)},
                        follow_redirects=False)
    assert r.status_code == 303
    assert "error=" in r.headers["location"]
    assert "July%202026" in r.headers["location"]


def test_a_file_that_is_not_xlsx_is_refused():
    r = client.post("/import", data={"month": "2026-07"},
                    files={"file": ("july.csv", b"a,b", "text/csv")},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "xlsx" in r.headers["location"]


def test_confirming_an_upload_that_does_not_exist_is_refused():
    before = _sales()
    r = client.post("/import/confirm", data={"token": "../../etc/passwd"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "error=" in r.headers["location"]
    assert _sales() == before
