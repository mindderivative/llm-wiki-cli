"""`cascade()` — step 5 of the pipeline: `ANALYZED` -> `CASCADING` -> `COMPLETED`.

See INGEST_PLAN.md §3 (state machine) and §11 (this step's design,
including why the terminal write is `COMPLETED` directly rather than a
separate durable `CASCADED` resting state — there's no real work between
them for a source-notes-only cascade).

This session only builds the always-happens part: writing a source note
for the compiled item via `compiler.write_source_note()`. Entity/concept
note creation (the actual "cascade" fan-out, append-only per §10) is a
later session — `ExtractionResult`'s `entities`/`concepts` stay pure
`queue_analysis` metadata for now, unused by this step.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from llm_wiki.compiler import write_source_note
from llm_wiki.llm.client import LlmClient
from llm_wiki.models import CompilationError, QueueItem, QueueStatus, utcnow
from llm_wiki.storage import StorageEngine, get_analysis_row, update_queue_row


def cascade(item: QueueItem, storage: StorageEngine, llm_client: LlmClient, vault_root: Path) -> QueueItem:
    """Write a source note for `item`'s compiled analysis, then flip
    `ANALYZED` -> `COMPLETED`.

    Accepts `ANALYZED` (normal start) or `CASCADING` (found parked
    mid-step after a crash — safe to redo from scratch, same recovery
    convention as `atomize()`/`compile()`). No-ops for any other status.
    Never raises for cascade-domain failures (note-write I/O errors,
    `llm_client.embed()` errors) — those come back as `FAILED`
    (`failed_at_step=CASCADING`), per the failure contract. See
    INGEST_PLAN.md §11 for the one known gap this doesn't solve: the
    note *file* write and the DB commit aren't atomic together, so a
    crash between them can leave a harmless orphaned file on disk.
    """
    if item.status not in (QueueStatus.ANALYZED, QueueStatus.CASCADING):
        return item

    cascading = item.model_copy(update={"status": QueueStatus.CASCADING, "updated_at": utcnow()})
    with storage.conn:
        update_queue_row(storage, cascading)

    analysis = get_analysis_row(storage, item.id)
    if analysis is None:
        logger.warning(f"Cascade failed for queue item {item.id}: no queue_analysis row found")
        failed = cascading.model_copy(
            update={
                "status": QueueStatus.FAILED,
                "error": "no queue_analysis row found for this item — compile() may not have run",
                "failed_at_step": QueueStatus.CASCADING,
                "updated_at": utcnow(),
            }
        )
        with storage.conn:
            return update_queue_row(storage, failed)

    completed = cascading.model_copy(update={"status": QueueStatus.COMPLETED, "updated_at": utcnow()})
    try:
        with storage.conn:
            write_source_note(item, analysis, vault_root, storage, llm_client)
            update_queue_row(storage, completed)
    except (OSError, CompilationError) as exc:
        logger.warning(f"Cascade failed for queue item {item.id}: {exc}")
        failed = cascading.model_copy(
            update={
                "status": QueueStatus.FAILED,
                "error": str(exc),
                "failed_at_step": QueueStatus.CASCADING,
                "updated_at": utcnow(),
            }
        )
        with storage.conn:
            return update_queue_row(storage, failed)

    return completed
