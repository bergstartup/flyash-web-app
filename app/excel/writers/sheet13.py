"""Sheet13 -- customer profile. Values only, no formulas."""

from __future__ import annotations

import datetime as dt

from app.excel.toolkit import SheetWriter


class Sheet13Writer(SheetWriter):
    sheet = "Sheet13"
    first_data_row = 5

    def write(self) -> int:
        rows = self.ctx.rows("""
            SELECT added_on, name, village, contact, customer_type
            FROM customer WHERE added_on IS NOT NULL
            ORDER BY added_on, id""")
        row, previous = self.first_data_row, None
        for added_on, name, village, contact, ctype in rows:
            day = dt.date.fromisoformat(str(added_on)[:10])
            self.date_or_ditto(row, 1, day, previous)
            previous = day
            self.put(row, 2, name)
            self.put(row, 3, village)
            self.put(row, 4, contact)
            self.put(row, 5, ctype)
            row += 1
        return row
