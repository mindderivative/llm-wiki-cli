from pathlib import Path

import pytest

from llm_wiki.models import Note, NoteType
from llm_wiki.storage import (
    StorageEngine,
    delete_note_row,
    get_note_row_by_slug,
    insert_note_row,
    list_note_rows,
    update_note_row,
)


@pytest.fixture
def storage(tmp_path: Path):
    with StorageEngine(tmp_path / "db.sqlite3") as engine:
        engine.init_schema()
        yield engine


def _note(slug: str = "acme-corp", title: str = "Acme Corp") -> Note:
    return Note(
        path=Path(f"wiki/sources/{slug}.md"),
        slug=slug,
        type=NoteType.SOURCE,
        title=title,
        tags=["x"],
        sources=["some source"],
        content_hash="deadbeef",
    )


def test_get_note_row_by_slug_returns_none_when_absent(storage: StorageEngine):
    assert get_note_row_by_slug(storage, "nope") is None


def test_insert_then_get_round_trips(storage: StorageEngine):
    note = _note()
    with storage.conn:
        inserted = insert_note_row(storage, note)

    fetched = get_note_row_by_slug(storage, "acme-corp")

    assert fetched.id == inserted.id
    assert fetched.title == "Acme Corp"
    assert fetched.tags == ["x"]
    assert fetched.sources == ["some source"]
    assert fetched.type == NoteType.SOURCE


def test_insert_duplicate_slug_raises(storage: StorageEngine):
    import sqlite3

    with storage.conn:
        insert_note_row(storage, _note(slug="dup"))

    with pytest.raises(sqlite3.IntegrityError):
        with storage.conn:
            insert_note_row(storage, _note(slug="dup", title="Different title"))


def test_update_note_row_persists_new_sources_and_hash(storage: StorageEngine):
    with storage.conn:
        inserted = insert_note_row(storage, _note())

    updated = inserted.model_copy(
        update={"sources": ["some source", "another source"], "content_hash": "newhash"}
    )
    with storage.conn:
        update_note_row(storage, updated)

    fetched = get_note_row_by_slug(storage, "acme-corp")
    assert fetched.sources == ["some source", "another source"]
    assert fetched.content_hash == "newhash"


def test_update_note_row_leaves_slug_untouched(storage: StorageEngine):
    with storage.conn:
        inserted = insert_note_row(storage, _note())

    updated = inserted.model_copy(update={"content_hash": "newhash"})
    with storage.conn:
        update_note_row(storage, updated)

    fetched = get_note_row_by_slug(storage, "acme-corp")
    assert fetched.slug == "acme-corp"
    assert fetched.type == NoteType.SOURCE


def test_update_note_row_can_change_type_and_title(storage: StorageEngine):
    # Broadened for graph.rebuild_links() (GRAPH_LINT_PLAN.md §2), which
    # reconciles a hand-edited note's frontmatter wholesale.
    with storage.conn:
        inserted = insert_note_row(storage, _note())

    updated = inserted.model_copy(update={"type": NoteType.CONCEPT, "title": "Renamed"})
    with storage.conn:
        update_note_row(storage, updated)

    fetched = get_note_row_by_slug(storage, "acme-corp")
    assert fetched.type == NoteType.CONCEPT
    assert fetched.title == "Renamed"


def test_list_note_rows_returns_every_note(storage: StorageEngine):
    with storage.conn:
        insert_note_row(storage, _note(slug="a"))
        insert_note_row(storage, _note(slug="b"))

    slugs = {note.slug for note in list_note_rows(storage)}
    assert slugs == {"a", "b"}


def test_list_note_rows_empty_when_no_notes(storage: StorageEngine):
    assert list_note_rows(storage) == []


def test_delete_note_row_removes_it(storage: StorageEngine):
    with storage.conn:
        inserted = insert_note_row(storage, _note())

    with storage.conn:
        delete_note_row(storage, inserted.id)

    assert get_note_row_by_slug(storage, "acme-corp") is None
