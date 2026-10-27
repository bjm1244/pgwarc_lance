# Development log

## 2026-10-27 — audit BM25 postings against stored WARC content

- 선택 이유: 직전 `.18`/`.19` write 비교에서 보존된 `.18` database clone이 현재 importer
  재계산보다 posting과 `sum(tf)`가 각각 18 작았다. `.19` index 비용과 무관하다고
  단정하지 않고, 실제 PostgreSQL snapshot에서 본문·term frequencies·posting을 비교할
  재현 가능한 read-only 검사가 필요했다. mismatch 때 자동 repair하지 않는 경계를 함께
  구현했다.
- 실제 입력은 이전 검증과 같은 Common Crawl WARC SHA-256
  `4b6b55dc25afdb8ef9bb0e48213faa73cf07756f195b0863ba2693638bf784f8`이며, WARC-backed
  21,873개 문서의 doc/content fingerprint는
  `59f5ce9b52f02c24838363364f5412ea9504317afa1953915f72d0a4d4159be9`다. 기존 `.18`
  복제본에서 감사기는 posting 8,240,889개 / `sum(tf)` 23,342,041과 본문에서 다시
  계산한 8,240,907개 / 23,342,059를 비교해 정확히 7개 문서 불일치를 보고했다. 기존
  `doc_len`은 posting `sum(tf)`와 일치했지만, 그 7건의 본문 기반 기대 `doc_len`과는
  달랐다.
- 불일치 문서 7건만 동일 `.18` native artifact로 전용 복제본에서 재색인한 뒤 감사기를
  다시 실행했다. PostgreSQL 16.15의 `REPEATABLE READ READ ONLY` 검사에서
  21,873개 모두 일치했고, posting 8,240,907개, `sum(tf)`와 `sum(doc_len)` 모두
  23,342,059, 문서 길이/본문 또는 posting mismatch 0, unverifiable 0이었다. 이는
  보존된 원본 DB가 아니라 disposable clone의 결과다. 불일치가 생긴 역사적 원인은
  확인되지 않았고 특정 migration/index 변경으로 귀속하지 않는다.
- `tools/audit_bm25_index.py`는 현재 `warc_importer` tokenizer와 긴 term storage key를
  사용해 한 read-only snapshot의 저장 본문/posting을 비교하고 JSON summary만 남긴다.
  본문과 term 값은 출력하지 않으며 mismatch/unverifiable document ID는 제한된 수만
  보고한다. 전체 범위 또는 `--warc-only`, `PGWARC_PSQL`, exit status 0/1/2를 지원하고
  어떤 수정·재색인도 하지 않는다. full scan과 Python tokenization은 I/O 및 CPU를 쓰고
  긴 snapshot을 유지하므로 운영 문서에 실행 시점·비공개 보고서 취급을 적었다.
  `--output-json`은 report를 mode 0600, 새 디렉터리는 0700으로 저장하고 stdout에는 ID
  없는 집계만 출력한다. 감사는
  tokenizer/posting 일치만 검사하며 charset 정확성, 추출 품질, Lance/vector 상태,
  qrels 기반 relevance를 입증하지 않는다.
- 검증: `python3 -B -m unittest tests.test_audit_bm25_index tests.test_warc_importer`
  89개, `python3 -B -m unittest discover -s tests -p 'test_*.py'` 466개,
  `make check-docs`, `py_compile`, `git diff --check`가 통과했다. 실 DB에서 최종 코드의
  전량 재색인 전 clone 감사는 exit 2와 7개 mismatch였고, 같은 `.18` 바이너리로
  불일치 문서를 재색인한 별도 clone은 exit 0이었다. 보고서 저장 mode 0600 및 stdout
  ID redaction은 실 DB 명령과 단위 검증에서 확인했다. 상세 명령, PG16.15 환경, private
  container/report, 실패 로그는 비공개 run evidence에 남겼다.
- 조사 실패/한계: 첫 private CSV reader는 기본 131,072-byte field 제한에 걸렸고,
  준비 helper의 token descriptor 버그는 private helper에서 수정했다. 첫 native
  reindex 시도는 base image의 다른 바이너리로 B-tree index row 크기 오류가 났으며
  transaction rollback을 확인했다. 그 뒤 보존된 `.18` 바이너리를 clone에 넣고 재시도해
  통과했다. PostgreSQL ARE는 `{2001,}` 반복 횟수를 거부해 그 탐색식은 버렸다. 감사
  코드의 실제 smoke 첫 시도도 사용하지 않는 CSV NULL 상수 참조로 시작 전에 실패했으며,
  field-present flag 처리로 고친 최종 코드의 전량 PG16 검사를 완료했다. 원인분석의
  중간 텍스트/문서 ID는 공개하지 않는다.
- native 입력(src/SQL/Cargo/vendor/control/Dockerfile)은 바꾸지 않았고 기존 native
  receipt는 수정하지 않았다. WARC, 검증 원본 DB, versioned artifacts는 읽기 전용으로
  두고 전용 clone만 사용했다. 이 도구는 수동 검토를 돕는 개발 기능이지 repair 절차나
  production 승인 근거가 아니다. 다음 과제는 mismatch 7건의 과거 색인 경위를 좁히고,
  실제 WARC import에서 잘못된 charset 및 unsupported Office 본문을 하나씩 안전하게
  처리하는 것이다.

## 2026-10-27 — measure 0.1.19 term-frequency index write cost

- 시작점은 clean `development` commit
  `aa6b5d4690017a130287296290955b7281aea660`이다. 이 직렬 실행 중 같은
  checkout의 다른 pglance writer는 확인되지 않았다. `.codex/config.toml`의
  `gpt-6-luna` / `max`를 유지했다. 선택 이유는 직전 로그의 다음 과제였던
  0.1.19 인덱스 실제 적재 비용을 0.1.18과 분리된 fresh PG16 clone에서 비교하고
  유지 여부를 결정하기 위해서다. 이번 단위에서는 native 코드를 변경하지 않았다.
- 입력은 read-only Common Crawl WARC SHA-256
  `4b6b55dc25afdb8ef9bb0e48213faa73cf07756f195b0863ba2693638bf784f8`의 실제 데이터다.
  WARC row가 있는 21,873개 문서를 사용했고 입력 본문은 186,924,823 bytes,
  doc-ID/length/content fingerprint는
  `59f5ce9b52f02c24838363364f5412ea9504317afa1953915f72d0a4d4159be9`이다.
  이전 write probe 64행은 WARC record가 없어 제외했다. `tools.warc_importer.iter_sql_chunks`
  로 BULK와 COPY SQL을 한 번씩 생성해 버전 쌍에 재사용했다. BULK SQL SHA-256은
  `a49c8ec8411f2952936830df60787c0d0c91129b16c02c5caa50a8cfa0f73f2ef`, COPY는
  `2ce8bdc9324420d4acbfd669500ef2c6ede8357fd08501156d83acf376d2fc4c`다.
- `python3 -B run_import_arm.py VERSION MODE` private runner로 `.18 bulk`, `.19 bulk`,
  `.19 copy`, `.18 copy` 순서의 네 arm을 실행했다. 각기 새 PostgreSQL 16.15 DB와
  network-none container를 사용했고 제한은 4 CPU/16 GB였다. `fsync`,
  `full_page_writes`, `synchronous_commit`은 켰다. `shared_buffers=128MB`,
  `work_mem=4MB`, `effective_cache_size=4GB`, parallel workers 0, checkpoint timeout
  1h, max WAL 16GB, `track_io_timing=on`으로 맞췄다. 각 arm에서 import와 명시적
  `CHECKPOINT`를 따로 재고, cgroup CPU/WAL/relation size/posting hash/고정 8개
  BM25 질의 fingerprint를 기록했다. PostgreSQL 기본 autovacuum은 실행 중이었다.
- 결과:

  | 입력 | import 초 `.18` → `.19` | cgroup CPU 초 `.18` → `.19` | CPU 증감 | WAL GB `.18` → `.19` | WAL 증감 |
  | --- | ---: | ---: | ---: | ---: | ---: |
  | BULK | 735.953 → 719.881 | 233.352 → 282.426 | +21.0% | 2.605 → 3.213 | +23.3% |
  | COPY | 342.855 → 498.602 | 138.688 → 201.898 | +45.6% | 1.824 → 2.432 | +33.3% |

  `.19` frequency index는 116,023,296 bytes(110.6 MiB)였다. BULK final checkpoint는
  `.18/.19` 23.118/34.227초, COPY는 59.440/38.776초였다. `.19` COPY elapsed는
  45.4% 길었지만 BULK elapsed는 CPU 증가에도 2.2% 짧았다. 1분 host load average가
  arm 사이 3.10–16.23으로 변해 wall time 차이는 확정적이지 않다.
- 네 arm 모두 21,873 docs, 8,240,907 postings, sum(tf)=23,342,059, 21,873 WARC
  rows를 만들었다. posting-stream SHA-256
  `f63f163c5b3d735e4a241a6f4abe4896ed8d2ea8ea5185a589ec60dd1999efea`와 여덟 query
  fingerprints가 전부 일치했다. 이는 계산 결과 일치이지 relevance 검증은 아니다.
  보존된 원본 `.18` DB는 8,240,889 postings / sum(tf)=23,342,041이므로 현재
  importer 출력이 posting 18개, tf 18만큼 다르다. 원인은 이 회차에서 규명하지
  않았고 인덱스 영향으로 단정하지 않는다.
- 결정: .19는 development candidate로 유지한다. q6 count scan의 shared hit 16,429→61
  감소는 직접 관측됐고 결과 fingerprints는 같지만, 실제 write CPU/WAL/space 비용도
  높다. workload mix와 ingest SLA가 없으므로 production 승인 근거는 아니다. 새 raw-WARC
  parsing 전체, Lance/vector write, independent qrels, cold/sustained SLA는 미검증이다.
- 준비 실패와 한계: 첫 SQL 생성은 실제 큰 필드가 csv 기본 131,072-byte cap을 넘어
  멈췄다. 비공개 generator cap을 2,147,483,647로 올려 full SQL을 다시 생성했다.
  첫 posting hash는 COPY delimiter를 잘못 줘 실패해 기본 tab으로 다시 계산했다.
  없는 frequency index의 relation size 조회도 regclass 오류 후 `to_regclass`로
  재실행했다. 네 arm 결과/명시적 checkpoint/검증 완료 후 container stop은 모두
  exit 137, OOM=false였다. 지표 캡처는 stop 이전에 끝났으나 graceful shutdown은
  검증하지 않았다. WARC, 원본 DB, versioned artifact는 수정하지 않았다.
- 문서 변경은 이 비교의 실측값·checksum·제한을 `PUBLICATION.md`와 `PRODUCTION.md`에
  반영한다. Python/SQL/native 코드는 변경하지 않아 native receipt는 수정하지 않았다.
  다음은 +18 posting 차이의 source/tokenizer 원인을 실제 import 경로에서 확인하고,
  이어서 charset/PDF-office/긴 토큰 실패를 하나씩 해결한다. 로컬 검증 후 development에
  docs-only commit하며 직접 push하지 않는다. 공개 동기화는 회차 후 authorized publisher
  결과를 확인한다.

## 2026-10-27 — consolidate, repair publication and remediate current zstd advisories

- 완료된 0.1.19 계보와 별도 PDF 작업을 통합했다. 기본 fail 정책을 유지하면서 명시적
  metadata-only 보존, signature 감지, 본문 없는 문서의 hash/외부 벡터 차단을 함께
  검증했다. 오류 상세는 16개×512자로 제한하고 나머지 생략 건수를 남긴다.
- 최신 RustSec DB의 새 zstd 취약점 3건과 unsound 10건을 확인했다. Arrow IPC와
  Lance encoding의 두 Cargo manifest만 고쳐 fixed zstd 0.14.3 / zstd-safe 8.2.0 /
  zstd-sys 2.1.2를 잠갔다. upstream source와 라이선스 출처·원 archive checksum을
  vendor/README.md에 보존했다. 같은 DB 재검사 결과 취약점·unsound·yanked 0건,
  미관리 경고 3건이다. version은 0.1.20, migration은 no-DDL이다.
- PG16.15/Rust1.96 locked offline release build 28m08s, 신규 설치, SQL smoke,
  SQL 회귀 4개, importer DB suite, runtime-safety 18개,
  populated-upgrade/backup-restore 15개가 통과했다. .19 실제 바이너리에서 기준값을
  얻은 별도 Common Crawl clone을 .20으로 재시작·업그레이드했다. 문서 21,873,
  posting 8,240,889, WARC 21,873, E5 vector 21,825를 유지했고 BM25/vector/hybrid
  24개 query fingerprint가 모두 같았다. 수학적 결과 비교이며 relevance/SLA는 아니다.
- 실제 PDF 221건·DOCX 3건을 통합 CLI로 적재했다. 문서 224, 본문 184,
  metadata-only 40, posting 119,837, hash vector 184, text-length mismatch 0이다.
  single-term self-hit도 통과했다. 원본 checksum은 기존
  `4b6b55dc25afdb8ef9bb0e48213faa73cf07756f195b0863ba2693638bf784f8`과 같다.
  source text/SQL/vector는 공개하지 않는다. 새 Arrow IPC/zstd 경로는 null/Unicode를
  포함한 compressed round-trip 32회를 통과했다. Python 전체 456개, 문서 검사와
  Rust fmt도 통과했다.
- 검증 환경 수정: source-only build 사본에서 Git metadata 시험 1개가 실패해 실제
  Git checkout에서 전체 suite를 검사하도록 했다. 첫 문서 DB 시험은 확장을 설치하지
  않아 실패했고, 해당 전용 DB를 재생성·설치한 뒤 해당 단계를 완주했다. 원 실패 로그를
  보존했다. vendor의 기존 protobuf whitespace 7줄은 원문 보존을 위해 변경하지 않았다.
- 브랜치 기준을 하나로 모으고 공개용 snapshot 갱신 경로를 연결했다. controller의
  비공개 이력은 공개 main에 merge/push하지 않는다. 게시 전 tracked source 검사,
  회귀·현재 보안 검사와 실제 native fingerprint 검증을 요구한다. 검증되지 않은
  native 변경은 게시를 차단한다. 실행/완료 추적과 자격증명은 공개 소스 밖에서 운영한다.
- 미해결: 이 builder의 cargo-pgrx GLIBC 호환 문제 때문에 공식 package SQL 생성은
  재검증하지 못했다. 변경 없는 .19 generated SQL과 no-DDL migration으로 실제
  설치·업그레이드는 통과했다. 두 package primary license text, 운영 qrels/SLA,
  전체 fresh raw-WARC 및 host power-loss 검증도 남아 있다. 바이너리 릴리스/운영
  승인을 하지 않는다. 다음은 .19 인덱스의 통제된 실제 적재/쓰기 비용 비교다.

## 2026-10-27 — integrate completed importer branches

- Consolidated the completed 0.1.19 BM25, bounded PDF/DOCX and record-scoped
  charset work. The separate PDF branch was reviewed against that line; its
  metadata-retention behavior was integrated while keeping the bounded,
  process-group-aware extractor. Older overlapping trials remain historical.
- Added explicit `--document-error-policy metadata-only`. The default remains
  `fail`; only configured PDF/DOCX extraction failures can retain empty-text
  metadata. Missing extractors, HTTP decoding/size and charset failures still
  stop before SQL. Failed documents receive no placeholder or supplied vector;
  embedding commands receive only text-bearing documents. PDF signatures are
  detected after HTTP decoding, including mislabeled application/octet-stream.
- Full Python suite: 454 tests passed; documentation and whitespace checks
  passed. New cases cover function/bulk/COPY output, precomputed/command
  embeddings, PDF signature detection and the extraction safety boundaries.
  Native source, SQL and Cargo.lock are unchanged from 0.1.19.
- A fresh RustSec scan discovered new zstd advisories. Publication and any binary
  promotion require remediation; historical zero-findings scans are not current
  security approval. Full raw-WARC ingestion, qrels, SLA and license gaps remain.

## 2026-10-27 — source-reviewed record-scoped charset correction

- 시작점: 직전 완료 브랜치 `codex/20261026-bounded-ingest-extraction`의
  `e1f88af657e54b590159f1d715ec58f73d453627`에서 격리 worktree로 이어갔다.
  원 checkout에는 같은 경로를 사용하는 장기 Codex 세션이 있어 수정하지 않았다.
  `.codex/config.toml`의 `gpt-6-luna` / `max`를 그대로 유지했고 SHA-256은
  `9cb045f6981fdb64ce630508f5a91577f7bf2852f6f6cce4ad70632992e24dc1`이다.
- 선택 근거: 직전 기록의 다음 작업은 실제 Common Crawl의 잘못된 charset label을
  검토하는 것이었다. read-only WARC의 SHA-256은
  `4b6b55dc25afdb8ef9bb0e48213faa73cf07756f195b0863ba2693638bf784f8`이다. response
  record 16,350의 HTTP label은 `uft-8`, 내부 HTML 선언은 `utf-8`이며 body 59,582 bytes는
  strict UTF-8 decoding에 실패한다. UTF-8 `errors=replace` 결과에는 U+FFFD 65개가
  관측됐다. 별도 record 675의 `None` label은 기존 strict `--charset-override
  None=utf-8`로 처리할 수 있다. 원문, URL, 벡터는 증거로 복사하지 않았다.
- 변경: importer에 `--charset-record-override WARC-RECORD-ID=CODEC`을 추가했다. 이
  옵션은 지정된 한 WARC record ID에만 `errors=replace`를 적용하고 summary에 설정 ID,
  실제 import record 수, 관측된 U+FFFD 수를 남긴다. 중복 ID, 없는 ID, text/minimum-length
  필터 때문에 import되지 않은 ID, label-wide override와의 중복 적용은 SQL 생성 전에
  실패한다. 기존 label-wide override는 strict decoding을 유지한다. tokenizer, BM25 식,
  schema, native binary, Cargo.lock과 확장 버전은 바꾸지 않았다.
- 실제 적재 검증: Linux x86_64, Python 3.12.15에서 기존 immutable 0.1.19 artifact
  (`pgwarc_lance.so` SHA-256
  `6429e334b3ce69235cf2491cfb04fdc52ef99ab4dfb3668a42fc5db8e2490fd0`)를 network-none
  disposable PostgreSQL 16.6에 설치했다. 원본 WARC에서 두 실제 record만 메모리에서
  선택해 `None=utf-8` strict label override와 ID-scoped `uft-8` override를 importer
  spool/SQL/execute 경로에 넣었다. `bm25_doc` 2행, `warc_record` 2행, 두 record 각각의
  BM25 search hit, Lance write 0건을 확인했다. `uft-8` record의 summary는 observed
  replacement 65개를 기록했다. SQL/text/vector는 저장하지 않았고 disposable container는
  제거했다.
- 검증 명령: `python3 -B -m unittest tests.test_warc_importer` 71개,
  `python3 -B -m unittest discover -s tests -p 'test_*.py'` 448개 통과.
  `python3 -B -m py_compile tools/warc_importer.py tests/test_warc_importer.py`,
  `make check-docs`, `git diff --check`, importer `--help` 확인도 통과했다.
- 실패와 한계: 첫 post-import assertion이 `bm25_search`를 `pgwarc_lance` schema로
  잘못 한정해 psql exit 3을 냈다. importer 실행 자체는 성공했으며, 함수의 실제 설치
  schema에 맞게 assertion을 고쳐 새 disposable DB에서 전체 smoke를 재실행해 통과했다.
  65개 수는 원문에 있던 literal U+FFFD와 decoder replacement를 구분하지 못한다. 이
  loss-tolerant 경로는 텍스트 의미 정확성이나 full raw-WARC import acceptance를 증명하지
  않는다. 나머지 charset labels, unsupported Office, OCR, independent qrels와 운영 SLA도
  미검증이다. Python-only 변경이라 RustSec 및 dependency notice 수집은 다시 실행하지
  않았고, 기존 binary notice 검토 공백은 남아 있다.
- 다음 작업: 0.1.19 BM25 term-frequency index의 실제 import/write 비용을 동일 입력·분리된
  clone에서 .18 baseline과 비교해 유지 여부를 판단한다. 현재 후보는 운영 승인 대상이
  아니다.
- 공개 반영: `publish_pending`. 검토된 publication checkout/remote가 없어 GitHub `main`을
  수정하지 않았다. 구현 commit은 `70ca85c394539f0a371db29c0a1a38319430b414`
  (`feat(import): scope charset correction by WARC record`)이다.

## 2026-10-26 — bounded PDF and DOCX extraction in the importer source

- 시작점·선택 이유: BM25 term-frequency index candidate commit
  `f7c080a5b920af185b44b1ce0e96681b90046741` (0.1.19 development candidate)에서
  격리 worktree를 만들었다. 원 체크아웃에는 여러 장기 Codex 세션이 연결되어 있어
  파일을 수정하지 않았다. `.codex/config.toml`의 `gpt-6-luna` / `max`는 원본과
  동일한 SHA-256 `9cb045f6981fdb64ce630508f5a91577f7bf2852f6f6cce4ad70632992e24dc1`로
  유지했다. BM25 warm p95 재비교는 4-CPU 공유 host load 변동으로 원인 분리가
  반복해서 성립하지 않았다. 앞선 Common Crawl 입력 감사에는 PDF 218건과 DOCX 3건이
  있었고 importer는 전체 fresh ingest를 승인받지 못했으므로, 다음 실제 ingest 문제인
  binary 본문 추출을 선택했다.
- 변경: 기존 bounded PDF/DOCX importer 구현을 0.1.19 source line으로 통합했다.
  `--pdftotext`는 확인된 Poppler executable을 명시해야 하며 기본 record timeout은
  30초다. stdout과 DOCX ZIP 확장/추출 본문은 `--max-record-bytes`(기본 64 MiB)로
  제한된다. `--extract-docx`는 WordprocessingML 본문만 읽고 entry 수, 경로, 암호화,
  DTD/entity, NUL XML, 빈 본문을 검사한다. 두 옵션은 기본 비활성이다. 추출기 누락,
  timeout, 오류, 무텍스트 PDF, 비지원 binary MIME은 실패로 남고 SQL 생성/실행 전에
  전체 입력 spool 검증이 끝나야 한다. 긴 BM25 token의 0.1.16 version guard와 요약
  필드는 함께 보존했다. 병합 중 `format_summary`에서 기존 긴 token 요약과 새
  PDF/DOCX 요약이 충돌해 `git apply --3way`는 exit 1이었다. 양쪽 필드를 모두
  포함하도록 해결했으며 그 뒤 importer 회귀는 통과했다.
- 실제 입력: read-only Common Crawl WARC SHA-256은
  `4b6b55dc25afdb8ef9bb0e48213faa73cf07756f195b0863ba2693638bf784f8`이며 크기
  938,695,574 bytes, 66,190 WARC record였다. 새 경로로 PDF 218건과 DOCX 3건을
  순차 처리했다. Poppler 24.02.0이 PDF 178건에서 2,900,966자를 추출했고 22건은
  무텍스트, 18건은 Poppler 오류였다. PDF 출력에 U+FFFD 9개가 남았다. DOCX 3건은
  13,689자를 추출했고 U+FFFD는 0개였다. 전체 scan은 32.255초였고 timeout 또는
  출력 한도 오류는 없었다. 원문·추출 텍스트·벡터는 보존하지 않고 집계 JSON만
  private run evidence에 저장했다.
- 실제 PG16 경로: 변경하지 않은 immutable 0.1.19 artifact
  (`pgwarc_lance.so` SHA-256
  `6429e334b3ce69235cf2491cfb04fdc52ef99ab4dfb3668a42fc5db8e2490fd0`)와 PostgreSQL
  16.15의 network-none disposable container를 사용했다. fresh extension install 후
  실제 WARC에서 추출한 DOCX 3건과 PDF 1건에 대해 importer `emit_sql` function mode를
  실행했다. `bm25_doc`/`warc_record` 4행, DOCX 3행, PDF 1행이 확인됐고, DOCX/PDF
  본문에서 선택한 term의 BM25 검색 결과가 각각 반환됐다. Lance/vector write는
  없었고 새 SQL이나 본문을 파일에 저장하지 않았다. 검증 뒤 컨테이너를 제거했다.
- 검증 명령: `python3 -B -m unittest discover -s tests -p 'test_warc_importer.py'`
  68개 통과; `python3 -B -m unittest discover -s tests -p 'test_*.py'` 445개 통과;
  `python3 -B -m py_compile tools/warc_importer.py tests/test_warc_importer.py`,
  `make check-docs`, `git diff --check` 통과. PG16.15 fresh `CREATE EXTENSION
  pgwarc_lance`와 실제 source-derived DOCX/PDF SQL/import/search smoke 통과.
  변경은 Python importer와 문서만이며 Cargo.lock, extension binary, SQL schema/version은
  바꾸지 않았다.
- 한계·다음 작업: 이 source candidate는 full raw-WARC import가 아니다. PDF 40건은
  무텍스트/오류이고 9 replacement characters가 남아 OCR·수동 검토와 텍스트 품질
  평가가 필요하다. legacy Office, RTF, EPUB, 나머지 binary MIME은 unsupported이며
  잘못된 charset 전체를 해결하지 않았다. 문서 본문 추출은 relevance qrels를 만들지
  않으며 independent retrieval quality, full import throughput, cold/sustained SLA,
  production approval도 검증하지 않았다. Poppler process memory는 OS/container limit에
  의존하고 Poppler는 vendoring/linking하지 않았다. 다음 우선 작업은 실제 입력의 잘못된
  charset label에 대해 source-level 근거를 검토하고 엄격한 명시 correction 경로를
  검증하는 것이다.
- 공개 반영: `publish_pending`. 검토된 별도 publication checkout/인증 경로가 없어
  GitHub `main`은 갱신하지 않았다. 확장 binary/release 업로드와 운영 배포는 하지 않았다.
- 로컬 구현 commit: `0ae43d9ae911fbc5eecfba4735e8524b7e80924f`
  (`feat(import): add bounded PDF and DOCX extraction`).

## 2026-10-26 — BM25 document-frequency index (0.1.19 development candidate)

- 선택 근거: Common Crawl q6 plan은 25,097 posting 행을 처리하면서 5,490행으로
  추정했다. `frequencies` CTE가 `(term, doc_id) INCLUDE (tf)` 기본키에서
  실제 matched-posting count를 수행해 cold capture에서 16,429 shared-hit
  blocks를 소비했다. temp block은 없어서 spill보다 좁은 count scan의 불필요한
  index-page access가 근거 있는 병목이었다.
- 변경: `bm25_term(term)` 단일 키의 `bm25_term_frequency_idx`를 fresh install과
  `0.1.18→0.1.19` update에 추가했다. setup regression은 인덱스의 단일 term key,
  non-unique/valid/ready 속성과 인덱스→extension 소유 `bm25_term` 테이블의
  자동 의존성을 확인한다. PostgreSQL 16에서 인덱스 자체는 직접 extension dependency가
  아닌 테이블에 대한 automatic dependency를 갖는다. tokenizer, BM25 식, query term
  정렬, 결과 결합과 기본 검색 의미는 변경하지 않았다. Dependency graph는 바뀌지 않았다.
- 입력·격리: 시작 commit `6d6195d28b7389f426ad76107116a6b32ff9045a`에서
  별도 `codex/20261026-bm25-term-frequency-index` worktree를 만들었다. 원 checkout의
  `.codex/config.toml` 값 `gpt-6-luna` / `max`와 SHA-256
  `9cb045f6981fdb64ce630508f5a91577f7bf2852f6f6cce4ad70632992e24dc1`를 그대로
  복사해 사용했다. 같은 checkout에는 장기 실행 Codex 세션이 있었지만 cargo,
  rustc, pg_regress 작업은 실행 중이지 않아 파일 충돌을 피하도록 격리했다.
  Linux x86_64, PostgreSQL 16.15, Rust 1.96.0, pgrx 0.19.1, 4 CPU/16 GiB,
  network-none, `shared_buffers=128MB`, `work_mem=4MB`, JIT/`track_io_timing`/fsync
  enabled 조건을 썼다. 고정 WARC SHA-256은
  `4b6b55dc25afdb8ef9bb0e48213faa73cf07756f195b0863ba2693638bf784f8`, query-vector
  fixture SHA-256은 `dbd53c21e42adca43647206a8f35f4992828afbbbc7030e4c28a0f30cfd8b118`다.
  Disposable clone에는 document 21,873, posting 8,240,889, WARC row 21,873,
  384차원 vector 21,825개가 있었다. 기존 DB/WARC/Lance/vector/artifact는 변경하지
  않았고 별도 PGDATA와 Lance 복사본만 사용했다.
- 실행계획: 같은 25,097 count 행에서 candidate frequency index scan은 cold plan
  capture의 shared hit 61개(이후 warm capture 58–61개)를 사용했다. q6 총 shared
  hits는 약 33,113→16,745로 바뀌었다. score scan은 계속 기본키를 이용했다.
  plan estimate는 개선 후에도 17,935로 실제치보다 낮았다. 양쪽 profile에서 temp
  file/byte는 0이었고, 이후 warm rounds의 physical block reads도 0이었다.
- 검색 비교: base/candidate는 같은 `.18` binary와 데이터로, 차이는 후보 인덱스만
  두었다. 동일 8 queries, `k=5`, warmup 8회, client별 request 128회, JIT on으로
  3회 alternating profile을 했다. BM25/hybrid 모든 query fingerprint가 같고 실패
  요청은 없었다. 8-client p95 median은 BM25 114.318→100.710ms, backend CPU
  13.496→12.988ms/request; hybrid 221.145→240.259ms, CPU
  46.582→46.426ms/request였다. 82MB index가 count scan buffer access를 줄였으나
  4-CPU host load가 약 1.95–25.02로 변해 p95 변화는 원인으로 귀속할 수 없다.
- 쓰기 비용의 한계: 실제 corpus의 64 documents를 4회 재색인하는 profile은 base
  29.207s, candidate 258.054s로 크게 달랐지만 cache와 host load 조건이 달라 해석할
  수 없다. 새로운 ID로 같은 64문서 content를 한 번 삽입한 한 쌍은 base/candidate
  wall 50.743/43.798s, cgroup CPU 1.592/1.817s여서 순서·I/O 변동을 배제하지 못한다.
  실제 importer bulk/COPY 처리량은 재지 않았다. 첫 write probe의
  `octet_length(content)` filter는 전체 TOAST table scan을 유발해 2m33s 후 cancel,
  rollback했다. 원본 DB는 건드리지 않았고 이 clone 이외에는 영향이 없었다.
- 준비 중 실패: 첫 profile은 UNIX socket 경로 길이 제한 때문에 SQL 전에 실패해
  짧은 socket alias로 재시도했다. 첫 build preflight는 컨테이너의 Rustup toolchain
  부재, 다음은 network-none 컨테이너에서 Rustup sync 시도로 중단됐다. 호스트 Rustup
  디렉터리를 read-only mount하고 Rust 1.96.0을 확인한 뒤 locked/offline compile을
  시작했다. cached `cargo-pgrx` 0.19.1도 version probe에서 GLIBC_2.39를 요구했지만
  PG16 build image는 GLIBC 2.36이라 실행할 수 없었다. 그래서 locked/offline
  `cargo rustc`를 사용하며, install SQL은 기존 generated `.18` SQL에 같은 새 DDL을
  추가한 뒤 실제 PG16 fresh install/update로 검증한다. 첫 RustSec CLI 입력의
  `--deny vulnerabilities` option은 cargo-audit 0.22.2가
  지원하지 않아 scan 전에 거부했다. documented flags로 다시 실행했다.
- 보안·notice 검사: `cargo audit --deny unsound --deny yanked`가 advisory DB
  revision `21d909e4c8bd4e14c7aea58bf980c881a0053ab1` (2026-10-24, 1,352
  advisories)을 가져와 561개 lock dependency를 검사했다. vulnerability, unsound,
  yanked는 0건이며 `paste` 1.0.15, `proc-macro-error2` 2.0.1, `serde_cbor` 0.11.2의
  unmaintained warning 세 건은 허용 상태로 남았다. audit 전후 Cargo.lock SHA-256은
  `02751df37bd975ac89579e57be01720b2ae9b608d28e338d16ec6752b5d8a659`로 같았다.
  PG16 target metadata의 dependency notice 수집은 440 package, `needs_review`로 끝났고
  `random_word@0.5.2`, `seahash@4.1.0`의 primary license text가 빠져 strict 수집은
  exit 2였다. 기존 license gap은 해소되지 않았고 binary redistribution은 승인되지
  않았다.
- 빌드·artifact: Linux x86_64, Rust 1.96.0, PG16.15 builder에서 다음 locked/offline
  명령이 20분 50초에 통과했다: `cargo rustc --manifest-path Cargo.toml --lib
  --release --features pg16 --no-default-features --locked --offline -- -C
  link-arg=-Wl,--no-gc-sections`. rustc temporary scratch도 private run의
  `build/tmp`를 사용했다. 산출물 `pgwarc_lance.so` SHA-256은
  `6429e334b3ce69235cf2491cfb04fdc52ef99ab4dfb3668a42fc5db8e2490fd0`이며
  `tools/check_release_artifacts.py --dist-dir <private>/artifacts/candidate-0.1.19
  --version 0.1.19 --pg-major 16`이 통과했다. 표준 `cargo pgrx package`는 캐시된
  cargo-pgrx가 GLIBC_2.39를 요구하고 builder가 GLIBC 2.36이라 실행하지 못했다.
  따라서 private install SQL은 기존 generated 0.1.18 SQL과 같은 새 DDL로 구성했고,
  pgrx package SQL 생성 자체는 검증하지 못했다.
- PG16 실제 DB: 새 regression DB에서 fresh `CREATE EXTENSION`과
  `setup/functions/search/security` 4/4가 통과했다. Disposable PGDATA의
  0.1.18 populated upgrade는 `ALTER EXTENSION ... UPDATE TO '0.1.19'` 5.870초,
  document/posting/WARC row 수 `21,873 / 8,240,889 / 21,873` 보존으로 끝났다.
  새 인덱스는 valid/ready, non-unique, 단일 `term` key이며 82 MB였다. 인덱스는
  extension 소유 `bm25_term` 테이블에 자동 의존한다. 같은 clean clone에서
  `.18` baseline과 `.19`의 동일 8개 질의, BM25/hybrid, 각 mode 128 requests의
  fingerprints가 모두 같았고 오류는 없었다. 이는 query 출력 일치이지 독립 relevance
  평가가 아니다.
- 검증 준비 중 실패: 처음 rustc가 기본 `/tmp`를 쓰려 하여 중지하고 전용 `build/tmp`로
  다시 빌드했다. 첫 재개에는 Cargo PATH가 없어 컴파일 전 종료됐다. 첫 upgrade profile은
  system Python의 `psycopg` 부재로 DB 접근 전 종료됐고, 기존 검증 venv로 재실행했다.
  profile stdout을 JSON 보고서와 같은 파일로 보내 결과가 섞인 시도, stale regression
  expected file, container 내 copied test file 권한 문제는 각각 보고서 경로·기대출력·
  권한을 바로잡아 재검증했다. 첫 assertion은 인덱스 자체에 `deptype='e'`를 요구해
  실패했으나 PG16 카탈로그에서 인덱스의 올바른 자동 의존성을 확인한 뒤 회귀를 고쳤다.
- 미검증과 다음 작업: 독립 qrels가 없어 relevance는 평가하지 않았고, 이전 공개
  0.1.5의 8-client 지연과 controlled 0.1.1 기준을 이번 회차에 재측정하지 않았다.
  이번 `.18` baseline A/B는 인덱스 한 가지 효과만 분리하므로 과거 지연 차이 전체를
  설명하지 않는다. Fresh full raw-WARC ingest, cold/sustained SLA, 대용량 migration
  lock/disk, importer throughput과 다른 PostgreSQL major는 미검증이다. RustSec는
  vulnerability/unsound/yanked 0건, unmaintained 경고 3건이다. 440-package notice
  bundle은 `needs_review`라 binary redistribution 승인 근거가 아니다. 다음 우선 작업은
  controlled `.1.5`/`.1.1` benchmark 재현이다. 이 후보는 운영 승인이 아니다.
- 공개 반영: `publish_pending`; 별도 검토된 publication checkout 연결이 없어
  GitHub `main`은 갱신하지 않았다. 새 credential, release/binary upload, 운영 배포는
  수행하지 않았다. 로컬 commit subject는 `Add BM25 term-frequency index for posting counts`다.
- 미검증과 다음 작업: 독립 qrels가 없어 relevance는 평가하지 않았고, 이전 공개
  0.1.5의 8-client 지연과 controlled 0.1.1 기준을 이번 회차에 재측정하지 않았다.
  이번 `.18` baseline A/B는 인덱스 한 가지 효과만 분리하므로 과거 지연 차이 전체를
  설명하지 않는다. Fresh full raw-WARC ingest, cold/sustained SLA, 대용량 migration
  lock/disk, importer throughput과 다른 PostgreSQL major는 미검증이다. 440-package
  notice bundle은 `needs_review`라 binary redistribution 승인 근거가 아니다.
  다음은 locked PG16 build, extension fresh install/SQL regression 및 populated
  `.18→.19` update의 count/result 확인이다. 이 후보는 운영 승인이나 공개 배포가 아니다.

## 2026-10-25 — BM25 IDF amortization (0.1.18 development candidate)

- 시작점과 범위: clean `4335e5df2de2ad1c25821adb607a2019783b936f`
  (`codex/20261024-rustsec`)에서 별도 worktree와 branch
  `codex/20261025-bm25-idf-amortization`로 진행했다. 같은 checkout의 idle
  개발 세션은 파일·빌드·DB 작업이 없어 그대로 두었고, 원 checkout은 변경하지
  않았다. `.codex/config.toml`은 `gpt-6-luna` / `max`, SHA-256
  `9cb045f6981fdb64ce630508f5a91577f7bf2852f6f6cce4ad70632992e24dc1`로
  유지했다. 구현 commit은
  `580bde0a0fe41eb71edf269715d7328663298375`다.
- 병목 근거와 변경: prior Common Crawl 분석에서 여덟 질의 중 긴 다중어 BM25
  질의(q6)가 c8 p50 약 92–94ms, p95 136–156ms로 가장 느렸다. baseline 계획은
  25,097 posting 행을 읽으며 contribution 식에서 posting마다 IDF `ln()`을
  평가했다. `src/bm25.rs`의 표준 및 long-token query에
  `term_weights AS MATERIALIZED`를 넣어 query-term별 IDF를 한 번 계산하도록
  바꿨다. 순서 있는 점수 합산·tokenizer·검색 의미는 그대로다. SQL catalog
  변경이 없어 `.17→.18` migration은 version-only comment이고 generated
  install SQL은 그대로다. Cargo dependency 그래프는 바뀌지 않았다.
- 입력·환경: Common Crawl clone의 documents 21,873, BM25 postings 8,240,889,
  WARC rows 21,873, 384-d Lance vectors 21,825. Linux x86_64, PostgreSQL 16.15,
  Rust 1.96.0, pgrx 0.19.1, 각 격리 container 4 CPU/16 GiB, network disabled;
  `shared_buffers=128MB`, `work_mem=4MB`, `effective_cache_size=4GB`, JIT 및
  `track_io_timing` on, `fsync` on. 원본 WARC·기존 DB·모델/벡터·기존 artifact는
  읽기 전용으로 뒀고, 폐기 가능한 clone과 Lance 복사본만 썼다. WARC, query
  vectors, Lance tree, `pg_control`, Cargo.lock, binary checksum은 private run
  manifest에 저장했다.
- 빌드·설치 검증: 실제 명령은
  `cargo rustc --manifest-path Cargo.toml --lib --release --features pg16 --no-default-features --locked --offline -- -C link-arg=-Wl,--no-gc-sections`
  이며 27m06s에 통과했다. binary SHA-256은
  `b674e20f0789c52982098d4bf00581644552ed7d8da4c4fdb94d58d922e3a045`,
  Cargo.lock SHA-256은
  `58260de9bb8c92474cd96e51ad2d06011b20b6399b67ad67f85a6b5214d5915e`다.
  `python tools/check_release_artifacts.py --dist-dir <private-artifact> --version 0.1.18 --pg-major 16`,
  fresh PG16 install, `tests/test.sql`, `pg_regress setup/functions/search/security`
  (4/4), populated `.17→.18` update와 counts 유지가 통과했다.
- 같은 조건 A/B: 8-client only, 동일 여덟 질의와 vector fixture, `k=5`, client당
  8 warmups + 128 requests, JIT on으로 세 차례 교차했다. 순서는 base→candidate,
  candidate→base, base→candidate. 각 버전·mode·round에서 결과 fingerprint가
  여덟 질의 모두 정확히 같고 요청 오류는 0이었다. c8 p95/CPU 중앙값은 아래와
  같다.

  | 검색 경로 | `.17` p95 / backend CPU | `.18` 후보 p95 / backend CPU | 변화 |
  | --- | ---: | ---: | ---: |
  | BM25 | 99.31 ms / 13.74 ms/request | 93.75 ms / 13.28 ms/request | p95 −5.6%, CPU −3.3% |
  | Hybrid | 197.04 ms / 47.34 ms/request | 192.63 ms / 46.94 ms/request | p95 −2.2%, CPU −0.8% |

  q6 p50는 93.88→88.31ms (−5.9%)였지만 q6 p95는 150.99→174.60ms (+15.6%)로
  악화했다. per-query p95의 원인은 확정하지 못했다. auto_explain 계획에서 6-term
  query는 JIT와 temp writes가 없었고 warm execution median은 `.17` 47.81ms,
  `.18` 45.51ms였다. 첫 plan capture는 각 버전에서 142 block을 읽었으므로 warm
  수치에 포함하지 않았다. 측정된 첫 회차에도 BM25 각 123 block, candidate
  hybrid 4 block physical read가 있었고 다음 두 회차는 0이었다. 모든 run의 temp
  bytes는 0. 호스트 load가 회차 동안 약 3.3→5.3으로 상승해 p95는 SLA가 아니다.
- 추가 DB 회귀: `python tests/db_search_memory.py --exec-prefix 'docker exec -i pgwarc-idf-bm25-candidate' --pg-bin /usr/lib/postgresql/16/bin --host /run-dev/socket --port 55434 --user postgres --json-output <private-json>` 통과. 100,000 docs / 800,000 postings, 64 KiB `work_mem` fixture에서 independent BM25 scores/rank, tie order, spill 발생, timeout 이후 세션 재사용, 1 MiB temp quota, 검색 경계 및 oversized token 경로를 확인했다. 독립 qrels 기반 relevance가 아니다.
- 실패·한계: 첫 프로파일 명령은 환경변수가 빠져 실행 전에 중단했고 데이터 변경은 없었다. 이후 올바른 환경에서 여섯 profile을 완료했다. 첫 `pg_regress` setup은 private run directory mode 때문에 실패했으며 전용 directory/file permissions를 조정해 재실행 4/4 통과했다. 첫 builder의 `file` 유틸 부재는 artifact checker로 대체 확인했다. cold/sustained load, 독립 qrels, fresh raw-WARC 전체 적재, charset/PDF/office correctness 및 운영 SLA는 검증하지 않았다. Rust dependency가 바뀌지 않아 RustSec·license dependency review는 재실행하지 않았다. binary notice review의 기존 공백은 남는다.
- 판단·다음 작업: query별 p50과 warm explain에서 개선, 전체 8-client BM25 p95/backend CPU에서 개선이 보여 0.1.18 source candidate를 유지한다. q6 p95 +15.6%는 추적할 회귀이며 이 결과만으로 프로덕션 승인하지 않는다. 다음에는 query별 tail 변동을 host load와 분리해 확인하거나, 실제 ingest 경로의 잘못된 charset 및 PDF/office 본문 손실을 조사한다.
- 공개 반영: local branch에만 커밋한다. 별도 publication checkout·검토된 인증이 없어 GitHub `main`은 갱신하지 않고 `publish_pending`으로 남긴다.

## 2026-10-25 — BM25 posting-frequency reuse trial (0.1.18, rejected)

- 시작점: clean `e0123c2` (`codex/20261024-rustsec`)에서 시작했고 같은 checkout의 실행 중 개발 작업은 없었다. 지정된 `.codex/config.toml`의 `gpt-6-luna` / `max`와 SHA-256 `9cb045f6981fdb64ce630508f5a91577f7bf2852f6f6cce4ad70632992e24dc1`를 유지했다. 실험 코드는 검증 후 제거해 현재 소스 기준은 0.1.17이다.
- 선택 이유와 실험: Common Crawl의 0.1.5 warm 부하에서 8-client BM25/hybrid p95 789.74/1,039.65 ms가 보고됐다. 앞선 실행 계획 분석에서는 0.1.5의 6-term BM25 계획 비용 826,702 및 28 JIT functions 중 193.7 ms가 JIT였고, 0.1.17은 predicate pushdown으로 이 고비용 계획을 피했다. 추가 후보는 filtered posting range를 한 번 읽도록 `frequencies` 집계 + 두 번째 posting scan을 `count(*) OVER (PARTITION BY term)`으로 바꾸는 것이었다.
- 실험 코드: 임시 0.1.18 후보는 해당 posting CTE에 정확한 document frequency window count를 붙였고 최종 term 순서 score aggregation은 유지했다. SQL API와 schema는 바꾸지 않는 version-only `.17→.18` migration이었다. 검색 tokenizer, 점수식, 기본 의미는 바꾸지 않았다.
- 환경과 입력: Linux x86_64, PostgreSQL 16.15, Rust 1.96.0, pgrx 0.19.1, 4 CPU/16 GiB 격리 컨테이너, network 없음. 같은 두 개의 PG data clone과 읽기 전용 Lance copy를 썼다. 각각 21,873 BM25 documents, 8,240,889 postings, 21,873 WARC rows, 21,825 384-d vectors를 유지했다. WARC 및 query-vector checksum, PG control identity, Cargo.lock checksum, binary hash, build command와 전체 profile/plan/raw logs는 private run evidence에 기록했다.
- 빌드/설치: 캐시된 `cargo-pgrx` 실행 파일은 builder image의 GLIBC 2.36에서 GLIBC 2.39 요구로 실행되지 않았다. Rust 1.96.0과 PG16.15의 `/usr/bin/pg_config`를 사용해 `cargo rustc --manifest-path Cargo.toml --lib --release --features pg16 --no-default-features --locked --offline -- -C link-arg=-Wl,--no-gc-sections`를 실행했고 release build가 27m37s에 끝났다. export signature가 바뀌지 않아 0.1.17 generated install SQL을 private 0.1.18 artifact용으로 재사용하고 control/version 및 update SQL을 구성했다. `tools/check_release_artifacts.py` 통과, 0.1.18 fresh install 및 `tests/test.sql` 통과, `pg_regress setup/functions/search/security` 4/4 통과, populated 0.1.14→0.1.18 update 통과. 양쪽 clone은 각자 `.17`/`.18`을 가리켰고 DB counts는 같았다. 첫 pg_regress 출력 디렉터리 생성은 private run 권한 문제로 실패했으며 postgres 소유 디렉터리에서 재실행해 통과했다.
- A/B 조건: 같은 8개 질의, `k=5`, JIT on, clients 1/8, client당 8 warmup 및 128 request를 버전별 세 번 교차 실행했다. 결과 fingerprint는 두 버전에서 모든 질의/모드/반복에 같았고 요청 오류는 0이었다. c8 OS load-average 중앙값은 `.17` 5.85, `.18` 7.72(최대 약 19)로 차이가 있어 p95는 SLA 증거가 아니다.

  | c8 검색 경로 | p95 중앙값 `.17→.18` | backend CPU ms/request | shared-hit blocks 중앙값 | warm physical reads/temp bytes |
  | --- | ---: | ---: | ---: | ---: |
  | BM25 | 94.63→101.78 ms (+7.6%) | 13.86→15.04 (+8.5%) | 10.25M→5.49M (−46.5%) | 0 / 0 |
  | Hybrid | 190.95→192.07 ms (+0.6%) | 46.69→46.59 (−0.2%) | 10.17M→5.36M (−47.3%) | 0 / 0 |

- 메모리 회귀: 같은 `tests/db_search_memory.py` 100,000-document/800,000-posting fixture와 64 KiB `work_mem`에서 양 버전 모두 독립 BM25 reference, tie order, timeout/quota 오류 후 backend 재사용 등 22 checks가 통과했다. forced-hash stress plan의 temp bytes는 `.17` 78,003,228에서 `.18` 108,300,700(+38.8%)으로, backend VmHWM은 237,212에서 242,520 KiB(+2.2%)로 증가했다. 이 고정 fixture의 관측치이며 운영 RSS 상한은 아니다.
- 판단: window plan은 posting index range를 한 번 스캔하고 buffer hits를 크게 줄였지만, BM25 backend CPU와 p95가 악화됐고 forced spill도 증가했다. Hybrid p95는 사실상 같았다. 따라서 0.1.18 source 변경과 migration은 보류하고 `.17` baseline을 유지했다. 두 버전 결과가 같다는 것은 수학적 일치 검사이지 독립 qrels 기반 relevance 증거가 아니다.
- 한계와 다음 작업: profile은 warm loaded clone에 한정되고 host load가 흔들렸다. cold physical I/O, 지속 부하, independent qrels, fresh full raw-WARC ingest, charset/PDF/office extraction과 운영 SLA는 검증하지 않았다. 다음은 `.17` baseline의 query별 BM25와 hybrid의 BM25/vector CPU·plan tail을 분리해 실제 개선 가치가 있는 병목을 선택한다.
- 공개 반영: source는 0.1.17로 유지했고 GitHub `main`은 갱신하지 않았다. 이 회차의 성능 결과 및 negative trial만 로컬 문서화하며 `publish_pending`이다.

## 2026-10-24 — Batch WARC metadata lookup in hybrid search (0.1.15 candidate)

- 시작점: `8f3e563`의 0.1.14 source에서 별도 worktree로 이어갔다. 원 checkout과
  옆의 `pglance`는 수정하지 않았다. `.codex/config.toml`의 `gpt-6-luna`/`max`
  설정과 SHA-256 `9cb045f6981fdb64ce630508f5a91577f7bf2852f6f6cce4ad70632992e24dc1`을
  유지했다.
- 선택 이유와 실행 계획: `hybrid_warc_search`가 결과 문서마다 SPI query로
  `warc_record`를 반복 조회했다. 기존 0.1.14 실제 clone의 `auto_explain`은
  k=5 결과에서 `warc_record_pkey` Index Scan을 결과마다 실행하는 구조였다.
  WARC metadata 접근만 문서 ID 배열 조회 하나로 바꾸고, 결과 순서·점수·기본
  metadata 값은 유지하는 범위로 정했다.
- 변경: `warc_metadata_for_docs`가 `doc_id = ANY($1::bigint[])`로 한 번에 조회해
  결과를 ID map으로 만든다. 빈 hit 목록은 query를 생략하며, metadata 행이 없는
  vector hit은 이전처럼 null/default 값을 받는다. 확장 SQL catalog와 데이터
  schema는 변하지 않아 0.1.14→0.1.15 migration은 catalog no-op이다. SQL 회귀와
  smoke fixture에 metadata가 빠진 vector-only 문서도 추가했다.
- 실제 plan: PostgreSQL 16.15의 두 nested-plan capture에서 0.1.14는 metadata
  `warc_record_pkey` Index Scan 10회(두 계획 각각 5회)를 보였다. 0.1.15는 각 계획에서
  `warc_record_pkey` Bitmap Index Scan 1회씩, 총 2회를 보였다. 두 capture 모두
  temporary read/write block과 shared read block은 0이었다. 이는 query 수 감소를
  확인하지만, 기존 높은 latency의 단독 원인이라는 증거는 아니다.
- Common Crawl 비교: PostgreSQL 16.15, Rust 1.96.0, cargo-pgrx 0.19.1, 4 CPU,
  16 GiB 제한, network 없음, 같은 PG16 clone과 읽기 전용 384차원 Lance copy 및
  query-vector fixture를 썼다. 21,873 documents, 8,240,889 postings, 21,873 WARC
  rows, 21,825 vectors 확인. 확장 update `.11→.14→.15` 성공 후 실제 행 수는 그대로였다.
  query-vector fixture SHA-256은
  `dbd53c21e42adca43647206a8f35f4992828afbbbc7030e4c28a0f30cfd8b118`, Lance copy의
  file tree SHA-256은 `f375181aecda22bedaa03b55e33d3b1f6bba9dc1457d78774714698981e8c96a`다.
  비공개 run 디렉터리에 원본 checksum, 프로파일, plan/raw logs를 보관한다.
- 프로파일: 8개 질의, k=5, client 8개, client별 8 warmup 및 128 request를 3회씩
  교차 실행했다. exact row counts는 run 전 별도 검증했고 프로파일 harness는 매 회
  대용량 count scan을 반복하지 않도록 catalog row estimates를 기록했다. 결과 hash는
  3,072 request/버전 및 8개 질의 모두 일치했다.

  | 경로 | p95 중앙값 `.14→.15` | backend CPU ms/request | QPS 중앙값 |
  | --- | ---: | ---: | ---: |
  | hybrid_warc, k=5, clients=8 (3회) | 379.77→294.93 (−22.3%) | 45.76→45.50 (−0.6%) | 47.36→56.22 (+18.7%) |
  | hybrid_warc, k=5, clients=1 (1회) | 59.27→55.66 | 42.81→41.09 | 30.33→33.92 |
  | hybrid_warc, k=100, clients=1 (1회) | 70.71→57.41 | 60.47→57.11 | 21.17→24.02 |
  | hybrid 대조군, k=5, clients=8 (1회) | 388.50→370.47 | 45.54→46.55 | 44.33→47.24 |

  OS 1-minute load average는 실행 중 7.47에서 49.17까지 증가했고 `.14` p95도
  round별 267.80→379.77→402.69ms로 크게 흔들렸다. 따라서 k=5 동시성 8 결과는
  방향성만 나타내며 batch query의 인과 성능 이득으로 단정하지 않는다. warm
  8-client run의 major fault는 0, read_bytes는 거의 모두 0(초기 `.14` round에
  8 KiB 1회)이었다. 이전 공개 0.1.5의 BM25 789.74ms/hybrid 1,039.65ms p95는 이번
  프로파일에서도 재현하지 못했으며 그 원인은 미확정이다. 이 수치는 SLA 또는
  production 승인이 아니다.
- 검증: locked/offline PG16 optimized package build가 15m33s에 성공했다.
  명령은 `cargo pgrx package --manifest-path Cargo.toml --pg-config
  /usr/bin/pg_config --no-default-features --features pg16 --out-dir <private-run>`이며
  `CARGO_NET_OFFLINE=true`, `PGRX_BUILD_FLAGS="--locked --offline"`를 사용했다.
  코드 commit은 `7850a72`이며 baseline/candidate `.so` SHA-256은 각각
  `ce5feabaf26630a2b6f69ff697946c2174b2a97df5253d9f5a625df2b1cdb391`과
  `632a5200221babfe13e7df2c1c4e45117444f60b926f3dccb9e0fd21083a0792`다.
  release artifact/version/control/upgrade 파일 검사 통과, PG16 pg_regress
  `setup/functions/search/security` 4/4 통과, fresh PG16의 `tests/test.sql` 통과,
  `cargo fmt --all -- --check`와 `git diff --check` 통과. Cargo.lock SHA-256은
  `0782d5c60824647b0bf5f236227aadb2e398b8e6d2d7a11ebfe70989777515f1`; 의존성 버전과
  dependency graph는 바뀌지 않았다. 새 의존성이 없어 license notice는 변경하지 않았다.
- 실패와 정정: 검증기가 nested package 경로 대신 평탄화된 dist 경로를 요구해
  첫 artifact check는 실패했고 수정 후 통과했다. 첫 baseline install은 copied
  artifact의 mode 0600 SQL/control 파일을 postgres가 읽지 못해 실패했다. runner에서
  `.so`는 0755, SQL/control은 0644로 명시 설치한 뒤 update를 통과했다. profile socket 경로 길이, 서버
  startup race, Lance clone 권한으로 workload 전 연결이 각각 실패해 짧은 socket alias,
  readiness 확인, clone의 read-only traverse 권한으로 정정했다. PG clone 재시작은
  데이터 파일 fsync recovery를 유발해 시작이 지연됐다. test container에서 소스 파일
  권한 때문에 첫 회 regression/smoke가 중단됐고, 공개 test fixture만 접근 가능한
  private test copy에서 재실행했다. 새 SQL 출력의 expected diff를 갱신한 뒤 regression
  4/4를 통과했다. `cargo audit`는 runner에 설치돼 있지 않아 현재 advisory DB 검사는
  하지 않았다. dependency graph가 바뀌지 않았으나 정식 publication 전 재검사가 남는다.
- 다음 작업: 부하가 낮고 통제된 host에서 0.1.14/0.1.15를 다시 교차 profile하고,
  BM25/Lance CPU와 historical 789/1,040ms tail 재현 여부를 분리한다. 독립 qrels 품질,
  cold physical I/O, SLA 및 production 동작은 이번에 검증하지 않았다. 기존 인증이
  연결된 검토용 publication checkout이 없어 GitHub 반영은 `publish_pending`이다.

## 2026-10-24 — Overlap BM25 and Lance work in hybrid search (0.1.14 candidate)

- 시작점: `36f60ba`의 0.1.11 source에서 이어갔다. 사용자가 지정한
  `.codex/config.toml`의 `gpt-6-luna`/`max` 값과 SHA-256
  `9cb045f6981fdb64ce630508f5a91577f7bf2852f6f6cce4ad70632992e24dc1`를 유지했다.
  기존 checkout은 변경하지 않았고, 별도 local worktree와 새 private run의 clone,
  패키지만 사용했다.
- 선택 이유: hybrid 경로가 BM25 SPI 검색을 끝낸 뒤 Lance 벡터 검색을 시작하는
  순차 구조였다. 같은 21,873-document/8,240,889-posting/21,873-WARC-row clone의
  0.1.11 baseline에서 warm 1-client p95는 BM25 37.76ms, vector 24.04ms, hybrid
  59.99ms였고 8-client p95는 101.73ms, 199.84ms, 261.46ms였다. 두 검색 경로의
  CPU 합이 hybrid CPU와 비슷해 단일 검색 latency를 겹치는 최소 변경을 평가했다.
- 변경: `hybrid::search`가 기존 Tokio runtime worker에 vector task를 먼저 예약하고
  PostgreSQL backend thread에서 BM25를 실행한 다음 결과를 기다린다. 비동기 Lance
  future는 PostgreSQL API를 호출하지 않는다. 대기는 기존 50ms interrupt poll을
  유지하며, pending task의 Drop은 PostgreSQL 오류/취소 때 task를 abort한다. SQL
  signature와 table/index schema는 바꾸지 않았다. crate version은 0.1.14로 올리고
  0.1.11→0.1.14 catalog-no-op migration을 추가했다.
- 검증 비교: PostgreSQL 16.15, CPU 4, memory 16 GiB, 같은 clone·384d E5 Lance
  dataset·query-vector fixture·8개 질의·`k=5`, client별 warmup 8회/측정 128회,
  concurrency 1/8을 사용했다. baseline과 candidate의 BM25/vector/hybrid/hybrid_warc
  모든 결과 hash가 같았다. PostgreSQL 설정은 `shared_buffers=128MB`,
  `work_mem=4MB`, `effective_cache_size=4GB`, `jit=on`, `track_io_timing=on`,
  `max_parallel_workers_per_gather=2`, `fsync=on`이었다. timed connection은
  read-only이고 Lance dataset은 read-only mount였다. Backend `/proc` CPU와 OS read
  counters도 수집했다.

  | 경로 / clients | p95 ms, 0.1.11→0.1.14 | backend CPU ms/request | QPS |
  | --- | ---: | ---: | ---: |
  | hybrid / 1 | 59.99→49.05 (−18.2%) | 38.20→37.42 (−2.0%) | 33.62→39.11 (+16.3%) |
  | hybrid / 8 | 261.46→267.07 (+2.1%) | 44.05→43.15 (−2.0%) | 61.00→58.79 (−3.6%) |
  | hybrid_warc / 1 | 57.54→45.45 (−21.0%) | 37.03→36.64 (−1.1%) | 36.25→41.59 (+14.7%) |
  | hybrid_warc / 8 | 272.68→264.22 (−3.1%) | 44.67→43.96 (−1.6%) | 58.97→59.44 (+0.8%) |

  단일 client의 latency/throughput은 개선됐지만 8-client hybrid p95와 throughput은
  개선되지 않았다. 따라서 이 변경을 고부하 성능 승인으로 보지 않는다. 두 run의
  공유 호스트 load average는 9.69–32.46 사이로 움직였고, 이 비교는 SLA가 아니다.
- 계획·I/O: baseline run의 warm BM25 `privacy` 및 긴 multi-term plan은 shared read와
  temp block이 모두 0이었다. 후보 서버 재시작 직후 cold EXPLAIN은 `privacy`에서
  115 shared read blocks/207.195ms read time, 긴 query에서 142 blocks/796.834ms를
  보였다. read time은 `bm25_term_pkey` Index Only Scan에서 관측됐고 temp spill은
  0이었다. 이는 계획 비교의 cache 상태가 같지 않았음을 뜻한다. 각 8-client timed
  profile은 warmup 후 backend `read_bytes=0`, major faults 0이었으며 temp spill도
  관측되지 않았다. 과거 0.1.5의 BM25 789.74ms/hybrid 1,039.65ms warm p95는 이
  프로파일에서 재현되지 않았다. 따라서 cold index I/O wait는 관측했지만 과거 저하의
  원인으로 확정하지 않았다.
- version/package 확인: PostgreSQL 16.15, Rust 1.96.0, cargo-pgrx 0.19.1에서
  `cargo pgrx package --manifest-path Cargo.toml --pg-config /usr/bin/pg_config
  --no-default-features --features pg16 --out-dir <private-run>` locked build가
  30m33s 만에 성공했다. 첫 offline 시도는 private Cargo cache에 잠금된
  `rustls 0.23.46` 소스가 없어 실패했고, 동일 lock으로 격리 빌드 컨테이너에서
  필요한 버전을 받아 재시도했다. Cargo.lock SHA-256은 build 전/후
  `2a8f9b8d108ea9209d5c9f81ba6ef2bcace17c44fd2f7b046dcd0a393cb86741`로 같았다.
  `tools/check_release_artifacts.py --version 0.1.14 --pg-major 16` 통과.
  fresh install은 extension version 0.1.14와 `tests/test.sql`을 통과했고, Common
  Crawl clone의 0.1.11→0.1.14 update 뒤 counts와 covering-PK schema가 보존됐다.
  `tests/db_runtime_safety.py`는 5ms hybrid timeout 및 같은 backend 재사용을 포함해
  모든 check가 통과했다. `cargo fmt --all -- --check`와 전체 Python `test_*.py`
  433개도 통과했다.
- 입력/산출물 지문: query-vector fixture SHA-256
  `dbd53c21e42adca43647206a8f35f4992828afbbbc7030e4c28a0f30cfd8b118`, 0.1.11
  baseline `.so` `12981a1771ab8f675a1885e9b2f951504c42756fe4132f2b66950da8017537d9`,
  0.1.14 candidate `.so`
  `70b1a8bfb460d8aa711b39a49250b85a4636739cf70de66809af7322b7fe7c5a`. Dependency
  graph는 바뀌지 않아 RustSec/third-party notice를 이번에 다시 평가하지 않았다.
  기존 `random_word`/`seahash` binary notice 공백은 남아 있다.
- 실패와 한계: 처음 읽기 전용 schema probe에서 없는 `documents` relation을 조회해
  실패했고 `pgwarc_lance.bm25_doc`로 정정했다. profile container의 첫 시작은 login
  shell의 PATH reset 때문에 실패해 non-login shell로 다시 시작했다. host Python 기본
  환경에 psycopg가 없어 benchmark harness는 기존 private module cache로 실행했다.
  이 작업은 원본 WARC를 재적재하지 않았고, 0.1.5 immutable binary와 동일 조건
  비교·cold/warm 반복,
  physical storage SLA, 독립 qrels를 검증하지 않았다. 코드 후보는 단일 client 이득이
  있지만 8-client hybrid 개선을 입증하지 못했다. 프로덕션 승인과 GitHub 반영은 없다.
- 다음 작업: 보존된 0.1.5 artifact를 확인해 실제 0.1.5/0.1.11/0.1.14를 동일
  PG16 clone·질의·warmup·cache 조건에서 비교하고, `bm25_term_pkey`의 cold I/O wait와
  historical 789/1,040ms tail의 재현성을 분리한다. 이 결과 전에는 8-client SLA나
  production approval을 부여하지 않는다.

## 2026-10-22 — BM25 document covering primary key (0.1.11 candidate)

- 시작점: `codex/20261022-bm25-docstats-real-corpus`의 `9ac2ca4`에서 이어갔다.
  `.codex/config.toml`은 `gpt-6-luna`/`max` 및 SHA-256
  `9cb045f6981fdb64ce630508f5a91577f7bf2852f6f6cce4ad70632992e24dc1`로 유지했다.
  기존 `.10` artifact, Common Crawl 원본, 원 DB, Lance 원본과 이전 증거는 읽기
  전용으로 보존하고 disposable PostgreSQL clone과 비공개 run만 사용했다.
- 선택 이유: 직전 실제 계획의 희소어 BM25 join은 `bm25_doc_pkey`를 5,763회
  반복 조회해 17,289 shared-hit blocks를 만들었고, multi-term query는 21,873개
  문서의 두 번째 조회를 1,598-block heap sequential scan으로 처리했다. temporary
  spill이나 warm shared read는 없었다. 따라서 BM25 수식·tokenizer·결과 순서를
  바꾸지 않고 `doc_len`을 기본키의 INCLUDE 열로 넣어 heap 접근을 줄이는 변경을 택했다.
- 변경: `bm25_doc` fresh-install PK를 `PRIMARY KEY (doc_id) INCLUDE (doc_len)`으로
  바꾸고 기존의 더 작은 `bm25_doc(doc_len)` 통계 인덱스는 유지했다. 새
  `0.1.10→0.1.11` migration은 covering unique index 생성, extension FK 재연결,
  PK 교체, FK validation을 한 트랜잭션으로 수행한다. pg_regress setup은 기본키의
  키/INCLUDE 속성을 검사한다. Cargo crate와 lockfile의 root package를 `0.1.11`로
  올렸으며 dependency graph는 바뀌지 않았다. 검색 수식과 tokenizer는 변경하지 않았다.
- 제외 근거: PK만 두고 통계 인덱스를 제거한 pilot의 8-client BM25 p95 중앙값은
  0.1.10 baseline 100.37ms와 거의 같은 100.68ms였다. backend CPU는 10.39→9.76ms/request로
  줄었지만 작은 통계 인덱스가 없어지면 두 query의 stats scan이 30에서 86 hit blocks로
  커졌다. 따라서 통계 인덱스도 유지했다. PK는 614,400에서 704,512 bytes로 90,112 bytes
  증가했고 통계 인덱스는 245,760 bytes였다.
- 실제 비교: PostgreSQL 16.15 clone 2개를 `VACUUM (ANALYZE)`한 뒤 baseline PK와
  covering PK schema를 교차 세 번 실행했다. 두 DB는 같은 21,873 documents,
  8,240,889 postings, 21,873 WARC rows와 동일한 extension code를 사용했고 PK schema만
  달랐다. 각 실행은 client 8개, client별 warmup 8회, client별 128 query, `k=5`, 같은
  8-query mix 및 persistent connection을 사용했다. 매 mode/run의 1,024 요청은 모두
  성공했고 BM25·hybrid 결과 hash는 전부 일치했다.

  | Measure | Baseline PK | Covering PK | Change |
  | --- | ---: | ---: | ---: |
  | BM25 p95 median (ms) | 170.92 | 133.13 | −22.1% |
  | BM25 backend CPU median (ms/request) | 10.33 | 9.27 | −10.3% |
  | Hybrid p95 median (ms) | 335.63 | 319.22 | −4.9% |
  | Hybrid backend CPU median (ms/request) | 47.30 | 46.09 | −2.6% |

  shared host load average는 마지막 교차 실행 뒤 24.84였고 system CPU는 여러
  호스트 프로세스와 공유됐다. p95는 SLA가 아니다. 실제 `.11` extension을 적용한
  별도 sequential runs에서도 결과 hash와 CPU 감소는 유지됐지만 hybrid p95가
  231.41/273.71/302.83ms로 크게 흔들려 위 교차 실행을 최종 비교로 삼았다.
- 실행 계획: VACUUM 뒤 희소어 문서 join은 `Index Scan`에서 PK의 `Index Only Scan`으로
  바뀌고 17,289→11,527 shared-hit blocks, heap fetch 0을 보였다. multi-term 문서 scan은
  1,598-hit `Seq Scan`에서 86-hit `Index Only Scan`으로 바뀌었다. stats scan은 30-hit
  index-only로 유지됐다. 관측한 warm plans는 shared reads/temp blocks 0이었다. 물리
  device I/O와 cold-cache는 측정하지 않았다.
- 빌드/DB 검증: PostgreSQL 16.15, Rust 1.96.0, cargo-pgrx 0.19.1, CPU limit 4,
  memory 16 GiB, network none에서 `cargo pgrx package --manifest-path Cargo.toml
  --pg-config /usr/bin/pg_config --no-default-features --features pg16 --out-dir dist`를
  `PGRX_BUILD_FLAGS="--locked --offline"`와
  `CARGO_NET_OFFLINE=true`로 성공시켰다 (release build 30분 7초). Artifact check
  `tools/check_release_artifacts.py --version 0.1.11 --pg-major 16` 통과, fresh
  install은 version `0.1.11` 및 PK `indnkeyatts=1`, `indnatts=2`, `doc_id` key,
  `doc_len` INCLUDE을 확인했다. `tests/test.sql` SQL smoke와 pg_regress
  `setup/functions/search/security` 4/4가 통과했다.
- 측정 PostgreSQL 설정: `shared_buffers=128MB`, `work_mem=4MB`,
  `effective_cache_size=4GB`, `jit=on`, `track_io_timing=on`,
  `max_parallel_workers_per_gather=2`, `fsync=on`. Benchmark connection은 read-only였고
  Lance dataset은 read-only mount였다. paired VACUUM은 `/dev/shm` 공간 제약을 피하려고
  `max_parallel_maintenance_workers=0`으로 실행했다.
- 실데이터 upgrade: 0.1.10→0.1.11 `ALTER EXTENSION UPDATE`가 준비된 clone에서
  1.23초 걸렸다. 이후 version, 21,873 docs, 8,240,889 postings, 21,873 WARC rows,
  PK 정의, 통계 인덱스, FK `convalidated=true`를 확인했다. 원본 WARC checksum은
  `4b6b55dc25afdb8ef9bb0e48213faa73cf07756f195b0863ba2693638bf784f8`, Lance manifest
  checksum은 `a9d1f88db8eec19ced70684ddf4dd0a09bc1b3a7092c8d26eee86d3adaf97093`, query
  vector fixture checksum은 `dbd53c21e42adca43647206a8f35f4992828afbbbc7030e4c28a0f30cfd8b118`이다.
  기존 DB와 Lance clone을 사용했고 raw WARC를 재적재하지 않았다. 새 `.so` checksum은
  `12981a1771ab8f675a1885e9b2f951504c42756fe4132f2b66950da8017537d9`, Cargo.lock
  checksum은 `42cb06eae8ff812caa623d6d985fc9e726142da454092e48db3e9b23f6bb32ec`이다.
- 실패와 수정: 첫 pilot Docker exec는 stdin 전달 누락으로 DDL이 실행되지 않아
  변경 없는 계획 결과를 후보로 인정하지 않았다. profile script의 첫 호출은 private
  psycopg 경로 누락, 첫 psql은 socket 경로 오류로 실패했고 올바른 `PYTHONPATH`와
  `/socket:55434`로 재실행했다. 첫 fresh install은 복사된 generated SQL mode 0600
  때문에 실패해 disposable artifact의 SQL/control을 0644로 바꾼 뒤 통과했다.
  baseline clone의 첫 VACUUM은 `/dev/shm`의 67,128,832-byte allocation 실패를 냈고
  `max_parallel_maintenance_workers=0`으로 재실행해 통과했다. 처음 clone 기동에서
  `PGDATA` 누락과 복구 중 이른 readiness polling이 있었으며 복사본에만
  `recovery_init_sync_method=syncfs`를 적용해 복구했다. 모든 로그는 비공개 run에 있다.
- 미검증/한계: write/import overhead, 큰 production corpus의 migration lock/disk 사용,
  autovacuum visibility-map coverage, fresh raw-WARC ingestion, cold/physical I/O,
  sustained load, 독립 qrels, 기타 PostgreSQL major, production acceptance는 미검증이다.
  Dependency graph가 바뀌지 않아 이번 후보에서는 RustSec와 third-party notice를 다시
  수집하지 않았다. `random_word`/`seahash` license-text gaps와 binary redistribution
  review는 남아 있다. controller master와 GitHub `main`은 변경하지 않았고 remote 및
  검토된 publication checkout이 없어 `publish_pending`이다.
- 다음 작업: 보존된 `.1.1`/`.10` artifact와 compatible corpus DB의 위치를 확인해
  과거 `.1.1` 기준과 현재 후보를 동일 PostgreSQL, query set, corpus, warm/cold 조건으로
  비교한다. 입력 추출의 charset·PDF/office·long-token 문제는 그 다음 actual ingest 경로에서
  우선순위를 정한다.

## 2026-10-22 — BM25 document-statistics Common Crawl A/B follow-up (0.1.10)

- 시작점: 검증할 코드 변경은 `d3dee89`의 0.1.10 `bm25_doc(doc_len)` index였다.
  요청한 project checkout에서 `codex/20261022-bm25-docstats-real-corpus`를 만들고
  해당 commit부터 이어갔다. `master`는 `fbe7bf2`에 그대로 두었고 기존 .9/.10
  worktree와 사전 artifact를 수정하지 않았다. `.codex/config.toml`의
  `gpt-6-luna`/`max`도 유지했다. 별도 동시 개발 프로세스는 확인되지 않았다.
  비공개 run ID는 `20261022-bm25-docstats-real-corpus`다.
- 선택 이유: 직전 .10 검증은 합성 통계 집계만 다뤘다. 준비된 Common Crawl corpus의
  BM25 통계 scan과 end-to-end BM25/hybrid에 같은 인덱스가 주는 효과를 검증했다.
- 입력/보존: 기준 WARC segment는 938,695,574 bytes, SHA-256
  `4b6b55dc25afdb8ef9bb0e48213faa73cf07756f195b0863ba2693638bf784f8`이다. 기존
  준비 DB의 21,873 documents, 8,240,889 BM25 postings, 21,873 metadata rows와
  21,825 384-dimensional vectors를 확인했다. 데이터베이스와 Lance dataset은 새
  전용 복제본으로 만들고, Lance 파일 manifest SHA-256은
  `a9d1f88db8eec19ced70684ddf4dd0a09bc1b3a7092c8d26eee86d3adaf97093`이다. query-vector
  fixture SHA-256은 `dbd53c21e42adca43647206a8f35f4992828afbbbc7030e4c28a0f30cfd8b118`이다.
  raw WARC를 재적재하지 않았다. 원본 WARC·기존 DB·vector dataset·기존 artifact는
  읽기 전용으로 보존했다.
- 동일 조건: 같은 0.1.6 prepared DB snapshot을 두 새 PG16.15 cluster에 복제하고
  baseline을 0.1.9, candidate를 0.1.10으로 올렸다. 모든 8.2M postings를 포함한
  update 경로는 각각 `.6→.7→.8→.9`와 `.6→.7→.8→.9→.10`이었고, 전체 경로 시간은
  14.165초와 14.889초였다. 문서/posting 개수는 유지됐다. CPU limit 4, memory
  16 GiB, `shared_buffers=128MB`, `work_mem=4MB`, `effective_cache_size=4GB`, `jit=on`,
  `max_parallel_workers_per_gather=2`, `fsync=on`, network disabled를 사용했다.
- 계획 원인: VACUUM 전 두 버전 모두 통계 aggregate가 `bm25_doc` sequential scan으로
  1,598 shared-hit blocks를 읽었다. 한 차례 8-client warm 실행에서 BM25 backend CPU는
  11.53→11.54 ms/request로 사실상 같았다. `VACUUM (ANALYZE) pgwarc_lance.bm25_doc`
  뒤 candidate는 통계 scan을 30-hit `Index Only Scan`, heap fetch 0으로 바꿨다.
  baseline은 1,598-hit sequential scan에 남았다. 다중 term query에는 목적이 다른
  `bm25_doc` sequential scan 하나가 더 남았다. 관측 계획의 shared read와 temp read/write는
  0이므로 이 결과는 warm buffer/CPU 동작이며 physical-device I/O나 spill 완화 증거가 아니다.
  새 index 크기는 245,760 bytes (240 KiB)였다.
- warm 부하: 양쪽 모두 client 8개, client별 warmup 8회, 요청 128회, `k=5`, 같은 8-query
  mix와 persistent connection을 썼다. VACUUM 뒤 3회씩 반복했고 매 run의 BM25 및 hybrid
  1,024요청이 전부 성공했다. 모든 query의 ID/score/source/metadata 결과 hash 8개가
  버전 사이에 일치했다.

  | Measure | 0.1.9 | 0.1.10 | Change |
  | --- | ---: | ---: | ---: |
  | BM25 p95 median (ms) | 92.85 | 90.81 | −2.2% |
  | BM25 backend CPU median (ms/request) | 11.51 | 10.15 | −11.9% |
  | Hybrid p95 median (ms) | 234.63 | 230.77 | −1.6% |
  | Hybrid backend CPU median (ms/request) | 52.14 | 50.57 | −3.0% |

- 판정/미검증: 계획과 backend CPU는 VACUUM 뒤의 이득을 보였고 p95 변화는 작았다.
  이전 synthetic fixture의 aggregate-only 개선과 구분해 기록한다. p95는 공유 host의
  세 warm run 중앙값이며 SLA가 아니다. fresh raw-WARC ingestion, importer/index write
  overhead, autovacuum visibility coverage, cold/sustained load, independent qrels, 다른
  PostgreSQL major와 production approval은 미검증이다. 두 dependency license-text
  공백은 남아 있다. dependency graph가 바뀌지 않아 RustSec/notice 재수집은 하지 않았다.
- 구현 검증 재사용: 0.1.10 locked offline PG16.15 package, artifact check, fresh install,
  SQL smoke와 pg_regress 4/4, PG16.6 fresh install 및 populated adjacent upgrade는
  선행 .10 run에서 통과했다. 이 회차는 실제 prepared corpus의 `.6→.9`/`.6→.10`
  update, count preservation, query plan, 부하와 결과 hash를 추가로 검증했다. 실행 환경,
  profile 원본, plans, 결과 JSON과 checksums는 비공개 run에 보관했다.
- 보존한 실행 설정 실패: host 계정에서 Lance source를 읽으려다 permission denied가
  났고, helper가 처음에는 비어 있는 image의 기본 data mount를 복사했다. 기존 profile
  container의 실제 PGDATA 위치를 찾아 source volume을 read-only mount로 읽고 새 사본만
  만들었다. 최초 PG readiness poll은 startup 중 너무 일찍 실행됐고, 재시도 후 준비됐다.
  컨테이너 Python에 필요한 packages가 없어 isolated Python 3.12.15/numpy 2.5.3 환경을
  사용하고 psycopg 3.2.9만 private run directory에 설치했다. 긴 Unix-socket pathname은
  제한을 넘어서 전용 private short alias로 연결했다. 원본 데이터와 제품 dependency는
  수정하지 않았다.
- 로컬 구현 commit은 `d3dee89`이며 이번 검증 기록과 문서 갱신을 별도 local commit으로
  남긴다. 공개 GitHub `main`과 controller `master`에는 반영하지 않았다. remote 및
  검토된 publication checkout이 없어 `publish_pending`이다.
- 문서 검사는 `make check-docs`와 `git diff --check`가 통과했다. 실행 후 이번 회차의
  전용 PostgreSQL 컨테이너를 정지하고 임시 socket alias를 제거했다. DB 사본과 전체
  실행 증거는 비공개 run directory에 보존했다.

## 2026-10-22 — BM25 document-statistics index (0.1.10 development candidate)

- 시작점: 공개 후보 `.9`에서 분리한 `codex/20261022-bm25-docstats-index`
  worktree를 사용했다. 시작 commit은 `374dd83a58f2650999795de1dfbb849cba4ec5a0`이며,
  같은 checkout의 진행 중 세션과 기존 `.9` worktree를 수정하지 않았다. 프로젝트의
  `.codex/config.toml`은 `gpt-6-luna`/`max`로 유지했다. 과거 별도 `pglance`, 프로덕션,
  원본 WARC·기존 검증 DB·기존 바이너리는 읽기 전용으로 두었다. 비공개 run ID는
  `20261022-bm25-docstats-index`다.
- 선택 이유: 실제 Common Crawl 21,873문서의 이전 PostgreSQL 계획에서 BM25 통계 CTE가
  `bm25_doc` sequential scan으로 1,598 shared blocks를 읽었고, temporary read/write는
  0이었다. 전체 corpus p95가 아니라 이 넓은 heap scan 하나를 줄일 수 있는지 검증하기
  위해 `doc_len`만 담는 좁은 인덱스를 선택했다. tokenizer, BM25 점수식, query limits,
  검색 결과와 snapshot semantics는 변경하지 않았다.
- 변경: `bm25_doc(doc_len)` 인덱스를 fresh install SQL과 0.1.9→0.1.10 upgrade SQL에
  추가했다. BM25 통계 CTE에 목적을 설명하는 SQL comment를 추가했고 회귀 setup에서
  인덱스가 valid/ready 상태이며 key가 `doc_len` 하나인지 확인한다. crate를 0.1.10으로
  올렸다. Cargo dependency graph는 바꾸지 않았다. `Cargo.lock` SHA-256은
  `47dc857fdd41b47ceb71c8f11a53cf93496715291b087df45139e65afff6f7d3`이다.
- 빌드/산출물: Linux x86_64, PostgreSQL 16.15, Rust 1.96.0, cargo-pgrx 0.19.1의 전용
  development container에서 `PGRX_BUILD_FLAGS="--locked --offline" cargo pgrx package`
  (PG16 feature) 가 성공했다. cold optimized package 빌드는 27분 37초였다.
  `python3 tools/check_release_artifacts.py --dist-dir <private-pg16-artifact> --version 0.1.10 --pg-major 16`
  통과. 새 private `.so` SHA-256은
  `59d986f942d65d7924298723c2b6480c478c9fd3d75469036ac1b00862f3f3fd`다. 기존 `.9`
  artifact는 read-only로 복사해 사용했고 덮어쓰지 않았다.
- PG16 회귀: candidate artifact로 PG16.15 임시 클러스터에 fresh `CREATE EXTENSION`,
  `tests/test.sql`, pg_regress `setup/functions/search/security` 4/4가 통과했다.
  공식 `postgres:16.6` 임시 컨테이너에서도 fresh install과 hello function 및
  `doc_len` index 확인이 통과했다. 같은 16.6 image에서 populated 0.1.9→0.1.10 artifact
  upgrade가 통과했고 BM25 문서 2건, WARC 2행, Lance 2행과 기존 upgrade helper의
  결과/버전 검사도 보존됐다. 추가 catalog check에서 extension version `0.1.10`,
  인덱스 valid/ready, 단일 key `doc_len`을 확인했다.
- 계획/CPU 비교: 비공개 합성 fixture는 21,873행, 문서당 고엔트로피 본문 512 bytes,
  heap 12 MiB와 새 index 184 KiB였다. baseline은 같은 DB에서 index 경로를 끄고
  sequential scan으로 측정했다. `EXPLAIN (ANALYZE, BUFFERS)` aggregate는 baseline
  1,563 shared-hit blocks/4.052 ms였고, bulk insert 직후 candidate도 아직 `Seq Scan`이었다.
  VACUUM 뒤 candidate는 `Index Only Scan`, 23 shared-hit blocks, heap fetch 0,
  execution 2.342 ms였다. baseline/candidate 모두 shared read 0, temp read/write 0.
  같은 BM25 query의 ordered 결과 파일 SHA-256은 양쪽 모두
  `3cb5b697d1d4358859602af19b23c60cca3bb21821e8a76e01c169af628ede76`이다.
  visibility map이 채워져야 planner가 이득을 얻는다는 조건을 확인했다.
- warm CPU 측정: 각 phase 1,000회 aggregate를 3회 실행했다. backend `/proc` user CPU
  tick 중앙값은 baseline 268 ticks (2.68 s)에서 candidate 117 ticks (1.17 s),
  측정 wall 중앙값은 2.838 s에서 1.206 s였다(100 ticks/s). 이는 synthetic fixture
  aggregate 반복 결과이며 BM25 요청 p95가 아니다. shared buffer에 올라온 데이터의
  반복 결과이고 cold storage I/O로 확대 해석하지 않는다.
- 실패와 복구: 이전 run CARGO_HOME 전체를 controller 계정으로 복사하려 한 시도에서
  root 소유 crate source 7개가 permission denied를 냈다. 원본은 그대로 두고 registry와
  cargo-pgrx 캐시만 root disposable container에서 새 run 경로로 복사해 locked/offline
  build를 성공시켰다. PG development image의 기본 command는 PostgreSQL이 아니라
  `sleep infinity`라서 전용 tmpfs 안에 수동 initdb/start했다. 첫 SQL smoke는 대상 DB를
  아직 만들지 않아 실패했고 disposable cluster에 DB를 만든 뒤 통과했다. 첫 upgrade
  smoke는 upgrade SQL 파일 권한 때문에 server 계정이 읽지 못해 실패해 private artifact
  copy만 mode 644로 바꿔 통과했다. 첫 catalog assertion은 psql boolean 출력 `true`를
  `t`와 비교해 실패했으며 표현을 맞춘 재실행에서 통과했다. 기존 source/artifact와
  판정 기준은 변경하지 않았다.
- 미검증/한계: 0.1.10 전체 Common Crawl 재적재, 같은 8-client 조건의 0.1.1/0.1.10
  BM25·hybrid p95, import throughput/write overhead, autovacuum 시점과 운영 corpus의
  visibility-map coverage는 측정하지 않았다. 따라서 observed 0.1.5의 BM25 789.74 ms /
  hybrid 1,039.65 ms와 개선 여부를 비교하지 않았고 SLA/production 승인도 없다.
  0.1.10에서 전체 corpus를 같은 입력·질의·warmup으로 복제해 CPU, block I/O, spill과
  p95를 비교하는 일이 다음 작업이다. Rust 의존성은 그대로라 이번 run에서 RustSec와
  license notice를 재수집하지 않았다. 기존 `random_word`/`seahash` notice 공백은 남아 있다.
- 공개 반영/커밋: 변경은 격리된 worktree에만 두고 로컬 commit으로 마감한다.
  public GitHub `main`과 controller `master`는 갱신/merge하지 않는다. 검토된 publication
  checkout이 없어 `publish_pending`이다.

## 2026-10-21 — BM25 covering index (0.1.9 development candidate)

- 시작점: 필수 문서와 직전 개발 로그, `.codex/config.toml`, git 상태를 확인했다.
  clean `master` `fbe7bf2`에서 별도 `codex/20261021-bm25-covering-019` worktree를
  만들었다. 같은 checkout에서 다른 Codex 세션은 idle 상태였고 변경을 건드리지 않았다.
  이 worktree의 시작 commit은 `fbe7bf2`다. 비공개 run ID는
  `20261021-bm25-covering-019-followup`이다.
- 선택 이유와 원인: 실제 Common Crawl 21,873문서/8,240,889 posting에서 긴 구문
  질의가 posting 25,097개를 방문할 때 `tf`를 읽느라 `Bitmap Heap Scan`이 발생했다.
  기존 EXPLAIN에서는 shared-buffer read와 PostgreSQL I/O 대기가 보였고 temporary
  spill은 0이었다. `tf`를 covering index에 포함한 진단 계획은 해당 posting scan을
  `Index Only Scan`/heap fetch 0으로 바꿨다. 대상 schema change는 이전 0.1.6→0.1.7
  controlled run에서 이미 구현·측정됐으나 RustSec 문제가 있는 버전이어서, 0.1.8
  dependency-only 상태에 동일한 최소 수정으로 포팅했다.
- 변경: `bm25_term` 기본키를 `(term, doc_id) INCLUDE (tf)`로 바꾸고 조회 조건과 중복되는
  `bm25_term_term_idx`를 제거했다. `0.1.8→0.1.9` upgrade SQL은 cover index를 생성한 뒤
  기본키를 교체하고 중복 인덱스를 삭제한다. PG regression setup은 index metadata를
  확인한다. tokenizer, BM25 수식, 검색 결과 제한과 vector 경로는 변경하지 않았다.
  crate/lock package version만 0.1.9로 올렸고 dependency graph는 바꾸지 않았다.
- 재사용한 성능 증거: 비공개 기존 Common Crawl 동일-fixture run에서 8-client warm BM25
  p95 두 회 median은 286.925→136.565 ms, backend CPU estimate/request는
  18.905→11.905 ms, BM25 인덱스 크기는 766→446 MiB였다. 16개 query/result hashes가
  일치했다. Hybrid p95 median은 533.175→354.335 ms였지만 vector-only도 같은 방향으로
  움직여 전부를 BM25 변경에 귀속하지 않았다. 해당 run은 0.1.6→0.1.7 측정이며 0.1.8은
  검색 코드가 동일하다. **이번 0.1.9 회차에서 Common Crawl 전체 부하를 재실행하지 않았으므로
  이 숫자는 재사용한 기존 결과이며 새 .9 실측/SLA가 아니다.** 원본 segment checksum은
  기존 공개 검증 근거와 같고, query-vector fixture checksum은 비공개 run summary에
  기록했다. 원본 WARC/DB/모델/벡터와 기존 evidence는 수정하지 않았다.
- 빌드와 artifact: Linux x86_64 disposable container, PostgreSQL 16.15, Rust 1.96.0,
  pgrx 0.19.1에서 `cargo rustc --locked --offline --lib --release --no-default-features
  --features pg16 -- -C link-arg=-Wl,--no-gc-sections`와
  `PGRX_BUILD_FLAGS="--locked --offline" cargo pgrx package --pg-config
  /usr/bin/pg_config --no-default-features --features pg16 --out-dir ...`가 통과했다.
  `tools/check_release_artifacts.py --dist-dir ... --version 0.1.9 --pg-major 16`도
  통과했다. 새 `.so` SHA-256은
  `c9f6ee25a30cf666c40207a6c18ac11496de47464b4e715ad75a4e671cdd3a10`이다. 이전 0.1.8
  artifact는 읽기 전용 보존했다. `cargo fmt --all --check`, locked offline metadata와
  `git diff --check`도 통과했다. 첫 `cargo update --offline`은 로컬 index에 필요한
  이미 잠긴 crate 버전 metadata가 없어 실패했다. dependency graph는 변경하지 않고
  root package version만 lockfile에 반영했다. 첫 partial Cargo source cache 빌드는
  `fnv` 파일 누락으로 멈췄으며, `cargo fetch --locked`로 전용 cache를 채운 뒤 locked
  offline package가 성공했다.
- PG16.15 검증: 깨끗한 disposable PGDATA에 `CREATE EXTENSION`, `tests/test.sql`,
  `pg_regress` setup/functions/search/security 4/4가 통과했다. 별도
  `postgres:16.6` populated upgrade smoke에서 BM25 2건, WARC 2건, Lance 2건을 보존했다.
  기존 profile fixture 대비 query result hash 일치도 이전 run에서 확인했다. `.9` full
  corpus 재측정은 하지 않았다. 실패 보존: 첫 `pg_regress --use-existing`은 DB 미생성으로
  전부 시작하지 못했고, 첫 schema assertion 회귀는 `setup.out` 갱신 누락으로 setup만
  실패했다. setup 예상 출력을 SQL 입력과 맞추고 새 PG16 인스턴스에서 재실행해 4/4로
  통과했다. 다른 retry 실패는 private evidence에 보존했고 테스트 조건을 약화하지 않았다.
- 보안·고지: dependency graph가 0.1.8과 같아 RustSec `e6f94f3760d5e6c54c1b05f204002263bc025524`
  (1,348 advisories), cargo-audit 0.22.2 검사를 재실행해 561 dependency에서 취약점,
  unsound, yanked 각각 0건, unmaintained 경고 3건으로 통과했다. license text가 확인되지
  않은 `random_word`/`seahash` strict notice 검토는 기존 `needs_review` 상태이며 이번
  변경으로 재검토하지 않았다.
- 한계와 다음 작업: `.8→.9` populated 검증은 2-document fixture뿐이다. 8.2M posting에서
  PK 재구성의 lock duration·peak disk와 회복 절차를 이번 버전으로 재지 않았으며,
  재사용한 latency는 독립 qrels나 SLA 증거가 아니다. cold/sustained 성능, raw-WARC 재수집,
  charset/PDF/office/긴 토큰, host durability와 다른 PG major는 미검증이다. 다음 회차는
  실제 적재 경로에서 잘못된 charset 실패를 재현하고 문서 손실 없이 다룬다.
- 로컬 구현 commit: `d396bd64384c2ee24a13fea42ceb9aa1e3c4c8a6`
  (`perf: cover BM25 postings with primary key index`).
- 공개 상태: 변경은 별도 로컬 branch에 커밋했다. 검토된 publication checkout과
  remote가 없어 GitHub `main`은 갱신하지 않았고 `publish_pending`이다. binary 재배포 및
  production 승인은 열려 있다.

## 2026-10-21 — RustSec 최신 권고 수정 (0.1.8 개발 후보)

- 시작점: 프로젝트 지침·필수 문서·직전 개발 로그와 git 상태를 읽었다. 같은 checkout에서
  실행 중인 개발 세션은 없었다. clean `master`의 `df8636d`가 기존 검증된
  `codex/rustsec-20261020` 커밋 `d9f557f`의 조상임을 확인한 뒤 로컬 `master`만
  fast-forward했다. 별도 worktree·원본 WARC/DB/모델/벡터·기존 artifact는 보존했다.
  `.codex/config.toml`의 `gpt-6-luna`/`max`도 유지했다. 이번 비공개 증거 run ID는
  `20261021-security-update`다.
- 선택 이유: 2026-10-21 RustSec DB revision
  `e6f94f3760d5e6c54c1b05f204002263bc025524`(1,348 advisories)로 재검사하자
  0.1.6 lock은 취약점 10건, 직전 0.1.7 후보는 새
  RUSTSEC-2026-0382 한 건 때문에 실패했다. `rustls-webpki` 0.103.15가 원인이며
  권고가 지정한 최소 수정 버전은 0.103.16이다.
- 변경: `rustls-webpki`만 `cargo update -p rustls-webpki --precise 0.103.16`으로
  잠그고 extension 버전을 0.1.8로 올렸다. DDL/권한 변경 없는
  `0.1.7→0.1.8` upgrade SQL을 추가했다. 검색 코드·tokenizer·BM25/vector 수학·입력
  동작은 바꾸지 않았다. WARC/문서 corpus 입력은 사용하지 않았다. 의존성 입력은 위
  RustSec DB revision에 고정했고 Cargo.lock SHA-256은
  `14bb64b18a50eb1675bc385e9a5537ca4f3be987fce7e0e47862ef5de77c4b6f`다.
- RustSec: 격리 설치한 cargo-audit 0.22.2로 `make audit-dependencies`를 최신
  revision에서 다시 실행했다. Linux x86_64 Cargo.lock 561개 dependency에서 취약점,
  unsound, yanked는 각각 0건으로 통과했다. `paste`, `proc-macro-error2`, `serde_cbor`의
  unmaintained 경고 3건은 남겼고 무시 규칙은 추가하지 않았다.
- 빌드 환경: 폐기 가능한 Linux x86_64 컨테이너, PostgreSQL 16.15, Rust 1.96.0,
  pgrx 0.19.1, 4 CPU/16 GiB. `cargo fetch --locked --target x86_64-unknown-linux-gnu`,
  `cargo rustc --locked --offline --lib --release --no-default-features --features pg16`
  및 `PGRX_BUILD_FLAGS="--locked --offline" cargo pgrx package --pg-config
  /usr/bin/pg_config --no-default-features --features pg16`가 통과했다. 첫 release build는
  26분 57초였다. `tools/check_release_artifacts.py --version 0.1.8 --pg-major 16`도
  통과했다. 개발 artifact `.so` SHA-256은
  `eb6aaf6b5b6b91f805e3223c39df8d555be3bbdb61b48b17f69eae6087976dd1`이다. 기존 0.1.7
  artifact는 수정하지 않았다.
- PG16.15 실제 검증: 설치 후 새 DB의 `CREATE EXTENSION pgwarc_lance`와
  `tests/test.sql` SQL smoke가 통과했다. `pg_regress`의 setup/functions/search/security
  4/4도 통과했다. release artifact를 이용한 `postgres:16.6` disposable container에서
  BM25 문서 2건, WARC row 2건, Lance row 2건을 적재한 뒤
  `ALTER EXTENSION ... UPDATE TO '0.1.8'`을 실행했다. extension version·검색 함수·각
  데이터 개수와 Lance version 보존 검사가 통과했다. mode 600인 기존 0.1.7 artifact의
  control 파일을 포함한 원본은 읽기 전용으로 두고, hash가 같은 별도 테스트 사본의 파일
  권한만 조정해 썼다. 비교에 사용한 기존 0.1.7 `.so` SHA-256은
  `da0943c06b46902d8281da68080eba851b739878ccf816f3c17771b51c3233e4`이다.
  실제 설치 명령은 `PGRX_BUILD_FLAGS="--locked --offline" cargo pgrx install --pg-config
  /usr/bin/pg_config --no-default-features --features pg16`이며, 새 DB에서
  postgres 사용자로 `cargo pgrx start pg16`을 실행하고,
  `psql -X -v ON_ERROR_STOP=1 -f tests/test.sql`과
  `pg_regress --host localhost --port 28816 --user postgres --use-existing
  --dbname=pgwarc_lance_regress --inputdir=tests/pg_regress setup functions search security`를
  실행했다. populated artifact 비교에는 `python3 tools/smoke_release_upgrade.py
  --old-version 0.1.7 --new-version 0.1.8 --pg-major 16 --postgres-image postgres:16.6`을
  사용했다.
- license 재검토: 같은 lock에 대해 Linux x86_64 PG16 target metadata 440개 dependency를
  다시 수집했다. strict collector 결과는 `needs_review`, exit 2이며
  `random_word` 0.5.2와 `seahash` 4.1.0의 primary license text가 계속 없다. 기준을 낮추지
  않았고 binary redistribution은 승인하지 않았다.
- 보존한 실행 설정 실패와 수정: 첫 PG 회귀 시도는 root로 `initdb`를 실행해 거부됐다.
  이후 설치는 성공했으나 postgres 사용자가 private validation mount의 실행 권한과 원본
  checkout의 상위 경로 권한을 얻지 못해 서버 시작 전 중단됐다. disposable run 디렉터리에
  접근 권한을 주고 현재 unignored source 153개 파일의 read-only 사본을 만들어 다시 실행해
  해결했다. 제품 코드나 회귀 기준은 바꾸지 않았다. 실패/성공 로그는 private run의
  `native-pg16-install-regression*.log`, artifact upgrade 로그와 exit 파일에 보존했다.
- 한계와 다음 작업: 최신 dependency scan 및 PG16 설치/upgrade 검증만 완료했다. strict
  binary notice review, 실제 raw-WARC 재적재와 charset/PDF/office/긴 토큰 추출, 독립 qrels,
  SLA, 다른 PG major 및 host/storage durability는 미완료이며 0.1.8은 production 후보 승인이나
  정식 release가 아니다. 다음 회차에는 실제 Common Crawl 적재 경로의 charset 실패를 다시
  재현하고 원인을 검증 가능한 단위로 다룬다.
- 로컬 구현 commit: `45ff06f` (`security: update rustls-webpki to 0.103.16`)에 의존성,
  version/upgrade SQL, 검증 결과와 공개 문서 변경을 커밋했다.
- 공개 반영: 공개 GitHub `main`은 갱신하지 않았다. 이 checkout에는 remote나 검토된 별도
  publication checkout이 없어 `publish_pending`이다.

## 2026-10-20 — RustSec dependency remediation (0.1.7 development candidate)

- 시작점: isolated branch `codex/rustsec-20261020`, source base `df8636d`
  (`master`는 수정하지 않음). 같은 checkout에서 다른 개발 세션이 실행 중인 것을
  확인해 별도 worktree를 사용했다. 과거 `pglance` 저장소와 프로덕션, 원본 WARC·DB·모델·벡터,
  기존 artifact/manifest는 수정하지 않았다. `.codex/config.toml`의
  `gpt-6-luna`/`max` 값도 변경하지 않았다. 비공개 run ID는
  `20261020-rustsec-followup`이다.
- 선택 이유: 직전 회차의 다음 보안 작업이다. RustSec DB revision
  `999ec89d5d3f10f20be14750ec8c2d8b44e0ac9b`(1,338 advisories) 기준 0.1.6은
  `quinn-proto` 0.11.15와 `rustls` 0.23.45에서 취약점 9건을 보고했다.
- 변경: `cargo update -p quinn-proto --precise 0.11.18` 및
  `cargo update -p rustls --precise 0.23.46`으로 advisory가 지정한 최소 수정 버전을
  적용했다. lock graph에는 호환되는 `getrandom`, `rand`와 `rand_pcg` 0.10.2도 반영됐다.
  검색 코드, tokenizer, ranking 수식, SQL 권한과 데이터 형식은 바꾸지 않았다.
  extension version을 0.1.7로 올리고 DDL/grant 없는 0.1.6→0.1.7 upgrade SQL을 추가했다.
- 보안 검증: `make audit-dependencies`가 같은 advisory revision에서 561개 crate
  dependency를 검사해 취약점, unsound, yanked 패키지 각각 0건으로 통과했다.
  `paste` 1.0.15, `proc-macro-error2` 2.0.1, `serde_cbor` 0.11.2의 기존
  unmaintained 경고 3건은 남았다. Cargo.lock SHA-256은
  `677a80ca664ff92defd3f7f8f4106639719ff72af8f2b9fcbf1a756d3ecbc19f`이다.
- 빌드 환경: Linux x86_64, PostgreSQL 16.15, Rust 1.96.0, pgrx 0.19.1,
  전용 4 CPU/16 GiB 컨테이너. 아래 locked/offline optimized build와 pgrx package가
  통과했다. 냉간 build는 24분 27초였다. `DIST_DIR`은 전용 private artifact 경로로
  설정했다.
  ```bash
  cargo rustc --locked --offline --lib --release --no-default-features --features pg16 -- -C link-arg=-Wl,--no-gc-sections
  PGRX_BUILD_FLAGS="--locked --offline" cargo pgrx package --pg-config /usr/bin/pg_config --no-default-features --features pg16 --out-dir "$DIST_DIR"
  python3 tools/check_release_artifacts.py --dist-dir "$DIST_DIR" --version 0.1.7 --pg-major 16
  ```
  package structure/version 검사도 통과했다. 새 개발 바이너리 SHA-256은
  `da0943c06b46902d8281da68080eba851b739878ccf816f3c17771b51c3233e4`이며,
  기존 0.1.6 artifact `efd2dcad09ea88ffe7f2e1cf3c32d2cc55e79d720804e0c6485ec385b7b19ceb`와
  분리 보존했다.
- PG16.15 실제 검증: 새 DB에서 extension 0.1.7 설치 및 `tests/test.sql`이 통과했다.
  `pg_regress`의 setup/functions/search/security 4/4도 통과했다. 기존 0.1.6 binary로
  별도 DB에 BM25 문서 2건, WARC row 2건, Lance row 2건을 적재하고 0.1.7 binary로
  교체한 후 populated extension upgrade를 실행했다. extension version, 함수 호출,
  BM25 document/search 수, WARC row 수, Lance row 수와 dataset version 보존 assertion이
  통과했다. 모든 DB, PGDATA, Lance fixture는 disposable 컨테이너의 전용 scratch에 있었다.
  검증 명령은 `CREATE EXTENSION pgwarc_lance`, `psql -v ON_ERROR_STOP=1 -f tests/test.sql`,
  `pg_regress --host localhost --port 5432 --user postgres --use-existing --dbname=pgwarc_lance_regress`
  (setup/functions/search/security), 그리고
  `ALTER EXTENSION pgwarc_lance UPDATE TO '0.1.7'` 후의 데이터 보존 assertion이다.
- 서드파티 증거: 갱신 lock 기준 PG16 target metadata는 extension 제외 dependency
  440개다. notice strict collector는 `random_word` 0.5.2와 `seahash` 4.1.0의
  primary license text 공백으로 `needs_review`, exit 2를 반환한다. 이 두 증거 공백은
  이전과 같으며 binary redistribution은 승인하지 않았다.
- 보존한 실패/복구: `cargo audit --locked`는 cargo-audit에서 지원되지 않아 검사를
  시작하지 못했다. 옵션을 제거하고 프로젝트의 `make audit-dependencies` gate를 실행해
  통과했다. 첫 PG 서버 시작은 격리 bind mount의 부모 통과 권한 부족으로 실패했으며,
  비공개 disposable workspace에 통과 권한을 추가한 뒤 PG16 검증 전부가 성공했다.
  테스트 데이터나 판정 기준은 바꾸지 않았다.
- 한계와 다음 작업: 0.1.7은 development candidate이며 production/SLA 승인 대상이 아니다.
  독립 qrels 품질, 실제 raw-WARC 재적재, charset 추출 오류, PDF/office 본문 추출,
  매우 긴 토큰 처리, 모든 PG major, host/storage durability는 이 회차에서 검증하지 않았다.
  다음으로 실제 입력 경로에서 charset·PDF/office·긴 토큰 실패를 재현하고 원인을 하나씩
  선택해 처리하되 실패 문서를 조용히 버리거나 검색 의미를 바꾸지 않는다.
- 공개 반영: `publish_pending`. 검토된 별도 publication checkout/인증 연결이 없어
  GitHub `main`에는 게시하지 않았다. 로컬 결과는 `codex/rustsec-20261020` 브랜치에
  커밋했다.

## 2026-10-20 — BM25 query-term estimates (0.1.6 development candidate)

- 시작점: `c92c74a3b4e173c4ffdcf0ec0d5b5f5141024250`, clean `master`.
  기존 개발 로그는 없었고 같은 checkout의 별도 개발 작업은 확인되지 않았다.
  과거 별도 저장소와 원본 WARC·DB·모델·벡터·릴리스는 수정하지 않았다.
  비공개 증거 run ID는 `20261020-bm25-plan`이며 원문·벡터·호스트 경로·원시 로그는
  공개 소스에 포함하지 않는다.
- 선택 이유: Common Crawl 0.1.5의 BM25/hybrid warm tail latency 회귀.
  전용 PG16 인스턴스로 복제한 corpus에서 문서/metadata 21,873개,
  posting 8,240,889개와 E5 384차원 벡터 21,825개를 다시 확인했다.
- 변경: scoring posting scan에도 동일한 bound query-term `ANY` 조건을 명시한다.
  materialized frequency CTE의 값이 보이지 않아 생기는 cardinality 과대 추정과
  불필요한 JIT 비용을 줄인다. tokenizer·BM25 수식·정렬·단일 statement snapshot·권한은
  유지한다. crate와 lockfile의 프로젝트 버전만 0.1.6으로 올리고 DDL/권한 변경 없는
  0.1.5→0.1.6 upgrade SQL을 추가했다. 기존 바이너리와 manifest는 보존한다.
- 초기 진단: warm `privacy` SQL의 기존 계획은 총 cost 547,888.87,
  JIT 257.478ms, 실행 276.858ms였다. 같은 결과를 내는 조건 명시 후보는
  cost 17,965.49, JIT 없음, 실행 28.191ms였다. 두 계획의 shared read와 temp
  read/write는 0이었다. 5회 wall 중앙값은 265.20→27.33ms,
  backend user CPU는 128→12 ticks였다(5회 합계, 초당 100 ticks).
  이 초기 진단은 한 질의의 증거이며 전체 부하/SLA 결과로 확대하지 않는다.
- 실패 보존: 복제 클러스터의 최초 기동 대기 60초 초과, cold native 질의·외부 SQL
  진단·corpus count의 30초 timeout을 보존했다. 파일별 복구 동기화가 오래 걸려 전용
  인스턴스만 `recovery_init_sync_method=syncfs`로 재기동했으며 `fsync=on`을 확인했다.
  cold 진단 준비에는 이후 180초 제한을 사용하고, warm 부하의 제한과 구분한다.
- 보안: cargo-audit 0.22.2, 최신 RustSec revision
  `999ec89d5d3f10f20be14750ec8c2d8b44e0ac9b`(1,338 advisories)에서
  `quinn-proto`·`rustls` 취약점 9건으로 non-zero. 무시 목록이나 기준 완화는 없다.
  성능 비교의 의존성은 고정했으며 이 후보는 운영/바이너리 배포 승인 대상이 아니다.

### 최종 검증

- Linux x86_64, PostgreSQL 16.15, Rust 1.96.0, pgrx 0.19.1.
  전용 컨테이너는 4 CPU/16 GiB, shared_buffers 128 MiB, work_mem 4 MiB,
  `fsync=on`, `track_io_timing=on`, `jit=on`이었다. CPU는 공유 호스트에서 예약하지 않았다.
  초기 외부 SQL 진단만 `max_parallel_workers_per_gather=0`으로 SPI 내부와 비교했으며,
  native/load 측정은 기본 planner 설정을 유지했다.
- 실제 native `privacy` 계획은 JIT 336.686→0ms, 실행 375.273→47.229ms,
  PostgreSQL read time 8.873→6.483ms였다. shared-buffer read는 남았고
  물리 디스크 또는 Lance 전체 I/O를 이 수치로 판단하지 않는다. native 8개 계획은
  이 설정에서 temp read/write 0이었다. 별도 `jit=off` 진단과 backend CPU ticks도 보존했다.
- 원본 프로토콜과 같은 8개 질의·k=5·client당 warm-up 8회/요청 128회·persistent session·
  URL/160자 snippet fetch로 1/8 clients를 측정했다. embedding/connect 시간은 제외했다.
  보존 바이너리를 검증 전용 C wrapper로 동일 DB·dataset에 연결하고
  A는 0.1.1→0.1.5→0.1.6, B는 역순으로 실행했다. 13,824개 요청 모두 성공했다.

| 바이너리 | 8-client BM25 p95 A / B (ms) | Hybrid p95 A / B (ms) |
| --- | ---: | ---: |
| 0.1.1 | 621.48 / 592.86 | 645.67 / 752.75 |
| 0.1.5 | 1195.95 / 1736.09 | 1678.72 / 1588.25 |
| 0.1.6 | 572.46 / 500.84 | 645.47 / 625.81 |

- 24개 질의의 BM25/hybrid 48개 결과가 기존 ID 및 점수(1e-9)와 일치했다.
  복제 0.1.5는 10월 1일 보존 결과와도 일치했고, 보존 0.1.1도 같은 검사를 통과했다.
  독립 Common Crawl BM25 계산 5개(1e-9), NumPy squared-L2 24개(1e-5)를 통과했다.
  독립 qrels 기반 품질 측정과 구분한다.
- `cargo rustc --locked --offline --lib --release --no-default-features --features pg16 -- -C link-arg=-Wl,--no-gc-sections`
  성공(18분 27초). `PGRX_BUILD_FLAGS="--locked --offline"`로 별도 경로의
  `cargo pgrx package`도 성공했다. 빌드 target·DB·Lance·검증 컨테이너의 데이터 임시는
  전용 데이터 디스크 경로를 사용했다. source control template는 그대로 두고 생성된
  control의 0.1.6과 새 upgrade SQL을 실제 fresh install/upgrade로 검증했다.
  새 개발 바이너리 SHA-256은
  `efd2dcad09ea88ffe7f2e1cf3c32d2cc55e79d720804e0c6485ec385b7b19ceb`이며 기존 artifact/manifest와 구분했다.
- PostgreSQL SQL 회귀 `setup/functions/search/security` 4/4, SQL smoke,
  importer DB smoke, runtime 17, search-memory 22, snapshot 7, upgrade/backup 13 통과.
  서로 다른 old/new 바이너리의 populated 0.1.5→0.1.6 upgrade와 fresh artifact install은
  별도 `postgres:16.6` 컨테이너에서 통과했다. 이외 native DB 검사는 PG16.15였다.
  100,000문서/800,000 posting·64 KiB work_mem 강제 hash stress의 temp delta는
  78,003,228 bytes, 관측 VmHWM은 236,720 KiB였으며 timeout/quota 오류 후 backend 재사용을 확인했다.
  120회 writer transaction과 400회 reader search가 겹쳤고 완전한 두 상태를 310/90회 관측했다.
- `python3 -m unittest discover -s tests -p 'test_*.py'` 433개, standalone Rust tokenizer,
  `cargo fmt --all --check`, 문서 검사 통과. 새 합성 시험 개수를 늘리는 대신 기존 회귀와
  실제 corpus 비교를 사용했다.
- importer DB 첫 시도는 검증 URI 부모가 PostgreSQL OS 계정에 쓰기 불가하여 실패했다.
  실패 DB/로그를 보존하고 새 DB·쓰기 가능한 전용 scratch URI에서 동일 fixture로 통과했다.
  제품 코드·판정 기준을 바꾸지 않았다.
- 서드파티 metadata 471개 package/version/license/source identity와 모든 non-project
  lock entry가 이전과 같음을 확인했다. 정상/build 437개 notice inventory는 기존 증거를
  재사용했다. strict notice 재수집 시도는 준비 중 중단되어 완료로 세지 않으며,
  `random_word`·`seahash` primary text 공백 2건은 유지한다. 새 바이너리의 재배포 승인은 없다.

### 한계와 다음 작업

- 이 회차는 준비된 corpus의 warm 성능 및 검색 계산 검증이다. 새 raw-WARC 적재,
  독립 operational qrels, 2/4-client 재측정, sustained/cold SLA, 실제 host/power-loss 복구,
  Lance 개별 CPU/I/O flamegraph는 미검증이다. 낮아진 p95를 production 승인으로 바꾸지 않는다.
- 다음 우선 작업은 최신 advisory를 다시 확인하고 `quinn-proto`·`rustls`를 수정 범위로
  갱신하여 locked 빌드·DB 회귀·현재 RustSec/yanked gate를 재검증하는 것이다.
  이후 실제 적재 경로의 charset·PDF/office·매우 긴 토큰을 처리하되 검색 의미를 보존하고
  실패 문서를 묵살하지 않는다. 기존 원본과 versioned artifact는 계속 보존한다.
- 공개 반영은 `publish_pending`. 현재 checkout은 controller `master`이고 검토된 별도
  publication checkout 연결은 없다. 기존 인증을 사용하는 public `main` checkout의
  연결·검수가 필요하다. 직접 push/merge/force-push, 새 인증, release/binary upload,
  운영 배포·메일/DM·새 자동화·서브에이전트는 실행하지 않았다.

## 2026-10-24 — Oversized BM25 terms (0.1.16 development candidate)

- 시작점: `16db637` (0.1.15)에서 격리한 `codex/20261024-long-token-bm25` worktree.
  주 checkout의 동시 개발 프로세스와 과거 `pglance` 저장소는 수정하지 않았다.
  반복된 BM25/hybrid 재측정에서 host-load 변동으로 원인 분리와 비교가 성립하지 않아,
  같은 profile을 반복하지 않고 Common Crawl에서 확인된 2,000바이트 초과 토큰 실패를
  선택했다. 비공개 run ID `20261024-long-token-bm25`; 원본 입력 checksum
  `4b6b55dc25afdb8ef9bb0e48213faa73cf07756f195b0863ba2693638bf784f8`.
- 관찰 근거: 입력 감사에서 8개 문서의 ASCII alphanumeric run이 2,000바이트를 넘고
  최댓값은 2,298,914자였다. 별도 7,680바이트 token fixture는 이전 경로에서
  PostgreSQL B-tree row-size 오류(SQLSTATE 54000)를 재현했다. 원본 WARC, 기존 DB,
  Lance dataset, 모델/vector, versioned artifact는 읽기 전용으로 두었다.
- 변경: 2,000바이트 초과 token은 posting table에 고정 길이 SHA-256 key를 쓰고
  `bm25_long_term`에 원문을 보관한다. 검색은 원문을 복원해 기존 term 정렬로 점수를
  합산하고 해시 충돌을 검사한다. function/bulk/COPY import 모두 같은 규약을 쓰며,
  importer는 0.1.16 이상을 사전 확인하고 2,500,000 UTF-8 byte 초과 token은 SQL 실행
  전에 실패한다. BM25/hybrid 질의 byte limit은 실제 최장 관측값을 수용하도록
  2,500,000으로 올렸다. dictionary는 config dump 대상이고 동시 writer 안전성을 위해
  append-only로 유지한다. populated 0.1.15 update는 기존 raw posting이 남지 않도록
  영향 document를 새 함수로 재색인한다. tokenizer 규칙·BM25 수식·기본 검색 의미는
  바꾸지 않았다.
- PG16 release 증거: Linux x86_64, PostgreSQL 16.15, Rust 1.96.0, pgrx 0.19.1에서
  `cargo pgrx install --pg-config /usr/bin/pg_config --release --no-default-features
  --features pg16` 성공(첫 전체 build 21분 9초). Cargo.lock SHA-256은
  `a44685a6005aa3d7ee97733066cf70be1b326f3b855b7e081a03c1e5512fe51f`; build 전후
  checksum이 일치했다. 전용 builder 및 PostgreSQL data directory는 run 격리 경로를
  사용했다. 설치 `.so`, control, 0.1.16 install SQL과 0.1.15→0.1.16 migration의
  hashes는 private evidence에 보존했다.
- 실제 검사: fresh install 및 SQL 회귀 `setup/functions/search/security` 4/4,
  `tests/test.sql`, WARC importer DB fixture의 function/bulk/COPY 3개 경로 통과.
  각 경로는 4개 레코드와 7,680바이트 token을 적재했고 긴 token 검색과 혼합
  short/long query 점수가 일치했다. `db_search_memory.py`의 독립 BM25 계산이 통과했고
  100,000문서/800,000 posting, work_mem 64 KiB fixture에서 78,003,228-byte temp spill,
  관측 backend VmHWM 230,652 KiB를 기록했다. timeout과 1 MiB temp quota 이후 backend
  재사용도 통과했다. fresh backup/restore와 least-privilege ingest replay도 통과했다.
- populated upgrade는 실제 0.1.15 install SQL에 2,001-byte raw posting을 넣은 뒤
  0.1.16으로 업데이트해 `legacy + 긴 token` 검색을 확인했다. table registration 6개,
  selective reader grant, Lance version과 restore 검색을 보존했다. 첫 migration 시도는
  update script 안의 temp table이 확장 소유 객체가 되어 commit 때 삭제되지 않는 오류로
  rollback됐다. 임시 테이블을 제거하고 document ID를 배열에 모아 처리하도록 고친 뒤
  전체 upgrade/backup/restore가 통과했다. 예상 출력만 추가된 첫 pg_regress 실패는
  회귀 출력을 검토해 갱신한 뒤 4/4 통과했다. 초기 `tests/test.sql` 호출은 테스트 DB의
  `CREATE EXTENSION` 준비 누락으로 실패했으며 올바른 초기화로 재실행해 통과했다.
- Python 및 source 검사: `python3 -m unittest discover -s tests -p 'test_*.py'`
  435개 통과, `py_compile`, `cargo fmt --all -- --check`, `make check-docs` 통과.
  최종 문서 편집 후 재검사할 것. 현재 RustSec audit 및 새 lock에 묶인 license notice
  재수집은 완료하지 못했다. cargo-audit 0.22.2 도구 build를 시작했으나 회차 시간 제한에
  맞춰 중단했다. 알려진 license text 공백을 해소하거나 binary redistribution을
  승인한 것으로 간주하지 않는다.
- 한계와 다음 작업: 후보 검증은 PG16.15와 고정 fixture에 한정된다. 새 raw-WARC 전체
  재적재, 독립 qrels, sustained/cold SLA, charset·PDF/office 본문 correctness와 다른
  PostgreSQL major는 미검증이다. 다음은 현재 RustSec audit와 lock-tied notice inventory를
  재검사하고, 이후 host-load를 통제한 0.1.5 BM25/hybrid profile을 재현한다. 운영 승인은
  아니다.
- 공개 반영은 `publish_pending`. 검토된 별도 publication checkout이 없어 GitHub `main`은
  갱신하지 않았다. 이 결과는 로컬 브랜치에만 커밋하며 공개 release/binary upload 및
  운영 배포를 수행하지 않는다.
- 로컬 commit: `Support oversized BM25 tokens` (branch HEAD).

## 2026-10-24 — RustSec dependency remediation (0.1.17 development candidate)

- 시작점은 clean `63640b5` (0.1.16) worktree였고, 작업 중 같은 checkout을 쓰는 별도
  개발 프로세스는 없었다. 전용 branch `codex/20261024-rustsec`에서 0.1.17을 준비했다.
  `.codex/config.toml`은 `gpt-6-luna` / `max`로 유지됐다. 비공개 검증 run ID는
  `20261024-rustsec-recheck`이며 입력 corpus, 이전 immutable artifact, DB와 Lance
  dataset은 읽기 전용으로 두었다.
- 선택 이유: 직전 0.1.16 개발 기록의 다음 작업은 최신 RustSec와 lock별 notice를
  재검사하는 것이었다. 최신 advisory DB에서 `ctutils` 0.4.2
  (RUSTSEC-2026-0385)와 `tokio` 1.52.3 (RUSTSEC-2026-0386)이 발견됐다.
  수정 범위의 최소 버전 `ctutils` 0.4.4와 `tokio` 1.53.3으로 Cargo.lock을
  갱신하고, crate version을 0.1.17로 올렸다. `.16→.17` migration은 catalog 변경
  없이 버전만 전진한다. 검색 코드·tokenizer·점수·기본 의미는 변경하지 않았다.
- 보안 근거: `cargo-audit` 0.22.2가 RustSec DB revision
  `21d909e4c8bd4e14c7aea58bf980c881a0053ab1`의 1,352건 advisory로 561개 lockfile
  dependency를 검사했다. .16 lock은 두 deny 항목으로 실패했다. .17 lock은 알려진
  vulnerability, unsound, yanked package 각각 0건으로 통과했고, `paste` 1.0.15,
  `proc-macro-error2` 2.0.1, `serde_cbor` 0.11.2의 미관리 경고 3건은 남았다.
  Linux x86_64 / PG16 대상의 440 package notice inventory는 `needs_review`, exit 2로
  `random_word` 0.5.2와 `seahash` 4.1.0의 primary license text 공백을 보고했다.
  Cargo.lock SHA-256은
  `0696a9cc0b14c0f8dcea5e1b6a35b6cd63afc1df971380c89a7461c3cf52638a`이며 binary
  redistribution은 승인되지 않았다.
- PG16 package: Linux x86_64, PostgreSQL 16.15, Rust 1.96.0, cargo-pgrx 0.19.1에서
  `cargo pgrx package --manifest-path Cargo.toml --pg-config /usr/bin/pg_config
  --no-default-features --features pg16 --out-dir "$DIST_DIR"` 성공했다
  (`$DIST_DIR`는 disposable builder의 출력 위치).
  `PGRX_BUILD_FLAGS='--locked --offline'`를 사용했고 optimized release link는
  30분 55초 걸렸다. 추출 artifact 검사도 통과했다. 0.1.17 `.so` SHA-256은
  `acce0b1e05869fb5ed628dab51cac410310db9b780bec59222da2e0aff4a8d01`이다.
- PG16 실제 DB 검증: 폐기 가능한 PG16.15 instance에서 fresh package를 설치하고
  SQL `setup/functions/search/security` 회귀 4/4를 통과했다. 이전 0.1.16 바이너리
  (`2c4c164877a4c2b6433f055785fe4047a7c0612fe3957f6dde0b1a4abcbb3949`)로 BM25 2건,
  WARC row 2건, Lance vector 2건을 만든 뒤 .17로 교체해 populated upgrade했다.
  `ALTER EXTENSION`, BM25 검색, WARC row 수, Lance row 수·dataset version 확인이
  통과했다. 첫 upgrade smoke는 private staging 파일이 mode 0600으로 복사되어
  PostgreSQL이 control file을 읽지 못했다. artifact 권한을 올바른 read mode로
  설정해 재실행했고 같은 입력으로 통과했다. `cargo fmt --all -- --check`,
  `git diff --check`, `make check-docs`도 통과했다.
- 준비 중 `cargo metadata --offline`은 lock에 있던 `rustls` 0.23.46의 source cache
  부재로 실패했고, registry source를 확보한 locked online metadata로 재실행했다.
  strict notice 수집의 non-zero는 두 license text 누락을 뜻하며 실패를 통과로
  재분류하지 않았다. 초기 smoke의 permission 실패도 위와 같이 수정해 재검증했다.
- 한계와 다음 작업: 이번 변경은 dependency security / PG16 release 경로 검증이다.
  다른 PostgreSQL major, 독립 relevance qrels, fresh raw-WARC 적재, benchmark,
  cold/sustained SLA와 native 라이선스 재배포 검토는 이번 회차에 검증하지 않았다.
  다음 우선 작업은 Common Crawl에서 확인된 BM25/hybrid 지연을 실행계획, CPU, I/O,
  temp spill로 분해하고 같은 환경·질의·artifact 조건에서 0.1.5 기준과 재비교하는
  것이다. 이유가 확인될 때만 최소 검색 변경을 한다.
- 공개 반영은 `publish_pending`. 검토된 별도 publication checkout이 없어 GitHub
  `main`에는 반영하지 않았다. 검증한 변경은 이 branch의 commit `6d31947`
  (`Update RustSec dependencies for 0.1.17`)에 로컬 반영했다.
