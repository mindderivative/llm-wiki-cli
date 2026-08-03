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

All under `src/llm_wiki/`, with tests in `tests/`. **77/77 tests passing**
against the real `~/pyDev/venv` interpreter as of this session.

| Module | File(s) | Status |
|---|---|---|
| Package scaffold | `pyproject.toml`, `src/llm_wiki/{vault,stager,ingest,llm,compiler,graph,lint,vcs,storage}/__init__.py` | Done — subpackages beyond `storage`/`vault`/`stager`/`ingest` are docstring-only stubs, not implemented |
| `config` | `config.py` | Done — `LlamaServerConfig`, `VaultSettings` (load/save `.llm-wiki-config`, derived path properties) |
| `models` | `models.py` | Done — `QueueItem` (10-value `QueueStatus`, `failed_at_step`, defaults to `STAGED`), `Note`, `Chunk`, `Link`, `LintFinding` (mirror §6 SQLite schema); typed exception hierarchy (`VaultNotFoundError`, `StorageError`, `IngestionError`, etc.); public `utcnow()` helper |
| `storage` | `storage/{schema,engine,queue_repo,chunk_repo}.py` | Done — `StorageEngine`: connect/init_schema/rebuild/close, best-effort `sqlite-vec` loading, FK + CHECK constraints verified against real SQLite. `queue` table has `failed_at_step`, schema version 2. `queue_repo`/`chunk_repo`: shared row (de)serialization for `stager`+`ingest`, no internal `.commit()` (callers wrap writes in `with storage.conn:`). `queue_repo` now also has `get_queue_row`/`list_queue_rows`/`list_pool` (new this session — the pool query is `list_pool()`: non-terminal items, oldest first, optional status filter) |
| `vault` | `vault/manager.py` | Done — `VaultManager`: create/load/validate vault trees, seeds `wiki/index.md`/`log.md`/`SCHEMA.md`, wires in `StorageEngine` on create, cross-vault recent-vaults list (path injectable for tests). `REQUIRED_DIRS` now includes `raw/.staged/` |
| `stager` | `stager/{stager,cleanup}.py` | Done — `stage()`: copies (never moves/deletes) into `raw/.sources/` + `raw/.staged/`, records `STAGED`/`FAILED`. `verify_and_clean()`: hash-verifies the archive, deletes the now-redundant original from `raw/`'s top level, flips to `FAILED` on mismatch. Deliberately separate functions (SRP) — **now composed together for the first time**, by `wiki-cli ingest add` (see below). Step 1 of the pipeline only — see `INGEST_PLAN.md` §2.1. 10 tests |
| `ingest` | `ingest/{accept,atomize,pipeline}.py` | `accept()` (step 2, `STAGED`→`QUEUED`). `atomize()` (step 3, `QUEUED`/`PARSING`→`PARSED`, plaintext/Markdown chunking via `markdown-it-py`'s block tokenizer — `#` inside fenced code blocks correctly ignored). `pipeline.py` (**new this session**): `step_once()`/`advance()`, the generic status→function dispatcher (INGEST_PLAN.md §4) — this is what lets the CLI drive the pipeline without knowing which concrete function handles which status. 18 tests (12 accept/atomize + 6 dispatcher) |
| CLI | `cli.py`, `__main__.py` | `vault` group done (smoke-tested only, still no automated tests — flagged tech debt). **`ingest` group is new this session and fully tested**: `add` (stages + cleans, composing `stager` for the first time), `list`, `status`, `step` (single id or `--count`/`--status` pool batch), `run` (explicit ids or `--count N\|AUTO`, stops on `FAILED`, `--count AUTO` interruptible via Ctrl-C). **`run` performs no commit yet** — says so in its own output; `vcs` isn't built. `retry`/`watch` deliberately not built this pass (see below). 14 CLI tests via `typer.testing.CliRunner` — first automated CLI coverage in the project. Run via `python -m llm_wiki ...`; not a `[project.scripts]` entry point (see gotcha below) |

Not started: `ingest` steps 4–6 (`compile()`, cascade-update, `COMPLETED`),
`llm`, `compiler`, `graph`, `lint`, `vcs`, `ingest retry`/`watch`.

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

## Session: stager cleanup — verify_and_clean()

User flagged the open gap from last session (stage()'s drop-zone
duplication) and specified the fix directly: hash-verify the archive
against the original, then delete the original, as its own SOLID/SRP
function separate from both `stage()` and the (still nonexistent)
watcher. **36/36 tests passing** (was 30, +6).

- `src/llm_wiki/stager/cleanup.py` (new): `verify_and_clean(item,
  original_path, vault_root, storage) -> QueueItem`. Hash-compares
  `raw/.sources/` archive against `original_path`; deletes
  `original_path` on a match, but only if it's still sitting at the
  literal top level of `raw/` (never touches external `ingest add`
  sources or anything already in `.sources/`/`.staged/`). Mismatch →
  flips item to `FAILED`/`failed_at_step=STAGED`, refuses to delete.
  Delete failure after a confirmed match → logged, item stays `STAGED`
  (archive's already verified intact — a stray duplicate is cosmetic,
  not a correctness problem). No-ops safely if there's nothing to do,
  so it's safe to call unconditionally after `stage()`.
- `src/llm_wiki/stager/_repo.py` (new): factored `QueueItem` <-> `queue`
  row (de)serialization out of `stager.py`, shared by `stage()`'s insert
  and `verify_and_clean()`'s update — avoids duplicating the mapping
  logic across the two now-separate functions.
- **Deliberately NOT wired together** — `stage()` does not call
  `verify_and_clean()` internally, and neither is called by anything yet
  (no CLI/watcher exists). Whoever builds `wiki-cli ingest add` next
  (§9 item 3) must call both, in sequence — flagged clearly in
  `INGEST_PLAN.md` §7 so this isn't silently forgotten.
- `tests/test_stager_cleanup.py` (new, 6 tests): match+delete, no-op for
  external sources, no-op if already cleaned, no-op for a failed stage,
  hash-mismatch → FAILED, delete-failure → stays STAGED.

## Session: ingest steps 2–3 — accept() + atomize()

Picked up `INGEST_PLAN.md` §9 build order item 2. **48/48 tests passing**
(was 36; +12 for `ingest`, net 0 for the refactor since it's a pure move).

- `src/llm_wiki/ingest/accept.py` (new): `accept(item, storage)` — step 2,
  `STAGED` → `QUEUED`. Confirms both files `stage()` produced (working
  copy + archive) still exist and are readable before `ingest` takes
  ownership. No `-ING` precursor (single fast check, not real work) —
  same one-write shape as `stage()`. `FAILED`/`failed_at_step=QUEUED` on
  a missing/unreadable file. No-ops if `item.status != STAGED`.
- `src/llm_wiki/ingest/atomize.py` (new): `atomize(item, storage)` — step
  3, `QUEUED` → `PARSING` → `PARSED`. Chunks `item.raw_path` into the
  `chunks` table. Markdown splits on headings using `markdown-it-py`'s
  **block tokenizer** (not a line regex) specifically so a `#` inside a
  fenced code block is never mistaken for a heading — verified with a
  dedicated test. Plaintext becomes one chunk. Unsupported extensions,
  empty files, and invalid UTF-8 all fail cleanly
  (`failed_at_step=PARSING`) rather than producing garbage chunks.
  Two-phase status write per the atomicity contract: `PARSING` commits
  alone before chunking starts; `PARSED` commits together with every
  chunk it produced, one transaction, per `INGEST_PLAN.md` §3.
- **Refactor forced by this** (done cleanly, not deferred): `ingest`
  needed the exact same `queue`-row insert/update logic `stager` already
  had. Rather than duplicate it a second time, moved it out of
  `stager/_repo.py` into `storage/queue_repo.py` (+ new
  `storage/chunk_repo.py` for chunk inserts) — `storage` already owns the
  SQL layer per its own docstring, so this is centralizing, not adding a
  new concern. Both repo modules dropped their internal `.commit()` —
  callers now wrap writes in `with storage.conn:` (same pattern
  `StorageEngine.init_schema()` already used). This is *why* `atomize()`
  can commit its chunk inserts and `PARSED` status in one transaction;
  without it, a crash between "chunks written" and "status updated"
  would've been possible despite the atomicity contract saying otherwise.
  `stage()`/`verify_and_clean()`/`accept()` all updated to the same
  `with storage.conn:` pattern for consistency, even though their
  single-row writes didn't strictly need it. All 10 existing `stager`
  tests still pass unchanged after the refactor.
- `tests/test_ingest_accept.py` (4 tests), `tests/test_ingest_atomize.py`
  (8 tests, including the code-fence heading edge case).
- `wiki-cli vault create`/`validate` re-smoke-tested — still clean.

## Session: ingest pipeline dispatcher + wiki-cli ingest add/list/status/step/run

Picked up `INGEST_PLAN.md` §9 build order item 3. **77/77 tests passing**
(was 48; +29: 8 `queue_repo` read-path, 6 `pipeline`, 1 `atomize` retry
regression, 14 CLI).

- `src/llm_wiki/ingest/pipeline.py` (new): the generic step dispatcher —
  a `status -> handler` table (`STAGED: accept, QUEUED: atomize,
  PARSING: atomize`) plus `step_once(item, storage)` (look up and run
  exactly one handler, no-op if none registered) and
  `advance(item, storage)` (loop `step_once` until status stops
  changing — i.e. terminal, or no handler registered). CLI/watcher never
  need to hardcode "what comes next" — this table is the single place
  that mapping lives, per §4.
- **Bug found while wiring `PARSING` into the dispatcher**:
  `atomize()`'s guard clause only accepted `item.status == QUEUED`, so a
  crash-parked `PARSING` item (the atomicity contract's whole reason for
  `-ING` statuses existing) would have silently no-op'd instead of being
  retried from scratch. Widened to `(QUEUED, PARSING)`. Added
  `test_atomize_retries_from_parking_status` as a regression test.
- `storage/queue_repo.py`: added the read-path — `get_queue_row(storage,
  item_id)`, `list_queue_rows(storage, *, status=None)`, and
  `list_pool(storage, *, status=None, limit=None)` (excludes
  `COMPLETED`/`FAILED`, ordered oldest-`created_at`-first). `list_pool`
  is "the pool" from §4/§5 — the batch-selection source for `step
  --count`/`run --count`.
- `src/llm_wiki/cli.py`: new `ingest` sub-app —
  - `add PATH` — runs `stage()` → `verify_and_clean()` → `accept()` in
    sequence, the first place these get composed into an actual chain
    instead of being called directly by tests.
  - `list [--status]` — table over `list_queue_rows()`.
  - `status ID` — full detail for one item; errors on missing id.
  - `step (ID | --count N|AUTO) [--status]` — single-step batch: each
    selected item advances by exactly **one** step via `step_once()`,
    never commits past that. Validates exactly one of `ID`/`--count`.
  - `run (ID... | --count N|AUTO)` — full-completion batch: each
    selected item runs `advance()` to terminal status. Stops immediately
    on the first `FAILED` (later items in the batch are left untouched,
    not attempted) and exits 1. Wrapped in `try/except
    KeyboardInterrupt` so an interrupted batch reports how much it got
    through rather than crashing.
  - **Discovery, not a build**: `sqlite3.Connection` used as `with
    storage.conn:` already rolls back automatically on *any* exception,
    including `KeyboardInterrupt`. So `run`'s interrupt handling needed
    no custom signal masking — the existing atomicity pattern already
    made batch interruption safe by construction. This resolves the
    "SIGINT handling mechanics" gap that was open in `INGEST_PLAN.md` §7.
  - `run` prints `"No commit performed"` at the end unconditionally —
    `vcs.GitEngine` doesn't exist yet (§9 item 5), so batch-end commit is
    a documented no-op for now, not silently skipped.
- **Deliberately deferred, not built**: `ingest retry` and `ingest
  watch`. Both are in `INGEST_PLAN.md` §6's CLI table but weren't named
  in §9 item 3's text. `retry` specifically has a real open design gap:
  a `FAILED` item with `failed_at_step=STAGED` could mean either "`stage()`
  itself failed, nothing was ever written" or "`verify_and_clean()`
  failed post-hoc on an already-good stage" — these need different retry
  behavior and the row doesn't currently distinguish them. Documented in
  §7 rather than building a half-correct version.
- **Documented, not a bug**: `PARSED` items have nothing registered to
  advance them past `PARSED` yet (no `compile()`), so they keep
  reappearing in `list_pool()` results and show a "no further step
  implemented yet" row on every future `run`/`step --count` call until
  `compile()` exists. Noted in §7 so it isn't mistaken for a defect
  later.
- `tests/test_queue_repo.py` (8), `tests/test_ingest_pipeline.py` (6),
  `tests/test_cli_ingest.py` (14, first automated CLI coverage in the
  project, uses `typer.testing.CliRunner`). One test bug caught and
  fixed during writing: asserted `"Stopped" in result.stdout`, but that
  message goes through `err_console` (a `rich.Console(stderr=True)`) —
  lands in `result.stderr`, a separate stream from `result.stdout` under
  this typer/click version.
- Manually smoke-tested end-to-end in the sandbox: `add`, `list`,
  `status`, `step` (single id + `--count`/`--status` batch), `run
  --count AUTO` draining the whole pool, `run` stopping correctly on a
  `FAILED` item with exit code 1, `list --status FAILED` filter.

## Suggested next step

Per `INGEST_PLAN.md` §9, item 3 is now done. Two things remain to unblock
before ingest is a complete pipeline:

- **Item 5, `vcs.GitEngine` (minimal commit support)** — the more
  self-contained of the two. `run`'s batch-end commit is currently a
  documented no-op ("No commit performed"); this is what would make it
  real. Doesn't depend on anything not already built.
- **Item 4, `ingest` steps 4–5 (`compile()`/cascade)** — still blocked on
  the `llm` package, which doesn't exist yet.

`GitEngine` is the more natural next pickup since it has no blockers.
`ingest retry`/`ingest watch` remain deferred per the design gaps noted
in §7 above.
