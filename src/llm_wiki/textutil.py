"""Tiny text utilities shared across packages that otherwise have no
common dependency on each other.

`slugify()` started as `stager.stage()`'s private helper for staged
filenames; `compiler.write_source_note()` (INGEST_PLAN.md §11) needed
the identical logic for note slugs — moved here once a second real
consumer existed, same "centralize once it's actually shared" call as
moving `queue`-row (de)serialization into `storage` (§9 item 2).
"""

from __future__ import annotations

import re

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """Lowercase, hyphen-separated slug for `text`. Never empty —
    falls back to `"untitled"` if `text` has no slug-able characters."""
    slug = _SLUG_RE.sub("-", text.lower()).strip("-")
    return slug or "untitled"
