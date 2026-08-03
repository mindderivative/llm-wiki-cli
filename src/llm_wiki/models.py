"""Domain models and typed exceptions shared across CLI/MCP/GUI.

Single source of truth for shapes that cross layer boundaries
(ARCHITECTURE.md §3, §4). Table-backed models mirror the SQLite schema in
§6 field-for-field so `storage` can (de)serialize rows without a separate
DTO layer.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------


class QueueStatus(str, enum.Enum):
    """Ingestion queue item lifecycle (INGEST_PLAN.md §3 state machine).

    Owned by `stager` through `STAGED`, by `ingest` from `QUEUED` onward.
    The `-ING` values are pre-work markers only — no committed output
    backs them yet, so finding one after a crash means "safe to retry
    from scratch" (INGEST_PLAN.md §3, atomicity contract). `FAILED` is
    terminal for whichever step raised; see `QueueItem.failed_at_step`
    for which one, structured rather than parsed out of `error`.
    """

    STAGED = "STAGED"        # stager: archived + working copy written
    QUEUED = "QUEUED"        # ingest: staged item accepted
    PARSING = "PARSING"      # ingest: atomize() in progress
    PARSED = "PARSED"        # ingest: chunks committed
    ANALYZING = "ANALYZING"  # ingest: compile() in progress
    ANALYZED = "ANALYZED"    # ingest: summary + extraction committed
    CASCADING = "CASCADING"  # ingest: cascade-update in progress
    CASCADED = "CASCADED"    # ingest: note writes + embeddings committed
    COMPLETED = "COMPLETED"  # ingest: pipeline finished
    FAILED = "FAILED"        # either: terminal, see failed_at_step + error


class NoteType(str, enum.Enum):
    """Matches the `wiki/` subdirectories in ARCHITECTURE.md §5."""

    SOURCE = "source"
    ENTITY = "entity"
    CONCEPT = "concept"
    SYNTHESIS = "synthesis"


# --------------------------------------------------------------------------
# Table-backed domain models (ARCHITECTURE.md §6)
# --------------------------------------------------------------------------


class QueueItem(BaseModel):
    """One row of the `queue` table — an in-flight ingestion job.

    `status` defaults to `STAGED` — the earliest state a row is actually
    created in (by `stager`, once a file is safely archived + staged).
    `failed_at_step` is set alongside `status=FAILED` + `error`, and
    cleared back to `None` by an explicit `retry` — see
    INGEST_PLAN.md §3.
    """

    id: int | None = None
    title: str
    raw_path: Path
    archive_path: Path | None = None
    status: QueueStatus = QueueStatus.STAGED
    error: str | None = None
    failed_at_step: QueueStatus | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Note(BaseModel):
    """One row of the `notes` table — a Markdown note in `wiki/`.

    `content_hash` gates incremental link/lint passes: a note is only
    re-parsed if its hash changed since the last run.
    """

    id: int | None = None
    path: Path
    slug: str
    type: NoteType
    title: str
    tags: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    content_hash: str
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Chunk(BaseModel):
    """One atomic GEO chunk (`chunks` table). Belongs to exactly one of
    a queue item (pre-compile) or a note (post-compile)."""

    id: int | None = None
    note_id: int | None = None
    queue_item_id: int | None = None
    ordinal: int
    title: str
    content: str
    word_count: int


class Mention(BaseModel):
    """One entity or concept `ingest.compile()`'s `extract()` call found in
    a source, plus a short note of what *this specific text* says about
    it. Shared by `Analysis` (storage shape) and `llm.client.ExtractionResult`
    (LLM output shape) — one shape, not two (INGEST_PLAN.md §12). Kept
    deliberately thin: a name and one sentence, not a full profile — real
    synthesis is `compiler.fan_out_mentions()`'s job, not extraction's.
    """

    name: str
    note: str


class Analysis(BaseModel):
    """One row of the `queue_analysis` table — `ingest.compile()`'s
    output (summary + extracted entities/concepts) for one queue item.

    Staged here until `cascade()` reads it and merges it into `wiki/`
    notes (INGEST_PLAN.md §11/§12). One row per queue item; a retried
    `compile()` overwrites it (`INSERT OR REPLACE`) rather than
    accumulating stale attempts, matching the pipeline's general "redo a
    step from scratch" recovery convention (§3).
    """

    queue_item_id: int
    summary: str
    entities: list[Mention] = Field(default_factory=list)
    concepts: list[Mention] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)


class Link(BaseModel):
    """One `[[wikilink]]` edge (`links` table)."""

    source_slug: str
    target_slug: str


class LintFinding(BaseModel):
    """One issue surfaced by a lint run (`lint_findings` table)."""

    run_id: str
    slug: str
    kind: str  # "broken_link" | "schema_violation" | "isolated_note" | ...
    message: str
    created_at: datetime = Field(default_factory=utcnow)


# --------------------------------------------------------------------------
# Typed exceptions (ARCHITECTURE.md §10)
#
# Engine functions raise these rather than returning None/False on
# failure, so every interface layer can handle failures explicitly
# instead of guessing from a falsy return.
# --------------------------------------------------------------------------


class LlmWikiError(Exception):
    """Base class for all engine-raised errors."""


class VaultNotFoundError(LlmWikiError):
    """Raised when a path does not contain a valid `.llm-wiki-config`."""


class VaultAlreadyExistsError(LlmWikiError):
    """Raised when initializing a vault over an already-initialized one."""


class IngestionError(LlmWikiError):
    """Raised when staging, archiving, or enqueueing a raw source fails."""


class CompilationError(LlmWikiError):
    """Raised when the summarize/extract/cascade pipeline fails."""


class LintError(LlmWikiError):
    """Raised when the lint pipeline itself cannot complete.

    Not to be confused with a `LintFinding` — a finding is an expected
    result, not a failure of the lint run.
    """


class StorageError(LlmWikiError):
    """Raised on SQLite/sqlite-vec cache read/write failures."""


class GitError(LlmWikiError):
    """Raised when a pygit2-backed VCS operation fails."""
