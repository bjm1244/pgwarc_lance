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
                "--name",
                container,
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
