"""`Link` <-> `links` row (de)serialization.

`links` has no FK to `notes` (a link's target may legitimately not exist
yet — that's what `lint`'s `broken_link` finding is for) and no
surrogate `id` — its primary key is the `(source_slug, target_slug)`
pair itself (`storage/schema.py`). Same no-internal-`.commit()` pattern
as every other repo module; `graph.rebuild_links()` (GRAPH_LINT_PLAN.md
§2) owns the transaction boundary.
"""

from __future__ import annotations

from llm_wiki.models import Link
from llm_wiki.storage.engine import StorageEngine


def replace_links_for_source(storage: StorageEngine, source_slug: str, target_slugs: list[str]) -> None:
    """Replace every `links` row for `source_slug` with `target_slugs`.

    Delete-then-reinsert rather than diffing adds/removes separately —
    simpler to reason about and cheap at vault scale (GRAPH_LINT_PLAN.md
    §2). Deduplicates `target_slugs` (a note can only link to another
    note once, regardless of how many times `[[it]]` appears in the body).
    """
    storage.conn.execute("DELETE FROM links WHERE source_slug = ?;", (source_slug,))
    for target_slug in dict.fromkeys(target_slugs):
        storage.conn.execute(
            "INSERT OR IGNORE INTO links (source_slug, target_slug) VALUES (?, ?);",
            (source_slug, target_slug),
        )


def get_link_targets_for_source(storage: StorageEngine, source_slug: str) -> list[str]:
    """Every `target_slug` currently linked from `source_slug` — used by
    `graph.rebuild_links()` to compute an added/removed delta before
    calling `replace_links_for_source()` (GRAPH_LINT_PLAN.md §2)."""
    rows = storage.conn.execute(
        "SELECT target_slug FROM links WHERE source_slug = ?;", (source_slug,)
    ).fetchall()
    return [row["target_slug"] for row in rows]


def delete_links_for_slug(storage: StorageEngine, slug: str) -> None:
    """Delete every `links` row mentioning `slug`, as either endpoint —
    used when a note is deleted (`graph.rebuild_links()`'s deletion
    detection), since `links` has no FK to cascade this automatically."""
    storage.conn.execute(
        "DELETE FROM links WHERE source_slug = ? OR target_slug = ?;", (slug, slug)
    )


def list_all_links(storage: StorageEngine) -> list[Link]:
    """Every `links` row — `lint.run()`'s `broken_link`/`isolated_note`
    checks read the whole table once rather than querying per-note."""
    rows = storage.conn.execute("SELECT * FROM links;").fetchall()
    return [Link(source_slug=row["source_slug"], target_slug=row["target_slug"]) for row in rows]
