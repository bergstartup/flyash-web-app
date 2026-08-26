"""Engine, session factory and view management."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.config import DATABASE_URL
from app.db.models import Base

engine = create_engine(DATABASE_URL, future=True)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record):
    cur = dbapi_connection.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


SessionLocal = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)

VIEWS_SQL = Path(__file__).with_name("views.sql")


def _statements(sql: str) -> list[str]:
    """Split views.sql into executable statements.

    Leading ``--`` comment lines belong to the statement that follows them, not
    to the previous one, and must be stripped -- otherwise a comment block at
    the top of the file swallows the first DROP.
    """
    out = []
    for chunk in sql.split(";\n"):
        lines = [
            ln for ln in chunk.splitlines()
            if ln.strip() and not ln.lstrip().startswith("--")
        ]
        if lines:
            out.append("\n".join(lines))
    return out


def apply_views(conn, strict: bool = True) -> int:
    """(Re)create every derived view. Idempotent -- run after any migration.

    ``strict=False`` skips views whose base tables do not exist yet, which is
    what alembic autogenerate needs on an empty database.
    """
    applied = 0
    for stmt in _statements(VIEWS_SQL.read_text()):
        try:
            conn.execute(text(stmt))
        except OperationalError:
            if strict:
                raise
            continue
        applied += 1
    return applied


def init_db(drop: bool = False) -> None:
    if drop:
        with engine.begin() as conn:
            for name in _view_names(conn):
                conn.execute(text(f"DROP VIEW IF EXISTS {name}"))
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        apply_views(conn)


def _view_names(conn) -> list[str]:
    rows = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='view'")
    ).fetchall()
    return [r[0] for r in rows]
