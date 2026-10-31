# pgwarc_lance

> **상태: 연구용 프로토타입 — 프로덕션 미지원.** 현재 소스 개발 후보는 **0.1.25**이다. URI별 document revision, tombstone, ordered vector upsert/delete outbox로 삭제·재생성 중 stale vector 노출을 막으며, 필터 검색은 동일 eligibility 집합을 BM25와 정확 vector 후보 탐색에 적용한다. 새 설명 API는 최종 결과 수와 소스별 후보 수, 가중 RRF 기여를 반환한다. PostgreSQL과 Lance 사이에 분산 원자성은 없으며 outbox 갱신은 commit 뒤 eventual consistency를 제공한다. 현재 후보의 실제 PG16 검증 범위와 한계는 [PUBLICATION.md](PUBLICATION.md)에 기록한다. 과거 artifact의 설치·upgrade·SQL·importer·runtime·backup 검증 및 artifact-bound 의존성 고지는 [THIRD_PARTY.md](THIRD_PARTY.md)에 보존한다. 검증은 binary 재배포·프로덕션 승인을 뜻하지 않는다.
>
> 라이선스: Apache-2.0 ([LICENSE](LICENSE)). 기여 전 [CONTRIBUTING.md](CONTRIBUTING.md)·[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)를 참고하라.

이전 0.1.18 실험, 0.1.19 인덱스, 0.1.20–0.1.25 후보의 결과·한계는 [개발 기록](docs/development-log.md), [PUBLICATION.md](PUBLICATION.md), [PRODUCTION.md](PRODUCTION.md), [THIRD_PARTY.md](THIRD_PARTY.md)에 기록한다.

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
│   ├── lance_outbox_worker.py # committed Lance outbox를 반영하는 standalone worker
│   ├── execution_budget.py    # 분석 하네스 run-scoped 실행 예산(iteration/query/deadline) gate
│   ├── make_quality_fixture.py # records/vector/label JSON -> quality fixture builder
│   ├── doctor_quality_labels.py # query label JSON schema/coverage doctor
│   ├── report_quality_fixture.py # quality fixture schema/label coverage report
│   ├── make_public_warc_quality_fixture.py # public WARC labeled quality fixture 생성
│   ├── check_release_artifacts.py # release artifact 구조/버전 검증
│   ├── collect_dependency_notices.py # target별 Cargo/native 의존성 고지 수집
│   ├── build_distribution_sbom.py # artifact/lock 체크섬 결합 CycloneDX SBOM 생성
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
│   ├── audit_bm25_index.py    # 저장 본문과 BM25 posting read-only 일관성 검사
│   ├── doctor_pg_lance.py     # PostgreSQL/Lance ID·metadata·outbox read-only 대사
│   ├── collection_manifest.py # operator-owned collection/model-version manifest
│   ├── audit_warc_import.py   # raw WARC import accounting과 fingerprint manifest
│   └── warc_importer.py       # WARC/WARC.GZ -> metadata/BM25/Lance SQL importer
├── src/
│   ├── lib.rs                 # SQL 함수 진입점 + extension SQL
│   ├── tokenizer.rs           # CJK bigram + ASCII tokenizer
│   ├── bm25.rs                # BM25 테이블 인덱스
│   ├── lance_store.rs         # Lance dataset create/insert/scan/vector search
│   └── hybrid.rs              # BM25 + vector RRF fusion
└── tests/
    ├── test.sql               # smoke 테스트 (라이브 컨테이너 대상)
    ├── test_audit_bm25_index.py # BM25 content/posting audit 단위 테스트
    ├── test_doctor_pg_lance.py # PG–Lance doctor 분류 회귀
    ├── test_warc_importer.py  # Python WARC importer 단위 테스트
    ├── test_collection_manifest.py # collection profile·stage·activate·rollback 회귀
    ├── test_lance_outbox_worker.py # outbox worker 단위 테스트
    ├── db_outbox.py           # PG16 committed-only/restart outbox 통합 테스트
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
| `lance_outbox_enqueue(uri, ids, flat_vectors, vector_dim, labels)` | caller transaction에 재시도 안전한 비버전 벡터 upsert 작업을 저장 |
| `lance_outbox_enqueue_revision(uri, ids, flat_vectors, vector_dim, labels, revisions)` | URI별 예상 문서 revision mapping과 벡터 작업을 같은 caller transaction에 저장 |
| `lance_document_delete(uri, ids, expected_revisions)` | 예상 revision이 현재 상태와 일치할 때 metadata/BM25를 지우고 tombstone과 순서가 보장된 Lance delete 작업을 커밋 |
| `lance_dataset_write_lock(uri)` | importer가 metadata/BM25 변경 전에 해당 URI writer 잠금을 획득 |
| `lance_outbox_process_next()` | 독립 transaction에서 가장 오래된 완료 가능 작업을 Lance에 반영하고 결과 버전/지연을 반환 |
| `lance_scan(uri, limit=100)` | Lance row 스캔 |
| `lance_scan_document_state(uri, limit=100)` | Lance row의 document ID, label, 실제 저장 revision을 읽기 전용으로 조회 |
| `lance_vector_dim(uri)` | Lance dataset의 vector 차원 반환 |
| `lance_vector_search(uri, query, k=10)` | Lance nearest-neighbor 검색 |
| `hybrid_search(query, vector_query, lance_uri, k=10)` | BM25와 벡터 검색을 RRF로 결합 |
| `hybrid_search_explain(query, vector_query, lance_uri, k=10, candidate_k=100, bm25_weight=1, vector_weight=1, rrf_k=60)` | 별도 후보 한도와 가중 RRF 옵션을 적용하고 소스별 순위·기여를 반환 |
| `hybrid_warc_search(query, vector_query, lance_uri, k=10)` | hybrid search 결과에 `warc_record` metadata를 결합해 반환 |
| `hybrid_warc_search_filtered(query, vector_query, lance_uri, filters, k=10)` | 같은 WARC eligibility 조건을 BM25와 정확 vector 후보 생성에 적용 |
| `hybrid_warc_search_filtered_explain(query, vector_query, lance_uri, filters, k=10, candidate_k=100, bm25_weight=1, vector_weight=1, rrf_k=60)` | 필터 검색에 후보·RRF 옵션을 적용하고 소스별 순위·기여를 반환 |
| `lance_dataset_versions(uri)` | dataset 버전 목록 `(version, created_ms)` 반환 (오래된 순) |
| `lance_restore_version(uri, version)` | 지정 버전을 최신 상태로 재승격 (롤백 보상 경로) |

`pgwarc_lance.lance_outbox`는 importer가 같은 PostgreSQL transaction에 저장한 vector payload, 대기/실패 상태, 재시도 수, 문서 revision, upsert/delete 종류와 완료 Lance 버전을 보관한다. 성공적으로 반영된 행은 완료와 applied version을 커밋할 때 큰 vector payload와 ID 목록을 비우지만 작은 revision 영수증은 유지한다. `lance_document_revision`은 URI/document별 기대 revision과 삭제 tombstone을 기록하고 Lance의 `revision` 열에는 실제 upsert한 revision이 저장된다. `lance_outbox_status`는 URI별 ready/pending/failed 작업 수와 pending/failed delete 작업 수, 가장 오래 기다린 지연, 마지막 완료 시각과 outbox 반영 버전을 요약한다.

`lance_create_table()`은 기존 1-argument 호출 호환성을 위해 기본 차원을 3으로 둔다. 실제 embedding 연동에서는 모델 차원에 맞춰 `vector_dim`을 명시한다. 테스트처럼 반복 실행이 필요한 경우에만 `overwrite=true`로 dataset을 재생성한다.

`pgwarc_lance.warc_record` metadata table은 importer가 적재한 WARC record의 URI, WARC date, content type, HTTP status, payload digest, text length, source file, 정규화 host, HTTP `Content-Language`, media type, manifest collection ID를 보관한다.

`pgwarc_lance.lance_write_log`는 direct API 쓰기와 성공한 outbox 반영의 URI, op, 행 수, 시각을 남긴다. `lance_outbox`의 `completed`와 `applied_version`이 해당 queued 작업의 완료 근거다.

### 트랜잭션·동시성 한계 (반드시 읽어라)

Lance dataset은 PostgreSQL이 관리하지 않는 파일이다. 기본 direct 함수와 importer의 `create`/`overwrite`/`append`/`upsert` 모드는 Lance 파일을 즉시 변경하므로 기존 경계가 남아 있다. 이 경계를 피하는 importer 모드는 `outbox`다.

- `outbox` 모드는 WARC metadata, BM25와 vector payload를 같은 PostgreSQL transaction에 기록한다. rollback되면 이 SQL 행들이 모두 사라지고 worker는 해당 작업을 볼 수 없다. commit 뒤 `python3 tools/lance_outbox_worker.py`가 vector payload를 Lance ID upsert로 반영한다.
- PostgreSQL과 Lance 간 단일 atomic commit은 없다. worker/DB가 Lance write 후 SQL completion commit 전에 중단되면 작업은 pending으로 되돌아오고 다시 전달된다. 같은 document ID와 payload를 upsert하므로 재전달은 멱등적이다. `completed`는 SQL acknowledgment와 Lance `applied_version`이 커밋된 상태를 뜻한다.
- worker는 autocommit의 standalone SQL call로만 동작한다. 같은 transaction의 미커밋 XID는 선택에서 제외되고 explicit `BEGIN` 안의 worker 호출은 거부된다. URI별 이전 작업이 완료되기 전에는 다음 이벤트를 처리하지 않으므로 한 URI의 pending/failed 이벤트가 뒤 작업을 막는다.
- 처리 전까지 BM25와 WARC metadata는 보이고 vector/hybrid 결과는 늦을 수 있다. read-after-write가 필요하면 해당 outbox ID가 `completed`인지 확인한 뒤 vector 검색을 수행한다. `lance_outbox_status`의 대기 지연, 실패, 완료 버전을 감시한다.
- direct Lance API는 즉시 쓰기 의미를 유지한다. 같은 URI에 미완료 outbox 이벤트가 있으면 direct write/restore는 거부되어 두 writer 경로가 순서를 바꾸지 못하게 한다. 한 dataset에서 outbox worker와 외부 Lance writer를 동시에 운용하지 않는다.
- importer의 `upsert`/`outbox`는 각 SQL batch에서 metadata/BM25를 바꾸기 전에 `lance_dataset_write_lock(uri)`를 호출한다. 삭제 함수와 worker도 같은 URI별 transaction lock을 사용하므로 커밋된 upsert/delete 이벤트가 한 dataset 안에서 순서대로 처리된다. revision이 등록된 ID에는 revision 없는 direct upsert를 허용하지 않는다.
- outbox 상태와 payload는 extension configuration data로 `pg_dump` 대상이다. backup 시 importer와 worker를 함께 멈추고 queue가 비었는지 확인한다. pending 작업을 보존한 snapshot은 SQL과 그 시점의 Lance directory를 함께 복구한 뒤 worker를 재개해야 한다.
- outbox를 사용하지 않는 direct write의 rollback/취소 보상은 `lance_dataset_versions()`와 `lance_restore_version()`으로 관리한다. 이 경우 `lance_write_log` 부재가 외부 파일 쓰기 부재를 증명하지 않는다.

### 문서 삭제·재생성 lifecycle (0.1.23)

삭제 전에 현재 `lance_document_revision.expected_revision`을 읽고 그 값을 그대로
`lance_document_delete(uri, ids, expected_revisions)`에 전달한다. revision mapping이
없거나 값이 바뀌었으면 삭제를 거부하므로, 먼저 importer의 revision-aware `outbox`
upsert로 해당 문서를 등록한다. 이 함수는 URI writer lock을 잡고 WARC metadata와
BM25 행을 삭제하고 postings cascade, 마지막 content revision을 보존한 tombstone, delete outbox event를 한 SQL transaction에
커밋한다. 같은 revision으로 반복 요청하면 새 event 없이 no-op이며, 이후 재생성은
새 revision을 포함한 revision-aware outbox upsert로 처리한다. 재생성 때 새 revision mapping이
기존 tombstone을 대체하고, 이전 vector 행은 새 upsert가 전달될 때까지 검색 필터에서 숨긴다.

```sql
SELECT lance_document_delete(uri, ARRAY[doc_id], ARRAY[expected_revision])
FROM pgwarc_lance.lance_document_revision
WHERE uri = '/srv/pgwarc/archive.lance' AND doc_id = 123 AND NOT is_deleted;
```

`lance_document_delete`는 destructive operation이며 함수 권한과
`pgwarc_lance.allow_destructive_ops` 설정을 모두 통과해야 한다. 삭제 commit 직후 BM25와
WARC metadata에서는 빠지고, tombstone이 Lance vector/hybrid 검색 결과를 즉시 거른다.
Worker는 pending event를 재시도해 Lance의 현재 dataset version에서 ID를 제거한다. Lance의
이전 version은 보존되며 extension이 자동 compaction이나 version GC를 수행하지 않는다.
오래된 version을 정리하는 일은 별도 백업·복구 계획 아래 운영자가 수행한다. Lance version만
복원해도 PostgreSQL tombstone은 유지되어 검색 결과가 되살아나지 않는다. 논리 문서 복구는
권위 있는 원본에서 metadata, BM25, 새 revision과 vector를 함께 재구성해야 한다.

### 논리 컬렉션·임베딩 버전 manifest (Issue #10)

`tools/collection_manifest.py`는 컬렉션 ID마다 운영자가 관리하는 JSON manifest를 만든다.
각 immutable 버전은 manifest 디렉터리 아래의 고유 dataset 상대 경로, 모델 ID와 고정 revision,
차원, 정규화 방식/version, chunking 설정/version, 예상 vector 행 수와 profile SHA-256을
기록한다. importer는 기존 ID를 보존하는 `whole_document`와 선택형 `char_window`
전략을 지원한다.

초기 버전과 재색인은 모두 비활성 staged 버전으로 준비한다. 다음 예의 경로는 PostgreSQL
서버도 접근할 수 있어야 하고, 관리자가 설정한 `pgwarc_lance.allowed_uri_prefix` 아래에
있어야 한다.

```bash
MANIFEST=/srv/pgwarc/collections/archive.json
python3 tools/collection_manifest.py create "$MANIFEST" --collection-id archive --version-id e5-v1 \
    --dataset-path datasets/e5-v1.lance --model example/e5-small \
    --model-version "$PINNED_MODEL_REVISION" --dimensions 384 \
    --expected-vectors "$EXPECTED_VECTOR_ROWS" --normalization l2 \
    --normalization-version l2-f32-v1 --chunking-version whole-document-v1
python3 tools/collection_manifest.py prepare "$MANIFEST" --version-id e5-v1 --psql "$ADMIN_PSQL"
python3 tools/warc_importer.py archive.warc.gz --skip-schema --execute \
    --collection-manifest "$MANIFEST" --collection-version e5-v1 --lance-mode upsert \
    --vector-dim 384 --embedding-jsonl vectors.jsonl \
    --embedding-model example/e5-small --embedding-model-version "$PINNED_MODEL_REVISION" \
    --embedding-normalization l2 --normalization-version l2-f32-v1 \
    --chunking-version whole-document-v1 \
    --audit-manifest-json "$PRIVATE_RUN/import-audit.json" --psql "$WRITER_PSQL"
python3 tools/collection_manifest.py activate "$MANIFEST" --version-id e5-v1 --psql "$ADMIN_PSQL"
```

적재 입력 audit의 checksum과 expected vector 수가 manifest-ready receipt에 결합되고,
차원·모델 revision·정규화/chunking version 중 하나라도 다르면 importer가 SQL 실행 전에
거부한다. `l2` 설정은 모든 입력 벡터의 실제 norm도 검사한다. 모델 이름/revision은 운영자가
주장한 provenance다. 원격 embedding service가 그 revision으로 실행됐음을 도구가 증명하지는
않으므로 provider deployment를 별도로 고정해야 한다.

재색인은 새 version과 dataset 경로에만 쓴다. 활성 version은 그대로 resolve된다. 입력 전체를
다시 적재하고 expected vector 수를 만족해 importer가 성공한 뒤에만 manifest가 staged version을
ready로 기록한다. `activate`는 PostgreSQL의 실제 `lance_vector_dim()` 결과를 다시 확인한 뒤
manifest를 atomic rename으로 전환한다. 이전 경로와 버전은 남는다. 문제가 있으면 `rollback`이
직전 active version으로 되돌리며 어느 dataset도 지우지 않는다. 실패한 staged 작업은 `abandon`으로
포인터만 비울 수 있고 해당 버전 metadata와 파일은 보존된다.

```bash
python3 tools/collection_manifest.py stage "$MANIFEST" --version-id e5-v2 \
    --dataset-path datasets/e5-v2.lance --model example/e5-large \
    --model-version "$NEXT_PINNED_MODEL_REVISION" --dimensions 1024 \
    --expected-vectors "$EXPECTED_VECTOR_ROWS" --normalization l2 \
    --normalization-version l2-f32-v1 --chunking-version whole-document-v1
python3 tools/collection_manifest.py prepare "$MANIFEST" --version-id e5-v2 --psql "$ADMIN_PSQL"
# importer: --collection-version e5-v2 and matching versioned profile flags
python3 tools/collection_manifest.py activate "$MANIFEST" --version-id e5-v2 --psql "$ADMIN_PSQL"
ACTIVE_URI=$(python3 tools/collection_manifest.py resolve "$MANIFEST")
python3 tools/collection_manifest.py rollback "$MANIFEST" --psql "$ADMIN_PSQL"
```

`resolve` 결과를 기존 SQL 검색 함수의 dataset URI로 넘긴다; SQL 검색 함수 자체는 여전히 URI를
받는다. manifest와 `.lock` 파일은 신뢰한 OS 운영자가 소유하고, 생성 파일은 mode 0600이다.
여러 버전의 경로는 backup set에 함께 보존한다. 이 기능은 신뢰한 운영자와 공유 corpus를 위한
경로/version 관리이며 사용자·tenant 격리, 독립 권한 경계, DB 전체 corpus namespace를 제공하지
않는다. 검색 애플리케이션이 manifest를 건너뛰고 URI를 직접 지정할 수 있으므로 이 경계를
격리 주장으로 확대하지 않는다.

### 문서 청크와 원문 출처 위치 (Issue #11)

`char_window`는 추출·정규화한 본문을 크기와 overlap에 따라 청크로 나눈다. offset은
원본 WARC byte offset이나 PDF 페이지가 아니라 정규화된 본문의 Unicode codepoint
기준 반개구간 `[char_start, char_end)`이다. 부모 capture ID, 청크 ID, 번호, offset,
전체 본문 문자 수와 chunking version은 embedding JSONL 및
`pgwarc_lance.warc_record`에 함께 보존한다. 같은 URL과 WARC-Date로 다시 추출한
수정본은 부모 ID를 유지하고, 같은 offset/version 청크 ID를 revision-aware upsert로
갱신한다. URL이나 날짜가 없을 때는 기존 content-digest 기반 ID를 사용한다.

```bash
python3 tools/warc_importer.py archive.warc.gz --chunking-strategy char_window \
    --chunk-size-chars 4000 --chunk-overlap-chars 300 \
    --chunking-version char-window-v1 --records-jsonl chunks.jsonl \
    --no-hash-vectors --output metadata-and-bm25.sql
```

각 JSONL row는 chunk `doc_id`, `parent_doc_id`, `chunk_index`, `char_start`,
`char_end`, `parent_text_len`, `chunking_version`과 해당 chunk의 `text`를 포함한다.
외부 embedding command/JSONL은 각 chunk `doc_id`를 key로 vector를 돌려줘야 한다.
활성 char-window collection의 갱신은 `outbox` mode로 수행한다. stale chunk 제거는
`pgwarc_lance.allow_destructive_ops=on`과 이전 revision의 outbox 기록을 요구한다.
완료된 입력에 포함된 부모만 자동 대사한다. 입력에서 완전히 빠진 문서는 기존
`lance_document_delete` API로 해당 부모의 모든 청크를 tombstone 처리한다. SQL과
권한 예제는 [OPERATIONS.md](OPERATIONS.md)를 참고한다.

검색은 `hybrid_warc_search` 결과의 chunk `doc_id`를 `warc_record`와 join해 URL,
수집 시각과 위치를 함께 반환할 수 있다. importer는 확인되지 않은 PDF 페이지 번호를
만들지 않는다. 현재 `--checkpoint-db`는 한 WARC record당 한 checkpoint ID를
전제하므로 `char_window` mode를 실행 전에 거부한다.

### PG–Lance read-only doctor (Issue #6 progress)

`tools/doctor_pg_lance.py`는 PostgreSQL read-only transaction에서 BM25/WARC ID,
URI별 기대 document revision, Lance ID/label/실제 revision, 미완료 outbox를 대사한다.
중복 Lance ID, revision mapping이 기대하는 벡터 중 누락·오래된 revision, PG에 없는
Lance/revision 행, mapping이 없는 벡터, WARC metadata와 다른 label, 대기/실패 outbox를 보고한다.
`--max-rows`는 기본 100,000행이고 250,000행을 넘길 수 없다. Lance scan은 벡터 배열을
backend에서 물질화하므로 행 제한을 넘긴 corpus는 `incomplete`로 보고하고 합격 판정을
내리지 않는다. 전체 corpus 대규모 대사에는 paged scanner가 필요하다.

revision은 안정 ID, target URI, content type, HTTP status, 추출 text 길이, WARC payload
digest와 BM25 content checksum을 결합한 입력 revision이다. embedding model/version
identity는 포함하지 않는다. 기존 Lance dataset은
읽기 전용 doctor가 수정하지 않으며, 첫 upsert가 nullable `revision` 열을 추가한 뒤 해당
row의 revision을 기록한다. 직접 revision 없는 Lance API를 쓰면 이 문서 mapping의 벡터는
unversioned/stale로 표시된다. `reconciliation_complete`는 PostgreSQL과 Lance 사이에
공통 원자 snapshot이 없으므로 계속 `false`다. 이 도구는 read-only 점검이며 자동 복구
명령을 제공하지 않는다. 출력은 변경 전 dry-run 진단으로 취급한다. source 변경을
확인한 뒤에는 같은 trusted embedding 입력으로 importer의 `outbox` 모드를 다시 실행하고,
PG commit 뒤 worker가 revisioned ID upsert를 적용하게 한다. Lance 쓰기 뒤 PG
acknowledgment가 실패해 job이 `pending`으로 돌아온 경우, 오류 원인과 doctor 결과를
검토한 다음 `python3 tools/lance_outbox_worker.py --once --psql "$WORKER_PSQL"`로
명시적으로 재생하고 doctor를 다시 실행한다. doctor 자체는 `--repair` 옵션이나 행 삭제를
수행하지 않는다.
Exit code `0`은 관측 mismatch가 없다는 뜻이지 대사 완료가 아니다. `1`은 관측된
mismatch, `2`는 조회 실패, `3`은 row limit 초과, `4`는 관측 mismatch는 없지만 구조적
검증 한계가 남았음을 뜻한다.

PostgreSQL 조회는 `REPEATABLE READ READ ONLY`, Lance 조회는 단일 dataset scan으로
고정하지만 두 시스템 사이의 원자적 snapshot은 아니다. 운영 중 writer가 돌고 있으면
결과가 한 시점의 cross-store 상태를 뜻하지 않으므로 importer/worker와 외부 Lance writer를
멈춘 점검 창에서 실행해야 한다.

```bash
python3 tools/doctor_pg_lance.py --uri "$LANCE_URI" \
    --psql "$READ_ONLY_PSQL" --max-rows 100000 \
    > "$PRIVATE_EVIDENCE_DIR/pg-lance-doctor.json"
```

### 보안/운영 설정 (GUC)

| GUC | 기본값 | 의미 |
|-----|--------|------|
| `pgwarc_lance.allowed_uri_prefix` | `NULL` | 비우면 호출자를 신뢰(기존 동작). 값을 주면 모든 Lance URI가 이 접두어로 시작해야 하며, 아니면 `insufficient_privilege`로 거부한다. |
| `pgwarc_lance.max_scan_rows` | `0`(무제한) | `lance_scan`이 백엔드 메모리에 담을 수 있는 최대 행 수. 초과 요청은 `program_limit_exceeded`로 거부한다. |
| `pgwarc_lance.allow_destructive_ops` | `on` | `off`면 문서 삭제, `lance_create_table(overwrite => true)`, `lance_restore_version()`을 거부한다. 데이터가 실려 있는 환경에서는 `off`를 권장한다. |

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
버전 bump 뒤 upgrade SQL까지 확인하려면 먼저 `make doctor-release-upgrade-inputs`로 old artifact와 `pgwarc_lance--<old>--<new>.sql` upgrade script가 실제로 존재하는지 DB·Docker 없이 preflight한다. doctor는 `Cargo.toml`의 현재 version과 `dist/`의 추출된 artifact만 보고하며, 입력이 없으면 `valid=false`/`status=blocked` report를 남기고 non-zero로 종료한다. 입력이 있으면 `UPGRADE_OLD_VERSION=<old> make smoke-release-upgrade`로 old version을 설치하고 `ALTER EXTENSION UPDATE TO`를 검증한다. 기본 old artifact 경로는 `dist/pgwarc_lance-<old>-pg<PG_MAJOR>/`이고 new artifact는 현재 `DIST_DIR`을 사용한다. 연속 upgrade SQL은 `sql/`에 보관한다. 0.1.10→0.1.11 populated Common Crawl clone upgrade는 PG16.15에서 통과했으며 다른 major는 별도 검증이 필요하다. Upgrade smoke report와 finalization은 major별 입력 preflight을 자동 강제한다.
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

현재 `tools/warc_importer.py`는 Python stdlib만 사용해 WARC/WARC.GZ를 읽고 SQL을 생성하거나 바로 `psql`로 실행한다. WARC record header에 non-negative `Content-Length`가 없거나 잘못되거나 중복되면 길이 0으로 묵살하지 않고 payload read 전에 입력 오류로 중단한다. HTTP `Transfer-Encoding: chunked` payload도 chunk size, payload length, data terminator, trailer, terminating zero-size chunk가 malformed/truncated이면 입력 오류로 중단한다. HTTP `Content-Encoding: gzip` payload도 압축 해제에 실패하면 원본 바이트를 본문으로 오인하지 않고 입력 오류로 중단한다. `Content-Type` charset parameter의 `=` 주변 공백을 허용해 선언된 문자셋으로 본문을 decode한다. `--charset-override LABEL=CODEC`은 strict decoding이며, 검토된 한 건에만 적용하는 `--charset-record-override WARC-RECORD-ID=CODEC`은 관찰된 U+FFFD 수를 summary에 남긴다. record ID가 없거나 중복되거나 실제 import되지 않으면 SQL을 생성하기 전에 실패한다. `--limit`은 non-negative record count이며 `--limit 0`은 빈 import을 만들고, `--min-text-chars`도 음수 값을 거부한다. importer가 같은 `doc_id`를 두 번 생성하면 SQL/Lance 중복 적재 전에 입력 오류로 중단한다. external embedding JSONL/command vector는 finite numeric value와 정확한 dimension을 가져야 하며, required `doc_id`가 여러 번 나오면 마지막 vector로 덮어쓰지 않고 중단한다. embedding은 세 가지 모드가 있다.

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
| `upsert` | 같은 transaction에서 `lance_upsert_many()`를 즉시 수행 | synchronous direct write. SQL rollback은 Lance write를 되돌리지 않음 |
| `outbox` | metadata/BM25/vector payload를 PostgreSQL transaction에 저장하고 별도 worker가 `lance_upsert_many` 의미로 반영 | commit 뒤 eventual consistency와 at-least-once recovery |

`--overwrite-lance`는 하위 호환용 alias이며 새 사용법에서는 `--lance-mode overwrite`를 쓴다. `append` 모드는 기존 dataset에 row를 추가하지만 Lance row 자체를 deduplicate하지는 않는다. 같은 WARC를 반복 import하면 PostgreSQL metadata/BM25는 doc_id 기준으로 갱신되지만 Lance dataset에는 중복 vector row가 쌓일 수 있다. 반복 실행이 필요한 테스트나 재생성 import에는 `overwrite`를 사용한다. importer는 append vector SQL에 중복 가능성 warning comment를 넣고, summary에는 `lance_append_duplicates=possible`을 표시한다. `outbox`에는 이미 만든 Lance dataset이 필요하며, importer의 `--execute`가 끝난 뒤 worker를 실행한다:

```bash
python3 tools/warc_importer.py input.warc.gz --skip-schema \
    --lance-uri /srv/pgwarc/archive.lance --lance-mode outbox \
    --vector-dim 384 --embedding-jsonl vectors.jsonl --execute \
    --psql "$WRITER_PSQL" --summary-json import.summary.json
python3 tools/lance_outbox_worker.py --psql "$OUTBOX_WORKER_PSQL" --poll-interval-ms 1000
```

worker는 각 job을 PostgreSQL standalone transaction으로 실행하므로 importer chunk가 rollback되면 Lance write도 시작하지 않는다. commit 응답을 얻은 뒤 `lance_outbox`의 해당 ID가 `completed`인지 확인해야 vector 검색의 read-after-write를 보장할 수 있다. 종료 후 새 worker를 시작하면 pending job을 이어서 처리한다.

`--embedding-command`는 stdin으로 record JSONL을 받는다. 각 줄은 `doc_id`, `target_uri`, `warc_date`, `content_type`, `http_status`, `payload_digest`, `text_len`, `source_file`, `text`를 포함한다. stdout은 줄마다 `{"doc_id": 123, "vector": [0.1, ...]}` 형식이어야 하며 vector 길이는 `--vector-dim`과 정확히 같아야 한다. `tools/embed_records_openai.py`는 이 계약을 구현한 OpenAI-compatible 예제이며, `--embedding-jsonl`도 같은 출력 형식을 사용한다.

기본 `--psql`은 `psql -h localhost -p 55432 -U pgwarc_lance -d pgwarc_lance_test -v ON_ERROR_STOP=1`이며, `PGWARC_PSQL` 환경변수로 바꿀 수 있다. `--batch-size`는 생성 SQL을 record 단위 transaction chunk로 나누며 기본값은 1000이다. schema/table 준비와 `lance_create_table()`은 record transaction 밖에서 한 번 실행되고, 각 record chunk는 `-- pgwarc_lance import batch ...` comment 다음 `BEGIN`/`COMMIT`으로 감싼다. `--bm25-mode function`은 기존처럼 record마다 `bm25_index_document()`를 호출하고, `--bm25-mode bulk`는 importer가 CJK bigram + ASCII tokenizer로 term frequency를 계산해 `bm25_doc`/`bm25_term` rows를 `INSERT ... VALUES`로 batch insert한다. `--bm25-mode copy`는 같은 term frequency를 계산하지만 `psql`의 `COPY FROM stdin` stream으로 `bm25_doc`/`bm25_term` rows를 적재한다. 0.1.16 후보에서는 2,000바이트보다 긴 ASCII token도 고정 길이 SHA-256 posting key와 `bm25_long_term` 사전으로 세 모드에서 보존한다. 그런 토큰이 있는 생성 SQL은 실행 전 확장 버전 0.1.16 이상을 확인하며 summary JSON에 token 수와 최장 길이를 기록한다. 2,500,000바이트를 넘는 단일 token은 검색 입력 한도를 초과하므로 importer가 SQL 실행 전에 전체 import를 오류로 중단한다. `copy` mode가 생성한 SQL artifact는 `psql` compatible input으로 실행해야 한다. `--summary`는 사람이 읽는 stderr 한 줄을 출력하고, `--summary-json <path>`는 같은 정보를 machine-readable JSON으로 저장한다. 생성된 summary는 `IMPORT_SUMMARY=<path> make doctor-import-summary`로 DB 없이 검사할 수 있고, `IMPORT_SUMMARY=<path> BENCH_OUT_DIR=<dir> make finalize-import-run`으로 run directory까지 마감할 수 있다.

### Import Failure Recovery

Importer-generated SQL의 direct Lance 모드는 PostgreSQL metadata/BM25를 chunk 단위 `BEGIN`/`COMMIT`으로 묶지만 파일 쓰기는 별도다. 그 경로의 실패 후 상태가 애매하면 같은 입력과 embedding 설정으로 `--lance-mode overwrite`를 사용해 전체 import를 재실행한다. `outbox`는 payload와 metadata를 먼저 한 SQL transaction에 commit하며 worker가 뒤에 반영한다. Lance write 후 DB completion commit 전에 worker가 중단되면 같은 ID의 멱등 upsert를 재전달한다. PostgreSQL/Lance 사이 exactly-once atomic commit은 제공하지 않는다.

대형 또는 중단될 수 있는 import에는 opt-in `--checkpoint-db <private-path>`를 사용한다. SQLite checkpoint는 archive SHA-256, importer 소스와 유효 설정 checksum, archive별 다음 WARC record ordinal, record outcome/revision, vector 및 PostgreSQL batch 상태를 보존한다. 같은 입력·설정으로 다시 실행하면 성공한 record의 본문 추출, embedding, SQL은 건너뛰고 실패 또는 commit 확인을 받지 못한 record만 다시 시도한다. gzip은 임의 압축 위치로 바로 이동할 수 없어 처음부터 checkpoint ordinal까지 순차 파싱하지만 이미 저장된 record는 재추출하지 않는다. JSON summary의 accounting은 실제로 방문한 raw record 범위만 설명한다. `--limit` 결과를 archive 전체 accounting으로 해석하지 않는다.

Checkpoint mode는 importer 인스턴스 하나만 SQLite file lock으로 실행한다. PostgreSQL 연결 대상 checksum을 state에 결합해 다른 DB에서 같은 checkpoint를 재사용하지 못하게 한다. `--execute`는 batch마다 psql error-stop을 켜고 성공 반환 뒤에만 batch를 완료 처리한다. DB commit 후 응답을 받기 전에 worker가 종료됐으면 해당 batch가 미확인 상태로 남아 멱등 재생된다. vector를 쓸 때는 이미 만든 dataset에 `--lance-mode upsert` 또는 `outbox`를 지정해야 한다. 운영에서는 PostgreSQL transaction과 durable retry를 이용하는 `outbox`가 권장되며, outbox worker가 종료돼도 importer checkpoint와 독립적으로 pending job을 재개한다.

Checkpoint SQLite는 추출 본문과 성공한 embedding vector를 포함할 수 있으므로 접근 권한이 제한된 private filesystem에 둔다. 파일은 mode `0600`으로 만들고, 기본 영속 DB 한도는 `--checkpoint-max-bytes 21474836480` (20 GiB), SQLite cache는 2 MiB다. `--checkpoint-max-memory-bytes`는 record, batch, vector 설정으로 계산한 importer working-set estimate를 제한하며 OS 수준 RSS quota는 아니다. record payload/본문은 `--max-record-bytes`, batch input은 `--batch-max-bytes`로 제한한다. checkpoint mode는 별도 SQL/temp spool을 만들지 않는다. embedding stdout은 line·batch 범위에서 스트리밍되고, 외부 embedding command 자체의 모델 memory는 importer가 제한하지 않으므로 별도로 측정한다. Summary의 record fingerprint와 archive checksum을 보관하고, 실패 상태를 조사하기 전 checkpoint를 삭제하거나 다른 target에 복사하지 않는다.

Embedding command 설정 checksum은 command 문자열을 기록하고, embedding JSONL 방식은 파일 내용을 checksum한다. 원격 model endpoint에서 사용된 실제 model revision까지 자동으로 알아내지는 않으므로 재시도 사이에 model version이 바뀌지 않도록 운영자가 고정해야 한다.

- 실패 위치가 batch comment 전이면 schema/table 준비나 Lance dataset 생성 단계에서 멈춘 것이다. 설정을 고친 뒤 `overwrite`로 재실행한다.
- `create`/`overwrite`/`append`/`upsert` direct mode가 batch 안에서 실패하면 PostgreSQL chunk는 rollback되지만 이미 호출된 Lance write는 남아 있을 수 있다. 안전한 재시도는 `overwrite`로 Lance dataset을 재생성하는 것이다.
- `outbox` mode의 SQL chunk가 rollback되면 해당 event와 metadata가 모두 사라진다. commit된 event의 worker가 종료되면 PostgreSQL transaction rollback으로 event는 pending에 남으며 다음 worker가 재개한다. Lance upsert 후 ack 전에 끊겨도 같은 payload가 멱등 재실행된다. `lance_outbox_status`와 event의 `attempt_count`, `last_error`, `applied_version`을 점검하고 `failed`가 있으면 원인을 해결하기 전 뒤 작업을 처리하지 않는다.
- `append` mode에서 실패하거나 같은 WARC를 반복 실행하면 Lance에는 같은 `doc_id` vector row가 중복될 수 있다. PostgreSQL metadata/BM25는 `doc_id` 기준으로 갱신되지만, vector search rank는 중복 row의 영향을 받을 수 있으므로 append 재시도는 dataset 상태가 명확할 때만 사용한다.
- 대형 import에서는 `--summary-json`을 항상 남기고 `IMPORT_SUMMARY=import.summary.json make doctor-import-summary` 또는 `IMPORT_SUMMARY=import.summary.json BENCH_OUT_DIR=benchmarks/local make finalize-import-run`으로 schema, `execute=false`, zero records, Lance append duplicate risk와 run directory completeness를 확인한다. 실패 분석이 필요할 때만 `--output import.sql`로 SQL artifact를 보관한다. 정상 실행 기본 경로는 disk에 큰 SQL 파일을 만들지 않는 `--execute` streaming이다.
- chunk 단위 수동 복구가 필요하면 generated SQL의 `-- pgwarc_lance import batch start-end` comment를 기준으로 실패 batch를 찾는다. 이 경우에도 Lance 상태가 불확실하면 batch만 재실행하지 말고 `overwrite` 전체 재실행을 우선한다.

전체 입력의 원인별 대사와 동일 입력 재현성을 확인하려면
`tools/audit_warc_import.py`를 사용한다. 이 명령은 importer와 같은 WARC parser,
설정된 extractor, BM25 tokenizer/storage key를 실행하며 PostgreSQL/Lance를 변경하지
않는다. 모든 raw WARC record를 imported, metadata-only, failed, policy-skipped로
분류하고 reason count가 각 분류와 일치하는지 검사한다. 입력 파일과 extraction/tokenizer
설정 checksum, 문서·posting·tf fingerprint를 기록한다. 실패 record가 있어도 accounting은
완료될 수 있으며 이때 `import_acceptable`은 `false`다. 결과는 입력 경로를 포함할 수 있어
mode-0700 private 디렉터리에 mode-0600 파일로 저장한다.

Manifest의 `format_outcomes`는 HTML, text, PDF, DOCX, unsupported, unknown,
non-document WARC 형식별로 imported, metadata-only, failed, policy-skipped 수를
대사한다. 형식 값은 allowlist label만 기록하며 응답의 원래 Content-Type 전체는
저장하지 않는다. `reasons.metadata_only_extraction_failures`는 추출 한도·timeout·
기타 extractor 오류를 원문 메시지 없이 집계한다. 텍스트가 없는 PDF는
`pdf_no_text_ocr_or_manual_review`로 기록된다. 이는 OCR 성공이나 OCR 가능성을
뜻하지 않으며, OCR 또는 수동 검토가 필요한 문서 수를 나타낸다. PDF/DOCX extractor
설정에는 실제 시도·추출 수와 출력 byte 수가 남는다. Format별 outcome 합도 raw record
수와 일치해야 manifest가 `accounted`가 된다. 잘못된 `Content-Language` 값은
`invalid_content_language` 실패로 보존하며 값을 임의로 수정하거나 무시하지 않는다.

`warc_importer.py --audit-manifest-json <path>`는 같은 전체 입력 preflight를 SQL 생성 전에
실행한다. 입력이 끝까지 대사되지 않거나 record 실패가 있으면 manifest를 남기고 SQL 실행을
중단한다. 성공한 preflight 뒤 실제 import record 수와 입력 file checksum도 다시 확인한다.
이 옵션은 `--limit`과 함께 사용할 수 없다.

```bash
python3 tools/warc_importer.py archive.warc.gz \
    --audit-manifest-json /private/run/import-manifest.json \
    --execute \
    --document-error-policy fail
```

### BM25 content/posting audit

`tools/audit_bm25_index.py`는 한 `REPEATABLE READ READ ONLY` snapshot에서
`bm25_doc.content`를 현재 importer tokenizer로 다시 세고 저장된 `bm25_term`와
비교한다. `--warc-only`는 WARC metadata가 있는 문서로 범위를 제한한다. 이 도구는
수정이나 자동 재색인을 하지 않는다. 전체 본문과 posting을 읽고 Python tokenizer를
실행하므로 큰 corpus에서는 I/O와 실행 시간이 들며, 검사 중 긴 snapshot이 유지된다.
`--output-json` 보고서는 새 파일을 mode `0600`, 새 상위 디렉터리를 mode `0700`으로
만들어 저장한다. 이 보고서에는 mismatch/unverifiable 문서 ID가 포함되지만 본문과
term 문자열은 포함하지 않으며, 해당 파일은 계속 private evidence로 취급한다. 이 옵션을
주면 stdout에는 ID 없는 집계만 출력되고, 옵션을 생략하면 전체 JSON이 stdout에 출력된다.
Exit code `0`은 일치, `2`는 불일치
또는 null content로 인한 검증 불가, `1`은 검사 실행 실패다. 불일치는 자동 repair의
근거가 아니며 입력·snapshot·버전을 보존하고 문서를 검토한 뒤 별도로 조치한다.

```bash
PGWARC_PSQL="$READ_ONLY_PSQL" python3 tools/audit_bm25_index.py --warc-only \
    --output-json "$PRIVATE_EVIDENCE_DIR/bm25-index-audit.json"
```

WARC 검색 결과를 바로 소비해야 하면 `hybrid_warc_search()`를 사용한다. 기존 `hybrid_search()`와 같은 backward-compatible ranking을 사용하되 `target_uri`, UTC 문자열 `warc_date`, `content_type`, `http_status`, `payload_digest`, `text_len`, `source_file`을 함께 반환한다. 해당 doc_id의 metadata가 없으면 ranking row는 유지하고 metadata 컬럼은 null/0 기본값으로 반환한다. 이 결과에 적용한 SQL `WHERE`는 후보 검색 이후 처리되므로 선택 조건에서 k개를 채우지 못할 수 있다.

조건 결과를 top-k 안에서도 채우려면 `hybrid_warc_search_filtered(query, vector_query, lance_uri, filters, k)`를 사용한다. `filters`는 JSONB 객체이며 `domain`, `from`, `to`, `language`, `format`, `collection_id`만 허용한다. Domain은 정규화한 정확 host, 날짜는 명시적 timezone이 있는 RFC 3339 inclusive 범위, language는 HTTP `Content-Language`의 정확 tag, format은 정확 media type, collection은 importer가 manifest에서 받은 정확 ID다. 값은 제한된 형식·길이로 검증되고 PostgreSQL 조회에는 bind parameter를 쓴다.

BM25는 eligibility ID 집합으로 먼저 제한하고, Lance는 같은 ID 조건을 prefilter로 적용한 뒤 vector index를 끄고 정확 탐색한다. 따라서 filtered API는 ANN을 쓰지 않으며 필터가 맞는 exact vector top-k를 반환한다. 일치하는 metadata row는 최대 10,000개까지 처리하고, 더 많으면 결과를 자르지 않고 `program_limit_exceeded`를 반환한다. 이 한도와 flat vector scan은 정확성·자원 경계를 위한 개발 후보 정책이며 규모별 SLA로 검증된 값은 아니다. 0.1.23 이하에서 이미 적재된 행의 domain/media type은 upgrade 때 가능한 범위에서 backfill되지만, 과거 HTTP language와 manifest collection은 추론하지 않는다. 해당 필터는 그 메타데이터를 새 importer 적재 또는 원본 기반 재적재로 채운 문서에만 일치한다.

기존 검색 함수의 서명과 기본값은 그대로다. 후보 수와 최종 반환 수를 조정하고 출처별 기여를 확인하려면 별도 옵션 API인 `hybrid_search_explain()` 또는 `hybrid_warc_search_filtered_explain()`을 쓴다. 결과는 `doc_id`, 최종 `score`, `source`, 1부터 시작하는 `bm25_rank`/`vector_rank`, 각 소스의 `*_contribution`을 반환한다. 각 기여는 `weight / (rrf_k + rank)`이고 점수는 두 기여의 합이다. 예를 들어:

```sql
SELECT doc_id, score, source, bm25_rank, vector_rank,
       bm25_contribution, vector_contribution
FROM hybrid_search_explain(
    '검색', ARRAY[0,0,0,0]::float4[], '/tmp/warc.lance',
    k => 10, candidate_k => 100,
    bm25_weight => 1.0, vector_weight => 1.0, rrf_k => 60.0
)
ORDER BY score DESC, doc_id ASC;
```

`candidate_k`는 fusion에 넣을 소스별 후보 수이며 0–10,000 범위다. `k`는 소스 후보의 합집합에 적용하는 최종 상한으로 별도로 제한한다. 후보 수가 작으면 실제 반환 행이 `k`보다 적을 수 있다. 두 값의 기본값은 각각 100과 10이다. 기본 BM25/vector 가중치 1/1과 `rrf_k=60`으로 `candidate_k=k`를 주면 기존 RRF 수식과 동일하다. 새 API의 기본 `candidate_k=100`은 legacy `k=10`보다 많은 후보를 fusion에 넣을 수 있다. 필터형 API의 vector 경로는 `candidate_k`를 적용하기 전에 적격 문서 집합(최대 10,000개)을 정확 탐색한다. 가중치는 유한한 0–1,000 값이어야 하고 둘 다 0일 수 없다. 0은 해당 소스를 실행하지 않는 명시적 설정이다. `rrf_k`는 1–10,000 범위이며 기본값 60이다. 결과 동점은 `doc_id` 오름차순으로 결정된다. 활성화한 소스가 실패하면 부분 결과를 성공처럼 돌려주지 않고 호출 전체가 오류가 된다. 함수는 새 설치와 upgrade에서 기본적으로 `PUBLIC EXECUTE`가 회수되므로 사용할 역할에 명시적으로 권한을 준다. 이 옵션을 추가한 사실은 독립 qrels 기반 품질 비교나 권장 가중치 판정을 뜻하지 않는다.

필터형 설명 함수의 Lance 검색은 이전과 같이 전체 적격 집합(최대 10,000개)을 정확 탐색해 stale revision을 거른 뒤 상위 `candidate_k`개를 fusion에 넘긴다. 따라서 `candidate_k`는 fusion 후보 한도이며 filtered exact scan 자체의 fan-out 상한을 대체하지 않는다.

필터 조건을 포함한 설명 결과의 metadata는 `doc_id`로 연결한다:

```sql
SELECT hit.*, record.target_uri, record.warc_date, record.domain
FROM hybrid_warc_search_filtered_explain(
    '검색', ARRAY[0,0,0,0]::float4[], '/tmp/warc.lance',
    '{"domain":"example.org"}'::jsonb,
    k => 10, candidate_k => 100,
    bm25_weight => 1.0, vector_weight => 1.0, rrf_k => 60.0
) AS hit
JOIN pgwarc_lance.warc_record AS record USING (doc_id)
ORDER BY hit.score DESC, hit.doc_id ASC;
```

Snippet/highlight는 extension 안에서 HTML을 렌더링하지 않는다. 설명 검색 결과를
`doc_id`로 `warc_record`와 `bm25_doc`에 연결하고, 선택한 chunk의 본문만 client로
가져온다. PostgreSQL의 `tokenize_korean(query)` 결과를 client helper에 전달하면
기본 BM25와 같은 검색어 token 경계로 snippet의 matched span을 계산한다.

```sql
WITH request(query_text) AS (VALUES (%s::text))
SELECT hit.doc_id, hit.score, hit.source,
       hit.bm25_rank, hit.vector_rank,
       hit.bm25_contribution, hit.vector_contribution,
       record.target_uri, record.parent_doc_id, record.chunk_index,
       record.char_start, record.char_end, record.chunking_version,
       tokenize_korean(request.query_text) AS query_terms,
       body.content
FROM request
CROSS JOIN LATERAL hybrid_search_explain(
    request.query_text, %s::real[], %s::text,
    k => 10, candidate_k => 100,
    bm25_weight => 1.0, vector_weight => 1.0, rrf_k => 60.0
) AS hit
JOIN pgwarc_lance.warc_record AS record USING (doc_id)
JOIN pgwarc_lance.bm25_doc AS body USING (doc_id)
ORDER BY hit.score DESC, hit.doc_id ASC;
```

Psycopg 같은 DB client에서 위 query의 parameter를 `(query_text, vector, lance_uri)`로
bind한 뒤 각 row에 다음 helper를 적용한다. `snippet.text`와 `matched_spans`는
chunk 안의 0부터 시작하는 Unicode codepoint 범위이며, `char_start`를 더하면 정규화된
부모 본문 위치를 얻는다. `parent_doc_id`, `chunk_index`, `char_start/end`,
`chunking_version`은 원 문서와 chunk를 다시 찾는 데 쓴다.

```python
from tools.search_snippets import build_snippet

snippet = build_snippet(row["content"], row["query_terms"], max_chars=240)
parent_spans = [
    (row["char_start"] + span.start, row["char_start"] + span.end)
    for span in snippet.matched_spans
]
# HTML 본문 텍스트 위치에는 escaped markup을 쓸 수 있다. 다른 출력 context에는
# snippet.text를 사용하고 해당 context의 escaping 규칙을 적용한다.
html_fragment = snippet.highlighted_html
```

`max_chars`는 1–2,000 codepoint 범위이고 기본값은 240이다. helper는 1,000,000자를
넘는 단일 chunk를 조용히 잘라내지 않고 오류로 알린다. CJK bigram 등 겹치는 matched
span은 HTML markup에서 합쳐 표시하지만 `matched_spans`에는 원래 span을 유지한다.
Vector-only 결과처럼 chunk 안에 query token이 없으면 본문 앞부분을 반환하고 span은
비어 있다. RRF contribution은 순위 점수의 출처 설명이며 특정 본문 span이 점수에
기여했다는 뜻은 아니다. 선택된 결과 본문만 가져오므로 DB 내부에서 HTML snippet을
반복 렌더링하지 않는다. 확장 함수는 설치 후 `PUBLIC EXECUTE`가 회수되어 있으므로
검색 role에 `hybrid_search_explain(...)`과 `tokenize_korean(text)` 실행,
`warc_record` 및 `bm25_doc` 조회 권한을 필요한 범위로 부여한다.

Candidate-level filter example:

```sql
SELECT doc_id, score, source, target_uri, warc_date, domain,
       content_language, media_type, collection_id
FROM hybrid_warc_search_filtered(
    '검색',
    ARRAY[0,0,0,0]::float4[],
    '/tmp/warc.lance',
    jsonb_build_object(
        'domain', 'example.org',
        'from', '2026-06-30T00:00:00Z',
        'to', '2026-07-01T00:00:00Z',
        'language', 'ko',
        'format', 'text/html',
        'collection_id', 'archive-v1'
    ),
    10
)
ORDER BY score DESC, doc_id ASC;
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

기본 출력은 `dist/pgwarc_lance-0.1.20-pg16/`이며, `PG_MAJOR`를 바꾸면 `dist/pgwarc_lance-0.1.20-pg17/`처럼 major별 디렉토리에 `.so`, `.control`, `pgwarc_lance--0.1.20.sql`을 추출한다. `release-artifacts`는 추출 직후 `.so` ELF header, control `default_version`/`module_pathname`, generated SQL의 핵심 함수 symbol을 검사한다. `smoke-release-artifacts`는 그 산출물을 build image 밖의 official Postgres 컨테이너에 설치해 fresh install smoke를 수행한다. `smoke-release-upgrade`는 old/new artifact와 `pgwarc_lance--OLD--NEW.sql`로 `ALTER EXTENSION UPDATE`를 확인한다. Matrix target은 `PG_MATRIX`의 major 목록을 순회하며, install/upgrade matrix는 기존 `dist/` 산출물을 사용한다. `report-release-artifacts`는 major별 artifact 상태와 SHA-256을 JSON/Markdown으로 남기고, run doctor는 이 artifact matrix report의 row/file schema도 검사한다. `report-release-smoke-matrix`는 major별 runtime smoke 실행 결과와 로그 tail을 남기며, run doctor는 smoke result rows와 pass/exit-code consistency도 검사한다. `RELEASE_SMOKE_TARGET=smoke-release-upgrade`이면 `report-release-upgrade-smoke-matrix`가 `tools/report_release_upgrade_smoke_matrix.py`로 각 major의 `doctor_release_upgrade_inputs` preflight을 먼저 실행하고, blocked major는 smoke를 실행하지 않은 채 per-major `preflight`/`smoke_skipped` field와 함께 non-zero report를 남긴다. `finalize-release-run`은 두 release report와 run metadata/manifest/doctor를 packaging profile로 한 run directory에 마감한다. host PostgreSQL 설치, version upgrade SQL 전략, Rust toolchain pinning 정책은 [PACKAGING.md](PACKAGING.md)에 정리한다.

PG16 배포 후보의 의존성 고지는 `python3 tools/collect_dependency_notices.py --metadata-json <cargo-metadata.json> --target x86_64-unknown-linux-gnu --output-dir <notice-dir> --strict`로 수집한다. 실제 `.so`, 동일 Cargo.lock, compiler/native inventory 및 strict notice report를 `python3 tools/build_distribution_sbom.py`에 전달해 CycloneDX SBOM과 통합 고지 파일을 만든다. 이 결과는 PG16 후보의 artifact·lock 체크섬에 결합되며, binary redistribution 승인을 뜻하지 않는다. 상세 scope와 해시는 [THIRD_PARTY.md](THIRD_PARTY.md)와 [PACKAGING.md](PACKAGING.md)에 있다.

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

`--lance-mode upsert`는 기존 dataset에 같은 ID를 교체하거나 새 ID를 추가하는 immediate direct write다.
단일 chunk 재시도에는 멱등이지만 PostgreSQL rollback과 원자적이지 않다. commit 이후의
재시작 복구가 필요한 batch ingestion에는 `--lance-mode outbox`와
`tools/lance_outbox_worker.py`를 사용한다. 최초 dataset 생성은 관리자가 한 번 수행한다.
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

## 문서 추출 실패 처리

PDF는 `--pdftotext /path/to/pdftotext`, DOCX는 `--extract-docx`로 명시적으로 켠다.
기본값 `--document-error-policy fail`은 추출 실패 시 SQL 실행 전에 중단한다.
`--document-error-policy metadata-only`를 지정하면 실패 문서는 빈 본문과 메타데이터로
보존하고 `extraction_retention`에 집계한다. 해당 문서에는 벡터를 만들지 않으며,
임베딩 명령에는 본문을 얻은 문서만 전달한다. 오류 상세는 최대 16개 종류, 각 512자로
제한하고 나머지는 생략 건수로 표시한다. 추출기 누락, HTTP decoding/크기 및 charset
오류는 이 옵션으로 무시하지 않는다. HTTP decoding 후 PDF signature도 확인한다.
빈 본문은 OCR이나 내용 복구의 성공을 뜻하지 않는다.
