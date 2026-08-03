"""ingest — accept staged items, atomize into chunks, drive the pipeline
from `QUEUED` through `COMPLETED`.

See INGEST_PLAN.md §3 (state machine) and §9 (build order). Staging
(`STAGED`/`FAILED`) is `stager`'s job, not this package's — `ingest`
picks up from an already-`STAGED` item.

Built so far: `accept()` (step 2, `STAGED` -> `QUEUED`) and `atomize()`
(step 3, `QUEUED` -> `PARSING` -> `PARSED`), plaintext/Markdown only.
`compile()` (step 4, needs `llm`) and cascade-update (step 5, needs
`compiler`) aren't built yet — see INGEST_PLAN.md §9 for the rest of the
build order, including the pool/`--count` batch runner and CLI wiring
that will actually chain these functions together.
"""

from llm_wiki.ingest.accept import accept
from llm_wiki.ingest.atomize import atomize

__all__ = ["accept", "atomize"]
