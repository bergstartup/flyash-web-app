"""One-time migration of JULY.xlsx into SQLite.

This is not a general Excel reader. It encodes what the July 2026 register
actually contains, including its conventions:

* ditto marks repeat the date, and on Sheet6 they can also repeat the S.No,
  which turns the next row into a second product line on the same sale;
* ``/`` means the labour had not joined yet, which is distinct from absent;
* text such as LEAVE or NIL appears in numeric columns;
* Sheet3's extra-wage column is typed by hand and is 78.6% larger than the
  only extra-work register that exists, so the balance is booked to a
  MIGRATION ADJUSTMENT work type rather than silently dropped.

Import is idempotent at the database level: :meth:`JulyImporter.run` expects an
empty schema and is normally preceded by ``alembic upgrade head`` on a fresh
file.
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
from dataclasses import dataclass, field

from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from app.db import models as m
from app.importer import source as S
from app.importer.names import norm, resolve

MONTHS = [calendar.month_name[i].upper() for i in range(1, 13)]

FAB = "FLY ASH BRICKS"
SOLID4 = '4" SOLID'
SOLID6 = '6"*12"  --  SOLID'
LEDGER_PRODUCTS = {FAB: "Sheet5", SOLID4: "Sheet8", SOLID6: "Sheet9"}

UNLOAD_CODE = "FLYASH_UNLOAD"
ADJUST_CODE = "MIGRATION_ADJUSTMENT"

CEMENT = "CEMENT"

# Sheet10's wide layout: column letter -> (material name, unit)
SHEET10_MATERIALS = {
    "E": ("POWDER", "UNIT"),
    "F": ("CHIPS", "UNIT"),
    "G": ("RAMCO CEMENT", "BAGS"),
    "H": ("M SAND", "UNIT"),
    "I": ("1/2 JALLY", "UNIT"),
    "J": ("VITALA NEO CHEMICAL", "LTRS"),
    "K": ("FAP MIX CEMENT", "BAGS"),
}


@dataclass
class ImportStats:
    counts: dict[str, int] = field(default_factory=dict)

    def add(self, key: str, n: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + n

    def __str__(self) -> str:
        width = max((len(k) for k in self.counts), default=0)
        return "\n".join(
            f"  {k:<{width}}  {v:>7,}" for k, v in sorted(self.counts.items())
        )


class JulyImporter:
    def __init__(self, session: Session, path):
        self.db = session
        self.path = str(path)
        self.wb = S.load(self.path, values=True)
        self.stats = ImportStats()
        self.labour: dict[str, m.Labour] = {}      # canonical name -> row
        self.products: dict[str, m.Product] = {}   # normalised name -> row
        self.vehicles: dict[str, m.Vehicle] = {}
        self.customers: dict[str, m.Customer] = {}
        self.suppliers: dict[str, m.Supplier] = {}
        self.materials: dict[str, m.RawMaterial] = {}
        self.work_types: dict[str, m.ExtraWorkType] = {}
        self.cement_lots: dict[str, m.MaterialLot] = {}
        self.sale_by_key: dict[tuple[dt.date, int], m.Sale] = {}

    # -- entry point ------------------------------------------------------

    def run(self) -> ImportStats:
        self.import_labour_master()
        self.import_power()
        self.import_attendance()
        self.import_extra_work()
        self.import_advances()
        self.import_salary_settlements()
        self.import_customers()
        self.import_sales()
        self.import_production_and_openings()
        self.import_damages()
        self.import_stock_history()
        self.import_deliveries()
        self.import_materials()
        self.import_cement()
        self.import_receivables()
        self.import_digital_ledger()
        self.import_misc_registers()
        self.db.commit()
        return self.stats

    # -- helpers ----------------------------------------------------------

    def _match_labour(self, raw: str | None) -> m.Labour | None:
        return resolve(raw, self.labour)

    def _product(self, name: str | None) -> m.Product | None:
        if not name:
            return None
        key = norm(name)
        if key in self.products:
            return self.products[key]
        ledger = next(
            (s for p, s in LEDGER_PRODUCTS.items() if norm(p) == key), None
        )
        prod = m.Product(
            code=key[:30] or "UNKNOWN",
            name=name,
            unit="NOS",
            has_stock_ledger=1 if ledger else 0,
            ledger_sheet=ledger,
            display_order=len(self.products),
        )
        self.db.add(prod)
        self.db.flush()
        self.products[key] = prod
        self.stats.add("product")
        return prod

    def _vehicle(self, name: str | None) -> m.Vehicle | None:
        if not name:
            return None
        key = norm(name)
        if key not in self.vehicles:
            v = m.Vehicle(name=name)
            self.db.add(v)
            self.db.flush()
            self.vehicles[key] = v
            self.stats.add("vehicle")
        return self.vehicles[key]

    def _supplier(self, name: str | None) -> m.Supplier | None:
        if not name:
            return None
        key = norm(name)
        if key not in self.suppliers:
            sup = m.Supplier(name=name)
            self.db.add(sup)
            self.db.flush()
            self.suppliers[key] = sup
            self.stats.add("supplier")
        return self.suppliers[key]

    def _material(self, name: str, unit: str = "UNIT", **kw) -> m.RawMaterial:
        key = norm(name)
        if key not in self.materials:
            mat = m.RawMaterial(name=name, unit=unit, display_order=len(self.materials), **kw)
            self.db.add(mat)
            self.db.flush()
            self.materials[key] = mat
            self.stats.add("raw_material")
        return self.materials[key]

    @staticmethod
    def _customer_key(name: str, village=None, contact=None) -> str:
        """Identity is name + village + contact, never the name alone.

        Sheet13 holds two people called ANAND in different villages with
        different phone numbers, and two called SHIVA, SURESH and THANAPAL.
        Collapsing on the name would merge strangers into one debtor.
        """
        return "|".join(norm(x) for x in (name, village, contact))

    def _customer(self, name: str | None, village=None, contact=None, ctype=None,
                  added_on=None) -> m.Customer | None:
        if not name:
            return None
        key = self._customer_key(name, village, contact)
        if key not in self.customers:
            # A row that gives only a name matches an existing customer when
            # exactly one shares that name; otherwise it is a new record.
            if not village and not contact:
                same = [k for k in self.customers if k.split("|")[0] == norm(name)]
                if len(same) == 1:
                    self.customers[key] = self.customers[same[0]]
                    return self.customers[key]
            cust = m.Customer(name=name, village=village, contact=contact,
                              customer_type=ctype, added_on=added_on)
            self.db.add(cust)
            self.db.flush()
            self.customers[key] = cust
            self.stats.add("customer")
        return self.customers[key]

    # -- Sheet15 + Sheet2/3/4: labour master ------------------------------

    def import_labour_master(self) -> None:
        ws = self.wb["Sheet15"]
        for r in range(4, 20):
            name = S.text(ws.cell(r, 2).value)
            if not name:
                continue
            wage_cell = ws.cell(r, 3).value
            rate = S.num(wage_cell)
            if rate is None:                       # 'FIXER'
                wage_type = "FIXED"
                rate = S.num(ws.cell(r, 5).value) or 0.0
            else:
                wage_type = "DAILY"
            lab = m.Labour(name=name, wage_type=wage_type, rate=rate, is_active=1)
            self.db.add(lab)
            self.db.flush()
            self.labour[norm(name)] = lab
            self.stats.add("labour")

        # Phone numbers and the Sheet3 spelling live on Sheet3 row 3/4.
        s3 = self.wb["Sheet3"]
        for col in range(2, 26, 2):
            nm = S.text(s3.cell(4, col).value)
            lab = self._match_labour(nm)
            if lab is None:
                continue
            phone = S.text(s3.cell(3, col).value)
            if phone and not lab.phone:
                lab.phone = phone
            # '/' in the first rows marks "had not joined yet".
            join = None
            for r in range(10, 44):
                d = S.date(s3.cell(r, 1).value)
                if d is None:
                    continue
                raw = S.raw_text(s3.cell(r, col).value)
                if raw == "/":
                    continue
                join = d
                break
            if join and join > dt.date(2026, 7, 1):
                lab.join_date = join

        # Per-sheet column order (C: the sheets disagree with each other).
        self._record_sheet_order("Sheet2", self.wb["Sheet2"], 2, range(2, 17))
        self._record_sheet_order("Sheet3", s3, 4, range(2, 26, 2))
        self._record_sheet_order("Sheet4", self.wb["Sheet4"], 2, range(2, 17))
        self._record_sheet_order("Sheet14", self.wb["Sheet14"], 2, range(3, 12))
        ws15 = self.wb["Sheet15"]
        for pos, r in enumerate(range(4, 20)):
            nm = S.text(ws15.cell(r, 2).value)
            lab = self._match_labour(nm)
            if lab:
                self.db.add(m.LabourSheetOrder(
                    sheet="Sheet15", labour_id=lab.id, position=pos, label=nm))
        self.db.flush()

    def _record_sheet_order(self, sheet, ws, header_row, cols) -> None:
        pos = 0
        for col in cols:
            nm = S.text(ws.cell(header_row, col).value)
            if not nm or nm.upper() == "TOTAL":
                continue
            lab = self._match_labour(nm)
            if lab is None:
                continue
            self.db.add(m.LabourSheetOrder(
                sheet=sheet, labour_id=lab.id, position=pos, label=nm))
            pos += 1

    # -- Sheet1: power ----------------------------------------------------

    def import_power(self) -> None:
        ws = self.wb["Sheet1"]
        seen_anchor: set[tuple] = set()
        for r in range(5, 39):
            d = S.date(ws.cell(r, 1).value)
            kwh = S.num(ws.cell(r, 2).value)
            kvah = S.num(ws.cell(r, 3).value)
            if d is None and kwh is None:
                continue
            if d is None:
                # Row 38 carries readings with no date: the 1st of next month.
                d = dt.date(2026, 8, 1)
            taken = 1 if (kwh is not None and kvah is not None) else 0
            remarks = None
            if not taken:
                remarks = " / ".join(
                    x for x in (S.raw_text(ws.cell(r, 2).value),
                                S.raw_text(ws.cell(r, 3).value)) if x)
            # The anchor is the row with readings but no daily delta beside it.
            is_anchor = int(taken and S.num(ws.cell(r, 4).value) is None
                            and S.num(ws.cell(r, 7).value) is None and r != 6)
            if is_anchor and (d, kwh, kvah) in seen_anchor:
                # July restates the 16 Jun anchor mid-sheet as a separator.
                self.stats.add("power_anchor_restated")
                continue
            if is_anchor:
                seen_anchor.add((d, kwh, kvah))
            existing = self.db.query(m.PowerReading).filter_by(reading_date=d).first()
            if existing:
                continue
            self.db.add(m.PowerReading(
                reading_date=d, kwh=kwh, kvah=kvah,
                is_eb_anchor=is_anchor, reading_taken=taken, remarks=remarks))
            self.stats.add("power_reading")
        self.db.flush()

    # -- Sheet2: attendance ----------------------------------------------

    def import_attendance(self) -> None:
        ws = self.wb["Sheet2"]
        cols = {}
        for col in range(2, 17):
            nm = S.text(ws.cell(2, col).value)
            if nm and nm.upper() != "TOTAL":
                lab = self._match_labour(nm)
                if lab:
                    cols[col] = lab
        for r in range(4, 35):
            d = S.date(ws.cell(r, 1).value)
            if d is None:
                continue
            for col, lab in cols.items():
                v = S.num(ws.cell(r, col).value)
                if v is None:
                    continue
                self.db.add(m.Attendance(att_date=d, labour_id=lab.id, shift_units=v))
                self.stats.add("attendance")
        self.db.flush()

    # -- Sheet14 + Sheet3: extra work -------------------------------------

    def import_extra_work(self) -> None:
        unload = m.ExtraWorkType(code=UNLOAD_CODE, name="Fly ash powder unloading",
                                 unit="UNIT")
        adjust = m.ExtraWorkType(
            code=ADJUST_CODE,
            name="Migration adjustment (category unknown)",
            unit="RS", is_adjustment=1)
        self.db.add_all([unload, adjust])
        self.db.flush()
        self.work_types = {UNLOAD_CODE: unload, ADJUST_CODE: adjust}

        ws = self.wb["Sheet14"]
        rate = S.num(ws.cell(37, 13).value) or 100.0
        self.db.add(m.ExtraWorkRate(work_type_id=unload.id, rate=rate,
                                    effective_from=dt.date(2026, 7, 1)))
        self.db.add(m.ExtraWorkRate(work_type_id=adjust.id, rate=1.0,
                                    effective_from=dt.date(2026, 7, 1)))

        cols = {}
        for col in range(3, 12):
            nm = S.text(ws.cell(2, col).value)
            lab = self._match_labour(nm)
            if lab:
                cols[col] = lab

        booked: dict[tuple[int, dt.date], float] = {}
        for r in range(5, 36):
            d = S.date(ws.cell(r, 1).value)
            if d is None:
                continue
            for col, lab in cols.items():
                qty = S.num(ws.cell(r, col).value)
                if not qty:
                    continue
                self.db.add(m.ExtraWork(work_date=d, labour_id=lab.id,
                                        work_type_id=unload.id, qty=qty, rate=rate))
                booked[(lab.id, d)] = booked.get((lab.id, d), 0.0) + qty * rate
                self.stats.add("extra_work_unloading")

        # Sheet3's EXTRA column is typed by hand. Whatever it holds beyond the
        # unloading register is booked to MIGRATION ADJUSTMENT so that July
        # reconciles to the rupee and the gap stays visible per labour.
        s3 = self.wb["Sheet3"]
        for col in range(2, 26, 2):
            lab = self._match_labour(S.text(s3.cell(4, col).value))
            if lab is None:
                continue
            for r in range(10, 44):
                d = S.date(s3.cell(r, 1).value)
                if d is None:
                    continue
                typed = S.num(s3.cell(r, col + 1).value)
                if typed is None:
                    continue
                gap = round(typed - booked.get((lab.id, d), 0.0), 2)
                if abs(gap) < 0.005:
                    continue
                self.db.add(m.ExtraWork(
                    work_date=d, labour_id=lab.id, work_type_id=adjust.id,
                    qty=gap, rate=1.0,
                    remarks="Sheet3 typed extra with no register behind it"))
                self.stats.add("extra_work_adjustment")
        self.db.flush()

    # -- Sheet4: advances -------------------------------------------------

    def import_advances(self) -> None:
        ws = self.wb["Sheet4"]
        cols = {}
        for col in range(2, 17):
            nm = S.text(ws.cell(2, col).value)
            if nm and nm.upper() != "TOTAL":
                lab = self._match_labour(nm)
                if lab:
                    cols[col] = lab
        for r in range(4, 19):
            d = S.date(ws.cell(r, 1).value)
            if d is None:
                continue
            for col, lab in cols.items():
                amt = S.num(ws.cell(r, col).value)
                if not amt:
                    continue
                self.db.add(m.Advance(adv_date=d, labour_id=lab.id, amount=amt))
                self.stats.add("advance")
        self.db.flush()

    # -- Sheet15: the entered part only -----------------------------------

    def import_salary_settlements(self) -> None:
        ws = self.wb["Sheet15"]
        for r in range(4, 20):
            nm = S.text(ws.cell(r, 2).value)
            lab = self._match_labour(nm)
            if lab is None:
                continue
            pending = S.num(ws.cell(r, 13).value) or 0.0
            duty = S.num(ws.cell(r, 4).value)
            has_attendance = self.db.query(m.Attendance).filter_by(
                labour_id=lab.id).count() > 0
            self.db.add(m.SalarySettlement(
                period_year=2026, period_month=7, labour_id=lab.id,
                paid_on=S.date(ws.cell(r, 10).value),
                authority=S.text(ws.cell(r, 11).value),
                status=S.raw_text(ws.cell(r, 12).value),
                outstanding=pending,
                # C15: M.DIRECTOR has duty but no attendance column anywhere.
                duty_override=None if has_attendance else duty,
            ))
            self.stats.add("salary_settlement")
        self.db.flush()

    # -- Sheet13: customer master ----------------------------------------

    def import_customers(self) -> None:
        ws = self.wb["Sheet13"]
        carry = None
        for r in range(5, 82):
            carry = S.date_column(ws, r, 1, carry)
            name = S.text(ws.cell(r, 2).value)
            if not name:
                continue
            self._customer(name,
                           village=S.text(ws.cell(r, 3).value),
                           contact=S.text(ws.cell(r, 4).value),
                           ctype=S.text(ws.cell(r, 5).value),
                           added_on=carry)
        self.db.flush()

    # -- Sheet6: sales ----------------------------------------------------

    def import_sales(self) -> None:
        ws = self.wb["Sheet6"]
        carry_date = None
        current: m.Sale | None = None
        line_no = 0
        for r in range(5, 213):
            carry_date = S.date_column(ws, r, 1, carry_date)
            name = S.text(ws.cell(r, 3).value)
            qty = S.num(ws.cell(r, 4).value)
            if carry_date is None or not name or qty is None:
                continue
            seq_cell = ws.cell(r, 2).value
            seq = S.num(seq_cell)
            if seq is not None and not (current and S.is_ditto(seq_cell)):
                current = self._new_sale(ws, r, carry_date, int(seq))
                line_no = 0
            elif current is None:
                current = self._new_sale(ws, r, carry_date, 1)
                line_no = 0
            line_no += 1
            # A trip's payment can be marked against any of its lines -- the
            # 12 July sale carries CASH on its third -- so a sale that has not
            # been marked yet keeps looking down its own lines.
            if current.payment_mode is None:
                current.payment_mode = self._payment_mode(ws, r)
            rate = S.num(ws.cell(r, 5).value)
            amount = S.num(ws.cell(r, 6).value)
            if amount is None:
                amount = (qty * rate) if rate is not None else 0.0
            overridden = int(
                rate is not None and abs(qty * rate - amount) > 0.01)
            prod = self._product(name)
            self.db.add(m.SaleLine(
                sale_id=current.id, line_no=line_no, product_id=prod.id,
                qty=qty, rate=rate, amount=amount, amount_overridden=overridden))
            self.stats.add("sale_line")
            if overridden:
                self.stats.add("sale_line_amount_overridden")
        self.db.flush()

    @staticmethod
    def _payment_mode(ws, r: int) -> str | None:
        """How the register marked this row paid.

        What is written on the sale wins. The P / PAY amount in the margin is
        a digital collection into PAULRAJ.T's account, not a property of the
        sale beside it -- the amounts do not even match -- so it only decides
        the mode when nothing else does.
        """
        cash = S.text(ws.cell(r, 10).value)
        # The CREDIT column also gets used for notes -- 'RS 3000' against two
        # July rows -- so only the word itself marks a credit sale.
        credit = "CREDIT" in (S.text(ws.cell(r, 11).value) or "").upper()
        digital = S.text(ws.cell(r, 12).value)
        if credit:
            return "CREDIT"
        if cash:
            return "CASH"
        if digital or S.num(ws.cell(r, 16).value):
            return "DIGITAL"
        return None

    def _new_sale(self, ws, r: int, d: dt.date, seq: int) -> m.Sale:
        collector = S.text(ws.cell(r, 14).value)
        mode = self._payment_mode(ws, r)
        while (d, seq) in self.sale_by_key:
            seq += 1000                      # keep the unique key intact
        sale = m.Sale(
            sale_date=d, seq_no=seq, movement_type="SALE",
            vehicle_id=(v.id if (v := self._vehicle(S.text(ws.cell(r, 8).value))) else None),
            destination=S.text(ws.cell(r, 9).value),
            payment_mode=mode, collected_by=collector)
        self.db.add(sale)
        self.db.flush()
        self.sale_by_key[(d, sale.seq_no)] = sale
        self.stats.add("sale")
        return sale

    # -- Sheets 5, 8, 9: production and opening stock ---------------------

    def import_production_and_openings(self) -> None:
        specs = [
            # sheet, product, rows, opening col, production col, sales col
            ("Sheet5", FAB, 5, 187, 3, 4, 6),
            ("Sheet8", SOLID4, 5, 35, 2, 3, 5),
            ("Sheet9", SOLID6, 5, 17, 2, 3, 5),
        ]
        for sheet, pname, first, last, open_col, prod_col, sale_col in specs:
            ws = self.wb[sheet]
            prod = self._product(pname)
            opening = S.num(ws.cell(first, open_col).value) or 0.0
            self.db.add(m.StockOpening(product_id=prod.id,
                                       as_on=dt.date(2026, 7, 1), qty=opening))
            self.stats.add("stock_opening")
            carry = None
            by_day: dict[dt.date, float] = {}
            # How many of the day's sales the register had already posted when
            # production went in. Days split into a DUTY and an AFTER 5 PM
            # shift are summed -- one entry per product per day, rule 5 -- and
            # take the position of the first of the two.
            after: dict[dt.date, int] = {}
            sales_so_far: dict[dt.date, int] = {}
            for r in range(first, last + 1):
                carry = S.date_column(ws, r, 1, carry)
                if carry is None:
                    continue
                if S.num(ws.cell(r, sale_col).value):
                    sales_so_far[carry] = sales_so_far.get(carry, 0) + 1
                qty = S.num(ws.cell(r, prod_col).value)
                if not qty:
                    continue
                by_day[carry] = by_day.get(carry, 0.0) + qty
                after.setdefault(carry, sales_so_far.get(carry, 0))
            for d, qty in by_day.items():
                self.db.add(m.Production(
                    prod_date=d, product_id=prod.id, qty=qty,
                    after_seq=self._sale_seq_after(prod.id, d, after[d])))
                self.stats.add("production")
        self.db.flush()

    def _sale_seq_after(self, product_id: int, day: dt.date,
                        sales_before: int) -> int | None:
        """The seq_no of the sale that production was posted after.

        The ledger sheets number a day's rows per product, while a sale carries
        Sheet6's own S.No across every product, so the position has to be
        resolved through the sales actually recorded for that day.
        """
        if not sales_before:
            return 0
        seqs = [row[0] for row in self.db.execute(sa_text("""
            SELECT DISTINCT s.seq_no FROM sale s
            JOIN sale_line l ON l.sale_id = s.id
            WHERE l.product_id = :p AND s.sale_date = :d
            ORDER BY s.seq_no"""), {"p": product_id, "d": day.isoformat()}).all()]
        if sales_before >= len(seqs):
            return None                        # posted after the day's last sale
        return seqs[sales_before - 1]

    # -- Sheets 8, 9: damages -------------------------------------------

    def import_damages(self) -> None:
        for sheet, pname, first, last, sale_col, veh_col in [
            ("Sheet8", SOLID4, 5, 35, 5, 7),
            ("Sheet9", SOLID6, 5, 17, 5, 7),
        ]:
            ws = self.wb[sheet]
            prod = self._product(pname)
            carry = None
            for r in range(first, last + 1):
                carry = S.date_column(ws, r, 1, carry)
                veh = (S.text(ws.cell(r, veh_col).value) or "").upper()
                qty = S.num(ws.cell(r, sale_col).value)
                if carry is None or not qty or "DAMAGE" not in veh:
                    continue
                seq = 9000
                while (carry, seq) in self.sale_by_key:
                    seq += 1
                booked_to = S.text(ws.cell(r, veh_col + 1).value)
                sale = m.Sale(sale_date=carry, seq_no=seq, movement_type="DAMAGE",
                              destination=booked_to,
                              remarks=f"{sheet}: recorded as a sale to "
                                      f"{booked_to}")
                self.db.add(sale)
                self.db.flush()
                self.sale_by_key[(carry, seq)] = sale
                self.db.add(m.SaleLine(sale_id=sale.id, line_no=1,
                                       product_id=prod.id, qty=qty,
                                       rate=None, amount=0.0))
                self.stats.add("damage")
        self.db.flush()

    # -- Sheet16: the months that closed before the app existed -----------

    def import_stock_history(self) -> None:
        """Sheet16's 30-month roll-up, minus any month the app can derive.

        The sheet carries financial-year subtotal rows with no month or year
        against them; those are skipped, because the writer recomputes them.
        """
        ws = self.wb["Sheet16"]
        prod = self._product(FAB)
        derived = set()
        for (start,) in self.db.execute(sa_text(
                "SELECT DISTINCT period_start FROM stock_month_summary "
                "WHERE product_id = :p"), {"p": prod.id}).all():
            owned = dt.date.fromisoformat(str(start)[:10])
            derived.add((owned.year, owned.month))
        for r in range(4, 41):
            month = S.text(ws.cell(r, 2).value)
            year = S.num(ws.cell(r, 3).value)
            if not month or year is None:
                continue                       # a subtotal row
            try:
                number = MONTHS.index(month.strip().upper()) + 1
            except ValueError:
                continue
            if (int(year), number) in derived:
                continue                       # the app owns this month
            self.db.add(m.StockMonthHistory(
                product_id=prod.id, period_year=int(year), period_month=number,
                opening=S.num(ws.cell(r, 4).value) or 0.0,
                production=S.num(ws.cell(r, 5).value) or 0.0,
                sales=S.num(ws.cell(r, 7).value) or 0.0,
                closing=S.num(ws.cell(r, 8).value) or 0.0))
            self.stats.add("stock_month_history")
        self.db.flush()

    # -- Sheet7: delivery notes ------------------------------------------

    def import_deliveries(self) -> None:
        ws = self.wb["Sheet7"]
        carry = None
        for r in range(4, 50):
            carry = S.date_column(ws, r, 2, carry)
            name = S.text(ws.cell(r, 3).value)
            if carry is None or not name:
                continue
            self.db.add(m.Delivery(
                delivery_date=carry,
                seq_no=int(S.num(ws.cell(r, 1).value) or 0) or None,
                name=name,
                particulars=S.text(ws.cell(r, 4).value),
                identification=S.text(ws.cell(r, 5).value),
                details=S.text(ws.cell(r, 6).value),
                unload_location=S.raw_text(ws.cell(r, 8).value),
                contact=S.raw_text(ws.cell(r, 9).value),
                follow_up=S.text(ws.cell(r, 10).value),
                page_no=S.raw_text(ws.cell(r, 11).value),
                driver_name=S.text(ws.cell(r, 12).value)))
            self.stats.add("delivery")
        self.db.flush()

    # -- Sheet10: raw material inward ------------------------------------

    def import_materials(self) -> None:
        ws = self.wb["Sheet10"]
        for name, unit in SHEET10_MATERIALS.values():
            self._material(name, unit,
                           is_lot_tracked=1 if "CEMENT" in name else 0,
                           is_saleable=1 if "CEMENT" in name else 0)
        self._material(CEMENT, "BAGS", is_lot_tracked=1, is_saleable=1)
        carry = None
        for r in range(6, 27):
            carry = S.date_column(ws, r, 1, carry)
            if carry is None:
                continue
            rate = S.num(ws.cell(r, 12).value)
            amount = S.num(ws.cell(r, 13).value)
            supplier = self._supplier(S.text(ws.cell(r, 2).value))
            for letter, (mname, _unit) in SHEET10_MATERIALS.items():
                col = S.col_letter_to_index(letter)
                qty = S.num(ws.cell(r, col).value)
                if not qty:
                    continue
                mat = self.materials[norm(mname)]
                amt = amount if amount is not None else (qty * (rate or 0.0))
                self.db.add(m.MaterialReceipt(
                    receipt_date=carry,
                    supplier_id=supplier.id if supplier else None,
                    bill_no=S.text(ws.cell(r, 3).value),
                    vehicle_no=S.text(ws.cell(r, 4).value),
                    material_id=mat.id, qty=qty, rate=rate or 0.0, amount=amt,
                    amount_overridden=int(
                        rate is not None and abs(qty * rate - amt) > 0.01),
                    driver_name=S.text(ws.cell(r, 14).value),
                    payment_status=S.text(ws.cell(r, 15).value) or "PENDING"))
                self.stats.add("material_receipt")
        self.db.flush()

    # -- Sheet12: cement stock and lots ----------------------------------

    def import_cement(self) -> None:
        ws = self.wb["Sheet12"]
        cement = self.materials[norm(CEMENT)]
        opening = S.num(ws.cell(5, 2).value) or 0.0
        self.db.add(m.MaterialOpening(material_id=cement.id,
                                      as_on=dt.date(2026, 7, 1), qty=opening))

        # Lot columns J..Q. Two of them are labelled rather than dated.
        lot_cols: dict[int, m.MaterialLot] = {}
        for i, col in enumerate(range(10, 18)):
            head = ws.cell(2, col).value
            lot_date = S.date(head)
            label = S.text(head) or f"LOT{col}"
            lot = m.MaterialLot(material_id=cement.id, lot_date=lot_date,
                                label=label, qty_in=0.0, display_order=i)
            self.db.add(lot)
            self.db.flush()
            lot_cols[col] = lot
            self.cement_lots[label] = lot
            self.stats.add("material_lot")

        prev = {col: (S.num(ws.cell(5, col).value) or 0.0) for col in lot_cols}
        for col, v in prev.items():
            lot_cols[col].qty_in = v          # balances standing on day one

        for r in range(5, 36):
            d = S.date(ws.cell(r, 1).value)
            if d is None:
                continue
            consumed = S.num(ws.cell(r, 6).value) or 0.0
            note = S.text(ws.cell(r, 9).value)
            cons = None
            if consumed:
                cons = m.MaterialConsumption(cons_date=d, material_id=cement.id,
                                             qty=consumed, remarks=note)
                self.db.add(cons)
                self.db.flush()
                self.stats.add("material_consumption")
            if r == 5:
                continue
            for col, lot in lot_cols.items():
                cur = S.num(ws.cell(r, col).value) or 0.0
                delta = round(prev[col] - cur, 3)
                prev[col] = cur
                if delta == 0:
                    continue
                if delta < 0:
                    # The lot grew: a receipt arriving, or a correction.
                    lot.qty_in = round(lot.qty_in - delta, 3)
                    if cons is None:
                        continue
                if cons is None:
                    continue
                self.db.add(m.ConsumptionAllocation(
                    lot_id=lot.id, consumption_id=cons.id, qty=delta))
                self.stats.add("lot_allocation")
        self.db.flush()

    # -- Sheet17: receivables --------------------------------------------

    def import_receivables(self) -> None:
        """Sheet17 is a debtor list with multi-row customers.

        A row whose name column is blank -- or holds a number, which is how the
        register writes a charge before a part-payment -- continues the
        customer above it. Dropping those rows loses Rs 14,428.
        """
        ws = self.wb["Sheet17"]
        # The snapshot already embodies July's credit sales, so it is dated as
        # the opening of the next period. That is what stops the balance view
        # counting those sales twice. The real ledger is Phase 3.
        as_on = dt.date(2026, 8, 1)
        for block_first, block_last in ((4, 10), (13, 72)):
            carry: m.Customer | None = None
            for r in range(block_first, block_last + 1):
                name = S.text(ws.cell(r, 2).value)
                amt = S.num(ws.cell(r, 5).value)
                is_continuation = not name or S.num(name) is not None
                if is_continuation:
                    cust = carry
                else:
                    cust = self._customer(
                        name,
                        village=S.text(ws.cell(r, 3).value),
                        contact=S.text(ws.cell(r, 4).value))
                    carry = cust
                if cust is None or amt is None or amt == 0:
                    continue
                existing = self.db.query(m.ReceivableOpening).filter_by(
                    customer_id=cust.id, as_on=as_on).first()
                if existing:
                    existing.amount = round(existing.amount + amt, 2)
                    continue
                self.db.add(m.ReceivableOpening(
                    customer_id=cust.id, as_on=as_on, amount=amt,
                    note=f"Sheet17 row {r}; dated "
                         f"{S.raw_text(ws.cell(r, 6).value) or 'unknown'}"))
                self.stats.add("receivable_opening")
        self.db.flush()

    # -- Sheet19: digital ledger -----------------------------------------

    def import_digital_ledger(self) -> None:
        ws = self.wb["Sheet19"]
        carry_in = carry_out = None
        for r in range(5, 39):
            carry_in = S.date_column(ws, r, 1, carry_in)
            desc = S.text(ws.cell(r, 2).value)
            amt = S.num(ws.cell(r, 3).value)
            if carry_in and desc and amt:
                opening = "BALANCE" in desc.upper()
                self.db.add(m.DigitalLedger(
                    txn_date=carry_in, direction="IN",
                    entry_type="OPENING" if opening else "MANUAL",
                    description=desc, amount=amt))
                self.stats.add("ledger_in")
            carry_out = S.date_column(ws, r, 4, carry_out)
            desc = S.text(ws.cell(r, 5).value)
            amt = S.num(ws.cell(r, 6).value)
            if carry_out and desc and amt:
                self.db.add(m.DigitalLedger(
                    txn_date=carry_out, direction="OUT", entry_type="MANUAL",
                    description=desc, amount=amt))
                self.stats.add("ledger_out")
        self.db.flush()

    # -- Sheets 11, 20 ---------------------------------------------------

    def import_misc_registers(self) -> None:
        ws = self.wb["Sheet11"]
        for r in range(1, 41):
            d = S.date(ws.cell(r, 1).value)
            start = S.num(ws.cell(r, 2).value)
            stop = S.num(ws.cell(r, 3).value)
            if d is None or start is None or stop is None:
                continue
            if self.db.query(m.BikeMileage).filter_by(log_date=d).first():
                continue
            self.db.add(m.BikeMileage(log_date=d, start_km=start, stop_km=stop))
            self.stats.add("bike_mileage")

        ws = self.wb["Sheet20"]
        for r in range(1, 68):
            d = S.date(ws.cell(r, 1).value)
            if d is None:
                continue
            if self.db.query(m.PalletMovement).filter_by(move_date=d).first():
                continue
            self.db.add(m.PalletMovement(
                move_date=d,
                stocking_area=S.num(ws.cell(r, 2).value),
                production_area=S.num(ws.cell(r, 3).value),
                today_prodn=S.num(ws.cell(r, 4).value),
                pallets_stocked=S.num(ws.cell(r, 6).value),
                shifted_from_stocking=S.num(ws.cell(r, 7).value)))
            self.stats.add("pallet_movement")
        self.db.flush()


def import_july(session: Session, path) -> ImportStats:
    return JulyImporter(session, path).run()
