"""Own the SQLite connection, schema migrations, and sqlite-vec loading.

`StorageEngine` is the only thing in `llm_wiki` that speaks SQL directly.
Higher layers (`ingest`, `compiler`, `graph`, `lint`) go through it rather
than opening their own connections, so the "rebuildable cache" guarantee
(ARCHITECTURE.md §2.1) has exactly one implementation to keep honest.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import TracebackType

from loguru import logger

from llm_wiki.models import StorageError
from llm_wiki.storage.schema import (
    ALL_TABLES_DROP_ORDER,
    CORE_TABLES,
    SCHEMA_VERSION,
    vec_chunks_ddl,
)

DEFAULT_EMBEDDING_DIM = 768


class StorageEngine:
    """Manages one vault's `.llm-wiki/db.sqlite3` connection and schema.

    Usage::

        with StorageEngine(settings.db_path, embedding_dim=settings.llama.embedding_dim) as storage:
            storage.init_schema()
    """

    def __init__(self, db_path: Path, *, embedding_dim: int = DEFAULT_EMBEDDING_DIM) -> None:
        self.db_path = db_path
        self.embedding_dim = embedding_dim
        self._conn: sqlite3.Connection | None = None
        self.vec_available = False

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise StorageError(
                "StorageEngine is not connected — call connect() or use it as a context manager."
            )
        return self._conn

    def connect(self) -> StorageEngine:
        """Open the connection (idempotent) and best-effort load sqlite-vec."""
        if self._conn is not None:
            return self
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            conn = sqlite3.connect(self.db_path)
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to open {self.db_path}: {exc}") from exc
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        self._conn = conn
        self.vec_available = self._try_load_vec()
        return self

    def _try_load_vec(self) -> bool:
        """Best-effort sqlite-vec extension load.

        Some Python builds ship `sqlite3` without extension-loading
        support, and `sqlite-vec` stays uninstalled in environments that
        never touch embeddings. Degrade to "vector search unavailable"
        rather than hard-crash the whole engine over an optional feature.
        """
        assert self._conn is not None
        try:
            import sqlite_vec  # optional dependency — imported lazily on purpose
        except ImportError:
            logger.warning("sqlite-vec not installed — vector search disabled.")
            return False
        try:
            self._conn.enable_load_extension(True)
            sqlite_vec.load(self._conn)
        except (AttributeError, sqlite3.OperationalError) as exc:
            logger.warning(f"sqlite-vec extension could not be loaded — vector search disabled ({exc}).")
            return False
        finally:
            try:
                self._conn.enable_load_extension(False)
            except AttributeError:
                pass
        return True

    def init_schema(self) -> None:
        """Create all tables if they don't already exist. Idempotent."""
        try:
            with self.conn:
                for ddl in CORE_TABLES:
                    self.conn.execute(ddl)
                if self.vec_available:
                    self.conn.execute(vec_chunks_ddl(self.embedding_dim))
                self.conn.execute(
                    "INSERT OR IGNORE INTO schema_meta (id, version) VALUES (0, ?);",
                    (SCHEMA_VERSION,),
                )
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to initialize schema: {exc}") from exc

    def rebuild(self) -> None:
        """Drop and recreate every table.

        The database is a cache — this only resets it to empty.
        Repopulating from `wiki/`/`raw/` is the job of the higher-level
        engines (`ingest`, `compiler`, `graph`), not `storage` itself.
        """
        try:
            with self.conn:
                for table in ALL_TABLES_DROP_ORDER:
                    self.conn.execute(f"DROP TABLE IF EXISTS {table};")
            self.init_schema()
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to rebuild schema: {exc}") from exc

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> StorageEngine:
        return self.connect()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
