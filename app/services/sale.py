"""Recording a sale.

One action, one row set. Sheet5, Sheet6, Sheet8, Sheet9 and Sheet16 are all
views of it -- today the same trip is keyed into four of them by hand.

A sale is a header with lines because a trip can carry two products, which
Sheet6 records by dittoing its S.No. Only a real sale carries money: a damage
or an internal issue moves stock and nothing else (C3).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.db.models import (Customer, Product, Receipt, Sale, SaleLine,
                           Vehicle)
from app.services.audit import record
from app.services.period import guard

MOVEMENT_TYPES = ("SALE", "DAMAGE", "INTERNAL", "SAMPLE")
PAYMENT_MODES = ("CASH", "CREDIT", "DIGITAL")


class StockShortfall(Exception):
    """Rule 1: a sale cannot issue stock that is not there."""


class HasReceipts(Exception):
    """Rule 12: a sale with money collected against it cannot be deleted."""


def available(db: Session, product_id: int, day: dt.date,
              ignoring: int | None = None) -> float:
    """Stock on hand at the end of ``day``: opening + production - issues.

    Scoped to the day's own month, because that is where the opening lives.
    ``ignoring`` leaves one sale out, so re-checking an edit does not count
    the quantity it is about to replace.
    """
    start = day.replace(day=1)
    return db.execute(text("""
        SELECT COALESCE((SELECT qty FROM stock_opening
                          WHERE product_id = :p AND as_on = :start), 0)
             + COALESCE((SELECT SUM(produced - issued) FROM stock_movement
                          WHERE product_id = :p AND d BETWEEN :start AND :day
                            AND (:ignoring IS NULL
                                 OR sale_id IS NULL OR sale_id <> :ignoring)), 0)
    """), {"p": product_id, "start": start.isoformat(), "day": day.isoformat(),
           "ignoring": ignoring}).scalar() or 0.0


def next_seq(db: Session, day: dt.date) -> int:
    highest = db.query(func.max(Sale.seq_no)).filter_by(sale_date=day).scalar()
    return (highest or 0) + 1


def for_day(db: Session, day: dt.date) -> list[Sale]:
    return db.query(Sale).filter_by(sale_date=day).order_by(Sale.seq_no).all()


def create(db: Session, actor: str, *, sale_date: dt.date, lines: list[dict],
           movement_type: str = "SALE", vehicle_id: int | None = None,
           destination: str | None = None, customer_id: int | None = None,
           payment_mode: str | None = None, collected_by: str | None = None,
           remarks: str | None = None) -> tuple[Sale, list[str]]:
    """Record a sale. Returns the sale and any warnings worth showing.

    Warnings do not block: a credit sale that takes a customer past their
    limit is the owner's call, not the app's.
    """
    guard(db, sale_date)
    if movement_type not in MOVEMENT_TYPES:
        raise ValueError(f"Movement type must be one of {MOVEMENT_TYPES}.")
    if not lines:
        raise ValueError("A sale needs at least one line.")
    if movement_type != "SALE":
        if payment_mode or customer_id:
            raise ValueError(
                f"A {movement_type} moves stock and nothing else -- it carries "
                "no customer and no payment.")
    else:
        if payment_mode and payment_mode not in PAYMENT_MODES:
            raise ValueError(f"Payment mode must be one of {PAYMENT_MODES}.")
        # A14: fifteen July credit sales name a destination and no customer,
        # so Rs 90,600 cannot be charged to anybody. Not again.
        if payment_mode == "CREDIT" and customer_id is None:
            raise ValueError(
                "A credit sale has to say who owes the money.")
    if customer_id is not None and db.get(Customer, customer_id) is None:
        raise ValueError(f"No such customer: {customer_id}")
    if vehicle_id is not None and db.get(Vehicle, vehicle_id) is None:
        raise ValueError(f"No such vehicle: {vehicle_id}")

    prepared = [_prepare(db, line) for line in lines]
    _check_stock(db, sale_date, prepared)

    sale = Sale(
        sale_date=sale_date, seq_no=next_seq(db, sale_date),
        movement_type=movement_type, vehicle_id=vehicle_id,
        destination=(destination or "").strip().upper() or None,
        customer_id=customer_id, payment_mode=payment_mode,
        collected_by=collected_by, remarks=remarks)
    db.add(sale)
    db.flush()
    for line_no, line in enumerate(prepared, start=1):
        db.add(SaleLine(sale_id=sale.id, line_no=line_no, **line))
    db.flush()
    record(db, actor, "sale", sale.id, "CREATE", new={
        "date": str(sale_date), "seq_no": sale.seq_no,
        "movement_type": movement_type, "payment_mode": payment_mode,
        "customer_id": customer_id, "destination": sale.destination,
        "lines": prepared})
    warnings = _warnings(db, sale, prepared)
    db.commit()
    return sale, warnings


def delete(db: Session, actor: str, sale_id: int) -> None:
    sale = db.get(Sale, sale_id)
    if sale is None:
        return
    guard(db, sale.sale_date)
    collected = db.query(func.count(Receipt.id)).filter_by(
        sale_id=sale.id).scalar()
    if collected:
        raise HasReceipts(
            f"{collected} receipt(s) are recorded against this sale. Reverse "
            "the collection first.")
    record(db, actor, "sale", sale.id, "DELETE",
           old={"date": str(sale.sale_date), "seq_no": sale.seq_no,
                "movement_type": sale.movement_type})
    db.delete(sale)
    db.commit()


def _prepare(db: Session, line: dict) -> dict:
    product_id = int(line["product_id"])
    product = db.get(Product, product_id)
    if product is None:
        raise ValueError(f"No such product: {product_id}")
    qty = float(line["qty"])
    if qty <= 0:
        raise ValueError(f"{product.name}: quantity must be positive.")
    rate = line.get("rate")
    rate = None if rate in (None, "") else float(rate)
    if rate is not None and rate < 0:
        raise ValueError(f"{product.name}: rate cannot be negative.")
    amount = line.get("amount")
    if amount in (None, ""):
        amount = (qty * rate) if rate is not None else 0.0
    amount = float(amount)
    # C8: the register sometimes charges something other than qty x rate --
    # a rounded lorry price. That is recorded, not corrected, but it is
    # recorded as a deliberate override.
    overridden = int(rate is not None and abs(qty * rate - amount) > 0.01)
    return {"product_id": product_id, "qty": qty, "rate": rate,
            "amount": amount, "amount_overridden": overridden}


def _check_stock(db: Session, day: dt.date, lines: list[dict]) -> None:
    """Rule 1, for the products that keep a ledger."""
    wanted: dict[int, float] = {}
    for line in lines:
        wanted[line["product_id"]] = wanted.get(line["product_id"], 0.0) \
            + line["qty"]
    for product_id, qty in wanted.items():
        product = db.get(Product, product_id)
        if not product.has_stock_ledger:
            continue
        on_hand = available(db, product_id, day)
        if qty > on_hand + 0.001:
            raise StockShortfall(
                f"{product.name}: {qty:,.0f} asked for, {on_hand:,.0f} on hand "
                f"on {day:%d %b %Y}.")


def _warnings(db: Session, sale: Sale, lines: list[dict]) -> list[str]:
    out = []
    if sale.payment_mode == "CREDIT" and sale.customer_id:
        customer = db.get(Customer, sale.customer_id)
        if customer.credit_limit:
            owed = db.execute(text(
                "SELECT COALESCE(outstanding, 0) FROM receivable_balance "
                "WHERE customer_id = :c"), {"c": customer.id}).scalar() or 0.0
            total = sum(line["amount"] for line in lines)
            if owed + total > customer.credit_limit:
                out.append(
                    f"{customer.name} would owe Rs {owed + total:,.0f} against "
                    f"a limit of Rs {customer.credit_limit:,.0f}.")
    if sale.movement_type == "SALE" and not sale.payment_mode:
        out.append("No payment mode recorded for this sale.")
    return out
