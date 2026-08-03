"""`GitEngine` — init/commit via `pygit2` (INGEST_PLAN.md §5).

Minimal scope for now: `init()` (idempotent repo + `.gitignore` setup) and
`commit()` (stage `raw/` + `wiki/`, commit if anything actually changed).
`push()`/`pull()` are deliberately not built yet — INGEST_PLAN.md §5 scopes
this build order item to local commit only; push stays a separate,
explicitly user-triggered action once it's built, never auto-called by
`ingest`.

`GitEngine` doesn't know anything about `QueueItem`/ingest semantics — it
just stages two directories and commits with whatever message it's given.
Deciding *when* to commit and *what* the message says is the caller's job
(`ingest run`'s batch-end commit, see `cli.py`), not this module's — same
separation-of-concerns reasoning as `stager`/`ingest` (INGEST_PLAN.md §1).
"""

from __future__ import annotations

from pathlib import Path

import pygit2
from loguru import logger

from llm_wiki.models import GitError

_GITIGNORE_LINE = ".llm-wiki/"
_TRACKED_DIRS = ("raw", "wiki")
_COMMIT_AUTHOR_NAME = "llm-wiki"
_COMMIT_AUTHOR_EMAIL = "llm-wiki@localhost"


class GitEngine:
    """Owns the vault's Git repo (`raw/` + `wiki/` are tracked;
    `.llm-wiki/` — the disposable SQLite cache — is gitignored, per
    ARCHITECTURE.md §3)."""

    def __init__(self, vault_root: Path) -> None:
        self.vault_root = Path(vault_root)

    def init(self) -> pygit2.Repository:
        """Open the vault's repo, creating it (+ `.gitignore`) first if it
        doesn't exist yet. Idempotent — safe to call on every `commit()`,
        which is exactly how it's used; nothing else needs to call this
        directly under normal use.
        """
        discovered = pygit2.discover_repository(str(self.vault_root))
        if discovered is None:
            try:
                repo = pygit2.init_repository(str(self.vault_root), bare=False)
            except pygit2.GitError as exc:
                raise GitError(f"could not initialize git repo at {self.vault_root}: {exc}") from exc
            logger.info(f"Initialized git repo at {self.vault_root}")
        else:
            repo = pygit2.Repository(discovered)

        self._ensure_gitignore()
        return repo

    def commit(self, message: str) -> str | None:
        """Stage `raw/` + `wiki/` (`git add -A` over both, per
        INGEST_PLAN.md §5's partial-failure-handling note — a plain
        whole-directory add, not per-item selective staging) and commit.

        Returns the new commit's hex oid, or `None` if there was nothing
        to commit — a no-op, not an error, since a `run` that completed
        zero items (or whose completed items produced no actual file
        changes) shouldn't create an empty commit.
        """
        repo = self.init()
        index = repo.index
        for tracked in _TRACKED_DIRS:
            if (self.vault_root / tracked).is_dir():
                index.add_all([f"{tracked}/*"])
        index.write()

        new_tree_oid = index.write_tree()
        if not self._has_changes(repo, new_tree_oid):
            return None

        signature = pygit2.Signature(_COMMIT_AUTHOR_NAME, _COMMIT_AUTHOR_EMAIL)
        parents = [] if repo.head_is_unborn else [repo.head.target]
        try:
            oid = repo.create_commit("HEAD", signature, signature, message, new_tree_oid, parents)
        except pygit2.GitError as exc:
            raise GitError(f"commit failed at {self.vault_root}: {exc}") from exc

        logger.info(f"Committed {oid} at {self.vault_root}: {message!r}")
        return str(oid)

    def _ensure_gitignore(self) -> None:
        gitignore = self.vault_root / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text(_GITIGNORE_LINE + "\n")
            return
        existing = gitignore.read_text().splitlines()
        if _GITIGNORE_LINE not in existing:
            with gitignore.open("a") as f:
                f.write(_GITIGNORE_LINE + "\n")

    @staticmethod
    def _has_changes(repo: pygit2.Repository, new_tree_oid: pygit2.Oid) -> bool:
        if repo.head_is_unborn:
            return len(repo.index) > 0
        return new_tree_oid != repo.head.peel(pygit2.Tree).id
