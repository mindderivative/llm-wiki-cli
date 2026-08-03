from pathlib import Path

import pytest

from llm_wiki.storage import (
    StorageEngine,
    delete_links_for_slug,
    list_all_links,
    replace_links_for_source,
)


@pytest.fixture
def storage(tmp_path: Path):
    with StorageEngine(tmp_path / "db.sqlite3") as engine:
        engine.init_schema()
        yield engine


def test_list_all_links_empty_initially(storage: StorageEngine):
    assert list_all_links(storage) == []


def test_replace_links_for_source_inserts_rows(storage: StorageEngine):
    with storage.conn:
        replace_links_for_source(storage, "a", ["b", "c"])

    links = {(link.source_slug, link.target_slug) for link in list_all_links(storage)}
    assert links == {("a", "b"), ("a", "c")}


def test_replace_links_for_source_dedupes_targets(storage: StorageEngine):
    with storage.conn:
        replace_links_for_source(storage, "a", ["b", "b", "b"])

    links = list_all_links(storage)
    assert len(links) == 1


def test_replace_links_for_source_replaces_previous_set(storage: StorageEngine):
    with storage.conn:
        replace_links_for_source(storage, "a", ["b", "c"])
    with storage.conn:
        replace_links_for_source(storage, "a", ["d"])

    links = {(link.source_slug, link.target_slug) for link in list_all_links(storage)}
    assert links == {("a", "d")}


def test_replace_links_for_source_does_not_touch_other_sources(storage: StorageEngine):
    with storage.conn:
        replace_links_for_source(storage, "a", ["x"])
        replace_links_for_source(storage, "b", ["y"])
    with storage.conn:
        replace_links_for_source(storage, "a", ["z"])

    links = {(link.source_slug, link.target_slug) for link in list_all_links(storage)}
    assert links == {("a", "z"), ("b", "y")}


def test_delete_links_for_slug_removes_as_source_and_target(storage: StorageEngine):
    with storage.conn:
        replace_links_for_source(storage, "a", ["b"])
        replace_links_for_source(storage, "c", ["a"])
        replace_links_for_source(storage, "d", ["e"])

    with storage.conn:
        delete_links_for_slug(storage, "a")

    links = {(link.source_slug, link.target_slug) for link in list_all_links(storage)}
    assert links == {("d", "e")}
