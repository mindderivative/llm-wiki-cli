"""storage — own the SQLite connection, schema migrations, sqlite-vec loading.

All tables (`queue`, `notes`, `chunks`, `vec_chunks`, `links`,
`lint_findings` — ARCHITECTURE.md §6) are derived from `wiki/`/`raw/` and
fully rebuildable via `StorageEngine.rebuild()`.
"""

from llm_wiki.storage.analysis_repo import get_analysis_row, upsert_analysis_row
from llm_wiki.storage.chunk_repo import insert_chunk_row, insert_embedding, list_chunks_for_queue_item
from llm_wiki.storage.engine import DEFAULT_EMBEDDING_DIM, StorageEngine
from llm_wiki.storage.notes_repo import get_note_row_by_slug, insert_note_row, update_note_row
from llm_wiki.storage.queue_repo import (
    get_queue_row,
    insert_queue_row,
    list_pool,
    list_queue_rows,
    update_queue_row,
)
from llm_wiki.storage.schema import SCHEMA_VERSION

__all__ = [
    "StorageEngine",
    "DEFAULT_EMBEDDING_DIM",
    "SCHEMA_VERSION",
    "insert_queue_row",
    "update_queue_row",
    "get_queue_row",
    "list_queue_rows",
    "list_pool",
    "insert_chunk_row",
    "list_chunks_for_queue_item",
    "insert_embedding",
    "upsert_analysis_row",
    "get_analysis_row",
    "insert_note_row",
    "get_note_row_by_slug",
    "update_note_row",
]
