"""`atomize()` — step 3 of the pipeline: `QUEUED` -> `PARSING` -> `PARSED`.

See INGEST_PLAN.md §3 (state machine, atomicity contract) and §7 (only
plaintext/Markdown is scoped — non-text formats are a deferred feature,
not a gap in this module).

Deliberately the simplest chunker that's actually correct, not a
placeholder that fakes it: Markdown files split on top-level headings
(via `markdown-it-py`'s block tokenizer, so a `#` inside a fenced code
block is never mistaken for a heading); plaintext files become one
chunk. Real semantic/GEO-quality chunking is `compiler`'s job later,
once an LLM is in the loop — this pass only needs to produce structurally
sound `chunks` rows for `compile()` to work from.

Two-phase status write per the atomicity contract: `PARSING` commits
alone, before any chunking work starts (pure observability — no output
backs it yet). `PARSED` commits together with every chunk it produced,
in one transaction — a crash between them is impossible by construction,
not just unlikely.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from markdown_it import MarkdownIt

from llm_wiki.models import Chunk, IngestionError, QueueItem, QueueStatus, utcnow
from llm_wiki.storage import StorageEngine, insert_chunk_row, update_queue_row

_MARKDOWN_SUFFIXES = {".md", ".markdown"}
_PLAINTEXT_SUFFIXES = {".txt"}
_md = MarkdownIt()


def atomize(item: QueueItem, storage: StorageEngine) -> QueueItem:
    """Chunk `item.raw_path` into the `chunks` table.

    Accepts `QUEUED` (normal start) or `PARSING` (found parked mid-step
    after a crash — safe to redo from scratch, nothing was committed for
    it yet, per INGEST_PLAN.md §3's atomicity/recovery convention).
    No-ops (returns `item` unchanged) for any other status. Never raises
    for atomize-domain failures (unsupported format, unreadable file,
    invalid encoding, no content) — those come back as `FAILED`
    (`failed_at_step=PARSING`), per INGEST_PLAN.md's failure contract.
    """
    if item.status not in (QueueStatus.QUEUED, QueueStatus.PARSING):
        return item

    parsing = item.model_copy(update={"status": QueueStatus.PARSING, "updated_at": utcnow()})
    with storage.conn:
        update_queue_row(storage, parsing)

    try:
        chunks = _atomize_file(item.raw_path)
    except (OSError, UnicodeDecodeError, IngestionError) as exc:
        logger.warning(f"Atomize failed for queue item {item.id}: {exc}")
        failed = parsing.model_copy(
            update={
                "status": QueueStatus.FAILED,
                "error": str(exc),
                "failed_at_step": QueueStatus.PARSING,
                "updated_at": utcnow(),
            }
        )
        with storage.conn:
            return update_queue_row(storage, failed)

    parsed = parsing.model_copy(update={"status": QueueStatus.PARSED, "updated_at": utcnow()})
    with storage.conn:
        for ordinal, (title, content) in enumerate(chunks):
            insert_chunk_row(
                storage,
                Chunk(
                    queue_item_id=item.id,
                    ordinal=ordinal,
                    title=title,
                    content=content,
                    word_count=len(content.split()),
                ),
            )
        return update_queue_row(storage, parsed)


def _atomize_file(path: Path) -> list[tuple[str, str]]:
    """`[(chunk_title, chunk_content), ...]` for `path`, or raises
    `IngestionError` if the format isn't supported or there's nothing to
    chunk."""
    suffix = path.suffix.lower()
    if suffix not in _MARKDOWN_SUFFIXES | _PLAINTEXT_SUFFIXES:
        raise IngestionError(
            f"unsupported file type for atomize(): '{suffix or '(no extension)'}' "
            "— only plaintext/Markdown are supported for now (other formats "
            "are a deferred feature, see INGEST_PLAN.md §7)"
        )

    text = path.read_text(encoding="utf-8")
    fallback_title = path.stem

    chunks = (
        _chunk_markdown(text, fallback_title)
        if suffix in _MARKDOWN_SUFFIXES
        else _chunk_plaintext(text, fallback_title)
    )

    if not chunks:
        raise IngestionError(f"{path} has no content to chunk")

    return chunks


def _chunk_plaintext(text: str, fallback_title: str) -> list[tuple[str, str]]:
    content = text.strip()
    return [(fallback_title, content)] if content else []


def _chunk_markdown(text: str, fallback_title: str) -> list[tuple[str, str]]:
    """Split on top-level headings. Any content before the first heading
    becomes its own chunk under `fallback_title`. A file with no headings
    at all becomes a single chunk, same as plaintext."""
    headings = _markdown_headings(text)
    if not headings:
        return _chunk_plaintext(text, fallback_title)

    lines = text.splitlines()
    chunks: list[tuple[str, str]] = []

    preamble = "\n".join(lines[: headings[0][1]]).strip()
    if preamble:
        chunks.append((fallback_title, preamble))

    for idx, (title, start) in enumerate(headings):
        end = headings[idx + 1][1] if idx + 1 < len(headings) else len(lines)
        content = "\n".join(lines[start:end]).strip()
        if content:
            chunks.append((title or fallback_title, content))

    return chunks


def _markdown_headings(text: str) -> list[tuple[str, int]]:
    """`[(heading_text, start_line), ...]`, in document order. Uses
    `markdown-it-py`'s block tokenizer rather than a line regex
    specifically so a `#` inside a fenced code block is never
    mis-detected as a heading."""
    tokens = _md.parse(text)
    headings = []
    for i, tok in enumerate(tokens):
        if tok.type == "heading_open" and tok.map:
            title = tokens[i + 1].content.strip() if i + 1 < len(tokens) else ""
            headings.append((title, tok.map[0]))
    return headings
