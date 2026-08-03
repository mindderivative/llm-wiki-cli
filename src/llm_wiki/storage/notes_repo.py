"""`Note` <-> `notes` row (de)serialization.

Same pattern as `queue_repo`/`chunk_repo`: no internal `.commit()`,
callers own the transaction boundary. `compiler.write_source_note()`
(INGEST_PLAN.md §11) inserts a note row and its chunk/embedding rows in
one `with storage.conn:` block, alongside the queue item's terminal
status write in `ingest.cascade()`. `list_note_rows()`/`delete_note_row()`
(GRAPH_LINT_PLAN.md §2) support `graph.rebuild_links()`'s filesystem
reconciliation — deletion detection and the isolated-note check need
every row, not just a slug lookup.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from llm_wiki.models import Note, NoteType
from llm_wiki.storage.engine import StorageEngine


def insert_note_row(storage: StorageEngine, note: Note) -> Note:
    """Insert `note` as a new `notes` row, returning it with `id` populated."""
    cursor = storage.conn.execute(
        """
        INSERT INTO notes (path, slug, type, title, tags, sources, content_hash, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (
            str(note.path),
            note.slug,
            note.type.value,
            note.title,
            json.dumps(note.tags),
            json.dumps(note.sources),
            note.content_hash,
            note.created_at.isoformat(),
            note.updated_at.isoformat(),
        ),
    )
    return note.model_copy(update={"id": cursor.lastrowid})


def get_note_row_by_slug(storage: StorageEngine, slug: str) -> Note | None:
    """The `notes` row with this `slug`, or `None` if there isn't one —
    used for slug-collision checking (INGEST_PLAN.md §11)."""
    row = storage.conn.execute("SELECT * FROM notes WHERE slug = ?;", (slug,)).fetchone()
    return _row_to_note(row) if row is not None else None


def update_note_row(storage: StorageEngine, note: Note) -> Note:
    """Update an existing `notes` row in place, by `id`.

    Updates every mutable field (`type`/`title`/`tags`/`sources`/
    `content_hash`/`updated_at`) — `path`/`slug`/`id`/`created_at` never
    change post-creation. Originally written narrower (only `tags`/
    `sources`/`content_hash`/`updated_at`) for `compiler.fan_out_mentions()`'s
    repeat-mention append path (INGEST_PLAN.md §12), which never changes
    `type`/`title` anyway — broadened for `graph.rebuild_links()`
    (GRAPH_LINT_PLAN.md §2), which reconciles a hand-edited note's
    frontmatter wholesale and may find `type`/`title` genuinely changed.
    Safe for both callers: one just always passes through its unchanged
    values for the newly-covered fields.
    """
    storage.conn.execute(
        """
        UPDATE notes
        SET type = ?, title = ?, tags = ?, sources = ?, content_hash = ?, updated_at = ?
        WHERE id = ?;
        """,
        (
            note.type.value,
            note.title,
            json.dumps(note.tags),
            json.dumps(note.sources),
            note.content_hash,
            note.updated_at.isoformat(),
            note.id,
        ),
    )
    return note


def list_note_rows(storage: StorageEngine) -> list[Note]:
    """Every `notes` row. Used by `graph.rebuild_links()`'s deletion
    detection (check each row's `path` still exists on disk) and
    `lint.run()`'s isolated-note check (GRAPH_LINT_PLAN.md §2/§3)."""
    rows = storage.conn.execute("SELECT * FROM notes;").fetchall()
    return [_row_to_note(row) for row in rows]


def delete_note_row(storage: StorageEngine, note_id: int) -> None:
    """Delete a `notes` row (cascades to its `chunks` via the existing
    FK). Does **not** touch `links` — `links` has no FK to `notes` by
    design (a link's target may legitimately not exist yet), so the
    caller is responsible for cleaning up any `links` rows mentioning
    this note's slug (`links_repo.delete_links_for_slug()`)."""
    storage.conn.execute("DELETE FROM notes WHERE id = ?;", (note_id,))


def _row_to_note(row) -> Note:
    return Note(
        id=row["id"],
        path=Path(row["path"]),
        slug=row["slug"],
        type=NoteType(row["type"]),
        title=row["title"],
        tags=json.loads(row["tags"]),
        sources=json.loads(row["sources"]),
        content_hash=row["content_hash"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )
