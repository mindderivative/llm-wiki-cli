"""`accept()` — step 2 of the pipeline: `STAGED` -> `QUEUED`.

See INGEST_PLAN.md §3. `stager` has already copied and archived the
file by the time this runs; `accept()`'s only job is to confirm both
copies `stage()` produced are still actually there and readable before
formally taking ownership of the item for the rest of the pipeline
(`atomize()`, `compile()`, cascade-update — all `ingest`'s from here on).

No `-ING` precursor for this step (see the state table in
INGEST_PLAN.md §3) — it's a single, fast check, not work worth showing
"in progress." Terminal outcome is `QUEUED` or `FAILED`
(`failed_at_step=QUEUED`), same one-write pattern as `stager.stage()`.
"""

from __future__ import annotations

from loguru import logger

from llm_wiki.models import QueueItem, QueueStatus, utcnow
from llm_wiki.storage import StorageEngine, update_queue_row


def accept(item: QueueItem, storage: StorageEngine) -> QueueItem:
    """Confirm `item`'s staged working copy and archive are both still
    readable, then flip `STAGED` -> `QUEUED`.

    No-ops (returns `item` unchanged) if `item.status != STAGED` — either
    it never reached staging successfully, or it's already past this
    step. Never raises for accept-domain failures (missing/unreadable
    file) — those come back as `FAILED` (`failed_at_step=QUEUED`), per
    INGEST_PLAN.md's failure contract.
    """
    if item.status != QueueStatus.STAGED:
        return item

    problem = _first_unreadable(item)
    if problem is not None:
        logger.warning(f"Accept failed for queue item {item.id}: {problem}")
        failed = item.model_copy(
            update={
                "status": QueueStatus.FAILED,
                "error": problem,
                "failed_at_step": QueueStatus.QUEUED,
                "updated_at": utcnow(),
            }
        )
        with storage.conn:
            return update_queue_row(storage, failed)

    accepted = item.model_copy(update={"status": QueueStatus.QUEUED, "updated_at": utcnow()})
    with storage.conn:
        return update_queue_row(storage, accepted)


def _first_unreadable(item: QueueItem) -> str | None:
    """The first problem found with `item`'s staged files, or `None` if
    both are present and readable."""
    if not item.raw_path.is_file():
        return f"staged working copy is missing: {item.raw_path}"
    if item.archive_path is None:
        return "archive_path was never set — stage() should have set it for a STAGED item"
    if not item.archive_path.is_file():
        return f"archived original is missing: {item.archive_path}"

    for path in (item.raw_path, item.archive_path):
        try:
            with path.open("rb"):
                pass
        except OSError as exc:
            return f"{path} exists but is not readable: {exc}"

    return None
