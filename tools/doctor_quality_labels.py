#!/usr/bin/env python3
"""Validate quality query labels before building an eval_quality fixture."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_no}: invalid JSON") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{line_no}: expected JSON object")
        rows.append(row)
    if not rows:
        raise ValueError(f"{path}: expected at least one row")
    return rows


def duplicate_values(values: list[Any]) -> list[Any]:
    counts = Counter(values)
    return sorted(value for value, count in counts.items() if count > 1)


MAX_DOC_ID = (1 << 63) - 1


def positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and 0 < value <= MAX_DOC_ID:
        return value
    return None


def numeric_vector(value: Any) -> list[float] | None:
    if not isinstance(value, list) or not value:
        return None
    vector = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return None
        try:
            numeric = float(item)
        except OverflowError:
            return None
        if not math.isfinite(numeric):
            return None
        vector.append(numeric)
    return vector


def load_record_doc_ids(records_jsonl: Path | None) -> tuple[set[int], list[str], list[str]]:
    if records_jsonl is None:
        return set(), [], ["records-jsonl not provided; expected_doc_ids were not checked against corpus records"]

    errors: list[str] = []
    rows = load_jsonl(records_jsonl)
    doc_ids = []
    for index, row in enumerate(rows, start=1):
        doc_id = positive_int(row.get("doc_id"))
        if doc_id is None:
            errors.append(f"{records_jsonl}:{index}: doc_id must be a positive integer")
            continue
        doc_ids.append(doc_id)
    duplicates = duplicate_values(doc_ids)
    if duplicates:
        errors.append(
            "duplicate record doc_id values: "
            + ", ".join(str(doc_id) for doc_id in duplicates[:10])
        )
    return set(doc_ids), errors, []


def load_queries(queries_json: Path) -> tuple[str, list[Any]]:
    raw = load_json(queries_json)
    if isinstance(raw, list):
        return queries_json.stem, raw
    if not isinstance(raw, dict):
        raise ValueError(f"{queries_json}: expected JSON object or query list")
    queries = raw.get("queries")
    if not isinstance(queries, list):
        raise ValueError(f"{queries_json}: expected queries list")
    return str(raw.get("name") or queries_json.stem), queries


def validate_query(
    *,
    query: Any,
    index: int,
    known_doc_ids: set[int],
    check_known_doc_ids: bool,
    expected_vector_dim: int | None,
) -> tuple[dict[str, Any] | None, int | None, list[str]]:
    label = f"queries[{index}]"
    errors: list[str] = []
    if not isinstance(query, dict):
        return None, expected_vector_dim, [f"{label}: expected JSON object"]

    name = query.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append(f"{label}.name must be a non-empty string")
        name = ""

    query_text = query.get("query")
    if not isinstance(query_text, str) or not query_text.strip():
        errors.append(f"{label}.query must be a non-empty string")
        query_text = ""

    vector = numeric_vector(query.get("vector"))
    if vector is None:
        errors.append(f"{label}.vector must be a non-empty numeric list")
        vector_dim = expected_vector_dim
    else:
        vector_dim = len(vector)
        if expected_vector_dim is None:
            expected_vector_dim = vector_dim
        elif vector_dim != expected_vector_dim:
            errors.append(
                f"{label}.vector has dim {vector_dim}, expected {expected_vector_dim}"
            )

    raw_expected = query.get("expected_doc_ids")
    expected: list[int] = []
    if not isinstance(raw_expected, list) or not raw_expected:
        errors.append(f"{label}.expected_doc_ids must be a non-empty list")
    else:
        for doc_index, raw_doc_id in enumerate(raw_expected, start=1):
            doc_id = positive_int(raw_doc_id)
            if doc_id is None:
                errors.append(
                    f"{label}.expected_doc_ids[{doc_index}] must be a positive integer"
                )
            else:
                expected.append(doc_id)

    duplicate_expected = duplicate_values(expected)
    if duplicate_expected:
        errors.append(
            f"{label}.expected_doc_ids has duplicate values: "
            + ", ".join(str(doc_id) for doc_id in duplicate_expected[:10])
        )

    unknown_expected = []
    if check_known_doc_ids:
        unknown_expected = sorted(set(expected).difference(known_doc_ids))
        if unknown_expected:
            errors.append(
                f"{label}.expected_doc_ids contains unknown doc_id values: "
                + ", ".join(str(doc_id) for doc_id in unknown_expected[:10])
            )

    return (
        {
            "name": name,
            "query": query_text,
            "expected_doc_ids": expected,
            "expected_count": len(expected),
            "unknown_expected_doc_ids": unknown_expected,
            "vector_dim": vector_dim or 0,
        },
        expected_vector_dim,
        errors,
    )


def build_report(
    *,
    queries_json: Path,
    records_jsonl: Path | None,
    min_queries: int,
    min_expected_labels: int,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    record_doc_ids, record_errors, record_warnings = load_record_doc_ids(records_jsonl)
    errors.extend(record_errors)
    warnings.extend(record_warnings)

    query_set, raw_queries = load_queries(queries_json)
    if not raw_queries:
        errors.append("queries list must not be empty")

    query_reports = []
    expected_vector_dim: int | None = None
    for index, query in enumerate(raw_queries, start=1):
        query_report, expected_vector_dim, query_errors = validate_query(
            query=query,
            index=index,
            known_doc_ids=record_doc_ids,
            check_known_doc_ids=records_jsonl is not None,
            expected_vector_dim=expected_vector_dim,
        )
        errors.extend(query_errors)
        if query_report is not None:
            query_reports.append(query_report)

    query_names = [report["name"] for report in query_reports if report["name"]]
    duplicate_query_names = duplicate_values(query_names)
    if duplicate_query_names:
        errors.append(
            "duplicate query names: "
            + ", ".join(str(name) for name in duplicate_query_names[:10])
        )

    expected_label_count = sum(report["expected_count"] for report in query_reports)
    labeled_doc_ids = {
        doc_id
        for report in query_reports
        for doc_id in report["expected_doc_ids"]
    }
    unlabeled_doc_count = 0
    if records_jsonl is not None and record_doc_ids:
        unlabeled_doc_count = len(record_doc_ids.difference(labeled_doc_ids))
        if unlabeled_doc_count == 0:
            warnings.append("records corpus has no unlabeled control documents")

    if len(query_reports) < min_queries:
        errors.append(f"query_count {len(query_reports)} is below minimum {min_queries}")
    if expected_label_count < min_expected_labels:
        errors.append(
            f"expected_label_count {expected_label_count} is below minimum {min_expected_labels}"
        )

    return {
        "schema_version": 1,
        "valid": not errors,
        "queries_json": str(queries_json),
        "records_jsonl": str(records_jsonl) if records_jsonl else "",
        "query_set": query_set,
        "query_count": len(query_reports),
        "record_count": len(record_doc_ids),
        "vector_dim": expected_vector_dim or 0,
        "expected_label_count": expected_label_count,
        "labeled_doc_count": len(labeled_doc_ids),
        "unlabeled_doc_count": unlabeled_doc_count,
        "min_queries": min_queries,
        "min_expected_labels": min_expected_labels,
        "duplicate_query_names": duplicate_query_names,
        "errors": errors,
        "warnings": warnings,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "queries": query_reports,
    }


def error_report(
    *,
    queries_json: Path,
    records_jsonl: Path | None,
    error: Exception,
    min_queries: int,
    min_expected_labels: int,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "valid": False,
        "queries_json": str(queries_json),
        "records_jsonl": str(records_jsonl) if records_jsonl else "",
        "query_set": "",
        "query_count": 0,
        "record_count": 0,
        "vector_dim": 0,
        "expected_label_count": 0,
        "labeled_doc_count": 0,
        "unlabeled_doc_count": 0,
        "min_queries": min_queries,
        "min_expected_labels": min_expected_labels,
        "duplicate_query_names": [],
        "errors": [str(error)],
        "warnings": [],
        "error_count": 1,
        "warning_count": 0,
        "queries": [],
    }


def markdown_cell(value: object) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# pgwarc_lance Quality Label Doctor",
        "",
        f"- Query set: `{report['query_set']}`",
        f"- Valid: `{str(report['valid']).lower()}`",
        f"- Queries: `{report['query_count']}`",
        f"- Records: `{report['record_count']}`",
        f"- Vector dim: `{report['vector_dim']}`",
        f"- Expected labels: `{report['expected_label_count']}`",
        f"- Labeled docs: `{report['labeled_doc_count']}`",
        f"- Unlabeled docs: `{report['unlabeled_doc_count']}`",
        "",
        "| Query | Expected Count | Unknown Doc IDs |",
        "|-------|----------------|-----------------|",
    ]
    for query in report["queries"]:
        lines.append(
            "| {name} | {count} | `{unknown}` |".format(
                name=markdown_cell(query["name"]),
                count=query["expected_count"],
                unknown=markdown_cell(query["unknown_expected_doc_ids"]),
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
    parser.add_argument("--queries-json", type=Path, required=True)
    parser.add_argument("--records-jsonl", type=Path)
    parser.add_argument("--min-queries", type=int, default=1)
    parser.add_argument("--min-expected-labels", type=int, default=1)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.min_queries <= 0:
        print("quality label doctor failed: min-queries must be positive", file=sys.stderr)
        return 2
    if args.min_expected_labels <= 0:
        print(
            "quality label doctor failed: min-expected-labels must be positive",
            file=sys.stderr,
        )
        return 2

    try:
        report = build_report(
            queries_json=args.queries_json,
            records_jsonl=args.records_jsonl,
            min_queries=args.min_queries,
            min_expected_labels=args.min_expected_labels,
        )
    except Exception as exc:
        report = error_report(
            queries_json=args.queries_json,
            records_jsonl=args.records_jsonl,
            error=exc,
            min_queries=args.min_queries,
            min_expected_labels=args.min_expected_labels,
        )

    if args.json_output:
        write_json(args.json_output, report)
    if args.markdown_output:
        write_text(args.markdown_output, render_markdown(report))

    print(
        "quality label doctor: "
        f"queries={report['query_count']} expected_labels={report['expected_label_count']} "
        f"errors={report['error_count']} warnings={report['warning_count']}"
    )
    if report["errors"]:
        for error in report["errors"]:
            print(f"quality label doctor failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
