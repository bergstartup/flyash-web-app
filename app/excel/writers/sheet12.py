"""Sheet12 -- the cement stock log, and the by-lot split beside it.

Two registers on one sheet. The left block is a daily stock ledger like
Sheet5's, except cement moves three ways: into production, out as a sale, and
in from two kinds of receipt. The right block is a pivot of the lot balances
-- one column per lot, showing what is left of each at the end of the day.

Nothing in either block is stored. Opening, total and closing come from the
day's movements; the lot columns come from ``lot_balance_daily``.
"""

from __future__ import annotations

import datetime as dt

from app.excel.toolkit import SheetWriter, col

CEMENT = "CEMENT"
RAMCO = "RAMCO CEMENT"
FAP = "FAP MIX CEMENT"


class Sheet12Writer(SheetWriter):
    sheet = "Sheet12"
    first_data_row = 5
    footer_row = 38

    date_col = 1
    opening_col = 2
    ramco_col = 3
    fap_col = 4
    total_col = 5
    consumption_col = 6
    sales_col = 7
    closing_col = 8
    note_col = 9
    #: the by-lot block starts here, one column per lot in display order
    lot_first_col = 10

    def write(self) -> int:
        y, m = str(self.ctx.year), f"{self.ctx.month:02d}"
        period = self.ctx.period_start.isoformat()
        # Every day the cement moved: consumed, received, sold, or shifted
        # between lots. 19 July only moved between lots and still gets a row.
        days = [_as_date(r[0]) for r in self.ctx.rows("""
            SELECT DISTINCT cons_date AS d FROM material_consumption
            WHERE strftime('%Y', cons_date) = :y AND strftime('%m', cons_date) = :m
            UNION
            SELECT DISTINCT receipt_date FROM material_receipt
            WHERE strftime('%Y', receipt_date) = :y
              AND strftime('%m', receipt_date) = :m
            UNION
            SELECT DISTINCT i.as_on FROM material_lot_inflow i
            JOIN material_lot ml ON ml.id = i.lot_id
            JOIN raw_material rm ON rm.id = ml.material_id
            WHERE rm.name = :cement AND strftime('%Y', i.as_on) = :y
              AND strftime('%m', i.as_on) = :m
            UNION
            SELECT DISTINCT s.sale_date FROM sale s
            JOIN sale_line l ON l.sale_id = s.id
            JOIN product p ON p.id = l.product_id
            WHERE UPPER(p.name) = :cement AND strftime('%Y', s.sale_date) = :y
              AND strftime('%m', s.sale_date) = :m
            ORDER BY 1""", y=y, m=m, cement=CEMENT)]
        opening = self.ctx.rows("""
            SELECT COALESCE(o.qty, 0) FROM material_opening o
            JOIN raw_material rm ON rm.id = o.material_id
            WHERE rm.name = :cement AND o.as_on = :period""",
            cement=CEMENT, period=period)
        opening = opening[0][0] if opening else 0.0

        received = self._by_day("""
            SELECT r.receipt_date, SUM(r.qty) FROM material_receipt r
            JOIN raw_material rm ON rm.id = r.material_id
            WHERE rm.name = :name AND strftime('%Y', r.receipt_date) = :y
              AND strftime('%m', r.receipt_date) = :m
            GROUP BY r.receipt_date""", y=y, m=m)
        consumed = self._by_day("""
            SELECT c.cons_date, SUM(c.qty) FROM material_consumption c
            JOIN raw_material rm ON rm.id = c.material_id
            WHERE rm.name = :name AND strftime('%Y', c.cons_date) = :y
              AND strftime('%m', c.cons_date) = :m
            GROUP BY c.cons_date""", y=y, m=m, name=CEMENT)
        sold = self._by_day("""
            SELECT s.sale_date, SUM(l.qty) FROM sale s
            JOIN sale_line l ON l.sale_id = s.id
            JOIN product p ON p.id = l.product_id
            WHERE UPPER(p.name) = :name AND strftime('%Y', s.sale_date) = :y
              AND strftime('%m', s.sale_date) = :m
            GROUP BY s.sale_date""", y=y, m=m, name=CEMENT)
        notes = self._by_day("""
            SELECT c.cons_date, c.remarks FROM material_consumption c
            JOIN raw_material rm ON rm.id = c.material_id
            WHERE rm.name = :name AND c.remarks IS NOT NULL
              AND strftime('%Y', c.cons_date) = :y
              AND strftime('%m', c.cons_date) = :m""", y=y, m=m, name=CEMENT)

        lots = self.ctx.rows("""
            SELECT ml.id, ml.label, ml.lot_date FROM material_lot ml
            JOIN raw_material rm ON rm.id = ml.material_id
            WHERE rm.name = :cement ORDER BY ml.display_order""", cement=CEMENT)
        remaining = {
            (r[0], str(r[1])): r[2] for r in self.ctx.rows("""
                SELECT lot_id, cons_date, qty_remaining FROM lot_balance_daily""")}
        # When each lot first received anything: the register widens the row
        # total as lots land rather than summing columns that do not exist yet.
        arrived = {
            r[0]: _as_date(r[1]) for r in self.ctx.rows("""
                SELECT lot_id, MIN(as_on) FROM material_lot_inflow
                GROUP BY lot_id""")}

        first = self.first_data_row
        row = first
        carried = opening
        for day in days:
            self.put(row, self.date_col, day)
            self.put(row, self.opening_col, carried)
            self.put(row, self.ramco_col, received.get((RAMCO, day), 0))
            self.put(row, self.fap_col, received.get((FAP, day), 0))
            self.formula(row, self.total_col,
                         f"{col(self.opening_col)}{row}+{col(self.ramco_col)}{row}"
                         f"+{col(self.fap_col)}{row}")
            out = consumed.get((CEMENT, day), 0)
            issued = sold.get((CEMENT, day), 0)
            self.put(row, self.consumption_col, out)
            self.put(row, self.sales_col, issued)
            self.formula(row, self.closing_col,
                         f"{col(self.total_col)}{row}-{col(self.consumption_col)}{row}"
                         f"-{col(self.sales_col)}{row}")
            self.put(row, self.note_col, notes.get((CEMENT, day)))
            last_lot = self._lots(row, day, lots, remaining, arrived)
            self.formula(row, self.lot_first_col + len(lots),
                         f"SUM({col(self.lot_first_col)}{row}:{col(last_lot)}{row})")
            carried = (carried + received.get((RAMCO, day), 0)
                       + received.get((FAP, day), 0) - out - issued)
            row += 1

        last_data = row - 1
        footer = max(self.footer_row, last_data + 2)
        self.put(footer, 1, "DESCRIPTION")
        self.put(footer, self.opening_col, opening)
        for c in (self.ramco_col, self.fap_col, self.consumption_col,
                  self.sales_col):
            self.formula(footer, c, f"SUM({col(c)}{first}:{col(c)}{footer - 1})")
        self.formula(footer, self.total_col,
                     f"{col(self.opening_col)}{footer}+{col(self.ramco_col)}{footer}"
                     f"+{col(self.fap_col)}{footer}")
        self.formula(footer, self.closing_col,
                     f"{col(self.total_col)}{footer}-{col(self.consumption_col)}{footer}"
                     f"-{col(self.sales_col)}{footer}")
        self.put(footer, self.note_col, "BAGS")
        for i, (_lot_id, label, lot_date) in enumerate(lots):
            self.put(footer, self.lot_first_col + i, _heading(label, lot_date))
        self.put(footer, self.lot_first_col + len(lots), "TOTAL")
        return footer + 1

    def _lots(self, row: int, day: dt.date, lots, remaining, arrived) -> int:
        """Fill the by-lot columns, and say which is the last one in play.

        A lot that has not received anything yet is written as 0 but left out
        of the row's total, which is how the register grows the block as lots
        land. Two of the buckets are labelled rather than dated, so when a lot
        arrived is a question for its inflows, not for its heading.
        """
        last = self.lot_first_col
        for i, (lot_id, _label, _lot_date) in enumerate(lots):
            column = self.lot_first_col + i
            self.put(row, column, remaining.get((lot_id, day.isoformat()), 0))
            first_inflow = arrived.get(lot_id)
            if first_inflow is not None and first_inflow <= day:
                last = column
        return last

    def _by_day(self, sql: str, **params) -> dict:
        name = params.pop("name", None)
        if name is not None:
            return {(name, _as_date(r[0])): r[1]
                    for r in self.ctx.rows(sql, name=name, **params)}
        out = {}
        for material in (RAMCO, FAP):
            for r in self.ctx.rows(sql, name=material, **params):
                out[(material, _as_date(r[0]))] = r[1]
        return out


def _heading(label: str, lot_date) -> object:
    """The register heads a dated lot with its date and a named one with its
    label."""
    return _as_date(lot_date) if lot_date else label


def _as_date(value) -> dt.date:
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])
