from pathlib import Path

import frontmatter
import pytest

from llm_wiki.compiler import fan_out_mentions, write_source_note
from llm_wiki.models import Analysis, Mention, Note, NoteType, QueueItem
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
    return Analysis(
        queue_item_id=1,
        summary=summary,
        entities=[Mention(name="Acme Corp", note="Posted strong Q3 results.")],
        concepts=[Mention(name="earnings", note="Central topic of the report.")],
    )


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


# -- fan_out_mentions() ----------------------------------------------------


def _write_source(vault_root: Path, storage: StorageEngine, title: str, analysis: Analysis) -> tuple[QueueItem, Note]:
    item = _item(vault_root, title=title)
    with storage.conn:
        note = write_source_note(item, analysis, vault_root, storage, FakeLlmClient())
    return item, note


def test_fan_out_mentions_creates_entity_and_concept_notes(vault_root: Path, storage: StorageEngine):
    analysis = _analysis(_item(vault_root))
    item, source_note = _write_source(vault_root, storage, "Q3 Report", analysis)

    with storage.conn:
        notes = fan_out_mentions(item, analysis, source_note, vault_root, storage, FakeLlmClient())

    assert {n.type for n in notes} == {NoteType.ENTITY, NoteType.CONCEPT}
    entity_note = next(n for n in notes if n.type == NoteType.ENTITY)
    concept_note = next(n for n in notes if n.type == NoteType.CONCEPT)
    assert entity_note.path == vault_root / "wiki" / "entities" / "acme-corp.md"
    assert concept_note.path == vault_root / "wiki" / "concepts" / "earnings.md"
    assert entity_note.path.is_file()
    assert concept_note.path.is_file()


def test_fan_out_mentions_first_mention_has_note_content_and_bullet(vault_root: Path, storage: StorageEngine):
    analysis = _analysis(_item(vault_root))
    item, source_note = _write_source(vault_root, storage, "Q3 Report", analysis)

    with storage.conn:
        [entity_note] = [
            n
            for n in fan_out_mentions(item, analysis, source_note, vault_root, storage, FakeLlmClient())
            if n.type == NoteType.ENTITY
        ]

    parsed = frontmatter.loads(entity_note.path.read_text())
    assert parsed.metadata["type"] == "entity"
    assert parsed.metadata["title"] == "Acme Corp"
    assert "Posted strong Q3 results." in parsed.content
    assert f"[[{source_note.slug}]]" in parsed.content


def test_fan_out_mentions_inserts_chunk_and_embedding_once(vault_root: Path, storage: StorageEngine):
    analysis = _analysis(_item(vault_root))
    item, source_note = _write_source(vault_root, storage, "Q3 Report", analysis)

    with storage.conn:
        [entity_note] = [
            n
            for n in fan_out_mentions(item, analysis, source_note, vault_root, storage, FakeLlmClient())
            if n.type == NoteType.ENTITY
        ]

    chunk_row = storage.conn.execute("SELECT * FROM chunks WHERE note_id = ?;", (entity_note.id,)).fetchone()
    assert chunk_row is not None
    assert chunk_row["content"] == "Posted strong Q3 results."
    vec_row = storage.conn.execute(
        "SELECT rowid FROM vec_chunks WHERE rowid = ?;", (chunk_row["id"],)
    ).fetchone()
    assert vec_row is not None


def test_fan_out_mentions_repeat_mention_appends_without_reembedding(vault_root: Path, storage: StorageEngine):
    analysis_a = _analysis(_item(vault_root))
    item_a, source_a = _write_source(vault_root, storage, "Q3 Report", analysis_a)
    with storage.conn:
        fan_out_mentions(item_a, analysis_a, source_a, vault_root, storage, FakeLlmClient())

    analysis_b = Analysis(
        queue_item_id=2,
        summary="second summary",
        entities=[Mention(name="Acme Corp", note="Signed a new lease.")],
        concepts=[],
    )
    item_b, source_b = _write_source(vault_root, storage, "Q4 Update", analysis_b)
    with storage.conn:
        [entity_note] = fan_out_mentions(item_b, analysis_b, source_b, vault_root, storage, FakeLlmClient())

    assert entity_note.sources == ["Q3 Report", "Q4 Update"]
    parsed = frontmatter.loads(entity_note.path.read_text())
    assert "Posted strong Q3 results." in parsed.content
    assert "Signed a new lease." in parsed.content
    assert f"[[{source_b.slug}]]" in parsed.content

    # still exactly one chunk/embedding -- no re-embed on repeat mention (§12 decision 2)
    chunk_rows = storage.conn.execute("SELECT * FROM chunks WHERE note_id = ?;", (entity_note.id,)).fetchall()
    assert len(chunk_rows) == 1


def test_fan_out_mentions_is_idempotent_for_a_retried_item(vault_root: Path, storage: StorageEngine):
    analysis = _analysis(_item(vault_root))
    item, source_note = _write_source(vault_root, storage, "Q3 Report", analysis)

    with storage.conn:
        fan_out_mentions(item, analysis, source_note, vault_root, storage, FakeLlmClient())
    with storage.conn:
        [entity_note] = [
            n
            for n in fan_out_mentions(item, analysis, source_note, vault_root, storage, FakeLlmClient())
            if n.type == NoteType.ENTITY
        ]

    # same item run through fan_out_mentions() twice (crash-retry) -- no duplicate bullet/source
    assert entity_note.sources == ["Q3 Report"]
    parsed = frontmatter.loads(entity_note.path.read_text())
    assert parsed.content.count("Q3 Report") == 1


def test_fan_out_mentions_dedupes_same_name_within_one_item(vault_root: Path, storage: StorageEngine):
    analysis = Analysis(
        queue_item_id=1,
        summary="summary",
        entities=[
            Mention(name="Acme Corp", note="First mention."),
            Mention(name="acme corp", note="Second mention, same entity."),
        ],
        concepts=[],
    )
    item, source_note = _write_source(vault_root, storage, "Q3 Report", analysis)

    with storage.conn:
        notes = fan_out_mentions(item, analysis, source_note, vault_root, storage, FakeLlmClient())

    assert len(notes) == 1


def test_fan_out_mentions_cross_type_slug_collision_gets_suffixed(vault_root: Path, storage: StorageEngine):
    concept_analysis = Analysis(
        queue_item_id=1, summary="s", entities=[], concepts=[Mention(name="Acme", note="A concept mention.")]
    )
    item_a, source_a = _write_source(vault_root, storage, "First Source", concept_analysis)
    with storage.conn:
        fan_out_mentions(item_a, concept_analysis, source_a, vault_root, storage, FakeLlmClient())

    entity_analysis = Analysis(
        queue_item_id=2, summary="s", entities=[Mention(name="Acme", note="An entity mention.")], concepts=[]
    )
    item_b, source_b = _write_source(vault_root, storage, "Second Source", entity_analysis)
    with storage.conn:
        [entity_note] = fan_out_mentions(item_b, entity_analysis, source_b, vault_root, storage, FakeLlmClient())

    assert entity_note.type == NoteType.ENTITY
    assert entity_note.slug != "acme"  # "acme" is already taken by the concept note
    assert entity_note.path.is_file()
