from pathlib import Path

import pytest

from llm_wiki.models import Chunk, QueueItem
from llm_wiki.storage import (
    StorageEngine,
    insert_chunk_row,
    insert_queue_row,
    list_chunks_for_queue_item,
)


@pytest.fixture
def storage(tmp_path: Path):
    with StorageEngine(tmp_path / "db.sqlite3") as engine:
        engine.init_schema()
        yield engine


def _insert_queue_item(storage: StorageEngine, title: str) -> QueueItem:
    item = QueueItem(title=title, raw_path=Path(f"raw/.staged/{title}.txt"))
    with storage.conn:
        return insert_queue_row(storage, item)


def _insert(storage: StorageEngine, queue_item_id: int, ordinal: int, title: str) -> Chunk:
    chunk = Chunk(queue_item_id=queue_item_id, ordinal=ordinal, title=title, content="body", word_count=1)
    return insert_chunk_row(storage, chunk)


def test_list_chunks_for_queue_item_empty(storage: StorageEngine):
    assert list_chunks_for_queue_item(storage, 999) == []


def test_list_chunks_for_queue_item_orders_by_ordinal(storage: StorageEngine):
    item = _insert_queue_item(storage, "doc")
    with storage.conn:
        _insert(storage, item.id, ordinal=2, title="third")
        _insert(storage, item.id, ordinal=0, title="first")
        _insert(storage, item.id, ordinal=1, title="second")

    chunks = list_chunks_for_queue_item(storage, item.id)

    assert [c.title for c in chunks] == ["first", "second", "third"]


def test_list_chunks_for_queue_item_excludes_other_items(storage: StorageEngine):
    mine = _insert_queue_item(storage, "mine-doc")
    other = _insert_queue_item(storage, "other-doc")
    with storage.conn:
        _insert(storage, mine.id, ordinal=0, title="mine")
        _insert(storage, other.id, ordinal=0, title="not mine")

    chunks = list_chunks_for_queue_item(storage, mine.id)

    assert [c.title for c in chunks] == ["mine"]
