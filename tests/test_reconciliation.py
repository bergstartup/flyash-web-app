"""The July 2026 correctness oracle, one test per figure."""

import pytest

from app.reports.reconciliation import EXPECTED, measure


def test_every_expected_figure_is_reproduced(conn):
    failures = [m for m in measure(conn) if not m.matches]
    assert not failures, "\n".join(
        f"{m.label}: expected {m.expected:,.2f}, derived {m.actual:,.2f}"
        for m in failures)


@pytest.mark.parametrize("label", list(EXPECTED))
def test_figure(conn, label):
    m = next(x for x in measure(conn) if x.label == label)
    assert m.actual == pytest.approx(m.expected, abs=0.01)


def test_fab_stock_invariant_holds(conn):
    """opening + production - sales = closing, as a hard invariant."""
    from sqlalchemy import text
    row = conn.execute(text("""
        SELECT opening, production, sales, closing FROM stock_month_summary s
        JOIN product p ON p.id = s.product_id WHERE p.ledger_sheet = 'Sheet5'
    """)).first()
    opening, production, sales, closing = row
    assert opening + production - sales == pytest.approx(closing, abs=0.01)


def test_stock_ledger_running_balance_matches_the_summary(conn):
    """The last balance in the ledger must equal the month's closing figure."""
    from sqlalchemy import text
    for sheet in ("Sheet5", "Sheet8", "Sheet9"):
        last = conn.execute(text("""
            SELECT balance FROM stock_ledger l JOIN product p ON p.id = l.product_id
            WHERE p.ledger_sheet = :s
            ORDER BY movement_date DESC, kind DESC, seq DESC, line_no DESC
            LIMIT 1"""), {"s": sheet}).scalar()
        closing = conn.execute(text("""
            SELECT closing FROM stock_month_summary s
            JOIN product p ON p.id = s.product_id WHERE p.ledger_sheet = :s"""),
            {"s": sheet}).scalar()
        assert last == pytest.approx(closing, abs=0.01), sheet
