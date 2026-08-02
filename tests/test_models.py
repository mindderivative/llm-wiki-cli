from pathlib import Path

from llm_wiki.models import Note, NoteType, QueueItem, QueueStatus


def test_queue_item_defaults():
    item = QueueItem(title="Test Source", raw_path=Path("raw/2026-08-01_test.pdf"))
    assert item.status == QueueStatus.QUEUED
    assert item.id is None
    assert item.error is None


def test_note_round_trip_json():
    note = Note(
        path=Path("wiki/entities/ada-lovelace.md"),
        slug="ada-lovelace",
        type=NoteType.ENTITY,
        title="Ada Lovelace",
        content_hash="deadbeef",
    )
    payload = note.model_dump_json()
    restored = Note.model_validate_json(payload)
    assert restored == note
