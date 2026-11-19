"""The opening figures screen: go-live, or a stock-take.

One form for the whole month, saved in one go, so a half-entered opening
never leaves stock carried and cash entered or the other way about.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from app.db.models import RawMaterial
from app.reports import opening as opening_rpt
from app.services import opening as opening_svc
from app.services import power as power_svc
from app.services.period import PeriodLocked
from app.web.actor import current_actor
from app.web.render import db_session, page, redirect_with_error

router = APIRouter()


def _month(value: str) -> dt.date:
    """``2026-10`` from the month picker, or this month."""
    if not value:
        return dt.date.today().replace(day=1)
    return dt.date.fromisoformat(f"{value[:7]}-01")


def _number(form, key: str) -> float | None:
    raw = (form.get(key) or "").replace(",", "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"'{raw}' is not a number.") from None


def _date(form, key: str, what: str) -> dt.date:
    raw = str(form.get(key) or "")
    if not raw:
        raise ValueError(f"Give the date of {what}.")
    return dt.date.fromisoformat(raw)


@router.get("/opening")
def opening_form(request: Request, month: str = "", db=Depends(db_session)):
    as_on = _month(month)
    return page(request, "opening.html", page="opening", as_on=as_on,
                last_day=as_on - dt.timedelta(days=1),
                figures=opening_rpt.for_month(db, as_on),
                lot_materials=db.query(RawMaterial).filter_by(
                    is_lot_tracked=1).order_by(RawMaterial.display_order).all(),
                saved=request.query_params.get("saved"),
                error=request.query_params.get("error"))


@router.post("/opening")
async def opening_save(request: Request, db=Depends(db_session)):
    form = await request.form()
    as_on = _month(str(form.get("month", "")))
    url = f"/opening?month={as_on:%Y-%m}"
    actor = current_actor(request)
    try:
        figures = opening_rpt.for_month(db, as_on)
        for line in figures.stock:
            opening_svc.set_stock(db, actor, as_on, line.id,
                                  _number(form, f"stock_{line.id}"))
        if figures.cement is not None:
            opening_svc.set_cement(db, actor, as_on, _number(form, "cement"))
        for line in figures.lots:
            opening_svc.set_lot(db, actor, as_on, line.id,
                                _number(form, f"lot_{line.id}"))
        new_qty = _number(form, "new_lot_qty")
        if new_qty is not None:
            lot_date = form.get("new_lot_date") or None
            opening_svc.add_lot(
                db, actor, as_on, int(form.get("new_lot_material")),
                str(form.get("new_lot_label", "")), new_qty,
                dt.date.fromisoformat(lot_date) if lot_date else None)
        opening_svc.set_ledger(db, actor, as_on, _number(form, "ledger"))

        anchor_kwh, anchor_kvah = (_number(form, "anchor_kwh"),
                                   _number(form, "anchor_kvah"))
        if (anchor_kwh is None) != (anchor_kvah is None):
            raise ValueError("The billing cycle's start needs both KWH and KVAH.")
        if anchor_kwh is not None:
            opening_svc.set_eb_anchor(
                db, actor, _date(form, "anchor_date", "the billing cycle's start"),
                anchor_kwh, anchor_kvah)

        close_kwh, close_kvah = (_number(form, "close_kwh"),
                                 _number(form, "close_kvah"))
        if (close_kwh is None) != (close_kvah is None):
            raise ValueError("The last meter reading needs both KWH and KVAH.")
        close_date = (_date(form, "close_date", "the last reading")
                      if close_kwh is not None else None)
        last = figures.last_reading
        unchanged = (last is not None and last.reading_date == close_date
                     and last.kwh == close_kwh and last.kvah == close_kvah)
        if close_kwh is not None and not unchanged:
            # take_reading commits, so it goes last: everything above is
            # validated by now and lands with it.
            power_svc.take_reading(
                db, actor, reading_date=close_date,
                kwh=close_kwh, kvah=close_kvah,
                remarks="LAST READING BEFORE GO-LIVE")
        db.commit()
    except (ValueError, TypeError, PeriodLocked,
            power_svc.MeterWentBackwards) as exc:
        db.rollback()
        return redirect_with_error(url, exc)
    return RedirectResponse(f"{url}&saved=1", status_code=303)
