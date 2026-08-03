from pathlib import Path

import pytest

from llm_wiki.ingest.cascade import cascade
from llm_wiki.models import Analysis, CompilationError, QueueItem, QueueStatus
from llm_wiki.storage import StorageEngine, get_queue_row, insert_queue_row, upsert_analysis_row


class FakeLlmClient:
    def __init__(self, *, fail: Exception | None = None):
        self.fail = fail

    def summarize(self, text: str) -> str:
        raise NotImplementedError

    def extract(self, text: str):
        raise NotImplementedError

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self.fail:
            raise self.fail
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "raw" / ".sources").mkdir(parents=True)
    return root


@pytest.fixture
def storage(tmp_path: Path):
    with StorageEngine(tmp_path / "db.sqlite3", embedding_dim=4) as engine:
        engine.init_schema()
        yield engine


def _item_with_analysis(
    vault_root: Path,
    storage: StorageEngine,
    *,
    status: QueueStatus = QueueStatus.ANALYZED,
    title: str = "note",
    with_analysis: bool = True,
) -> QueueItem:
    archive_path = vault_root / "raw" / ".sources" / f"{title}.txt"
    archive_path.write_text("hello")
    item = QueueItem(title=title, raw_path=archive_path, archive_path=archive_path, status=status)
    with storage.conn:
        item = insert_queue_row(storage, item)
        if with_analysis:
            upsert_analysis_row(
                storage, Analysis(queue_item_id=item.id, summary="a summary", entities=[], concepts=[])
            )
    return item


def test_cascade_advances_analyzed_to_completed(vault_root: Path, storage: StorageEngine):
    item = _item_with_analysis(vault_root, storage)

    result = cascade(item, storage, FakeLlmClient(), vault_root)

    assert result.status == QueueStatus.COMPLETED


def test_cascade_retries_from_cascading(vault_root: Path, storage: StorageEngine):
    item = _item_with_analysis(vault_root, storage, status=QueueStatus.CASCADING)

    result = cascade(item, storage, FakeLlmClient(), vault_root)

    assert result.status == QueueStatus.COMPLETED


def test_cascade_noop_for_other_statuses(vault_root: Path, storage: StorageEngine):
    item = _item_with_analysis(vault_root, storage, status=QueueStatus.PARSED, with_analysis=False)

    result = cascade(item, storage, FakeLlmClient(), vault_root)

    assert result == item


def test_cascade_writes_source_note_file(vault_root: Path, storage: StorageEngine):
    item = _item_with_analysis(vault_root, storage, title="quarterly-report")

    cascade(item, storage, FakeLlmClient(), vault_root)

    notes = list((vault_root / "wiki" / "sources").glob("*.md"))
    assert len(notes) == 1
    assert "a summary" in notes[0].read_text()


def test_cascade_fails_cleanly_with_no_analysis_row(vault_root: Path, storage: StorageEngine):
    item = _item_with_analysis(vault_root, storage, with_analysis=False)

    result = cascade(item, storage, FakeLlmClient(), vault_root)

    assert result.status == QueueStatus.FAILED
    assert result.failed_at_step == QueueStatus.CASCADING


def test_cascade_fails_cleanly_on_embed_error(vault_root: Path, storage: StorageEngine):
    item = _item_with_analysis(vault_root, storage)
    llm = FakeLlmClient(fail=CompilationError("embed service down"))

    result = cascade(item, storage, llm, vault_root)

    assert result.status == QueueStatus.FAILED
    assert result.failed_at_step == QueueStatus.CASCADING
    assert "embed service down" in result.error
    # persisted, not just returned in memory
    persisted = get_queue_row(storage, item.id)
    assert persisted.status == QueueStatus.FAILED


def test_cascade_does_not_leave_orphan_note_row_on_embed_failure(vault_root: Path, storage: StorageEngine):
    # write_source_note() inserts notes/chunks rows before calling
    # embed() -- confirms those roll back together with the failure,
    # since the whole thing runs inside one `with storage.conn:` block.
    item = _item_with_analysis(vault_root, storage)
    llm = FakeLlmClient(fail=CompilationError("embed service down"))

    cascade(item, storage, llm, vault_root)

    count = storage.conn.execute("SELECT COUNT(*) AS n FROM notes;").fetchone()["n"]
    assert count == 0
