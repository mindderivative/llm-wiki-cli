from pathlib import Path

import pytest

from llm_wiki.graph import rebuild_links
from llm_wiki.models import NoteType
from llm_wiki.storage import StorageEngine, get_note_row_by_slug, list_all_links


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    return tmp_path / "vault"


@pytest.fixture
def storage(tmp_path: Path):
    with StorageEngine(tmp_path / "db.sqlite3", embedding_dim=4) as engine:
        engine.init_schema()
        yield engine


def _write_note(vault_root: Path, folder: str, slug: str, *, frontmatter_text: str, body: str) -> Path:
    path = vault_root / "wiki" / folder / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{frontmatter_text}\n---\n\n{body}")
    return path


def _entity(vault_root: Path, slug: str, body: str = "body text") -> Path:
    return _write_note(
        vault_root,
        "entities",
        slug,
        frontmatter_text=f"type: entity\ntitle: {slug}\ntags: []\nsources: []",
        body=body,
    )


def test_rebuild_links_empty_vault(vault_root: Path, storage: StorageEngine):
    result = rebuild_links(vault_root, storage)

    assert result.notes_scanned == 0
    assert result.notes_indexed == 0


def test_rebuild_links_indexes_new_note_and_extracts_links(vault_root: Path, storage: StorageEngine):
    _entity(vault_root, "acme-corp", body="Mentioned in [[quarterly-report]].")

    result = rebuild_links(vault_root, storage)

    assert result.notes_scanned == 1
    assert result.notes_indexed == 1
    assert result.links_added == 1
    note = get_note_row_by_slug(storage, "acme-corp")
    assert note is not None
    assert note.type == NoteType.ENTITY
    links = list_all_links(storage)
    assert (links[0].source_slug, links[0].target_slug) == ("acme-corp", "quarterly-report")


def test_rebuild_links_second_run_is_a_noop_when_nothing_changed(vault_root: Path, storage: StorageEngine):
    _entity(vault_root, "acme-corp", body="[[quarterly-report]]")
    rebuild_links(vault_root, storage)

    result = rebuild_links(vault_root, storage)

    assert result.notes_scanned == 1
    assert result.notes_indexed == 0
    assert result.notes_updated == 0
    assert result.links_added == 0
    assert result.links_removed == 0


def test_rebuild_links_detects_hand_edited_note(vault_root: Path, storage: StorageEngine):
    path = _entity(vault_root, "acme-corp", body="[[quarterly-report]]")
    rebuild_links(vault_root, storage)

    path.write_text(path.read_text().replace("quarterly-report", "annual-report"))
    result = rebuild_links(vault_root, storage)

    assert result.notes_updated == 1
    assert result.links_added == 1
    assert result.links_removed == 1
    links = {(link.source_slug, link.target_slug) for link in list_all_links(storage)}
    assert links == {("acme-corp", "annual-report")}


def test_rebuild_links_discovers_hand_authored_note(vault_root: Path, storage: StorageEngine):
    # No compiler/cascade() involved -- a file dropped straight into
    # wiki/ by the user, per GRAPH_LINT_PLAN.md §2.
    _write_note(
        vault_root,
        "concepts",
        "hand-written",
        frontmatter_text="type: concept\ntitle: Hand Written\ntags: []\nsources: []",
        body="No pipeline ever touched this file.",
    )

    result = rebuild_links(vault_root, storage)

    assert result.notes_indexed == 1
    note = get_note_row_by_slug(storage, "hand-written")
    assert note is not None
    assert note.type == NoteType.CONCEPT
    assert note.title == "Hand Written"


def test_rebuild_links_flags_unreadable_frontmatter(vault_root: Path, storage: StorageEngine):
    path = vault_root / "wiki" / "entities" / "broken.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("no frontmatter at all, just text")

    result = rebuild_links(vault_root, storage)

    assert result.notes_unreadable == ["broken"]
    assert get_note_row_by_slug(storage, "broken") is None


def test_rebuild_links_flags_invalid_type_as_unreadable(vault_root: Path, storage: StorageEngine):
    _write_note(
        vault_root,
        "entities",
        "bad-type",
        frontmatter_text="type: not-a-real-type\ntitle: X",
        body="body",
    )

    result = rebuild_links(vault_root, storage)

    assert result.notes_unreadable == ["bad-type"]


def test_rebuild_links_detects_deleted_note(vault_root: Path, storage: StorageEngine):
    path = _entity(vault_root, "acme-corp", body="[[quarterly-report]]")
    rebuild_links(vault_root, storage)

    path.unlink()
    result = rebuild_links(vault_root, storage)

    assert result.notes_deleted == 1
    assert get_note_row_by_slug(storage, "acme-corp") is None
    assert list_all_links(storage) == []


def test_rebuild_links_full_forces_reprocessing_even_if_unchanged(vault_root: Path, storage: StorageEngine):
    _entity(vault_root, "acme-corp", body="[[quarterly-report]]")
    rebuild_links(vault_root, storage)

    result = rebuild_links(vault_root, storage, full=True)

    assert result.notes_updated == 1  # reprocessed even though content_hash is unchanged


def test_rebuild_links_dedupes_repeated_wikilink_targets(vault_root: Path, storage: StorageEngine):
    _entity(vault_root, "acme-corp", body="[[quarterly-report]] and again [[quarterly-report]]")

    rebuild_links(vault_root, storage)

    links = list_all_links(storage)
    assert len(links) == 1


def test_rebuild_links_normalizes_wikilink_target_casing(vault_root: Path, storage: StorageEngine):
    _entity(vault_root, "acme-corp", body="See [[Quarterly Report]].")

    rebuild_links(vault_root, storage)

    links = list_all_links(storage)
    assert links[0].target_slug == "quarterly-report"


def test_rebuild_links_extracts_links_for_a_note_whose_hash_already_matches(
    vault_root: Path, storage: StorageEngine
):
    # Regression test for a real bug caught via manual end-to-end
    # testing (GRAPH_LINT_PLAN.md §2): `compiler.write_source_note()`/
    # `fan_out_mentions()` insert a `notes` row with `content_hash`
    # already set to match the file they just wrote -- they never touch
    # `links`. The first `graph rebuild` that sees such a note must
    # still extract its links, even though `content_hash` "matches".
    from llm_wiki.models import Note, NoteType
    from llm_wiki.storage import insert_note_row
    from llm_wiki.textutil import content_hash as _hash

    path = _entity(vault_root, "acme-corp", body="[[quarterly-report]]")
    text = path.read_text()
    with storage.conn:
        insert_note_row(
            storage,
            Note(
                path=path,
                slug="acme-corp",
                type=NoteType.ENTITY,
                title="acme-corp",
                tags=[],
                sources=[],
                content_hash=_hash(text),
            ),
        )

    result = rebuild_links(vault_root, storage)

    assert result.notes_updated == 0  # the notes-row write was correctly skipped
    assert result.links_added == 1  # but the link was still extracted
    links = list_all_links(storage)
    assert (links[0].source_slug, links[0].target_slug) == ("acme-corp", "quarterly-report")


def test_rebuild_links_persists_across_a_fresh_connection(vault_root: Path, tmp_path: Path):
    # Regression test: rebuild_links()'s writes must be wrapped in
    # `with storage.conn:` to actually commit -- reading back on the
    # *same* open connection isn't proof of durability, since sqlite3
    # lets a connection see its own uncommitted writes.
    _entity(vault_root, "acme-corp", body="[[quarterly-report]]")
    db_path = tmp_path / "db.sqlite3"

    with StorageEngine(db_path, embedding_dim=4) as engine:
        engine.init_schema()
        rebuild_links(vault_root, engine)

    with StorageEngine(db_path, embedding_dim=4) as reopened:
        note = get_note_row_by_slug(reopened, "acme-corp")
        links = list_all_links(reopened)

    assert note is not None
    assert len(links) == 1
