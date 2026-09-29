#!/usr/bin/env python3
"""Diagnose pgwarc_lance WARC importer summary JSON artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REQUIRED_FIELDS = {
    "records",
    "sql_bytes",
    "vectors",
    "lance_mode",
    "lance_append_duplicates",
    "bm25_mode",
    "execute",
    "psql",
}
LANCE_MODES = {"none", "create", "overwrite", "append"}
LANCE_APPEND_DUPLICATES = {"none", "possible"}
BM25_MODES = {"function", "bulk", "copy"}


def is_nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def valid_vectors(value: object) -> bool:
    return value == "hash" or is_nonnegative_int(value)


def vectors_present(value: object) -> bool:
    return value == "hash" or (is_nonnegative_int(value) and value > 0)


def load_summary(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        return None, [f"cannot read summary JSON: {error}"]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        return None, [f"invalid JSON: {error}"]
    if not isinstance(data, dict):
        return None, ["summary JSON must be an object"]
    return data, []


def diagnose_summary(path: Path, summary: dict[str, Any] | None, load_errors: list[str]) -> dict[str, Any]:
    errors = list(load_errors)
    warnings: list[str] = []
    if summary is None:
        return {
            "path": str(path),
            "valid": False,
            "errors": errors,
            "warnings": warnings,
            "summary": None,
        }

    missing = sorted(REQUIRED_FIELDS - set(summary))
    for field in missing:
        errors.append(f"missing required field: {field}")

    records = summary.get("records")
    sql_bytes = summary.get("sql_bytes")
    vectors = summary.get("vectors")
    lance_mode = summary.get("lance_mode")
    append_duplicates = summary.get("lance_append_duplicates")
    bm25_mode = summary.get("bm25_mode")
    execute = summary.get("execute")
    psql = summary.get("psql")

    if "records" in summary and not is_nonnegative_int(records):
        errors.append("records must be a non-negative integer")
    if "sql_bytes" in summary and not is_nonnegative_int(sql_bytes):
        errors.append("sql_bytes must be a non-negative integer")
    if "vectors" in summary and not valid_vectors(vectors):
        errors.append("vectors must be a non-negative integer or 'hash'")
    if "lance_mode" in summary and lance_mode not in LANCE_MODES:
        errors.append(
            "lance_mode must be one of: " + ", ".join(sorted(LANCE_MODES))
        )
    if "lance_append_duplicates" in summary and append_duplicates not in LANCE_APPEND_DUPLICATES:
        errors.append("lance_append_duplicates must be 'none' or 'possible'")
    if "bm25_mode" in summary and bm25_mode not in BM25_MODES:
        errors.append("bm25_mode must be one of: " + ", ".join(sorted(BM25_MODES)))
    if "execute" in summary and not isinstance(execute, bool):
        errors.append("execute must be a boolean")
    if "psql" in summary and not isinstance(psql, str):
        errors.append("psql must be a string")

    if not errors:
        assert isinstance(records, int)
        assert isinstance(sql_bytes, int)
        assert isinstance(lance_mode, str)
        assert isinstance(append_duplicates, str)
        assert isinstance(execute, bool)

        if records == 0:
            warnings.append("summary contains zero records")
        if sql_bytes == 0:
            errors.append("generated SQL is empty")
        if not execute:
            warnings.append("summary describes generated SQL only; execute=false")
        if lance_mode == "none":
            if vectors_present(vectors):
                errors.append("lance_mode=none must not report vectors")
            if append_duplicates != "none":
                errors.append("lance_mode=none must report lance_append_duplicates=none")
        elif not vectors_present(vectors):
            warnings.append("Lance mode is set but no vectors were emitted")

        if append_duplicates == "possible":
            if lance_mode != "append":
                errors.append("lance_append_duplicates=possible requires lance_mode=append")
            else:
                warnings.append("Lance append mode can duplicate vector rows on retry")
        elif lance_mode == "append" and vectors_present(vectors):
            errors.append(
                "append vector imports must report lance_append_duplicates=possible"
            )

        if execute and not str(psql).strip():
            errors.append("execute=true requires a non-empty psql command")

    return {
        "path": str(path),
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "summary": summary,
    }


def build_report(summary_paths: list[Path]) -> dict[str, Any]:
    entries = []
    for path in summary_paths:
        summary, load_errors = load_summary(path)
        entries.append(diagnose_summary(path, summary, load_errors))
    return {
        "all_valid": all(entry["valid"] for entry in entries),
        "summary_count": len(entries),
        "error_count": sum(len(entry["errors"]) for entry in entries),
        "warning_count": sum(len(entry["warnings"]) for entry in entries),
        "summaries": entries,
    }


def markdown_cell(value: object) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# pgwarc_lance Import Summary Doctor",
        "",
        f"- All valid: `{str(report['all_valid']).lower()}`",
        f"- Summaries: `{report['summary_count']}`",
        f"- Errors: `{report['error_count']}`",
        f"- Warnings: `{report['warning_count']}`",
        "",
        "| Summary | Status | Records | SQL Bytes | Vectors | Lance | BM25 | Execute | Warnings |",
        "|---------|--------|---------|-----------|---------|-------|------|---------|----------|",
    ]
    for entry in report["summaries"]:
        summary = entry["summary"] or {}
        warnings = "; ".join(entry["warnings"]) if entry["warnings"] else "-"
        lines.append(
            "| {path} | {status} | {records} | {sql_bytes} | {vectors} | {lance} | {bm25} | {execute} | {warnings} |".format(
                path=f"`{markdown_cell(entry['path'])}`",
                status="pass" if entry["valid"] else "fail",
                records=markdown_cell(summary.get("records", "-")),
                sql_bytes=markdown_cell(summary.get("sql_bytes", "-")),
                vectors=markdown_cell(summary.get("vectors", "-")),
                lance=markdown_cell(summary.get("lance_mode", "-")),
                bm25=markdown_cell(summary.get("bm25_mode", "-")),
                execute=markdown_cell(summary.get("execute", "-")),
                warnings=markdown_cell(warnings),
            )
        )

    failing = [entry for entry in report["summaries"] if entry["errors"]]
    if failing:
        lines.extend(["", "## Errors", ""])
        for entry in failing:
            lines.append(f"### `{markdown_cell(entry['path'])}`")
            for error in entry["errors"]:
                lines.append(f"- {markdown_cell(error)}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, report: dict[str, Any]) -> None:
    write_text(path, json.dumps(report, indent=2, sort_keys=True) + "\n")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--summary-json",
        type=Path,
        action="append",
        required=True,
        help="importer --summary-json artifact to diagnose; may be repeated",
    )
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_report(args.summary_json)
    if args.json_output:
        write_json(args.json_output, report)
    if args.markdown_output:
        write_text(args.markdown_output, render_markdown(report))

    checked = report["summary_count"]
    print(
        "import summary doctor: "
        f"{checked} checked, {report['error_count']} errors, {report['warning_count']} warnings"
    )
    if not report["all_valid"]:
        for entry in report["summaries"]:
            for error in entry["errors"]:
                print(
                    f"import summary doctor failed for {entry['path']}: {error}",
                    file=sys.stderr,
                )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
