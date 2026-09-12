"""Sheet16 -- the fly ash brick stock roll-up, month by month since Jan 2024.

Pure aggregate: never typed, and never a source of anything. Months the app
owns come from the ledger; the 29 that closed before the database existed come
from imported history. Blocks are financial years, so the running list breaks
at April with a subtotal and a blank row -- except the year in progress, which
the register leaves open.
"""

from __future__ import annotations

import calendar

from app.config import FY_START_MONTH
from app.excel.toolkit import SheetWriter


class Sheet16Writer(SheetWriter):
    sheet = "Sheet16"
    first_data_row = 4

    seq_col = 1
    month_col = 2
    year_col = 3
    opening_col = 4
    production_col = 5
    total_col = 6
    sales_col = 7
    closing_col = 8

    def write(self) -> int:
        months = self.ctx.rows("""
            SELECT h.period_year, h.period_month,
                   h.opening, h.production, h.sales, h.closing
            FROM stock_month_history h JOIN product p ON p.id = h.product_id
            WHERE p.ledger_sheet = 'Sheet5'
            UNION ALL
            SELECT CAST(strftime('%Y', s.period_start) AS INTEGER),
                   CAST(strftime('%m', s.period_start) AS INTEGER),
                   s.opening, s.production, s.sales, s.closing
            FROM stock_month_summary s JOIN product p ON p.id = s.product_id
            WHERE p.ledger_sheet = 'Sheet5'
            ORDER BY 1, 2""")

        blocks = _financial_years(months)
        row = self.first_data_row
        seq = 0
        for i, block in enumerate(blocks):
            for year, month, opening, production, sales, closing in block:
                seq += 1
                # The register's own S.No repeats 8 and so runs one short.
                self.put(row, self.seq_col, seq)
                self.put(row, self.month_col, calendar.month_name[month].upper())
                self.put(row, self.year_col, year)
                self._figures(row, opening, production, sales, closing)
                row += 1
            row = self._subtotal(row, block, closed=i < len(blocks) - 1)

        footer = row + 1
        for c, value in enumerate(
                ["S ; NO", "MONTH", "YEAR", "OPENING STOCK", "PRODUCTION",
                 "TOTAL", "SALES", "CLOSING STOCK"], start=1):
            self.put(footer, c, value)
        return footer + 1

    def _figures(self, row, opening, production, sales, closing) -> None:
        self.put(row, self.opening_col, opening)
        self.put(row, self.production_col, production)
        self.put(row, self.total_col, opening + production)
        self.put(row, self.sales_col, sales)
        self.put(row, self.closing_col, closing)

    def _subtotal(self, row: int, block, closed: bool) -> int:
        """A finished financial year gets a total and a blank row after it.

        The year in progress gets neither -- the register leaves it open.
        """
        if not closed:
            return row
        opening = block[0][2]
        production = sum(b[3] for b in block)
        sales = sum(b[4] for b in block)
        self._figures(row, opening, production, sales, block[-1][5])
        return row + 2


def _financial_years(months) -> list[list]:
    """Split the month list at the financial year boundary."""
    blocks: list[list] = []
    current = None
    for entry in months:
        year, month = entry[0], entry[1]
        fy = year if month >= FY_START_MONTH else year - 1
        if fy != current:
            blocks.append([])
            current = fy
        blocks[-1].append(entry)
    return blocks
