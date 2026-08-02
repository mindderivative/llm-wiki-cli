# LLM-Wiki — Architecture
 
## 1. Vision
 
A local-first, self-maintaining knowledge base manager. The user drops raw
source documents in; the system atomizes, summarizes, links, and lints them
into a GEO-optimized Markdown wiki, powered entirely by a local `llama.cpp`
cluster. The Markdown vault is the permanent artifact — everything else
(database, indexes, UI) is a replaceable lens on top of it.
 
## 2. Design Principles (locked decisions)
 
These came out of explicit tradeoff discussions and should not be silently
relitigated mid-build:
 
1. **Markdown + Git is the source of truth.** Every derived store (SQLite,
   vector index) must be fully rebuildable from the `wiki/` directory tree.
   Losing the database must never lose information — only rebuild time.
2. **Cross-platform targeted.** TBD desktop UI.
3. **Local-first inference only.** All LLM calls go to the user's own
   `llama-server` cluster over its OpenAI-compatible endpoint. No cloud
   provider SDKs enter the dependency tree.
4. **Small footprint over framework convenience.** Prefer thin, single
   purpose libraries (`sqlite-vec`, `pygit2`, `outlines`) over heavier
   all-in-one frameworks (Pixeltable, LangChain) when the project's actual
   scale (a personal/small-team wiki, not a billion-vector production
   system) doesn't need the heavier tool's guarantees.
5. **Backend before frontend.** The `llm-wiki` engine package has zero GUI
   dependency and is fully usable/testable from a CLI before any GUI code
   exists. The GUI is the *last* thing built, and it is a thin consumer of
   a stable backend API — not the other way around, which is what caused
   rework in the previous attempt.
6. **Everything the backend does is scriptable and testable headlessly.**
   If a feature can't be exercised by a `pytest` test or a CLI command
   without opening a window, it isn't done.

## 3. Layered Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                          INTERFACES                             │
│  ┌───────────┐    ┌───────────┐     ┌─────────────────────────┐ │
│  │  CLI      │    │  MCP      │     │         GUI             │ │
│  │ (Typer)   │    │ (FastMCP) │     │        (TBD)            │ │
│  └─────┬─────┘    └─────┬─────┘     └────────────┬────────────┘ │
└────────┼────────────────┼────────────────────────┼──────────────┘
         │                │                        │
         └────────────────┴────────────┬───────────┘
                                       │  all interfaces call the
                                       │  same engine API — no
                                       │  interface-specific logic
                                       ▼
┌────────────────────────────────────────────────────────────────────┐
│                      llm_wiki ENGINE (pure Python)                 │
│                                                                    │
│  vault/       VaultManager — create/load/validate vault trees      │
│  stager/      Stager — watch raw/, archive + stage incoming files  │
│               (see INGEST_PLAN.md — split out of ingest/)          │
│  ingest/      IngestEngine, Atomizer — queue + chunk raw files     │
│  llm/         LlamaClient, structured extraction (outlines)        │
│               EmbeddingService                                     │
│  compiler/    CompilerEngine — summarize, extract, cascade-update  │
│  graph/       LinkEngine — NetworkX graph over the vault           │
│  lint/        LintEngine — schema + link + contradiction checks    │
│  vcs/         GitEngine (pygit2) — init/stage/commit/push/pull     │
│  storage/     SQLite + sqlite-vec cache layer (rebuildable)        │
│  config.py    Pydantic-settings typed configuration                │
│  models.py    Pydantic domain models shared by every layer         │
└────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────┐
│                         PERSISTENCE                             │
│  wiki/              canonical Markdown notes  (Git-tracked)     │
│  raw/               immutable source archive  (Git-tracked)     │
│  .llm-wiki/db.sqlite3   derived cache: queue, chunk index,      │
│                         embeddings (sqlite-vec), link cache     │
│                         — safe to delete, rebuilds from wiki/   │
└─────────────────────────────────────────────────────────────────┘
```
 
## 4. Technology Stack
 
| Concern | Tool | Rationale |
|---|---|---|
| Language / packaging | Python 3.14, `uv` | Fast resolver, lockfile reproducibility |
| Domain models / validation | `pydantic` v2 | Single source of truth for shapes shared across CLI/MCP/GUI |
| Config | `pydantic-settings` | Typed, validated, env-var aware; replaces hand-rolled equality-check JSON manager |
| Local relational + vector store | `sqlite3` (stdlib) + `sqlite-vec` extension | Zero-server, single-file, minimal footprint; brute-force KNN is fine at wiki scale |
| LLM transport | `openai` Python SDK against `llama-server`'s OpenAI-compatible endpoint | Already the right tool; no change needed |
| Structured extraction | `outlines` | Grammar-constrained decoding against a model you control — guaranteed-valid JSON, no retry loop |
| Markdown parsing | `markdown-it-py` | Already correct in the original build |
| Frontmatter | `python-frontmatter` | Already correct |
| Graph structure | `networkx` | Already correct |
| Graph layout | `networkx.spring_layout` / `kamada_kawai_layout` | Replaces the hand-rolled (and buggy) force simulation |
| Version control | `pygit2` (libgit2 bindings) | In-process, thread-safe, no subprocess spawn per Git op |
| File watching | `watchdog` | Powers the `auto_watch_raw` config option (previously dead code) |
| MCP server | `fastmcp` | Already the right tool; previously unimplemented |
| CLI | `typer` | Thin, typed CLI as the first real interface onto the engine |
| Logging | `loguru` | Already correct |
| Desktop UI (TBD) | (dashboard, terminal, log view, text editing) | Cross-platform capabile |
| Testing | `pytest`, `hypothesis` (optional, for chunker edge cases) | Backend must be 100% testable without GUI |
 
## 5. Vault Filesystem Layout (unchanged, canonical)
 
```
<vault_root>/
├── .llm-wiki-config              # vault identity JSON
├── .llm-wiki/
│   └── db.sqlite3                # derived cache — rebuildable, gitignored
├── raw/
│   ├── .sources/                 # untouched original uploads
│   └── .staged/                  # staged working copies (see INGEST_PLAN.md §2)
│       └── {date}_{slug}.ext
├── wiki/
│   ├── index.md
│   ├── log.md
│   ├── sources/                  # per-source summaries
│   ├── entities/                 # profiles of core subjects
│   ├── concepts/                 # foundational principles
│   ├── synthesis/                # cross-cutting insights
│   └── .system/
│       └── prompts/              # versioned prompt templates (new — see §8)
└── SCHEMA.md                     # human-readable operational rules
```
 
`raw/` and `wiki/` are Git-tracked. `.llm-wiki/` is gitignored — it is a
cache, not state.
 
## 6. Internal Storage Schema (SQLite + sqlite-vec)
 
All tables are derived from `wiki/` and `raw/` and can be dropped and
rebuilt at any time via `llm_wiki storage rebuild`.
 
- **`queue`** — ingestion queue items (`id`, `title`, `raw_path`,
  `archive_path`, `status`, `error`, `created_at`, `updated_at`). Replaces
  the old `queue.json` — gives us transactional updates instead of full-file
  rewrites on every status change.
- **`notes`** — one row per Markdown note (`path`, `slug`, `type`, `title`,
  `tags`, `sources`, `content_hash`, `created_at`, `updated_at`). `content_hash` is the
  key to incremental link/lint passes — a note is only re-parsed if its
  hash changed since the last run.
- **`chunks`** — atomic GEO chunks (`note_id` or `queue_item_id`, `ordinal`,
  `title`, `content`, `word_count`).
- **`vec_chunks`** — `sqlite-vec` virtual table, one embedding per row in
  `chunks`, joined by rowid.
- **`links`** — parsed `[[wikilink]]` edges (`source_slug`, `target_slug`),
  rebuilt only for notes whose `content_hash` changed.
- **`lint_findings`** — latest lint run's broken links, schema violations,
  isolated notes, with a `run_id` and timestamp for history.

## 7. Component Responsibilities
 
| Package | Responsibility | Notably fixes vs. previous build |
|---|---|---|
| `vault` | Create/validate/load vault directory trees; recent-vaults list | No behavior change, just decoupled from GUI |
| `storage` | Own the SQLite connection, schema migrations, `sqlite-vec` loading | New — didn't exist before |
| `stager` | Watch `raw/` (non-recursive), archive originals to `raw/.sources/`, write staged copies to `raw/.staged/`; own the `STAGED`/`FAILED` state of a queue row | New — split out of `ingest` so filesystem hygiene never depends on the LLM pipeline being healthy (see `INGEST_PLAN.md`) |
| `ingest` | Accept staged items, atomize into GEO chunks, drive the queue state machine from `QUEUED` through `COMPLETED`; commit batch changes via `vcs` once a run finishes | Queue is transactional DB rows, not a JSON file rewritten wholesale each time; full state machine and batch-commit semantics in `INGEST_PLAN.md` |
| `llm` | Wrap `llama-server`'s OpenAI-compatible endpoint; structured extraction via `outlines`; embedding generation | Replaces regex-JSON-scraping entity extraction with grammar-constrained, guaranteed-valid output |
| `compiler` | Orchestrate: summarize → extract entities/concepts → cascade-update existing notes → generate embeddings | Cascade updates now actually *merge* into existing notes instead of only creating stubs for missing ones |
| `graph` | Build/maintain the NetworkX link graph incrementally from `notes`/`links` tables | Incremental via `content_hash`, not a full vault re-walk every run |
| `lint` | Schema validation, broken-link detection, health scoring | Same logic, now reads from the DB cache instead of re-parsing every file |
| `vcs` | Init/stage/commit/push/pull/status via `pygit2` | Fixes the stdout-draining bug from the `QProcess` implementation by construction — no subprocess streams to manage |
| `config` | Typed settings, loaded from `.llm-wiki-config` + environment | Fixes the manual equality-check save-loop guard with `pydantic-settings`' native change detection |
 
## 8. Data Flow: The Three Pipelines
 
**`/wiki-ingest`**
Superseded by the detailed, resumable state machine in `INGEST_PLAN.md`
— `stager` (`STAGED`/`FAILED`) hands off to `ingest`
(`QUEUED` → `PARSING`/`PARSED` → `ANALYZING`/`ANALYZED` →
`CASCADING`/`CASCADED` → `COMPLETED`, or `FAILED` + `failed_at_step` at
any point), with a batch-end commit via `vcs`. Every status transition is
a single DB row update, not a file rewrite. See `INGEST_PLAN.md` for the
full design, including why staging is a separate component and the
step-through/resume UX.
 
**`/wiki-link`**
For every note whose `content_hash` differs from its last-seen value:
re-extract `[[wikilink]]`s, diff against the `links` table, apply the
delta. Full graph is only ever fully rebuilt on explicit request
(`llm_wiki graph rebuild --full`).
 
**`/wiki-lint`**
Reads `notes` + `links` from the DB (already current thanks to the link
pipeline's incremental hashing), validates frontmatter against the Pydantic
schema, computes health score. No filesystem walk needed unless the DB is
being rebuilt from scratch.
 
## 9. Concurrency Model
 
The engine package uses **no GUI classes at all** — no GUI Thread, no
GUI elements, no signals. Long-running work (LLM calls, embedding batches) is
exposed as plain Python generators/callables using `concurrent.futures` or
`asyncio` where actual parallelism helps (e.g. embedding a batch of chunks
concurrently).
 
Each interface layer adapts this to its own concurrency idiom:
- **CLI**: runs synchronously, prints progress to stdout.
- **MCP**: `FastMCP` tools call the engine directly; async where the engine
  exposes async methods.
- **GUI (TBD)**: a thin GUI Thread wrapper subscribes to the engine's
  plain-Python progress callbacks and re-emits them as GUI signals. This
  adapter is the *only* place GUI threading concepts exist in the whole
  codebase.

## 10. Error Handling & Logging
 
- `loguru` remains the logging backbone. Logs to stderr
  and a rotating file inside `.llm-wiki/`.
- Engine functions raise typed exceptions (`VaultNotFoundError`,
  `IngestionError`, `CompilationError`, etc., defined in `models.py`) rather
  than returning `None`/`False` on failure, so every interface layer can
  handle failures explicitly instead of guessing from a falsy return.

## 11. Testing Strategy
 
- Every `llm_wiki` package gets a corresponding `tests/test_*.py` using
  `pytest`, with a fixture vault built in a `tmp_path` for each test —
  no shared mutable test state.
- LLM-dependent tests (`compiler`, `llm`) run against a **mocked** llama
  client by default (so the suite runs offline/in CI); a small marked
  subset (`@pytest.mark.live_llm`) hits the real local `llama-server` for
  manual verification.
- GUI testing is TBD, scoped to the thin GUI adapter
  layer — the engine's own tests never import GUI libraries.
## 12. Out of Scope (for now)
 
- Cloud LLM providers.
- Multi-user/concurrent-writer vault access.
- Mobile/web clients (MCP is the integration surface for other tools like
  Claude Desktop/Cursor instead).
- Real-time collaborative editing.
These aren't rejected forever — just explicitly deferred so they don't
creep into early phases.
 
