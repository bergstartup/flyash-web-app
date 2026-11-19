"""Read one month's register workbook into SQLite.

This is not a general Excel reader. It encodes the layout of the plant's
monthly register -- the July 2026 file is the reference -- including its
conventions:

* ditto marks repeat the date, and on Sheet6 they can also repeat the S.No,
  which turns the next row into a second product line on the same sale;
* ``/`` means the labour had not joined yet, which is distinct from absent;
* text such as LEAVE or NIL appears in numeric columns;
* Sheet3's extra-wage column is typed by hand and runs well ahead of the only
  extra-work register that exists, so the balance is booked to a MIGRATION
  ADJUSTMENT work type rather than silently dropped.

Months mostly keep the same sheets and columns, but not entirely: October
moved the digital ledger and the pallet register to other sheets, listed
fewer hands on Sheet14 and dropped Sheet15's STATUS. So the moved registers
are found by their titles, column positions that depend on the hands listed
are found by their headings, and each register is read from its first data row down
to the footer the sheet itself writes (``DESCRIPTION``, ``TOTAL`` and the
like), and only rows dated inside the month are taken.

Masters -- labour, products, customers, vehicles, suppliers, materials, lots,
work types -- are matched against what the database already holds and created
only when the file names something new. On an empty database that creates
them all, which is the July migration; on a live one it is the month import
(``app/services/month_import.py``), which clears the month first.
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import func
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
    # The register's own spelling; the writer emits the unit verbatim.
    "J": ("VITALA NEO CHEMICAL", "LTR'S"),
    "K": ("FAP MIX CEMENT", "BAGS"),
}

# Every sheet the importer reads by name. A file missing one is not a register.
SHEETS = ("Sheet1", "Sheet2", "Sheet3", "Sheet4", "Sheet5", "Sheet6", "Sheet7",
          "Sheet8", "Sheet9", "Sheet10", "Sheet11", "Sheet12", "Sheet13",
          "Sheet14", "Sheet15", "Sheet16", "Sheet17")

# Registers that have moved between months, found by the title in A1 instead:
# the digital ledger is Sheet19 in July and Sheet24 in October, the pallet
# register Sheet20 in July and Sheet19 in October.
TITLED = {"ledger": "DIGITAL ACCOUNT", "pallets": "PALLETS DETAILS"}

# Header cells that every month's register carries, to refuse a file that has
# the right sheet names but some other layout.
HEADERS = (("Sheet2", "A2", "NAME"), ("Sheet6", "A3", "DATE"),
           ("Sheet6", "C3", "NAME"), ("Sheet10", "A4", "DATE"),
           ("Sheet12", "A3", "DATE"), ("Sheet15", "B2", "NAME"),
           ("Sheet17", "B2", "NAME"), ("ledger", "A3", "DATE"))


class LayoutError(ValueError):
    """The workbook is not laid out like the monthly register."""


def titled_sheet(wb, key: str):
    """The sheet whose A1 title names the register, or None."""
    want = TITLED[key]
    for ws in wb.worksheets:
        title = re.sub(r"\s+", " ", (S.text(ws["A1"].value) or "").upper())
        if want in title:
            return ws
    return None


def check_layout(wb) -> None:
    missing = [s for s in SHEETS if s not in wb.sheetnames]
    missing += [f"the {TITLED[k].lower()} sheet" for k in TITLED
                if titled_sheet(wb, k) is None]
    if missing:
        raise LayoutError(
            "This is not the monthly register: it has no "
            + ", ".join(missing) + ".")
    for sheet, ref, want in HEADERS:
        ws = titled_sheet(wb, sheet) if sheet in TITLED else wb[sheet]
        got = (S.text(ws[ref].value) or "").upper()
        if want not in got:
            raise LayoutError(
                f"{ws.title}!{ref} should read {want} but reads "
                f"'{got or 'nothing'}'. The file is not laid out like the "
                "monthly register.")


def workbook_month(wb) -> dt.date:
    """The month a register is for: the month most of Sheet2's days fall in."""
    ws = wb["Sheet2"]
    months = Counter(
        d.replace(day=1) for r in range(4, ws.max_row + 1)
        if (d := S.date(ws.cell(r, 1).value)) is not None)
    if not months:
        raise LayoutError("Sheet2 has no dates, so the month cannot be told.")
    return months.most_common(1)[0][0]


def next_month(day: dt.date) -> dt.date:
    return (day.replace(day=28) + dt.timedelta(days=4)).replace(day=1)


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


class WorkbookImporter:
    def __init__(self, session: Session, path, month: dt.date | None = None):
        self.db = session
        self.path = str(path)
        self.wb = S.load(self.path, values=True)
        check_layout(self.wb)
        self.start = (month or workbook_month(self.wb)).replace(day=1)
        self.end = next_month(self.start)
        self.stats = ImportStats()
        # Names this import had to add to a master list, for the summary.
        self.created: list[tuple[str, str]] = []
        self.warnings: list[str] = []
        self.new_labour: set[int] = set()

        self.labour: dict[str, m.Labour] = {
            norm(x.name): x for x in self.db.query(m.Labour)}
        self.products: dict[str, m.Product] = {
            norm(x.name): x for x in self.db.query(m.Product)}
        self.vehicles: dict[str, m.Vehicle] = {
            norm(x.name): x for x in self.db.query(m.Vehicle)}
        self.suppliers: dict[str, m.Supplier] = {
            norm(x.name): x for x in self.db.query(m.Supplier)}
        self.materials: dict[str, m.RawMaterial] = {
            norm(x.name): x for x in self.db.query(m.RawMaterial)}
        self.work_types: dict[str, m.ExtraWorkType] = {
            x.code: x for x in self.db.query(m.ExtraWorkType)}
        self.customers: dict[str, m.Customer] = {}
        for c in self.db.query(m.Customer).order_by(m.Customer.id):
            self.customers.setdefault(
                self._customer_key(c.name, c.village, c.contact), c)
        self.cement_lots: dict[str, m.MaterialLot] = {}
        self.sale_by_key: dict[tuple[dt.date, int], m.Sale] = {}
        self.ledger_ws = titled_sheet(self.wb, "ledger")
        self.pallet_ws = titled_sheet(self.wb, "pallets")

    # -- entry point ------------------------------------------------------

    def run(self, commit: bool = True) -> ImportStats:
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
        if commit:
            self.db.commit()
        else:
            self.db.flush()
        return self.stats

    # -- helpers ----------------------------------------------------------

    def _in_month(self, d: dt.date | None) -> bool:
        """Whether a row belongs to the month. A row from either side of it
        -- a register that runs on into the next month -- is counted and left
        alone, so importing one month never touches another."""
        if d is None:
            return False
        if self.start <= d < self.end:
            return True
        self.stats.add("row_outside_month")
        return False

    @staticmethod
    def _last_row(ws, first: int, is_footer) -> int:
        """The last data row: the row before the sheet's own footer."""
        for r in range(first, ws.max_row + 1):
            if is_footer(r):
                return r - 1
        return ws.max_row

    @staticmethod
    def _label(ws, r: int, c: int) -> str:
        return (S.text(ws.cell(r, c).value) or "").upper()

    def _created(self, kind: str, name: str) -> None:
        self.created.append((kind, name))

    def _match_labour(self, raw: str | None) -> m.Labour | None:
        return resolve(raw, self.labour)

    def _product(self, name: str | None) -> m.Product | None:
        if not name:
            return None
        key = norm(name)
        if key in self.products:
            return self.products[key]
        same = self._product_like(name)
        if same is not None:
            self.products[key] = same
            return same
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
        self._created("product", name)
        return prod

    @staticmethod
    def _product_tokens(name: str) -> list[str]:
        return sorted(re.findall(r"\d+(?:\.\d+)?|[A-Z]+", name.upper()))

    def _product_like(self, name: str) -> m.Product | None:
        """An existing product the register has written another way.

        October writes 5"*5"*10' K-PO for July's 10'*5"*5" K -- PO -- the
        same sizes in another order -- and sells 6" -- SOLID out of the
        6"*12" -- SOLID ledger. So a name matches a product with the same
        sizes and words in any order, or else the one stock-ledger product
        whose name holds all of its sizes and words.
        """
        tokens = self._product_tokens(name)
        if not tokens:
            return None
        known = {id(p): p for p in self.products.values()}.values()
        same = [p for p in known if self._product_tokens(p.name) == tokens]
        if len(same) == 1:
            return same[0]
        wanted = Counter(tokens)
        within = [p for p in known if p.has_stock_ledger
                  and not wanted - Counter(self._product_tokens(p.name))]
        return within[0] if len(within) == 1 else None

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
            self._created("supplier", name)
        return self.suppliers[key]

    def _material(self, name: str, unit: str = "UNIT", **kw) -> m.RawMaterial:
        key = norm(name)
        if key not in self.materials:
            mat = m.RawMaterial(name=name, unit=unit, display_order=len(self.materials), **kw)
            self.db.add(mat)
            self.db.flush()
            self.materials[key] = mat
            self.stats.add("raw_material")
            self._created("material", name)
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
                same = {id(v): k for k, v in self.customers.items()
                        if k.split("|")[0] == norm(name)}
                if len(same) == 1:
                    self.customers[key] = self.customers[next(iter(same.values()))]
                    return self.customers[key]
            cust = m.Customer(name=name, village=village, contact=contact,
                              customer_type=ctype, added_on=added_on)
            self.db.add(cust)
            self.db.flush()
            self.customers[key] = cust
            self.stats.add("customer")
            self._created("customer", name)
        return self.customers[key]

    def _work_type(self, code: str, **kw) -> m.ExtraWorkType:
        if code not in self.work_types:
            wt = m.ExtraWorkType(code=code, **kw)
            self.db.add(wt)
            self.work_types[code] = wt
        return self.work_types[code]

    def _rate_from(self, work_type: m.ExtraWorkType, rate: float) -> None:
        """Start ``rate`` on the 1st, unless it is already the rate then.
        Rates are snapshotted onto each entry, so a new one never rewrites
        an earlier month."""
        current = self.db.query(m.ExtraWorkRate).filter(
            m.ExtraWorkRate.work_type_id == work_type.id,
            m.ExtraWorkRate.effective_from <= self.start,
        ).order_by(m.ExtraWorkRate.effective_from.desc()).first()
        if current is not None and abs(current.rate - rate) < 1e-9:
            return
        if current is not None and current.effective_from == self.start:
            current.rate = rate
            return
        self.db.add(m.ExtraWorkRate(work_type_id=work_type.id, rate=rate,
                                    effective_from=self.start))

    # -- Sheet15 + Sheet2/3/4: labour master ------------------------------

    def _sheet15_rows(self) -> range:
        ws = self.wb["Sheet15"]
        # The table ends where the sheet repeats its header for the totals.
        last = self._last_row(ws, 4, lambda r: self._label(ws, r, 2) == "NAME")
        return range(4, last + 1)

    def import_labour_master(self) -> None:
        ws = self.wb["Sheet15"]
        for r in self._sheet15_rows():
            name = S.text(ws.cell(r, 2).value)
            if not name:
                continue
            if norm(name) in self.labour:
                # The master is the app's: a wage changed in the register is
                # changed on the Staff screen, so it never rewrites a month.
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
            self.new_labour.add(lab.id)
            self.stats.add("labour")
            self._created("labour", name)

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
            if lab.id not in self.new_labour:
                continue
            # '/' in the first rows marks "had not joined yet".
            join = None
            for r in range(10, s3.max_row + 1):
                d = S.date(s3.cell(r, 1).value)
                if d is None:
                    continue
                raw = S.raw_text(s3.cell(r, col).value)
                if raw == "/":
                    continue
                join = d
                break
            if join and join > self.start:
                lab.join_date = join

        # Per-sheet column order (C: the sheets disagree with each other).
        self._record_sheet_order("Sheet2", self.wb["Sheet2"], 2, range(2, 17))
        self._record_sheet_order("Sheet3", s3, 4, range(2, 26, 2))
        self._record_sheet_order("Sheet4", self.wb["Sheet4"], 2, range(2, 17))
        self._record_sheet_order("Sheet14", self.wb["Sheet14"], 2, range(3, 12))
        ws15 = self.wb["Sheet15"]
        for pos, r in enumerate(self._sheet15_rows()):
            nm = S.text(ws15.cell(r, 2).value)
            lab = self._match_labour(nm)
            if lab:
                self._sheet_order("Sheet15", lab, pos, nm)
        self.db.flush()

    def _sheet_order(self, sheet: str, lab: m.Labour, pos: int, label: str) -> None:
        row = self.db.query(m.LabourSheetOrder).filter_by(
            sheet=sheet, labour_id=lab.id).first()
        if row is None:
            self.db.add(m.LabourSheetOrder(
                sheet=sheet, labour_id=lab.id, position=pos, label=label))
        else:
            row.position, row.label = pos, label

    def _record_sheet_order(self, sheet, ws, header_row, cols) -> None:
        pos = 0
        for col in cols:
            nm = S.text(ws.cell(header_row, col).value)
            if not nm or nm.upper() == "TOTAL":
                continue
            lab = self._match_labour(nm)
            if lab is None:
                continue
            self._sheet_order(sheet, lab, pos, nm)
            pos += 1

    # -- Sheet1: power ----------------------------------------------------

    def import_power(self) -> None:
        ws = self.wb["Sheet1"]
        seen_anchor: set[tuple] = set()
        taken_here: dict[dt.date, int] = {}
        undated_seen = False
        for r in range(5, ws.max_row + 1):
            d = S.date(ws.cell(r, 1).value)
            kwh = S.num(ws.cell(r, 2).value)
            kvah = S.num(ws.cell(r, 3).value)
            if d is None and kwh is None:
                continue
            if d is None:
                # The last row carries readings with no date: the 1st of the
                # next month.
                if undated_seen:
                    self.warnings.append(
                        f"Sheet1 row {r}: a second reading with no date was "
                        "left out.")
                    continue
                undated_seen = True
                d = self.end
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
            if d in taken_here:
                # October writes a second 21 Oct row -- the EB meter reading
                # that starts the next billing cycle -- under the day's own.
                # The app holds one reading a day, so the first one stands.
                note = (" It looks like a new EB billing start, so the "
                        "cumulative figures after it still run from the "
                        "earlier one." if is_anchor else "")
                self.warnings.append(
                    f"Sheet1 row {r}: a second reading for {d:%d %b %Y} "
                    f"({kwh} / {kvah}) was left out; the app keeps one "
                    f"reading a day and kept row {taken_here[d]}'s.{note}")
                continue
            taken_here[d] = r
            existing = self.db.query(m.PowerReading).filter_by(reading_date=d).first()
            if existing:
                # Only a reading outside the month can still be here: the
                # month itself was cleared. The app's copy stands.
                if (existing.kwh, existing.kvah) != (kwh, kvah):
                    self.warnings.append(
                        f"Sheet1: the {d:%d %b %Y} reading in the file "
                        f"({kwh} / {kvah}) differs from the app's "
                        f"({existing.kwh} / {existing.kvah}); the app's was "
                        "kept, because that day is outside this month.")
                continue
            self.db.add(m.PowerReading(
                reading_date=d, kwh=kwh, kvah=kvah,
                is_eb_anchor=is_anchor, reading_taken=taken, remarks=remarks))
            self.stats.add("power_reading")
        self.db.flush()

    # -- Sheet2: attendance ----------------------------------------------

    def _labour_columns(self, ws, header_row: int, cols) -> dict[int, m.Labour]:
        out = {}
        for col in cols:
            nm = S.text(ws.cell(header_row, col).value)
            if nm and nm.upper() != "TOTAL":
                lab = self._match_labour(nm)
                if lab:
                    out[col] = lab
        return out

    def import_attendance(self) -> None:
        ws = self.wb["Sheet2"]
        cols = self._labour_columns(ws, 2, range(2, 17))
        for r in range(4, ws.max_row + 1):
            d = S.date(ws.cell(r, 1).value)
            if not self._in_month(d):
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
        unload = self._work_type(UNLOAD_CODE, name="Fly ash powder unloading",
                                 unit="UNIT")
        adjust = self._work_type(
            ADJUST_CODE,
            name="Migration adjustment (category unknown)",
            unit="RS", is_adjustment=1)
        self.db.flush()

        ws = self.wb["Sheet14"]
        # One column per hand, then PER DAY. The rate sits on the totals row
        # -- the first one headed DATE below the days -- just right of the
        # PER DAY total. Which column that is depends on how many hands the
        # month lists: M in July, L in October.
        per_day = next(
            (c for c in range(3, ws.max_column + 1)
             if "PERDAY" in norm(self._label(ws, 2, c))), 12)
        footer = self._last_row(ws, 5, lambda r: self._label(ws, r, 1) == "DATE") + 1
        rate = S.num(ws.cell(footer, per_day + 1).value) or 100.0
        self._rate_from(unload, rate)
        self._rate_from(adjust, 1.0)

        cols = {}
        for col in range(3, per_day):
            nm = S.text(ws.cell(2, col).value)
            lab = self._match_labour(nm)
            if lab:
                cols[col] = lab

        booked: dict[tuple[int, dt.date], float] = {}
        for r in range(5, ws.max_row + 1):
            d = S.date(ws.cell(r, 1).value)
            if not self._in_month(d):
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
        # unloading register is booked to MIGRATION ADJUSTMENT so that the
        # month reconciles to the rupee and the gap stays visible per labour.
        s3 = self.wb["Sheet3"]
        for col in range(2, 26, 2):
            lab = self._match_labour(S.text(s3.cell(4, col).value))
            if lab is None:
                continue
            for r in range(10, s3.max_row + 1):
                d = S.date(s3.cell(r, 1).value)
                if not self._in_month(d):
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
        cols = self._labour_columns(ws, 2, range(2, 17))
        for r in range(4, ws.max_row + 1):
            d = S.date(ws.cell(r, 1).value)
            if not self._in_month(d):
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
        # July has a STATUS column in L; October uses L for the month
        # before's pending amount instead.
        has_status = self._label(ws, 2, 12) == "STATUS"
        for r in self._sheet15_rows():
            nm = S.text(ws.cell(r, 2).value)
            lab = self._match_labour(nm)
            if lab is None:
                continue
            pending = S.num(ws.cell(r, 13).value) or 0.0
            duty = S.num(ws.cell(r, 4).value)
            has_attendance = self.db.query(m.Attendance).filter(
                m.Attendance.labour_id == lab.id,
                m.Attendance.att_date >= self.start,
                m.Attendance.att_date < self.end).count() > 0
            self.db.add(m.SalarySettlement(
                period_year=self.start.year, period_month=self.start.month,
                labour_id=lab.id,
                paid_on=S.date(ws.cell(r, 10).value),
                authority=S.text(ws.cell(r, 11).value),
                status=S.raw_text(ws.cell(r, 12).value) if has_status else None,
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
        for r in range(5, ws.max_row + 1):
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

        def footer(r: int) -> bool:
            # Below the sales the sheet repeats its header: a word in the date
            # column where only dates and ditto marks belong.
            v = ws.cell(r, 1).value
            return (isinstance(v, str) and not S.is_ditto(v)
                    and S.date(v) is None and bool(v.strip()))

        carry_date = None
        current: m.Sale | None = None
        line_no = 0
        for r in range(5, self._last_row(ws, 5, footer) + 1):
            carry_date = S.date_column(ws, r, 1, carry_date)
            name = S.text(ws.cell(r, 3).value)
            qty = S.num(ws.cell(r, 4).value)
            if carry_date is None or not name or qty is None:
                continue
            if not self._in_month(carry_date):
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
        if (digital or S.num(ws.cell(r, 16).value)
                or WorkbookImporter._collection(ws, r)[1]):
            return "DIGITAL"
        return None

    @staticmethod
    def _collection(ws, r: int) -> tuple[str | None, float | None]:
        """Who took a digital collection in the margin, and how much.

        July writes ``P / PAY | PAULRAJ.T | A / C | 8880`` from M; October
        writes ``PAUL RAJ .T | PHO / PAY | AMT / RS | 4000`` from L. Either
        way the name sits beside the PAY marker and the amount is the first
        number after it. A row with no marker keeps the July reading: N.
        """
        marker = next(
            (c for c in range(12, ws.max_column + 1)
             if norm(S.text(ws.cell(r, c).value)).endswith("PAY")), None)
        if marker is None:
            return S.text(ws.cell(r, 14).value), None
        captions = {"AMTRS", "AC", "RS", "ONLY"}
        who = next(
            (t for c in (marker + 1, marker - 1)
             if (t := S.text(ws.cell(r, c).value)) and norm(t) not in captions
             and S.num(t) is None), None)
        amount = next(
            (v for c in range(marker + 1, ws.max_column + 1)
             if (v := S.num(ws.cell(r, c).value)) is not None), None)
        return who, amount

    def _new_sale(self, ws, r: int, d: dt.date, seq: int) -> m.Sale:
        collector = self._collection(ws, r)[0]
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

    def _ledger_last_row(self, ws, first: int) -> int:
        """Sheets 5, 8 and 9 close on a DESCRIPTION totals row -- spelt
        DISCRIPTION on October's Sheet5."""
        return self._last_row(
            ws, first, lambda r: self._label(ws, r, 1) in (
                "DESCRIPTION", "DISCRIPTION"))

    def import_production_and_openings(self) -> None:
        specs = [
            # sheet, product, first row, opening col, production col, sales col
            ("Sheet5", FAB, 5, 3, 4, 6),
            ("Sheet8", SOLID4, 5, 2, 3, 5),
            ("Sheet9", SOLID6, 5, 2, 3, 5),
        ]
        for sheet, pname, first, open_col, prod_col, sale_col in specs:
            ws = self.wb[sheet]
            last = self._ledger_last_row(ws, first)
            prod = self._product(pname)
            opening = S.num(ws.cell(first, open_col).value) or 0.0
            self.db.add(m.StockOpening(product_id=prod.id,
                                       as_on=self.start, qty=opening))
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
                if carry is None or not (self.start <= carry < self.end):
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
        for sheet, pname, first, sale_col, veh_col in [
            ("Sheet8", SOLID4, 5, 5, 7),
            ("Sheet9", SOLID6, 5, 5, 7),
        ]:
            ws = self.wb[sheet]
            prod = self._product(pname)
            carry = None
            for r in range(first, self._ledger_last_row(ws, first) + 1):
                carry = S.date_column(ws, r, 1, carry)
                veh = (S.text(ws.cell(r, veh_col).value) or "").upper()
                qty = S.num(ws.cell(r, sale_col).value)
                if carry is None or not qty or "DAMAGE" not in veh:
                    continue
                if not self._in_month(carry):
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
        """Sheet16's month roll-up, minus any month the app can derive or
        already holds.

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
        held = {(h.period_year, h.period_month)
                for h in self.db.query(m.StockMonthHistory).filter_by(
                    product_id=prod.id)}
        for r in range(4, ws.max_row + 1):
            month = S.text(ws.cell(r, 2).value)
            year = S.num(ws.cell(r, 3).value)
            if not month or year is None:
                continue                       # a subtotal row
            try:
                number = MONTHS.index(month.strip().upper()) + 1
            except ValueError:
                continue
            if (int(year), number) in derived | held:
                continue                       # the app owns this month
            held.add((int(year), number))
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
        for r in range(4, ws.max_row + 1):
            carry = S.date_column(ws, r, 2, carry)
            name = S.text(ws.cell(r, 3).value)
            if carry is None or not name:
                continue
            if not self._in_month(carry):
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
        for letter, (name, unit) in SHEET10_MATERIALS.items():
            # Which column Sheet10 gives this material. The sheet is wide --
            # one column per material -- so the writer needs the mapping, and
            # it belongs on the material rather than in the writer.
            self._material(name, unit, sheet10_column=letter,
                           is_lot_tracked=1 if "CEMENT" in name else 0,
                           is_saleable=1 if "CEMENT" in name else 0)
        self._material(CEMENT, "BAGS", is_lot_tracked=1, is_saleable=1)
        # The deliveries end at the column totals, the row headed RATE.
        last = self._last_row(ws, 6, lambda r: self._label(ws, r, 12) == "RATE")
        carry = None
        for r in range(6, last + 1):
            carry = S.date_column(ws, r, 1, carry)
            if carry is None:
                continue
            rate = S.num(ws.cell(r, 12).value)
            amount = S.num(ws.cell(r, 13).value)
            quantities = {
                letter: qty for letter in SHEET10_MATERIALS
                if (qty := S.num(ws.cell(r, S.col_letter_to_index(letter)).value))}
            if not quantities or not self._in_month(carry):
                continue
            supplier = self._supplier(S.text(ws.cell(r, 2).value))
            for letter, qty in quantities.items():
                mname, _unit = SHEET10_MATERIALS[letter]
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

    def _lot_carried(self, lot: m.MaterialLot) -> float:
        """What a lot held at the end of the month before, as Sheet12 counts
        it: what flowed in, less what production drew from it."""
        return self.db.execute(sa_text("""
            SELECT COALESCE((SELECT SUM(i.qty) FROM material_lot_inflow i
                              WHERE i.lot_id = :lot AND i.as_on < :start), 0)
                 - COALESCE((SELECT SUM(ca.qty) FROM consumption_allocation ca
                              JOIN material_consumption mc
                                ON mc.id = ca.consumption_id
                              WHERE ca.lot_id = :lot
                                AND mc.cons_date < :start), 0)"""),
            {"lot": lot.id, "start": self.start.isoformat()}).scalar() or 0.0

    def _cement_lot(self, cement: m.RawMaterial, label: str,
                    lot_date: dt.date | None, order: int) -> m.MaterialLot:
        lot = self.db.query(m.MaterialLot).filter_by(
            material_id=cement.id, label=label).first()
        if lot is None:
            if self.db.query(m.MaterialLot).filter_by(
                    material_id=cement.id).count():
                highest = self.db.query(func.max(m.MaterialLot.display_order)
                                        ).filter_by(material_id=cement.id).scalar()
                order = (highest or 0) + 1
            lot = m.MaterialLot(material_id=cement.id, lot_date=lot_date,
                                label=label, display_order=order)
            self.db.add(lot)
            self.db.flush()
            self.stats.add("material_lot")
            self._created("cement lot", label)
        return lot

    def import_cement(self) -> None:
        ws = self.wb["Sheet12"]
        cement = self.materials[norm(CEMENT)]
        opening = S.num(ws.cell(5, 2).value) or 0.0
        self.db.add(m.MaterialOpening(material_id=cement.id,
                                      as_on=self.start, qty=opening))

        # Lot columns from J up to the TOTAL column: J..Q in July, J..N in
        # October. Some are labelled (M / N, CON) rather than dated.
        lot_cols: dict[int, m.MaterialLot] = {}
        for i, col in enumerate(range(10, 18)):
            head = ws.cell(2, col).value
            if self._label(ws, 2, col) == "TOTAL":
                break
            if S.text(head) is None and not any(
                    S.num(ws.cell(r, col).value) for r in range(5, ws.max_row + 1)):
                continue                       # an unused column
            lot_date = S.date(head)
            label = S.text(head) or f"LOT{col}"
            lot = self._cement_lot(cement, label, lot_date, i)
            lot_cols[col] = lot
            self.cement_lots[label] = lot

        # What each lot stands at on the 1st is the file's figure. The
        # difference from what the app carried lands as one receipt-less
        # inflow on the 1st, the same way an opening count does -- and a lot
        # the file no longer lists is counted down to nothing.
        first_day = self.start
        prev = {col: (S.num(ws.cell(5, col).value) or 0.0) for col in lot_cols}
        listed = {lot.id for lot in lot_cols.values()}
        for col, standing in prev.items():
            delta = round(standing - self._lot_carried(lot_cols[col]), 3)
            if delta:
                self._lot_inflow(lot_cols[col], first_day, delta)
        for lot in self.db.query(m.MaterialLot).filter_by(material_id=cement.id):
            if lot.id in listed:
                continue
            carried = round(self._lot_carried(lot), 3)
            if carried:
                self._lot_inflow(lot, first_day, -carried)

        for r in range(5, ws.max_row + 1):
            d = S.date(ws.cell(r, 1).value)
            if not self._in_month(d):
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
                    # The lot grew: stock arriving, dated to the day it landed
                    # rather than folded into a total with no date on it.
                    self._lot_inflow(lot, d, -delta)
                elif cons is not None:
                    self.db.add(m.ConsumptionAllocation(
                        lot_id=lot.id, consumption_id=cons.id, qty=delta))
                    self.stats.add("lot_allocation")
                else:
                    # The lot shrank on a day with nothing consumed. 19 Jul
                    # moves 10 bags out of one bucket and into another with no
                    # consumption behind either; the register does not say what
                    # became of them, so neither does the app.
                    self._lot_inflow(lot, d, -delta)
        self.db.flush()

    def _lot_inflow(self, lot: m.MaterialLot, as_on: dt.date, qty: float) -> None:
        self.db.add(m.MaterialLotInflow(lot_id=lot.id, as_on=as_on, qty=qty))
        self.stats.add("lot_inflow")

    # -- Sheet17: receivables --------------------------------------------

    def import_receivables(self) -> None:
        """Sheet17 is a debtor list with multi-row customers.

        A row whose name column is blank -- or holds a number, which is how the
        register writes a charge before a part-payment -- continues the
        customer above it. Dropping those rows loses Rs 14,428.

        The list comes in blocks, each closed by a blank row and a subtotal;
        a blank row ends the customer being continued, so a subtotal is never
        taken for a part-payment. The grand total row ends the list.
        """
        ws = self.wb["Sheet17"]
        # The snapshot already embodies the month's credit sales, so it is
        # dated as the opening of the next period. That is what stops the
        # balance view counting those sales twice.
        as_on = self.end

        def footer(r: int) -> bool:
            cells = [self._label(ws, r, c) for c in range(1, 7)]
            return cells[5] == "DATE" or any("TOTAL" in c for c in cells)

        carry: m.Customer | None = None
        for r in range(4, self._last_row(ws, 4, footer) + 1):
            if all(ws.cell(r, c).value in (None, "") for c in range(1, 7)):
                carry = None
                continue
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

    # -- digital ledger: Sheet19 in July, Sheet24 in October ---------------

    def import_digital_ledger(self) -> None:
        ws = self.ledger_ws
        # The ledger ends at its totals: IN COME / EXPENSE in July, a row
        # that repeats the DATE headings in October.
        last = self._last_row(ws, 5, lambda r: (
            self._label(ws, r, 2).startswith("IN COME")
            or self._label(ws, r, 5).startswith("EXPENSE")
            or self._label(ws, r, 1) == "DATE"
            or self._label(ws, r, 7) == "BALANCE"))
        carry_in = carry_out = None
        for r in range(5, last + 1):
            carry_in = S.date_column(ws, r, 1, carry_in)
            desc = S.text(ws.cell(r, 2).value)
            amt = S.num(ws.cell(r, 3).value)
            if carry_in and desc and amt and self._in_month(carry_in):
                opening = "BALANCE" in desc.upper()
                self.db.add(m.DigitalLedger(
                    txn_date=carry_in, direction="IN",
                    entry_type="OPENING" if opening else "MANUAL",
                    description=desc, amount=amt))
                self.stats.add("ledger_in")
            carry_out = S.date_column(ws, r, 4, carry_out)
            desc = S.text(ws.cell(r, 5).value)
            amt = S.num(ws.cell(r, 6).value)
            if carry_out and desc and amt and self._in_month(carry_out):
                self.db.add(m.DigitalLedger(
                    txn_date=carry_out, direction="OUT", entry_type="MANUAL",
                    description=desc, amount=amt))
                self.stats.add("ledger_out")
        self.db.flush()

    # -- Sheet11, and the pallet register --------------------------------

    def import_misc_registers(self) -> None:
        ws = self.wb["Sheet11"]
        for r in range(1, ws.max_row + 1):
            d = S.date(ws.cell(r, 1).value)
            start = S.num(ws.cell(r, 2).value)
            stop = S.num(ws.cell(r, 3).value)
            if d is None or start is None or stop is None:
                continue
            if not self._in_month(d):
                continue
            if self.db.query(m.BikeMileage).filter_by(log_date=d).first():
                continue
            self.db.add(m.BikeMileage(log_date=d, start_km=start, stop_km=stop))
            self.stats.add("bike_mileage")

        ws = self.pallet_ws
        by_hand = self._label(ws, 3, 8) == "METHOD"
        for r in range(1, ws.max_row + 1):
            d = S.date(ws.cell(r, 1).value)
            if not self._in_month(d):
                continue
            if self.db.query(m.PalletMovement).filter_by(move_date=d).first():
                continue
            self.db.add(self._pallet_row(ws, r, d) if by_hand else
                        m.PalletMovement(
                            move_date=d,
                            stocking_area=S.num(ws.cell(r, 2).value),
                            production_area=S.num(ws.cell(r, 3).value),
                            today_prodn=S.num(ws.cell(r, 4).value),
                            pallets_stocked=S.num(ws.cell(r, 6).value),
                            shifted_from_stocking=S.num(ws.cell(r, 7).value),
                            production_balance=S.num(ws.cell(r, 8).value),
                            stocking_balance=S.num(ws.cell(r, 9).value)))
            self.stats.add("pallet_movement")
        self.db.flush()

    def _pallet_row(self, ws, r: int, d: dt.date) -> m.PalletMovement:
        """October's pallet register: the closing report in B..E, ordered
        TOTAL PRODN, PRODN AREA, STOCKING AREA, TOTAL as its footer heads
        them; then two rows a day -- STOCKING ONLY, SHIFTING & STOCKING --
        counted per hand, totalled in the column headed TOTAL. It has no
        area balances. Who stocked how many is not kept yet."""
        total = next((c for c in range(9, ws.max_column + 1)
                      if self._label(ws, 3, c) == "TOTAL"), 17)

        def method(row: int, *words: str) -> float | None:
            label = norm(self._label(ws, row, 8))
            return (S.num(ws.cell(row, total).value)
                    if any(w in label for w in words) else None)

        return m.PalletMovement(
            move_date=d,
            today_prodn=S.num(ws.cell(r, 2).value),
            production_area=S.num(ws.cell(r, 3).value),
            stocking_area=S.num(ws.cell(r, 4).value),
            pallets_stocked=method(r, "STOCKINGONLY"),
            # The register spells it SHFTING.
            shifted_from_stocking=method(r + 1, "SHFTING", "SHIFTING"))
