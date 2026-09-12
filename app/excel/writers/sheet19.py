"""Sheet19 -- the digital account ledger.

Two independent lists side by side: money in on the left, money out on the
right. They are not rows of one table -- the fourth receipt and the fourth
payment share a line only because that is where the pen was.

Much of this posts itself. A labour advance paid digitally already writes its
own OUT entry, which is the Sheet4 / Sheet15 / Sheet19 triple-keying the app
exists to remove.
"""

from __future__ import annotations

import datetime as dt

from app.excel.toolkit import SheetWriter, col


class Sheet19Writer(SheetWriter):
    sheet = "Sheet19"
    first_data_row = 5
    footer_row = 39

    in_date_col = 1
    in_desc_col = 2
    in_amount_col = 3
    out_date_col = 4
    out_desc_col = 5
    out_amount_col = 6

    def write(self) -> int:
        y, m = str(self.ctx.year), f"{self.ctx.month:02d}"
        incoming = self._side("IN", y, m)
        outgoing = self._side("OUT", y, m)

        first = self.first_data_row
        self._column(incoming, first, self.in_date_col, self.in_desc_col,
                     self.in_amount_col)
        self._column(outgoing, first, self.out_date_col, self.out_desc_col,
                     self.out_amount_col)

        last_data = first + max(len(incoming), len(outgoing)) - 1
        footer = max(self.footer_row, last_data + 2)
        self.put(footer, self.in_desc_col, "IN COME  AMT  /  RS")
        self.formula(footer, self.in_amount_col,
                     f"SUM({col(self.in_amount_col)}{first}:"
                     f"{col(self.in_amount_col)}{footer - 1})")
        self.put(footer, self.out_desc_col, "EXPENSE  AMT  /  RS")
        self.formula(footer, self.out_amount_col,
                     f"SUM({col(self.out_amount_col)}{first}:"
                     f"{col(self.out_amount_col)}{footer - 1})")
        self.put(footer, 7, "BALANCE")
        self.formula(footer, 8,
                     f"{col(self.in_amount_col)}{footer}"
                     f"-{col(self.out_amount_col)}{footer}")
        self.put(footer, 9, "AMT / RS")
        self.put(footer, 10, "ONLY")
        return footer + 1

    def _side(self, direction: str, y: str, m: str) -> list:
        """One side of the ledger, opening entry first.

        C14: the June balance is an OPENING entry rather than income, so it
        does not inflate the month's takings -- but the register still writes
        it at the top of the income column, and so does the app.
        """
        return self.ctx.rows("""
            SELECT txn_date, description, amount FROM digital_ledger
            WHERE direction = :direction
              AND strftime('%Y', txn_date) = :y AND strftime('%m', txn_date) = :m
            ORDER BY CASE WHEN entry_type = 'OPENING' THEN 0 ELSE 1 END,
                     txn_date, id""", direction=direction, y=y, m=m)

    def _column(self, entries, first: int, date_col: int, desc_col: int,
                amount_col: int) -> None:
        previous: dt.date | None = None
        for offset, (when, description, amount) in enumerate(entries):
            row = first + offset
            day = _as_date(when)
            self.date_or_ditto(row, date_col, day, previous)
            previous = day
            self.put(row, desc_col, description)
            self.put(row, amount_col, amount)


def _as_date(value) -> dt.date:
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])
