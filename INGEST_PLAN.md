# Ingest Pipeline Plan — `stager` + `ingest`

Status: **proposed, not yet implemented**. Extends ARCHITECTURE.md §7
(component responsibilities) and §8 (`/wiki-ingest` data flow) with a
resumable, step-gated state machine and a new `stager` component. Read
alongside ARCHITECTURE.md — this doc doesn't repeat what's already
settled there (tech stack, filesystem layout except where amended below,
concurrency model).

## 1. Why split `stager` from `ingest`

Originally scoped as one pipeline (§7/§8). Splitting the file-handling
front end from the LLM-driven back end is a standard staging-area
pattern, for the same reasons it's used in data engineering generally:

- **Error prevention** — a corrupted or partial file gets caught and
  rejected at the filesystem-copy stage, before it can reach the LLM
  pipeline or touch `wiki/`. A bad `raw/` drop never gets the chance to
  produce a bad note.
- **Load isolation** — the expensive, slow part of ingest (LLM summary +
  extraction) never runs against a file that hasn't been validated as
  safely archived yet. Staging is cheap and fast; it shouldn't be coupled
  to the slow path failing or blocking it.
- **Standardization before processing** — `stager` is the one place that
  normalizes naming, archives the untouched original, and confirms the
  working copy is readable, so everything downstream (`ingest`,
  `compiler`) can assume a consistent, already-validated input shape
  instead of re-checking file hygiene at every step.

`stager` owns the filesystem side (watch, copy, archive). `ingest` owns
the DB/LLM side (parse, analyze, cascade). Both operate on the same
`queue` row, in sequence, but are separate packages with separate
responsibilities and separate failure modes.

## 2. Entry points

Both entry points are thin triggers. Neither does staging work itself —
they just hand a file path to `stager` and step 1 begins.

**A — file dropped into `raw/`, watcher enabled** (`auto_watch_raw: true`)
A `watchdog` `Observer` watches **only** the literal `raw/` directory,
**non-recursive**. It does not watch `raw/.sources/` or any other
subdirectory. Its only job is observe-and-report: on a file-create event
directly under `raw/`, it calls `stager.stage(path)`. No filtering,
naming, or validation logic lives in the watcher itself.

**B — `wiki-cli ingest add <path>`**
Same call, `stager.stage(path)`, triggered explicitly instead of by a
filesystem event. This is the primary supported path and should be built
and tested first; the watcher is a convenience layer on top of the same
function, added second.

Both entry points only stage. Neither runs the item through the pipeline
or commits anything — that's a separate, explicit action (§5/§6). A
staged file sits at `STAGED` until something processes it.

### Resolved: watcher self-trigger risk

**Decided.** The original layout had the stager write its working copy
to `raw/{date}_{slug}.ext` — directly inside the exact directory the
watcher observes, risking the watcher re-triggering on its own output.
Fix: staged working copies move out of the watched directory, mirroring
`.sources/`:

```
raw/
├── .sources/               # untouched original uploads (existing)
├── .staged/                # NEW — staged working copies
│   └── {date}_{slug}.ext
```

With the watcher non-recursive on `raw/` only, writes into either
`.sources/` or `.staged/` become invisible to it by construction — no
dedup logic needed. This amends ARCHITECTURE.md §5, applied there
directly (no longer just proposed here).

## 3. State machine

One `queue` row per file, from first contact through completion. Owner
changes partway through the lifecycle, but it's the same row throughout
— this is what makes "check status of the current file" a single lookup
regardless of which component last touched it.

| Status | Owner | Meaning |
|---|---|---|
| `STAGED` | `stager` | Original archived to `.sources/`, working copy written to `.staged/`. Terminal success for step 1. |
| `QUEUED` | `ingest` | `ingest` has accepted the staged item — verified the staged files exist and are readable — and formally enqueued it. |
| `PARSING` | `ingest` | `atomize()` in progress. |
| `PARSED` | `ingest` | Chunks committed to `chunks` table. Terminal success for step 3. |
| `ANALYZING` | `ingest` | `compile()` in progress — LLM summary + `outlines` extraction. |
| `ANALYZED` | `ingest` | Summary + extracted entities/concepts committed. Terminal success for step 4. |
| `CASCADING` | `ingest` | Cascade-update in progress — merging into/creating notes, regenerating embeddings. |
| `CASCADED` | `ingest` | Note writes + embeddings committed. Terminal success for step 5. |
| `COMPLETED` | `ingest` | Pipeline finished. |
| `FAILED` | either | Terminal for whichever step raised. `error` holds the human-readable message. `failed_at_step` (new field, see below) holds which step it was — structured, not string-parsed. |

`QueueStatus` (`models.py`) needs to grow from its current 6 values
(`QUEUED, PARSING, ANALYZING, CASCADE, COMPLETED, FAILED`) to the 10
above — `CASCADE` splits into `CASCADING`/`CASCADED`, and `STAGED`,
`PARSED`, `ANALYZED` are new. This is a required schema/model change
before implementation starts (see §7).

**Built in from the start:** `QueueItem` gets a `failed_at_step:
QueueStatus | None` field, set alongside `status=FAILED` + `error`,
cleared back to `None` by `retry`. This makes "show me everything that
failed during PARSING" a plain query (`WHERE failed_at_step = 'PARSING'`)
instead of string-matching `error` text. `error` still carries the
human-readable detail for that failure; `failed_at_step` carries the
structured "where."

### Atomicity contract

Every step's output data (chunks, notes, embeddings, links) commits in
the **same transaction** as its terminal status write. A step is never
left holding output that isn't reflected in its status, or a status that
isn't backed by its output.

The `-ING` statuses (`PARSING`, `ANALYZING`, `CASCADING`) are written
**before** the step's work starts, as a separate, earlier commit — purely
observability ("something is currently working on this"). They carry no
committed output. This means an ungraceful shutdown mid-step leaves the
row parked in an `-ING` status with nothing else changed.

**Recovery convention:** finding a row parked in an `-ING` status on
resume is expected after a crash or shutdown, not a corrupted state. It
means the step was interrupted before producing any committed output.
`step`/`run` treat this the same as if the step had never started — they
re-attempt it from the top.

### Failure contract

A step that raises writes `status=FAILED` + `error` and stops. It does
not attempt subsequent steps, does not retry automatically, and does not
leave the row in an ambiguous state. Nothing auto-retries a `FAILED` row
— that's an explicit, separate action (see `retry` in §5), so a failure
is never silently masked by a later successful-looking status.

## 4. Resumability / step-through UX

Two primitives, both operating on a single queue item at a time — the
engine **never processes more than one file concurrently**. Not a
scaling limitation to fix later: local LLM calls in `compile()` go
through one `llama-server`, so sequential is the correct behavior, not
just the simplest one.

- **`step(id)`** — execute exactly the next pending step for this item,
  then stop, regardless of outcome. Never commits (§5) — it's explicitly
  not meant to finish anything.
- **`advance(id)`** — execute steps repeatedly for this one item until it
  reaches `COMPLETED` or `FAILED`.

Both honor the failure contract above: `advance` stops immediately on
`FAILED` for that item, it does not skip ahead or retry.

**Both primitives generalize to a pool + count**, not just a single id —
this is the actual day-to-day interface (§5, §6):

- `step` applied to N items = a **single-step batch**: pull up to N items
  from the pool, advance each by exactly one step, then stop. E.g. stage
  100 files, then run a batch of `step`, `--count 10`, targeting items
  currently at `STAGED` (ready for step 2) — those 10 move to `QUEUED`
  and everything stops there. Later, a separate `step` batch, `--count
  5`, targeting items at `QUEUED` (ready for step 3) — those 5 move
  through `PARSING`/`PARSED`. Different counts, different cohorts,
  fully user-paced, one macro-step at a time. No commit happens from
  this mode — nothing reaches `COMPLETED` just by moving one step.
- `advance` applied to N items = a **run**, driving each of N items all
  the way to `COMPLETED`/`FAILED` before moving to the next (§5).
- `advance` applied to the **whole pool, unbounded** = `AUTO` (§5) — the
  only mode that needs no manual step-by-step or cohort-by-cohort
  gating; it just keeps calling `advance()` across the pool until
  nothing's left or it's stopped.

This satisfies the "walk away, shut down, come back, check status,
continue" requirement directly: state lives entirely in the `queue` row,
never in process memory, so resuming after a reboot is just invoking
`step` or a batch run again — the row's current status is the only
thing that determines what happens next.

## 5. Batch runs: user-controlled count, pool-driven

**Decided — staging and processing are fully decoupled.** `add` and the
watcher only stage (§2). Processing is always a separate, explicit
action: a **batch run**, sized by the user, pulling from the pool of
items that aren't yet finished.

**The pool:** every queue item not in a terminal state (`COMPLETED` or
`FAILED`), oldest `created_at` first, **optionally filtered to a
specific status** (`--status QUEUED`, etc.) — this is what makes "push
10 through step 2, then a different 5 through step 3" possible: without
a filter, pool selection just grabs the oldest pending items regardless
of which step they're sitting at next, which stops being precise once
the pool is a mix of items at different stages (§4).

**Sizing a run**, via `ingest run` (full-completion) or `ingest step`
(single-step batch, §4):

- `--count N` (any positive integer) — pull up to N items from the pool,
  call `advance()` on each **one at a time, in order**. Stops after N
  items are attempted, or when the pool runs dry, whichever comes first.
- `--count AUTO` (alias `--all`) — same, but drains the entire pool
  instead of stopping at a fixed N. Keeps going until nothing's left, or
  until stopped/paused (below).
- Explicit ids (`ingest run <id...>`) — bypasses the pool, runs exactly
  those items instead. Still one at a time, still batches into a single
  commit. Useful for resuming a specific known set, e.g. retried items.

These are mutually exclusive ways of defining the same underlying
primitive: **a batch = whatever one `run` invocation processes.** One
commit happens at the end of that invocation, regardless of which
selection mode picked the items.

**A `FAILED` item stops the run**, at whatever count it reached — same
failure contract as everywhere else (§3). `--count 25` does not mean
"keep trying until 25 succeed"; it means "attempt up to 25, stop
immediately if one fails, report which one and why." This keeps the
failure rule identical in every mode instead of `--count`/`AUTO` being a
special case that silently skips bad items.

**Stopping/pausing an `AUTO` run:** since it can run indefinitely, it
needs to be interruptible. On stop request (Ctrl-C/SIGINT in the
foreground CLI), finish the **current step** — not abandon it mid-write,
even though that's already safe by construction per the atomicity
contract (§3) — then stop before starting the next item, commit whatever
completed in the run so far, and report where it stopped. An abrupt
kill is also recoverable (same `-ING`-state convention as any crash,
§3) — the graceful path just gives a clean "stopped after N, commit
done" report instead of silence.

**`auto_watch_raw` vs. `--count AUTO` — two different "auto"s, don't
conflate them.** `auto_watch_raw` controls whether `stager` watches
`raw/` and auto-stages new drops. `--count AUTO` controls how many
already-staged items one `run` invocation processes before stopping. A
file can sit at `STAGED` indefinitely under `auto_watch_raw: true` if no
one ever runs a batch against it — staging never triggers processing.

**Partial-failure handling within a batch:** commit covers exactly the
items that reached `COMPLETED` before the run stopped (by count, by
`FAILED`, or by pause); items that didn't complete are excluded and, per
the atomicity contract (§3), should have left no file-level residue in
`raw/`/`wiki/` at all — so committing is a plain `git add -A` over both
directories, not a selective per-item staging operation. This depends on
cascade's note writes (`CASCADING` → `CASCADED`) being
write-temp-then-rename, same atomicity discipline as the DB writes —
calling this out explicitly since it's a real implementation requirement
this decision now depends on, not just a nice-to-have.

**Scope:** commit only, local, no push. `vcs.GitEngine.push()` stays a
separate, explicitly user-triggered action — not wired into ingest.

**Commit message (proposed, adjustable):** `"ingest: <title>"` for a
one-item run, `"ingest: N files (title1, title2, ...)"` for a multi-item
run.

This confirms `ingest` (or a thin layer coordinating `stager`/`ingest`/
`vcs` per batch) has a real dependency on `vcs.GitEngine`, resolving the
open question from the original walkthrough.

## 6. Proposed CLI surface

All under a `wiki-cli ingest` group (CLI grouping is user-facing and
doesn't need to mirror the `stager`/`ingest` package split internally):

| Command | Behavior |
|---|---|
| `ingest add <path...>` | Entry point B — stage each path only. No run, no commit. |
| `ingest list [--status STAGED\|FAILED\|...]` | List queue items, optionally filtered by status. Primary "what's the state of everything" view after a reboot. |
| `ingest status <id>` | Full detail for one item — status, error, `failed_at_step`, raw_path, archive_path, timestamps. |
| `ingest step <id>` | Run exactly the next pending step for one item. No commit. |
| `ingest step --count N [--status STATUS]` | Single-step batch (§4) — advance up to N pool items by one step each, then stop. No commit. |
| `ingest run --count N\|AUTO` | Pool-driven, full-completion batch run (§5), one commit at the end. `AUTO` drains the whole pool, no manual gating. |
| `ingest run <id...>` | Explicit-id, full-completion batch run (§5), one commit at the end. |
| `ingest retry <id>` | Reset a `FAILED` item to its last good status, clear `failed_at_step`/`error`. Does not itself run or commit — the item goes back into the pool for the next batch run, or can be targeted directly by id. Never automatic. |
| `ingest watch` | Foreground `stager` watcher process (or driven by `auto_watch_raw` config, TBD which). Stages only — does not run or commit (§5). |

## 7. Known gaps / open decisions

- **Non-text formats.** Deferred — confirmed as a later feature upgrade,
  not blocking initial `ingest`/`stager` build. Only plaintext/Markdown
  chunking is scoped for `atomize()` for now.
- **Cascade write atomicity.** Note writes during `CASCADING` need to be
  write-temp-then-rename (§5) — not yet verified against how `compiler`
  will actually write files, since `compiler` isn't built yet.
- **SIGINT handling mechanics.** §5 describes the intended pause
  behavior for `--count AUTO`; the actual signal-handling implementation
  (where the interrupt is checked, how "finish current step" is
  guaranteed) is a real implementation detail still to work out when
  `run` gets built, not just a config flag.
- **Does the watcher's drop-zone self-clean?** `stage()` (built this
  session) always copies from the source file, never moves/deletes it —
  even when the source is a file the watcher just observed sitting at
  the top level of `raw/`. That means, today, a watcher-triggered file
  stays sitting loose at top-level `raw/` forever after being staged,
  which will look like clutter/an unprocessed item even though it's
  already safely archived. Defaulted to the safe (non-destructive)
  behavior since deletion is a one-way door not explicitly decided in
  this doc — revisit when the watcher itself gets built.

## 8. Required changes before implementation starts

1. ~~`models.py`: expand `QueueStatus` to the 10-value enum~~ — **done**.
   `QueueItem.failed_at_step: QueueStatus | None` added; `status` default
   changed `QUEUED` → `STAGED` (the actual earliest state a row is
   created in now).
2. ~~`storage/schema.py`: add `failed_at_step TEXT` column~~ — **done**.
   `CREATE_QUEUE`'s `status` default also updated to `'STAGED'`.
   `SCHEMA_VERSION` bumped 1 → 2 to mark the shape change.
3. ~~`ARCHITECTURE.md` §3/§7: add `stager` as its own package~~ — **done**.
4. ~~`ARCHITECTURE.md` §5: apply the `.staged/` layout change~~ — **done**,
   and `vault/manager.py`'s `REQUIRED_DIRS` now seeds `raw/.staged/` on
   `vault create` (existing test `test_create_builds_full_tree` covers it
   generically, no test changes needed).
5. ~~`src/llm_wiki/stager/` — new package~~ — **done** (`stage()` only —
   see §9 build order item 1, this is step 1 of the pipeline, nothing
   past `STAGED`/`FAILED`).

## 9. Suggested build order

1. ~~`stager.stage()` — filesystem copy/archive + `STAGED`/`FAILED`~~ —
   **done**, `src/llm_wiki/stager/stager.py`, 4 tests, all passing.
   Always copies from the source, never moves/deletes it — see the new
   open decision in §7 (does the watcher's drop-zone self-clean?).
2. `ingest` steps 2–3 (`QUEUED` → `PARSING` → `PARSED`) — atomize
   plaintext/Markdown only. Still no LLM dependency.
3. `wiki-cli ingest add/list/status/step/run`, including the pool +
   `--count`/`--status` batch selection (§4, §5), against the above (no
   commit yet) — proves the state machine and resumability contract
   end-to-end before the LLM steps or `vcs` exist.
4. `ingest` steps 4–5 (`ANALYZING`/`CASCADING`) — depends on `llm`
   (not started). Mocked LLM client for tests per ARCHITECTURE.md §11.
   Cascade note writes built atomic (write-temp-then-rename) from the
   start, per §5's dependency on that.
5. `vcs.GitEngine` minimal commit support, wired into `add`/`run`'s
   batch-end commit (§5).
6. Watcher (`stager.watch()`), wired to `auto_watch_raw` — last, since
   it's a convenience layer over an already-proven `stage()` +
   `add`/`run` path.
