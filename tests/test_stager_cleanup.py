from pathlib import Path

import pytest

from llm_wiki.models import QueueStatus
from llm_wiki.stager import stage, verify_and_clean
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


def _drop_in_raw(vault_root: Path, name: str, content: str = "dropped content") -> Path:
    """Simulate a file the watcher would observe: sitting directly at
    raw/'s top level, not in a subdirectory."""
    path = vault_root / "raw" / name
    path.write_text(content)
    return path


def test_verify_and_clean_deletes_original_after_match(vault_root: Path, storage: StorageEngine):
    original = _drop_in_raw(vault_root, "dropped.md")

    item = stage(original, vault_root, storage)
    assert item.status == QueueStatus.STAGED

    result = verify_and_clean(item, original, vault_root, storage)

    assert result.status == QueueStatus.STAGED
    assert not original.exists()  # the watcher's drop-zone copy is gone
    assert result.archive_path.exists()  # raw/.sources/ is now canonical
    assert result.archive_path.read_text() == "dropped content"


def test_verify_and_clean_noop_for_external_source(tmp_path: Path, vault_root: Path, storage: StorageEngine):
    # A file staged via `ingest add ~/Downloads/x.pdf` — never lived
    # under raw/ at all, so there's nothing under raw/ to clean up.
    external = tmp_path / "external.txt"
    external.write_text("not from raw/")

    item = stage(external, vault_root, storage)
    result = verify_and_clean(item, external, vault_root, storage)

    assert result == item  # unchanged
    assert external.exists()  # never touched


def test_verify_and_clean_noop_if_already_cleaned(vault_root: Path, storage: StorageEngine):
    original = _drop_in_raw(vault_root, "dropped.md")
    item = stage(original, vault_root, storage)

    first = verify_and_clean(item, original, vault_root, storage)
    second = verify_and_clean(first, original, vault_root, storage)  # original already gone

    assert second == first  # no-op, no error


def test_verify_and_clean_noop_for_failed_stage(vault_root: Path, storage: StorageEngine):
    missing = vault_root / "raw" / "never-existed.md"

    item = stage(missing, vault_root, storage)
    assert item.status == QueueStatus.FAILED

    result = verify_and_clean(item, missing, vault_root, storage)

    assert result == item  # unchanged — nothing was ever staged to verify


def test_verify_and_clean_fails_item_on_hash_mismatch(vault_root: Path, storage: StorageEngine):
    original = _drop_in_raw(vault_root, "dropped.md")
    item = stage(original, vault_root, storage)
    assert item.status == QueueStatus.STAGED

    # Simulate a corrupted archive copy despite stage() not raising.
    item.archive_path.write_text("corrupted, does not match the original")

    result = verify_and_clean(item, original, vault_root, storage)

    assert result.status == QueueStatus.FAILED
    assert result.failed_at_step == QueueStatus.STAGED
    assert result.error is not None
    assert original.exists()  # refused to delete on mismatch

    row = storage.conn.execute("SELECT * FROM queue WHERE id = ?;", (result.id,)).fetchone()
    assert row["status"] == "FAILED"
    assert row["failed_at_step"] == "STAGED"


def test_verify_and_clean_delete_failure_does_not_fail_item(
    vault_root: Path, storage: StorageEngine, monkeypatch: pytest.MonkeyPatch
):
    original = _drop_in_raw(vault_root, "dropped.md")
    item = stage(original, vault_root, storage)

    def _boom(self: Path) -> None:
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "unlink", _boom)

    result = verify_and_clean(item, original, vault_root, storage)

    assert result.status == QueueStatus.STAGED  # archive was already confirmed intact
    assert result.error is None
    assert original.exists()  # delete failed, original is still there
