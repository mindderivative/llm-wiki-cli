# Graph + Lint Plan — `graph` + `lint`

Design doc for the two remaining ARCHITECTURE.md §7 packages that give the
vault its "self-maintaining" property: `graph` (the `/wiki-link` pipeline —
maintain the `links` table from `[[wikilink]]`s in `wiki/`) and `lint`
(the `/wiki-lint` pipeline — schema validation, broken-link detection,
isolated-note detection, health score). Same living-doc convention as
`INGEST_PLAN.md` — update as decisions land, don't relitigate silently.

Two questions asked before writing any code (`AskUserQuestion`, same
pattern as every `ingest`/`compiler` session):

1. **Should `graph rebuild`/`lint run` wire into `ingest run`'s
   batch-end flow, or stay separate manual commands?** **Decided:
   separate.** New standalone CLI commands, not auto-triggered by
   `ingest run`. Keeps this session scoped to the two new packages
   themselves — auto-wiring (and the real question it raises: does a
   lint failure block the git commit?) is an easy, lower-risk follow-up
   once both are proven standalone, same incremental pattern already
   used for `vcs.GitEngine`.
2. **Should `lint run` compute a numeric health score, or just report
   structured findings?** **Decided: compute a score.** `1 -
   (distinct_notes_with_findings / total_notes)`, expressed as a
   percentage. `0` notes in the vault scores `100%` (nothing to be
   unhealthy about) rather than dividing by zero.

## 1. Scope for this session

Building: wikilink extraction, incremental `links` table maintenance
(including discovering/reconciling hand-authored or hand-edited notes —
see §2, this turned out to be nearly free once incremental hashing is
implemented correctly), broken-link detection, frontmatter schema
validation, isolated-note detection, the health score, and CLI commands
for both.

Explicitly **not** this session:
- **Contradiction checks** — ARCHITECTURE.md §7 lists this as part of
  `LintEngine`'s eventual job. It requires an LLM call to detect
  semantic contradictions between notes; `lint` has no `LlmClient`
  dependency this session and none of its other checks need one. A
  clear, separate future increment.
- **Auto-wiring into `ingest run`** — decided above.
- **Rename detection.** If a note's file is renamed on disk (not
  edited, renamed), `graph rebuild` sees "old slug's file is gone" +
  "new slug's file is new" — i.e. a delete plus a create, losing the
  note's identity/history rather than tracking a rename. Same category
  of "not solved, documented" gap as other filesystem-reconciliation
  edges in this project (§11's note/DB atomicity gap, etc.).

## 2. `graph.rebuild_links()` — incrementality forces filesystem discovery

ARCHITECTURE.md §8 describes `/wiki-link` as: "for every note whose
`content_hash` differs from its last-seen value, re-extract
`[[wikilink]]`s, diff against the `links` table, apply the delta."

Read literally, this **requires walking `wiki/`, not just querying the
DB** — a `content_hash` comparison needs the file's *current* hash, and
a note that was hand-authored directly in `wiki/` (bypassing `ingest`/
`compiler` entirely) has no DB row to query in the first place. So
"incremental hash-based re-linking" and "discover hand-authored notes"
turn out to be **the same mechanism**, not two separate features to
choose between — this also happens to be exactly the "`StorageEngine.rebuild()`
doesn't repopulate from `wiki/` — that's `ingest`/`compiler`/`graph`'s
job once they exist" gap flagged all the way back in an early session
(`chat_history.md`). `graph rebuild --full` immediately after
`StorageEngine.rebuild()` is what finally closes it.

### Algorithm

Walk every `wiki/{sources,entities,concepts,synthesis}/*.md`. For each
file, `slug = path.stem` (the slug is recoverable from the filename
alone, regardless of whether the frontmatter inside is even valid — this
is what makes schema-violation handling below possible without losing
track of the file).

**Correctness fix, caught by manual end-to-end testing (not a unit
test) before this shipped**: the first draft skipped a note entirely
(no link extraction at all) whenever its `content_hash` already
matched. That's wrong — `compiler.write_source_note()`/
`fan_out_mentions()` set a note's `content_hash` at creation time
without ever touching `links` (that's `graph`'s job), so a freshly-
`cascade()`d note reached its first `graph rebuild` with a hash that
already "matched," silently skipping link extraction forever. Fixed:
**link extraction always runs, every file, every call** — cheap (one
regex pass over already-read text + a small `links` replace) at this
project's target scale. The hash comparison only gates the more
expensive `notes` row write:

- **Row write skipped (default, unchanged)**: if a `notes` row already
  exists for this slug and its stored `content_hash` matches the file's
  current hash, the `notes` table isn't touched — but `links` still
  gets recomputed from the file's actual current content either way.
- **Row write happens** (no existing row — new/hand-authored file — or
  hash changed — hand-edited, or `--full` forces it regardless): parse
  frontmatter via `python-frontmatter` and try to build a `Note`.
  - **Parses successfully**: insert (new slug) or update (existing
    slug) the `notes` row — `notes_repo.update_note_row()` broadened
    this session to update every mutable field (`type`, `title`,
    `tags`, `sources`, `content_hash`, `updated_at`), not just the
    `sources`/`content_hash` subset `compiler`'s append path needed —
    safe, since that path already passes through unchanged values for
    fields it doesn't intend to touch. Then extract `[[wikilink]]`
    targets from the body (regex, strip optional `|alias`/`#anchor`
    syntax defensively even though nothing writes it yet, `slugify()`
    each target so a hand-written `[[Acme Corp]]` resolves the same as
    the machine-written `[[acme-corp]]`), and replace that source
    slug's rows in `links` wholesale (delete-then-reinsert for that
    `source_slug` — simpler to reason about than diffing adds/removes
    separately, and cheap at this scale).
  - **Fails to parse** (missing/invalid `type`, malformed YAML, etc.):
    **skip** — not inserted into `notes`, no links extracted. Counted
    in the result stats as `unreadable`. Deliberately **not** where
    schema-violation findings get recorded — see §3 for why that's
    `lint`'s job, not `graph`'s, even though `graph` is the one that
    notices first.
- **Deletion detection** (every run, not just `--full`): for every
  `notes` row already in the DB, if `note.path` no longer exists on
  disk, delete the row (cascades to its `chunks` via the existing FK)
  and delete any `links` rows where its slug appears as `source_slug`
  *or* `target_slug` (no FK there, so this is explicit — `links` has no
  `notes` foreign key by design, since a link's target may legitimately
  not exist yet — that's exactly what a broken-link finding is for).

### New repo functions

- `storage/notes_repo.py`: `list_note_rows()` (every note, for deletion
  detection + `lint`'s isolated-note check), `delete_note_row()`.
  `update_note_row()`'s scope broadened (see above).
- `storage/links_repo.py` (new module): `replace_links_for_source(storage,
  source_slug, target_slugs)`, `delete_links_for_slug(storage, slug)`,
  `list_all_links(storage) -> list[Link]`.

### What `--full` actually changes

Given deletion-detection, new-file-discovery, and link extraction all
happen on every run regardless, the *only* thing `--full` changes is
whether the `notes` row itself (type/title/tags/sources) gets
force-rewritten even when its `content_hash` is unchanged. Useful if a
hand-edit only touched frontmatter in a way that doesn't affect the
overall file hash comparison logic, or after a schema change to what
fields `Note` captures.

## 3. `lint.run()`

Reads `notes`/`links` from the DB for two of its three checks (no
filesystem walk needed — ARCHITECTURE.md §8's framing holds for these),
plus one filesystem walk of its own for the third:

1. **`broken_link`**: for every `links` row, if `target_slug` doesn't
   exist as a `notes.slug`, one finding per broken row (`slug` =
   the *source*, so `wiki-cli lint run` output points at the note that
   needs fixing).
2. **`isolated_note`**: every `notes` row whose slug appears in neither
   `source_slug` nor `target_slug` of any `links` row.
3. **`schema_violation`**: **this is the one check that walks `wiki/`
   directly**, deliberately not delegated to `graph`. `graph`'s job
   (§2) is producing correct link data; `lint`'s stated job
   (ARCHITECTURE.md §7) explicitly includes "validate frontmatter
   against the Pydantic schema" — that's inherently a file-reading
   operation, not a DB query, no matter which package does it. Piggy-
   backing on `graph`'s walk and having it write into `lint_findings`
   was considered and rejected — it would blur which package owns the
   `lint_findings` table and couples the two packages' internals
   together for no real benefit, since `lint run` doing its own
   (cheap, vault-scale) walk is simple and keeps `graph`'s output
   (link data) and `lint`'s output (findings) cleanly separate.

Each `lint run` invocation gets a fresh `run_id` (`uuid4().hex[:12]`);
every finding from that run is inserted with it. Old runs' findings stay
in `lint_findings` as history (per ARCHITECTURE.md §6) — nothing prunes
them this session.

**Health score**: `1 - (distinct notes with >=1 finding / total notes)`,
as a percentage. `0` notes scores `100%`.

### New repo functions

- `storage/lint_repo.py` (new module): `insert_lint_finding_row(storage,
  finding) -> LintFinding`.

### New models

`models.py` gains two small non-table-backed result shapes, consistent
with this project's existing "Pydantic model over ad-hoc dict/tuple"
style: `LinkRebuildResult` (scanned/indexed/updated/deleted/unreadable
counts + links added/removed) and `LintReport` (run_id, findings,
score, total_notes).

## 4. CLI surface

- `wiki-cli graph rebuild [--full] [--vault PATH]` — prints
  `LinkRebuildResult`'s stats.
- `wiki-cli lint run [--vault PATH]` — prints the findings table +
  health score.

No `graph`/`lint` auto-invocation from `ingest run` this session (§0,
decision 1).
