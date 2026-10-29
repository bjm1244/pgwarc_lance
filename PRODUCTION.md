# Production readiness

This is still a source preview. Passing unit tests is not a production approval.
The current source candidate is 0.1.23. It adds revision-checked deletion,
tombstones and an ordered Lance delete event to the transactionally stored
vector outbox. PostgreSQL and Lance do not share an atomic commit. The locked
PG16.15 candidate passed fresh installation, four SQL regressions, 143 outbox
lifecycle checks, 18 importer database checks, 18 runtime-safety checks, a
populated 0.1.22-to-0.1.23 upgrade and 15 backup/restore checks. Strict Cargo
notices, a matching CycloneDX SBOM and current RustSec audit are recorded for
the validation artifact. See [PACKAGING.md](PACKAGING.md),
[PUBLICATION.md](PUBLICATION.md) and [THIRD_PARTY.md](THIRD_PARTY.md) for the
verified scope and remaining gaps. Package verification and notice collection
do not grant binary redistribution or production approval.
Install the complete consecutive upgrade chain and reconnect existing backends.
See [PUBLICATION.md](PUBLICATION.md) for current evidence and limitations.
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
| Outbox consistency | Verify committed-only delivery, duplicate replay, per-URI ordering, failure blocking, event version/lag reporting and pending-queue backup recovery. |
| Release and upgrade | PG16 candidate has a versioned artifact, checksum-bound Cargo/native/compiler notices and SBOM, fresh install, and populated upgrade evidence. Binary redistribution and production approval remain separate decisions. Test an actual upgrade whenever a supported previous version exists; reconnect sessions after replacing the shared library. |
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
extension configuration tables/sequences, pending outbox payload restoration,
BM25 results, Lance contents and versions, and selective function grants after
restoring within the same cluster.
The extracted-artifact corruption test creates two separate PostgreSQL containers
with no network, published ports or host data mounts. It restores role definitions
from a separate backup into the second cluster, verifies grants and database
policies, and verifies a new write after restoring SQL and Lance.

## Upgrade from 0.1.0 through 0.1.10

The 0.1.11 candidate includes `sql/pgwarc_lance--0.1.0--0.1.1.sql`,
`sql/pgwarc_lance--0.1.1--0.1.2.sql`, `sql/pgwarc_lance--0.1.2--0.1.3.sql`,
`sql/pgwarc_lance--0.1.3--0.1.4.sql` through `sql/pgwarc_lance--0.1.10--0.1.11.sql`. Stop writers,
back up SQL and Lance together, install the new shared library/control/install
SQL/upgrade SQL, and reconnect all sessions that loaded the previous library.
For a legacy 0.1.0 installation, run `sql/pgwarc_lance-restrict-access.sql` as the extension owner **before**
`ALTER EXTENSION pgwarc_lance UPDATE TO '0.1.11';` in each database.
The first migration registers backup data and requires PUBLIC function access to be
revoked. The second adds the document posting index and a new upsert function
with PUBLIC execution revoked; existing function grants are preserved. The third, fourth and fifth
only advance versions for the dependency, executor and statement-snapshot changes; they perform no DDL or
grant changes. The 0.1.10→0.1.11 update replaces the `bm25_doc` primary-key index
and rebuilds the extension-owned foreign key as validated. The chain retains
data and explicit role grants. Privilege changes must be
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

The 0.1.11 distribution includes consecutive upgrade scripts through
0.1.10→0.1.11. The latest populated check upgraded a prepared PostgreSQL 16.15
Common Crawl clone with 21,873 documents, 8,240,889 postings and 21,873 WARC
rows in 1.23 seconds; it preserved all rows and a validated foreign key. This
does not revalidate every legacy starting version or production lock behavior.

## 0.1.10 BM25 corpus-statistics index candidate

The candidate adds `bm25_doc(doc_len)` to support the `count(*)` and `avg(doc_len)`
aggregate in BM25 search. The preceding Common Crawl profile had 21,873 documents;
that statistics CTE used a sequential scan touching 1,598 shared blocks, with no
temporary reads or writes. A synthetic fixture with 21,873 rows and about 512
bytes of high-entropy content per row occupied 12 MiB; the new index occupied
184 KiB. On that fixture, the baseline sequential scan touched 1,563 shared
blocks and took 4.052 ms for the aggregate. Immediately after insert, PostgreSQL
still chose a sequential scan. After VACUUM marked the table pages all-visible,
the aggregate used an index-only scan, touched 23 shared blocks, had zero heap
fetches, and took 2.342 ms. Both plans reported zero read and temporary blocks.
The ordered BM25 result rows before and after had the same SHA-256. In three
warm repetitions of 1,000 aggregate calls, median backend CPU from `/proc` ticks
was 2.68 s before versus 1.17 s after; median observed wall time was 2.838 s
versus 1.206 s. These fixture measurements include loop overhead and are not
search request latency.

This isolates the aggregate plan; its measurements are not request latency. A
follow-up compared 0.1.9 and 0.1.10 on separate PostgreSQL 16.15 copies of the
prepared Common Crawl corpus (21,873 documents, 8,240,889 postings, 21,873
metadata rows and 21,825 vectors). The saved source WARC checksum was
`4b6b55dc25afdb8ef9bb0e48213faa73cf07756f195b0863ba2693638bf784f8`. The run
copied the loaded validation database and Lance dataset; it did not reimport raw
WARC. Both versions used eight clients, eight warmups per client, 128 requests
per client, `k=5` and the same eight-query mix. Each mode completed 1,024 requests
without errors, and all eight per-query BM25/hybrid result hashes matched.

After `VACUUM (ANALYZE)`, the median of three warm runs per version was:

| Measure | 0.1.9 | 0.1.10 | Change |
| --- | ---: | ---: | ---: |
| BM25 p95 (ms) | 92.85 | 90.81 | −2.2% |
| BM25 backend CPU (ms/request) | 11.51 | 10.15 | −11.9% |
| Hybrid p95 (ms) | 234.63 | 230.77 | −1.6% |
| Hybrid backend CPU (ms/request) | 52.14 | 50.57 | −3.0% |

For the simple `privacy` query, the statistics scan changed from a 1,598-hit
sequential scan to a 30-hit index-only scan with zero heap fetches. A second
document scan remains for a multi-term plan. Before VACUUM, the candidate still
used a sequential scan and CPU/p95 were effectively unchanged. The index occupied
240 KiB. The warm plans reported zero shared reads and temporary blocks; physical
device I/O and cold-cache latency were not measured. The p95 changes are modest,
the host was shared, and these values are not an SLA.

Import throughput, index write overhead, autovacuum timing, fresh raw-WARC
ingestion, independent qrels and sustained load remain unmeasured. On PostgreSQL
16.6, the populated 0.1.9→0.1.10 upgrade preserved the BM25, WARC and Lance
fixture rows and produced a valid `doc_len` index. A fresh artifact install also
passed on PG16.6. This candidate remains outside production approval.

## 0.1.11 BM25 document covering primary-key candidate

The 0.1.11 candidate adds `doc_len` as an INCLUDE column on `bm25_doc_pkey`,
while retaining the smaller `bm25_doc(doc_len)` statistics index. After
`VACUUM (ANALYZE)`, a prepared Common Crawl comparison showed the rare-term
document join use an index-only scan: shared hits fell from 17,289 to 11,527
across 5,763 loops, with zero heap fetches. A multi-term document scan changed
from a 1,598-hit heap sequential scan to an 86-hit index-only scan. The
corpus-statistics scan remained an index-only scan with 30 shared hits.

Three alternating warm 8-client runs per schema on PostgreSQL 16.15 had p95
medians of 170.92→133.13 ms for BM25 and 335.63→319.22 ms for hybrid. Backend
CPU estimates were 10.33→9.27 and 47.30→46.09 ms/request. The shared host was
under substantial load, so these observations are not an SLA. A primary-key-only
pilot did not improve BM25 p95; retaining the narrow statistics index is part
of the measured candidate schema.

The 0.1.10→0.1.11 migration replaces the primary key and revalidates the
extension-owned foreign key. The prepared-clone command took 1.23 seconds, but
locks, peak disk use and larger production corpus behavior were not measured.
Index construction and foreign-key validation occur during the extension
update transaction; schedule a maintenance window, test on a full-size restore,
and keep a verified SQL plus Lance backup before using this candidate. The
package passed fresh install, the project SQL smoke, four SQL regressions and
the populated clone upgrade on PG16.15. Independent qrels, fresh raw-WARC
ingestion, write overhead, cold I/O and sustained load remain unmeasured. This
candidate is not production approved.

## Backup and restore procedure

PostgreSQL backups do not contain Lance files. Treat the SQL dump and the entire
dataset directories as one backup set, including their version manifests and
data files. Version 0.1.23 registers seven extension tables and two sequences
with `pg_extension_config_dump`; ordinary `pg_dump` includes their contents.

1. Stop new imports, all outbox workers, and all SQL/external Lance writers. Wait for active write
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
5. Verify SQL row counts, outbox state, sequence state, BM25 results, Lance row counts and
   versions, backup file hashes, representative vector/hybrid results, and
   reader/writer grants. Exercise a controlled write to verify restored file ownership.
   Enable application traffic only after these checks pass.

The automated fixture exercises a quiescent backup. It does not establish
consistency for snapshots taken while writers are running. A successful SQL
dump alone is insufficient to recover the vector store.

## Transaction and storage contract

The direct Lance functions and importer `create`, `overwrite`, `append` and
`upsert` modes mutate files separately from PostgreSQL. A SQL rollback, statement
cancellation, backend termination or a subsequent SQL error can leave a newer
Lance version while the SQL transaction rolls back. Use importer `outbox` mode
when the SQL-side queue must commit with metadata and BM25. That mode delays
vector visibility until a separate worker applies the committed event; if the
Lance upsert succeeds but the PostgreSQL acknowledgment rolls back, the event is
retried by ID. This is at-least-once delivery and eventual consistency, not a
distributed atomic commit. Unfinished events block later events for that URI and
direct writers cannot overtake them. Check completion and `applied_version`
before relying on vector read-after-write. The behavior remains a production
gate until these checks are repeated with representative workload and recovery
conditions.

The direct SQL write log is inserted before file mutation so a permission or sequence
error cannot first mutate the dataset. A rolled-back SQL log entry disappears;
therefore absence of a log entry does not prove absence of a Lance write.
Restore to a known dataset version only after stopping all writers and verifying
the PostgreSQL snapshot to which it corresponds. External Lance writers are not
covered by PostgreSQL advisory locks.

Use an existing local dataset directory for `pgwarc_lance.allowed_uri_prefix`.
Only trusted OS users may change that directory and its parents. Apply a positive
administrator-controlled `max_scan_rows` limit and disable `allow_destructive_ops`
when ordinary workloads do not need delete/overwrite/restore. Grant only individual
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

Unknown labels still fail before SQL execution unless an operator supplies a
corpus-level `--charset-override LABEL=CODEC`. Label overrides use strict
decoding: invalid bytes abort rather than being silently replaced.

For one reviewed record with a malformed label, use
`--charset-record-override '<WARC-Record-ID>=CODEC'`. The override is limited to
that exact WARC ID and uses replacement decoding; the JSON summary records the
ID, number of imported records and observed U+FFFD characters. The selected ID
must be unique and must produce an imported text record. A missing, duplicate,
filtered or conflicting target fails before SQL generation. Review the source
bytes and declarations before choosing a codec; do not generalize a record
correction to other records.

On the reviewed Common Crawl segment, response record 16,350 has an HTTP
`uft-8` label and an internal HTML UTF-8 declaration. Its 59,582-byte body is
not strict UTF-8; explicit record-scoped UTF-8 replacement yielded 65 observed
U+FFFD characters. A two-record disposable PostgreSQL 16.6 smoke imported this
record and a separate `None`-label record using the existing strict label
override, and BM25 search returned both. This verifies import plumbing only;
it does not establish the malformed record's semantic text accuracy or full
archive acceptance. Broader charset coverage remains open.

PDF, office, archive and other binary payloads still require separate
extraction; byte-to-text conversion alone does not establish usable document
text.


## 0.1.16 candidate: oversized BM25 terms

The 0.1.15 importer rejected ASCII alphanumeric tokens longer than 2,000 bytes
because they cannot safely be stored directly in a standard PostgreSQL B-tree
entry. The 0.1.16 candidate preserves those tokens: it stores a fixed-size
SHA-256 posting key in `bm25_term` and keeps the original token in
`bm25_long_term` for collision checks and stable score ordering. Function, bulk,
and COPY import modes use the same representation. Import SQL checks the
installed extension version before any schema or batch work when such a token
is present. The candidate raises the search-input limit to 2,500,000 UTF-8 bytes;
this covers the 2,298,914-character maximum observed in the audited Common Crawl
slice. It does not change tokenizer rules or truncate/drop terms.
Tokens longer than that configured searchable-input bound fail explicitly in
direct indexing calls and during the importer's pre-execution spool pass.

The long-term dictionary is included in extension configuration dumps and keeps
orphan entries after reindex/delete to avoid races with concurrent indexing.
During a writer maintenance window, operators can prune entries not referenced
by `bm25_term`; see [OPERATIONS.md](OPERATIONS.md). PDF/office extraction,
charset quality and unrelated import failures remain separate work.

The disposable PostgreSQL 16.15 validation passed the release build, fresh
install, four SQL regressions, SQL smoke, all three importer modes, populated
0.1.15-to-0.1.16 upgrade with a legacy 2,001-byte posting, backup/restore, and
least-privilege ingest replay. The upgrade reindexed the legacy posting and
preserved searchability. This is a development candidate, not production
approval; full raw-WARC acceptance, independent relevance labels, operational
charset/PDF/office extraction, sustained/cold SLA, current RustSec review, and
binary license notice review remain open.

## 0.1.6 development: Common Crawl BM25 planning (2026-10-20)

This development candidate adds the bound query-term restriction to the scoring
posting scan as well as the frequency scan. The materialized frequency CTE hides
its values from the planner; the join alone can overestimate matching postings
and trigger expensive JIT compilation. Scoring, tokenization, ordered summation,
input limits and the single-statement snapshot remain unchanged. The control
template generates version 0.1.6; the new upgrade script changes no DDL or grants.
Existing artifacts and manifests were preserved. No binary release is approved.
The private development binary SHA-256 is
`efd2dcad09ea88ffe7f2e1cf3c32d2cc55e79d720804e0c6485ec385b7b19ceb`.

In an instrumented native `privacy` query, 0.1.5 spent 336.686 ms in JIT out of
375.273 ms execution; 0.1.6 took 47.229 ms with no JIT. Recorded PostgreSQL read
time was 8.873/6.483 ms respectively. Shared-buffer reads remained, and these
figures do not measure physical-device reads or all Lance I/O. None of the eight
native diagnostic plans spilled at 4 MiB work_mem. The separate diagnostic
`jit=off` experiment supports CPU attribution; load runs retained `jit=on`
and default planner settings.

The same prepared Common Crawl corpus (21,873 documents, 8,240,889 postings,
21,825 E5 vectors of dimension 384) was copied into disposable infrastructure.
Validation-only C wrappers loaded preserved 0.1.1/0.1.5 binaries beside 0.1.6
against the same tables and copied dataset. Linux PG16.15, Rust 1.96.0,
pgrx 0.19.1, four CPUs, 16 GiB, 128 MiB shared_buffers and 4 MiB work_mem were
used. The host was shared; CPUs were not reserved.

Both runs used the original eight-query mix, k=5, eight warm-ups per client,
128 timed requests per persistent client, concurrency 1 and 8, and URL/160-character
snippet fetching. Connection and embedding time were excluded. Run A measured
0.1.1→0.1.5→0.1.6; run B reversed that order. Eight-client p95 in milliseconds:

| Native binary | BM25 A / B | Hybrid A / B |
| --- | ---: | ---: |
| 0.1.1 | 621.48 / 592.86 | 645.67 / 752.75 |
| 0.1.5 | 1195.95 / 1736.09 | 1678.72 / 1588.25 |
| 0.1.6 | 572.46 / 500.84 | 645.47 / 625.81 |

All 13,824 timed requests succeeded. These are two warm, short measurements,
with visible host variation, not an SLA or a direct comparison to historical
host timings. Concurrency 2/4, sustained load and cold acceptance were not
remeasured. Initial cold diagnostics hit the retained 30-second timeout;
subsequent diagnostic preparation used 180 seconds, while warm load retained
the original 30-second limit.

All 48 BM25/hybrid results for 24 queries retained baseline IDs and scores
within 1e-9; preserved 0.1.1 results also matched. Five independently calculated
Common Crawl BM25 references passed at 1e-9 and 24 NumPy squared-L2 references
passed at 1e-5. This verifies calculations, not labelled operational relevance.
No new qrels quality score or fresh raw-WARC ingestion is claimed.

The locked optimized build/package, artifact checks, fresh install and populated
old/new-binary 0.1.5→0.1.6 upgrade passed. Four SQL regressions, SQL smoke, importer
DB smoke and 59 selected database checks passed: runtime safety 17, search
memory 22, statement snapshots 7, upgrade/backup 13. The snapshot fixture saw
both states in 400 searches against 120 writer transactions. The forced-hash
resource fixture retained 78,003,228 temporary-file bytes under 64 KiB work_mem,
observed backend VmHWM 236,720 KiB, and reusable backends after timeout/quota
errors. This is a fixed synthetic stress observation, not a universal RSS bound.
Python tests (433), tokenizer tests, formatting and documentation checks passed.

The 0.1.6 baseline audit found nine advisories in `quinn-proto` and `rustls`.
The 0.1.7 follow-up updates both dependencies and its current RustSec gate
passes; strict binary notice collection still lacks primary license texts for
`random_word` 0.5.2 and `seahash` 4.1.0. Representative relevance, fresh input
extraction, operational SLA and host/storage durability gates also remain open.
The failed initial importer test used a non-writable validation URI; the same
fixture passed in a new database with a writable dedicated scratch URI. See
[the development log](docs/development-log.md) for scope and next work.

## 0.1.7 security follow-up (2026-10-20)

The locked Linux x86_64 PostgreSQL 16.15 build and package passed with Rust
1.96.0 and pgrx 0.19.1. The current RustSec database revision scanned 561 locked
dependencies and reported zero vulnerabilities, unsound or yanked packages;
three existing unmaintained warnings remain. Fresh extension install, the
project SQL smoke, all four `pg_regress` cases and a populated 0.1.6-to-0.1.7
upgrade passed. The upgrade preserved BM25, WARC and Lance test records. These
checks do not close the license-notice, labelled relevance, raw-input extraction,
operational SLA or durability gates, and do not approve production use or binary
redistribution. Full commands and artifact identity are in
[the development log](docs/development-log.md).

## 0.1.8 security follow-up (2026-10-21)

The latest RustSec database identified RUSTSEC-2026-0382 in `rustls-webpki`
0.103.15. Version 0.1.8 updates that locked dependency to 0.103.16. On Linux
x86_64 with PostgreSQL 16.15, Rust 1.96.0 and pgrx 0.19.1, the locked optimized
package and artifact checks passed. The current audit scanned 561 dependencies:
zero vulnerabilities, unsound packages or yanked packages, with three
unmaintained warnings. A fresh install, `tests/test.sql` and all four SQL
regressions passed on PG16.15. A separate disposable `postgres:16.6` container
passed the populated 0.1.7-to-0.1.8 artifact upgrade. Its fixture contained two
BM25 documents, two WARC rows and two Lance rows and verified their preservation.
This is PG16 development validation, not production approval.

The lockfile-triggered strict notice collection covered 440 target dependencies
and remains `needs_review` (exit 2) for missing primary texts for `random_word`
0.5.2 and `seahash` 4.1.0. Production workload/relevance, raw-WARC extraction,
other PostgreSQL majors, operational durability and binary redistribution remain
open. The exact environment, artifact hash, commands, and setup corrections are
recorded in [the development log](docs/development-log.md).

## 0.1.9 BM25 index follow-up (2026-10-21)

The 0.1.9 candidate changes `bm25_term` to use a `(term, doc_id)` primary-key
index that includes `tf`, then removes the redundant term-only index. The
covered posting scan can read all scoring columns from the index. An earlier
controlled Common Crawl run on the same code change measured BM25 8-client warm
p95 median at 286.925→136.565 ms, estimated backend CPU per request at
18.905→11.905 ms, and total BM25 index size at 766→446 MiB, with query result
hashes unchanged. The 0.1.8 source retained this search implementation, but
these are reused 0.1.6-to-0.1.7 measurements; no full-corpus benchmark was run
for 0.1.9. The hybrid p95 observation improved as well, while vector-only
latency moved, so the hybrid change is not attributed solely to the index.

The PG16.15 locked candidate package, fresh install, project SQL smoke, four SQL
regressions and populated adjacent upgrade passed. The upgrade fixture held two
BM25 documents, two WARC rows and two Lance rows. Full-corpus upgrade lock time,
peak disk use, labelled relevance, cold/sustained load and production SLA remain
unverified. See [the development log](docs/development-log.md) for the exact
commands, artifact identity and observed setup failures.

## 0.1.17 dependency security follow-up (2026-10-24)

The 0.1.16 lockfile was rechecked against RustSec database revision
`21d909e4c8bd4e14c7aea58bf980c881a0053ab1` (1,352 advisories). It identified
`ctutils` 0.4.2 and `tokio` 1.52.3; 0.1.17 locks `ctutils` 0.4.4 and `tokio`
1.53.3. The updated scan covered 561 dependencies and found zero vulnerabilities,
unsound or yanked packages. Three unmaintained warnings remain.

On Linux x86_64 with PostgreSQL 16.15, Rust 1.96.0 and pgrx 0.19.1, the locked
optimized package, artifact checks and four SQL regressions passed. A populated
0.1.16-to-0.1.17 upgrade in a disposable PostgreSQL 16 container preserved two
BM25 documents, two WARC rows, two Lance rows and the Lance dataset versions.
The migration only advances the extension version. This verifies the adjacent
upgrade path; it does not revalidate older starting versions, production load or
recovery behavior. Strict notice collection remains `needs_review` for missing
primary license texts for `random_word` 0.5.2 and `seahash` 4.1.0. Binary
redistribution and production approval remain open. See the [development log](docs/development-log.md).

## 2026-10-25 Common Crawl BM25 posting-frequency trial

On PostgreSQL 16.15, the 0.1.17 baseline and a temporary 0.1.18 posting-window candidate were run against two copies of the same Common Crawl clone. Three alternating warm rounds used eight fixed queries, clients 1 and 8, eight warmups and 128 requests per client. Eight-query BM25/hybrid result fingerprints matched exactly. At eight clients, BM25 p95/backend CPU changed from 94.63 ms/13.86 ms per request to 101.78 ms/15.04 ms; hybrid changed from 190.95 ms/46.69 ms to 192.07 ms/46.59 ms. The candidate cut shared-hit blocks roughly in half, with zero warm physical reads and temporary bytes, but its 100,000-document/800,000-posting stress run wrote 108,300,700 temporary bytes versus 78,003,228 for 0.1.17 under 64 KiB `work_mem`. Both search-memory runs passed correctness, cancellation and quota checks. The candidate was not retained. This is a warm-clone observation, not a production SLA or relevance evaluation; host load varied, cold/sustained performance is unmeasured, and independent qrels remain required. See [PUBLICATION.md](PUBLICATION.md) and [the development log](docs/development-log.md).

## 0.1.18 BM25 IDF amortization candidate (2026-10-25)

A separate 0.1.18 candidate materializes one IDF value per unique query term in
both the standard and long-token BM25 paths. On the same PostgreSQL 16.15
Common Crawl fixture (21,873 documents and 8,240,889 postings), three
alternating eight-client warm rounds used eight queries, `k=5`, eight warmups
and 128 requests per client. All result fingerprints matched exactly and no
requests failed. Median BM25 p95/backend CPU changed from 99.31 ms/13.74
ms per request on 0.1.17 to 93.75 ms/13.28 ms on the candidate; hybrid changed
from 197.04 ms/47.34 ms to 192.63 ms/46.94 ms. The six-term BM25 plan processed
25,097 posting rows while computing six IDFs in the materialized CTE; warm
single-plan execution median changed from 47.81 to 45.51 ms, with no temp
writes. The long query's load-test p95 increased 15.6% (150.99 to 174.60 ms)
while its p50 improved 5.9%; this tail result is unresolved. A fixed
100,000-document/800,000-posting search-memory regression passed independent
score/rank, spill, timeout, quota and backend-reuse checks. These are cloned,
warm workload results and mathematical-output checks; they do not establish
relevance, cold or sustained SLA, fresh raw-WARC ingest, or production
approval. See [PUBLICATION.md](PUBLICATION.md) and [the development log](docs/development-log.md).

## 0.1.19 BM25 document-frequency index candidate (2026-10-26)

The 0.1.19 candidate adds `bm25_term_frequency_idx` on `bm25_term(term)` to
accelerate the BM25 query's matching-posting count. In the Common Crawl q6
plan, the count scanned 25,097 postings; the narrow index reduced its observed
shared-hit blocks from 16,429 to 61. The total plan's shared hits fell from
33,113 to 16,745 and it wrote no temporary bytes. The additional index occupied
82 MB in that prior, already-populated 8,240,889-posting database.

Three alternating warm 8-client rounds had identical BM25 and hybrid result
fingerprints. Median BM25 p95/backend CPU was 114.32 ms/13.50 ms per request
without the index and 100.71 ms/12.99 ms with it. Hybrid changed from
221.15 ms/46.58 ms to 240.26 ms/46.43 ms. Host load varied from 1.95 to 25.02
on four CPUs, so neither p95 difference establishes a causal latency change.
The 2026-10-27 real-content fresh-load comparison replayed identical importer
SQL into separate `.18` and `.19` PG16.15 databases. Relative to `.18`, `.19`
used 21.0% more container CPU for BULK and 45.6% more for COPY; WAL grew 23.3%
and 33.3%. The newly maintained index occupied 116,023,296 bytes (110.6 MiB).
BULK elapsed time was 2.2% lower despite the CPU increase; COPY elapsed time
increased 45.4%. Host load and default autovacuum varied, so wall time is
inconclusive. The q6 count-scan read benefit is clear, but no workload mix or
write SLA is available to accept these costs. The index remains a development
candidate only; see [PUBLICATION.md](PUBLICATION.md) for input checksums and
limitations.

The locked/offline PG16 library build completed with Rust 1.96.0; the resulting
candidate `.so` SHA-256 is
`6429e334b3ce69235cf2491cfb04fdc52ef99ab4dfb3668a42fc5db8e2490fd0`, and the
artifact integrity check passed. Fresh installation and all four PG16 SQL
regressions passed. A populated 0.1.18-to-0.1.19 update took 5.870 seconds and
preserved 21,873 documents, 8,240,889 postings and 21,873 WARC rows. The new
index was valid, ready, non-unique and keyed only by `term`; all BM25 and hybrid
query fingerprints matched on the same 0.1.18/0.1.19 corpus.

At that historical 0.1.19 run, the standard `cargo pgrx package` command could
not run because cached `cargo-pgrx` required GLIBC 2.39 while the PG16 builder
had GLIBC 2.36. The library was built through locked/offline `cargo rustc` and
the tested install SQL was assembled from the previous generated SQL plus the
new index DDL. The 2026-10-28 0.1.20 verification in [PACKAGING.md](PACKAGING.md)
now covers the official package path. This candidate is not production
approved. Full raw-WARC parsing/ingestion throughput, upgrade
lock/disk cost at larger corpus size, independently labelled relevance,
cold/sustained load, and binary license notice review remain open. The
0.1.5-to-0.1.1 historical comparison was
not repeated. Do not promote the version or use it to set an SLA based on these
measurements.

## 2026-10-26 bounded PDF/DOCX importer source candidate

The importer now offers opt-in PDF extraction through an operator-supplied
Poppler `pdftotext` process and bounded DOCX WordprocessingML body extraction.
The public WARC segment used for the earlier corpus validation contains 218 PDF
and 3 DOCX responses. A read-only run with Poppler 24.02.0 extracted text from
178 PDFs; 22 yielded no text and 18 returned extractor errors. The PDF output
included nine replacement characters. DOCX extraction returned 13,689
characters across all three records with no replacement characters. Both paths
apply the 64 MiB per-record limit; PDF processes have a 30-second timeout.
Extractor failures remain explicit and the default CLI does not enable either
extractor. The full archive was not imported into PostgreSQL or Lance, and this
run does not establish semantic text quality, OCR coverage, corpus-wide charset
correctness, import throughput or retrieval relevance. Legacy Office, RTF,
EPUB and other unsupported binary records remain unsupported. The Python tests
exercise SQL generation and failure-before-execution behavior; no extension
binary, SQL schema, Cargo dependency or release artifact changed. Production
approval remains open.
