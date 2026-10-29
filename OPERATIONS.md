# PostgreSQL 16 local-storage operating procedure

This procedure covers one PostgreSQL instance, local Lance directories and
trusted reader/writer roles. Current acceptance evidence and remaining gates
are in [PRODUCTION.md](PRODUCTION.md). Use an administrator to install and create
datasets, then run unattended ingestion with the writer role. Application
credentials and the OS dataset root must be managed by the operator.

## Install and configure

Install the tested artifact, create the extension as administrator, and create
an existing directory such as `/srv/pgwarc` owned by the PostgreSQL OS account.
Only trusted OS accounts may change it or its parents. Choose database name,
paths, scan limit and timeouts for the measured workload; the values below are
examples rather than performance targets.

```sql
CREATE EXTENSION pgwarc_lance;
ALTER DATABASE archive SET pgwarc_lance.allowed_uri_prefix = '/srv/pgwarc';
ALTER DATABASE archive SET pgwarc_lance.max_scan_rows = '10000';
ALTER DATABASE archive SET pgwarc_lance.allow_destructive_ops = 'off';
```

Reconnect so database defaults apply, verify them with `SHOW`, then create each
dataset once. Do not allow another SQL or external process to recreate it.

```sql
SELECT lance_create_table('/srv/pgwarc/archive.lance', 384, false);
```

Keep administrator/install credentials out of ingestion and query workers.
The extension SQL entry points normally install in `public`; adjust qualified
function names below if the administrator installed them in another schema.
Restrict schema creation to administrators and use a trusted search path for
workers. Existing role grants must be reviewed before altering shared schemas.

## Reader and writer grants

The following roles intentionally have no login or superuser rights. Grant
them to existing managed service identities. Passwords are not stored in this
file. The writer supports function-mode BM25, upsert ingestion and revision-aware
document deletion; it cannot create/overwrite/restore datasets. These grants passed an actual PostgreSQL 16
test using `SET ROLE` and importer startup with the selected role.

```sql
CREATE ROLE pgwarc_reader NOLOGIN;
CREATE ROLE pgwarc_writer NOLOGIN;
GRANT USAGE ON SCHEMA public, pgwarc_lance TO pgwarc_reader, pgwarc_writer;

GRANT SELECT ON pgwarc_lance.bm25_doc, pgwarc_lance.bm25_term,
    pgwarc_lance.bm25_long_term, pgwarc_lance.warc_record TO pgwarc_reader;
GRANT SELECT (uri, doc_id, expected_revision, is_deleted)
    ON pgwarc_lance.lance_document_revision TO pgwarc_reader;
GRANT SELECT (id, uri, state, ids)
    ON pgwarc_lance.lance_outbox TO pgwarc_reader;
GRANT EXECUTE ON FUNCTION public.hybrid_warc_search(text,real[],text,integer)
    TO pgwarc_reader;
GRANT EXECUTE ON FUNCTION public.lance_scan_document_state(text,integer)
    TO pgwarc_reader;

GRANT SELECT, INSERT, UPDATE ON pgwarc_lance.bm25_doc,
    pgwarc_lance.warc_record TO pgwarc_writer;
GRANT SELECT, INSERT, DELETE ON pgwarc_lance.bm25_term,
    pgwarc_lance.bm25_long_term TO pgwarc_writer;
GRANT INSERT ON pgwarc_lance.lance_write_log TO pgwarc_writer;
GRANT USAGE ON SEQUENCE pgwarc_lance.lance_write_log_id_seq TO pgwarc_writer;
GRANT SELECT (uri, state) ON pgwarc_lance.lance_outbox TO pgwarc_writer;
GRANT SELECT ON pgwarc_lance.lance_outbox_status TO pgwarc_reader, pgwarc_writer;
GRANT EXECUTE ON FUNCTION public.bm25_index_document(bigint,text),
    public.lance_upsert_many(text,bigint[],real[],integer,text[]),
    public.lance_outbox_enqueue(text,bigint[],real[],integer,text[]),
    public.lance_outbox_enqueue_revision(text,bigint[],real[],integer,text[],text[]),
    public.lance_dataset_write_lock(text),
    public.lance_document_delete(text,bigint[],text[])
    TO pgwarc_writer;

CREATE ROLE pgwarc_outbox_worker NOLOGIN;
GRANT USAGE ON SCHEMA public, pgwarc_lance TO pgwarc_outbox_worker;
GRANT SELECT ON pgwarc_lance.lance_outbox_status,
    pgwarc_lance.lance_outbox_failures TO pgwarc_outbox_worker;
GRANT EXECUTE ON FUNCTION public.lance_outbox_process_next() TO pgwarc_outbox_worker;

-- Replace these existing managed login names with your service identities.
GRANT pgwarc_reader TO archive_reader_login;
GRANT pgwarc_writer TO archive_writer_login;
GRANT pgwarc_outbox_worker TO archive_worker_login;
ALTER ROLE archive_reader_login SET statement_timeout = '2s';
ALTER ROLE archive_reader_login SET idle_in_transaction_session_timeout = '30s';
ALTER ROLE archive_reader_login SET work_mem = '4MB';
ALTER ROLE archive_reader_login SET temp_file_limit = '256MB';
ALTER ROLE archive_writer_login SET statement_timeout = '120s';
ALTER ROLE archive_writer_login SET idle_in_transaction_session_timeout = '30s';
```

Apply timeout defaults to the actual login identities and reconnect. PostgreSQL
[SET ROLE](https://www.postgresql.org/docs/16/sql-set-role.html) does not load the
selected group role's ALTER ROLE settings. These timeout defaults are operating
limits for trusted services, not an untrusted-user resource isolation boundary.

These are trusted whole-corpus roles. Table grants and dataset-function access
are not a per-document or per-tenant isolation mechanism. The writer can modify
its granted SQL data and write permitted datasets, so use a separate database
and OS root when corpus isolation is required. The importer writer receives
only SELECT on outbox `uri` and `state` for direct-writer ordering checks.
Outbox enqueue and delivery use granted C functions with a locked search path;
application roles receive no payload-table write rights. The delivery role can
inspect aggregate status and failed-event diagnostics without reading vector
payloads. Readers need no write-log grant. The worker does not need BM25
content-table write grants.

## Commit-safe vector ingestion

Create the Lance dataset administratively, then import with the `outbox` mode.
The WARC metadata, BM25 changes and vector payload commit as one SQL transaction.
An external worker applies each committed payload after commit:

```bash
python3 tools/warc_importer.py input.warc.gz --skip-schema \
    --lance-uri /srv/pgwarc/archive.lance --lance-mode outbox \
    --vector-dim 384 --embedding-jsonl vectors.jsonl --execute \
    --psql "$WRITER_PSQL" --summary-json import.summary.json
python3 tools/lance_outbox_worker.py --psql "$WORKER_PSQL" --poll-interval-ms 1000
```

Revision-aware `outbox` imports require extension 0.1.23 or newer. The importer
records a content revision in PostgreSQL and the worker writes the same value to
the Lance row. The revision combines the stable document ID, WARC target URI,
content type, HTTP status, extracted text length, payload digest and BM25 content
checksum; it does not identify the embedding model. Existing
Lance rows acquire a nullable `revision` column on their first upsert, and stay
unversioned until such an update. Do not grant the writer direct access to
`lance_document_revision`; the security-definer enqueue function maintains it.
Custom import transactions must call `lance_dataset_write_lock(uri)` before the
first WARC/BM25 write, then queue the revisioned upsert in that same transaction.

### Delete and recreate a document

The writer may call `lance_document_delete(text,bigint[],text[])` only when
`pgwarc_lance.allow_destructive_ops` is on. Pass the active expected revision
with the stable document ID. A missing or changed revision is rejected; first
import an untracked legacy row through revision-aware `outbox`. The delete
transaction takes the dataset writer lock, removes WARC metadata and BM25
content, stores the last content revision with `is_deleted=true`, and queues
the physical Lance delete. The tombstone
filters vector and hybrid candidates immediately while the worker is pending.
Repeating the same delete is a no-op; recreate through a new revision-aware
`outbox` upsert.

```sql
SELECT lance_document_delete(uri, ARRAY[doc_id], ARRAY[expected_revision])
FROM pgwarc_lance.lance_document_revision
WHERE uri = '/srv/pgwarc/archive.lance' AND doc_id = 123 AND NOT is_deleted;
```

The extension does not run Lance compaction or version garbage collection.
Older dataset versions remain until an operator explicitly manages them under a
separate backup/restore plan. Do not run compaction or version cleanup until the
retention policy confirms which preserved snapshots must remain and a matching
PostgreSQL/Lance restore set is available. Promoting an old Lance version does not clear
PostgreSQL tombstones or restore deleted metadata/BM25. A logical restore must
rebuild metadata, BM25, revision and vector from authoritative input.

For a long-running source, preserve a durable importer checkpoint across
worker restarts:

```bash
python3 tools/warc_importer.py input.warc.gz --skip-schema \
    --lance-uri /srv/pgwarc/archive.lance --lance-mode outbox \
    --vector-dim 384 --embedding-jsonl vectors.jsonl \
    --checkpoint-db /srv/pgwarc/checkpoints/import.sqlite \
    --checkpoint-max-bytes 21474836480 \
    --batch-size 500 --batch-max-bytes 16777216 \
    --execute --psql "$WRITER_PSQL" --summary-json import.summary.json
```

Create the checkpoint parent directory with operator-only access. The SQLite
file contains extracted text and vector material and is created mode `0600`.
Its config binds to each source SHA-256, importer source and import settings;
its stored target hash prevents accidental reuse against a different PostgreSQL
instance. Keep the same checkpoint file, source bytes, embedding JSONL and
settings when resuming. Changed source bytes or semantic settings create a
separate checkpoint lineage. The importer re-reads gzip framing from byte zero
to the saved record ordinal, skips completed extraction and database batches,
and retries failed or unacknowledged records. It returns non-zero while a
record or vector remains failed. A batch is checkpointed only after `psql`
exits successfully; an uncertain commit is replayed through idempotent document
upsert/outbox delivery.

The durable database is capped by `--checkpoint-max-bytes`. The default
working-set check is estimated at 256 MiB and is not an OS RSS quota. The
checkpoint cap and measured RSS/temp/WAL during a representative run are
separate operating limits. Do not delete a checkpoint to clear an import
failure; resolve the record or preserve the state for diagnosis first.

Run one worker instance per deployment unless the workload needs parallelism;
multiple workers are safe, and PostgreSQL row/advisory locks preserve the event
order for each URI. A failed earlier event blocks later work for that URI.
Check backlog and failure status with:

```sql
SELECT uri, pending_jobs, failed_jobs, pending_delete_jobs, failed_delete_jobs,
       oldest_unfinished_ms, latest_applied_version, last_completed_at
FROM pgwarc_lance.lance_outbox_status;

SELECT id, uri, operation, attempt_count, last_attempt_at, last_error
FROM pgwarc_lance.lance_outbox_failures
ORDER BY id;
```

If PostgreSQL rolled back its acknowledgment after a Lance upsert or delete, the
event remains `pending`. Run the worker explicitly to retry the same event. An
upsert converges on the same Lance row; repeating a delete for an absent ID is
safe and does not remove another document:

```bash
python3 tools/lance_outbox_worker.py --once --psql "$WORKER_PSQL"
```

For a terminal `failed` event, first inspect `last_error` and compare PostgreSQL
with Lance using the read-only doctor. After resolving the cause and confirming
that the stored operation remains safe to replay, an administrator may return
that event to the retry queue:

```sql
UPDATE pgwarc_lance.lance_outbox
SET state = 'pending', attempt_count = 0, last_error = NULL,
    available_at = clock_timestamp(), completed_at = NULL,
    applied_version = NULL
WHERE id = 123 AND state = 'failed';
```

Do not set a row to `completed` manually. PostgreSQL and Lance have no shared
atomic commit: if a Lance mutation succeeds but its SQL acknowledgment rolls
back, the worker replays the same event. Completed rows clear their vector
payload and ID list after the success acknowledgment commits; failed rows retain their payload for a
reviewed retry. Read-after-write for vector/hybrid queries is
available after the relevant outbox event is `completed`; BM25 and WARC metadata
are visible immediately after the importer transaction commits. For backups,
pause ingestion and workers and snapshot the SQL database and Lance directory as
one consistent set. A pending queue must be restored with the matching Lance
directory before workers restart. The worker executable provides polling and
signal handling but does not install a service manager or scheduler.

From 0.1.4, BM25 and hybrid query text is limited to 65,536 UTF-8 bytes; the
0.1.16 candidate raises that bound to 2,500,000 bytes so the longest token in the
audited Common Crawl slice can be searched exactly. Search `k` remains limited
to 10,000 across BM25/vector/hybrid entry points. Oversized requests fail with
SQLSTATE 54000. `k=0` still returns no results. These limits do not replace
authentication or admission control for concurrent requests.

The 0.1.16 candidate stores the full oversized token in
`pgwarc_lance.bm25_long_term` and uses a fixed-size SHA-256 key in BM25 postings;
the table is included in extension configuration dumps. Grant readers SELECT
and writers SELECT/INSERT/DELETE on this table as shown above. The dictionary
keeps entries after a document is reindexed or removed so concurrent indexing
cannot race orphan cleanup. To remove orphaned token text, pause all BM25
writers first, then run:

```sql
DELETE FROM pgwarc_lance.bm25_long_term AS long_term
WHERE NOT EXISTS (
    SELECT 1 FROM pgwarc_lance.bm25_term AS posting
    WHERE posting.term = long_term.term_key
);
```

The importer reports oversized token counts and requires extension 0.1.16 or
newer before it executes a batch containing such a token. Tokens above the
2,500,000-byte searchable limit fail the complete import before any SQL runs;
the extension rejects the same input from direct indexing calls with SQLSTATE
54000.

The 0.1.15-to-0.1.16 upgrade scans existing postings for raw terms over 2,000
bytes and reindexes only the affected documents from `bm25_doc.content`. Stop
BM25 writers and reconnect sessions after installing the new shared library
before running `ALTER EXTENSION ... UPDATE`; the data rewrite is part of the
upgrade transaction and rolls back on any indexing error.

BM25 aggregation and ranking now execute inside PostgreSQL and only the top
`k` rows cross into Rust. Sorting/hashing can spill to temporary files.
`work_mem` applies per executor operation and `hash_mem_multiplier` can increase
hash allocations, so concurrent sessions multiply memory use. The configured
`temp_file_limit` bounds temporary executor files per process; exceeding it
cancels the query. These example values require workload validation and are
not exact RSS limits. PostgreSQL describes both settings in its
[resource configuration](https://www.postgresql.org/docs/16/runtime-config-resource.html).

From 0.1.5, BM25 corpus statistics and postings are read in one SQL statement
snapshot, including a transaction that has already written data. This provides
consistent SQL scoring under concurrent commits; SQL/Lance consistency still
requires the ingestion and recovery procedures below.

Use genuine precomputed embeddings from the intended model, and record the
model version, dimension and input normalization with each import:

```bash
python3 tools/warc_importer.py input.warc.gz --skip-schema --lance-uri /srv/pgwarc/archive.lance --lance-mode upsert --bm25-mode function --vector-dim 384 --embedding-jsonl vectors.jsonl --batch-size 500 --batch-max-bytes 16777216 --temp-dir /srv/pgwarc-spool --execute --psql "$WRITER_PSQL" --summary-json import.summary.json
```

`WRITER_PSQL` must target the managed writer identity and include
`-X -v ON_ERROR_STOP=1`; use a protected `.pgpass` or service definition.
`--skip-schema` suppresses all schema/table/index DDL. Missing installation or
grants cause an error. The default hash vectors are development placeholders,
so always provide real embeddings for operational retrieval.

PDF and DOCX body extraction are opt-in. Supply `--pdftotext /path/to/pdftotext`
to enable Poppler PDF extraction and `--extract-docx` for WordprocessingML text
from DOCX files. The importer checks the configured Poppler executable, limits
each PDF process to 30 seconds by default, and bounds both input and output by
`--max-record-bytes` (64 MiB by default). DOCX parsing limits ZIP entries and
expanded/output bytes and rejects encrypted or unsafe entries, DTD/entity
declarations, NUL XML and empty bodies. Text extraction failures stop the import
before SQL execution; inspect the summary and resolve every failed record.
Scanned PDFs with no text need OCR or manual review. Legacy Office, RTF, EPUB,
and other unsupported binary formats remain unsupported and fail explicitly.

Review HTTP charset declarations before importing known problem records.
`--charset-override LABEL=CODEC` applies a corpus-level mapping with strict
decoding. `--charset-record-override '<WARC-Record-ID>=CODEC'` applies a
loss-tolerant correction to one reviewed record and records observed U+FFFD
characters in the summary. Missing, duplicate or filtered targets stop before
SQL generation. The importer does not guess a replacement codec for other
records.

After a major load, use an administrative maintenance connection to collect
planner statistics before checking latency or admitting the new corpus:

```sql
ANALYZE pgwarc_lance.bm25_doc, pgwarc_lance.bm25_term, pgwarc_lance.warc_record;
-- In the appropriate maintenance window, also refresh visibility information:
VACUUM (ANALYZE) pgwarc_lance.bm25_doc, pgwarc_lance.bm25_term,
    pgwarc_lance.warc_record;
```

On PG16 the ordinary writer's table grants do not give it ownership-level
maintenance rights. Keep maintenance in the administrator's procedure;
PostgreSQL explains [ANALYZE permissions and statistics](https://www.postgresql.org/docs/16/sql-analyze.html)
and [vacuuming and visibility maps](https://www.postgresql.org/docs/16/routine-vacuuming.html).
Validate autovacuum settings for the actual ingestion rate and monitor its
progress; a fresh corpus can otherwise wait for its first automatic analysis.
The `bm25_doc(doc_len)` index introduced in 0.1.10 only supported an index-only
corpus-statistics scan after VACUUM marked the table pages all-visible in the
measured corpus. Before that maintenance, PostgreSQL kept the sequential scan.
Check the query plan after the maintenance step before attributing a latency
change to this index.

The 0.1.11 candidate also includes `doc_len` in `bm25_doc_pkey`, so document
lookups and the full document statistics scan can use index-only plans. Heap
fetches still depend on visibility-map coverage; check `Heap Fetches` after a
load or update and refresh visibility in the administrator's maintenance
window. The 0.1.10→0.1.11 extension update rebuilds the primary key and validates
the extension-owned foreign key in the same transaction. Its populated clone
check completed in 1.23 seconds, but production lock duration and larger-corpus
behavior remain unmeasured. This is a development candidate, not an approved
production upgrade procedure.

## Verify BM25 content/posting consistency

`tools/audit_bm25_index.py` recalculates term frequencies from stored document
content with the importer tokenizer, then compares them with stored postings in
one `REPEATABLE READ READ ONLY` snapshot. Run it with a read-only identity that
can SELECT `pgwarc_lance.bm25_doc`, `bm25_term`, and, for `--warc-only`,
`warc_record`:

```bash
PGWARC_PSQL="$READ_ONLY_PSQL" python3 tools/audit_bm25_index.py --warc-only \
    --output-json "$PRIVATE_EVIDENCE_DIR/bm25-index-audit.json"
```

The audit reads all selected content and postings and runs the Python tokenizer;
it can take substantial I/O and keeps a long snapshot, so schedule it during a
low-activity maintenance window. It is read-only and never repairs or reindexes.
Exit code `0` means the current importer tokenizer agrees with the selected
snapshot, `2` means posting mismatches or documents with null content could not
be verified, and `1` means the audit could not complete. Its report contains
document IDs for affected rows. `--output-json` creates the report file with
mode `0600` and any new parent directories with mode `0700`; stdout then contains
only aggregate counts. Keep the report in protected private evidence storage.
Without `--output-json`, stdout contains the full report and IDs. Consistency
does not validate extraction quality, Lance/vector state, or search relevance.
Review mismatched documents and preserve the input/version evidence before
deciding on a separate repair.

## Monitor and respond

Record import start/end, input identity, embedding identity, batch limits,
exit status and summary. Monitor PostgreSQL errors, statement timeouts,
latency percentiles, lock waits, active/idle transactions, spool free space,
Lance free space and dataset versions. The write log contains only committed
SQL entries and cannot prove that a cancelled external write did not happen.

Administrative diagnostic examples:

```sql
SELECT extversion FROM pg_extension WHERE extname = 'pgwarc_lance';
SHOW pgwarc_lance.allowed_uri_prefix;
SHOW pgwarc_lance.max_scan_rows;
SHOW pgwarc_lance.allow_destructive_ops;
SELECT pid, application_name, state, wait_event_type, wait_event,
    now() - xact_start AS transaction_age
FROM pg_stat_activity WHERE datname = current_database();
SELECT uri, op, row_count, wrote_at
FROM pgwarc_lance.lance_write_log ORDER BY id DESC LIMIT 20;
SELECT * FROM lance_dataset_versions('/srv/pgwarc/archive.lance');
SELECT count(*) FROM pgwarc_lance.warc_record;
SELECT count(*) FROM pgwarc_lance.bm25_doc;
SELECT lance_count('/srv/pgwarc/archive.lance');
SELECT temp_files, temp_bytes FROM pg_stat_database WHERE datname=current_database();
SELECT relname, n_mod_since_analyze, n_dead_tup, last_autoanalyze, last_autovacuum
FROM pg_stat_all_tables WHERE schemaname='pgwarc_lance';
```

SQL table counts are global to this extension installation. Comparing them
with one Lance dataset is meaningful only when that dataset represents the
same entire corpus. Counts alone do not prove ID/content consistency. Validate
known document IDs, vector/metadata contents and labelled queries after imports
and restores. A missing Lance data object can leave `lance_count` unchanged;
compare saved file hashes and exercise representative content reads during recovery.
Avoid full `lance_scan` in routine monitoring.

If a writer times out or disconnects, preserve its inputs/embeddings and logs,
stop conflicting writers, and retry the same input with `upsert`. Compare the
corpus and representative queries before resuming imports. Affected legacy
IDs with duplicate vectors are rejected; take a coordinated backup and rebuild
that dataset from authoritative SQL/input data instead of retrying append.

For a corrupt/missing dataset or unexplained version/count difference, stop all
writers and preserve the current directories for diagnosis. Follow the
coordinated backup/restore procedure in [PRODUCTION.md](PRODUCTION.md); restore
SQL and Lance from the same stopped-writer backup set into an isolated instance
first. Restore global roles separately, apply recorded database policies and
PostgreSQL OS ownership, then verify grants, sequence, counts, file hashes, versions,
a controlled write and labelled retrieval before
cutting over. Never use `overwrite=true` as a recovery shortcut.

Rehearse `make test-runtime-safety`, `make test-ingest-replay`,
`make test-write-cancellation`, `make test-backup-restore`, `make test-search-memory`,
`make test-bm25-snapshot` and the extracted-artifact `make test-storage-faults`
and `make test-storage-corruption` in disposable infrastructure.
Fixtures cover rollback, backend/container termination, isolated ENOSPC,
permissions, retry, missing/truncated Lance files and quiescent recovery into
a separately initialized cluster, including roles and a post-restore write. Host power loss, the actual storage
stack and domain-specific recovery time objectives still require operating-environment drills.
