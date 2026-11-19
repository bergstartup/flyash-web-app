"""Raw material receipts and cement consumption (Sheets 10 and 12).

Consumption is the interesting one. Cement is drawn from named lots, and the
draw is *not* FIFO -- 2 July takes from two lots at once and 3 July from three
-- so the split is entered, not inferred. What the app does enforce is that
the split adds up and that no lot is drawn past what is in it.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.db.models import (ConsumptionAllocation, MaterialConsumption,
                           MaterialLot, MaterialLotInflow, MaterialReceipt,
                           RawMaterial, Supplier)
from app.services.audit import record
from app.services.period import guard


class LotShortfall(Exception):
    """A lot cannot give up more than it holds."""


class AllocationMismatch(Exception):
    """The by-lot split has to add up to the quantity consumed."""


def materials(db: Session) -> list[RawMaterial]:
    return db.query(RawMaterial).order_by(
        RawMaterial.display_order, RawMaterial.id).all()


def suppliers(db: Session) -> list[Supplier]:
    return db.query(Supplier).order_by(Supplier.name).all()


# -- the master: materials and suppliers ------------------------------------

def _clean(value: str | None) -> str:
    return " ".join((value or "").split()).upper()


def _positive_or_none(value: float | None, what: str) -> float | None:
    if value is not None and value < 0:
        raise ValueError(f"{what} cannot be negative.")
    return value or None


def has_history(db: Session, material_id: int) -> bool:
    """Whether anything has been recorded against a material yet."""
    return any(db.query(model).filter_by(material_id=material_id).first()
               for model in (MaterialReceipt, MaterialConsumption, MaterialLot))


def create_material(db: Session, actor: str, *, name: str, unit: str,
                    default_rate: float | None = None,
                    reorder_level: float | None = None,
                    is_lot_tracked: bool = False,
                    is_saleable: bool = False) -> RawMaterial:
    name, unit = _clean(name), _clean(unit)
    if not name or not unit:
        raise ValueError("A material needs a name and a unit.")
    if db.query(RawMaterial).filter(func.upper(RawMaterial.name) == name).first():
        raise ValueError(f"'{name}' is already a material.")
    last = db.query(func.max(RawMaterial.display_order)).scalar()
    material = RawMaterial(
        name=name, unit=unit,
        default_rate=_positive_or_none(default_rate, "A rate"),
        reorder_level=_positive_or_none(reorder_level, "A reorder level"),
        is_lot_tracked=int(is_lot_tracked), is_saleable=int(is_saleable),
        display_order=(last or 0) + 1)
    db.add(material)
    db.flush()
    record(db, actor, "raw_material", material.id, "CREATE", new={
        "name": name, "unit": unit, "default_rate": material.default_rate,
        "reorder_level": material.reorder_level,
        "is_lot_tracked": is_lot_tracked, "is_saleable": is_saleable})
    db.commit()
    return material


def update_material(db: Session, actor: str, material_id: int, *, unit: str,
                    default_rate: float | None, reorder_level: float | None,
                    is_lot_tracked: bool, is_saleable: bool) -> RawMaterial:
    """Everything but the name. Sheet12 and the cement opening find their
    materials by name, and a delivery keeps the material it was entered
    against, so a rename would quietly re-label history."""
    material = db.get(RawMaterial, material_id)
    if material is None:
        raise ValueError(f"No such material: {material_id}")
    unit = _clean(unit)
    if not unit:
        raise ValueError(f"{material.name} needs a unit.")
    if bool(material.is_lot_tracked) != bool(is_lot_tracked) \
            and has_history(db, material_id):
        raise ValueError(
            f"{material.name} already has deliveries or lots, so whether it is "
            "kept in lots cannot change - its balances would stop adding up.")
    old = {"unit": material.unit, "default_rate": material.default_rate,
           "reorder_level": material.reorder_level,
           "is_lot_tracked": bool(material.is_lot_tracked),
           "is_saleable": bool(material.is_saleable)}
    material.unit = unit
    material.default_rate = _positive_or_none(default_rate, "A rate")
    material.reorder_level = _positive_or_none(reorder_level, "A reorder level")
    material.is_lot_tracked = int(is_lot_tracked)
    material.is_saleable = int(is_saleable)
    new = {"unit": material.unit, "default_rate": material.default_rate,
           "reorder_level": material.reorder_level,
           "is_lot_tracked": bool(is_lot_tracked),
           "is_saleable": bool(is_saleable)}
    if new != old:
        record(db, actor, "raw_material", material.id, "UPDATE", old=old, new=new)
    db.commit()
    return material


def create_supplier(db: Session, actor: str, *, name: str,
                    contact: str | None = None) -> Supplier:
    name = _clean(name)
    if not name:
        raise ValueError("A supplier needs a name.")
    if db.query(Supplier).filter(func.upper(Supplier.name) == name).first():
        raise ValueError(f"'{name}' is already a supplier.")
    supplier = Supplier(name=name, contact=(contact or "").strip() or None)
    db.add(supplier)
    db.flush()
    record(db, actor, "supplier", supplier.id, "CREATE",
           new={"name": name, "contact": supplier.contact})
    db.commit()
    return supplier


def update_supplier(db: Session, actor: str, supplier_id: int, *, name: str,
                    contact: str | None) -> Supplier:
    """A supplier can be renamed: deliveries point at it by id, so the old
    ones follow the new name, which is what fixing a misspelling wants."""
    supplier = db.get(Supplier, supplier_id)
    if supplier is None:
        raise ValueError(f"No such supplier: {supplier_id}")
    name = _clean(name)
    if not name:
        raise ValueError("A supplier needs a name.")
    clash = db.query(Supplier).filter(func.upper(Supplier.name) == name,
                                      Supplier.id != supplier_id).first()
    if clash:
        raise ValueError(f"'{name}' is already a supplier.")
    old = {"name": supplier.name, "contact": supplier.contact}
    supplier.name = name
    supplier.contact = (contact or "").strip() or None
    new = {"name": supplier.name, "contact": supplier.contact}
    if new != old:
        record(db, actor, "supplier", supplier.id, "UPDATE", old=old, new=new)
    db.commit()
    return supplier


def deliveries_count(db: Session) -> dict[str, dict[int, int]]:
    """How many deliveries each material and each supplier has -- shown on
    the master so nobody wonders whether a row is in use."""
    by_material = dict(db.query(MaterialReceipt.material_id,
                                func.count(MaterialReceipt.id)).group_by(
        MaterialReceipt.material_id).all())
    by_supplier = dict(db.query(MaterialReceipt.supplier_id,
                                func.count(MaterialReceipt.id)).filter(
        MaterialReceipt.supplier_id.is_not(None)).group_by(
        MaterialReceipt.supplier_id).all())
    return {"material": by_material, "supplier": by_supplier}


def receipts_for_day(db: Session, day: dt.date) -> list[MaterialReceipt]:
    return db.query(MaterialReceipt).filter_by(receipt_date=day).order_by(
        MaterialReceipt.id).all()


def receive(db: Session, actor: str, *, receipt_date: dt.date, material_id: int,
            qty: float, rate: float, amount: float | None = None,
            supplier_id: int | None = None, bill_no: str | None = None,
            vehicle_no: str | None = None, driver_name: str | None = None,
            payment_status: str = "PENDING",
            lot_id: int | None = None) -> MaterialReceipt:
    """Record material arriving.

    A lot-tracked material lands in a lot; ``lot_id`` says which, and the
    arrival is dated so the by-lot balance is right for the days before it.
    """
    guard(db, receipt_date)
    material = db.get(RawMaterial, material_id)
    if material is None:
        raise ValueError(f"No such material: {material_id}")
    if qty <= 0:
        raise ValueError("Received quantity must be positive.")
    if rate < 0:
        raise ValueError("Rate cannot be negative.")
    if amount in (None, ""):
        amount = qty * rate
    amount = float(amount)
    # C8: the register sometimes carries a rounded price rather than qty x rate.
    overridden = int(abs(qty * rate - amount) > 0.01)
    receipt = MaterialReceipt(
        receipt_date=receipt_date, supplier_id=supplier_id, bill_no=bill_no,
        vehicle_no=vehicle_no, material_id=material_id, qty=qty, rate=rate,
        amount=amount, amount_overridden=overridden, driver_name=driver_name,
        payment_status=payment_status)
    db.add(receipt)
    db.flush()
    if lot_id is not None:
        lot = db.get(MaterialLot, lot_id)
        if lot is None or lot.material_id != material_id:
            raise ValueError("That lot does not hold this material.")
        db.add(MaterialLotInflow(lot_id=lot_id, receipt_id=receipt.id,
                                 as_on=receipt_date, qty=qty))
    elif material.is_lot_tracked:
        # A lot-tracked material with nowhere to go would vanish from the
        # by-lot block, so it opens a lot of its own, named for the day.
        lot = MaterialLot(material_id=material_id, lot_date=receipt_date,
                          label=receipt_date.isoformat(),
                          display_order=_next_lot_order(db, material_id))
        db.add(lot)
        db.flush()
        db.add(MaterialLotInflow(lot_id=lot.id, receipt_id=receipt.id,
                                 as_on=receipt_date, qty=qty))
    record(db, actor, "material_receipt", receipt.id, "CREATE", new={
        "date": str(receipt_date), "material_id": material_id, "qty": qty,
        "rate": rate, "amount": amount})
    db.commit()
    return receipt


def lots(db: Session, material_id: int) -> list:
    """Every lot of a material with what is left in it."""
    return db.execute(text("""
        SELECT lot_id, label, lot_date, qty_in, qty_remaining
        FROM lot_balance WHERE material_id = :m
        ORDER BY display_order"""), {"m": material_id}).all()


def remaining(db: Session, lot_id: int, on: dt.date) -> float:
    return db.execute(text("""
        SELECT COALESCE((SELECT SUM(qty) FROM material_lot_inflow
                          WHERE lot_id = :l AND as_on <= :d), 0.0)
             - COALESCE((SELECT SUM(ca.qty) FROM consumption_allocation ca
                          JOIN material_consumption mc ON mc.id = ca.consumption_id
                          WHERE ca.lot_id = :l AND mc.cons_date <= :d), 0.0)
    """), {"l": lot_id, "d": on.isoformat()}).scalar() or 0.0


def consume(db: Session, actor: str, *, cons_date: dt.date, material_id: int,
            allocations: dict[int, float], remarks: str | None = None
            ) -> MaterialConsumption:
    """Record material going into production, split across the lots it came
    from. The quantity is the sum of the split -- there is nothing to type
    twice and nothing to disagree with itself."""
    guard(db, cons_date)
    material = db.get(RawMaterial, material_id)
    if material is None:
        raise ValueError(f"No such material: {material_id}")
    split = {lot_id: qty for lot_id, qty in allocations.items() if qty}
    if not split:
        raise ValueError("Nothing consumed.")
    total = round(sum(split.values()), 3)
    if total <= 0:
        raise ValueError("Consumed quantity must be positive.")
    for lot_id, qty in split.items():
        lot = db.get(MaterialLot, lot_id)
        if lot is None or lot.material_id != material_id:
            raise ValueError("That lot does not hold this material.")
        if qty > 0:
            available = remaining(db, lot_id, cons_date)
            if qty > available + 0.001:
                raise LotShortfall(
                    f"{lot.label}: {qty:,.2f} asked for, {available:,.2f} left "
                    f"on {cons_date:%d %b %Y}.")

    consumption = MaterialConsumption(
        cons_date=cons_date, material_id=material_id, qty=total, remarks=remarks)
    db.add(consumption)
    db.flush()
    for lot_id, qty in split.items():
        db.add(ConsumptionAllocation(lot_id=lot_id,
                                     consumption_id=consumption.id, qty=qty))
    record(db, actor, "material_consumption", consumption.id, "CREATE", new={
        "date": str(cons_date), "material_id": material_id, "qty": total,
        "allocations": {str(k): v for k, v in split.items()}})
    db.commit()
    return consumption


def consumption_for_day(db: Session, day: dt.date) -> list:
    return db.execute(text("""
        SELECT c.id, rm.name, c.qty, c.remarks,
               GROUP_CONCAT(ml.label || ' ' || CAST(ca.qty AS TEXT), ', ')
        FROM material_consumption c
        JOIN raw_material rm ON rm.id = c.material_id
        LEFT JOIN consumption_allocation ca ON ca.consumption_id = c.id
        LEFT JOIN material_lot ml ON ml.id = ca.lot_id
        WHERE c.cons_date = :d
        GROUP BY c.id ORDER BY c.id"""), {"d": day.isoformat()}).all()


def below_reorder(db: Session) -> list:
    """Rule 10: materials that have fallen under their reorder level."""
    return db.execute(text("""
        SELECT rm.name, rm.reorder_level,
               COALESCE(SUM(lb.qty_remaining), 0) AS on_hand
        FROM raw_material rm
        LEFT JOIN lot_balance lb ON lb.material_id = rm.id
        WHERE rm.reorder_level IS NOT NULL
        GROUP BY rm.id HAVING on_hand < rm.reorder_level
        ORDER BY rm.display_order""")).all()


def _next_lot_order(db: Session, material_id: int) -> int:
    highest = db.query(func.max(MaterialLot.display_order)).filter_by(
        material_id=material_id).scalar()
    return (highest or 0) + 1
