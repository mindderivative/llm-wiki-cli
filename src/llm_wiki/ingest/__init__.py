"""ingest — accept staged items, atomize into chunks, drive the pipeline
from `QUEUED` through `COMPLETED`.

See INGEST_PLAN.md §3 (state machine), §4 (resumability primitives), and
§9 (build order). Staging (`STAGED`/`FAILED`) is `stager`'s job, not this
package's — `ingest` picks up from an already-`STAGED` item.

Built so far: `accept()` (step 2, `STAGED` -> `QUEUED`) and `atomize()`
(step 3, `QUEUED` -> `PARSING` -> `PARSED`), plaintext/Markdown only, plus
the generic `step_once()`/`advance()` dispatcher that drives either
without the caller needing to know which function handles which status.
`compile()` (step 4, needs `llm`) and cascade-update (step 5, needs
`compiler`) aren't built yet — an item that reaches `PARSED` currently
just stays there; `step_once()`/`advance()` treat that as "nothing more
to do yet," not an error.
"""

from llm_wiki.ingest.accept import accept
from llm_wiki.ingest.atomize import atomize
from llm_wiki.ingest.pipeline import advance, step_once

__all__ = ["accept", "atomize", "step_once", "advance"]
