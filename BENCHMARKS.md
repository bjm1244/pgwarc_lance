# pgwarc_lance Benchmarks

작성일: 2026-07-01

이 문서는 로컬 Docker 개발 환경에서 얻은 기준선과 성능 관련 결정을 기록한다. 수치는 노트북/컨테이너 상태에 민감하므로 release benchmark가 아니라 회귀 비교용 smoke baseline이다.

## Issue #17 — PostgreSQL 16 scale qualification profile (2026-11-01)

이 프로파일은 1.0 성능 자격 심사 기준이다. 사용자에게 약속하는 절대 latency SLA가
아니며, 별도의 제품 목표가 승인되기 전에는 p95/p99 절대값으로 서비스 수준을
광고하지 않는다.

- 기준 corpus는 Common Crawl 2026-39에서 확인한 실제 whole-document 입력이다.
  문서 21,873개, BM25 posting 8,240,889개, WARC metadata 21,873개, 384차원 E5
  vector 21,825개, importer 입력 text 186,924,823 bytes다. 기존 최대 정규화 본문은
  5,234,243자였다. chunk splitting이나 길이 절단을 하지 않고 문서별 길이를 보존한다.
- 목표 규모는 고유한 실제 문서 100,000개와 1,000,000개다. source가 충분하지 않으면
  같은 행을 복제하거나 합성해 규모를 채우지 않는다. 현재 승인된 corpus만으로는
  두 목표 규모의 검색·적재 측정을 시작할 수 없다.
- 서버 조건은 Linux x86_64, PostgreSQL 16.15, 격리 container 4 CPU / 16 GiB,
  network 없음이다. DB image는
  `docker.io/library/postgres:16-bookworm@sha256:d81f7b51697014367236d07ec58b54153fc28f828b72bf247fe5c7487520bcb0`으로
  고정한다. `fsync`, `full_page_writes`, `synchronous_commit`을 켜고,
  `shared_buffers=128MB`, `work_mem=4MB`, `effective_cache_size=4GB`,
  `max_parallel_workers_per_gather=0`, `checkpoint_timeout=1h`, `max_wal_size=16GB`,
  `track_io_timing=on`으로 고정한다. 실행 전 container image digest도 pin해 보존한다.
- 검색 mix는 고정된 8개 query/vector 쌍이며 BM25, vector, hybrid를 각각 실행한다.
  `k=5`, client 1개와 8개, client당 warmup 8회와 측정 128회를 사용하고, arm마다
  5회 교차 실행한다. 비교 arm의 1분 host load가 각각 4 이하이고 쌍 간 차이가
  1 이하인 round만 유효하다. Query 내용은 공개 문서에 싣지 않고 결과 fingerprint를 남긴다.
- 적재는 같은 distinct input으로 BULK와 COPY를 각각 fresh PostgreSQL instance에
  실행하고 mode/index 조합마다 fresh instance를 3회 만든다. importer 시간과 최종
  checkpoint를 분리해 재고, cgroup CPU, peak RSS, PostgreSQL temp bytes, WAL bytes,
  database/index 크기를 함께 수집한다.

frequency index를 기본으로 유지하기 위한 사전 등록 비교 예산은 다음과 같다.

- 각 arm의 row accounting과 검색 결과 fingerprint가 같고, query/import 오류가 없어야
  한다. 이는 점수 계산의 수학적 일치이지 relevance 품질 판정은 아니다.
- 8-client 검색에서 BM25 p95가 최소 10% 개선되고 p99 퇴보는 5% 이하여야 한다.
  vector와 hybrid p95/p99 퇴보도 각각 5% 이하여야 한다. 이 조건은 두 목표 규모에서
  모두 성립해야 한다.
- BULK와 COPY 모두 적재 시간·cgroup CPU·WAL 증가가 index 없는 arm 대비 10% 이하여야
  하며, checkpoint 후 database 전체 증가량은 5% 이하여야 한다. peak RSS는 16 GiB
  container의 80% 이하로 두고 OOM, temp limit, disk full이 없어야 한다.
- 모든 측정치는 기록하되 이 상대 비교 budget을 절대 production SLA로 해석하지 않는다.
  100,000 및 1,000,000 고유 문서에서 통과하기 전에는 지원 규모를 주장하지 않는다.

### Frequency index — current-corpus decision evidence

기존 PostgreSQL 16.15의 21,873-document 비교에서는 narrow count scan의 shared-hit이
16,429에서 61로 줄었고 BM25 p95 중앙값은 114.32에서 100.71 ms로 낮아졌다. 같은
8-query profile의 hybrid p95는 221.15에서 240.26 ms로 높아졌으며 4-CPU host load는
1.95–25.02로 흔들렸다. 따라서 end-to-end latency 변화는 원인 귀속이 불가능하다.

동일 실제 corpus의 fresh-load 비교에서 index는 116,023,296 bytes를 차지했다. BULK에서
cgroup CPU와 WAL은 각각 21.0%, 23.3% 증가했고 COPY에서는 45.6%, 33.3% 증가했다.
적재 arm은 순차 실행되어 host load와 storage contention 영향을 완전히 분리하지
못했다. 현재 evidence에 따른 결정은 production 기본값으로 승격하지 않고 optional
구성으로 준비하는 것이다. 100,000 및 1,000,000 문서 실측 전에는 source schema를
바꾸거나 운영 지원을 주장하지 않는다.

## 환경

- Repo: `<local checkout>`
- PostgreSQL: Docker Compose `pgwarc_lance` 컨테이너, PostgreSQL 16
- 검증 범위: small synthetic rows, benchmark harness contract 확인
- Artifact 출력: `/tmp/pglance_phase3/*.json`, `/tmp/pglance_phase3/*.md`

## 실행 명령

```bash
BENCH_OUT_DIR=/tmp/pglance_phase3 \
BENCH_ROWS=10,20 \
BENCH_DIM=16 \
BENCH_BATCH=10 \
BM25_WORDS=20 \
WARC_ROWS=20 \
WARC_WORDS=20 \
make bench

python3 tools/bench_warc.py \
    --rows 20 \
    --body-words 20 \
    --vector-dim 16 \
    --batch-size 10 \
    --execute \
    --json-output /tmp/pglance_phase3/warc_execute.json \
    --markdown-output /tmp/pglance_phase3/warc_execute.md

python3 tools/bench_warc.py \
    --rows 20 \
    --body-words 20 \
    --vector-dim 16 \
    --batch-size 10 \
    --bm25-mode bulk \
    --execute \
    --json-output /tmp/pglance_bm25_bulk/warc_execute.json \
    --markdown-output /tmp/pglance_bm25_bulk/warc_execute.md

WARC_COMPARE_ROWS=100,500 \
WARC_WORDS=80 \
BENCH_DIM=64 \
BENCH_BATCH=100 \
WARC_COMPARE_EXECUTE=1 \
BENCH_OUT_DIR=/tmp/pglance_bm25_modes_copy_reset \
make bench-warc-bm25-modes

WARC_COMPARE_ROWS=100,500 \
WARC_WORDS=80 \
BENCH_DIM=64 \
BENCH_BATCH=100 \
WARC_COMPARE_EXECUTE=1 \
WARC_COMPARE_NO_HASH_VECTORS=1 \
BENCH_OUT_DIR=/tmp/pglance_bm25_modes_copy_reset_nohash \
make bench-warc-bm25-modes

WARC_COMPARE_ROWS=1000,2000 \
WARC_WORDS=80 \
BENCH_DIM=64 \
BENCH_BATCH=500 \
WARC_COMPARE_EXECUTE=1 \
BENCH_OUT_DIR=/tmp/pglance_bm25_scale_full \
make bench-warc-bm25-modes

WARC_COMPARE_ROWS=1000,2000 \
WARC_WORDS=80 \
BENCH_DIM=64 \
BENCH_BATCH=500 \
WARC_COMPARE_EXECUTE=1 \
WARC_COMPARE_NO_HASH_VECTORS=1 \
BENCH_OUT_DIR=/tmp/pglance_bm25_scale_nohash \
make bench-warc-bm25-modes

BENCH_OUT_DIR=/tmp/pglance_bm25_scale_nohash \
RUN_METADATA_COMMAND='make bench-warc-bm25-modes' \
make write-run-metadata

BENCH_OUT_DIR=/tmp/pglance_bm25_scale_nohash \
make report-run-directory

BENCH_OUT_DIR=/tmp/pglance_bm25_scale_nohash \
RUN_DOCTOR_PROFILE=benchmark \
make doctor-run-directory

BENCH_OUT_DIR=/tmp/pglance_bm25_scale_nohash \
RUN_METADATA_COMMAND='make bench-warc-bm25-modes' \
RUN_DOCTOR_PROFILE=benchmark \
make finalize-run-directory

BENCH_OUT_DIR=/tmp/pglance_bm25_modes_run \
WARC_COMPARE_ROWS=100,500 \
WARC_COMPARE_NO_HASH_VECTORS=1 \
make finalize-warc-bm25-modes-run

BENCH_OUT_DIR=/tmp/pglance_benchmark_run \
BENCH_ROWS=10,20 \
BENCH_DIM=16 \
BENCH_BATCH=10 \
BM25_WORDS=20 \
WARC_ROWS=20 \
WARC_WORDS=20 \
make finalize-benchmark-run
```

`doctor-run-directory` keeps the `benchmark` profile generic by default, but when a benchmark JSON is listed in `RUN_DOCTOR_REQUIRE` it validates the known benchmark schema as well as file presence. Core `finalize-benchmark-run` therefore checks `bm25.json`, `lance.json`, and `warc.json`; targeted `finalize-warc-bm25-modes-run` checks `warc_bm25_modes.json`. The doctor also verifies that manifest entries still point to existing files and, when present, that recorded size and SHA-256 match the current artifact bytes.

## Finalized Benchmark Run Evidence

2026-09-13 17:04 KST에 core benchmark finalization을 실제로 실행해 재현 가능한 run directory를 보존했다.

- Run directory: `/path/to/pglance-benchmark-run-20260913-1701`
- Command: `BENCH_OUT_DIR=/path/to/pglance-benchmark-run-20260913-1701 RUN_DIR=/path/to/pglance-benchmark-run-20260913-1701 BENCH_ROWS=100,500 BENCH_DIM=64 BENCH_BATCH=100 BM25_WORDS=80 WARC_ROWS=500 WARC_WORDS=80 WARC_BM25_MODE=function make finalize-benchmark-run`
- Artifacts: `bm25.json`, `lance.json`, `warc.json`와 Markdown 결과, `run_metadata`, `run_manifest`, `run_doctor`
- Finalization result: `run_doctor.json`의 `profile=benchmark`, errors `0`, warnings `0`; metadata는 commit `06a75f3`, branch `master`, clean worktree를 기록했다.
- Scope: synthetic benchmark이며 WARC SQL은 `execute=false`로 생성만 측정했다. 따라서 이 run은 benchmark artifact finalization과 schema/completeness 증거이지, 실제 WARC corpus 품질 baseline이나 실행 성능 gate가 아니다.

| benchmark | observed result |
|---|---|
| BM25 | 100 rows: `0.464s`, `215.4 docs/s`; 500 rows: `2.394s`, `208.8 docs/s` |
| Lance | 100 rows: batch `0.138s`, `4.18x`; 500 rows: batch `0.191s`, `17.80x` |
| WARC | 500 rows: generate `0.010s`, parse `0.050s`, SQL `0.030s`, `1046.4 KiB`; execution `unmeasured` |

이 evidence로 core benchmark run 보존 항목은 닫았지만, `finalize-warc-bm25-modes-run`의 실행 비교와 versioned PostgreSQL upgrade smoke는 별도 미측정 항목으로 남긴다.

## Finalized WARC BM25 Mode Run Evidence

2026-09-13 19:01 KST에 같은 synthetic WARC shape의 function/bulk/copy full-import 실행 비교를 finalization artifact로 보존했다.

- Run directory: `/path/to/pglance-warc-bm25-modes-run-20260913-1901`
- Command: `BENCH_OUT_DIR=/path/to/pglance-warc-bm25-modes-run-20260913-1901 RUN_DIR=/path/to/pglance-warc-bm25-modes-run-20260913-1901 WARC_COMPARE_ROWS=100,500 WARC_WORDS=80 BENCH_DIM=64 BENCH_BATCH=100 WARC_COMPARE_EXECUTE=1 make finalize-warc-bm25-modes-run`
- Finalization result: `warc_bm25_modes.json`와 Markdown, metadata/manifest/doctor를 보존했고 `run_doctor.json`의 errors `0`, warnings `0`, metadata commit은 `62dcd05`였다.
- Scope: `execute=true`와 Lance hash vector writes를 포함한 synthetic 100/500-row smoke다. real corpus나 production-scale regression gate로 해석하지 않는다.

| rows | function execute_s | bulk execute_s | copy execute_s | fastest |
|---:|---:|---:|---:|---|
| 100 | 0.480 | 0.270 | 0.227 | copy (`2.11x` vs function) |
| 500 | 2.375 | 0.904 | 0.648 | copy (`3.67x` vs function) |

관측 범위에서는 `copy`가 두 row size 모두 가장 빨랐고, 500행에서 function 대비 `3.67x`였다. bulk/copy는 SQL 생성 시간과 크기가 더 크지만 DB 실행 단계의 per-document BM25 SPI 비용을 피한다. 이 결과만으로 기본 모드를 변경하지 않고, 더 큰 실제 corpus와 failure/retry 관측 후에 승격 여부를 다시 판단한다.

## Finalized Larger WARC BM25 Mode Run Evidence

2026-09-13 21:02 KST에 같은 실행 경로를 1,000/2,000행으로 확장해 larger synthetic comparison artifact를 보존했다.

- Run directory: `/path/to/pglance-warc-bm25-modes-run-20260913-2101`
- Command: `BENCH_OUT_DIR=/path/to/pglance-warc-bm25-modes-run-20260913-2101 RUN_DIR=/path/to/pglance-warc-bm25-modes-run-20260913-2101 WARC_COMPARE_ROWS=1000,2000 WARC_WORDS=80 BENCH_DIM=64 BENCH_BATCH=500 WARC_COMPARE_EXECUTE=1 make finalize-warc-bm25-modes-run`
- Finalization result: `warc_bm25_modes.json`와 Markdown, metadata/manifest/doctor를 보존했고 `run_doctor.json`의 errors `0`, warnings `0`, metadata commit은 `ad78257`였다.
- Scope: `execute=true`와 Lance hash vector writes를 포함한 synthetic 1,000/2,000-row smoke다. real corpus·장시간 운영·failure/retry 특성은 측정하지 않았다.

| rows | function execute_s | bulk execute_s | copy execute_s | fastest |
|---:|---:|---:|---:|---|
| 1,000 | 5.859 | 1.646 | 1.120 | copy (`5.23x` vs function) |
| 2,000 | 16.263 | 3.721 | 2.224 | copy (`7.31x` vs function) |

100/500행 smoke와 larger 1,000/2,000행 smoke에서 모두 copy가 가장 빨랐지만, 두 결과 모두 synthetic이므로 기본 importer 모드나 성능 gate를 자동 변경하지 않는다. 다음 성능 승격 근거는 real corpus와 안정적인 반복 측정·failure/retry 관측이다.

## Benchmark Regression Comparison Harness Smoke

2026-09-13 23:06 KST에 Phase 74 comparator를 2,000행 finalized artifact를 baseline과 candidate로 각각 읽는 self-comparison으로 실행했다. 이는 comparator의 설정 일치·6개 mode/row 비교·report 출력 smoke이며, 새 성능 측정이나 regression 결론으로 집계하지 않는다.

- Command: `BENCHMARK_BASELINE_JSON=/path/to/pglance-warc-bm25-modes-run-20260913-2101/warc_bm25_modes.json BENCHMARK_CANDIDATE_JSON=/path/to/pglance-warc-bm25-modes-run-20260913-2101/warc_bm25_modes.json BENCHMARK_MAX_REGRESSION_RATIO=0.10 BENCH_OUT_DIR=/path/to/pglance-benchmark-comparison-20260913-2306 make compare-benchmark-runs`
- Output: `/path/to/pglance-benchmark-comparison-20260913-2306/benchmark_comparison.json`와 `.md`
- Result: `valid=true`, `passed=true`, comparisons `6`, regressions `0`
- Gate behavior: config/row/mode mismatch와 `execute=false` artifact는 fail하며, 명시적 threshold를 초과한 `execute_s`는 non-zero로 판정한다.

## Finalized Benchmark Comparison Run Evidence

2026-09-14 01:12 KST에 같은 2,000행 finalized artifact를 baseline과 candidate로 비교하고, 결과를 metadata/manifest/doctor까지 포함한 별도 run directory로 마감했다.

```bash
BENCHMARK_BASELINE_JSON=/path/to/pglance-warc-bm25-modes-run-20260913-2101/warc_bm25_modes.json \
BENCHMARK_CANDIDATE_JSON=/path/to/pglance-warc-bm25-modes-run-20260913-2101/warc_bm25_modes.json \
BENCHMARK_MAX_REGRESSION_RATIO=0.10 \
BENCH_OUT_DIR=/path/to/pglance-benchmark-comparison-run-20260914-0112 \
make finalize-benchmark-comparison-run
```

- Run directory: `/path/to/pglance-benchmark-comparison-run-20260914-0112`
- Result: comparison `valid=true`, `passed=true`, 6 comparisons, 0 regressions; run manifest `6 files, 0 errors, 0 warnings`, run doctor `errors=0`, `warnings=0`
- Metadata: commit `6eabea2`, `dirty=false`
- The Phase 75 run predates schema v2 and remains as historical schema-1 compatibility evidence; the Phase 76 provenance run is recorded below.
- Scope: self-comparison은 finalization·doctor smoke일 뿐이며, synthetic artifact의 성능 결론이나 real corpus baseline으로 집계하지 않는다.

## Benchmark Comparison Input Provenance Evidence

2026-09-14 03:06 KST에 Phase 76 comparator를 같은 2,000행 finalized artifact에 실행해 baseline/candidate 입력 provenance와 사후 drift 검사를 실제 run directory에서 확인했다.

```bash
BENCHMARK_BASELINE_JSON=/path/to/pglance-warc-bm25-modes-run-20260913-2101/warc_bm25_modes.json \
BENCHMARK_CANDIDATE_JSON=/path/to/pglance-warc-bm25-modes-run-20260913-2101/warc_bm25_modes.json \
BENCHMARK_MAX_REGRESSION_RATIO=0.10 \
BENCH_OUT_DIR=/path/to/pglance-benchmark-comparison-run-20260914-0306 \
make finalize-benchmark-comparison-run
```

- Run directory: `/path/to/pglance-benchmark-comparison-run-20260914-0306`
- Result: schema version `2`, comparison `valid=true`, `passed=true`, 6 comparisons, 0 regressions; input bytes `2927` for both files
- Input SHA-256: `26222d97a9a2e473851c40b6c7fd47ab37337b2b83b0ca5c2792cb2968ab87e5` for both baseline and candidate
- Finalization: run manifest `6 files, 0 errors, 0 warnings`, run doctor `errors=0`, `warnings=0`; metadata commit `c4c8f9b`, `dirty=false`
- Scope: input hash provenance and drift detection are reproducibility evidence only. The source is synthetic and self-compared, so this does not establish a production performance gate or real corpus quality baseline.

## BM25 Baseline

`bm25_index_document()` is still document-oriented and inserts one `bm25_term` row per unique term through SPI. `term_rows_per_s` is the current proxy metric for that bottleneck.

body_words=20

| rows | elapsed_s | docs_per_s | term_rows_per_s | doc_count | term_rows |
|---:|---:|---:|---:|---:|---:|
| 10 | 0.161 | 62.2 | 1243.9 | 10 | 200 |
| 20 | 0.182 | 109.7 | 2193.2 | 20 | 400 |

## Lance Baseline

dim=16, batch_size=10

| rows | row_by_row_s | batch_s | speedup | row_count | batch_count |
|---:|---:|---:|---:|---:|---:|
| 10 | 0.258 | 0.218 | 1.18x | 10 | 10 |
| 20 | 0.277 | 0.144 | 1.92x | 20 | 20 |

## WARC Baseline

rows=20, body_words=20, vector_dim=16, batch_size=10

| mode | warc_kb | generate_s | parse_s | sql_s | sql_kb | execute_s |
|---|---:|---:|---:|---:|---:|---:|
| SQL generation only | 0.7 | 0.001 | 0.002 | 0.000 | 23.8 | - |
| Execute via Docker psql stdin | 0.7 | 0.001 | 0.002 | 0.000 | 23.8 | 0.161 |

Same synthetic WARC input with `--bm25-mode bulk`:

| mode | warc_kb | generate_s | parse_s | sql_s | sql_kb | execute_s |
|---|---:|---:|---:|---:|---:|---:|
| SQL generation only | 0.7 | 0.001 | 0.003 | 0.002 | 40.8 | - |
| Execute via Docker psql stdin | 0.7 | 0.002 | 0.002 | 0.007 | 40.8 | 0.394 |

## WARC BM25 Mode Comparison

This compares `--bm25-mode function`, `--bm25-mode bulk`, and `--bm25-mode copy` on the same synthetic WARC shape. These numbers include Docker `psql` startup and are a smoke baseline rather than a large-corpus conclusion.

rows=100,500, body_words=80, vector_dim=64, batch_size=100, execute=true. The comparison harness recreates the extension before each mode run so `execute_s` starts from the same empty BM25/WARC state.

| rows | bm25_mode | warc_kb | generate_s | parse_s | sql_s | sql_kb | execute_s | execute_docs_per_s | sql_speedup | execute_speedup |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 100 | function | 2.3 | 0.002 | 0.010 | 0.006 | 210.4 | 0.839 | 119.2 | 1.00x | 1.00x |
| 100 | bulk | 2.3 | 0.002 | 0.010 | 0.027 | 500.0 | 1.726 | 58.0 | 0.23x | 0.49x |
| 100 | copy | 2.3 | 0.002 | 0.010 | 0.035 | 442.5 | 1.419 | 70.5 | 0.18x | 0.59x |
| 500 | function | 9.1 | 0.029 | 0.058 | 0.036 | 1048.4 | 4.452 | 112.3 | 1.00x | 1.00x |
| 500 | bulk | 9.1 | 0.029 | 0.058 | 0.137 | 2497.1 | 1.572 | 318.0 | 0.26x | 2.83x |
| 500 | copy | 9.1 | 0.029 | 0.058 | 0.171 | 2209.3 | 1.694 | 295.1 | 0.21x | 2.63x |

Same comparison with `WARC_COMPARE_NO_HASH_VECTORS=1` isolates the BM25/metadata path by skipping Lance hash vector writes.

| rows | bm25_mode | warc_kb | generate_s | parse_s | sql_s | sql_kb | execute_s | execute_docs_per_s | sql_speedup | execute_speedup |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 100 | function | 2.3 | 0.002 | 0.010 | 0.001 | 133.4 | 1.789 | 55.9 | 1.00x | 1.00x |
| 100 | bulk | 2.3 | 0.002 | 0.010 | 0.052 | 423.1 | 0.345 | 289.9 | 0.01x | 5.19x |
| 100 | copy | 2.3 | 0.002 | 0.010 | 0.061 | 365.6 | 0.282 | 354.0 | 0.01x | 6.33x |
| 500 | function | 9.1 | 0.036 | 0.054 | 0.003 | 663.6 | 2.562 | 195.2 | 1.00x | 1.00x |
| 500 | bulk | 9.1 | 0.036 | 0.054 | 0.127 | 2112.4 | 2.247 | 222.5 | 0.03x | 1.14x |
| 500 | copy | 9.1 | 0.036 | 0.054 | 0.141 | 1824.5 | 1.500 | 333.3 | 0.02x | 1.71x |

Observation: importer-side modes spend more time generating SQL than the function path, but they avoid per-document SPI term inserts during DB execution. At this smoke size, `bulk` is best for full import at 500 rows, while `copy` is best for BM25/metadata-only at 100 and 500 rows and emits smaller SQL than `bulk`. The next meaningful step is a larger corpus run before replacing the default large-import recommendation.

## BM25 Scale Follow-up

2026-07-05에 같은 synthetic WARC shape를 1000/2000 row로 키워 다시 측정했다. rows=1000,2000, body_words=80, vector_dim=64, batch_size=500, execute=true. 이전 비교와 동일하게 각 mode 실행 전 extension을 재생성했다.

Full import path with Lance hash vector writes:

| rows | bm25_mode | warc_kb | generate_s | parse_s | sql_s | sql_kb | execute_s | execute_docs_per_s | sql_speedup | execute_speedup |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1000 | function | 17.4 | 0.024 | 0.100 | 0.073 | 2095.3 | 6.050 | 165.3 | 1.00x | 1.00x |
| 1000 | bulk | 17.4 | 0.024 | 0.100 | 0.271 | 4990.4 | 1.826 | 547.8 | 0.27x | 3.31x |
| 1000 | copy | 17.4 | 0.024 | 0.100 | 0.335 | 4416.0 | 1.238 | 808.0 | 0.22x | 4.89x |
| 2000 | function | 34.1 | 0.042 | 0.196 | 0.121 | 4192.6 | 16.734 | 119.5 | 1.00x | 1.00x |
| 2000 | bulk | 34.1 | 0.042 | 0.196 | 0.545 | 9985.5 | 5.042 | 396.7 | 0.22x | 3.32x |
| 2000 | copy | 34.1 | 0.042 | 0.196 | 0.669 | 8836.5 | 2.728 | 733.0 | 0.18x | 6.13x |

BM25/metadata-only path with `WARC_COMPARE_NO_HASH_VECTORS=1`:

| rows | bm25_mode | warc_kb | generate_s | parse_s | sql_s | sql_kb | execute_s | execute_docs_per_s | sql_speedup | execute_speedup |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1000 | function | 17.4 | 0.021 | 0.096 | 0.007 | 1326.8 | 5.897 | 169.6 | 1.00x | 1.00x |
| 1000 | bulk | 17.4 | 0.021 | 0.096 | 0.220 | 4221.9 | 1.917 | 521.6 | 0.03x | 3.08x |
| 1000 | copy | 17.4 | 0.021 | 0.096 | 0.283 | 3647.4 | 1.609 | 621.6 | 0.03x | 3.67x |
| 2000 | function | 34.1 | 0.042 | 0.195 | 0.011 | 2654.5 | 16.859 | 118.6 | 1.00x | 1.00x |
| 2000 | bulk | 34.1 | 0.042 | 0.195 | 0.440 | 8447.5 | 3.405 | 587.4 | 0.03x | 4.95x |
| 2000 | copy | 34.1 | 0.042 | 0.195 | 0.581 | 7298.4 | 4.434 | 451.1 | 0.02x | 3.80x |

Observation: at 1000/2000 rows, both importer-side paths clearly beat `bm25_index_document()` execution. `copy` is the strongest psql-driven full import candidate and keeps generated SQL smaller than `bulk`, while BM25/metadata-only runs still need side-by-side measurement because 2000 rows favored `bulk` on this local run. COPY mode is not yet a replacement for a future staging-table path; it is the current best large full-import recommendation until a larger real corpus exposes a different bottleneck.

## Decisions

- BM25 bulk/COPY path: keep `bm25_index_document()` as the stable SQL API. Use importer-side `--bm25-mode bulk` for portable generated SQL based on `INSERT ... VALUES`. Prefer `--bm25-mode copy` as the current psql-driven full-import candidate for larger local runs, and keep bulk-vs-COPY comparison for BM25/metadata-only imports because the faster path varied at 1000/2000 rows.
- Lance ingestion: `lance_insert_many()` remains the default importer vector path. Row-by-row `lance_insert()` is retained for tests and small manual experiments.
- Benchmark artifacts: all `tools/bench_*.py` scripts support stdout Markdown plus optional `--json-output` and `--markdown-output`. Makefile supports `BENCH_OUT_DIR=<dir>` to write comparable artifacts for all benchmark targets. After collecting artifacts, run `BENCH_OUT_DIR=<dir> RUN_METADATA_COMMAND='<command>' RUN_DOCTOR_PROFILE=benchmark make finalize-run-directory` to capture git/command/env metadata, write `run_manifest.json`/`run_manifest.md` with file size and SHA-256 checksums, run the completeness doctor, and refresh the manifest so it includes doctor output.
- WARC execution: keep streaming `psql` stdin as the default execution path for large imports. SQL files are optional artifacts for audit, debugging, and chunk-level failure analysis.
