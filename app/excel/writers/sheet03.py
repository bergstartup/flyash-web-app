"""Sheet3 -- staff per-day wages. Nothing on this sheet is ever entered.

Two columns per labour, REGULAR and EXTRA, in Sheet3's own order and under its
own spellings. Regular is attendance x the labour's rate; extra is the day's
extra-work register. A labour who had not joined yet gets ``/`` rather than
``0`` -- the register's way of saying "no such row", which is not the same as
"absent" and must not be summed as zero.
"""

from __future__ import annotations

import datetime as dt

from app.excel.toolkit import NOT_JOINED, SheetWriter, col


class Sheet03Writer(SheetWriter):
    sheet = "Sheet3"
    first_data_row = 10
    #: rows from the last data row to the TOTAL row, as the register lays it out
    footer_offset = 4

    def write(self) -> int:
        y, m = str(self.ctx.year), f"{self.ctx.month:02d}"
        roster = self.ctx.rows("""
            SELECT o.labour_id, COALESCE(o.label, l.name), l.rate, l.join_date
            FROM labour_sheet_order o JOIN labour l ON l.id = o.labour_id
            WHERE o.sheet = 'Sheet3' ORDER BY o.position""")
        # Row 3 carries each hand's mobile number. It comes from the labour
        # record, not the template, so the template holds no one's number.
        phones = dict(self.ctx.rows("""
            SELECT o.position, l.phone FROM labour_sheet_order o
            JOIN labour l ON l.id = o.labour_id WHERE o.sheet = 'Sheet3'"""))
        for i, pos in enumerate(sorted(phones)):
            phone = phones[pos]
            self.ws.cell(3, 2 + 2 * i).value = (
                int(phone) if phone and phone.isdigit() else phone)
        days = [_as_date(r[0]) for r in self.ctx.rows("""
            SELECT DISTINCT att_date FROM attendance
            WHERE strftime('%Y', att_date) = :y AND strftime('%m', att_date) = :m
            ORDER BY att_date""", y=y, m=m)]
        regular = {
            (r[0], str(r[1])): r[2] for r in self.ctx.rows("""
                SELECT labour_id, att_date, shift_units FROM attendance
                WHERE strftime('%Y', att_date) = :y
                  AND strftime('%m', att_date) = :m""", y=y, m=m)}
        extra = {
            (r[0], str(r[1])): r[2] for r in self.ctx.rows("""
                SELECT labour_id, work_date, extra_amount FROM extra_work_daily
                WHERE strftime('%Y', work_date) = :y
                  AND strftime('%m', work_date) = :m""", y=y, m=m)}

        first = self.first_data_row
        last_col = 1 + 2 * len(roster)          # last EXTRA column
        total_col = last_col + 1
        row = first
        for day in days:
            self.put(row, 1, day)
            for i, (labour_id, _label, rate, join_date) in enumerate(roster):
                joined = not (join_date and day < _as_date(join_date))
                shifts = regular.get((labour_id, day.isoformat()))
                amount = extra.get((labour_id, day.isoformat()))
                self.put(row, 2 + 2 * i,
                         (shifts or 0.0) * rate if joined else NOT_JOINED)
                # An unjoined labour can still have extra work booked against
                # them -- Sheet3 S11 carries 100 for VIKRAM the day before he
                # first shows up in the muster.
                self.put(row, 3 + 2 * i,
                         amount if amount is not None
                         else (0 if joined else NOT_JOINED))
            self.formula(row, total_col, f"SUM({col(2)}{row}:{col(last_col)}{row})")
            row += 1

        last_data = row - 1
        total_row = last_data + self.footer_offset
        # The floating column total the register carries just above TOTAL.
        self.formula(total_row - 1, total_col,
                     f"SUM({col(total_col)}{first}:{col(total_col)}{total_row - 2})")

        self.put(total_row, 1, "TOTAL")
        for c in range(2, total_col):
            # One consistent range per column; the register hand-adjusts the
            # start row of the four columns whose labour joined late.
            self.formula(total_row, c, f"SUM({col(c)}{first}:{col(c)}{total_row - 1})")
        self.formula(total_row, total_col,
                     f"SUM({col(2)}{total_row}:{col(last_col)}{total_row})")

        grand = total_row + 1
        self.put(grand, 1, "GRANT TOTAL")
        for i in range(len(roster)):
            reg, ext = 2 + 2 * i, 3 + 2 * i
            self.formula(grand, reg, f"{col(reg)}{total_row}+{col(ext)}{total_row}")
        self.formula(grand, total_col,
                     f"SUM({col(2)}{grand}:{col(last_col)}{grand})")

        footer = grand + 1
        self.put(footer, 1, "      NAME")
        for i, (_lid, label, _rate, _join) in enumerate(roster):
            self.put(footer, 2 + 2 * i, label)
        self.put(footer, total_col, "REGULAR")
        return footer + 1


def _as_date(value) -> dt.date:
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])
