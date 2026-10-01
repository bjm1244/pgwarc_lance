#!/usr/bin/env python3
"""Install extracted release artifacts into a disposable PostgreSQL container."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import check_release_artifacts


EXTENSION_NAME = check_release_artifacts.EXTENSION_NAME


class SmokeError(RuntimeError):
    pass


def run_command(
    command: list[str],
    *,
    capture: bool = False,
    quiet: bool = False,
    check: bool = True,
) -> str:
    stdout = subprocess.PIPE if capture else (subprocess.DEVNULL if quiet else None)
    stderr = subprocess.PIPE if capture else (subprocess.DEVNULL if quiet else None)
    try:
        result = subprocess.run(
            command,
            check=check,
            text=True,
            stdout=stdout,
            stderr=stderr,
        )
    except FileNotFoundError as error:
        raise SmokeError(f"command not found: {command[0]}") from error
    except subprocess.CalledProcessError as error:
        detail = ""
        if error.stderr:
            detail = f": {error.stderr.strip()}"
        raise SmokeError(f"command failed ({error.returncode}): {' '.join(command)}{detail}") from error
    if capture:
        return result.stdout.strip()
    return ""


def remove_container(container: str) -> None:
    run_command(["docker", "rm", "-f", container], quiet=True, check=False)


def wait_until_ready(
    container: str,
    *,
    user: str,
    database: str,
    timeout_seconds: int,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = subprocess.run(
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
                "SELECT 1;",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            return
        time.sleep(2)
    raise SmokeError(f"PostgreSQL did not become ready within {timeout_seconds}s")


def docker_destination(container: str, directory: str, filename: str) -> str:
    return f"{container}:{directory.rstrip('/')}/{filename}"


def install_artifacts(
    *,
    container: str,
    dist_dir: Path,
    version: str,
) -> None:
    pkglibdir = run_command(["docker", "exec", container, "pg_config", "--pkglibdir"], capture=True)
    sharedir = run_command(["docker", "exec", container, "pg_config", "--sharedir"], capture=True)
    extension_dir = f"{sharedir.rstrip('/')}/extension"

    artifact_pairs = [
        (dist_dir / f"{EXTENSION_NAME}.so", pkglibdir),
        (dist_dir / f"{EXTENSION_NAME}.control", extension_dir),
        (dist_dir / f"{EXTENSION_NAME}--{version}.sql", extension_dir),
    ]
    for source, directory in artifact_pairs:
        run_command(["docker", "cp", str(source), docker_destination(container, directory, source.name)])


def run_extension_smoke(*, container: str, user: str, database: str) -> None:
    sql = f"CREATE EXTENSION {EXTENSION_NAME}; SELECT hello_pgwarc_lance();"
    run_command(
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
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=check_release_artifacts.ROOT
        / "dist"
        / f"{EXTENSION_NAME}-{version}-pg16",
    )
    parser.add_argument("--version", default=version)
    parser.add_argument("--pg-major", type=int, default=16)
    parser.add_argument("--postgres-image", default=None)
    parser.add_argument("--container-name", default=None)
    parser.add_argument("--user", default=EXTENSION_NAME)
    parser.add_argument("--password", default=EXTENSION_NAME)
    parser.add_argument("--database", default=f"{EXTENSION_NAME}_test")
    parser.add_argument("--startup-timeout", type=int, default=90)
    return parser.parse_args(argv)


def smoke(args: argparse.Namespace) -> None:
    errors = check_release_artifacts.validate_artifacts(
        args.dist_dir,
        version=args.version,
        pg_major=args.pg_major,
    )
    if errors:
        raise SmokeError("; ".join(errors))

    image = args.postgres_image or f"postgres:{args.pg_major}"
    container = args.container_name or f"{EXTENSION_NAME}_artifact_install_pg{args.pg_major}"

    remove_container(container)
    try:
        run_command(
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
        wait_until_ready(
            container,
            user=args.user,
            database=args.database,
            timeout_seconds=args.startup_timeout,
        )
        install_artifacts(container=container, dist_dir=args.dist_dir, version=args.version)
        run_extension_smoke(container=container, user=args.user, database=args.database)
    finally:
        remove_container(container)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        smoke(args)
    except SmokeError as error:
        print(f"release artifact install smoke failed: {error}", file=sys.stderr)
        return 1
    print(
        "release artifact install smoke passed "
        f"for {args.dist_dir} on postgres:{args.pg_major}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
