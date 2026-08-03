"""`run()` — the `/wiki-lint` pipeline (ARCHITECTURE.md §8).

See GRAPH_LINT_PLAN.md §3 for the full design. `broken_link`/
`isolated_note` read purely from the DB (`notes`/`links`, already
current thanks to `graph.rebuild_links()` having run first — this
function does **not** call it itself, per the "separate commands"
decision in GRAPH_LINT_PLAN.md). `schema_violation` does its own
filesystem walk — reuses `graph.link_engine`'s parsing helpers rather
than duplicating them, but deliberately isn't delegated *to* `graph`:
frontmatter validation is `lint`'s own stated responsibility
(ARCHITECTURE.md §7), and keeping it here means `graph`'s output stays
purely link data, not a mix of link data and validation findings.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from llm_wiki.graph.link_engine import UNREADABLE_ERRORS, iter_note_files, parse_note_metadata
from llm_wiki.models import Link, LintFinding, LintReport, Note
from llm_wiki.storage import StorageEngine, insert_lint_finding_row, list_all_links, list_note_rows


def run(vault_root: Path, storage: StorageEngine) -> LintReport:
    """Run every lint check once, persist each finding under a fresh
    `run_id`, and return a `LintReport` summarizing this pass.

    Findings from previous runs stay in `lint_findings` as history
    (ARCHITECTURE.md §6) — nothing here prunes them.
    """
    run_id = uuid.uuid4().hex[:12]
    notes = list_note_rows(storage)
    links = list_all_links(storage)
    note_slugs = {note.slug for note in notes}

    findings = [
        *_broken_link_findings(run_id, links, note_slugs),
        *_isolated_note_findings(run_id, notes, links),
        *_schema_violation_findings(run_id, vault_root),
    ]
    # One transaction for the whole run -- a run's findings are one
    # atomic unit, all tagged with the same run_id; a crash partway
    # through shouldn't leave a run_id with only some of its findings
    # persisted.
    with storage.conn:
        for finding in findings:
            insert_lint_finding_row(storage, finding)

    total_notes = len(notes)
    notes_with_findings = len({finding.slug for finding in findings})
    score = 1.0 if total_notes == 0 else 1 - (notes_with_findings / total_notes)

    return LintReport(run_id=run_id, findings=findings, total_notes=total_notes, health_score=score)


def _broken_link_findings(run_id: str, links: list[Link], note_slugs: set[str]) -> list[LintFinding]:
    return [
        LintFinding(
            run_id=run_id,
            slug=link.source_slug,
            kind="broken_link",
            message=f"Broken link to '{link.target_slug}' (note not found)",
        )
        for link in links
        if link.target_slug not in note_slugs
    ]


def _isolated_note_findings(run_id: str, notes: list[Note], links: list[Link]) -> list[LintFinding]:
    linked_slugs: set[str] = set()
    for link in links:
        linked_slugs.add(link.source_slug)
        linked_slugs.add(link.target_slug)
    return [
        LintFinding(
            run_id=run_id, slug=note.slug, kind="isolated_note", message="No incoming or outgoing links"
        )
        for note in notes
        if note.slug not in linked_slugs
    ]


def _schema_violation_findings(run_id: str, vault_root: Path) -> list[LintFinding]:
    findings = []
    for path in iter_note_files(vault_root):
        try:
            parse_note_metadata(path)
        except UNREADABLE_ERRORS as exc:
            findings.append(
                LintFinding(run_id=run_id, slug=path.stem, kind="schema_violation", message=str(exc))
            )
    return findings
