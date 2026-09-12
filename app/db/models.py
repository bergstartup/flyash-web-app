"""SQLAlchemy schema for the SMCP Kallakudi plant register.

Rules this schema enforces, drawn from the build plan:

* Nothing derived is stored. There is no ``opening``, ``balance``, ``total`` or
  ``qty_remaining`` column anywhere -- those all live in ``views.sql``.
* Rates are snapshotted onto the transaction that used them, so changing a
  master rate never rewrites a settled month.
* Quantities are REAL. Half-bags of cement and half-shifts of attendance both
  occur in the source data.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------
# Masters
# --------------------------------------------------------------------------

class Labour(Base):
    __tablename__ = "labour"
    __table_args__ = (
        CheckConstraint("wage_type IN ('DAILY','FIXED')", name="ck_labour_wage_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    phone: Mapped[str | None] = mapped_column(String(20))
    # C: Aadhaar last four digits only -- see build plan section 12.
    aadhar_last4: Mapped[str | None] = mapped_column(String(4))
    wage_type: Mapped[str] = mapped_column(String(8))
    # rupees per shift when DAILY, rupees per month when FIXED
    rate: Mapped[float] = mapped_column(Float)
    join_date: Mapped[dt.date | None] = mapped_column(Date)
    exit_date: Mapped[dt.date | None] = mapped_column(Date)
    is_active: Mapped[int] = mapped_column(Integer, default=1)
    # Canonical order for the app's own entry screens -- distinct from
    # labour_sheet_order, which exists only to reproduce each *source* sheet's
    # own divergent ordering (Sheet2 and Sheet3 disagree) on export.
    display_order: Mapped[int] = mapped_column(Integer, default=0)

    sheet_orders: Mapped[list["LabourSheetOrder"]] = relationship(
        back_populates="labour", cascade="all, delete-orphan"
    )


class LabourSheetOrder(Base):
    """Per-sheet column order.

    Sheets 2, 3, 4 and 15 each list the labour in a different order -- MANJULA
    and PALANI SAMY swap places between Sheet2 and Sheet3 -- so a single global
    ``display_order`` cannot reproduce the workbook.
    """

    __tablename__ = "labour_sheet_order"
    __table_args__ = (UniqueConstraint("sheet", "labour_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    sheet: Mapped[str] = mapped_column(String(10))
    labour_id: Mapped[int] = mapped_column(ForeignKey("labour.id"))
    position: Mapped[int] = mapped_column(Integer)
    # the spelling this sheet uses, e.g. "NEERAJ . P" vs "NEERAJ.P"
    label: Mapped[str | None] = mapped_column(String(80))

    labour: Mapped[Labour] = relationship(back_populates="sheet_orders")


class Product(Base):
    __tablename__ = "product"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    unit: Mapped[str] = mapped_column(String(12), default="NOS")
    default_rate: Mapped[float | None] = mapped_column(Float)
    # C10: fifteen products sell, three have stock ledgers.
    has_stock_ledger: Mapped[int] = mapped_column(Integer, default=0)
    # which sheet renders this product's ledger, when it has one
    ledger_sheet: Mapped[str | None] = mapped_column(String(10))
    is_active: Mapped[int] = mapped_column(Integer, default=1)
    display_order: Mapped[int] = mapped_column(Integer, default=0)


class RawMaterial(Base):
    __tablename__ = "raw_material"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True)
    unit: Mapped[str] = mapped_column(String(12))
    default_rate: Mapped[float | None] = mapped_column(Float)
    is_lot_tracked: Mapped[int] = mapped_column(Integer, default=0)
    is_saleable: Mapped[int] = mapped_column(Integer, default=0)
    reorder_level: Mapped[float | None] = mapped_column(Float)
    # column letter in the wide Sheet10 layout
    sheet10_column: Mapped[str | None] = mapped_column(String(2))
    display_order: Mapped[int] = mapped_column(Integer, default=0)


class Customer(Base):
    __tablename__ = "customer"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    village: Mapped[str | None] = mapped_column(String(80))
    contact: Mapped[str | None] = mapped_column(String(40))
    customer_type: Mapped[str | None] = mapped_column(String(40))
    credit_limit: Mapped[float | None] = mapped_column(Float)
    added_on: Mapped[dt.date | None] = mapped_column(Date)
    is_active: Mapped[int] = mapped_column(Integer, default=1)


class Supplier(Base):
    __tablename__ = "supplier"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    contact: Mapped[str | None] = mapped_column(String(40))


class Vehicle(Base):
    __tablename__ = "vehicle"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    driver_name: Mapped[str | None] = mapped_column(String(60))
    contact: Mapped[str | None] = mapped_column(String(40))


class ExtraWorkType(Base):
    """C7: one row per extra-work category.

    ``is_adjustment`` marks the MIGRATION ADJUSTMENT bucket that absorbs the
    Rs 36,442 of July extra wages with no register behind them, so July
    reconciles to the rupee while the real categories are still unknown.
    """

    __tablename__ = "extra_work_type"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    unit: Mapped[str | None] = mapped_column(String(12))
    is_adjustment: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[int] = mapped_column(Integer, default=1)

    rates: Mapped[list["ExtraWorkRate"]] = relationship(
        back_populates="work_type", cascade="all, delete-orphan"
    )


class ExtraWorkRate(Base):
    __tablename__ = "extra_work_rate"
    __table_args__ = (UniqueConstraint("work_type_id", "effective_from"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    work_type_id: Mapped[int] = mapped_column(ForeignKey("extra_work_type.id"))
    rate: Mapped[float] = mapped_column(Float)
    effective_from: Mapped[dt.date] = mapped_column(Date)

    work_type: Mapped[ExtraWorkType] = relationship(back_populates="rates")


# --------------------------------------------------------------------------
# Power  (Sheet1)
# --------------------------------------------------------------------------

class PowerReading(Base):
    __tablename__ = "power_reading"

    id: Mapped[int] = mapped_column(primary_key=True)
    reading_date: Mapped[dt.date] = mapped_column(Date, unique=True)
    kwh: Mapped[float | None] = mapped_column(Float)
    kvah: Mapped[float | None] = mapped_column(Float)
    # July has TWO anchor rows, both 16 Jun with identical readings.
    is_eb_anchor: Mapped[int] = mapped_column(Integer, default=0)
    # 29 Jul carries the text LEAVE / SIR instead of a reading.
    reading_taken: Mapped[int] = mapped_column(Integer, default=1)
    remarks: Mapped[str | None] = mapped_column(Text)


# --------------------------------------------------------------------------
# Labour transactions  (Sheets 2, 3, 4, 14, 15)
# --------------------------------------------------------------------------

class Attendance(Base):
    __tablename__ = "attendance"
    __table_args__ = (
        UniqueConstraint("att_date", "labour_id"),
        # C4: the workbook uses exactly these five values.
        CheckConstraint(
            "shift_units IN (0, 0.5, 1, 1.5, 2)", name="ck_attendance_shift_units"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    att_date: Mapped[dt.date] = mapped_column(Date, index=True)
    labour_id: Mapped[int] = mapped_column(ForeignKey("labour.id"))
    shift_units: Mapped[float] = mapped_column(Float)
    remarks: Mapped[str | None] = mapped_column(Text)


class ExtraWork(Base):
    __tablename__ = "extra_work"

    id: Mapped[int] = mapped_column(primary_key=True)
    work_date: Mapped[dt.date] = mapped_column(Date, index=True)
    labour_id: Mapped[int] = mapped_column(ForeignKey("labour.id"))
    work_type_id: Mapped[int] = mapped_column(ForeignKey("extra_work_type.id"))
    qty: Mapped[float] = mapped_column(Float)
    rate: Mapped[float] = mapped_column(Float)  # snapshotted
    remarks: Mapped[str | None] = mapped_column(Text)


class Advance(Base):
    __tablename__ = "advance"
    __table_args__ = (
        CheckConstraint(
            "payment_mode IN ('CASH','DIGITAL')", name="ck_advance_payment_mode"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    adv_date: Mapped[dt.date] = mapped_column(Date, index=True)
    labour_id: Mapped[int] = mapped_column(ForeignKey("labour.id"))
    amount: Mapped[float] = mapped_column(Float)
    payment_mode: Mapped[str | None] = mapped_column(String(10))
    # C13: Sheet19 shows advances paid into third-party accounts.
    paid_to: Mapped[str | None] = mapped_column(String(80))
    remarks: Mapped[str | None] = mapped_column(Text)


class SalarySettlement(Base):
    """Only the non-derived part of Sheet15."""

    __tablename__ = "salary_settlement"
    __table_args__ = (UniqueConstraint("period_year", "period_month", "labour_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    period_year: Mapped[int] = mapped_column(Integer)
    period_month: Mapped[int] = mapped_column(Integer)
    labour_id: Mapped[int] = mapped_column(ForeignKey("labour.id"))
    paid_on: Mapped[dt.date | None] = mapped_column(Date)
    authority: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str | None] = mapped_column(String(20))
    outstanding: Mapped[float] = mapped_column(Float, default=0.0)
    # C15: FIXED staff (M.DIRECTOR) have duty with no attendance column.
    duty_override: Mapped[float | None] = mapped_column(Float)


# --------------------------------------------------------------------------
# Stock  (Sheets 5, 6, 7, 8, 9, 16)
# --------------------------------------------------------------------------

class StockOpening(Base):
    __tablename__ = "stock_opening"
    __table_args__ = (UniqueConstraint("product_id", "as_on"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id"))
    as_on: Mapped[dt.date] = mapped_column(Date)
    qty: Mapped[float] = mapped_column(Float)


class StockMonthHistory(Base):
    """Months that closed before the app existed (Sheet16, Jan 2024 onward).

    The guiding rule bans stored balances for anything the app can derive, and
    this does not breach it: there are no movements behind these months to
    derive from, so the roll-up is imported history in the same sense as
    ``stock_opening``. Any month the app owns is derived and wins over a row
    here.
    """

    __tablename__ = "stock_month_history"
    __table_args__ = (
        UniqueConstraint("product_id", "period_year", "period_month"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id"))
    period_year: Mapped[int] = mapped_column(Integer)
    period_month: Mapped[int] = mapped_column(Integer)
    opening: Mapped[float] = mapped_column(Float)
    production: Mapped[float] = mapped_column(Float)
    sales: Mapped[float] = mapped_column(Float)
    closing: Mapped[float] = mapped_column(Float)


class Production(Base):
    """One entry per product per day -- validation rule 5.

    The register splits some days into a DUTY and an AFTER 5 PM shift; those
    are summed here, by decision, so the daily figure stays the unit of entry.
    """

    __tablename__ = "production"
    __table_args__ = (UniqueConstraint("prod_date", "product_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    prod_date: Mapped[dt.date] = mapped_column(Date, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id"))
    qty: Mapped[float] = mapped_column(Float)
    # Where the day's production falls among that day's sales: the seq_no of
    # the sale it was posted after, 0 before any of them. The running balance
    # depends on it -- bricks come off the press during the day, not after it.
    # NULL means "posted last", which is what a new entry defaults to.
    after_seq: Mapped[int | None] = mapped_column(Integer)
    remarks: Mapped[str | None] = mapped_column(Text)


class Sale(Base):
    """C9: a sale is a header. Its products are ``sale_line`` rows.

    Sheet6 row 203 dittos both the date and the S.No to add a second product to
    the same trip, so a flat one-row-per-product model cannot round-trip.
    """

    __tablename__ = "sale"
    __table_args__ = (
        UniqueConstraint("sale_date", "seq_no"),
        CheckConstraint(
            "movement_type IN ('SALE','DAMAGE','INTERNAL','SAMPLE')",
            name="ck_sale_movement_type",
        ),
        CheckConstraint(
            "payment_mode IS NULL OR payment_mode IN ('CASH','CREDIT','DIGITAL')",
            name="ck_sale_payment_mode",
        ),
        # C3: only a real sale carries a customer and a payment mode.
        CheckConstraint(
            "movement_type = 'SALE' OR (payment_mode IS NULL AND customer_id IS NULL)",
            name="ck_sale_non_sale_has_no_money",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sale_date: Mapped[dt.date] = mapped_column(Date, index=True)
    seq_no: Mapped[int] = mapped_column(Integer)
    movement_type: Mapped[str] = mapped_column(String(10), default="SALE")
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey("vehicle.id"))
    # C11: what the operator writes today -- a village, a person, a landmark.
    destination: Mapped[str | None] = mapped_column(String(80))
    # C11: optional, and required only for CREDIT sales.
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customer.id"))
    payment_mode: Mapped[str | None] = mapped_column(String(10))
    collected_by: Mapped[str | None] = mapped_column(String(60))
    remarks: Mapped[str | None] = mapped_column(Text)

    lines: Mapped[list["SaleLine"]] = relationship(
        back_populates="sale", cascade="all, delete-orphan"
    )


class SaleLine(Base):
    __tablename__ = "sale_line"
    __table_args__ = (UniqueConstraint("sale_id", "line_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    sale_id: Mapped[int] = mapped_column(ForeignKey("sale.id"))
    line_no: Mapped[int] = mapped_column(Integer, default=1)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id"))
    qty: Mapped[float] = mapped_column(Float)
    rate: Mapped[float | None] = mapped_column(Float)
    # C8: entered, not generated. Five July rows have amount != qty * rate.
    amount: Mapped[float] = mapped_column(Float)
    amount_overridden: Mapped[int] = mapped_column(Integer, default=0)

    sale: Mapped[Sale] = relationship(back_populates="lines")


class Delivery(Base):
    """C2: Sheet7. 217 sales in July, 37 delivery notes."""

    __tablename__ = "delivery"

    id: Mapped[int] = mapped_column(primary_key=True)
    sale_id: Mapped[int | None] = mapped_column(ForeignKey("sale.id"))
    delivery_date: Mapped[dt.date] = mapped_column(Date, index=True)
    seq_no: Mapped[int | None] = mapped_column(Integer)
    name: Mapped[str | None] = mapped_column(String(80))
    particulars: Mapped[str | None] = mapped_column(String(80))
    identification: Mapped[str | None] = mapped_column(String(80))
    details: Mapped[str | None] = mapped_column(String(80))
    unload_location: Mapped[str | None] = mapped_column(String(80))
    contact: Mapped[str | None] = mapped_column(String(40))
    follow_up: Mapped[str | None] = mapped_column(String(20))
    page_no: Mapped[str | None] = mapped_column(String(20))
    driver_name: Mapped[str | None] = mapped_column(String(60))


# --------------------------------------------------------------------------
# Raw materials  (Sheets 10, 12)
# --------------------------------------------------------------------------

class MaterialOpening(Base):
    __tablename__ = "material_opening"
    __table_args__ = (UniqueConstraint("material_id", "as_on"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("raw_material.id"))
    as_on: Mapped[dt.date] = mapped_column(Date)
    qty: Mapped[float] = mapped_column(Float)


class MaterialReceipt(Base):
    __tablename__ = "material_receipt"

    id: Mapped[int] = mapped_column(primary_key=True)
    receipt_date: Mapped[dt.date] = mapped_column(Date, index=True)
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("supplier.id"))
    bill_no: Mapped[str | None] = mapped_column(String(40))
    vehicle_no: Mapped[str | None] = mapped_column(String(40))
    material_id: Mapped[int] = mapped_column(ForeignKey("raw_material.id"))
    qty: Mapped[float] = mapped_column(Float)
    rate: Mapped[float] = mapped_column(Float)
    amount: Mapped[float] = mapped_column(Float)
    amount_overridden: Mapped[int] = mapped_column(Integer, default=0)
    driver_name: Mapped[str | None] = mapped_column(String(60))
    payment_status: Mapped[str | None] = mapped_column(String(20), default="PENDING")


class MaterialLot(Base):
    """C1: no ``qty_remaining``, and no ``qty_in`` either.

    Remaining = ``SUM(inflows) - SUM(consumption_allocation.qty)``, derived in
    ``views.sql``. Sheet12's by-lot columns are a pivot of that view.
    """

    __tablename__ = "material_lot"

    id: Mapped[int] = mapped_column(primary_key=True)
    receipt_id: Mapped[int | None] = mapped_column(ForeignKey("material_receipt.id"))
    material_id: Mapped[int] = mapped_column(ForeignKey("raw_material.id"))
    lot_date: Mapped[dt.date | None] = mapped_column(Date)
    # Sheet12 labels two buckets "M / N" and "CON" rather than by date.
    label: Mapped[str] = mapped_column(String(20))
    display_order: Mapped[int] = mapped_column(Integer, default=0)

    inflows: Mapped[list["MaterialLotInflow"]] = relationship(
        back_populates="lot", cascade="all, delete-orphan"
    )


class MaterialLotInflow(Base):
    """Stock arriving into a lot, on the day it arrived.

    A lot is not filled once. Sheet12's "M / N" bucket takes stock on eight
    separate days, and the 19 Jul top-up lands on a day with no consumption
    at all, so an inflow cannot be modelled as a negative draw-down against a
    consumption. Without the date, every lot looks full from the first of the
    month and the by-lot balances are wrong for the days before it arrived.
    """

    __tablename__ = "material_lot_inflow"

    id: Mapped[int] = mapped_column(primary_key=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("material_lot.id"))
    receipt_id: Mapped[int | None] = mapped_column(ForeignKey("material_receipt.id"))
    as_on: Mapped[dt.date] = mapped_column(Date, index=True)
    qty: Mapped[float] = mapped_column(Float)

    lot: Mapped[MaterialLot] = relationship(back_populates="inflows")


class MaterialConsumption(Base):
    __tablename__ = "material_consumption"

    id: Mapped[int] = mapped_column(primary_key=True)
    cons_date: Mapped[dt.date] = mapped_column(Date, index=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("raw_material.id"))
    qty: Mapped[float] = mapped_column(Float)
    remarks: Mapped[str | None] = mapped_column(Text)

    allocations: Mapped[list["ConsumptionAllocation"]] = relationship(
        back_populates="consumption", cascade="all, delete-orphan"
    )


class ConsumptionAllocation(Base):
    """C1 / C12: the explicit lot split.

    The July data is not FIFO -- 02 Jul draws from two lots at once and 03 Jul
    from three -- so the allocation is stored, not inferred.

    A lot is drawn down by production consumption *or* by a sale, because
    cement is both a raw material and a saleable item. Exactly one of
    ``consumption_id`` and ``sale_line_id`` is set.

    ``qty`` may be negative: 04 Jul returns 4.5 bags to a lot with no receipt
    behind it, and the register has to be able to say so.
    """

    __tablename__ = "consumption_allocation"
    __table_args__ = (
        CheckConstraint(
            "(consumption_id IS NOT NULL) <> (sale_line_id IS NOT NULL)",
            name="ck_allocation_one_source",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("material_lot.id"))
    consumption_id: Mapped[int | None] = mapped_column(
        ForeignKey("material_consumption.id")
    )
    sale_line_id: Mapped[int | None] = mapped_column(ForeignKey("sale_line.id"))
    qty: Mapped[float] = mapped_column(Float)

    consumption: Mapped[MaterialConsumption | None] = relationship(
        back_populates="allocations"
    )


# --------------------------------------------------------------------------
# Money  (Sheets 17, 18, 19)
# --------------------------------------------------------------------------

class ReceivableOpening(Base):
    __tablename__ = "receivable_opening"
    __table_args__ = (UniqueConstraint("customer_id", "as_on"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"))
    as_on: Mapped[dt.date] = mapped_column(Date)
    amount: Mapped[float] = mapped_column(Float)
    note: Mapped[str | None] = mapped_column(Text)


class Receipt(Base):
    """Customer collections. Not in the original note -- required to close the
    debtor loop."""

    __tablename__ = "receipt"

    id: Mapped[int] = mapped_column(primary_key=True)
    receipt_date: Mapped[dt.date] = mapped_column(Date, index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customer.id"))
    sale_id: Mapped[int | None] = mapped_column(ForeignKey("sale.id"))
    amount: Mapped[float] = mapped_column(Float)
    payment_mode: Mapped[str | None] = mapped_column(String(10))
    collected_by: Mapped[str | None] = mapped_column(String(60))
    remarks: Mapped[str | None] = mapped_column(Text)


class DigitalLedger(Base):
    __tablename__ = "digital_ledger"
    __table_args__ = (
        CheckConstraint("direction IN ('IN','OUT')", name="ck_ledger_direction"),
        CheckConstraint(
            "entry_type IN ('OPENING','AUTO','MANUAL')", name="ck_ledger_entry_type"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    txn_date: Mapped[dt.date] = mapped_column(Date, index=True)
    direction: Mapped[str] = mapped_column(String(4))
    # C14: the Rs 14,273 "JUNE BALANCE" row is an OPENING entry, not income.
    entry_type: Mapped[str] = mapped_column(String(10), default="MANUAL")
    description: Mapped[str | None] = mapped_column(Text)
    amount: Mapped[float] = mapped_column(Float)
    source_table: Mapped[str | None] = mapped_column(String(40))
    source_id: Mapped[int | None] = mapped_column(Integer)


# --------------------------------------------------------------------------
# Misc registers  (Sheets 11, 20)
# --------------------------------------------------------------------------

class PalletMovement(Base):
    __tablename__ = "pallet_movement"

    id: Mapped[int] = mapped_column(primary_key=True)
    move_date: Mapped[dt.date] = mapped_column(Date, unique=True)
    stocking_area: Mapped[float | None] = mapped_column(Float)
    production_area: Mapped[float | None] = mapped_column(Float)
    today_prodn: Mapped[float | None] = mapped_column(Float)
    pallets_stocked: Mapped[float | None] = mapped_column(Float)
    shifted_from_stocking: Mapped[float | None] = mapped_column(Float)


class BikeMileage(Base):
    __tablename__ = "bike_mileage"

    id: Mapped[int] = mapped_column(primary_key=True)
    log_date: Mapped[dt.date] = mapped_column(Date, unique=True)
    start_km: Mapped[float | None] = mapped_column(Float)
    stop_km: Mapped[float | None] = mapped_column(Float)


# --------------------------------------------------------------------------
# Control
# --------------------------------------------------------------------------

class PeriodLock(Base):
    """C6: month-end close."""

    __tablename__ = "period_lock"
    __table_args__ = (UniqueConstraint("period_year", "period_month"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    period_year: Mapped[int] = mapped_column(Integer)
    period_month: Mapped[int] = mapped_column(Integer)
    locked_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    locked_by: Mapped[str | None] = mapped_column(String(60))
    reopened_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    reopened_by: Mapped[str | None] = mapped_column(String(60))
    reopen_reason: Mapped[str | None] = mapped_column(Text)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    table_name: Mapped[str] = mapped_column(String(40))
    record_id: Mapped[int | None] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(10))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    # fed by the actor seam -- a name, no password. See app/web/actor.py
    changed_by: Mapped[str | None] = mapped_column(String(60))
    changed_at: Mapped[dt.datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )


class ImportAnomaly(Base):
    """The error register from the build plan, recorded at import time."""

    __tablename__ = "import_anomaly"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20))
    sheet: Mapped[str | None] = mapped_column(String(20))
    cell: Mapped[str | None] = mapped_column(String(20))
    severity: Mapped[str] = mapped_column(String(10), default="ERROR")
    summary: Mapped[str] = mapped_column(Text)
    workbook_value: Mapped[str | None] = mapped_column(String(80))
    derived_value: Mapped[str | None] = mapped_column(String(80))
    difference: Mapped[str | None] = mapped_column(String(80))
    resolved: Mapped[int] = mapped_column(Integer, default=0)
    resolution_note: Mapped[str | None] = mapped_column(Text)
