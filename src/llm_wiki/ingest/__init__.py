"""ingest — stage raw files, archive originals, enqueue; atomize into GEO chunks.

`IngestEngine` and `Atomizer` live here. See ARCHITECTURE.md §8 (`/wiki-ingest`
pipeline). Queue state is transactional DB rows (`queue` table, §6), not a
JSON file rewritten wholesale each time. Not yet implemented.
"""
