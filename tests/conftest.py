from pathlib import Path

import pytest

from twyd.db import connect
from twyd.ingest import ingest

FIXTURE_DIR = Path(__file__).parent / "fixtures"
REAL_DATA_DIR = Path(__file__).parent.parent / "data"


@pytest.fixture(scope="session")
def fixture_db(tmp_path_factory) -> Path:
    """A database built from the tiny fixture CSVs. Fast and offline."""
    db_path = tmp_path_factory.mktemp("db") / "fixture.duckdb"
    ingest(FIXTURE_DIR, db_path)
    return db_path


@pytest.fixture
def conn(fixture_db):
    with connect(fixture_db) as c:
        yield c
