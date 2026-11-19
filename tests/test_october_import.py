"""The October 2026 register, which is laid out differently from July's.

The digital ledger moved from Sheet19 to Sheet24 and the pallet register from
Sheet20 to Sheet19 (with a per-hand stocking report); Sheet14 lists fewer
hands, so its rate moved a column left; Sheet15 dropped STATUS; Sheet5's
footer is spelt DISCRIPTION; names and products are written new ways. Every
figure below is the one the October workbook itself totals.
"""

import datetime as dt
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from app.config import BASE_DIR, SOURCE_WORKBOOK
from app.db.models import Base
from app.db.session import apply_views
from app.importer.july import import_july
from app.importer.names import resolve
from app.services import month_import

OCTOBER_FILE = BASE_DIR / "OCTOBER.xlsx"
OCTOBER = dt.date(2026, 10, 1)

pytestmark = pytest.mark.skipif(not Path(OCTOBER_FILE).exists(),
                                reason="OCTOBER.xlsx is not in the project")


@pytest.fixture(scope="module")
def imported(tmp_path_factory):
    path = tmp_path_factory.mktemp("oct") / "oct.db"
    engine = create_engine(f"sqlite+pysqlite:///{path}")

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with engine.begin() as c:
        apply_views(c)
    with sessionmaker(bind=engine)() as s:
        import_july(s, SOURCE_WORKBOOK)
        result = month_import.run(s, "OFFICE", OCTOBER_FILE, OCTOBER,
                                  commit=True)
        yield s, result
    engine.dispose()


def _one(db, sql, **p):
    return db.execute(text(sql), p).first()


def test_the_october_file_is_read_as_october():
    assert month_import.read_month(OCTOBER_FILE) == OCTOBER


@pytest.mark.parametrize("product, figures", [
    ("FLY ASH BRICKS", (101454, 120800, 92572, 129682)),
    ('4" SOLID', (1438, 3386, 2276, 2548)),
    # Sheet6 sells it as 6" -- SOLID; it is still the Sheet9 ledger.
    ('6"*12" -- SOLID', (3484, 0, 1068, 2416)),
])
def test_stock_matches_the_ledger_sheets(imported, product, figures):
    db, _ = imported
    row = _one(db, """
        SELECT s.opening, s.production, s.sales, s.closing
        FROM stock_month_summary s JOIN product p ON p.id = s.product_id
        WHERE p.name = :n AND s.period_start = :p""",
        n=product, p=OCTOBER.isoformat())
    assert tuple(row) == figures


def test_sales_total_matches_sheet6(imported):
    db, _ = imported
    total = _one(db, """SELECT SUM(amount) FROM sales_register
                        WHERE sale_date >= '2026-10-01'""")[0]
    assert total == 701702


def test_a_reordered_product_name_is_the_same_product(imported):
    db, result = imported
    created = {n for kind, n in result.created if kind == "product"}
    assert '6" -- SOLID' not in created
    assert '5"*5"*10\' K-PO' not in created          # July's 10'*5"*5" K -- PO


def test_attendance_matches_sheet2_including_iyyappan(imported):
    db, _ = imported
    duty = dict(db.execute(text("""
        SELECT l.name, SUM(a.shift_units) FROM attendance a
        JOIN labour l ON l.id = a.labour_id
        WHERE a.att_date >= '2026-10-01' GROUP BY l.name""")).all())
    assert duty["IYAPPAN"] == 24.5                  # IYYAPPAN on Sheet2
    assert duty["RAGHUL"] == 26
    assert duty["PAULRAJ.T"] == 27.5                 # PAUL RAJ . T on Sheet2
    assert sum(duty.values()) == 321


def test_unloading_rate_is_read_from_its_new_column(imported):
    db, _ = imported
    rate = _one(db, """
        SELECT r.rate FROM extra_work_rate r
        JOIN extra_work_type t ON t.id = r.work_type_id
        WHERE t.code = 'FLYASH_UNLOAD' ORDER BY r.effective_from DESC""")[0]
    assert rate == 100


def test_salary_status_is_not_read_from_the_pending_column(imported):
    db, _ = imported
    statuses = {r[0] for r in db.execute(text(
        "SELECT status FROM salary_settlement WHERE period_month = 10"))}
    assert statuses == {None}


def test_digital_ledger_is_read_from_sheet24(imported):
    db, _ = imported
    row = _one(db, """SELECT income, expense FROM digital_ledger_month
                      WHERE period_start = '2026-10-01'""")
    assert tuple(row) == (47575, 38650)


def test_pallets_are_read_from_sheet19(imported):
    db, _ = imported
    row = _one(db, """
        SELECT today_prodn, production_area, stocking_area, pallets_stocked,
               shifted_from_stocking
        FROM pallet_movement WHERE move_date = '2026-10-02'""")
    assert tuple(row) == (75, 46, 9, 40, 15)


def test_the_second_21_october_power_reading_is_reported(imported):
    db, result = imported
    assert any("21 Oct 2026" in w and "row 28" in w for w in result.warnings)
    assert _one(db, """SELECT kwh FROM power_reading
                       WHERE reading_date = '2026-10-21'""")[0] == 65761.1


def test_a_doubled_letter_still_names_the_same_person():
    hands = {"IYAPPAN": 1, "NEERAJ": 2, "NEERAJP": 3}
    assert resolve("IYYAPPAN", hands) == 1
    assert resolve("NEERAJ . P", hands) == 3
