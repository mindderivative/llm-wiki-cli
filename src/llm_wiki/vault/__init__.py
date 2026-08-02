"""vault — create/load/validate vault directory trees; recent-vaults list.

See ARCHITECTURE.md §5 (filesystem layout) and §7 (responsibility table).
"""

from llm_wiki.vault.manager import (
    REQUIRED_DIRS,
    REQUIRED_FILES,
    RecentVaultEntry,
    VaultManager,
)

__all__ = ["VaultManager", "RecentVaultEntry", "REQUIRED_DIRS", "REQUIRED_FILES"]
