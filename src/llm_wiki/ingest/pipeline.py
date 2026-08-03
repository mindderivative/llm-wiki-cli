"""Generic step dispatcher — `step_once()` / `advance()` (INGEST_PLAN.md §4).

Maps a `QueueItem`'s current status to the function that advances it, so
no interface (CLI/MCP/GUI) needs its own hardcoded "what runs next"
logic. `accept()` (`STAGED`), `atomize()` (`QUEUED`/`PARSING`), and
`compile()` (`PARSED`/`ANALYZING`) are registered — `cascade()`
(`ANALYZED` onward) doesn't exist yet (INGEST_PLAN.md §10). Landing on a
status with nothing registered (e.g. `ANALYZED`, pending `cascade()`) is
expected right now, not an error: `step_once()` just returns the item
unchanged, same as it would for a genuinely terminal item. Callers
distinguish the two by checking `item.status`, not by any different
return shape.

`PARSING` maps to the same handler as `QUEUED` (`atomize()` itself
accepts either); `ANALYZING` maps to the same handler as `PARSED`
(`compile()` accepts either) — an item found parked at either `-ING`
status after a crash has no committed output yet, so redoing it from
scratch is correct, per the atomicity contract's recovery convention
(INGEST_PLAN.md §3).

`compile()` needs an `llm_client` the other two steps don't — an actual
external dependency, not something derivable from the row itself. Rather
than force `accept()`/`atomize()` to accept-and-ignore a parameter they
don't need, `build_pipeline(llm_client)` returns a dispatch table with
`compile()` bound in via `functools.partial`. `step_once()`/`advance()`
accept an optional `dispatch_table` override; omitting it uses the base
table below (`STAGED`/`QUEUED`/`PARSING` only) unchanged — every existing
call site and test keeps working exactly as before. Only callers that
actually have an `llm_client` available (the CLI) need to know
`build_pipeline()` exists.
"""

from __future__ import annotations

import functools
from collections.abc import Callable

from llm_wiki.ingest.accept import accept
from llm_wiki.ingest.atomize import atomize
from llm_wiki.ingest.compile import compile as compile_step
from llm_wiki.llm.client import LlmClient
from llm_wiki.models import QueueItem, QueueStatus
from llm_wiki.storage import StorageEngine

_StepFn = Callable[[QueueItem, StorageEngine], QueueItem]

_STEP_FOR_STATUS: dict[QueueStatus, _StepFn] = {
    QueueStatus.STAGED: accept,
    QueueStatus.QUEUED: atomize,
    QueueStatus.PARSING: atomize,
}


def build_pipeline(llm_client: LlmClient | None = None) -> dict[QueueStatus, _StepFn]:
    """The dispatch table for one run.

    Without an `llm_client`, this is exactly the base table —
    `compile()` stays unregistered, so `PARSED`/`ANALYZING` items no-op,
    same as any other not-yet-implemented step. With one, `compile()` is
    bound in via `functools.partial` for both its statuses.
    """
    table = dict(_STEP_FOR_STATUS)
    if llm_client is not None:
        bound_compile = functools.partial(compile_step, llm_client=llm_client)
        table[QueueStatus.PARSED] = bound_compile
        table[QueueStatus.ANALYZING] = bound_compile
    return table


def step_once(
    item: QueueItem,
    storage: StorageEngine,
    *,
    dispatch_table: dict[QueueStatus, _StepFn] | None = None,
) -> QueueItem:
    """Run exactly the step registered for `item`'s current status.

    Returns `item` unchanged if it's already terminal
    (`COMPLETED`/`FAILED`) or if nothing is registered yet for its
    status — both look identical to a caller (nothing happened); check
    `item.status` to tell them apart. `dispatch_table` defaults to the
    base table (see `build_pipeline()` for the one that also knows about
    `compile()`).
    """
    table = dispatch_table if dispatch_table is not None else _STEP_FOR_STATUS
    handler = table.get(item.status)
    if handler is None:
        return item
    return handler(item, storage)


def advance(
    item: QueueItem,
    storage: StorageEngine,
    *,
    dispatch_table: dict[QueueStatus, _StepFn] | None = None,
) -> QueueItem:
    """Call `step_once()` repeatedly until it stops making progress.

    Stops when `item` reaches a terminal status, or lands on a status
    with no next step registered yet (as far as this dispatch table can
    currently take it) — `step_once()` returning the item unchanged is
    exactly that signal, so no separate termination check is needed.
    """
    while True:
        next_item = step_once(item, storage, dispatch_table=dispatch_table)
        if next_item.status == item.status:
            return next_item
        item = next_item
