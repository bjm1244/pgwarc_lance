#!/usr/bin/env python3
"""Smoke test PostgreSQL extension upgrades from extracted release artifacts."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import check_release_artifacts
import smoke_release_artifacts


EXTENSION_NAME = check_release_artifacts.EXTENSION_NAME
SmokeError = smoke_release_artifacts.SmokeError
UPGRADE_BASELINE_SQL_SYMBOLS = [
    "hello_pgwarc_lance",
    "bm25_index_document",
    "bm25_search",
    "lance_create_table",
    "lance_insert_many",
    "lance_outbox_enqueue",
    "hybrid_warc_search",
]


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def upgrade_script_path(dist_dir: Path, *, old_version: str, new_version: str) -> Path:
    return dist_dir / f"{EXTENSION_NAME}--{old_version}--{new_version}.sql"


def validate_upgrade_artifacts(
    *,
    old_dist_dir: Path,
    old_version: str,
    new_dist_dir: Path,
    new_version: str,
    pg_major: int,
) -> list[str]:
    errors: list[str] = []
    if old_version == new_version:
        errors.append("old and new extension versions must differ")

    errors.extend(
        f"old artifact: {error}"
        for error in check_release_artifacts.validate_artifacts(
            old_dist_dir,
            version=old_version,
            pg_major=pg_major,
            required_sql_symbols=UPGRADE_BASELINE_SQL_SYMBOLS,
        )
    )
    errors.extend(
        f"new artifact: {error}"
        for error in check_release_artifacts.validate_artifacts(
            new_dist_dir,
            version=new_version,
            pg_major=pg_major,
        )
    )

    upgrade_path = upgrade_script_path(
        new_dist_dir,
        old_version=old_version,
        new_version=new_version,
    )
    if not upgrade_path.is_file():
        errors.append(f"missing upgrade script: {upgrade_path}")
    elif upgrade_path.stat().st_size == 0:
        errors.append(f"upgrade script is empty: {upgrade_path}")

    return errors


def install_upgrade_script(
    *,
    container: str,
    upgrade_script: Path,
) -> None:
    sharedir = smoke_release_artifacts.run_command(
        ["docker", "exec", container, "pg_config", "--sharedir"],
        capture=True,
    )
    extension_dir = f"{sharedir.rstrip('/')}/extension"
    smoke_release_artifacts.run_command(
        [
            "docker",
            "cp",
            str(upgrade_script),
            smoke_release_artifacts.docker_destination(
                container,
                extension_dir,
                upgrade_script.name,
            ),
        ]
    )


def run_create_old_smoke(
    *,
    container: str,
    user: str,
    database: str,
    old_version: str,
) -> None:
    sql = " ".join(
        [
            f"CREATE EXTENSION {EXTENSION_NAME} VERSION {sql_literal(old_version)};",
            "SELECT hello_pgwarc_lance();",
            "SELECT bm25_index_document(-101,'first shared');",
            "SELECT bm25_index_document(-102,'second shared');",
            "INSERT INTO pgwarc_lance.warc_record (doc_id,text_len,source_file) VALUES (-101,12,'upgrade-smoke'),(-102,13,'upgrade-smoke');",
            "SELECT lance_create_table('/tmp/pgwarc_artifact_upgrade.lance',4,false);",
            "SELECT lance_insert_many('/tmp/pgwarc_artifact_upgrade.lance',ARRAY[-101,-102]::bigint[],ARRAY[0,0,0,0,1,0,0,0]::float4[],4,ARRAY['first','second']);",
        ]
    )
    smoke_release_artifacts.run_command(
        [
            "docker",
            "exec",
            container,
            "psql",
            "-U",
            user,
            "-d",
            database,
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            sql,
        ],
        capture=True,
    )


def run_update_smoke(
    *,
    container: str,
    user: str,
    database: str,
    new_version: str,
) -> None:
    sql = " ".join(
        [
            f"ALTER EXTENSION {EXTENSION_NAME} UPDATE TO {sql_literal(new_version)};",
            (
                "SELECT extversion FROM pg_extension "
                f"WHERE extname = {sql_literal(EXTENSION_NAME)};"
            ),
            "SELECT hello_pgwarc_lance();",
            """DO $$ BEGIN
                IF bm25_doc_count() <> 2 OR (SELECT count(*) FROM pgwarc_lance.warc_record) <> 2
                   OR (SELECT count(*) FROM bm25_search('shared',10)) <> 2
                   OR lance_count('/tmp/pgwarc_artifact_upgrade.lance') <> 2
                   OR (SELECT count(*) FROM lance_dataset_versions('/tmp/pgwarc_artifact_upgrade.lance')) < 2 THEN
                    RAISE EXCEPTION 'old-binary data did not survive the artifact upgrade';
                END IF;
            END $$;""",
        ]
    )
    smoke_release_artifacts.run_command(
        [
            "docker",
            "exec",
            container,
            "psql",
            "-U",
            user,
            "-d",
            database,
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            sql,
        ],
        capture=True,
    )

    # The old Lance dataset has no revision column. Confirm the read-only API
    # exposes that gap, then let the first revisioned outbox upsert evolve the
    # nullable Lance schema and retain existing IDs/labels.
    def run_sql(statement: str) -> str:
        return smoke_release_artifacts.run_command(
            [
                "docker", "exec", container, "psql", "-U", user, "-d", database,
                "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-c", statement,
            ],
            capture=True,
        ).strip()

    legacy_revision_count = run_sql(
        "SELECT count(*) FROM lance_scan_document_state('/tmp/pgwarc_artifact_upgrade.lance',10) "
        "WHERE revision IS NULL;"
    )
    if legacy_revision_count != "2":
        raise SmokeError(
            "legacy Lance rows were not reported without revisions "
            f"(expected 2, got {legacy_revision_count!r})"
        )
    revision = "a" * 64
    run_sql(
        "SELECT lance_outbox_enqueue_revision("
        "'/tmp/pgwarc_artifact_upgrade.lance',ARRAY[-101]::bigint[],"
        "ARRAY[0,0,0,0]::real[],4,ARRAY['first']::text[],ARRAY['" + revision + "']::text[]);"
    )
    delivery = run_sql("SELECT status FROM lance_outbox_process_next();")
    if delivery != "completed":
        failure = run_sql(
            "SELECT coalesce(last_error, 'no error recorded') "
            "FROM pgwarc_lance.lance_outbox ORDER BY id DESC LIMIT 1;"
        )
        raise SmokeError(
            "legacy dataset revision upsert did not complete "
            f"({delivery!r}; {failure!r})"
        )
    resulting_revision = run_sql(
        "SELECT revision FROM lance_scan_document_state('/tmp/pgwarc_artifact_upgrade.lance',10) "
        "WHERE doc_id=-101;"
    )
    if resulting_revision != revision:
        raise SmokeError("revisioned upsert did not evolve and update the legacy Lance row")
    if run_sql("SELECT lance_count('/tmp/pgwarc_artifact_upgrade.lance');") != "2":
        raise SmokeError("revisioned upsert changed the populated Lance row count")


def parse_args(argv: list[str]) -> argparse.Namespace:
    version = check_release_artifacts.cargo_version()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-dist-dir", type=Path, required=True)
    parser.add_argument("--old-version", required=True)
    parser.add_argument(
        "--new-dist-dir",
        type=Path,
        default=check_release_artifacts.ROOT
        / "dist"
        / f"{EXTENSION_NAME}-{version}-pg16",
    )
    parser.add_argument("--new-version", default=version)
    parser.add_argument("--pg-major", type=int, default=16)
    parser.add_argument("--postgres-image", default=None)
    parser.add_argument("--container-name", default=None)
    parser.add_argument("--user", default=EXTENSION_NAME)
    parser.add_argument("--password", default=EXTENSION_NAME)
    parser.add_argument("--database", default=f"{EXTENSION_NAME}_test")
    parser.add_argument("--startup-timeout", type=int, default=90)
    return parser.parse_args(argv)


def smoke(args: argparse.Namespace) -> None:
    errors = validate_upgrade_artifacts(
        old_dist_dir=args.old_dist_dir,
        old_version=args.old_version,
        new_dist_dir=args.new_dist_dir,
        new_version=args.new_version,
        pg_major=args.pg_major,
    )
    if errors:
        raise SmokeError("; ".join(errors))

    image = args.postgres_image or f"postgres:{args.pg_major}"
    container = args.container_name or f"{EXTENSION_NAME}_artifact_upgrade_pg{args.pg_major}"
    upgrade_path = upgrade_script_path(
        args.new_dist_dir,
        old_version=args.old_version,
        new_version=args.new_version,
    )

    smoke_release_artifacts.remove_container(container)
    try:
        smoke_release_artifacts.run_command(
            [
                "docker",
                "run",
                "-d",
                "--network",
                "none",
                "--name",
                container,
                "--tmpfs",
                "/var/lib/postgresql/data:rw,nosuid,nodev,size=268435456",
                "-e",
                f"POSTGRES_USER={args.user}",
                "-e",
                f"POSTGRES_PASSWORD={args.password}",
                "-e",
                f"POSTGRES_DB={args.database}",
                image,
            ],
            quiet=True,
        )
        smoke_release_artifacts.wait_until_ready(
            container,
            user=args.user,
            database=args.database,
            timeout_seconds=args.startup_timeout,
        )
        smoke_release_artifacts.install_artifacts(
            container=container,
            dist_dir=args.old_dist_dir,
            version=args.old_version,
        )
        run_create_old_smoke(
            container=container,
            user=args.user,
            database=args.database,
            old_version=args.old_version,
        )
        smoke_release_artifacts.install_artifacts(
            container=container,
            dist_dir=args.new_dist_dir,
            version=args.new_version,
        )
        install_upgrade_script(container=container, upgrade_script=upgrade_path)
        run_update_smoke(
            container=container,
            user=args.user,
            database=args.database,
            new_version=args.new_version,
        )
    finally:
        smoke_release_artifacts.remove_container(container)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        smoke(args)
    except SmokeError as error:
        print(f"release artifact upgrade smoke failed: {error}", file=sys.stderr)
        return 1
    print(
        "release artifact upgrade smoke passed "
        f"from {args.old_version} to {args.new_version} on postgres:{args.pg_major}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
