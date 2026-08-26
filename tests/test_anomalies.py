"""The error register. Each defect must be rediscovered from the raw workbook.

These double as regression tests: if a future change lets the app *produce* one
of these states, the corresponding derivation has stopped working.
"""

import pytest
from sqlalchemy import text

from app.importer.anomalies import CHECKS, detect

#: code -> the difference the register carries, from the build plan
EXPECTED_DEFECTS = {
    "A01": 100.0,       # Sheet9 closing overstated by 100 units
    "A02": 450.0,       # a 4" sale never posted to the stock ledger
    "A03": 31.0,        # cement lot split exceeds closing stock
    "A04": 1.0,         # FAP cement: 42 bags vs 43
    "A05": 400.0,       # MANJULA regular wage
    "A06": -600.0,      # MANJULA advance
    "A07": 12000.0,     # IYAPPAN advance with an empty column
    "A08": 323633.0,    # spread across four receivable registers
    "A11": -0.5,        # hand-typed 374 beside the computed 374.5
}


@pytest.fixture(scope="module")
def found(workbook):
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        return detect(db, workbook)


#: the eleven defects the build plan's error register names
REGISTER_CODES = {"A01", "A02", "A03", "A04", "A05", "A06", "A07",
                  "A08", "A09", "A10", "A11"}


def test_the_whole_register_is_rediscovered(found):
    """All eleven named defects, found from the raw workbook alone."""
    assert REGISTER_CODES <= {a.code for a in found}


def test_each_defect_is_reported_once(found):
    """A check that fires twice for one defect is noise, not thoroughness."""
    from collections import Counter
    counts = Counter(a.code for a in found if a.code in REGISTER_CODES)
    assert all(n == 1 for n in counts.values()), counts


def test_checks_are_registered(found):
    assert len(CHECKS) >= 11


@pytest.mark.parametrize("code,difference", sorted(EXPECTED_DEFECTS.items()))
def test_defect_is_found_with_the_right_size(found, code, difference):
    hits = [a for a in found if a.code == code]
    assert hits, f"{code} was not rediscovered"
    sizes = [float(str(a.difference).replace(",", "")) for a in hits]
    assert any(abs(s - difference) < 0.51 for s in sizes), \
        f"{code}: expected a difference near {difference}, got {sizes}"


def test_text_in_the_meter_column_is_found(found):
    assert [a for a in found if a.code == "A09"]


def test_off_by_one_formula_is_found(found):
    hits = [a for a in found if a.code == "A10"]
    assert hits and "I36" in hits[0].workbook_value


def test_the_app_cannot_reproduce_a01(conn):
    """Sheet9 closes at 2,287 in the app, not the 2,387 the register prints."""
    closing = conn.execute(text("""
        SELECT closing FROM stock_month_summary s JOIN product p ON p.id = s.product_id
        WHERE p.ledger_sheet = 'Sheet9'""")).scalar()
    assert closing == pytest.approx(2287.0)


def test_the_app_cannot_reproduce_a11(conn):
    """Total shifts derive to 374.5; the register's typed 374 is unreachable."""
    total = conn.execute(text("SELECT SUM(shift_units) FROM attendance")).scalar()
    assert total == pytest.approx(374.5)


def test_customers_sharing_a_name_are_kept_apart(conn):
    """People called ANAND in different villages must stay separate rows.

    Merging them on the name alone would put one debtor's balance on another.
    """
    rows = conn.execute(text(
        "SELECT village, contact FROM customer WHERE UPPER(name) = 'ANAND'")).all()
    assert len(rows) >= 2
    assert len({r[1] for r in rows}) == len(rows), \
        f"customers named ANAND must have distinct contacts, got {rows}"


def test_no_two_customers_share_a_name_village_and_contact(conn):
    """Identity is name + village + contact; genuine duplicates collapse."""
    dupes = conn.execute(text("""
        SELECT UPPER(name), UPPER(COALESCE(village,'')), COALESCE(contact,''),
               COUNT(*) AS n
        FROM customer GROUP BY 1, 2, 3 HAVING n > 1""")).all()
    assert not dupes, dupes


def test_credit_sales_without_a_customer_are_excluded_from_the_ledger(conn):
    """They are reported as A14, not silently charged to nobody."""
    orphaned = conn.execute(text("""
        SELECT COUNT(*) FROM receivable_entry
        WHERE kind = 'SALE' AND customer_id IS NULL""")).scalar()
    assert orphaned == 0
