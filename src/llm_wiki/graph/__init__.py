"""graph — build/maintain the `links` table incrementally from `wiki/`.

`rebuild_links()` (GRAPH_LINT_PLAN.md §2) is the `/wiki-link` pipeline —
walks `wiki/`, reconciles `notes` against what's actually on disk, and
re-extracts `[[wikilink]]`s for anything new or changed.
"""

from llm_wiki.graph.link_engine import rebuild_links

__all__ = ["rebuild_links"]
