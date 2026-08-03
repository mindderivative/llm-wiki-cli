"""compiler — turn compiled analysis into real `wiki/` notes.

`summarize`/`extract` ended up implemented directly in `ingest.compile()`
(no package-worthy logic to factor out — see INGEST_PLAN.md §11). This
package owns what's genuinely reusable: turning a `queue_analysis` row
into an actual note file + DB rows. `write_source_note()` (this session)
handles the always-happens case, one note per compiled source.
Entity/concept note creation + the append-only cascade-merge (§10) is a
later session — not built yet.
"""

from llm_wiki.compiler.notes import write_source_note

__all__ = ["write_source_note"]
