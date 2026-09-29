#!/usr/bin/env python3
"""Validate extracted pgwarc_lance release artifacts."""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION_NAME = "pgwarc_lance"
REQUIRED_SQL_SYMBOLS = [
    "hello_pgwarc_lance",
    "bm25_index_document",
    "bm25_search",
    "lance_create_table",
    "lance_insert_many",
    "hybrid_warc_search",
]


def cargo_version(cargo_toml: Path = ROOT / "Cargo.toml") -> str:
    data = tomllib.loads(cargo_toml.read_text(encoding="utf-8"))
    package = data.get("package", {})
    version = package.get("version")
    if not isinstance(version, str) or not version:
        raise ValueError(f"{cargo_toml}: missing package.version")
    return version


def parse_control(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*'(.*)'\s*$", stripped)
        if match:
            values[match.group(1)] = match.group(2)
    return values


def require_file(path: Path, label: str, errors: list[str]) -> bool:
    if not path.is_file():
        errors.append(f"{label}: missing required artifact: {path}")
        return False
    if path.stat().st_size == 0:
        errors.append(f"{label}: artifact is empty: {path}")
        return False
    return True


def validate_artifacts(
    dist_dir: Path,
    *,
    version: str,
    pg_major: int | None = None,
) -> list[str]:
    errors: list[str] = []
    if not dist_dir.is_dir():
        return [f"dist directory does not exist: {dist_dir}"]

    so_path = dist_dir / f"{EXTENSION_NAME}.so"
    control_path = dist_dir / f"{EXTENSION_NAME}.control"
    sql_path = dist_dir / f"{EXTENSION_NAME}--{version}.sql"

    so_exists = require_file(so_path, "shared library", errors)
    control_exists = require_file(control_path, "control file", errors)
    sql_exists = require_file(sql_path, "extension SQL", errors)

    if so_exists and so_path.read_bytes()[:4] != b"\x7fELF":
        errors.append(f"shared library is not an ELF object: {so_path}")

    if control_exists:
        control_text = control_path.read_text(encoding="utf-8")
        control = parse_control(control_text)
        if "@CARGO_VERSION@" in control_text:
            errors.append(f"control file still contains @CARGO_VERSION@: {control_path}")
        if control.get("default_version") != version:
            errors.append(
                "control default_version mismatch: "
                f"expected {version!r}, got {control.get('default_version')!r}"
            )
        if control.get("module_pathname") != EXTENSION_NAME:
            errors.append(
                "control module_pathname mismatch: "
                f"expected {EXTENSION_NAME!r}, got {control.get('module_pathname')!r}"
            )

    if sql_exists:
        sql_text = sql_path.read_text(encoding="utf-8")
        if "@CARGO_VERSION@" in sql_text:
            errors.append(f"extension SQL still contains @CARGO_VERSION@: {sql_path}")
        for symbol in REQUIRED_SQL_SYMBOLS:
            if symbol not in sql_text:
                errors.append(f"extension SQL missing expected symbol {symbol!r}: {sql_path}")

    if pg_major is not None and not (13 <= pg_major <= 17):
        errors.append(f"unsupported PostgreSQL major for this project: {pg_major}")

    return errors


def parse_args(argv: list[str]) -> argparse.Namespace:
    version = cargo_version()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=ROOT / "dist" / f"{EXTENSION_NAME}-{version}-pg16",
    )
    parser.add_argument("--version", default=version)
    parser.add_argument("--pg-major", type=int)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    errors = validate_artifacts(
        args.dist_dir,
        version=args.version,
        pg_major=args.pg_major,
    )
    if errors:
        for error in errors:
            print(f"release artifact check failed: {error}", file=sys.stderr)
        return 1

    pg_suffix = f" pg{args.pg_major}" if args.pg_major is not None else ""
    print(f"release artifact checks passed for {args.dist_dir}{pg_suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
