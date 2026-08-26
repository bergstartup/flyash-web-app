"""Sheet2 -- staff attendance.

One row per date, one column per labour in that sheet's own order, TOTAL in the
last column as a live SUM. The register leaves two blank rows after the data,
carries a floating per-day total, then the TOTAL row and a repeated header, so
the footer sits at a fixed offset from the last data row.
"""

from __future__ import annotations

import datetime as dt

from app.excel.toolkit import SheetWriter, col


class Sheet02Writer(SheetWriter):
    sheet = "Sheet2"
    first_data_row = 4
    #: rows from the last data row to the TOTAL row, as the register lays it out
    footer_offset = 4

    def write(self) -> int:
        roster = self.ctx.rows("""
            SELECT o.position, l.id, COALESCE(o.label, l.name) AS label
            FROM labour_sheet_order o JOIN labour l ON l.id = o.labour_id
            WHERE o.sheet = 'Sheet2' ORDER BY o.position""")
        days = [r[0] for r in self.ctx.rows("""
            SELECT DISTINCT att_date FROM attendance
            WHERE strftime('%Y', att_date) = :y AND strftime('%m', att_date) = :m
            ORDER BY att_date""",
            y=str(self.ctx.year), m=f"{self.ctx.month:02d}")]
        shifts = {
            (r[0], str(r[1])): r[2]
            for r in self.ctx.rows("""
                SELECT labour_id, att_date, shift_units FROM attendance
                WHERE strftime('%Y', att_date) = :y
                  AND strftime('%m', att_date) = :m""",
                y=str(self.ctx.year), m=f"{self.ctx.month:02d}")
        }

        first = self.first_data_row
        last_labour_col = len(roster) + 1
        total_col = len(roster) + 2
        row = first
        for day in days:
            self.put(row, 1, _as_date(day))
            for i, (_pos, labour_id, _label) in enumerate(roster):
                self.put(row, 2 + i, shifts.get((labour_id, str(day))))
            self.formula(row, total_col,
                         f"SUM({col(2)}{row}:{col(last_labour_col)}{row})")
            row += 1

        last_data = row - 1
        total_row = last_data + self.footer_offset
        # The floating per-day total the register carries just above TOTAL.
        self.formula(total_row - 1, total_col,
                     f"SUM({col(total_col)}{first}:{col(total_col)}{total_row - 2})")

        self.put(total_row, 1, "TOTAL")
        for i in range(len(roster)):
            c = 2 + i
            # One consistent range per column. The register hand-adjusts the
            # start row for two columns; the app does not reproduce that.
            self.formula(total_row, c,
                         f"SUM({col(c)}{first}:{col(c)}{total_row - 1})")
        self.formula(total_row, total_col,
                     f"SUM({col(2)}{total_row}:{col(last_labour_col)}{total_row})")

        footer = total_row + 1
        self.put(footer, 1, "NAME")
        for i, (_pos, _lid, label) in enumerate(roster):
            self.put(footer, 2 + i, label)
        self.put(footer, total_col, "TOTAL")
        return footer + 1


def _as_date(value):
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])
