"""Audit trail. Every service write goes through :func:`record`."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.db.models import AuditLog


def record(db: Session, actor: str, table: str, record_id: int | None,
           action: str, old=None, new=None) -> AuditLog:
    entry = AuditLog(
        table_name=table, record_id=record_id, action=action,
        old_value=None if old is None else json.dumps(old, default=str),
        new_value=None if new is None else json.dumps(new, default=str),
        changed_by=actor)
    db.add(entry)
    return entry
