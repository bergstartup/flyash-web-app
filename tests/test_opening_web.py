"""HTTP-level tests for the opening figures screen, against the July database.

Nothing here saves: a refused form must leave the database as it found it.
"""

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import engine
from app.main import app

client = TestClient(app)


def test_the_screen_shows_julys_closing_as_augusts_carried_opening():
    body = client.get("/opening?month=2026-08").text
    assert "Opening figures &middot; August 2026" in body
    assert "44,479.00" in body          # FAB closed July on 44,479
    assert "1,107.00" in body           # the digital account closed on 1,107


def test_a_figure_that_is_not_a_number_comes_back_as_a_message():
    with engine.connect() as c:
        before = c.execute(text("SELECT COUNT(*) FROM stock_opening")).scalar()
        product = c.execute(text(
            "SELECT id FROM product WHERE has_stock_ledger = 1 AND is_active = 1 "
            "ORDER BY display_order, name LIMIT 1")).scalar()
    r = client.post("/opening", follow_redirects=False, data={
        "month": "2026-10", f"stock_{product}": "lots"})
    assert r.status_code == 303
    assert "error=" in r.headers["location"]
    with engine.connect() as c:
        after = c.execute(text("SELECT COUNT(*) FROM stock_opening")).scalar()
    assert after == before
