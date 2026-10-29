# Source publication review — updated 2026-10-30

## Latest source and distribution review: 0.1.23 (2026-10-30)

Issue #8 adds a revision-checked document delete API. It atomically removes WARC
metadata and BM25 rows, records an `is_deleted` tombstone and queues a durable
Lance delete. Vector and hybrid search filter tombstones and stale row revisions
as soon as the PostgreSQL transaction commits. Recreate requires a new
revision-aware outbox upsert. URI writer locks serialize importer metadata
updates, deletes and outbox delivery. The delete retry and concurrent delete /
upsert paths passed actual isolated PG16 tests.

The locked Linux x86_64 package targets PostgreSQL 16.15, Rust 1.96.0 and
cargo-pgrx 0.19.1. Artifact SHA-256 is
`42045ec4de13487786015698ad9a871f02f96aefe07eacd289d14d66a4df61ec`, Cargo.lock
SHA-256 is
`147e894f1e41a2fa9c5cddf16feb68f9346661efbc450e4b895e92bdef2863ed`, and its
native fingerprint is
`b659227e940cfe9c4f44eabb4aaf31d4fcc390c973d6ee8e34a4f7926da934a5`. Fresh
installation, all four SQL regressions, the issue #8 outbox integration (143
checks), importer database (18), runtime safety (18), storage recovery (36),
populated 0.1.22-to-0.1.23 upgrade and backup/restore (15 checks) passed on
PostgreSQL 16.15.

Strict target-filtered notices cover 436 Cargo packages with no missing primary
texts. The artifact-bound CycloneDX 1.6 SBOM contains 441 components; its SHA-256
is `520c3b3d50135a8e6f603d71804a8a9015ed9c3a5026f0ce05314a8086f49228` and the
complete notice-bundle SHA-256 is
`b1a6eea300c87fb3787ce478b797be039a463aec0cc1f654dd9761b49d9c909b`. Current
RustSec database revision `ee1a49a2769a24a596afc1c20cb33748d2769586` (1,386
advisories) found zero vulnerabilities, unsound or yanked packages; three
unmaintained warnings remain. Artifact-bound hashes and target scope are
recorded in [THIRD_PARTY.md](THIRD_PARTY.md).

Issue #8 C1-C3 are verified for this PG16 target and disposable fixtures.
PostgreSQL and Lance still lack a distributed atomic commit. Lance keeps older
dataset versions; the extension does not compact or garbage-collect them.
Restoring an old Lance version does not clear PostgreSQL tombstones, so logical
recovery must rebuild metadata, BM25, revision state and vectors from
authoritative input. No independent retrieval labels, production SLA,
multi-host Lance coordination, binary redistribution right or production
approval is established. This source preview is committed locally for the
separate reviewed fast-forward publisher; no direct push was made.

## Issue #9 — isolated backup and new-instance recovery (2026-10-30)

Using the unchanged 0.1.23 PG16 artifact, a synthetic quiescent PostgreSQL 16.15
fixture was dumped with its global no-password role definition, role settings,
database policies, seven extension configuration tables, sequences, revision
map, and a committed pending outbox payload. A second network-isolated cluster
restored the SQL and Lance snapshot. It matched the four SQL documents and
revision mappings, restored the three vectors present at the snapshot point,
replayed the fourth pending vector exactly once, preserved search results and
grants, and accepted a revision-aware write. A restore copy missing its Lance
data object failed with an internal storage error and no result rows; the
untouched backup then restored successfully.

Separate PG16.15 fixtures distinguished clean SIGTERM from container SIGKILL,
an 8 MiB isolated-tmpfs ENOSPC error, and missing or truncated Lance files.
SIGTERM preserved committed rows, revisions, retrieval and ACLs. SIGKILL
triggered PostgreSQL automatic recovery; the open SQL revision update rolled
back while a direct Lance write remained, then explicit idempotent replay
reconciled it. The transactional revisioned outbox event was absent after the
rollback and succeeded when explicitly retried. Counts, revisions, retrieval
and reader privileges were checked after recovery.

Across three repeat runs, the synthetic new-instance restore, including
startup, role/settings restore, SQL/Lance restore, verification and a
post-restore write, took 10.551–10.912 seconds. The SIGTERM restart and verified
state took 1.353–1.580 seconds; SIGKILL plus PostgreSQL recovery, explicit
reconciliation and verification took 4.471–4.642 seconds. These are measured
times for four small fixture documents and four-dimensional test vectors on
one local Docker host, not production RTOs. The quiescent snapshot had zero
observed loss of committed fixture state, including the pending outbox event.
The recovery procedure discards uncommitted SQL/outbox transactions and
requires all committed state at the last verified snapshot to survive; later
writes may be lost back to that point. No maximum time-based production loss is
set because a backup schedule is not approved. Direct PostgreSQL/Lance calls
still have no shared atomic commit.
Host power loss, active-writer snapshots, hardware/storage-stack faults,
production corpus restore, an approved RPO/RTO and production approval remain
unverified. Private command logs and checksums stay outside this repository.

## Previous package and distribution review: 0.1.22 (2026-10-29)

Issue #6 adds a URI-scoped document revision mapping, revision-bearing Lance
rows, and a read-only PG–Lance doctor. Re-imports enqueue the content revision
in PostgreSQL's transactional vector outbox; the supervised worker applies
retries idempotently and records the applied revision. The doctor compares
stable IDs and revisions and reports duplicate, missing, orphan, stale and
unfinished-outbox findings without changing either store.

The locked Linux x86_64 package was built for PostgreSQL 16.15 using Rust
1.96.0 and cargo-pgrx 0.19.1. Artifact SHA-256 is
`0e67b2c8259a70c986223f51307c7cd56534d3659d4862e69ac49a961d26e25f`, Cargo.lock
SHA-256 is
`854a1cb98e529e7c5fa089741f68d39753e586c8e9da3b12df1688c7ab94a8a9`, and the
native fingerprint is
`899c2cafbce2e705cb84a9f380619a07041b02da835b718935fb93a44dfa871c`. Fresh
installation, the SQL smoke test, all four SQL regressions, the importer and
reconciliation database run, runtime-safety checks, populated 0.1.21-to-0.1.22
upgrade and backup/restore checks passed. The isolated reconciliation run
passed 100 checks on PostgreSQL 16.6; the package artifact targets PostgreSQL
16.15.

Strict target-filtered notices cover 436 Cargo packages with no missing primary
texts. The artifact-bound CycloneDX 1.6 SBOM contains 441 components (436 Cargo
packages and five native/compiler environment components). Its SHA-256 is
`9c69075fd81fa46ec9da3729a4b4a6b36843d964b485920060f185a220d7f232`; the
complete notice-bundle SHA-256 is
`2086597c781294ef80235f125e5aec2bb0dbe62b474c91f806a44a654541ad0d`. RustSec
audit found zero vulnerabilities, unsound or yanked packages; three
unmaintained warnings remain. Full hashes and audit scope are in
[THIRD_PARTY.md](THIRD_PARTY.md).

Issue #6's C1-C3 checks passed for the recorded Linux x86_64 / PG16 build and
isolated fixtures. PostgreSQL and Lance still lack a distributed atomic
commit; reconciliation is a point-in-time observation and operators should
pause writers for a meaningful scan. The content revision does not include
embedding-model identity, and legacy rows gain a known revision only after an
upsert. No independent retrieval labels, production SLA, multi-host Lance
coordination, full raw-WARC re-import, binary redistribution right or
production approval is established. Public source publication remains a
separate reviewed fast-forward operation.

## Historical 0.1.21 PG16 package checkpoint (2026-10-28)

Issue #4's package notice and RustSec criteria were rechecked against the
corrected 0.1.21 Linux x86_64 / PostgreSQL 16.15 artifact. Strict collection
found primary texts for all 436 reachable Cargo packages. The CycloneDX 1.6
SBOM binds the 0.1.21 `.so`, Cargo.lock, native fingerprint and full notice
bundle. The current audit found zero vulnerabilities, unsound or yanked
packages and retained three unmaintained warnings. Artifact fresh install,
four SQL regressions, importer DB, runtime safety, populated 0.1.20-to-0.1.21
upgrade and backup/restore passed. Hashes and the limited target are in
[THIRD_PARTY.md](THIRD_PARTY.md) and [PACKAGING.md](PACKAGING.md).

An earlier supplemental outbox harness run stopped on stale status-view query
assumptions. Issue #5's follow-up below corrected the test queries and passed
the full isolated PG16 delivery/recovery integration run.
Packaging evidence does not approve binary redistribution or production use.
Public source publication remains subject to the separate reviewed publisher.

## Current source review: 0.1.20 consolidation (2026-10-28)

The completed BM25, bounded PDF/DOCX and record-scoped charset branches are
consolidated on one development line. The importer adds explicit
`--document-error-policy metadata-only`; its default remains fail-before-SQL.
Failed documents retain empty-text metadata and receive no placeholder or
supplied vector, including the direct SQL API. Embedding commands receive only
text-bearing records. Failure diagnostics are bounded to 16 messages of 512
characters, with an omitted-error count. PDF signatures are checked after HTTP
decoding. This does not establish OCR or accurate text recovery.

A fresh RustSec DB (`3279c99911354c400e1d41e47c7d60e39258b147`, 1,378
advisories) found three vulnerabilities and ten unsound warnings in the .19
zstd graph. The .20 candidate locks zstd 0.14.3, zstd-safe 8.2.0 and zstd-sys
2.1.2+zstd.1.5.7. Two narrowly patched upstream crate manifests permit the fixed
line; source/notice provenance is in [vendor/README.md](vendor/README.md).
The same audit reports zero vulnerabilities, unsound or yanked findings, with
three unmaintained warnings retained. No security ignore list is added.

Validation performed on Linux x86_64 / PostgreSQL 16.15 / Rust 1.96.0:

- An earlier locked, offline rustc-only release build passed in 28m 08s. Its
  private .20 shared-library SHA-256 is
  `c28e0225aee314ebce24801482bb27b1a889190f66f1947598628268dc50df30`; it reused
  install SQL and is distinct from the official package artifact below.
- Fresh installation, SQL smoke and all four SQL regressions passed. The importer
  DB suite, 18 runtime-safety checks and 15 populated-upgrade/backup-restore
  checks passed. An actual .19 binary was used for the Common Crawl baseline,
  followed by a restart with .20 and a populated extension upgrade.
- The separate corpus clone retained 21,873 documents, 8,240,889 postings,
  21,873 WARC rows and 21,825 E5 vectors. All 24 BM25/vector/hybrid query
  fingerprints matched exactly before and after upgrade. This verifies output
  agreement, not operational relevance or a latency improvement.
- The actual importer CLI loaded all 221 PDF/signature and three DOCX responses
  from the read-only Common Crawl segment: 224 metadata/document rows, 184
  text-bearing rows, 40 metadata-only rows, 119,837 postings and 184 hash-vector
  rows. Text-length mismatches were zero and a self-term search hit passed.
  Hash vectors test ingestion mechanics, not retrieval quality. No raw corpus,
  extracted text, SQL or vectors are included in the source publication.
- The newly linked Arrow IPC/zstd path passed 32 compressed round trips,
  including nulls and Unicode. The full Python suite passed 456 tests; docs and
  Rust formatting passed. Owned-source whitespace checks passed; seven existing
  whitespace lines in upstream vendored protobuf files were preserved.

The initial PDF/DOCX database attempt omitted CREATE EXTENSION in that new test
DB and failed before Lance creation. The isolated DB was recreated with the
extension installed and the complete document-import stage passed. A Python
suite attempt in the source-only build directory lacked Git metadata and failed
its Git-environment test; the source suite is run in an actual Git checkout.
These setup failures are retained in the private evidence rather than hidden.

The 2026-10-28 PG16 package run resolves the official-generation gap for this
.20 source candidate. On the pinned PostgreSQL 16.15 Bookworm image, Rust
1.96.0 and cargo-pgrx 0.19.1 ran with GLIBC 2.36. The official `cargo pgrx
package` command wrote the 0.1.20 install SQL from the extension schema, and
the build confirmed the lockfile was unchanged. The generated package passed
artifact checks, a fresh install, four SQL regressions, importer DB, runtime
safety, backup/restore, and populated 0.1.19→0.1.20 upgrade checks.

The official package artifact SHA-256 is
`701e7050ca694d68d2f828fcc61928640828db49d5139e800620a5b40e896d29`; generated
0.1.20 install SQL SHA-256 is
`8f0768851836f72b5536a4ffea4eecf174b5d58b1baf838b7992933cad51863b`, and
Cargo.lock SHA-256 remains
`3869b04a56336e1de2880b5b04a18b22110aa588525ff40d656ad0073cda4550`. See
[PACKAGING.md](PACKAGING.md) for the Linux x86_64/PG16 reproduction commands,
CI path and verified scope. GitHub Actions itself was not run in this local
validation. Package verification does not approve binary redistribution or
production deployment. Independent operational qrels, cold/sustained SLA,
full fresh raw-WARC ingestion and host power-loss recovery remain open.

Issue #4 C1–C3 were verified for the Linux x86_64 PostgreSQL 16.15 package
candidate. Strict collection covered 436 Cargo packages with zero missing
package-level primary texts. Its CycloneDX 1.6 SBOM has 441 components (436
Cargo and five native/compiler environment components) and is bound to the
artifact SHA-256 `701e7050ca694d68d2f828fcc61928640828db49d5139e800620a5b40e896d29`,
Cargo.lock SHA-256
`3869b04a56336e1de2880b5b04a18b22110aa588525ff40d656ad0073cda4550`, native
fingerprint `ccab4bc97d2c04590a86e380ddb232488e1dff8aaac139cc7dcbfee9092c87ab`,
and notice-bundle SHA-256
`df3ca0236e1a296a490cd11d02df900d7f0a50c42515d410f0112b10f78a1fa7`.
The SBOM SHA-256 is
`58ef2bbb4313f0db1f17c6bbfb0fb22a081e72f6cec9cd93139ca9793dee7080`.
This closes the issue's notice collection and artifact-bound inventory checks
for this target. It is not legal clearance for binary redistribution or
production approval; other PostgreSQL majors and distribution profiles were
not covered. See [THIRD_PARTY.md](THIRD_PARTY.md).

The source-only snapshot review covers 275 tracked files and preserves the
existing public GitHub history; controller Git history and execution
infrastructure are excluded. The following sections retain historical results;
older publication-pending and clean-security statements apply to their dates.

The 2026-10-27 Python importer change adds an explicit `--charset-record-override WARC-RECORD-ID=CODEC` for a single reviewed WARC response. On the read-only Common Crawl segment (SHA-256 `4b6b55dc25afdb8ef9bb0e48213faa73cf07756f195b0863ba2693638bf784f8`), record 16,350 declares `uft-8` in HTTP headers and `utf-8` in HTML metadata; its 59,582-byte body fails strict UTF-8 decoding and replacement decoding observes 65 U+FFFD characters. A two-record source-derived smoke used the existing strict `None=utf-8` label correction for record 675 and the new ID-scoped correction for record 16,350. On a disposable PostgreSQL 16.6 database with the existing 0.1.19 artifact, both records entered BM25 and both had a BM25 search hit; no Lance writes occurred. No source body, extracted text or SQL was saved. This verifies a narrow import path, not semantic recovery, full archive ingestion or relevance. Wider charset correctness remains open.

The local 0.1.19 development candidate adds a narrow `bm25_term(term)` index for document-frequency counts. On the PG16.15 Common Crawl clone, the q6 count scan processed 25,097 postings and fell from 16,429 to 61 shared-hit blocks; the total plan's shared hits fell from 33,113 to 16,745. Three alternating eight-client warm rounds produced matching result fingerprints, but p95 is not attributable: host load varied from 1.95 to 25.02 on four CPUs, BM25 p95 medians were 114.32/100.71 ms, and hybrid medians were 221.15/240.26 ms. A 2026-10-27 four-arm fresh-load comparison measured the write cost: cgroup CPU increased 21.0% for BULK and 45.6% for COPY, WAL increased 23.3% and 33.3%, and the index occupied 116,023,296 bytes. The index remains a development candidate because of the count-scan improvement; these write costs do not establish production suitability. See the detailed comparison below. A locked/offline PG16 library build, artifact check, fresh install, populated 0.1.18-to-0.1.19 upgrade and all four SQL regressions passed. The standard `cargo pgrx package` path was blocked by GLIBC 2.39 being unavailable in the builder; the tested install SQL was assembled from the prior generated script and the new DDL, so official package SQL generation remains unverified. This is a source candidate only, not production or binary-distribution approval. At that historical run, source publication was pending; the 2026-10-27 consolidation review below supersedes its publication status. See [SECURITY.md](SECURITY.md) and [THIRD_PARTY.md](THIRD_PARTY.md) for review limitations.

The 2026-10-26 importer source candidate adds opt-in, bounded PDF and DOCX text extraction. On the read-only Common Crawl segment (SHA-256 `4b6b55dc25afdb8ef9bb0e48213faa73cf07756f195b0863ba2693638bf784f8`), 3/3 DOCX bodies yielded 13,689 characters with no replacement characters. Poppler 24.02.0 extracted text from 178/218 PDFs; 22 returned no text and 18 ended with extractor errors. The successful PDF output included nine U+FFFD characters, so text quality is not established. Each extraction was capped at 64 MiB and PDF execution at 30 seconds per record. The read-only scan saved no source body or extracted text and did not generate SQL, create vectors, or run a database import. This is a Python source change; the 0.1.19 extension binary and schema are unchanged. Full fresh raw-WARC ingestion, OCR, corpus-wide charset correctness, unsupported office/binary records, independent qrels and production acceptance remain open.

The GitHub repository is https://github.com/bjm1244/pgwarc_lance; the controller project directory is named `pglance-public`, while the extension is `pgwarc_lance`.

Public updates preserve the existing source-preview history and import only a reviewed source snapshot. Keep repository visibility unchanged during synchronization; visibility changes are separate publication decisions. The local `master` history is not the GitHub `main` history and must not be pushed or merged directly.

## 2026-10-26 — BM25 document-frequency index candidate

The Common Crawl q6 plan processed 25,097 posting rows but estimated 5,490.
Before the change, the `frequencies` CTE used an index-only scan of
`bm25_term_pkey` to count matching postings, touching 16,429 shared-hit blocks
in a cold capture. The candidate's one-key `bm25_term_frequency_idx` used 61
shared hits for the same count; q6 total shared hits fell from 33,113 to 16,745.
No temp files or bytes were recorded. This identifies repeated index-page work,
not a temporary spill, as a query-plan cost that can be removed with a narrow
index.

The three alternating warm rounds used the same 21,873 documents, 8,240,889
postings, 21,873 WARC rows and 21,825 384-dimensional vectors; eight fixed
queries, `k=5`, eight warmups and 128 requests per client; and JIT enabled.
Every BM25 and hybrid query fingerprint matched between the same `.18` binary
with and without the index. Median measurements were:

| 8-client measure | no frequency index | frequency index | Change |
| --- | ---: | ---: | ---: |
| BM25 p95 | 114.32 ms | 100.71 ms | −11.9% |
| BM25 backend CPU | 13.50 ms/request | 12.99 ms/request | −3.8% |
| Hybrid p95 | 221.15 ms | 240.26 ms | +8.6% |
| Hybrid backend CPU | 46.58 ms/request | 46.43 ms/request | −0.3% |

The p95 changes are inconclusive: load on the four-CPU host ranged from 1.95
to 25.02 across the rounds. Later rounds had zero physical block reads; each
first round had 122–130 BM25 reads and four hybrid reads. All profiles recorded
zero temporary blocks. The index used 82 MB. A bounded 64-document repeated
reindex profile and one 64-document fresh-ID insert pair had inconsistent cache,
I/O and CPU conditions; neither establishes ingestion cost. A first write probe
that computed `octet_length(content)` scanned the full toasted table, was
canceled, and rolled back without changing the historical source database.

Search output comparison is mathematical equality, not independent relevance.
The historical 0.1.5 eight-client latency and controlled 0.1.1 comparison were
not repeated; this `.18` schema A/B isolates one plan cost and does not explain
the earlier end-to-end latency gap.
The locked/offline PG16 library build completed and the candidate artifact
integrity check passed. The fresh install and all four SQL regressions passed;
a populated 0.1.18-to-0.1.19 update took 5.870 seconds and preserved 21,873
documents, 8,240,889 postings and 21,873 WARC rows. The `.18` and `.19` profiles
matched on all query fingerprints. The `cargo pgrx package` command could not
run because its cached binary requires GLIBC 2.39 while the PG16 builder has
GLIBC 2.36. The SQL tested here was assembled from the previous generated
install SQL plus the new index DDL, so pgrx package SQL generation remains
unverified. No qrels, fresh full raw-WARC importer throughput, cold/sustained
SLA, full production migration lock/disk assessment, or binary notice approval
was established. At the 2026-10-26 run, GitHub publication was pending.

## 2026-10-27 — 0.1.19 actual import write-cost comparison

The prior 0.1.19 evaluation had not isolated the new index's full-load cost. This
comparison replayed real content from the preserved Common Crawl clone into
separate fresh PostgreSQL 16.15 databases using the immutable 0.1.18 and 0.1.19
artifacts. The source WARC SHA-256 was
`4b6b55dc25afdb8ef9bb0e48213faa73cf07756f195b0863ba2693638bf784f8`; 21,873
documents and WARC records supplied 186,924,823 bytes of content, with a
doc-ID/length/content fingerprint of
`59f5ce9b52f02c24838363364f5412ea9504317afa1953915f72d0a4d4159be9`. The bulk
and COPY inputs were each generated once by the tracked importer and had
identical SQL hashes across both extension versions. The immutable `.18` and
`.19` library SHA-256 values were
`b674e20f0789c52982098d4bf00581644552ed7d8da4c4fdb94d58d922e3a045` and
`6429e334b3ce69235cf2491cfb04fdc52ef99ab4dfb3668a42fc5db8e2490fd0`.

Each arm ran in a network-isolated container limited to four CPUs and 16 GB,
with PostgreSQL fsync, full-page writes and synchronous commit enabled. The
server was PostgreSQL 16.15. Each import and final explicit checkpoint was timed
separately; CPU is container cgroup time through the checkpoint, while WAL is
captured after import. Results were:

| Input mode | Import time `.18` → `.19` | Cgroup CPU `.18` → `.19` | CPU change | WAL `.18` → `.19` | WAL change | `.19` frequency index |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| BULK | 735.953 → 719.881 s | 233.352 → 282.426 s | +21.0% | 2.605 → 3.213 GB | +23.3% | 116,023,296 bytes |
| COPY | 342.855 → 498.602 s | 138.688 → 201.898 s | +45.6% | 1.824 → 2.432 GB | +33.3% | 116,023,296 bytes |

The explicit final checkpoint took 23.118/34.227 seconds for `.18`/`.19` BULK
and 59.440/38.776 seconds for COPY. The COPY import elapsed time increased
45.4%; BULK elapsed time decreased 2.2% despite higher measured CPU. The
one-minute host load average varied across sequential arms from 3.10 to 16.23,
so wall time is not a stable performance conclusion. PostgreSQL's
default autovacuum was enabled and ran during the loads. The index added 110.6
MiB to the fresh-load relation.

All four results contained 21,873 documents, 8,240,907 postings, total tf
23,342,059 and 21,873 WARC rows. The posting-stream SHA-256 and all eight fixed
BM25 query fingerprints matched across versions and modes. This establishes
mathematical output equality, not independent relevance. The preserved `.18`
source database contained 8,240,889 postings and total tf 23,342,041; current
importer output therefore differs by 18 postings and 18 total tf. The cause is
unresolved and was not attributed to the frequency index. Exact input SQL
hashes were `a49c8ec8411f2952936830df60787c0d0c91129b16c02c5caa50a8cfa0f73f2ef`
for BULK and `2ce8bdc9324420d4acbfd669500ef2c6ede8357fd08501156d83acf376d2fc4c`
for COPY.

The index remains in the development candidate: the earlier q6 plan showed a
large count-scan block reduction, while this run quantifies a material CPU,
WAL and storage penalty. No workload mix or write SLA is available to balance
those costs, so this decision is not production approval. This test replays
prepared actual corpus text through the database importer path; it does not
measure a fresh full raw-WARC parse, Lance/vector writes, relevance, cold or
sustained SLA, or recovery. All result capture and explicit checkpoints
completed before container stop; the disposable containers then exited with
code 137 (not OOM), so graceful database shutdown was not verified.

## 2026-10-25 — BM25 IDF amortization candidate

Both versions used 21,873 documents, 8,240,889 postings, 21,873 WARC rows and 21,825 384-dimensional vectors. Three alternating eight-client warm rounds used the same eight queries, `k=5`, eight warmups and 128 requests per client, with JIT enabled. All query fingerprints matched exactly across both versions and modes; no requests failed.

| 8-client measure | 0.1.17 | 0.1.18 candidate | Change |
| --- | ---: | ---: | ---: |
| BM25 p95 median | 99.31 ms | 93.75 ms | −5.6% |
| BM25 backend CPU | 13.74 ms/request | 13.28 ms/request | −3.3% |
| Hybrid p95 median | 197.04 ms | 192.63 ms | −2.2% |
| Hybrid backend CPU | 47.34 ms/request | 46.94 ms/request | −0.8% |
| Long multi-term query p50 | 93.88 ms | 88.31 ms | −5.9% |
| Long multi-term query p95 | 150.99 ms | 174.60 ms | +15.6% |

The six-term plan processed 25,097 posting rows. The baseline plan evaluated IDF in the per-posting contribution expression; the candidate plan materialized six IDF rows, one per query term. Neither plan used JIT or wrote temporary blocks. Warm plan execution time for this query had a 47.81 ms baseline median and 45.51 ms candidate median. One initial capture per version read 142 blocks, so that first execution is not treated as warm evidence. No profile wrote temporary bytes. The first load round still recorded 123 physical reads in each BM25 run and four reads in candidate hybrid; later rounds had zero physical reads. The fixed 100,000-document/800,000-posting `db_search_memory.py` fixture passed its independent-score, tie-order, spill, timeout, quota and backend-reuse checks. These measurements compare output mathematics, not relevance labels. Host load varied and the query-specific p95 regression remains unexplained; there is no cold or sustained SLA evidence.

## 2026-10-25 — Earlier BM25 posting-frequency trial (rejected)

The same PostgreSQL 16.15 Common Crawl clone held 21,873 documents, 8,240,889 BM25 postings, 21,873 WARC rows and 21,825 384-dimensional vectors for the 0.1.17 baseline and temporary 0.1.18 candidate. Three alternating warm rounds used eight fixed queries, `k=5`, clients 1 and 8, eight warmups and 128 requests per client, with JIT enabled. All eight query fingerprints matched and no requests failed.

| 8-client measure | 0.1.17 | Temporary 0.1.18 | Change |
| --- | ---: | ---: | ---: |
| BM25 p95 median | 94.63 ms | 101.78 ms | +7.6% |
| BM25 backend CPU | 13.86 ms/request | 15.04 ms/request | +8.5% |
| Hybrid p95 median | 190.95 ms | 192.07 ms | +0.6% |
| Hybrid backend CPU | 46.69 ms/request | 46.59 ms/request | −0.2% |
| BM25 shared-hit blocks | 10.25M | 5.49M | −46.5% |
| Hybrid shared-hit blocks | 10.17M | 5.36M | −47.3% |

Warm load runs recorded zero physical block reads and zero temporary bytes. That earlier candidate reduced shared buffer accesses but raised BM25 CPU/p95; the fixed 100,000-document/800,000-posting memory stress also increased temp bytes from 78,003,228 to 108,300,700 under 64 KiB `work_mem`. Both versions passed the search-memory checks and exact-result fingerprints. The posting-window source change was rejected and not retained. Host load varied across rounds, so those observations are not SLA evidence; exact artifact/input hashes and run evidence remain private. This compares mathematical output, not independently labelled relevance.

## Source and distribution boundaries

- Project source includes the complete Apache-2.0 LICENSE, project NOTICE,
  contribution guidance, security limitations and third-party review notes.
- A pattern scan of the 275 tracked source files found no high-confidence private
  keys, AWS access keys, GitHub/Slack tokens or credential-bearing HTTP URLs.
  It also found no controller host, internal address or dataset mount references.
  This is a bounded scan, not proof that every possible secret is absent.
- Corpus files, database dumps, vectors, model weights, credentials and validation
  infrastructure are excluded from the source snapshot. Two Apache-2.0 upstream source crates are
  vendored with manifest-only zstd compatibility patches; see [vendor/README.md](vendor/README.md).
- The PG16 candidate's Cargo, native/compiler and standard-library notices are
  collected in a checksum-bound SBOM bundle. This evidence does not approve
  binary redistribution; review applicable distribution terms before any
  binary publication. See [THIRD_PARTY.md](THIRD_PARTY.md).
- Common Crawl provides crawled content that can remain subject to its owners'
  separate terms and rights. This project does not relabel that content Apache-2.0
  or redistribute the validation corpus. See the [Common Crawl terms](https://commoncrawl.org/terms-of-use).

## Validation and remaining production work

The consolidated Python source passed 456 unit/contract tests, the documentation checker,
Python compilation and Rust formatting. Existing 0.1.5 evidence includes 122
runtime/database checks, four SQL regressions, a public SciFact retrieval baseline,
load measurements, and a subsequent 36-check corrupt-storage/clean-instance
restore drill. These results are version-, fixture- and platform-specific;
[PRODUCTION.md](PRODUCTION.md) describes their limits.

The candidate is not approved for production: labelled operational relevance,
fresh full raw-WARC import, workload/SLA acceptance and actual host/storage
power-loss recovery remain open. PostgreSQL and Lance writes are not a single
atomic transaction; trusted-role whole-corpus access is not tenant isolation.

## Common Crawl candidate validation

The CC-MAIN-2026-39 segment
`CC-MAIN-20260904131603-20260904161603-00000.warc.gz` has 938,695,574 bytes and
SHA-256 `4b6b55dc25afdb8ef9bb0e48213faa73cf07756f195b0863ba2693638bf784f8`.
Its 66,190 raw records include 22,063 HTTP responses. The original corpus DB was
preserved; a separate clone was upgraded from 0.1.1 to 0.1.5 and analyzed.
It contains 21,873 documents/metadata rows and 8,240,889 BM25 postings.
21,825 verified E5-small document vectors (384 dimensions) were loaded into a
fresh dataset in 19.33 seconds. The native binary SHA-256 is
`a77bbdd296f334d7767f73212d287424d3ae1a28c59fdd548eaff31b6fc9d77a`.
This is the unchanged previously tested binary; the Python corrections are a
source supplement, not a replacement of the immutable artifact.

All 24 query-vector top-five results matched independent NumPy squared-L2
references within the declared float32 tolerance of 1e-5. Five English BM25
queries matched independently calculated top-five IDs and scores within 1e-9.
All 72 BM25/vector/hybrid query results had unique IDs and finite scores.
The cloned extension had zero functions granting PUBLIC EXECUTE.
These checks establish calculations and invariants, not semantic relevance:
the Common Crawl queries have no independently labelled gold set.

Warm persistent-session load used eight fixed queries, top-k five, 128 requests
per client, eight warm-ups per client and concurrency 1/2/4/8. SQL execution and
URL/160-character snippet fetching were timed; connection setup and embedding
were excluded. All 5,760 requests succeeded. Candidate p95 values in milliseconds:

| Clients | BM25 | Vector | Hybrid |
|---:|---:|---:|---:|
| 1 | 310.73 | 21.02 | 323.58 |
| 2 | 325.95 | 34.09 | 368.49 |
| 4 | 370.78 | 60.62 | 480.67 |
| 8 | 789.74 | 178.70 | 1039.65 |

The historical 0.1.1 run used the same query mix and request protocol, but was not
a simultaneous controlled experiment. Its eight-client BM25/hybrid p95 values
were 300.94/504.81 ms; this candidate measured 789.74/1,039.65 ms. The observed
slowdown remains a production concern requiring profiling and a controlled
repeat. A zero-error run does not satisfy a production latency/SLA gate.

Full raw-input conversion reached EOF in 213.37 seconds, but **did not pass full
import acceptance**: 21,872 responses converted, 190 were empty, and one explicit
`uft-8=utf-8` correction failed strict decoding at raw record 16,350. The `None`
label correction decoded its one record successfully. Binary MIME payloads also
need document extraction. Eight documents contained ASCII runs over 2,000 bytes;
the longest had 2,298,914 characters. A separate 7,680-byte incompressible-token
fixture reproduced SQLSTATE 54000 (B-tree row exceeds 2,704 bytes); transaction
rollback left no fixture document. The 0.1.15 importer rejected such runs
before SQL execution rather than silently changing token content. A late-record
regression proved no SQL was executed in function/bulk/copy modes. The 0.1.16
development candidate adds fixed-size posting keys and an original-token
dictionary for exact long-token indexing; that behavior is not part of the
0.1.15 measurements or production approval.

The run reused the existing prepared text corpus and embeddings. It therefore
does not prove a fresh raw-WARC ingestion pipeline, correct PDF/office extraction,
corpus-wide charset quality, retrieval relevance, sustained/cold performance,
production SLA, or actual host/power-loss durability. Those gates remain open.

The GitHub source-preview CI retains automatic Python, documentation, Rust
format and current RustSec checks. The full PostgreSQL build/database suite is preserved separately
in `native-validation.yml`, triggered with workflow_dispatch. The recorded PG16
local evidence does not certify every PostgreSQL version in that build matrix.

## 2026-10-20 follow-up

The 0.1.7 candidate replaces the vulnerable locked versions of `quinn-proto`
and `rustls`. On Linux x86_64 with PostgreSQL 16.15, the locked optimized
package, fresh install, SQL smoke, four SQL regressions and a populated
0.1.6-to-0.1.7 extension upgrade passed. The current RustSec scan reports zero
vulnerable, unsound or yanked dependencies and retains three unmaintained
warnings. Strict binary notice collection still needs primary license texts
for `random_word` 0.5.2 and `seahash` 4.1.0. Production acceptance, binary
redistribution approval and public GitHub synchronization remain pending.

## 2026-10-21 follow-up

The current RustSec advisory database adds RUSTSEC-2026-0382 for the previously
locked `rustls-webpki` 0.103.15. The 0.1.8 development candidate updates it to
0.103.16; the same Linux x86_64 audit scanned 561 dependencies with zero
vulnerabilities, unsound packages or yanked packages. Three unmaintained warnings
remain. The locked PG16.15 package and artifact checker passed, as did a fresh
extension install, project SQL smoke, all four SQL regressions and a populated
0.1.7-to-0.1.8 artifact upgrade. The upgrade preserved the BM25, WARC and Lance
fixture data. Strict notice collection still reports missing primary license
texts for `random_word` 0.5.2 and `seahash` 4.1.0 (exit 2). These checks do not
approve production use or binary redistribution. GitHub `main` was not updated;
the local result remains pending publication review.

## 2026-10-21 BM25 covering-index follow-up

The 0.1.9 development candidate stores `tf` as an included column of the
`(term, doc_id)` primary-key index and removes the now-redundant term-only index.
On the earlier controlled Common Crawl comparison, this schema change produced
index-only posting scans with zero heap fetches for the covered scan, reduced
the BM25 index total from 766 MiB to 446 MiB, and preserved all compared query
result hashes. The 8-client BM25 p95 median changed from 286.925 ms to
136.565 ms and estimated backend CPU per request from 18.905 ms to 11.905 ms.
Hybrid p95 changed from 533.175 ms to 354.335 ms, but vector-only latency also
moved during that run, so the entire hybrid gain cannot be attributed to this
index change. These figures are reused from the controlled 0.1.6-to-0.1.7
experiment: 0.1.8 retained that search source, but this 0.1.9 follow-up did not
rerun the full corpus benchmark. They are workload observations, not an SLA.

The current PG16.15 candidate passed a locked offline package build, artifact
checks, fresh install, project SQL smoke, all four SQL regressions, and a
populated 0.1.8-to-0.1.9 upgrade preserving its BM25, WARC and Lance fixture
rows. Only a two-document fixture was used for the upgrade; the full-corpus
migration lock duration and peak disk use were not remeasured. Binary notice
review and production approval remain open, and GitHub `main` has not been
updated.

## 2026-10-22 — 0.1.10 BM25 corpus-statistics index candidate

The 0.1.10 development candidate adds an index on `bm25_doc(doc_len)` for the
count/average corpus-statistics aggregate used by BM25 search. This follows the
observed Common Crawl plan, where the aggregate read 21,873 document rows through
a sequential scan touching 1,598 shared blocks with no temporary spill.

On an isolated synthetic fixture with 21,873 documents and 512 bytes of
high-entropy content per row, the aggregate plan touched 1,563 shared blocks
with a sequential scan before vacuum. After VACUUM, PostgreSQL chose an
index-only scan: 23 shared blocks, zero heap fetches, and 2.342 ms execution
versus 4.052 ms for the baseline aggregate. The plan immediately after insert
remained a sequential scan. The two BM25 result files were byte-identical.
In three warm repetitions of 1,000 aggregate calls, median backend CPU ticks
were 2.68 s before versus 1.17 s after, and median observed wall time was
2.838 s versus 1.206 s. This is a structural fixture result, not a Common Crawl
rerun or BM25 p95 claim.

The PG16.15 locked offline package passed fresh installation, the project SQL
smoke and all four SQL regressions. On PostgreSQL 16.6, a populated 0.1.9-to-0.1.10
artifact upgrade preserved the BM25, WARC and Lance fixture rows and created a
valid `doc_len` index.

A follow-up compared 0.1.9 and 0.1.10 on separate PostgreSQL 16.15 copies of the
prepared Common Crawl corpus: 21,873 documents, 8,240,889 BM25 postings, 21,873
metadata rows and 21,825 384-dimensional vectors. The saved source WARC checksum
remained `4b6b55dc25afdb8ef9bb0e48213faa73cf07756f195b0863ba2693638bf784f8`;
this run copied the loaded validation database and Lance dataset and did not
reimport raw WARC. Both versions used the same eight-query mix, eight clients,
eight warmups per client, 128 requests per client and `k=5`. Each run completed
1,024 requests per mode without errors, and all eight per-query BM25 and hybrid
result hashes matched across versions.

After `VACUUM (ANALYZE)`, the median of three warm runs per version was:

| Measure | 0.1.9 | 0.1.10 | Change |
| --- | ---: | ---: | ---: |
| BM25 p95 (ms) | 92.85 | 90.81 | −2.2% |
| BM25 backend CPU (ms/request) | 11.51 | 10.15 | −11.9% |
| Hybrid p95 (ms) | 234.63 | 230.77 | −1.6% |
| Hybrid backend CPU (ms/request) | 52.14 | 50.57 | −3.0% |

For the `privacy` BM25 plan, the `bm25_doc` statistics scan changed from a
sequential scan with 1,598 shared hits to a 30-hit index-only scan with zero
heap fetches. A second document scan remains in a multi-term plan. Before
VACUUM, the candidate retained a sequential statistics scan and measured CPU
was effectively unchanged. The new index occupied 240 KiB in this corpus.
These warm measurements showed zero shared reads and temporary blocks; they do
not measure physical-device I/O or cold-cache behavior. The p95 changes are
small and are not an SLA result.

Import throughput, index write overhead, autovacuum timing, fresh raw-WARC
ingestion, independent qrels, cold/sustained load and production acceptance
remain unmeasured. Production approval and binary redistribution review remain
open; public GitHub `main` was not updated and publication is pending.

## 2026-10-22 — 0.1.11 BM25 document covering primary key candidate

The preceding Common Crawl plan showed repeated heap access through
`bm25_doc_pkey`: the rare-term document join made 5,763 index lookups touching
17,289 shared-hit blocks, and a multi-term query scanned all 21,873 document
rows from the heap. The 0.1.11 schema adds `doc_len` as an INCLUDE column on
the `bm25_doc` primary key. It retains the narrower `bm25_doc(doc_len)` index,
which was measurably better for the corpus-statistics aggregate than scanning
the larger covering primary key alone.

The fresh install uses `PRIMARY KEY (doc_id) INCLUDE (doc_len)`. The
0.1.10-to-0.1.11 migration creates a unique covering index, rebinds the
extension-owned foreign key, replaces the primary key and validates the foreign
key before commit. On a PostgreSQL 16.15 clone with 21,873 documents and
8,240,889 postings, the migration completed in 1.23 seconds and preserved
21,873 WARC rows, all document/posting rows and a validated foreign key. This
does not predict migration locks or duration at larger corpus sizes.

For the controlled comparison using the 0.1.11 binary, two copies of that loaded
corpus differed in the document primary-key INCLUDE column; both had the same
validated foreign key. Both were vacuumed and analyzed. Their
baseline and covering-key schemas were run alternately three times, with eight
clients, eight warmups per client, 128 requests per client, `k=5` and the same
eight-query mix. Every run completed 1,024 requests for each search mode, and
BM25 and hybrid result hashes matched. The median measurements were:

| Measure | Baseline PK | Covering PK | Change |
| --- | ---: | ---: | ---: |
| BM25 p95 (ms) | 170.92 | 133.13 | −22.1% |
| BM25 backend CPU (ms/request) | 10.33 | 9.27 | −10.3% |
| Hybrid p95 (ms) | 335.63 | 319.22 | −4.9% |
| Hybrid backend CPU (ms/request) | 47.30 | 46.09 | −2.6% |

The shared host had a load average of 24.84 after the measurement window, so the
latency values are workload observations, not an SLA. In the rare-term plan the
document join changed to index-only scan, reducing shared hits from 17,289 to
11,527 with zero heap fetches. In the multi-term plan the second document scan
changed from a 1,598-hit sequential scan to an 86-hit index-only scan. The
statistics scan remained a 30-hit index-only scan. These warm plans reported
zero shared reads and temporary blocks; physical I/O was not measured. The
covering primary key occupied 704,512 bytes versus 614,400 for the old primary
key, a 90,112-byte increase; the 245,760-byte statistics index remains.

A separate primary-key-only pilot dropped the statistics index. Its BM25 p95
median (100.68 ms) was effectively unchanged from the 0.1.10 baseline
(100.37 ms), so that index remains in the selected schema. The WARC checksum
remained `4b6b55dc25afdb8ef9bb0e48213faa73cf07756f195b0863ba2693638bf784f8`;
the loaded database and Lance dataset were copied and raw WARC was not
reimported. No independent qrels, fresh ingestion, write overhead, cold I/O,
autovacuum coverage or sustained production workload was validated. Binary
notice review remains open; this source-preview candidate is not production
approved, and GitHub `main` remains unchanged.

## 2026-10-28 — 0.1.21 outbox validation checkpoint

The current source candidate adds a PostgreSQL transactional vector outbox and
a supervised delivery worker. A PG16.15 package built before the final SPI
correction passed artifact inspection, fresh installation, four SQL regressions,
the importer DB and runtime-safety fixtures, and a populated 0.1.20-to-0.1.21
upgrade. Its outbox worker could not claim a committed event because a locking
query went through pgrx's read-only SPI path; the backup-restore worker check
failed at the same point. The source has been corrected to use writable SPI,
but the earlier binary and failing attempt did not close issue #5. The next
section records the later outbox-specific follow-up. The candidate remains a
source preview; technical package evidence is not production approval.

## 2026-10-29 — issue #5 outbox delivery and recovery verified

The current native fingerprint is unchanged from the existing 0.1.21 PG16
receipt, so the exact immutable Linux x86_64 / PostgreSQL 16.15 artifact was
reused rather than rebuilt. Its `.so` SHA-256 is
`f326f9b649c177e196768c91e08f819d8a65dc5d1bd92025c17a2d1ad20efb60`; the
Cargo.lock SHA-256 remains
`2c9fed1e80c4f7a1bf7acbbdf54e645632df53b991d4615c3f24e3abc158c36c`.
The isolated PostgreSQL 16.15 integration run passed the importer rollback,
committed-only delivery, applied-version and queue-lag reporting, failure
visibility, same-URI ordering, backend termination replay, duplicate
idempotence and PostgreSQL crash/restart checks. BM25 remained visible before
vector delivery, and the vector became visible after the committed event was
acknowledged. The test-only status query and write-log baseline assumptions
were corrected after actual PG16 errors exposed them; no native source change
was needed.

This verifies the outbox contract on the tested Linux x86_64 / PostgreSQL
16.15 target with disposable test fixtures. It does not establish performance
under production load, multi-host Lance coordination, other PostgreSQL majors,
binary redistribution rights or production approval. GitHub `main` remains
unchanged until the separate publisher reviews and fast-forwards the source.
