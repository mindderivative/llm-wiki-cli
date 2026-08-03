from pathlib import Path

import pytest

from llm_wiki.ingest.compile import compile
from llm_wiki.llm.client import ExtractionResult
from llm_wiki.models import Chunk, CompilationError, QueueItem, QueueStatus
from llm_wiki.storage import (
    StorageEngine,
    get_analysis_row,
    get_queue_row,
    insert_chunk_row,
    insert_queue_row,
)


class FakeLlmClient:
    """Hand-written `LlmClient` double — never touches `outlines`/`openai`,
    so these tests prove `compile()`'s own logic (state transitions,
    `queue_analysis` writes, failure handling) independent of whichever
    real `LlmClient` implementation is plugged in (INGEST_PLAN.md §10)."""

    def __init__(self, *, summary="a summary", entities=None, concepts=None, fail=None):
        self.summary = summary
        self.entities = entities or []
        self.concepts = concepts or []
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    def summarize(self, text: str) -> str:
        self.calls.append(("summarize", text))
        if self.fail:
            raise self.fail
        return self.summary

    def extract(self, text: str) -> ExtractionResult:
        self.calls.append(("extract", text))
        if self.fail:
            raise self.fail
        return ExtractionResult(entities=self.entities, concepts=self.concepts)

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError("not exercised by compile()")


@pytest.fixture
def storage(tmp_path: Path):
    with StorageEngine(tmp_path / "db.sqlite3") as engine:
        engine.init_schema()
        yield engine


def _item_with_chunk(storage: StorageEngine, *, status: QueueStatus = QueueStatus.PARSED) -> QueueItem:
    item = QueueItem(title="doc", raw_path=Path("raw/.staged/doc.md"), status=status)
    with storage.conn:
        item = insert_queue_row(storage, item)
        insert_chunk_row(
            storage,
            Chunk(queue_item_id=item.id, ordinal=0, title="Intro", content="Acme Corp text.", word_count=3),
        )
    return item


def test_compile_advances_parsed_to_analyzed(storage: StorageEngine):
    item = _item_with_chunk(storage)
    llm = FakeLlmClient(summary="Acme summary", entities=["Acme Corp"], concepts=["supply chain"])

    result = compile(item, storage, llm)

    assert result.status == QueueStatus.ANALYZED
    analysis = get_analysis_row(storage, item.id)
    assert analysis.summary == "Acme summary"
    assert analysis.entities == ["Acme Corp"]
    assert analysis.concepts == ["supply chain"]


def test_compile_retries_from_analyzing(storage: StorageEngine):
    item = _item_with_chunk(storage, status=QueueStatus.ANALYZING)
    llm = FakeLlmClient(summary="redo summary")

    result = compile(item, storage, llm)

    assert result.status == QueueStatus.ANALYZED
    assert get_analysis_row(storage, item.id).summary == "redo summary"


def test_compile_noop_for_other_statuses(storage: StorageEngine):
    item = _item_with_chunk(storage, status=QueueStatus.QUEUED)
    llm = FakeLlmClient()

    result = compile(item, storage, llm)

    assert result == item
    assert llm.calls == []


def test_compile_fails_on_llm_client_error(storage: StorageEngine):
    item = _item_with_chunk(storage)
    llm = FakeLlmClient(fail=CompilationError("model is down"))

    result = compile(item, storage, llm)

    assert result.status == QueueStatus.FAILED
    assert result.failed_at_step == QueueStatus.ANALYZING
    assert "model is down" in result.error


def test_compile_persists_analyzing_marker_even_on_failure(storage: StorageEngine):
    # Confirms the ANALYZING pre-work marker actually committed before
    # the LLM call ran -- re-reads from the DB independently rather than
    # trusting the in-memory return value, per the atomicity contract's
    # two-phase pattern (§3).
    item = _item_with_chunk(storage)
    llm = FakeLlmClient(fail=CompilationError("boom"))

    compile(item, storage, llm)

    persisted = get_queue_row(storage, item.id)
    assert persisted.status == QueueStatus.FAILED
    assert persisted.failed_at_step == QueueStatus.ANALYZING


def test_compile_concatenates_chunks_with_titles(storage: StorageEngine):
    item = QueueItem(title="doc", raw_path=Path("raw/.staged/doc.md"), status=QueueStatus.PARSED)
    with storage.conn:
        item = insert_queue_row(storage, item)
        insert_chunk_row(
            storage, Chunk(queue_item_id=item.id, ordinal=0, title="First", content="alpha", word_count=1)
        )
        insert_chunk_row(
            storage, Chunk(queue_item_id=item.id, ordinal=1, title="Second", content="beta", word_count=1)
        )
    llm = FakeLlmClient()

    compile(item, storage, llm)

    summarize_text = llm.calls[0][1]
    assert "First" in summarize_text
    assert "alpha" in summarize_text
    assert "Second" in summarize_text
    assert "beta" in summarize_text
