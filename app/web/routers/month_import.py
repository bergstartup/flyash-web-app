"""Import a month from its Excel register: upload, check, confirm.

The upload is checked by running the whole import and rolling it back, so
the page the operator confirms shows exactly what will change. Confirming
takes a backup of the database, then runs the same import for real.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse

from app.config import BACKUP_DIR, IMPORT_DIR
from app.services import month_import as month_svc
from app.services.period import PeriodLocked
from app.web.actor import current_actor
from app.web.render import db_session, page, redirect_with_error

router = APIRouter()

MAX_BYTES = 20 * 1024 * 1024
TOKEN = re.compile(r"^(\d{4})-(\d{2})-[0-9a-f]{32}$")


def _month(value: str) -> dt.date:
    """``2026-08`` from the month picker, or last month -- the one whose
    book has just been closed."""
    if not value:
        return (dt.date.today().replace(day=1) - dt.timedelta(days=1)).replace(day=1)
    try:
        return dt.date.fromisoformat(f"{value[:7]}-01")
    except ValueError:
        raise ValueError(f"'{value}' is not a month.") from None


@router.get("/import")
def import_form(request: Request, month: str = ""):
    try:
        as_on = _month(month)
    except ValueError:
        as_on = _month("")
    return page(request, "import.html", page="import", as_on=as_on,
                saved=request.query_params.get("saved"),
                backup=request.query_params.get("backup"),
                error=request.query_params.get("error"))


@router.post("/import")
async def import_check(request: Request, month: str = Form(""),
                       file: Optional[UploadFile] = File(None),
                       db=Depends(db_session)):
    url = f"/import?month={month[:7]}" if month else "/import"
    try:
        as_on = _month(month)
        if file is None or not file.filename:
            raise ValueError("Choose the month's Excel file to upload.")
        if not file.filename.lower().endswith(".xlsx"):
            raise ValueError("Upload the register as an .xlsx file.")
        data = await file.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError("That file is over 20 MB; a month's register "
                             "is far smaller.")
    except ValueError as exc:
        return redirect_with_error(url, exc)

    token = f"{as_on:%Y-%m}-{uuid.uuid4().hex}"
    IMPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = IMPORT_DIR / f"{token}.xlsx"
    path.write_bytes(data)
    try:
        result = month_svc.run(db, current_actor(request), path, as_on,
                               commit=False)
    except (ValueError, PeriodLocked) as exc:
        path.unlink(missing_ok=True)
        return redirect_with_error(url, exc)
    return page(request, "import.html", page="import", as_on=as_on,
                preview=result, token=token, filename=file.filename)


@router.post("/import/confirm")
def import_confirm(request: Request, token: str = Form(""),
                   db=Depends(db_session)):
    match = TOKEN.match(token)
    path = IMPORT_DIR / f"{token}.xlsx" if match else None
    if path is None or not path.exists():
        return redirect_with_error(
            "/import", ValueError("That upload has gone. Upload the file again."))
    as_on = dt.date(int(match.group(1)), int(match.group(2)), 1)
    url = f"/import?month={as_on:%Y-%m}"
    try:
        snapshot = month_svc.backup(db, BACKUP_DIR, as_on)
        month_svc.run(db, current_actor(request), path, as_on, commit=True)
    except (ValueError, PeriodLocked) as exc:
        return redirect_with_error(url, exc)
    return RedirectResponse(f"{url}&saved=1&backup={snapshot.name}",
                            status_code=303)
