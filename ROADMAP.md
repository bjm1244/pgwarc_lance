# pgwarc_lance Roadmap

작성일: 2026-07-01

이 문서는 현재 코드 기준으로 `pgwarc_lance`를 WARC/Web archive 검색용 PostgreSQL 확장으로 안정화하기 위한 작업 순서를 정리한다. 원격 CI는 전제하지 않고, 기본 검증 기준은 로컬 Docker 환경의 `make test-all`이다.

## 현재 기준선

- PostgreSQL 확장명과 크레이트명은 `pgwarc_lance`다.
- 핵심 SQL 표면은 BM25 색인/검색, Lance dataset 생성/삽입/검색, RRF hybrid search, WARC metadata table로 구성되어 있다.
- WARC importer는 Python stdlib만 사용하며 WARC/WARC.GZ 파싱, metadata upsert, BM25 색인, deterministic hash vector, external embedding command, precomputed embedding JSONL을 지원한다.
- Lance 적재 모드는 `create`, `overwrite`, `append`로 분리되어 있다. 반복 테스트나 재생성 import는 `overwrite`, 기존 dataset 누적 import는 `append`를 사용한다.
- 테스트 하네스는 smoke SQL, tokenizer unit, WARC importer unit, pg_regress, retrieval quality eval 축이다.
- 문서 하네스는 Markdown 링크, README 하네스 목록, roadmap 검증 명령 drift를 확인한다.
- 벤치마크 하네스는 BM25 document indexing, Lance row-by-row vs batch insert, synthetic WARC parse/SQL generation/execution split을 다룬다.

## 원칙

- 대형 WARC 파일 파싱은 확장 내부가 아니라 외부 importer에서 스트리밍 처리한다.
- PostgreSQL 안의 metadata/BM25 상태와 Lance dataset 상태가 서로 다른 실패 지점을 갖는다는 점을 명시적으로 다룬다.
- SQL 함수 표면은 가능한 한 작게 유지하되, importer와 검색 UI가 의존할 계약은 회귀 테스트로 고정한다.
- GitHub Actions 같은 원격 CI 대신 로컬 Docker 검증 명령을 문서화하고 반복 가능하게 유지한다.

## Phase 0: 완료된 MVP 기반

- [x] pgrx 0.19.1 기반 PostgreSQL 16 개발 컨테이너와 확장 설치 흐름
- [x] CJK bigram + ASCII tokenizer
- [x] table-backed BM25 색인/검색
- [x] Lance dataset 생성, 단건 삽입, batch 삽입, scan, vector search
- [x] BM25 + vector RRF hybrid search
- [x] WARC metadata table
- [x] WARC/WARC.GZ importer와 embedding JSONL/command 계약
- [x] `lance_insert_many()` 기반 batch vector insert
- [x] `--lance-mode create|overwrite|append`
- [x] smoke/unit/WARC/pg_regress 테스트 하네스
- [x] Lance/WARC synthetic 벤치마크 하네스

## Phase 1: Import Contract Hardening

목표: WARC import를 반복 실행해도 상태가 예측 가능하고, 실패했을 때 재시도 전략이 명확해야 한다.

- [x] 작은 WARC fixture를 이용한 DB 없는 end-to-end importer SQL 회귀 테스트 추가
- [x] 같은 fixture 계열을 `make test-warc-db` DB smoke 하네스에서 실행하기로 결정
- [x] importer가 생성한 SQL의 transaction chunk 동작을 DB smoke와 단위 테스트로 고정
- [x] Lance append 중복 row 정책 결정: 현재는 중복 허용, warning/summary로 명시, 반복 import는 overwrite 권장
- [x] generated SQL의 transaction chunk contract를 batch comment와 단위 테스트로 고정
- [x] `warc_import_run` table은 보류하고 `--summary-json` artifact를 현재 run metadata 계약으로 사용하기로 결정
- [x] `--summary-json`으로 machine-readable JSON summary 저장 지원
- [x] embedding command 실패, 누락 doc_id, vector dimension mismatch를 CLI/helper 수준에서 테스트

## Phase 2: Search Result Surface

목표: 검색 결과가 doc_id만이 아니라 WARC 검색 결과로 바로 소비될 수 있어야 한다.

- [x] `hybrid_warc_search`에서 `warc_record` metadata를 결합해 target URI, WARC date, status, content type을 반환
- [x] BM25/vector/hybrid 결과의 deterministic tie-breaker 적용
- [x] metadata filter는 `hybrid_warc_search()` 결과에 SQL `WHERE`를 적용하는 계약으로 설계
- [x] snippet/highlight는 extension 내부 함수가 아니라 SQL/client layer에서 `bm25_doc` join으로 처리하기로 결정
- [x] BM25-only, vector-only, hybrid diagnostics는 현재 `source` 컬럼만 안정 계약으로 두고 상세 breakdown은 품질 평가 단계로 보류

## Phase 3: Scale And Performance

목표: importer와 검색 core가 작은 데모를 넘어 수만-수백만 record 실험을 견딜 수 있어야 한다.

- [x] `bm25_index_document()`의 per-term SPI insert 병목 측정
- [x] BM25 batch index 함수 또는 importer-side bulk table insert 경로 설계
- [x] Lance batch insert 벤치마크에 baseline 수치 기록
- [x] WARC parse, SQL generation, DB execution을 분리해 benchmark output에 남기기
- [x] `make bench-*` 결과를 비교 가능한 Markdown/JSON 형식으로 저장하는 옵션 검토
- [x] 대용량 SQL 파일 대신 streaming `psql` 입력을 기본 경로로 유지할지 결정

Phase 3 기준선과 결정은 [BENCHMARKS.md](BENCHMARKS.md)에 기록한다. 현재 결론은 `bm25_index_document()` API를 유지하되 대량 색인은 importer-side bulk table insert 또는 staging/COPY 경로로 별도 설계하고, 대형 WARC import 실행은 SQL 파일보다 streaming `psql` stdin을 기본으로 둔다는 것이다.

## Phase 4: Packaging And Reproducibility

목표: 개발 컨테이너 밖에서도 버전과 업그레이드 경로가 설명 가능해야 한다.

- [x] extension version upgrade SQL 전략 수립
- [x] Dockerfile의 Rust stable toolchain을 특정 버전으로 고정할지 결정
- [x] PostgreSQL 13-17 feature gate는 유지하되, 실제 검증 대상 범위 명시
- [x] local-only CI 대체로 `make verify` alias가 필요한지 결정
- [x] release artifact와 local install 절차 문서화

Phase 4 패키징 정책은 [PACKAGING.md](PACKAGING.md)에 기록한다. 현재 검증 범위는 PostgreSQL 16이며, Rust toolchain은 `1.96.0`으로 고정한다.

## Phase 5: Retrieval Quality

목표: plumbing 검증을 넘어 실제 WARC 검색 품질을 측정한다.

- [x] 작은 공개 WARC sample 또는 synthetic relevance fixture 선정
- [x] real embedding provider/command 예제 추가
- [x] query set과 expected relevant docs를 이용한 smoke-level quality eval 추가
- [x] BM25/tokenizer 개선 후보 정리: stopword, ASCII stemming, CJK token 정책
- [x] RRF parameter와 source weighting을 설정 가능하게 할지 결정

Phase 5 품질 기준선과 결정은 [QUALITY.md](QUALITY.md)에 기록한다. 현재는 synthetic relevance fixture를 기준으로 `make eval-quality`에서 hit-rate@3/MRR@3를 측정한다.

## Phase 6: Documentation And Harness Governance

목표: Phase 1-5 이후 작업 선택 기준을 문서화하고, 문서/하네스 drift를 로컬 검증에서 잡는다.

- [x] 미래 청사진을 [BLUEPRINT.md](BLUEPRINT.md)에 분리해 corpus quality, import operations, search API, packaging matrix, scale/storage horizon으로 정리
- [x] `make check-docs`를 추가해 Markdown local link, README 하네스 목록, roadmap 검증 명령을 확인
- [x] `make verify`에 docs check와 `tools/check_docs.py` syntax check 포함
- [x] README 디렉토리 구조와 테스트/평가 하네스 표에 public WARC smoke와 docs check 반영

## Phase 7: BM25 Bulk Import Baseline

목표: importer-side `--bm25-mode bulk`가 기존 `bm25_index_document()` 호출 경로와 비교해 어느 지점에서 이득인지 측정할 수 있어야 한다.

- [x] `tools/bench_warc_bm25_modes.py`를 추가해 같은 synthetic WARC input에서 `function`/`bulk` SQL generation, SQL size, optional DB execute 시간을 비교
- [x] `make bench-warc-bm25-modes` target과 `BENCH_OUT_DIR` JSON/Markdown artifact 출력을 추가
- [x] Lance hash vector 포함/제외 모드를 모두 지원해 full import path와 BM25/metadata-only path를 분리 측정
- [x] 100/500 row smoke baseline을 [BENCHMARKS.md](BENCHMARKS.md)에 기록

## Phase 8: BM25 COPY Import Path

목표: `INSERT ... VALUES` bulk SQL size가 커질 때 쓸 수 있는 psql `COPY FROM stdin` 기반 BM25 적재 경로를 추가한다.

- [x] `--bm25-mode copy`를 추가해 importer-side tokenizer 결과를 `bm25_doc`/`bm25_term` COPY stream으로 적재
- [x] copy mode는 batch transaction 안에서 해당 doc_id의 기존 `bm25_doc`을 삭제한 뒤 COPY해 재실행 최종 상태를 보존
- [x] unit test와 live DB WARC importer smoke를 copy mode로 검증
- [x] `tools/bench_warc_bm25_modes.py`가 `function`/`bulk`/`copy` 전체 모드를 비교하도록 갱신

## Phase 9: External Quality Fixture

목표: synthetic 내장 fixture를 넘어 실제 corpus label을 품질 평가 하네스에 넣을 수 있어야 한다.

- [x] `tools/eval_quality.py --fixture-json <path>`를 추가해 외부 docs/query/vector/relevance fixture를 로드
- [x] `QUALITY_FIXTURE=<path> make eval-quality`로 Makefile 경로 연결
- [x] `tests/fixtures/quality_fixture.json` 예제 fixture와 `tests/test_eval_quality.py` 단위 테스트 추가
- [x] fixture schema, external smoke baseline, real corpus label 계약을 [QUALITY.md](QUALITY.md)에 기록

## Phase 10: Quality Fixture Builder

목표: importer record JSONL, embedding JSONL, query label JSON을 `eval-quality`가 바로 소비할 수 있는 external fixture로 변환한다.

- [x] `tools/make_quality_fixture.py`를 추가해 records, vectors, labels를 fixture schema로 join
- [x] `make make-quality-fixture` target과 `QUALITY_RECORDS_JSONL`/`QUALITY_EMBEDDING_JSONL`/`QUALITY_QUERIES_JSON` 변수 연결
- [x] `tests/fixtures/quality_queries.json`와 `tests/test_make_quality_fixture.py`로 success, missing vector, unknown label doc_id 검증
- [x] fixture builder pipeline을 [QUALITY.md](QUALITY.md)와 README에 문서화

## Phase 11: BM25 Scale Follow-up

목표: 100/500 row smoke 기준선을 넘어 importer-side `bulk`/`copy` 경로가 더 큰 synthetic corpus에서 어떤 선택지를 주는지 기록한다.

- [x] `make bench-warc-bm25-modes`로 1000/2000 row full import 비교 실행
- [x] `WARC_COMPARE_NO_HASH_VECTORS=1`로 BM25/metadata-only 비교 실행
- [x] [BENCHMARKS.md](BENCHMARKS.md)에 full import와 BM25-only 결과, COPY/bulk 선택 기준을 기록
- [x] COPY mode를 psql-driven large full-import 후보로 유지하고, BM25-only는 bulk/COPY를 계속 나란히 비교하기로 결정

## Phase 12: Public WARC Quality Smoke

목표: synthetic fixture 밖에서 public WARC sample을 labeled quality eval pipeline까지 연결한다.

- [x] `tools/make_public_warc_quality_fixture.py`로 documented pywb public WARC sample을 external quality fixture로 변환
- [x] `make public-warc-quality-fixture`와 `make eval-public-warc-quality` target 추가
- [x] 로컬 WARC fixture 기반 단위 테스트로 label matching, fixture output, intermediate artifact output 검증
- [x] public WARC quality smoke는 optional network target으로 유지하고 `make verify`에는 포함하지 않기로 문서화

## Phase 13: PostgreSQL Version Smoke Scaffold

목표: PostgreSQL 16 전체 검증은 유지하면서, PG13-17 fresh install 호환성을 선택적으로 확인할 수 있어야 한다.

- [x] Dockerfile에 `PG_MAJOR` build arg를 추가해 base image, server dev package, pgrx init flag, Cargo `pgXX` feature, install path를 같은 major로 맞춤
- [x] `make build-pg-version`, `make smoke-pg-version`, `make pg-version-matrix`로 major별 build + `CREATE EXTENSION` smoke를 실행할 수 있게 함
- [x] `make release-artifacts`를 `PG_MAJOR` aware로 바꿔 `dist/pgwarc_lance-<version>-pgXX/` artifact를 추출할 수 있게 함
- [x] 기본 `make verify` 범위는 PostgreSQL 16 full regression 기준으로 유지하고, version matrix는 optional packaging smoke로 문서화
- [x] 2026-09-13 `PG_MATRIX="13 14 15 16 17"` packaging run에서 artifact `5/5 valid`, official install smoke `5/5 pass`, packaging run doctor errors/warnings `0/0`을 기록함

## Phase 14: Release Artifact Content Check

목표: artifact를 추출한 뒤 PostgreSQL을 띄우기 전에도 산출물 구조와 버전 drift를 빠르게 잡을 수 있어야 한다.

- [x] `tools/check_release_artifacts.py`를 추가해 `.so`, `.control`, `pgwarc_lance--<version>.sql` 존재 여부와 기본 내용을 검증
- [x] checker가 shared library ELF header, control `default_version`/`module_pathname`, unreplaced `@CARGO_VERSION@`, generated SQL 핵심 symbol을 확인하도록 함
- [x] `make check-release-artifacts` target을 추가하고 `make release-artifacts` 추출 직후 자동 실행되도록 연결
- [x] fake artifact fixture 기반 단위 테스트로 정상 artifact, 누락 파일, non-ELF, control version mismatch, SQL symbol 누락을 고정

## Phase 15: Release Artifact Install Smoke

목표: build image 안에서 생성한 artifact가 깨끗한 PostgreSQL runtime image에서도 설치되는지 확인한다.

- [x] `tools/smoke_release_artifacts.py`를 추가해 `DIST_DIR` artifact를 disposable official `postgres:<PG_MAJOR>` 컨테이너에 복사하고 `CREATE EXTENSION` smoke를 실행
- [x] target container의 `pg_config --pkglibdir`/`--sharedir`를 사용해 install path를 runtime image 기준으로 확인
- [x] `make smoke-release-artifacts` target을 추가하고 `check-release-artifacts` 이후 install smoke를 수행하도록 연결
- [x] Docker command sequencing, artifact copy destination, validation failure short-circuit, cleanup 동작을 단위 테스트로 고정

## Phase 16: Release Artifact Matrix Targets

목표: 단일 PostgreSQL major artifact target을 `PG_MATRIX` 전체로 반복 실행할 수 있게 한다.

- [x] `make release-artifacts-matrix` target을 추가해 `PG_MATRIX`의 major별 `release-artifacts`를 순차 실행
- [x] `make smoke-release-artifacts-matrix` target을 추가해 이미 추출된 major별 artifact install smoke를 순차 실행
- [x] README, PACKAGING, BLUEPRINT, docs drift checker에 matrix target 계약을 문서화
- [x] matrix target은 optional heavyweight packaging verification으로 유지하고 기본 `make verify` 범위에는 넣지 않음

## Phase 17: Release Artifact Matrix Report

목표: major별 artifact matrix 실행 결과를 재현 가능한 report artifact로 남길 수 있어야 한다.

- [x] `tools/report_release_artifacts.py`를 추가해 `PG_MATRIX` artifact의 검증 상태, file size, SHA-256 digest를 JSON/Markdown으로 기록
- [x] `make report-release-artifacts` target을 추가하고 `BENCH_OUT_DIR` 기반 report output 변수와 연결
- [x] 누락된 major artifact가 있으면 실패하되 요청된 report file은 작성하도록 CLI 동작을 고정
- [x] 단위 테스트로 major parsing, valid artifact digest, missing major failure, Markdown error rendering, output file 생성을 검증

## Phase 18: Release Upgrade Smoke Scaffold

목표: 첫 version bump 전에 `ALTER EXTENSION UPDATE` 검증 경로를 미리 고정한다.

- [x] `tools/smoke_release_upgrade.py`를 추가해 old/new artifact 디렉토리와 `pgwarc_lance--OLD--NEW.sql` upgrade script를 검증
- [x] disposable official PostgreSQL 컨테이너에서 old version을 `CREATE EXTENSION VERSION`으로 설치한 뒤 new artifact와 upgrade script를 복사하고 `ALTER EXTENSION UPDATE TO`를 실행하도록 구성
- [x] `make smoke-release-upgrade` target을 추가하고 `UPGRADE_OLD_VERSION`, `UPGRADE_OLD_DIST_DIR`, `UPGRADE_NEW_VERSION`, `UPGRADE_NEW_DIST_DIR` override 계약을 문서화
- [x] 단위 테스트로 SQL literal escaping, upgrade script validation, runtime extension dir copy, ALTER SQL, validation short-circuit, cleanup sequencing을 검증

## Phase 19: Release Upgrade Matrix Target

목표: 단일 major upgrade smoke를 `PG_MATRIX` 전체로 반복 실행할 수 있게 한다.

- [x] `make smoke-release-upgrade-matrix` target을 추가해 `PG_MATRIX`의 major별 `smoke-release-upgrade`를 순차 실행
- [x] matrix target이 `UPGRADE_OLD_VERSION`을 필수 입력으로 요구하고, 각 major의 기본 old/new artifact path를 재사용하도록 구성
- [x] README, PACKAGING, BLUEPRINT, docs drift checker에 upgrade matrix 계약을 문서화
- [x] upgrade matrix는 optional heavyweight packaging verification으로 유지하고 기본 `make verify` 범위에는 넣지 않음

## Phase 20: Import Summary Doctor

목표: importer `--summary-json` artifact를 DB 없이 검사해 재시도 위험과 summary drift를 빠르게 판단한다.

- [x] `tools/doctor_import_summary.py`를 추가해 required field, type, Lance mode, BM25 mode, execute flag, SQL size 계약을 검증
- [x] Lance append duplicate risk, `execute=false`, zero records, Lance URI without vectors를 warning으로 분리
- [x] `make doctor-import-summary` target과 `BENCH_OUT_DIR` 기반 JSON/Markdown report output을 연결
- [x] 단위 테스트로 정상 summary, append retry warning, missing field, empty SQL, zero records, CLI output 파일 생성을 검증

## Phase 21: Run Directory Manifest

목표: benchmark, quality, import, packaging 산출물을 한 run directory에 모았을 때 재현 가능한 파일 manifest를 남긴다.

- [x] `tools/report_run_directory.py`를 추가해 run directory 파일을 재귀 스캔하고 크기와 SHA-256 digest를 JSON/Markdown으로 기록
- [x] known artifact 이름을 benchmark, quality, import, packaging kind로 분류하고 unknown 파일은 generic artifact로 보존
- [x] `make report-run-directory` target과 `RUN_DIR`/`BENCH_OUT_DIR` 기반 `run_manifest.json`/`run_manifest.md` output을 연결
- [x] 단위 테스트로 digest, artifact kind, output manifest self-exclusion, missing directory failure, empty directory warning, CLI output 생성을 검증

## Phase 22: Run Metadata Capture

목표: run directory에 파일 manifest뿐 아니라 실험을 재실행하는 데 필요한 git/command/env metadata를 남긴다.

- [x] `tools/write_run_metadata.py`를 추가해 `run_metadata.json`/`run_metadata.md`에 generated_at, run dir, command, git head/branch/dirty/status, 선택 env 값을 기록
- [x] env 이름에 `TOKEN`, `SECRET`, `PASSWORD`, `KEY`, `COOKIE`가 포함되면 값이 redaction되도록 해 metadata capture가 비밀값을 저장하지 않게 함
- [x] `make write-run-metadata` target과 `RUN_METADATA_COMMAND`, `RUN_METADATA_ENV`, `RUN_DIR`/`BENCH_OUT_DIR` 기반 output을 연결
- [x] `report-run-directory`가 `run_metadata.*`와 `run_manifest.*`를 metadata artifact로 분류하도록 갱신
- [x] 단위 테스트로 env redaction, git metadata, Markdown rendering, CLI JSON/Markdown output 생성을 검증

## Phase 23: Run Directory Doctor

목표: run directory가 benchmark, quality, import, packaging 실험별 필수 산출물을 갖췄는지 DB 없이 검사한다.

- [x] `tools/doctor_run_directory.py`를 추가해 `generic`, `benchmark`, `quality`, `import`, `packaging` profile별 required artifact를 확인
- [x] `--require`/`RUN_DOCTOR_REQUIRE`로 run-specific 필수 파일을 추가할 수 있게 함
- [x] `run_metadata.json`과 `run_manifest.json`을 읽어 dirty git state, missing command, manifest 누락 entry를 warning으로 보고
- [x] `make doctor-run-directory` target과 `RUN_DOCTOR_PROFILE`, `RUN_DOCTOR_JSON`, `RUN_DOCTOR_MARKDOWN` output을 연결
- [x] 단위 테스트로 profile success/failure, 추가 required artifact, manifest warning, dirty/missing command warning, CLI output failure를 검증

## Phase 24: Run Directory Finalization

목표: run directory 마감 절차를 하나의 target으로 표준화해 metadata, manifest, doctor 산출물을 누락 없이 남긴다.

- [x] `make finalize-run-directory` target을 추가해 `write-run-metadata`, `report-run-directory`, `doctor-run-directory`, final `report-run-directory`를 순서대로 실행
- [x] final manifest가 `run_doctor.json`/`run_doctor.md`까지 포함하도록 doctor 실행 후 manifest를 다시 생성
- [x] `RUN_METADATA_COMMAND`, `RUN_METADATA_ENV`, `RUN_DOCTOR_PROFILE`, `RUN_DOCTOR_REQUIRE` override 계약을 nested Make 호출에 전달
- [x] README, BLUEPRINT, BENCHMARKS, docs drift checker에 run finalization 흐름을 문서화

## Phase 25: Release Smoke Matrix Report

목표: PostgreSQL major별 release install/upgrade smoke 실행 결과를 run directory에 남길 수 있는 report artifact로 고정한다.

- [x] `tools/report_release_smoke_matrix.py`를 추가해 `PG_MATRIX` major별 Makefile target 실행 결과, exit code, duration, stdout/stderr tail을 JSON/Markdown으로 기록
- [x] `make report-release-smoke-matrix` target과 `RELEASE_SMOKE_TARGET`, `RELEASE_SMOKE_MAKE_VARS`, `BENCH_OUT_DIR` 기반 output 변수를 연결
- [x] 민감한 Make 변수 이름(`TOKEN`, `SECRET`, `PASSWORD`, `KEY`, `COOKIE`)은 report command와 make_vars에서 redaction
- [x] `report-run-directory`와 `doctor-run-directory` packaging profile이 `release_smoke_matrix.json`을 packaging completeness artifact로 다루도록 갱신
- [x] 단위 테스트로 matrix parsing, failure continuation, log tail capture, redaction, CLI output 생성을 검증

## Phase 26: Quality Fixture Report

목표: real corpus quality loop에 들어가기 전에 external quality fixture의 schema와 label coverage를 DB 없이 report artifact로 남긴다.

- [x] `tools/report_quality_fixture.py`를 추가해 synthetic 또는 `QUALITY_FIXTURE` external fixture의 doc/query 수, vector dimension, expected label 수, labeled/unlabeled doc 수를 JSON/Markdown으로 기록
- [x] duplicate `doc_id`, duplicate query name, `QUALITY_FIXTURE_MIN_DOCS`, `QUALITY_FIXTURE_MIN_QUERIES` 기준 미달을 error로 보고
- [x] `make report-quality-fixture` target과 `BENCH_OUT_DIR` 기반 `quality_fixture_report.json`/`.md` output을 연결
- [x] `report-run-directory`가 `quality_fixture_report.*`를 quality artifact로 분류하고, `doctor-run-directory` quality profile이 `quality_fixture_report.json`과 `quality.json`을 함께 요구하도록 갱신
- [x] 단위 테스트로 fixture shape report, minimum query failure, duplicate doc_id failure, invalid fixture output 생성을 검증

## Phase 27: Quality Run Finalization Target

목표: quality fixture report, DB eval, run directory finalization을 하나의 target으로 묶어 quality baseline run을 누락 없이 닫는다.

- [x] `make finalize-quality-run` target을 추가해 `report-quality-fixture`, `eval-quality`, `finalize-run-directory`를 순서대로 실행
- [x] `RUN_DIR`/`BENCH_OUT_DIR` 기반 `quality_fixture_report.json`/`.md`, `quality.json`/`.md`, `run_metadata.*`, `run_manifest.*`, `run_doctor.*` output을 한 run directory에 모으도록 연결
- [x] nested finalization에서 `RUN_DOCTOR_PROFILE=quality`를 사용해 `quality_fixture_report.json`과 `quality.json` completeness를 확인
- [x] `QUALITY_FIXTURE`, `QUALITY_FIXTURE_MIN_DOCS`, `QUALITY_FIXTURE_MIN_QUERIES`, output path override, `RUN_METADATA_COMMAND`, `RUN_DOCTOR_REQUIRE`를 nested Make 호출에 전달
- [x] README, QUALITY, BLUEPRINT, docs drift checker에 quality run finalization 흐름을 문서화

## Phase 28: Public WARC Quality Run Finalization

목표: optional public WARC quality smoke도 fixture 생성부터 run directory finalization까지 한 target으로 재생성할 수 있게 한다.

- [x] `make finalize-public-warc-quality-run` target을 추가해 `public-warc-quality-fixture`와 `finalize-quality-run`을 순서대로 실행
- [x] `RUN_DIR`/`BENCH_OUT_DIR` 기반 `quality.fixture.json`, `records.jsonl`, `vectors.jsonl`, `quality_queries.json`, quality report/eval, run metadata/manifest/doctor output을 한 run directory에 모으도록 연결
- [x] `public-warc-quality-fixture` target이 optional `--records-jsonl`, `--embedding-jsonl`, `--queries-json` artifact outputs를 받을 수 있게 함
- [x] `report-run-directory`가 `quality_queries.json`을 quality artifact로 분류하도록 갱신
- [x] README, QUALITY, BLUEPRINT, docs drift checker에 optional public quality finalization 흐름을 문서화

## Phase 29: Quality Baseline Doctor

목표: real corpus quality baseline을 마감하기 전에 생성된 fixture report와 eval metrics가 acceptance threshold를 만족하는지 DB 없이 검사한다.

- [x] `tools/doctor_quality_baseline.py`를 추가해 `quality_fixture_report.json`과 `quality.json`의 fixture/doc/query count 일치, expected label 수, hit-rate, MRR 기준을 검사
- [x] `make doctor-quality-baseline` target과 `BENCH_OUT_DIR`/`RUN_DIR` 기반 `quality_baseline_doctor.json`/`.md` output을 연결하고 `finalize-quality-run` 흐름에 포함
- [x] `QUALITY_BASELINE_MIN_DOCS`, `QUALITY_BASELINE_MIN_QUERIES`, `QUALITY_BASELINE_MIN_EXPECTED_LABELS`, `QUALITY_BASELINE_MIN_HIT_RATE`, `QUALITY_BASELINE_MIN_MRR` threshold override를 지원
- [x] `report-run-directory`가 `quality_baseline_doctor.*`를 quality artifact로 분류하도록 갱신
- [x] 단위 테스트로 threshold pass/fail, invalid fixture report, count mismatch, CLI output 생성을 검증
- [x] README, QUALITY, BLUEPRINT, docs drift checker에 quality baseline doctor 흐름을 문서화

## Phase 30: Release Run Finalization Target

목표: release artifact matrix report와 release smoke matrix report를 packaging run directory로 한 번에 마감한다.

- [x] `make finalize-release-run` target을 추가해 `report-release-artifacts`, `report-release-smoke-matrix`, `finalize-run-directory`를 순서대로 실행
- [x] `RUN_DIR`/`BENCH_OUT_DIR` 기반 `release_artifacts_matrix.json`/`.md`, `release_smoke_matrix.json`/`.md`, `run_metadata.*`, `run_manifest.*`, `run_doctor.*` output을 한 run directory에 모으도록 연결
- [x] nested finalization에서 `RUN_DOCTOR_PROFILE=packaging`을 사용해 release artifact matrix와 release smoke matrix completeness를 확인
- [x] `RELEASE_SMOKE_TARGET`, `RELEASE_SMOKE_MAKE_VARS`, output path override, `RUN_METADATA_COMMAND`, `RUN_DOCTOR_REQUIRE`를 기존 release report/finalization 흐름으로 전달
- [x] README, PACKAGING, BLUEPRINT, docs drift checker에 release run finalization 흐름을 문서화

## Phase 31: Import Run Finalization Target

목표: importer `--summary-json` 산출물과 import doctor, run metadata/manifest/doctor를 한 run directory로 마감한다.

- [x] `make finalize-import-run` target을 추가해 `IMPORT_SUMMARY`를 `RUN_DIR`/`BENCH_OUT_DIR`의 `import.summary.json`으로 보관하고 `doctor-import-summary`, `finalize-run-directory`를 순서대로 실행
- [x] `RUN_DIR`/`BENCH_OUT_DIR` 기반 `import.summary.json`, `import_doctor.json`/`.md`, `run_metadata.*`, `run_manifest.*`, `run_doctor.*` output을 한 run directory에 모으도록 연결
- [x] nested finalization에서 `RUN_DOCTOR_PROFILE=import`를 사용해 `import.summary.json` completeness를 확인
- [x] `RUN_METADATA_COMMAND`, `RUN_DOCTOR_REQUIRE`, output path override를 import run finalization 흐름으로 전달
- [x] README, BLUEPRINT, docs drift checker에 import run finalization 흐름을 문서화

## Phase 32: Benchmark Run Finalization Target

목표: BM25/Lance/WARC benchmark baseline을 실행부터 run directory finalization까지 한 target으로 마감한다.

- [x] `make finalize-benchmark-run` target을 추가해 `bench`와 `finalize-run-directory`를 순서대로 실행
- [x] `RUN_DIR`/`BENCH_OUT_DIR` 기반 `bm25.json`, `lance.json`, `warc.json`, benchmark Markdown, `run_metadata.*`, `run_manifest.*`, `run_doctor.*` output을 한 run directory에 모으도록 연결
- [x] `finalize-benchmark-run`이 `bm25.json`, `lance.json`, `warc.json`을 추가 필수 benchmark artifact로 요구하도록 연결
- [x] `BENCH_ROWS`, `BENCH_DIM`, `BENCH_BATCH`, `BM25_WORDS`, `WARC_ROWS`, `WARC_WORDS`, `RUN_METADATA_COMMAND`, `RUN_DOCTOR_REQUIRE`를 기존 benchmark/finalization 흐름으로 전달
- [x] README, BENCHMARKS, BLUEPRINT, docs drift checker에 benchmark run finalization 흐름을 문서화

## Phase 33: Benchmark Doctor Compatibility

목표: core benchmark finalization은 엄격하게 유지하되 targeted benchmark run directory doctor는 계속 사용할 수 있게 한다.

- [x] `doctor-run-directory`의 `benchmark` profile은 metadata/manifest 중심의 범용 profile로 유지
- [x] `finalize-benchmark-run`이 `BENCH_RUN_DOCTOR_REQUIRE` 기본값으로 `bm25.json`, `lance.json`, `warc.json`을 추가 요구하도록 변경
- [x] `RUN_DOCTOR_REQUIRE`를 `BENCH_RUN_DOCTOR_REQUIRE`와 병합해 사용자가 benchmark run별 필수 artifact를 추가할 수 있게 함
- [x] targeted `warc_bm25_modes.json` run directory가 benchmark profile을 계속 통과하는 단위 테스트 추가
- [x] README와 ROADMAP 설명을 benchmark profile 자체 강화가 아니라 finalization target의 추가 요구사항으로 정정

## Phase 34: Run Directory Report Status Checks

목표: run directory doctor가 profile별 필수 파일 존재뿐 아니라 report artifact의 성공 상태도 확인하게 한다.

- [x] `doctor-run-directory`가 quality report의 `valid`, import doctor의 `all_valid`, release report의 `all_valid`/`all_passed` 필드를 검사하도록 강화
- [x] optional `quality_baseline_doctor.json`이 존재하면 `valid=true`를 요구하도록 연결
- [x] targeted benchmark compatibility는 유지하고 status check는 quality/import/packaging report artifact에만 적용
- [x] 단위 테스트로 quality baseline doctor failure, import doctor failure, packaging smoke report failure를 검증
- [x] README와 ROADMAP에 run doctor status check 흐름을 문서화

## Phase 35: Targeted BM25 Modes Run Finalization

목표: WARC BM25 function/bulk/copy 비교 benchmark도 실행부터 run directory finalization까지 한 target으로 마감한다.

- [x] `make finalize-warc-bm25-modes-run` target을 추가해 `bench-warc-bm25-modes`와 `finalize-run-directory`를 순서대로 실행
- [x] `WARC_BM25_MODES_RUN_DOCTOR_REQUIRE` 기본값으로 `warc_bm25_modes.json`을 추가 요구해 targeted benchmark artifact 존재를 확인
- [x] `WARC_COMPARE_ROWS`, `WARC_COMPARE_EXECUTE`, `WARC_COMPARE_NO_HASH_VECTORS`, `BENCH_DIM`, `BENCH_BATCH`, `RUN_METADATA_COMMAND`, `RUN_DOCTOR_REQUIRE`를 기존 benchmark/finalization 흐름으로 전달
- [x] README, BENCHMARKS, BLUEPRINT, docs drift checker에 targeted BM25 modes run finalization 흐름을 문서화

## Phase 36: Benchmark Artifact Schema Checks

목표: benchmark run doctor가 필수 benchmark JSON의 존재뿐 아니라 기본 schema와 비어 있지 않은 결과를 확인하게 한다.

- [x] `benchmark` profile 자체는 generic으로 유지하고, `RUN_DOCTOR_REQUIRE`에 포함된 알려진 benchmark JSON만 schema 검사 대상으로 제한
- [x] `bm25.json`, `lance.json`, `warc.json`, `warc_bm25_modes.json`의 expected `benchmark` name, positive row/size/count, non-empty result/mode list를 검사
- [x] core `finalize-benchmark-run`과 targeted `finalize-warc-bm25-modes-run`이 각각 요구하는 benchmark JSON을 doctor가 검증하도록 연결
- [x] 단위 테스트로 malformed core benchmark JSON과 missing BM25 mode targeted benchmark JSON failure를 검증
- [x] README, BENCHMARKS, BLUEPRINT, ROADMAP에 benchmark artifact schema check 흐름을 문서화

## Phase 37: Run Manifest Integrity Checks

목표: run directory doctor가 manifest에 기록된 파일 크기와 SHA-256 drift를 감지하게 한다.

- [x] `doctor-run-directory`가 `run_manifest.json`의 file entry path가 run directory 안에 있고 실제 파일을 가리키는지 확인하도록 강화
- [x] manifest entry에 `bytes`가 있으면 현재 file size와 비교하고, `sha256`이 있으면 현재 SHA-256 digest와 비교
- [x] legacy/test manifest처럼 `bytes`/`sha256`이 없는 entry는 기존 호환성을 위해 presence check만 수행
- [x] 단위 테스트로 manifest byte/digest mismatch와 manifest entry missing file failure를 검증
- [x] README, BENCHMARKS, BLUEPRINT, ROADMAP에 manifest integrity check 흐름을 문서화

## Phase 38: Finalization Doctor Artifact Requirements

목표: quality/import finalization target이 생성한 doctor artifact를 run doctor 필수 산출물로 요구한다.

- [x] `QUALITY_RUN_DOCTOR_REQUIRE=quality_baseline_doctor.json` 기본값을 추가하고 `finalize-quality-run`의 `RUN_DOCTOR_REQUIRE`와 병합
- [x] `IMPORT_RUN_DOCTOR_REQUIRE=import_doctor.json` 기본값을 추가하고 `finalize-import-run`의 `RUN_DOCTOR_REQUIRE`와 병합
- [x] quality/import profile 자체 요구사항은 호환성을 위해 유지하고 finalization target만 더 엄격하게 동작하도록 제한
- [x] README, QUALITY, BLUEPRINT, ROADMAP에 target 전용 doctor artifact requirement를 문서화

## Phase 39: Documentation Phase Count Drift Check

목표: README와 ROADMAP의 현재 완료 phase 범위가 최신 ROADMAP phase header와 어긋나면 docs check가 실패하게 한다.

- [x] `tools/check_docs.py`가 ROADMAP의 최신 `## Phase N:` header를 계산하도록 추가
- [x] README의 `현재 완료된 Phase 1-N` 문구와 ROADMAP의 `현재 로드맵의 Phase 1-N` 문구가 최신 phase와 일치하는지 검사
- [x] stale README 완료 phase count 문구를 Phase 1-39로 갱신
- [x] 단위 테스트로 최신 phase 계산과 phase count mismatch error를 고정

## Phase 40: Quality Label Doctor

목표: real corpus quality fixture를 만들기 전에 query label JSON을 DB/embedding 없이 검증한다.

- [x] `tools/doctor_quality_labels.py`를 추가해 query name/text, vector dimension, expected label 수, duplicate query name을 검사
- [x] optional records JSONL을 받아 `expected_doc_ids`가 실제 record `doc_id`에 존재하는지 확인하고 labeled/unlabeled doc coverage를 report
- [x] `make doctor-quality-labels` target과 `BENCH_OUT_DIR` 기반 `quality_labels_doctor.json`/`.md` output을 연결
- [x] `report-run-directory`가 `quality_labels_doctor.*`를 quality artifact로 분류하도록 갱신
- [x] 단위 테스트로 valid labels, unknown doc_id, duplicate query name, vector dim mismatch, minimum label threshold, CLI output 생성을 검증
- [x] README, QUALITY, BLUEPRINT, docs drift checker에 quality label doctor 흐름을 문서화

## Phase 41: Quality Label Run Finalization

목표: query label doctor report도 run directory metadata/manifest/doctor와 함께 마감할 수 있게 한다.

- [x] `make finalize-quality-labels-run` target을 추가해 `doctor-quality-labels`와 `finalize-run-directory`를 순서대로 실행
- [x] `RUN_DIR`/`BENCH_OUT_DIR` 기반 `quality_labels_doctor.json`/`.md`, `run_metadata.*`, `run_manifest.*`, `run_doctor.*` output을 한 run directory에 모으도록 연결
- [x] `QUALITY_LABELS_RUN_DOCTOR_REQUIRE=quality_labels_doctor.json` 기본값으로 label doctor report를 필수 artifact로 요구
- [x] `QUALITY_LABELS_JSON`, `QUALITY_LABELS_RECORDS_JSONL`, minimum threshold, `RUN_METADATA_COMMAND`, `RUN_DOCTOR_REQUIRE`를 nested Make 호출에 전달
- [x] README, QUALITY, BLUEPRINT, docs drift checker에 quality label run finalization 흐름을 문서화

## Phase 42: Required Status Artifact Checks

목표: generic run finalization에서도 `RUN_DOCTOR_REQUIRE`로 요구된 알려진 status report의 성공 플래그를 검사한다.

- [x] `doctor-run-directory`가 required artifact 목록에 있는 알려진 status artifact를 profile과 무관하게 검사하도록 확장
- [x] `quality_labels_doctor.json`의 `valid=true`를 검사 대상으로 추가해 `finalize-quality-labels-run`의 generic profile을 강화
- [x] 단위 테스트로 required `quality_labels_doctor.json`이 `valid=false`일 때 run doctor가 실패하는지 검증
- [x] README, QUALITY, BLUEPRINT, ROADMAP에 label doctor status check 흐름을 문서화

## Phase 43: Quality Fixture Build Run Finalization

목표: records/vector/query labels에서 DB 없이 quality fixture와 fixture report를 만들고 run directory로 마감한다.

- [x] `make finalize-quality-fixture-build-run` target을 추가해 `doctor-quality-labels`, `make-quality-fixture`, `report-quality-fixture`, `finalize-run-directory`를 순서대로 실행
- [x] `RUN_DIR`/`BENCH_OUT_DIR` 기반 `quality_labels_doctor.json`/`.md`, `quality.fixture.json`, `quality_fixture_report.json`/`.md`, `run_metadata.*`, `run_manifest.*`, `run_doctor.*` output을 한 run directory에 모으도록 연결
- [x] `QUALITY_BUILD_RUN_DOCTOR_REQUIRE=quality_labels_doctor.json quality.fixture.json quality_fixture_report.json` 기본값으로 DB-free fixture build 산출물을 필수 artifact로 요구
- [x] `RUN_METADATA_ENV`에 `QUALITY_RECORDS_JSONL`, `QUALITY_EMBEDDING_JSONL`, `QUALITY_QUERIES_JSON`를 추가해 build input 경로를 metadata로 남기게 함
- [x] README, QUALITY, BLUEPRINT, docs drift checker에 quality fixture build finalization 흐름을 문서화

## Phase 44: Required Quality Fixture Schema Checks

목표: run doctor가 required `quality.fixture.json`의 존재뿐 아니라 fixture schema drift도 검사한다.

- [x] `doctor-run-directory`가 `RUN_DOCTOR_REQUIRE`에 `quality.fixture.json`이 포함되면 `eval_quality.fixture_from_dict()`로 schema, vector dimension, expected doc ids를 검사
- [x] required `quality.fixture.json`에서 duplicate `doc_id`와 duplicate query name을 error로 보고
- [x] 단위 테스트로 unknown expected doc id와 duplicate doc/query failure를 검증
- [x] README, QUALITY, BLUEPRINT, ROADMAP에 required quality fixture schema check 흐름을 문서화

## Phase 45: Quality Eval Artifact Schema Checks

목표: run doctor가 required `quality.json` eval metrics artifact의 schema drift를 검사한다.

- [x] `doctor-run-directory`가 profile requirement 또는 `RUN_DOCTOR_REQUIRE`에 포함된 `quality.json`을 schema 검사 대상으로 다루도록 추가
- [x] `quality.json`의 `benchmark`, fixture name, doc/query/vector/k counts, hit-rate/MRR 범위, non-empty result rows를 검사
- [x] result row의 query name, expected/returned doc id lists, hit boolean, reciprocal rank 범위를 검사
- [x] 단위 테스트로 malformed `quality.json` benchmark, metric range, empty result failure를 검증
- [x] README, QUALITY, BLUEPRINT, ROADMAP에 quality eval artifact schema check 흐름을 문서화

## Phase 46: Quality Fixture Report Schema Checks

목표: run doctor가 required `quality_fixture_report.json`의 success flag뿐 아니라 report schema drift도 검사한다.

- [x] `doctor-run-directory`가 profile requirement 또는 `RUN_DOCTOR_REQUIRE`에 포함된 `quality_fixture_report.json`을 schema 검사 대상으로 다루도록 추가
- [x] fixture name, vector/doc/query/expected label counts, labeled/unlabeled counts, query rows를 검사
- [x] query row expected label total이 top-level `expected_label_count`와 일치하는지 검사
- [x] 단위 테스트로 malformed expected label total failure를 검증
- [x] README, QUALITY, BLUEPRINT, ROADMAP에 quality fixture report schema check 흐름을 문서화

## Phase 47: Quality Baseline Doctor Schema Checks

목표: run doctor가 required `quality_baseline_doctor.json`의 success flag뿐 아니라 baseline doctor schema drift도 검사한다.

- [x] `doctor-run-directory`가 `RUN_DOCTOR_REQUIRE`에 포함된 `quality_baseline_doctor.json`을 schema 검사 대상으로 다루도록 추가
- [x] fixture name, thresholds, doc/query/expected label counts, k, hit-rate/MRR, result rows를 검사
- [x] threshold probability ranges와 result count/query count 일치를 검사
- [x] 단위 테스트로 malformed threshold와 result count mismatch failure를 검증
- [x] README, QUALITY, BLUEPRINT, ROADMAP에 quality baseline doctor schema check 흐름을 문서화

## Phase 48: Import Summary Artifact Schema Checks

목표: run doctor가 import profile의 `import.summary.json` 존재뿐 아니라 importer summary schema drift도 검사한다.

- [x] `doctor-run-directory`가 profile requirement 또는 `RUN_DOCTOR_REQUIRE`에 포함된 `import.summary.json`을 schema 검사 대상으로 다루도록 추가
- [x] importer summary required fields, numeric fields, BM25/Lance mode enum, execute/psql type contract를 검사
- [x] Lance mode/vector/append duplicate consistency를 기존 import summary doctor 규칙으로 검사
- [x] 단위 테스트로 malformed `import.summary.json` field/type/enum failure를 검증
- [x] README, BLUEPRINT, ROADMAP에 import summary artifact schema check 흐름을 문서화

## Phase 49: Import Doctor Artifact Schema Checks

목표: run doctor가 required `import_doctor.json`의 `all_valid` flag뿐 아니라 report schema drift도 검사한다.

- [x] `doctor-run-directory`가 `RUN_DOCTOR_REQUIRE`에 포함된 `import_doctor.json`을 schema 검사 대상으로 다루도록 추가
- [x] top-level `all_valid`, summary/error/warning counts, non-empty `summaries` list를 검사
- [x] per-summary path, valid flag, errors/warnings lists, embedded valid summary schema를 검사
- [x] 단위 테스트로 malformed count와 summary validity mismatch failure를 검증
- [x] README, BLUEPRINT, ROADMAP에 import doctor artifact schema check 흐름을 문서화

## Phase 50: Release Artifact Matrix Schema Checks

목표: run doctor가 packaging profile의 `release_artifacts_matrix.json` success flag뿐 아니라 artifact matrix schema drift도 검사한다.

- [x] `doctor-run-directory`가 profile requirement 또는 `RUN_DOCTOR_REQUIRE`에 포함된 `release_artifacts_matrix.json`을 schema 검사 대상으로 다루도록 추가
- [x] extension/version/dist root, PostgreSQL major list, artifact row count/order, `all_valid` consistency를 검사
- [x] artifact row의 dist dir, valid/errors consistency, shared library/control/sql file report exists/bytes/SHA-256 schema를 검사
- [x] 단위 테스트로 malformed matrix count, validity, file digest failure를 검증
- [x] README, BLUEPRINT, PACKAGING, ROADMAP에 release artifact matrix schema check 흐름을 문서화

## Phase 51: Release Smoke Matrix Schema Checks

목표: run doctor가 packaging profile의 `release_smoke_matrix.json` success flag뿐 아니라 release smoke result schema drift도 검사한다.

- [x] `doctor-run-directory`가 profile requirement 또는 `RUN_DOCTOR_REQUIRE`에 포함된 `release_smoke_matrix.json`을 schema 검사 대상으로 다루도록 추가
- [x] schema version, generated metadata, Make target/variables, PostgreSQL major list, result row count/order를 검사
- [x] result row의 command, exit code, passed flag, duration, stdout/stderr tail, error field와 pass/exit-code consistency를 검사
- [x] 단위 테스트로 malformed result count, passed/exit-code, `all_passed` mismatch failure를 검증
- [x] README, BLUEPRINT, PACKAGING, ROADMAP에 release smoke matrix schema check 흐름을 문서화

## Phase 52: Run Metadata Schema Checks

목표: run doctor가 모든 profile의 `run_metadata.json` 존재뿐 아니라 metadata schema drift도 검사한다.

- [x] `doctor-run-directory`가 `run_metadata.json`의 schema version, generated/run dir metadata, command field를 검사하도록 추가
- [x] environment map value와 warnings list schema를 검사
- [x] git root/head/head_short/branch/dirty/status schema와 head prefix consistency를 검사
- [x] 단위 테스트로 malformed metadata schema failure를 검증
- [x] README, BLUEPRINT, ROADMAP에 run metadata schema check 흐름을 문서화

## Phase 53: Multi-Query Public WARC Quality Specs

목표: public WARC quality fixture가 단일 label/query smoke를 넘어 여러 labeled query를 같은 corpus에서 재현할 수 있게 한다.

- [x] `--query-spec-json`으로 유일한 `name`, `query`, `label_text`를 가진 query spec 배열을 읽도록 추가
- [x] 각 label이 최소 하나의 WARC record와 일치하는지 검사하고 query별 평균 vector를 생성
- [x] 중복 query name, 빈 field, 무일치 label failure를 테스트
- [x] Make target과 run metadata 환경에 query spec 경로를 연결
- [x] README, QUALITY, BLUEPRINT, ROADMAP에 다중 query fixture 계약과 다음 corpus manifest 이슈를 문서화

## Phase 54: Public WARC Provenance Manifest

목표: quality fixture를 만든 WARC source와 query spec을 checksum·시각·fixture 요약과 함께 재현 가능하게 남긴다.

- [x] `--manifest-json`으로 source URL/로컬 경로, bytes, SHA-256, 다운로드 시각을 기록
- [x] query spec 파일 경로/SHA-256/실제 spec과 fixture doc/query/vector 요약을 같은 manifest에 기록
- [x] `PUBLIC_WARC_PROVENANCE_JSON` Make 변수와 public quality finalization 경로를 연결
- [x] local WARC path 기준 provenance manifest 회귀 테스트를 추가
- [x] README, QUALITY, BLUEPRINT, ROADMAP에 manifest schema와 다음 multi-source corpus 이슈를 문서화

## Phase 55: Multi-Source Public WARC Corpus Manifest

목표: 여러 WARC 입력을 하나의 public quality fixture로 합치고, 입력별 provenance를 corpus manifest에서 복원 가능하게 만든다.

- [x] `--source-spec-json`으로 path 또는 URL source spec 배열을 읽고 중복/누락 입력을 거부
- [x] 여러 로컬 WARC와 URL 다운로드 WARC를 합쳐 fixture를 생성하고 duplicate `doc_id`를 실패 처리
- [x] schema v2 manifest의 `source_spec`와 `sources` 목록에 입력별 kind/URL/path/bytes/SHA-256/downloaded_at을 기록
- [x] `PUBLIC_WARC_SOURCE_SPEC_JSON` Make 변수, run metadata, public quality finalization 경로를 연결
- [x] 로컬 입력과 `file://` URL을 함께 사용하는 corpus manifest 회귀 테스트를 추가
- [x] README, QUALITY, BLUEPRINT, ROADMAP에 source spec 계약과 다음 source별 coverage 통계 이슈를 문서화

## Phase 56: Public WARC Source Coverage Statistics

목표: corpus manifest가 입력별 parse/import 손실과 query label coverage를 함께 보여 실제 quality baseline의 입력 편중을 확인할 수 있게 한다.

- [x] source별 raw WARC record, imported record, skipped record 수를 schema v2 manifest에 기록
- [x] source별 fixture doc/labeled/unlabeled coverage를 query label 기준으로 기록
- [x] 빈 response record 회귀 테스트로 skipped 통계를 고정하고 duplicate `doc_id` guard를 유지
- [x] README, QUALITY, BLUEPRINT, ROADMAP에 source coverage 필드와 다음 doctor threshold 이슈를 문서화

## Phase 57: Quality Fixture Source Coverage Thresholds

목표: quality fixture report가 schema v2 corpus manifest의 source coverage를 검증하고, 작은 입력이나 높은 skipped 비율을 baseline 전에 차단한다.

- [x] `report_quality_fixture.py`가 provenance schema v2의 source records/coverage 합계와 fixture doc count를 검증
- [x] `--min-source-docs`와 `--max-source-skipped-ratio` threshold 및 source coverage Markdown 표를 추가
- [x] schema v1 provenance는 warning으로 호환하고, malformed schema v2와 threshold 초과는 report invalid로 처리
- [x] `QUALITY_PROVENANCE_JSON`, `QUALITY_MIN_SOURCE_DOCS`, `QUALITY_MAX_SOURCE_SKIPPED_RATIO` Make 변수를 quality/public finalization에 연결
- [x] source coverage report/threshold 회귀 테스트와 README, QUALITY, BLUEPRINT, ROADMAP 문서화를 추가

## Phase 58: Run Doctor Source Coverage Contract

목표: quality fixture report에 기록된 WARC source coverage와 threshold 결과를 run directory의 required artifact 계약에서도 재검증해, report와 finalization 사이의 변조·누락을 발견한다.

- [x] schema v2 `source_count`/`source_coverage` 필수 구조와 source별 raw/imported/skipped 및 labeled/unlabeled 합계를 검사
- [x] source별 skipped ratio, source 최소 doc 수, 전체 fixture coverage 합계와 threshold field를 재검증
- [x] provenance가 없거나 schema v1인 기존 빈 source coverage report는 하위 호환
- [x] run doctor 회귀 테스트와 README, QUALITY, BLUEPRINT, ROADMAP 문서화를 추가

## Phase 59: Quality Baseline Source Coverage Contract

목표: quality baseline doctor artifact가 WARC source coverage와 threshold 문맥을 보존하고, run doctor가 baseline 산출물에서도 같은 계약을 재검증하게 한다.

- [x] `doctor_quality_baseline.py`가 schema v2 provenance/source coverage와 source threshold를 JSON/Markdown에 보존
- [x] source count 불일치를 baseline doctor에서 실패시키고, source coverage 표를 baseline Markdown에 추가
- [x] run doctor가 required `quality_baseline_doctor.json`의 보존된 source coverage 합계와 ratio/threshold를 검사
- [x] synthetic/schema v1 baseline 하위 호환 회귀 테스트와 문서화를 추가

## Phase 60: Public WARC Source Specification Contract

목표: 여러 WARC 입력을 받는 public quality fixture builder가 문서에 정의한 단일 source identity 계약(path 또는 URL)을 코드에서도 강제해 provenance와 다운로드 동작을 모호하지 않게 한다.

- [x] source spec에서 path와 URL을 동시에 지정한 입력을 명확한 validation error로 거부
- [x] path-only와 URL-only multi-source 동작의 기존 회귀 경로를 유지
- [x] source spec contract 회귀 테스트와 README, QUALITY, BLUEPRINT, ROADMAP 문서화를 추가

## Phase 61: WARC Record Length Contract

목표: WARC parser가 필수 record payload length를 누락·음수 입력에서 0 또는 stream 전체로 오해하지 않고 명확히 실패하게 한다.

- [x] WARC `Content-Length` 누락과 음수 값을 입력 오류로 거부
- [x] 정상 WARC/WARC.GZ parsing과 truncated payload 오류 동작을 유지
- [x] malformed length 회귀 테스트와 README, QUALITY, BLUEPRINT, ROADMAP 문서화를 추가

## Phase 62: HTTP Chunked Payload Contract

목표: WARC 안에 보관된 HTTP `Transfer-Encoding: chunked` payload가 잘린 데이터나 불완전한 종료를 정상 본문으로 오인하지 않고 명확히 실패하게 한다.

- [x] chunk size와 optional extension을 검증하고 chunk payload 길이와 data terminator를 확인
- [x] terminating zero-size chunk, trailer section, malformed/truncated 입력 오류를 회귀 테스트로 고정
- [x] chunked payload validation과 다음 real corpus/upgrade baseline 청사진을 README, QUALITY, BLUEPRINT, ROADMAP에 문서화

## Phase 63: HTTP Gzip Content-Encoding Contract

목표: WARC 안에 보관된 HTTP `Content-Encoding: gzip` payload의 압축 해제 실패를 원본 바이트의 정상 본문으로 오인하지 않고 명확히 실패하게 한다.

- [x] 정상 gzip HTTP payload의 압축 해제 동작을 유지
- [x] 잘못된 gzip header와 truncated gzip stream을 일관된 importer 입력 오류로 거부
- [x] gzip content-encoding validation과 다음 real corpus/upgrade baseline 청사진을 README, QUALITY, BLUEPRINT, ROADMAP에 문서화

## Phase 64: Importer Selection Limit Contract

목표: WARC importer의 record count와 text-length selection 인자가 음수 입력을 허용하거나 `--limit 0`을 한 건 import으로 오해하지 않게 한다.

- [x] `--limit`과 `--min-text-chars`의 non-negative 입력 계약을 CLI와 내부 loader에서 검증
- [x] `--limit 0`이 파일을 읽지 않고 빈 record 목록을 반환하는 동작을 회귀 테스트로 고정
- [x] selection limit validation과 다음 real corpus/upgrade baseline 청사진을 README, QUALITY, BLUEPRINT, ROADMAP에 문서화

## Phase 65: Importer Document Identity Contract

목표: 여러 WARC 입력 또는 중복 record가 같은 stable `doc_id`를 만들 때 PostgreSQL multi-row upsert 오류와 Lance duplicate vector 적재를 실행 전에 차단한다.

- [x] importer가 SQL 생성 전 imported record의 `doc_id` 중복을 source 경로와 함께 거부
- [x] 한 WARC 안의 duplicate record 회귀 테스트를 추가하고 기존 unique record 경로를 유지
- [x] imported `doc_id` uniqueness 계약과 다음 real corpus/upgrade baseline 청사진을 README, QUALITY, BLUEPRINT, ROADMAP에 문서화

## Phase 66: HTTP Charset Parameter Contract

목표: WARC HTTP payload의 `Content-Type` charset parameter가 `=` 주변 공백을 포함해도 선언된 문자셋으로 decode되어 비ASCII 본문이 손상되지 않게 한다.

- [x] charset parameter의 optional whitespace와 기존 quoted value parsing을 지원
- [x] iso-8859-1 비ASCII payload 회귀 테스트로 UTF-8 fallback 오해석을 방지
- [x] charset decoding contract와 다음 real corpus/upgrade baseline 청사진을 README, QUALITY, BLUEPRINT, ROADMAP에 문서화

## Phase 67: WARC Content-Length Uniqueness Contract

목표: WARC header block의 중복 `Content-Length`가 마지막 값으로 덮어써져 record payload 경계를 흐리지 않게 한다.

- [x] header parser가 중복 `Content-Length`를 payload read 전에 명확한 입력 오류로 거부
- [x] 동일 값의 중복 length 회귀 테스트로 overwrite 우회를 차단
- [x] WARC length uniqueness contract와 다음 real corpus/upgrade baseline 청사진을 README, QUALITY, BLUEPRINT, ROADMAP에 문서화

## Phase 68: Embedding Vector Numeric Contract

목표: external embedding JSONL/command의 `NaN`, `Infinity`, out-of-range integer가 Lance/SQL vector 적재 경로까지 도달하지 않게 한다.

- [x] vector 값이 finite numeric이고 Python float 범위 안에 있는지 importer에서 검증
- [x] `NaN`/`Infinity`와 거대 정수 회귀 테스트로 invalid vector를 명확히 거부
- [x] embedding numeric validation과 다음 real corpus/upgrade baseline 청사진을 README, QUALITY, BLUEPRINT, ROADMAP에 문서화

## Phase 69: Embedding Document Identity Contract

목표: external embedding JSONL/command가 같은 required `doc_id`에 여러 vector를 반환할 때 마지막 값으로 조용히 덮어쓰지 않고 Lance/SQL 적재 전에 중단한다.

- [x] importer가 required `doc_id`별 duplicate vector row를 source와 line number를 포함한 입력 오류로 거부
- [x] 동일 `doc_id` vector 회귀 테스트로 overwrite 우회를 차단
- [x] embedding document identity contract와 다음 real corpus/upgrade baseline 청사진을 README, QUALITY, BLUEPRINT, ROADMAP에 문서화

## Phase 70: Quality Fixture Finite Vector Contract

목표: external quality fixture 평가 경로가 `NaN`, `Infinity`, float 변환 overflow를 SQL/Lance setup까지 전달하지 않게 한다.

- [x] `tools/eval_quality.py`와 `tools/doctor_quality_labels.py`의 doc/query vector validation에서 finite numeric과 Python float 범위를 확인
- [x] `NaN`/`Infinity`/음의 무한대/거대 정수 regression test로 invalid external fixture를 명확히 거부
- [x] quality fixture finite-vector contract와 다음 real corpus/upgrade baseline 청사진을 README, QUALITY, BLUEPRINT, ROADMAP에 문서화

## Phase 71: Quality Relevance Label Identity Contract

목표: external quality fixture의 query가 같은 `doc_id`를 여러 번 relevance label로 선언해 label 수와 품질 metric을 조용히 부풀리지 않게 한다.

- [x] `tools/eval_quality.py`가 fixture를 DB/Lance setup 전에 query별 duplicate `expected_doc_ids`로 거부
- [x] direct external fixture regression test로 duplicate relevance label overwrite/count inflation 우회를 차단
- [x] quality relevance label identity contract와 다음 real corpus/upgrade baseline 청사진을 README, QUALITY, BLUEPRINT, ROADMAP에 문서화

## Phase 72: Quality Fixture Document/Query Identity Contract

목표: quality fixture 생성·평가 경로가 duplicate `doc_id`와 query `name`을 SQL/Lance setup 또는 metric 집계 전에 거부해 문서 충돌과 query 결과 덮어쓰기를 차단한다.

- [x] `tools/eval_quality.py`가 fixture를 DB/Lance setup 전에 duplicate document `doc_id`와 query `name`으로 거부
- [x] `tools/make_quality_fixture.py`도 동일한 strict identity 검증을 사용해 생성 단계에서 duplicate query name을 거부
- [x] direct external fixture와 fixture builder regression test로 duplicate document/query identity 충돌을 고정
- [x] quality fixture identity contract와 다음 real corpus/upgrade baseline 청사진을 README, QUALITY, BLUEPRINT, ROADMAP에 문서화

## Phase 73: Quality Fixture Document ID Domain Contract

목표: quality fixture의 document와 relevance label `doc_id`가 PostgreSQL `bigint`에 안전한 양의 signed-64 정수인지 생성·평가 전에 확인해 묵시적 형변환과 SQL/Lance 적재 실패를 차단한다.

- [x] `tools/eval_quality.py`와 `tools/make_quality_fixture.py`가 0, 음수, bool, 숫자 문자열, bigint 범위 초과 `doc_id`를 거부
- [x] `tools/doctor_quality_labels.py`의 record/expected label 검증을 같은 PostgreSQL bigint 상한에 맞춤
- [x] direct fixture, fixture builder, label doctor regression test와 다음 real corpus/upgrade baseline 청사진을 문서화

## Phase 74: Benchmark Regression Comparison Harness

목표: finalized WARC BM25 mode benchmark artifact를 동일 조건의 baseline과 비교해 허용 회귀를 독립적으로 판정한다.

- [x] `tools/compare_benchmark_runs.py`가 baseline/candidate의 benchmark 설정, rows/mode 집합과 `execute_s` 측정값을 검증
- [x] 명시적 non-negative `BENCHMARK_MAX_REGRESSION_RATIO`를 기준으로 mode별 회귀를 계산하고 threshold 초과 시 non-zero로 종료
- [x] JSON/Markdown 비교 report와 Makefile `compare-benchmark-runs` target을 추가해 기존 run artifact와 분리된 비교 결과를 보존
- [x] pass, regression, 설정 불일치, 미실행 artifact 단위 테스트와 README/BLUEPRINT/BENCHMARKS 문서화를 추가

## Phase 75: Benchmark Comparison Run Finalization

목표: benchmark comparison report의 자기신고만으로 성공 처리하지 않고, run metadata/manifest/doctor와 함께 재검수 가능한 분석 artifact로 보존한다.

- [x] `compare_benchmark_runs.py`가 persisted report schema, count, mode별 threshold 계산과 `valid`/`passed` consistency를 검증할 수 있게 함
- [x] `doctor-run-directory`가 required `benchmark_comparison.json`을 독립적으로 검사하고 `passed=true` status를 요구하도록 연결
- [x] `make finalize-benchmark-comparison-run`으로 비교 report와 metadata/manifest/doctor를 같은 run directory에 보존
- [x] tampered report, status failure, finalization smoke와 문서 drift 회귀 테스트를 추가

## Phase 76: Benchmark Comparison Input Provenance

목표: baseline/candidate benchmark 입력의 bytes와 SHA-256을 comparison report에 보존해 원본 drift를 사후에 확인할 수 있게 한다.

- [x] comparator가 schema v2 `input_provenance`에 baseline/candidate 경로, bytes, SHA-256을 기록
- [x] persisted report validator와 `run_doctor`가 입력 파일이 남아 있을 때 현재 bytes/hash drift를 검출
- [x] 기존 schema v1 report는 하위 호환하고 새 report의 provenance 및 Markdown 표시를 회귀 테스트
- [x] 입력 provenance 한계와 real corpus/upgrade baseline 미측정을 문서화

## Phase 77: WARC Provenance Source Drift Doctor

목표: real labeled WARC baseline을 만들기 전에 provenance manifest 자체를 quality report와 독립적으로 검수해 source identity와 입력 drift를 놓치지 않게 한다.

- [x] `doctor_run_directory.py`가 required `warc_provenance.json` schema v1/v2의 source identity, source/query-spec/fixture count와 source coverage arithmetic을 재검증
- [x] 기록된 local source, source-spec, query-spec 파일이 현재 존재하면 bytes/SHA-256 drift를 오류로 검출하고, URL/삭제 파일은 재다운로드하지 않는 한계를 유지
- [x] `finalize-public-warc-quality-run`이 provenance manifest를 required run artifact로 넘기도록 연결
- [x] valid/drifted provenance 회귀 테스트와 README, QUALITY, BLUEPRINT 문서화를 추가

## Phase 78: Quality Run Provenance Staging

목표: real-corpus quality finalization이 외부 provenance 경로를 참조만 한 채 run directory를 닫지 않도록, 입력 manifest를 표준 artifact로 보존하고 report/doctor가 같은 복사본을 사용하게 한다.

- [x] `finalize-quality-run`이 `QUALITY_PROVENANCE_JSON`을 `<RUN_DIR>/warc_provenance.json`으로 stage
- [x] quality fixture report가 외부 입력 대신 staged provenance path를 기록하고, provenance가 지정된 quality run의 run doctor required artifact를 연결
- [x] source manifest 누락·동일 파일 staging·실제 drift 경로를 안전하게 처리하고 Make expansion/documentation으로 계약을 고정

## Phase 79: Real Corpus Input Manifest Contract

목표: real labeled WARC baseline을 만들기 전에 실제 corpus와 독립 query/qrels가 존재하고 사용 권한·체크섬·버전·평가 조건이 기록됐는지 독립적으로 검증하고, synthetic/public smoke를 real baseline으로 포장하지 못하게 한다.

- [x] `tools/doctor_real_corpus_input.py`가 manifest schema v1에 corpus source identity(path/URL, bytes, SHA-256), license/license_url/redistribution, query/qrels 버전, label 독립성, 평가 commit, PostgreSQL 버전, 검색 설정을 요구
- [x] `corpus.origin`/`labels.independence`가 `external`이 아니면 거부하고, 20-50개 query 범위와 local source/label 파일의 bytes/SHA-256 drift를 검증
- [x] `make doctor-real-corpus-input` target과 JSON/Markdown report, 회귀 테스트를 추가
- [x] 실제 corpus 미확보 상태를 blocker로 기록하고 real baseline 재개에 필요한 입력 요구사항을 README/QUALITY/BLUEPRINT에 명시

## Phase 80: Release Upgrade Input Preflight Contract

목표: versioned release upgrade smoke가 old artifact와 upgrade SQL 없이 성공으로 오인되거나 임의로 만들어진 입력에 의존하지 않도록, 실제 추출된 artifact tree에서 upgrade 입력 존재/버전을 DB·Docker 없이 먼저 검증하고 미측정 상태를 명시적으로 report한다.

- [x] `tools/doctor_release_upgrade_inputs.py`가 현재 `Cargo.toml` version과 `dist/`의 실제 artifact directory/version만으로 old/new version, old/new artifact validity, `pgwarc_lance--OLD--NEW.sql` 존재를 검사
- [x] old artifact 또는 upgrade SQL이 없으면 `valid=false`/`status=blocked`, 사용 가능한 version 목록, 누락 사유, resume 요구사항과 재개 명령을 JSON/Markdown report로 남기고 non-zero로 종료
- [x] `make doctor-release-upgrade-inputs` target과 `BENCH_OUT_DIR` 기반 `release_upgrade_input_doctor.json`/`.md` output, `UPGRADE_OLD_VERSION` override 계약을 추가하고 `make smoke-release-upgrade` 실행 전 preflight로 문서화
- [x] 2026-09-14 저장소에는 version `0.1.0` artifact만 있고 old artifact/upgrade SQL이 없어 versioned upgrade smoke가 미측정(blocked)임을 실제 report로 기록
- [x] preflight 입력 누락, version 동일, old artifact 누락, upgrade script 누락, ready 상태 회귀 테스트를 추가

## Phase 81: Release Upgrade Preflight Enforcement

목표: versioned release upgrade smoke가 finalization/matrix 경로에서 preflight을 우회해 실행되지 않도록, `RELEASE_SMOKE_TARGET=smoke-release-upgrade`일 때 각 major의 입력 preflight을 smoke 이전에 강제하고 blocked major를 명시적으로 non-zero report로 남긴다.

- [x] `RELEASE_SMOKE_TARGET=smoke-release-upgrade`일 때 `report-release-smoke-matrix`가 `report-release-upgrade-smoke-matrix`로 위임하고, `tools/report_release_upgrade_smoke_matrix.py`가 `PG_MATRIX`의 각 major에 대해 `doctor_release_upgrade_inputs`를 같은 old/new dist/version/pg_major 입력으로 먼저 실행
- [x] blocked major는 `smoke-release-upgrade`를 실행하지 않고 `preflight`/`preflight_passed`/`smoke_skipped`/`smoke_skipped_reason` field를 가진 `release_smoke_matrix.json` result row로 보존하며 전체 report를 non-zero로 종료
- [x] 기존 `release_smoke_matrix.json` schema와 run doctor 검사를 유지하고, non-upgrade target은 기존 `report_release_smoke_matrix.py` 경로를 그대로 사용
- [x] 2026-09-14 실제 저장소 상태(`0.1.0` artifact만 존재)에서 `RELEASE_SMOKE_TARGET=smoke-release-upgrade make finalize-release-run`이 smoke attempt `0/5`와 blocked per-major report로 non-zero(`2`)에 종료하고 `ALTER EXTENSION UPDATE`를 실행하지 않음을 기록
- [x] ready old/new artifact와 upgrade script fixture에서 preflight 통과 후 matching input으로 기존 `smoke-release-upgrade` command를 dispatch하는 회귀 테스트, mixed-major blocked/ready, non-upgrade 경로 보존, nonzero 전파 테스트 추가

## Phase 82: Run-Scoped Execution Budget Gate

목표: 2시간 분석 루프가 예산 없이 무한 반복되거나 부분 산출물을 성공으로 포장하지 않도록, 분석 하네스가 공유하는 run-scoped 실행 예산(iteration/query/deadline) 계약을 프로젝트 로컬에 고정한다.

- [x] `tools/execution_budget.py`가 `max_iterations`, `max_queries`, `deadline_seconds`를 work 시작 전에 검증하고 missing, wrong-type, 0, 음수를 거부
- [x] 하나의 `ExecutionBudget` 인스턴스가 iteration, query, retry, child work에 공유되어 retry/subtask가 counter를 reset하지 못하고 N+1 callback을 호출하지 않도록 exact-N gate를 구현
- [x] deadline은 `time.monotonic`(테스트용 injectable clock) 기준이며 `cap_timeout()`이 남은 시간으로 query timeout을 cap
- [x] 예산 소진 시 `budget_exhausted` machine-readable status(limit_kind/used/limit/partial-result 보존)를 남기고 partial output을 성공으로 표기하지 않음
- [x] `tools/eval_quality.py` query loop에 gate를 연결하고 `make eval-quality-budget` target으로 세 예산 값을 모두 요구하며 소진 시 non-zero로 종료
- [x] `tests/test_execution_budget.py`와 `tests/test_eval_quality.py`가 invalid config, exact boundary/N+1, retry 공유, child 공유, deadline/timeout을 deterministic fake clock으로 검증
- [x] 프로젝트 밖 `kilo-gpt-report-harness`는 이 저장소에서 수정하지 않고, 이 모듈을 하네스가 호출할 수 있는 프로젝트 로컬 경계로 문서화
- [x] 2026-09-14 Phase 82 correction: query loop가 iteration과 query를 개별 charge하면서 query limit가 먼저 소진될 때 차단된 N+1 시도가 `used_iterations`만 증가시키던 correctness bug를 수정했다. `ExecutionBudget.charge_query_unit()`이 `max_iterations`와 `max_queries`를 모두 먼저 검사하고 둘 다 통과할 때만 두 카운터를 원자적으로 차감하므로, 어느 한도가 먼저 걸려도 차단 시도는 어떤 카운터도 소비하지 않는다 (query limit 선도달 시 iteration도 미소비). 둘 다 도달한 경우 iteration limit이 결정론적으로 우선한다. query-first, iteration-first, simultaneous/잔여 카운터 의미를 결정론적으로 검증하는 회귀 테스트를 추가했고 retry/child/deadline semantics와 machine-readable status/partial result는 그대로 보존한다
- [x] 2026-09-14 Phase 82 timeout wiring: `tools/eval_quality.py`에 `--query-timeout-seconds`(Make `QUALITY_QUERY_TIMEOUT_SECONDS`)를 추가해 setup/query psql subprocess에 client-side timeout을 전달하고, 옵션 생략 시 `subprocess.run`에 `timeout` 인자를 넘기지 않아 기존 동작을 보존한다. `0`/음수/`NaN`/`Infinity`는 SQL 실행 전에 exit code 2로 거부한다. run budget이 있으면 각 subprocess 직전에 `remaining_seconds()`와 명시 timeout 중 더 짧은 값을 `cap_timeout()`으로 계산하고 deadline 소진 시 runner를 호출하지 않으며, budget 없이도 명시 timeout은 적용된다. setup/query는 새 budget query unit이 아니며 기존 iteration/query charge 의미는 바뀌지 않는다
- [x] 2026-09-14 Phase 82 timeout artifact contract: timeout 시 성공/0점으로 처리하지 않고 `status=quality_eval_timeout`, `success=false`, `timeout.stage`(`setup`|`query`)/`reason`/`limit_seconds`/partial 보존 여부와 완료된 `results`만 남기고 exit code 4로 종료한다. query timeout은 앞서 완료한 results만 partial로 보존하고 누락 query를 채우거나 자동 retry하지 않으며, setup timeout은 query를 전혀 실행하지 않는다. `budget_exhausted`(exit 3)와 partial artifact 계약은 유지하고, Python subprocess timeout이 PostgreSQL 서버 측 query cancellation을 보장하지 않는다는 client 경계를 문서화한다
- [x] 2026-09-14 Phase 82 timeout 테스트: `tests/test_eval_quality.py`가 CLI timeout 값 검증이 SQL 전에 실패, `subprocess.run` timeout 실제 전달/생략, query timeout only 및 명시 timeout+deadline cap(fake monotonic clock), deadline 소진 시 runner 미호출, setup/query timeout 각각 non-success/partial artifact/후속 query 미실행, 정상 경로와 기존 `budget_exhausted` 회귀를 mock 기반으로 검증한다
- [x] 2026-09-14 Phase 82 non-success 소비 계약: `tools/eval_quality.py`가 `artifact_status_kind()`/`artifact_status_errors()`/`artifact_diagnostics()`로 success/non-success/unknown/contradiction을 분류하고 원본 진단을 보존한다. `tools/doctor_quality_baseline.py`는 `quality_eval_timeout`/`budget_exhausted`를 `hit_rate_at_k`/`mrr_at_k` 변환보다 먼저 식별해 metric을 `n/a`로 비우고 원본 `status`/완료 결과 수/timeout(stage·reason·limit_seconds·client_side_only)/budget(limit_kind·used·limit) 진단을 기존 report JSON/Markdown에 보존한 채 non-zero로 종료한다. `tools/doctor_run_directory.py`는 성공 schema 검증과 non-success 구조 검증을 분리해 status별 오류와 timeout/budget partial 구조를 검사하고 `success=false`/unknown status/status·success 모순을 structured error로 처리하므로 timeout artifact가 남은 run directory는 valid=true가 될 수 없다
- [x] 2026-09-14 Phase 82 non-success 테스트와 전파: producer `main()`이 실제 생성하는 setup-timeout(결과 0)/query-timeout(partial)/budget-exhausted/success artifact와 모순·unknown 입력으로 두 doctor의 non-zero 종료, JSON/Markdown 내용, traceback 부재를 검증하고 기존 eval/budget·doctor 테스트 의미를 보존한다. `Makefile` `finalize-quality-run`의 eval-quality non-zero 전파를 그대로 유지하며 preflight나 실패 무시를 추가하지 않는다
- [x] 2026-09-14 Phase 82 query 전용 서버 statement_timeout: `tools/eval_quality.py`에 `--query-statement-timeout-seconds`(Make `QUERY_STATEMENT_TIMEOUT_SECONDS`) opt-in 서버 제한을 추가한다. Decimal 경계로 PostgreSQL ms 범위 `1..2147483647` 표현 가능 여부를 검증하고 `0`/음수/`NaN`/`Infinity`/1ms 미만/범위 초과를 SQL 전에 exit 2로 거부하며, 값이 없으면 서버 timeout SQL을 전혀 주입하지 않는다. 제한은 `query_sql`의 `hybrid_warc_search` SELECT 하나에만 적용하고 `setup_sql`에는 `SET LOCAL`/`statement_timeout`을 주입하지 않는다. query마다 별도 psql session에서 `BEGIN; SET LOCAL statement_timeout; SELECT; COMMIT;` 래퍼(`\set QUIET on`/`\set ON_ERROR_STOP on`/`\set VERBOSITY verbose`)로 command tag를 배제하고 SELECT 실패 후 COMMIT을 성공 처리하지 않는다
- [x] 2026-09-14 Phase 82 서버 제한 cap과 분류: query 직전에 명시 서버 제한/client effective limit/run budget remaining deadline 중 최단을 PostgreSQL 정수 ms로 내림(0ms 금지)하고 1ms 미만 cap이면 runner를 호출하지 않고 기존 client/budget 비성공 경로로 종료한다. 기존 `--query-timeout-seconds`는 client subprocess 제한으로만 유지하고 budget charge/setup·query 카운터 의미를 바꾸지 않는다. 서버 statement timeout은 SQLSTATE 57014만으로 추측하지 않고 `canceling statement due to statement timeout` 안정 진단이 확인될 때만 기존 `quality_eval_timeout`/exit 4 경로로 분류하며, 사용자 취소·기타 SQL 오류는 일반 오류 경로로, `subprocess.TimeoutExpired`는 client 경로로 남긴다. timeout artifact에 `requested_statement_timeout_ms`/`effective_statement_timeout_ms`/`timeout_source`/`sqlstate`를 선택 필드로 보존하고 새 top-level status/pg_cancel_backend 계층을 만들지 않으며 backend 종료·rollback·자원 회수 보장을 주장하지 않는다
- [x] 2026-09-14 Phase 82 서버 timeout 테스트와 문서: `tests/test_eval_quality.py`가 Make opt-in/미지정 command, setup 미주입·query 래퍼, 1ms/최대/소수 내림/invalid 입력, client·budget cap 조합과 fake clock, 1ms 미만 pre-run gate, server/client/budget/general SQL failure 분류, 57014지만 statement-timeout 사유가 아닌 미분류, 결과 파싱, server timeout artifact non-success와 두 doctor 소비, 정상 성공 회귀를 mock으로 검증한다(실시간 sleep 없음). README/QUALITY/BLUEPRINT/ROADMAP에 opt-in 사용법, client/server 차이, setup 제외 이유, race와 backend 정리 비보장, 다음 청사진(backend 상태 관측·취소 확인)을 기록한다


- [x] 2026-09-14 Phase 82 timeout observation 한계: `timeout_payload()`가 client timeout/확인된 서버 statement timeout artifact에만 `timeout.timeout_observation`(`statement_outcome`=`unknown`|`server_statement_timeout_reported`, `backend_state_after_timeout=unobserved`, `cancellation_completion=unverified`, `transaction_cleanup=unverified`)을 추가하고 성공/일반 SQL/pre-run 예산 소진에는 넣지 않는다. client timeout 예외/stderr의 statement-timeout 문구는 서버 outcome으로 승격하지 않으며 기존 SQLSTATE/진단 분류와 status/exit code, subprocess/SQL 동작을 바꾸지 않는다
- [x] 2026-09-14 Phase 82 timeout observation 검증과 문서: `tools/doctor_run_directory.py`가 observation 객체/enum을 정확히 검증하고 `client_side_only`-source-outcome 일관성을 확인하며 legacy observation-없음은 수용하되 미기록(unverified)으로 렌더링하고 malformed는 구조화 오류로만 처리한다. `tools/doctor_quality_baseline.py` Markdown이 observation 값/미기록을 표시하고 metric `n/a`/`valid=false`를 유지한다. `tests/test_eval_quality.py`/`tests/test_doctor_run_directory.py`/`tests/test_doctor_quality_baseline.py`가 client/server/success/general SQL/budget/legacy/malformed/wrong-source 케이스를 결정론적 mock으로 검증하고, README/QUALITY/BLUEPRINT/ROADMAP에 관측 한계와 다음 청사진(session identity, 별도 권한 connection, target/timeout race, budget, cleanup 판정)을 기록한다. real labeled corpus/qrels(Issue A)와 old/new artifact·upgrade SQL(Issue B) 입력은 계속 blocked/separate다

- [x] 2026-09-15 Phase 82 timeout observation schema 경계: `tools/doctor_run_directory.py`의 `validate_timeout_observation()`이 `eval_quality.TIMEOUT_OBSERVATION_FIELDS`의 네 필드만 허용하고 `backend_pid`/`cancelled`/`rollback_completed` 같은 unsupported claim을 `must not include unsupported fields` structured error로 거부한다. 네 필드 누락/`null`/non-object/bad enum/source-outcome 모순 규칙과 legacy observation-없음 수용, client timeout `unknown` 규칙은 그대로 유지되며 timeout exit code/status와 성공 평가 동작은 바뀌지 않는다. `tests/fixtures/timeout_observation_contract_cases.json`에 수기 계약 케이스를 추가하고 `tests/test_doctor_run_directory.py`가 독립 fixture 입력으로 검증하며 `tests/test_doctor_quality_baseline.py`가 unsupported claim이 있어도 non-success semantics(valid=false, metric n/a, results=[])가 유지됨을 확인한다. active cancellation/backend 상태 관측은 구현하지 않았고(계속 unobserved/unverified) pg_cancel_backend·pg_terminate_backend·권한 connection·PID SQL·자동 retry·새 runtime 필드를 추가하지 않는다. real labeled corpus/qrels(Issue A)와 old/new artifact·upgrade SQL(Issue B)은 계속 blocked다

- [x] 2026-09-15 Phase 82 prospective query attempt identity 계약: `contracts/quality_attempt_identity.v1.schema.json`에 strict v1 envelope(`schema_version=1`, correlation-only non-empty `attempt_id`, `capture_state`=`unavailable`|`captured`, 조건부 `session_identity`, `transaction_identity={"state":"unobserved"}`, `capture_source`=`none`|`query_session`)를 모든 object level `additionalProperties=false`로 고정하고, 양의 `backend_pid`(bool 불가)·explicit timezone ISO-8601 `backend_start`·조건 일관성 규칙을 schema와 description에 반영한다. cancellation/rollback/termination field는 정의하지 않는다

- [x] 2026-09-15 Phase 82 attempt identity doctor: `tools/doctor_quality_attempt_identity.py`가 stdlib만으로 `validate_identity(payload, label=...) -> list[str]`를 노출해 strict key/type/enum/조건 일관성, 양의 PID, timezone 포함 timestamp, 정확한 unobserved transaction object를 검증하고 JSON/Markdown optional output과 malformed 입력 non-zero exit를 제공한다. `make doctor-quality-attempt-identity QUALITY_ATTEMPT_IDENTITY_JSON=<path>`로만 실행되며 `eval-quality`/`doctor_run_directory`/run finalization/`make verify`는 호출하지 않는다. `tests/fixtures/quality_attempt_identity_contract_cases.json`은 producer 호출 없이 손으로 작성하고 `tests/test_doctor_quality_attempt_identity.py`가 fixture/CLI/exit code와 정상 timeout·eval artifact 무변경을 검증한다. structural consistency만 증명하며 authenticity·backend 존재·query/transaction identity·cancellation·rollback·cleanup을 증명하지 않는다
- [x] 2026-09-15 Phase 82 Issue A read-only quality input preflight: tools/preflight_quality_inputs.py와 make preflight-quality-inputs가 명시 corpus/query/qrels 경로의 존재·readability·bytes·SHA-256, 기존 query-label/records reference check, provenance/label-origin reference를 검사한다. PostgreSQL·WARC import·quality query·download·fixture 생성은 실행하지 않으며, missing input/provenance는 actionable blocked/non-zero report로 남긴다. report의 valid=true는 preflight checks만 통과했다는 뜻이고 qrels 독립성·진위·corpus 대표성·quality 측정은 증명하지 않는다. tests/test_preflight_quality_inputs.py가 missing/unreadable, records reference mismatch, query/qrels name mismatch, structural fixture, JSON/Markdown output을 검증하며 Issue A real labeled corpus/qrels와 Issue B old/new artifact·upgrade SQL은 계속 blocked다.
- [x] 2026-09-15 Phase 82 Issue A manifest gate connection: `QUALITY_INPUT_PREFLIGHT_MANIFEST_JSON`/`--manifest-json`을 지정하면 `tools/preflight_quality_inputs.py`가 기존 `doctor_real_corpus_input.build_report()`를 재사용해 manifest source/license/checksum, 독립 label, 20-50 query, evaluation context 판정을 같은 JSON/Markdown report에 보존한다. doctor error는 blocked로 전파하고 manifest 미지정은 unresolved로 남기며, manifest가 provenance를 대신할 수 있어도 quality_measured=false/real_evidence_established=false를 유지한다. DB·WARC import·network·fixture·baseline 측정은 실행하지 않고, valid manifest/invalid manifest/not-run 회귀 테스트를 추가했다. 실제 labeled corpus/qrels와 Issue B old/new artifact·upgrade SQL은 계속 blocked다.


## 최근 완료

- 2026-09-15 03:00 KST에 Phase 82 prospective query attempt identity 계약/acceptance boundary를 추가했다. `contracts/quality_attempt_identity.v1.schema.json`은 backend 관측이 아니라 future identity envelope의 strict v1 구조를 정의하고, `tools/doctor_quality_attempt_identity.py`는 stdlib opt-in validator로 `validate_identity(payload, label=...) -> list[str]`와 JSON/Markdown output, malformed 입력 non-zero exit를 제공한다. `capture_state`가 `unavailable`이면 `session_identity=null`/`capture_source=none`, `captured`이면 정확히 `{backend_pid, backend_start}`/`capture_source=query_session`이고, `backend_pid`는 양의 정수(bool 불가), `backend_start`는 explicit timezone ISO-8601, `transaction_identity`는 정확히 `{"state":"unobserved"}`다. 모든 object level `additionalProperties=false`로 `cancelled`/`rollback_completed`/`backend_name` 같은 unsupported claim을 거부한다. `make doctor-quality-attempt-identity QUALITY_ATTEMPT_IDENTITY_JSON=<path>`로만 실행되고 `eval-quality`/`doctor_run_directory`/run finalization/`make verify`는 호출하지 않으며, 기존 `timeout_observation` 네 필드와 eval-quality 결과 parsing/default 동작은 그대로 유지한다. 계약은 synthetic·opt-in이고 structural consistency만 증명하므로 backend/statement/transaction identity의 authenticity, backend의 계속된 존재, 취소·rollback·cleanup을 증명하지 않는다. fixture는 producer를 호출하지 않고 손으로 작성했다. 검증은 `python3 -m unittest discover -s tests -p 'test_*.py'`, `make test-warc`, `make check-docs`, `make verify`, `git diff --check`로 확인한다. next runtime gate는 same-session reliably framed identity를 quality query 이전에 capture하는 것과 미래 observer connection의 explicit permission·race·budget·cleanup evidence이며, Issue A real labeled corpus/qrels와 Issue B old/new artifact·upgrade SQL은 계속 blocked/separate다.

- 2026-09-15 01:00 KST에 Phase 82 timeout observation schema 경계를 강화했다. `tools/eval_quality.py`에 `TIMEOUT_OBSERVATION_FIELDS`(네 필드)를 정의하고 `tools/doctor_run_directory.py`의 `validate_timeout_observation()`이 그 네 필드만 허용하도록 했다. 객체가 있으면 `statement_outcome`(`unknown`|`server_statement_timeout_reported`), `backend_state_after_timeout=unobserved`, `cancellation_completion=unverified`, `transaction_cleanup=unverified`가 모두 정확해야 하며, 누락 필드·`null`·non-object·bad enum은 기존대로 structured error다. `client_side_only`-`timeout_source`-`statement_outcome` 일관성 규칙(관측이 없는 legacy timeout 수용, client timeout의 stderr statement-timeout 문구 비승격 포함)은 그대로 유지된다. 새로 `backend_pid`/`cancelled`/`rollback_completed` 같은 unsupported claim을 `timeout.timeout_observation must not include unsupported fields: ...` 오류로 거부한다. `tests/fixtures/timeout_observation_contract_cases.json`은 producer를 호출하지 않고 손으로 작성한 계약 케이스(수용 server/client, legacy 부재, `null`/non-object/누락 필드/bad enum, source-outcome 모순, 추가 claim 거부)를 담고, `tests/test_doctor_run_directory.py`가 이를 반복 검증하며 `tests/test_doctor_quality_baseline.py`는 unsupported claim이 있어도 timeout artifact가 `valid=false`, `hit_rate_at_k`/`mrr_at_k` `n/a`, `results=[]`이고 원본 진단만 보존됨을 확인한다. 이번 slice는 schema/문서 경계일 뿐 active cancellation이나 backend 상태 관측을 구현하지 않으며(값은 계속 `unobserved`/`unverified`), `pg_cancel_backend`/`pg_terminate_backend`·별도 권한 connection·PID SQL·자동 retry·새 runtime 필드를 추가하지 않고 기존 timeout exit code/status와 성공 평가 동작을 바꾸지 않는다. 검증은 `python3 -m unittest discover -s tests -p 'test_*.py'`, `make test-warc`, `make check-docs`, `make verify`, `git diff --check`로 확인한다. Issue A real labeled corpus/qrels와 Issue B old/new artifact·upgrade SQL 입력은 계속 blocked/separate다.

- 2026-09-14 23:00 KST에 Phase 82 timeout observation 한계 slice를 반영했다. `tools/eval_quality.py`의 `timeout_payload()`가 client timeout과 확인된 서버 statement timeout artifact에만 `timeout.timeout_observation`(`statement_outcome`=`unknown`|`server_statement_timeout_reported`, `backend_state_after_timeout=unobserved`, `cancellation_completion=unverified`, `transaction_cleanup=unverified`)을 추가하고 성공/일반 SQL/pre-run 예산 소진에는 넣지 않는다. `statement_outcome`은 기존 SQLSTATE/진단 분류를 바꾸지 않고 확인된 서버 path에만 `server_statement_timeout_reported`를 쓰며, client subprocess timeout(`subprocess.TimeoutExpired`→`SqlTimeout`, pre-run 1ms 미만 cap 포함)은 stderr에 statement-timeout 문구가 있어도 `unknown`이다. `unverified`는 관측하지 않았다는 뜻이지 실패·rollback·계속 실행을 확인한 것이 아니다. `tools/doctor_run_directory.py`는 observation 객체/enum을 정확히 검증하고 `client_side_only=false`는 `timeout_source=server_statement`+`statement_outcome=server_statement_timeout_reported`+서버 필드에만 허용하며, observation이 없는 legacy timeout은 수용하되 미기록(unverified)을 렌더링하고 malformed observation은 구조화 오류로만 처리한다. `tools/doctor_quality_baseline.py` Markdown은 observation 값/미기록을 표시하고 metric `n/a`/`valid=false`를 유지한다. 회귀 테스트는 client unknown, server reported, client stderr 비승격, success/general SQL/budget no-observation, 두 doctor의 legacy 수용과 미기록 표기, malformed object/enum 구조화 오류, server `client_side_only=false` 구조적 수용과 wrong source/outcome 거부를 결정론적 mock으로 검증하고 기존 timeout status/severity/exit code와 subprocess/SQL 동작은 그대로 유지한다. 검증은 `python3 -m unittest discover -s tests -p 'test_*.py'`(298 tests), `make test-warc`, `make check-docs`, `make verify`, `git diff --check`로 확인한다. 다음 청사진은 session identity(PID/backend/transaction), 별도 권한 connection, target/timeout race, budget, rollback/transaction cleanup 판정을 요구하는 backend 상태 관측/active cancellation 계약이며, real labeled corpus/qrels(Issue A)와 old/new artifact·upgrade SQL(Issue B) 입력은 계속 blocked/separate다.

- 2026-09-14 21:00 KST에 Phase 82 query 전용 서버 statement_timeout slice를 반영했다. `tools/eval_quality.py`가 `--query-statement-timeout-seconds`(Make `QUERY_STATEMENT_TIMEOUT_SECONDS`) opt-in 서버 제한을 받아 Decimal 경계로 PostgreSQL millisecond 범위 `1..2147483647ms` 표현 가능 여부를 검증하고 `0`/음수/`NaN`/`Infinity`/1ms 미만/범위 초과를 SQL 실행 전에 exit 2로 거부하며, 값이 없으면 서버 timeout SQL을 전혀 주입하지 않아 기존 동작을 보존한다. 제한은 `query_sql`의 `hybrid_warc_search` SELECT 하나에만 적용되고 `setup_sql`에는 `SET LOCAL`/`statement_timeout`을 주입하지 않으며 setup transaction 구조도 그대로 둔다. query마다 별도 psql subprocess/session에서 `\set QUIET on`/`\set ON_ERROR_STOP on`/`\set VERBOSITY verbose`와 `BEGIN; SET LOCAL statement_timeout = <ms>; SELECT ...; COMMIT;` 래퍼를 실행해 command tag가 결과에 섞이지 않고 SELECT 실패 후 COMMIT이 성공으로 진행되지 않는다. query 직전에 명시 서버 제한, client effective limit(`--query-timeout-seconds`), run budget remaining deadline 중 가장 짧은 값을 PostgreSQL 정수 ms로 내림해 적용하되 0ms는 만들지 않고, 1ms 미만 cap이면 runner를 호출하지 않고 기존 client/budget 비성공 경로로 종료한다. `--query-timeout-seconds`는 client subprocess 제한으로만 유지되고 서버 제한이 이를 대체하거나 늘리지 않으며 기존 budget charge/setup·query 카운터 의미는 바뀌지 않는다. 서버 statement timeout은 SQLSTATE 57014만으로 추측하지 않고 `canceling statement due to statement timeout` 안정 진단이 확인될 때만 기존 `quality_eval_timeout`/exit 4 경로로 분류하고, 사용자 취소·기타 SQL 오류는 일반 오류 경로, `subprocess.TimeoutExpired`는 client timeout 경로로 남긴다. timeout artifact에는 `requested_statement_timeout_ms`/`effective_statement_timeout_ms`/`timeout_source`(`server_statement`/`client`/`run_budget`)/`sqlstate`(없으면 null)를 선택 필드로 보존하고 `client_side_only`는 false가 되며 두 doctor는 이를 계속 `valid=false`로 구조화해 거부한다. 새 top-level status나 `pg_cancel_backend`/별도 취소 connection/자동 retry/ALTER SYSTEM은 추가하지 않았고, 서버 timeout이 backend 종료·rollback 완료·자원 회수나 취소 완료를 보장한다고 주장하지 않는다. 검증은 server timeout/invalid/캡 분류를 포함한 mock 기반 `python3 -m unittest tests.test_eval_quality`(47 tests)와 `make test-warc`, `make check-docs`, `make verify`, `git diff --check`로 확인했다. 이 변경은 harness 경계 증거이지 real quality baseline이 아니며, Issue A real labeled corpus/qrels와 Issue B old/new artifact·versioned upgrade SQL은 계속 미측정(blocked)이다. 다음 청사진은 client/server timeout 이후 backend 상태 관측·취소 확인 계약이다.

- 2026-09-14 19:00 KST에 Phase 82 non-success artifact 소비 계약 slice를 반영했다. `tools/eval_quality.py`에 `artifact_status_kind()`/`artifact_status_errors()`/`artifact_diagnostics()`를 추가해 producer가 남기는 `quality_eval_timeout`/`budget_exhausted` partial artifact를 success/non-success/unknown/contradiction으로 분류하고 원본 status·완료 결과 수·timeout/budget 진단을 보존한다. `tools/doctor_quality_baseline.py`는 metric 숫자 변환 전에 non-success를 식별해 `valid=false`, `hit_rate_at_k`/`mrr_at_k` `n/a`, 원본 진단을 report JSON/Markdown에 남기고 non-zero로 종료하며 partial을 0점 success metric으로 재계산하지 않는다. `tools/doctor_run_directory.py`의 `quality.json` 검사는 성공 schema와 non-success 구조 검증을 분리하고 `success=false`/unknown status/status·success 모순도 structured error로 처리하므로 timeout artifact가 남은 run directory는 valid=true가 되지 않는다. 회귀 테스트는 producer `main()`이 실제 생성하는 setup-timeout(결과 0), query-timeout(partial), budget-exhausted, 정상 성공 artifact와 모순·unknown 입력을 두 doctor에 넣어 non-zero, JSON/Markdown 내용, traceback 부재를 검증했고, 기존 54 eval/budget 테스트와 doctor 테스트 의미는 보존했다. `Makefile` `finalize-quality-run`은 eval-quality의 non-zero(exit 4)를 그대로 전파하고 preflight나 실패 무시를 추가하지 않았다. timeout은 여전히 Python client subprocess 경계일 뿐 PostgreSQL 서버 측 query cancellation이 아니며, real labeled corpus/qrels(Issue A)와 old/new artifact·upgrade SQL(Issue B)은 계속 미측정(blocked)이다.

- 2026-09-14 17:00 KST에 Phase 82 timeout wiring slice를 반영했다. `tools/eval_quality.py`가 `--query-timeout-seconds`(Make `QUALITY_QUERY_TIMEOUT_SECONDS`)를 받아 setup SQL과 평가 query SQL psql subprocess에 client-side timeout을 전달하고, 옵션 생략 시 `subprocess.run`에 `timeout` 인자를 넘기지 않아 기존 동작을 보존한다. `0`/음수/`NaN`/`Infinity`는 SQL 실행 전에 exit code 2로 거부하며, run budget이 있으면 각 subprocess 직전에 남은 deadline과 명시 timeout 중 더 짧은 값을 계산하고 deadline 소진 시 runner를 호출하지 않는다. timeout은 성공/0점으로 처리하지 않고 `status=quality_eval_timeout`/`success=false`/`timeout.stage`/`timeout.reason`/`timeout.limit_seconds`/partial result를 남기고 exit code 4로 종료하며, query timeout은 완료된 results만 partial로 보존하고 누락 query를 채우거나 자동 retry하지 않는다. 이 timeout은 client subprocess 경계일 뿐 PostgreSQL 서버 측 query cancellation을 보장하지 않으며, `budget_exhausted`(exit 3)/partial artifact 계약은 보존한다. 검증은 `python3 -m unittest tests.test_eval_quality tests.test_execution_budget`(54 tests), `make test-warc`, `make check-docs`, `make verify`, `git diff --check`로 확인했다. real labeled corpus/qrels baseline과 old/new artifact·upgrade SQL 증거는 여전히 미측정(blocked)이다.

- 2026-09-14 16:00 KST에 Phase 82 correction을 반영했다. `tools/eval_quality.py` query loop가 매 query마다 `charge_iteration()` 후 `charge_query()`를 호출해 query limit 선도달 시 blocked N+1 시도가 `used_iterations`를 부풀리던 bug(예: `max_iterations=5`, `max_queries=2`, 3 queries에서 results=2인데 used_iterations=3)를 수정했다. `tools/execution_budget.py`에 `charge_query_unit()`을 추가해 두 한도를 먼저 검사하고 통과 시에만 원자적으로 함께 차감하며, 어느 한도가 먼저 걸려도 어떤 카운터도 소비하지 않는다. query-limit-first/iteration-limit-first/simultaneous/잔여 카운터 의미는 `tests/test_execution_budget.py`와 `tests/test_eval_quality.py`의 결정론적 회귀 테스트로 고정했고, 기존 retry/child/deadline semantics와 `budget_exhausted` machine-readable status/partial result는 보존한다. 실제 labeled corpus baseline과 versioned upgrade 성공 증거 상태는 변경하지 않았다.

- 2026-09-14 15:00 KST에 Phase 82 run-scoped execution budget gate를 추가했다. `tools/execution_budget.py`가 missing/wrong-type/zero/negative `max_iterations`/`max_queries`/`deadline_seconds`를 work 전에 거부하고, 하나의 budget 인스턴스를 iteration/query/retry/child work에 공유해 N+1 callback 없이 exact-N으로 제한하며, monotonic deadline과 `budget_exhausted` machine-readable non-success status를 남긴다. `make eval-quality-budget`으로 `eval_quality` query loop에 연결했고, 세 값이 모두 없으면 거부되며 소진 시 partial `results`와 `status=budget_exhausted`를 남기고 exit code 3으로 종료한다. 이는 하네스 경계 증거이며 real labeled corpus와 versioned upgrade 성공 증거가 아니다.

- 2026-09-14 13:00 KST에 Phase 81 release upgrade preflight enforcement를 추가했다. `RELEASE_SMOKE_TARGET=smoke-release-upgrade`일 때 `report-release-smoke-matrix`/`finalize-release-run`이 major별 `doctor_release_upgrade_inputs`를 smoke 이전에 강제하고, blocked major를 `release_smoke_matrix.json` result row에 `preflight`/`smoke_skipped_reason`으로 보존하며 non-zero로 종료한다. 현재 저장소 상태에서 smoke attempt `0/5`, `ALTER EXTENSION UPDATE` 미실행, `blocked_majors=[13, 14, 15, 16, 17]`을 확인했고, ready fixture dispatch와 mixed-major block, non-upgrade 경로 보존은 회귀 테스트로 검증했다. versioned upgrade 성공 증거와 Issue A real labeled corpus baseline은 계속 blocked다.
- 2026-09-14 11:00 KST에 Phase 80 release upgrade input preflight contract를 추가했다. `make doctor-release-upgrade-inputs`가 현재 `0.1.0` artifact만 존재하고 `dist/`에 old artifact나 `pgwarc_lance--OLD--NEW.sql` upgrade script가 없음을 `valid=false`/`status=blocked` report로 남기고 non-zero로 종료한다. 이 회차에서는 legitimate old/new artifact pair가 없어 `make smoke-release-upgrade`를 실행하지 않았고 versioned upgrade는 미측정(blocked)으로 유지한다. Issue A의 real labeled WARC corpus도 계속 blocked다.
- 2026-09-14 09:00 KST에 Phase 79 real corpus input manifest doctor를 추가했다. `make doctor-real-corpus-input`이 corpus source/license/checksum, query/qrels 버전, label 독립성, 평가 commit/PostgreSQL 버전/검색 설정을 검증하고 synthetic/public/self-labeled 입력을 거부한다. 저장소와 접근 가능한 로컬 개발 환경에 독립 labeled WARC corpus가 확인되지 않아 real baseline 점수는 미측정(blocked)이며, 필요한 corpus·qrels 요구사항만 문서화했다.
- 2026-09-14 07:00 KST에 Phase 78 quality provenance staging을 추가했다. `finalize-quality-run`이 `QUALITY_PROVENANCE_JSON`을 run directory의 `warc_provenance.json`으로 보존하고 report/doctor가 staged copy를 사용하도록 연결했으며, 실제 corpus를 임의로 만들지 않은 상태에서 Make expansion과 전체 검증을 확인했다. real labeled corpus와 versioned upgrade는 여전히 미측정이다.
- 2026-09-14 05:00 KST에 Phase 77 WARC provenance doctor를 추가했다. public quality finalization의 required `warc_provenance.json`을 schema/coverage/hash 관점에서 독립 검수하도록 했고, 실제 corpus를 임의로 만들지 않은 상태에서 source hash drift 회귀 테스트와 전체 검증을 완료했다. real labeled corpus와 versioned upgrade는 여전히 미측정이다.
- 2026-09-14 03:06 KST에 comparison report schema v2 입력 provenance와 run doctor의 현재 파일 hash drift 검사를 추가하고 `/path/to/pglance-benchmark-comparison-run-20260914-0306`에서 6개 comparison, 0 regressions, run doctor errors/warnings `0/0`을 확인했다. real corpus와 versioned upgrade는 여전히 미측정이다.
- 2026-09-14 01:12 KST에 `make finalize-benchmark-comparison-run`과 comparison report doctor 연결을 구현했다. comparator 결과의 schema/count/threshold 계산을 run doctor가 재검증하고 baseline/candidate 경로, threshold를 run metadata에 보존하도록 했으며, real corpus·versioned upgrade는 여전히 미측정이다.
- 2026-09-13 23:06 KST에 `tools/compare_benchmark_runs.py`와 `make compare-benchmark-runs`를 추가했다. finalized WARC BM25 mode artifact 두 개의 benchmark 설정, rows/mode 집합과 `execute_s` 측정값을 먼저 검증하고, 명시적 ratio threshold 초과를 non-zero로 판정하는 JSON/Markdown 회귀 하네스다. real corpus와 versioned upgrade는 여전히 미측정이다.
- 2026-09-13 21:02 KST에 `WARC_COMPARE_ROWS=1000,2000`, `WARC_WORDS=80`, `BENCH_DIM=64`, `BENCH_BATCH=500`, `WARC_COMPARE_EXECUTE=1`로 larger `make finalize-warc-bm25-modes-run`을 실행했다. `/path/to/pglance-warc-bm25-modes-run-20260913-2101`에 function/bulk/copy full-import 결과와 metadata/manifest/doctor를 보존했고 benchmark run doctor errors/warnings `0/0`을 확인했다. 2,000행 실행 시간은 function `16.263s`, bulk `3.721s`, copy `2.224s`였지만 synthetic 입력이라 기본 모드 변경이나 성능 gate 승격은 보류했다.
- 2026-09-13 19:01 KST에 `WARC_COMPARE_ROWS=100,500`, `WARC_WORDS=80`, `BENCH_DIM=64`, `BENCH_BATCH=100`, `WARC_COMPARE_EXECUTE=1`로 `make finalize-warc-bm25-modes-run`을 실행했다. `/path/to/pglance-warc-bm25-modes-run-20260913-1901`에 function/bulk/copy 결과와 metadata/manifest/doctor를 보존했고 benchmark run doctor errors/warnings `0/0`을 확인했다. 500행 실행 시간은 function `2.375s`, bulk `0.904s`, copy `0.648s`였지만 synthetic 소규모 결과라 기본 모드 변경 근거로 승격하지 않았다.
- 2026-09-13 17:04 KST에 `BENCH_ROWS=100,500`, `BENCH_DIM=64`, `BENCH_BATCH=100`, `WARC_ROWS=500` 설정으로 `make finalize-benchmark-run`을 실행했다. `/path/to/pglance-benchmark-run-20260913-1701`에 BM25/Lance/WARC JSON·Markdown, clean commit metadata, SHA-256 manifest를 보존했고 benchmark run doctor errors/warnings `0/0`을 확인했다. synthetic WARC SQL은 `execute=false`였으므로 실제 corpus 품질과 실행 성능 gate는 여전히 미측정이다.
- 2026-09-13 11:02 KST에 Phase 72 커밋 후 live PostgreSQL 16에서 `make test`, `make test-warc-db`, `make test-regress`를 재실행해 모두 통과했다. pg_regress는 `setup`, `functions`, `search` 3/3을 통과했으며, 실제 labeled WARC corpus 품질 점수는 여전히 미측정이다.
- 2026-09-13에 quality fixture document와 relevance label `doc_id`를 양의 PostgreSQL `bigint` 범위로 제한했다. 0, 음수, bool, 숫자 문자열과 bigint 범위 초과값의 묵시적 변환을 차단했으며, 실제 labeled corpus 자체의 점수 측정은 아직 미측정이다.
- 2026-09-13에 standalone `make-quality-fixture` 경로도 strict identity 검증을 사용하도록 보강했다. query label doctor를 우회해도 duplicate query name fixture가 생성되지 않으며, 실제 labeled corpus 자체의 점수 측정은 아직 미측정이다.
- 2026-09-13에 direct external quality fixture가 duplicate `doc_id`와 query `name`을 SQL/Lance setup 전에 거부하도록 보강했다. 동일 식별자 충돌로 문서가 덮이거나 query 결과가 혼합되는 경로를 차단했으며, real labeled corpus 자체의 점수 측정은 아직 미측정이다.
- 2026-09-13에 external quality fixture query의 duplicate `expected_doc_ids`를 DB/Lance setup 전에 거부하도록 보강했다. 중복 label이 expected label count와 relevance metric을 부풀리지 않으며, real labeled corpus 자체의 점수 측정은 아직 미측정이다.
- 2026-09-13에 external quality fixture의 doc/query vector가 `NaN`, `Infinity`, float 변환 overflow를 SQL/Lance setup 전에 거부하도록 `eval_quality.py`와 회귀 테스트를 보강했다. 이는 실제 WARC quality baseline을 만들기 위한 입력 무결성 blocker를 닫은 것이며, real labeled corpus 자체의 점수 측정은 아직 미측정이다.
- 2026-09-13에 `PG_MATRIX="13 14 15 16 17"` packaging run을 실행해 PG13-17 artifact matrix `5/5 valid`, official `postgres:<major>` install smoke `5/5 pass`, packaging run doctor errors/warnings `0/0`을 기록했다. PG15는 root disk 99% 상태의 일시적 linker bus error 후 비활성 matrix image와 Docker build cache를 정리하고 단독 재시도해 통과했다. versioned upgrade smoke는 old artifact/version bump가 없어 미측정이다.
- 2026-09-12에 `PG_MATRIX=16` packaging run을 실행해 PG16 artifact matrix `1/1 valid`, official `postgres:16` install smoke `1/1 pass`, packaging run doctor errors/warnings `0/0`을 기록했다. PG13-17 matrix와 versioned upgrade smoke는 old artifact/version bump가 없어 미측정이다.
- 2026-09-12에 개발 컨테이너를 새로 빌드해 live PostgreSQL 16에서 `make test`, `make test-warc-db`, `make test-regress`를 실행했고 모두 통과한 결과를 [QUALITY.md](QUALITY.md)에 기록했다. 실제 라벨 코퍼스 품질 점수는 이 결과와 별도의 미측정 항목으로 유지한다.
- `QUALITY_BASELINE_REQUIRE_REAL_CORPUS=1`과 `--require-real-corpus`를 추가해 schema v2 WARC provenance/source coverage, 20-50개 query, 양수 hit-rate/MRR threshold 없이는 real baseline doctor가 통과하지 않도록 했다. 기존 synthetic/public smoke 경로는 호환성을 유지한다.
- Phase 1 Import Contract Hardening을 완료했다.
- Phase 2 Search Result Surface를 완료했다.
- Phase 3 Scale And Performance를 완료했다.
- Phase 4 Packaging And Reproducibility를 완료했다.
- Phase 5 Retrieval Quality를 완료했다.
- DB 없는 end-to-end importer fixture test를 추가해 작은 `.warc`에서 metadata/BM25/Lance SQL이 생성되는 경로를 고정했다.
- `make test-warc-db`를 추가해 importer-generated SQL을 라이브 DB에서 실행하고 metadata/BM25/Lance/hybrid_warc_search 결과를 검증한다.
- Lance append 중복 정책, `--summary-json`, generated SQL batch transaction comment, embedding command failure tests를 추가했다.
- `hybrid_warc_search()`를 추가해 기존 ranking API를 깨지 않고 WARC metadata가 붙은 검색 결과를 반환한다.
- pg_regress와 smoke SQL에서 `hybrid_warc_search()` 결과를 검증한다.
- `bench-bm25`, benchmark JSON/Markdown 출력 옵션, WARC execute split, 로컬 benchmark baseline을 추가했다.
- Rust toolchain pinning, `make verify`, release artifact 추출, local install/upgrade 문서를 추가했다.
- `make eval-quality`, synthetic relevance fixture, OpenAI-compatible embedding command 예제, tokenizer/BM25/RRF 품질 결정을 추가했다.
- Import failure recovery 정책을 README에 추가해 chunk retry, Lance overwrite, append duplicate 위험을 문서화했다.
- 공개 WARC smoke 후보로 `webrecorder/pywb`의 3.4KB `example.warc.gz`를 기록했다.
- Search filter helper는 보류하고 `hybrid_warc_search()` 결과에 SQL `WHERE`를 적용하는 계약을 유지하기로 결정했다.
- Importer-side `--bm25-mode bulk`를 추가해 대량 import에서 `bm25_doc`/`bm25_term` batch insert 경로를 사용할 수 있게 했다.
- `make smoke-public-warc`를 추가해 public pywb WARC sample의 parse/import/BM25/Lance smoke를 선택적으로 실행할 수 있게 했다.
- [BLUEPRINT.md](BLUEPRINT.md)와 `make check-docs`를 추가해 미래 작업 선택 기준과 문서/하네스 drift 검증을 고정했다.
- `make bench-warc-bm25-modes`와 100/500 row 기준선을 추가해 BM25 function/bulk import path를 같은 입력에서 비교할 수 있게 했다.
- `--bm25-mode copy`를 추가해 psql `COPY FROM stdin` 기반 BM25 batch 적재 경로를 사용할 수 있게 했다.
- `--fixture-json`/`QUALITY_FIXTURE`를 추가해 real corpus quality labels를 `make eval-quality`에 넣을 수 있게 했다.
- `make make-quality-fixture`를 추가해 importer records JSONL, embedding JSONL, query label JSON을 external quality fixture로 만들 수 있게 했다.
- 1000/2000 row BM25 mode scale follow-up을 실행해 full import에서는 COPY가 가장 빠르고, BM25-only에서는 bulk/COPY 비교가 여전히 필요하다는 결정을 [BENCHMARKS.md](BENCHMARKS.md)에 기록했다.
- `make public-warc-quality-fixture`와 `make eval-public-warc-quality`를 추가해 documented pywb public WARC sample을 labeled quality smoke fixture로 평가할 수 있게 했다.
- `PG_MAJOR` 기반 Docker build와 `make smoke-pg-version`/`make pg-version-matrix`를 추가해 PG13-17 fresh install smoke를 선택적으로 돌릴 수 있게 했다.
- `make check-release-artifacts`를 추가해 release artifact 추출 직후 `.so/.control/.sql` 구조와 버전 drift를 DB 없이 검증할 수 있게 했다.
- `make smoke-release-artifacts`를 추가해 추출된 artifact를 build image 밖의 official Postgres 컨테이너에 설치하고 `CREATE EXTENSION` smoke를 실행할 수 있게 했다.
- `make release-artifacts-matrix`와 `make smoke-release-artifacts-matrix`를 추가해 `PG_MATRIX` 전체 artifact 추출/검사와 runtime install smoke를 선택적으로 반복할 수 있게 했다.
- `make report-release-artifacts`를 추가해 `PG_MATRIX` artifact matrix의 검증 상태와 checksum manifest를 JSON/Markdown으로 남길 수 있게 했다.
- `make smoke-release-upgrade`를 추가해 향후 version bump 시 old/new artifact와 upgrade SQL로 `ALTER EXTENSION UPDATE` smoke를 실행할 수 있게 했다.
- `make smoke-release-upgrade-matrix`를 추가해 version bump 뒤 PG13-17 upgrade smoke를 같은 계약으로 반복할 수 있게 했다.
- `make doctor-import-summary`를 추가해 importer `--summary-json` artifact의 schema와 Lance append retry risk를 DB 없이 검사할 수 있게 했다.
- `make report-run-directory`를 추가해 BENCH_OUT_DIR/RUN_DIR 산출물 manifest를 파일 크기와 SHA-256으로 남길 수 있게 했다.
- `make write-run-metadata`를 추가해 run directory에 git revision, dirty 상태, 실행 command, 안전한 env metadata를 남길 수 있게 했다.
- `make doctor-run-directory`를 추가해 run directory profile별 필수 산출물과 manifest 포함 여부를 DB 없이 검사할 수 있게 했다.
- `make finalize-run-directory`를 추가해 run directory 마감 산출물을 metadata, manifest, doctor, final manifest 순서로 생성할 수 있게 했다.
- `make report-release-smoke-matrix`를 추가해 PG_MATRIX release smoke 실행 결과를 JSON/Markdown으로 남길 수 있게 했다.
- `make report-quality-fixture`를 추가해 external quality fixture의 schema와 label coverage를 DB 없이 JSON/Markdown으로 남길 수 있게 했다.
- `make finalize-quality-run`을 추가해 quality fixture report, eval result, run metadata/manifest/doctor를 한 번에 생성할 수 있게 했다.
- `make finalize-public-warc-quality-run`을 추가해 public WARC quality fixture 생성과 quality run finalization을 한 번에 실행할 수 있게 했다.
- `make doctor-quality-baseline`을 추가하고 `finalize-quality-run`에 포함해 생성된 quality fixture report와 eval metrics가 baseline threshold를 만족하는지 DB 없이 검사할 수 있게 했다.
- `make finalize-release-run`을 추가해 release artifact report, release smoke matrix report, packaging run metadata/manifest/doctor를 한 번에 생성할 수 있게 했다.
- `make finalize-import-run`을 추가해 import summary, import doctor, run metadata/manifest/doctor를 한 번에 생성할 수 있게 했다.
- `make finalize-benchmark-run`을 추가해 BM25/Lance/WARC benchmark, run metadata/manifest/doctor를 한 번에 생성할 수 있게 했다.
- `make finalize-benchmark-run`의 core benchmark 필수 artifact 요구를 target 전용 `BENCH_RUN_DOCTOR_REQUIRE`로 옮겨 targeted benchmark doctor 호환성을 유지했다.
- `doctor-run-directory`가 quality/import/packaging report artifact의 성공 status field까지 검사하도록 강화했다.
- `make finalize-warc-bm25-modes-run`을 추가해 WARC BM25 function/bulk/copy 비교 benchmark도 run metadata/manifest/doctor와 함께 마감할 수 있게 했다.
- `doctor-run-directory`가 `RUN_DOCTOR_REQUIRE`로 요구된 알려진 benchmark JSON의 schema와 non-empty result를 검사하도록 강화했다.
- `doctor-run-directory`가 `run_manifest.json`에 기록된 file entry 존재, size, SHA-256 drift를 검사하도록 강화했다.
- `finalize-quality-run`과 `finalize-import-run`이 각각 `quality_baseline_doctor.json`, `import_doctor.json`을 target 전용 필수 run doctor artifact로 요구하도록 강화했다.
- `make check-docs`가 README/ROADMAP의 현재 완료 phase count drift를 잡도록 강화했다.
- `make doctor-quality-labels`를 추가해 query label JSON을 fixture 생성 전에 DB/embedding 없이 검사할 수 있게 했다.
- `make finalize-quality-labels-run`을 추가해 label doctor report와 run metadata/manifest/doctor를 한 run directory로 마감할 수 있게 했다.
- `doctor-run-directory`가 required `quality_labels_doctor.json`의 `valid=true` status를 검사하도록 강화했다.
- `make finalize-quality-fixture-build-run`을 추가해 records/vector/query labels에서 DB 없이 quality fixture와 fixture report를 만들고 run directory로 마감할 수 있게 했다.
- `doctor-run-directory`가 required `quality.fixture.json`의 fixture schema와 duplicate doc/query를 검사하도록 강화했다.
- `doctor-run-directory`가 required `quality.json`의 eval metric schema와 query result rows를 검사하도록 강화했다.
- `doctor-run-directory`가 required `quality_fixture_report.json`의 report count/query row schema를 검사하도록 강화했다.
- `doctor-run-directory`가 required `quality_baseline_doctor.json`의 threshold/count/result schema를 검사하도록 강화했다.
- `doctor-run-directory`가 required/profile `import.summary.json`의 importer summary schema와 Lance/execute consistency를 검사하도록 강화했다.
- `doctor-run-directory`가 required `import_doctor.json`의 report counts, per-summary rows, embedded valid summary schema를 검사하도록 강화했다.
- `doctor-run-directory`가 required/profile `release_artifacts_matrix.json`의 artifact row/file digest schema를 검사하도록 강화했다.
- `doctor-run-directory`가 required/profile `release_smoke_matrix.json`의 result row and pass/exit-code schema를 검사하도록 강화했다.
- `doctor-run-directory`가 `run_metadata.json`의 generated metadata, environment, git metadata, warning list schema를 검사하도록 강화했다.
- Phase 53에서 `--query-spec-json` 다중 query public WARC quality fixture 생성, 중복/무일치 label 검증, Make target 연결을 추가했다.
- Phase 54에서 단일 WARC source/query spec의 URL·경로·bytes·SHA-256·시각·fixture 요약을 provenance manifest로 남기고 public quality finalization에 연결했다.
- Phase 55에서 여러 WARC path/URL을 합치는 source spec과 schema v2 `sources` corpus manifest, duplicate `doc_id` guard, Make finalization 연결을 추가했다.
- Phase 56에서 schema v2 manifest에 source별 raw/imported/skipped record 수와 labeled/unlabeled coverage를 추가했다.
- Phase 57에서 quality fixture report가 source coverage 합계와 최소 doc/skipped ratio threshold를 검증하도록 연결했다.
- Phase 58에서 run doctor가 schema v2 source coverage report의 구조, 합계, ratio, threshold 계약을 다시 검사하도록 연결했다.
- Phase 59에서 quality baseline doctor artifact가 source coverage 문맥을 보존하고 run doctor가 baseline에서도 재검증하도록 연결했다.
- Phase 60에서 public WARC source spec이 path 또는 URL 정확히 하나만 받도록 validation contract를 고정했다.
- Phase 61에서 WARC record의 필수 non-negative `Content-Length` contract를 parser와 회귀 테스트에 고정했다.
- Phase 62에서 HTTP chunked payload의 size, length, terminator, trailer, terminating zero-size chunk contract를 parser와 회귀 테스트에 고정했다.
- Phase 63에서 HTTP gzip `Content-Encoding` 압축 해제 실패를 importer 입력 오류로 처리하는 contract를 parser와 회귀 테스트에 고정했다.
- Phase 64에서 importer `--limit`/`--min-text-chars` selection limit contract와 `--limit 0` empty import 동작을 parser/CLI와 회귀 테스트에 고정했다.
- Phase 65에서 importer가 동일 stable `doc_id`를 SQL/Lance 적재 전에 거부하는 document identity contract를 parser와 회귀 테스트에 고정했다.
- Phase 66에서 HTTP `Content-Type` charset parameter의 optional whitespace parsing과 비ASCII decode 동작을 importer와 회귀 테스트에 고정했다.
- Phase 67에서 WARC header의 duplicate `Content-Length` overwrite를 payload read 전에 거부하는 contract를 parser와 회귀 테스트에 고정했다.
- Phase 68에서 external embedding vector의 finite numeric/range contract를 parser와 회귀 테스트에 고정했다.
- Phase 69에서 required embedding `doc_id`의 duplicate vector overwrite를 거부하는 document identity contract를 parser와 회귀 테스트에 고정했다.

## Phase 83: Quality Finalization Input Preflight Enforcement

목표: real-corpus quality finalization이 read-only 입력 준비성 검사를 우회해 완료 산출물을 만들지 않도록, 기존 preflight contract를 finalization entry point에 연결한다.

- [x] `QUALITY_INPUT_PREFLIGHT_REQUIRED=1`이면 `finalize-quality-run`이 기존 `make preflight-quality-inputs`를 `stage-quality-provenance`, fixture report, `eval-quality`, baseline doctor, final run-directory 단계보다 먼저 실행하도록 연결
- [x] `QUALITY_BASELINE_REQUIRE_REAL_CORPUS=1`이 preflight gate를 자동 활성화하고, corpus/query/qrels 입력 누락·readability/reference 오류·rejected manifest/validator error를 non-zero로 전파하도록 고정
- [x] 기존 `--manifest-json`/`QUALITY_INPUT_PREFLIGHT_MANIFEST_JSON` 연결과 optional provenance/records 전달을 재사용하고, synthetic/public finalization 경로의 기본 동작은 유지
- [x] preflight 실패 시 diagnostic `quality_input_preflight.json/md`만 남고 quality fixture/eval/baseline/final manifest completion artifact가 생성되지 않는 Make 회귀 테스트와 dry-run 순서 검사를 추가
- [x] README, QUALITY, BLUEPRINT에 gate 활성화 조건·실패 경계·preflight-only 의미·8시간 개발 회차·의사결정 재사용·에스컬레이션 게이트와 자동화 worker 구성를 문서화; 실제 labeled corpus/qrels와 Issue B old/new artifact·upgrade SQL은 계속 blocked

## Phase 84: Timeout-After-Timeout State Judgement Contract

목표: timeout 이후 backend 상태 판정을 런타임 없이 판정 가능한 pure deterministic 계약으로 고정하고, timeout과 확인된 backend 종료, 취소 수락과 실제 종료, 관측 실패와 cleanup 완료, budget 소진과 cleanup 성공을 분리한다(개발 의사결정 2026-09-15).

- [x] `tools/quality_timeout_state_decision.py`의 `decide_quality_timeout_state(events, label=...) -> dict`를 추가해 합성 event 목록을 상태(`attempt_recorded`, `timeout_recorded_backend_unobserved`, `cancellation_accepted_not_confirmed`, `statement_completed`, `termination_confirmed_cleanup_unconfirmed`, `cleanup_confirmed`, `budget_exhausted_without_cleanup`, `indeterminate_no_claims`)로 fold한다
- [x] 기존 `quality_attempt_identity.v1` envelope 검증(`doctor_quality_attempt_identity.validate_identity`)과 `eval_quality.TIMEOUT_OBSERVATION_FIELDS` 네 필드 enum을 재사용하고, confirmation-grade 종료/cleanup 증거는 기록된 attempt의 `session_identity`와 정확히 일치할 때만 인정한다
- [x] identity 불일치는 전체 판정을 `indeterminate_no_claims`로 만들고, 누락·미캡처 identity 증거는 ignore로 남기며 confirm하지 않는다 (누락/불일치 identity가 false termination/cleanup claim을 만들지 않는다)
- [x] stale/out-of-order(`event_seq`)와 duplicate(`event_id`) 이벤트를 ignore로 판정에서 제외하고, `observation_failed`와 `budget_exhausted`는 절대 cleanup claim을 만들지 않는다
- [x] opt-in CLI(`python3 tools/quality_timeout_state_decision.py --input-json ...`)만 제공하고 `eval-quality`/`doctor_run_directory`/run finalization/`make verify`는 호출하지 않는다; runtime DB connection, pg_cancel_backend/pg_terminate_backend, 별도 권한 connection, 실제 취소는 구현하지 않는다
- [x] `tests/test_quality_timeout_state_decision.py`가 timeout-only, timeout 전후 자연 완료, 취소 수락 후 backend 계속 실행, identity 일치 종료+명시 cleanup 증거, identity mismatch/누락/미캡처, stale/duplicate, 관측 실패, budget 소진, malformed observation/envelope, deterministic 재계산을 결정론적인 손작성 합성 입력으로 검증
- [x] README, QUALITY, BLUEPRINT, ROADMAP에 계약과 synthetic-only 경계, next runtime integration requirements(identity capture, permission을 가진 observer connection, target/timeout race, budget, cleanup 판정 DB 증거)를 문서화; Issue A real labeled corpus/qrels와 Issue B old/new artifact·upgrade SQL은 계속 blocked

## Phase 85: Synthetic Opt-in Eligibility and Plan Contract

목표: backend 관측/취소 런타임이 아직 없는 상태에서 관측 적격성과 취소 적격성을 분리 판정하는 default-deny opt-in 계획 계약을 pure deterministic으로 고정한다(개발 의사결정 2026-09-15). 합성 적격성은 일관성 검사일 뿐 실행 승인이 아니며 backend 종료·정리의 증거가 아니다.

- [x] `tools/quality_synthetic_optin_plan.py`의 `decide_synthetic_optin_plan(request, label=...) -> dict`를 추가해 합성 opt-in request를 `invalid_request`/`rejected_default_deny`/`observation_eligible_only`/`observation_and_cancellation_eligible`로 fold하고, opt-in 누락과 필수 조건 누락·미지·모순은 계획을 거부한다
- [x] 기존 `quality_attempt_identity.v1` envelope 검증(`doctor_quality_attempt_identity.validate_identity`), tz-aware ISO-8601 판정, 양의 정수 예산 검사를 재사용해 identity/state 의미를 중복·약화하지 않는다; same-session identity capture(`capture_state=captured`)를 모든 적격성의 전제로 요구한다
- [x] 관측 적격성이 취소 적격성을 자동 부여하지 않는다; 취소는 별도 요청과 자체 permission·유한 `max_cancellations` 예산·만료 시각, 실행 시점 재검증(`requires_execution_revalidation`) 명시, 단일 기록 attempt 대상(`target_scope=single_recorded_attempt`), `identity_confirmation`이 기록 `session_identity`와 정확히 일치, 관측 grant 만료 없음을 모두 요구한다
- [x] 계획 산출물은 항상 `execution_authority=none`, `not_claimed` claims(backend termination/cancellation completed/rollback completed/transaction cleanup)와 runtime 통합 요구사항 목록(identity capture, permission을 가진 observer connection, 실행 시점 재검증, target/timeout race, 유한 예산, rollback/transaction cleanup 실제 DB 증거)을 포함한다
- [x] opt-in CLI(`python3 tools/quality_synthetic_optin_plan.py --input-json ...`)만 제공하고 `eval-quality`/`doctor_run_directory`/run finalization/`make verify`는 호출하지 않는다; runtime DB connection, 관측, 취소 실행, 권한 부여, 공개 API/CLI/config/schema 변경은 없다
- [x] `tests/test_quality_synthetic_optin_plan.py`가 opt-in 누락/거짓/모순, 조건 누락·미지, 관측-취소 분리, identity 불일치·재사용 대상·만료, 실행 시점 재검증 명시, 예산 비유한/만료, 기존 timeout state 계약과의 조합(자연 완료 vs timeout race, 취소 응답 vs 종료 확인, budget 소진), no false termination/cleanup claims, `eval-quality`/`make verify` 미연결을 결정론적 손작성 입력으로 검증
- [x] plan output schema uniformity contract: `decide_synthetic_optin_plan`의 non-object request 경로를 포함한 모든 판정 경계가 동일한 top-level field 집합과 `error_count`를 남기도록 고정하고, non-object 입력은 `invalid_request`/`error_count=1` plan으로 거부되는 것을 회귀 테스트로 검증
- [x] attempt clock consistency contract: 캡처된 `session_identity.backend_start`가 request `decision_time` 이후인 모순 request를 관측·취소 조건과 무관하게 `invalid_request`로 거부하고, 같은 순간(임의 timezone offset 포함)은 수용되는 것을 회귀 테스트로 고정
- [x] internal batch consumer: `tools/quality_synthetic_optin_batch.py`의 `consume_synthetic_optin_batch(requests)`가 정렬된 합성 request 목록을 기존 Phase 85 evaluator에 요소마다 한 번씩 통과시키고 개별 plan을 변경 없이 입력 순서(중복 포함)로 반환하며, explicit `eligible`만 독립적으로 세는 `observation_eligible_total`/`cancellation_eligible_total`을 제공한다. 빈 입력 빈 결과/0 totals, 직접 평가 동등성(non-object·`error_count` 포함), 혼합 입력 결과 보존, repaired clock 경계(range 포함, timezone 등가) 재커버, 반복 평가 동일성·입력 비변이·예외 전파를 `tests/test_quality_synthetic_optin_batch.py`로 결정론적으로 검증한다. 입력 경계는 순서 보존 iterable만 수용하며 mapping, unordered iterable(`set`/`frozenset`), scalar text/byte sequence(`str`/`bytes`/`bytearray`/`memoryview`), non-iterable scalar(`None`/`int`/`float`/`bool` 등)는 evaluator 실행 전 동일한 contract `TypeError` 메시지로 거부되는 것도 회귀 테스트로 고정했다. 별도 CLI·공개 인터페이스·런타임 경로 연결은 없으며 합성 전용 경계와 실행 승인 아님을 QUALITY.md에 문서화했다
- [x] README, QUALITY, BLUEPRINT, ROADMAP에 Phase 85 계약과 synthetic-only 경계, 다음 runtime 통합 요구사항을 문서화; Issue A real labeled corpus/qrels와 Issue B old/new artifact·upgrade SQL은 계속 blocked

## 다음 구현 단위

현재 로드맵의 Phase 1-85와 후속 문서화/벤치마크/품질/패키징/운영 doctor/run manifest/metadata/finalization 하네스 단위는 완료했다. 2026-09-13 17:04 KST core benchmark finalization, 19:01 KST 100/500-row targeted comparison, 21:02 KST 1000/2000-row larger comparison을 각각 clean metadata와 benchmark doctor `0/0`으로 보존했고, 23:06 KST에는 baseline/candidate `execute_s` 회귀 비교 하네스를 추가했으며 2026-09-14 01시에는 comparison report finalization과 독립 doctor 검사, 03시에는 input provenance와 drift 검사, 05시에는 WARC provenance source drift doctor와 public finalization required artifact 연결, 07시에는 generic quality finalization의 provenance staging, 09시에는 real corpus input manifest doctor, 11시에는 release upgrade input preflight doctor, 13시에는 upgrade finalization/matrix 경로의 preflight enforcement, 15시에는 Issue C의 첫 slice로 run-scoped execution budget gate를, 16시에는 budget query-loop charge ordering correction을, 17시에는 query timeout wiring과 `quality_eval_timeout` artifact 계약을, 19시에는 그 non-success artifact를 성공 baseline으로 뭉개지 않는 doctor 소비 계약과 producer 생성 artifact 기반 회귀 테스트를, 21시에는 query 전용 opt-in 서버 `statement_timeout`과 안정 진단 기반 분류·timeout artifact 보존을, 23시에는 timeout artifact의 관측 한계(`timeout.timeout_observation`으로 statement outcome과 unobserved backend/cancellation/transaction 상태 분리, client stderr 비승격, doctor enum 검증과 legacy 미기록 렌더링)를 추가했지만 증거는 모두 synthetic 및 harness 범위다. 다음 청사진은 backend 상태 관측/active cancellation을 session identity, 별도 권한 connection, target/timeout race, budget, cleanup 판정 기준과 함께 별도 계약으로 다룬다. Direction Review Gate에 따라 Issue A의 real labeled-corpus quality baseline을 먼저 닫고, Issue B의 real/stable performance regression과 남은 versioned release/upgrade evidence를 닫으며, 분석 하네스의 기술적 read-only·schema completion gate·8시간 loop 운영 기준을 강화한다(Issue C). PG13-17 fresh-install packaging evidence는 2026-09-13에 기록했지만 version bump 전이라 upgrade smoke는 미측정이다. 이 증거들이 닫히기 전에는 보안·데이터 손상 blocker를 제외한 새 parser edge-case phase를 추가하지 않는다. 상세 선택 기준은 [BLUEPRINT.md](BLUEPRINT.md)에 기록한다.

2026-09-15 23:00 KST 회차에서 개발 의사결정(2026-09-15)에 따라 Phase 85의 합성 opt-in 적격성/계획 계약을 추가했다. `tools/quality_synthetic_optin_plan.py`의 `decide_synthetic_optin_plan(request)`는 합성 opt-in request를 관측 적격성과 취소 적격성으로 분리 판정하며 default deny로 동작한다. opt-in 누락, 필수 조건 누락/미지/모순은 계획을 거부하고, 관측 적격성이 취소 적격성을 자동 부여하지 않는다. 취소 계획은 identity 불일치, 재사용/모호한 target, 관측 만료를 거부하며 실행 시점 재검증을 명시 요구한다. 계획 산출물은 `execution_authority=none`과 `not_claimed` claims만 가지며 실제 backend 종료·rollback·transaction cleanup을 주장하지 않는다. `tests/test_quality_synthetic_optin_plan.py`와 기존 timeout state 계약 조합 테스트가 이를 검증한다. Issue A real labeled corpus/qrels와 Issue B old/new artifact·upgrade SQL은 계속 blocked다.

2026-09-16 23:00 KST 회차에서 개발 의사결정(2026-09-16)에 따라 Phase 85 evaluator의 internal pure deterministic batch consumer를 추가했다. `tools/quality_synthetic_optin_batch.py`의 `consume_synthetic_optin_batch(requests)`는 합성 request 목록을 요소마다 한 번씩 기존 evaluator에 통과시키고 개별 결과를 입력 순서(중복 포함)로 변경 없이 반환하며, 관측·취소 eligible totals를 독립적으로 센다. DB semantics나 A/B 증거 없이 기존 결정론적 동작을 합성 회귀 시나리오에서 그대로 재사용할 수 있게 했고, runtime 관측/취소, 공개 API/CLI/config/schema, Phase 84/85 정책은 변경하지 않는다. `tests/test_quality_synthetic_optin_batch.py`가 수용 기준을 검증한다. Issue A와 Issue B는 계속 blocked다.

## 검증 명령

```bash
make check-docs
make test-warc
make test-unit
make test-regress
make test
make test-all
BENCH_OUT_DIR=benchmarks/local make finalize-benchmark-run
BENCH_OUT_DIR=benchmarks/local make finalize-warc-bm25-modes-run
BENCHMARK_BASELINE_JSON=/path/to/baseline/warc_bm25_modes.json BENCHMARK_CANDIDATE_JSON=/path/to/candidate/warc_bm25_modes.json BENCHMARK_MAX_REGRESSION_RATIO=0.10 make compare-benchmark-runs
BENCHMARK_BASELINE_JSON=/path/to/baseline/warc_bm25_modes.json BENCHMARK_CANDIDATE_JSON=/path/to/candidate/warc_bm25_modes.json BENCHMARK_MAX_REGRESSION_RATIO=0.10 BENCH_OUT_DIR=benchmarks/local make finalize-benchmark-comparison-run
QUALITY_LABELS_JSON=queries.json QUALITY_LABELS_RECORDS_JSONL=records.jsonl make doctor-quality-labels
QUALITY_LABELS_JSON=queries.json QUALITY_LABELS_RECORDS_JSONL=records.jsonl BENCH_OUT_DIR=benchmarks/local make finalize-quality-labels-run
QUALITY_RECORDS_JSONL=records.jsonl QUALITY_EMBEDDING_JSONL=vectors.jsonl QUALITY_QUERIES_JSON=queries.json BENCH_OUT_DIR=benchmarks/local make finalize-quality-fixture-build-run
QUALITY_RECORDS_JSONL=records.jsonl QUALITY_EMBEDDING_JSONL=vectors.jsonl QUALITY_QUERIES_JSON=queries.json make make-quality-fixture
QUALITY_FIXTURE=quality.fixture.json BENCH_OUT_DIR=benchmarks/local make report-quality-fixture
QUALITY_FIXTURE=quality.fixture.json BENCH_OUT_DIR=benchmarks/local make finalize-quality-run
BENCH_OUT_DIR=benchmarks/local make doctor-quality-baseline
BENCH_OUT_DIR=benchmarks/local make finalize-public-warc-quality-run
PUBLIC_WARC_QUALITY_FIXTURE=/tmp/pywb-public.fixture.json make public-warc-quality-fixture
PG_MAJOR=17 make smoke-pg-version
make pg-version-matrix
make check-release-artifacts
make smoke-release-artifacts
make doctor-release-upgrade-inputs
UPGRADE_OLD_VERSION=0.0.9 make smoke-release-upgrade
make release-artifacts-matrix
make smoke-release-artifacts-matrix
UPGRADE_OLD_VERSION=0.0.9 make smoke-release-upgrade-matrix
BENCH_OUT_DIR=benchmarks/local make report-release-artifacts
BENCH_OUT_DIR=benchmarks/local make report-release-smoke-matrix
RELEASE_SMOKE_TARGET=smoke-release-upgrade make report-release-smoke-matrix
BENCH_OUT_DIR=benchmarks/local make finalize-release-run
RELEASE_SMOKE_TARGET=smoke-release-upgrade make finalize-release-run
IMPORT_SUMMARY=import.summary.json make doctor-import-summary
IMPORT_SUMMARY=import.summary.json BENCH_OUT_DIR=benchmarks/local make finalize-import-run
BENCH_OUT_DIR=benchmarks/local make report-run-directory
BENCH_OUT_DIR=benchmarks/local RUN_METADATA_COMMAND='make bench' make write-run-metadata
BENCH_OUT_DIR=benchmarks/local RUN_DOCTOR_PROFILE=benchmark make doctor-run-directory
BENCH_OUT_DIR=benchmarks/local RUN_METADATA_COMMAND='make bench' RUN_DOCTOR_PROFILE=benchmark make finalize-run-directory
make eval-quality
QUALITY_QUERY_TIMEOUT_SECONDS=30 make eval-quality
ANALYSIS_MAX_ITERATIONS=50 ANALYSIS_MAX_QUERIES=50 ANALYSIS_DEADLINE_SECONDS=120 make eval-quality-budget
ANALYSIS_MAX_ITERATIONS=50 ANALYSIS_MAX_QUERIES=50 ANALYSIS_DEADLINE_SECONDS=120 QUALITY_QUERY_TIMEOUT_SECONDS=30 make eval-quality-budget
make verify
git diff --check
```
