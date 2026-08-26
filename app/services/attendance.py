"""The attendance grid -- the highest-frequency screen in the app.

One tap per exception, one save: the grid defaults every active, already-
joined labour to a full shift, the operator only touches the rows that
differ, and one submit writes the whole day. This is also what satisfies
schema decision C5 -- a missing row means "not yet entered", distinct from an
explicit 0, only if the grid guarantees a row per active labour per day. It
must, so :func:`save_day` always writes one.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.models import Attendance, Labour
from app.services.audit import record
from app.services.period import guard

SHIFT_VALUES = (0, 0.5, 1, 1.5, 2)


@dataclass
class RosterRow:
    labour: Labour
    shift_units: float | None      # None = not entered yet
    joined: bool                   # False -> render '/', no input at all


def roster_for_day(db: Session, day: dt.date) -> list[RosterRow]:
    labour = (db.query(Labour).filter_by(is_active=1)
             .order_by(Labour.display_order, Labour.id).all())
    existing = {a.labour_id: a.shift_units for a in
               db.query(Attendance).filter_by(att_date=day).all()}
    out = []
    for lab in labour:
        joined = lab.join_date is None or lab.join_date <= day
        default = 1.0 if joined and lab.id not in existing else None
        out.append(RosterRow(lab, existing.get(lab.id, default), joined))
    return out


def save_day(db: Session, actor: str, day: dt.date,
            entries: dict[int, float]) -> int:
    """``entries`` maps labour_id -> shift_units for every joined, active
    labour on this day. Rows for anyone not joined yet are rejected, not
    silently dropped -- the '/' marker on the grid should have made entry
    impossible in the first place."""
    guard(db, day)
    joined_ids = {
        lab.id for lab in db.query(Labour).filter_by(is_active=1).all()
        if lab.join_date is None or lab.join_date <= day
    }
    written = 0
    for labour_id, shift_units in entries.items():
        if labour_id not in joined_ids:
            raise ValueError(
                f"Labour {labour_id} had not joined by {day}; cannot record "
                "attendance.")
        if shift_units not in SHIFT_VALUES:
            raise ValueError(
                f"Shift units must be one of {SHIFT_VALUES}, got {shift_units}.")
        row = db.query(Attendance).filter_by(
            att_date=day, labour_id=labour_id).first()
        if row is None:
            db.add(Attendance(att_date=day, labour_id=labour_id,
                              shift_units=shift_units))
            record(db, actor, "attendance", None, "CREATE",
                   new={"date": str(day), "labour_id": labour_id,
                        "shift_units": shift_units})
        elif row.shift_units != shift_units:
            old = row.shift_units
            row.shift_units = shift_units
            record(db, actor, "attendance", row.id, "UPDATE",
                   old={"shift_units": old}, new={"shift_units": shift_units})
        else:
            continue
        written += 1
    db.flush()
    db.commit()
    return written
