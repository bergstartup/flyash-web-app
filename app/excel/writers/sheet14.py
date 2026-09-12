"""Sheet14 -- fly ash powder unloading, the one extra-work register the
workbook keeps.

Quantities only; the rate lives once in the footer and the money is a formula
off it. Extra-work types other than unloading never appear here -- they are
Sheet3's EXTRA column, which is why the two disagree (see the error register).
"""

from __future__ import annotations

import datetime as dt

from app.excel.toolkit import SheetWriter, col

BULKER = "BULKER"


class Sheet14Writer(SheetWriter):
    sheet = "Sheet14"
    first_data_row = 5
    #: blank rows the register leaves between the data and the totals row
    footer_offset = 3

    def write(self) -> int:
        y, m = str(self.ctx.year), f"{self.ctx.month:02d}"
        roster = self.ctx.rows("""
            SELECT o.labour_id, COALESCE(o.label, l.name)
            FROM labour_sheet_order o JOIN labour l ON l.id = o.labour_id
            WHERE o.sheet = 'Sheet14' ORDER BY o.position""")
        booked = {
            (r[0], str(r[1])): r[2] for r in self.ctx.rows("""
                SELECT e.labour_id, e.work_date, SUM(e.qty) FROM extra_work e
                JOIN extra_work_type t ON t.id = e.work_type_id
                WHERE t.is_adjustment = 0
                  AND strftime('%Y', e.work_date) = :y
                  AND strftime('%m', e.work_date) = :m
                GROUP BY e.labour_id, e.work_date""", y=y, m=m)}
        days = sorted({_as_date(d) for _lid, d in booked})
        rate = self.ctx.rows("""
            SELECT r.rate FROM extra_work_rate r
            JOIN extra_work_type t ON t.id = r.work_type_id
            WHERE t.is_adjustment = 0 AND r.effective_from <= :until
            ORDER BY r.effective_from DESC LIMIT 1""",
            until=_month_end(self.ctx.year, self.ctx.month).isoformat())
        rate = rate[0][0] if rate else None

        first = self.first_data_row
        last_col = len(roster) + 2             # last labour column
        total_col = last_col + 1               # PER DAY
        rate_col = total_col + 1
        amount_col = rate_col + 1
        row = first
        for day in days:
            self.put(row, 1, day)
            self.put(row, 2, BULKER)
            for i, (labour_id, _label) in enumerate(roster):
                self.put(row, 3 + i, booked.get((labour_id, day.isoformat()), 0))
            # One consistent range; the register's first four rows stop at I.
            self.formula(row, total_col, f"SUM({col(3)}{row}:{col(last_col)}{row})")
            row += 1

        last_data = row - 1
        total_row = last_data + self.footer_offset
        self.put(total_row, 1, "DATE")
        self.put(total_row, 2, BULKER)
        for c in range(3, total_col):
            self.formula(total_row, c, f"SUM({col(c)}{first}:{col(c)}{total_row - 1})")
        self.formula(total_row, total_col,
                     f"SUM({col(3)}{total_row}:{col(last_col)}{total_row})")
        self.put(total_row, rate_col, rate)
        self.formula(total_row, amount_col,
                     f"{col(total_col)}{total_row}*{col(rate_col)}{total_row}")

        footer = total_row + 1
        self.put(footer, 1, "NAME")
        self.put(footer, 2, BULKER)
        for i, (_lid, label) in enumerate(roster):
            self.put(footer, 3 + i, label)
        self.put(footer, total_col, "TOTAL QTY")
        self.put(footer, rate_col, "RATE")
        self.put(footer, amount_col, "AMT/RS")
        return footer + 1


def _month_end(year: int, month: int) -> dt.date:
    import calendar
    return dt.date(year, month, calendar.monthrange(year, month)[1])


def _as_date(value) -> dt.date:
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])
