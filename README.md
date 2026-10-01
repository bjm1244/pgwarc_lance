# pgwarc_lance

> **상태: 연구용 프로토타입 — 프로덕션 미지원.** PostgreSQL 인사이드에서 BM25 + 벡터 hybrid 검색을 실험하는 구현이다. 0.1.5 후보는 BM25 집계·입력/결과 제한에 이어 동시 적재 중 통계 혼합 오류를 수정했다. Python 433개, DB 검증 122개, 공개 SciFact 품질 및 준비된 2,000건 부하 검증을 통과했다. 통계 미수집 상태의 첫 성능 실패와 측정 준비 수정도 검증 기록에 남겼다. 0.1.2의 취약 바이너리를 운영에 쓰지 마라. 운영 WARC 품질, 목표 규모·지연시간, 운영 절차 검증은 진행 중이다. 사용 전 [SECURITY.md](SECURITY.md)의 알려진 제약을 확인하라.
>
> 라이선스: Apache-2.0 ([LICENSE](LICENSE)). 기여 전 [CONTRIBUTING.md](CONTRIBUTING.md)·[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)를 참고하라.

`pgwarc_lance`는 WARC/Web archive 데이터를 PostgreSQL 안에서 검색 가능한 레코드로 다루기 위한 pgrx 기반 확장이다. 현재 구현은 PostgreSQL 테이블 기반 BM25, Lance dataset 기반 벡터 검색, RRF 기반 hybrid search, 그리고 초기 WARC/WARC.GZ SQL importer를 제공한다.

목표 구조는 WARC/WARC.GZ importer가 웹 레코드를 추출하고, `pgwarc_lance`가 텍스트 인덱스와 Lance 벡터 저장소를 결합해 검색하는 방식이다.

운영 준비의 검증 기준과 남은 제약은 [PRODUCTION.md](PRODUCTION.md)에 정리한다.

완료된 구현 이력은 [ROADMAP.md](ROADMAP.md), 미완료 항목의 선택 기준은 [BLUEPRINT.md](BLUEPRINT.md)에 정리한다.

공개 검토 범위와 Common Crawl 검증은 [PUBLICATION.md](PUBLICATION.md)에 정리한다.

## 개발 DB와 함수 권한

폐기 가능한 로컬 DB용 비밀번호를 먼저 지정한다. Compose는 기본적으로 localhost에만 노출한다.

```bash
export PGWARC_DEV_PASSWORD="$(openssl rand -base64 24)"
```

새 설치는 확장 함수의 `PUBLIC EXECUTE`를 회수한다. 슈퍼유저가 아닌 역할에는
필요한 함수와 테이블 권한을 개별적으로 부여해야 한다. 파일 접근 함수는 신뢰한 역할에만
허용하고 관리자가 `pgwarc_lance.allowed_uri_prefix`를 실제 존재하는 로컬 데이터 디렉토리로
설정한다. 경로 문자열의 일부가 아니라 디렉토리 경계와 심볼릭 링크를 검사하며,
`..`·상대 경로·원격 URI·percent encoding은 제한이 설정된 동안 거부한다.
자세한 설정과 남은 제약은 [SECURITY.md](SECURITY.md)를 참고한다.

## 디렉토리 구조

```
pgwarc_lance/
├── Cargo.toml                 # pgrx 익스텐션 크레이트 (pg13~17 기능 게이트)
├── ROADMAP.md                 # 현재 상태와 단계별 작업 계획
├── BLUEPRINT.md               # Phase 1-5 이후 미래 청사진과 작업 선택 기준
├── PACKAGING.md               # 로컬 release artifact, install, upgrade 정책
├── QUALITY.md                 # retrieval quality fixture, eval, ranking 정책
├── SECURITY.md                # 알려진 보안 제약과 제보 절차
├── CONTRIBUTING.md            # 개발 환경·작업 원칙·PR 기준
├── CODE_OF_CONDUCT.md         # Contributor Covenant 2.1
├── LICENSE                    # Apache-2.0
├── NOTICE                     # 서드파티 라이선스 고지
├── README.en.md               # English overview
├── pgwarc_lance.control       # PostgreSQL 확장 제어 파일
├── Dockerfile                 # postgres:16 + Rust/cargo-pgrx 빌드/설치 환경
├── docker-compose.yml         # PostgreSQL 컨테이너 (포트 55432)
├── Makefile                   # 빌드/실행/테스트 하네스 명령 래퍼
├── BENCHMARKS.md              # 로컬 벤치마크 기준선과 Phase 3 결정 기록
├── sql/
│   └── 01-create-extension.sql# 컨테이너 초기화 시 자동 CREATE EXTENSION pgwarc_lance
├── tools/
│   ├── bench_bm25.py          # bm25_index_document 성능 하네스
│   ├── bench_lance.py         # lance_insert vs lance_insert_many 성능 하네스
│   ├── bench_warc.py          # synthetic WARC parser/importer 성능 하네스
│   ├── bench_warc_bm25_modes.py # function/bulk BM25 WARC import 비교 하네스
│   ├── compare_benchmark_runs.py # baseline/candidate benchmark 회귀 비교 하네스
│   ├── eval_quality.py        # synthetic relevance quality eval 하네스
│   ├── execution_budget.py    # 분석 하네스 run-scoped 실행 예산(iteration/query/deadline) gate
│   ├── make_quality_fixture.py # records/vector/label JSON -> quality fixture builder
│   ├── doctor_quality_labels.py # query label JSON schema/coverage doctor
│   ├── report_quality_fixture.py # quality fixture schema/label coverage report
│   ├── make_public_warc_quality_fixture.py # public WARC labeled quality fixture 생성
│   ├── check_release_artifacts.py # release artifact 구조/버전 검증
│   ├── smoke_release_artifacts.py # release artifact를 깨끗한 Postgres 컨테이너에 설치 smoke
│   ├── smoke_release_upgrade.py # release artifact 기반 ALTER EXTENSION smoke
│   ├── doctor_release_upgrade_inputs.py # old artifact/upgrade SQL preflight doctor
│   ├── report_release_artifacts.py # release artifact matrix 검증 report 생성
│   ├── report_release_smoke_matrix.py # release smoke matrix 실행 결과 report 생성
│   ├── doctor_import_summary.py # importer summary JSON 운영/재시도 risk doctor
│   ├── report_run_directory.py # run directory artifact manifest 생성
│   ├── write_run_metadata.py # run directory 재현 metadata 생성
│   ├── doctor_run_directory.py # run directory 필수 산출물 completeness doctor
│   ├── embed_records_openai.py# OpenAI-compatible embedding command 예제
│   ├── smoke_public_warc.py    # public WARC parser/import smoke 하네스
│   ├── check_docs.py           # 문서 링크와 하네스 drift 검증
│   ├── preflight_quality_inputs.py # WARC/query/qrels와 real-corpus manifest read-only preflight
│   └── warc_importer.py       # WARC/WARC.GZ -> metadata/BM25/Lance SQL importer
├── src/
│   ├── lib.rs                 # SQL 함수 진입점 + extension SQL
│   ├── tokenizer.rs           # CJK bigram + ASCII tokenizer
│   ├── bm25.rs                # BM25 테이블 인덱스
│   ├── lance_store.rs         # Lance dataset create/insert/scan/vector search
│   └── hybrid.rs              # BM25 + vector RRF fusion
└── tests/
    ├── test.sql               # smoke 테스트 (라이브 컨테이너 대상)
    ├── test_warc_importer.py  # Python WARC importer 단위 테스트
    ├── test_eval_quality.py   # quality fixture 단위 테스트
    ├── test_execution_budget.py # 분석 실행 예산 gate 단위 테스트
    ├── test_make_quality_fixture.py # quality fixture builder 단위 테스트
    ├── test_doctor_quality_labels.py # quality label doctor 단위 테스트
    ├── test_report_quality_fixture.py # quality fixture report 단위 테스트
    ├── test_public_warc_quality_fixture.py # public WARC quality fixture 단위 테스트
    ├── test_check_release_artifacts.py # release artifact checker 단위 테스트
    ├── test_smoke_release_artifacts.py # release artifact install smoke 단위 테스트
    ├── test_smoke_release_upgrade.py # release artifact upgrade smoke 단위 테스트
    ├── test_doctor_release_upgrade_inputs.py # release upgrade 입력 preflight 단위 테스트
    ├── test_report_release_artifacts.py # release artifact matrix report 단위 테스트
    ├── test_report_release_smoke_matrix.py # release smoke matrix report 단위 테스트
    ├── test_doctor_import_summary.py # importer summary doctor 단위 테스트
    ├── test_report_run_directory.py # run directory manifest 단위 테스트
    ├── test_write_run_metadata.py # run directory metadata 단위 테스트
    ├── test_doctor_run_directory.py # run directory doctor 단위 테스트
    ├── test_compare_benchmark_runs.py # benchmark 회귀 비교 단위 테스트
    ├── fixtures/              # reusable Python/quality test fixtures
    ├── unit_tokenizer.rs      # tokenizer 단위 테스트
    └── pg_regress/            # pg_regress 회귀 테스트 하네스
        ├── sql/
        │   ├── setup.sql      # 회귀 DB 생성 직후 실행 (CREATE EXTENSION)
        │   ├── functions.sql  # 함수 회귀 테스트
        │   └── search.sql     # BM25/Lance/Hybrid 회귀 테스트
        └── expected/
            ├── setup.out      # setup 기대 출력
            ├── functions.out  # functions 기대 출력
            └── search.out     # search 기대 출력
```

## SQL 함수

`src/lib.rs`에 정의된 pgrx 함수 (SQL에서 호출 가능):

| 함수 | 설명 |
|------|------|
| `hello_pgwarc_lance()` | "Hello, pgwarc_lance!" 반환 |
| `add_numbers(a, b)` | 정수 합 |
| `fibonacci(n)` | 피보나치 (오버플로우 방지) |
| `array_sum(values)` | 배열 합 |
| `shout(text)` | 대문자 변환 |
| `greatest_of(a, b, c)` | 세 값 중 최대 |
| `is_even(n)` | 짝수 판별 |
| `word_count(text)` | 단어 수 |
| `double_or_none(n)` | Option 처리 예제 |
| `greeting(name, age=20)` | default 인수 예제 (`default!()` 매크로) |
| `tokenize_korean(text)` | CJK bigram + ASCII tokenization |
| `bm25_index_document(doc_id, content)` | 문서를 `pgwarc_lance.bm25_*` 테이블에 색인 |
| `bm25_search(query, k=10)` | BM25 검색 결과 `(doc_id, score)` 반환 |
| `lance_create_table(uri, vector_dim=3, overwrite=false)` | Lance dataset 생성 |
| `lance_insert(uri, id, vector, label)` | Lance dataset에 벡터 row 삽입 |
| `lance_insert_many(uri, ids, flat_vectors, vector_dim, labels)` | Lance dataset에 여러 벡터 row를 한 번의 batch append로 삽입 |
| `lance_scan(uri, limit=100)` | Lance row 스캔 |
| `lance_vector_dim(uri)` | Lance dataset의 vector 차원 반환 |
| `lance_vector_search(uri, query, k=10)` | Lance nearest-neighbor 검색 |
| `hybrid_search(query, vector_query, lance_uri, k=10)` | BM25와 벡터 검색을 RRF로 결합 |
| `hybrid_warc_search(query, vector_query, lance_uri, k=10)` | hybrid search 결과에 `warc_record` metadata를 결합해 반환 |
| `lance_dataset_versions(uri)` | dataset 버전 목록 `(version, created_ms)` 반환 (오래된 순) |
| `lance_restore_version(uri, version)` | 지정 버전을 최신 상태로 재승격 (롤백 보상 경로) |

`lance_create_table()`은 기존 1-argument 호출 호환성을 위해 기본 차원을 3으로 둔다. 실제 embedding 연동에서는 모델 차원에 맞춰 `vector_dim`을 명시한다. 테스트처럼 반복 실행이 필요한 경우에만 `overwrite=true`로 dataset을 재생성한다.

`pgwarc_lance.warc_record` metadata table은 importer가 적재한 WARC record의 URI, WARC date, content type, HTTP status, payload digest, text length, source file을 보관한다.

`pgwarc_lance.lance_write_log` table은 PostgreSQL이 **커밋한** Lance 쓰기(uri, op, row 수, 시각)를 남긴다. 아래의 트랜잭션 한계를 운영에서 reconciliation할 때 `lance_dataset_versions()`와 함께 사용한다.

### 트랜잭션·동시성 한계 (반드시 읽어라)

Lance dataset은 PostgreSQL이 관리하지 않는 파일이다. 따라서:

- **`ROLLBACK`은 이미 커밋된 Lance append를 되돌리지 않는다.** PostgreSQL 트랜잭션이 실패해도 파일에는 데이터가 남는다. 정합성이 필요한 서비스는 이 경계를 설계에 반영해야 한다.
- 되돌려야 하는 경우 `lance_dataset_versions()`로 목표 버전을 찾고 `lance_restore_version()`으로 복구한다. `pgwarc_lance.lance_write_log`가 어떤 쓰기가 커밋되었는지 알려준다.
- 동시 writer는 `pg_advisory_xact_lock`으로 dataset 경로별 직렬화된다. 트랜잭션이 끝나면 해제되므로 데드락은 발생하지 않지만, 대량 적재는 직렬로 수행된다.

### 보안/운영 설정 (GUC)

| GUC | 기본값 | 의미 |
|-----|--------|------|
| `pgwarc_lance.allowed_uri_prefix` | `NULL` | 비우면 호출자를 신뢰(기존 동작). 값을 주면 모든 Lance URI가 이 접두어로 시작해야 하며, 아니면 `insufficient_privilege`로 거부한다. | `pgwarc_lance.max_scan_rows` | `0`(무제한) | `lance_scan`이 백엔드 메모리에 담을 수 있는 최대 행 수. 초과 요청은 `program_limit_exceeded`로 거부한다.
| `pgwarc_lance.allow_destructive_ops` | `on` | `off`면 `lance_create_table(overwrite => true)`와 `lance_restore_version()`을 거부한다. 데이터가 실려 있는 환경에서는 `off`를 권장한다.

```sql
SET pgwarc_lance.allowed_uri_prefix = '/var/lib/pgwarc_lance/datasets';
SET pgwarc_lance.max_scan_rows = 10000;
SET pgwarc_lance.allow_destructive_ops = off;
```

추가 제약(슈퍼유저 설치 필요, 파일 경로 접근, Upon rollback 불가)은 [SECURITY.md](SECURITY.md)에 정리했다.

## 테스트/평가 하네스

| 하네스 | 명령 | 대상 | 실행 방식 |
|--------|------|------|-----------|
| **smoke** | `make test` | `tests/test.sql` | 라이브 컨테이너의 psql로 BM25/Lance/Hybrid 호출 검증 |
| **unit** | `make test-unit` | `tests/unit_tokenizer.rs` | `rustc --test` — DB/extension 링크 없는 순수 tokenizer 단위 테스트 |
| **warc** | `make test-warc` | `tests/test_*.py` | Python stdlib 기반 WARC parser/importer와 quality fixture 단위 테스트 |
| **warc-db** | `make test-warc-db` | `tests/db_warc_importer.py` | 작은 WARC.GZ fixture를 생성하고 importer-generated SQL을 라이브 DB에 실행 |
| **regress** | `make test-regress` | `tests/pg_regress/` | scalar 함수와 검색 코어의 pg_regress SQL 회귀 테스트 (입력/기대출력 diff) |
| **benchmark-finalize** | `make finalize-benchmark-run` | benchmark run finalization | BM25/Lance/WARC benchmark와 metadata, manifest, doctor를 한 run directory에 생성 |
| **benchmark-bm25-modes-finalize** | `make finalize-warc-bm25-modes-run` | targeted benchmark run finalization | WARC BM25 function/bulk/copy 비교와 metadata, manifest, doctor를 한 run directory에 생성 |
| **benchmark-compare** | `make compare-benchmark-runs` | benchmark regression comparison | 같은 WARC BM25 mode artifact의 실행 시간을 baseline과 비교하고 허용 회귀를 gate |
| **benchmark-compare-finalize** | `make finalize-benchmark-comparison-run` | benchmark comparison finalization | 비교 report와 metadata, manifest, doctor를 한 run directory에 생성 |
| **quality-fixture** | `make make-quality-fixture` | `tools/make_quality_fixture.py` | importer records/vector JSONL과 query labels를 eval fixture로 변환 |
| **quality-label-doctor** | `make doctor-quality-labels` | `tools/doctor_quality_labels.py` | query label JSON schema와 optional records doc_id coverage를 fixture 생성 전에 검사 |
| **quality-labels-finalize** | `make finalize-quality-labels-run` | quality label doctor + run finalization | query label doctor report, metadata, manifest, doctor를 한 run directory에 생성 |
| **quality-fixture-build-finalize** | `make finalize-quality-fixture-build-run` | fixture build + report + run finalization | records/vector/query labels에서 fixture와 fixture report를 만들고 run directory에 마감 |
| **quality-fixture-report** | `make report-quality-fixture` | `tools/report_quality_fixture.py` | quality fixture schema, query label coverage, 중복 doc/query 확인 |
| **quality-baseline-doctor** | `make doctor-quality-baseline` | `tools/doctor_quality_baseline.py` | quality fixture report와 eval metrics가 baseline 기준을 만족하는지 검사 |
| **quality-finalize** | `make finalize-quality-run` | quality report + eval + run finalization | quality fixture report, eval result, baseline doctor, metadata, manifest, doctor를 한 run directory에 생성 |
| **public-quality-finalize** | `make finalize-public-warc-quality-run` | public WARC quality + run finalization | public WARC fixture 생성부터 quality run directory 마감까지 실행 |
| **quality** | `make eval-quality` | `tools/eval_quality.py` | synthetic 또는 external relevance fixture로 hit-rate/MRR smoke eval |
| **quality-budget** | `make eval-quality-budget` | `tools/execution_budget.py` + `tools/eval_quality.py` | run-scoped iteration/query/deadline 예산을 검증하고 초과 시 `budget_exhausted` non-success로 중단 |
| **docs** | `make check-docs` | `tools/check_docs.py` | Markdown 링크, README 하네스 목록, roadmap 검증 명령 drift 확인 |
| **pg-version** | `make smoke-pg-version` | Dockerfile + `CREATE EXTENSION` | 선택한 PostgreSQL major image를 빌드하고 extension fresh install smoke 실행 |
| **release-artifact** | `make check-release-artifacts` | `tools/check_release_artifacts.py` | 추출된 `.so/.control/.sql` artifact 구조, version, SQL symbol 확인 |
| **release-install** | `make smoke-release-artifacts` | `tools/smoke_release_artifacts.py` | 추출된 artifact를 공식 Postgres 컨테이너에 복사해 `CREATE EXTENSION` 확인 |
| **release-upgrade** | `make smoke-release-upgrade` | `tools/smoke_release_upgrade.py` | old/new artifact와 upgrade SQL로 `ALTER EXTENSION UPDATE` 확인 |
| **release-upgrade-input-doctor** | `make doctor-release-upgrade-inputs` | `tools/doctor_release_upgrade_inputs.py` | old artifact와 versioned upgrade SQL 존재/버전을 DB·Docker 없이 preflight하고 누락 시 non-zero report |
| **release-matrix** | `make release-artifacts-matrix` | `PG_MATRIX` + `release-artifacts` | PostgreSQL major별 artifact 추출과 구조 검사를 순차 실행 |
| **release-install-matrix** | `make smoke-release-artifacts-matrix` | `PG_MATRIX` + `smoke-release-artifacts` | 기존 major별 artifact를 공식 Postgres 컨테이너에 설치 smoke |
| **release-upgrade-matrix** | `make smoke-release-upgrade-matrix` | `PG_MATRIX` + `smoke-release-upgrade` | 기존 major별 old/new artifact upgrade smoke |
| **release-report** | `make report-release-artifacts` | `tools/report_release_artifacts.py` | major별 artifact 검증 상태, 크기, SHA-256 report 생성 |
| **release-smoke-report** | `make report-release-smoke-matrix` | `tools/report_release_smoke_matrix.py` | major별 release smoke 실행 결과, exit code, 로그 tail report 생성 |
| **release-upgrade-smoke-report** | `make report-release-upgrade-smoke-matrix` | `tools/report_release_upgrade_smoke_matrix.py` | `RELEASE_SMOKE_TARGET=smoke-release-upgrade`일 때 major별 upgrade preflight(doctor)을 먼저 강제하고 blocked major는 smoke 없이 non-zero report |
| **release-finalize** | `make finalize-release-run` | release reports + run finalization | artifact matrix report, smoke matrix report, metadata, manifest, doctor를 한 run directory에 생성 |
| **import-doctor** | `make doctor-import-summary` | `tools/doctor_import_summary.py` | importer `--summary-json` schema, 실행 여부, Lance append retry risk 확인 |
| **import-finalize** | `make finalize-import-run` | import summary doctor + run finalization | import summary, import doctor, metadata, manifest, run doctor를 한 run directory에 생성 |
| **run-manifest** | `make report-run-directory` | `tools/report_run_directory.py` | `RUN_DIR` 산출물 파일 크기와 SHA-256 manifest 생성 |
| **run-metadata** | `make write-run-metadata` | `tools/write_run_metadata.py` | `RUN_DIR`의 git commit, dirty 상태, command, 선택 env 기록 |
| **run-doctor** | `make doctor-run-directory` | `tools/doctor_run_directory.py` | `RUN_DIR` profile별 필수 산출물, manifest 무결성, 알려진 report status/schema 확인 |
| **run-finalize** | `make finalize-run-directory` | run metadata + manifest + doctor | `RUN_DIR` 마감 산출물을 정해진 순서로 생성 |
| **all** | `make test-all` | 위 하네스 순차 실행 | |
| **verify** | `make verify` | `test-all` + quality eval + docs check + Python syntax + whitespace diff | 로컬 CI 대체 검증 |

- 회귀 하네스는 pgrx 관리 Postgres 인스턴스를 사용하므로 **root 불가** → 실행 중인 컨테이너 안에서 `postgres` 유저로 실행 (Makefile이 `--user postgres` + `USER`/`RUSTUP_HOME`/`CARGO_HOME` 환경변수를 자동 지정). cargo-pgrx 0.19의 pg_regress launcher가 이 환경에서 불안정해 `make test-regress`는 `cargo pgrx install` 후 PostgreSQL `pg_regress`를 직접 호출한다. unit 하네스는 Lance/DataFusion 전체 링크를 피하기 위해 tokenizer 모듈만 `rustc --test`로 직접 컴파일한다.
- smoke 하네스는 최신 extension SQL 함수를 보장하기 위해 테스트 DB의 `pgwarc_lance` extension을 drop/create 한다.
- 회귀 하네스의 `expected/*.out`은 이미지에 구워져 비교에 사용된다.

## 사용 방법

### 빌드 및 실행

```bash
make build      # Docker 이미지 빌드 (pgwarc_lance 익스텐션 컴파일 + 설치 + 테스트 하네스 포함)
make up         # PostgreSQL 컨테이너 기동 (포트 55432)
make status     # 컨테이너 상태 확인
```

Lance/DataFusion 의존성 때문에 첫 Docker 빌드는 오래 걸린다. 이후 빌드는 Docker layer cache가 유지되면 훨씬 짧아진다.

### 접속

```bash
make psql                                            # 컨테이너 내 psql 진입
psql -h localhost -p 55432 -U pgwarc_lance -d pgwarc_lance_test    # 호스트에서 접속 (비밀번호: pgwarc_lance)
```

### 테스트 (하네스)

```bash
make test        # smoke: tests/test.sql 실행 (라이브 DB)
make test-unit   # unit: rustc --test tests/unit_tokenizer.rs
make test-warc   # warc: Python WARC importer 테스트
make test-warc-db # warc-db: importer-generated SQL을 라이브 DB에 실행
make test-regress # regress: cargo pgrx install + direct pg_regress
make test-all    # 전체 하네스 순차 실행
BENCH_OUT_DIR=benchmarks/local make finalize-benchmark-run # benchmark run directory 마감
BENCH_OUT_DIR=benchmarks/local make finalize-warc-bm25-modes-run # BM25 modes benchmark run directory 마감
BENCHMARK_BASELINE_JSON=/path/to/baseline/warc_bm25_modes.json BENCHMARK_CANDIDATE_JSON=/path/to/candidate/warc_bm25_modes.json BENCHMARK_MAX_REGRESSION_RATIO=0.10 make compare-benchmark-runs # baseline 대비 실행 시간 회귀 gate
BENCHMARK_BASELINE_JSON=/path/to/baseline/warc_bm25_modes.json BENCHMARK_CANDIDATE_JSON=/path/to/candidate/warc_bm25_modes.json BENCHMARK_MAX_REGRESSION_RATIO=0.10 BENCH_OUT_DIR=benchmarks/comparison make finalize-benchmark-comparison-run # 비교 report/metadata/manifest/doctor 마감
make make-quality-fixture # quality fixture builder: env var로 입력 경로 지정
make doctor-quality-labels # quality query label doctor: env var로 입력 경로 지정
BENCH_OUT_DIR=benchmarks/local make finalize-quality-labels-run # quality label doctor run directory 마감
BENCH_OUT_DIR=benchmarks/local make finalize-quality-fixture-build-run # records/vectors/labels -> fixture/report run directory 마감
make report-quality-fixture # quality fixture schema/label coverage report
BENCH_OUT_DIR=benchmarks/local make finalize-quality-run # quality run directory 마감
BENCH_OUT_DIR=benchmarks/local make doctor-quality-baseline # quality baseline threshold gate
BENCH_OUT_DIR=benchmarks/local make finalize-public-warc-quality-run # optional network: public WARC quality run 마감
make public-warc-quality-fixture # optional network: public WARC labeled fixture 생성
make eval-quality # quality: synthetic relevance eval
make eval-public-warc-quality # optional network+DB: public WARC labeled eval
make check-docs  # docs: Markdown 링크와 하네스 문서화 drift 확인
make smoke-public-warc # optional network smoke: pywb public WARC sample
PG_MAJOR=17 make smoke-pg-version # optional: 특정 PostgreSQL major CREATE EXTENSION smoke
make pg-version-matrix # optional: PG13/14/15/16/17 smoke matrix
make check-release-artifacts # release-artifacts 산출물 구조/버전 검사
make smoke-release-artifacts # release-artifacts 산출물을 clean Postgres에 설치 smoke
make doctor-release-upgrade-inputs # old artifact/upgrade SQL preflight (legitimate 입력 필요)
UPGRADE_OLD_VERSION=0.0.9 make smoke-release-upgrade # old->current ALTER EXTENSION smoke
make release-artifacts-matrix # optional: PG_MATRIX major별 artifact 추출+검사
make smoke-release-artifacts-matrix # optional: PG_MATRIX major별 artifact install smoke
UPGRADE_OLD_VERSION=0.0.9 make smoke-release-upgrade-matrix # optional: PG_MATRIX upgrade smoke
make report-release-artifacts # optional: PG_MATRIX artifact report 생성
make report-release-smoke-matrix # optional: PG_MATRIX release smoke 실행 결과 report 생성
RELEASE_SMOKE_TARGET=smoke-release-upgrade make report-release-smoke-matrix # upgrade target은 major별 preflight(doctor)을 강제 (legitimate 입력 필요)
BENCH_OUT_DIR=benchmarks/local make finalize-release-run # optional: release reports + run directory 마감
IMPORT_SUMMARY=import.summary.json make doctor-import-summary # importer summary JSON doctor
IMPORT_SUMMARY=import.summary.json BENCH_OUT_DIR=benchmarks/local make finalize-import-run # import run directory 마감
BENCH_OUT_DIR=benchmarks/local make report-run-directory # run directory artifact manifest
BENCH_OUT_DIR=benchmarks/local RUN_METADATA_COMMAND='make bench' make write-run-metadata
BENCH_OUT_DIR=benchmarks/local RUN_DOCTOR_PROFILE=benchmark make doctor-run-directory
BENCH_OUT_DIR=benchmarks/local RUN_METADATA_COMMAND='make bench' RUN_DOCTOR_PROFILE=benchmark make finalize-run-directory
make verify      # 전체 하네스 + quality eval + py_compile + git diff --check
```

### 로컬 검증 기준

이 프로젝트는 현재 GitHub Actions 같은 원격 CI를 전제로 하지 않는다. 변경 후 기본 검증은 로컬 Docker PostgreSQL 16 환경에서 `make verify`를 실행하는 것이다. 빠른 확인만 필요하면 `make check-docs`, `make test-unit`, `make test-warc`를 먼저 실행하고, DB/extension 표면을 건드린 변경은 `make test-regress`, `make test`, `make test-warc-db`까지 확인한다. PostgreSQL major 호환성 표면을 건드린 변경은 선택적으로 `PG_MAJOR=<13..17> make smoke-pg-version` 또는 `make pg-version-matrix`를 실행한다.
패키징 산출물을 만들었으면 `make release-artifacts`가 자동으로 `make check-release-artifacts`를 호출한다. 기존 `DIST_DIR`만 다시 검사하려면 `make check-release-artifacts`를 별도로 실행한다.
추출된 artifact가 build image 밖에서도 설치되는지 확인하려면 `make smoke-release-artifacts`를 실행한다. 이 target은 `DIST_DIR` artifact를 공식 `postgres:<PG_MAJOR>` 컨테이너에 복사한 뒤 `CREATE EXTENSION pgwarc_lance`와 `hello_pgwarc_lance()`를 실행한다.
버전 bump 뒤 upgrade SQL까지 확인하려면 먼저 `make doctor-release-upgrade-inputs`로 old artifact와 `pgwarc_lance--<old>--<new>.sql` upgrade script가 실제로 존재하는지 DB·Docker 없이 preflight한다. 이 doctor는 현재 version(`0.1.5`)과 `dist/`에 실제로 추출된 artifact version만 보고하며, old artifact나 upgrade SQL이 없으면 `valid=false`/`status=blocked` report를 남기고 non-zero로 종료해 `ALTER EXTENSION UPDATE` 미측정 상태를 명시한다. 입력이 모두 있으면 `UPGRADE_OLD_VERSION=<old> make smoke-release-upgrade`로 old version을 먼저 설치한 뒤 `ALTER EXTENSION UPDATE TO`를 실행한다. 기본 old artifact 경로는 `dist/pgwarc_lance-<old>-pg<PG_MAJOR>/`이고, new artifact는 현재 `DIST_DIR`을 사용한다. 저장소에는 0.1.0→0.1.1, 0.1.1→0.1.2, 0.1.2→0.1.3, 0.1.3→0.1.4, 0.1.4→0.1.5 upgrade SQL이 있다. 이전 바이너리는 별도로 보관해야 한다. PG16의 populated upgrade/backup과 0.1.4→0.1.5 old/new 바이너리 교체 검증은 통과했으며, 다른 major는 별도 검증이 필요하다. `RELEASE_SMOKE_TARGET=smoke-release-upgrade`로 실행하는 `make report-release-smoke-matrix`와 `make finalize-release-run`은 이 preflight을 major별로 자동 강제하므로, doctor를 건너뛰고 upgrade smoke를 실행할 수 없다.
PG13-17 전체 산출물 baseline을 만들 때는 `make release-artifacts-matrix`를 실행하고, 이미 추출된 major별 산출물을 runtime image에서 다시 확인할 때는 `make smoke-release-artifacts-matrix`를 실행한다. Version bump 뒤 major별 upgrade smoke를 반복하려면 `UPGRADE_OLD_VERSION=<old> make smoke-release-upgrade-matrix`를 실행한다.
Matrix 산출물의 검증 상태와 checksum을 남기려면 `BENCH_OUT_DIR=benchmarks/local make report-release-artifacts`를 실행해 JSON/Markdown report를 생성한다.
Matrix install smoke 실행 결과를 major별 baseline으로 남기려면 `BENCH_OUT_DIR=benchmarks/local make report-release-smoke-matrix`를 실행한다. 기본 target은 `smoke-release-artifacts`이며, version bump 뒤 upgrade smoke를 같은 형식으로 남기려면 `RELEASE_SMOKE_TARGET=smoke-release-upgrade RELEASE_SMOKE_MAKE_VARS='UPGRADE_OLD_VERSION=<old>' make report-release-smoke-matrix`를 사용한다. Upgrade target일 때 `report-release-smoke-matrix`는 `report-release-upgrade-smoke-matrix`로 위임해 `PG_MATRIX`의 각 major에 대해 `tools/doctor_release_upgrade_inputs.py`를 같은 old/new dist/version/pg_major 입력으로 먼저 실행하고, `valid=false`인 major는 `smoke-release-upgrade`를 실행하지 않는다. 이때 report는 `release_smoke_matrix.json` schema를 유지하면서 각 major result에 `preflight`/`preflight_passed`/`smoke_skipped`/`smoke_skipped_reason` field를 추가하고, blocked major가 하나라도 있으면 non-zero로 종료한다. 이는 하네스 검증이며 versioned upgrade 성공 증거가 아니다.
Release artifact report와 smoke matrix report를 같은 run directory에 마감하려면 `BENCH_OUT_DIR=benchmarks/local make finalize-release-run`을 실행한다. 이 target은 `report-release-artifacts`, `report-release-smoke-matrix`, `finalize-run-directory`를 순서대로 실행하고 `RUN_DOCTOR_PROFILE=packaging`으로 completeness를 확인한다. Run doctor는 `release_artifacts_matrix.json`의 artifact rows/per-file size/SHA-256 schema와 `release_smoke_matrix.json`의 per-major result schema도 검사한다. Upgrade target이면 두 번째 단계가 preflight을 강제하므로, legitimate old artifact와 upgrade SQL이 없는 현재 상태에서는 `ALTER EXTENSION UPDATE` smoke를 시도하기 전에 non-zero로 중단된다.
Quality query labels를 fixture로 만들기 전에 schema와 label coverage를 확인하려면 `QUALITY_LABELS_JSON=queries.json make doctor-quality-labels`를 실행한다. `QUALITY_LABELS_RECORDS_JSONL=records.jsonl`을 함께 주면 label의 `expected_doc_ids`가 실제 record `doc_id`에 존재하는지 확인하고 unlabeled control doc 수를 report에 남긴다. `BENCH_OUT_DIR=benchmarks/local`을 주면 `quality_labels_doctor.json`과 `quality_labels_doctor.md`가 생성된다. Label doctor report를 metadata/manifest/doctor와 함께 run directory로 마감하려면 `QUALITY_LABELS_JSON=queries.json QUALITY_LABELS_RECORDS_JSONL=records.jsonl BENCH_OUT_DIR=benchmarks/local make finalize-quality-labels-run`을 실행한다. 이 target은 `quality_labels_doctor.json`을 필수 artifact로 요구하고 run doctor가 `valid=true`도 확인한다.
Quality fixture를 DB에 적재하기 전에 schema와 label coverage를 남기려면 `QUALITY_FIXTURE=quality.fixture.json BENCH_OUT_DIR=benchmarks/local make report-quality-fixture`를 실행한다. 기본값은 synthetic fixture이며, `QUALITY_FIXTURE_MIN_DOCS`와 `QUALITY_FIXTURE_MIN_QUERIES`로 최소 doc/query 수를 높일 수 있다. WARC schema v2 provenance까지 검사하려면 `QUALITY_PROVENANCE_JSON=warc_provenance.json QUALITY_MIN_SOURCE_DOCS=1 QUALITY_MAX_SOURCE_SKIPPED_RATIO=0.5`를 추가한다. Run doctor는 required `quality_fixture_report.json`의 count fields와 query label rows, schema v2 source coverage 합계/ratio/threshold 계약도 재검증한다.
Records, vectors, query labels에서 fixture와 fixture report까지 DB 없이 만들고 run directory로 마감하려면 `QUALITY_RECORDS_JSONL=records.jsonl QUALITY_EMBEDDING_JSONL=vectors.jsonl QUALITY_QUERIES_JSON=queries.json BENCH_OUT_DIR=benchmarks/local make finalize-quality-fixture-build-run`을 실행한다. 이 target은 label doctor, fixture build, fixture report, run finalization을 순서대로 실행하고 `quality_labels_doctor.json`, `quality.fixture.json`, `quality_fixture_report.json`을 필수 artifact로 요구한다. Run doctor는 required `quality.fixture.json`의 fixture schema, vector dimension, expected doc ids, duplicate doc/query도 검사한다.
Quality eval 결과까지 같은 run directory에 마감하려면 `QUALITY_FIXTURE=quality.fixture.json BENCH_OUT_DIR=benchmarks/local make finalize-quality-run`을 실행한다. 이 target은 fixture report, `eval-quality`, `doctor-quality-baseline`, `finalize-run-directory`를 순서대로 실행하고 `RUN_DOCTOR_PROFILE=quality`와 `QUALITY_RUN_DOCTOR_REQUIRE=quality_baseline_doctor.json`로 completeness를 확인한다. Run doctor는 `quality.json`의 benchmark name, doc/query/vector/k counts, hit-rate/MRR, query result rows도 검사한다.
`QUALITY_PROVENANCE_JSON=<manifest.json>`을 함께 지정하면 `finalize-quality-run`이 입력 manifest를 `<RUN_DIR>/warc_provenance.json`으로 stage하고 report도 stage된 복사본을 참조한다. 따라서 real-corpus baseline은 provenance를 run directory와 manifest/doctor에 함께 보존하며, 입력 manifest가 없거나 stage된 artifact가 drift하면 finalization이 실패한다.
`QUALITY_INPUT_PREFLIGHT_REQUIRED=1`을 지정하면 `finalize-quality-run`이 기존 `make preflight-quality-inputs`를 quality 산출물 확정 전에 실행한다. `QUALITY_BASELINE_REQUIRE_REAL_CORPUS=1`도 이 gate를 자동 활성화하며, corpus/query/qrels 입력이 없거나 manifest doctor가 거부하면 preflight report만 남기고 fixture/eval/baseline/final manifest를 만들지 않는다. 이 gate는 read-only 입력 준비성만 확인하고 quality 측정이나 real evidence를 주장하지 않는다.
이미 생성된 quality run을 더 엄격한 baseline 기준으로 다시 검사하려면 `BENCH_OUT_DIR=benchmarks/local make doctor-quality-baseline`을 실행한다. 이 target은 `quality_fixture_report.json`과 `quality.json`을 읽어 최소 doc/query/label 수와 `QUALITY_BASELINE_MIN_HIT_RATE`, `QUALITY_BASELINE_MIN_MRR` 기준을 `quality_baseline_doctor.json`/`.md`로 남긴다. WARC schema v2 report가 입력이면 source coverage와 source threshold도 baseline artifact/표에 보존하며, Run doctor가 그 구조와 합계를 다시 검사한다.
실제 WARC baseline을 synthetic/public smoke와 구분하려면 `QUALITY_BASELINE_REQUIRE_REAL_CORPUS=1`을 지정한다. 이 모드는 schema v2 provenance, source coverage, 20–50개 query, 0보다 큰 hit-rate/MRR threshold를 요구하며, 실행한 flag와 threshold도 run metadata에 남긴다.
실제 labeled WARC corpus를 확보하기 전에 입력 자체를 검증하려면 real corpus input manifest를 작성하고 `tools/doctor_real_corpus_input.py`를 `REAL_CORPUS_INPUT_JSON=real_corpus_input.json BENCH_OUT_DIR=benchmarks/local make doctor-real-corpus-input`으로 실행한다. 이 doctor는 corpus source identity(path/URL, bytes, SHA-256), 사용 권한(`license`, `license_url`, `redistribution`), query/qrels 버전, label 독립성, 평가 commit, PostgreSQL 버전, 검색 설정을 요구하고, local source와 label 파일이 존재하면 현재 bytes/SHA-256을 다시 계산해 drift를 오류로 처리한다. `corpus.origin`은 `external`만, `labels.independence`는 `external`만 허용하므로 synthetic/public smoke나 self-labeled relevance는 real baseline 입력으로 통과하지 않는다. Report는 `real_corpus_input_doctor.json`/`.md`로 남는다. 현재 저장소에는 이 계약을 만족하는 실제 labeled corpus가 없어 Issue A의 real baseline 점수는 미측정(blocked)이며, `QUALITY_BASELINE_REQUIRE_REAL_CORPUS=1` finalization을 실행하려면 이 manifest와 독립 query label JSON이 먼저 필요하다.
Public WARC quality smoke도 같은 방식으로 닫으려면 `BENCH_OUT_DIR=benchmarks/local make finalize-public-warc-quality-run`을 실행한다. 이 target은 public WARC fixture와 `records.jsonl`, `vectors.jsonl`, `quality_queries.json` 중간 산출물을 run directory에 쓰고 `finalize-quality-run`으로 이어간다. 여러 labeled query를 사용하려면 `PUBLIC_WARC_QUERY_SPEC_JSON=<path>`를 추가하고, 여러 WARC 입력을 합치려면 `PUBLIC_WARC_SOURCE_SPEC_JSON=<path>`를 추가한다.
Importer `--summary-json` 산출물을 운영/재시도 관점에서 확인하려면 `IMPORT_SUMMARY=import.summary.json make doctor-import-summary`를 실행한다. `BENCH_OUT_DIR=benchmarks/local`을 주면 `import_doctor.json`과 `import_doctor.md`도 함께 남긴다. Summary, import doctor, run metadata/manifest/doctor를 한 run directory에 마감하려면 `IMPORT_SUMMARY=import.summary.json BENCH_OUT_DIR=benchmarks/local make finalize-import-run`을 실행한다. 이 target은 `IMPORT_RUN_DOCTOR_REQUIRE=import_doctor.json`로 import doctor artifact까지 필수로 요구하며, run doctor도 `import.summary.json`의 importer summary schema와 `import_doctor.json`의 report count/summary row schema를 검사한다.
벤치마크, quality, import doctor, release report 같은 산출물을 한 디렉토리에 모았으면 `BENCH_OUT_DIR=benchmarks/local make report-run-directory`로 `run_manifest.json`과 `run_manifest.md`를 생성해 파일 크기와 SHA-256 manifest를 남긴다. `RUN_DIR=<dir>`로 `BENCH_OUT_DIR`과 별도 디렉토리를 지정할 수도 있다.
같은 run directory에 재현 metadata를 남기려면 `BENCH_OUT_DIR=benchmarks/local RUN_METADATA_COMMAND='make bench' make write-run-metadata`를 실행한다. 이 target은 현재 git commit, branch, dirty 상태, 명령 문자열, 기본 실험 env 값을 `run_metadata.json`과 `run_metadata.md`로 저장한다. `TOKEN`, `SECRET`, `PASSWORD`, `KEY`, `COOKIE`가 포함된 env 이름은 값이 자동으로 redaction된다.
run directory가 필요한 산출물을 갖췄는지 확인하려면 `BENCH_OUT_DIR=benchmarks/local RUN_DOCTOR_PROFILE=benchmark make doctor-run-directory`를 실행한다. profile은 `generic`, `benchmark`, `quality`, `import`, `packaging`이며, `quality` profile은 `quality_fixture_report.json`과 `quality.json`, `import` profile은 `import.summary.json`, `packaging` profile은 `release_artifacts_matrix.json`과 `release_smoke_matrix.json`을 요구한다. `RUN_DOCTOR_REQUIRE='extra.json extra.md'`로 필수 파일을 추가할 수 있다. Doctor는 `run_metadata.json`의 schema version, generated metadata, command/environment, git metadata, warning list schema를 검사한다. 또한 `run_manifest.json`에 기록된 파일이 실제로 존재하는지 확인하고, manifest entry에 `bytes`/`sha256`이 있으면 현재 파일 크기와 SHA-256이 일치하는지도 검사한다. `benchmark` profile에서 `RUN_DOCTOR_REQUIRE`로 `bm25.json`, `lance.json`, `warc.json`, `warc_bm25_modes.json` 같은 알려진 benchmark JSON을 요구하면 파일 존재뿐 아니라 benchmark name, non-empty result, 핵심 numeric field도 검사한다. `import` profile이나 `RUN_DOCTOR_REQUIRE`로 요구된 `import.summary.json`은 importer summary schema, BM25/Lance mode enum, execute/psql contract도 검사하고, required `import_doctor.json`은 top-level counts와 per-summary row consistency를 검사한다. `packaging` profile의 `release_artifacts_matrix.json`은 major rows, all_valid consistency, per-file exists/bytes/SHA-256 schema를 검사하고, `release_smoke_matrix.json`은 generated metadata, per-major result rows, all_passed/passed/exit-code consistency를 검사한다. 결과는 `run_doctor.json`과 `run_doctor.md`로 저장된다.
run directory를 한 번에 마감하려면 `BENCH_OUT_DIR=benchmarks/local RUN_METADATA_COMMAND='make bench' RUN_DOCTOR_PROFILE=benchmark make finalize-run-directory`를 실행한다. 이 target은 metadata를 쓰고, manifest를 만들고, doctor를 실행한 뒤 doctor output까지 포함되도록 manifest를 한 번 더 갱신한다.

### 미래 청사진

[BLUEPRINT.md](BLUEPRINT.md)는 현재 완료된 Phase 1-85 이후의 작업 선택 기준을 다룬다. Direction Review Gate에 따라 real corpus/live DB 품질 증거(Issue A), 성능·release/upgrade 증거(Issue B), 분석 하네스 안전성과 8시간 루프 완료 게이트(Issue C)를 순서대로 닫고, 그 전까지 새 parser edge-case phase는 동결한다. Phase 82는 Issue C의 첫 slice로 run-scoped 실행 예산 gate를 추가했지만, 하네스 자체(`kilo-gpt-report-harness`)의 read-only 강제·완료 게이트 전면 적용은 저장소 밖이라 미적용이다. Phase 82 correction에서 query loop의 이중 charge ordering bug를 수정했다: `ExecutionBudget.charge_query_unit()`이 iteration/query 한도를 먼저 함께 검사하고 통과 시에만 원자적으로 차감해 어느 한도가 먼저 걸려도 차단된 N+1 시도가 카운터를 소비하지 않는다. Phase 82 timeout wiring slice는 `--query-timeout-seconds`/`QUALITY_QUERY_TIMEOUT_SECONDS`를 setup/query psql subprocess에 연결하고, run budget이 있으면 남은 deadline과 명시 timeout 중 더 짧은 값을 쓰며, timeout 시 `quality_eval_timeout` non-success artifact(exit 4)만 남기고 서버 측 query cancellation은 보장하지 않는다. Phase 82 timeout observation slice는 `timeout.timeout_observation`으로 statement outcome과 unobserved backend state를 분리하고 client timeout의 서버 outcome 승격을 막는다. 다음 청사진은 backend 상태 관측/active cancellation을 session identity, 별도 권한 connection, target/timeout race, budget, rollback/transaction cleanup 판정 기준과 함께 별도 계약으로 다룬다. 이 문단은 Phase 82 당시 상태를 기록한다. 이후 PG16 upgrade 성공과 공개 corpus 기준선은 [PRODUCTION.md](PRODUCTION.md)에 기록했다.

Phase 83은 real-corpus quality finalization이 read-only input preflight를 우회하지 않도록 `QUALITY_INPUT_PREFLIGHT_REQUIRED`와 `QUALITY_BASELINE_REQUIRE_REAL_CORPUS` 경로를 연결하고, preflight 실패 시 완료 산출물을 만들지 않는 회귀 테스트와 문서 계약을 추가한다. 실제 labeled WARC corpus/qrels가 없다는 Issue A blocker와 Issue B의 old/new artifact·upgrade SQL blocker는 유지한다.

Phase 84는 timeout 이후 backend 상태 판정을 런타임 없이 fold하는 pure deterministic 계약(`tools/quality_timeout_state_decision.py`)으로 고정했고, Phase 85(개발 의사결정(2026-09-15))는 그 위에 관측 적격성과 취소 적격성을 분리 판정하는 default-deny 합성 opt-in 계획 계약(`tools/quality_synthetic_optin_plan.py`, `python3 tools/quality_synthetic_optin_plan.py --input-json ...`)을 추가한다. opt-in 누락·조건 누락/미지/모순은 계획을 거부하고, 취소 계획은 별도 요청, 실행 시점 재검증 명시, 단일 attempt 대상, identity 일치, 유한 예산, 만료 없음을 요구한다. 계획 산출물은 `execution_authority=none`과 `not_claimed` claims만 가지며 실제 backend 종료·취소·rollback·transaction cleanup을 주장하지 않는다. 이 contract는 opt-in이고 `eval-quality`/run finalization/`make verify`는 호출하지 않는다. 캡처된 `session_identity.backend_start`가 request `decision_time` 이후인 모순 request는 `invalid_request`로 거부된다. 실제 labeled WARC corpus/qrels(Issue A)와 old/new artifact·upgrade SQL(Issue B) blocker는 유지한다.
### 검색 품질 평가

```bash
make eval-quality
BENCH_OUT_DIR=benchmarks/local make eval-quality
QUALITY_FIXTURE=tests/fixtures/quality_fixture.json make eval-quality
QUALITY_LABELS_JSON=tests/fixtures/quality_queries.json make doctor-quality-labels
QUALITY_LABELS_JSON=tests/fixtures/quality_queries.json BENCH_OUT_DIR=benchmarks/local make finalize-quality-labels-run
QUALITY_RECORDS_JSONL=records.jsonl QUALITY_EMBEDDING_JSONL=vectors.jsonl QUALITY_QUERIES_JSON=queries.json BENCH_OUT_DIR=benchmarks/local make finalize-quality-fixture-build-run
QUALITY_FIXTURE=tests/fixtures/quality_fixture.json make report-quality-fixture
QUALITY_FIXTURE=tests/fixtures/quality_fixture.json BENCH_OUT_DIR=benchmarks/local make finalize-quality-run
BENCH_OUT_DIR=benchmarks/local make doctor-quality-baseline
REAL_CORPUS_INPUT_JSON=real_corpus_input.json BENCH_OUT_DIR=benchmarks/local make doctor-real-corpus-input
QUALITY_INPUT_PREFLIGHT_CORPUS=corpus/part-000.warc.gz QUALITY_INPUT_PREFLIGHT_QUERIES_JSON=queries.json QUALITY_INPUT_PREFLIGHT_QRELS_JSON=qrels.json QUALITY_INPUT_PREFLIGHT_MANIFEST_JSON=real_corpus_input.json BENCH_OUT_DIR=benchmarks/local make preflight-quality-inputs
PUBLIC_WARC_QUALITY_FIXTURE=/tmp/pywb-public.fixture.json make public-warc-quality-fixture
BENCH_OUT_DIR=benchmarks/local make finalize-public-warc-quality-run
QUALITY_FIXTURE=/tmp/pywb-public.fixture.json make eval-quality
make eval-public-warc-quality
```

`eval-quality`는 개발 DB의 extension을 drop/create한 뒤 labeled relevance corpus를 적재하고 `hybrid_warc_search()` 결과의 hit-rate/MRR을 측정한다. 기본값은 내장 synthetic fixture이며, `QUALITY_FIXTURE=<path>`로 외부 JSON fixture를 지정할 수 있다. `doctor-quality-labels`는 fixture 생성 전에 query label JSON의 schema, vector dimension, duplicate query name, optional record `doc_id` coverage를 검사한다. `make-quality-fixture`는 importer `--records-jsonl`, embedding JSONL, query label JSON을 같은 schema의 external fixture로 묶는다.

```bash
QUALITY_RECORDS_JSONL=records.jsonl \
QUALITY_EMBEDDING_JSONL=vectors.jsonl \
QUALITY_QUERIES_JSON=queries.json \
QUALITY_FIXTURE_OUTPUT=quality.fixture.json \
make make-quality-fixture

QUALITY_FIXTURE=quality.fixture.json BENCH_OUT_DIR=benchmarks/local make report-quality-fixture
QUALITY_FIXTURE=quality.fixture.json make eval-quality
QUALITY_FIXTURE=quality.fixture.json BENCH_OUT_DIR=benchmarks/local make finalize-quality-run
BENCH_OUT_DIR=benchmarks/local make doctor-quality-baseline
```

현재 기준선과 tokenizer/BM25/RRF 품질 결정은 [QUALITY.md](QUALITY.md)에 기록한다.

`public-warc-quality-fixture`는 같은 public WARC sample에서 `Example Domain` label을 가진 작은 quality fixture를 만든다. 기본 CLI 인자는 하위 호환을 위해 한 query와 한 WARC 입력을 만들고, 여러 query가 필요하면 `--query-spec-json`에 `[{"name": "...", "query": "...", "label_text": "..."}]` 배열을 넘긴다. 여러 WARC 입력을 합칠 때는 `--source-spec-json`에 `[ {"path": "local.warc"}, {"url": "https://example.test/other.warc"} ]` 배열을 넘긴다. 각 source는 path 또는 url 중 정확히 하나를 가져야 하며, 둘을 동시에 지정하면 입력 오류로 거부한다. 입력별 path/URL, bytes, SHA-256, 다운로드 시각이 schema v2 corpus manifest의 `sources` 목록에 기록된다. 각 source에는 raw/imported/skipped record 수와 labeled/unlabeled doc coverage도 남는다. Make target에서는 각각 `PUBLIC_WARC_QUERY_SPEC_JSON=<path>`와 `PUBLIC_WARC_SOURCE_SPEC_JSON=<path>`로 같은 계약을 사용할 수 있다. `--manifest-json` 또는 `PUBLIC_WARC_PROVENANCE_JSON=<path>`를 지정하면 query spec과 fixture 요약까지 provenance manifest로 남기며, 단일 입력의 기존 manifest schema v1도 유지한다. `QUALITY_MIN_SOURCE_DOCS`와 `QUALITY_MAX_SOURCE_SKIPPED_RATIO`를 지정하면 `finalize-public-warc-quality-run`의 fixture report가 source coverage threshold도 검사한다. `BENCH_OUT_DIR=benchmarks/local make finalize-public-warc-quality-run`은 기본적으로 `warc_provenance.json`도 run directory에 남긴다. `eval-public-warc-quality`는 fixture 생성 뒤 `eval-quality`까지 실행한다. `smoke-public-warc`와 public quality targets는 외부 네트워크와 GitHub raw URL에 의존하므로 `make verify`에는 포함하지 않는다.
`finalize-public-warc-quality-run`의 run doctor는 required `warc_provenance.json`도 독립적으로 재검증한다. schema v1/v2 source identity, source/query-spec/fixture count, source별 raw/imported/skipped·coverage 합계를 확인하고, 기록된 local path 또는 spec/query-spec 파일이 현재 존재하면 bytes/SHA-256 drift를 오류로 보고한다. URL 다운로드 source나 삭제된 외부 파일은 네트워크를 다시 호출하지 않고 저장된 provenance만 보존하므로, 이 경우 현재 파일과의 독립 비교가 되지 않았다는 한계를 함께 기록해야 한다.

### 분석 실행 예산 (Issue C 하네스 경계)
입력 파일을 실제 평가 전에 read-only로 확인하려면 QUALITY_INPUT_PREFLIGHT_CORPUS, QUALITY_INPUT_PREFLIGHT_QUERIES_JSON, QUALITY_INPUT_PREFLIGHT_QRELS_JSON를 지정해 make preflight-quality-inputs를 실행한다. 실제 real-corpus manifest가 있으면 QUALITY_INPUT_PREFLIGHT_MANIFEST_JSON으로 넘겨 기존 doctor_real_corpus_input 검증을 같은 report에 연결할 수 있고, 이 manifest가 source/label provenance 입력을 대신한다. 이 preflight는 입력 파일의 bytes/SHA-256, 기존 query-label/records/qrels 참조 일관성, provenance/label-origin과 manifest doctor 결과만 검사하며 PostgreSQL, WARC import, quality query, 다운로드, fixture 생성은 실행하지 않는다. manifest가 없거나 doctor가 실행되지 않으면 unresolved로 남고, 필수 입력이나 제공된 manifest가 유효하지 않으면 blocked/non-zero report를 남긴다. qrels 독립성·진위와 실제 corpus 대표성은 계속 unresolved이며, report가 valid=true여도 real baseline이나 quality 측정을 의미하지 않는다.

Implementation: tools/preflight_quality_inputs.py. Its report is preflight evidence only and must not be promoted to a real quality baseline.

8시간 분석 루프가 예산 없이 반복되거나 부분 산출물을 성공으로 포장하지 않도록 `tools/execution_budget.py`에 dependency-free run-scoped 실행 예산 gate를 둔다. `max_iterations`, `max_queries`, `deadline_seconds`는 work 시작 전에 검증되며 missing, 잘못된 type, 0, 음수는 거부된다. 하나의 `ExecutionBudget` 인스턴스가 iteration, query, retry, child work에 공유되므로 retry나 subtask가 counter를 reset할 수 없고, N번째 unit까지만 허용하며 N+1 callback은 호출하지 않는다. deadline은 `time.monotonic` 기준이고 `cap_timeout()`은 남은 시간으로 query timeout을 cap한다. 초과 시 `budget_exhausted` machine-readable status(limit_kind, used, limit, partial-result 보존 여부)를 남기며 부분 산출물을 성공으로 표기하지 않는다.

`tools/eval_quality.py`는 `--query-timeout-seconds`(Make: `QUALITY_QUERY_TIMEOUT_SECONDS`)로 이 계약을 실제 psql subprocess에 연결한다. 값은 유한한 양수만 허용하며 `0`, 음수, `NaN`, `Infinity`는 SQL 실행 전에 exit code 2로 거부한다. 옵션을 생략하면 `subprocess.run`에 `timeout` 인자를 넘기지 않아 기존 동작을 그대로 보존한다. run budget이 함께 걸려 있으면 각 setup SQL과 query SQL subprocess 직전에 `remaining_seconds()`와 명시 timeout 중 더 짧은 유효 timeout을 계산하고, deadline이 이미 소진됐으면 subprocess를 시작하지 않는다. budget이 없어도 명시 timeout은 그대로 적용되며, setup/query는 새 budget query unit으로 세지 않아 기존 iteration/query charge 의미는 바뀌지 않는다.

이 timeout은 Python `subprocess.run`이 client subprocess 반환을 제한하는 경계일 뿐이다. PostgreSQL 서버 측 query cancellation을 보장하지 않으므로 timeout 후에도 서버 backend statement가 계속 실행 중일 수 있다. 서버 측 취소가 필요하면 별도 `statement_timeout`/`pg_cancel_backend` 계층을 함께 써야 한다. timeout은 성공/0점으로 처리하지 않는다. `quality.json`에 `status=quality_eval_timeout`, `success=false`, `timeout.stage`(`setup`|`query`), `timeout.reason`, `timeout.limit_seconds`, partial 보존 여부와 완료된 `results`만 기록하고 exit code 4로 종료한다. query 단계 timeout은 앞서 완료한 results만 partial로 남기고 누락 query를 채우거나 자동 retry하지 않으며, setup 단계 timeout은 query를 전혀 실행하지 않는다. 기존 `budget_exhausted`(exit 3)와 partial artifact 계약은 그대로 유지된다.

`tools/eval_quality.py`는 위 client 제한과 별도로 `--query-statement-timeout-seconds`(Make: `QUERY_STATEMENT_TIMEOUT_SECONDS`) opt-in 서버 제한을 제공한다. 값이 없으면 새 서버 timeout SQL을 전혀 주입하지 않아 기존 동작을 보존한다. 값은 Decimal 경계로 검증해 PostgreSQL millisecond 범위 `1..2147483647ms`로 표현 가능한 유한 양수만 허용하며, `0`, 음수, `NaN`, `Infinity`, 1ms 미만, 범위 초과는 SQL 실행 전에 exit code 2로 거부한다. 이 제한은 `query_sql`의 단일 `hybrid_warc_search` SELECT에만 적용된다: query마다 별도 psql subprocess/session에서 `\set QUIET on`, `\set ON_ERROR_STOP on`, `\set VERBOSITY verbose`, `BEGIN;`, `SET LOCAL statement_timeout = <ms>;`, `SELECT ...`, `COMMIT;` 래퍼를 실행해 command tag가 결과에 섞이지 않고 SELECT 실패 후 COMMIT이 성공으로 진행되지 않는다. `setup_sql`에는 `SET LOCAL`/`statement_timeout`을 주입하지 않고 setup transaction 구조도 바꾸지 않는다(생성·색인 단계는 실패 시 재시도 가능한 setup이므로 제한 대상에서 제외). 각 query 직전에 명시 서버 제한, 존재하는 client effective limit(`--query-timeout-seconds`, 필요하면 run budget으로 cap), run budget remaining deadline 중 가장 짧은 제한을 계산해 PostgreSQL 정수 ms로 내림하며 0ms는 만들지 않는다. 1ms 미만 cap이면 query subprocess를 시작하지 않고 기존 client/budget 비성공 경로로 안전하게 종료한다. `--query-timeout-seconds`는 계속 Python client subprocess 제한으로만 유지되며 서버 제한이 이를 대체하거나 늘리지 않는다.

서버 statement timeout은 SQLSTATE 57014만으로 추측하지 않고 `canceling statement due to statement timeout` 안정 진단이 확인될 때만 기존 `quality_eval_timeout` non-success status/exit 4 경로로 분류한다. 사용자 취소(`canceling statement due to user request`)나 기타 SQL 오류는 기존 일반 오류 경로로 남고, `subprocess.TimeoutExpired`가 먼저 발생하면 기존 client timeout 경로를 유지한다. timeout artifact에는 가능한 경우 `timeout.requested_statement_timeout_ms`, `timeout.effective_statement_timeout_ms`, `timeout.timeout_source`(`server_statement`/`client`/`run_budget`), `timeout.sqlstate`(없으면 null)를 선택 필드로 보존하고 `timeout.client_side_only`는 false가 된다. 기존 timeout/budget artifact 소비 doctor는 이 artifact를 계속 `valid=false`로 구조화해 거부하며 정상 성공 artifact에는 영향이 없다. 이때 서버 timeout을 성공/0점으로 처리하지 않고 새 top-level status나 `pg_cancel_backend`·별도 취소 connection 계층을 만들지 않으며, 서버 timeout이 backend 종료·rollback 완료·자원 회수를 보장한다고 주장하지 않는다(서버 timeout과 psql 종료 사이에는 race가 있을 수 있다). 이 변경은 harness 경계 증거이지 real quality baseline이 아니다.

timeout artifact는 `timeout.timeout_observation` 객체로 관측 경계를 구분한다. client subprocess timeout(`subprocess.TimeoutExpired` → `SqlTimeout`, pre-run 1ms 미만 cap 포함)은 `statement_outcome=unknown`이고, 기존 확인된 서버 statement timeout 경로만 `statement_outcome=server_statement_timeout_reported`가 된다. 두 경우 모두 `backend_state_after_timeout=unobserved`, `cancellation_completion=unverified`, `transaction_cleanup=unverified`이며, 이는 관측하지 않았다는 뜻이지 실패했거나 계속 실행 중이라는 뜻이 아니다. client timeout 예외의 stderr에 statement-timeout 문구가 우연히 포함돼도 서버 outcome으로 승격하지 않고, `subprocess.TimeoutExpired`가 먼저 나면 항상 client 경로를 유지한다. `timeout_observation`은 성공 artifact, 일반 SQL 오류, pre-run 예산 소진(`budget_exhausted`)에는 추가하지 않는다. 기존 timeout artifact와의 호환을 위해 observation이 없는 legacy timeout은 계속 수용하되 `tools/doctor_run_directory.py`/`tools/doctor_quality_baseline.py`가 관측이 기록되지 않았음(backend state/cancellation unverified)을 명시하며, malformed observation은 구조화된 검증 오류로만 처리하고 confirmed outcome으로 취급하지 않는다. `timeout.client_side_only=true`는 client timeout에만, `false`는 `timeout_source=server_statement`와 `statement_outcome=server_statement_timeout_reported`인 경우에만 유효하다.

`eval-quality`에 예산을 걸려면 세 값을 모두 명시해야 한다. 하나라도 빠지면 work 시작 전에 거부된다.

```bash
ANALYSIS_MAX_ITERATIONS=50 ANALYSIS_MAX_QUERIES=50 ANALYSIS_DEADLINE_SECONDS=120 \
  QUALITY_FIXTURE=quality.fixture.json QUALITY_QUERY_TIMEOUT_SECONDS=30 \
  QUERY_STATEMENT_TIMEOUT_SECONDS=5 \
  BENCH_OUT_DIR=benchmarks/local make eval-quality-budget
QUALITY_FIXTURE=quality.fixture.json QUALITY_QUERY_TIMEOUT_SECONDS=30 \
  QUERY_STATEMENT_TIMEOUT_SECONDS=5 make eval-quality
python3 tools/eval_quality.py --max-iterations 50 --max-queries 50 --deadline-seconds 120 \
  --query-timeout-seconds 30 --query-statement-timeout-seconds 5 --json-output benchmarks/local/quality.json
python3 tools/execution_budget.py --max-iterations 50 --max-queries 50 --deadline-seconds 120 \
  --json-output benchmarks/local/analysis_budget_status.json
```

`make eval-quality-budget`는 `ANALYSIS_BUDGET_STATUS_JSON`(기본 `$(BENCH_OUT_DIR)/analysis_budget_status.json`)에 상태를 남긴다. 예산이 소진되면 `quality.json`에 `status=budget_exhausted`, `success=false`, `budget` field와 partial `results`가 기록되고 exit code 3으로 종료되므로 run doctor의 `quality.json` schema 검사(query_count 대비 results 수)에서도 성공으로 통과하지 않는다. timeout의 경우 budget status JSON은 재작성하지 않고 timeout payload만 남긴다. 이 모듈은 프로젝트 밖 `kilo-gpt-report-harness`를 수정하지 않고 그 하네스가 호출할 수 있는 프로젝트 로컬 경계이며, real labeled corpus와 versioned upgrade 증거가 아니다.

timeout/budget non-success artifact를 소비하는 doctor는 이를 성공 baseline으로 승인하지 않는다. `tools/doctor_quality_baseline.py`는 `quality.json`의 `quality_eval_timeout`/`budget_exhausted`를 `hit_rate_at_k`/`mrr_at_k` 숫자 변환보다 먼저 식별하고, 원본 `status`, 완료 결과 수, `timeout.stage`/`reason`/`limit_seconds`/`timeout_observation` 또는 `budget` 진단을 기존 report JSON/Markdown 구조에 보존한 채 metric을 `n/a`로 비워 둔다(partial을 0점 success metric으로 재계산하지 않음). `tools/doctor_run_directory.py`의 `quality.json` 검사는 성공 schema와 non-success artifact를 분리해 status별 명시 오류, timeout observation enum(`statement_outcome`/`unobserved`/`unverified`), timeout/budget partial 구조 검증을 남기므로, timeout artifact가 남은 run directory는 `valid=false`/non-zero로 끝난다. producer exit code(3/4)를 doctor exit code로 통일하지 않으며 traceback 대신 구조화된 오류를 출력한다. `success=false` 또는 알 수 없는 status, `status`/`success` 모순도 같은 structured error로 처리한다. 이 소비 계약은 실행 실패/부분 산출물을 정확히 보존하는 하네스 증거일 뿐이며, opt-in query-only 서버 `statement_timeout` 제한 외에 PostgreSQL 서버 측 취소(`pg_cancel_backend`)와 backend 상태 관측·취소 확인은 구현하지 않았고 real labeled corpus/qrels(Issue A)와 old/new artifact·upgrade SQL(Issue B)은 계속 blocked/unmeasured다. 다음 청사진은 backend 상태를 실제로 관측하거나 active cancellation을 수행하는 별도 계약이며, 그 전에 session identity(PID/backend/transaction), 별도 권한 connection과 permission 경계, target race(취소 대상이 이미 종료됐을 수 있음), timeout race(취소 요청과 서버/client timeout의 경합), 관측·취소 시도 자체의 budget 소모, rollback/transaction cleanup 판정 기준을 먼저 정의해야 한다. 그 전까지는 어떤 timeout artifact도 backend 종료·transaction 정리를 확인한 것으로 표기하지 않는다.


### 벤치마크 (하네스)

```bash
make bench-bm25                          # bm25_index_document 색인 경로 측정
make bench-lance                         # lance_insert vs lance_insert_many 비교
make bench-warc                          # synthetic WARC parse + SQL generation 측정
make bench-warc-bm25-modes               # WARC importer BM25 function/bulk/copy 비교
make bench                               # BM25/Lance/WARC 기본 3종 순차 실행

BENCH_ROWS=100,500,1000 BM25_WORDS=80 make bench-bm25
BENCH_ROWS=100,500,1000 BENCH_DIM=384 BENCH_BATCH=500 make bench-lance
WARC_ROWS=5000 WARC_WORDS=80 BENCH_DIM=384 BENCH_BATCH=500 make bench-warc
WARC_BM25_MODE=bulk WARC_ROWS=5000 make bench-warc
WARC_BM25_MODE=copy WARC_ROWS=5000 make bench-warc
WARC_COMPARE_ROWS=100,500 WARC_COMPARE_EXECUTE=1 make bench-warc-bm25-modes
WARC_COMPARE_ROWS=1000,2000 WARC_COMPARE_EXECUTE=1 BENCH_BATCH=500 make bench-warc-bm25-modes
WARC_COMPARE_NO_HASH_VECTORS=1 WARC_COMPARE_ROWS=100,500 make bench-warc-bm25-modes
BENCH_OUT_DIR=benchmarks/local BENCH_ROWS=100,500 WARC_ROWS=500 make bench
BENCH_OUT_DIR=benchmarks/local BENCH_ROWS=100,500 WARC_ROWS=500 make finalize-benchmark-run
BENCH_OUT_DIR=benchmarks/local WARC_COMPARE_ROWS=100,500 make finalize-warc-bm25-modes-run
BENCHMARK_BASELINE_JSON=/path/to/baseline/warc_bm25_modes.json \
BENCHMARK_CANDIDATE_JSON=/path/to/candidate/warc_bm25_modes.json \
BENCHMARK_MAX_REGRESSION_RATIO=0.10 \
make compare-benchmark-runs
BENCHMARK_BASELINE_JSON=/path/to/baseline/warc_bm25_modes.json \
BENCHMARK_CANDIDATE_JSON=/path/to/candidate/warc_bm25_modes.json \
BENCHMARK_MAX_REGRESSION_RATIO=0.10 \
BENCH_OUT_DIR=benchmarks/local \
make finalize-benchmark-comparison-run
BENCH_OUT_DIR=benchmarks/local RUN_METADATA_COMMAND='make bench' make write-run-metadata
BENCH_OUT_DIR=benchmarks/local make report-run-directory
BENCH_OUT_DIR=benchmarks/local RUN_DOCTOR_PROFILE=benchmark make doctor-run-directory
BENCH_OUT_DIR=benchmarks/local RUN_METADATA_COMMAND='make bench' RUN_DOCTOR_PROFILE=benchmark make finalize-run-directory
```

- 모든 `tools/bench_*.py` 하네스는 stdout에 Markdown table을 출력하고, `--json-output <path>`와 `--markdown-output <path>`로 비교 가능한 아티팩트를 저장할 수 있다. Makefile에서는 `BENCH_OUT_DIR=<dir>`를 주면 각 benchmark target이 `bm25.json/md`, `lance.json/md`, `warc.json/md`, `warc_bm25_modes.json/md` 같은 비교 아티팩트를 남긴다.
- `report-run-directory`는 `BENCH_OUT_DIR` 또는 `RUN_DIR` 아래의 산출물을 재귀적으로 스캔해 `run_manifest.json/md`에 파일 크기와 SHA-256을 남긴다. 이 manifest는 이전 manifest 출력 파일을 자체 입력에서 제외하므로 같은 run directory에서 반복 실행할 수 있다.
- `write-run-metadata`는 같은 run directory에 현재 git revision, dirty 상태, 실행 command, 선택 env 값을 남긴다. 먼저 metadata를 쓰고 마지막에 `report-run-directory`를 실행하면 manifest가 metadata 파일까지 포함한다.
- `doctor-quality-baseline`은 이미 생성된 quality run의 `quality_fixture_report.json`과 `quality.json`을 읽어 doc/query/label 수와 hit-rate/MRR 기준을 검사하고 `quality_baseline_doctor.json/md`를 남긴다. Run doctor는 required baseline doctor artifact의 threshold/count/result schema도 검사한다.
- `finalize-quality-run`과 `finalize-import-run`은 profile 기본 요구사항에 더해 각각 `quality_baseline_doctor.json`, `import_doctor.json`을 target 전용 필수 artifact로 요구한다.
- `finalize-benchmark-run`은 `bench`를 실행한 뒤 `bm25.json`, `lance.json`, `warc.json`을 추가 필수 산출물로 요구하며 benchmark profile로 run directory를 마감한다.
- `finalize-warc-bm25-modes-run`은 `bench-warc-bm25-modes`를 실행한 뒤 `warc_bm25_modes.json`을 추가 필수 산출물로 요구하며 benchmark profile로 targeted BM25 modes run directory를 마감한다.
- `compare-benchmark-runs`는 두 `warc_bm25_modes.json` artifact의 benchmark 설정, row/mode 집합, `execute_s` 측정값을 비교한다. `BENCHMARK_MAX_REGRESSION_RATIO`를 명시해야 하며 후보 실행 시간이 허용 비율을 넘으면 non-zero로 종료한다. 결과는 선택적으로 `BENCHMARK_COMPARE_JSON`/`BENCHMARK_COMPARE_MARKDOWN` 또는 `BENCH_OUT_DIR` 아래에 저장한다. real corpus가 아닌 synthetic artifact의 결과는 production 성능 gate로 승격하지 않는다.
- `finalize-benchmark-comparison-run`은 comparator를 실행한 뒤 `benchmark_comparison.json/md`와 `run_metadata`, `run_manifest`, `run_doctor`를 같은 run directory에 남긴다. 비교 report schema와 mode별 threshold 계산, `passed=true` 상태를 run doctor가 다시 검사하며, 비교가 실패하면 report를 남긴 채 finalization은 non-zero로 중단된다.
- comparison report schema v2는 baseline/candidate 입력의 bytes와 SHA-256을 보존한다. 입력 파일이 나중에도 존재하면 run doctor가 현재 bytes/hash를 다시 계산해 drift를 오류로 보고하고, 파일이 보관되지 않은 경우에도 report에 기록된 provenance는 유지한다.
- `RUN_DOCTOR_REQUIRE=benchmark_comparison.json make doctor-run-directory`를 사용하면 run doctor가 comparison report의 schema, count, baseline/candidate 계산, threshold 판정과 `passed=true` 상태를 독립적으로 재검증한다.
- `doctor-run-directory`는 run directory가 profile별 필수 산출물을 갖췄는지 확인한다. `quality` profile은 `quality_fixture_report.json`과 `quality.json`, `import` profile은 `import.summary.json`, `packaging` profile은 `release_artifacts_matrix.json`과 `release_smoke_matrix.json`을 요구한다. 또한 `run_metadata.json`의 schema version, generated/run dir metadata, command/environment, git head/branch/dirty/status, warning list schema를 검사하고, manifest에 기록된 파일 존재, size, SHA-256을 확인한다. quality/import/packaging report JSON의 `valid`, `all_valid`, `all_passed` 성공 플래그가 false이면 실패로 처리하며, `RUN_DOCTOR_REQUIRE`로 `quality_labels_doctor.json` 같은 알려진 status artifact를 요구하면 해당 성공 플래그도 검사한다. Required `quality.fixture.json`은 fixture schema와 duplicate doc/query를 검사하고, required `quality_fixture_report.json`은 report count/query row schema를 검사하며, required `quality.json`은 eval metric schema와 query result rows를 검사한다. Required `quality_baseline_doctor.json`은 threshold/count/result schema를 검사한다. Required/profile `import.summary.json`은 importer summary fields, BM25/Lance mode enum, Lance append duplicate risk contract, execute/psql contract를 검사한다. Required `import_doctor.json`은 all_valid, summary/error/warning counts, per-summary path/valid/errors/warnings/summary rows, embedded valid summary schema를 검사한다. Required/profile `release_artifacts_matrix.json`은 extension/version/dist root, PostgreSQL major rows, all_valid consistency, per-file exists/bytes/SHA-256 schema를 검사한다. Required/profile `release_smoke_matrix.json`은 schema version, generated metadata, Make variables, PostgreSQL major result rows, all_passed consistency, exit-code/error consistency를 검사한다. benchmark profile에서 `RUN_DOCTOR_REQUIRE`로 요구된 알려진 benchmark JSON의 schema와 non-empty result도 검사한다.
- `finalize-run-directory`는 metadata, manifest, doctor, final manifest 순서로 run directory 마감 산출물을 생성한다. 개별 target을 수동 실행할 필요가 없으면 이 target을 사용한다.
- `bench-bm25`는 `bm25_index_document()`의 document별 호출 경로를 측정한다. 출력의 `term_rows_per_s`는 현재 구현의 per-term SPI insert 병목을 비교하기 위한 지표다.
- `bench-lance`는 live Docker DB에 실제 Lance dataset을 만들고 row-by-row append와 batch append wall time을 비교한다. source 변경 직후 최신 extension 함수가 필요하면 먼저 `make test-regress` 또는 `make test-all`을 실행한다.
- `bench-warc`는 임시 synthetic `.warc.gz`를 만들고 WARC 생성, parse/extract, SQL generation, 선택적 DB execution 시간을 분리해 측정한다. 기본 target은 DB 실행을 하지 않는다. end-to-end 실행은 `python3 tools/bench_warc.py --execute`로 별도 실행한다.
- `bench-warc-bm25-modes`는 같은 synthetic WARC input에서 `--bm25-mode function`, `bulk`, `copy`의 SQL generation, SQL size, 선택적 DB execution 시간을 나란히 비교한다. DB 실행 비교는 기본적으로 mode마다 extension을 drop/create해 같은 빈 상태에서 측정한다. `WARC_COMPARE_NO_HASH_VECTORS=1`을 주면 Lance hash vector 적재를 제외해 BM25 경로만 더 좁게 본다.
- 현재 대용량 import 실행 경로는 SQL 파일을 먼저 만들기보다 `psql` stdin으로 streaming 실행하는 `--execute`를 기본으로 둔다. `--output` SQL 파일은 audit, debugging, 실패 chunk 분석이 필요할 때의 선택 아티팩트다.

현재 로컬 기준선과 Phase 3 성능 결정은 [BENCHMARKS.md](BENCHMARKS.md)에 기록한다.

### 기타

```bash
make logs        # PostgreSQL 로그 실시간 확인
make restart     # 컨테이너 재시작
make down        # 컨테이너 중지
make rebuild     # 캐시 없이 재빌드
make clean       # 컨테이너 중지 + 볼륨 삭제
```

기존 `pglance` 이름으로 만든 개발 컨테이너/볼륨이 남아 있다면 새 `pgwarc_lance` 사용자와 DB가 자동으로 생기지 않을 수 있다. 로컬 개발 DB를 비워도 되는 경우 `make clean && make up`으로 재초기화한다.

## 연결 정보

- 호스트: `localhost`
- 포트: `55432` (컨테이너 내부 5432)
- 사용자/비밀번호/DB: `pgwarc_lance` / `pgwarc_lance` / `pgwarc_lance_test` (compose env: `POSTGRES_USER=pgwarc_lance` 등)

> 참고: 익스텐션명/디렉토리명은 `pgwarc_lance`이고, 컴포즈 환경변수(`POSTGRES_USER` 등)는 `pgwarc_lance`로 설정되어 DB 사용자/DB명도 `pgwarc_lance`/`pgwarc_lance_test`다.

## 회귀 테스트 하네스 확장 방법

새 SQL 회귀 테스트 추가:

```bash
# 1. tests/pg_regress/sql/<name>.sql 작성
# 2. 컨테이너 /build에 최신 tests를 반영한 뒤 회귀 실행
docker cp tests/. pgwarc_lance:/build/tests/
make test-regress
# 3. 새 expected가 없어서 실패하면 생성된 결과를 호스트 expected로 복사
docker cp pgwarc_lance:/tmp/pgwarc_lance_pg_regress/results/<name>.out \
    tests/pg_regress/expected/<name>.out
# 4. make test-regress 재실행 후 diff 확인
```

> 주의: `setup.sql`은 회귀 DB 재생성 시에만 실행되며, `setup.out`은 명령 태그가 억제된 입력 에코 형식(2줄)이어야 한다.

## WARC 지원 방향

WARC 지원은 extension 내부에서 대형 파일을 오래 파싱하기보다, 외부 importer가 WARC/WARC.GZ를 스트리밍 파싱하고 PostgreSQL/Lance에 적재하는 방식이 안전하다.

초기 importer 흐름:

1. WARC record에서 `WARC-Target-URI`, `WARC-Date`, `Content-Type`, HTTP status, payload digest 추출
2. HTML/text payload에서 본문 추출
3. Lance 적재 모드에 따라 dataset 생성, 재생성, 또는 기존 dataset append 수행
4. 본문은 기본적으로 `bm25_index_document()`로 색인하거나, 대량 import에서는 `--bm25-mode bulk` 또는 `--bm25-mode copy`로 `bm25_doc`/`bm25_term`에 batch 적재
5. embedding은 `lance_insert_many()`로 Lance dataset에 batch 저장
6. 검색은 `hybrid_search()`로 결합

현재 `tools/warc_importer.py`는 Python stdlib만 사용해 WARC/WARC.GZ를 읽고 SQL을 생성하거나 바로 `psql`로 실행한다. WARC record header에 non-negative `Content-Length`가 없거나 잘못되거나 중복되면 길이 0으로 묵살하지 않고 payload read 전에 입력 오류로 중단한다. HTTP `Transfer-Encoding: chunked` payload도 chunk size, payload length, data terminator, trailer, terminating zero-size chunk가 malformed/truncated이면 입력 오류로 중단한다. HTTP `Content-Encoding: gzip` payload도 압축 해제에 실패하면 원본 바이트를 본문으로 오인하지 않고 입력 오류로 중단한다. `Content-Type` charset parameter의 `=` 주변 공백을 허용해 선언된 문자셋으로 본문을 decode한다. `--limit`은 non-negative record count이며 `--limit 0`은 빈 import을 만들고, `--min-text-chars`도 음수 값을 거부한다. importer가 같은 `doc_id`를 두 번 생성하면 SQL/Lance 중복 적재 전에 입력 오류로 중단한다. external embedding JSONL/command vector는 finite numeric value와 정확한 dimension을 가져야 하며, required `doc_id`가 여러 번 나오면 마지막 vector로 덮어쓰지 않고 중단한다. embedding은 세 가지 모드가 있다.

- 기본값: 본문에서 deterministic placeholder vector를 만든다. 검색 plumbing 검증용이다.
- `--embedding-command`: 추출 record JSONL을 stdin으로 넘기고, stdout의 vector JSONL을 읽는다.
- `--embedding-jsonl`: 미리 계산한 vector JSONL을 읽는다.

```bash
# SQL 파일 생성
python3 tools/warc_importer.py sample.warc.gz \
    --lance-uri /tmp/warc.lance \
    --vector-dim 384 \
    --lance-mode overwrite \
    --output import.sql \
    --summary \
    --summary-json import.summary.json

# 실행 중인 개발 DB에 바로 적재
python3 tools/warc_importer.py sample.warc.gz \
    --execute \
    --lance-uri /tmp/warc.lance \
    --vector-dim 384 \
    --lance-mode overwrite \
    --bm25-mode copy \
    --summary

# BM25 + metadata만 적재하고 Lance placeholder vector는 건너뛰기
python3 tools/warc_importer.py sample.warc.gz --execute --no-hash-vectors

# embedding 입력용 record JSONL 생성
python3 tools/warc_importer.py sample.warc.gz \
    --records-jsonl records.jsonl \
    --no-hash-vectors \
    --output metadata_bm25.sql

# 외부 embedding command를 통해 실제 vector 적재
python3 tools/warc_importer.py sample.warc.gz \
    --execute \
    --lance-uri /tmp/warc.lance \
    --vector-dim 1536 \
    --embedding-command 'python3 tools/embed_records_openai.py --model text-embedding-3-small --expect-dim 1536' \
    --batch-size 500 \
    --summary

# 미리 만든 vector JSONL 사용
python3 tools/warc_importer.py sample.warc.gz \
    --execute \
    --lance-uri /tmp/warc.lance \
    --vector-dim 384 \
    --embedding-jsonl vectors.jsonl
```

`--lance-mode`는 vector 적재 전에 Lance dataset을 어떻게 다룰지 정한다.

| 모드 | 동작 | 용도 |
|------|------|------|
| `create` | `lance_create_table(uri, dim, false)` 호출. 기존 dataset이 있으면 실패 | 새 dataset 최초 적재 |
| `overwrite` | `lance_create_table(uri, dim, true)` 호출. 기존 dataset 재생성 | 테스트/재실행 |
| `append` | dataset 생성 호출을 생략하고 `lance_insert_many()`만 수행 | 기존 dataset에 추가 적재 |

`--overwrite-lance`는 하위 호환용 alias이며 새 사용법에서는 `--lance-mode overwrite`를 쓴다. `append` 모드는 기존 dataset에 row를 추가하지만 Lance row 자체를 deduplicate하지는 않는다. 같은 WARC를 반복 import하면 PostgreSQL metadata/BM25는 doc_id 기준으로 갱신되지만 Lance dataset에는 중복 vector row가 쌓일 수 있다. 반복 실행이 필요한 테스트나 재생성 import에는 `overwrite`를 사용한다. importer는 append vector SQL에 중복 가능성 warning comment를 넣고, summary에는 `lance_append_duplicates=possible`을 표시한다.

`--embedding-command`는 stdin으로 record JSONL을 받는다. 각 줄은 `doc_id`, `target_uri`, `warc_date`, `content_type`, `http_status`, `payload_digest`, `text_len`, `source_file`, `text`를 포함한다. stdout은 줄마다 `{"doc_id": 123, "vector": [0.1, ...]}` 형식이어야 하며 vector 길이는 `--vector-dim`과 정확히 같아야 한다. `tools/embed_records_openai.py`는 이 계약을 구현한 OpenAI-compatible 예제이며, `--embedding-jsonl`도 같은 출력 형식을 사용한다.

기본 `--psql`은 `psql -h localhost -p 55432 -U pgwarc_lance -d pgwarc_lance_test -v ON_ERROR_STOP=1`이며, `PGWARC_PSQL` 환경변수로 바꿀 수 있다. `--batch-size`는 생성 SQL을 record 단위 transaction chunk로 나누며 기본값은 1000이다. schema/table 준비와 `lance_create_table()`은 record transaction 밖에서 한 번 실행되고, 각 record chunk는 `-- pgwarc_lance import batch ...` comment 다음 `BEGIN`/`COMMIT`으로 감싼다. `--bm25-mode function`은 기존처럼 record마다 `bm25_index_document()`를 호출하고, `--bm25-mode bulk`는 importer가 CJK bigram + ASCII tokenizer로 term frequency를 계산해 `bm25_doc`/`bm25_term` rows를 `INSERT ... VALUES`로 batch insert한다. `--bm25-mode copy`는 같은 term frequency를 계산하지만 `psql`의 `COPY FROM stdin` stream으로 `bm25_doc`/`bm25_term` rows를 적재한다. `copy` mode가 생성한 SQL artifact는 `psql` compatible input으로 실행해야 한다. `--summary`는 사람이 읽는 stderr 한 줄을 출력하고, `--summary-json <path>`는 같은 정보를 machine-readable JSON으로 저장한다. 생성된 summary는 `IMPORT_SUMMARY=<path> make doctor-import-summary`로 DB 없이 검사할 수 있고, `IMPORT_SUMMARY=<path> BENCH_OUT_DIR=<dir> make finalize-import-run`으로 run directory까지 마감할 수 있다.

### Import Failure Recovery

Importer-generated SQL은 PostgreSQL metadata/BM25 변경을 chunk 단위 `BEGIN`/`COMMIT`으로 묶지만, Lance dataset write는 PostgreSQL transaction rollback과 같은 원자성을 보장하지 않는다. 실패 후 상태가 애매하면 같은 입력 WARC 목록과 같은 embedding 설정으로 `--lance-mode overwrite`를 사용해 전체 import를 재실행하는 것이 기본 복구 경로다.

- 실패 위치가 batch comment 전이면 schema/table 준비나 Lance dataset 생성 단계에서 멈춘 것이다. 설정을 고친 뒤 `overwrite`로 재실행한다.
- 실패 위치가 batch 내부이면 PostgreSQL의 해당 chunk는 rollback되지만, 이미 호출된 `lance_insert_many()`의 외부 dataset write는 남아 있을 수 있다. 안전한 재시도는 `overwrite`로 Lance dataset을 재생성하는 것이다.
- `append` mode에서 실패하거나 같은 WARC를 반복 실행하면 Lance에는 같은 `doc_id` vector row가 중복될 수 있다. PostgreSQL metadata/BM25는 `doc_id` 기준으로 갱신되지만, vector search rank는 중복 row의 영향을 받을 수 있으므로 append 재시도는 dataset 상태가 명확할 때만 사용한다.
- 대형 import에서는 `--summary-json`을 항상 남기고 `IMPORT_SUMMARY=import.summary.json make doctor-import-summary` 또는 `IMPORT_SUMMARY=import.summary.json BENCH_OUT_DIR=benchmarks/local make finalize-import-run`으로 schema, `execute=false`, zero records, Lance append duplicate risk와 run directory completeness를 확인한다. 실패 분석이 필요할 때만 `--output import.sql`로 SQL artifact를 보관한다. 정상 실행 기본 경로는 disk에 큰 SQL 파일을 만들지 않는 `--execute` streaming이다.
- chunk 단위 수동 복구가 필요하면 generated SQL의 `-- pgwarc_lance import batch start-end` comment를 기준으로 실패 batch를 찾는다. 이 경우에도 Lance 상태가 불확실하면 batch만 재실행하지 말고 `overwrite` 전체 재실행을 우선한다.

WARC 검색 결과를 바로 소비해야 하면 `hybrid_warc_search()`를 사용한다. 기존 `hybrid_search()`와 같은 ranking을 사용하되 `target_uri`, UTC 문자열 `warc_date`, `content_type`, `http_status`, `payload_digest`, `text_len`, `source_file`을 함께 반환한다. 해당 doc_id의 metadata가 없으면 ranking row는 유지하고 metadata 컬럼은 null/0 기본값으로 반환한다.

metadata filter는 새 함수 인자를 늘리지 않고 SQL `WHERE`로 표현하는 것을 현재 계약으로 둔다. snippet/highlight도 extension API로 고정하지 않고, 필요하면 `pgwarc_lance.bm25_doc`를 join해 SQL/client layer에서 만든다. ranking diagnostics는 현재 `source` 컬럼(`bm25`, `vector`, `bm25+vector`)만 안정 계약으로 제공하고, per-source rank/score breakdown은 품질 평가 단계에서 별도 함수로 검토한다.

```sql
SELECT h.doc_id, h.score, h.source, h.target_uri,
       substring(d.content from 1 for 240) AS snippet
FROM hybrid_warc_search(
    '검색',
    ARRAY[0,0,0,0]::float4[],
    '/tmp/warc.lance',
    20
) AS h
LEFT JOIN pgwarc_lance.bm25_doc d USING (doc_id)
WHERE h.http_status = 200
  AND h.content_type ILIKE 'text/html%'
  AND h.warc_date::timestamptz >= '2026-06-30T00:00:00Z'::timestamptz
  AND h.source_file = 'sample.warc'
ORDER BY h.score DESC, h.doc_id ASC;
```

`lance_insert_many()`는 vector를 2차원 SQL 배열이 아니라 row-major flat `float4[]`로 받는다. 예를 들어 4차원 vector 2개는 `ARRAY[0,0,0,0,10,0,0,0]::float4[]`, `vector_dim=4`, `ids=ARRAY[1,2]::bigint[]`, `labels=ARRAY['doc1','doc2']::text[]`로 호출한다. importer는 batch마다 이 함수를 한 번 emit해서 row별 `lance_insert()` 호출을 피한다.

## 코드 수정 후 재배포

`src/lib.rs` 또는 하네스 파일 수정 후:

```bash
make rebuild               # 익스텐션 재컴파일 + 이미지 갱신
make down && make up       # 컨테이너 재시작 (새 익스텐션 로드)
make verify                # 전체 하네스 검증
```

## 패키징

로컬 PostgreSQL artifact 추출:

```bash
make release-artifacts
PG_MAJOR=17 make release-artifacts
make check-release-artifacts
make smoke-release-artifacts
UPGRADE_OLD_VERSION=0.0.9 make smoke-release-upgrade
make release-artifacts-matrix
make smoke-release-artifacts-matrix
UPGRADE_OLD_VERSION=0.0.9 make smoke-release-upgrade-matrix
BENCH_OUT_DIR=benchmarks/local make report-release-artifacts
BENCH_OUT_DIR=benchmarks/local make report-release-smoke-matrix
BENCH_OUT_DIR=benchmarks/local make finalize-release-run
```

기본 출력은 `dist/pgwarc_lance-0.1.0-pg16/`이며, `PG_MAJOR`를 바꾸면 `dist/pgwarc_lance-0.1.0-pg17/`처럼 major별 디렉토리에 `.so`, `.control`, `pgwarc_lance--0.1.0.sql`을 추출한다. `release-artifacts`는 추출 직후 `.so` ELF header, control `default_version`/`module_pathname`, generated SQL의 핵심 함수 symbol을 검사한다. `smoke-release-artifacts`는 그 산출물을 build image 밖의 official Postgres 컨테이너에 설치해 fresh install smoke를 수행한다. `smoke-release-upgrade`는 old/new artifact와 `pgwarc_lance--OLD--NEW.sql`로 `ALTER EXTENSION UPDATE`를 확인한다. Matrix target은 `PG_MATRIX`의 major 목록을 순회하며, install/upgrade matrix는 기존 `dist/` 산출물을 사용한다. `report-release-artifacts`는 major별 artifact 상태와 SHA-256을 JSON/Markdown으로 남기고, run doctor는 이 artifact matrix report의 row/file schema도 검사한다. `report-release-smoke-matrix`는 major별 runtime smoke 실행 결과와 로그 tail을 남기며, run doctor는 smoke result rows와 pass/exit-code consistency도 검사한다. `RELEASE_SMOKE_TARGET=smoke-release-upgrade`이면 `report-release-upgrade-smoke-matrix`가 `tools/report_release_upgrade_smoke_matrix.py`로 각 major의 `doctor_release_upgrade_inputs` preflight을 먼저 실행하고, blocked major는 smoke를 실행하지 않은 채 per-major `preflight`/`smoke_skipped` field와 함께 non-zero report를 남긴다. `finalize-release-run`은 두 release report와 run metadata/manifest/doctor를 packaging profile로 한 run directory에 마감한다. host PostgreSQL 설치, version upgrade SQL 전략, Rust toolchain pinning 정책은 [PACKAGING.md](PACKAGING.md)에 정리한다.

## 기술 스택

- PostgreSQL 16 기본 개발 환경 (`postgres:16` 공식 이미지 + `postgresql-server-dev-16` 헤더), 선택 smoke는 `PG_MAJOR=13..17`
- pgrx `=0.19.1` / cargo-pgrx `0.19.1`
- Rust `1.96.0` (Dockerfile `RUST_TOOLCHAIN` build arg 기본값)
- Lance crate major `7` (`default-features = false`)
- Arrow / arrow-array `58`
- Tokio `1` (`rt-multi-thread`)
- Docker Compose
- 테스트 하네스: standalone Rust `rustc --test` + pgrx `pg_regress` + smoke SQL

> 버전 기준: pgrx/cargo-pgrx와 Rust toolchain은 고정되어 있고, PostgreSQL/Lance/Tokio는 현재 개발 기준을 문서화한 것이다.

## 빌드 과정 (Dockerfile)

1. `postgres:${PG_MAJOR}` 베이스 + 빌드 도구/헤더(`postgresql-server-dev-${PG_MAJOR}`, `libclang-dev`) 설치
2. rustup으로 Rust `1.96.0` 설치 후 `cargo-pgrx 0.19.1` 설치
3. `cargo pgrx init --pg${PG_MAJOR}`로 해당 major의 바인딩 헤더 준비
4. 의존성만 먼저 `pg${PG_MAJOR}` feature로 빌드(스텁 lib.rs)하여 Docker 레이어 캐시 활용
5. 실제 소스 + control + tests 복사 후 `cargo pgrx install --no-default-features --features pg${PG_MAJOR}`로 `.so`/`.control`/SQL 설치
6. pg_test/pg_regress용 디렉토리 권한 조정 (비-root postgres 유저로 initdb/회귀 실행)

## 0.1.2 운영 후보

`--lance-mode upsert`는 기존 데이터셋에 같은 ID를 교체하거나 새 ID를 추가한다.
반복 적재와 중단 후 재시도에는 이 모드를 사용한다. 최초 데이터셋 생성은 관리자가
한 번 수행하고, 이후에는 `create`/`overwrite` 대신 `upsert`를 사용한다.
기존 데이터셋의 해당 ID가 이미 중복이면 쓰기 전에 거부한다. 한 배치는 최대
10,000행이다. SQL과 Lance의 commit은 별개이며 전체 import의 원자성을 보장하지 않는다.

PG16 release 빌드, 권한·동시성·취소·복구, 반복 적재·최소 권한 18개 검사, 데이터가 있는
0.1.0→0.1.2 및 0.1.1→0.1.2 업그레이드/백업을 검증했다. 공개 SciFact 5,183문서와
MiniLM 384차원 임베딩, 독립 라벨 질의 50개에서 hit@10=0.96, MRR@10=0.7877을
측정했고 벡터 검색은 NumPy exact L2 결과와 대조했다. 이 corpus는 과학 논문 초록이며
운영 WARC를 대표하지 않는다. 재현 명령과 남은 기준은 [PRODUCTION.md](PRODUCTION.md)에 있다.

최소 권한 reader/writer, DDL 없이 적재하는 `--skip-schema`, 모니터링과 장애 대응은 [OPERATIONS.md](OPERATIONS.md)에 있다.

0.1.3은 의존성 보안 권고를 수정하고 PG16 빌드·회귀·업그레이드·복구를 다시 검증했다.
공간 부족·OS 권한 오류·컨테이너 강제 종료와 재시도 20개 검사도 통과했다.
검색 품질은 이전 공개 기준선과 같고, 동시 클라이언트 1·4·8개에서 오류 없이
2,000건을 처리했다. 의존성 검사와 장애 검증은 CI에 추가했다. 상세 수치와
아직 열린 운영 기준은 [PRODUCTION.md](PRODUCTION.md)에 있다.
