"""Routes for the Phase 1 labour screens: masters, attendance, extra work,
advances, and salary settlement.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.reports import labour as reports
from app.services import advance as advance_svc
from app.services import attendance as attendance_svc
from app.services import extra_work as extra_work_svc
from app.services import extra_work_type as ewt_svc
from app.services import labour as labour_svc
from app.reports.period import latest_period
from app.services import salary as salary_svc
from app.services.period import PeriodLocked
from app.web.actor import current_actor
from app.web.render import db_session, page, parse_date, redirect_with_error

router = APIRouter()


# -- Labour master ------------------------------------------------------------

@router.get("/labour")
def labour_list(request: Request, db=Depends(db_session)):
    return page(request, "labour/list.html", page="labour",
                labour=labour_svc.list_labour(db, include_inactive=True),
                error=request.query_params.get("error"))


@router.post("/labour")
def labour_create(request: Request, name: str = Form(...),
                  wage_type: str = Form(...), rate: float = Form(...),
                  phone: str = Form(""), aadhar_last4: str = Form(""),
                  join_date: str = Form(""), db=Depends(db_session)):
    try:
        labour_svc.create(
            db, current_actor(request), name=name, wage_type=wage_type,
            rate=rate, phone=phone or None, aadhar_last4=aadhar_last4 or None,
            join_date=dt.date.fromisoformat(join_date) if join_date else None)
    except ValueError as exc:
        return redirect_with_error("/labour", exc)
    return RedirectResponse("/labour", status_code=303)


@router.post("/labour/{labour_id}/deactivate")
def labour_deactivate(request: Request, labour_id: int, db=Depends(db_session)):
    labour_svc.deactivate(db, current_actor(request), labour_id)
    return RedirectResponse("/labour", status_code=303)


@router.post("/labour/{labour_id}/reactivate")
def labour_reactivate(request: Request, labour_id: int, db=Depends(db_session)):
    labour_svc.reactivate(db, current_actor(request), labour_id)
    return RedirectResponse("/labour", status_code=303)


# -- Extra-work-type master ---------------------------------------------------

@router.get("/extra-work-types")
def ewt_list(request: Request, db=Depends(db_session)):
    types = ewt_svc.list_types(db, include_inactive=True)
    rates = {t.id: ewt_svc.current_rate(db, t.id) for t in types}
    return page(request, "labour/extra_work_types.html", page="extra-work-types",
                types=types, rates=rates,
                error=request.query_params.get("error"))


@router.post("/extra-work-types")
def ewt_create(request: Request, code: str = Form(...), name: str = Form(...),
               unit: str = Form(""), rate: float = Form(...),
               db=Depends(db_session)):
    try:
        ewt_svc.create(db, current_actor(request), code=code, name=name,
                       unit=unit or None, rate=rate)
    except ValueError as exc:
        return redirect_with_error("/extra-work-types", exc)
    return RedirectResponse("/extra-work-types", status_code=303)


@router.post("/extra-work-types/{type_id}/rate")
def ewt_set_rate(request: Request, type_id: int, rate: float = Form(...),
                 effective_from: str = Form(...), db=Depends(db_session)):
    try:
        ewt_svc.set_rate(db, current_actor(request), type_id, rate,
                         dt.date.fromisoformat(effective_from))
    except ValueError as exc:
        return redirect_with_error("/extra-work-types", exc)
    return RedirectResponse("/extra-work-types", status_code=303)


# -- Attendance ----------------------------------------------------------------

@router.get("/attendance")
def attendance_form(request: Request, date: str = "", db=Depends(db_session)):
    day = parse_date(date)
    roster = attendance_svc.roster_for_day(db, day)
    return page(request, "labour/attendance.html", page="attendance",
                day=day, roster=roster, shift_values=attendance_svc.SHIFT_VALUES,
                error=request.query_params.get("error"))


@router.post("/attendance")
async def attendance_save(request: Request, db=Depends(db_session)):
    form = await request.form()
    day = parse_date(form.get("date"))
    entries: dict[int, float] = {}
    for key, value in form.items():
        if key.startswith("shift_") and value != "":
            labour_id = int(key.removeprefix("shift_"))
            entries[labour_id] = float(value)
    try:
        attendance_svc.save_day(db, current_actor(request), day, entries)
    except ValueError as exc:
        return redirect_with_error(f"/attendance?date={day.isoformat()}", exc)
    return RedirectResponse(f"/attendance?date={day.isoformat()}", status_code=303)


# -- Extra work ------------------------------------------------------------

@router.get("/extra-work")
def extra_work_form(request: Request, date: str = "", db=Depends(db_session)):
    day = parse_date(date)
    return page(request, "labour/extra_work.html", page="extra-work", day=day,
                labour=labour_svc.list_labour(db),
                types=ewt_svc.list_types(db),
                entries=reports.extra_work_for_day(db, day),
                error=request.query_params.get("error"))


@router.post("/extra-work")
def extra_work_add(request: Request, date: str = Form(...),
                   labour_id: int = Form(...), work_type_id: int = Form(...),
                   qty: float = Form(...), remarks: str = Form(""),
                   db=Depends(db_session)):
    day = parse_date(date)
    try:
        extra_work_svc.add(db, current_actor(request), work_date=day,
                           labour_id=labour_id, work_type_id=work_type_id,
                           qty=qty, remarks=remarks or None)
    except (ValueError, PeriodLocked) as exc:
        return redirect_with_error(f"/extra-work?date={day.isoformat()}", exc)
    return RedirectResponse(f"/extra-work?date={day.isoformat()}", status_code=303)


@router.post("/extra-work/{entry_id}/delete")
def extra_work_delete(request: Request, entry_id: int, date: str = Form(...),
                      db=Depends(db_session)):
    extra_work_svc.delete(db, current_actor(request), entry_id)
    return RedirectResponse(f"/extra-work?date={date}", status_code=303)


@router.get("/extra-work/adjustments")
def extra_work_adjustments(request: Request, year: int = 0, month: int = 0,
                           db=Depends(db_session)):
    if not year or not month:
        year, month = latest_period(db)
    return page(request, "labour/adjustments.html", page="extra-work",
                year=year, month=month,
                rows=reports.extra_wage_adjustment_report(db, year, month))


# -- Advances ---------------------------------------------------------------

@router.get("/advances")
def advances_form(request: Request, date: str = "", db=Depends(db_session)):
    day = parse_date(date)
    return page(request, "labour/advances.html", page="advances", day=day,
                labour=labour_svc.list_labour(db),
                entries=reports.advances_for_day(db, day),
                error=request.query_params.get("error"))


@router.post("/advances")
def advances_add(request: Request, date: str = Form(...),
                 labour_id: int = Form(...), amount: float = Form(...),
                 payment_mode: str = Form(""), paid_to: str = Form(""),
                 remarks: str = Form(""), db=Depends(db_session)):
    day = parse_date(date)
    try:
        advance_svc.add(db, current_actor(request), adv_date=day,
                        labour_id=labour_id, amount=amount,
                        payment_mode=payment_mode or None,
                        paid_to=paid_to or None, remarks=remarks or None)
    except (ValueError, PeriodLocked) as exc:
        return redirect_with_error(f"/advances?date={day.isoformat()}", exc)
    return RedirectResponse(f"/advances?date={day.isoformat()}", status_code=303)


@router.post("/advances/{advance_id}/delete")
def advances_delete(request: Request, advance_id: int, date: str = Form(...),
                    db=Depends(db_session)):
    advance_svc.delete(db, current_actor(request), advance_id)
    return RedirectResponse(f"/advances?date={date}", status_code=303)


# -- Salary -------------------------------------------------------------------

@router.get("/salary")
def salary_view(request: Request, year: int = 0, month: int = 0,
                db=Depends(db_session)):
    if not year or not month:
        year, month = latest_period(db)
    return page(request, "labour/salary.html", page="salary",
                year=year, month=month,
                rows=reports.salary_month(db, year, month))


@router.post("/salary/{labour_id}/settle")
def salary_settle(request: Request, labour_id: int, year: int = Form(...),
                  month: int = Form(...), paid_on: str = Form(""),
                  authority: str = Form(""), status: str = Form(""),
                  outstanding: float = Form(0.0), db=Depends(db_session)):
    salary_svc.settle(
        db, current_actor(request), period_year=year, period_month=month,
        labour_id=labour_id,
        paid_on=dt.date.fromisoformat(paid_on) if paid_on else None,
        authority=authority or None, status=status or None,
        outstanding=outstanding)
    return RedirectResponse(f"/salary?year={year}&month={month}", status_code=303)
