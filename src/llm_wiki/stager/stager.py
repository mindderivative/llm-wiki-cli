"""`stage()` — archive + working-copy a raw file, record it as a `queue` row.

See INGEST_PLAN.md §1 (why this is split out of `ingest`), §2 (entry
points), §3 (state machine). This is step 1 of the pipeline only:
`STAGED` or `FAILED`, nothing else. Everything from `QUEUED` onward is
`ingest`'s job, not this module's.

`stage()` always **copies** from `source_path` — it never moves or
deletes it, even if `source_path` already lives under `raw/` (e.g. a
file the watcher just observed sitting at the top level of `raw/`).
Cleaning up that now-redundant original is a separate concern, on
purpose — see `stager.cleanup.verify_and_clean()`, not this module. Call
both, in sequence, wherever staging actually gets triggered from (CLI
`ingest add`, or the watcher handler, once either exists).
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from loguru import logger

from llm_wiki.models import IngestionError, QueueItem, QueueStatus, utcnow
from llm_wiki.stager._repo import insert_queue_row
from llm_wiki.storage import StorageEngine

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def stage(source_path: Path, vault_root: Path, storage: StorageEngine) -> QueueItem:
    """Archive `source_path` into `raw/.sources/`, write a working copy into
    `raw/.staged/`, and record the result as a `queue` row.

    Never raises for staging-domain failures (missing/unreadable source,
    copy I/O errors) — those come back as a `QueueItem` with
    `status=FAILED`, `failed_at_step=STAGED`, and `error` set, per
    INGEST_PLAN.md's failure contract (§3). Only a genuine storage-layer
    problem propagates (`StorageError`, raised by `storage` itself).

    The returned `QueueItem.raw_path` points at the `.staged/` working
    copy (what `ingest` reads from step 2 onward); `archive_path` points
    at the untouched `.sources/` original.
    """
    title = source_path.stem

    try:
        if not source_path.is_file():
            raise IngestionError(f"source file not found or not a regular file: {source_path}")

        sources_dir = vault_root / "raw" / ".sources"
        staged_dir = vault_root / "raw" / ".staged"
        sources_dir.mkdir(parents=True, exist_ok=True)
        staged_dir.mkdir(parents=True, exist_ok=True)

        date_str = utcnow().strftime("%Y-%m-%d")
        slug = _slugify(source_path.stem)

        archive_path = _unique_path(sources_dir / f"{date_str}_{source_path.name}")
        staged_path = _unique_path(staged_dir / f"{date_str}_{slug}{source_path.suffix}")

        shutil.copy2(source_path, archive_path)
        shutil.copy2(source_path, staged_path)

    except (OSError, IngestionError) as exc:
        logger.warning(f"Staging failed for {source_path}: {exc}")
        item = QueueItem(
            title=title,
            raw_path=source_path,
            status=QueueStatus.FAILED,
            error=str(exc),
            failed_at_step=QueueStatus.STAGED,
        )
        return insert_queue_row(storage, item)

    logger.info(f"Staged {source_path} -> {staged_path} (archived: {archive_path})")
    item = QueueItem(
        title=title,
        raw_path=staged_path,
        archive_path=archive_path,
        status=QueueStatus.STAGED,
    )
    return insert_queue_row(storage, item)


def _slugify(text: str) -> str:
    slug = _SLUG_RE.sub("-", text.lower()).strip("-")
    return slug or "untitled"


def _unique_path(path: Path) -> Path:
    """`path`, or `path` with a `-2`, `-3`, ... suffix if it already exists.

    Guards against same-named files staged the same day colliding on the
    `{date}_{...}` naming convention.
    """
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    n = 2
    while True:
        candidate = path.with_name(f"{stem}-{n}{suffix}")
        if not candidate.exists():
            return candidate
        n += 1
