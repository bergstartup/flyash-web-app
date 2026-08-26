"""Staff advance entry (Sheet4).

C13: Sheet19 shows advances paid into third-party accounts -- "UGAN ADVANCE /
CHANDAN KUMAR YADAV A/C" -- so the payee is captured separately from the
labour it is charged against.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from app.db.models import Advance, Labour
from app.services.audit import record
from app.services.period import guard


def for_day(db: Session, day: dt.date) -> list[Advance]:
    return db.query(Advance).filter_by(adv_date=day).order_by(Advance.id).all()


def add(db: Session, actor: str, *, adv_date: dt.date, labour_id: int,
        amount: float, payment_mode: str | None = None,
        paid_to: str | None = None, remarks: str | None = None) -> Advance:
    guard(db, adv_date)
    if amount <= 0:
        raise ValueError("Advance amount must be positive.")
    if db.get(Labour, labour_id) is None:
        raise ValueError(f"No such labour: {labour_id}")
    if payment_mode and payment_mode not in ("CASH", "DIGITAL"):
        raise ValueError("Payment mode must be CASH or DIGITAL.")
    adv = Advance(adv_date=adv_date, labour_id=labour_id, amount=amount,
                 payment_mode=payment_mode, paid_to=paid_to, remarks=remarks)
    db.add(adv)
    db.flush()
    record(db, actor, "advance", adv.id, "CREATE", new={
        "date": str(adv_date), "labour_id": labour_id, "amount": amount,
        "payment_mode": payment_mode, "paid_to": paid_to})
    # C6/plan Q6: advances auto-post to the digital ledger when paid digitally
    # -- exactly the triple-keying (Sheet4/Sheet15/Sheet19) the app removes.
    if payment_mode == "DIGITAL":
        from app.db.models import DigitalLedger
        desc = f"Advance to {db.get(Labour, labour_id).name}"
        if paid_to:
            desc += f" / {paid_to}"
        db.add(DigitalLedger(txn_date=adv_date, direction="OUT",
                             entry_type="AUTO", description=desc, amount=amount,
                             source_table="advance", source_id=adv.id))
    db.commit()
    return adv


def delete(db: Session, actor: str, advance_id: int) -> None:
    adv = db.get(Advance, advance_id)
    if adv is None:
        return
    guard(db, adv.adv_date)
    from app.db.models import DigitalLedger
    db.query(DigitalLedger).filter_by(
        source_table="advance", source_id=adv.id).delete()
    record(db, actor, "advance", adv.id, "DELETE",
           old={"date": str(adv.adv_date), "labour_id": adv.labour_id,
                "amount": adv.amount})
    db.delete(adv)
    db.commit()
