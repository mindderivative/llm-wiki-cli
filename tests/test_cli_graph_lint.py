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


def _write_note(vault_root: Path, folder: str, slug: str, *, frontmatter_text: str, body: str) -> Path:
    path = vault_root / "wiki" / folder / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{frontmatter_text}\n---\n\n{body}")
    return path


def _entity(vault_root: Path, slug: str, body: str = "") -> Path:
    return _write_note(
        vault_root,
        "entities",
        slug,
        frontmatter_text=f"type: entity\ntitle: {slug}\ntags: []\nsources: []",
        body=body,
    )


# -- graph rebuild -----------------------------------------------------------


def test_graph_rebuild_empty_vault(vault_root: Path):
    result = runner.invoke(app, ["graph", "rebuild", "--vault", str(vault_root)])

    assert result.exit_code == 0
    assert "notes_scanned" in result.stdout


def test_graph_rebuild_indexes_a_note(vault_root: Path):
    _entity(vault_root, "acme-corp", body="[[quarterly-report]]")

    result = runner.invoke(app, ["graph", "rebuild", "--vault", str(vault_root)])

    assert result.exit_code == 0
    assert "notes_indexed" in result.stdout
    # 1 note indexed, 1 link added -- printed as table rows
    assert "1" in result.stdout


def test_graph_rebuild_reports_unreadable_notes(vault_root: Path):
    path = vault_root / "wiki" / "entities" / "broken.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("no frontmatter here")

    result = runner.invoke(app, ["graph", "rebuild", "--vault", str(vault_root)])

    assert result.exit_code == 0
    assert "broken" in result.stdout
    assert "unreadable" in result.stdout


def test_graph_rebuild_full_flag_accepted(vault_root: Path):
    _entity(vault_root, "acme-corp")

    result = runner.invoke(app, ["graph", "rebuild", "--full", "--vault", str(vault_root)])

    assert result.exit_code == 0


def test_graph_rebuild_missing_vault_fails(tmp_path: Path):
    result = runner.invoke(app, ["graph", "rebuild", "--vault", str(tmp_path / "nope")])

    assert result.exit_code == 1


# -- lint run ------------------------------------------------------------


def test_lint_run_empty_vault_has_no_findings(vault_root: Path):
    result = runner.invoke(app, ["lint", "run", "--vault", str(vault_root)])

    assert result.exit_code == 0
    assert "No findings" in result.stdout
    assert "100%" in result.stdout


def test_lint_run_reports_broken_link_and_exits_nonzero(vault_root: Path):
    _entity(vault_root, "acme-corp", body="[[missing-note]]")
    runner.invoke(app, ["graph", "rebuild", "--vault", str(vault_root)])

    result = runner.invoke(app, ["lint", "run", "--vault", str(vault_root)])

    assert result.exit_code == 1
    assert "broken_link" in result.stdout
    assert "acme-corp" in result.stdout


def test_lint_run_detects_schema_violation_without_graph_rebuild(vault_root: Path):
    path = vault_root / "wiki" / "concepts" / "bad.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not even frontmatter")

    result = runner.invoke(app, ["lint", "run", "--vault", str(vault_root)])

    assert result.exit_code == 1
    assert "schema_violation" in result.stdout


def test_lint_run_missing_vault_fails(tmp_path: Path):
    result = runner.invoke(app, ["lint", "run", "--vault", str(tmp_path / "nope")])

    assert result.exit_code == 1
