"""Sheet6 -- the sales register. One row per product sold, not per trip.

A trip carrying two products dittos both the date and the S.No to add a line,
which is why a sale is a header with lines rather than a flat row. The amount
is a formula off qty and rate except where the register overrode it, and five
July rows did.

The margin block in columns M to Q is not reproduced. It is a jotting of
digital collections into PAULRAJ.T's account, and every one of its thirty
amounts is already a row in the digital ledger, where the app keeps them.
"""

from __future__ import annotations

import datetime as dt

from app.excel.toolkit import DITTO, SheetWriter, col


class Sheet06Writer(SheetWriter):
    sheet = "Sheet6"
    first_data_row = 5
    footer_row = 213

    date_col = 1
    seq_col = 2
    product_col = 3
    qty_col = 4
    rate_col = 5
    amount_col = 6
    perday_col = 7
    vehicle_col = 8
    consumer_col = 9
    cash_col = 10
    credit_col = 11
    digital_col = 12

    def write(self) -> int:
        y, m = str(self.ctx.year), f"{self.ctx.month:02d}"
        rows = self.ctx.rows("""
            SELECT s.sale_date, s.seq_no, l.line_no, p.name, l.qty, l.rate,
                   l.amount, l.amount_overridden, v.name, s.destination,
                   s.payment_mode
            FROM sale s
            JOIN sale_line l ON l.sale_id = s.id
            JOIN product p ON p.id = l.product_id
            LEFT JOIN vehicle v ON v.id = s.vehicle_id
            WHERE s.movement_type = 'SALE'
              AND strftime('%Y', s.sale_date) = :y
              AND strftime('%m', s.sale_date) = :m
            ORDER BY s.sale_date, s.seq_no, l.line_no""", y=y, m=m)

        # The day's takings sit on the day's last row and nowhere else.
        per_day: dict[str, float] = {}
        last_of_day: dict[str, int] = {}
        for i, r in enumerate(rows):
            day = str(r[0])
            per_day[day] = per_day.get(day, 0.0) + (r[6] or 0.0)
            last_of_day[day] = i

        first = self.first_data_row
        row = first
        previous: dt.date | None = None
        for i, (sold, seq, line_no, product, qty, rate, amount, overridden,
                vehicle, destination, mode) in enumerate(rows):
            day = _as_date(sold)
            self.date_or_ditto(row, self.date_col, day, previous)
            previous = day
            self.put(row, self.seq_col, seq if line_no == 1 else DITTO)
            self.put(row, self.product_col, product)
            self.put(row, self.qty_col, qty)
            self.put(row, self.rate_col, rate)
            if overridden or rate is None:
                # C8: five July rows carry an amount that is not qty x rate.
                self.put(row, self.amount_col, amount)
            else:
                self.formula(row, self.amount_col,
                             f"{col(self.qty_col)}{row}*{col(self.rate_col)}{row}")
            if last_of_day[str(sold)] == i:
                self.put(row, self.perday_col, per_day[str(sold)])
            self.put(row, self.vehicle_col, vehicle)
            self.put(row, self.consumer_col, destination)
            if mode == "CASH":
                self.put(row, self.cash_col, "CASH")
            elif mode == "CREDIT":
                self.put(row, self.credit_col, "CREDIT")
            row += 1

        last_data = row - 1
        footer = max(self.footer_row, last_data + 2)
        for c, value in enumerate(
                ["DATE", "S.NO", "TOTAL", _month(self.ctx.month), "AMT", "RS"],
                start=1):
            self.put(footer, c, value)
        self.formula(footer, self.perday_col,
                     f"SUM({col(self.perday_col)}{first}:"
                     f"{col(self.perday_col)}{footer - 1})")
        self.put(footer, self.vehicle_col, "CONSUMER")
        self.put(footer, self.consumer_col, "SATISFACTION")
        self.put(footer, self.cash_col, "CASH")
        self.put(footer, self.credit_col, "CREDIT")
        self.put(footer, self.digital_col, "DIGITAL  A/C")
        return footer + 1


def _month(month: int) -> str:
    import calendar
    return calendar.month_name[month].upper()


def _as_date(value) -> dt.date:
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])
