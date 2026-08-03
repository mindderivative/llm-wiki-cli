"""Generic step dispatcher — `step_once()` / `advance()` (INGEST_PLAN.md §4).

Maps a `QueueItem`'s current status to the function that advances it, so
no interface (CLI/MCP/GUI) needs its own hardcoded "what runs next"
logic. `accept()` (`STAGED`), `atomize()` (`QUEUED`/`PARSING`),
`compile()` (`PARSED`/`ANALYZING`), and `cascade()`
(`ANALYZED`/`CASCADING`) are registered. Landing on a status with
nothing registered (e.g. `COMPLETED`/`FAILED` — genuinely terminal, or
some future status not built yet) is expected, not an error:
`step_once()` just returns the item unchanged. Callers distinguish the
two by checking `item.status`, not by any different return shape.

`PARSING` maps to the same handler as `QUEUED`; `ANALYZING` maps to the
same handler as `PARSED`; `CASCADING` maps to the same handler as
`ANALYZED` — an item found parked at any `-ING` status after a crash has
no committed output yet, so redoing it from scratch is correct, per the
atomicity contract's recovery convention (INGEST_PLAN.md §3).

`compile()` needs an `llm_client`; `cascade()` needs both an
`llm_client` (for `embed()`) and `vault_root` (to know where `wiki/`
is) — real external dependencies the earlier steps don't have. Rather
than force `accept()`/`atomize()` to accept-and-ignore parameters they
don't need, `build_pipeline(llm_client, vault_root)` returns a dispatch
table with `compile()`/`cascade()` bound in via `functools.partial`.
`step_once()`/`advance()` accept an optional `dispatch_table` override;
omitting it uses the base table below (`STAGED`/`QUEUED`/`PARSING`
only) unchanged — every existing call site and test keeps working
exactly as before. Only callers that actually have an `llm_client`/
`vault_root` available (the CLI) need to know `build_pipeline()` exists.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from pathlib import Path

from llm_wiki.ingest.accept import accept
from llm_wiki.ingest.atomize import atomize
from llm_wiki.ingest.cascade import cascade as cascade_step
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


def build_pipeline(
    llm_client: LlmClient | None = None, vault_root: Path | None = None
) -> dict[QueueStatus, _StepFn]:
    """The dispatch table for one run.

    Without an `llm_client`, this is exactly the base table —
    `compile()`/`cascade()` stay unregistered, so `PARSED`/`ANALYZING`/
    `ANALYZED`/`CASCADING` items no-op, same as any other
    not-yet-implemented step. With an `llm_client`, `compile()` gets
    bound in. `cascade()` additionally needs `vault_root`, so it's only
    bound in when *both* are given.
    """
    table = dict(_STEP_FOR_STATUS)
    if llm_client is not None:
        bound_compile = functools.partial(compile_step, llm_client=llm_client)
        table[QueueStatus.PARSED] = bound_compile
        table[QueueStatus.ANALYZING] = bound_compile
        if vault_root is not None:
            bound_cascade = functools.partial(cascade_step, llm_client=llm_client, vault_root=vault_root)
            table[QueueStatus.ANALYZED] = bound_cascade
            table[QueueStatus.CASCADING] = bound_cascade
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
