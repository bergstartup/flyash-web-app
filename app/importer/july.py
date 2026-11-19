"""One-time migration of JULY.xlsx into an empty database.

The reading is :class:`app.importer.workbook.WorkbookImporter`'s; on an empty
schema it creates every master as it goes. It is normally preceded by
``alembic upgrade head`` on a fresh file.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from app.importer.workbook import ImportStats, WorkbookImporter

JULY = dt.date(2026, 7, 1)


def import_july(session: Session, path) -> ImportStats:
    return WorkbookImporter(session, path, JULY).run()
