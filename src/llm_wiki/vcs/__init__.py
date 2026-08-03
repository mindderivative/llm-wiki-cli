"""vcs — init/stage/commit/push/pull/status via pygit2.

`GitEngine` lives here. In-process libgit2 bindings — no subprocess spawn
per Git op, avoiding the stdout-draining bug of the old `QProcess`
implementation by construction.

Minimal scope so far (INGEST_PLAN.md §9 build order item 5): `init()` +
`commit()`. `push()`/`pull()`/`status()` not built yet — see
`vcs/engine.py`'s docstring.
"""

from llm_wiki.vcs.engine import GitEngine

__all__ = ["GitEngine"]
