from pathlib import Path

import pytest

from llm_wiki.models import QueueStatus
from llm_wiki.stager import stage
from llm_wiki.storage import StorageEngine
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


def _make_source(tmp_path: Path, name: str, content: str = "hello world") -> Path:
    path = tmp_path / name
    path.write_text(content)
    return path


def test_stage_success(tmp_path: Path, vault_root: Path, storage: StorageEngine):
    source = _make_source(tmp_path, "My Upload.md", "some source content")

    item = stage(source, vault_root, storage)

    assert item.status == QueueStatus.STAGED
    assert item.id is not None
    assert item.error is None
    assert item.failed_at_step is None

    # Working copy under raw/.staged/, matches content, named by slug.
    assert item.raw_path.parent == vault_root / "raw" / ".staged"
    assert item.raw_path.name.endswith("_my-upload.md")
    assert item.raw_path.read_text() == "some source content"

    # Untouched original archived under raw/.sources/, original filename kept.
    assert item.archive_path is not None
    assert item.archive_path.parent == vault_root / "raw" / ".sources"
    assert item.archive_path.name.endswith("_My Upload.md")
    assert item.archive_path.read_text() == "some source content"

    row = storage.conn.execute("SELECT * FROM queue WHERE id = ?;", (item.id,)).fetchone()
    assert row["status"] == "STAGED"
    assert row["failed_at_step"] is None


def test_stage_does_not_move_or_delete_source(tmp_path: Path, vault_root: Path, storage: StorageEngine):
    source = _make_source(tmp_path, "keepme.txt")

    stage(source, vault_root, storage)

    assert source.exists()  # never moved/deleted, only copied from


def test_stage_missing_file_records_failed_row(tmp_path: Path, vault_root: Path, storage: StorageEngine):
    missing = tmp_path / "does-not-exist.pdf"

    item = stage(missing, vault_root, storage)

    assert item.status == QueueStatus.FAILED
    assert item.failed_at_step == QueueStatus.STAGED
    assert item.error is not None
    assert item.id is not None  # still recorded, not silently dropped

    row = storage.conn.execute("SELECT * FROM queue WHERE id = ?;", (item.id,)).fetchone()
    assert row["status"] == "FAILED"
    assert row["failed_at_step"] == "STAGED"


def test_stage_name_collision_gets_suffixed(tmp_path: Path, vault_root: Path, storage: StorageEngine):
    src_dir_a = tmp_path / "a"
    src_dir_b = tmp_path / "b"
    src_dir_a.mkdir()
    src_dir_b.mkdir()
    first = _make_source(src_dir_a, "Notes.md", "first")
    second = _make_source(src_dir_b, "notes.md", "second")  # same slug as "Notes.md"

    item1 = stage(first, vault_root, storage)
    item2 = stage(second, vault_root, storage)

    assert item1.raw_path != item2.raw_path
    assert item1.raw_path.read_text() == "first"
    assert item2.raw_path.read_text() == "second"
    assert "-2" in item2.raw_path.name
