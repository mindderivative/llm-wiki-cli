"""ingest — accept staged items, atomize into chunks, drive the pipeline
from `QUEUED` through `COMPLETED`.

See INGEST_PLAN.md §3 (state machine), §4 (resumability primitives), §9
(build order), §10 (`compile()`'s design), and §11 (`cascade()`'s
design). Staging (`STAGED`/`FAILED`) is `stager`'s job, not this
package's — `ingest` picks up from an already-`STAGED` item.

Built so far: `accept()` (step 2), `atomize()` (step 3, plaintext/
Markdown only), `compile()` (step 4, summarize + extract via an injected
`LlmClient`), and `cascade()` (step 5, `ANALYZED` -> `CASCADING` ->
`COMPLETED` — writes a source note via `compiler.write_source_note()`;
entity/concept notes are a later session, §11) — plus the generic
`step_once()`/`advance()` dispatcher that drives any of them without the
caller needing to know which function handles which status.
`build_pipeline(llm_client, vault_root)` is what wires `compile()`/
`cascade()` in, since they're the only steps with external dependencies
the others don't need.
"""

from llm_wiki.ingest.accept import accept
from llm_wiki.ingest.atomize import atomize
from llm_wiki.ingest.cascade import cascade
from llm_wiki.ingest.compile import compile
from llm_wiki.ingest.pipeline import advance, build_pipeline, step_once

__all__ = ["accept", "atomize", "compile", "cascade", "step_once", "advance", "build_pipeline"]
