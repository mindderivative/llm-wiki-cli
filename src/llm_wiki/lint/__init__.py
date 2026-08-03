"""lint — schema validation, broken-link detection, isolated-note
detection, health scoring.

`run()` (GRAPH_LINT_PLAN.md §3) is the `/wiki-lint` pipeline. Reads
`notes`/`links` from the DB for two of its three checks; does its own
small filesystem walk for frontmatter schema validation. Contradiction
checks (ARCHITECTURE.md §7) need an LLM call and aren't built yet.
"""

from llm_wiki.lint.lint_engine import run

__all__ = ["run"]
