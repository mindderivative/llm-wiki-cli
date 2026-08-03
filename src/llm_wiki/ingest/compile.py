"""`compile()` — step 4 of the pipeline: `PARSED` -> `ANALYZING` -> `ANALYZED`.

See INGEST_PLAN.md §3 (state machine) and §10 (this step's design,
including why it stops at `ANALYZED` rather than going all the way to
`COMPLETED` — `cascade()`, step 5, is deliberately not built this
session).

Reads `chunks` already committed by `atomize()` (no reason to re-parse
the raw file — that's what `atomize()` is for), asks `llm_client` for a
summary and an entity/concept extraction, and commits both as a
`queue_analysis` row alongside the terminal `ANALYZED` status, in one
transaction, per the atomicity contract (§3).
"""

from __future__ import annotations

from loguru import logger

from llm_wiki.llm.client import LlmClient
from llm_wiki.models import Analysis, CompilationError, QueueItem, QueueStatus, utcnow
from llm_wiki.storage import (
    StorageEngine,
    list_chunks_for_queue_item,
    update_queue_row,
    upsert_analysis_row,
)


def compile(item: QueueItem, storage: StorageEngine, llm_client: LlmClient) -> QueueItem:
    """Summarize + extract entities/concepts from `item`'s chunks, then
    flip `PARSED` -> `ANALYZED`.

    Accepts `PARSED` (normal start) or `ANALYZING` (found parked mid-step
    after a crash — safe to redo from scratch, same recovery convention
    as `atomize()`/`PARSING`, §3). No-ops for any other status. Never
    raises for compile-domain failures (`llm_client` errors) — those come
    back as `FAILED` (`failed_at_step=ANALYZING`), per the failure
    contract.
    """
    if item.status not in (QueueStatus.PARSED, QueueStatus.ANALYZING):
        return item

    analyzing = item.model_copy(update={"status": QueueStatus.ANALYZING, "updated_at": utcnow()})
    with storage.conn:
        update_queue_row(storage, analyzing)

    chunks = list_chunks_for_queue_item(storage, item.id)
    text = "\n\n".join(f"## {chunk.title}\n\n{chunk.content}" for chunk in chunks)

    try:
        summary = llm_client.summarize(text)
        extraction = llm_client.extract(text)
    except CompilationError as exc:
        logger.warning(f"Compile failed for queue item {item.id}: {exc}")
        failed = analyzing.model_copy(
            update={
                "status": QueueStatus.FAILED,
                "error": str(exc),
                "failed_at_step": QueueStatus.ANALYZING,
                "updated_at": utcnow(),
            }
        )
        with storage.conn:
            return update_queue_row(storage, failed)

    analysis = Analysis(
        queue_item_id=item.id,
        summary=summary,
        entities=extraction.entities,
        concepts=extraction.concepts,
    )
    analyzed = analyzing.model_copy(update={"status": QueueStatus.ANALYZED, "updated_at": utcnow()})
    with storage.conn:
        upsert_analysis_row(storage, analysis)
        return update_queue_row(storage, analyzed)
