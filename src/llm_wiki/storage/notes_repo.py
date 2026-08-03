"""`Note` <-> `notes` row (de)serialization.

Same pattern as `queue_repo`/`chunk_repo`: no internal `.commit()`,
callers own the transaction boundary. `compiler.write_source_note()`
(INGEST_PLAN.md §11) inserts a note row and its chunk/embedding rows in
one `with storage.conn:` block, alongside the queue item's terminal
status write in `ingest.cascade()`.
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

    Only `tags`/`sources`/`content_hash`/`updated_at` are expected to
    change post-creation — `path`/`slug`/`type`/`title` are set once at
    creation and never rewritten here. Used by
    `compiler.fan_out_mentions()`'s repeat-mention append path
    (INGEST_PLAN.md §12); the caller is responsible for having already
    merged the new `sources` entry and recomputed `content_hash` before
    calling this.
    """
    storage.conn.execute(
        """
        UPDATE notes
        SET tags = ?, sources = ?, content_hash = ?, updated_at = ?
        WHERE id = ?;
        """,
        (
            json.dumps(note.tags),
            json.dumps(note.sources),
            note.content_hash,
            note.updated_at.isoformat(),
            note.id,
        ),
    )
    return note


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
