# pgwarc_lance Future Blueprint

작성일: 2026-07-01

이 문서는 Phase 1-5 이후 `pgwarc_lance`를 어떤 순서로 키울지 정리한다. 목적은 기능 아이디어를 많이 쌓는 것이 아니라, 다음 작업을 선택할 때 품질, 성능, 운영 안정성, 패키징 중 어느 증거가 부족한지 빠르게 판단하는 것이다.

## Product Shape

`pgwarc_lance`는 PostgreSQL 안에서 WARC/Web archive 검색을 만들기 위한 작은 extension으로 유지한다. Extension은 BM25, Lance vector search, hybrid ranking, WARC metadata table 같은 검색 primitive를 제공하고, 대형 WARC parsing, embedding provider 호출, batch import orchestration은 외부 importer와 하네스가 담당한다.

이 경계는 다음 이유로 유지한다.

- WARC parsing과 embedding 호출은 실패 지점과 실행 시간이 길어서 PostgreSQL backend 안에 넣기 어렵다.
- SQL 함수 표면은 한 번 퍼지면 회수하기 어렵기 때문에, 새 API는 품질 또는 운영 반복에서 필요성이 확인된 뒤 추가한다.
- 현재 로컬 개발 기준은 GitHub Actions가 아니라 Docker-first `make verify`다. 따라서 재현 가능한 로컬 하네스가 원격 CI보다 우선이다.

## Horizon 1: Corpus-Grade Validation

목표: synthetic smoke를 넘어 실제 검색 품질과 import 성능의 첫 기준선을 만든다.

작업 후보:

- 공개 또는 내부 통제 corpus를 하나 고르고, 20-50개 query와 relevant doc_id label을 만든다.
- `make doctor-quality-labels`로 query label JSON의 schema, vector dimension, expected label coverage를 fixture 생성 전에 확인한다.
- `make make-quality-fixture`로 importer records, embedding JSONL, query labels를 external quality fixture로 만들고 `QUALITY_FIXTURE=<path> make eval-quality`로 real corpus hit-rate/MRR을 기록한다.
- `make bench-warc-bm25-modes`로 `function`/`bulk`/`copy` BM25 import path를 같은 corpus 크기에서 비교해 batch path가 언제 이득인지 정한다.
- public WARC fixture builder는 `--query-spec-json`으로 여러 labeled query를 같은 입력 corpus에서 생성할 수 있어야 한다.
- quality run은 여러 WARC source와 query spec의 checksum/provenance를 fixture와 함께 보존해야 한다.
- corpus manifest는 source별 raw/imported/skipped record 수와 labeled/unlabeled coverage를 보존해야 한다.
- tokenizer 변경 후보(stopword, ASCII stemming, CJK token 정책)는 품질 eval 전후 비교가 가능할 때만 적용한다.

완료 기준:

- `QUALITY.md`에 corpus, query set, metric, baseline 결과가 기록되어 있다.
- `BENCHMARKS.md`에 BM25 function/bulk/copy 비교가 같은 입력 조건으로 기록되어 있다.
- 회귀 가능한 fixture 또는 artifact 생성 절차가 문서화되어 있다.
- 여러 query label과 중복 이름/무일치 label 오류가 회귀 테스트로 고정되어 있다.
- source/query-spec checksum과 생성 시각이 provenance manifest로 복원 가능하다.

## Horizon 2: Import Operations

목표: 반복 import, 실패 복구, 대용량 적재를 운영 절차로 설명 가능하게 만든다.

작업 후보:

- `make doctor-import-summary`로 `--summary-json` schema, `execute=false`, zero records, Lance append duplicate risk를 DB 없이 확인한다.
- `--summary-json`을 더 강한 import manifest로 승격할지, `warc_import_run` table을 도입할지 결정한다.
- ambiguous failure 이후 PostgreSQL/BM25/Lance 상태를 실제 DB와 Lance dataset까지 점검하는 doctor 하네스가 필요한지 결정한다.
- `INSERT ... VALUES` 기반 BM25 bulk가 한계에 닿으면 staging table 또는 `COPY` 기반 경로를 설계한다.
- Lance append 중복을 감지하거나, rebuild/overwrite를 기본 운영 모드로 고정한다.

완료 기준:

- 실패 유형별 재시도 절차가 README 또는 별도 운영 문서에 있다.
- 대용량 import가 chunk, summary, retry policy를 남긴다.
- Lance duplicate risk가 `make doctor-import-summary` 또는 명령 출력으로 확인 가능하다.

## Horizon 3: Search API And UX

목표: 검색 결과를 애플리케이션이 바로 소비할 수 있게 하되, SQL 표면을 불필요하게 넓히지 않는다.

작업 후보:

- quality loop에서 필요성이 확인되면 ranking diagnostics를 안정 계약으로 추가한다.
- metadata filtering은 현재처럼 `hybrid_warc_search()` 결과에 SQL `WHERE`를 적용하는 방식을 기본으로 유지한다.
- RRF weighting, source weighting, filter options는 실사용 query에서 필요성이 확인되면 options-bearing API로 검토한다.
- snippet/highlight는 extension 내부 함수보다 `bm25_doc` join 또는 client layer를 우선한다.

완료 기준:

- 새 검색 API는 pg_regress와 smoke SQL에서 deterministic order까지 검증한다.
- README의 SQL 함수 표면과 regression expected output이 함께 갱신되어 있다.

## Horizon 4: Packaging Matrix

목표: PostgreSQL 16 local baseline을 넘어 지원 범위를 명확히 한다.

작업 후보:

- PostgreSQL 13/14/15/17별 dev package와 `cargo pgrx init --pgXX` 절차를 문서화한다.
- 각 major version artifact를 빌드하고 `CREATE EXTENSION` smoke를 통과시킨다.
- 첫 version bump 전에 upgrade SQL 작성 원칙을 실제 파일로 검증한다.

완료 기준:

- `PACKAGING.md`에 version별 검증 결과와 미지원 범위가 명확하다.
- release artifact 생성과 fresh install smoke가 같은 절차로 반복 가능하다.

## Horizon 5: Scale And Storage Shape

목표: 수십만-수백만 record 실험에서 병목과 데이터 배치를 설명 가능하게 만든다.

작업 후보:

- WARC chunk size, transaction batch size, vector dimension, Lance dataset path 정책을 benchmark 변수로 고정한다.
- importer stdout/stderr, summary JSON, benchmark JSON을 한 run directory에 모으고 `make finalize-run-directory`로 재현 metadata, file manifest, completeness check를 남긴다.
- BM25 table size, Lance dataset size, import elapsed time을 함께 기록하는 benchmark summary를 추가한다.

완료 기준:

- 큰 import 실험을 재실행할 때 입력, 명령, 출력 artifact 위치가 문서만 보고 복원된다.
- run directory에 git revision, dirty 상태, 실행 command, 주요 env 값이 `run_metadata.json`/`run_metadata.md`로 남아 있다.
- run directory의 산출물 크기와 SHA-256이 `run_manifest.json`/`run_manifest.md`로 남아 있다.
- run directory의 profile-specific 필수 산출물 검사가 `run_doctor.json`/`run_doctor.md`로 남아 있다.
- run directory 마감 절차가 `make finalize-run-directory` 하나로 재실행 가능하다.
- 성능 변화는 `BENCHMARKS.md`의 기존 baseline과 비교 가능하다.

## Validation Ladder

작업 크기에 따라 아래 순서로 검증한다.

| 범위 | 명령 | 사용 시점 |
|---|---|---|
| docs only | `make check-docs` | 문서 링크, 하네스 목록, roadmap 명령을 바꿀 때 |
| fast syntax/unit | `make test-warc`, `make test-unit` | importer/tokenizer 중심 변경 |
| benchmark run finalize | `make finalize-benchmark-run` | BM25/Lance/WARC benchmark와 run directory completeness를 함께 남길 때 |
| targeted BM25 modes finalize | `make finalize-warc-bm25-modes-run` | WARC BM25 function/bulk/copy 비교를 run directory로 마감할 때 |
| benchmark comparison finalize | `make finalize-benchmark-comparison-run` | baseline/candidate 회귀 report와 metadata/manifest/doctor를 함께 보존할 때 |
| quality label doctor | `make doctor-quality-labels` | query label JSON을 fixture build 전에 DB/embedding 없이 검사할 때 |
| quality labels finalize | `make finalize-quality-labels-run` | query label doctor report와 run metadata/manifest/doctor를 함께 남기고 `valid=true`를 확인할 때 |
| quality fixture build finalize | `make finalize-quality-fixture-build-run` | records/vector/query labels를 DB 없이 fixture와 fixture report로 만들고 run directory에 마감하며 fixture schema를 확인할 때 |
| quality fixture report | `make report-quality-fixture` | labeled fixture schema와 label coverage를 DB 없이 검사하고 report schema를 run doctor로 확인할 때 |
| quality fixture | `make make-quality-fixture`, `make eval-quality` | real corpus labels 또는 embedding fixture를 바꿀 때 |
| quality baseline doctor | `make doctor-quality-baseline` | 생성된 quality report/eval이 baseline threshold를 만족하고 baseline doctor schema가 닫히는지 확인할 때 |
| quality run finalize | `make finalize-quality-run`, `make finalize-public-warc-quality-run` | quality fixture report, eval result schema, baseline doctor, run metadata/manifest/doctor를 한 번에 남길 때 |
| import summary | `make doctor-import-summary` | importer `--summary-json` 산출물과 재시도 risk를 확인할 때 |
| import run finalize | `make finalize-import-run` | import summary schema, import doctor report schema, run metadata/manifest/doctor를 한 번에 남기고 import doctor artifact를 필수화할 때 |
| run metadata | `make write-run-metadata` | BENCH_OUT_DIR/RUN_DIR의 재현 metadata를 남길 때 |
| run manifest | `make report-run-directory` | BENCH_OUT_DIR/RUN_DIR 산출물 manifest를 남길 때 |
| run doctor | `make doctor-run-directory` | BENCH_OUT_DIR/RUN_DIR 필수 산출물 completeness, manifest integrity, 알려진 report status/schema를 확인할 때 |
| run finalize | `make finalize-run-directory` | metadata, manifest, doctor 산출물을 순서대로 마감할 때 |
| release smoke report | `make report-release-smoke-matrix` | PG_MATRIX install/upgrade smoke 실행 결과를 JSON/Markdown으로 남길 때 |
| release upgrade smoke report | `make report-release-upgrade-smoke-matrix` | upgrade target의 major별 입력 preflight을 강제하고 blocked major를 smoke 없이 non-zero로 남길 때 |
| release run finalize | `make finalize-release-run` | release artifact/smoke matrix schema와 packaging completeness를 한 run directory에 남길 때 |
| DB contract | `make test-regress`, `make test`, `make test-warc-db` | SQL 함수, table, importer SQL 변경 |
| local CI substitute | `make verify` | 커밋 전 기본 기준 |
| optional network | `make smoke-public-warc` | public WARC parser/import smoke 확인 |
| performance | `make bench`, targeted `make bench-*` | 성능 주장 또는 baseline 갱신 |

`make verify`가 너무 무거운 실험 단계에서는 좁은 하네스를 먼저 통과시킨 뒤, 커밋 전에는 가능한 한 전체 검증으로 닫는다.

## Decision Rules

- 새 SQL 함수는 README, pg_regress, smoke SQL 중 최소 두 곳에서 소비 표면이 고정될 때 추가한다.
- 새 importer flag는 DB 없는 unit test와 실행 예제가 함께 있어야 한다.
- 새 benchmark 숫자는 입력 크기, vector dimension, batch size, BM25 mode를 같이 기록한다.
- synthetic fixture에서 좋아 보이는 ranking 변경은 real corpus label 없이 기본 동작으로 승격하지 않는다.
- 외부 네트워크에 의존하는 smoke는 `make verify`에 넣지 않는다.

## Direction Review Gate

2026-09-12 코드 read-only review에서 현재 방향을 다시 판정했다. HEAD `e7781a2` 기준 Python unittest 149건과 문서 검증은 통과했지만, 최근 Phase 60-69는 WARC/embedding 입력 계약에 집중했고 라이브 PostgreSQL/Lance smoke, real labeled-corpus 품질 기준선, 성능 회귀, release/upgrade 결과는 아직 실측되지 않았다. 따라서 Phase 70 이후의 기본 목표는 parser contract 개수가 아니라 제품 가치와 운영 가능성의 증거를 닫는 것으로 한다.

### Phase 76 이후 운영 게이트

- real corpus 품질 기준선 또는 라이브 DB/Lance 검증을 막는 보안·데이터 손상 이슈가 아닌 한, 새 WARC/embedding edge-case phase 추가를 동결한다.
- 8시간 회차마다 issue를 아래 세 blueprint issue 중 하나에서 선택하고, 구현 회차와 증거 회차를 구분한다. 새 phase를 만들려면 제품 가치, 운영, 릴리스 중 최소 하나의 미측정 증거를 줄여야 한다.
- 완료는 코드/테스트만으로 닫지 않는다. 실행 명령, 실제 산출물, 관측 범위·시각, 미측정 사유, 문서화와 다음 issue가 함께 남아야 한다.
- 동일한 환경 blocker가 3회 반복되면 성공으로 포장하지 않고 `needs_attention`/blocked 상태와 필요한 외부 조치를 보고한다.
- 07:00 보고에는 계약 누적 수가 아니라 제품 증거(quality baseline, live smoke, benchmark, release matrix)와 남은 공백을 먼저 표시한다.

### Blueprint Issue A: Live Product Validation And Real Corpus Baseline

목표: synthetic/public smoke를 넘어 검색 제품이 실제 corpus에서 동작하고 품질을 설명할 수 있다는 첫 기준선을 만든다.

- 컨테이너 healthy 상태에서 `make test`, `make test-warc-db`, `make test-regress`를 실행하고 로그를 run directory에 보존한다.
- 20-50개 query와 relevant `doc_id` label을 가진 통제 가능한 corpus를 정해 `make doctor-quality-labels`, `make make-quality-fixture`, `QUALITY_FIXTURE=<path> BENCH_OUT_DIR=<dir> make finalize-quality-run`을 실행한다.
- `QUALITY_BASELINE_MIN_HIT_RATE`와 `QUALITY_BASELINE_MIN_MRR`를 0보다 큰 명시적 threshold로 정하고 `quality_baseline_doctor.json`의 `valid=true`를 확인한다.
- `QUALITY_BASELINE_REQUIRE_REAL_CORPUS=1` 게이트를 사용해 schema v2 WARC provenance/source coverage와 20-50개 query를 강제하고, 실행 flag와 threshold가 run metadata에 남는지 확인한다. Synthetic/public smoke는 이 게이트의 real baseline 증거로 집계하지 않는다.
- 2026-09-14 09시 회차에서 Phase 79 real corpus input manifest doctor를 추가했다. `tools/doctor_real_corpus_input.py`와 `make doctor-real-corpus-input`이 corpus source identity(path/URL, bytes, SHA-256), `license`/`license_url`/`redistribution`, query/qrels 버전, label 독립성, 평가 commit, PostgreSQL 버전, 검색 설정을 검증하고 `corpus.origin`/`labels.independence`가 `external`이 아니면 거부하며 local source·label 파일의 bytes/SHA-256 drift를 오류로 처리한다. 2026-09-14 기준 로컬 개발 컨테이너 DB에는 이전 smoke의 WARC record 4건만 있고 파일시스템에 실제 WARC/qrels corpus가 없어 real baseline은 여전히 미측정(blocked)이다. 필요한 입력(corpus 사용 권한/체크섬, 독립 20-50 query label, 평가 commit/PostgreSQL 버전/검색 설정)은 README와 QUALITY.md에 명시했고, synthetic/public smoke는 real 점수로 승격하지 않는다.

인수 기준:

- `quality.json`, `quality_fixture_report.json`, `quality_baseline_doctor.json`, `run_doctor.json`이 같은 run directory에 있고 서로의 doc/query/vector/label 수가 일치한다.
- QUALITY.md에 corpus 범위, query 수, labeled/unlabeled coverage, hit-rate@k, MRR, threshold, 관측시각과 재현 명령이 기록된다.
- 라이브 smoke를 실행하지 못한 항목은 0이나 성공으로 쓰지 않고 `unmeasured`와 blocker를 남긴다.

### Blueprint Issue B: Performance And Release/Upgrade Evidence

목표: 검색·import 성능과 PostgreSQL 배포/업그레이드 가능성을 반복 가능한 산출물로 고정한다.

- 동일한 rows/words/vector dimension/batch/mode 조건으로 `make finalize-benchmark-run`과 `make finalize-warc-bm25-modes-run`을 실행하고 before/after 비교 기준을 BENCHMARKS.md에 남긴다.
- 2026-09-13 17:04 KST에 core `make finalize-benchmark-run`을 `BENCH_ROWS=100,500`, `BENCH_DIM=64`, `BENCH_BATCH=100`, `WARC_ROWS=500`으로 실행해 `/path/to/pglance-benchmark-run-20260913-1701`에 benchmark JSON/Markdown와 metadata/manifest/doctor를 보존했다. run doctor는 errors/warnings `0/0`, git은 clean commit `06a75f3`였다. WARC는 synthetic SQL generation(`execute=false`)만 측정했으므로 실제 실행 성능과 real corpus quality는 이 증거에 포함하지 않는다.
- 2026-09-13 19:01 KST에 targeted `make finalize-warc-bm25-modes-run`을 같은 100/500-row·80-word·64-dimension·batch 100 조건과 `WARC_COMPARE_EXECUTE=1`로 실행해 `/path/to/pglance-warc-bm25-modes-run-20260913-1901`에 function/bulk/copy 결과를 보존했다. run doctor는 errors/warnings `0/0`, git은 clean commit `62dcd05`였고, 500행 실행 시간은 function `2.375s`, bulk `0.904s`, copy `0.648s`였다. synthetic 소규모 결과이므로 기본 모드 변경이나 production performance gate로 승격하지 않는다.
- 2026-09-13 21:02 KST에 targeted run을 1,000/2,000-row·80-word·64-dimension·batch 500 조건으로 확장해 `/path/to/pglance-warc-bm25-modes-run-20260913-2101`에 보존했다. run doctor는 errors/warnings `0/0`, git은 clean commit `ad78257`였고, 2,000행 실행 시간은 function `16.263s`, bulk `3.721s`, copy `2.224s`였다. 100/500과 larger synthetic 범위에서 copy가 일관되게 빨랐지만 real/stable regression 전에는 기본 모드나 performance gate를 변경하지 않는다.
- 2026-09-13 23:06 KST에 Phase 74의 `compare_benchmark_runs.py`와 `make compare-benchmark-runs`를 추가해 finalized WARC BM25 mode artifact 두 개의 config/rows/modes를 먼저 맞추고 `execute_s` 회귀를 명시적 ratio threshold로 판정하도록 했다. 비교 결과가 threshold를 넘으면 non-zero로 종료하며, real corpus·versioned upgrade가 없는 동안 `make verify`에는 자동 포함하지 않는다.
- 2026-09-14 01시 회차에서 Phase 75의 `make finalize-benchmark-comparison-run`과 `run_doctor` comparison report 검사를 추가했다. baseline/candidate 경로와 threshold를 metadata에 남기고, manifest 무결성·report schema·mode별 threshold 계산·`passed=true`를 독립적으로 확인하지만 real corpus·versioned upgrade 증거로 승격하지 않는다.
- 2026-09-14 03시 회차에서 Phase 76의 comparison report schema v2 `input_provenance`를 추가했다. baseline/candidate bytes와 SHA-256을 저장하고, 원본 파일이 남아 있으면 `run_doctor`가 현재 hash drift를 오류로 검출하지만 파일이 없는 경우에도 기록된 provenance는 보존한다.
- comparison report의 input provenance는 입력 파일이 외부 run directory에 있어도 bytes/SHA-256을 남기는 재현성 보조 장치다. source file이 삭제된 경우에는 저장된 hash를 보여주지만 독립적인 현재 파일 비교는 할 수 없으며, 이를 성공 증거로 오인하지 않는다.
- 2026-09-14 05시 회차에서 Phase 77의 WARC provenance doctor를 추가했다. `finalize-public-warc-quality-run`은 `warc_provenance.json`을 required artifact로 고정하고, run doctor가 schema v1/v2 source identity·source/query-spec/fixture count·schema v2 source coverage 합계를 report와 독립적으로 재검증한다. 기록된 local source/spec/query-spec 파일이 남아 있으면 bytes/SHA-256 drift를 오류로 검출하며, URL source나 삭제된 외부 파일은 재다운로드하지 않고 저장된 hash만 보존한다.
- 2026-09-14 07시 회차에서 Phase 78의 quality provenance staging을 추가했다. `finalize-quality-run`이 외부 `QUALITY_PROVENANCE_JSON`을 run directory의 표준 `warc_provenance.json`으로 보존하고 report가 staged copy를 참조하게 해, real-corpus baseline이 provenance를 외부 경로에만 의존하지 않도록 했다. provenance가 지정되면 run doctor required artifact로 독립 검수하며, manifest 누락·복사 실패·hash drift는 성공으로 처리하지 않는다.
- release artifact 검증과 PG_MATRIX install smoke를 실행하고, version bump가 필요한 시점에는 old/new artifact와 upgrade SQL로 `RELEASE_SMOKE_TARGET=smoke-release-upgrade make finalize-release-run`을 실행한다.
- 2026-09-12 PG16 기준 artifact content/install smoke와 packaging run doctor를 통과했다. 이는 당시 PG16 fresh-install 증거를 닫은 기록이다.
- 2026-09-13 PG13-17 전체 packaging matrix에서 artifact `5/5 valid`, official `postgres:<major>` install smoke `5/5 pass`(각 exit code 0), packaging run doctor `0 errors/0 warnings`를 기록했다. PG15의 첫 linker bus error는 root disk 99% 자원 압박에서 관찰됐고 비활성 matrix image/build cache 정리 후 재시도했으며, old/new upgrade smoke는 version bump 부재로 여전히 미측정이다.
- 2026-09-14 11시 회차에서 Phase 80 release upgrade input preflight를 추가했다. `make doctor-release-upgrade-inputs`가 현재 `Cargo.toml` version과 `dist/`의 실제 artifact directory만 보고 old/new version, artifact validity, `pgwarc_lance--OLD--NEW.sql` 존재를 검사한다. 현재는 `0.1.0` artifact만 있고 distinct old artifact와 upgrade SQL이 없어 `valid=false`/`status=blocked` report가 non-zero로 종료하며, `make smoke-release-upgrade`를 실행하지 않는다. versioned `ALTER EXTENSION UPDATE`는 미측정(blocked)으로 유지하고, 필요한 입력과 재개 명령은 report에 보존한다. Issue A의 real labeled corpus baseline도 계속 blocked다.
- 2026-09-14 13시 회차에서 Phase 81 upgrade preflight enforcement를 추가했다. `RELEASE_SMOKE_TARGET=smoke-release-upgrade`일 때 `report-release-smoke-matrix`가 `report-release-upgrade-smoke-matrix`로 위임해 `PG_MATRIX`의 각 major에 대해 `tools/doctor_release_upgrade_inputs.py`를 같은 old/new dist/version/pg_major 입력으로 먼저 실행하고, blocked major는 `smoke-release-upgrade`를 실행하지 않는다. `release_smoke_matrix.json` schema는 유지한 채 per-major `preflight`/`preflight_passed`/`smoke_skipped`/`smoke_skipped_reason` field를 더하고, blocked major가 있으면 non-zero로 종료한다. 현재 저장소 상태로 `RELEASE_SMOKE_TARGET=smoke-release-upgrade make finalize-release-run`을 실행해 smoke attempt `0/5`와 `ALTER EXTENSION UPDATE` 미실행, blocked report를 확인했으며, unit-test fixture로 ready preflight 후 matching input smoke dispatch와 mixed-major block, non-upgrade 경로 보존을 검증했다. versioned upgrade 자체는 여전히 미측정(blocked)이고 Issue A real labeled corpus도 계속 blocked다.
- 성능 gate를 `make verify`에 넣을지 결정한다. 넣지 않으면 적용하지 않는 이유와 다음 측정 시점을 청사진에 남긴다.

인수 기준:

- benchmark, `release_artifacts_matrix.json`, `release_smoke_matrix.json`, `run_metadata.json`, `run_manifest.json`, `run_doctor.json`이 재현 가능한 run directory에 보존된다.
- 동일 조건의 benchmark 결과와 허용 회귀 범위가 문서화되고, release/upgrade smoke의 pass/exit code가 doctor에서 재검증된다.
- 실제 version bump 전에는 upgrade SQL을 임의로 성공 처리하지 않고, 미측정 상태를 명시한다.

### Blueprint Issue C: Analysis Harness Safety And Loop Completion

목표: 8시간 반복 개발이 방향 drift나 원본 변경 없이 재현 가능하게 분석·구현·검증·문서화되도록 하네스의 기술적 통제를 강화한다. 이 issue의 일부는 프로젝트 저장소 밖 `kilo-gpt-report-harness`에 반영한다.

- 대상 repository를 read-only mount, 별도 권한 계정, sandbox 또는 동등한 파일시스템 정책으로 열어 프롬프트 의존 쓰기 방지를 제거한다.
- `remote_job.py` 완료 게이트가 JSON 파싱, 필수 schema, producer/model, observed_at/scope/limitations, declared artifact 간 수치 일관성을 검사하도록 한다. 파일 존재만으로 성공 처리하지 않는다.
- `KILO_CONFIG_CONTENT`와 `--pure`가 전역 provider/instruction/memory 설정을 덮어쓰는지 재현 테스트를 추가하고, 모델 availability는 실제 선택 모델과 provider catalog를 기준으로 기록한다.
- issue 선택 ledger에 목표 증거, 성공/실패 기준, 실행 명령, 재시도 횟수, 다음 issue를 남기고, 같은 edge-case 계약을 반복 선택하지 않도록 중복 탐지한다.
- 분석 하네스는 모델 산출물의 자기신고를 최종 사실로 취급하지 않고, 관리자 검수와 독립 명령 결과를 함께 요구한다.

2026-09-14 15시 회차에서 Issue C의 가장 작은 유효 slice로 프로젝트 로컬 run-scoped 실행 예산 gate를 추가했다. `tools/execution_budget.py`는 `max_iterations`, `max_queries`, `deadline_seconds`를 work 시작 전에 검증하고 missing/wrong-type/0/negative를 거부하며, 하나의 budget 인스턴스를 iteration/query/retry/child work에 공유해 retry나 subtask가 counter를 reset하지 못하게 한다. exact-N semantics로 N번째 unit까지만 허용하고 N+1 callback은 호출하지 않으며, deadline은 주입 가능한 `time.monotonic` clock을 쓰고 `cap_timeout()`이 남은 시간으로 query timeout을 cap한다. 소진 시 `budget_exhausted` machine-readable status(`limit_kind`/`used`/`limit`/partial-result 보존 여부)를 남기고 partial output을 성공으로 표기하지 않는다. 이 gate는 `tools/eval_quality.py`의 query loop에 연결되어 `make eval-quality-budget`으로 세 값이 모두 명시되지 않으면 work 전에 거부되고, 소진 시 `status=budget_exhausted`/`success=false`/partial `results`를 남기고 exit code 3으로 종료한다. `tests/test_execution_budget.py`와 `tests/test_eval_quality.py`가 invalid config, exact boundary/N+1, retry/child 공유, deadline/timeout을 deterministic fake clock으로 검증한다.

2026-09-14 17시 회차에서 그 `cap_timeout()` 계약을 실제 SQL subprocess 실행에 연결했다. `tools/eval_quality.py`에 `--query-timeout-seconds`(Make `QUALITY_QUERY_TIMEOUT_SECONDS`)를 추가해 setup SQL과 평가 query SQL psql subprocess에 client-side timeout을 전달한다. 옵션을 생략하면 `subprocess.run`에 `timeout` 인자를 넘기지 않아 기존 동작을 보존하고, `0`/음수/`NaN`/`Infinity`는 SQL 실행 전에 exit code 2로 거부한다. run budget이 있으면 각 subprocess 직전에 `remaining_seconds()`와 명시 timeout 중 더 짧은 유효 timeout을 `cap_timeout()`으로 계산하고 deadline이 소진됐으면 subprocess를 시작하지 않으며, budget 없이도 명시 timeout은 그대로 적용된다. setup/query는 새 budget query unit으로 세지 않아 기존 iteration/query charge 의미는 바뀌지 않는다. timeout은 성공/0점으로 처리하지 않고 `status=quality_eval_timeout`, `success=false`, `timeout.stage`(`setup`|`query`)/`reason`/`limit_seconds`/partial 보존 여부와 완료된 `results`만 남기고 exit code 4로 종료한다. query timeout은 앞서 완료한 results만 partial로 보존하고 누락 query를 채우거나 자동 retry하지 않으며 timeout 이후 새 SQL을 실행하지 않고, setup timeout은 query를 전혀 실행하지 않는다. `budget_exhausted`(exit 3)와 partial artifact 계약은 유지한다.

적용 범위와 한계: 프로젝트 밖 `kilo-gpt-report-harness`(예: `remote_job.py`)는 이 저장소에서 수정하지 않았으므로 read-only mount 강제, JSON/schema completion gate, provider/model·hash ledger, retry archive 같은 나머지 Issue C 항목은 미적용이다. 이 모듈은 그 하네스가 호출할 수 있는 프로젝트 로컬 경계이며, `--query-timeout-seconds`는 Python `subprocess.run`이 client subprocess 반환을 제한하는 경계일 뿐 PostgreSQL 서버 측 query cancellation을 보장하지 않는다. timeout 후에도 서버 backend statement가 계속 실행 중일 수 있고, psql client가 종료돼도 backend query가 남을 수 있으므로 실제 서버 측 취소는 `statement_timeout`/`pg_cancel_backend` 같은 별도 계층이 필요하다. 이번 slice는 harness 경계 증거이며 real labeled corpus 품질이나 versioned upgrade 성공 증거가 아니다. 다음 청사진은 client/server timeout 이후 backend 상태를 관측하고 취소 완료를 확인하는 별도 계약이다.

2026-09-14 19시 회차에서 producer의 timeout/budget non-success artifact를 소비하는 doctor 계약을 연결했다. `tools/eval_quality.py`에 `artifact_status_kind()`/`artifact_status_errors()`/`artifact_diagnostics()`를 두어 status/success로 artifact를 success/non-success/unknown/contradiction으로 분류하고 원본 진단을 보존한다. `tools/doctor_quality_baseline.py`는 `quality_eval_timeout`/`budget_exhausted`를 `hit_rate_at_k`/`mrr_at_k` 숫자 변환보다 먼저 식별해 `valid=false`, metric `n/a`, 원본 `status`/완료 결과 수/`timeout`(stage·reason·limit_seconds·client_side_only) 또는 `budget`(limit_kind·used·limit) 진단을 기존 report JSON/Markdown에 남기고 non-zero로 종료한다. `tools/doctor_run_directory.py`의 `quality.json` 검사는 성공 schema 검증과 non-success 검증을 분리해 status별 오류와 timeout/budget partial 구조(완료 수·partial count·client_side_only 일치)를 확인하므로 timeout artifact가 남은 run directory는 `valid=true`가 될 수 없다. `success=false`/unknown status/`status`+`success` 모순도 structured error로 처리하며 producer exit code(3/4)를 doctor exit code로 통일하지 않는다. 회귀 테스트는 producer `main()`이 실제 생성하는 setup-timeout(결과 0)/query-timeout(partial)/budget-exhausted/success artifact와 모순·unknown 입력을 두 doctor에 넣고 non-zero, JSON/Markdown 내용, traceback 부재를 검증한다. `Makefile` `finalize-quality-run`은 기존처럼 eval-quality의 non-zero 종료를 그대로 전파하며 preflight나 실패 무시를 추가하지 않았다. 이번 slice도 harness 경계 증거이고 PostgreSQL 서버 측 취소는 구현하지 않았으며, Issue A real labeled corpus/qrels와 Issue B old/new artifact·upgrade SQL은 계속 blocked/unmeasured다.

2026-09-14 21시 회차에서 Issue C의 query 전용 서버 statement_timeout 최소 slice를 추가했다. `tools/eval_quality.py`는 `--query-statement-timeout-seconds`(Make `QUERY_STATEMENT_TIMEOUT_SECONDS`) opt-in 서버 제한을 받아 Decimal 경계로 PostgreSQL millisecond 범위 `1..2147483647ms` 표현 가능 여부를 검증하고 `0`/음수/`NaN`/`Infinity`/1ms 미만/범위 초과를 SQL 실행 전에 exit 2로 거부한다. 값이 없으면 서버 timeout SQL을 전혀 주입하지 않아 기존 동작을 보존한다. 제한은 `query_sql`의 단일 `hybrid_warc_search` SELECT에만 적용되며, query마다 별도 psql subprocess/session에서 `\set QUIET on`/`\set ON_ERROR_STOP on`/`\set VERBOSITY verbose`와 `BEGIN; SET LOCAL statement_timeout = <ms>; SELECT ...; COMMIT;` 래퍼를 실행해 command tag가 결과에 섞이지 않고 SELECT 실패 후 COMMIT이 성공으로 진행되지 않는다. `setup_sql`에는 `SET LOCAL`/`statement_timeout`을 주입하지 않고 setup transaction 구조도 바꾸지 않는다. query 직전에 명시 서버 제한, client effective limit, run budget remaining deadline 중 가장 짧은 값을 PostgreSQL 정수 ms로 내림해 적용하되 0ms는 만들지 않고, 1ms 미만 cap이면 query subprocess를 시작하지 않고 기존 client/budget 비성공 경로로 종료한다. `--query-timeout-seconds`는 계속 client subprocess 제한으로만 유지되며 서버 제한이 이를 대체하거나 늘리지 않고 기존 budget charge/setup·query 카운터 의미도 바뀌지 않는다. 서버 statement timeout은 SQLSTATE 57014만으로 추측하지 않고 `canceling statement due to statement timeout` 안정 진단이 확인될 때만 기존 `quality_eval_timeout`/exit 4 경로로 분류하고, 사용자 취소·기타 SQL 오류는 기존 오류 경로로, `subprocess.TimeoutExpired`는 client timeout 경로로 남긴다. timeout artifact에는 `requested_statement_timeout_ms`/`effective_statement_timeout_ms`/`timeout_source`(`server_statement`/`client`/`run_budget`)/`sqlstate`(없으면 null)를 선택 필드로 보존하고 `client_side_only`는 false가 되며 기존 doctor는 이를 계속 `valid=false`로 구조화해 거부한다. 새 top-level status나 `pg_cancel_backend`/별도 취소 connection/자동 retry/ALTER SYSTEM은 추가하지 않았고, 서버 timeout이 backend 종료·rollback 완료·자원 회수나 취소 완료를 보장한다고 주장하지 않는다(server timeout과 psql 종료 사이 race 가능). 테스트는 opt-in/미지정 Make command, setup 미주입·query 래퍼, 1ms/최대/소수 내림/invalid 입력, client·budget cap 조합과 fake clock, 1ms 미만 pre-run gate, server/client/budget/general SQL failure 분류, 57014지만 statement-timeout 사유가 아닌 경우 미분류, 결과 파싱, server timeout artifact non-success와 doctor 소비, 정상 성공 회귀를 mock으로 검증한다(실시간 sleep 없음). 이 변경은 harness 경계 증거이지 real quality baseline이 아니며, Issue A real labeled corpus/qrels와 Issue B old/new artifact·versioned upgrade SQL은 계속 blocked/unmeasured다. 다음 청사진은 client/server timeout 이후 backend 상태 관측·취소 확인 계약이다.

2026-09-14 23시 회차에서 Issue C의 가장 작은 안전 slice로 timeout artifact의 관측 한계를 명확히 했다. `tools/eval_quality.py`의 `timeout_payload()`가 client timeout과 확인된 서버 statement timeout 모두에 `timeout.timeout_observation` 객체(`statement_outcome`, `backend_state_after_timeout=unobserved`, `cancellation_completion=unverified`, `transaction_cleanup=unverified`)를 기록한다. `statement_outcome`은 기존 SQLSTATE/진단 분류를 바꾸지 않고, 확인된 서버 statement timeout path에만 `server_statement_timeout_reported`를 쓰며 client subprocess timeout(`subprocess.TimeoutExpired`→`SqlTimeout`, pre-run 1ms 미만 cap 포함)은 stderr에 statement-timeout 문구가 우연히 있어도 `unknown`으로 남는다. observation은 성공 artifact, 일반 SQL 오류, pre-run 예산 소진에는 추가하지 않는다. `unverified`는 관측하지 않았다는 뜻이지 실패·rollback·계속 실행을 확인한 것이 아니며, 어떤 필드도 backend termination·rollback·transaction cleanup·cancellation completion을 주장하지 않는다. `tools/doctor_run_directory.py`는 observation이 객체일 때만 enum을 정확히 검증하고(`client_side_only=true`는 client timeout에만, `false`는 `timeout_source=server_statement`+`statement_outcome=server_statement_timeout_reported`+서버 필드 조합에만), observation이 없는 legacy timeout은 계속 수용하되 관측 미기록(unverified)을 렌더링하며 malformed observation은 구조화 오류로만 처리하고 confirmed outcome으로 취급하지 않는다. `tools/doctor_quality_baseline.py` Markdown은 observation 값을 표시하거나 legacy에 대해 관측 미기록을 명시하고 metric `n/a`/`valid=false`를 유지한다. `artifact_status_errors()`는 `timeout.client_side_only`에 따라 client 한계와 확인된 서버 timeout 문구를 구분하고 non-success 의미를 유지한다. 테스트는 client observation unknown, 서버 observation reported, client stderr가 서버 outcome이 되지 않음, 성공/일반 SQL/예산 소진에 observation 없음, 두 doctor의 legacy 수용과 미기록 표기, malformed object/enum 구조화 오류, 서버 `client_side_only=false` 구조적 수용과 wrong source/outcome 거부를 결정론적 mock으로 검증한다(실시간 sleep/추가 DB 호출 없음). 다음 청사진은 backend 상태 관측/active cancellation을 별도 계약으로 다루며 session identity(PID/backend/transaction), 별도 권한 connection, target race(취소 대상이 이미 종료됐을 수 있음), timeout race(취소 요청과 서버/client timeout의 경합), 관측·취소 시도 자체의 budget 소모, rollback/transaction cleanup 판정 기준을 먼저 요구한다. 그 전에는 timeout artifact가 backend 종료나 transaction 정리를 확인한 것으로 표기하지 않는다. real labeled corpus/qrels(Issue A) 입력과 old/new artifact·upgrade SQL(Issue B) 입력은 계속 blocked/separate이며 이번 slice가 대체하지 않는다.

2026-09-15 01시 회차에서 23시 timeout observation slice의 schema 경계를 강화했다. `tools/doctor_run_directory.py`의 `validate_timeout_observation()`은 `eval_quality.TIMEOUT_OBSERVATION_FIELDS`에 정의된 네 필드(`statement_outcome`, `backend_state_after_timeout`, `cancellation_completion`, `transaction_cleanup`)만 지원하고 `backend_pid`/`cancelled`/`rollback_completed` 같은 추가 claim을 `must not include unsupported fields` 구조화 오류로 거부한다. 객체가 존재하면 네 필드가 모두 있어야 하고 enum(`unknown`|`server_statement_timeout_reported`, `unobserved`, `unverified`)과 `client_side_only`-`timeout_source`-`statement_outcome` 일관성 규칙은 그대로 유지된다. observation이 없는 legacy timeout은 계속 수용하고 explicit null/non-object는 malformed다. client timeout은 stderr에 statement-timeout 문구가 있어도 `unknown`이다. `tests/fixtures/timeout_observation_contract_cases.json`에 수기 작성한 계약 케이스(수용/legacy/null/non-object/누락 필드/bad enum/source-outcome 모순/추가 claim 거부)를 두고 `tests/test_doctor_run_directory.py`가 이를 독립 입력으로 검증하며, `tests/test_doctor_quality_baseline.py`는 추가 claim이 있어도 timeout이 계속 `valid=false`/metric `n/a`/`results=[]`이고 진단만 보존됨을 확인한다. 이번 slice는 문서·검증 계약이며 active cancellation이나 backend 상태 관측을 구현하지 않는다(값은 계속 `unobserved`/`unverified`). 권한 있는 connection, `pg_cancel_backend`/`pg_terminate_backend`, PID SQL, 자동 retry, 새 runtime 필드, exit code/status 변경은 없다. real labeled corpus/qrels(Issue A)와 old/new artifact·upgrade SQL(Issue B)은 계속 blocked/separate다.

2026-09-15 03시 회차에서 Issue C의 next runtime gate를 문서/검증 계약으로만 앞당긴 prospective query attempt identity slice를 추가했다. 아직 런타임이 존재하지 않는 future query attempt identity를 위해 `contracts/quality_attempt_identity.v1.schema.json`에 strict v1 envelope를 고정하고, `tools/doctor_quality_attempt_identity.py`가 `validate_identity(payload, label=...) -> list[str]`로 strict key/type/enum/조건 일관성, 양의 `backend_pid`(bool 불가), explicit timezone을 가진 ISO-8601 `backend_start`, 정확한 `{state: unobserved}` transaction object를 검증한다. `capture_state`가 `unavailable`이면 `session_identity=null`과 `capture_source=none`, `captured`이면 정확히 `{backend_pid, backend_start}`와 `capture_source=query_session`이어야 하며, 모든 object level은 `additionalProperties=false`라 `cancelled`/`rollback_completed`/`backend_name` 같은 unsupported claim을 거부한다. `make doctor-quality-attempt-identity QUALITY_ATTEMPT_IDENTITY_JSON=<path>`로 명시 입력에만 실행되며 `eval-quality`, `doctor_run_directory`, run finalization, `make verify`는 이 validator를 호출하지 않는다. 기존 `timeout_observation` 네 필드와 eval-quality 결과 parsing/default 동작은 바뀌지 않는다. 이 계약은 synthetic이고 opt-in이며 structural consistency만 증명한다. backend/statement/transaction identity의 authenticity, backend의 계속된 존재, 취소·rollback·cleanup을 증명하지 않으며, fixture도 producer를 호출하지 않고 손으로 작성했다. next runtime gate는 same-session에서 reliably framed identity를 quality query 이전에 capture하는 것이고, 미래 observer connection은 explicit permission·target race·timeout race·budget·cleanup 판정 evidence를 먼저 갖춰야 한다. Issue A real labeled corpus/qrels와 Issue B old/new artifact·upgrade SQL은 계속 blocked/separate다.

2026-09-15 15:03 KST 회차에서 개발 의사결정(2026-09-15)에 따라 Issue C의 timeout-after-timeout 상태 판정을 pure deterministic contract로 고정했다. `tools/quality_timeout_state_decision.py`의 `decide_quality_timeout_state(events) -> dict`는 합성 event 목록을 받아 `attempt_recorded`/`timeout_recorded_backend_unobserved`/`cancellation_accepted_not_confirmed`/`statement_completed`/`termination_confirmed_cleanup_unconfirmed`/`cleanup_confirmed`/`budget_exhausted_without_cleanup`/`indeterminate_no_claims` 상태로 fold한다. 기존 `quality_attempt_identity.v1` envelope 검증과 `eval_quality.TIMEOUT_OBSERVATION_FIELDS` 네 필드 enum을 재사용하고, confirmation-grade 종료/cleanup 증거는 기록된 attempt의 `session_identity`와 정확히 일치할 때만 인정된다. 관측 실패, budget 소진, 취소 수락, 자연 완료가 timeout이나 cleanup claim을 절대 만들지 않는다. identity 불일치는 전체 판정을 `indeterminate_no_claims`로 만들고, 누락·미캡처 identity 증거는 ignore된다. stale/out-of-order(`event_seq`)와 duplicate(`event_id`) 이벤트는 판정에 기여하지 않는다. 이 contract는 synthetic이고 opt-in(`python3 tools/quality_timeout_state_decision.py --input-json ...`)이며 `eval-quality`/`doctor_run_directory`/run finalization/`make verify`는 호출하지 않고, runtime DB connection·pg_cancel_backend/pg_terminate_backend·별도 권한 connection·실제 취소를 구현하지 않는다. `tests/test_quality_timeout_state_decision.py`가 상태 구분을 결정론적으로 검증한다. Issue A real labeled corpus/qrels와 Issue B old/new artifact·upgrade SQL은 계속 blocked/separate다.

2026-09-15 23:00 KST 회차에서 개발 의사결정(2026-09-15)에 따라 Issue C의 합성 opt-in 적격성/계획 계약을 추가했다. `tools/quality_synthetic_optin_plan.py`의 `decide_synthetic_optin_plan(request) -> dict`는 합성 opt-in request를 받아 관측 적격성과 취소 적격성을 분리 판정하며 default deny로 동작한다. opt-in 누락, 필수 조건 누락/미지/모순(예: permission 미부여 + permission source 존재), malformed request는 계획을 `rejected_default_deny`/`invalid_request`로 만든다. 기존 `quality_attempt_identity.v1` envelope 검증과 tz-aware ISO-8601 판정, 양의 정수 예산 검사를 재사용하고 same-session identity capture(`capture_state=captured` + `session_identity` 객체)를 모든 적격성의 전제로 요구한다. 관측 적격성은 취소 적격성을 자동 부여하지 않는다; 취소는 별도 요청, 자체 permission과 유한 `max_cancellations` 예산, 만료 시각, `requires_execution_revalidation=true` 명시, `target_scope=single_recorded_attempt`, `identity_confirmation`이 기록 `session_identity`와 정확히 일치, 관측 grant 만료 없음이 모두 필요하다(identity 불일치·재사용/모호 target·관측 만료는 취소 계획을 거부한다). 계획 산출물은 항상 `execution_authority=none`, `not_claimed` claims(backend termination/cancellation completed/rollback completed/transaction cleanup), execution_requirements(identity capture, permission을 가진 observer connection, 실행 시점 재검증, target/timeout race, 유한 예산, rollback/transaction cleanup 실제 DB 증거)를 포함한다. 합성 적격성은 일관성 검사일 뿐 실행 승인이 아니고 backend 종료·정리의 증거가 아니다. 이 contract는 opt-in(`python3 tools/quality_synthetic_optin_plan.py --input-json ...`)이며 `eval-quality`/`doctor_run_directory`/run finalization/`make verify`는 호출하지 않고, runtime DB connection·관측·취소 실행·권한 부여·공개 인터페이스 변경은 없다. `tests/test_quality_synthetic_optin_plan.py`와 기존 timeout state 계약 조합이 자연 완료 vs timeout race, 취소 응답 vs 종료 확인, budget 소진을 결정론적으로 검증한다. Issue A real labeled corpus/qrels와 Issue B old/new artifact·upgrade SQL은 계속 blocked/separate다.

인수 기준:
2026-09-15 05시 회차에서 의사결정 판단에 따라 Issue A를 위한 read-only quality input preflight를 추가한다. tools/preflight_quality_inputs.py와 make preflight-quality-inputs는 명시된 WARC/query/qrels 경로의 존재·readability·bytes·SHA-256, 기존 query-label/records 참조 일관성, provenance/label-origin 참조 유무만 검사하고 PostgreSQL·WARC import·quality query·download·fixture 생성은 하지 않는다. qrels 독립성·진위·corpus 대표성은 unresolved로 남고, 필수 입력이나 provenance가 없으면 blocked/non-zero report를 남기며 valid=true도 real baseline이나 quality 측정을 뜻하지 않는다. Issue A real labeled corpus/qrels와 Issue B old/new artifact·upgrade SQL은 계속 blocked다.
2026-09-15 07시 회차에서 의사결정 판단에 따라 위 preflight를 기존 real-corpus manifest doctor와 연결한다. `QUALITY_INPUT_PREFLIGHT_MANIFEST_JSON`/`--manifest-json`이 주어지면 `doctor_real_corpus_input.build_report()`를 재사용해 manifest의 source/license/checksum, 독립 label, 20-50 query와 evaluation context의 오류·경고를 통합 report에 보존하고 doctor 오류는 blocked로 전파한다. manifest가 없으면 manifest check는 unresolved이며, manifest가 provenance를 대신해도 `quality_measured=false`/`real_evidence_established=false`를 유지한다. 이 연결은 read-only이고 DB·WARC import·network·fixture·baseline 측정·Issue B/C 구현을 하지 않는다. 실제 labeled WARC corpus/qrels가 없다는 blocker는 유지한다.
2026-09-15 13시 회차에서 의사결정 판단에 따라 Phase 83 quality finalization input preflight enforcement를 추가한다. `QUALITY_INPUT_PREFLIGHT_REQUIRED=1` 또는 `QUALITY_BASELINE_REQUIRE_REAL_CORPUS=1`이면 `finalize-quality-run`이 기존 `preflight-quality-inputs`를 `stage-quality-provenance`와 quality 산출물 생성보다 먼저 호출하고, corpus/query/qrels 누락·rejected manifest·validator error를 non-zero로 전파한다. preflight report는 read-only 입력 준비성만 기록하며 valid=true도 quality 측정이나 real evidence를 뜻하지 않는다. synthetic/public finalization 호환성을 유지하고, 실패 시 quality fixture/eval/baseline/final manifest를 만들지 않는 Make 회귀 테스트를 추가한다. 실제 labeled WARC corpus/qrels(Issue A)와 Issue B old/new artifact·upgrade SQL은 계속 blocked다.


- 대상 저장소 쓰기 시도가 기술적으로 실패하는 재현 테스트가 있고, 분석 run은 clean 상태를 보존한다.
- 잘못된 JSON, 필수 필드 누락, 모델 불일치, 입력 hash drift, 금지 도구 호출이 `needs_attention`으로 닫힌다.
- provider/model, 메모리·웹 차단, 입력/산출물 hash, retry archive, 보고서 시각이 run artifact로 추적 가능하다.
- 8시간 회차가 세 issue 중 어디에 기여했는지와 제품 가치 증거가 늘었는지를 오전 보고에서 확인할 수 있다.

### 의사결정 재사용·에스컬레이션 게이트

2026-09-15: 중복 자문을 줄이기 위해 의사결정의 역할을 새로운 결정·고위험 변경의 승인 자문으로 한정하고, 동일 결정의 반복 확인은 생략한다. 결정 로그를 canonical ledger로 사용하며, 결정에는 scope, issue queue, assumptions, acceptance criteria, non-goals, validation evidence, invalidation triggers, expires_at을 남긴다. 결정은 최대 9회차 또는 72시간 중 먼저 도달하는 시점까지 재사용한다.

다음 조건에서는 구현 시작 전 의사결정 자문을 필수로 한다: ledger 부재·만료·전제 변경, BLUEPRINT/공개 인터페이스/스키마/수용 기준/우선순위 변경, 승인 큐 밖 이슈, WARC 호환성·저장 형식·마이그레이션·삭제·데이터 손실·보안·권한·재시도 의미 변경, 원인 불명 blocked·동일 실패 재발·품질 회귀. 승인 범위 안의 구현 방식과 이미 정의된 실패 대응은 후속 검증으로 처리한다. 모든 회차는 called/reused/escalated/unavailable, decision_id, gate 이유, TTL/무효화 검사, 검증 증거를 ledger와 오전 보고에 남긴다. 최근 30개 완료 이슈에서 재오픈·되돌림 비율이 기준선보다 10%p 상승하거나 데이터 손실·호환성 파괴·필수 게이트 누락이 확인되면 기존 매 판단 호출 방식으로 복귀한다.


## Current Next Choices

2026-09-13: Phase 73에서 quality fixture document와 relevance label `doc_id`를 양의 PostgreSQL signed `bigint` 범위로 제한했다. 생성·평가·label doctor 경로의 묵시적 형변환을 차단했지만, 실제 labeled WARC corpus baseline은 아직 미측정이다.

2026-09-13: Issue B core benchmark finalization을 완료했다. `BENCH_ROWS=100,500`, `BENCH_DIM=64`, `BENCH_BATCH=100`, `WARC_ROWS=500` 설정의 `/path/to/pglance-benchmark-run-20260913-1701`에서 BM25/Lance/WARC 결과와 run metadata/manifest/doctor를 보존했고, benchmark doctor errors/warnings `0/0`을 확인했다. 이는 synthetic·WARC SQL generation-only 증거이며, targeted function/bulk/copy 실행 비교와 versioned upgrade smoke는 아직 남아 있다.

2026-09-13: Issue B targeted function/bulk/copy execution comparison도 완료했다. `/path/to/pglance-warc-bm25-modes-run-20260913-1901`에 `execute=true` full-import 결과와 run metadata/manifest/doctor를 보존했으며 benchmark doctor errors/warnings `0/0`이었다. 500행에서는 copy가 function 대비 `3.67x` 빨랐지만 synthetic·소규모 결과라 larger/real regression과 versioned upgrade smoke는 남아 있다.

2026-09-13: Issue B larger targeted comparison도 완료했다. `/path/to/pglance-warc-bm25-modes-run-20260913-2101`에 `execute=true` 1,000/2,000-row full-import 결과와 run metadata/manifest/doctor를 보존했으며 benchmark doctor errors/warnings `0/0`이었다. 2,000행에서는 copy가 function 대비 `7.31x` 빨랐지만 real/stable regression, failure/retry 관측과 versioned upgrade smoke는 남아 있다.

2026-09-13: Phase 74 benchmark regression comparison harness를 추가했다. `BENCHMARK_MAX_REGRESSION_RATIO`를 명시적으로 받아 설정 불일치, 미실행 artifact, mode별 `execute_s` 회귀를 자동 판정하지만 baseline/candidate 파일 경로와 threshold가 필요한 선택형 하네스라 기본 `make verify`에는 넣지 않았다. 다음 제품 증거는 real labeled corpus이며, version bump 전 upgrade smoke는 미측정이다.

2026-09-14: Phase 75 benchmark comparison finalization을 추가했다. `make finalize-benchmark-comparison-run`이 comparison report, run metadata, manifest, doctor를 함께 남기고 required report를 독립 검수하지만, comparator가 synthetic 성능 결과를 production gate로 판정하지 않도록 선택형으로 유지한다.

2026-09-14: Phase 76 comparison report schema v2가 baseline/candidate 입력 bytes와 SHA-256을 남기고, run doctor가 보존된 입력이 현재도 존재할 때 hash drift를 확인한다. 이 provenance는 재현성 보강이지 real corpus 품질 또는 versioned upgrade 증거가 아니다.
2026-09-14: Phase 77 WARC provenance doctor가 public quality finalization의 manifest schema/source coverage/source hash drift를 독립 검수하게 됐다. Phase 78에서는 `finalize-quality-run`도 외부 provenance를 run directory에 stage하고 report/doctor가 같은 보존본을 사용하게 했다. 실제 labeled corpus baseline 자체는 아직 미측정이다.

2026-09-14: Phase 79 real corpus input manifest doctor를 추가했다. `make doctor-real-corpus-input`이 corpus source/license/checksum, query/qrels 버전, label 독립성, 평가 commit/PostgreSQL 버전/검색 설정을 요구하고 synthetic/public/self-labeled 입력을 real baseline으로 거부한다. 로컬 개발 컨테이너 DB에는 이전 smoke WARC record 4건만 있고 파일시스템에 실제 WARC/qrels corpus가 없어 real baseline hit-rate/MRR은 미측정(blocked)이며, 필요한 corpus·qrels 요구사항과 재개 명령만 문서화했다. 다음 실질 진전은 사용 권한이 명확한 real WARC corpus와 독립 20-50 query label을 확보하는 것이며, 그 전까지 분포를 만들어 점수를 만들지 않는다.

2026-09-14: Phase 80 release upgrade input preflight를 추가했다. `make doctor-release-upgrade-inputs`가 현재 `0.1.0` artifact만 존재하고 distinct old artifact와 `pgwarc_lance--OLD--NEW.sql` upgrade script가 없음을 `valid=false`/`status=blocked` report로 남기고 non-zero로 종료한다. legitimate old/new artifact pair가 준비되기 전에는 `make smoke-release-upgrade`를 실행하지 않고 versioned `ALTER EXTENSION UPDATE`를 미측정(blocked)으로 유지한다. Issue A의 real labeled corpus baseline도 계속 blocked다.

2026-09-14: Phase 81 upgrade preflight enforcement를 추가했다. `RELEASE_SMOKE_TARGET=smoke-release-upgrade`일 때 `report-release-smoke-matrix`와 `finalize-release-run`이 major별 `doctor_release_upgrade_inputs` preflight을 smoke 이전에 강제하고, blocked major는 smoke를 실행하지 않은 채 non-zero report를 남긴다. 현재 저장소 상태에서는 `ALTER EXTENSION UPDATE`가 시도되지 않았고, ready fixture dispatch와 mixed-major block, non-upgrade 경로 보존은 unit test로만 검증했다. versioned upgrade 성공 증거는 여전히 없으며, old/new artifact와 upgrade SQL이 준비된 뒤에만 측정한다.

2026-09-14: Phase 82 run-scoped execution budget gate를 추가했다. `tools/execution_budget.py`가 `max_iterations`/`max_queries`/`deadline_seconds`를 work 전에 검증하고, 하나의 budget을 iteration/query/retry/child work에 공유하는 exact-N gate, injectable monotonic deadline, `budget_exhausted` machine-readable non-success status를 제공한다. `make eval-quality-budget`이 `eval_quality` query loop에 연결해 세 값 누락 시 work 전에 거부하고 소진 시 partial `results`와 non-zero(exit 3)로 종료한다. 이는 Issue C의 첫 slice이며, 프로젝트 밖 `kilo-gpt-report-harness`의 read-only/완료 게이트/provider·hash ledger 항목은 저장소 밖이라 미적용이다. real labeled corpus baseline과 versioned upgrade 성공 증거는 여전히 미측정(blocked)이다.

2026-09-14: Phase 82 correction으로 query loop의 이중 charge ordering bug를 고쳤다. 기존 loop는 매 query마다 `charge_iteration()` 성공 후 `charge_query()`를 호출해 query limit가 먼저 소진되면 blocked N+1 시도가 `used_iterations`만 증가시켰다(예: `max_iterations=5`, `max_queries=2`, 3 queries에서 results/used_queries=2인데 used_iterations=3). `ExecutionBudget.charge_query_unit()`이 두 한도를 먼저 검사하고 둘 다 통과할 때만 원자적으로 함께 차감하므로 어느 한도가 먼저 걸려도 어떤 카운터도 소비하지 않는다. query-first/iteration-first/simultaneous/잔여 카운터 의미는 결정론적 회귀 테스트로 고정했고 retry/child/deadline semantics와 machine-readable status/partial result는 보존한다. 실제 corpus/upgrade 증거 상태는 변경하지 않았다.

2026-09-14: Phase 82 timeout wiring slice로 `cap_timeout()` 계약을 실제 SQL subprocess에 연결했다. `tools/eval_quality.py`가 `--query-timeout-seconds`/`QUALITY_QUERY_TIMEOUT_SECONDS`를 받아 setup/query psql subprocess에 `timeout`을 전달하고, 옵션 생략 시 기존 동작을 보존하며 `0`/음수/`NaN`/`Infinity`를 SQL 전에 거부한다. run budget이 있으면 각 subprocess 직전에 남은 deadline과 명시 timeout 중 더 짧은 값을 쓰고 deadline 소진 시 runner를 호출하지 않으며, budget 없이도 명시 timeout은 적용된다. timeout 시 `quality_eval_timeout` non-success artifact(exit 4)만 남기고 성공/0점 처리나 자동 retry를 하지 않으며, query 단계는 완료된 results만 partial로 보존한다. 이는 client subprocess 경계일 뿐 PostgreSQL 서버 측 query cancellation을 보장하지 않는다. real labeled corpus baseline과 versioned upgrade 성공 증거는 계속 미측정(blocked)이다.

2026-09-13: Phase 72 이후 live PostgreSQL/Lance smoke를 재실행해 `make test`, `make test-warc-db`, `make test-regress`와 pg_regress 3/3 통과를 확인했다. Issue A의 live DB 경로는 현재 healthy 증거를 갖췄지만 실제 labeled WARC corpus baseline은 아직 미측정이며, 다음 우선순위는 20-50개 query와 provenance를 가진 real corpus를 확보하는 것이다.

2026-09-13: Phase 72에서 quality fixture 생성·평가 경로의 duplicate `doc_id`와 query `name`을 SQL/Lance setup 전에 거부하는 document/query identity 계약을 고정했다. standalone fixture builder가 query label doctor를 우회해도 중복 query name을 생성하지 않으며, 실제 labeled WARC corpus baseline은 아직 미측정이다.

2026-09-13: Phase 71에서 external quality fixture query의 duplicate `expected_doc_ids`를 거부하는 relevance label identity 계약을 고정했다. 이 검증은 direct fixture 평가 경로에서도 label count와 metric inflation을 방지하며, 실제 labeled WARC corpus baseline은 아직 미측정이다.

2026-09-12: public WARC fixture builder의 `--query-spec-json` 다중 query 지원과 provenance manifest를 추가했다. Phase 55에서 `--source-spec-json` 여러 WARC 입력과 schema v2 `sources` 목록을 구현했고, Phase 56에서 source별 raw/imported/skipped record와 labeled/unlabeled coverage를 추가했다. Phase 57에서 quality fixture report가 manifest를 읽어 source별 최소 coverage와 skipped 비율 threshold를 검증하게 만들었고, Phase 58에서 run doctor가 이 report의 source coverage schema와 threshold 결과를 required artifact 계약으로 재검증하게 만들었다. Phase 59에서 quality baseline doctor artifact에도 source coverage 문맥을 보존하고 run doctor가 baseline 산출물에서 재검증하게 만들었다. Phase 60에서 source spec의 path/URL 단일성 validation을 고정했고, Phase 61에서 malformed WARC `Content-Length` 입력 계약을 고정했다. Phase 62에서 HTTP chunked payload의 size, length, terminator, trailer, terminating zero-size chunk 계약을 고정했다. Phase 63에서 HTTP gzip `Content-Encoding` 압축 해제 실패 입력 계약을 고정했다. Phase 64에서 importer selection limit과 `--limit 0` empty import 계약을 고정했다. Phase 65에서 duplicate imported `doc_id`를 SQL/Lance 적재 전에 거부하는 identity 계약을 고정했다. Phase 66에서 HTTP `Content-Type` charset parameter의 optional whitespace parsing 계약을 고정했다. Phase 67에서 duplicate WARC `Content-Length` overwrite를 payload read 전에 거부하는 header identity 계약을 고정했다. Phase 68에서 external embedding vector의 finite numeric/range 계약을 고정했다. Phase 69에서 required embedding `doc_id`의 duplicate vector overwrite를 거부하는 document identity 계약을 고정했다. Phase 70에서 external quality fixture doc/query vector의 finite numeric 및 float 범위 계약을 고정했다. 2026-09-13에 PG13-17 fresh-install packaging matrix evidence도 기록했다. Direction Review Gate 순서에 따라 다음 청사진 이슈는 public smoke를 넘는 실제 labeled corpus quality baseline이며, version bump 이후 PostgreSQL major upgrade smoke baseline을 그 다음으로 둔다.

현재 가장 실용적인 다음 작업은 두 가지다. BM25 scale follow-up은 2026-07-05에 1000/2000 row 기준선까지 완료했고, benchmark run finalization은 `make finalize-benchmark-run`과 targeted `make finalize-warc-bm25-modes-run`으로 고정했다. Public WARC quality smoke도 optional network fixture와 finalization target으로 연결했고, Phase 55-78에서 여러 입력 corpus manifest, source coverage, coverage threshold report, run doctor와 baseline artifact 재검증, source spec validation, provenance source drift와 quality-run staging, malformed WARC length와 duplicate length, HTTP chunked payload, gzip content-encoding, selection limit, document identity, charset parameter, embedding numeric와 duplicate vector identity, quality fixture finite-vector와 doc/query identity 및 signed-64 `doc_id` validation까지 추가했다. PostgreSQL major별 fresh install smoke scaffold도 `make smoke-pg-version`/`make pg-version-matrix`로 추가했고, release artifact content/install/upgrade smoke, matrix wrappers, checksum report, smoke execution report, release run finalization은 `make check-release-artifacts`, `make smoke-release-artifacts`, `make smoke-release-upgrade`, `make release-artifacts-matrix`, `make smoke-release-artifacts-matrix`, `make smoke-release-upgrade-matrix`, `make report-release-artifacts`, `make report-release-smoke-matrix`, `make finalize-release-run`으로 고정했다. Run doctor는 `run_metadata.json` schema와 required/profile `release_artifacts_matrix.json`의 matrix row/file schema, `release_smoke_matrix.json`의 result row schema도 검사한다. Quality label doctor, label doctor finalization, fixture build finalization, fixture schema/label coverage report, quality baseline threshold gate, quality run finalization은 `make doctor-quality-labels`, `make finalize-quality-labels-run`, `make finalize-quality-fixture-build-run`, `make report-quality-fixture`, `make doctor-quality-baseline`, `make finalize-quality-run`, `make finalize-public-warc-quality-run`으로 고정했다. Import summary doctor와 import run finalization은 `make doctor-import-summary`, `make finalize-import-run`으로 고정했고, run doctor도 required/profile `import.summary.json`과 required `import_doctor.json` schema를 검사한다. Generic run directory metadata/manifest/completeness finalization은 `make finalize-run-directory`로 고정했다. 후속 대형 실험은 실제 corpus나 staging-table 설계가 필요할 때 다시 연다.

Phase 74-78의 comparison report, input provenance, finalization/doctor는 synthetic baseline을 안전하게 재검수하는 운영 장치이며, real corpus 품질 증거를 대체하지 않는다.

1. Real corpus quality baseline: public WARC smoke를 넘어 20-50개 query label이 있는 corpus를 정하고 `make make-quality-fixture`, `QUALITY_FIXTURE=<path> BENCH_OUT_DIR=<dir> make finalize-quality-run` 결과를 한 run directory로 마감한 뒤 `QUALITY_BASELINE_MIN_HIT_RATE=<rate> QUALITY_BASELINE_MIN_MRR=<rate> make doctor-quality-baseline`로 acceptance threshold를 남긴다. Public smoke 기준은 `BENCH_OUT_DIR=<dir> make finalize-public-warc-quality-run`으로 재생성한다. 이때 Phase 58-73의 run/baseline doctor, source spec, WARC record length와 duplicate length, HTTP chunked payload, gzip content-encoding, selection limit, document identity, charset parameter, embedding numeric와 duplicate vector identity, quality fixture finite-vector와 doc/query identity contract가 source coverage와 입력 integrity를 함께 검증한다.
2. PostgreSQL upgrade baseline: version bump 이후 old/new artifact와 upgrade SQL을 준비하고 `RELEASE_SMOKE_TARGET=smoke-release-upgrade make finalize-release-run`으로 PG_MATRIX upgrade smoke 결과와 packaging run metadata/manifest/doctor를 기록한다.

선택 기준은 Direction Review Gate의 순서를 따른다. 먼저 Issue A로 제품 가치 증거를 확보하고, Issue B로 성능·배포 증거를 닫은 뒤, Issue C의 하네스 통제를 강화한다. 보안·데이터 손상 blocker를 제외한 추가 parser contract는 Issue A의 acceptance criteria를 통과한 뒤 재개한다.
