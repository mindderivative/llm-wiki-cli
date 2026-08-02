"""vcs — init/stage/commit/push/pull/status via pygit2.

`GitEngine` lives here. In-process libgit2 bindings — no subprocess spawn
per Git op, avoiding the stdout-draining bug of the old `QProcess`
implementation by construction. Not yet implemented.
"""
