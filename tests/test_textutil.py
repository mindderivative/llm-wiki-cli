from llm_wiki.textutil import slugify


def test_slugify_lowercases_and_hyphenates():
    assert slugify("Meeting Notes 2026") == "meeting-notes-2026"


def test_slugify_collapses_punctuation():
    assert slugify("Acme Corp.! Q3 Report??") == "acme-corp-q3-report"


def test_slugify_strips_leading_trailing_hyphens():
    assert slugify("  --hello world--  ") == "hello-world"


def test_slugify_falls_back_to_untitled_for_no_slug_chars():
    assert slugify("!!!") == "untitled"


def test_slugify_empty_string_falls_back_to_untitled():
    assert slugify("") == "untitled"
