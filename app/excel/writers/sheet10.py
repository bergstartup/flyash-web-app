"""Sheet10 -- raw materials inward.

Wide: one column per material, so a receipt fills one of seven columns and
writes ``---`` in the other six. The app keeps materials as rows and the
column each one occupies is recorded on the material itself, so adding an
eighth material is a row here rather than a new column in a spreadsheet.
"""

from __future__ import annotations

import datetime as dt

from app.excel.toolkit import NIL, NOT_APPLICABLE, SheetWriter, col


class Sheet10Writer(SheetWriter):
    sheet = "Sheet10"
    first_data_row = 6
    #: where the register puts its totals row
    footer_row = 27

    date_col = 1
    supplier_col = 2
    bill_col = 3
    vehicle_col = 4
    rate_col = 12
    amount_col = 13
    driver_col = 14
    status_col = 15

    def write(self) -> int:
        y, m = str(self.ctx.year), f"{self.ctx.month:02d}"
        materials = self.ctx.rows("""
            SELECT id, sheet10_column FROM raw_material
            WHERE sheet10_column IS NOT NULL ORDER BY display_order""")
        columns = {mid: _column_index(letter) for mid, letter in materials}
        rows = self.ctx.rows("""
            SELECT r.receipt_date, s.name, r.bill_no, r.vehicle_no,
                   r.material_id, r.qty, r.rate, r.amount, r.amount_overridden,
                   r.driver_name, r.payment_status
            FROM material_receipt r
            LEFT JOIN supplier s ON s.id = r.supplier_id
            WHERE strftime('%Y', r.receipt_date) = :y
              AND strftime('%m', r.receipt_date) = :m
            ORDER BY r.receipt_date, r.id""", y=y, m=m)

        first = self.first_data_row
        row = first
        previous: dt.date | None = None
        for (day, supplier, bill, vehicle, material_id, qty, rate, amount,
             overridden, driver, status) in rows:
            day = _as_date(day)
            self.date_or_ditto(row, self.date_col, day, previous)
            previous = day
            self.put(row, self.supplier_col, supplier)
            # The register never leaves these blank -- it writes NIL, which
            # the importer reads as "nothing" and stores as NULL.
            self.put(row, self.bill_col, bill or NIL)
            self.put(row, self.vehicle_col, vehicle or NIL)
            for other_id, column in columns.items():
                self.put(row, column,
                         qty if other_id == material_id else NOT_APPLICABLE)
            self.put(row, self.rate_col, rate)
            if overridden:
                self.put(row, self.amount_col, amount)
            else:
                self.formula(row, self.amount_col,
                             f"{col(columns[material_id])}{row}"
                             f"*{col(self.rate_col)}{row}")
            self.put(row, self.driver_col, driver or NIL)
            self.put(row, self.status_col, status)
            row += 1

        last_data = row - 1
        footer = max(self.footer_row, last_data + 2)
        for column in columns.values():
            self.formula(footer, column,
                         f"SUM({col(column)}{first}:{col(column)}{footer - 1})")
        self.put(footer, self.rate_col, "RATE")
        self.formula(footer, self.amount_col,
                     f"SUM({col(self.amount_col)}{first}:"
                     f"{col(self.amount_col)}{footer - 1})")
        self.put(footer, self.driver_col, "NAME")
        self.put(footer, self.status_col, "STATUS")

        header = footer + 1
        self._repeat_header(header, columns)
        return header + 2

    def _repeat_header(self, row: int, columns: dict[int, int]) -> None:
        """The register restates its two header rows below the totals."""
        labels = self.ctx.rows("""
            SELECT sheet10_column, name, unit FROM raw_material
            WHERE sheet10_column IS NOT NULL ORDER BY display_order""")
        for c, value in ((self.date_col, "ON"), (self.supplier_col, "DESCRIPTION")):
            self.put(row, c, value)
        for letter, name, _unit in labels:
            self.put(row, _column_index(letter), _sheet10_label(name))
        self.put(row, self.rate_col, "LIT/BAG")
        self.put(row, self.amount_col, "AMT")
        self.put(row, self.driver_col, "DRIVER")

        second = row + 1
        for c, value in ((self.date_col, "DATE"), (self.supplier_col, "FROM"),
                         (self.bill_col, "BILL  NO"),
                         (self.vehicle_col, "VEHICLE  --  NO")):
            self.put(second, c, value)
        for letter, _name, unit in labels:
            self.put(second, _column_index(letter), unit)
        self.put(second, self.rate_col, "RATE")
        self.put(second, self.amount_col, "RS")
        self.put(second, self.driver_col, "NAME")
        self.put(second, self.status_col, "STATUS")


def _sheet10_label(name: str) -> str:
    """The register heads each column with the material's short name; the
    supplier's brand sits on the row above, which the template already holds."""
    return {"RAMCO CEMENT": "CEMENT", "VITALA NEO CHEMICAL": "CHEMICAL",
            "FAP MIX CEMENT": "CEMENT", "POWDER": " POWDER"}.get(name, name)


def _column_index(letter: str) -> int:
    return ord(letter.upper()) - ord("A") + 1


def _as_date(value) -> dt.date:
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])
