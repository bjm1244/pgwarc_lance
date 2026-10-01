# Security Policy

`pgwarc_lance`는 **연구용 프로토타입**이다. 아래 제약을 이해하지 않고 신뢰 경계가 있는 환경에 설치하지 마라.

## 지원 버전

보안 수정은 최신 `master`에만 반영한다. PostgreSQL 13–17 빌드를 지원하지만, 각 major의 보안 패치 보증을 제공하지 않는다.

## 알려진 보안 제약 (현재 코드 기준)

1. **확장은 `superuser = true`, `trusted = false`로 설치된다.** `CREATE EXTENSION`에 슈퍼유저 권한이 필요하다. 일반 사용자에게 확장 소유권을 넘기지 마라.
2. **파일 접근은 PostgreSQL 서버 프로세스의 OS 권한으로 동작한다.** SQL 호출자의 OS 권한이 아니다. 새 설치는 모든 확장 함수의 `PUBLIC EXECUTE`를 회수한다. 관리자는 신뢰한 역할에 필요한 함수와 테이블 권한만 개별적으로 부여하라. 기존 0.1.0 설치에는 전체 upgrade SQL을 포함한 0.1.5 artifact를 설치하고, `sql/pgwarc_lance-restrict-access.sql`을 업그레이드 바깥에서 먼저 실행한 뒤 `ALTER EXTENSION pgwarc_lance UPDATE TO '0.1.5';`을 적용하라. 설치에 슈퍼유저가 필요하다는 사실만으로 이후 함수 호출이 제한되지는 않는다.
3. **경로 제한은 관리자가 설정한다.** `pgwarc_lance.allowed_uri_prefix`는 실제 존재하는 로컬 디렉토리다. 설정하면 경로 구성요소 경계와 심볼릭 링크를 확인하고, `..`, 상대 경로, 원격 URI, percent encoding, 깨진 심볼릭 링크를 거부한다. 파일 접근·쓰기 잠금·로그에는 확인된 실제 경로를 사용한다. 비어 있으면 신뢰한 역할의 URI를 제한하지 않는다. 경로 검사와 파일 접근 사이에 OS 사용자가 디렉토리나 심볼릭 링크를 교체하는 경쟁까지 막지는 못하므로, 데이터 루트와 상위 디렉토리는 신뢰한 OS 사용자만 수정할 수 있게 하라.
4. **자원 제한은 관리자만 변경한다.** `pgwarc_lance.max_scan_rows`, `allowed_uri_prefix`, `allow_destructive_ops`는 슈퍼유저 설정이다. `max_scan_rows = 0`은 무제한이므로 필요에 맞는 양수 상한을 설정하라.
5. **`overwrite = true`는 되돌릴 수 없다.** `lance_create_table(uri, dim, true)`는 기존 Lance 데이터셋을 삭제하고 새로 만든다.
6. **Lance 데이터셋 쓰기는 PostgreSQL 트랜잭션 밖에서 수행된다.** `ROLLBACK`으로 되돌려지지 않으며, 동일 URI의 쓰기는 트랜잭션 단위 advisory lock으로 직렬화한다. 경로 제한을 설정하면 실제 로컬 경로를 잠금 키로 사용한다. 제한이 비어 있을 때 서로 다른 URI로 같은 데이터셋을 가리키는 경우와 외부 writer는 이 잠금으로 조정되지 않는다. 인덱스 정합성은 애플리케이션이 보장해야 한다. (`src/lance_store.rs`)
7. **대용량 스캔은 취소가 즉시 반영되지 않는다.** Lance future 대기는 약 50ms 주기로 PostgreSQL 인터럽트를 확인하고 스캔은 배치 경계에서도 확인한다. 취소된 쓰기가 이미 Lance에 반영됐을 수 있으므로, 취소는 파일 변경의 롤백을 보장하지 않는다.

SQL 쓰기 로그는 파일 변경 전에 INSERT한다. 로그 테이블·sequence 권한 오류는
Lance를 변경하기 전에 실패하고, 복구(`restore`)도 기록한다. 다만 이후 SQL
ROLLBACK·세션 종료·파일 변경 직후 오류는 Lance 변경을 되돌리지 못한다.

동일 BM25 문서의 동시 재색인은 문서 행을 먼저 upsert해 잠근 뒤 posting을
교체한다. 신뢰한 writer끼리도 같은 문서를 동시에 수정할 수 있으므로 실제 DB
동시성 회귀 검증을 운영 배포의 선행 조건으로 둔다.

## 아카이브 입력 한도

Python importer는 WARC 헤더를 64 KiB, 레코드 payload와 압축 해제된 HTTP 본문을
각각 기본 64 MiB로 제한한다. `--max-record-bytes`로 양수 한도를 지정할 수 있다.
CLI는 레코드·중복 ID·벡터를 임시 SQLite에 보관하고 SQL을 디스크에 생성한 뒤
실행한다. 뒤쪽 레코드/벡터 검증 실패는 SQL 실행 전에 발생한다. `--batch-size`와
`--batch-max-bytes`(기본 16 MiB 입력 가중치)로 배치를 제한한다. 단일 큰 레코드는
레코드 한도까지 허용되며, SQL·posting 생성과 Python 객체의 추가 메모리가 필요하므로
바이트 설정이 프로세스 RSS의 정확한 상한은 아니다. `--temp-dir`에는 충분한 디스크
용량이 필요하고 중단된 프로세스의 임시 파일은 운영자가 정리해야 할 수 있다.
임베딩 명령은 기본 600초 제한을 적용한다(`--embedding-timeout`). 명령 자체가
전체 입력을 메모리에 적재하는 경우는 importer가 통제하지 못한다. 라이브러리의
`load_import_records()`와 `emit_sql()` API는 작은 입력용 메모리 방식도 유지한다.
PostgreSQL/Lance 변경은 실행 이후에는 별도의 복구 계약을 따른다.

## 개발 환경

Compose는 폐기 가능한 개발 DB다. `PGWARC_DEV_PASSWORD`를 지정해야 시작하며,
기본 포트는 `127.0.0.1:55432`에만 바인딩된다. `.env` 파일은 Git에서 제외한다.
Compose의 `DEV_PERMISSIONS=1`은 테스트를 위해 빌드/설치 디렉토리를 쓰기 가능하게
만드므로 운영 이미지에는 사용하지 마라. Dockerfile 자체 기본값은 `0`이다.

## 의존성 보안 검사

0.1.2 후보의 2026-10-01 RustSec 검사에서 알려진 취약점 3건과 unsound 경고가
발견됐다. 그 바이너리는 운영에 사용하지 마라. 0.1.3은 `crossbeam-epoch` 0.9.20,
`h2` 0.4.16, `rustls` 0.23.45 (`rustls-webpki` 0.103.15), `event-listener` 5.4.2로
수정하고 yanked 버전 `chacha20` 0.10.1을 0.10.2로 교체한다.
권고는 [RUSTSEC-2026-0204](https://rustsec.org/advisories/RUSTSEC-2026-0204.html),
[RUSTSEC-2026-0258](https://rustsec.org/advisories/RUSTSEC-2026-0258.html),
[RUSTSEC-2026-0285](https://rustsec.org/advisories/RUSTSEC-2026-0285.html),
[RUSTSEC-2026-0221](https://rustsec.org/advisories/RUSTSEC-2026-0221.html)에 있다.

`cargo-audit` 0.22.2로 같은 advisory DB revision
`9b3a3b73a7f42606494c943e95f8196e9994df46`을 사용한 재검사에서 알려진 취약점,
unsound, yanked 결과는 모두 0건이다. 이는 검사 시점의 Cargo.lock 결과이며
소스·네이티브 라이브러리의 안전성이나 향후 권고까지 보장하지 않는다.

CI와 `make audit-dependencies`는 최신 advisory DB를 가져와 알려진 취약점,
unsound, yanked 패키지가 있으면 실패한다. 무시 목록은 사용하지 않는다.
유지보수 중단 경고는 출력에 남는다. `paste` 1.0.15는 Lance/DataFusion의 빌드
매크로이며, `serde_cbor` 0.11.2는 pgrx의 간접 의존성이다. 이를 제거하려면
상위 라이브러리의 호환성 검증이 필요하다. `proc-macro-error2` 2.0.1도
전체 플랫폼 lockfile과 보수적인 metadata 목록에 있다. Linux PG16의
`cargo tree --locked -e normal,build -i proc-macro-error2`에는 활성 경로가 출력되지
않지만 이를 경고 무시의 근거로 사용하지 않는다. 다른 플랫폼이나 feature는 별도로
검사하라. 유지보수 위험은 운영 위험 목록에
남기고 릴리스 전과 정기 점검 시 다시 확인하라.

```bash
cargo install cargo-audit --version 0.22.2 --locked
make audit-dependencies
```

## 제보

보안 취약점은 공개 이슈로 올리지 말고 저장소 관리자에게 비공개로 전달하라(Security Advisory). 포함할 내용:

- 영향받는 커밋/버전과 PostgreSQL major, 빌드 feature(`pg13`–`pg17`)
- 재현 절차(최소 SQL 또는 하네스 명령)
- 영향(임의 파일 접근, 데이터 손실, 서비스 거부 등)

답변 목표: 7일 내 접수 확인, 심각도 평가 후 수정 또는 “수정하지 않음(wontfix)” 결정과 근거를 회신한다. SQL 인자로 임의 경로를 열 수 있다는 설계 자체는 의도된 동작으로 분류되므로, 이에 대한 경고를 문서로 보강하는 방향으로 처리한다.

## 재시도와 문서 ID

0.1.2의 `lance_upsert_many`와 importer `--lance-mode upsert`는 동일 ID의 벡터와
label을 교체한다. 배치 내 중복 ID와 해당 기존 ID의 중복 행은 변경 전에 거부한다.
함수 호출과 로그/sequence에 대한 개별 권한이 필요하다. `append`는 계속 중복을 허용한다.
WARC importer의 ID 생성 규칙은 바뀌지 않았다. 입력 identity/content가 달라져 새 ID가
생기면 별도 문서로 처리되므로 삭제·버전 관리 정책은 애플리케이션에서 정해야 한다.
강제 종료 후 SQL은 롤백됐어도 Lance 쓰기는 남을 수 있다. 같은 입력을 upsert로
재실행해 SQL/BM25와 Lance를 함께 맞추고, 운영 백업 복구 절차를 따르라.

벡터 쓰기·vector/hybrid 검색 API는 NaN과 ±Infinity 입력을 SQLSTATE 22023으로
거부한다. 쓰기 로그와 Lance 변경 이전에 검사하며 기존 데이터의 비정상 값을
자동 정리하지는 않는다.

## 검색 입력과 중간 메모리

0.1.4부터 BM25/vector/hybrid 검색의 `k`는 최대 10,000이며 BM25/hybrid 질의는
UTF-8 기준 65,536바이트까지 허용한다. 초과하면 SQLSTATE 54000으로 실패한다.
음수 `k`는 계속 22023으로 거부하고 `k=0`은 빈 결과를 반환한다.
BM25는 PostgreSQL에서 점수를 합산하고 정렬한 뒤 상위 `k`행만 Rust로 가져온다.
전체 posting과 전체 문서 점수를 Rust Vec/HashMap으로 복사하던 경로를 제거했다.
관리자는 로그인 역할의 `work_mem`, `temp_file_limit`, `statement_timeout`과 동시
접속 수를 측정해 설정하라. 설정은 프로세스 RSS의 단일 상한이나 SQL/Lance 전체
자원 격리를 보장하지 않는다. 실제 입력 크기·임베딩 차원·Lance 스캔/버전 조회와
쓰기의 자원 계약도 별도 검증해야 한다. [OPERATIONS.md](OPERATIONS.md)를 따르라.

## 동시 적재 중 BM25 통계

0.1.4까지 BM25는 문서 수, 평균 길이, posting을 별도 SPI 문장에서 읽었다.
앞선 SQL 쓰기로 XID가 할당된 트랜잭션에서는 동시 커밋 사이에 서로 다른
snapshot을 읽어 점수를 왜곡할 수 있다. 폐기용 DB에서 1,000/4,000 문서의
원자적 교체 120회와 검색 400회를 겹쳐 잘못된 양수/음수 점수를 재현했다.
0.1.5는 통계와 posting을 한 SQL 문장에서 읽도록 수정한다. 이 변경의 검증은
[PRODUCTION.md](PRODUCTION.md)에 기록한다. SQL과 Lance 사이의 원자성을
제공하는 변경은 아니다.
