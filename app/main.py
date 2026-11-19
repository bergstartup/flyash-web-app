"""SMCP Kallakudi plant register.

Server-rendered FastAPI. One process serves the pages and owns the database;
there is no separate API to keep in step and no build step to run on the plant
machine.
"""

from __future__ import annotations

import calendar
import tempfile
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.db.models import ImportAnomaly
from app.db.session import engine
from app.excel.workbook import build_month
from app.reports import daily as daily_svc
from app.reports.period import latest_period
from app.reports.reconciliation import measure
from app.web.actor import set_actor
from app.web.render import db_session, page, parse_date
from app.web.routers.labour import router as labour_router
from app.web.routers.month_import import router as month_import_router
from app.web.routers.opening import router as opening_router
from app.web.routers.plant import router as plant_router
from app.web.routers.stock import router as stock_router

WEB = Path(__file__).parent / "web"

app = FastAPI(title="SMCP Kallakudi")
app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")
app.include_router(labour_router)
app.include_router(stock_router)
app.include_router(plant_router)
app.include_router(opening_router)
app.include_router(month_import_router)


@app.post("/actor")
def choose_actor(request: Request, name: str = Form(...)):
    response = RedirectResponse(
        request.headers.get("referer", "/"), status_code=303)
    set_actor(response, name)
    return response


@app.get("/")
def dashboard(request: Request, year: int = 0, month: int = 0,
             db=Depends(db_session)):
    if not year or not month:
        year, month = latest_period(db)
    period = f"{year:04d}-{month:02d}-01"
    y, m = f"{year:04d}", f"{month:02d}"
    stock = db.execute(text("""
        SELECT p.name, p.ledger_sheet, s.opening, s.production, s.sales, s.closing
        FROM stock_month_summary s JOIN product p ON p.id = s.product_id
        WHERE s.period_start = :period
        ORDER BY p.has_stock_ledger DESC, s.sales DESC"""), {"period": period}).all()
    salary = db.execute(text("""
        SELECT labour_name, wage_type, duty, salary, advance, extra,
               total_payment, outstanding
        FROM salary_month WHERE period_year = :y AND period_month = :m
        ORDER BY total_payment DESC"""), {"y": year, "m": month}).all()
    totals = db.execute(text("""
        SELECT (SELECT SUM(amount) FROM sales_register
                 WHERE strftime('%Y', sale_date) = :y AND strftime('%m', sale_date) = :m),
               (SELECT SUM(total_payment) FROM salary_month
                 WHERE period_year = :yi AND period_month = :mi),
               (SELECT SUM(amount) FROM material_receipt
                 WHERE strftime('%Y', receipt_date) = :y AND strftime('%m', receipt_date) = :m),
               (SELECT closing FROM digital_ledger_month WHERE period_start = :period),
               (SELECT SUM(outstanding) FROM receivable_balance),
               (SELECT COUNT(*) FROM import_anomaly WHERE severity = 'ERROR')
        """), {"y": y, "m": m, "yi": year, "mi": month, "period": period}).first()
    rupee = lambda v: f"Rs {float(v or 0):,.0f}"
    cards = [
        {"label": "Sales", "value": rupee(totals[0]), "note": "15 products"},
        {"label": "Wages", "value": rupee(totals[1]), "note": "16 on the roll"},
        {"label": "Materials in", "value": rupee(totals[2]), "note": "16 receipts"},
        {"label": "Digital balance", "value": rupee(totals[3]),
         "note": "opening + in - out"},
        {"label": "Receivables", "value": rupee(totals[4]),
         "note": "to be adjudicated, running balance"},
        {"label": "Register errors", "value": f"{totals[5]}",
         "note": "found in JULY.xlsx"},
    ]
    return page(request, "dashboard.html", page="dashboard",
                cards=cards, stock=stock, salary=salary, year=year, month=month)


@app.get("/daily")
def daily_round(request: Request, date: str = "", db=Depends(db_session)):
    day = parse_date(date)
    the_round = daily_svc.round_for(db, day)
    return page(request, "daily.html", page="daily", day=day,
               round=the_round)


@app.get("/export")
def export_workbook(month: str = "", db=Depends(db_session)):
    """The month's register as a workbook, straight to the browser.

    Built fresh on every click from the database, so a copy downloaded
    mid-month is simply that day's picture -- nothing is kept on the server.
    """
    if month:
        year, mon = int(month[:4]), int(month[5:7])
    else:
        year, mon = latest_period(db)
    name = f"{calendar.month_name[mon].upper()}-{year}.xlsx"
    with tempfile.TemporaryDirectory() as tmp, engine.connect() as conn:
        out = Path(tmp) / name
        build_month(conn, year, mon, out)
        body = out.read_bytes()
    return Response(body, media_type=(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/reconciliation")
def reconciliation(request: Request):
    with engine.connect() as conn:
        rows = measure(conn)
    return page(request, "reconciliation.html", page="reconciliation",
                rows=rows, total=len(rows),
                passed=sum(1 for r in rows if r.matches))


@app.get("/anomalies")
def anomalies(request: Request, db=Depends(db_session)):
    order = {"ERROR": 0, "WARN": 1, "INFO": 2}
    rows = sorted(db.query(ImportAnomaly).all(),
                  key=lambda a: (order.get(a.severity, 3), a.code))
    return page(request, "anomalies.html", page="anomalies", anomalies=rows)


@app.get("/health")
def health(db=Depends(db_session)):
    return {"status": "ok",
            "anomalies": db.query(ImportAnomaly).count()}
