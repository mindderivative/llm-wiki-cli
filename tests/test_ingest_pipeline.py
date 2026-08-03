from pathlib import Path

import pytest

from llm_wiki.ingest import advance, build_pipeline, step_once
from llm_wiki.llm.client import ExtractionResult
from llm_wiki.models import Chunk, Mention, QueueItem, QueueStatus
from llm_wiki.storage import StorageEngine, insert_chunk_row, insert_queue_row
from llm_wiki.vault import VaultManager


class FakeLlmClient:
    """Minimal `LlmClient` double -- see test_ingest_compile.py for the
    fuller version with failure injection. Only used here to prove the
    dispatcher wires `compile()` in correctly via `build_pipeline()`,
    not to re-test `compile()`'s own logic."""

    def summarize(self, text: str) -> str:
        return "a summary"

    def extract(self, text: str) -> ExtractionResult:
        return ExtractionResult(entities=[Mention(name="Acme Corp", note="n")], concepts=[])

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


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


def _item(vault_root: Path, storage: StorageEngine, name: str, status: QueueStatus, content: str = "hello") -> QueueItem:
    staged_path = vault_root / "raw" / ".staged" / name
    archive_path = vault_root / "raw" / ".sources" / name
    staged_path.write_text(content)
    archive_path.write_text(content)

    item = QueueItem(title=Path(name).stem, raw_path=staged_path, archive_path=archive_path, status=status)
    with storage.conn:
        return insert_queue_row(storage, item)


def test_step_once_staged_calls_accept(vault_root: Path, storage: StorageEngine):
    item = _item(vault_root, storage, "note.md", QueueStatus.STAGED)

    result = step_once(item, storage)

    assert result.status == QueueStatus.QUEUED


def test_step_once_queued_calls_atomize(vault_root: Path, storage: StorageEngine):
    item = _item(vault_root, storage, "note.txt", QueueStatus.QUEUED)

    result = step_once(item, storage)

    assert result.status == QueueStatus.PARSED


def test_step_once_noop_for_terminal_status(vault_root: Path, storage: StorageEngine):
    item = _item(vault_root, storage, "note.md", QueueStatus.COMPLETED)

    result = step_once(item, storage)

    assert result == item


def test_step_once_noop_for_unimplemented_status(vault_root: Path, storage: StorageEngine):
    # PARSED is a real, valid status, and compile() exists now -- but
    # the *default* dispatch table (used when no dispatch_table is
    # passed) never registers compile(), since it needs an llm_client
    # nothing here provides. See test_step_once_with_dispatch_table_
    # drives_compile below for the build_pipeline(llm_client) path.
    item = _item(vault_root, storage, "note.md", QueueStatus.PARSED)

    result = step_once(item, storage)

    assert result == item


def test_advance_runs_staged_all_the_way_to_parsed(vault_root: Path, storage: StorageEngine):
    item = _item(vault_root, storage, "note.txt", QueueStatus.STAGED)

    result = advance(item, storage)

    # STAGED -> QUEUED -> PARSED is as far as the pipeline currently goes.
    assert result.status == QueueStatus.PARSED


def test_advance_stops_on_failure_without_raising(vault_root: Path, storage: StorageEngine):
    item = _item(vault_root, storage, "scan.pdf", QueueStatus.STAGED)  # unsupported format

    result = advance(item, storage)

    assert result.status == QueueStatus.FAILED
    assert result.failed_at_step == QueueStatus.PARSING  # got through accept(), failed in atomize()


# -- build_pipeline() / dispatch_table (compile() wiring) --------------------


def test_build_pipeline_without_llm_client_omits_compile():
    table = build_pipeline()

    assert QueueStatus.PARSED not in table
    assert QueueStatus.ANALYZING not in table


def test_build_pipeline_with_llm_client_registers_compile_for_both_statuses():
    table = build_pipeline(FakeLlmClient())

    assert QueueStatus.PARSED in table
    assert QueueStatus.ANALYZING in table
    # Base statuses are still there too -- build_pipeline() extends the
    # base table, it doesn't replace it.
    assert QueueStatus.STAGED in table


def test_step_once_with_dispatch_table_drives_compile(vault_root: Path, storage: StorageEngine):
    item = _item(vault_root, storage, "note.md", QueueStatus.PARSED)
    with storage.conn:
        insert_chunk_row(
            storage, Chunk(queue_item_id=item.id, ordinal=0, title="Intro", content="text", word_count=1)
        )
    table = build_pipeline(FakeLlmClient())

    result = step_once(item, storage, dispatch_table=table)

    assert result.status == QueueStatus.ANALYZED


def test_advance_with_dispatch_table_reaches_analyzed(vault_root: Path, storage: StorageEngine):
    item = _item(vault_root, storage, "note.txt", QueueStatus.STAGED)
    table = build_pipeline(FakeLlmClient())

    result = advance(item, storage, dispatch_table=table)

    # STAGED -> QUEUED -> PARSED -> ANALYZED is as far as the pipeline
    # goes now -- cascade() (-> COMPLETED) isn't built yet (§10).
    assert result.status == QueueStatus.ANALYZED
