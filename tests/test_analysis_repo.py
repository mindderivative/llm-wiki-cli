from pathlib import Path

import pytest

from llm_wiki.models import Analysis, QueueItem
from llm_wiki.storage import StorageEngine, get_analysis_row, insert_queue_row, upsert_analysis_row


@pytest.fixture
def storage(tmp_path: Path):
    with StorageEngine(tmp_path / "db.sqlite3") as engine:
        engine.init_schema()
        yield engine


def _insert_queue_item(storage: StorageEngine, title: str = "doc") -> QueueItem:
    item = QueueItem(title=title, raw_path=Path(f"raw/.staged/{title}.txt"))
    with storage.conn:
        return insert_queue_row(storage, item)


def test_get_analysis_row_returns_none_when_absent(storage: StorageEngine):
    item = _insert_queue_item(storage)

    assert get_analysis_row(storage, item.id) is None


def test_upsert_then_get_round_trips(storage: StorageEngine):
    item = _insert_queue_item(storage)
    analysis = Analysis(
        queue_item_id=item.id,
        summary="A concise summary.",
        entities=["Acme Corp"],
        concepts=["supply chain risk"],
    )

    with storage.conn:
        upsert_analysis_row(storage, analysis)

    fetched = get_analysis_row(storage, item.id)
    assert fetched.summary == "A concise summary."
    assert fetched.entities == ["Acme Corp"]
    assert fetched.concepts == ["supply chain risk"]


def test_upsert_overwrites_previous_attempt(storage: StorageEngine):
    item = _insert_queue_item(storage)
    with storage.conn:
        upsert_analysis_row(
            storage, Analysis(queue_item_id=item.id, summary="first attempt", entities=["A"])
        )
        upsert_analysis_row(
            storage, Analysis(queue_item_id=item.id, summary="retried attempt", entities=["B"], concepts=["C"])
        )

    fetched = get_analysis_row(storage, item.id)
    assert fetched.summary == "retried attempt"
    assert fetched.entities == ["B"]
    assert fetched.concepts == ["C"]


def test_analysis_defaults_to_empty_lists(storage: StorageEngine):
    item = _insert_queue_item(storage)
    with storage.conn:
        upsert_analysis_row(storage, Analysis(queue_item_id=item.id, summary="no entities or concepts"))

    fetched = get_analysis_row(storage, item.id)
    assert fetched.entities == []
    assert fetched.concepts == []
