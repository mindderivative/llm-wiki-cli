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

All under `src/llm_wiki/`, with tests in `tests/`. **197/197 tests passing**
against the real `~/pyDev/venv` interpreter as of this session.

`graph`/`lint` (new this session — see `GRAPH_LINT_PLAN.md`): `graph.rebuild_links(vault_root, storage, *, full=False)` walks `wiki/`, reconciles the `notes` table against actual files (discovers hand-authored notes, detects hand-edits via `content_hash`, detects deletions), and maintains the `links` table from `[[wikilink]]`s. `lint.run(vault_root, storage)` reports `broken_link`/`isolated_note` (from the DB) and `schema_violation` (its own small filesystem walk) findings plus a health score, persisted per-run under a `run_id`. New CLI: `wiki-cli graph rebuild [--full]`, `wiki-cli lint run` — deliberately **not** auto-wired into `ingest run` (asked the user; kept separate for this first cut). New repo modules `storage/links_repo.py`, `storage/lint_repo.py`; `storage/notes_repo.py` gained `list_note_rows()`/`delete_note_row()` and `update_note_row()` was broadened to cover every mutable field. `textutil.py` gained `content_hash()` (moved out of `compiler`, now shared with `graph`).

**Two real bugs found and fixed this session, both via manual end-to-end smoke testing, not caught by unit tests alone**: (1) `graph.rebuild_links()`'s writes weren't wrapped in `with storage.conn:`, so nothing actually persisted across a connection reopen — fixed, regression test added. (2) `content_hash` matching was wrongly treated as "links already extracted," since `compiler` sets that hash without ever touching `links` — a freshly-`cascade()`d note's links were silently never indexed on its first `graph rebuild`. Fixed: link extraction now always runs every file, every call; only the `notes` row write itself is hash-gated. Also fixed a latent bug in `ingest status`'s CLI output (`", ".join(analysis.entities)` broke once `entities` became `list[Mention]` in the prior session — never triggered because no test exercised it with real data).

| Module | File(s) | Status |
|---|---|---|
| Package scaffold | `pyproject.toml`, `src/llm_wiki/{vault,stager,ingest,llm,compiler,graph,lint,vcs,storage}/__init__.py` | Done — subpackages beyond `storage`/`vault`/`stager`/`ingest`/`vcs`/`llm`/`compiler` are docstring-only stubs, not implemented |
| `config` | `config.py` | Done — `LlamaServerConfig`, `VaultSettings` (load/save `.llm-wiki-config`, derived path properties) |
| `models` | `models.py` | Done — `QueueItem` (10-value `QueueStatus`, `failed_at_step`, defaults to `STAGED`), `Note`, `Chunk`, `Analysis`, `Mention` (**new this session** — `name` + a one-sentence `note`, replaces bare entity/concept name strings), `Link`, `LintFinding`; typed exception hierarchy; public `utcnow()` helper |
| `textutil` | `textutil.py` | `slugify(text)` — shared, used by both `stager` and `compiler` |
| `storage` | `storage/{schema,engine,queue_repo,chunk_repo,analysis_repo,notes_repo}.py` | Done — `StorageEngine`: connect/init_schema/rebuild/close, best-effort `sqlite-vec` loading, FK + CHECK constraints verified against real SQLite. Schema version 3. `queue_repo`/`chunk_repo`/`analysis_repo`/`notes_repo`: shared row (de)serialization, no internal `.commit()`. `notes_repo` gained `update_note_row()` (**new this session** — in-place `tags`/`sources`/`content_hash`/`updated_at` update, used by the entity/concept append path) |
| `vault` | `vault/manager.py` | Done — `VaultManager`: create/load/validate vault trees, seeds `wiki/index.md`/`log.md`/`SCHEMA.md`, wires in `StorageEngine` on create, cross-vault recent-vaults list. **Does not `git init` the vault** — lazy, on first real commit |
| `stager` | `stager/{stager,cleanup}.py` | Done — `stage()`/`verify_and_clean()`, composed by `wiki-cli ingest add`. Step 1 of the pipeline only. 10 tests |
| `llm` | `llm/client.py` | `LlamaClient`: wraps `llama-server`'s OpenAI-compatible endpoint via `openai` (chat + embeddings) and `outlines` (grammar-constrained structured extraction). `ExtractionResult.entities`/`.concepts` are now `list[Mention]` (**changed this session** — a name plus a one-sentence note of what the text says about it, not a bare string), same single `extract()` call as before, no new LLM call. 11 tests |
| `compiler` | `compiler/notes.py` | `write_source_note(item, analysis, vault_root, storage, llm_client) -> Note` — builds `wiki/sources/{slug}.md`, one chunk (the summary), embedded. `fan_out_mentions(item, analysis, source_note, vault_root, storage, llm_client) -> list[Note]` (**new this session** — the actual "cascade") — for every extracted entity/concept, creates a `wiki/entities/`/`wiki/concepts/{slug}.md` stub (frontmatter + a "Mentioned in" bullet carrying the `Mention.note` text, embedded once) or appends a new dated bullet to an already-existing one (append-only, idempotent per source, no re-embed on append). Slug matching by `slugify(name)`, cross-type collisions suffixed same as same-titled sources. 19 tests (6 `write_source_note()` + 13 `fan_out_mentions()`) |
| `ingest` | `ingest/{accept,atomize,compile,cascade,pipeline}.py` | `accept()` (step 2), `atomize()` (step 3), `compile()` (step 4). `cascade()` (step 5, `ANALYZED`→`CASCADING`→`COMPLETED`) **now calls both halves** — `write_source_note()` then `fan_out_mentions()`, same transaction as the terminal `COMPLETED` write, straight to `COMPLETED` with no separate durable `CASCADED` state. `pipeline.py`: `step_once()`/`advance()` dispatcher + `build_pipeline(llm_client, vault_root)`. 39 tests (12 accept/atomize + 6 `compile()` + 9 `cascade()` + 12 dispatcher) |
| `vcs` | `vcs/engine.py` | `GitEngine`: `init()` (idempotent) + `commit(message)`. `push()`/`pull()`/`status()` deliberately not built. Exercisable end-to-end for real. 9 tests |
| CLI | `cli.py`, `__main__.py` | `vault` group (smoke-tested only). `ingest` group: `add`, `list`, `status`, `step`, `run` — genuinely drive items all the way to `COMPLETED`, including the entity/concept fan-out. `retry`/`watch` still deliberately not built. 19 CLI tests via `typer.testing.CliRunner` |

Not started: `graph`, `lint`, `vcs.push()`/`pull()`/`status()`,
`ingest retry`/`watch`, watcher (`stager.watch()`), and the follow-on
"§12 decision 2" limitation — entity/concept note embeddings are never
refreshed after their first-mention content, even as the file itself
accumulates more mentions (documented known v1 gap, `INGEST_PLAN.md`
§12).

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

## Session: vcs.GitEngine — minimal commit support, wired into run

Picked up `INGEST_PLAN.md` §9 build order item 5. **89/89 tests passing**
(was 77; +12: 9 `GitEngine`, 3 CLI commit-wiring).

- `src/llm_wiki/vcs/engine.py` (new): `GitEngine(vault_root)`.
  - `init()` — idempotent. Uses `pygit2.discover_repository()` to check
    for an existing repo before creating one; writes `.gitignore` with
    `.llm-wiki/` if either the file or the line is missing. Safe to call
    on every `commit()` (it does), so nothing else needs to call it
    directly.
  - `commit(message)` — `index.add_all(["raw/*"])` +
    `index.add_all(["wiki/*"])` (whole-directory add, per §5's
    partial-failure-handling note — not selective per-item staging),
    then commits if the resulting tree actually differs from `HEAD`
    (or, for the very first commit, if the index has any entries at
    all). Returns the new commit's hex oid, or `None` for a genuine
    no-op — a run that completes zero items, or whose completed items
    produced no real file diff, must not create an empty commit.
  - **Deliberately not built**: `push()`/`pull()`/`status()`. §5 scopes
    this item to local commit only; push stays a separate,
    explicitly-user-triggered action once it exists, never auto-called
    by `ingest` — consistent with the "commit locally, push manually"
    pattern already established for this repo itself.
  - **Design call, not yet applied elsewhere**: vault creation
    (`VaultManager.create()`) does **not** call `GitEngine.init()`.
    The repo comes into existence lazily, the first time `commit()`
    actually runs. Deliberate — item 5's scope is "wired into `run`'s
    batch-end commit," not "wire git init into vault creation." Revisit
    if this turns out to be confusing UX (a brand-new vault has no
    `.git` until something actually completes).
- `cli.py`'s `ingest run`: now collects every item that reaches
  `COMPLETED` during the run (`completed_items`), and after the loop —
  regardless of whether it stopped by count, by `FAILED`, or by
  interrupt — commits exactly those via `GitEngine`, if any. Message
  format is §5's proposed one: `"ingest: <title>"` for a single
  completed item, `"ingest: N files (title1, title2, ...)"` for several
  (`_ingest_commit_message()`, a small pure helper). `step` is
  unaffected — it never commits, by design (§4).
- **Confirms the partial-failure semantics from §5 actually work**:
  verified with a test where item 1 completes, item 2 fails (stopping
  the run), item 3 is never attempted — the resulting commit message
  names only item 1, not item 3, and there's exactly one commit.
- `tests/test_vcs_engine.py` (9, new): init creates/opens idempotently,
  `.gitignore` written and not duplicated, first commit picks up
  `vault create`'s own scaffold files (`wiki/index.md`/`log.md`), commit
  only ever tracks `raw/`/`wiki/` (a file written directly under
  `.llm-wiki/` is confirmed absent from the resulting tree), no-op
  returns `None` and creates no second commit, a real subsequent change
  does create a second commit.
- `tests/test_cli_ingest.py` (+3): since `compile()`/cascade don't exist
  yet, nothing in the real pipeline currently reaches `COMPLETED` to
  exercise `run`'s commit wiring end-to-end. Worked around with a
  `monkeypatch.setitem(ingest_pipeline._STEP_FOR_STATUS, PARSED,
  fake_complete)` fixture that stands in for the not-yet-built compile
  step — flips `PARSED` straight to `COMPLETED` for the duration of one
  test, without touching production dispatcher code. Confirmed with
  `pygit2` directly against the resulting repo (commit count, commit
  message), not just CLI output text.

## Session: llm package + ingest.compile() — step 4a (item 4 split further)

Picked up `INGEST_PLAN.md` §9 item 4. Before writing code: item 4 covers
two real steps (`compile()` and `cascade()`) and had several genuinely
open design questions (how much to build this session; how much an LLM
should be trusted to rewrite existing `wiki/` notes during cascade).
Asked the user directly rather than guessing — see `INGEST_PLAN.md` §10
for both answers, locked in: **this session builds `llm` + `compile()`
only** (`cascade()` deferred to its own session), and **cascade's future
merge behavior will be append-only** (LLM only ever proposes new content
under a dated subsection; existing note text is never sent back through
the LLM for rewriting — chosen for auditability, since a full rewrite
can't distinguish LLM output from a human's manual edits). **119/119
tests passing** (was 89; +30).

- `src/llm_wiki/llm/client.py` (new): `LlamaClient`, `LlmClient`
  (`Protocol`), `ExtractionResult`. Verified against the *actually
  installed* `outlines` version in this sandbox (1.x-era API, no
  `outlines.generate` module) before writing any production code —
  `outlines.from_openai(client, model)` does a real `isinstance` check
  (a bare duck-typed fake fails it), and `model(prompt,
  output_type=SomeModel)` returns a **JSON string**, not an
  already-parsed instance. Found that `unittest.mock.MagicMock(spec=
  openai.OpenAI)` passes the isinstance check and is easy to stub on
  exactly the surface `outlines`' OpenAI backend touches
  (`chat.completions.create()`, `.choices[i].message.{content,refusal}`)
  — confirmed working end-to-end in the sandbox first, so `LlamaClient`'s
  11 tests exercise the *real* `outlines` integration with zero network
  access, per ARCHITECTURE.md §11.
- New `queue_analysis` table (`SCHEMA_VERSION` 2→3) + `Analysis` model +
  `storage/analysis_repo.py` (`upsert_analysis_row`/`get_analysis_row`,
  `INSERT OR REPLACE` — a retried `compile()` overwrites the previous
  attempt, same "redo from scratch" convention as everywhere else).
  `chunk_repo.py` gained `list_chunks_for_queue_item()` (ordered read
  path) — `compile()` reads `atomize()`'s already-committed chunks
  rather than re-parsing the raw file.
- `src/llm_wiki/ingest/compile.py` (new): `compile(item, storage,
  llm_client)` — step 4, `PARSED`→`ANALYZING`→`ANALYZED`. Same two-phase
  atomicity pattern as `atomize()` (durable `ANALYZING` marker commits
  first, chunks-derived text goes to `llm_client.summarize()`/
  `.extract()`, then `queue_analysis` + `ANALYZED` commit together).
  Accepts `PARSED` or `ANALYZING` as valid starts (crash-retry) —
  **got this right from the start**, unlike `atomize()`'s original
  guard-clause bug from an earlier session.
- **Dispatcher wrinkle, resolved**: `compile()` needs an `llm_client` —
  a real external dependency `accept()`/`atomize()` don't have.
  `pipeline.py` gained `build_pipeline(llm_client=None)`, which returns
  the base dispatch table with `compile()` bound in via
  `functools.partial` for both its statuses. `step_once()`/`advance()`
  gained an optional `dispatch_table` kwarg — omit it and every existing
  call site/test is unaffected (base table, `compile()` unregistered).
  This kept the dispatcher's whole point intact (§4: no interface
  hardcodes "what runs next") while accommodating real DI instead of a
  global `LlamaClient`.
- `cli.py`: `ingest step`/`run` now call a new `_dispatch_table()` that
  builds a real `LlamaClient` from the vault's config on every
  invocation (cheap — construction makes no network call) and passes it
  through `build_pipeline()`. `ingest status` shows `summary`/
  `entities`/`concepts` once an item reaches `ANALYZED`.
- **Real behavior change this surfaced, not a bug**: since `compile()`
  is now unconditionally wired in, `advance()` no longer stops quietly
  at `PARSED` — it keeps going into `compile()`, and without a reachable
  `llama-server` that now fails cleanly as `FAILED`/`failed_at_step=
  ANALYZING` (confirmed via a new test,
  `test_run_fails_cleanly_with_no_llama_server_reachable` — a real
  connection error, caught and wrapped, not a crash). Every pre-existing
  CLI test that assumed "the pipeline stops at PARSED" needed updating —
  either to expect the clean failure, or (`with_fake_llm_client` fixture,
  monkeypatches `cli.LlamaClient`) to inject a fake client so the item
  can reach the new real frontier, `ANALYZED`. The three run-commits-
  something tests from last session also needed their fake reworked:
  their old `with_fake_compile_step` monkeypatched the base
  `_STEP_FOR_STATUS[PARSED]` directly, which `build_pipeline()` now
  always overwrites when given a non-`None` `llm_client` (which `cli.py`
  always provides) — replaced with `with_fake_llm_client` (makes real
  `compile()` succeed) + a new `with_fake_cascade_step` (stands in only
  for the step that's still actually missing, `ANALYZED`→`COMPLETED`,
  since `build_pipeline()` never touches that status).
- Also fixed a sandbox-only test artifact: constructing `openai.OpenAI()`
  consults proxy env vars, and this sandbox sets a global SOCKS proxy
  that httpx can't use without the optional `socksio` package — added an
  autouse fixture clearing those vars for `test_cli_ingest.py` (this has
  nothing to do with `LlamaClient`'s own correctness, purely a sandbox
  quirk, same fix already applied narrowly in `test_llm_client.py`).
- `tests/test_llm_client.py` (11, new), `tests/test_ingest_compile.py`
  (6, new, hand-written `FakeLlmClient` — proves `compile()`'s own logic
  independent of any real `LlmClient`), `tests/test_analysis_repo.py`
  (4, new), `tests/test_chunk_repo.py` (3, new), `tests/
  test_ingest_pipeline.py` (+4 — `build_pipeline()`/`dispatch_table`),
  `tests/test_cli_ingest.py` (+2 net after the rework above).

## Session: compiler package + ingest.cascade() — step 4b, source notes only

Picked up `INGEST_PLAN.md` §9 item 4b. Before writing code, asked the
user two clarifying questions (same pattern as the `compile()` session):
whether this session builds source notes only or the full entity/concept
cascade too (**user chose source notes only** — the entity/concept fan-out
is its own future session), and whether a first-mentioned entity/concept's
note should be a minimal stub or an LLM-drafted blurb (**user chose
minimal stub**, locked in now even though it isn't built this session, so
it won't need relitigating later). See `INGEST_PLAN.md` §11 for the full
design writeup. **142/142 tests passing** (was 119; +23 net after +3 for a
CLI test-fixture cleanup unrelated to `cascade()` itself).

- `src/llm_wiki/textutil.py` (new): `slugify()`, moved out of
  `stager/stager.py`'s private `_slugify` — `compiler.write_source_note()`
  needed the identical logic for note slugs, so centralized it (same
  "centralize once a second real consumer exists" call as `queue_repo`
  back in §9 item 2). 5 tests.
- `storage/notes_repo.py` (new): `insert_note_row()`/`get_note_row_by_slug()`.
  `chunk_repo.py` gained `insert_embedding(storage, chunk_id, vector)` —
  verified the actual `sqlite-vec` insert mechanics in the sandbox first
  (`sqlite_vec.serialize_float32(vector)` then `INSERT INTO
  vec_chunks(rowid, embedding) VALUES (?, ?)`, `rowid` = `chunks.id`), and
  found empirically that vector width must match `StorageEngine`'s
  configured `embedding_dim` or `sqlite-vec` raises a dimension-mismatch
  error — test fixtures now build `StorageEngine(..., embedding_dim=4)`
  to match their small test vectors. 3 + 2 tests.
- `src/llm_wiki/compiler/notes.py` (new package): `write_source_note(item,
  analysis, vault_root, storage, llm_client) -> Note`. ARCHITECTURE.md §7
  already reserved `compiler` for this; `compile()` didn't end up needing
  it (nothing package-worthy to factor out), but note-writing is genuinely
  reusable logic with a real second consumer (`cascade()`) from day one.
  Slug from `slugify(item.title)`, collision-checked against both the
  `notes` table and the filesystem (suffix `-2`, `-3`... — same pattern
  `stager.stage()` already uses), written via `python-frontmatter` +
  write-temp-then-`Path.replace()` (atomic rename), one chunk for the
  whole note (its body *is* the summary — chunking further would
  manufacture structure that isn't there), embedded via
  `llm_client.embed()`. Deliberately excludes entities/concepts from the
  note body — inventing a `[[wikilink]]` format before the entity/concept
  session exists felt premature. 6 tests.
- `src/llm_wiki/ingest/cascade.py` (new): `cascade(item, storage,
  llm_client, vault_root)` — step 5, `ANALYZED`→`CASCADING`→`COMPLETED`.
  Same two-phase atomicity pattern as `atomize()`/`compile()` (durable
  `CASCADING` marker first, crash-retry accepts `ANALYZED` or
  `CASCADING`). **New decision**: the terminal write is `COMPLETED`
  directly — no separate durable `CASCADED` resting state, since nothing
  in this source-notes-only cut ever needs to observe "note written, not
  yet marked complete" as its own state (unlike `PARSING`→`PARSED`, where
  real committed output actually differs). The `CASCADED` enum value
  stays defined for schema completeness / a future split. Fails cleanly
  (`FAILED`/`failed_at_step=CASCADING`) if no `queue_analysis` row exists
  (e.g. `compile()` never ran) or if `write_source_note()` raises
  `OSError`/`CompilationError`. 7 tests, including one confirming the
  `notes`/`chunks` inserts roll back together with an embed failure since
  the whole thing runs inside one `with storage.conn:` block.
- `pipeline.py`: `build_pipeline()` gained a `vault_root` parameter —
  `cascade()` needs it (to know where `wiki/` is) in addition to
  `llm_client`, so it's only bound into the dispatch table when *both*
  are given. `cli.py`'s `_dispatch_table()` now passes both.
- **Known gap, documented not solved**: the note file write and the DB
  transaction commit aren't atomic *together* (each is atomic on its
  own). A crash between them leaves a harmless orphaned `.md` file on
  disk — the slug-collision check makes a retry safe (creates a new file
  rather than corrupting the orphan), so the only cost is disk litter.
  Same category of gap as `stager`'s pre-`verify_and_clean()` problem;
  building the equivalent for `cascade()` was judged scope creep for a
  first cut. See `INGEST_PLAN.md` §11's closing section.
- `tests/test_cli_ingest.py`: wiring `cascade()` in for real surfaced 6
  failures — the shared `FakeLlmClient.embed()` was still a leftover
  `NotImplementedError` stub from before `cascade()` existed, and its
  fake vectors needed to be 768-wide to match the vault fixtures' real
  `DEFAULT_EMBEDDING_DIM` (not a `StorageEngine` monkeypatch — simpler to
  just make the fake vectors the right shape). The now-obsolete
  `with_fake_cascade_step` stand-in fixture was removed entirely (real
  `cascade()` replaces it); three tests' stale "`ANALYZED`"/"`No commit
  performed`" assertions updated to "`COMPLETED`"/"`Committed`" since the
  pipeline now genuinely completes end-to-end.
- Several `INGEST_PLAN.md` §7 gaps resolved as a direct consequence:
  cascade write atomicity (now built, with the one documented residual
  gap above), the pool re-offering `ANALYZED` items forever (now
  terminal), and `run`'s commit wiring being untestable end-to-end (now
  exercised for real, not via monkeypatch).

## Session: entity/concept note fan-out — step 4b, second half (the actual "cascade")

Picked up the piece deliberately sliced off last session. Asked two
clarifying questions before coding (both recommended options chosen —
see `INGEST_PLAN.md` §12): source notes stay wikilink-free this session
(entity/concept side carries all the new linking), and entity/concept
note embeddings are computed once at creation, never refreshed on later
mentions. **153/153 tests passing** (was 142; +11).

- **Mid-session correction, not part of the original plan**: after
  drafting §12 with the literal "minimal stub" reading from last
  session, the user flagged the obvious problem — `ExtractionResult`
  only ever held bare entity/concept name strings, so a first-mention
  note would have been genuinely empty (title + a "mentioned in" pointer,
  no actual information). Rather than build that, asked a third
  question: keep bare names, or make extraction richer? **User chose
  richer extraction** — `ExtractionResult`/`Analysis.entities`/`.concepts`
  became `list[Mention]` (`name` + a one-sentence `note` of what the
  text says about it), still produced by `compile()`'s single existing
  `extract()` call, no new LLM call. This changed `models.py`,
  `llm/client.py` (prompt + schema), `storage/analysis_repo.py`
  (JSON shape), and every test fixture touching entities/concepts —
  caught and fixed before it became a production bug, not after.
- `models.py`: new `Mention(name, note)`, shared by `Analysis` and
  `llm.client.ExtractionResult` (one shape, not two).
- `storage/notes_repo.py`: `update_note_row()` (new) — in-place
  `tags`/`sources`/`content_hash`/`updated_at` update; `path`/`slug`/
  `type`/`title` never change post-creation. 2 new tests.
- `src/llm_wiki/compiler/notes.py`: `fan_out_mentions(item, analysis,
  source_note, vault_root, storage, llm_client) -> list[Note]` (new).
  Matches an extracted name to an existing note by `slugify(name)`
  (case-insensitive merge for free — "Acme Corp" and "acme corp" land on
  the same note; genuine entity resolution across different phrasings
  like "Acme" vs. "Acme Corporation" is explicitly out of scope, §12).
  New mention: writes a `wiki/entities/`/`wiki/concepts/{slug}.md` stub
  (frontmatter + one "Mentioned in" bullet — date, `[[source-slug]]`
  wikilink, source title, the `Mention.note` text), one chunk, embedded
  once. Repeat mention: reads the existing file via `frontmatter.loads()`,
  appends one bullet to the body, updates the frontmatter `sources` list
  to match (DB and file never drift, per the "SQLite is a rebuildable
  cache of `wiki/`" invariant), rewrites atomically — no chunk/embedding
  touch (per the "embed once" decision). **Idempotent by construction**:
  if `item.title` is already in the note's `sources`, the append is
  skipped, so a `cascade()` crash-retry can't double-append a bullet.
  Cross-type slug collisions (rare — an entity and an unrelated concept
  happen to slugify the same) reuse §11's `-2`/`-3` suffixing rather than
  a new mechanism. `_unique_slug()`/`_note_path()` refactored to be
  type/folder-aware (`sources`/`entities`/`concepts`) so both
  `write_source_note()` and the new fan-out share them. 13 new tests
  (create, content/bullet shape, chunk+embedding-once, repeat-mention
  append without re-embed, crash-retry idempotency, within-item dedup,
  cross-type collision suffixing).
- `ingest/cascade.py`: now calls `write_source_note()` then
  `fan_out_mentions()` in the same `with storage.conn:` block as the
  terminal `COMPLETED` write — one atomic "either the whole cascade
  landed or none of it did" unit, same shape as before. 2 new tests
  (fans out correctly; crash-retry doesn't duplicate a mention).
- Manually verified the actual rendered output in the sandbox (not just
  assertions) — confirmed `acme-corp.md` correctly merges a second
  source's lowercase "acme corp" mention into the same note with both
  dated bullets present, and that `[[wikilink]]`s point at the right
  source slugs.
- `INGEST_PLAN.md` §7's pool-re-offering gap note simplified now that
  4b is fully done (no more "source notes only" caveat).

## Suggested next step

Per `INGEST_PLAN.md` §9, items 1 through 5 are now fully done — the
whole `STAGED`→`COMPLETED` pipeline runs end-to-end, including real
entity/concept notes. What's left:

- **Item 6, the watcher** (`stager.watch()`, wired to `auto_watch_raw`)
  — last on purpose, a convenience layer over the already-proven
  `stage()` + `add`/`run` path. The only remaining item in the original
  build order.
- `ingest retry`/`ingest watch` remain deferred per the design gaps noted
  in §7.
- **`graph`/`lint` packages** — not started. Now that real
  `[[wikilink]]`s exist in entity/concept notes (§12), `graph`'s
  `wiki-link` pipeline (parse `[[wikilinks]]`, populate the `links`
  table) has real content to work against for the first time.
- Two documented, deliberately-deferred v1 limitations worth revisiting
  eventually, not urgently: entity/concept note embeddings never refresh
  after first creation (§12 decision 2), and note file writes aren't
  atomic together with their DB commit (§11) — both call out "revisit if
  it proves to matter in practice."
- Real end-to-end manual smoke-testing of `compile()`/`cascade()` against
  an actual running `llama-server` still hasn't happened (no server
  reachable in this sandbox) — worth doing on the user's machine once
  convenient, to sanity-check the summarize/extract/embed calls actually
  produce sensible output against a real local model, not just against
  mocks.
