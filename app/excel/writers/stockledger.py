"""Shared shape for the three inventory ledgers -- Sheets 5, 8 and 9.

All three are the same register: one row per stock movement, opening carried
in from the row above, TOTAL and BALANCE as live formulas. They differ only in
which columns those land in, whether the sheet numbers the day's sales, and
whether it carries a per-day sales total.

Opening is written as a value and not as ``=E5-F5``. That is the register's own
convention -- 177 of Sheet5's 181 opening cells are typed -- and it is honest
here for a different reason: the number comes from the ledger view, so it is
derived even though Excel is not the thing deriving it.
"""

from __future__ import annotations

import datetime as dt

from app.excel.toolkit import DITTO, NIL, SheetWriter, col


class StockLedgerWriter(SheetWriter):
    first_data_row = 5
    #: where the register puts its DESCRIPTION row. The sheets reserve more
    #: space than July needs (Sheet9 leaves seven blank rows), so the footer
    #: sits at that fixed row unless a bigger month pushes past it.
    footer_row = 0

    date_col = 1
    seq_col: int | None = None
    opening_col = 2
    prodn_col = 3
    total_col = 4
    sales_col = 5
    balance_col = 6
    vehicle_col = 7
    consumer_col = 8
    perday_col: int | None = None

    def write(self) -> int:
        period = self.ctx.period_start.isoformat()
        rows = self.ctx.rows("""
            SELECT l.movement_date, l.kind, l.seq, l.movement_type,
                   l.opening, l.produced, l.issued, v.name, l.destination
            FROM stock_ledger l
            JOIN product p ON p.id = l.product_id
            LEFT JOIN vehicle v ON v.id = l.vehicle_id
            WHERE p.ledger_sheet = :sheet AND l.period_start = :period
            ORDER BY l.movement_date, l.seq, l.kind, l.line_no""",
            sheet=self.sheet, period=period)

        # Where the month opens with stock but nothing moves on the first, the
        # register still states where it started -- Sheet8 does, Sheet9 has
        # nothing to state, and Sheet5's first sale carries the opening itself.
        opening_row = self._opening_row(rows)

        # The per-day sales total sits on the day's last row, whichever kind of
        # movement that turns out to be.
        per_day: dict[str, float] = {}
        last_of_day: dict[str, int] = {}
        for i, r in enumerate(rows):
            day = str(r[0])
            per_day[day] = per_day.get(day, 0.0) + (r[6] or 0.0)
            last_of_day[day] = i

        first = self.first_data_row
        row = first
        previous: dt.date | None = None
        if opening_row is not None:
            self.put(row, self.date_col, self.ctx.period_start)
            previous = self.ctx.period_start
            self.put(row, self.opening_col, opening_row)
            self.put(row, self.prodn_col, 0)
            self.formula(row, self.total_col,
                         f"{col(self.opening_col)}{row}+{col(self.prodn_col)}{row}")
            self.put(row, self.sales_col, 0)
            self.formula(row, self.balance_col,
                         f"{col(self.total_col)}{row}-{col(self.sales_col)}{row}")
            self.put(row, self.vehicle_col, NIL)
            self.put(row, self.consumer_col, NIL)
            if self.perday_col:
                self.put(row, self.perday_col, 0)
            row += 1
        day_seq = 0
        for i, (moved, kind, seq, movement, opening, produced, issued,
                vehicle, destination) in enumerate(rows):
            day = _as_date(moved)
            if day != previous:
                day_seq = 0
            # A production row carries the date only when it opens the day.
            # Mid-day it carries nothing at all -- not even the ditto mark a
            # continuing sale row gets.
            if kind == 0 or day != previous:
                self.date_or_ditto(row, self.date_col, day, previous)
            previous = day
            if kind == 0:
                day_seq += 1
                if self.seq_col:
                    # The sheet numbers its own rows for this product; a sale's
                    # seq_no counts every product sold that day, so it is not
                    # the number that belongs here.
                    self.put(row, self.seq_col, day_seq)
            self.put(row, self.opening_col, opening)
            self.put(row, self.prodn_col, produced)
            self.formula(row, self.total_col,
                         f"{col(self.opening_col)}{row}+{col(self.prodn_col)}{row}")
            self.put(row, self.sales_col, issued)
            self.formula(row, self.balance_col,
                         f"{col(self.total_col)}{row}-{col(self.sales_col)}{row}")
            self.put(row, self.vehicle_col, _vehicle(kind, movement, vehicle))
            self.put(row, self.consumer_col,
                     destination if kind == 0 and destination else NIL)
            if self.perday_col:
                self.put(row, self.perday_col,
                         per_day[str(moved)] if last_of_day[str(moved)] == i else 0)
            row += 1

        last_data = row - 1
        # The register leaves blank rows between the data and DESCRIPTION; a
        # month with more movements than it reserved keeps at least one.
        footer = max(self.footer_row, last_data + 2)
        self.clear_from(last_data + 1, self.consumer_col, footer - 1)

        self.put(footer, 1, "DESCRIPTION")
        if self.seq_col:
            self.put(footer, self.seq_col, "S:NO")
        self.put(footer, self.opening_col,
                 opening_row if opening_row is not None else _month_opening(rows))
        self.formula(footer, self.prodn_col,
                     f"SUM({col(self.prodn_col)}{first}:"
                     f"{col(self.prodn_col)}{footer - 1})")
        self.formula(footer, self.total_col,
                     f"{col(self.opening_col)}{footer}+{col(self.prodn_col)}{footer}")
        self.formula(footer, self.sales_col,
                     f"SUM({col(self.sales_col)}{first}:"
                     f"{col(self.sales_col)}{footer - 1})")
        self.formula(footer, self.balance_col,
                     f"{col(self.total_col)}{footer}-{col(self.sales_col)}{footer}")
        self.put(footer, self.vehicle_col, "CONSUMER")
        self.put(footer, self.consumer_col, "SATISFACTION")
        if self.perday_col:
            self.formula(footer, self.perday_col,
                         f"SUM({col(self.perday_col)}{first}:"
                         f"{col(self.perday_col)}{footer - 1})")
        return footer + 1


    def _opening_row(self, rows) -> float | None:
        """The month's opening, when it deserves a row of its own."""
        if not rows:
            return None
        opening = rows[0][4]
        if not opening:
            return None
        if _as_date(rows[0][0]) == self.ctx.period_start:
            return None                     # the first movement states it
        return opening


def _vehicle(kind: int, movement: str, vehicle: str | None) -> str:
    """What the register writes in the VEHICLE column.

    Production has no vehicle, and a damage is not carried by one either -- the
    register names the movement instead, which is how ``DAMAGE / SIR`` came to
    look like a sale.
    """
    if kind != 0:
        return NIL
    if movement != "SALE":
        return movement
    return vehicle or NIL


def _month_opening(rows) -> float:
    return rows[0][4] if rows else 0.0


def _as_date(value) -> dt.date:
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])
