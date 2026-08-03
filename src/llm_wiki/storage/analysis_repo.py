"""`Analysis` <-> `queue_analysis` row (de)serialization.

One row per queue item, holding `ingest.compile()`'s output (summary +
extracted entities/concepts) until `cascade()` (INGEST_PLAN.md §10 — not
built yet) consumes it. `INSERT OR REPLACE` on write — a retried
`compile()` overwrites whatever the previous attempt produced rather
than accumulating stale rows, same "redo from scratch" convention as
everywhere else in the pipeline (§3). No internal `.commit()` — same
pattern as `queue_repo`/`chunk_repo`: `compile()` writes this row and
its terminal `ANALYZED` status in one `with storage.conn:` block, per
the atomicity contract.
"""

from __future__ import annotations

import json
from datetime import datetime

from llm_wiki.models import Analysis, Mention
from llm_wiki.storage.engine import StorageEngine


def upsert_analysis_row(storage: StorageEngine, analysis: Analysis) -> Analysis:
    """Insert or overwrite the `queue_analysis` row for `analysis.queue_item_id`."""
    storage.conn.execute(
        """
        INSERT OR REPLACE INTO queue_analysis
            (queue_item_id, summary, entities, concepts, created_at)
        VALUES (?, ?, ?, ?, ?);
        """,
        (
            analysis.queue_item_id,
            analysis.summary,
            json.dumps([mention.model_dump() for mention in analysis.entities]),
            json.dumps([mention.model_dump() for mention in analysis.concepts]),
            analysis.created_at.isoformat(),
        ),
    )
    return analysis


def get_analysis_row(storage: StorageEngine, queue_item_id: int) -> Analysis | None:
    """The `queue_analysis` row for `queue_item_id`, or `None` if
    `compile()` hasn't produced one — either it hasn't run yet, or the
    item failed before reaching `ANALYZED`."""
    row = storage.conn.execute(
        "SELECT * FROM queue_analysis WHERE queue_item_id = ?;", (queue_item_id,)
    ).fetchone()
    if row is None:
        return None
    return Analysis(
        queue_item_id=row["queue_item_id"],
        summary=row["summary"],
        entities=[Mention(**m) for m in json.loads(row["entities"])],
        concepts=[Mention(**m) for m in json.loads(row["concepts"])],
        created_at=datetime.fromisoformat(row["created_at"]),
    )
