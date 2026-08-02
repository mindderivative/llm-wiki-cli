from pathlib import Path

from llm_wiki.models import Note, NoteType, QueueItem, QueueStatus


def test_queue_item_defaults():
    item = QueueItem(title="Test Source", raw_path=Path("raw/2026-08-01_test.pdf"))
    assert item.status == QueueStatus.STAGED
    assert item.id is None
    assert item.error is None
    assert item.failed_at_step is None


def test_queue_status_has_all_pipeline_states():
    # INGEST_PLAN.md §3 — 10-value state machine, stager's STAGED through
    # ingest's terminal COMPLETED/FAILED. Locks the full set so a typo'd
    # or accidentally-dropped value fails loudly here instead of only
    # showing up much later against real pipeline code.
    assert {s.value for s in QueueStatus} == {
        "STAGED",
        "QUEUED",
        "PARSING",
        "PARSED",
        "ANALYZING",
        "ANALYZED",
        "CASCADING",
        "CASCADED",
        "COMPLETED",
        "FAILED",
    }


def test_queue_item_failed_at_step_round_trip_json():
    item = QueueItem(
        title="Test Source",
        raw_path=Path("raw/.staged/2026-08-01_test.pdf"),
        status=QueueStatus.FAILED,
        error="chunker choked on malformed frontmatter",
        failed_at_step=QueueStatus.PARSING,
    )
    payload = item.model_dump_json()
    restored = QueueItem.model_validate_json(payload)
    assert restored == item
    assert restored.failed_at_step == QueueStatus.PARSING


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
