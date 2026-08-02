"""VaultManager — create/load/validate vault directory trees; recent-vaults list.

See ARCHITECTURE.md §5 (filesystem layout) and §7 (responsibility table).
Wires `config.VaultSettings` and `storage.StorageEngine` together — this is
the first thing any interface layer touches before doing real work.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from loguru import logger
from pydantic import BaseModel, Field

from llm_wiki.config import CONFIG_FILENAME, LlamaServerConfig, VaultSettings
from llm_wiki.models import VaultAlreadyExistsError, VaultNotFoundError, utcnow
from llm_wiki.storage import StorageEngine

REQUIRED_DIRS = (
    "raw",
    "raw/.sources",
    "raw/.staged",
    "wiki",
    "wiki/sources",
    "wiki/entities",
    "wiki/concepts",
    "wiki/synthesis",
    "wiki/.system/prompts",
)

REQUIRED_FILES = ("wiki/index.md", "wiki/log.md", "SCHEMA.md")

DEFAULT_RECENT_VAULTS_PATH = Path.home() / ".llm-wiki" / "recent_vaults.json"
MAX_RECENT_VAULTS = 20

_INDEX_TEMPLATE = """# {name}

This is the root index of your LLM-Wiki vault. The compiler extends this
file as notes are added — treat it as a living table of contents, not a
fixed document.

## Sections

- [[sources]] — per-source summaries
- [[entities]] — profiles of core subjects
- [[concepts]] — foundational principles
- [[synthesis]] — cross-cutting insights
"""

_LOG_TEMPLATE = """# Log

Chronological record of ingestion and compilation events. Newest entries
at the top.
"""

_SCHEMA_TEMPLATE = """# Vault Schema

Operational rules for notes in this vault. This file documents intent for
humans and LLM prompts — the `lint` engine enforces it in code, not by
parsing this file.

## Note types

- `source`    — `wiki/sources/`   — one per ingested raw document
- `entity`    — `wiki/entities/`  — profile of a person, org, or system
- `concept`   — `wiki/concepts/`  — a foundational principle or idea
- `synthesis` — `wiki/synthesis/` — a cross-cutting insight spanning notes

## Required frontmatter

Every note must declare:

- `type`  — one of the note types above
- `title` — human-readable title
- `tags`  — list of strings (may be empty)

## Linking

Reference other notes with `[[slug]]` wikilinks. `graph` parses these;
`lint` flags links that resolve to nothing.
"""


class RecentVaultEntry(BaseModel):
    """One row of the cross-vault "recent vaults" list."""

    path: Path
    last_opened: datetime = Field(default_factory=utcnow)


class VaultManager:
    """Create, load, and validate vault directory trees.

    Also owns the "recent vaults" list — the one piece of state that
    lives outside any single vault_root. Its storage location is
    injectable so tests never touch the real home directory.
    """

    def __init__(self, recent_vaults_path: Path | None = None) -> None:
        self.recent_vaults_path = recent_vaults_path or DEFAULT_RECENT_VAULTS_PATH

    # -- creation / loading -------------------------------------------------

    def is_vault(self, vault_root: Path) -> bool:
        """True if `vault_root` has already been initialized as a vault."""
        return (vault_root / CONFIG_FILENAME).is_file()

    def create(
        self,
        vault_root: Path,
        *,
        llama: LlamaServerConfig | None = None,
        auto_watch_raw: bool = False,
        lint_on_save: bool = True,
    ) -> VaultSettings:
        """Initialize a new vault at `vault_root`.

        Tolerant of `vault_root` already existing as a plain directory
        (like `git init`), but raises `VaultAlreadyExistsError` if it's
        already an llm-wiki vault.
        """
        if self.is_vault(vault_root):
            raise VaultAlreadyExistsError(f"{vault_root} is already an llm-wiki vault.")

        vault_root.mkdir(parents=True, exist_ok=True)
        for rel_dir in REQUIRED_DIRS:
            (vault_root / rel_dir).mkdir(parents=True, exist_ok=True)

        name = vault_root.resolve().name
        self._write_if_absent(vault_root / "wiki" / "index.md", _INDEX_TEMPLATE.format(name=name))
        self._write_if_absent(vault_root / "wiki" / "log.md", _LOG_TEMPLATE)
        self._write_if_absent(vault_root / "SCHEMA.md", _SCHEMA_TEMPLATE)

        settings = VaultSettings(
            vault_root=vault_root,
            auto_watch_raw=auto_watch_raw,
            lint_on_save=lint_on_save,
            llama=llama or LlamaServerConfig(),
        )
        settings.save()

        with StorageEngine(settings.db_path, embedding_dim=settings.llama.embedding_dim) as storage:
            storage.init_schema()

        logger.info(f"Initialized vault at {vault_root}")
        self._record_recent(vault_root)
        return settings

    def load(self, vault_root: Path) -> VaultSettings:
        """Load an existing vault's settings.

        Raises `VaultNotFoundError` if `vault_root` isn't a valid vault.
        """
        if not self.is_vault(vault_root):
            raise VaultNotFoundError(f"{vault_root} is not an llm-wiki vault (no {CONFIG_FILENAME}).")
        settings = VaultSettings.load(vault_root)
        self._record_recent(vault_root)
        return settings

    def validate(self, vault_root: Path) -> list[str]:
        """Check a vault's structural health.

        Returns a list of human-readable problems — empty means healthy.
        Raises `VaultNotFoundError` if there's no vault to validate at all.
        """
        if not self.is_vault(vault_root):
            raise VaultNotFoundError(f"{vault_root} is not an llm-wiki vault (no {CONFIG_FILENAME}).")

        problems: list[str] = []

        try:
            VaultSettings.load(vault_root)
        except Exception as exc:  # noqa: BLE001 - surfaced as a finding, not raised
            problems.append(f"{CONFIG_FILENAME} is present but invalid: {exc}")

        for rel_dir in REQUIRED_DIRS:
            if not (vault_root / rel_dir).is_dir():
                problems.append(f"missing directory: {rel_dir}")

        for rel_file in REQUIRED_FILES:
            if not (vault_root / rel_file).is_file():
                problems.append(f"missing file: {rel_file}")

        return problems

    @staticmethod
    def _write_if_absent(path: Path, content: str) -> None:
        if not path.exists():
            path.write_text(content)

    # -- recent vaults --------------------------------------------------

    def list_recent(self, limit: int = 10) -> list[RecentVaultEntry]:
        """Most-recently-opened vaults first."""
        return self._read_recent()[:limit]

    def forget_recent(self, vault_root: Path) -> None:
        """Remove `vault_root` from the recent-vaults list, if present."""
        resolved = vault_root.resolve()
        entries = [e for e in self._read_recent() if e.path != resolved]
        self._write_recent(entries)

    def _record_recent(self, vault_root: Path) -> None:
        resolved = vault_root.resolve()
        entries = [e for e in self._read_recent() if e.path != resolved]
        entries.insert(0, RecentVaultEntry(path=resolved))
        self._write_recent(entries[:MAX_RECENT_VAULTS])

    def _read_recent(self) -> list[RecentVaultEntry]:
        if not self.recent_vaults_path.exists():
            return []
        try:
            raw = json.loads(self.recent_vaults_path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(f"Could not read {self.recent_vaults_path}, starting fresh ({exc}).")
            return []
        return [RecentVaultEntry.model_validate(item) for item in raw]

    def _write_recent(self, entries: list[RecentVaultEntry]) -> None:
        self.recent_vaults_path.parent.mkdir(parents=True, exist_ok=True)
        payload = [e.model_dump(mode="json") for e in entries]
        self.recent_vaults_path.write_text(json.dumps(payload, indent=2) + "\n")
