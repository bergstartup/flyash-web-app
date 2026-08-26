"""The export diff harness: the only objective correctness oracle we have."""

import pytest

from app.db.session import engine
from app.excel.diff import compare_cell, diff_workbooks
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
