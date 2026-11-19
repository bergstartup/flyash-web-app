"""Product master (Sheet6's INVENTORY NAME column, and Sheets 5, 8 and 9).

Fifteen products sell; three of them keep a stock ledger. Which sheet renders
a product's ledger is recorded here, so a writer never has to name a product.
"""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import Product, Sale, SaleLine
from app.services.audit import record

LEDGER_SHEETS = ("Sheet5", "Sheet8", "Sheet9")


def listing(db: Session, include_inactive: bool = False) -> list[Product]:
    q = db.query(Product)
    if not include_inactive:
        q = q.filter(Product.is_active == 1)
    return q.order_by(Product.display_order, Product.id).all()


def create(db: Session, actor: str, *, code: str, name: str,
           unit: str = "NOS", default_rate: float | None = None,
           ledger_sheet: str | None = None) -> Product:
    code, name = (code or "").strip().upper(), (name or "").strip().upper()
    if not code or not name:
        raise ValueError("A product needs a code and a name.")
    if db.query(Product).filter(func.upper(Product.code) == code).first():
        raise ValueError(f"Code '{code}' is already in use.")
    if db.query(Product).filter(func.upper(Product.name) == name).first():
        raise ValueError(f"'{name}' is already a product.")
    if ledger_sheet and ledger_sheet not in LEDGER_SHEETS:
        raise ValueError(f"A stock ledger lives on one of {LEDGER_SHEETS}.")
    if ledger_sheet and db.query(Product).filter_by(
            ledger_sheet=ledger_sheet).first():
        raise ValueError(f"{ledger_sheet} already renders another product.")
    if default_rate is not None and default_rate <= 0:
        raise ValueError("A rate must be positive.")
    last = db.query(func.max(Product.display_order)).scalar()
    product = Product(
        code=code, name=name, unit=(unit or "NOS").strip().upper(),
        default_rate=default_rate, ledger_sheet=ledger_sheet,
        has_stock_ledger=int(bool(ledger_sheet)),
        display_order=(last or 0) + 1)
    db.add(product)
    db.flush()
    record(db, actor, "product", product.id, "CREATE",
           new={"code": code, "name": name, "ledger_sheet": ledger_sheet})
    db.commit()
    return product


def set_rate(db: Session, actor: str, product_id: int,
             rate: float | None) -> Product:
    """The rate a sale screen offers. A sale snapshots what it charged, so
    changing this never rewrites a sale already recorded."""
    product = db.get(Product, product_id)
    if product is None:
        raise ValueError(f"No such product: {product_id}")
    if rate is not None and rate <= 0:
        raise ValueError("A rate must be positive.")
    old = product.default_rate
    product.default_rate = rate
    record(db, actor, "product", product.id, "RATE",
           old={"default_rate": old}, new={"default_rate": rate})
    db.commit()
    return product


def deactivate(db: Session, actor: str, product_id: int) -> Product:
    """Soft delete -- a product that has ever been sold keeps its history."""
    product = db.get(Product, product_id)
    if product is None:
        raise ValueError(f"No such product: {product_id}")
    product.is_active = 0
    record(db, actor, "product", product.id, "DEACTIVATE")
    db.commit()
    return product


def reactivate(db: Session, actor: str, product_id: int) -> Product:
    product = db.get(Product, product_id)
    if product is None:
        raise ValueError(f"No such product: {product_id}")
    product.is_active = 1
    record(db, actor, "product", product.id, "REACTIVATE")
    db.commit()
    return product


def times_sold(db: Session, product_id: int) -> int:
    return db.query(func.count(SaleLine.id)).filter_by(
        product_id=product_id).scalar() or 0


def last_charged(db: Session, product_id: int) -> tuple[float, object] | None:
    """The rate the latest real sale of a product charged, and its date --
    what the plant is actually charging, next to what the screen offers."""
    return db.query(SaleLine.rate, Sale.sale_date).join(Sale).filter(
        SaleLine.product_id == product_id, Sale.movement_type == "SALE",
        SaleLine.rate.is_not(None)).order_by(
        Sale.sale_date.desc(), Sale.seq_no.desc(), SaleLine.line_no.desc()).first()
