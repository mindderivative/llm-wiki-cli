from pathlib import Path

import pygit2
import pytest

from llm_wiki.vault import VaultManager
from llm_wiki.vcs import GitEngine


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    manager = VaultManager(recent_vaults_path=tmp_path / "_recent" / "recent_vaults.json")
    settings = manager.create(tmp_path / "vault")
    return settings.vault_root


def _write(vault_root: Path, rel_path: str, content: str = "hello") -> Path:
    path = vault_root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def _log(repo: pygit2.Repository) -> list[pygit2.Commit]:
    if repo.head_is_unborn:
        return []
    return list(repo.walk(repo.head.target))


# -- init ------------------------------------------------------------------


def test_init_creates_repo(vault_root: Path):
    GitEngine(vault_root).init()

    assert pygit2.discover_repository(str(vault_root)) is not None


def test_init_is_idempotent(vault_root: Path):
    engine = GitEngine(vault_root)
    engine.init()
    engine.init()  # must not raise, must not create a second repo

    assert pygit2.discover_repository(str(vault_root)) is not None


def test_init_writes_gitignore_for_llm_wiki_dir(vault_root: Path):
    GitEngine(vault_root).init()

    gitignore = (vault_root / ".gitignore").read_text()
    assert ".llm-wiki/" in gitignore.splitlines()


def test_init_does_not_duplicate_gitignore_line(vault_root: Path):
    engine = GitEngine(vault_root)
    engine.init()
    engine.init()

    lines = (vault_root / ".gitignore").read_text().splitlines()
    assert lines.count(".llm-wiki/") == 1


# -- commit ------------------------------------------------------------------


def test_commit_returns_none_when_nothing_to_commit(vault_root: Path):
    # `vault create` already seeds wiki/index.md + wiki/log.md, so the
    # *first* commit picks those up -- "nothing to commit" only shows up
    # on a second call with no changes since.
    engine = GitEngine(vault_root)
    engine.commit("ingest: scaffold")

    result = engine.commit("ingest: nothing")

    assert result is None


def test_commit_creates_first_commit_with_tracked_files(vault_root: Path):
    _write(vault_root, "raw/.sources/2026-08-02_note.txt", "hello")
    _write(vault_root, "wiki/sources/note.md", "# note")

    oid = GitEngine(vault_root).commit("ingest: note")

    assert oid is not None
    repo = pygit2.Repository(str(vault_root))
    commits = _log(repo)
    assert len(commits) == 1
    assert commits[0].message == "ingest: note"


def test_commit_only_tracks_raw_and_wiki(vault_root: Path):
    _write(vault_root, "raw/.sources/2026-08-02_note.txt", "hello")
    _write(vault_root, ".llm-wiki/db.sqlite3", "not really a db")

    GitEngine(vault_root).commit("ingest: note")

    repo = pygit2.Repository(str(vault_root))
    tree = repo.head.peel(pygit2.Tree)
    paths = {entry.name for entry in tree}
    assert ".llm-wiki" not in paths
    assert "raw" in paths


def test_commit_is_noop_when_nothing_changed_since_last_commit(vault_root: Path):
    _write(vault_root, "raw/.sources/2026-08-02_note.txt", "hello")
    engine = GitEngine(vault_root)
    first_oid = engine.commit("ingest: note")

    second_result = engine.commit("ingest: note again")

    repo = pygit2.Repository(str(vault_root))
    assert second_result is None
    assert len(_log(repo)) == 1
    assert str(_log(repo)[0].id) == first_oid


def test_commit_creates_second_commit_when_something_changed(vault_root: Path):
    _write(vault_root, "raw/.sources/2026-08-02_one.txt", "hello")
    engine = GitEngine(vault_root)
    first_oid = engine.commit("ingest: one")

    _write(vault_root, "raw/.sources/2026-08-02_two.txt", "world")
    second_oid = engine.commit("ingest: two")

    repo = pygit2.Repository(str(vault_root))
    commits = _log(repo)
    assert len(commits) == 2
    assert second_oid is not None
    assert second_oid != first_oid
