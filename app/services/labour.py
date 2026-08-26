"""Labour master. View/Edit/New, soft delete only -- per the spec's Master
rules in section 6.2.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import Labour
from app.services.audit import record


def list_labour(db: Session, include_inactive: bool = False) -> list[Labour]:
    q = db.query(Labour)
    if not include_inactive:
        q = q.filter_by(is_active=1)
    return q.order_by(Labour.display_order, Labour.id).all()


def get(db: Session, labour_id: int) -> Labour | None:
    return db.get(Labour, labour_id)


def create(db: Session, actor: str, *, name: str, wage_type: str, rate: float,
           phone: str | None = None, aadhar_last4: str | None = None,
           join_date: dt.date | None = None) -> Labour:
    name = name.strip()
    if not name:
        raise ValueError("A labour record needs a name.")
    if wage_type not in ("DAILY", "FIXED"):
        raise ValueError("Wage type must be DAILY or FIXED.")
    if rate <= 0:
        raise ValueError("Rate must be positive.")
    if aadhar_last4 and (len(aadhar_last4) != 4 or not aadhar_last4.isdigit()):
        # C: store the last four digits only -- full Aadhaar carries a
        # securing obligation a LAN box with no auth cannot meet.
        raise ValueError("Store only the last 4 digits of Aadhaar.")
    next_order = (db.query(func.max(Labour.display_order)).scalar() or 0) + 1
    lab = Labour(name=name, wage_type=wage_type, rate=rate, phone=phone,
                aadhar_last4=aadhar_last4, join_date=join_date or dt.date.today(),
                is_active=1, display_order=next_order)
    db.add(lab)
    db.flush()
    record(db, actor, "labour", lab.id, "CREATE", new={
        "name": name, "wage_type": wage_type, "rate": rate})
    db.commit()
    return lab


def update(db: Session, actor: str, labour_id: int, **fields) -> Labour:
    lab = db.get(Labour, labour_id)
    if lab is None:
        raise ValueError(f"No such labour: {labour_id}")
    old = {"name": lab.name, "rate": lab.rate, "wage_type": lab.wage_type,
           "phone": lab.phone}
    for key in ("name", "phone", "aadhar_last4", "wage_type", "rate", "join_date"):
        if key in fields and fields[key] is not None:
            setattr(lab, key, fields[key])
    db.flush()
    record(db, actor, "labour", lab.id, "UPDATE", old=old, new=fields)
    db.commit()
    return lab


def deactivate(db: Session, actor: str, labour_id: int,
               exit_date: dt.date | None = None) -> Labour:
    lab = db.get(Labour, labour_id)
    if lab is None:
        raise ValueError(f"No such labour: {labour_id}")
    lab.is_active = 0
    lab.exit_date = exit_date or dt.date.today()
    db.flush()
    record(db, actor, "labour", lab.id, "DEACTIVATE",
           new={"exit_date": str(lab.exit_date)})
    db.commit()
    return lab


def reactivate(db: Session, actor: str, labour_id: int) -> Labour:
    lab = db.get(Labour, labour_id)
    if lab is None:
        raise ValueError(f"No such labour: {labour_id}")
    lab.is_active = 1
    lab.exit_date = None
    db.flush()
    record(db, actor, "labour", lab.id, "REACTIVATE")
    db.commit()
    return lab
