"""Routes for the Phase 5 screens: the power book and the digital ledger."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.reports.period import latest_period
from app.services import power as power_svc
from app.services.period import PeriodLocked
from app.web.actor import current_actor
from app.web.render import db_session, page, parse_date, redirect_with_error

router = APIRouter()


@router.get("/power")
def power_form(request: Request, date: str = "", year: int = 0, month: int = 0,
               db=Depends(db_session)):
    day = parse_date(date)
    if not year or not month:
        year, month = day.year, day.month
    return page(request, "plant/power.html", page="power", day=day,
                year=year, month=month,
                previous=power_svc.previous_reading(db, day),
                rows=power_svc.readings_for_month(db, year, month),
                error=request.query_params.get("error"))


@router.post("/power")
def power_save(request: Request, date: str = Form(...), kwh: str = Form(""),
               kvah: str = Form(""), remarks: str = Form(""),
               db=Depends(db_session)):
    day = parse_date(date)
    url = f"/power?date={day.isoformat()}"
    try:
        power_svc.take_reading(db, current_actor(request), reading_date=day,
                               kwh=float(kwh) if kwh else None,
                               kvah=float(kvah) if kvah else None,
                               remarks=remarks or None)
    except (ValueError, PeriodLocked, power_svc.MeterWentBackwards) as exc:
        return redirect_with_error(url, exc)
    return RedirectResponse(url, status_code=303)


@router.get("/ledger")
def ledger_page(request: Request, year: int = 0, month: int = 0,
                db=Depends(db_session)):
    if not year or not month:
        year, month = latest_period(db)
    return page(request, "plant/ledger.html", page="ledger",
                year=year, month=month,
                entries=power_svc.ledger_for_month(db, year, month),
                summary=power_svc.ledger_summary(db, year, month),
                directions=power_svc.DIRECTIONS,
                error=request.query_params.get("error"))


@router.post("/ledger")
def ledger_post(request: Request, txn_date: str = Form(...),
                direction: str = Form(...), amount: float = Form(...),
                description: str = Form(""), db=Depends(db_session)):
    day = parse_date(txn_date)
    url = f"/ledger?year={day.year}&month={day.month}"
    try:
        power_svc.post(db, current_actor(request), txn_date=day,
                       direction=direction, amount=amount,
                       description=description or None)
    except (ValueError, PeriodLocked) as exc:
        return redirect_with_error(url, exc)
    return RedirectResponse(url, status_code=303)


@router.post("/ledger/{entry_id}/delete")
def ledger_delete(request: Request, entry_id: int, year: int = Form(...),
                  month: int = Form(...), db=Depends(db_session)):
    url = f"/ledger?year={year}&month={month}"
    try:
        power_svc.delete(db, current_actor(request), entry_id)
    except (ValueError, PeriodLocked) as exc:
        return redirect_with_error(url, exc)
    return RedirectResponse(url, status_code=303)
