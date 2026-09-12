"""Shared plumbing for the routers: a session, a page, a date, an error.

Each router used to carry its own copy of all four. They are the same four.
"""

from __future__ import annotations

import datetime as dt
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import ACTORS
from app.db.session import SessionLocal
from app.web import nav
from app.web.actor import current_actor

templates = Jinja2Templates(directory="app/web/templates")


def db_session():
    with SessionLocal() as session:
        yield session


def page(request: Request, name: str, **context):
    """Render a screen. Every screen gets the actor and the navigation."""
    current = context.get("page")
    return templates.TemplateResponse(request, name, {
        "actor": current_actor(request), "actors": ACTORS,
        "nav_groups": nav.GROUPS, "nav_active": nav.active(current),
        **context})


def parse_date(value: str | None) -> dt.date:
    return dt.date.fromisoformat(value) if value else dt.date.today()


def redirect_with_error(url: str, exc: Exception) -> RedirectResponse:
    """Send the operator back where they were, with the reason it was refused."""
    separator = "&" if "?" in url else "?"
    return RedirectResponse(f"{url}{separator}error={quote(str(exc))}",
                            status_code=303)
