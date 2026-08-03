from pathlib import Path

import pytest

from llm_wiki.models import Note, NoteType
from llm_wiki.storage import StorageEngine, get_note_row_by_slug, insert_note_row


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
