"""`LintFinding` <-> `lint_findings` row (de)serialization.

Same no-internal-`.commit()` pattern as every other repo module.
`lint.run()` (GRAPH_LINT_PLAN.md §3) owns the transaction boundary and
the `run_id` each finding is tagged with. Old runs' findings stay in the
table as history (ARCHITECTURE.md §6) — nothing here prunes them.
"""

from __future__ import annotations

from llm_wiki.models import LintFinding
from llm_wiki.storage.engine import StorageEngine


def insert_lint_finding_row(storage: StorageEngine, finding: LintFinding) -> LintFinding:
    """Insert `finding` as a new `lint_findings` row, returning it with
    `id` populated."""
    cursor = storage.conn.execute(
        """
        INSERT INTO lint_findings (run_id, slug, kind, message, created_at)
        VALUES (?, ?, ?, ?, ?);
        """,
        (finding.run_id, finding.slug, finding.kind, finding.message, finding.created_at.isoformat()),
    )
    return finding.model_copy(update={"id": cursor.lastrowid})
