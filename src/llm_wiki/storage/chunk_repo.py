"""`Chunk` <-> `chunks` row insert.

Same pattern as `queue_repo.py`: no internal `.commit()`, callers own the
transaction boundary. `ingest.atomize()` inserts a queue item's chunks
and writes its terminal `PARSED` status inside one `with storage.conn:`
block, so partial output can never outlive a crash without its status
reflecting it (INGEST_PLAN.md §3, atomicity contract).
"""

from __future__ import annotations

from llm_wiki.models import Chunk
from llm_wiki.storage.engine import StorageEngine


def insert_chunk_row(storage: StorageEngine, chunk: Chunk) -> Chunk:
    """Insert `chunk` as a new `chunks` row, returning it with `id` populated."""
    cursor = storage.conn.execute(
        """
        INSERT INTO chunks (note_id, queue_item_id, ordinal, title, content, word_count)
        VALUES (?, ?, ?, ?, ?, ?);
        """,
        (
            chunk.note_id,
            chunk.queue_item_id,
            chunk.ordinal,
            chunk.title,
            chunk.content,
            chunk.word_count,
        ),
    )
    return chunk.model_copy(update={"id": cursor.lastrowid})
