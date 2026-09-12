"""Sheet15 -- salary summary. Every money column is derived.

Only the settlement is entered: the date it was paid, who authorised it, and
what was left pending. Duty comes from the muster, advance from the advance
register, extra from the extra-work register.
"""

from __future__ import annotations

import calendar
import datetime as dt

from app.excel.toolkit import NIL, SheetWriter, col


class Sheet15Writer(SheetWriter):
    sheet = "Sheet15"
    first_data_row = 4
    #: blank row the register leaves between the data and the footer
    footer_offset = 2

    def write(self) -> int:
        roster = self.ctx.rows("""
            SELECT o.labour_id, COALESCE(o.label, l.name)
            FROM labour_sheet_order o JOIN labour l ON l.id = o.labour_id
            WHERE o.sheet = 'Sheet15' ORDER BY o.position""")
        salary = {
            r[0]: r for r in self.ctx.rows("""
                SELECT labour_id, wage_type, muster_wage, duty, salary, advance,
                       extra, paid_on, authority, status, outstanding
                FROM salary_month
                WHERE period_year = :y AND period_month = :m""",
                y=self.ctx.year, m=self.ctx.month)}

        first = self.first_data_row
        row = first
        for seq, (labour_id, label) in enumerate(roster, start=1):
            s = salary.get(labour_id)
            # The register's S.No is hand-typed and repeats 7; the app numbers
            # the roster it actually lays out.
            self.put(row, 1, seq)
            self.put(row, 2, label)
            if s is None:
                row += 1
                continue
            daily = s[1] == "DAILY"
            self.put(row, 3, s[2] if daily else "FIXER")
            self.put(row, 4, s[3])
            # A daily wage is rate x duty on the face of the sheet; a fixed one
            # has nothing to multiply, so the figure itself is written.
            if daily:
                self.formula(row, 5, f"{col(3)}{row}*{col(4)}{row}")
            else:
                self.put(row, 5, s[4])
            self.put(row, 6, s[5])
            self.formula(row, 7, f"{col(5)}{row}-{col(6)}{row}")
            self.put(row, 8, s[6])
            self.formula(row, 9, f"{col(5)}{row}+{col(8)}{row}")
            self.put(row, 10, _as_date(s[7]) if s[7] else None)
            self.put(row, 11, s[8])
            self.put(row, 12, s[9])
            self.put(row, 13, s[10] if s[10] else NIL)
            # Nobody in the register carries a balance past the month's close.
            self.put(row, 14, NIL)
            row += 1

        last_data = row - 1
        footer = last_data + self.footer_offset
        for c, value in enumerate(
                ["S-NO", "NAME", "WAGE'S", "DUTY", "SMCP",
                 calendar.month_name[self.ctx.month].upper(), "TOTAL",
                 "PAYMENT"], start=1):
            self.put(footer, c, value)
        self.formula(footer, 9, f"SUM({col(9)}{first}:{col(9)}{last_data})")
        self.put(footer, 10, "DATE")
        self.put(footer, 11, "AUTHORITY")
        self.put(footer, 12, "STATUS")
        self.formula(footer, 13, f"SUM({col(13)}{first}:{col(13)}{last_data})")
        self.put(footer, 14, "OUT STANDING")
        return footer + 1


def _as_date(value) -> dt.date:
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])
