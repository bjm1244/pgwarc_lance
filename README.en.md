# pgwarc_lance

PostgreSQL extension (built with [pgrx](https://github.com/pgcentralfoundation/pgrx)) that adds table-backed BM25 search, Lance-based vector search, and RRF hybrid search for WARC / web-archive records — all callable from SQL. Korean documentation is in [README.md](README.md).

> **Status: research prototype — not production-ready.** The current source development candidate is **0.1.22**. It records URI-scoped document revisions in the committed vector outbox and Lance rows, and provides a read-only PG–Lance doctor. PostgreSQL and Lance still have no distributed atomic commit, and embedding-model identity is not included in the content revision. The Linux x86_64 / PostgreSQL 16 artifact, importer/reconciliation checks, upgrade, backup/restore, dependency notices and SBOM have been validated; see [PUBLICATION.md](PUBLICATION.md) and [THIRD_PARTY.md](THIRD_PARTY.md) for scope and remaining limits. Binary redistribution and production approval remain open. Independent relevance evaluation, operational SLA and full raw-WARC ingestion also remain open. Read [SECURITY.md](SECURITY.md) for security and transaction boundaries.

Production acceptance gates are recorded in [PRODUCTION.md](PRODUCTION.md).
Earlier 0.1.18 posting-window and IDF trials are recorded separately in the development log. The 0.1.19 term-frequency-index candidate reduces work in the q6 document-frequency scan, while its end-to-end latency and write costs remain inconclusive. Result fingerprints matched, but no independent qrels were available. See the [development log](docs/development-log.md), [PUBLICATION.md](PUBLICATION.md), [PRODUCTION.md](PRODUCTION.md), and [THIRD_PARTY.md](THIRD_PARTY.md) for scope and limitations.

The 0.1.19 candidate adds a narrow BM25 term index through a 0.1.18-to-0.1.19 migration. The supported script path continues from 0.1.11 through 0.1.14, 0.1.15, 0.1.16, 0.1.17, 0.1.18 and 0.1.19; the 0.1.11 baseline has migrations from 0.1.0 through 0.1.11, backup tests, a disk-backed importer CLI and idempotent `--lance-mode upsert` for an existing dataset. Use `--temp-dir`, `--batch-size`, `--batch-max-bytes`, and `--embedding-timeout` to set ingestion resource limits; see the production guide for their precise limits and recovery procedure.

License: Apache-2.0 ([LICENSE](LICENSE), [NOTICE](NOTICE)).

Source publication review: [PUBLICATION.md](PUBLICATION.md).

The 0.1.11 baseline retains both the BM25 posting covering index and
the narrow `bm25_doc(doc_len)` statistics index, and adds `doc_len` to the BM25
document primary key's INCLUDE columns. A PG16.15 locked package, fresh install,
SQL smoke, four regressions and a populated 0.1.10-to-0.1.11 Common Crawl clone
upgrade passed. The upgrade took 1.23 seconds and retained 21,873 documents,
8,240,889 postings and 21,873 WARC rows with a validated foreign key. Alternating
baseline and covering-key schemas across three warm 8-client runs each gave p95
medians of 170.92→133.13 ms for BM25 and 335.63→319.22 ms for hybrid. Backend CPU
fell from 10.33→9.27 and 47.30→46.09 ms/request. Result hashes matched. The stats
scan remained at 30 shared hits; the multi-term document scan changed from a
1,598-hit sequential scan to an 86-hit index-only scan, and rare-term lookups
fell from 17,289 to 11,527 shared hits with zero heap fetches. The host was under
substantial shared load; raw WARC was not reimported and cold I/O was not
measured. These are not SLA results. The package-level notice gaps described in this
historical baseline were resolved for the 0.1.20 PG16 package candidate; binary
redistribution and production approval remain open. See [the development
log](docs/development-log.md), [SECURITY.md](SECURITY.md) and [THIRD_PARTY.md](THIRD_PARTY.md).

## Contents

| Path | Purpose |
|------|---------|
| `src/lib.rs` | SQL entry points, extension SQL schema, GUCs |
| `src/tokenizer.rs` | CJK bigram + ASCII tokenizer |
| `src/bm25.rs` | BM25 index and scoring over PostgreSQL tables |
| `src/lance_store.rs` | Lance dataset create / append / scan / vector search |
| `src/hybrid.rs` | Reciprocal-rank fusion of BM25 and vector hits |
| `tools/` | Python harnesses ( benchmarks, quality evaluation, packaging ) |
| `tests/` | Python unit tests, `pg_regress` cases, SQL smoke tests |

## Requirements

PostgreSQL 13–17 server headers are required, and the build needs `cargo-pgrx` 0.19.1. Everything is wired into Docker, which is the only supported build path:

```bash
export PGWARC_DEV_PASSWORD="$(openssl rand -base64 24)"
PG_MAJOR=16 docker compose build     # compile and install the extension into the image
make up                              # start PostgreSQL on localhost:55432
make psql                            # connect as pgwarc_lance
```

```sql
CREATE EXTENSION pgwarc_lance;
```

Fresh installation revokes `PUBLIC EXECUTE` on every extension function.
Administrators must grant individual functions and required table permissions to
trusted roles. Compose requires a password and binds only to `127.0.0.1` by default.

## SQL API

| Function | Description |
|----------|-------------|
| `tokenize_korean(text)` | Tokenizer used by the BM25 index (CJK bigrams, ASCII words) |
| `bm25_index_document(doc_id, content)` | Index a document into `pgwarc_lance.bm25_*` |
| `bm25_search(query, k => 10)` | BM25 ranked hits `(doc_id, score)` |
| `lance_create_table(uri, vector_dim => 3, overwrite => false)` | Create a Lance dataset |
| `lance_insert(uri, id, vector, label)` | Append one vector row |
| `lance_insert_many(uri, ids, flat_vectors, vector_dim, labels)` | Append a batch in one write |
| `lance_count(uri)` / `lance_vector_dim(uri)` | Dataset metadata |
| `lance_vector_search(uri, query, k => 10)` | Brute-force nearest-neighbour search |
| `lance_scan(uri, limit => 100)` | Scan rows |
| `lance_scan_document_state(uri, limit => 100)` | Read document IDs and persisted revisions for reconciliation |
| `lance_outbox_enqueue_revision(uri, ids, flat_vectors, vector_dim, labels, revisions)` | Commit revisioned vectors and URI/document expectations in the caller's transaction |
| `lance_outbox_process_next()` | Apply the oldest eligible committed outbox event |
| `lance_dataset_versions(uri)` | Dataset versions `(version, created_ms)` |
| `lance_restore_version(uri, version)` | Promote a past version back to the tip |
| `hybrid_search(query, vector_query, lance_uri, k => 10)` | RRF fusion of BM25 and vector search |
| `hybrid_warc_search(...)` | Hybrid hits joined with `pgwarc_lance.warc_record` metadata |

Revision-aware outbox imports require extension 0.1.22 or newer. The revision
combines document ID, target URI, content type, HTTP status, extracted text
length, WARC payload digest and BM25 content checksum. Existing Lance rows gain
a nullable revision column on their first upsert. `tools/doctor_pg_lance.py`
reports duplicate IDs, missing or stale vectors, orphan rows, metadata label
mismatches and unfinished outbox work without modifying either store. It cannot
claim a common atomic snapshot across PostgreSQL and Lance. Treat its output as
a dry-run diagnosis: the doctor has no repair flag. After reviewing a confirmed
source change, rerun the importer with the same trusted embedding input in
`outbox` mode. If a Lance write succeeded but its PostgreSQL acknowledgment
rolled back, inspect the pending event and explicitly replay it with
`python3 tools/lance_outbox_worker.py --once --psql "$WORKER_PSQL"`, then run
the doctor again. No vector deletion is performed by this workflow.

### Example

```sql
BEGIN;
SELECT bm25_index_document(1, 'PostgreSQL 검색 엔진');
SELECT bm25_index_document(2, 'Lance vector search');
SELECT * FROM bm25_search('검색', 10);
COMMIT;

SELECT lance_create_table('/tmp/smoke.lance', 4, true);
SELECT lance_insert_many(
    '/tmp/smoke.lance',
    ARRAY[1, 2]::bigint[],
    ARRAY[0,0,0,0, 10,0,0,0]::float4[],
    4,
    ARRAY['doc1', 'doc2']::text[]
);
SELECT * FROM lance_vector_search('/tmp/smoke.lance', ARRAY[0,0,0,0]::float4[], 2);
```

## Operational settings

| GUC | Default | Meaning |
|-----|---------|---------|
| `pgwarc_lance.allowed_uri_prefix` | `NULL` | Existing local dataset directory. When set, paths are resolved inside it; sibling prefixes, `..`, escaping/dangling symlinks, relative/remote/encoded URIs are rejected. Empty permits unrestricted URIs for explicitly trusted roles. |
| `pgwarc_lance.max_scan_rows` | `0` (unlimited) | Administrator-only cap on rows `lance_scan` may buffer per call; excess requests fail with `program_limit_exceeded`. |
| `pgwarc_lance.allow_destructive_ops` | `on` | When `off`, `lance_create_table(overwrite => true)` and `lance_restore_version()` are rejected. |

The following settings require a superuser. Create the local dataset directory
with trusted OS ownership first. Only trusted OS users may modify it or its parents;
path validation cannot prevent filesystem changes between validation and I/O.

```sql
SET pgwarc_lance.allowed_uri_prefix = '/var/lib/pgwarc_lance/datasets';
SET pgwarc_lance.max_scan_rows = 10000;
SET pgwarc_lance.allow_destructive_ops = off;
```

## Transactionality and concurrency limits

This is the most important thing to understand before adopting the extension:

- **A Lance write cannot be rolled back.** `ROLLBACK` undoes the SQL side (`bm25_doc`, `bm25_term`, `lance_write_log`) but leaves the already-appended Lance version on disk. Design reconciliation into your application.
- **Recovery path:** use `pgwarc_lance.lance_write_log` (committed writes) together with `lance_dataset_versions()` to pick a target version, then `lance_restore_version()` to promote it.
- **Writers are serialized** per dataset URI (resolved local path when a directory restriction is set) with `pg_advisory_xact_lock`, released at COMMIT or ROLLBACK, so cooperating calls using that same URI serialize. External writers and URI aliases without a directory restriction are not coordinated by this lock.
- Long-running scans are interruptible: Lance futures are driven on a runtime that polls for cancel requests roughly every 50 ms, so `pg_cancel_backend()` and `statement_timeout` actually stop work. The runtime uses a single worker thread (not one per core) to keep the PostgreSQL backend footprint small.
- Errors are reported through `ereport!` with SQLSTATEs (`invalid_parameter_value`, `insufficient_privilege`, `program_limit_exceeded`, `internal_error`) rather than Rust panics.

## Testing

```bash
make test          # tests/test.sql against the live container
make test-unit     # Rust tokenizer unit harness
make test-uri-policy # filesystem containment unit tests
make test-regress  # pg_regress suite (PostgreSQL 16 only, see Makefile)
make test-warc     # Python harness unit tests
make check-docs    # documentation/harness consistency gate
make verify        # all of the above plus quality evaluation
```

`.github/workflows/ci.yml` builds every supported PostgreSQL major and runs the tests above.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Known constraints and how to report vulnerabilities privately are documented in [SECURITY.md](SECURITY.md).

The public quality regression uses 5,183 SciFact abstracts, real 384-dimensional
MiniLM embeddings and 50 independently labelled queries: hit@10 0.96 and MRR@10
0.7877. All 50 vector rankings passed an independent exact L2 reference check.
This is not the operational WARC corpus; see [PRODUCTION.md](PRODUCTION.md) for
reproduction, migration and remaining acceptance gates.

See [OPERATIONS.md](OPERATIONS.md) for tested reader/writer grants, DDL-free ingestion, monitoring and incident response.

## Document extraction failures

PDF and DOCX extraction is opt-in: use `--pdftotext /path/to/pdftotext` and
`--extract-docx`. The default `--document-error-policy fail` stops before SQL on
extraction failure. Explicit `--document-error-policy metadata-only` retains a
failed document with empty text and reports it in `extraction_retention`; it
receives no vector, and embedding commands only see text-bearing records.
Diagnostics retain at most 16 error messages of 512 characters each and count
additional omitted errors. This policy does not suppress missing extractors, HTTP decoding/size or
charset errors. PDF signatures are recognized after HTTP decoding. Empty text is not
OCR or evidence of successful content recovery.
