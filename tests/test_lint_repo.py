from pathlib import Path

import pytest

from llm_wiki.models import LintFinding
from llm_wiki.storage import StorageEngine, insert_lint_finding_row


@pytest.fixture
def storage(tmp_path: Path):
    with StorageEngine(tmp_path / "db.sqlite3") as engine:
        engine.init_schema()
        yield engine


def test_insert_lint_finding_row_populates_id(storage: StorageEngine):
    finding = LintFinding(run_id="run1", slug="acme-corp", kind="broken_link", message="target missing")

    with storage.conn:
        inserted = insert_lint_finding_row(storage, finding)

    assert inserted.id is not None
    row = storage.conn.execute("SELECT * FROM lint_findings WHERE id = ?;", (inserted.id,)).fetchone()
    assert row["run_id"] == "run1"
    assert row["slug"] == "acme-corp"
    assert row["kind"] == "broken_link"
    assert row["message"] == "target missing"


def test_insert_multiple_findings_for_same_run(storage: StorageEngine):
    with storage.conn:
        insert_lint_finding_row(
            storage, LintFinding(run_id="run1", slug="a", kind="broken_link", message="m1")
        )
        insert_lint_finding_row(
            storage, LintFinding(run_id="run1", slug="b", kind="isolated_note", message="m2")
        )

    count = storage.conn.execute(
        "SELECT COUNT(*) AS n FROM lint_findings WHERE run_id = ?;", ("run1",)
    ).fetchone()["n"]
    assert count == 2
