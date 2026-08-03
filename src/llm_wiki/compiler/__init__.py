"""compiler — turn compiled analysis into real `wiki/` notes.

`summarize`/`extract` ended up implemented directly in `ingest.compile()`
(no package-worthy logic to factor out — see INGEST_PLAN.md §11). This
package owns what's genuinely reusable: turning a `queue_analysis` row
into actual note files + DB rows.

`write_source_note()` (§11) handles the always-happens case, one note
per compiled source. `fan_out_mentions()` (§12) handles the actual
"cascade" — the append-only entity/concept note create-or-update for
everything `compile()` extracted.
"""

from llm_wiki.compiler.notes import fan_out_mentions, write_source_note

__all__ = ["write_source_note", "fan_out_mentions"]
