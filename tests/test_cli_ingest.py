from pathlib import Path

import pytest
from typer.testing import CliRunner

from llm_wiki.cli import app
from llm_wiki.vault import VaultManager

runner = CliRunner()


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    manager = VaultManager(recent_vaults_path=tmp_path / "_recent" / "recent_vaults.json")
    settings = manager.create(tmp_path / "vault")
    return settings.vault_root


def _write(tmp_path: Path, name: str, content: str = "hello") -> Path:
    path = tmp_path / name
    path.write_text(content)
    return path


def _add(vault_root: Path, path: Path):
    return runner.invoke(app, ["ingest", "add", str(path), "--vault", str(vault_root)])


# -- add ---------------------------------------------------------------


def test_add_stages_file(tmp_path: Path, vault_root: Path):
    source = _write(tmp_path, "note.md", "# heading\ncontent")

    result = _add(vault_root, source)

    assert result.exit_code == 0
    assert "STAGED" in result.stdout


def test_add_missing_file_fails(tmp_path: Path, vault_root: Path):
    result = _add(vault_root, tmp_path / "does-not-exist.md")

    assert result.exit_code == 1
    assert "FAILED" in result.stdout


# -- list / status -------------------------------------------------------


def test_list_shows_staged_item(tmp_path: Path, vault_root: Path):
    _add(vault_root, _write(tmp_path, "note.md"))

    result = runner.invoke(app, ["ingest", "list", "--vault", str(vault_root)])

    assert result.exit_code == 0
    assert "note" in result.stdout
    assert "STAGED" in result.stdout


def test_list_status_filter(tmp_path: Path, vault_root: Path):
    _add(vault_root, _write(tmp_path, "good.txt"))
    _add(vault_root, _write(tmp_path, "bad.pdf"))
    # Drive both to completion (or failure) -- "bad.pdf" isn't a supported format.
    runner.invoke(app, ["ingest", "run", "--count", "AUTO", "--vault", str(vault_root)])

    result = runner.invoke(app, ["ingest", "list", "--status", "FAILED", "--vault", str(vault_root)])

    assert result.exit_code == 0
    assert "bad" in result.stdout
    assert "good" not in result.stdout


def test_status_shows_detail(tmp_path: Path, vault_root: Path):
    _add(vault_root, _write(tmp_path, "note.md"))

    result = runner.invoke(app, ["ingest", "status", "1", "--vault", str(vault_root)])

    assert result.exit_code == 0
    assert "raw_path" in result.stdout
    assert "archive_path" in result.stdout


def test_status_missing_id_fails(vault_root: Path):
    result = runner.invoke(app, ["ingest", "status", "999", "--vault", str(vault_root)])

    assert result.exit_code == 1


# -- step ----------------------------------------------------------------


def test_step_single_id_advances_one_step(tmp_path: Path, vault_root: Path):
    _add(vault_root, _write(tmp_path, "note.md"))

    result = runner.invoke(app, ["ingest", "step", "1", "--vault", str(vault_root)])

    assert result.exit_code == 0
    assert "STAGED" in result.stdout
    assert "QUEUED" in result.stdout


def test_step_count_batch_filtered_by_status(tmp_path: Path, vault_root: Path):
    _add(vault_root, _write(tmp_path, "one.txt"))
    _add(vault_root, _write(tmp_path, "two.txt"))

    result = runner.invoke(
        app, ["ingest", "step", "--count", "5", "--status", "STAGED", "--vault", str(vault_root)]
    )

    assert result.exit_code == 0
    list_result = runner.invoke(app, ["ingest", "list", "--vault", str(vault_root)])
    assert list_result.stdout.count("QUEUED") == 2


def test_step_requires_exactly_one_of_id_or_count(vault_root: Path):
    neither = runner.invoke(app, ["ingest", "step", "--vault", str(vault_root)])
    both = runner.invoke(app, ["ingest", "step", "1", "--count", "5", "--vault", str(vault_root)])

    assert neither.exit_code == 1
    assert both.exit_code == 1


# -- run -------------------------------------------------------------------


def test_run_explicit_id_to_completion(tmp_path: Path, vault_root: Path):
    _add(vault_root, _write(tmp_path, "note.txt"))

    result = runner.invoke(app, ["ingest", "run", "1", "--vault", str(vault_root)])

    assert result.exit_code == 0
    assert "PARSED" in result.stdout  # as far as the pipeline currently goes
    assert "No commit performed" in result.stdout


def test_run_count_auto_drains_pool(tmp_path: Path, vault_root: Path):
    _add(vault_root, _write(tmp_path, "one.txt"))
    _add(vault_root, _write(tmp_path, "two.txt"))

    result = runner.invoke(app, ["ingest", "run", "--count", "AUTO", "--vault", str(vault_root)])

    assert result.exit_code == 0
    list_result = runner.invoke(app, ["ingest", "list", "--vault", str(vault_root)])
    assert list_result.stdout.count("PARSED") == 2


def test_run_stops_on_failure(tmp_path: Path, vault_root: Path):
    _add(vault_root, _write(tmp_path, "good.txt"))
    _add(vault_root, _write(tmp_path, "bad.pdf"))  # unsupported format
    _add(vault_root, _write(tmp_path, "third.txt"))

    result = runner.invoke(app, ["ingest", "run", "--count", "AUTO", "--vault", str(vault_root)])

    assert result.exit_code == 1
    assert "FAILED" in result.stdout
    assert "Stopped" in result.stderr  # error-ish summary goes to stderr, not stdout
    # third.txt (id 3) must not have been touched -- the run stopped at bad.pdf (id 2).
    status_result = runner.invoke(app, ["ingest", "status", "3", "--vault", str(vault_root)])
    assert "STAGED" in status_result.stdout


def test_run_invalid_count_fails(vault_root: Path):
    result = runner.invoke(app, ["ingest", "run", "--count", "not-a-number", "--vault", str(vault_root)])

    assert result.exit_code == 1


def test_run_requires_exactly_one_of_ids_or_count(vault_root: Path):
    neither = runner.invoke(app, ["ingest", "run", "--vault", str(vault_root)])

    assert neither.exit_code == 1
