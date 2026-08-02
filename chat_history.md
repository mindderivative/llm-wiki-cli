# Session Log — LLM-Wiki-CLI

Running log of what's been built and decided, for continuity across Cowork
sessions. Update this file at the end of a working session rather than
relying on conversation history other sessions can't see.

## Environment (read this before running anything)

- Python env: `~/pyDev/venv`, now genuinely Python 3.14.6, managed by `uv`.
  - `~/pyDev/venv/.uv-python/` holds a self-contained `uv`-downloaded
    interpreter build. The venv's `bin/python*` symlinks point into it
    using **relative** paths — this was a deliberate fix (see below), do
    not "clean up" these symlinks back to absolute paths.
  - Installed and confirmed importable: `pydantic`, `pydantic-settings`,
    `pytest`, `loguru`, `networkx`, `python-frontmatter`, `markdown-it-py`,
    `openai`, `pygit2`, `sqlite-vec`, `outlines`, `typer` (+ `rich`, its
    transitive dep), `hypothesis`. All five previously-missing packages
    installed cleanly on 2026-08-01 — the earlier network gotcha below no
    longer reproduces, but leaving the note in case it recurs.
  - **Known-broken in this sandbox**: `source ~/pyDev/venv/bin/activate`
    silently falls back to system Python 3.10 instead of the venv's
    3.14.6 interpreter (`which python` after sourcing still resolves to
    `/usr/bin/python`). Cause not yet root-caused. Workaround: invoke the
    venv's interpreter directly — `~/pyDev/venv/bin/python` — rather than
    activating first. This is what the CLI testing steps below do.
- `pyproject.toml` has `[tool.pytest.ini_options] pythonpath = ["src"]` so
  `pytest` works without an editable install (the shared venv doesn't have
  the `hatchling` build backend installed, so `pip install -e .` fails
  there — don't try to "fix" this by installing hatchling into that venv,
  the pythonpath approach is intentional and simpler).
- There is also a second, project-local `.venv/` inside `LLM-Wiki-CLI/`
  that Cowork's own provisioning appears to have created automatically
  from `pyproject.toml` (has everything installed, including the missing
  five above, in editable mode). It's gitignored. Not the venv we're
  standardizing on per user instruction — `~/pyDev/venv` is canonical.

### Gotcha: sandboxed Cowork sessions have no general network access

Package-manager network access ("Package managers only" mode) does not
currently work reliably from an interactive Cowork sandbox shell — direct
requests to `pypi.org`, `github.com`, `files.pythonhosted.org` return
`403` from the sandbox's egress proxy even when the org/user network
setting looks correct. Network settings only take effect on **newly
created** sessions, not one already in progress, and even then it's
unconfirmed whether "fork" counts as a fresh session for this purpose.
If a future session needs to install a new dependency, expect this to
fail and either ask the user to install it on their end, or fork/start a
fresh session and test connectivity first.

## Architecture recap (see `ARCHITECTURE.md` for full detail)

Local-first knowledge base manager. Markdown + Git in `wiki/`/`raw/` is
the only durable state; SQLite + sqlite-vec is a disposable, rebuildable
cache. Backend (`llm_wiki`, pure Python, zero GUI deps) built and tested
before any interface (CLI/MCP/GUI). No cloud LLM SDKs — local
`llama-server` only.

## What's built so far

All under `src/llm_wiki/`, with tests in `tests/`. **30/30 tests passing**
against the real `~/pyDev/venv` interpreter as of this session.

| Module | File(s) | Status |
|---|---|---|
| Package scaffold | `pyproject.toml`, `src/llm_wiki/{vault,stager,ingest,llm,compiler,graph,lint,vcs,storage}/__init__.py` | Done — subpackages beyond `storage`/`vault`/`stager` are docstring-only stubs, not implemented |
| `config` | `config.py` | Done — `LlamaServerConfig`, `VaultSettings` (load/save `.llm-wiki-config`, derived path properties) |
| `models` | `models.py` | Done — `QueueItem` (10-value `QueueStatus`, `failed_at_step`, defaults to `STAGED`), `Note`, `Chunk`, `Link`, `LintFinding` (mirror §6 SQLite schema); typed exception hierarchy (`VaultNotFoundError`, `StorageError`, `IngestionError`, etc.); public `utcnow()` helper |
| `storage` | `storage/schema.py`, `storage/engine.py` | Done — `StorageEngine`: connect/init_schema/rebuild/close, best-effort `sqlite-vec` loading (degrades gracefully if unavailable), FK + CHECK constraints on `chunks` verified against real SQLite. `queue` table has `failed_at_step`, schema version 2 |
| `vault` | `vault/manager.py` | Done — `VaultManager`: create/load/validate vault trees, seeds `wiki/index.md`/`log.md`/`SCHEMA.md`, wires in `StorageEngine` on create, cross-vault recent-vaults list (path injectable for tests). `REQUIRED_DIRS` now includes `raw/.staged/` |
| `stager` | `stager/stager.py` | **New this session** — `stage(source_path, vault_root, storage)`: copies (never moves/deletes) into `raw/.sources/` + `raw/.staged/`, records `STAGED`/`FAILED` as a `queue` row. Step 1 of the pipeline only — see `INGEST_PLAN.md`. 4 tests |
| CLI (vault only) | `cli.py`, `__main__.py` | Done, manually smoke-tested, **no automated tests yet** — thin `typer` wrapper exposing `vault create/info/validate/list-recent/forget`. Run via `python -m llm_wiki ...`; not a `[project.scripts]` entry point (see gotcha below). **No `ingest`/`stager` CLI commands yet** — `stage()` only has direct Python-API test coverage so far |

Not started: `ingest` (steps 2–6), `llm`, `compiler`, `graph`, `lint`,
`vcs`. No CLI commands exist yet for `stager`/`ingest` — only `vault`.

## Key decisions made this session

- Renamed `models._utcnow` → public `models.utcnow` so `vault` could reuse
  it without duplicating the helper.
- `StorageEngine.rebuild()` only resets the schema to empty — it does not
  repopulate from `wiki/`/`raw/`. That's `ingest`/`compiler`/`graph`'s job
  once they exist.
- `VaultManager.create()` is tolerant of `vault_root` already existing as
  a plain (non-vault) directory, like `git init` — only raises
  `VaultAlreadyExistsError` if `.llm-wiki-config` is already there.
- Recent-vaults list lives outside any single vault (`~/.llm-wiki/recent_vaults.json`
  by default), constructor-injectable specifically so tests never touch
  the real home directory.
- Embedding dimension is a config value (`LlamaServerConfig.embedding_dim`,
  default 768) threaded through to `StorageEngine` so the `vec_chunks`
  virtual table width isn't hardcoded in two places.

- Added `src/llm_wiki/cli.py` + `src/llm_wiki/__main__.py`: minimal
  `typer` CLI exposing everything `VaultManager` already does (`create`,
  `info`, `validate`, `list-recent`, `forget`). No `[project.scripts]`
  entry point added — the shared `~/pyDev/venv` isn't set up for an
  editable install (see the `pyproject.toml` note above), so the
  supported invocation is `PYTHONPATH=src ~/pyDev/venv/bin/python -m
  llm_wiki vault ...`, matching how `pytest` already resolves imports.
  User's shell already has `wiki` aliased to their production LLM-Wiki
  folder (unrelated, pre-existing, not to be changed) — do **not** suggest
  `alias wiki=...` for this CLI. User's own alias for it is `wiki-cli`;
  use that name in any future testing instructions.
  If/when an editable install becomes viable, revisit adding a real
  `llm-wiki` console script.
- CLI has **no automated test coverage yet** (only manually smoke-tested:
  create/create-again-conflict/info/validate-healthy/validate-broken/
  list-recent/forget, all exit codes verified). Adding `tests/test_cli.py`
  with `typer.testing.CliRunner` is the natural next step before this
  grows further — call this out as tech debt if more commands get added
  before tests land.

- Designed and wrote `INGEST_PLAN.md` — full state-machine design for
  `stager` (new package, splits filesystem staging out of `ingest`) +
  `ingest`. Key decisions locked in this session:
  - 10-value `QueueStatus` enum: `STAGED, QUEUED, PARSING, PARSED,
    ANALYZING, ANALYZED, CASCADING, CASCADED, COMPLETED, FAILED`, each
    step's terminal write committed atomically with its output; `-ING`
    statuses are pre-work markers with no committed output, so finding
    one after a crash means "safe to retry from scratch."
  - `QueueItem` gets `failed_at_step: QueueStatus | None` — structured,
    not string-parsed from `error`. **Not implemented yet** (see below).
  - Staged working copies move to `raw/.staged/` (was top-level
    `raw/{date}_{slug}.ext`) — fixes a watcher self-trigger bug where the
    non-recursive `raw/` observer would see its own stager output as a
    new file. **Applied**: `ARCHITECTURE.md` §5 updated, and
    `vault/manager.py`'s `REQUIRED_DIRS` now seeds `raw/.staged/` (23/23
    tests still pass, confirmed this session).
  - Git commit timing: batch-end, not per-step. **Revised twice this
    session** — final design: `add`/watcher ONLY stage, never run or
    commit. Processing is a separate, explicit, user-sized batch run
    pulling from "the pool" (every non-terminal queue item, oldest
    first, optionally filtered by `--status`). Two batch primitives:
    `step --count N [--status X]` (single-step batch — advance N items
    by exactly one macro-step, then stop, e.g. "10 through step 2, then
    5 through step 3," no commit) and `run --count N|AUTO` (full-
    completion batch — drive N items to `COMPLETED`/`FAILED`, one commit
    at the end; `AUTO` drains the whole pool with no manual gating,
    stoppable/pausable via graceful SIGINT handling — mechanics still
    TBD). Engine never processes more than one file concurrently, by
    design (one local `llama-server`, not a scaling shortcut). Commit is
    local only (no auto-push). Depends on cascade's note writes being
    write-temp-then-rename (not yet verified, `compiler` isn't built).
  - Non-text formats (PDF/DOCX) explicitly deferred to a later feature
    upgrade — not blocking `ingest`/`stager`.
  - `ARCHITECTURE.md` §3/§7 updated to add `stager` as its own package
    and note `ingest`'s new dependency on `vcs` for batch commits.
## Session: implemented §8/§9 build-order items 1 (prereqs) and 1 (stager)

Picked up exactly where the previous session left off — the three
`INGEST_PLAN.md` §8 prerequisites, then `stager.stage()` itself.
**30/30 tests passing** (was 23; +3 for the model/schema changes, +4 for
`stager`).

- `models.py`: `QueueStatus` expanded to the full 10-value state machine
  (`STAGED, QUEUED, PARSING, PARSED, ANALYZING, ANALYZED, CASCADING,
  CASCADED, COMPLETED, FAILED`), each with an inline comment on who owns
  it and what it means. `QueueItem.failed_at_step: QueueStatus | None`
  added. `QueueItem.status` default changed `QUEUED` → `STAGED` (the row
  now actually gets created at `STAGED` first, by `stager`).
- `storage/schema.py`: `queue.failed_at_step TEXT` column added (nullable).
  `queue.status` DDL default updated to `'STAGED'` to match. `SCHEMA_VERSION`
  bumped 1 → 2 — no real migration logic exists yet (`init_schema` is all
  `CREATE TABLE IF NOT EXISTS`), this is just marking that the shape
  changed for whenever migrations become a real thing.
- `src/llm_wiki/stager/stager.py` (new): `stage(source_path, vault_root,
  storage) -> QueueItem`. Copies `source_path` into `raw/.sources/`
  (untouched original, original filename + date prefix) and
  `raw/.staged/` (working copy, slugified name + date prefix, name
  collisions resolved with a `-2`/`-3`/... suffix). Always copies, never
  moves/deletes the source — see the new open decision below. Never
  raises for staging-domain failures (missing file, I/O error) — returns
  a `FAILED` + `failed_at_step=STAGED` `QueueItem` instead, per
  `INGEST_PLAN.md`'s failure contract; only a genuine `StorageError`
  propagates. 4 tests in `tests/test_stager.py`.
- **New open decision, added to `INGEST_PLAN.md` §7**: `stage()` never
  deletes/moves its source, even when the source is a file the watcher
  observed sitting at top-level `raw/` — meaning that file will sit there
  forever after being staged, once the watcher exists. Deliberately
  chose the non-destructive default since deletion is a one-way door;
  revisit when the watcher actually gets built.
- `wiki-cli vault create`/`validate` re-smoke-tested after the model/schema
  changes — still clean.

## Suggested next step

`ingest` steps 2–3 per `INGEST_PLAN.md` §9 build order item 2: accept a
`STAGED` item (`QUEUED`), then `atomize()` into the `chunks` table
(`PARSING` → `PARSED`), plaintext/Markdown only. Still no `llm` dependency
— fully testable with `tmp_path` fixtures and mocked/trivial chunking.
After that, `wiki-cli ingest add/list/status/step/run` (§9 item 3) to
prove the pool + `--count`/`--status` state machine end-to-end before any
LLM-dependent steps exist.
