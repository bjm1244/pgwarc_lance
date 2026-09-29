# Roadmap

The implementation through Phase 85 contains a WARC importer, BM25 search,
Lance vector operations, hybrid search, and evaluation/packaging tools.
The current roadmap completed Phase 1-85 (현재 로드맵의 Phase 1-85) as code work,
without declaring a supported release.

## Phase 85: Synthetic timeout decision contracts

Pure decision functions model timeout observation and cancellation eligibility.
They do not perform runtime backend cancellation or prove transaction cleanup.

## First-release gates

1. Use licensed, representative WARC records and independent relevance labels
   for 20–50 queries. Run `make doctor-real-corpus-input` and the real-corpus
   quality finalization. Set positive hit-rate and MRR thresholds before seeing
   evaluation results.
2. Run a representative import/search benchmark with explicit data size,
   concurrency, latency, memory, and storage limits.
3. Run `make verify`, the PostgreSQL 13–17 fresh-install matrix, and a
   privilege regression on the exact release commit.
4. Publish a clean source history after license, dependency, privacy, and
   secret review. Enable pull-request CI.

The first release has no previous public artifact to upgrade. Future versions
must preserve the old artifact, provide versioned SQL, and pass an actual
`ALTER EXTENSION UPDATE` smoke test.
