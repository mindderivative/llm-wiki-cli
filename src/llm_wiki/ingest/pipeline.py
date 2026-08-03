"""Generic step dispatcher — `step_once()` / `advance()` (INGEST_PLAN.md §4).

Maps a `QueueItem`'s current status to the function that advances it, so
no interface (CLI/MCP/GUI) needs its own hardcoded "what runs next"
logic. Only `accept()` (`STAGED`) and `atomize()` (`QUEUED`/`PARSING`)
are registered so far — `compile()`/cascade-update don't exist yet.
Landing on a status with nothing registered (e.g. `PARSED`, pending
`compile()`) is expected right now, not an error: `step_once()` just
returns the item unchanged, same as it would for a genuinely terminal
item. Callers distinguish the two by checking `item.status`, not by any
different return shape.

`PARSING` maps to the same handler as `QUEUED` (`atomize()` itself
accepts either) — an item found parked at `PARSING` after a crash has no
committed output yet, so redoing it from scratch is correct, per the
atomicity contract's recovery convention (INGEST_PLAN.md §3).
"""

from __future__ import annotations

from collections.abc import Callable

from llm_wiki.ingest.accept import accept
from llm_wiki.ingest.atomize import atomize
from llm_wiki.models import QueueItem, QueueStatus
from llm_wiki.storage import StorageEngine

_STEP_FOR_STATUS: dict[QueueStatus, Callable[[QueueItem, StorageEngine], QueueItem]] = {
    QueueStatus.STAGED: accept,
    QueueStatus.QUEUED: atomize,
    QueueStatus.PARSING: atomize,
}


def step_once(item: QueueItem, storage: StorageEngine) -> QueueItem:
    """Run exactly the step registered for `item`'s current status.

    Returns `item` unchanged if it's already terminal
    (`COMPLETED`/`FAILED`) or if nothing is registered yet for its
    status — both look identical to a caller (nothing happened); check
    `item.status` to tell them apart.
    """
    handler = _STEP_FOR_STATUS.get(item.status)
    if handler is None:
        return item
    return handler(item, storage)


def advance(item: QueueItem, storage: StorageEngine) -> QueueItem:
    """Call `step_once()` repeatedly until it stops making progress.

    Stops when `item` reaches a terminal status, or lands on a status
    with no next step registered yet (as far as the pipeline can
    currently take it) — `step_once()` returning the item unchanged is
    exactly that signal, so no separate termination check is needed.
    """
    while True:
        next_item = step_once(item, storage)
        if next_item.status == item.status:
            return next_item
        item = next_item
