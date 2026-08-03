"""Thin `typer` CLI onto `VaultManager`, `stager`, `ingest`, `llm`, and
`vcs` (ARCHITECTURE.md §7, §9; INGEST_PLAN.md §6).

`vault` exposes what `VaultManager` does (create/load/validate/
list-recent/forget). `ingest` composes `stager` + `ingest`'s functions —
`add` stages, `step`/`run` advance the pipeline (INGEST_PLAN.md §4/§5).
`step`/`run` build a `LlamaClient` + dispatch table per invocation
(`_dispatch_table()` below) so `compile()` (INGEST_PLAN.md §10) gets
driven the same way as every other step, without the CLI hardcoding
which status needs an LLM. `run` also does the batch-end commit via
`vcs.GitEngine` once at least one item reaches `COMPLETED` during the
run — see `_ingest_commit_message()` below. No `lint`/`graph` commands
yet; those land once the corresponding subpackages exist.

Run via `python -m llm_wiki <command>` (see `__main__.py`). Not wired up
as a `[project.scripts]` console entry point yet, since the shared
`~/pyDev/venv` isn't set up for an editable install (see chat_history.md).
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from llm_wiki.config import VaultSettings
from llm_wiki.ingest import advance, build_pipeline, step_once
from llm_wiki.llm import LlamaClient
from llm_wiki.models import QueueItem, QueueStatus, VaultAlreadyExistsError, VaultNotFoundError
from llm_wiki.stager import stage, verify_and_clean
from llm_wiki.storage import StorageEngine, get_analysis_row, get_queue_row, list_pool, list_queue_rows
from llm_wiki.vault import VaultManager
from llm_wiki.vcs import GitEngine

app = typer.Typer(
    name="llm-wiki",
    help="Local-first, self-maintaining knowledge base manager.",
    no_args_is_help=True,
)
vault_app = typer.Typer(help="Create, inspect, and validate vaults.", no_args_is_help=True)
app.add_typer(vault_app, name="vault")
ingest_app = typer.Typer(help="Stage files and drive them through the pipeline.", no_args_is_help=True)
app.add_typer(ingest_app, name="ingest")

console = Console()
err_console = Console(stderr=True)
_VAULT_OPTION = typer.Option(Path("."), "--vault", help="Vault root.")


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


@contextlib.contextmanager
def _open_vault(vault_path: Path) -> Iterator[tuple[VaultSettings, StorageEngine]]:
    try:
        settings = VaultManager().load(vault_path)
    except VaultNotFoundError as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    with StorageEngine(settings.db_path, embedding_dim=settings.llama.embedding_dim) as storage:
        yield settings, storage


def _dispatch_table(settings: VaultSettings):
    """A fresh `LlamaClient` (from this vault's `llama` config) plus the
    dispatch table it enables — constructing the client never makes a
    network call, only actually running `compile()`/`cascade()` on an
    item does, so it's cheap to build unconditionally on every
    `step`/`run` invocation rather than threading a lazily-constructed
    one through. `vault_root` is what additionally unlocks `cascade()`
    (INGEST_PLAN.md §11) — `build_pipeline()` only binds it in when both
    are present."""
    return build_pipeline(LlamaClient(settings.llama), settings.vault_root)


def _parse_status(value: str) -> QueueStatus:
    try:
        return QueueStatus(value.strip().upper())
    except ValueError as exc:
        valid = ", ".join(s.value for s in QueueStatus)
        err_console.print(f"[red]Error:[/red] invalid status '{value}'. Valid values: {valid}")
        raise typer.Exit(code=1) from exc


@ingest_app.command("add")
def ingest_add(
    paths: list[Path] = typer.Argument(..., help="File(s) to stage into the vault."),
    vault: Path = _VAULT_OPTION,
) -> None:
    """Stage PATHS (archive + working copy). Does not run or commit them."""
    any_failed = False
    with _open_vault(vault) as (settings, storage):
        table = Table("Title", "Status", "Detail")
        for path in paths:
            item = stage(path, settings.vault_root, storage)
            item = verify_and_clean(item, path, settings.vault_root, storage)
            if item.status == QueueStatus.FAILED:
                any_failed = True
                style, detail = "red", item.error or ""
            else:
                style, detail = "green", f"id={item.id}"
            table.add_row(item.title, f"[{style}]{item.status.value}[/{style}]", detail)
        console.print(table)

    if any_failed:
        raise typer.Exit(code=1)


@ingest_app.command("list")
def ingest_list(
    status: str | None = typer.Option(None, "--status", help="Filter to one status."),
    vault: Path = _VAULT_OPTION,
) -> None:
    """List queue items, optionally filtered by status."""
    status_filter = _parse_status(status) if status else None
    with _open_vault(vault) as (settings, storage):
        items = list_queue_rows(storage, status=status_filter)

    if not items:
        console.print("No queue items.")
        return

    table = Table("ID", "Title", "Status", "Failed at", "Updated")
    for item in items:
        table.add_row(
            str(item.id),
            item.title,
            item.status.value,
            item.failed_at_step.value if item.failed_at_step else "",
            item.updated_at.isoformat(),
        )
    console.print(table)


@ingest_app.command("status")
def ingest_status(
    item_id: int = typer.Argument(..., help="Queue item id."),
    vault: Path = _VAULT_OPTION,
) -> None:
    """Full detail for one queue item."""
    with _open_vault(vault) as (settings, storage):
        item = get_queue_row(storage, item_id)
        analysis = get_analysis_row(storage, item_id) if item is not None else None

    if item is None:
        err_console.print(f"[red]Error:[/red] no queue item with id {item_id}.")
        raise typer.Exit(code=1)

    table = Table(show_header=False)
    table.add_row("id", str(item.id))
    table.add_row("title", item.title)
    table.add_row("status", item.status.value)
    table.add_row("raw_path", str(item.raw_path))
    table.add_row("archive_path", str(item.archive_path) if item.archive_path else "")
    table.add_row("error", item.error or "")
    table.add_row("failed_at_step", item.failed_at_step.value if item.failed_at_step else "")
    table.add_row("created_at", item.created_at.isoformat())
    table.add_row("updated_at", item.updated_at.isoformat())
    if analysis is not None:
        table.add_row("summary", analysis.summary)
        table.add_row("entities", ", ".join(analysis.entities))
        table.add_row("concepts", ", ".join(analysis.concepts))
    console.print(table)


@ingest_app.command("step")
def ingest_step(
    item_id: int | None = typer.Argument(
        None, help="Run exactly the next step for this one item."
    ),
    count: int | None = typer.Option(
        None, "--count", help="Single-step batch: advance up to N pool items by one step each."
    ),
    status: str | None = typer.Option(
        None, "--status", help="Restrict the batch to items currently at this status (only with --count)."
    ),
    vault: Path = _VAULT_OPTION,
) -> None:
    """Advance one item, or a batch of pool items, by exactly one step each.

    Never commits — that only ever happens at the end of a `run`, and
    only for items that reached COMPLETED (INGEST_PLAN.md §5).
    """
    if (item_id is None) == (count is None):
        err_console.print("[red]Error:[/red] provide exactly one of ITEM_ID or --count.")
        raise typer.Exit(code=1)
    if item_id is not None and status is not None:
        err_console.print("[red]Error:[/red] --status only applies together with --count.")
        raise typer.Exit(code=1)

    status_filter = _parse_status(status) if status else None

    with _open_vault(vault) as (settings, storage):
        if item_id is not None:
            item = get_queue_row(storage, item_id)
            if item is None:
                err_console.print(f"[red]Error:[/red] no queue item with id {item_id}.")
                raise typer.Exit(code=1)
            items = [item]
        else:
            items = list_pool(storage, status=status_filter, limit=count)

        if not items:
            console.print("Nothing to do.")
            return

        dispatch_table = _dispatch_table(settings)
        table = Table("ID", "Before", "After")
        for pending in items:
            before = pending.status
            result = step_once(pending, storage, dispatch_table=dispatch_table)
            table.add_row(str(result.id), before.value, result.status.value)
        console.print(table)


def _ingest_commit_message(items: list[QueueItem]) -> str:
    """`"ingest: <title>"` for one item, `"ingest: N files (title1, ...)"`
    for several — INGEST_PLAN.md §5's proposed format, applied to exactly
    the items that reached `COMPLETED` in this run (not everything
    attempted)."""
    if len(items) == 1:
        return f"ingest: {items[0].title}"
    titles = ", ".join(item.title for item in items)
    return f"ingest: {len(items)} files ({titles})"


@ingest_app.command("run")
def ingest_run(
    item_ids: list[int] | None = typer.Argument(
        None, help="Run exactly these items to completion/failure."
    ),
    count: str | None = typer.Option(
        None, "--count", help="Pool-driven: a positive integer, or AUTO to drain the whole pool."
    ),
    vault: Path = _VAULT_OPTION,
) -> None:
    """Run one or more items to COMPLETED/FAILED, one at a time.

    A FAILED item stops the run at whatever count it reached. `--count
    AUTO` is interruptible (Ctrl-C) — stops cleanly between items rather
    than mid-step. Once the run stops (by count, by FAILED, or by
    interrupt), everything that reached COMPLETED is committed in a
    single batch-end commit via `vcs.GitEngine` (INGEST_PLAN.md §5) — a
    run that completes zero items commits nothing.
    """
    ids_given = bool(item_ids)
    if ids_given == (count is not None):
        err_console.print("[red]Error:[/red] provide exactly one of ITEM_IDS or --count.")
        raise typer.Exit(code=1)

    limit: int | None = None
    if count is not None and count.strip().upper() != "AUTO":
        try:
            limit = int(count)
            if limit <= 0:
                raise ValueError
        except ValueError:
            err_console.print("[red]Error:[/red] --count must be a positive integer or AUTO.")
            raise typer.Exit(code=1) from None

    completed = 0
    completed_items: list[QueueItem] = []
    attempted = 0
    failed_item = None
    interrupted = False

    with _open_vault(vault) as (settings, storage):
        if ids_given:
            items = []
            for item_id in item_ids:
                item = get_queue_row(storage, item_id)
                if item is None:
                    err_console.print(f"[red]Error:[/red] no queue item with id {item_id}.")
                    raise typer.Exit(code=1)
                items.append(item)
        else:
            items = list_pool(storage, limit=limit)

        if not items:
            console.print("Nothing to do.")
            return

        dispatch_table = _dispatch_table(settings)
        table = Table("ID", "Title", "Result", "Detail")
        try:
            for pending in items:
                attempted += 1
                result = advance(pending, storage, dispatch_table=dispatch_table)
                if result.status == QueueStatus.COMPLETED:
                    completed += 1
                    completed_items.append(result)
                    table.add_row(str(result.id), result.title, "[green]COMPLETED[/green]", "")
                elif result.status == QueueStatus.FAILED:
                    table.add_row(str(result.id), result.title, "[red]FAILED[/red]", result.error or "")
                    failed_item = result
                    break
                else:
                    table.add_row(
                        str(result.id), result.title, result.status.value, "no further step implemented yet"
                    )
        except KeyboardInterrupt:
            interrupted = True

        console.print(table)

    if interrupted:
        console.print(
            f"[yellow]Stopped (interrupted) after attempting {attempted} item(s), "
            f"{completed} completed.[/yellow]"
        )
    elif failed_item is not None:
        step_name = failed_item.failed_at_step.value if failed_item.failed_at_step else "?"
        err_console.print(f"[red]Stopped:[/red] item {failed_item.id} failed at {step_name}.")

    if completed_items:
        message = _ingest_commit_message(completed_items)
        oid = GitEngine(settings.vault_root).commit(message)
        if oid is not None:
            console.print(f"[green]Committed[/green] {len(completed_items)} item(s) -> {oid[:10]} ({message!r})")
        else:
            console.print("[dim]Nothing to commit — completed item(s) produced no file changes.[/dim]")
    else:
        console.print("[dim]No commit performed — no items reached COMPLETED this run.[/dim]")

    if failed_item is not None:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
