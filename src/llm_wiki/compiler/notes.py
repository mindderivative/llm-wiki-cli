"""`write_source_note()` + `fan_out_mentions()` — turn `compile()`'s
output into real `wiki/` notes.

See INGEST_PLAN.md §11 (source notes) and §12 (entity/concept fan-out —
the actual "cascade") for the full design. `ingest.cascade()` owns the
`ANALYZED` -> `CASCADING` -> `COMPLETED` state transition and calls both;
this module owns turning a `queue_analysis` row into actual `wiki/` files
plus their `notes`/`chunks`/`vec_chunks` rows.

Both functions raise rather than catching their own failures — same
"helper raises, step function catches" split already used between
`atomize()`'s private `_atomize_file()` and `atomize()` itself.
`ingest.cascade()` is what converts an exception here into
`FAILED`/`failed_at_step=CASCADING`.
"""

from __future__ import annotations

from pathlib import Path

import frontmatter

from llm_wiki.llm.client import LlmClient
from llm_wiki.models import NOTE_TYPE_FOLDERS, Analysis, Chunk, Mention, Note, NoteType, QueueItem, utcnow
from llm_wiki.storage import (
    StorageEngine,
    get_note_row_by_slug,
    insert_chunk_row,
    insert_embedding,
    insert_note_row,
    update_note_row,
)
from llm_wiki.textutil import content_hash, slugify


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
    slug = _unique_slug(storage, vault_root, NoteType.SOURCE, slugify(item.title))
    path = _note_path(vault_root, NoteType.SOURCE, slug)

    text = _render_source_file(item, analysis)
    _write_atomic(path, text)

    note = Note(
        path=path,
        slug=slug,
        type=NoteType.SOURCE,
        title=item.title,
        tags=[],
        sources=[item.title],
        content_hash=content_hash(text),
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


def fan_out_mentions(
    item: QueueItem,
    analysis: Analysis,
    source_note: Note,
    vault_root: Path,
    storage: StorageEngine,
    llm_client: LlmClient,
) -> list[Note]:
    """For every entity/concept `compile()` extracted, create or update
    its `wiki/entities/`/`wiki/concepts/` note — the actual "cascade"
    half of item 4b (INGEST_PLAN.md §12).

    A brand-new entity/concept gets a stub note (frontmatter + one
    "Mentioned in" bullet, embedded once). An already-existing one gets a
    new bullet appended to its "Mentioned in" list — append-only, no
    LLM rewrite of existing content, no embedding refresh (both decided
    in §12). `source_note` is `write_source_note()`'s already-committed
    result for this item — its slug is what each bullet's `[[wikilink]]`
    points to.

    Dedupes `analysis.entities`/`.concepts` by slug within this one
    item's extraction first, so the same name mentioned twice by one
    source doesn't double-append.
    """
    mentions = _dedupe_by_slug(analysis.entities, NoteType.ENTITY) + _dedupe_by_slug(
        analysis.concepts, NoteType.CONCEPT
    )
    return [
        _upsert_mention_note(mention, note_type, item, source_note, vault_root, storage, llm_client)
        for mention, note_type in mentions
    ]


def _dedupe_by_slug(mentions: list[Mention], note_type: NoteType) -> list[tuple[Mention, NoteType]]:
    seen: dict[str, Mention] = {}
    for mention in mentions:
        seen.setdefault(slugify(mention.name), mention)
    return [(mention, note_type) for mention in seen.values()]


def _upsert_mention_note(
    mention: Mention,
    note_type: NoteType,
    item: QueueItem,
    source_note: Note,
    vault_root: Path,
    storage: StorageEngine,
    llm_client: LlmClient,
) -> Note:
    slug = slugify(mention.name)
    existing = get_note_row_by_slug(storage, slug)

    if existing is not None and existing.type == note_type:
        return _append_mention(existing, mention, item, source_note, storage)

    if existing is not None:
        # Genuine cross-type slug collision (rare) -- same handling as
        # two same-titled sources in write_source_note() (§11), just
        # keyed on note_type's own folder for the filesystem check.
        slug = _unique_slug(storage, vault_root, note_type, slug)

    return _create_mention_note(slug, mention, note_type, item, source_note, vault_root, storage, llm_client)


def _create_mention_note(
    slug: str,
    mention: Mention,
    note_type: NoteType,
    item: QueueItem,
    source_note: Note,
    vault_root: Path,
    storage: StorageEngine,
    llm_client: LlmClient,
) -> Note:
    path = _note_path(vault_root, note_type, slug)
    text = _render_mention_stub(mention, note_type, item, source_note)
    _write_atomic(path, text)

    note = Note(
        path=path,
        slug=slug,
        type=note_type,
        title=mention.name,
        tags=[],
        sources=[item.title],
        content_hash=content_hash(text),
    )
    inserted = insert_note_row(storage, note)

    chunk = insert_chunk_row(
        storage,
        Chunk(
            note_id=inserted.id,
            ordinal=0,
            title=mention.name,
            content=mention.note,
            word_count=len(mention.note.split()),
        ),
    )

    # Embed once, at creation, per §12 decision 2 -- never refreshed on
    # later appends, even though the file's body keeps growing.
    [vector] = llm_client.embed([mention.note])
    insert_embedding(storage, chunk.id, vector)

    return inserted


def _append_mention(
    existing: Note,
    mention: Mention,
    item: QueueItem,
    source_note: Note,
    storage: StorageEngine,
) -> Note:
    """Append one "Mentioned in" bullet to an already-existing entity/
    concept note. Idempotent: if `item.title` is already in
    `existing.sources`, this is a no-op crash-retry safety net
    (INGEST_PLAN.md §12) — `cascade()` re-running `fan_out_mentions()`
    after a crash parked at `CASCADING` must not duplicate the bullet.
    """
    if item.title in existing.sources:
        return existing

    post = frontmatter.loads(existing.path.read_text(encoding="utf-8"))
    new_sources = [*existing.sources, item.title]
    post["sources"] = new_sources
    post.content = post.content.rstrip("\n") + "\n" + _mention_bullet(item, source_note, mention) + "\n"
    text = frontmatter.dumps(post) + "\n"
    _write_atomic(existing.path, text)

    updated = existing.model_copy(
        update={"sources": new_sources, "content_hash": content_hash(text), "updated_at": utcnow()}
    )
    return update_note_row(storage, updated)


def _render_source_file(item: QueueItem, analysis: Analysis) -> str:
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


def _render_mention_stub(mention: Mention, note_type: NoteType, item: QueueItem, source_note: Note) -> str:
    body = f"# {mention.name}\n\n## Mentioned in\n\n{_mention_bullet(item, source_note, mention)}\n"
    post = frontmatter.Post(
        body,
        type=note_type.value,
        title=mention.name,
        tags=[],
        sources=[item.title],
    )
    return frontmatter.dumps(post) + "\n"


def _mention_bullet(item: QueueItem, source_note: Note, mention: Mention) -> str:
    date = utcnow().strftime("%Y-%m-%d")
    return f"- {date}: [[{source_note.slug}]] ({item.title}) — {mention.note}"


def _write_atomic(path: Path, text: str) -> None:
    """Write-temp-then-rename — `path.replace()` is an atomic rename on
    POSIX, satisfying INGEST_PLAN.md §5's requirement for cascade note
    writes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(path)


def _unique_slug(storage: StorageEngine, vault_root: Path, note_type: NoteType, base_slug: str) -> str:
    slug = base_slug
    n = 2
    while (
        get_note_row_by_slug(storage, slug) is not None or _note_path(vault_root, note_type, slug).exists()
    ):
        slug = f"{base_slug}-{n}"
        n += 1
    return slug


def _note_path(vault_root: Path, note_type: NoteType, slug: str) -> Path:
    return vault_root / "wiki" / NOTE_TYPE_FOLDERS[note_type] / f"{slug}.md"
