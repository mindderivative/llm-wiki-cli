"""SQL DDL for the derived SQLite + sqlite-vec cache (ARCHITECTURE.md §6).

Every table here is derived from `wiki/` and `raw/` and safe to drop —
losing this database must never lose information, only rebuild time
(ARCHITECTURE.md §2.1).
"""

from __future__ import annotations

SCHEMA_VERSION = 3  # v3: queue_analysis table (INGEST_PLAN.md §10)

CREATE_SCHEMA_META = """
CREATE TABLE IF NOT EXISTS schema_meta (
    id INTEGER PRIMARY KEY CHECK (id = 0),
    version INTEGER NOT NULL
);
"""

CREATE_QUEUE = """
CREATE TABLE IF NOT EXISTS queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    raw_path TEXT NOT NULL,
    archive_path TEXT,
    status TEXT NOT NULL DEFAULT 'STAGED',
    error TEXT,
    failed_at_step TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

CREATE_NOTES = """
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    slug TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    sources TEXT NOT NULL DEFAULT '[]',
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

CREATE_NOTES_SLUG_IDX = """
CREATE INDEX IF NOT EXISTS idx_notes_slug ON notes (slug);
"""

CREATE_CHUNKS = """
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id INTEGER REFERENCES notes (id) ON DELETE CASCADE,
    queue_item_id INTEGER REFERENCES queue (id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    word_count INTEGER NOT NULL,
    CHECK ((note_id IS NULL) <> (queue_item_id IS NULL))
);
"""

CREATE_CHUNKS_NOTE_IDX = """
CREATE INDEX IF NOT EXISTS idx_chunks_note_id ON chunks (note_id);
"""

CREATE_CHUNKS_QUEUE_IDX = """
CREATE INDEX IF NOT EXISTS idx_chunks_queue_item_id ON chunks (queue_item_id);
"""

CREATE_QUEUE_ANALYSIS = """
CREATE TABLE IF NOT EXISTS queue_analysis (
    queue_item_id INTEGER PRIMARY KEY REFERENCES queue (id) ON DELETE CASCADE,
    summary TEXT NOT NULL,
    entities TEXT NOT NULL DEFAULT '[]',
    concepts TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
"""

CREATE_LINKS = """
CREATE TABLE IF NOT EXISTS links (
    source_slug TEXT NOT NULL,
    target_slug TEXT NOT NULL,
    PRIMARY KEY (source_slug, target_slug)
);
"""

CREATE_LINT_FINDINGS = """
CREATE TABLE IF NOT EXISTS lint_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

CREATE_LINT_FINDINGS_RUN_IDX = """
CREATE INDEX IF NOT EXISTS idx_lint_findings_run_id ON lint_findings (run_id);
"""


def vec_chunks_ddl(embedding_dim: int) -> str:
    """DDL for the `sqlite-vec` virtual table — one row per `chunks.id`,
    joined by rowid (ARCHITECTURE.md §6).

    A function rather than a constant because vec0 bakes the embedding
    width into the column type: `float[{embedding_dim}]`.
    """
    return f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(embedding float[{embedding_dim}]);"


# Applied in order for a from-scratch schema. `vec_chunks` is handled
# separately by the engine since it depends on the sqlite-vec extension
# actually being loadable and on the configured embedding dimension.
CORE_TABLES = (
    CREATE_SCHEMA_META,
    CREATE_QUEUE,
    CREATE_NOTES,
    CREATE_NOTES_SLUG_IDX,
    CREATE_CHUNKS,
    CREATE_CHUNKS_NOTE_IDX,
    CREATE_CHUNKS_QUEUE_IDX,
    CREATE_QUEUE_ANALYSIS,
    CREATE_LINKS,
    CREATE_LINT_FINDINGS,
    CREATE_LINT_FINDINGS_RUN_IDX,
)

# Dropped by rebuild(), children before parents. Table names are fixed
# internal constants (not user input) — safe to interpolate into DDL.
ALL_TABLES_DROP_ORDER = (
    "lint_findings",
    "links",
    "vec_chunks",
    "chunks",
    "queue_analysis",
    "notes",
    "queue",
    "schema_meta",
)
