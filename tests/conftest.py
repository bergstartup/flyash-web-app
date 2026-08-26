import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import SOURCE_WORKBOOK  # noqa: E402
from app.db.session import engine       # noqa: E402


@pytest.fixture(scope="session")
def conn():
    """A connection to the imported July database.

    The suite runs against the real migrated database rather than a fixture,
    because the whole point of these tests is that the app reproduces the
    actual register.
    """
    with engine.connect() as c:
        yield c


@pytest.fixture(scope="session")
def workbook():
    return SOURCE_WORKBOOK
