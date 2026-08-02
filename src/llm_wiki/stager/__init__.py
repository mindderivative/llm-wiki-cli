"""stager — watch raw/, archive originals, write staged working copies.

See INGEST_PLAN.md §1–§3. Split out of `ingest` so filesystem hygiene
never depends on the LLM pipeline being healthy. Owns exactly one
transition: a source file becomes a `STAGED` (or `FAILED`) `queue` row.
Everything from `QUEUED` onward belongs to `ingest`.

Two independent, single-purpose functions, composed by whatever triggers
staging (not yet built — CLI `ingest add` or the watcher handler):
`stage()` copies + records; `verify_and_clean()` confirms the copy and
removes the now-redundant original from `raw/`'s top level. Always call
both, in that order.
"""

from llm_wiki.stager.cleanup import verify_and_clean
from llm_wiki.stager.stager import stage

__all__ = ["stage", "verify_and_clean"]
