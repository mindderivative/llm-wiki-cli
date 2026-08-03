"""`verify_and_clean()` — confirm a `raw/.sources/` archive is intact,
then remove the now-redundant original from `raw/`'s top level.

Deliberately its own function, separate from `stage()` (copying) and
from any future watcher (observing) — one reason to change each. Not
called automatically by `stage()`; whatever triggers staging (CLI
`ingest add`, or the watcher handler, once either exists) is responsible
for calling `stage()` then `verify_and_clean()` in sequence.

Scoped narrowly on purpose: only ever touches a file still sitting at
the *literal top level* of `raw/` — never a file staged from anywhere
else (e.g. `ingest add ~/Downloads/x.pdf` has nothing under `raw/` to
clean up), and never anything already inside `raw/.sources/` or
`raw/.staged/`. Safe to call unconditionally right after `stage()` —
no-ops if there's nothing to verify or nothing left to clean up.

Once this succeeds, `raw/.sources/<archived file>` is the sole source of
truth for that item — the original that was sitting loose at the top of
`raw/` is gone, and nothing else needs to reference it. `archive_path`
on the `queue` row (already persisted by `stage()`) is that tracking:
future `lint`/`graph`/re-ingest passes read `archive_path`, not
whatever transient location the original happened to arrive at.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from loguru import logger

from llm_wiki.models import IngestionError, QueueItem, QueueStatus, utcnow
from llm_wiki.storage import StorageEngine, update_queue_row

_HASH_CHUNK_SIZE = 1024 * 1024


def verify_and_clean(
    item: QueueItem,
    original_path: Path,
    vault_root: Path,
    storage: StorageEngine,
) -> QueueItem:
    """Hash-compare `item.archive_path` against `original_path`; if they
    match and `original_path` is still sitting at the top level of
    `raw/`, delete `original_path`.

    A hash mismatch is treated as a genuine step-1 failure — despite
    `stage()`'s copy not having raised, the archive doesn't actually
    match, which is a correctness problem, not a cleanliness one. Flips
    `item` to `FAILED` (`failed_at_step=STAGED`) rather than deleting
    anything.

    A failure to *delete* the now-confirmed-redundant original (e.g.
    permissions) is logged and does not fail the item — the archive is
    already confirmed intact at that point; a leftover duplicate in
    `raw/` is a cleanliness problem, not a correctness one.

    No-ops (returns `item` unchanged) if: staging didn't succeed
    (`item.status != STAGED`), `original_path` isn't sitting directly
    under `raw/`'s top level, or `original_path` no longer exists
    (already cleaned up by an earlier call).
    """
    if item.status != QueueStatus.STAGED or item.archive_path is None:
        return item

    if original_path.parent != vault_root / "raw" or not original_path.is_file():
        return item

    try:
        if _sha256(original_path) != _sha256(item.archive_path):
            raise IngestionError(
                f"{item.archive_path} does not match {original_path} "
                "— refusing to delete the original"
            )
    except (OSError, IngestionError) as exc:
        logger.warning(f"Verification failed for queue item {item.id}: {exc}")
        failed = item.model_copy(
            update={
                "status": QueueStatus.FAILED,
                "error": str(exc),
                "failed_at_step": QueueStatus.STAGED,
                "updated_at": utcnow(),
            }
        )
        with storage.conn:
            return update_queue_row(storage, failed)

    try:
        original_path.unlink()
        logger.info(
            f"Removed {original_path} from raw/ — {item.archive_path} is now canonical."
        )
    except OSError as exc:
        logger.warning(f"Verified {original_path} but could not remove it: {exc}")

    verified = item.model_copy(update={"updated_at": utcnow()})
    with storage.conn:
        return update_queue_row(storage, verified)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(_HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()
