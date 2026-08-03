"""`rebuild_links()` — the `/wiki-link` pipeline (ARCHITECTURE.md §8).

See GRAPH_LINT_PLAN.md §2 for the full design. Walks `wiki/`, reconciles
the `notes` table against what's actually on disk (new/hand-authored
files get indexed, hand-edited files get re-parsed, deleted files get
removed), and re-extracts `[[wikilink]]` targets for anything that
changed, replacing that note's rows in `links`.

Incrementality and filesystem discovery turn out to be the same
mechanism, not two separate features — see GRAPH_LINT_PLAN.md §2 for why.

`iter_note_files()`/`parse_note_metadata()`/`UNREADABLE_ERRORS` are
reused by `lint.run()`'s schema-violation check (GRAPH_LINT_PLAN.md §3)
— deliberately not underscore-private, since a second real package
depends on them, but not re-exported from `graph/__init__.py` either:
`rebuild_links()` stays the one real public entry point, these are
sibling-package-shared internals.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import frontmatter
import yaml
from pydantic import ValidationError

from llm_wiki.models import NOTE_TYPE_FOLDERS, LinkRebuildResult, Note, NoteType, utcnow
from llm_wiki.storage import (
    StorageEngine,
    delete_links_for_slug,
    delete_note_row,
    get_link_targets_for_source,
    get_note_row_by_slug,
    insert_note_row,
    list_note_rows,
    replace_links_for_source,
    update_note_row,
)
from llm_wiki.textutil import content_hash, slugify

_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")

# What parsing a file's frontmatter into note metadata can raise --
# deliberately broad-but-specific, not a bare `except Exception`, so a
# real bug elsewhere isn't silently swallowed as "just another
# unreadable note."
UNREADABLE_ERRORS = (yaml.YAMLError, ValueError, TypeError, ValidationError)


def rebuild_links(vault_root: Path, storage: StorageEngine, *, full: bool = False) -> LinkRebuildResult:
    """Reconcile `notes`/`links` against `wiki/`'s actual current state.

    Link extraction always runs for every note, every call — cheap at
    this project's target scale, and required for correctness (a note
    whose `content_hash` was set by `compiler`, not `graph`, still needs
    its links extracted the first time `graph` sees it, even though the
    hash already "matches"). What `full=True` actually controls is
    narrower: whether the `notes` row itself (type/title/tags/sources)
    gets rewritten even when its `content_hash` is unchanged. Deletion
    detection and new-file discovery happen every run either way
    (GRAPH_LINT_PLAN.md §2).
    """
    result = LinkRebuildResult()

    for path in iter_note_files(vault_root):
        _reconcile_one_file(path, storage, full=full, result=result)

    _detect_deletions(storage, result)

    return result


def iter_note_files(vault_root: Path) -> Iterator[Path]:
    """Every `.md` file under any of `wiki/`'s note-type subdirectories,
    in a stable (sorted, folder-by-folder) order."""
    for folder in NOTE_TYPE_FOLDERS.values():
        folder_path = vault_root / "wiki" / folder
        if not folder_path.is_dir():
            continue
        yield from sorted(folder_path.glob("*.md"))


def parse_note_metadata(path: Path) -> tuple[NoteType, str, list[str], list[str], str]:
    """Parse `path`'s frontmatter into `(type, title, tags, sources,
    body)`. Raises `UNREADABLE_ERRORS` if the frontmatter is missing,
    malformed YAML, or `type` isn't a valid `NoteType` — the one check
    both `graph` (skip indexing) and `lint` (schema-violation finding)
    need, kept in one place so they can't drift (GRAPH_LINT_PLAN.md §3).
    """
    post = frontmatter.loads(path.read_text(encoding="utf-8"))
    note_type = NoteType(post.get("type"))
    title = post.get("title") or path.stem
    tags = post.get("tags") or []
    sources = post.get("sources") or []
    if not isinstance(tags, list) or not isinstance(sources, list):
        raise ValueError(f"{path}: 'tags' and 'sources' must be lists")
    return note_type, title, tags, sources, post.content


def _reconcile_one_file(path: Path, storage: StorageEngine, *, full: bool, result: LinkRebuildResult) -> None:
    result.notes_scanned += 1
    slug = path.stem
    current_hash = content_hash(path.read_text(encoding="utf-8"))
    existing = get_note_row_by_slug(storage, slug)

    try:
        note_type, title, tags, sources, body = parse_note_metadata(path)
    except UNREADABLE_ERRORS:
        result.notes_unreadable.append(slug)
        return

    # `content_hash` matching only tells us the *note row* (type/title/
    # tags/sources) is already up to date -- it does NOT mean this
    # file's links were ever extracted. `compiler.write_source_note()`/
    # `fan_out_mentions()` set a note's `content_hash` at creation time
    # without touching `links` at all (that's `graph`'s job), so a
    # freshly-`cascade()`d note reaches its first `graph rebuild` with a
    # hash that already "matches" -- skipping link extraction here would
    # silently leave it permanently unlinked. Caught via manual
    # end-to-end testing, not a unit test, before this shipped
    # (GRAPH_LINT_PLAN.md §2). So: the notes-row DB write is skippable
    # when unchanged, but link extraction always runs -- cheap (one
    # regex pass over already-read text + a small `links` replace) at
    # the vault scale this project targets.
    row_unchanged = not full and existing is not None and existing.content_hash == current_hash

    # One transaction per file -- a crash mid-rebuild loses at most the
    # note currently being processed, not every note already reconciled
    # in this pass. A retry's content_hash-based skip makes re-running
    # from the top safe either way (same recovery convention as
    # INGEST_PLAN.md §3's atomicity contract, applied to a filesystem
    # walk instead of a queue item).
    with storage.conn:
        if not row_unchanged:
            note = Note(
                id=existing.id if existing is not None else None,
                path=path,
                slug=slug,
                type=note_type,
                title=title,
                tags=tags,
                sources=sources,
                content_hash=current_hash,
                created_at=existing.created_at if existing is not None else utcnow(),
                updated_at=utcnow(),
            )
            if existing is None:
                insert_note_row(storage, note)
                result.notes_indexed += 1
            else:
                update_note_row(storage, note)
                result.notes_updated += 1

        before_targets = set(get_link_targets_for_source(storage, slug))
        after_targets = set(_extract_wikilink_targets(body))
        replace_links_for_source(storage, slug, list(after_targets))
        result.links_added += len(after_targets - before_targets)
        result.links_removed += len(before_targets - after_targets)


def _detect_deletions(storage: StorageEngine, result: LinkRebuildResult) -> None:
    for note in list_note_rows(storage):
        if not note.path.exists():
            with storage.conn:
                delete_note_row(storage, note.id)
                delete_links_for_slug(storage, note.slug)
                result.notes_deleted += 1


def _extract_wikilink_targets(body: str) -> list[str]:
    """Every `[[target]]` in `body`, normalized to a slug. Strips an
    optional `|alias` or `#anchor` suffix defensively -- nothing writes
    that syntax yet, but it's cheap to handle now rather than surprise a
    future writer. `slugify()`s the raw target so a hand-written
    `[[Acme Corp]]` resolves the same as the machine-written
    `[[acme-corp]]` (GRAPH_LINT_PLAN.md §2)."""
    targets = []
    for match in _WIKILINK_RE.finditer(body):
        raw = match.group(1).split("|", 1)[0].split("#", 1)[0].strip()
        if raw:
            targets.append(slugify(raw))
    return targets
