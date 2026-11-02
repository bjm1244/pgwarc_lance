# SQL and CLI contract

This is the public contract for the 0.1.26 development candidate. The source is
still a research prototype; passing these checks does not approve a 1.0 release
or production use.

## SQL inputs and errors

The SQL API uses PostgreSQL `real[]` for vectors. A dataset dimension must be
positive. Single-vector writes and searches must match that dimension; batch
inputs are row-major flat arrays with exactly `row_count * vector_dim` values.
NaN and either infinity are rejected. Synchronous write/search dimension
mismatches, invalid batch lengths, non-finite values, negative search limits,
and invalid ranking settings raise SQLSTATE `22023`
(`invalid_parameter_value`). A required NULL passed to
`lance_insert(uri, id, vector, label)` raises `22004`; `label` is intentionally
nullable and stores SQL NULL. Other pgrx functions whose inputs are non-NULL
Rust types use PostgreSQL STRICT semantics: a NULL scalar call returns NULL and
a NULL set-returning call returns no rows without invoking the function.
`double_or_none(NULL)` intentionally returns NULL.

Outbox enqueue validates a positive dimension, row shape, and finite values in
the caller's transaction. It does not open the external dataset there; a
dataset-dimension mismatch is reported by the worker as a failed, inspectable
outbox event. Clients must check outbox status before treating vector
materialization as complete.

Search `k` defaults to 10; zero returns no rows, negative values raise `22023`,
and values above 10,000 raise `54000` (`program_limit_exceeded`). Hybrid
`candidate_k` uses the same bounds. Hybrid weights must be finite and in
`[0,1000]`, with at least one nonzero; `rrf_k` must be finite and in
`[1,10000]`. Search text is limited to 2,500,000 UTF-8 bytes. Filtered search
rejects more than 10,000 eligible document IDs instead of truncating them.

For these validation failures, PostgreSQL reports `22023` (`invalid_parameter_value`);
the documented size caps report `54000`. A required NULL to `lance_insert`
reports `22004` (`null_value_not_allowed`). A disallowed URI or a destructive
operation with `pgwarc_lance.allow_destructive_ops=off` reports `42501`
(`insufficient_privilege`). Direct writes and table recreation are rejected
with `55000` (`object_not_in_prerequisite_state`) while that URI has unfinished
outbox work. Storage, Lance, and other unexpected operation failures report
`XX000`; inspect the operation's log and state before retrying an external write.

The primary function surface is:

| Function | Contract |
|---|---|
| `bm25_index_document(doc_id, content)` | Inserts or replaces a document and its postings. |
| `bm25_doc_count()` / `bm25_avg_doc_len()` | Return BM25 corpus document count / mean token length. |
| `bm25_search(query, k DEFAULT 10)` | Returns `(doc_id, score)`; `k=0` is empty. |
| `lance_create_table(uri, vector_dim DEFAULT 3, overwrite DEFAULT false)` | Creates a dataset; overwrite is destructive and can be disabled with `pgwarc_lance.allow_destructive_ops=off`. |
| `lance_insert(uri, id, vector, label)` | Appends one row; label may be NULL; required NULL arguments raise `22004`. |
| `lance_insert_many(uri, ids, flat_vectors, vector_dim, labels)` | Appends row-major vectors; IDs and labels must align. |
| `lance_upsert_many(uri, ids, flat_vectors, vector_dim, labels)` | Idempotently merges unique document IDs. |
| `lance_count(uri)` / `lance_vector_dim(uri)` | Return stored row count / dataset vector dimension. |
| `lance_scan(uri, limit DEFAULT 100)` | Returns rows in dataset scan order; callers should not infer a stable order. The configured maximum scan limit is enforced. |
| `lance_scan_document_state(uri, limit DEFAULT 100)` | Returns document ID, label, and stored revision for reconciliation. |
| `lance_vector_search(uri, query, k DEFAULT 10)` | Returns `(id, label, distance)`. |
| `hybrid_search(query, vector_query, lance_uri, k DEFAULT 10)` | Fuses BM25 and vector candidates using reciprocal-rank fusion. |
| `hybrid_search_explain(...)` | Also returns source ranks and weighted RRF contributions. |
| `hybrid_warc_search(...)` | Adds available `warc_record` fields to hybrid results. |
| `hybrid_warc_search_filtered(...)` / `hybrid_warc_search_filtered_explain(...)` | Apply documented eligibility filters before BM25 and exact vector candidate generation; the explain form includes source ranks/contributions. |
| `lance_dataset_write_lock(uri)` | Acquires the URI transaction lock before coordinated PostgreSQL metadata/BM25 changes. |
| `lance_outbox_enqueue(uri, ids, flat_vectors, vector_dim, labels)` | Enqueues non-versioned upsert work in the caller transaction. |
| `lance_outbox_enqueue_revision(...)` | Commits ordered, revision-aware vector work with the caller's PostgreSQL transaction. |
| `lance_document_delete(uri, ids, expected_revisions)` | Removes PostgreSQL metadata/BM25 rows and commits tombstones plus an ordered Lance delete event. Requires unique IDs and one current 64-character lowercase hexadecimal revision per ID. |
| `lance_outbox_process_next()` | Applies one oldest eligible committed event; call in autocommit, outside an explicit transaction block. |
| `lance_dataset_versions(uri)` / `lance_restore_version(uri, version)` | Lists Lance versions and compensates for external writes; restore is destructive and requires no pending outbox work. |

The extension also retains example helpers: `hello_pgwarc_lance()`,
`add_numbers`, `fibonacci`, `array_sum`, `shout`, `greatest_of`, `is_even`,
`word_count`, `double_or_none`, `greeting`, and `tokenize_korean`. Integer
addition, sums, multiplication, and Fibonacci accumulation saturate at their
Rust integer limits. `fibonacci(n)` returns zero for `n <= 0`; `shout(text)`
uppercases and appends `!`; `greeting` defaults `age` to 20; and
`tokenize_korean` exposes the configured tokenizer. `double_or_none(NULL)`
returns NULL. Other non-NULL Rust arguments use PostgreSQL STRICT behavior.

SQL result order is not guaranteed unless the caller supplies `ORDER BY`. Use
`ORDER BY score DESC, doc_id ASC` for BM25/hybrid results and
`ORDER BY distance ASC, id ASC` for vector results. These tie-breaks make a
client's requested order deterministic.

PostgreSQL cancellation or `statement_timeout` reports `57014` and rolls back
the current PostgreSQL transaction. A direct Lance mutation may already have
committed outside PostgreSQL, so it cannot be assumed to roll back with SQL;
inspect `lance_write_log` and dataset versions, then use the documented
compensation procedure. For imports and revisioned deletes, prefer the outbox:
PostgreSQL rows, revisions/tombstones, and the outbox job commit together, while
Lance materialization is eventual and replayable. A failed or pending vector
job is visible in outbox state and must be repaired or replayed; it is not
silently discarded. PostgreSQL and Lance do not provide distributed atomic
commit.

Input/permission/state errors use PostgreSQL SQLSTATEs. Other storage or
runtime failures may use `XX000`; their detailed message is diagnostic and is
not a stable API string. Clients should branch on SQLSTATE and documented
return columns, not localized or implementation-specific message text.

## Importer CLI

The CLI is `python3 tools/warc_importer.py WARC [WARC ...]`. Its option names
are:

```text
--charset-override --charset-record-override --pdftotext
--pdf-timeout-seconds --document-error-policy --extract-docx
--output/-o --execute --skip-schema --psql --lance-uri --vector-dim
--collection-manifest --collection-version --embedding-model
--embedding-model-version --embedding-normalization
--normalization-version --chunking-version --chunking-strategy
--chunk-size-chars --chunk-overlap-chars
--lance-mode {create,overwrite,append,upsert,outbox} --overwrite-lance
--embedding-command --embedding-jsonl --records-jsonl --no-hash-vectors
--temp-dir --embedding-timeout --batch-max-bytes --batch-size --bm25-mode
--max-record-bytes --limit --min-text-chars --summary --summary-json
--audit-manifest-json --checkpoint-db --checkpoint-max-bytes
--checkpoint-max-memory-bytes
```

The CLI option defaults and validation rules are:

| Option | Default / validation |
|---|---|
| `--vector-dim` | `3`; must be positive. Supplied embedding vectors must match it and contain only finite numbers before SQL execution. |
| `--lance-mode` | `create`; `overwrite`, `append`, `upsert`, and `outbox` have the meanings below. Modes other than `create` require `--lance-uri`. |
| `--bm25-mode` | `function`; accepts `bulk` and `copy` as well. |
| `--batch-size` / `--batch-max-bytes` | `1000` / `16 MiB`; both positive. `upsert` and `outbox` cap rows per batch at 10,000. A single record may exceed the byte batch target but remains subject to `--max-record-bytes`. |
| `--max-record-bytes` | `64 MiB`; positive limit for a WARC payload and decoded HTTP body. |
| `--embedding-timeout` / `--pdf-timeout-seconds` | `600` / `30` seconds; each must be finite and positive. |
| `--document-error-policy` | `fail`; `metadata-only` is explicit and applies only to configured PDF/DOCX extraction failures. |
| `--limit` / `--min-text-chars` | no limit / `1`; both must be non-negative when supplied. `--limit 0` imports no records. |
| `--chunking-strategy` | `whole_document`; default size is omitted, overlap is `0`, version is `whole-document-v1`. `char_window` requires a positive size, overlap smaller than size, and a non-empty version; active vector imports use revisioned `outbox`. |
| `--checkpoint-max-bytes` / `--checkpoint-max-memory-bytes` | `20 GiB` / `256 MiB`; each must be positive. `char_window` cannot be combined with `--checkpoint-db`. |

Other option behavior is fixed as follows. `-h/--help` prints usage and exits
0. `--charset-override` and `--charset-record-override` are repeatable and have
no default overrides; the former uses strict decoding, while the latter is for
an individually reviewed record, uses replacement, and counts U+FFFD. Both
choices are included in the summary. PDF input requires an available Poppler
`pdftotext` executable; DOCX extraction is opt-in and bounded. `--output/-o`
defaults to stdout, `--execute` and `--skip-schema` default false, and `--psql`
uses `PGWARC_PSQL` or `psql` when omitted. `--limit` defaults to no record cap;
`--min-text-chars` defaults to 1. `--summary`, `--summary-json`, and
`--audit-manifest-json` are opt-in; a full-input audit manifest cannot be
combined with `--limit`. `--checkpoint-db` is opt-in, mutually exclusive with
the audit manifest, vector imports, and `char_window`; its byte and memory
limits default to 20 GiB and 256 MiB.

`--collection-manifest` is opt-in and holds a shared lock for the import.
Collection model, immutable model version, normalization, normalization
version, and chunking options assert the manifest profile; the collection
version defaults to the active version. `--chunking-strategy` defaults to
`whole_document`; explicit chunk size is invalid for that strategy. For
`char_window`, overlap defaults to zero and size/version are required. An
embedding command and embedding JSONL cannot be supplied together; either
requires `--lance-uri`. `--records-jsonl` is an optional extraction export.
`--no-hash-vectors` opts into metadata/BM25-only output instead of the
deterministic placeholder vectors. `--temp-dir` selects a disk-spool directory;
the default is a private temporary directory. These controls do not change
the rule that any supplied vector must have exactly the configured dimension
and finite values.

`--lance-mode create` is the default; `overwrite` recreates a dataset,
`append` adds rows, `upsert` merges by document ID, and `outbox` queues ordered
work for an existing dataset. `--overwrite-lance` is a deprecated alias for
`--lance-mode overwrite`. `--bm25-mode` accepts `function`, `bulk`, or `copy`.
`--embedding-command` reads extracted record JSONL from stdin and must write
`{"doc_id": ..., "vector": [...]}` JSONL to stdout. `--embedding-jsonl` accepts
precomputed vectors; `--records-jsonl` emits records for a separate embedding
step. Use a real embedding provider/model for semantic search. Deterministic
hash vectors are placeholders, not semantic embeddings; `--no-hash-vectors`
skips them. Embedding dimensions and finite values are checked before SQL is
executed.

The default document error policy is `fail`: extraction, decoding, or embedding
failures stop the import before SQL execution. `metadata-only` is an explicit
choice for supported PDF/DOCX extraction failures and is reported in the
summary. SQL is only executed when `--execute` is supplied; otherwise generated
SQL is written with `--output` or to stdout. Argument errors use argparse's exit
code 2. Import/preflight/embedding failures exit 1; SQL execution propagates
the `psql` exit status. Success exits 0. The importer does not silently turn
failed documents into successful vector rows.

`tools/lance_outbox_worker.py` accepts `--psql`, `--once`,
`--poll-interval-ms` (10–60,000; default 1,000), and
`--statement-timeout-seconds` ((0, 3,600]; default 300). `--once` processes at
most one ready job and exits 0 for completed or idle; a worker error exits 1.
Invalid worker arguments exit 2. `--once` is useful after a worker restart to
drain a job that committed before the worker stopped.

## PG16 package and end-to-end example

Build and install-test the versioned package against the pinned PostgreSQL 16
Bookworm image in a disposable Docker environment:

```bash
PG_MAJOR=16 make release-artifacts
PG_MAJOR=16 make smoke-release-artifacts
```

The smoke target installs the extracted package in a fresh PostgreSQL container
and runs `CREATE EXTENSION` and SQL checks. See [PACKAGING.md](../PACKAGING.md)
for the artifact layout, checksums and target limits. Other PostgreSQL majors
and operating systems are outside the currently verified support target.

The following sequence uses a trusted adapter command that reads extracted
record JSONL from stdin and writes one real 384-dimensional embedding per
record to stdout. Its query mode in this example accepts `--query TEXT` and
prints a PostgreSQL `real[]` literal (for example, `{0.1,0.2}`); this is an
adapter convention, not a pgwarc_lance CLI option. `PGWARC_PSQL` must connect to
the disposable PG16 database; use the deployment's protected libpq credential
source. The `/tmp` dataset is only for this disposable example; operations
should configure an administrator-owned local dataset directory through
`pgwarc_lance.allowed_uri_prefix`.

```bash
export PGWARC_PSQL='psql -X -v ON_ERROR_STOP=1 -d webarchive'
export EMBEDDING_COMMAND='python3 ./embedding-adapter.py'
export LANCE_URI=/tmp/pgwarc-contract-example.lance

"$PGWARC_PSQL" -c 'CREATE EXTENSION IF NOT EXISTS pgwarc_lance'
"$PGWARC_PSQL" -v lance_uri="$LANCE_URI" \
  -c "SELECT lance_create_table(:'lance_uri', 384, false)"

python3 tools/warc_importer.py capture.warc \
  --lance-uri "$LANCE_URI" --vector-dim 384 --lance-mode outbox \
  --chunking-strategy char_window --chunk-size-chars 512 \
  --chunk-overlap-chars 64 --chunking-version archive-text-v1 \
  --embedding-command "$EMBEDDING_COMMAND" --execute

# Search vectors from the same model/provider profile as the collection.
QUERY_VECTOR="$(python3 ./embedding-adapter.py --query 'archival search')"
"$PGWARC_PSQL" -v lance_uri="$LANCE_URI" -v query_vector="$QUERY_VECTOR" \
  -c "SELECT * FROM lance_vector_search(:'lance_uri', :'query_vector'::real[], 10) ORDER BY distance ASC, id ASC"
"$PGWARC_PSQL" -v lance_uri="$LANCE_URI" -v query_vector="$QUERY_VECTOR" \
  -c "SELECT * FROM hybrid_search('archival search', :'query_vector'::real[], :'lance_uri', 10) ORDER BY score DESC, doc_id ASC"

# Reimport a revised capture with the same target URL/date and chunk profile.
python3 tools/warc_importer.py revised-capture.warc \
  --lance-uri "$LANCE_URI" --vector-dim 384 --lance-mode outbox \
  --chunking-strategy char_window --chunk-size-chars 512 \
  --chunk-overlap-chars 64 --chunking-version archive-text-v1 \
  --embedding-command "$EMBEDDING_COMMAND" --execute

# Apply committed work; rerun after restarting the worker if a job remains.
python3 tools/lance_outbox_worker.py --once --psql "$PGWARC_PSQL"

# Delete one current revision and materialize the tombstone through the outbox.
"$PGWARC_PSQL" -v lance_uri="$LANCE_URI" <<'SQL'
SELECT doc_id, expected_revision
FROM pgwarc_lance.lance_document_revision
WHERE uri = :'lance_uri' AND NOT is_deleted
ORDER BY doc_id LIMIT 1 \gset
SELECT lance_document_delete(
  :'lance_uri', ARRAY[:doc_id]::bigint[], ARRAY[:'expected_revision']::text[]
);
SQL
python3 tools/lance_outbox_worker.py --once --psql "$PGWARC_PSQL"
```

`char_window` needs an explicit version and outbox mode for revisioned
reconciliation. Reuse the same target URL/date and chunk profile when reimporting
an updated capture; changed text updates its stable chunk identity and revision.
The worker only processes committed events. A PostgreSQL rollback removes its
metadata and outbox event, while a committed event remains available to a later
worker process. PostgreSQL and Lance still do not provide distributed atomic
commit.

## Version policy

Versions follow SemVer 2.0.0. While the major version is zero, a documented SQL,
CLI, error-code, result-order, or stored-format incompatibility increments the
minor component (`0.y.0`); compatible fixes and dependency/security updates
increment the patch component (`0.y.z`). After `1.0.0`, incompatible contract
or storage changes require a major increment, compatible additions a minor
increment, and compatible fixes a patch increment. Versioned SQL migrations
must accompany extension changes that need database updates. The 1.0.0 tag,
binary release, and production approval remain separate human decisions after
the complete v1.0 gate.
