"""`write_source_note()` — turn `compile()`'s output into a real `wiki/` note.

See INGEST_PLAN.md §11 for the full design. `ingest.cascade()` owns the
`ANALYZED` -> `CASCADING` -> `COMPLETED` state transition and calls this;
this module owns turning a `queue_analysis` row into an actual
`wiki/sources/{slug}.md` file plus its `notes`/`chunks`/`vec_chunks` rows.

Deliberately excludes entities/concepts from the note body — inventing a
`[[wikilink]]` format to notes that don't exist yet (entity/concept
notes are a later session, §11) felt premature. They stay in
`queue_analysis` until that session actually needs them.

Raises rather than catching its own failures — same "helper raises, step
function catches" split already used between `atomize()`'s private
`_atomize_file()` and `atomize()` itself. `ingest.cascade()` is what
converts an exception here into `FAILED`/`failed_at_step=CASCADING`.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import frontmatter

from llm_wiki.llm.client import LlmClient
from llm_wiki.models import Analysis, Chunk, Note, NoteType, QueueItem, utcnow
from llm_wiki.storage import (
    StorageEngine,
    get_note_row_by_slug,
    insert_chunk_row,
    insert_embedding,
    insert_note_row,
)
from llm_wiki.textutil import slugify


def write_source_note(
    item: QueueItem,
    analysis: Analysis,
    vault_root: Path,
    storage: StorageEngine,
    llm_client: LlmClient,
) -> Note:
    """Write `wiki/sources/{slug}.md` for `item`, plus its `notes` row, a
    single chunk (the summary — a short synthesized paragraph doesn't
    need further splitting, same "simplest chunker that's actually
    correct" reasoning as `atomize()`), and that chunk's embedding.

    Slug collisions (two distinct sources sharing a title) get a `-2`,
    `-3`, ... suffix — same `_unique_path`-style handling `stager.stage()`
    already uses, checked against both the `notes` table and the
    filesystem (INGEST_PLAN.md §11).
    """
    slug = _unique_slug(storage, vault_root, slugify(item.title))
    path = vault_root / "wiki" / "sources" / f"{slug}.md"

    text = _render_file(item, analysis)
    _write_atomic(path, text)

    note = Note(
        path=path,
        slug=slug,
        type=NoteType.SOURCE,
        title=item.title,
        tags=[],
        sources=[item.title],
        content_hash=_sha256_text(text),
    )
    inserted = insert_note_row(storage, note)

    chunk = insert_chunk_row(
        storage,
        Chunk(
            note_id=inserted.id,
            ordinal=0,
            title=item.title,
            content=analysis.summary,
            word_count=len(analysis.summary.split()),
        ),
    )

    [vector] = llm_client.embed([analysis.summary])
    insert_embedding(storage, chunk.id, vector)

    return inserted


def _render_file(item: QueueItem, analysis: Analysis) -> str:
    archive = item.archive_path or item.raw_path
    body = (
        f"# {item.title}\n\n"
        f"{analysis.summary}\n\n"
        "## Source\n\n"
        f"- Original: `{archive}`\n"
        f"- Ingested: {utcnow().strftime('%Y-%m-%d')}\n"
    )
    post = frontmatter.Post(
        body,
        type=NoteType.SOURCE.value,
        title=item.title,
        tags=[],
        sources=[item.title],
    )
    return frontmatter.dumps(post) + "\n"


def _write_atomic(path: Path, text: str) -> None:
    """Write-temp-then-rename — `path.replace()` is an atomic rename on
    POSIX, satisfying INGEST_PLAN.md §5's requirement for cascade note
    writes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(path)


def _unique_slug(storage: StorageEngine, vault_root: Path, base_slug: str) -> str:
    slug = base_slug
    n = 2
    while get_note_row_by_slug(storage, slug) is not None or _note_path(vault_root, slug).exists():
        slug = f"{base_slug}-{n}"
        n += 1
    return slug


def _note_path(vault_root: Path, slug: str) -> Path:
    return vault_root / "wiki" / "sources" / f"{slug}.md"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
