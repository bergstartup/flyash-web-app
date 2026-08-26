"""Generate a month's workbook from the database."""

from __future__ import annotations

from pathlib import Path

import openpyxl

from app.config import EXCEL_TEMPLATE
from app.excel.toolkit import MonthContext
from app.excel.writers import ALL_SHEETS, WRITERS


def build_month(conn, year: int, month: int, destination: Path,
                template: Path | None = None) -> dict[str, str]:
    wb = openpyxl.load_workbook(str(template or EXCEL_TEMPLATE))
    ctx = MonthContext(conn, year, month)
    status: dict[str, str] = {}
    for sheet in ALL_SHEETS:
        writer_cls = WRITERS.get(sheet)
        if writer_cls is None:
            status[sheet] = "NOT IMPLEMENTED"
            continue
        rows = writer_cls(wb[sheet], ctx).write()
        status[sheet] = f"written to row {rows - 1}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(destination))
    return status
