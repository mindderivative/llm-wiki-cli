"""Tiny text utilities shared across packages that otherwise have no
common dependency on each other.

`slugify()` started as `stager.stage()`'s private helper for staged
filenames; `compiler.write_source_note()` (INGEST_PLAN.md §11) needed
the identical logic for note slugs — moved here once a second real
consumer existed, same "centralize once it's actually shared" call as
moving `queue`-row (de)serialization into `storage` (§9 item 2).

`content_hash()` moved here the same way — `compiler`'s note-writers
computed a note's hash at write time; `graph.rebuild_links()`
(GRAPH_LINT_PLAN.md §2) needs to compute the *same* hash for a file's
current on-disk content to compare against `notes.content_hash` and
detect drift, so both call sites must agree on the exact algorithm.
"""

from __future__ import annotations

import hashlib
import re

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """Lowercase, hyphen-separated slug for `text`. Never empty —
    falls back to `"untitled"` if `text` has no slug-able characters."""
    slug = _SLUG_RE.sub("-", text.lower()).strip("-")
    return slug or "untitled"


def content_hash(text: str) -> str:
    """sha256 hex digest of `text` (UTF-8 encoded) — the canonical
    "has this note's content changed" comparison used by both
    `compiler` (hash at write time) and `graph` (hash of the current
    file, to detect drift)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
