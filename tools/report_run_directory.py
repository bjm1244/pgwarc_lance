#!/usr/bin/env python3
"""Report files and digests for a pgwarc_lance run artifact directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


CHUNK_SIZE = 1024 * 1024
KNOWN_ARTIFACTS = {
    "bm25.json": "benchmark",
    "bm25.md": "benchmark",
    "lance.json": "benchmark",
    "lance.md": "benchmark",
    "warc.json": "benchmark",
    "warc.md": "benchmark",
    "warc_bm25_modes.json": "benchmark",
    "warc_bm25_modes.md": "benchmark",
    "quality.json": "quality",
    "quality.md": "quality",
    "quality_fixture_report.json": "quality",
    "quality_fixture_report.md": "quality",
    "quality_labels_doctor.json": "quality",
    "quality_labels_doctor.md": "quality",
    "quality_baseline_doctor.json": "quality",
    "quality_baseline_doctor.md": "quality",
    "release_artifacts_matrix.json": "packaging",
    "release_artifacts_matrix.md": "packaging",
    "release_smoke_matrix.json": "packaging",
    "release_smoke_matrix.md": "packaging",
    "import.summary.json": "import",
    "import_doctor.json": "import",
    "import_doctor.md": "import",
    "records.jsonl": "import",
    "vectors.jsonl": "import",
    "quality.fixture.json": "quality",
    "quality_queries.json": "quality",
    "run_metadata.json": "metadata",
    "run_metadata.md": "metadata",
    "run_manifest.json": "metadata",
    "run_manifest.md": "metadata",
    "run_doctor.json": "metadata",
    "run_doctor.md": "metadata",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_kind(path: Path) -> str:
    return KNOWN_ARTIFACTS.get(path.name, "artifact")


def relative_file_report(run_dir: Path, path: Path) -> dict[str, Any]:
    relative = path.relative_to(run_dir).as_posix()
    return {
        "path": relative,
        "kind": artifact_kind(path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def build_report(run_dir: Path, exclude_paths: set[Path] | None = None) -> dict[str, Any]:
    resolved_run_dir = run_dir.resolve()
    excludes = {path.resolve() for path in (exclude_paths or set())}
    errors: list[str] = []
    warnings: list[str] = []
    files: list[dict[str, Any]] = []

    if not run_dir.exists():
        errors.append(f"run directory does not exist: {run_dir}")
    elif not run_dir.is_dir():
        errors.append(f"run path is not a directory: {run_dir}")
    else:
        for path in sorted(run_dir.rglob("*")):
            if not path.is_file():
                continue
            if path.resolve() in excludes:
                continue
            files.append(relative_file_report(resolved_run_dir, path.resolve()))

    if not errors and not files:
        warnings.append("run directory contains no files")

    return {
        "run_dir": str(run_dir),
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "file_count": len(files),
        "total_bytes": sum(file["bytes"] for file in files),
        "files": files,
    }


def markdown_cell(value: object) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# pgwarc_lance Run Directory Manifest",
        "",
        f"- Run dir: `{report['run_dir']}`",
        f"- Valid: `{str(report['valid']).lower()}`",
        f"- Files: `{report['file_count']}`",
        f"- Total bytes: `{report['total_bytes']}`",
        f"- Warnings: `{report['warning_count']}`",
        "",
        "| Path | Kind | Bytes | SHA-256 |",
        "|------|------|-------|---------|",
    ]
    for file in report["files"]:
        lines.append(
            "| {path} | {kind} | {bytes} | `{sha}` |".format(
                path=f"`{markdown_cell(file['path'])}`",
                kind=markdown_cell(file["kind"]),
                bytes=file["bytes"],
                sha=file["sha256"],
            )
        )

    if report["warnings"]:
        lines.extend(["", "## Warnings", ""])
        for warning in report["warnings"]:
            lines.append(f"- {markdown_cell(warning)}")

    if report["errors"]:
        lines.extend(["", "## Errors", ""])
        for error in report["errors"]:
            lines.append(f"- {markdown_cell(error)}")

    return "\n".join(lines).rstrip() + "\n"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, report: dict[str, Any]) -> None:
    write_text(path, json.dumps(report, indent=2, sort_keys=True) + "\n")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    return parser.parse_args(argv)


def output_excludes(args: argparse.Namespace) -> set[Path]:
    return {
        path
        for path in (args.json_output, args.markdown_output)
        if path is not None
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_report(args.run_dir, exclude_paths=output_excludes(args))
    if args.json_output:
        write_json(args.json_output, report)
    if args.markdown_output:
        write_text(args.markdown_output, render_markdown(report))

    print(
        "run directory report: "
        f"{report['file_count']} files, {report['error_count']} errors, "
        f"{report['warning_count']} warnings"
    )
    if report["errors"]:
        for error in report["errors"]:
            print(f"run directory report failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
