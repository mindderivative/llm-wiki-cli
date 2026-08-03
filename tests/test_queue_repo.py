from pathlib import Path

import pytest

from llm_wiki.models import QueueItem, QueueStatus
from llm_wiki.storage import (
    StorageEngine,
    get_queue_row,
    insert_queue_row,
    list_pool,
    list_queue_rows,
)


@pytest.fixture
def storage(tmp_path: Path):
    with StorageEngine(tmp_path / "db.sqlite3") as engine:
        engine.init_schema()
        yield engine


def _insert(storage: StorageEngine, title: str, status: QueueStatus, **kwargs) -> QueueItem:
    item = QueueItem(title=title, raw_path=Path(f"raw/.staged/{title}.txt"), status=status, **kwargs)
    with storage.conn:
        return insert_queue_row(storage, item)


def test_get_queue_row_returns_none_for_missing_id(storage: StorageEngine):
    assert get_queue_row(storage, 999) is None


def test_get_queue_row_round_trip(storage: StorageEngine):
    inserted = _insert(storage, "note", QueueStatus.STAGED)

    fetched = get_queue_row(storage, inserted.id)

    assert fetched == inserted


def test_list_queue_rows_includes_terminal_items(storage: StorageEngine):
    _insert(storage, "done", QueueStatus.COMPLETED)
    _insert(storage, "broke", QueueStatus.FAILED, failed_at_step=QueueStatus.PARSING)

    items = list_queue_rows(storage)

    assert {i.title for i in items} == {"done", "broke"}


def test_list_queue_rows_status_filter(storage: StorageEngine):
    _insert(storage, "a", QueueStatus.STAGED)
    _insert(storage, "b", QueueStatus.QUEUED)

    items = list_queue_rows(storage, status=QueueStatus.QUEUED)

    assert [i.title for i in items] == ["b"]


def test_list_pool_excludes_terminal_items(storage: StorageEngine):
    _insert(storage, "pending", QueueStatus.STAGED)
    _insert(storage, "done", QueueStatus.COMPLETED)
    _insert(storage, "broke", QueueStatus.FAILED)

    items = list_pool(storage)

    assert [i.title for i in items] == ["pending"]


def test_list_pool_status_filter(storage: StorageEngine):
    _insert(storage, "a", QueueStatus.STAGED)
    _insert(storage, "b", QueueStatus.QUEUED)

    items = list_pool(storage, status=QueueStatus.STAGED)

    assert [i.title for i in items] == ["a"]


def test_list_pool_respects_limit(storage: StorageEngine):
    for i in range(5):
        _insert(storage, f"item-{i}", QueueStatus.STAGED)

    items = list_pool(storage, limit=2)

    assert len(items) == 2


def test_list_pool_orders_oldest_first(storage: StorageEngine):
    from llm_wiki.models import utcnow

    first = _insert(storage, "first", QueueStatus.STAGED)
    # Force distinct created_at so ordering is unambiguous rather than
    # relying on insert-order/rowid ties within the same microsecond.
    second_item = QueueItem(
        title="second",
        raw_path=Path("raw/.staged/second.txt"),
        status=QueueStatus.STAGED,
        created_at=utcnow(),
    )
    with storage.conn:
        second = insert_queue_row(storage, second_item)

    items = list_pool(storage)

    assert [i.id for i in items] == [first.id, second.id]
