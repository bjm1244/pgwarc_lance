# pgwarc_lance Retrieval Quality

작성일: 2026-07-01

이 문서는 검색 plumbing을 넘어 retrieval quality를 smoke 수준에서 측정하는 방법과 현재 품질 결정을 기록한다.

## Fixture Choice

현재 기본 품질 fixture는 `tools/eval_quality.py` 안의 synthetic relevance corpus를 사용한다. 공개 WARC sample은 크기, 네트워크 접근성, 라이선스 상태가 실행마다 흔들릴 수 있으므로 기본 품질 기준선에서는 제외한다. 대신 `make public-warc-quality-fixture`로 작은 labeled public WARC smoke fixture를 생성할 수 있다. 실제 corpus 반복은 `--fixture-json` 또는 `QUALITY_FIXTURE=<path>`로 labeled fixture를 넘겨 실행한다.

Fixture documents:

| doc_id | topic |
|---:|---|
| 1001 | Korean/BM25 search |
| 1002 | Lance vector embeddings |
| 1003 | WARC archive metadata |
| 1004 | unrelated noise |

`eval_quality.py`는 deterministic 4-dimensional vectors와 expected relevant doc IDs를 함께 적재한다. 실행은 개발 DB의 extension을 drop/create하므로 local smoke/eval 전용이며, 같은 DB를 대상으로 여러 quality eval을 병렬 실행하면 결과가 섞일 수 있다.

External fixture schema:

```json
{
  "name": "my-corpus",
  "vector_dim": 4,
  "docs": [
    {
      "doc_id": 1,
      "target_uri": "https://example.test/doc",
      "text": "document text",
      "vector": [1.0, 0.0, 0.0, 0.0]
    }
  ],
  "queries": [
    {
      "name": "query_name",
      "query": "search text",
      "vector": [1.0, 0.0, 0.0, 0.0],
      "expected_doc_ids": [1]
    }
  ]
}
```

Optional document fields are `warc_date`, `content_type`, `http_status`, and `source_file`. Document and query `expected_doc_ids` values must be positive integers within the PostgreSQL signed `bigint` range and query `expected_doc_ids` must refer to docs in the fixture.

## Fixture Builder Pipeline

실제 corpus 품질 반복은 세 입력을 하나의 external fixture로 묶어 실행한다.

1. `tools/warc_importer.py --records-jsonl records.jsonl`로 importer가 추출한 record metadata와 text를 저장한다. WARC record header에 non-negative `Content-Length`가 없거나 중복되거나 HTTP chunked body의 size/payload/terminator/trailer 계약, `Content-Encoding: gzip` 압축 해제, Content-Type charset decode, importer selection limit, imported `doc_id` uniqueness 계약이 깨진 malformed input은 importer가 실패 처리한다. charset parameter는 `charset = iso-8859-1`처럼 `=` 주변 공백이 있어도 해석한다. `--limit 0`은 빈 fixture를 만들며 `--min-text-chars`와 음수 limit, duplicate `doc_id`는 허용하지 않는다.
2. `tools/embed_records_openai.py` 또는 호환 provider로 `{"doc_id": ..., "vector": [...]}` JSONL을 만든다. 각 vector는 정확한 dimension의 finite numeric values만 가져야 하며 `NaN`, `Infinity`, out-of-range integer와 required `doc_id`의 중복 vector row는 importer가 거부한다.
3. query label JSON에 query text, query vector, expected relevant `doc_id` 목록을 기록한다.

Query label JSON은 list 자체이거나 아래처럼 `queries` key를 가진 object일 수 있다.

```json
{
  "name": "my-corpus-labels",
  "queries": [
    {
      "name": "query_name",
      "query": "search text",
      "vector": [1.0, 0.0, 0.0, 0.0],
      "expected_doc_ids": [1]
    }
  ]
}
```

Fixture를 만들기 전에 query label JSON만 빠르게 검사하려면 `make doctor-quality-labels`를 먼저 실행한다. 이 target은 DB와 embedding JSONL 없이 query name/text, finite numeric vector와 vector dimension, expected label 수, duplicate query name을 확인한다. `QUALITY_LABELS_RECORDS_JSONL`을 함께 주면 `expected_doc_ids`가 실제 record `doc_id`에 존재하는지 검사하고 labeled/unlabeled doc coverage를 report한다.

```bash
QUALITY_LABELS_JSON=queries.json \
QUALITY_LABELS_RECORDS_JSONL=records.jsonl \
BENCH_OUT_DIR=benchmarks/local \
make doctor-quality-labels
```

`BENCH_OUT_DIR`를 주면 `quality_labels_doctor.json`과 `quality_labels_doctor.md`가 생성된다. `QUALITY_LABELS_MIN_QUERIES`와 `QUALITY_LABELS_MIN_EXPECTED_LABELS`로 real corpus baseline에 필요한 최소 query/label 수를 강제할 수 있다.

Labeling session을 run directory로 마감하려면 `make finalize-quality-labels-run`을 사용한다. 이 target은 `doctor-quality-labels`를 실행한 뒤 `finalize-run-directory`를 호출하고, `quality_labels_doctor.json`을 필수 artifact로 요구한다. Run doctor는 이 report의 `valid=true`도 확인한다.

```bash
QUALITY_LABELS_JSON=queries.json \
QUALITY_LABELS_RECORDS_JSONL=records.jsonl \
BENCH_OUT_DIR=benchmarks/local \
make finalize-quality-labels-run
```

Records, vectors, query labels에서 DB 없이 fixture와 fixture report까지 만들고 run directory로 마감하려면 `make finalize-quality-fixture-build-run`을 사용한다.

```bash
QUALITY_RECORDS_JSONL=records.jsonl \
QUALITY_EMBEDDING_JSONL=vectors.jsonl \
QUALITY_QUERIES_JSON=queries.json \
BENCH_OUT_DIR=benchmarks/local \
make finalize-quality-fixture-build-run
```

이 target은 `doctor-quality-labels`, `make-quality-fixture`, `report-quality-fixture`, `finalize-run-directory`를 순서대로 실행하고 `quality_labels_doctor.json`, `quality.fixture.json`, `quality_fixture_report.json`을 필수 artifact로 요구한다. Run doctor는 required status artifact의 성공 플래그와 `quality.fixture.json`의 fixture schema, vector dimension, expected doc ids, duplicate doc/query도 확인한다.

Fixture build와 eval:

```bash
QUALITY_RECORDS_JSONL=records.jsonl \
QUALITY_EMBEDDING_JSONL=vectors.jsonl \
QUALITY_QUERIES_JSON=queries.json \
QUALITY_FIXTURE_OUTPUT=quality.fixture.json \
make make-quality-fixture

QUALITY_FIXTURE=quality.fixture.json BENCH_OUT_DIR=benchmarks/local make report-quality-fixture
QUALITY_FIXTURE=quality.fixture.json make eval-quality
```

`tools/make_quality_fixture.py`는 record `doc_id` 중복, positive signed-64 `doc_id` domain, 누락 vector, vector dimension mismatch, query label의 unknown `doc_id`와 duplicate query name을 fixture 생성 전에 거부한다. `tools/warc_importer.py`의 embedding JSONL 경로도 required `doc_id`별 vector가 정확히 하나인지 확인해 중복 row를 마지막 값으로 덮어쓰지 않는다. `tools/eval_quality.py --fixture-json` 경로도 모든 doc/query vector의 finite numeric 및 Python float 범위와 positive PostgreSQL `bigint` `doc_id` domain을 확인하고 duplicate `doc_id`, query name, query별 `expected_doc_ids`를 거부해 `NaN`, `Infinity`, float 변환 overflow와 identity 충돌이 SQL/Lance setup으로 전달되지 않게 한다. 출력 fixture는 `tools/eval_quality.py --fixture-json`과 같은 schema를 사용한다.

`tools/report_quality_fixture.py`는 fixture를 DB에 적재하지 않고 schema와 label coverage를 검사해 `quality_fixture_report.json`과 `quality_fixture_report.md`를 남긴다. report에는 doc/query 수, vector dimension, expected label 수, labeled/unlabeled doc 수, duplicate `doc_id`, duplicate query name, query별 expected doc 수가 들어간다. 각 query의 `expected_doc_ids`는 같은 문서를 두 번 세어 label 수나 metric을 부풀리지 않도록 중복 값을 거부한다. direct `tools/eval_quality.py --fixture-json` 경로도 동일한 doc/query identity와 relevance label 중복을 setup 전에 거부한다. `QUALITY_FIXTURE_MIN_DOCS`와 `QUALITY_FIXTURE_MIN_QUERIES`로 real corpus baseline에 필요한 최소 크기를 강제할 수 있다. Run doctor는 required `quality_fixture_report.json`의 count fields, expected label total, query rows도 검사한다.
schema v2 WARC corpus manifest도 함께 검사하려면 `QUALITY_PROVENANCE_JSON=warc_provenance.json make report-quality-fixture`를 사용한다. report는 source별 raw/imported/skipped record 수와 labeled/unlabeled coverage를 복사하고, `QUALITY_MIN_SOURCE_DOCS`로 source별 최소 doc 수를, `QUALITY_MAX_SOURCE_SKIPPED_RATIO`로 source별 skipped 비율 상한을 강제한다. schema v1 provenance는 coverage가 없으므로 warning만 남기고 기존 단일 WARC report를 계속 허용한다. Run doctor는 schema v2 report의 `source_count`/`source_coverage` 필수 구조, source별 합계와 ratio, 전체 fixture coverage 합계, threshold 필드와 결과도 재검증한다.
`finalize-public-warc-quality-run`은 `warc_provenance.json`을 required artifact로 넘긴다. Run doctor는 manifest schema v1/v2의 source identity, source/query-spec/fixture count, schema v2 source별 record·coverage 합계를 report와 별도로 검사하고, 기록된 local source/spec/query-spec 파일이 존재할 때 bytes/SHA-256 drift를 검출한다. URL source 또는 삭제된 외부 파일은 재다운로드하지 않으므로 저장된 hash만 남고 현재 파일과의 독립 비교는 수행되지 않는다.

Run directory에 quality baseline을 남길 때는 `finalize-quality-run`을 사용한다. 이 target은 `stage-quality-provenance`, `report-quality-fixture`, `eval-quality`, `doctor-quality-baseline`, `finalize-run-directory`를 순서대로 실행하고 `RUN_DOCTOR_PROFILE=quality`와 `QUALITY_RUN_DOCTOR_REQUIRE=quality_baseline_doctor.json`로 `quality_fixture_report.json`, `quality.json`, `quality_baseline_doctor.json`을 검사한다. `QUALITY_PROVENANCE_JSON`을 지정하면 입력 manifest를 run directory의 표준 이름 `warc_provenance.json`으로 복사하고, report는 그 staged path를 참조하며, run doctor가 해당 artifact를 required provenance contract로 독립 검수한다. source가 삭제되거나 URL-only라 현재 hash를 재계산할 수 없는 경우에는 저장된 provenance만 보존한다.
Run doctor는 `quality.json`의 `retrieval_quality_smoke` benchmark name, doc/query/vector/k counts, hit-rate/MRR 범위, result row schema도 검사한다.

```bash
BENCH_OUT_DIR=benchmarks/local \
QUALITY_FIXTURE=quality.fixture.json \
make finalize-quality-run

BENCH_OUT_DIR=benchmarks/local \
QUALITY_BASELINE_MIN_HIT_RATE=0.8 \
QUALITY_BASELINE_MIN_MRR=0.6 \
make doctor-quality-baseline
```

`tools/doctor_quality_baseline.py`는 `quality_fixture_report.json`과 `quality.json`을 읽어 최소 doc/query/expected label 수, hit-rate, MRR 기준을 검사하고 `quality_baseline_doctor.json`과 `quality_baseline_doctor.md`를 남긴다. WARC schema v2 fixture report가 있으면 source provenance, source별 raw/imported/skipped/coverage, source threshold도 baseline artifact와 Markdown 표에 보존한다. `finalize-quality-run`은 이 doctor를 기본 실행한다. Run doctor는 required `quality_baseline_doctor.json`의 threshold/count/result schema와 보존된 source coverage 계약도 재검증한다. 기본 metric threshold는 `0`이며, 실제 corpus baseline을 고정할 때 `QUALITY_BASELINE_MIN_HIT_RATE`와 `QUALITY_BASELINE_MIN_MRR`을 명시한다.

실제 WARC baseline을 마감할 때는 `QUALITY_BASELINE_REQUIRE_REAL_CORPUS=1`을 함께 지정한다. 이 모드는 schema v2 provenance manifest와 최소 하나의 source, 20–50개 query, 0보다 큰 hit-rate/MRR threshold를 모두 요구하므로 synthetic 또는 작은 public smoke가 real baseline으로 통과하지 않는다.

```bash
QUALITY_FIXTURE_MIN_QUERIES=20 \
QUALITY_BASELINE_REQUIRE_REAL_CORPUS=1 \
QUALITY_BASELINE_MIN_HIT_RATE=0.8 \
QUALITY_BASELINE_MIN_MRR=0.6 \
QUALITY_INPUT_PREFLIGHT_CORPUS=<real-corpus.warc.gz> \
QUALITY_INPUT_PREFLIGHT_QUERIES_JSON=<queries.json> \
QUALITY_INPUT_PREFLIGHT_QRELS_JSON=<qrels.json> \
QUALITY_INPUT_PREFLIGHT_MANIFEST_JSON=<real-corpus-input.json> \
QUALITY_FIXTURE=<real-quality-fixture.json> \
QUALITY_PROVENANCE_JSON=<real-warc-provenance.json> \
BENCH_OUT_DIR=<real-quality-run> \
make finalize-quality-run
```

## Real Corpus Input Contract

Synthetic/public smoke를 real baseline으로 포장하지 않기 위해, 실제 corpus를 평가에 넣기 전에 real corpus input manifest를 먼저 검증한다. 검증은 `tools/doctor_real_corpus_input.py`를 `make doctor-real-corpus-input`으로 실행한다.

```bash
REAL_CORPUS_INPUT_JSON=real_corpus_input.json BENCH_OUT_DIR=benchmarks/local make doctor-real-corpus-input
```

Manifest schema v1:

```json
{
  "schema_version": 1,
  "corpus": {
    "name": "controlled-real-corpus",
    "origin": "external",
    "sources": [
      {
        "name": "part-000",
        "kind": "path",
        "path": "corpus/part-000.warc.gz",
        "url": "",
        "bytes": 123456,
        "sha256": "<64-hex>",
        "license": "CC-BY-4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "redistribution": "permitted"
      }
    ]
  },
  "labels": {
    "query_set_version": "v1",
    "qrels_version": "v1",
    "independence": "external",
    "method": "manual relevance judging by domain expert",
    "labels_path": "labels/queries.json",
    "labels_sha256": "<64-hex>",
    "query_count": 30
  },
  "evaluation": {
    "commit": "<7-40 hex git revision>",
    "postgres_version": "16.4",
    "search_settings": {"function": "hybrid_warc_search", "k": 10}
  }
}
```

Doctor가 강제하는 계약:

- `corpus.origin`은 `external`만, `labels.independence`는 `external`만 허용한다. synthetic/public smoke와 self-labeled relevance는 real baseline 입력으로 통과하지 않는다.
- 각 source는 `kind`가 `path` 또는 `url` 중 하나이고 정확히 한쪽만 채운다. `path` source는 파일이 존재해야 하며 기록된 `bytes`/`sha256`과 현재 값이 다르면 오류다. `url` source는 현재 bytes/SHA-256을 독립 재검증하지 못하므로 warning을 남긴다.
- `license`, `license_url`, `redistribution`(`permitted`/`restricted`)을 모두 요구한다. 알 수 없는 사용 권한은 통과하지 않는다.
- `labels.query_count`는 `REAL_CORPUS_INPUT_MIN_QUERIES`-`REAL_CORPUS_INPUT_MAX_QUERIES`(기본 20-50) 범위여야 하고 `labels_path`의 실제 query 수와 일치해야 한다.
- `labels_path`는 query list 또는 `{"queries": [...]}` object여야 하며, query `name`은 유일하고 `query` text와 비어 있지 않은 중복 없는 양의 정수 `expected_doc_ids`를 가져야 한다.
- `evaluation.commit`은 7-40자 hex git revision, `postgres_version`은 비어 있지 않은 문자열, `search_settings`는 비어 있지 않은 object여야 한다.

Report는 `real_corpus_input_doctor.json`/`real_corpus_input_doctor.md`로 남고, invalid 입력은 non-zero로 종료한다.

### Non-success artifact 소비 계약

`tools/eval_quality.py`가 남기는 `quality_eval_timeout`/`budget_exhausted` artifact는 유효한 실행 실패/부분 산출물이지만 성공 baseline이 아니다. `tools/doctor_quality_baseline.py`는 이를 `hit_rate_at_k`/`mrr_at_k` 숫자 변환보다 먼저 status로 식별해 metric을 `n/a`로 비우고 원본 `status`, 완료 결과 수, `timeout`(stage/reason/limit_seconds/client_side_only/timeout_observation) 또는 `budget`(limit_kind/used/limit) 진단을 기존 report JSON/Markdown 구조에 보존한 뒤 valid=false/non-zero로 끝난다. `tools/doctor_run_directory.py`의 `quality.json` 검사는 성공 schema 검증과 non-success 구조 검증을 분리하고 `success=false`/알 수 없는 status/`status`+`success` 모순을 structured error로 처리하므로 timeout artifact가 남은 run directory는 valid=true가 될 수 없다. partial 결과는 0점이나 성공 점수로 재계산하지 않는다. 이번 소비 계약에도 real labeled corpus/qrels(Issue A)와 old/new artifact·upgrade SQL(Issue B)은 계속 미측정(blocked)이다.

### Timeout observation 한계

timeout artifact는 `timeout.timeout_observation` 객체로 statement outcome과 backend follow-up 상태를 분리한다. 값은 `statement_outcome`(`unknown` 또는 확인된 서버 path에서만 `server_statement_timeout_reported`), `backend_state_after_timeout=unobserved`, `cancellation_completion=unverified`, `transaction_cleanup=unverified`다. client subprocess timeout(`subprocess.TimeoutExpired` → `SqlTimeout`, pre-run 1ms 미만 cap 포함)은 statement outcome을 알 수 없으므로 항상 `unknown`이며, client timeout 예외의 stderr에 statement-timeout 문구가 들어 있어도 서버 outcome으로 승격하지 않는다. `unverified`는 관측하지 않았다는 뜻이지 실패·rollback·계속 실행을 확인했다는 뜻이 아니다. observation은 성공 artifact, 일반 SQL 오류, pre-run 예산 소진에는 넣지 않는다. observation이 없는 legacy timeout은 계속 수용하되 두 doctor가 관측이 기록되지 않았음(unverified)을 렌더링하고, malformed observation은 구조화된 검증 오류로만 처리하며 confirmed outcome으로 취급하지 않는다. `tools/doctor_run_directory.py`는 observation이 객체일 때 enum을 정확히 검증하고 `client_side_only=true`는 client timeout에만, `false`는 `timeout_source=server_statement`와 `statement_outcome=server_statement_timeout_reported` 조합에만 허용한다. observation 객체는 정확히 위 네 필드만 가질 수 있으며 `backend_pid`/`cancelled`/`rollback_completed` 같은 추가 claim은 구조화된 검증 오류(`must not include unsupported fields`)로 거부한다. 이 schema 경계는 문서·검증 계약일 뿐이며 active cancellation이나 backend 상태 관측은 구현되지 않았다(값은 계속 `unobserved`/`unverified`다). 이번 slice는 권한 있는 connection, `pg_cancel_backend`/`pg_terminate_backend`, PID SQL, 자동 retry, 새 runtime 필드를 추가하지 않는다.

### Opt-in query 전용 서버 statement_timeout

`tools/eval_quality.py`는 client subprocess timeout(`--query-timeout-seconds`, Make `QUALITY_QUERY_TIMEOUT_SECONDS`)과 별도로 `--query-statement-timeout-seconds`(Make `QUERY_STATEMENT_TIMEOUT_SECONDS`) opt-in 서버 제한을 제공한다. 값이 없으면 서버 timeout SQL을 전혀 주입하지 않고 기존 동작을 보존한다. 값은 Decimal 경계로 PostgreSQL millisecond 범위 `1..2147483647ms`로 표현 가능한 유한 양수만 허용하고 `0`/음수/`NaN`/`Infinity`/1ms 미만/범위 초과는 SQL 실행 전에 exit code 2로 거부한다. 이 제한은 `query_sql`의 `hybrid_warc_search` SELECT 하나에만 적용되며, query마다 별도 psql subprocess/session에서 `\set QUIET on`/`\set ON_ERROR_STOP on`/`\set VERBOSITY verbose`와 `BEGIN; SET LOCAL statement_timeout = <ms>; SELECT ...; COMMIT;` 래퍼로 실행해 command tag가 결과에 섞이지 않고 SELECT 실패 후 COMMIT이 성공으로 진행되지 않는다. `setup_sql`에는 `SET LOCAL`/`statement_timeout`을 주입하지 않고 setup transaction 구조도 바꾸지 않는다. query 직전에 명시 서버 제한, client effective limit, run budget remaining deadline 중 가장 짧은 값을 PostgreSQL 정수 ms로 내림해 적용하되 0ms는 만들지 않으며, 1ms 미만 cap이면 query subprocess를 시작하지 않고 기존 client/budget 비성공 경로로 종료한다. 서버 statement timeout은 SQLSTATE 57014만으로 추측하지 않고 `canceling statement due to statement timeout` 안정 진단이 확인될 때만 기존 `quality_eval_timeout`/exit 4 경로로 분류하고, 사용자 취소·기타 SQL 오류는 기존 오류 경로로 남기며 `subprocess.TimeoutExpired`가 먼저 나면 client timeout 경로를 유지한다. timeout artifact에는 `requested_statement_timeout_ms`/`effective_statement_timeout_ms`/`timeout_source`(`server_statement`/`client`/`run_budget`)/`sqlstate`(없으면 null)를 선택 필드로 보존하고 `client_side_only`는 false가 되며, 확인된 서버 statement timeout에만 `timeout_observation.statement_outcome=server_statement_timeout_reported`를 기록한다. doctor는 이 artifact를 계속 valid=false로 거부한다. 서버 timeout은 성공/0점으로 처리하지 않고, backend 종료·rollback 완료·자원 회수나 취소 완료를 보장하지 않는다(server timeout과 psql 종료 사이 race 가능). 이는 harness 경계 증거이지 real quality baseline이 아니다. 다음 청사진은 backend 상태 관측/active cancellation을 별도 계약으로 다루며 session identity(PID/backend/transaction), 별도 권한 connection, target race, timeout race, 관측·취소 시도의 budget, rollback/transaction cleanup 판정 기준을 먼저 요구한다. 그 전에는 timeout artifact가 backend 종료나 transaction 정리를 확인한 것으로 표기되지 않는다. real labeled corpus/qrels(Issue A) 입력과 old/new artifact·upgrade SQL(Issue B) 입력은 계속 blocked/separate다.

### Prospective query attempt identity 계약 (opt-in, synthetic)

2026-09-15 03:00 KST 기준으로 아직 런타임이 존재하지 않는 query attempt identity를 위한 prospective acceptance boundary만 고정했다. 이 계약은 backend 상태 관측이 아니라 schema/검증 경계다.

- `contracts/quality_attempt_identity.v1.schema.json`은 strict v1 envelope를 정의한다. 모든 object level에서 `additionalProperties=false`이고 required field는 `schema_version`(=1), correlation-only non-empty `attempt_id`, `capture_state`(`unavailable`|`captured`), `session_identity`, `transaction_identity`(v1은 정확히 `{"state":"unobserved"}`), `capture_source`(`none`|`query_session`)다. `captured`이면 `session_identity`는 정확히 `{backend_pid, backend_start}`이고 `backend_pid`는 양의 정수(bool 불가), `backend_start`는 explicit timezone을 가진 ISO-8601 date-time이며 `capture_source=query_session`이어야 한다. `unavailable`이면 `session_identity=null`, `capture_source=none`이어야 한다. schema description에 non-authenticity 한계를 명시했고 cancellation/rollback/termination field는 정의하지 않는다.
- `tools/doctor_quality_attempt_identity.py`는 stdlib만 쓰는 opt-in validator다. `validate_identity(payload, label=...) -> list[str]`를 노출하고 strict key/type/enum/조건 일관성, 양의 PID, timezone 포함 timestamp, 정확한 unobserved transaction object를 검증하며 JSON/Markdown optional output과 malformed 입력에 대한 non-zero exit를 제공한다. `make doctor-quality-attempt-identity QUALITY_ATTEMPT_IDENTITY_JSON=<path>`로 실행한다.
- 이 validator는 synthetic이고 opt-in이며 `tools/eval_quality.py`, `tools/doctor_run_directory.py`, run finalization, `make verify`에서 호출되지 않는다. 기존 `timeout_observation` 네 필드와 eval-quality 결과 parsing/default 동작은 그대로 유지한다.
- 검증은 structural consistency만 증명한다. backend/statement/transaction identity의 authenticity, backend의 계속된 존재, 취소·rollback·cleanup을 증명하지 않는다. fixture(`tests/fixtures/quality_attempt_identity_contract_cases.json`)도 손으로 작성했고 producer를 호출해 생성하지 않았다.
- next runtime gate: same-session에서 reliably framed identity를 quality query 이전에 capture해야 하며, 미래의 observer connection은 explicit permission, target race, timeout race, budget, cleanup 판정 evidence를 먼저 갖춰야 한다. 그 전까지 이 계약을 runtime backend observation으로 승격하지 않는다. Issue A real labeled corpus/qrels와 Issue B old/new artifact·upgrade SQL은 계속 blocked/separate다.

### Timeout-after-timeout 상태 판정 계약 (pure, synthetic)

2026-09-15 15:03 KST 개발 의사결정(2026-09-15)에 따라 timeout 이후 backend 상태 판정을 런타임 없이 판정 가능한 pure contract로 고정했다. `tools/quality_timeout_state_decision.py`의 `decide_quality_timeout_state(events, label=...) -> dict`는 합성 event 목록을 받아 결정론적으로 상태를 fold한다. DB connection, backend 관측, 취소 요청, 정리 실행은 전혀 수행하지 않는다.

- 재사용: event는 `attempt_recorded`로 `quality_attempt_identity.v1` envelope(`doctor_quality_attempt_identity.validate_identity`로 재검증)를 확립하고, timeout은 기존 `eval_quality.TIMEOUT_OBSERVATION_FIELDS` 네 필드 enum(`statement_outcome`, `backend_state_after_timeout=unobserved`, `cancellation_completion=unverified`, `transaction_cleanup=unverified`)을 엄격히 검증해 소비한다.
- 판정 상태: `no_attempt`, `attempt_recorded`, `timeout_recorded_backend_unobserved`, `cancellation_accepted_not_confirmed`, `statement_completed`, `termination_confirmed_cleanup_unconfirmed`, `cleanup_confirmed`, `budget_exhausted_without_cleanup`, `indeterminate_no_claims`. timeout 기록과 확인된 backend 종료, 취소 수락과 실제 종료, 관측 실패와 cleanup 완료, budget 소진과 cleanup 성공은 상태와 boolean flag로 분리된다.
- 안전 규칙: `backend_termination_reported`와 `cleanup_completed`가 confirmation-grade이려면 이벤트가 기록된 attempt의 `session_identity`와 정확히 같은 dict를 가져야 한다. identity 불일치(present-but-different)는 전체 판정을 `indeterminate_no_claims`로 만들고, identity 누락(missing)·미캡처(`capture_state=unavailable`)은 해당 증거를 ignore로 남기고 confirm하지 않는다. `cancellation_accepted`는 `cancellation_requested` 이전에 오면 구조 오류다. stale/out-of-order(`event_seq`가 마지막 수용 seq 이하), duplicate(`event_id` 중복) 이벤트는 판정에 기여하지 않는다. `observation_failed`와 `budget_exhausted`는 절대 cleanup claim을 만들지 않는다.
- 문서화된 합성 경계: 이 contract는 synthetic이며 opt-in CLI(`python3 tools/quality_timeout_state_decision.py --input-json <events.json> [--json-output <path>]`)만 제공하고, `eval-quality`, `doctor_run_directory`, run finalization, `make verify`는 호출하지 않는다. 결과의 `confirmed`/`cleanup_confirmed`는 합성 증거에 대한 결정일 뿐 실제 backend 종료·rollback·자원 회수를 증명하지 않는다.
- 테스트: `tests/test_quality_timeout_state_decision.py`가 timeout-only, timeout 전후 자연 완료, 취소 수락 후 backend 계속 실행, identity 일치 종료+명시 cleanup 증거, identity mismatch/누락/미캡처, stale/duplicate, 관측 실패, budget 소진, malformed observation/envelope를 결정론적으로 검증한다.
- next runtime integration requirements: 이 판정을 runtime에 연결하려면 (1) same-session에서 identity를 query 이전에 기록하는 capture 단계, (2) 명시적 permission을 가진 별도 observer connection, (3) target race(대상 backend가 이미 종료된 경우)와 timeout race(취소 요청과 client/server timeout의 경합) 판정 규칙, (4) 관측·취소 시도 자체를 세는 budget 계약, (5) rollback/transaction cleanup 판정에 대한 실제 DB 증거가 먼저 필요하다. 그 전까지 이 계약을 실제 관측으로 승격하지 않는다.

### Synthetic opt-in eligibility/plan 계약 (pure, synthetic)

2026-09-15 23:00 KST 개발 의사결정(2026-09-15)에 따라 backend 관측/취소 런타임이 없는 상태에서 관측 적격성과 취소 적격성을 분리 판정하는 default-deny opt-in 계획 계약을 고정했다. `tools/quality_synthetic_optin_plan.py`의 `decide_synthetic_optin_plan(request, label=...) -> dict`는 합성 opt-in request를 받아 결정론적으로 적격성을 판정한다. DB connection, backend 관측, 취소 실행, 권한 부여, 실제 identity capture는 전혀 수행하지 않는다.

- default deny: opt-in 누락/거짓, 필수 조건(schema_version/attempt/decision_time) 누락, 미지 값, 모순(permission 미부여인데 source 존재), malformed request와 unsupported field는 계획을 `invalid_request` 또는 `rejected_default_deny`로 거부한다. opt-in이 없으면 어떤 적격성도 계산되지 않는다.
- 재사용: attempt는 `quality_attempt_identity.v1` envelope이고 `doctor_quality_attempt_identity.validate_identity`로 재검증되며, tz-aware ISO-8601 기한 판정과 양의 정수 예산 검사도 같은 모듈 규약을 재사용한다. same-session identity capture(`capture_state=captured` + `session_identity` 객체)를 모든 적격성의 전제로 요구한다.
- 관측/취소 분리: 관측 적격성은 permission 부여와 source, same-session capture 선언, target/timeout race 처리 선언, 유한 `max_observations` 예산, explicit timezone 만료를 요구하고, 취소 적격성을 자동 부여하지 않는다. 취소는 별도 요청 위에 자체 permission, `requires_execution_revalidation=true`(실행 시점 재검증 명시), `target_scope=single_recorded_attempt`(재사용/모호 target 거부), `identity_confirmation`이 기록 `session_identity`와 정확히 일치, 유한 `max_cancellations`, 만료 시각, 관측 grant 만료 없음이 모두 필요하다.
- 합성 경계: 계획 산출물은 항상 `execution_authority=none`이고 `claims`(backend termination/cancellation completed/rollback completed/transaction cleanup)는 `not_claimed`로 고정된다. 적격성은 일관성 검사일 뿐 실행 승인이 아니며 실제 backend 종료·취소 완료·rollback·transaction cleanup을 증명하지 않는다. `execution_requirements`는 runtime 통합 전제(identity capture, permission을 가진 observer connection, 실행 시점 재검증, race 처리, 유한 예산, 실제 DB 증거)를 명시한다.
- CLI와 문서 명령: `python3 tools/quality_synthetic_optin_plan.py --input-json <request.json> [--json-output <path>]`만 제공한다. 이 contract는 opt-in이며 `eval-quality`, `doctor_run_directory`, run finalization, `make verify`는 호출하지 않고 eval-quality 산출물과 공개 API/CLI/config/schema는 변경하지 않는다.
- plan output schema uniformity: `decide_synthetic_optin_plan`의 모든 판정 경계는 동일한 top-level field 집합(`schema_version`, `plan_state`, `opt_in_declared`, `attempt_id`, `decision_time`, `observation_*`, `cancellation_*`, `request_errors`, `error_count`, `execution_authority`, `claims`, `execution_requirements`, `scope`)을 가진다. non-object request(`list`/`str`/정수/`null`/boolean)도 `invalid_request`/`error_count=1` plan을 남긴다; 산출물 schema가 경로별로 달라지는 contract gap을 회귀 테스트로 고정했다.
- attempt clock consistency: 캡처된 same-session identity의 `backend_start`가 request `decision_time`보다 이후인 합성 request는 내부 모순이며, 관측·취소 조건과 무관하게 `invalid_request` plan으로 거부된다 (`decision_time`과 같은 순간이거나 다른 timezone offset으로 같은 순간이면 수용된다). 시계가 존재를 앞서는 identity가 적격성을 통과하던 request/planner 경계 gap을 회귀 테스트로 고정했다.
- internal batch consumer (synthetic 전용): 2026-09-16 23:00 KST 개발 의사결정(2026-09-16)에 따라 `tools/quality_synthetic_optin_batch.py`의 `consume_synthetic_optin_batch(requests)`를 추가했다. 정렬된 합성 request 목록을 받아 기존 `decide_synthetic_optin_plan`을 요소마다 한 번씩 호출하고 개별 plan을 변경 없이 입력 순서(중복 포함)로 반환하며, `observation_eligibility == "eligible"`과 `cancellation_eligibility == "eligible"`만 각각 독립적으로 센 `observation_eligible_total`/`cancellation_eligible_total`을 함께 제공한다. 빈 입력은 빈 결과와 0 totals를 내고, non-object 요소를 포함한 모든 요소 결과는 직접 평가와 동일하며(`error_count` 포함), 평가자 예외는 변환 없이 전파된다. 이 batch consumer는 ambient clock·I/O·persistent state 없이 반복 평가 결과가 동일한 순수 함수이고 별도 CLI·공개 인터페이스를 추가하지 않으며 eval-quality/doctor_run_directory/run finalization/`make verify`가 호출하지 않는다. 입력 경계는 순서 보존 iterable만 수용한다: mapping, unordered iterable(`set`/`frozenset`), scalar text/byte sequence(`str`/`bytes`/`bytearray`/`memoryview`), non-iterable scalar(`None`/`int`/`float`/`bool` 등)는 evaluator 실행 전 동일한 contract `TypeError`로 거부되며, 거부된 입력에는 evaluator가 실행되지 않는다. batch totals는 합성 회귀용 일관성 통계일 뿐 실행 승인이 아니며 관측·취소·backend 종료·rollback의 증거가 아니다.
- 테스트: `python3 -m unittest tests.test_quality_synthetic_optin_plan`가 opt-in 누락/거짓/모순, 조건 누락·미지, 관측-취소 분리, identity 불일치/재사용 target/만료, 실행 시점 재검증, 예산 비유한, deterministic 재계산과 CLI 동작, `eval_quality.py`/`doctor_run_directory.py`/`Makefile` 미연결을 결정론적으로 검증한다. 자연 완료 vs timeout race, 취소 응답 vs 종료 확인, budget 소진은 `tests/test_quality_timeout_state_decision.py`로 결정론적으로 검증한다.
- next runtime integration requirements: 이 계획을 실행으로 승격하려면 same-session identity capture, explicit permission을 가진 별도 observer connection, 실행 시점 재검증, target/timeout race 판정, 유한 관측/취소 budget 시행, rollback/transaction cleanup에 대한 실제 DB 증거가 먼저 필요하다. 그 전까지 관측·취소를 실행하지 않는다. Issue A real labeled corpus/qrels와 Issue B old/new artifact·upgrade SQL은 계속 blocked/separate다.

### Issue A Blocker

2026-09-14 기준 이 저장소와 접근 가능한 로컬 개발 환경에는 real corpus input manifest 계약을 만족하는 실제 labeled WARC corpus와 독립 query/qrels가 확인되지 않았다. 로컬 개발 컨테이너 DB에는 이전 smoke에서 남은 WARC record 4건과 synthetic `bm25_doc` 4건만 있고, 파일시스템에는 `*.warc`/`qrels` corpus가 없으며 이번 회차에 원격 corpus 위치도 주어지지 않았다. 따라서 real baseline hit-rate/MRR은 미측정(blocked)이며 synthetic/public smoke로 대체하지 않는다.

Real baseline 재개에 필요한 입력:

1. 사용 권한이 명확한 실제 WARC corpus(들). source별 bytes/SHA-256과 `license`/`license_url`/`redistribution`을 manifest에 기록할 수 있어야 한다.
2. corpus를 index한 문서와 독립적으로 작성된 20-50개 relevance query와 `expected_doc_ids` label. self-label이나 pipeline 자동 label은 허용하지 않는다.
3. 평가 시점의 git commit, PostgreSQL 버전, `hybrid_warc_search()` 검색 설정을 기록할 run 환경.
4. 위 입력으로 `make doctor-real-corpus-input` 통과 후 `QUALITY_BASELINE_REQUIRE_REAL_CORPUS=1` finalization과 0보다 큰 hit-rate/MRR threshold를 고정.

## Read-only quality input preflight

Before a real baseline, run make preflight-quality-inputs with explicit corpus, query, qrels, and optional records/provenance paths. The report records bytes and SHA-256 for readable files, reuses the existing query-label and records reference checks, and never starts PostgreSQL, imports WARC, downloads inputs, or measures quality.

Missing inputs or provenance are blocked/non-zero; qrels independence, label authorship, corpus authenticity, and representativeness remain unresolved. A valid preflight is not a real baseline.

When `QUALITY_INPUT_PREFLIGHT_MANIFEST_JSON` is supplied, the preflight reuses `tools/doctor_real_corpus_input.py` with its existing 20-50 query and external-source contract and preserves that doctor report in the combined JSON/Markdown output. The manifest can satisfy the provenance input; omitting it leaves `real_corpus_manifest_check` unresolved. This connection remains read-only and does not turn input readiness into quality measurement.
`finalize-quality-run` invokes this preflight before `stage-quality-provenance`, fixture reporting, `eval-quality`, baseline doctor, and final run-directory completion whenever `QUALITY_INPUT_PREFLIGHT_REQUIRED=1` or `QUALITY_BASELINE_REQUIRE_REAL_CORPUS=1`. A failed gate exits non-zero after writing the preflight diagnostic, without writing completion artifacts. Synthetic/public finalization remains compatible when the gate is not requested.

## Live PostgreSQL Smoke

2026-09-13 11:02 KST에 Phase 72 커밋 후 현재 개발 컨테이너에서 live smoke를 재실행했다. `make test`, `make test-warc-db`, `make test-regress`가 모두 통과했고, pg_regress는 `setup`, `functions`, `search` 3/3을 통과했다. 이는 현재 DB/extension/import/search 경로가 건강하다는 증거이며, synthetic/public fixture 점수나 실제 labeled WARC corpus baseline을 대체하지 않는다.

2026-09-12 19:31 KST에 개발 컨테이너에서 실제 PostgreSQL 16 경로를 검증했다. `make up`이 `pgwarc_lance:latest` 이미지를 새로 빌드했고 컨테이너 health check가 `healthy`가 된 뒤 다음 명령을 실행했다.

```text
make test          # passed: extension, BM25, Lance, hybrid SQL checks
make test-warc-db  # passed: db_warc_importer: ok
make test-regress  # passed: setup, functions, search (3/3)
```

`test-regress`는 확장을 재설치하고 별도 pgrx PostgreSQL 16 인스턴스(port 28816)를 초기화한 뒤 회귀 suite를 실행한다. 이번 실행은 모든 회귀 테스트가 통과했으므로, 컨테이너 기동·확장 설치·WARC importer SQL·기본 검색 경로에 대한 라이브 smoke 증거를 확보했다. 이 결과는 synthetic/public fixture의 품질 점수나 실제 라벨 코퍼스 기준선을 대체하지 않으며, Issue A의 남은 작업은 실제 WARC corpus와 20–50개 query의 provenance/label coverage 및 양수 threshold를 고정하는 것이다.

## Public WARC Candidate

반복 가능한 공개 smoke sample 후보는 `webrecorder/pywb`의 sample archive다.

| file | raw URL | checked size |
|---|---|---:|
| `example.warc.gz` | `https://raw.githubusercontent.com/webrecorder/pywb/main/sample_archive/warcs/example.warc.gz` | 3,484 bytes |
| `example.warc` | `https://raw.githubusercontent.com/webrecorder/pywb/main/sample_archive/warcs/example.warc` | 5,629 bytes |

GitHub source page:

https://github.com/webrecorder/pywb/blob/main/sample_archive/warcs/example.warc.gz

Manual parser/import smoke:

```bash
make smoke-public-warc

curl -L -o /tmp/pywb-example.warc.gz \
    https://raw.githubusercontent.com/webrecorder/pywb/main/sample_archive/warcs/example.warc.gz

python3 tools/warc_importer.py /tmp/pywb-example.warc.gz \
    --execute \
    --lance-uri /tmp/pywb-example.lance \
    --vector-dim 4 \
    --lance-mode overwrite \
    --summary
```

Labeled quality smoke:

```bash
PUBLIC_WARC_QUALITY_FIXTURE=/tmp/pywb-public.fixture.json make public-warc-quality-fixture
QUALITY_FIXTURE=/tmp/pywb-public.fixture.json make eval-quality

make eval-public-warc-quality

BENCH_OUT_DIR=benchmarks/local make finalize-public-warc-quality-run
BENCH_OUT_DIR=benchmarks/local QUALITY_BASELINE_MIN_HIT_RATE=1 QUALITY_BASELINE_MIN_MRR=1 make doctor-quality-baseline
```

`tools/make_public_warc_quality_fixture.py`는 public WARC sample에서 label text를 포함한 record를 relevant doc으로 label하고, deterministic hash vector를 문서 vector로 넣은 external fixture를 만든다. Query vector는 relevant document vector의 평균이다. 기본 CLI 인자는 기존처럼 한 query를 만들지만, 여러 labeled query를 만들려면 `--query-spec-json`에 다음과 같은 JSON 배열을 전달한다.

```json
[
  {"name": "example_domain", "query": "Example Domain", "label_text": "Example Domain"},
  {"name": "other_page", "query": "Other Page", "label_text": "Other Page"}
]
```

각 `name`은 유일해야 하며, 각 `label_text`는 최소 한 record와 일치해야 한다. Make target에서는 `PUBLIC_WARC_QUERY_SPEC_JSON=queries.json make public-warc-quality-fixture`로 같은 계약을 사용한다. 이 sample은 parser/replay와 quality pipeline smoke에는 적합하지만 corpus 크기와 query 수가 작기 때문에 `make eval-quality`의 기본 품질 기준선은 synthetic fixture로 유지한다.

여러 WARC 입력을 하나의 fixture로 합치려면 `--source-spec-json`에 다음처럼 path 또는 URL 배열을 넘긴다. 각 항목은 path와 URL 중 정확히 하나만 가져야 한다. URL만 지정한 입력은 임시 WARC 파일로 내려받고, 기존 로컬 path가 있는 입력은 그대로 읽는다.

```json
[
  {"path": "fixtures/first.warc"},
  {"url": "https://example.test/second.warc.gz"}
]
```

각 source spec은 중복이나 path+URL 동시 지정을 허용하지 않으며, 여러 입력이 같은 `doc_id`를 만들면 fixture 생성이 실패한다. `PUBLIC_WARC_SOURCE_SPEC_JSON=sources.json make public-warc-quality-fixture`로 Make target에서도 같은 계약을 사용한다.

재현성을 위해 `--manifest-json`을 지정하면 provenance manifest를 만든다. 단일 입력의 schema v1은 기존 `source` object를 유지하고, `--source-spec-json`을 사용한 schema v2는 source spec 파일의 경로/SHA-256/실제 spec과 각 source의 `kind`/URL/경로/bytes/SHA-256/`downloaded_at`을 `sources` 목록에 기록한다. 각 source에는 전체 WARC record 수, fixture에 들어간 imported record 수, 비어 있거나 최소 text 길이에서 제외된 skipped record 수를 `records`로, query label 기준 doc/labeled/unlabeled 수를 `coverage`로 기록한다. 두 schema 모두 query spec 파일 경로/SHA-256/실제 spec과 fixture의 doc/query/vector 요약을 남긴다. Make target에서는 `PUBLIC_WARC_PROVENANCE_JSON=warc_provenance.json make public-warc-quality-fixture`로 지정하며, `finalize-public-warc-quality-run`은 run directory에서 기본 경로를 자동으로 연결한다.

`make finalize-public-warc-quality-run`은 public fixture 생성, `records.jsonl`, `vectors.jsonl`, `quality_queries.json` 중간 산출물 저장, quality fixture report, DB eval, run directory finalization을 한 번에 실행한다. 외부 네트워크와 GitHub raw URL에 의존하므로 `make verify`에는 포함하지 않는다.

Public WARC quality smoke baseline from 2026-07-05:

```text
PUBLIC_WARC_QUALITY_FIXTURE=/tmp/pglance_pywb_public.fixture.json make public-warc-quality-fixture
QUALITY_FIXTURE=/tmp/pglance_pywb_public.fixture.json make eval-quality

wrote /tmp/pglance_pywb_public.fixture.json docs=2 queries=1 expected_doc_ids=[2279161322381067537]
hit_rate_at_3=1.000 mrr_at_3=1.000
```

## Eval Command

```bash
make eval-quality

BENCH_OUT_DIR=/tmp/pglance_quality make eval-quality

QUALITY_FIXTURE=tests/fixtures/quality_fixture.json make eval-quality
```

Baseline:

```text
hit_rate_at_3=1.000 mrr_at_3=1.000
```

| query | expected | returned | hit@3 | reciprocal_rank |
|---|---|---|---:|---:|
| korean_bm25 | [1001] | [1001, 1002, 1003] | 1 | 1.000 |
| vector_embeddings | [1002] | [1002, 1001, 1003] | 1 | 1.000 |
| warc_metadata | [1003] | [1003, 1001, 1002] | 1 | 1.000 |

External fixture smoke:

```text
QUALITY_FIXTURE=tests/fixtures/quality_fixture.json make eval-quality

hit_rate_at_3=1.000 mrr_at_3=1.000
```

## Embedding Command Example

`tools/embed_records_openai.py` reads importer record JSONL from stdin and writes vector JSONL compatible with `--embedding-command`.

```bash
python3 tools/warc_importer.py sample.warc.gz \
    --execute \
    --lance-uri /tmp/warc.lance \
    --vector-dim 1536 \
    --embedding-command 'python3 tools/embed_records_openai.py --model text-embedding-3-small --expect-dim 1536'
```

The command uses an OpenAI-compatible `POST /v1/embeddings` request with `input`, `model`, and float embeddings in `data[].embedding`, matching the official API reference:

https://developers.openai.com/api/reference/resources/embeddings/methods/create

Use `--url` for compatible local gateways, `--api-key-env` for a non-default key environment variable, and `--no-auth` only for trusted local endpoints.

## Tokenizer And BM25 Candidates

- Stopwords: defer. Add only after the quality fixture includes false positives that stopwords explain.
- ASCII stemming: defer. Prefer importer-side preprocessing experiments before changing extension tokenization.
- CJK policy: keep CJK bigrams as the stable baseline; add query/document fixtures before considering unigram or dictionary segmentation.
- Normalization: likely next low-risk candidate is consistent Unicode/case normalization in tokenizer tests.

## RRF And Weighting Decision

Keep `hybrid_search()` and `hybrid_warc_search()` fixed-parameter for now. If quality evals show systematic BM25/vector imbalance, add a new options-bearing function instead of changing the existing signatures.
