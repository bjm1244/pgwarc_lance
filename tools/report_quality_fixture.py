#!/usr/bin/env python3
"""Report schema and label coverage for eval_quality fixtures."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import eval_quality


def duplicate_values(values: list[Any]) -> list[Any]:
    counts = Counter(values)
    return sorted(value for value, count in counts.items() if count > 1)


def query_reports(queries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reports = []
    for query in queries:
        expected = list(query["expected_doc_ids"])
        reports.append(
            {
                "name": query["name"],
                "query": query["query"],
                "expected_doc_ids": expected,
                "expected_count": len(expected),
            }
        )
    return reports


def load_provenance(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return payload


def non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field}: expected a non-negative integer")
    return value


def source_coverage_report(
    *,
    provenance: dict[str, Any],
    fixture_doc_count: int,
    provenance_source: str,
    min_source_docs: int,
    max_source_skipped_ratio: float,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    schema_version = provenance.get("schema_version")
    if schema_version == 1:
        warnings.append(
            "provenance schema v1 has no source coverage; source thresholds were not applied"
        )
        return [], errors, warnings
    if schema_version != 2:
        errors.append(f"{provenance_source}: unsupported provenance schema_version {schema_version!r}")
        return [], errors, warnings

    raw_sources = provenance.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        errors.append(f"{provenance_source}: sources must be a non-empty list")
        return [], errors, warnings

    source_spec = provenance.get("source_spec")
    if not isinstance(source_spec, dict):
        errors.append(f"{provenance_source}: source_spec must be an object")
    elif source_spec.get("count") != len(raw_sources):
        errors.append(
            f"{provenance_source}: source_spec.count {source_spec.get('count')!r} "
            f"does not match sources {len(raw_sources)}"
        )

    reports: list[dict[str, Any]] = []
    imported_total = 0
    coverage_total = 0
    for index, raw_source in enumerate(raw_sources, start=1):
        label = f"{provenance_source}.sources[{index}]"
        if not isinstance(raw_source, dict):
            errors.append(f"{label} must be an object")
            continue
        records = raw_source.get("records")
        coverage = raw_source.get("coverage")
        if not isinstance(records, dict):
            errors.append(f"{label}.records must be an object")
            continue
        if not isinstance(coverage, dict):
            errors.append(f"{label}.coverage must be an object")
            continue

        try:
            warc_record_count = non_negative_int(
                records.get("warc_record_count"), f"{label}.records.warc_record_count"
            )
            imported_record_count = non_negative_int(
                records.get("imported_record_count"),
                f"{label}.records.imported_record_count",
            )
            skipped_record_count = non_negative_int(
                records.get("skipped_record_count"),
                f"{label}.records.skipped_record_count",
            )
            doc_count = non_negative_int(coverage.get("doc_count"), f"{label}.coverage.doc_count")
            labeled_doc_count = non_negative_int(
                coverage.get("labeled_doc_count"),
                f"{label}.coverage.labeled_doc_count",
            )
            unlabeled_doc_count = non_negative_int(
                coverage.get("unlabeled_doc_count"),
                f"{label}.coverage.unlabeled_doc_count",
            )
        except ValueError as error:
            errors.append(str(error))
            continue

        if warc_record_count != imported_record_count + skipped_record_count:
            errors.append(
                f"{label}.records counts do not add up: raw={warc_record_count} "
                f"imported={imported_record_count} skipped={skipped_record_count}"
            )
        if imported_record_count != doc_count:
            errors.append(
                f"{label} imported_record_count {imported_record_count} "
                f"does not match coverage.doc_count {doc_count}"
            )
        if labeled_doc_count + unlabeled_doc_count != doc_count:
            errors.append(
                f"{label}.coverage labeled/unlabeled counts do not add up to doc_count"
            )
        if doc_count < min_source_docs:
            errors.append(
                f"{label}.coverage.doc_count {doc_count} is below minimum {min_source_docs}"
            )

        skipped_ratio = (
            skipped_record_count / warc_record_count if warc_record_count else 0.0
        )
        if skipped_ratio > max_source_skipped_ratio:
            errors.append(
                f"{label} skipped ratio {skipped_ratio:.6f} is above maximum "
                f"{max_source_skipped_ratio:.6f}"
            )

        imported_total += imported_record_count
        coverage_total += doc_count
        reports.append(
            {
                "index": index,
                "kind": raw_source.get("kind"),
                "url": raw_source.get("url"),
                "path": raw_source.get("path"),
                "warc_record_count": warc_record_count,
                "imported_record_count": imported_record_count,
                "skipped_record_count": skipped_record_count,
                "skipped_ratio": skipped_ratio,
                "doc_count": doc_count,
                "labeled_doc_count": labeled_doc_count,
                "unlabeled_doc_count": unlabeled_doc_count,
            }
        )

    if imported_total != fixture_doc_count:
        errors.append(
            f"{provenance_source} imported record total {imported_total} "
            f"does not match fixture doc_count {fixture_doc_count}"
        )
    if coverage_total != fixture_doc_count:
        errors.append(
            f"{provenance_source} coverage doc total {coverage_total} "
            f"does not match fixture doc_count {fixture_doc_count}"
        )
    return reports, errors, warnings


def build_report(
    *,
    fixture: dict[str, Any],
    source: str,
    min_docs: int,
    min_queries: int,
    provenance: dict[str, Any] | None = None,
    provenance_source: str = "",
    min_source_docs: int = 0,
    max_source_skipped_ratio: float = 1.0,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    docs = list(fixture["docs"])
    queries = list(fixture["queries"])
    doc_ids = [doc["doc_id"] for doc in docs]
    query_names = [query["name"] for query in queries]
    duplicate_doc_ids = duplicate_values(doc_ids)
    duplicate_query_names = duplicate_values(query_names)

    if len(docs) < min_docs:
        errors.append(f"doc_count {len(docs)} is below minimum {min_docs}")
    if len(queries) < min_queries:
        errors.append(f"query_count {len(queries)} is below minimum {min_queries}")
    if duplicate_doc_ids:
        errors.append(
            "duplicate doc_id values: "
            + ", ".join(str(doc_id) for doc_id in duplicate_doc_ids[:10])
        )
    if duplicate_query_names:
        errors.append(
            "duplicate query names: "
            + ", ".join(str(name) for name in duplicate_query_names[:10])
        )

    expected_counts = [len(query["expected_doc_ids"]) for query in queries]
    labeled_doc_ids = {
        doc_id
        for query in queries
        for doc_id in query["expected_doc_ids"]
    }
    unlabeled_doc_count = len(set(doc_ids).difference(labeled_doc_ids))
    if unlabeled_doc_count == 0:
        warnings.append("fixture has no unlabeled control documents")

    content_types = Counter(str(doc["content_type"]) for doc in docs)
    source_files = Counter(str(doc["source_file"]) for doc in docs)
    source_coverage: list[dict[str, Any]] = []
    provenance_errors: list[str] = []
    provenance_warnings: list[str] = []
    if provenance is not None:
        (
            source_coverage,
            provenance_errors,
            provenance_warnings,
        ) = source_coverage_report(
            provenance=provenance,
            fixture_doc_count=len(docs),
            provenance_source=provenance_source or "provenance",
            min_source_docs=min_source_docs,
            max_source_skipped_ratio=max_source_skipped_ratio,
        )
        errors.extend(provenance_errors)
        warnings.extend(provenance_warnings)
    report = {
        "schema_version": 1,
        "source": source,
        "fixture": fixture["name"],
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "vector_dim": fixture["vector_dim"],
        "doc_count": len(docs),
        "query_count": len(queries),
        "min_docs": min_docs,
        "min_queries": min_queries,
        "provenance": provenance_source or None,
        "provenance_schema_version": provenance.get("schema_version") if provenance else None,
        "min_source_docs": min_source_docs,
        "max_source_skipped_ratio": max_source_skipped_ratio,
        "source_count": len(source_coverage),
        "source_coverage": source_coverage,
        "duplicate_doc_ids": duplicate_doc_ids,
        "duplicate_query_names": duplicate_query_names,
        "expected_label_count": sum(expected_counts),
        "expected_per_query_min": min(expected_counts) if expected_counts else 0,
        "expected_per_query_max": max(expected_counts) if expected_counts else 0,
        "labeled_doc_count": len(labeled_doc_ids),
        "unlabeled_doc_count": unlabeled_doc_count,
        "content_types": dict(sorted(content_types.items())),
        "source_files": dict(sorted(source_files.items())),
        "queries": query_reports(queries),
    }
    return report


def error_report(*, source: str, error: Exception) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "source": source,
        "fixture": "",
        "valid": False,
        "errors": [str(error)],
        "warnings": [],
        "error_count": 1,
        "warning_count": 0,
        "vector_dim": 0,
        "doc_count": 0,
        "query_count": 0,
        "min_docs": 0,
        "min_queries": 0,
        "provenance": None,
        "provenance_schema_version": None,
        "min_source_docs": 0,
        "max_source_skipped_ratio": 1.0,
        "source_count": 0,
        "source_coverage": [],
        "duplicate_doc_ids": [],
        "duplicate_query_names": [],
        "expected_label_count": 0,
        "expected_per_query_min": 0,
        "expected_per_query_max": 0,
        "labeled_doc_count": 0,
        "unlabeled_doc_count": 0,
        "content_types": {},
        "source_files": {},
        "queries": [],
    }


def markdown_cell(value: object) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# pgwarc_lance Quality Fixture Report",
        "",
        f"- Source: `{report['source']}`",
        f"- Fixture: `{report['fixture']}`",
        f"- Valid: `{str(report['valid']).lower()}`",
        f"- Vector dim: `{report['vector_dim']}`",
        f"- Docs: `{report['doc_count']}`",
        f"- Queries: `{report['query_count']}`",
        f"- Expected labels: `{report['expected_label_count']}`",
        f"- Labeled docs: `{report['labeled_doc_count']}`",
        f"- Unlabeled docs: `{report['unlabeled_doc_count']}`",
    ]
    if report["source_coverage"]:
        lines.extend(
            [
                "",
                "| Source | Raw | Imported | Skipped | Skip Ratio | Docs | Labeled | Unlabeled |",
                "|--------|-----|----------|---------|------------|------|---------|-----------|",
            ]
        )
        for source in report["source_coverage"]:
            identity = source.get("path") or source.get("url") or source["index"]
            lines.append(
                "| {identity} | {raw} | {imported} | {skipped} | {ratio:.3f} | "
                "{docs} | {labeled} | {unlabeled} |".format(
                    identity=markdown_cell(identity),
                    raw=source["warc_record_count"],
                    imported=source["imported_record_count"],
                    skipped=source["skipped_record_count"],
                    ratio=source["skipped_ratio"],
                    docs=source["doc_count"],
                    labeled=source["labeled_doc_count"],
                    unlabeled=source["unlabeled_doc_count"],
                )
            )
    lines.extend(
        [
            "",
            "| Query | Expected Count | Expected Doc IDs |",
            "|-------|----------------|------------------|",
        ]
    )
    for query in report["queries"]:
        lines.append(
            "| {name} | {count} | `{doc_ids}` |".format(
                name=markdown_cell(query["name"]),
                count=query["expected_count"],
                doc_ids=markdown_cell(query["expected_doc_ids"]),
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
    parser.add_argument("--fixture-json", type=Path)
    parser.add_argument("--min-docs", type=int, default=1)
    parser.add_argument("--min-queries", type=int, default=1)
    parser.add_argument("--provenance-json", type=Path)
    parser.add_argument("--min-source-docs", type=int, default=0)
    parser.add_argument("--max-source-skipped-ratio", type=float, default=1.0)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    source = str(args.fixture_json) if args.fixture_json else "synthetic"
    if args.min_docs <= 0:
        print("quality fixture report failed: min-docs must be positive", file=sys.stderr)
        return 2
    if args.min_queries <= 0:
        print("quality fixture report failed: min-queries must be positive", file=sys.stderr)
        return 2
    if args.min_source_docs < 0:
        print("quality fixture report failed: min-source-docs must be non-negative", file=sys.stderr)
        return 2
    if not 0.0 <= args.max_source_skipped_ratio <= 1.0:
        print(
            "quality fixture report failed: max-source-skipped-ratio must be between 0 and 1",
            file=sys.stderr,
        )
        return 2

    try:
        fixture = eval_quality.load_fixture(
            args.fixture_json,
            reject_duplicate_identities=False,
        )
        provenance = load_provenance(args.provenance_json) if args.provenance_json else None
        report = build_report(
            fixture=fixture,
            source=source,
            min_docs=args.min_docs,
            min_queries=args.min_queries,
            provenance=provenance,
            provenance_source=str(args.provenance_json) if args.provenance_json else "",
            min_source_docs=args.min_source_docs,
            max_source_skipped_ratio=args.max_source_skipped_ratio,
        )
    except Exception as exc:
        report = error_report(source=source, error=exc)

    if args.json_output:
        write_json(args.json_output, report)
    if args.markdown_output:
        write_text(args.markdown_output, render_markdown(report))

    print(
        "quality fixture report: "
        f"fixture={report['fixture'] or 'unknown'} docs={report['doc_count']} "
        f"queries={report['query_count']} errors={report['error_count']} "
        f"warnings={report['warning_count']}"
    )
    if report["errors"]:
        for error in report["errors"]:
            print(f"quality fixture report failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
