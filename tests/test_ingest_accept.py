from pathlib import Path

import pytest

from llm_wiki.ingest import accept
from llm_wiki.models import QueueItem, QueueStatus
from llm_wiki.storage import StorageEngine, insert_queue_row
from llm_wiki.vault import VaultManager


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    manager = VaultManager(recent_vaults_path=tmp_path / "_recent" / "recent_vaults.json")
    settings = manager.create(tmp_path / "vault")
    return settings.vault_root


@pytest.fixture
def storage(vault_root: Path):
    db_path = vault_root / ".llm-wiki" / "db.sqlite3"
    with StorageEngine(db_path) as engine:
        yield engine


def _staged_item(vault_root: Path, storage: StorageEngine, name: str, content: str = "hello") -> QueueItem:
    """A STAGED QueueItem with real files backing it, built directly
    (not via stager.stage()) so these tests only depend on the STAGED
    contract accept() actually relies on, not stager's implementation."""
    staged_path = vault_root / "raw" / ".staged" / name
    archive_path = vault_root / "raw" / ".sources" / name
    staged_path.write_text(content)
    archive_path.write_text(content)

    item = QueueItem(
        title=Path(name).stem,
        raw_path=staged_path,
        archive_path=archive_path,
        status=QueueStatus.STAGED,
    )
    with storage.conn:
        return insert_queue_row(storage, item)


def test_accept_success(vault_root: Path, storage: StorageEngine):
    item = _staged_item(vault_root, storage, "note.md")

    result = accept(item, storage)

    assert result.status == QueueStatus.QUEUED
    assert result.error is None
    assert result.failed_at_step is None

    row = storage.conn.execute("SELECT status FROM queue WHERE id = ?;", (result.id,)).fetchone()
    assert row["status"] == "QUEUED"


def test_accept_missing_staged_copy_fails(vault_root: Path, storage: StorageEngine):
    item = _staged_item(vault_root, storage, "note.md")
    item.raw_path.unlink()  # simulate someone deleting the working copy after stage()

    result = accept(item, storage)

    assert result.status == QueueStatus.FAILED
    assert result.failed_at_step == QueueStatus.QUEUED
    assert "missing" in result.error.lower()


def test_accept_missing_archive_fails(vault_root: Path, storage: StorageEngine):
    item = _staged_item(vault_root, storage, "note.md")
    item.archive_path.unlink()

    result = accept(item, storage)

    assert result.status == QueueStatus.FAILED
    assert result.failed_at_step == QueueStatus.QUEUED
    assert "missing" in result.error.lower()


def test_accept_noop_for_non_staged_item(vault_root: Path, storage: StorageEngine):
    item = _staged_item(vault_root, storage, "note.md")
    already_queued = accept(item, storage)
    assert already_queued.status == QueueStatus.QUEUED

    result = accept(already_queued, storage)

    assert result == already_queued  # unchanged, no double-processing
