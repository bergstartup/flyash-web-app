"""Sheet4 -- staff advance.

Sparse: a row exists only on days an advance was actually paid. The PENDING row
below the data is the month's unsettled salary per labour, which lives on the
salary settlement, not in the advance register.
"""

from __future__ import annotations

import calendar
import datetime as dt

from app.excel.toolkit import SheetWriter, col


class Sheet04Writer(SheetWriter):
    sheet = "Sheet4"
    first_data_row = 4

    def write(self) -> int:
        y, m = str(self.ctx.year), f"{self.ctx.month:02d}"
        roster = self.ctx.rows("""
            SELECT o.labour_id, COALESCE(o.label, l.name)
            FROM labour_sheet_order o JOIN labour l ON l.id = o.labour_id
            WHERE o.sheet = 'Sheet4' ORDER BY o.position""")
        days = [_as_date(r[0]) for r in self.ctx.rows("""
            SELECT DISTINCT adv_date FROM advance
            WHERE strftime('%Y', adv_date) = :y AND strftime('%m', adv_date) = :m
            ORDER BY adv_date""", y=y, m=m)]
        paid = {
            (r[0], str(r[1])): r[2] for r in self.ctx.rows("""
                SELECT labour_id, adv_date, SUM(amount) FROM advance
                WHERE strftime('%Y', adv_date) = :y
                  AND strftime('%m', adv_date) = :m
                GROUP BY labour_id, adv_date""", y=y, m=m)}
        pending = {
            r[0]: r[1] for r in self.ctx.rows("""
                SELECT labour_id, outstanding FROM salary_month
                WHERE period_year = :yi AND period_month = :mi""",
                yi=self.ctx.year, mi=self.ctx.month)}

        first = self.first_data_row
        last_col = len(roster) + 1
        total_col = last_col + 1
        day_col = total_col + 1
        row = first
        for day in days:
            self.put(row, 1, day)
            for i, (labour_id, _label) in enumerate(roster):
                self.put(row, 2 + i, paid.get((labour_id, day.isoformat()), 0))
            self.formula(row, total_col, f"SUM({col(2)}{row}:{col(last_col)}{row})")
            self.put(row, day_col, calendar.day_name[day.weekday()].upper())
            row += 1

        pending_row = row
        self.put(pending_row, 1, "PENDING")
        for i, (labour_id, _label) in enumerate(roster):
            self.put(pending_row, 2 + i, pending.get(labour_id, 0) or 0)
        self.formula(pending_row, total_col,
                     f"SUM({col(2)}{pending_row}:{col(last_col)}{pending_row})")
        self.put(pending_row, day_col,
                 calendar.month_name[self.ctx.month].upper())

        total_row = pending_row + 3
        self.formula(total_row - 1, total_col,
                     f"SUM({col(total_col)}{first}:{col(total_col)}{pending_row})")

        self.put(total_row, 1, "TOTAL")
        for c in range(2, total_col):
            self.formula(total_row, c, f"SUM({col(c)}{first}:{col(c)}{total_row - 1})")
        self.formula(total_row, total_col,
                     f"SUM({col(2)}{total_row}:{col(last_col)}{total_row})")
        self.put(total_row, day_col, "DAY")

        footer = total_row + 1
        self.put(footer, 1, "NAME")
        for i, (_lid, label) in enumerate(roster):
            self.put(footer, 2 + i, label)
        self.put(footer, total_col, "TOTAL")
        self.put(footer, day_col, "DAY")
        return footer + 1


def _as_date(value) -> dt.date:
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])
