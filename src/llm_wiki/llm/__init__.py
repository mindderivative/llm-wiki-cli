"""llm — wrap llama-server's OpenAI-compatible endpoint.

`LlamaClient` (chat + structured extraction via `outlines`, plus
embeddings) lives here — see `llm/client.py` and INGEST_PLAN.md §10 for
the full design. `LlmClient` is the narrow `Protocol` `ingest.compile()`
actually depends on, so tests can inject a trivial fake instead of a
real `LlamaClient`. No cloud provider SDKs enter this package
(ARCHITECTURE.md §2.3).
"""

from llm_wiki.llm.client import ExtractionResult, LlamaClient, LlmClient

__all__ = ["LlamaClient", "LlmClient", "ExtractionResult"]
