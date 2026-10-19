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


class StubJev:
    """Stands in for the Jev client, returning a label we choose.

    It builds the SDK's own response types rather than duck-typing them, so if
    TypeSafe changes the shape of an answer these tests fail here - which is
    the point of a stub standing in for a provider.
    """

    def __init__(self, label: str = "ANSWERABLE", confidence: float = 0.99):
        self.label = label
        self.confidence = confidence
        self.questions: list[str] = []

    def system_one(self, state, questions):
        from typesafe_sdk import ChoiceAnswer, SystemOneResponse, Usage

        self.questions.append(state)
        others = (1.0 - self.confidence) / 2
        return SystemOneResponse(
            model="jev-stub",
            usage=Usage(input_tokens=1, output_tokens=0),
            answers={
                name: ChoiceAnswer(
                    choice=self.label,
                    confidence=self.confidence,
                    probabilities={
                        label: self.confidence if label == self.label else others
                        for label in ("ANSWERABLE", "AMBIGUOUS", "UNSUPPORTED")
                    },
                )
                for name in questions
            },
        )

    @property
    def call_count(self) -> int:
        return len(self.questions)


@pytest.fixture
def jev():
    """A classifier that calls everything answerable, so a test can focus on
    what happens after classification."""
    return StubJev()
