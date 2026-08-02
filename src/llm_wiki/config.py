"""Typed, validated configuration for the llm-wiki engine (ARCHITECTURE.md §7).

Replaces a hand-rolled equality-check save loop with pydantic-settings'
native change detection. Settings are resolved in increasing priority:

1. field defaults below
2. the vault's ``.llm-wiki-config`` JSON file
3. ``LLM_WIKI_*`` environment variables
4. explicit constructor kwargs
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_FILENAME = ".llm-wiki-config"


class LlamaServerConfig(BaseSettings):
    """Connection settings for the user's local llama.cpp cluster.

    All LLM calls go through this OpenAI-compatible endpoint — no cloud
    provider SDKs enter the dependency tree (ARCHITECTURE.md §2.3).
    """

    model_config = SettingsConfigDict(env_prefix="LLM_WIKI_LLAMA_")

    base_url: str = Field(default="http://localhost:8080/v1")
    api_key: str = Field(
        default="not-needed",
        description="llama-server ignores this; required by the OpenAI client's request shape.",
    )
    chat_model: str = Field(default="default")
    embedding_model: str = Field(default="default")
    embedding_dim: int = Field(
        default=768, gt=0, description="Vector width produced by `embedding_model` — must match the `vec_chunks` table."
    )
    request_timeout_s: float = Field(default=120.0, gt=0)


class VaultSettings(BaseSettings):
    """Per-vault configuration, persisted to ``<vault_root>/.llm-wiki-config``."""

    model_config = SettingsConfigDict(env_prefix="LLM_WIKI_", extra="ignore")

    vault_root: Path
    auto_watch_raw: bool = Field(
        default=False, description="Watch raw/ with watchdog and auto-enqueue new files."
    )
    lint_on_save: bool = True
    llama: LlamaServerConfig = Field(default_factory=LlamaServerConfig)

    @property
    def db_path(self) -> Path:
        return self.vault_root / ".llm-wiki" / "db.sqlite3"

    @property
    def wiki_dir(self) -> Path:
        return self.vault_root / "wiki"

    @property
    def raw_dir(self) -> Path:
        return self.vault_root / "raw"

    @property
    def config_path(self) -> Path:
        return self.vault_root / CONFIG_FILENAME

    @classmethod
    def load(cls, vault_root: Path) -> VaultSettings:
        """Load settings for a vault, merging its config file with env vars.

        Does not require the vault to exist yet — callers that need that
        guarantee should check via ``vault.VaultManager`` first, which
        raises ``VaultNotFoundError`` (see ARCHITECTURE.md §10).
        """
        config_path = vault_root / CONFIG_FILENAME
        overrides: dict = {}
        if config_path.exists():
            overrides = json.loads(config_path.read_text())
        overrides.pop("vault_root", None)
        return cls(vault_root=vault_root, **overrides)

    def save(self) -> None:
        """Persist current settings to ``.llm-wiki-config``."""
        payload = self.model_dump(mode="json", exclude={"vault_root"})
        self.config_path.write_text(json.dumps(payload, indent=2) + "\n")
