"""Routes for the Phase 2 screens: products, production, sales and the ledger.

The sale screen is the point of the phase. One form writes one sale, and
Sheets 5, 6, 8, 9 and 16 all read it -- where today the same trip is keyed
into four sheets by hand.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from app.db.models import Customer, Vehicle
from app.db.session import SessionLocal
from app.reports.period import latest_period
from app.services import product as product_svc
from app.services import production as production_svc
from app.services import sale as sale_svc
from app.services.period import PeriodLocked
from app.web.actor import current_actor

router = APIRouter()
templates = Jinja2Templates(directory="app/web/templates")


def db_session():
    with SessionLocal() as session:
        yield session


def page(request: Request, name: str, **context):
    from app.config import ACTORS
    return templates.TemplateResponse(request, name, {
        "actor": current_actor(request), "actors": ACTORS, **context})


def _parse_date(value: str | None) -> dt.date:
    return dt.date.fromisoformat(value) if value else dt.date.today()


def _redirect_with_error(url: str, exc: Exception) -> RedirectResponse:
    from urllib.parse import quote
    sep = "&" if "?" in url else "?"
    return RedirectResponse(f"{url}{sep}error={quote(str(exc))}", status_code=303)


# -- Product master ---------------------------------------------------------

@router.get("/products")
def product_list(request: Request, db=Depends(db_session)):
    products = product_svc.listing(db, include_inactive=True)
    return page(request, "stock/products.html", page="products",
                products=products,
                sold={p.id: product_svc.times_sold(db, p.id) for p in products},
                ledger_sheets=product_svc.LEDGER_SHEETS,
                error=request.query_params.get("error"))


@router.post("/products")
def product_create(request: Request, code: str = Form(...), name: str = Form(...),
                   unit: str = Form("NOS"), default_rate: str = Form(""),
                   ledger_sheet: str = Form(""), db=Depends(db_session)):
    try:
        product_svc.create(db, current_actor(request), code=code, name=name,
                           unit=unit,
                           default_rate=float(default_rate) if default_rate else None,
                           ledger_sheet=ledger_sheet or None)
    except ValueError as exc:
        return _redirect_with_error("/products", exc)
    return RedirectResponse("/products", status_code=303)


@router.post("/products/{product_id}/rate")
def product_rate(request: Request, product_id: int, rate: str = Form(""),
                 db=Depends(db_session)):
    try:
        product_svc.set_rate(db, current_actor(request), product_id,
                             float(rate) if rate else None)
    except ValueError as exc:
        return _redirect_with_error("/products", exc)
    return RedirectResponse("/products", status_code=303)


@router.post("/products/{product_id}/deactivate")
def product_deactivate(request: Request, product_id: int, db=Depends(db_session)):
    product_svc.deactivate(db, current_actor(request), product_id)
    return RedirectResponse("/products", status_code=303)


@router.post("/products/{product_id}/reactivate")
def product_reactivate(request: Request, product_id: int, db=Depends(db_session)):
    product_svc.reactivate(db, current_actor(request), product_id)
    return RedirectResponse("/products", status_code=303)


# -- Production -------------------------------------------------------------

@router.get("/production")
def production_form(request: Request, date: str = "", db=Depends(db_session)):
    day = _parse_date(date)
    entered = {p.product_id: p.qty for p in production_svc.for_day(db, day)}
    return page(request, "stock/production.html", page="production", day=day,
                products=product_svc.listing(db), entered=entered,
                error=request.query_params.get("error"))


@router.post("/production")
async def production_save(request: Request, db=Depends(db_session)):
    form = await request.form()
    day = _parse_date(form.get("date"))
    quantities = {
        int(key.removeprefix("qty_")): float(value) if value else None
        for key, value in form.items() if key.startswith("qty_")}
    try:
        production_svc.save_day(db, current_actor(request), day, quantities)
    except (ValueError, PeriodLocked) as exc:
        return _redirect_with_error(f"/production?date={day.isoformat()}", exc)
    return RedirectResponse(f"/production?date={day.isoformat()}", status_code=303)


# -- Sales ------------------------------------------------------------------

@router.get("/sales")
def sales_form(request: Request, date: str = "", db=Depends(db_session)):
    day = _parse_date(date)
    products = product_svc.listing(db)
    return page(request, "stock/sales.html", page="sales", day=day,
                products=products,
                on_hand={p.id: sale_svc.available(db, p.id, day)
                         for p in products if p.has_stock_ledger},
                vehicles=db.query(Vehicle).order_by(Vehicle.name).all(),
                customers=db.query(Customer).filter_by(is_active=1)
                            .order_by(Customer.name).all(),
                movement_types=sale_svc.MOVEMENT_TYPES,
                payment_modes=sale_svc.PAYMENT_MODES,
                sales=_day_listing(db, day),
                next_seq=sale_svc.next_seq(db, day),
                warning=request.query_params.get("warning"),
                error=request.query_params.get("error"))


@router.post("/sales")
async def sales_create(request: Request, db=Depends(db_session)):
    form = await request.form()
    day = _parse_date(form.get("date"))
    url = f"/sales?date={day.isoformat()}"
    lines = []
    for index in range(1, 6):
        product_id = form.get(f"product_id_{index}")
        qty = form.get(f"qty_{index}")
        if not product_id or not qty:
            continue
        lines.append({"product_id": int(product_id), "qty": qty,
                      "rate": form.get(f"rate_{index}") or None,
                      "amount": form.get(f"amount_{index}") or None})
    movement = form.get("movement_type") or "SALE"
    try:
        _sale, warnings = sale_svc.create(
            db, current_actor(request), sale_date=day, lines=lines,
            movement_type=movement,
            vehicle_id=int(form["vehicle_id"]) if form.get("vehicle_id") else None,
            destination=form.get("destination") or None,
            customer_id=int(form["customer_id"]) if form.get("customer_id") else None,
            payment_mode=form.get("payment_mode") or None,
            collected_by=form.get("collected_by") or None)
    except (ValueError, PeriodLocked, sale_svc.StockShortfall) as exc:
        return _redirect_with_error(url, exc)
    if warnings:
        from urllib.parse import quote
        return RedirectResponse(f"{url}&warning={quote(' '.join(warnings))}",
                                status_code=303)
    return RedirectResponse(url, status_code=303)


@router.post("/sales/{sale_id}/delete")
def sales_delete(request: Request, sale_id: int, date: str = Form(...),
                 db=Depends(db_session)):
    try:
        sale_svc.delete(db, current_actor(request), sale_id)
    except (sale_svc.HasReceipts, PeriodLocked) as exc:
        return _redirect_with_error(f"/sales?date={date}", exc)
    return RedirectResponse(f"/sales?date={date}", status_code=303)


# -- Stock ledger -----------------------------------------------------------

@router.get("/stock")
def stock_ledger(request: Request, product_id: int = 0, year: int = 0,
                 month: int = 0, db=Depends(db_session)):
    if not year or not month:
        year, month = latest_period(db)
    ledgers = [p for p in product_svc.listing(db) if p.has_stock_ledger]
    if not product_id and ledgers:
        product_id = ledgers[0].id
    start = dt.date(year, month, 1)
    return page(request, "stock/ledger.html", page="stock", year=year,
                month=month, products=ledgers, product_id=product_id,
                rows=production_svc.ledger(db, product_id, start) if product_id else [],
                summary=_summary(db, product_id, start) if product_id else None)


def _day_listing(db, day: dt.date) -> list:
    return db.execute(text("""
        SELECT s.id, s.seq_no, s.movement_type, v.name, s.destination,
               s.payment_mode, c.name,
               GROUP_CONCAT(p.name || ' x ' || CAST(l.qty AS INT), ', '),
               SUM(l.amount)
        FROM sale s
        JOIN sale_line l ON l.sale_id = s.id
        JOIN product p ON p.id = l.product_id
        LEFT JOIN vehicle v ON v.id = s.vehicle_id
        LEFT JOIN customer c ON c.id = s.customer_id
        WHERE s.sale_date = :d
        GROUP BY s.id ORDER BY s.seq_no"""), {"d": day.isoformat()}).all()


def _summary(db, product_id: int, start: dt.date):
    return db.execute(text("""
        SELECT opening, production, total, sales, closing
        FROM stock_month_summary
        WHERE product_id = :p AND period_start = :start"""),
        {"p": product_id, "start": start.isoformat()}).first()
