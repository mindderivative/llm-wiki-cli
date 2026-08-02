from pathlib import Path

import pytest

from llm_wiki.models import VaultAlreadyExistsError, VaultNotFoundError
from llm_wiki.vault import REQUIRED_DIRS, REQUIRED_FILES, VaultManager


@pytest.fixture
def manager(tmp_path: Path) -> VaultManager:
    # Recent-vaults list lives outside any vault_root — point it at a
    # tmp_path file so tests never touch the real home directory.
    return VaultManager(recent_vaults_path=tmp_path / "_recent" / "recent_vaults.json")


def test_create_builds_full_tree(tmp_path: Path, manager: VaultManager):
    vault_root = tmp_path / "myvault"
    settings = manager.create(vault_root)

    assert settings.vault_root == vault_root
    assert (vault_root / ".llm-wiki-config").is_file()
    for rel_dir in REQUIRED_DIRS:
        assert (vault_root / rel_dir).is_dir(), rel_dir
    for rel_file in REQUIRED_FILES:
        assert (vault_root / rel_file).is_file(), rel_file
    assert settings.db_path.exists()  # storage schema was initialized


def test_create_twice_raises(tmp_path: Path, manager: VaultManager):
    vault_root = tmp_path / "myvault"
    manager.create(vault_root)
    with pytest.raises(VaultAlreadyExistsError):
        manager.create(vault_root)


def test_create_tolerates_preexisting_nonvault_dir(tmp_path: Path, manager: VaultManager):
    vault_root = tmp_path / "myvault"
    vault_root.mkdir()
    (vault_root / "some_unrelated_file.txt").write_text("hi")
    settings = manager.create(vault_root)
    assert settings.vault_root == vault_root


def test_load_round_trips_settings(tmp_path: Path, manager: VaultManager):
    vault_root = tmp_path / "myvault"
    created = manager.create(vault_root, auto_watch_raw=True, lint_on_save=False)
    loaded = manager.load(vault_root)
    assert loaded.auto_watch_raw == created.auto_watch_raw is True
    assert loaded.lint_on_save == created.lint_on_save is False


def test_load_missing_vault_raises(tmp_path: Path, manager: VaultManager):
    with pytest.raises(VaultNotFoundError):
        manager.load(tmp_path / "nope")


def test_validate_healthy_vault_has_no_problems(tmp_path: Path, manager: VaultManager):
    vault_root = tmp_path / "myvault"
    manager.create(vault_root)
    assert manager.validate(vault_root) == []


def test_validate_reports_missing_directory(tmp_path: Path, manager: VaultManager):
    vault_root = tmp_path / "myvault"
    manager.create(vault_root)
    import shutil

    shutil.rmtree(vault_root / "wiki" / "entities")

    problems = manager.validate(vault_root)
    assert any("wiki/entities" in p for p in problems)


def test_validate_missing_vault_raises(tmp_path: Path, manager: VaultManager):
    with pytest.raises(VaultNotFoundError):
        manager.validate(tmp_path / "nope")


def test_is_vault(tmp_path: Path, manager: VaultManager):
    vault_root = tmp_path / "myvault"
    assert manager.is_vault(vault_root) is False
    manager.create(vault_root)
    assert manager.is_vault(vault_root) is True


def test_recent_vaults_most_recent_first(tmp_path: Path, manager: VaultManager):
    a, b = tmp_path / "a", tmp_path / "b"
    manager.create(a)
    manager.create(b)
    recent = manager.list_recent()
    assert [e.path for e in recent] == [b.resolve(), a.resolve()]


def test_recent_vaults_reopening_moves_to_front(tmp_path: Path, manager: VaultManager):
    a, b = tmp_path / "a", tmp_path / "b"
    manager.create(a)
    manager.create(b)
    manager.load(a)  # re-open a — should jump back to the front
    recent = manager.list_recent()
    assert [e.path for e in recent] == [a.resolve(), b.resolve()]


def test_recent_vaults_forget(tmp_path: Path, manager: VaultManager):
    a, b = tmp_path / "a", tmp_path / "b"
    manager.create(a)
    manager.create(b)
    manager.forget_recent(a)
    recent = manager.list_recent()
    assert [e.path for e in recent] == [b.resolve()]


def test_recent_vaults_respects_limit(tmp_path: Path, manager: VaultManager):
    for i in range(5):
        manager.create(tmp_path / f"vault{i}")
    assert len(manager.list_recent(limit=2)) == 2
