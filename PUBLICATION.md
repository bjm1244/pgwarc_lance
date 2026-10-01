# Source publication review — 2026-10-01

The 0.1.5 source is suitable for an **experimental source preview**, subject to
maintaining the documented limits and existing ownership/licensing declarations.
This review does not approve a production release or a prebuilt binary release.
The GitHub repository is https://github.com/bjm1244/pgwarc_lance; the controller
project directory is named `pglance-public`, while the extension is `pgwarc_lance`.
The GitHub update preserves its existing source-preview history and imports the
latest reviewed source snapshot. Repository visibility remains private during
this update; changing visibility is a separate publication action.

## Source and distribution boundaries

- Project source includes the complete Apache-2.0 LICENSE, project NOTICE,
  contribution guidance, security limitations and third-party review notes.
- A pattern scan of the 147 tracked source files found no high-confidence private
  keys, AWS access keys, GitHub/Slack tokens or credential-bearing HTTP URLs.
  It also found no controller host, internal address or dataset mount references.
  This is a bounded scan, not proof that every possible secret is absent.
- Corpus files, database dumps, vectors, model weights, credentials and validation
  infrastructure are excluded from the source snapshot. The source distribution
  does not vendor the Cargo dependency graph.
- The binary redistribution notice review still has package-level evidence gaps
  for `random_word` 0.5.2 and `seahash` 4.1.0. Cargo, native build and standard-library
  notices must accompany any eventual binary distribution. See [THIRD_PARTY.md](THIRD_PARTY.md).
- Common Crawl provides crawled content that can remain subject to its owners'
  separate terms and rights. This project does not relabel that content Apache-2.0
  or redistribute the validation corpus. See the [Common Crawl terms](https://commoncrawl.org/terms-of-use).

## Validation and remaining production work

The latest Python source passed 433 unit/contract tests, the documentation checker,
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
rollback left no fixture document. The final importer now rejects such runs
before SQL execution rather than silently changing token content. A late-record
regression proves no SQL is executed in function/bulk/copy modes.

The run reused the existing prepared text corpus and embeddings. It therefore
does not prove a fresh raw-WARC ingestion pipeline, correct PDF/office extraction,
corpus-wide charset quality, retrieval relevance, sustained/cold performance,
production SLA, or actual host/power-loss durability. Those gates remain open.

The GitHub source-preview CI retains automatic Python, documentation, Rust
format and current RustSec checks. The full PostgreSQL build/database suite is preserved separately
in `native-validation.yml`, triggered with workflow_dispatch. The recorded PG16
local evidence does not certify every PostgreSQL version in that build matrix.
