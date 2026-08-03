from pathlib import Path

import frontmatter
import pytest

from llm_wiki.compiler import write_source_note
from llm_wiki.models import Analysis, QueueItem
from llm_wiki.storage import StorageEngine, get_note_row_by_slug


class FakeLlmClient:
    """Minimal `LlmClient` double -- `write_source_note()` only calls
    `embed()`, so that's all this needs."""

    def summarize(self, text: str) -> str:
        raise NotImplementedError

    def extract(self, text: str):
        raise NotImplementedError

    def embed(self, texts: list[str]) -> list[list[float]]:
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


def _item(vault_root: Path, title: str = "Acme Corp Report") -> QueueItem:
    archive_path = vault_root / "raw" / ".sources" / "2026-08-03_report.txt"
    archive_path.write_text("original content")
    return QueueItem(title=title, raw_path=archive_path, archive_path=archive_path)


def _analysis(item: QueueItem, summary: str = "Acme Corp posted strong Q3 results.") -> Analysis:
    return Analysis(queue_item_id=1, summary=summary, entities=["Acme Corp"], concepts=["earnings"])


def test_write_source_note_creates_file_with_frontmatter(vault_root: Path, storage: StorageEngine):
    item = _item(vault_root)
    analysis = _analysis(item)

    with storage.conn:
        note = write_source_note(item, analysis, vault_root, storage, FakeLlmClient())

    assert note.path.is_file()
    parsed = frontmatter.loads(note.path.read_text())
    assert parsed.metadata["type"] == "source"
    assert parsed.metadata["title"] == "Acme Corp Report"
    assert parsed.metadata["tags"] == []
    assert "Acme Corp posted strong Q3 results." in parsed.content


def test_write_source_note_inserts_note_row(vault_root: Path, storage: StorageEngine):
    item = _item(vault_root)
    analysis = _analysis(item)

    with storage.conn:
        note = write_source_note(item, analysis, vault_root, storage, FakeLlmClient())

    fetched = get_note_row_by_slug(storage, note.slug)
    assert fetched is not None
    assert fetched.title == "Acme Corp Report"


def test_write_source_note_inserts_chunk_and_embedding(vault_root: Path, storage: StorageEngine):
    item = _item(vault_root)
    analysis = _analysis(item)

    with storage.conn:
        note = write_source_note(item, analysis, vault_root, storage, FakeLlmClient())

    chunk_row = storage.conn.execute(
        "SELECT * FROM chunks WHERE note_id = ?;", (note.id,)
    ).fetchone()
    assert chunk_row is not None
    assert chunk_row["content"] == analysis.summary

    vec_row = storage.conn.execute(
        "SELECT rowid FROM vec_chunks WHERE rowid = ?;", (chunk_row["id"],)
    ).fetchone()
    assert vec_row is not None


def test_write_source_note_handles_slug_collision(vault_root: Path, storage: StorageEngine):
    item_a = _item(vault_root, title="Quarterly Notes")
    item_b = _item(vault_root, title="Quarterly Notes")  # same title -> same base slug

    with storage.conn:
        note_a = write_source_note(item_a, _analysis(item_a), vault_root, storage, FakeLlmClient())
    with storage.conn:
        note_b = write_source_note(item_b, _analysis(item_b), vault_root, storage, FakeLlmClient())

    assert note_a.slug != note_b.slug
    assert note_a.path != note_b.path
    assert note_a.path.is_file()
    assert note_b.path.is_file()


def test_write_source_note_content_hash_matches_written_file(vault_root: Path, storage: StorageEngine):
    item = _item(vault_root)
    analysis = _analysis(item)

    with storage.conn:
        note = write_source_note(item, analysis, vault_root, storage, FakeLlmClient())

    import hashlib

    on_disk = note.path.read_text()
    assert note.content_hash == hashlib.sha256(on_disk.encode("utf-8")).hexdigest()


def test_write_source_note_leaves_no_tmp_file_behind(vault_root: Path, storage: StorageEngine):
    item = _item(vault_root)
    analysis = _analysis(item)

    with storage.conn:
        write_source_note(item, analysis, vault_root, storage, FakeLlmClient())

    tmp_files = list((vault_root / "wiki" / "sources").glob("*.tmp"))
    assert tmp_files == []
