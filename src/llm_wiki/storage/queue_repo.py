"""`QueueItem` <-> `queue` row (de)serialization.

Centralized here — not duplicated per-package — because both `stager`
(`STAGED`/`FAILED`) and `ingest` (`QUEUED` onward) read and write the
same `queue` table, via the same `StorageEngine` connection.

Nothing in this module calls `.commit()`. Callers own the transaction
boundary: wrap a call (or several) in `with storage.conn:` — the same
pattern `StorageEngine.init_schema()` already uses — so that a
multi-statement step (e.g. `ingest.atomize()`'s chunk inserts + its
terminal status write) commits atomically, per INGEST_PLAN.md §3's
atomicity contract. A single call outside any `with` block still works,
but won't be durable until something commits it.
"""

from __future__ import annotations

from llm_wiki.models import QueueItem
from llm_wiki.storage.engine import StorageEngine

_COLUMNS = (
    "title",
    "raw_path",
    "archive_path",
    "status",
    "error",
    "failed_at_step",
    "created_at",
    "updated_at",
)


def _params(item: QueueItem) -> tuple:
    return (
        item.title,
        str(item.raw_path),
        str(item.archive_path) if item.archive_path else None,
        item.status.value,
        item.error,
        item.failed_at_step.value if item.failed_at_step else None,
        item.created_at.isoformat(),
        item.updated_at.isoformat(),
    )


def insert_queue_row(storage: StorageEngine, item: QueueItem) -> QueueItem:
    """Insert `item` as a new `queue` row, returning it with `id` populated."""
    placeholders = ", ".join("?" for _ in _COLUMNS)
    cursor = storage.conn.execute(
        f"INSERT INTO queue ({', '.join(_COLUMNS)}) VALUES ({placeholders});",
        _params(item),
    )
    return item.model_copy(update={"id": cursor.lastrowid})


def update_queue_row(storage: StorageEngine, item: QueueItem) -> QueueItem:
    """Overwrite the existing `queue` row matching `item.id` with `item`'s fields."""
    if item.id is None:
        raise ValueError("cannot update a QueueItem that was never inserted (id is None)")
    set_clause = ", ".join(f"{col} = ?" for col in _COLUMNS)
    storage.conn.execute(
        f"UPDATE queue SET {set_clause} WHERE id = ?;",
        (*_params(item), item.id),
    )
    return item
