# Retrieval quality

A real baseline needs licensed WARC data and relevance labels made
independently of this system's output. The input manifest records source
identity, byte length, SHA-256, rights, label origin, query/qrels versions,
evaluation commit, PostgreSQL version, and search settings.

Run `make doctor-real-corpus-input` with `REAL_CORPUS_INPUT_JSON` before
importing the corpus. For final evaluation, set
`QUALITY_BASELINE_REQUIRE_REAL_CORPUS=1`, use 20–50 independent queries,
and set positive `QUALITY_BASELINE_MIN_HIT_RATE` and
`QUALITY_BASELINE_MIN_MRR` thresholds in advance.

The bundled fixtures and public WARC sample exercise parser and evaluation
plumbing only. Their scores must not be reported as real retrieval quality.
