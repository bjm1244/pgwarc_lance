#!/usr/bin/env python3
"""Report validation status and file digests for release artifact matrices."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import check_release_artifacts


EXTENSION_NAME = check_release_artifacts.EXTENSION_NAME
CHUNK_SIZE = 1024 * 1024


def parse_pg_majors(value: str) -> list[int]:
    majors: list[int] = []
    for raw_part in re.split(r"[,\s]+", value.strip()):
        if not raw_part:
            continue
        try:
            majors.append(int(raw_part))
        except ValueError as error:
            raise argparse.ArgumentTypeError(
                f"invalid PostgreSQL major {raw_part!r}"
            ) from error
    if not majors:
        raise argparse.ArgumentTypeError("at least one PostgreSQL major is required")
    return majors


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_report(path: Path) -> dict[str, Any]:
    report: dict[str, Any] = {
        "path": str(path),
        "exists": path.is_file(),
    }
    if path.is_file():
        report["bytes"] = path.stat().st_size
        report["sha256"] = sha256_file(path)
    return report


def artifact_files(dist_dir: Path, version: str) -> dict[str, Path]:
    return {
        "shared_library": dist_dir / f"{EXTENSION_NAME}.so",
        "control": dist_dir / f"{EXTENSION_NAME}.control",
        "sql": dist_dir / f"{EXTENSION_NAME}--{version}.sql",
    }


def build_report(
    *,
    dist_root: Path,
    version: str,
    pg_majors: list[int],
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for pg_major in pg_majors:
        dist_dir = dist_root / f"{EXTENSION_NAME}-{version}-pg{pg_major}"
        errors = check_release_artifacts.validate_artifacts(
            dist_dir,
            version=version,
            pg_major=pg_major,
        )
        entries.append(
            {
                "pg_major": pg_major,
                "dist_dir": str(dist_dir),
                "valid": not errors,
                "errors": errors,
                "files": {
                    label: file_report(path)
                    for label, path in artifact_files(dist_dir, version).items()
                },
            }
        )

    return {
        "extension": EXTENSION_NAME,
        "version": version,
        "dist_root": str(dist_root),
        "pg_majors": pg_majors,
        "all_valid": all(entry["valid"] for entry in entries),
        "artifacts": entries,
    }


def markdown_cell(value: object) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# {EXTENSION_NAME} Release Artifact Matrix",
        "",
        f"- Version: `{report['version']}`",
        f"- Dist root: `{report['dist_root']}`",
        f"- All valid: `{str(report['all_valid']).lower()}`",
        "",
        "| PostgreSQL | Status | Dist Dir | Shared Library | Control | SQL |",
        "|------------|--------|----------|----------------|---------|-----|",
    ]
    for entry in report["artifacts"]:
        files = entry["files"]
        lines.append(
            "| {pg} | {status} | `{dist}` | {so} | {control} | {sql} |".format(
                pg=entry["pg_major"],
                status="pass" if entry["valid"] else "fail",
                dist=markdown_cell(entry["dist_dir"]),
                so=file_summary(files["shared_library"]),
                control=file_summary(files["control"]),
                sql=file_summary(files["sql"]),
            )
        )

    failing = [entry for entry in report["artifacts"] if entry["errors"]]
    if failing:
        lines.extend(["", "## Errors", ""])
        for entry in failing:
            lines.append(f"### PostgreSQL {entry['pg_major']}")
            for error in entry["errors"]:
                lines.append(f"- {markdown_cell(error)}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def file_summary(report: dict[str, Any]) -> str:
    if not report["exists"]:
        return "missing"
    digest = str(report["sha256"])[:12]
    return f"{report['bytes']} bytes / `{digest}`"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, report: dict[str, Any]) -> None:
    write_text(path, json.dumps(report, indent=2, sort_keys=True) + "\n")


def parse_args(argv: list[str]) -> argparse.Namespace:
    version = check_release_artifacts.cargo_version()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-root", type=Path, default=check_release_artifacts.ROOT / "dist")
    parser.add_argument("--version", default=version)
    parser.add_argument(
        "--pg-majors",
        type=parse_pg_majors,
        default=parse_pg_majors("13 14 15 16 17"),
    )
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_report(
        dist_root=args.dist_root,
        version=args.version,
        pg_majors=args.pg_majors,
    )
    if args.json_output:
        write_json(args.json_output, report)
    if args.markdown_output:
        write_text(args.markdown_output, render_markdown(report))

    valid_count = sum(1 for entry in report["artifacts"] if entry["valid"])
    total_count = len(report["artifacts"])
    print(
        "release artifact matrix report: "
        f"{valid_count}/{total_count} PostgreSQL majors valid"
    )
    if not report["all_valid"]:
        for entry in report["artifacts"]:
            for error in entry["errors"]:
                print(
                    f"release artifact matrix failed for pg{entry['pg_major']}: {error}",
                    file=sys.stderr,
                )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
