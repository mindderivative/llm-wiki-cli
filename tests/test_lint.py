from pathlib import Path

import pytest

from llm_wiki.graph import rebuild_links
from llm_wiki.lint import run as lint_run
from llm_wiki.storage import StorageEngine


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    return tmp_path / "vault"


@pytest.fixture
def storage(tmp_path: Path):
    with StorageEngine(tmp_path / "db.sqlite3", embedding_dim=4) as engine:
        engine.init_schema()
        yield engine


def _write_note(vault_root: Path, folder: str, slug: str, *, frontmatter_text: str, body: str) -> Path:
    path = vault_root / "wiki" / folder / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{frontmatter_text}\n---\n\n{body}")
    return path


def _entity(vault_root: Path, slug: str, body: str = "body text") -> Path:
    return _write_note(
        vault_root,
        "entities",
        slug,
        frontmatter_text=f"type: entity\ntitle: {slug}\ntags: []\nsources: []",
        body=body,
    )


def test_run_empty_vault_scores_perfect(vault_root: Path, storage: StorageEngine):
    report = lint_run(vault_root, storage)

    assert report.total_notes == 0
    assert report.findings == []
    assert report.health_score == 1.0


def test_run_detects_broken_link(vault_root: Path, storage: StorageEngine):
    _entity(vault_root, "acme-corp", body="[[missing-note]]")
    rebuild_links(vault_root, storage)

    report = lint_run(vault_root, storage)

    kinds = [f.kind for f in report.findings]
    assert "broken_link" in kinds
    broken = next(f for f in report.findings if f.kind == "broken_link")
    assert broken.slug == "acme-corp"
    assert "missing-note" in broken.message


def test_run_detects_isolated_note(vault_root: Path, storage: StorageEngine):
    _entity(vault_root, "lonely", body="No links here.")
    rebuild_links(vault_root, storage)

    report = lint_run(vault_root, storage)

    isolated = [f for f in report.findings if f.kind == "isolated_note"]
    assert len(isolated) == 1
    assert isolated[0].slug == "lonely"


def test_run_linked_notes_are_not_isolated(vault_root: Path, storage: StorageEngine):
    _entity(vault_root, "a", body="[[b]]")
    _entity(vault_root, "b", body="no outgoing links")
    rebuild_links(vault_root, storage)

    report = lint_run(vault_root, storage)

    isolated_slugs = {f.slug for f in report.findings if f.kind == "isolated_note"}
    assert isolated_slugs == set()  # "a" has an outgoing link, "b" has an incoming one


def test_run_detects_schema_violation(vault_root: Path, storage: StorageEngine):
    path = vault_root / "wiki" / "entities" / "broken.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("no frontmatter at all")

    report = lint_run(vault_root, storage)

    violations = [f for f in report.findings if f.kind == "schema_violation"]
    assert len(violations) == 1
    assert violations[0].slug == "broken"


def test_run_schema_violation_does_not_need_graph_rebuild_first(vault_root: Path, storage: StorageEngine):
    # lint does its own filesystem walk for this check -- no rebuild_links() call.
    path = vault_root / "wiki" / "concepts" / "bad.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\ntype: not-real\n---\n\nbody")

    report = lint_run(vault_root, storage)

    assert any(f.kind == "schema_violation" and f.slug == "bad" for f in report.findings)


def test_run_health_score_reflects_unhealthy_fraction(vault_root: Path, storage: StorageEngine):
    _entity(vault_root, "healthy-a", body="[[healthy-b]]")
    _entity(vault_root, "healthy-b", body="")
    _entity(vault_root, "broken-link-note", body="[[nowhere]]")
    rebuild_links(vault_root, storage)

    report = lint_run(vault_root, storage)

    assert report.total_notes == 3
    # exactly one of the three notes (broken-link-note) has a finding
    assert report.health_score == pytest.approx(1 - 1 / 3)


def test_run_persists_findings_under_a_run_id(vault_root: Path, storage: StorageEngine):
    _entity(vault_root, "lonely", body="")
    rebuild_links(vault_root, storage)

    report = lint_run(vault_root, storage)

    count = storage.conn.execute(
        "SELECT COUNT(*) AS n FROM lint_findings WHERE run_id = ?;", (report.run_id,)
    ).fetchone()["n"]
    assert count == len(report.findings) > 0


def test_run_twice_keeps_both_runs_history(vault_root: Path, storage: StorageEngine):
    _entity(vault_root, "lonely", body="")
    rebuild_links(vault_root, storage)

    first = lint_run(vault_root, storage)
    second = lint_run(vault_root, storage)

    assert first.run_id != second.run_id
    total = storage.conn.execute("SELECT COUNT(*) AS n FROM lint_findings;").fetchone()["n"]
    assert total == len(first.findings) + len(second.findings)
