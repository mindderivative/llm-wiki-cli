from pathlib import Path

import pytest

from llm_wiki.models import StorageError
from llm_wiki.storage import SCHEMA_VERSION, StorageEngine

CORE_TABLE_NAMES = {
    "schema_meta",
    "queue",
    "notes",
    "chunks",
    "queue_analysis",
    "links",
    "lint_findings",
}


def _table_names(storage: StorageEngine) -> set[str]:
    rows = storage.conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'view');"
    ).fetchall()
    return {row["name"] for row in rows}


def test_init_schema_creates_core_tables(tmp_path: Path):
    with StorageEngine(tmp_path / "db.sqlite3") as storage:
        storage.init_schema()
        names = _table_names(storage)
        assert CORE_TABLE_NAMES <= names
        # vec_chunks only appears if the optional sqlite-vec extension loaded.
        assert storage.vec_available == ("vec_chunks" in names)


def test_init_schema_is_idempotent(tmp_path: Path):
    with StorageEngine(tmp_path / "db.sqlite3") as storage:
        storage.init_schema()
        storage.init_schema()  # must not raise on a second call
        version = storage.conn.execute("SELECT version FROM schema_meta;").fetchone()["version"]
        assert version == SCHEMA_VERSION


def test_rebuild_empties_tables(tmp_path: Path):
    with StorageEngine(tmp_path / "db.sqlite3") as storage:
        storage.init_schema()
        storage.conn.execute(
            "INSERT INTO queue (title, raw_path, status, created_at, updated_at) "
            "VALUES ('t', 'raw/t.pdf', 'QUEUED', '2026-08-01T00:00:00Z', '2026-08-01T00:00:00Z');"
        )
        storage.conn.commit()
        assert storage.conn.execute("SELECT COUNT(*) AS n FROM queue;").fetchone()["n"] == 1

        storage.rebuild()

        assert storage.conn.execute("SELECT COUNT(*) AS n FROM queue;").fetchone()["n"] == 0
        assert CORE_TABLE_NAMES <= _table_names(storage)


def test_conn_raises_before_connect(tmp_path: Path):
    storage = StorageEngine(tmp_path / "db.sqlite3")
    with pytest.raises(StorageError):
        _ = storage.conn


def test_context_manager_closes_connection(tmp_path: Path):
    storage = StorageEngine(tmp_path / "db.sqlite3")
    with storage as s:
        s.init_schema()
    with pytest.raises(StorageError):
        _ = storage.conn


def test_queue_table_has_failed_at_step_column(tmp_path: Path):
    with StorageEngine(tmp_path / "db.sqlite3") as storage:
        storage.init_schema()
        columns = {
            row["name"]
            for row in storage.conn.execute("PRAGMA table_info(queue);").fetchall()
        }
        assert "failed_at_step" in columns

        # Nullable — a normal (non-failed) row doesn't need to set it.
        storage.conn.execute(
            "INSERT INTO queue (title, raw_path, status, created_at, updated_at) "
            "VALUES ('t', 'raw/.staged/t.md', 'STAGED', '2026-08-01T00:00:00Z', '2026-08-01T00:00:00Z');"
        )
        storage.conn.commit()
        row = storage.conn.execute("SELECT failed_at_step FROM queue;").fetchone()
        assert row["failed_at_step"] is None


def test_db_file_created_on_connect(tmp_path: Path):
    db_path = tmp_path / "nested" / "db.sqlite3"
    with StorageEngine(db_path) as storage:
        storage.init_schema()
    assert db_path.exists()
