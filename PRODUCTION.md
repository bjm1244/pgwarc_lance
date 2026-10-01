# Production readiness

This is still a source preview. Passing unit tests is not a production approval.
The initial validation profile is PostgreSQL 16, a single instance, local Lance
storage, trusted operators, and a separate disposable validation database.
Confirm the intended workload and corpus before assigning release thresholds.

## Required evidence

| Gate | Acceptance evidence |
| --- | --- |
| Build and fresh install | Linux PostgreSQL 16 builds from the locked dependencies; `CREATE EXTENSION` succeeds in a fresh DB. Test other majors separately before claiming support. |
| Dependency security | Current RustSec audit passes with vulnerabilities, unsound and yanked packages denied; review maintenance warnings and native dependencies separately. Never promote the vulnerable 0.1.2 binary. |
| SQL correctness and grants | Scalar/search/security regression outputs match; ungranted roles cannot execute functions; selective grants work. |
| Runtime safety | `tests/db_runtime_safety.py` passes concurrent same-document indexing, denied-write side effects, scan timeout/backend reuse, rollback behavior, and version recovery. |
| Import safety | WARC/gzip limits, validation before SQL execution and bounded-batch CLI memory pass. Measure representative corpus/embedding-command resources too. |
| Import replay | The 0.1.2 upsert path passes repeated import, concurrent writers and backend termination/retry. Validate the same procedure with operational inputs. Append still permits duplicate IDs. |
| Real retrieval quality | A licensed representative corpus, genuine embeddings and independent relevance labels for 20–50 queries; positive hit-rate/MRR thresholds fixed before evaluating results. Synthetic/hash vectors do not satisfy this gate. |
| Performance | Explicit corpus size, vector dimension, concurrency, latency targets and memory/storage limits; reproducible import/search measurements. |
| Search memory | Bound query/output inputs and BM25 intermediate memory under common terms in a larger corpus; cancellation must leave the backend reusable. 0.1.4 moves aggregation into the PostgreSQL executor and passes a fixed 100,000-document spill/timeout/quota fixture; validate operational query memory and temp-file limits too. |
| Backup and recovery | Coordinated PostgreSQL and Lance snapshots; restore into a clean instance and verify row counts, dataset versions and retrieval results. |
| Release and upgrade | Immutable versioned artifact with license notices; fresh install tested from the artifact. Test an actual upgrade whenever a supported previous version exists. Existing sessions must reconnect to load the replacement shared library. |
| Operations | Least-privilege roles, managed credentials, OS-controlled dataset root, monitoring, cancellation limits and tested incident procedures. |

## Validation commands

Run database commands only in a disposable environment:

```bash
export PGWARC_DEV_PASSWORD="$(openssl rand -base64 24)"
make test-uri-policy
make test-warc
make check-docs
cargo fmt --all --check
make verify
python3 tests/db_runtime_safety.py --json-output /tmp/pgwarc-runtime-safety.json
make test-backup-restore
make test-ingest-replay
make test-write-cancellation
make test-search-memory
make test-bm25-snapshot
make audit-dependencies
# After extracting PG16 release artifacts:
make test-storage-faults DIST_DIR=/path/to/pgwarc_lance-0.1.5-pg16
make test-storage-corruption DIST_DIR=/path/to/pgwarc_lance-0.1.5-pg16
```

The runtime test requires an installed extension and an administrative test
role. It creates temporary BM25 records and Lance datasets. SQL test data is
cleaned up; dataset files are retained for inspection. It does not touch another
database and must never be pointed at production. The backup test creates two
new databases and a temporary reader role, then removes them. It verifies all
four SQL data tables, the write-log sequence, BM25 results, Lance contents and
versions, and selective function grants after restoring within the same cluster.
The extracted-artifact corruption test creates two separate PostgreSQL containers
with no network, published ports or host data mounts. It restores role definitions
from a separate backup into the second cluster, verifies grants and database
policies, and verifies a new write after restoring SQL and Lance.

## Upgrade from 0.1.0, 0.1.1, 0.1.2, 0.1.3 or 0.1.4

The 0.1.5 candidate includes `sql/pgwarc_lance--0.1.0--0.1.1.sql`,
`sql/pgwarc_lance--0.1.1--0.1.2.sql`, `sql/pgwarc_lance--0.1.2--0.1.3.sql`,
`sql/pgwarc_lance--0.1.3--0.1.4.sql` and `sql/pgwarc_lance--0.1.4--0.1.5.sql`. Stop writers,
back up SQL and Lance together, install the new shared library/control/install
SQL/upgrade SQL, and reconnect all sessions that loaded the previous library.
For a legacy 0.1.0 installation, run `sql/pgwarc_lance-restrict-access.sql` as the extension owner **before**
`ALTER EXTENSION pgwarc_lance UPDATE TO '0.1.5';` in each database.
The first migration registers backup data and requires PUBLIC function access to be
revoked. The second adds the document posting index and a new upsert function
with PUBLIC execution revoked; existing function grants are preserved. The third, fourth and fifth
only advance versions for the dependency, executor and statement-snapshot changes; they perform no DDL or
grant changes. The chain retains data and explicit role grants. Privilege changes must be
applied outside the extension update so PostgreSQL does not record user grants
as extension defaults and omit them from later dumps. Recheck those grants and runtime
settings before resuming service. Older backups from unregistered 0.1.0 tables
may have omitted data already; upgrading cannot recover omitted backup contents.

Exercise the populated upgrade and backup path in a disposable environment with
`python3 tests/db_backup_restore.py --upgrade-from 0.1.0`. Both versions' install
SQL and the upgrade script must be installed. Also exercise
`--legacy-public-execute` to verify pre-upgrade remediation and grants after
restoring a legacy installation. Never replace the shared library
while application backends are actively using it.

## Backup and restore procedure

PostgreSQL backups do not contain Lance files. Treat the SQL dump and the entire
dataset directories as one backup set, including their version manifests and
data files. The extension registers its four SQL tables and write-log sequence
with `pg_extension_config_dump`; ordinary `pg_dump` includes their contents.

1. Stop new imports and all SQL/external Lance writers. Wait for active write
   transactions and file operations to finish. Keep writers stopped until both
   snapshots are complete.
2. Record the extension version, PostgreSQL major, dataset URIs and
   `lance_dataset_versions()` output. Save roles/grants separately for a new
   cluster (for example `pg_dumpall --roles-only`, stored securely). Record
   database/role settings, dataset-root OS ownership and managed credentials.
   [PostgreSQL documents](https://www.postgresql.org/docs/16/app-pg-dumpall.html)
   that a single-database `pg_dump` omits global roles. With
   `--no-role-passwords`, restore credentials separately before enabling login.
   Resolve existing bootstrap roles explicitly rather than ignoring all restore errors.
3. Dump the database with `pg_dump` and copy or snapshot each complete Lance
   directory. Do not run dataset cleanup/vacuum during the copy. Record checksums
   for the backup files and keep the SQL dump and filesystem snapshot together.
4. In an isolated recovery instance, install the same extension artifact, restore
   the required roles, restore the SQL dump, and restore Lance directories with
   PostgreSQL OS ownership and permissions. Reapply recorded URI, scan and
   destructive-operation policies on new connections. Use the original URIs, or explicitly
   update every application/configuration reference when relocating them; the
   historical write log continues to contain the original URIs.
5. Verify SQL row counts, sequence state, BM25 results, Lance row counts and
   versions, backup file hashes, representative vector/hybrid results, and
   reader/writer grants. Exercise a controlled write to verify restored file ownership.
   Enable application traffic only after these checks pass.

The automated fixture exercises a quiescent backup. It does not establish
consistency for snapshots taken while writers are running. A successful SQL
dump alone is insufficient to recover the vector store.

## Transaction and storage contract

Lance commits are separate from PostgreSQL commits. A SQL rollback, statement
cancellation, backend termination or a subsequent SQL error can leave a newer
Lance version while the SQL transaction rolls back. Until coordinated recovery
has been exercised on representative data, this behavior remains a release
gate, not an atomicity guarantee.

The SQL write log is inserted before file mutation so a permission or sequence
error cannot first mutate the dataset. A rolled-back SQL log entry disappears;
therefore absence of a log entry does not prove absence of a Lance write.
Restore to a known dataset version only after stopping all writers and verifying
the PostgreSQL snapshot to which it corresponds. External Lance writers are not
covered by PostgreSQL advisory locks.

Use an existing local dataset directory for `pgwarc_lance.allowed_uri_prefix`.
Only trusted OS users may change that directory and its parents. Apply a positive
administrator-controlled `max_scan_rows` limit and disable `allow_destructive_ops`
when ordinary workloads do not need overwrite/restore. Grant only individual
functions and required table/sequence privileges to trusted roles.

The importer CLI spools records, duplicate IDs, embeddings and generated SQL to
disk before executing any SQL. Memory depends on record size, row/byte batch
limits, vector dimension and generated posting/SQL overhead rather than the
whole corpus. `--batch-max-bytes` defaults to a 16 MiB input weight; one record
may exceed it up to `--max-record-bytes`. It is not an exact RSS ceiling.
Temporary disk space is proportional to inputs/vectors/SQL. External embedding
commands retain their own memory contract, with a default 600-second timeout.
The library list/string APIs remain intended for small inputs.

Measure importer resource use with
`python3 tests/profile_import_memory.py --output-dir /tmp/pgwarc-import-profile`
on Linux. Its default synthetic fixture grows from 1,000 to 10,000 8 KiB documents
with 100-row batches; thresholds are 256 MiB peak RSS and 32 MiB RSS growth.
This does not establish performance or memory limits for a representative corpus.

## Verified on 2026-10-01

The initial hardening validation used a disposable Linux container, PostgreSQL
16.15, Rust 1.96.0, pgrx 0.19.1 and the locked dependency versions.

- Initial Python harness tests: 408 passed; filesystem policy tests: 5 passed.
- Documentation, formatting and whitespace checks passed.
- macOS `cargo check --locked --features pg16` passed.
- Linux development-profile build and actual extension installation passed.
- PostgreSQL regression suites `setup`, `functions`, `search`, `security` passed;
  SQL smoke passed.
- Runtime safety checks passed, including SQL-literal round trips for all three
  importer modes and an attempted pre-load GUC override by an untrusted role.
- Extracted development artifacts passed installation in a fresh `postgres:16.6`
  container; LICENSE and NOTICE are included with the extracted artifacts.

The 0.1.1 candidate additionally passed:

- Linux optimized release build/install, with the Cargo.lock checksum unchanged.
- 415 Python tests and the four PostgreSQL regression suites on a new database;
  importer DB integration, SQL smoke and all 13 runtime safety checks.
- All nine fresh-install SQL/Lance backup/restore checks and all 15 populated
  upgrade/legacy ACL remediation/backup checks. The old baseline is the locally
  validated 0.1.0 source preview, not a published release.
- Extracted release artifact fresh-install and old/new-binary upgrade smoke in
  new `postgres:16.6` containers. The artifact includes the migration/remediation
  SQL, LICENSE and NOTICE; complete transitive binary notices still require review.
- Synthetic CLI resource profile: 1,000 to 10,000 8 KiB records, 100-row batches;
  peak RSS 32,744 to 33,144 KiB (growth 400 KiB).
- Synthetic end-to-end import: 10,000 8 KiB records and 384-dimensional placeholder
  vectors, 500-row batches; 45.19 seconds, importer peak RSS 92,428 KiB. BM25,
  WARC metadata and Lance row counts all matched 10,000.
- Synthetic hybrid query load on that dataset: 4 clients, 400 transactions,
  2-second per-statement timeout, exactly 10 hits checked each time; 400/400
  succeeded, average latency 63.213 ms and 59.77 transactions/second. The
  validation container had a 4-CPU/16-GiB limit. These are one-run measurements,
  not a representative workload or an agreed production latency target.

The 0.1.2 candidate additionally passed:

- 422 Python tests, macOS locked compilation, Linux optimized release build,
  four PostgreSQL regression suites, importer integration and all 17 runtime checks.
- All 18 ingestion and least-privilege checks: repeated CLI imports, vector/label replacement,
  invalid input before mutation, concurrent writes, forced backend termination
  after an external write, retry without duplicate vectors, and rejecting legacy
  duplicate IDs before mutation.
- Populated 0.1.0→0.1.2 (including legacy PUBLIC ACL remediation) and 0.1.1→0.1.2
  upgrade/backup/restore; extracted artifact fresh install and populated old/new
  binary 0.1.1→0.1.2 upgrade in fresh PostgreSQL containers.
- SciFact native-function setup and 50 hybrid queries completed in 25.65 seconds
  with a 4-CPU/16-GiB PostgreSQL container and a separate host evaluation client. This includes fixture loading and queries,
  not pure import latency. The previous setup exceeded its 120-second timeout.
  A document-ID posting index changes the 613,002-row posting DELETE plan from
  a sequential scan to an index scan.

Operational corpus quality, workload-specific scale/tail latency, complete binary
notices, incident drills and other PostgreSQL majors remain open. No unconditional
production-readiness claim is made.

## Idempotent ingestion

Create the dataset once with `lance_create_table(uri, dim, false)` before enabling
writers. For all subsequent imports, use `--skip-schema --lance-mode upsert --lance-uri <uri>`
with the same embeddings and document-ID rules. The mode requires an existing
dataset and a batch size at most 10,000. Matching IDs replace vectors/labels;
new IDs append. Existing duplicate rows for an affected ID cause a rejection
before mutation. Reconcile legacy duplicate datasets under an operator-controlled
backup/rebuild procedure before switching writers to upsert.

The import commits batches separately. SQL metadata/BM25 and Lance commits are
still separate. After a failed batch or disconnected writer, stop conflicting
writers and retry the same input in upsert mode, then compare metadata/BM25 IDs
and Lance contents. The automated test explicitly observes an external write
surviving SQL rollback and verifies that retry repairs the fixture without
adding duplicate IDs. Changed content/identity can produce a different importer
ID and is a new document; upsert is not a deletion or archival version policy.

## Reproduce the public retrieval baseline

This optional CPU benchmark uses [BEIR SciFact](https://huggingface.co/datasets/BeIR/scifact)
(CC-BY-SA-4.0) and [all-MiniLM-L6-v2](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2)
(Apache-2.0). Sources and attribution are saved in `assets.json` and the fixture
spec. Keep them with derived artifacts. Models/corpus are downloaded outside Git.
A separate Python environment avoids adding model dependencies to the extension.
The lock records the tested Linux/Python environment; package availability for
other platforms must be verified separately.

```bash
python3 -m venv /tmp/pgwarc-quality-venv
/tmp/pgwarc-quality-venv/bin/pip install -r tools/requirements-scifact.lock
/tmp/pgwarc-quality-venv/bin/python tools/download_scifact_onnx_assets.py --output-dir /tmp/pgwarc-quality-assets
/tmp/pgwarc-quality-venv/bin/python tools/make_scifact_onnx_fixture.py --asset-dir /tmp/pgwarc-quality-assets --output-dir /tmp/pgwarc-quality-baseline
```

Use a **new disposable database**, install 0.1.4 and configure a trusted writable
local dataset root. Set `PSQL_COMMAND` to its administrative connection and
`LANCE_URI` to a new dataset below that root. The following evaluation drops and
recreates extension tables in that database:

```bash
/tmp/pgwarc-quality-venv/bin/python tools/eval_quality.py --fixture-json /tmp/pgwarc-quality-baseline/fixture.json --psql "$PSQL_COMMAND" --lance-uri "$LANCE_URI" --k 10 --setup-timeout-seconds 120 --query-timeout-seconds 5 --query-statement-timeout-seconds 2 --json-output /tmp/pgwarc-quality-baseline/quality.json --markdown-output /tmp/pgwarc-quality-baseline/quality.md
/tmp/pgwarc-quality-venv/bin/python tools/validate_scifact_baseline.py --baseline-dir /tmp/pgwarc-quality-baseline --psql-command "$PSQL_COMMAND" --lance-uri "$LANCE_URI"
```

The model revision and asset SHA-256 values are pinned. Before retrieval,
`baseline-spec.json` fixes the first 50 test-qrel query IDs in numeric order,
k=10, minimum hit-rate 0.60 and MRR 0.40. Pooling is attention-masked mean followed
by L2 normalization, with 256 word-piece truncation and 384 dimensions. No hash
vectors or labels inferred from query text are used. Observed hit@10 is 48/50
(0.96) and MRR@10 is 0.7876667. Every query's vector result also passed an
independent NumPy exact float4 squared-L2 check with serialization matched and
1e-5 tolerance for numerical error/ties.

SciFact is a scientific abstract corpus, not WARC or Korean operational data.
Model training overlap with the benchmark was not audited. These results form
a public regression baseline and do not establish domain quality, zero-shot
generalization, representative latency or operational scale.

See [OPERATIONS.md](OPERATIONS.md) for tested reader/writer grants, DDL-free ingestion, monitoring and incident response.

Dependency review now records 437 Linux PG16 normal/build packages with pinned
upstream text overrides. Two primary texts remain unresolved; see
[THIRD_PARTY.md](THIRD_PARTY.md). This is an open binary redistribution gate.

## Public fixed-corpus query load

`tools/bench_quality_load.py` runs read-only hybrid queries on an already
populated, validated fixture database. It fixes the random seed, applies a
2-second server statement timeout, checks exactly k results per transaction,
logs every transaction, refuses failed/missing log entries and requires every
fixture query to be exercised. It performs no dataset/schema initialization.

```bash
python3 tools/bench_quality_load.py --fixture-json /tmp/pgwarc-quality-baseline/fixture.json --lance-uri "$LANCE_URI" --database pgwarc_quality_test --host /var/run/postgresql --user pgwarc_operator --output-dir /tmp/pgwarc-quality-load-c4 --clients 4 --transactions 200
```

The output directory must be new. Retain its query scripts, pgbench stdout/stderr,
transaction logs and `load.json`. Reported percentiles use nearest-rank logged
transaction duration, including BEGIN/SET/query/COMMIT overhead. They are not
query-only duration or measurements under concurrent ingestion. Measure the
operational corpus, cold caches and expected concurrency separately before
agreeing on an SLA.

The final 0.1.2 binary also passed 10 active-write cancellation checks. The test
observes the uniquely tagged upsert holding its dataset advisory lock, requests
server cancellation, confirms SQLSTATE 57014 and backend reuse, and retries
10,000 384-dimensional vectors. After retry all 10,000 IDs are unique, every
label is current and only the successful SQL write log is committed. A further
2-second observation found no late version/label changes. In this run the
cancelled write did not commit a Lance version; separate rollback/termination
fixtures cover an external commit surviving SQL rollback. These observations
are not a proof for every filesystem/task schedule or a SQL/Lance atomicity guarantee.

Final public fixed-corpus load (PostgreSQL 4 CPUs / 16 GiB, warm caches,
`hybrid_warc_search`, 50 query scripts, 2-second statement timeout, no writers):

| Clients | Successful transactions | P50 ms | P95 ms | P99 ms | Max ms |
|---|---:|---:|---:|---:|---:|
| 1 | 400/400 | 27.770 | 38.256 | 42.987 | 45.988 |
| 4 | 800/800 | 33.418 | 54.890 | 72.293 | 102.661 |
| 8 | 800/800 | 62.659 | 104.503 | 128.915 | 175.228 |

These one-run percentiles describe this public regression corpus, not a
representative operating corpus or an agreed service-level objective.

## 0.1.3 security patch validation (2026-10-01)

The 0.1.2 candidate was subsequently found to contain three known vulnerable
Cargo dependencies, an unsound dependency and a yanked release. Its archived
artifact is retained for reproducibility and must not be promoted to production.
The 0.1.3 dependency patches and audit policy are recorded in [SECURITY.md](SECURITY.md).
A current `cargo-audit` 0.22.2 run (1,277 advisories, DB revision
`9b3a3b73a7f42606494c943e95f8196e9994df46`) found zero known vulnerabilities,
unsound or yanked packages. Three maintenance warnings remain visible; the
license bundle still requires the review described in [THIRD_PARTY.md](THIRD_PARTY.md).

The locked Linux PG16 optimized build passed, with Cargo.lock unchanged.
Python tests (422), SQL regressions (4), importer and SQL smoke tests passed.
Runtime safety (17), ingestion replay/least privilege (18), active write
cancellation (10), storage faults (20), legacy 0.1.0 upgrade/backup (15) and
0.1.2 upgrade/backup (13) passed. A populated old-binary 0.1.2 → new-binary
0.1.3 upgrade passed in a separate PostgreSQL container.

`tests/db_storage_faults.py` installs the extracted artifact in a new container
with no network or published ports. It uses an isolated 8 MiB tmpfs to observe
actual OS permission and ENOSPC errors, then verifies rolled-back SQL/logs,
readable previous Lance contents, reuse of the same backend after ENOSPC and
successful retry after freeing space. It also observes a committed Lance
upsert inside an open SQL transaction before SIGKILL, confirms exit code 137
and PostgreSQL automatic crash recovery, then verifies that the SQL changes
were rolled back while the external version remained. Replaying the same ID
restores agreement without duplicate rows. This tests process/container crash
recovery; it does not simulate host power loss or certify filesystem durability.
The fixture and its anonymous volumes are removed after testing.

The unchanged public SciFact fixture (5,183 documents, 384 dimensions, 50
preselected queries) again achieved hit@10=0.96 and MRR@10=0.7877. All 50 exact
NumPy L2 comparisons passed. Native BM25 fixture setup plus queries took
27.24 seconds; the host evaluation client peaked at 286,024 KiB RSS (whole
fixture in memory), with the PG backend in the 4-CPU/16-GiB validation container.
This client measurement is not the disk-spooled importer's memory bound.

Warm `hybrid_warc_search` transaction latency, fixed k=10 corpus, no concurrent
writers, 2-second statement timeout:

| Clients | Transactions | p50 ms | p95 ms | p99 ms | max ms |
| --- | --- | --- | --- | --- | --- |
| 1 | 400 | 27.332 | 37.261 | 40.283 | 44.577 |
| 4 | 800 | 31.287 | 45.945 | 55.431 | 124.212 |
| 8 | 800 | 59.189 | 98.958 | 140.826 | 200.259 |

All transactions succeeded and all 50 query scripts were exercised in every
profile. A security-patch regression limit of 1.5× the 0.1.2 p95/p99 was recorded
before measuring; every profile passed. These are single-run regression
measurements, not an operational SLA. Representative WARC quality, operational
scale/targets, BM25 intermediate-memory bounds, native/license review and
host/storage durability drills remain open.

## 0.1.4 search resource validation (2026-10-01)

BM25 now aggregates postings in the PostgreSQL executor and returns only the
requested top-k rows to Rust. Search queries are limited to 65,536 UTF-8 bytes
and result counts to 10,000. The executor can spill to disk under `work_mem`;
`temp_file_limit` and `statement_timeout` are operating controls, not a global
backend RSS guarantee. See [OPERATIONS.md](OPERATIONS.md) for role settings.

The optimized Linux PG16 build, unchanged lockfile check, 422 Python tests,
four SQL regression cases, importer and SQL smoke passed. Database fixtures
passed 114 checks: runtime safety 17, replay/least privilege 18, cancellation 10,
storage faults 20, search memory 21, legacy 0.1.0 upgrade/backup 15 and 0.1.3
upgrade/backup 13. The populated old-binary 0.1.3 → new-binary 0.1.4 upgrade and
storage fault fixture passed using `postgres:16.15` (image digest
`sha256:1a6ab3f5345eb6dbe04a1349529caabdb0ab09293a09590fad07b2246bfa4b54`).

The search resource fixture independently checks BM25 scores and ranks for six
queries, including duplicate terms, absent terms and empty documents. With
100,000 documents and 800,000 postings, the native BM25 function returns ten
correctly ordered rows under 64 KiB work_mem. PostgreSQL temporary-file activity
increased by 65,174,556 bytes and the observed backend VmHWM was 191,392 KiB
(about 187 MiB). A 10 ms statement timeout and 1 MiB temporary-file quota
produced SQLSTATE 57014 and 53400 respectively; the same backend remained
usable after each error. These fixed-fixture observations do not establish
universal memory bounds or cancellation behavior at every execution phase.

The unchanged public SciFact baseline again passed hit@10=0.96,
MRR@10=0.7876667 and all 50 independent exact NumPy L2 comparisons. Native
fixture setup plus queries took 30.19 seconds; the host evaluation client
peaked at 286,020 KiB RSS. This is not the disk-spooled importer's memory bound.

Warm hybrid query transaction latency (same public corpus, 4 CPUs / 16 GiB,
k=10, 50 scripts, two-second statement timeout, no concurrent writers):

| Clients | Transactions | p50 ms | p95 ms | p99 ms | max ms |
| --- | --- | --- | --- | --- | --- |
| 1 | 400 | 33.189 | 43.303 | 48.762 | 61.458 |
| 4 | 800 | 39.196 | 55.770 | 75.421 | 106.844 |
| 8 | 800 | 76.202 | 117.991 | 148.712 | 197.074 |

All 2,000 transactions succeeded and all 50 scripts ran in every profile.
The predeclared 1.5× 0.1.3 p95/p99 regression limit passed. The executor change
increased measured latency: p95 ratios are 1.162/1.214/1.192 and p99 ratios
1.211/1.361/1.056 for 1/4/8 clients. The independent vector check was launched
concurrently with this load sequence and may overlap its first profile. These
are retained single-run regression observations, not an operational SLA.

The current dependency audit still reports zero known vulnerabilities, unsound
or yanked packages, with three maintenance warnings. Two missing primary
license texts and native distribution review remain open. Production approval
also requires representative operational WARC quality, expected scale and SLA,
resource settings under that workload and host/storage durability drills.

## 0.1.5 BM25 statement snapshot validation (2026-10-01)

The prior implementation read document count, average length and postings in
separate SPI statements. After an earlier write assigns a transaction ID, pgrx
uses writable SPI snapshots and concurrent commits can be seen between those
statements. A native 0.1.4 fixture atomically alternated 1,000/4,000 documents
in 120 transactions while issuing 400 searches. It returned scores of
-1.38541985 and 1.38604445 where valid complete-state scores were about
0.0004996253 or 0.0001249766. The failing pre-fix evidence is retained.

0.1.5 reads all statistics and postings in one SQL statement snapshot. The same
fixture passed all 400 searches, observed both committed states (250/150
results), saw the reader's own prior writes and handled empty/zero-length
corpora. This is a SQL scoring consistency guarantee; SQL and Lance commits
remain separate and require the documented replay/recovery procedure.

The new default plan uses ordered index scans and incremental sorting on the
100,000-document/800,000-posting resource fixture, without temporary-file I/O.
The initial test incorrectly required spill from every default plan and failed;
that attempt is retained separately. The corrected fixture checks exact top-k
IDs in the default plan, then disables nested-loop and merge joins in a separate
stress phase to exercise hash-join spill and temporary-file quotas. No planner
flags are changed for the public quality or load runs.

The stress phase under 64 KiB work_mem recorded 78,003,228 temporary-file bytes
and an observed backend VmHWM of 209,700 KiB (about 205 MiB). Statement timeout
(default planner) and temporary-file quota (forced hash plan) errors left their
backends reusable. These observations describe specific plans and fixtures;
they do not establish a global RSS bound.

The original public load's single-client p95 was 101.424 ms, exceeding the
unchanged 1.5× historical 0.1.4 limit. The first profile finished immediately
before automatic vacuum/analyze. A controlled restored clone reproduced median
native query time of 92.9985 ms without statistics, 41.717 ms after ANALYZE and
35.733 ms after VACUUM ANALYZE, with identical rows/Lance URI and no planner
flag changes. This identifies a missing fixture preparation step; it does not
remove the operating requirement to refresh statistics after large imports.

`eval_quality.py` now completes VACUUM ANALYZE after rebuilding its disposable
fixture in all three loading modes. Both bulk and copy mode native smoke
checks passed and recorded column statistics. It also enforces pipe-separated
tuple output for custom psql commands, verified natively without `-At`.
The initial missing-deadline and formatted-output command failures, the original
unprepared load and the original default-plan spill-assumption failure remain
in separate attempt directories. They are not counted as passing evidence.

Final 0.1.5 validation passed: locked macOS check and Linux optimized install
with unchanged Cargo.lock, 422 Python tests, four SQL regressions, importer and
SQL smoke; 122 database fixture checks (runtime 17, replay 18, cancellation 10,
search memory 22, statement snapshots 7, storage faults 20, legacy upgrade/backup
15 and 0.1.4 upgrade/backup 13). A populated old-binary 0.1.4 → new-binary 0.1.5
upgrade passed in a fresh PostgreSQL 16.15 container. Storage failures used that
same image version and the previously recorded digest.

The final prepared SciFact fixture again passed hit@10=0.96,
MRR@10=0.7876667 and all 50 independent NumPy L2 checks. Setup (including
maintenance) plus queries took 25.71 seconds; host evaluation-client peak RSS
was 286,132 KiB. The DB backend remained in the 4-CPU/16-GiB container.

Final prepared warm hybrid transaction latency, 50 scripts, no concurrent
writers, k=10, two-second statement timeout:

| Clients | Transactions | p50 ms | p95 ms | p99 ms | max ms |
| --- | --- | --- | --- | --- | --- |
| 1 | 400 | 36.477 | 45.710 | 50.298 | 60.415 |
| 4 | 800 | 44.101 | 61.411 | 85.474 | 108.574 |
| 8 | 800 | 81.764 | 136.264 | 161.371 | 194.791 |

All 2,000 transactions succeeded and every script was exercised in each
profile. The original 1.5× p95/p99 limit passed after fixing the preparation
protocol, without changing its thresholds. The historical 0.1.4 run did not
explicitly prepare statistics, so this qualified comparison is not a pure
binary-only performance measurement or an operational SLA. The original
unprepared failure remains visible.

Current RustSec results remain zero known vulnerabilities/unsound/yanked,
with three maintenance warnings. An ELF inventory records four dynamic
library dependencies (Debian libc6 and libgcc-s1) and copyright hashes in the
validation image; static native/compiler coverage and redistribution review
remain open. The same two missing primary Cargo license texts remain open.
Representative operating WARC quality, operating scale/SLA/resource settings
and actual host/storage durability drills still prevent production approval.

## 0.1.5 file corruption and clean-instance recovery validation (2026-10-01)

The unchanged 0.1.5 release binary passed 36 additional checks in two newly
initialized PostgreSQL 16.15 containers (two CPUs, 2 GiB each, no network,
published ports or host data mounts). This extends the existing release evidence;
it does not replace the original versioned archive or its manifest.

Only generated copies were damaged: the latest manifest was truncated to seven
bytes; a data object was removed, then restored and truncated to seven bytes.
Manifest/count, scan, vector and hybrid reads returned `XX000` where required.
All six failing sessions kept the same backend PID and subsequently executed SQL.
The result output checks exclude partial search rows. SQL data/write-log contents,
the healthy source and the stopped-writer backup stayed unchanged.
`lance_count` still returned three for a copy whose data file was missing, because
it could obtain the count from the manifest; row count is not a file integrity test.

A fresh second cluster initially lacked the generated NOLOGIN reader. It received
an independent `pg_dumpall --roles-only --no-role-passwords` backup, omitting only
the verified preexisting bootstrap role's exact `CREATE ROLE postgres` statement.
The SQL dump and complete Lance tree were restored, file ownership and recorded
URI/scan/destructive policies applied, and binary/file hashes checked. Role
attributes, all extension function/table/sequence grants, PUBLIC revocation,
SQL tables, sequence state, BM25 and hybrid results, IDs/vectors/labels and versions
matched. A subsequent BM25/upsert write and reader query succeeded, generated
one new Lance version and left the original backup untouched.

This is a quiescent fixture with a generated reader and three documents. It does
not certify live-writer snapshots, actual storage durability, production
credentials, representative data recovery time or operator recovery objectives.


## Explicit HTTP charset corrections

The importer accepts the HTTP labels `windows-874` and `windows-31j` on Python
versions whose codec registry lacks those aliases, using `cp874` and `cp932`.
This small compatibility map is not a complete browser encoding implementation.
See the [WHATWG encoding labels](https://encoding.spec.whatwg.org/) and
[Python codecs](https://docs.python.org/3/library/codecs.html).

Unknown labels still fail before SQL execution. For a reviewed source with a
malformed declaration, an operator may explicitly specify, for example,
`--charset-override None=utf-8`. These corrections
use strict decoding: invalid bytes fail rather than being silently replaced.
The JSON summary preserves the normalized mapping and successfully decoded
record counts for each override. Corrections are corpus-specific decisions;
do not apply this example to arbitrary archives without reviewing their bytes.
PDF, office, archive and other binary payloads still require separate extraction;
byte-to-text conversion alone does not establish usable document text.


## Importer preflight for oversized terms

The CLI now rejects ASCII alphanumeric token runs longer than 2,000 bytes before
executing SQL, in all three BM25 import modes. This is a conservative limit for
the term/doc_id B-tree on standard 8 KiB PostgreSQL pages; custom page sizes need
separate validation. CJK bigrams are unaffected. The guard does not truncate text
or silently drop postings. A malformed late record fails while the complete
input is still being spooled, so earlier batches are not executed.
Direct SQL indexing APIs can still reach PostgreSQL's own index-row-size error;
the Python guard is not a change to the extension's tokenizer or database format.
