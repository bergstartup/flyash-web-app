"""Routes for the stock screens.

Phase 2: products, production, sales and the stock ledger. Phase 4: raw
material receipts and cement consumption.

The sale screen is the point of the phase. One form writes one sale, and
Sheets 5, 6, 8, 9 and 16 all read it -- where today the same trip is keyed
into four sheets by hand.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import text

from app.db.models import Customer, Vehicle
from app.reports.period import latest_period
from app.services import material as material_svc
from app.services import product as product_svc
from app.services import production as production_svc
from app.services import sale as sale_svc
from app.services.period import PeriodLocked
from app.web.actor import current_actor
from app.web.render import db_session, page, parse_date, redirect_with_error

router = APIRouter()


# -- Product master ---------------------------------------------------------

@router.get("/products")
def product_list(request: Request, db=Depends(db_session)):
    products = product_svc.listing(db, include_inactive=True)
    return page(request, "stock/products.html", page="products",
                products=products,
                sold={p.id: product_svc.times_sold(db, p.id) for p in products},
                last={p.id: product_svc.last_charged(db, p.id) for p in products},
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
        return redirect_with_error("/products", exc)
    return RedirectResponse("/products", status_code=303)


@router.post("/products/{product_id}/rate")
def product_rate(request: Request, product_id: int, rate: str = Form(""),
                 db=Depends(db_session)):
    try:
        product_svc.set_rate(db, current_actor(request), product_id,
                             float(rate) if rate else None)
    except ValueError as exc:
        return redirect_with_error("/products", exc)
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
    day = parse_date(date)
    entered = {p.product_id: p.qty for p in production_svc.for_day(db, day)}
    return page(request, "stock/production.html", page="production", day=day,
                products=product_svc.listing(db), entered=entered,
                error=request.query_params.get("error"))


@router.post("/production")
async def production_save(request: Request, db=Depends(db_session)):
    form = await request.form()
    day = parse_date(form.get("date"))
    quantities = {
        int(key.removeprefix("qty_")): float(value) if value else None
        for key, value in form.items() if key.startswith("qty_")}
    try:
        production_svc.save_day(db, current_actor(request), day, quantities)
    except (ValueError, PeriodLocked) as exc:
        return redirect_with_error(f"/production?date={day.isoformat()}", exc)
    return RedirectResponse(f"/production?date={day.isoformat()}", status_code=303)


# -- Sales ------------------------------------------------------------------

@router.get("/sales")
def sales_form(request: Request, date: str = "", db=Depends(db_session)):
    day = parse_date(date)
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
    day = parse_date(form.get("date"))
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
        return redirect_with_error(url, exc)
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
        return redirect_with_error(f"/sales?date={date}", exc)
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


# -- Raw materials and cement (Phase 4) -------------------------------------

def _optional_number(value: str, what: str) -> float | None:
    value = (value or "").replace(",", "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        raise ValueError(f"{what}: '{value}' is not a number.") from None


@router.get("/raw-materials")
def material_master(request: Request, db=Depends(db_session)):
    all_materials = material_svc.materials(db)
    return page(request, "stock/raw_materials.html", page="raw-materials",
                materials=all_materials,
                suppliers=material_svc.suppliers(db),
                counts=material_svc.deliveries_count(db),
                locked={m.id for m in all_materials
                        if material_svc.has_history(db, m.id)},
                error=request.query_params.get("error"))


@router.post("/raw-materials")
def material_create(request: Request, name: str = Form(...),
                    unit: str = Form(...), default_rate: str = Form(""),
                    reorder_level: str = Form(""), is_lot_tracked: str = Form(""),
                    is_saleable: str = Form(""), db=Depends(db_session)):
    try:
        material_svc.create_material(
            db, current_actor(request), name=name, unit=unit,
            default_rate=_optional_number(default_rate, "Rate"),
            reorder_level=_optional_number(reorder_level, "Reorder level"),
            is_lot_tracked=bool(is_lot_tracked), is_saleable=bool(is_saleable))
    except ValueError as exc:
        return redirect_with_error("/raw-materials", exc)
    return RedirectResponse("/raw-materials", status_code=303)


@router.post("/raw-materials/{material_id}")
def material_update(request: Request, material_id: int, unit: str = Form(...),
                    default_rate: str = Form(""), reorder_level: str = Form(""),
                    is_lot_tracked: str = Form(""), is_saleable: str = Form(""),
                    db=Depends(db_session)):
    try:
        material_svc.update_material(
            db, current_actor(request), material_id, unit=unit,
            default_rate=_optional_number(default_rate, "Rate"),
            reorder_level=_optional_number(reorder_level, "Reorder level"),
            is_lot_tracked=bool(is_lot_tracked), is_saleable=bool(is_saleable))
    except ValueError as exc:
        return redirect_with_error("/raw-materials", exc)
    return RedirectResponse("/raw-materials", status_code=303)


@router.post("/suppliers")
def supplier_create(request: Request, name: str = Form(...),
                    contact: str = Form(""), db=Depends(db_session)):
    try:
        material_svc.create_supplier(db, current_actor(request), name=name,
                                     contact=contact)
    except ValueError as exc:
        return redirect_with_error("/raw-materials", exc)
    return RedirectResponse("/raw-materials#suppliers", status_code=303)


@router.post("/suppliers/{supplier_id}")
def supplier_update(request: Request, supplier_id: int, name: str = Form(...),
                    contact: str = Form(""), db=Depends(db_session)):
    try:
        material_svc.update_supplier(db, current_actor(request), supplier_id,
                                     name=name, contact=contact)
    except ValueError as exc:
        return redirect_with_error("/raw-materials", exc)
    return RedirectResponse("/raw-materials#suppliers", status_code=303)


@router.get("/materials")
def materials_form(request: Request, date: str = "", db=Depends(db_session)):
    day = parse_date(date)
    all_materials = material_svc.materials(db)
    return page(request, "stock/materials.html", page="materials", day=day,
                materials=all_materials,
                material_names={m.id: m.name for m in all_materials},
                suppliers=material_svc.suppliers(db),
                receipts=material_svc.receipts_for_day(db, day),
                lots_by_material={
                    m.id: material_svc.lots(db, m.id)
                    for m in all_materials if m.is_lot_tracked},
                low=material_svc.below_reorder(db),
                error=request.query_params.get("error"))


@router.post("/materials")
def materials_receive(request: Request, date: str = Form(...),
                      material_id: int = Form(...), qty: float = Form(...),
                      rate: float = Form(...), amount: str = Form(""),
                      supplier_id: str = Form(""), bill_no: str = Form(""),
                      vehicle_no: str = Form(""), driver_name: str = Form(""),
                      payment_status: str = Form("PENDING"),
                      lot_id: str = Form(""), db=Depends(db_session)):
    day = parse_date(date)
    try:
        material_svc.receive(
            db, current_actor(request), receipt_date=day,
            material_id=material_id, qty=qty, rate=rate,
            amount=float(amount) if amount else None,
            supplier_id=int(supplier_id) if supplier_id else None,
            bill_no=bill_no or None, vehicle_no=vehicle_no or None,
            driver_name=driver_name or None, payment_status=payment_status,
            lot_id=int(lot_id) if lot_id else None)
    except (ValueError, PeriodLocked) as exc:
        return redirect_with_error(f"/materials?date={day.isoformat()}", exc)
    return RedirectResponse(f"/materials?date={day.isoformat()}", status_code=303)


@router.get("/consumption")
def consumption_form(request: Request, date: str = "", material_id: int = 0,
                     db=Depends(db_session)):
    day = parse_date(date)
    tracked = [m for m in material_svc.materials(db) if m.is_lot_tracked]
    if not material_id and tracked:
        material_id = tracked[0].id
    return page(request, "stock/consumption.html", page="consumption", day=day,
                materials=tracked, material_id=material_id,
                lots=[(lot, material_svc.remaining(db, lot[0], day))
                      for lot in material_svc.lots(db, material_id)]
                     if material_id else [],
                entries=material_svc.consumption_for_day(db, day),
                error=request.query_params.get("error"))


@router.post("/consumption")
async def consumption_save(request: Request, db=Depends(db_session)):
    form = await request.form()
    day = parse_date(form.get("date"))
    material_id = int(form["material_id"])
    url = f"/consumption?date={day.isoformat()}&material_id={material_id}"
    allocations = {
        int(key.removeprefix("lot_")): float(value)
        for key, value in form.items() if key.startswith("lot_") and value}
    try:
        material_svc.consume(db, current_actor(request), cons_date=day,
                             material_id=material_id, allocations=allocations,
                             remarks=form.get("remarks") or None)
    except (ValueError, PeriodLocked, material_svc.LotShortfall) as exc:
        return redirect_with_error(url, exc)
    return RedirectResponse(url, status_code=303)
