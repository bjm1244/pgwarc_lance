# Contributing to pgwarc_lance

환영한다. 다만 이 프로젝트는 아직 프로덕션 준비가 끝나지 않은 프로토타입이다. 기여 우선순위는 기능 추가보다 **정합성·안전성·검증 증거**다.

## 시작하기

```bash
export PGWARC_DEV_PASSWORD="$(openssl rand -base64 24)"
PG_MAJOR=16 docker compose build     # PostgreSQL 16 + Rust/cargo-pgrx 빌드 이미지
make up                              # 컨테이너 기동
make test                            # SQL smoke
make test-unit                       # tokenizer unit harness
make test-uri-policy                 # 경로 경계·심볼릭 링크 검사
make test-regress                    # pg_regress (setup/functions/search/security)
make test-warc                       # Python 하네스 (unittest)
make test-runtime-safety             # 실제 DB 권한·동시성·취소·복구
make test-backup-restore              # SQL/Lance 백업·복원
make check-docs                      # 문서-하네스 정합성
```

전체: `make verify`. Docker 없이는 빌드할 수 없다(`pgrx`가 PostgreSQL 서버 헤더와 `PGRX_HOME`을 요구한다).

## 작업 원칙

1. **SQL 함수 표면은 신중하게.** 배포된 SQL 함수 시그니처는 회수하기 어렵다. SQL 함수 추가/변경은 필요성 근거(재현 스크립트 또는 실패 사례)를 PR에 포함한다.
2. **SQLite가 아니라 PostgreSQL이다.** 서버 백엔드 프로세스 안에서 동작한다. 스레드 생성, fork 안전성, 인터럽트(취소) 처리, 메모리 상한을 항상 고려한다. 취소 가능해야 하는 루프에는 인터럽트 검사를 넣는다.
3. **SPI는 바인딩 파라미터를 사용한다.** 문자열 보간으로 SQL을 조립하지 않는다.
4. **에러는 `ereport!`로.** `panic!`으로 에러를 전달하지 않는다(적절한 SQLSTATE 사용).
5. **증거 없는 “완료” 금지.** Synthetic fixture 결과를 실제 corpus 품질 기준선이나 성능 gate로 승격하지 않는다. 문서에 그 경계를 명시한다.

## 커밋·PR

- 커밋 메시지는 한 줄 요약 + 필요한 경우 본문. 내역과 근거를 남긴다.
- `make check-docs`가 통과해야 한다. 문서(README/ROADMAP/BLUEPRINT/BENCHMARKS/PACKAGING/QUALITY)와 Makefile target, 하네스 스크립트는 서로 참조가 검증된다.
- 새 하네스 스크립트는 `tests/test_*.py`에 대응 테스트를 추가한다.
- Python 변경 시 `python3 -m unittest discover -s tests -p 'test_*.py'`, Rust 변경 시 `cargo fmt`를 실행한다.

## 라이선스

기여는 Apache License, Version 2.0 하에 배포되는 것에 동의하는 것으로 간주한다([LICENSE](LICENSE)). 서드파티 코드를 포함할 때는 라이선스와 출처를 [NOTICE](NOTICE)에 추가한다.

## 행동 강령

[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)를 따른다.
