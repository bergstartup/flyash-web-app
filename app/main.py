"""SMCP Kallakudi plant register.

Server-rendered FastAPI. One process serves the pages and owns the database;
there is no separate API to keep in step and no build step to run on the plant
machine.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from app.config import ACTORS
from app.db.models import ImportAnomaly
from app.db.session import SessionLocal, engine
from app.reports.reconciliation import measure
from app.web.actor import current_actor, set_actor

WEB = Path(__file__).parent / "web"

app = FastAPI(title="SMCP Kallakudi")
app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")
templates = Jinja2Templates(directory=str(WEB / "templates"))


def db_session():
    with SessionLocal() as session:
        yield session


def page(request: Request, name: str, **context):
    return templates.TemplateResponse(request, name, {
        "actor": current_actor(request), "actors": ACTORS, **context})


@app.post("/actor")
def choose_actor(request: Request, name: str = Form(...)):
    response = RedirectResponse(
        request.headers.get("referer", "/"), status_code=303)
    set_actor(response, name)
    return response


@app.get("/")
def dashboard(request: Request, db=Depends(db_session)):
    stock = db.execute(text("""
        SELECT p.name, p.ledger_sheet, s.opening, s.production, s.sales, s.closing
        FROM stock_month_summary s JOIN product p ON p.id = s.product_id
        ORDER BY p.has_stock_ledger DESC, s.sales DESC""")).all()
    salary = db.execute(text("""
        SELECT labour_name, wage_type, duty, salary, advance, extra,
               total_payment, outstanding
        FROM salary_month WHERE period_year = 2026 AND period_month = 7
        ORDER BY total_payment DESC""")).all()
    totals = db.execute(text("""
        SELECT (SELECT SUM(amount)        FROM sales_register),
               (SELECT SUM(total_payment) FROM salary_month),
               (SELECT SUM(amount)        FROM material_receipt),
               (SELECT closing            FROM digital_ledger_month),
               (SELECT SUM(outstanding)   FROM receivable_balance),
               (SELECT COUNT(*)           FROM import_anomaly
                 WHERE severity = 'ERROR')""")).first()
    rupee = lambda v: f"Rs {float(v or 0):,.0f}"
    cards = [
        {"label": "Sales", "value": rupee(totals[0]), "note": "15 products"},
        {"label": "Wages", "value": rupee(totals[1]), "note": "16 on the roll"},
        {"label": "Materials in", "value": rupee(totals[2]), "note": "16 receipts"},
        {"label": "Digital balance", "value": rupee(totals[3]),
         "note": "opening + in - out"},
        {"label": "Receivables", "value": rupee(totals[4]),
         "note": "to be adjudicated"},
        {"label": "Register errors", "value": f"{totals[5]}",
         "note": "found in JULY.xlsx"},
    ]
    return page(request, "dashboard.html", page="dashboard",
                cards=cards, stock=stock, salary=salary)


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
