from pathlib import Path

import pytest

from llm_wiki.ingest import atomize
from llm_wiki.models import QueueItem, QueueStatus
from llm_wiki.storage import StorageEngine, insert_queue_row
from llm_wiki.vault import VaultManager


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    manager = VaultManager(recent_vaults_path=tmp_path / "_recent" / "recent_vaults.json")
    settings = manager.create(tmp_path / "vault")
    return settings.vault_root


@pytest.fixture
def storage(vault_root: Path):
    db_path = vault_root / ".llm-wiki" / "db.sqlite3"
    with StorageEngine(db_path) as engine:
        yield engine


def _queued_item(vault_root: Path, storage: StorageEngine, name: str, content: str = "hello") -> QueueItem:
    """A QUEUED QueueItem with a real file backing it — atomize() only
    cares about raw_path, so this skips the STAGED/accept() history."""
    staged_path = vault_root / "raw" / ".staged" / name
    staged_path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)

    item = QueueItem(
        title=Path(name).stem,
        raw_path=staged_path,
        archive_path=vault_root / "raw" / ".sources" / name,
        status=QueueStatus.QUEUED,
    )
    with storage.conn:
        return insert_queue_row(storage, item)


def _chunks_for(storage: StorageEngine, item_id: int) -> list[dict]:
    rows = storage.conn.execute(
        "SELECT * FROM chunks WHERE queue_item_id = ? ORDER BY ordinal;", (item_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def test_atomize_plaintext_single_chunk(vault_root: Path, storage: StorageEngine):
    item = _queued_item(vault_root, storage, "notes.txt", "  some plain text content  \n")

    result = atomize(item, storage)

    assert result.status == QueueStatus.PARSED
    chunks = _chunks_for(storage, result.id)
    assert len(chunks) == 1
    assert chunks[0]["title"] == "notes"
    assert chunks[0]["content"] == "some plain text content"
    assert chunks[0]["ordinal"] == 0
    assert chunks[0]["word_count"] == 4
    assert chunks[0]["note_id"] is None


MARKDOWN_WITH_HEADINGS = """\
Some intro text before any heading.

# First Section

Content of the first section.

## Second Section

Content of the second section,
spanning multiple lines.
"""


def test_atomize_markdown_splits_on_headings(vault_root: Path, storage: StorageEngine):
    item = _queued_item(vault_root, storage, "doc.md", MARKDOWN_WITH_HEADINGS)

    result = atomize(item, storage)

    assert result.status == QueueStatus.PARSED
    chunks = _chunks_for(storage, result.id)
    assert len(chunks) == 3

    assert chunks[0]["title"] == "doc"  # fallback title for the preamble
    assert "intro text" in chunks[0]["content"]

    assert chunks[1]["title"] == "First Section"
    assert "Content of the first section." in chunks[1]["content"]
    assert "Second Section" not in chunks[1]["content"]

    assert chunks[2]["title"] == "Second Section"
    assert "spanning multiple lines" in chunks[2]["content"]

    assert [c["ordinal"] for c in chunks] == [0, 1, 2]


MARKDOWN_WITH_FENCED_HEADING_LOOKALIKE = """\
# Real Heading

Some text.

```
# this is inside a code fence, not a real heading
still inside the fence
```

More text after the fence.
"""


def test_atomize_markdown_ignores_headings_inside_code_fence(vault_root: Path, storage: StorageEngine):
    item = _queued_item(vault_root, storage, "doc.md", MARKDOWN_WITH_FENCED_HEADING_LOOKALIKE)

    result = atomize(item, storage)

    assert result.status == QueueStatus.PARSED
    chunks = _chunks_for(storage, result.id)
    assert len(chunks) == 1  # the fenced "#" line must not split the chunk
    assert chunks[0]["title"] == "Real Heading"
    assert "this is inside a code fence" in chunks[0]["content"]
    assert "More text after the fence." in chunks[0]["content"]


def test_atomize_markdown_no_headings_is_single_chunk(vault_root: Path, storage: StorageEngine):
    item = _queued_item(vault_root, storage, "plain.md", "Just a paragraph, no headings at all.")

    result = atomize(item, storage)

    assert result.status == QueueStatus.PARSED
    chunks = _chunks_for(storage, result.id)
    assert len(chunks) == 1
    assert chunks[0]["title"] == "plain"


def test_atomize_unsupported_format_fails(vault_root: Path, storage: StorageEngine):
    item = _queued_item(vault_root, storage, "scan.pdf", "%PDF-1.4 fake binary content")

    result = atomize(item, storage)

    assert result.status == QueueStatus.FAILED
    assert result.failed_at_step == QueueStatus.PARSING
    assert "unsupported" in result.error.lower()
    assert _chunks_for(storage, result.id) == []


def test_atomize_empty_file_fails(vault_root: Path, storage: StorageEngine):
    item = _queued_item(vault_root, storage, "empty.txt", "   \n\n  ")

    result = atomize(item, storage)

    assert result.status == QueueStatus.FAILED
    assert result.failed_at_step == QueueStatus.PARSING
    assert "no content" in result.error.lower()


def test_atomize_invalid_utf8_fails(vault_root: Path, storage: StorageEngine):
    item = _queued_item(vault_root, storage, "bad-encoding.txt", b"\xff\xfe not valid utf-8")

    result = atomize(item, storage)

    assert result.status == QueueStatus.FAILED
    assert result.failed_at_step == QueueStatus.PARSING


def test_atomize_retries_from_parking_status(vault_root: Path, storage: StorageEngine):
    # Simulates a crash that left the row parked at PARSING — nothing
    # committed for it yet, so atomize() must be able to redo it from
    # scratch (INGEST_PLAN.md §3's recovery convention), not just QUEUED.
    item = _queued_item(vault_root, storage, "notes.txt", "content")
    parked = item.model_copy(update={"status": QueueStatus.PARSING})

    result = atomize(parked, storage)

    assert result.status == QueueStatus.PARSED
    assert len(_chunks_for(storage, result.id)) == 1


def test_atomize_noop_for_non_queued_item(vault_root: Path, storage: StorageEngine):
    item = _queued_item(vault_root, storage, "notes.txt", "content")
    parsed = atomize(item, storage)
    assert parsed.status == QueueStatus.PARSED

    result = atomize(parsed, storage)

    assert result == parsed  # unchanged, no double-processing
    assert len(_chunks_for(storage, parsed.id)) == 1  # not duplicated
