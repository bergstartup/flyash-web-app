"""Daily production (the PRODN column of Sheets 5, 8 and 9).

One entry per product per day -- validation rule 5. Where the register split a
day into a DUTY and an AFTER 5 PM shift, the two are one entry here.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models import Product, Production
from app.services.audit import record
from app.services.period import guard


def for_day(db: Session, day: dt.date) -> list[Production]:
    return db.query(Production).filter_by(prod_date=day).order_by(
        Production.product_id).all()


def save_day(db: Session, actor: str, day: dt.date,
             quantities: dict[int, float | None]) -> list[Production]:
    """Record what each product made that day. A blank or zero removes the
    entry rather than storing a nought."""
    guard(db, day)
    existing = {p.product_id: p for p in for_day(db, day)}
    saved = []
    for product_id, qty in quantities.items():
        if db.get(Product, product_id) is None:
            raise ValueError(f"No such product: {product_id}")
        if qty is not None and qty < 0:
            raise ValueError("Production cannot be negative.")
        row = existing.get(product_id)
        if not qty:
            if row is not None:
                record(db, actor, "production", row.id, "DELETE",
                       old={"date": str(day), "product_id": product_id,
                            "qty": row.qty})
                db.delete(row)
            continue
        if row is None:
            row = Production(prod_date=day, product_id=product_id, qty=qty)
            db.add(row)
            db.flush()
            record(db, actor, "production", row.id, "CREATE",
                   new={"date": str(day), "product_id": product_id, "qty": qty})
        elif row.qty != qty:
            record(db, actor, "production", row.id, "UPDATE",
                   old={"qty": row.qty}, new={"qty": qty})
            row.qty = qty
        saved.append(row)
    db.commit()
    return saved


def set_position(db: Session, actor: str, production_id: int,
                 after_seq: int | None) -> Production:
    """Where in the day's sales this production was posted.

    Bricks come off the press during the day, so the running balance depends
    on it. New entries post last, which is the safe default; this exists for
    the day somebody needs to say otherwise.
    """
    row = db.get(Production, production_id)
    if row is None:
        raise ValueError(f"No such production entry: {production_id}")
    guard(db, row.prod_date)
    if after_seq is not None and after_seq < 0:
        raise ValueError("A position cannot be negative.")
    record(db, actor, "production", row.id, "POSITION",
           old={"after_seq": row.after_seq}, new={"after_seq": after_seq})
    row.after_seq = after_seq
    db.commit()
    return row


def ledger(db: Session, product_id: int, period_start: dt.date) -> list:
    """The product's stock ledger for a month, as the entry screen shows it."""
    return db.execute(text("""
        SELECT l.movement_date, l.kind, l.seq, l.movement_type, v.name,
               l.destination, l.opening, l.produced, l.issued, l.balance
        FROM stock_ledger l
        LEFT JOIN vehicle v ON v.id = l.vehicle_id
        WHERE l.product_id = :p AND l.period_start = :start
        ORDER BY l.movement_date, l.seq, l.kind, l.line_no"""),
        {"p": product_id, "start": period_start.isoformat()}).all()
