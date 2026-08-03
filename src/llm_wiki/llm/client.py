"""`LlamaClient` — wraps `llama-server`'s OpenAI-compatible endpoint.

See INGEST_PLAN.md §10 for the full design writeup. Two things live here:

- `LlmClient` — a `Protocol`, not an ABC. The narrow interface
  `ingest.compile()` actually depends on (`summarize`/`extract`/`embed`).
  Deliberately small so tests can inject a trivial hand-written fake
  without touching `outlines`/`openai` at all (ARCHITECTURE.md §11).
- `LlamaClient` — the real implementation, talking to `llama-server` via
  the `openai` SDK (chat completions + embeddings) and `outlines`
  (grammar-constrained structured extraction).

Structured extraction goes through `outlines.from_openai(client, model)`,
which does a real `isinstance(client, openai.OpenAI | ...)` check — a
bare duck-typed fake does *not* work as a test double here.
`unittest.mock.MagicMock(spec=openai.OpenAI)` does (`spec=` makes the
isinstance check pass), and the only surface `outlines`' OpenAI backend
actually touches is `client.chat.completions.create()` and
`.choices[i].message.{content,refusal}` — both easy to stub. This is
what lets `LlamaClient`'s own tests exercise the *real* `outlines`
integration with zero network access. Verified against the actually
installed `outlines` version in this sandbox before writing this file —
`model(prompt, output_type=SomePydanticModel)` returns a **JSON string**
(not an already-parsed instance), so callers must
`SomeModel.model_validate_json(raw)` themselves.
"""

from __future__ import annotations

from typing import Protocol

import openai
import outlines
from outlines.exceptions import OutlinesError
from pydantic import BaseModel, Field

from llm_wiki.config import LlamaServerConfig
from llm_wiki.models import CompilationError, Mention

_SUMMARIZE_SYSTEM_PROMPT = (
    "You are a precise summarizer for a personal knowledge wiki. Summarize "
    "the following source material in 2-4 sentences, covering only what is "
    "actually present in the text. Do not add information, opinions, or "
    "speculation that isn't in the source."
)

_EXTRACT_INSTRUCTIONS = (
    "Identify every named entity (people, organizations, products, or "
    "other specific named things) and every general concept (ideas, "
    "principles, methods) explicitly discussed in the text below. Only "
    "include items that are actually present in the text — do not invent "
    "or infer anything not mentioned. For each one, also write a single "
    "concise sentence capturing what this specific text says about it — "
    "only what's actually stated here, not general background "
    "knowledge.\n\n"
)


class ExtractionResult(BaseModel):
    """Structured output of `LlamaClient.extract()`. `entities`/`concepts`
    are `Mention`s (name + a one-sentence note of what this text says
    about it) — richer than a bare name list so a first-mention entity/
    concept note actually has content, without needing a second LLM call
    per entity (INGEST_PLAN.md §12)."""

    entities: list[Mention] = Field(default_factory=list)
    concepts: list[Mention] = Field(default_factory=list)


class LlmClient(Protocol):
    """What `ingest.compile()` depends on. `LlamaClient` implements this
    structurally — nothing else needs to subclass it, just match the
    shape."""

    def summarize(self, text: str) -> str: ...

    def extract(self, text: str) -> ExtractionResult: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class LlamaClient:
    """Real `LlmClient` — talks to `llama-server`'s OpenAI-compatible
    endpoint. `openai_client` is injectable for tests (a
    `MagicMock(spec=openai.OpenAI)`); defaults to a real client built
    from `config`.
    """

    def __init__(self, config: LlamaServerConfig, *, openai_client: openai.OpenAI | None = None) -> None:
        self.config = config
        self._client = openai_client or openai.OpenAI(
            base_url=config.base_url, api_key=config.api_key, timeout=config.request_timeout_s
        )
        self._structured_model = outlines.from_openai(self._client, config.chat_model)

    def summarize(self, text: str) -> str:
        """Plain (unstructured) chat completion — a short summary of `text`."""
        try:
            response = self._client.chat.completions.create(
                model=self.config.chat_model,
                messages=[
                    {"role": "system", "content": _SUMMARIZE_SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                ],
            )
        except openai.OpenAIError as exc:
            raise CompilationError(f"summarize() failed: {exc}") from exc
        return (response.choices[0].message.content or "").strip()

    def extract(self, text: str) -> ExtractionResult:
        """Grammar-constrained extraction via `outlines` — guaranteed
        schema-valid JSON, parsed into `ExtractionResult`."""
        try:
            raw = self._structured_model(_EXTRACT_INSTRUCTIONS + text, output_type=ExtractionResult)
        except (openai.OpenAIError, OutlinesError) as exc:
            raise CompilationError(f"extract() failed: {exc}") from exc
        try:
            return ExtractionResult.model_validate_json(raw)
        except ValueError as exc:
            raise CompilationError(f"extract() returned invalid JSON: {exc}") from exc

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embedding vectors for `texts`, in order. Not consumed by
        `compile()` — `cascade()` (INGEST_PLAN.md §10, not built yet) is
        its first real caller; included now since it's the same
        client/endpoint and part of this package's agreed scope."""
        if not texts:
            return []
        try:
            response = self._client.embeddings.create(model=self.config.embedding_model, input=texts)
        except openai.OpenAIError as exc:
            raise CompilationError(f"embed() failed: {exc}") from exc
        return [item.embedding for item in response.data]
