"""ingest — accept staged items, atomize into chunks, drive the pipeline
from `QUEUED` through `COMPLETED`.

See INGEST_PLAN.md §3 (state machine), §4 (resumability primitives), §9
(build order), and §10 (`compile()`'s design). Staging (`STAGED`/`FAILED`)
is `stager`'s job, not this package's — `ingest` picks up from an
already-`STAGED` item.

Built so far: `accept()` (step 2, `STAGED` -> `QUEUED`), `atomize()`
(step 3, `QUEUED` -> `PARSING` -> `PARSED`, plaintext/Markdown only), and
`compile()` (step 4, `PARSED` -> `ANALYZING` -> `ANALYZED`, summarize +
extract via an injected `LlmClient`) — plus the generic
`step_once()`/`advance()` dispatcher that drives any of them without the
caller needing to know which function handles which status.
`build_pipeline(llm_client)` is what wires `compile()` in, since it's
the only step with an external dependency the others don't need.
`cascade()` (step 5, `ANALYZED` -> `COMPLETED`, needs `compiler`) isn't
built yet — deliberately deferred, see §10. An item that reaches
`ANALYZED` currently just stays there; `step_once()`/`advance()` treat
that as "nothing more to do yet," not an error.
"""

from llm_wiki.ingest.accept import accept
from llm_wiki.ingest.atomize import atomize
from llm_wiki.ingest.compile import compile
from llm_wiki.ingest.pipeline import advance, build_pipeline, step_once

__all__ = ["accept", "atomize", "compile", "step_once", "advance", "build_pipeline"]
