"""Sheet20 -- pallets moving between the stocking area and the production area.

Two counts a day and two balances. The balances are counted rather than
calculated -- 28 July has four pallets in production and three in stocking
against four made -- so they are recorded like the rest.
"""

from __future__ import annotations

import datetime as dt

from app.excel.toolkit import SheetWriter, col


class Sheet20Writer(SheetWriter):
    sheet = "Sheet20"
    first_data_row = 6
    #: where the register restates its three header rows
    footer_row = 40

    date_col = 1
    stocking_col = 2
    production_col = 3
    today_col = 4
    total_col = 5
    stocked_col = 6
    shifted_col = 7
    prod_balance_col = 8
    stock_balance_col = 9
    pallets_col = 10

    def write(self) -> int:
        y, m = str(self.ctx.year), f"{self.ctx.month:02d}"
        rows = self.ctx.rows("""
            SELECT move_date, stocking_area, production_area, today_prodn,
                   pallets_stocked, shifted_from_stocking,
                   production_balance, stocking_balance
            FROM pallet_movement
            WHERE strftime('%Y', move_date) = :y AND strftime('%m', move_date) = :m
            ORDER BY move_date""", y=y, m=m)

        row = self.first_data_row
        for (day, stocking, production, today, stocked, shifted,
             prod_balance, stock_balance) in rows:
            self.put(row, self.date_col, _as_date(day))
            self.put(row, self.stocking_col, stocking or 0)
            self.put(row, self.production_col, production or 0)
            self.put(row, self.today_col, today or 0)
            self.formula(row, self.total_col,
                         f"{col(self.stocking_col)}{row}"
                         f"+{col(self.production_col)}{row}"
                         f"+{col(self.today_col)}{row}")
            self.put(row, self.stocked_col, stocked or 0)
            self.put(row, self.shifted_col, shifted or 0)
            self.put(row, self.prod_balance_col, prod_balance or 0)
            self.put(row, self.stock_balance_col, stock_balance or 0)
            self.formula(row, self.pallets_col,
                         f"{col(self.prod_balance_col)}{row}"
                         f"+{col(self.stock_balance_col)}{row}")
            row += 1

        footer = max(self.footer_row, row + 1)
        for offset, labels in enumerate(_HEADINGS):
            for column, value in labels.items():
                self.put(footer + offset, column, value)
        return footer + len(_HEADINGS)


#: The three header rows the register restates below the data, column by column.
_HEADINGS = (
    {2: "STOCKING", 3: "PRODN", 4: "TODAY", 5: "PALLETS",
     6: "PRODUCTION  AERA", 7: "FROM  STOCKING  AREA", 8: "PRODUCTION  AERA",
     9: "STOCKING  AREA", 10: "TOTAL"},
    {2: "AREA", 3: "AREA", 4: "PRODN", 5: "TOTAL", 6: "STOCKING PALLETS",
     7: "SHIFFTING  & STOCKING", 8: "BALANCE", 9: "BALANCE", 10: "PALLETS"},
    {1: "DATE", 2: "QTY", 3: "QTY", 4: "QTY", 5: "QTY", 6: "QTY", 7: "QTY",
     8: "QTY", 9: "QTY", 10: "QTY"},
)


def _as_date(value) -> dt.date:
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])
