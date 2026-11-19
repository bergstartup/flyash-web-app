"""Resetting a month to its Excel register.

Each test builds its own database -- July imported from JULY.xlsx -- because
these imports replace whole months. The August file is JULY.xlsx with its
July dates moved on a month, which is what the next register looks like.
"""

import datetime as dt
import shutil

import openpyxl
import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from app.config import SOURCE_WORKBOOK
from app.db.models import Base, Labour
from app.db.session import apply_views
from app.importer.july import import_july
from app.importer.workbook import LayoutError
from app.services import month_import
from app.services import sale as sale_svc
from app.services.period import PeriodLocked, close

JULY = dt.date(2026, 7, 1)
AUGUST = dt.date(2026, 8, 1)


@pytest.fixture(scope="module")
def august_file(tmp_path_factory):
    """JULY.xlsx as the August register: every July date a month on, the lot
    headings left alone, and one new hand on the salary sheet."""
    wb = openpyxl.load_workbook(SOURCE_WORKBOOK, data_only=True)
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                v = cell.value
                if ws.title == "Sheet12" and cell.row == 2:
                    continue
                if isinstance(v, dt.datetime) and (v.year, v.month) == (2026, 7):
                    cell.value = v.replace(month=8)
    wb["Sheet15"]["B20"] = "MUTHUVEL"
    wb["Sheet15"]["C20"] = 450
    path = tmp_path_factory.mktemp("aug") / "AUGUST.xlsx"
    wb.save(path)
    return path


@pytest.fixture(scope="module")
def july_db(tmp_path_factory):
    """A database with July imported, built once and copied per test."""
    path = tmp_path_factory.mktemp("july") / "july.db"
    engine = _engine(path)
    Base.metadata.create_all(engine)
    with engine.begin() as c:
        apply_views(c)
    with sessionmaker(bind=engine)() as s:
        import_july(s, SOURCE_WORKBOOK)
    engine.dispose()
    return path


@pytest.fixture()
def db(july_db, tmp_path):
    path = tmp_path / "t.db"
    shutil.copy(july_db, path)
    engine = _engine(path)
    with sessionmaker(bind=engine)() as s:
        yield s
    engine.dispose()


def _engine(path):
    engine = create_engine(f"sqlite+pysqlite:///{path}")

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")
    return engine


def _figures(db, month: dt.date) -> dict:
    """The month's headline figures, as the dashboard and workbook read them."""
    p = {"y": f"{month:%Y}", "m": f"{month:%m}", "yi": month.year,
         "mi": month.month, "period": month.isoformat()}
    return dict(db.execute(text("""
        SELECT
          (SELECT ROUND(SUM(amount), 2) FROM sales_register
            WHERE strftime('%Y', sale_date) = :y
              AND strftime('%m', sale_date) = :m)           AS sales,
          (SELECT ROUND(SUM(total_payment), 2) FROM salary_month
            WHERE period_year = :yi AND period_month = :mi) AS salary,
          (SELECT ROUND(SUM(amount), 2) FROM material_receipt
            WHERE strftime('%Y', receipt_date) = :y
              AND strftime('%m', receipt_date) = :m)        AS materials,
          (SELECT closing FROM digital_ledger_month
            WHERE period_start = :period)                   AS ledger,
          (SELECT GROUP_CONCAT(closing, ',') FROM (
             SELECT closing FROM stock_month_summary
              WHERE period_start = :period
              ORDER BY product_id))                         AS stock
    """), p).mappings().first())


def _rows(db, table: str, col: str, month: dt.date) -> int:
    nxt = (month.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return db.execute(text(
        f"SELECT COUNT(*) FROM {table} WHERE {col} >= :s AND {col} < :e"),
        {"s": month.isoformat(), "e": nxt.isoformat()}).scalar()


def test_reimporting_july_over_itself_changes_no_figure(db):
    before = _figures(db, JULY)
    result = month_import.run(db, "OFFICE", SOURCE_WORKBOOK, JULY, commit=True)
    assert _figures(db, JULY) == before
    assert all(line.before == line.after for line in result.lines)
    assert result.created == []
    assert result.warnings == []


def test_a_check_changes_nothing(db):
    sale_svc.create(db, "OFFICE", sale_date=dt.date(2026, 7, 31),
                    payment_mode="CASH", lines=[{
                        "product_id": db.execute(text(
                            "SELECT id FROM product WHERE name = 'FLY ASH BRICKS'"
                        )).scalar(), "qty": 10, "rate": 6}])
    sales = _rows(db, "sale", "sale_date", JULY)
    result = month_import.run(db, "OFFICE", SOURCE_WORKBOOK, JULY, commit=False)
    line = next(x for x in result.lines if x.label == "Sales and damages")
    assert (line.before, line.after) == (sales, sales - 1)
    assert _rows(db, "sale", "sale_date", JULY) == sales


def test_what_was_entered_in_the_app_for_the_month_is_replaced(db):
    fab = db.execute(text(
        "SELECT id FROM product WHERE name = 'FLY ASH BRICKS'")).scalar()
    sale_svc.create(db, "OFFICE", sale_date=dt.date(2026, 7, 31),
                    payment_mode="CASH",
                    lines=[{"product_id": fab, "qty": 10, "rate": 6}])
    before = _figures(db, JULY)
    month_import.run(db, "OFFICE", SOURCE_WORKBOOK, JULY, commit=True)
    after = _figures(db, JULY)
    assert after["sales"] == round(before["sales"] - 60, 2)


def test_august_comes_in_without_touching_july(db, august_file):
    july = _figures(db, JULY)
    july_sales = _rows(db, "sale", "sale_date", JULY)

    result = month_import.run(db, "OFFICE", august_file, AUGUST, commit=True)

    assert _figures(db, JULY) == july
    assert _rows(db, "sale", "sale_date", JULY) == july_sales
    assert _rows(db, "sale", "sale_date", AUGUST) == july_sales
    assert _rows(db, "attendance", "att_date", AUGUST) == \
        _rows(db, "attendance", "att_date", JULY)
    aug = _figures(db, AUGUST)
    assert aug["sales"] == july["sales"]
    assert aug["salary"] == july["salary"]
    assert ("labour", "MUTHUVEL") in result.created
    assert db.query(Labour).filter_by(name="MUTHUVEL").one().rate == 450
    assert db.execute(text("""
        SELECT COUNT(*) FROM salary_month
        WHERE labour_name = 'MUTHUVEL' AND period_year = 2026
          AND period_month = 8""")).scalar() == 1


def test_august_opens_on_the_files_figures_and_says_where_they_differ(
        db, august_file):
    result = month_import.run(db, "OFFICE", august_file, AUGUST, commit=True)
    fab = db.execute(text("""
        SELECT s.opening FROM stock_month_summary s
        JOIN product p ON p.id = s.product_id
        WHERE p.name = 'FLY ASH BRICKS' AND s.period_start = '2026-08-01'
    """)).scalar()
    assert fab == 64403                            # the file's, not July's 44,479
    fab_warning = next(w for w in result.warnings
                       if w.startswith("FLY ASH BRICKS"))
    assert "64,403.00" in fab_warning and "44,479.00" in fab_warning


def test_importing_august_twice_gives_the_same_august(db, august_file):
    month_import.run(db, "OFFICE", august_file, AUGUST, commit=True)
    first = _figures(db, AUGUST)
    result = month_import.run(db, "OFFICE", august_file, AUGUST, commit=True)
    assert _figures(db, AUGUST) == first
    assert all(line.before == line.after for line in result.lines)
    assert result.created == []


def test_a_file_for_another_month_is_refused(db):
    with pytest.raises(ValueError, match="for July 2026, not August 2026"):
        month_import.run(db, "OFFICE", SOURCE_WORKBOOK, AUGUST, commit=False)


def test_a_closed_month_is_refused_until_reopened(db):
    close(db, "OFFICE", 2026, 7)
    with pytest.raises(PeriodLocked):
        month_import.run(db, "OFFICE", SOURCE_WORKBOOK, JULY, commit=True)


def test_a_workbook_that_is_not_the_register_is_refused(db, tmp_path):
    wb = openpyxl.Workbook()
    wb.active.title = "Sheet1"
    path = tmp_path / "other.xlsx"
    wb.save(path)
    with pytest.raises(LayoutError, match="not the monthly register"):
        month_import.run(db, "OFFICE", path, JULY, commit=False)


def test_a_file_that_is_not_excel_is_refused(db, tmp_path):
    path = tmp_path / "notes.xlsx"
    path.write_text("not a workbook")
    with pytest.raises(LayoutError, match="could not be opened"):
        month_import.run(db, "OFFICE", path, JULY, commit=False)


def test_a_backup_is_a_working_copy(db, tmp_path):
    snapshot = month_import.backup(db, tmp_path / "backups", JULY)
    copy = create_engine(f"sqlite+pysqlite:///{snapshot}")
    with copy.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM sale")).scalar() == \
            db.execute(text("SELECT COUNT(*) FROM sale")).scalar()
    copy.dispose()
