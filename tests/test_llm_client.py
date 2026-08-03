import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import openai
import pytest

from llm_wiki.config import LlamaServerConfig
from llm_wiki.llm.client import ExtractionResult, LlamaClient
from llm_wiki.models import CompilationError


@pytest.fixture
def config() -> LlamaServerConfig:
    return LlamaServerConfig(
        base_url="http://localhost:8080/v1", chat_model="test-chat", embedding_model="test-embed"
    )


@pytest.fixture
def fake_openai():
    """A `MagicMock(spec=openai.OpenAI)` — passes `outlines.from_openai()`'s
    real `isinstance` check while letting us stub exactly the surface it
    touches (`chat.completions.create()`), per INGEST_PLAN.md §10."""
    return MagicMock(spec=openai.OpenAI)


def _chat_response(content: str) -> SimpleNamespace:
    message = SimpleNamespace(content=content, refusal=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _embedding_response(vectors: list[list[float]]) -> SimpleNamespace:
    return SimpleNamespace(data=[SimpleNamespace(embedding=v) for v in vectors])


# -- summarize ---------------------------------------------------------------


def test_summarize_returns_content(config, fake_openai):
    fake_openai.chat.completions.create.return_value = _chat_response("  a short summary.  ")
    client = LlamaClient(config, openai_client=fake_openai)

    result = client.summarize("some source text")

    assert result == "a short summary."


def test_summarize_uses_configured_chat_model(config, fake_openai):
    fake_openai.chat.completions.create.return_value = _chat_response("summary")
    client = LlamaClient(config, openai_client=fake_openai)

    client.summarize("text")

    _, kwargs = fake_openai.chat.completions.create.call_args
    assert kwargs["model"] == "test-chat"


def test_summarize_wraps_openai_errors(config, fake_openai):
    fake_openai.chat.completions.create.side_effect = openai.APIConnectionError(request=MagicMock())
    client = LlamaClient(config, openai_client=fake_openai)

    with pytest.raises(CompilationError):
        client.summarize("text")


# -- extract -------------------------------------------------------------


def test_extract_returns_structured_result(config, fake_openai):
    payload = json.dumps({"entities": ["Acme Corp"], "concepts": ["supply chain"]})
    fake_openai.chat.completions.create.return_value = _chat_response(payload)
    client = LlamaClient(config, openai_client=fake_openai)

    result = client.extract("Acme Corp manages its supply chain carefully.")

    assert result == ExtractionResult(entities=["Acme Corp"], concepts=["supply chain"])


def test_extract_requests_json_schema_for_extraction_result(config, fake_openai):
    payload = json.dumps({"entities": [], "concepts": []})
    fake_openai.chat.completions.create.return_value = _chat_response(payload)
    client = LlamaClient(config, openai_client=fake_openai)

    client.extract("text")

    _, kwargs = fake_openai.chat.completions.create.call_args
    assert kwargs["response_format"]["type"] == "json_schema"


def test_extract_wraps_provider_errors(config, fake_openai):
    fake_openai.chat.completions.create.side_effect = openai.APIConnectionError(request=MagicMock())
    client = LlamaClient(config, openai_client=fake_openai)

    with pytest.raises(CompilationError):
        client.extract("text")


def test_extract_wraps_invalid_json(config, fake_openai):
    fake_openai.chat.completions.create.return_value = _chat_response("not valid json")
    client = LlamaClient(config, openai_client=fake_openai)

    with pytest.raises(CompilationError):
        client.extract("text")


# -- embed -----------------------------------------------------------------


def test_embed_returns_vectors_in_order(config, fake_openai):
    fake_openai.embeddings.create.return_value = _embedding_response([[0.1, 0.2], [0.3, 0.4]])
    client = LlamaClient(config, openai_client=fake_openai)

    result = client.embed(["first", "second"])

    assert result == [[0.1, 0.2], [0.3, 0.4]]


def test_embed_empty_list_skips_the_api_call(config, fake_openai):
    client = LlamaClient(config, openai_client=fake_openai)

    result = client.embed([])

    assert result == []
    fake_openai.embeddings.create.assert_not_called()


def test_embed_wraps_openai_errors(config, fake_openai):
    fake_openai.embeddings.create.side_effect = openai.APIConnectionError(request=MagicMock())
    client = LlamaClient(config, openai_client=fake_openai)

    with pytest.raises(CompilationError):
        client.embed(["text"])


# -- construction ------------------------------------------------------------


def test_default_construction_does_not_raise(config, monkeypatch):
    # No injected client -- builds a real openai.OpenAI(...) against
    # config.base_url. Constructing the client never makes a network
    # call (only actually calling summarize/extract/embed would), so
    # this must succeed even with no server listening. Clear proxy env
    # vars first -- this sandbox sets a SOCKS proxy globally, and
    # httpx's default transport would otherwise need the optional
    # `socksio` package just to construct, which is a sandbox artifact
    # unrelated to LlamaClient's own correctness.
    for var in ("ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.delenv(var, raising=False)

    LlamaClient(config)
