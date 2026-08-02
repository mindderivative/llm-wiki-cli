"""stager — watch raw/, archive originals, write staged working copies.

See INGEST_PLAN.md §1–§3. Split out of `ingest` so filesystem hygiene
never depends on the LLM pipeline being healthy. Owns exactly one
transition: a source file becomes a `STAGED` (or `FAILED`) `queue` row.
Everything from `QUEUED` onward belongs to `ingest`.
"""

from llm_wiki.stager.stager import stage

__all__ = ["stage"]
