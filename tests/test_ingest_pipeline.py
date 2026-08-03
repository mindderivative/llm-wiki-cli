from pathlib import Path

import pytest

from llm_wiki.ingest import advance, step_once
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


def _item(vault_root: Path, storage: StorageEngine, name: str, status: QueueStatus, content: str = "hello") -> QueueItem:
    staged_path = vault_root / "raw" / ".staged" / name
    archive_path = vault_root / "raw" / ".sources" / name
    staged_path.write_text(content)
    archive_path.write_text(content)

    item = QueueItem(title=Path(name).stem, raw_path=staged_path, archive_path=archive_path, status=status)
    with storage.conn:
        return insert_queue_row(storage, item)


def test_step_once_staged_calls_accept(vault_root: Path, storage: StorageEngine):
    item = _item(vault_root, storage, "note.md", QueueStatus.STAGED)

    result = step_once(item, storage)

    assert result.status == QueueStatus.QUEUED


def test_step_once_queued_calls_atomize(vault_root: Path, storage: StorageEngine):
    item = _item(vault_root, storage, "note.txt", QueueStatus.QUEUED)

    result = step_once(item, storage)

    assert result.status == QueueStatus.PARSED


def test_step_once_noop_for_terminal_status(vault_root: Path, storage: StorageEngine):
    item = _item(vault_root, storage, "note.md", QueueStatus.COMPLETED)

    result = step_once(item, storage)

    assert result == item


def test_step_once_noop_for_unimplemented_status(vault_root: Path, storage: StorageEngine):
    # PARSED is a real, valid status -- just nothing's registered to
    # advance past it yet (compile() doesn't exist).
    item = _item(vault_root, storage, "note.md", QueueStatus.PARSED)

    result = step_once(item, storage)

    assert result == item


def test_advance_runs_staged_all_the_way_to_parsed(vault_root: Path, storage: StorageEngine):
    item = _item(vault_root, storage, "note.txt", QueueStatus.STAGED)

    result = advance(item, storage)

    # STAGED -> QUEUED -> PARSED is as far as the pipeline currently goes.
    assert result.status == QueueStatus.PARSED


def test_advance_stops_on_failure_without_raising(vault_root: Path, storage: StorageEngine):
    item = _item(vault_root, storage, "scan.pdf", QueueStatus.STAGED)  # unsupported format

    result = advance(item, storage)

    assert result.status == QueueStatus.FAILED
    assert result.failed_at_step == QueueStatus.PARSING  # got through accept(), failed in atomize()
