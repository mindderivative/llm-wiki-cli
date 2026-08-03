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

import sqlite3
from datetime import datetime
from pathlib import Path

from llm_wiki.models import QueueItem, QueueStatus
from llm_wiki.storage.engine import StorageEngine

_TERMINAL_STATUSES = (QueueStatus.COMPLETED.value, QueueStatus.FAILED.value)

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


def _row_to_item(row: sqlite3.Row) -> QueueItem:
    return QueueItem(
        id=row["id"],
        title=row["title"],
        raw_path=Path(row["raw_path"]),
        archive_path=Path(row["archive_path"]) if row["archive_path"] else None,
        status=QueueStatus(row["status"]),
        error=row["error"],
        failed_at_step=QueueStatus(row["failed_at_step"]) if row["failed_at_step"] else None,
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def get_queue_row(storage: StorageEngine, item_id: int) -> QueueItem | None:
    """The `queue` row with id `item_id`, or `None` if there isn't one."""
    row = storage.conn.execute("SELECT * FROM queue WHERE id = ?;", (item_id,)).fetchone()
    return _row_to_item(row) if row is not None else None


def list_queue_rows(storage: StorageEngine, *, status: QueueStatus | None = None) -> list[QueueItem]:
    """Every `queue` row, oldest `created_at` first — optionally filtered
    to one status. Includes terminal (`COMPLETED`/`FAILED`) items, unlike
    `list_pool()`; this is the "what's the state of everything" view
    (`wiki-cli ingest list`), not the batch-run selection pool."""
    if status is not None:
        rows = storage.conn.execute(
            "SELECT * FROM queue WHERE status = ? ORDER BY created_at ASC;",
            (status.value,),
        ).fetchall()
    else:
        rows = storage.conn.execute("SELECT * FROM queue ORDER BY created_at ASC;").fetchall()
    return [_row_to_item(r) for r in rows]


def list_pool(
    storage: StorageEngine, *, status: QueueStatus | None = None, limit: int | None = None
) -> list[QueueItem]:
    """The batch-run selection pool (INGEST_PLAN.md §5): every `queue`
    row **not** in a terminal state (`COMPLETED`/`FAILED`), oldest
    `created_at` first, optionally filtered to one status and/or capped
    at `limit` rows."""
    clauses = ["status NOT IN (?, ?)"]
    params: list = [*_TERMINAL_STATUSES]
    if status is not None:
        clauses.append("status = ?")
        params.append(status.value)

    sql = f"SELECT * FROM queue WHERE {' AND '.join(clauses)} ORDER BY created_at ASC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)

    rows = storage.conn.execute(sql + ";", params).fetchall()
    return [_row_to_item(r) for r in rows]
