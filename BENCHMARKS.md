# Benchmarks

The repository contains synthetic BM25, Lance, and WARC import benchmarks.
They are useful for detecting local regressions, but they do not establish
production latency or capacity.

Before the first supported release, run a representative corpus under a
documented PostgreSQL major version, hardware profile, batch size,
concurrency level, and storage layout. Record p50/p95 query latency, import
throughput, memory use, disk growth, error rate, and the run directory hashes.
Compare a candidate against a frozen baseline with the existing
`make compare-benchmark-runs` harness.
