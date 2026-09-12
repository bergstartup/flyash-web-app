"""Sheet1 -- power consumption.

Two meter readings a day, and everything else on the sheet is the arithmetic
between them: the daily movement, the movement since the EB anchor, and the
power factor of each.

Three things the register does that the arithmetic does not: it opens with an
anchor row carrying readings and no deltas, it leaves a day with no reading as
text, and the day after such a gap reaches back past it -- so a delta is
always against the last row that actually holds a reading.
"""

from __future__ import annotations

import datetime as dt

from app.excel.toolkit import SheetWriter, col


class Sheet01Writer(SheetWriter):
    sheet = "Sheet1"
    first_data_row = 5

    date_col = 1
    kwh_col = 2
    kvah_col = 3
    daily_kwh_col = 4
    daily_kvah_col = 5
    daily_pf_col = 6
    cum_kwh_col = 7
    cum_kvah_col = 8
    cum_pf_col = 9
    pf_move_col = 10

    def write(self) -> int:
        readings = self.ctx.rows("""
            SELECT reading_date, kwh, kvah, is_eb_anchor, reading_taken, remarks
            FROM power_reading ORDER BY reading_date, id""")

        row = self.first_data_row
        anchor_row: int | None = None
        previous_row: int | None = None      # last row holding a reading
        cumulative_rows: list[int] = []
        for when, kwh, kvah, is_anchor, taken, remarks in readings:
            self.put(row, self.date_col, _as_date(when))
            if is_anchor:
                # The anchor states where the meter stood; nothing is measured
                # from it on its own row.
                self.put(row, self.kwh_col, kwh)
                self.put(row, self.kvah_col, kvah)
                anchor_row = row
                previous_row = row
                row += 1
                continue
            if not taken:
                # 29 Jul reads LEAVE / SIR. The reading was never taken, so
                # there is nothing to subtract and the next day spans two.
                left, _, right = (remarks or "").partition("/")
                self.put(row, self.kwh_col, left.strip() or None)
                self.put(row, self.kvah_col, right.strip() or None)
                row += 1
                continue
            self.put(row, self.kwh_col, kwh)
            self.put(row, self.kvah_col, kvah)
            if previous_row is not None:
                self._delta(row, previous_row, self.kwh_col, self.daily_kwh_col)
                self._delta(row, previous_row, self.kvah_col, self.daily_kvah_col)
                self._ratio(row, self.daily_kwh_col, self.daily_kvah_col,
                            self.daily_pf_col)
            # The cumulative block starts one row below the daily one: the
            # first reading after an anchor has nothing to compare against yet.
            if anchor_row is not None and cumulative_rows:
                self._delta(row, anchor_row, self.kwh_col, self.cum_kwh_col)
                self._delta(row, anchor_row, self.kvah_col, self.cum_kvah_col)
                self._ratio(row, self.cum_kwh_col, self.cum_kvah_col,
                            self.cum_pf_col)
                if len(cumulative_rows) > 1:
                    self.formula(row, self.pf_move_col,
                                 f"{col(self.cum_pf_col)}{row}"
                                 f"-{col(self.cum_pf_col)}{cumulative_rows[-1]}")
                cumulative_rows.append(row)
            elif anchor_row is not None:
                cumulative_rows.append(row)
            previous_row = row
            row += 1
        return row

    def _delta(self, row: int, against: int, source: int, target: int) -> None:
        self.formula(row, target,
                     f"{col(source)}{row}-{col(source)}{against}")

    def _ratio(self, row: int, numerator: int, denominator: int,
               target: int) -> None:
        self.formula(row, target,
                     f"{col(numerator)}{row}/{col(denominator)}{row}")


def _as_date(value) -> dt.date:
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])
