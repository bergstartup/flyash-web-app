"""HTTP-level smoke tests for the Phase 2 screens, against the imported July
database. The business rules are covered by test_stock_services; these check
the screens render the real data and that a blocked write comes back as a
message rather than a traceback.

The two POSTs here are ones the service must refuse, so they leave the July
data untouched -- which is what lets this file run against the real database
without disturbing the export oracle."""

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@pytest.mark.parametrize("path", [
    "/products", "/production", "/production?date=2026-07-15",
    "/sales", "/sales?date=2026-07-02", "/stock",
    "/stock?product_id=1&year=2026&month=7",
    "/materials", "/materials?date=2026-07-06",
    "/consumption", "/consumption?date=2026-07-02",
])
def test_pages_render(path):
    r = client.get(path)
    assert r.status_code == 200
    assert "SMCP Kallakudi" in r.text


def test_the_sales_screen_shows_the_days_trips():
    body = client.get("/sales?date=2026-07-02").text
    assert "KVK" in body
    assert "FLY ASH BRICKS" in body


def test_the_stock_ledger_shows_julys_derived_summary():
    body = client.get("/stock?product_id=1&year=2026&month=7").text
    for figure in ("64,403", "122,900", "142,824", "44,479"):
        assert figure in body, f"{figure} missing from the ledger summary"


def test_the_sales_screen_carries_what_is_on_hand():
    body = client.get("/sales?date=2026-07-31").text
    assert "on hand" in body


def test_a_sale_over_the_available_stock_comes_back_as_a_message():
    r = client.post("/sales", follow_redirects=False, data={
        "date": "2026-07-31", "movement_type": "SALE", "payment_mode": "CASH",
        "product_id_1": "1", "qty_1": "9999999", "rate_1": "6"})
    assert r.status_code == 303
    assert "error=" in r.headers["location"]
    assert "on+hand" in r.headers["location"] or "on%20hand" in r.headers["location"]


def test_a_credit_sale_without_a_customer_comes_back_as_a_message():
    r = client.post("/sales", follow_redirects=False, data={
        "date": "2026-07-31", "movement_type": "SALE", "payment_mode": "CREDIT",
        "product_id_1": "1", "qty_1": "10", "rate_1": "6"})
    assert r.status_code == 303
    assert "error=" in r.headers["location"]


def test_the_products_screen_lists_the_three_ledger_products():
    body = client.get("/products").text
    for sheet in ("Sheet5", "Sheet8", "Sheet9"):
        assert sheet in body


def test_the_materials_screen_names_its_materials():
    body = client.get("/materials?date=2026-07-06").text
    assert "SHRI VARI BLUE METAL" in body
    assert "POWDER" in body


def test_the_consumption_screen_lists_the_cement_lots():
    body = client.get("/consumption?date=2026-07-02").text
    assert "M / N" in body


def test_drawing_more_than_a_lot_holds_comes_back_as_a_message():
    r = client.post("/consumption", follow_redirects=False, data={
        "date": "2026-07-02", "material_id": "8", "lot_1": "99999"})
    assert r.status_code == 303
    assert "error=" in r.headers["location"]


def test_the_material_master_lists_materials_and_suppliers():
    body = client.get("/raw-materials").text
    assert "RAMCO CEMENT" in body and "Default rate" in body
    assert 'id="suppliers"' in body


def test_the_delivery_form_offers_the_masters_rate():
    body = client.get("/materials").text
    assert 'data-rate=' in body and 'id="rate"' in body
