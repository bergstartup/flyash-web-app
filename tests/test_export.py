"""The export diff harness: the only objective correctness oracle we have."""

import pytest

from app.db.session import engine
from app.excel.diff import _rebase, compare_cell, diff_workbooks
from app.excel.workbook import build_month
from app.excel.writers import ALL_SHEETS, WRITERS


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    out = tmp_path_factory.mktemp("export") / "JULY-generated.xlsx"
    with engine.connect() as conn:
        build_month(conn, 2026, 7, out)
    return out


def test_implemented_sheets_match_the_register(generated, workbook):
    results = diff_workbooks(workbook, generated, set(WRITERS))
    failures = [r for r in results if r.implemented and not r.ok]
    assert not failures, "\n".join(
        f"{r.sheet}: " + "; ".join(str(d) for d in r.differences[:5])
        for r in failures)


def test_coverage_is_reported_not_hidden(generated, workbook):
    results = diff_workbooks(workbook, generated, set(WRITERS))
    assert {r.sheet for r in results} == set(ALL_SHEETS)
    assert [r.sheet for r in results if not r.implemented], \
        "unimplemented sheets must be reported, never silently passed"


@pytest.mark.parametrize("a,b,expected", [
    (1.0, 1.005, None),
    (1.0, 1.5, "value"),
    ("=SUM(A1:A3)", "=SUM( A1:A3 )", None),
    ("=SUM(A1:A3)", "=SUM(A1:A4)", "formula"),
    ("  MKS   AUTO ", "MKS AUTO", None),
    (None, None, None),
    (5, None, "missing"),
])
def test_cell_comparison_rules(a, b, expected):
    assert compare_cell(a, b) == expected


@pytest.mark.parametrize("value,row,other,shifted", [
    # the same statement at two different addresses
    ("=B29+C29", 29, "=B30+C30", 30),
    ("=B23-B22", 23, "=B22-B21", 22),
    ("=SUM(C5:C35)", 36, "=SUM(C4:C34)", 35),
])
def test_aligned_rows_compare_whatever_address_they_sit_at(value, row, other,
                                                           shifted):
    assert _rebase(value, row) == _rebase(other, shifted)


@pytest.mark.parametrize("value,row,other,shifted", [
    # the cumulative column pointed at a different anchor: a real difference
    ("=B25-B21", 25, "=B24-B5", 24),
    ("=E13-F13", 14, "=E12-F12", 14),
])
def test_a_reference_somewhere_else_stays_a_difference(value, row, other,
                                                       shifted):
    assert _rebase(value, row) != _rebase(other, shifted)


@pytest.mark.parametrize("value", [452, None, "NIL"])
def test_rebasing_leaves_everything_that_is_not_a_formula_alone(value):
    assert _rebase(value, 5) == value


def test_a_shifted_ledger_still_reports_only_its_real_differences(generated, workbook):
    """The ledger sheets post a different number of rows from the register --
    merged production shifts, and a sale Sheet8 never posted. Every row below
    the first of those shifts, and the harness must not report the shift as
    hundreds of differences."""
    results = {r.sheet: r for r in diff_workbooks(workbook, generated, set(WRITERS))}
    for sheet in ("Sheet5", "Sheet8", "Sheet9"):
        assert results[sheet].mode == "movements"
        assert results[sheet].compared > 0
    assert results["Sheet5"].ok and results["Sheet8"].ok
