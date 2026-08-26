"""The actor seam.

The deployment decision was no authentication: the app runs on the plant LAN
and anyone who can reach it can use it. That is workable, but it leaves
``audit_log.changed_by`` unable to answer *who*, so every write passes through
this one dependency and stamps a name chosen from a dropdown.

Adding real logins later means implementing :func:`current_actor` against a
session table. Nothing else in the app has to change.
"""

from __future__ import annotations

from fastapi import Request, Response

from app.config import ACTORS

COOKIE = "smcp_actor"
DEFAULT = ACTORS[0] if ACTORS else "OFFICE"


def current_actor(request: Request) -> str:
    """Who is making this change. Never raises -- there is no login to fail."""
    value = request.cookies.get(COOKIE)
    return value if value in ACTORS else DEFAULT


def set_actor(response: Response, name: str) -> str:
    chosen = name if name in ACTORS else DEFAULT
    response.set_cookie(COOKIE, chosen, max_age=60 * 60 * 24 * 365,
                        httponly=True, samesite="lax")
    return chosen
