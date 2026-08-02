"""Thin `typer` CLI onto `VaultManager` (ARCHITECTURE.md §7, §9).

Deliberately minimal — exposes exactly what `VaultManager` already does
(create/load/validate/list-recent/forget). No ingest/compile/lint commands
yet; those land once the corresponding subpackages are implemented.

Run via `python -m llm_wiki <command>` (see `__main__.py`). Not wired up
as a `[project.scripts]` console entry point yet, since the shared
`~/pyDev/venv` isn't set up for an editable install (see chat_history.md).
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from llm_wiki.models import VaultAlreadyExistsError, VaultNotFoundError
from llm_wiki.vault import VaultManager

app = typer.Typer(
    name="llm-wiki",
    help="Local-first, self-maintaining knowledge base manager.",
    no_args_is_help=True,
)
vault_app = typer.Typer(help="Create, inspect, and validate vaults.", no_args_is_help=True)
app.add_typer(vault_app, name="vault")

console = Console()
err_console = Console(stderr=True)


def _manager() -> VaultManager:
    return VaultManager()


@vault_app.command("create")
def vault_create(
    path: Path = typer.Argument(..., help="Directory to initialize as a new vault."),
    auto_watch_raw: bool = typer.Option(False, help="Watch raw/ and auto-enqueue new files."),
    lint_on_save: bool = typer.Option(True, help="Run lint checks whenever a note is saved."),
) -> None:
    """Initialize a new vault at PATH."""
    try:
        settings = _manager().create(path, auto_watch_raw=auto_watch_raw, lint_on_save=lint_on_save)
    except VaultAlreadyExistsError as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(f"[green]Initialized vault[/green] at {settings.vault_root.resolve()}")
    console.print(f"  config: {settings.config_path}")
    console.print(f"  db:     {settings.db_path}")


@vault_app.command("info")
def vault_info(
    path: Path = typer.Argument(Path("."), help="Vault root (defaults to current directory)."),
) -> None:
    """Show a vault's resolved settings."""
    try:
        settings = _manager().load(path)
    except VaultNotFoundError as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    table = Table(show_header=False)
    table.add_row("vault_root", str(settings.vault_root.resolve()))
    table.add_row("auto_watch_raw", str(settings.auto_watch_raw))
    table.add_row("lint_on_save", str(settings.lint_on_save))
    table.add_row("llama.base_url", settings.llama.base_url)
    table.add_row("llama.chat_model", settings.llama.chat_model)
    table.add_row("llama.embedding_model", settings.llama.embedding_model)
    table.add_row("llama.embedding_dim", str(settings.llama.embedding_dim))
    table.add_row("db_path", str(settings.db_path))
    console.print(table)


@vault_app.command("validate")
def vault_validate(
    path: Path = typer.Argument(Path("."), help="Vault root (defaults to current directory)."),
) -> None:
    """Check a vault's structural health."""
    try:
        problems = _manager().validate(path)
    except VaultNotFoundError as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    if not problems:
        console.print("[green]Vault is healthy.[/green]")
        return

    console.print(f"[yellow]Found {len(problems)} problem(s):[/yellow]")
    for problem in problems:
        console.print(f"  - {problem}")
    raise typer.Exit(code=1)


@vault_app.command("list-recent")
def vault_list_recent(
    limit: int = typer.Option(10, help="Max number of entries to show."),
) -> None:
    """List recently opened vaults, most recent first."""
    entries = _manager().list_recent(limit=limit)
    if not entries:
        console.print("No recent vaults.")
        return

    table = Table("Path", "Last opened")
    for entry in entries:
        table.add_row(str(entry.path), entry.last_opened.isoformat())
    console.print(table)


@vault_app.command("forget")
def vault_forget(
    path: Path = typer.Argument(..., help="Vault to remove from the recent-vaults list."),
) -> None:
    """Remove PATH from the recent-vaults list (does not delete anything on disk)."""
    _manager().forget_recent(path)
    console.print(f"Removed {path.resolve()} from recent vaults (if it was present).")


if __name__ == "__main__":
    app()
