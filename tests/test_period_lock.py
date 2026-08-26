"""A settled month must not be editable without an explicit reopen."""

import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base
from app.services.period import PeriodLocked, close, guard, is_locked, reopen


@pytest.fixture()
def engine(tmp_path):
    e = create_engine(f"sqlite+pysqlite:///{tmp_path/'lock.db'}")
    Base.metadata.create_all(e)
    return e


@pytest.fixture()
def db(engine):
    with sessionmaker(bind=engine)() as s:
        yield s


def test_open_month_allows_writes(db):
    guard(db, dt.date(2026, 7, 15))


def test_closed_month_blocks_writes(db):
    close(db, "M.DIRECTOR", 2026, 7)
    assert is_locked(db, dt.date(2026, 7, 15))
    with pytest.raises(PeriodLocked):
        guard(db, dt.date(2026, 7, 15))


def test_reopen_requires_a_reason(db):
    close(db, "M.DIRECTOR", 2026, 7)
    with pytest.raises(ValueError):
        reopen(db, "OFFICE", 2026, 7, "  ")
    reopen(db, "OFFICE", 2026, 7, "IYAPPAN advance query")
    guard(db, dt.date(2026, 7, 15))


def test_reopen_is_audited(db):
    close(db, "M.DIRECTOR", 2026, 7)
    reopen(db, "OFFICE", 2026, 7, "stock recount")
    from app.db.models import AuditLog
    actions = [a.action for a in db.query(AuditLog).all()]
    assert actions == ["CLOSE", "REOPEN"]
    assert db.query(AuditLog).filter_by(action="REOPEN").one().changed_by == "OFFICE"


def test_close_survives_a_new_session(engine):
    """A web request gets a fresh session per call, unlike these tests'
    single-session fixture -- which is exactly why a missing commit() in
    close()/reopen() previously went undetected. This closes in one session
    and checks the lock from a completely different one."""
    Session = sessionmaker(bind=engine)
    with Session() as s1:
        close(s1, "M.DIRECTOR", 2026, 7)
    with Session() as s2:
        assert is_locked(s2, dt.date(2026, 7, 15))
        with pytest.raises(PeriodLocked):
            guard(s2, dt.date(2026, 7, 15))


def test_reopen_survives_a_new_session(engine):
    Session = sessionmaker(bind=engine)
    with Session() as s1:
        close(s1, "M.DIRECTOR", 2026, 7)
    with Session() as s2:
        reopen(s2, "OFFICE", 2026, 7, "cross-session check")
    with Session() as s3:
        assert not is_locked(s3, dt.date(2026, 7, 15))
