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
file. The writer supports function-mode BM25 and upsert ingestion; it cannot
create/overwrite/restore datasets. These grants passed an actual PostgreSQL 16
test using `SET ROLE` and importer startup with the selected role.

```sql
CREATE ROLE pgwarc_reader NOLOGIN;
CREATE ROLE pgwarc_writer NOLOGIN;
GRANT USAGE ON SCHEMA public, pgwarc_lance TO pgwarc_reader, pgwarc_writer;

GRANT SELECT ON pgwarc_lance.bm25_doc, pgwarc_lance.bm25_term,
    pgwarc_lance.warc_record TO pgwarc_reader;
GRANT EXECUTE ON FUNCTION public.hybrid_warc_search(text,real[],text,integer)
    TO pgwarc_reader;

GRANT SELECT, INSERT, UPDATE ON pgwarc_lance.bm25_doc,
    pgwarc_lance.warc_record TO pgwarc_writer;
GRANT SELECT, INSERT, DELETE ON pgwarc_lance.bm25_term TO pgwarc_writer;
GRANT INSERT ON pgwarc_lance.lance_write_log TO pgwarc_writer;
GRANT USAGE ON SEQUENCE pgwarc_lance.lance_write_log_id_seq TO pgwarc_writer;
GRANT EXECUTE ON FUNCTION public.bm25_index_document(bigint,text),
    public.lance_upsert_many(text,bigint[],real[],integer,text[]) TO pgwarc_writer;

-- Replace these existing managed login names with your service identities.
GRANT pgwarc_reader TO archive_reader_login;
GRANT pgwarc_writer TO archive_writer_login;
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
and OS root when corpus isolation is required. Readers need no write-log grant.

From 0.1.4, BM25 and hybrid query text is limited to 65,536 UTF-8 bytes; search
`k` is limited to 10,000 across BM25/vector/hybrid entry points. Oversized
requests fail with SQLSTATE 54000. Use smaller search requests; `k=0` still
returns no results. These limits do not replace authentication or admission
control for concurrent requests.

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
