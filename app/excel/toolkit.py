"""Shared machinery for the sheet writers.

The exporter does not re-author the workbook's headers. It fills a template
lifted from JULY.xlsx, which is why the 49 merged ranges in Sheet3's header
block and the 723 bordered cells cost nothing to reproduce.
"""

from __future__ import annotations

import datetime as dt
from copy import copy

from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

DITTO = '"'
NOT_JOINED = "/"
NOT_APPLICABLE = "     ---"
NIL = "NIL"


def a1(row: int, col: int) -> str:
    return f"{get_column_letter(col)}{row}"


def col(idx: int) -> str:
    return get_column_letter(idx)


class SheetWriter:
    """Base class. Subclasses declare where data starts and fill rows.

    ``first_data_row`` is lifted from the source workbook, so a writer never
    has to know how tall the header block is -- only where its own rows begin.
    """

    sheet: str = ""
    first_data_row: int = 1
    #: rows in the template that must be preserved and re-emitted after the
    #: data block (totals, repeated headers)
    footer_rows: tuple[int, ...] = ()

    def __init__(self, ws: Worksheet, ctx: "MonthContext"):
        self.ws = ws
        self.ctx = ctx
        self._style_row = self.first_data_row

    # -- cell helpers -----------------------------------------------------

    def put(self, row: int, column: int, value, style_from: int | None = None):
        cell = self.ws.cell(row, column)
        src = self.ws.cell(style_from or self._style_row, column)
        if src is not cell and src.has_style:
            cell._style = copy(src._style)
        cell.value = value
        return cell

    def formula(self, row: int, column: int, expr: str, style_from=None):
        return self.put(row, column, expr if expr.startswith("=") else "=" + expr,
                        style_from)

    def date_or_ditto(self, row: int, column: int, value: dt.date,
                      previous: dt.date | None):
        """Sheet5, Sheet6 and Sheet10 all repeat a date with a ditto mark."""
        return self.put(row, column, DITTO if value == previous else value)

    def clear_from(self, row: int, last_col: int, until: int) -> None:
        for r in range(row, until + 1):
            for c in range(1, last_col + 1):
                self.ws.cell(r, c).value = None

    # -- contract ---------------------------------------------------------

    def write(self) -> int:
        """Fill the sheet. Returns the row after the last one written."""
        raise NotImplementedError


class MonthContext:
    """Everything a writer needs for one month."""

    def __init__(self, conn, year: int, month: int):
        self.conn = conn
        self.year = year
        self.month = month

    @property
    def period_start(self) -> dt.date:
        return dt.date(self.year, self.month, 1)

    def rows(self, sql: str, **params):
        from sqlalchemy import text
        return self.conn.execute(text(sql), params).all()
