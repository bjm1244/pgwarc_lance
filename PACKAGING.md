# pgwarc_lance Packaging

작성일: 2026-07-01

이 문서는 로컬 Docker 기반으로 `pgwarc_lance` extension artifact를 만들고 설치하는 절차와 version upgrade 정책을 정리한다.

## Version Coordinates

- Extension/crate version: `0.1.0` (`Cargo.toml`)
- Control file version: `default_version = '@CARGO_VERSION@'`
- PostgreSQL build target: PostgreSQL 16 by default; optional Docker build arg `PG_MAJOR=13..17`
- Rust toolchain: `1.96.0`
- cargo-pgrx: `0.19.1`
- Current validation scope: PostgreSQL 16 full local verification, optional `CREATE EXTENSION` and artifact install smoke for PG13-17

`Cargo.toml`에는 `pg13`부터 `pg17` feature gate가 남아 있다. Dockerfile은 `PG_MAJOR` build arg로 `postgres:${PG_MAJOR}`, 해당 server dev package, `cargo pgrx init --pg${PG_MAJOR}`, matching `pg${PG_MAJOR}` Cargo feature를 함께 선택한다. 기본 local verification은 PostgreSQL 16 기준이며, 다른 major는 `make smoke-pg-version` 또는 `make pg-version-matrix`로 fresh install smoke를 확인한다.

## Local Verification

Full local verification is:

```bash
make verify
```

`make verify` runs all local harnesses through Docker and then checks documentation consistency, Python syntax for helper scripts, and `git diff --check`. Fast iteration can still use narrower targets such as `make check-docs`, `make test-warc`, `make test-unit`, or `make bench-*`.

Optional PostgreSQL major smoke checks:

```bash
PG_MAJOR=17 make smoke-pg-version
make pg-version-matrix
```

These targets build a major-specific image, start a disposable container, run `CREATE EXTENSION pgwarc_lance`, and call `hello_pgwarc_lance()`. They do not replace `make verify`, which remains the PostgreSQL 16 full regression baseline.

Artifact validation and install smoke:

```bash
make check-release-artifacts
make smoke-release-artifacts
make doctor-release-upgrade-inputs
UPGRADE_OLD_VERSION=0.0.9 make smoke-release-upgrade
make release-artifacts-matrix
make smoke-release-artifacts-matrix
UPGRADE_OLD_VERSION=0.0.9 make smoke-release-upgrade-matrix
BENCH_OUT_DIR=benchmarks/local make report-release-artifacts
BENCH_OUT_DIR=benchmarks/local make report-release-smoke-matrix
BENCH_OUT_DIR=benchmarks/local make finalize-release-run
```

`make check-release-artifacts` validates an already extracted `DIST_DIR` without starting PostgreSQL. It checks that the shared library is an ELF object, the control file has the expected `default_version` and `module_pathname`, and the generated SQL contains core function symbols used by the extension surface.

`make smoke-release-artifacts` goes one step further: it starts a disposable official `postgres:<PG_MAJOR>` container, copies the artifacts from `DIST_DIR` into the target server's `pg_config` paths, then runs `CREATE EXTENSION pgwarc_lance` and `hello_pgwarc_lance()`. Use it after `make release-artifacts` when you want to check that runtime dependencies and install paths work outside the build image.

`make smoke-release-upgrade` validates old and new artifact directories, copies both versions plus `pgwarc_lance--OLD--NEW.sql` into a disposable official PostgreSQL container, creates the old extension version, and runs `ALTER EXTENSION pgwarc_lance UPDATE TO 'NEW'`. Set `UPGRADE_OLD_VERSION`; override `UPGRADE_OLD_DIST_DIR`, `UPGRADE_NEW_VERSION`, or `UPGRADE_NEW_DIST_DIR` when testing non-default artifact layouts.

`make doctor-release-upgrade-inputs` is the DB-free and Docker-free preflight for that smoke. It reads the current `Cargo.toml` version, scans `dist/` for actually extracted `pgwarc_lance-<version>-pg<major>/` artifact directories, and checks that a distinct old artifact plus a non-empty `pgwarc_lance--OLD--NEW.sql` upgrade script exist for the requested versions. It writes `release_upgrade_input_doctor.json`/`.md` (with `BENCH_OUT_DIR`), records the available versions, missing prerequisites, resume requirements, and resume commands, and exits non-zero with `valid=false`/`status=blocked` when a legitimate upgrade input pair is absent. It never installs an extension and never claims an upgrade pass, so a blocked preflight must not be reported as a successful `ALTER EXTENSION UPDATE`.

`make release-artifacts-matrix`, `make smoke-release-artifacts-matrix`, and `make smoke-release-upgrade-matrix` iterate `PG_MATRIX` (`13 14 15 16 17` by default). The extraction matrix builds and checks each major's artifacts; the install and upgrade smoke matrices reuse already extracted `dist/pgwarc_lance-<version>-pgXX/` directories so checks can be rerun without rebuilding images.

`make report-release-artifacts` validates the extracted matrix without Docker and emits a status summary. Set `BENCH_OUT_DIR` to write `release_artifacts_matrix.json` and `release_artifacts_matrix.md` containing per-major validity, file sizes, and SHA-256 digests.

`make report-release-smoke-matrix` runs a release smoke target for each major in `PG_MATRIX` and records exit codes, duration, and stdout/stderr tails. The default target is `smoke-release-artifacts`; set `RELEASE_SMOKE_TARGET=smoke-release-upgrade` and pass values such as `RELEASE_SMOKE_MAKE_VARS='UPGRADE_OLD_VERSION=0.0.9'` when you want the upgrade smoke report. Set `BENCH_OUT_DIR` to write `release_smoke_matrix.json` and `release_smoke_matrix.md`. When the target is `smoke-release-upgrade`, `report-release-smoke-matrix` delegates to `make report-release-upgrade-smoke-matrix`, which runs `tools/doctor_release_upgrade_inputs.py` for each major with the same old/new dist, version, and `pg_major` inputs that `smoke-release-upgrade` would receive, before any smoke runs. A `valid=false` major keeps its blocked reason and input identity, still appears as a `release_smoke_matrix.json` result row with `passed=false`, is not passed to `smoke-release-upgrade`, and makes the report exit non-zero. This preserves the existing `release_smoke_matrix.json` schema (the run doctor still validates the result rows) while adding per-major `preflight`, `preflight_passed`, `smoke_skipped`, and `smoke_skipped_reason` fields. Fresh-install/fresh-smoke behavior is unchanged because the original report tool is used for every non-upgrade target.

`make finalize-release-run` runs the artifact matrix report, the release smoke matrix report, and run directory finalization in sequence. It writes packaging reports plus `run_metadata.*`, `run_manifest.*`, and `run_doctor.*` into `BENCH_OUT_DIR`/`RUN_DIR`, using `RUN_DOCTOR_PROFILE=packaging` so both release report artifacts are required. The run doctor also checks `release_artifacts_matrix.json` row consistency and per-file `exists`/`bytes`/`sha256` schema, plus `release_smoke_matrix.json` result rows and pass/exit-code consistency. When `RELEASE_SMOKE_TARGET=smoke-release-upgrade`, the second step is the guarded `report-release-upgrade-smoke-matrix`, so a blocked preflight fails finalization before any `ALTER EXTENSION UPDATE` smoke is attempted.

## Observed PG16 Packaging Smoke

2026-09-12 23:24 KST에 HEAD `5073947` (clean)에서 `PG_MATRIX=16 BENCH_OUT_DIR=/tmp/pglance-release-pg16-20260912-23 make finalize-release-run`을 실행했다.

- release artifact matrix: PostgreSQL 16 `1/1 valid`
- official `postgres:16` install smoke: `1/1 passed`, exit code 0, 3.635s
- packaging run doctor: `valid=true`, errors 0, warnings 0
- artifact sizes: `.so` 187298488 bytes, control 205 bytes, generated SQL 9462 bytes

이 결과는 2026-09-12 당시 PG16 fresh install/package evidence다. 당시에는 PG13-17 전체 matrix를 아직 실행하지 않았고, old/new artifact와 versioned upgrade SQL이 없어 `ALTER EXTENSION UPDATE`도 미측정이었다. 실제 WARC quality baseline과도 별개의 증거다. Run directory는 `/tmp/pglance-release-pg16-20260912-23` temporary path이며, reports는 실제 생성됐지만 커밋하지 않고 이 요약만 문서에 보존한다.

## Observed PG13–17 Packaging Matrix

2026-09-13 KST에 HEAD `62e84e0` (clean)에서 `PG_MATRIX="13 14 15 16 17" BENCH_OUT_DIR=/tmp/pglance-release-matrix-20260913-01 make finalize-release-run`을 실행했다.

- release artifact matrix: PostgreSQL 13, 14, 15, 16, 17 `5/5 valid`
- official `postgres:<major>` install smoke: `5/5 passed`, 모든 major exit code 0
  - PG13 `31.716s`, PG14 `9.775s`, PG15 `8.833s`, PG16 `6.123s`, PG17 `7.019s`
- packaging run doctor: `valid=true`, errors 0, warnings 0
- shared library sizes: PG13 `187297888` bytes, PG14 `187287984`, PG15 `187298512`, PG16 `187298488`, PG17 `187300248`; control `205` bytes와 generated SQL `9462` bytes는 전 major에서 동일

PG15 첫 artifact build 시 root filesystem이 99% 사용(가용 1.1G)인 상태에서 final linker가 `ld terminated with signal 7 [Bus error]`로 실패했다. 생성된 비활성 matrix build image와 1시간 이상 된 unused Docker build cache를 정리해 공간을 회복한 뒤 PG15 단독 재시도를 통과시켰고, 최종 matrix는 정상 완료됐다. 이 기록은 PG13-17 fresh install/package evidence이며, version bump와 old/new artifact 및 versioned upgrade SQL이 없어 `ALTER EXTENSION UPDATE`는 여전히 미측정이다. 실제 WARC quality baseline과도 별개의 증거다. Run directory는 `/tmp/pglance-release-matrix-20260913-01` temporary path이며, reports는 실제 생성됐지만 커밋하지 않고 이 요약만 문서에 보존한다.

## Observed Release Upgrade Input Preflight

2026-09-14 11:07 KST에 base HEAD `2e65e83` (clean)에 Phase 80 upgrade input doctor를 추가한 작업 트리에서 `BENCH_OUT_DIR=/tmp/pglance-upgrade-doctor-20260914-1105/default make doctor-release-upgrade-inputs`를 실행했다.

- exit code: non-zero (doctor는 `1`, make는 recipe 실패로 `2`)
- report: `release_upgrade_input_doctor.json`/`.md`, `valid=false`, `status=blocked`
- current version / available versions: `0.1.0`만 존재 (`dist/pgwarc_lance-0.1.0-pg13..17`)
- missing inputs: distinct old artifact version, old artifact directory, `pgwarc_lance--OLD--NEW.sql` upgrade script
- `new_artifact_valid`: true (`dist/pgwarc_lance-0.1.0-pg16`)
- `UPGRADE_OLD_VERSION=0.0.9`로 실행한 두 번째 preflight도 `dist/pgwarc_lance-0.0.9-pg16` 부재와 `dist/pgwarc_lance-0.1.0-pg16/pgwarc_lance--0.0.9--0.1.0.sql` 부재를 각각 기록하며 non-zero로 종료했다.

이 결과는 versioned `ALTER EXTENSION UPDATE`가 미측정(blocked)임을 명시하는 preflight 증거이며, upgrade 성공 증거가 아니다. 실제 old artifact와 upgrade SQL이 준비되기 전에는 `make smoke-release-upgrade`를 실행하지 않는다. 재개 조건과 명령은 report의 `resume_requirements`/`smoke_command`/`finalize_command`에 보존한다.

## Observed Release Upgrade Preflight Enforcement

2026-09-14 13:07 KST에 base HEAD `9ed72e0` (clean)에 Phase 81 upgrade preflight enforcement를 추가한 작업 트리에서 실제 저장소 상태로 upgrade target finalization을 실행했다.

- `RUN_DIR=/tmp/kilo/pg81-upgrade-finalize BENCH_OUT_DIR=/tmp/kilo/pg81-upgrade-finalize RELEASE_SMOKE_TARGET=smoke-release-upgrade make finalize-release-run`
- exit code: non-zero (matrix tool과 `report-release-upgrade-smoke-matrix`는 `1`, 이를 감싼 `report-release-smoke-matrix`와 `finalize-release-run` recipe는 `2`)
- `release_artifacts_matrix.json`: `5/5 PostgreSQL majors valid`로 정상 생성
- `release_smoke_matrix.json`/`.md`: `all_passed=false`, `blocked_majors=[13, 14, 15, 16, 17]`, 각 major `preflight.status=blocked`, `preflight_passed=false`, `smoke_skipped=true`, `exit_code=null`
- `ALTER EXTENSION UPDATE`는 실행되지 않았고 smoke attempt는 `0/5`였다. `dist/` artifact는 추가/변경하지 않았다.
- `BENCH_OUT_DIR=/tmp/kilo/pg81-upgrade-override RELEASE_SMOKE_TARGET=smoke-release-upgrade RELEASE_SMOKE_MAKE_VARS='UPGRADE_OLD_VERSION=0.0.9' PG_MATRIX=16 make report-release-smoke-matrix`는 `dist/pgwarc_lance-0.0.9-pg16` 부재와 `dist/pgwarc_lance-0.1.0-pg16/pgwarc_lance--0.0.9--0.1.0.sql` 부재를 각각 기록하며 non-zero(`2`)로 종료했다.
- non-upgrade target(`RELEASE_SMOKE_TARGET=check-release-artifacts PG_MATRIX=16 make report-release-smoke-matrix`)은 기존 `report_release_smoke_matrix.py` 경로를 그대로 사용해 `0`으로 종료했고 `preflight` field가 없었다.

이 증거는 upgrade finalization이 smoke 이전에 blocked 입력을 검출해 중단됨을 보여주는 하네스 검증이며, versioned upgrade 성공 증거가 아니다. old/new artifact와 upgrade SQL이 준비된 경우에는 unit-test fixture가 preflight 통과 후 matching input으로 `smoke-release-upgrade`를 dispatch함을 검증하지만, 저장소에는 아직 `0.1.0` artifact만 있어 versioned `ALTER EXTENSION UPDATE`는 미측정(blocked)이다.

## Release Artifacts

Build and extract PostgreSQL artifacts:

```bash
make release-artifacts
PG_MAJOR=17 make release-artifacts
make smoke-release-artifacts
UPGRADE_OLD_VERSION=0.0.9 make smoke-release-upgrade
make release-artifacts-matrix
make smoke-release-artifacts-matrix
UPGRADE_OLD_VERSION=0.0.9 make smoke-release-upgrade-matrix
BENCH_OUT_DIR=benchmarks/local make report-release-artifacts
BENCH_OUT_DIR=benchmarks/local make report-release-smoke-matrix
BENCH_OUT_DIR=benchmarks/local make finalize-release-run
```

Default output path for PostgreSQL 16:

```text
dist/pgwarc_lance-0.1.0-pg16/
```

Expected files:

```text
pgwarc_lance.so
pgwarc_lance.control
pgwarc_lance--0.1.0.sql
```

The SQL file is generated by pgrx during `cargo pgrx install`. It contains the `CREATE EXTENSION` surface for the current crate version.

When `PG_MAJOR` is set, the Docker image tag, artifact extraction paths, and output directory follow the selected major, for example `dist/pgwarc_lance-0.1.0-pg17/`.

`make release-artifacts` runs `make check-release-artifacts` after extracting files. This catches missing files, unreplaced `@CARGO_VERSION@` placeholders, mismatched SQL filenames, and obvious non-extension artifacts before manual install.

For all supported majors, run `make release-artifacts-matrix` first, then `make smoke-release-artifacts-matrix` to install those extracted artifacts into official runtime images. After a version bump with old artifacts and upgrade SQL available for each major, run `UPGRADE_OLD_VERSION=OLD make smoke-release-upgrade-matrix`.

After a matrix run, use `BENCH_OUT_DIR=benchmarks/local make report-release-artifacts` to capture a reproducible artifact manifest. The target fails if any expected major directory or artifact is missing or invalid, but it still writes requested report files. Use `BENCH_OUT_DIR=benchmarks/local make report-release-smoke-matrix` after extracting artifacts to capture the actual per-major install smoke result. This target continues through all requested majors, writes the requested report files, and exits nonzero if any major fails. With `RELEASE_SMOKE_TARGET=smoke-release-upgrade` the same command first enforces the per-major upgrade input preflight and never runs upgrade smoke for a blocked major. Use `BENCH_OUT_DIR=benchmarks/local make finalize-release-run` when both reports should be captured and closed with run metadata, manifest, packaging completeness, artifact matrix schema checks, and smoke matrix schema checks.

The install smoke uses these defaults, all overrideable from `make`:

```text
ARTIFACT_SMOKE_IMAGE=postgres:<PG_MAJOR>
ARTIFACT_SMOKE_CONTAINER=pgwarc_lance_artifact_install_pg<PG_MAJOR>
ARTIFACT_SMOKE_TIMEOUT=90
```

## Local Install

Install artifacts into a host PostgreSQL whose major version and `pg_config` match the build ABI:

```bash
sudo install -m 755 dist/pgwarc_lance-0.1.0-pg16/pgwarc_lance.so \
    "$(pg_config --pkglibdir)/"
sudo install -m 644 dist/pgwarc_lance-0.1.0-pg16/pgwarc_lance.control \
    "$(pg_config --sharedir)/extension/"
sudo install -m 644 dist/pgwarc_lance-0.1.0-pg16/pgwarc_lance--0.1.0.sql \
    "$(pg_config --sharedir)/extension/"

psql -d "$DATABASE_URL_OR_NAME" -c "CREATE EXTENSION pgwarc_lance;"
```

If the target database already has the extension installed, use the upgrade flow below instead of replacing files and assuming existing SQL objects changed automatically.

## Upgrade Strategy

For pre-release local development, rebuilding the Docker image and recreating the extension in the test database is acceptable:

```bash
make build
make test
```

For a released version, use PostgreSQL extension upgrade files:

1. Bump `Cargo.toml` `version`.
2. Keep the previous generated `pgwarc_lance--OLD.sql` artifact immutable.
3. Add a `pgwarc_lance--OLD--NEW.sql` upgrade script for schema/data migrations.
4. Make upgrade scripts idempotent where practical and preserve user data by default.
5. Run `make doctor-release-upgrade-inputs` to confirm the old artifact and versioned upgrade script exist before running the smoke. `make report-release-smoke-matrix` and `make finalize-release-run` run this same preflight per major automatically when `RELEASE_SMOKE_TARGET=smoke-release-upgrade`, and fail without running upgrade smoke for any blocked major.
6. Verify both fresh install and upgrade:

```sql
CREATE EXTENSION pgwarc_lance VERSION 'OLD';
ALTER EXTENSION pgwarc_lance UPDATE TO 'NEW';
```

The local Docker equivalent is:

```bash
make doctor-release-upgrade-inputs
UPGRADE_OLD_VERSION=OLD UPGRADE_NEW_VERSION=NEW make smoke-release-upgrade
UPGRADE_OLD_VERSION=OLD UPGRADE_NEW_VERSION=NEW make smoke-release-upgrade-matrix
```

Until the first non-local release, the project treats `0.1.0` as a development baseline and does not maintain downgrade scripts. As of 2026-09-14 only `0.1.0` artifacts exist and no `pgwarc_lance--OLD--NEW.sql` upgrade script is present, so the preflight reports `blocked` and versioned upgrade remains unmeasured.

## Toolchain Pinning

The Dockerfile pins Rust through `ARG RUST_TOOLCHAIN=1.96.0`. To test a newer toolchain without changing the default:

```bash
PG_MAJOR=16 docker compose build --build-arg RUST_TOOLCHAIN=stable
```

Do not update the default pin without running `make verify` in the rebuilt container and recording the change in `ROADMAP.md` or release notes.
