"""storage — own the SQLite connection, schema migrations, sqlite-vec loading.

All tables (`queue`, `notes`, `chunks`, `vec_chunks`, `links`,
`lint_findings` — ARCHITECTURE.md §6) are derived from `wiki/`/`raw/` and
fully rebuildable via `StorageEngine.rebuild()`.
"""

from llm_wiki.storage.engine import DEFAULT_EMBEDDING_DIM, StorageEngine
from llm_wiki.storage.schema import SCHEMA_VERSION

__all__ = ["StorageEngine", "DEFAULT_EMBEDDING_DIM", "SCHEMA_VERSION"]
