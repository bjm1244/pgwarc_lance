#!/usr/bin/env python3
"""Validate quality run artifacts against baseline acceptance thresholds."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import eval_quality


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected JSON object")
    return payload


def number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field}: expected number")
    return float(value)


def integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field}: expected integer")
    return value


def non_success_report(
    *,
    fixture_report: dict[str, Any],
    quality: dict[str, Any],
    fixture_report_path: Path,
    quality_path: Path,
    min_docs: int,
    min_queries: int,
    min_expected_labels: int,
    min_hit_rate: float,
    min_mrr: float,
    require_real_corpus: bool,
    kind: str,
) -> dict[str, Any]:
    """Build a non-approving report for a non-success or invalid quality artifact.

    The producer's timeout/budget artifacts are valid execution failures or
    partial outputs, so their original status, completed result count, and
    timeout/budget diagnostics are preserved rather than recomputed as zero or
    success metrics. The report never becomes a successful baseline.
    """

    errors: list[str] = []
    warnings: list[str] = []

    if not bool(fixture_report.get("valid")):
        errors.append("fixture report is invalid")
        for error in fixture_report.get("errors", []):
            errors.append(f"fixture report error: {error}")
    for warning in fixture_report.get("warnings", []):
        warnings.append(f"fixture report warning: {warning}")

    errors.extend(eval_quality.artifact_status_errors(quality, kind, label="quality artifact"))

    fixture_name = str(fixture_report.get("fixture") or quality.get("fixture") or "")
    fixture_doc_count = fixture_report.get("doc_count")
    if not isinstance(fixture_doc_count, int) or isinstance(fixture_doc_count, bool):
        fixture_doc_count = 0
    fixture_query_count = fixture_report.get("query_count")
    if not isinstance(fixture_query_count, int) or isinstance(fixture_query_count, bool):
        fixture_query_count = 0
    expected_label_count = fixture_report.get("expected_label_count")
    if not isinstance(expected_label_count, int) or isinstance(expected_label_count, bool):
        expected_label_count = 0
    k = quality.get("k")
    if not isinstance(k, int) or isinstance(k, bool):
        k = 0

    source_coverage = fixture_report.get("source_coverage")
    if not isinstance(source_coverage, list):
        source_coverage = []
    source_count = fixture_report.get("source_count")
    if not isinstance(source_count, int) or isinstance(source_count, bool):
        source_count = 0
    min_source_docs = fixture_report.get("min_source_docs")
    if not isinstance(min_source_docs, int) or isinstance(min_source_docs, bool):
        min_source_docs = 0
    max_source_skipped_ratio = fixture_report.get("max_source_skipped_ratio")
    if not isinstance(max_source_skipped_ratio, (int, float)) or isinstance(
        max_source_skipped_ratio, bool
    ):
        max_source_skipped_ratio = 1.0

    report: dict[str, Any] = {
        "schema_version": 1,
        "valid": False,
        "errors": errors,
        "warnings": warnings,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "fixture": fixture_name,
        "validation_profile": "real_warc" if require_real_corpus else "default",
        "real_corpus_required": require_real_corpus,
        "fixture_report_path": str(fixture_report_path),
        "quality_path": str(quality_path),
        "provenance": fixture_report.get("provenance"),
        "provenance_schema_version": fixture_report.get("provenance_schema_version"),
        "min_source_docs": min_source_docs,
        "max_source_skipped_ratio": max_source_skipped_ratio,
        "source_count": source_count,
        "source_coverage": source_coverage,
        "thresholds": {
            "min_docs": min_docs,
            "min_queries": min_queries,
            "min_expected_labels": min_expected_labels,
            "min_hit_rate_at_k": min_hit_rate,
            "min_mrr_at_k": min_mrr,
        },
        "doc_count": fixture_doc_count,
        "query_count": fixture_query_count,
        "expected_label_count": expected_label_count,
        "k": k,
        "hit_rate_at_k": None,
        "mrr_at_k": None,
        "metrics_available": False,
        "quality_status": quality.get("status"),
        "quality_success": quality.get("success"),
        "quality_kind": kind,
        "quality_diagnostics": eval_quality.artifact_diagnostics(quality),
        "results": [],
    }
    if "labeled_doc_count" in fixture_report:
        report["labeled_doc_count"] = fixture_report["labeled_doc_count"]
    if "unlabeled_doc_count" in fixture_report:
        report["unlabeled_doc_count"] = fixture_report["unlabeled_doc_count"]
    return report


def build_report(
    *,
    fixture_report: dict[str, Any],
    quality: dict[str, Any],
    fixture_report_path: Path,
    quality_path: Path,
    min_docs: int,
    min_queries: int,
    min_expected_labels: int,
    min_hit_rate: float,
    min_mrr: float,
    require_real_corpus: bool = False,
) -> dict[str, Any]:
    quality_kind = eval_quality.artifact_status_kind(quality)
    if quality_kind != eval_quality.STATUS_KIND_SUCCESS:
        return non_success_report(
            fixture_report=fixture_report,
            quality=quality,
            fixture_report_path=fixture_report_path,
            quality_path=quality_path,
            min_docs=min_docs,
            min_queries=min_queries,
            min_expected_labels=min_expected_labels,
            min_hit_rate=min_hit_rate,
            min_mrr=min_mrr,
            require_real_corpus=require_real_corpus,
            kind=quality_kind,
        )

    errors: list[str] = []
    warnings: list[str] = []

    fixture_valid = bool(fixture_report.get("valid"))
    if not fixture_valid:
        errors.append("fixture report is invalid")
        for error in fixture_report.get("errors", []):
            errors.append(f"fixture report error: {error}")

    fixture_name = str(fixture_report.get("fixture") or quality.get("fixture") or "")
    fixture_doc_count = integer(fixture_report.get("doc_count", 0), "fixture_report.doc_count")
    fixture_query_count = integer(
        fixture_report.get("query_count", 0), "fixture_report.query_count"
    )
    expected_label_count = integer(
        fixture_report.get("expected_label_count", 0),
        "fixture_report.expected_label_count",
    )
    quality_doc_count = integer(quality.get("doc_count", 0), "quality.doc_count")
    quality_query_count = integer(quality.get("query_count", 0), "quality.query_count")
    hit_rate = number(quality.get("hit_rate_at_k"), "quality.hit_rate_at_k")
    mrr = number(quality.get("mrr_at_k"), "quality.mrr_at_k")
    k = integer(quality.get("k", 0), "quality.k")
    provenance = fixture_report.get("provenance")
    provenance_schema_version = fixture_report.get("provenance_schema_version")
    source_count = integer(fixture_report.get("source_count", 0), "fixture_report.source_count")
    min_source_docs = integer(
        fixture_report.get("min_source_docs", 0), "fixture_report.min_source_docs"
    )
    max_source_skipped_ratio = number(
        fixture_report.get("max_source_skipped_ratio", 1.0),
        "fixture_report.max_source_skipped_ratio",
    )
    labeled_doc_count = None
    if "labeled_doc_count" in fixture_report:
        labeled_doc_count = integer(
            fixture_report["labeled_doc_count"], "fixture_report.labeled_doc_count"
        )
    unlabeled_doc_count = None
    if "unlabeled_doc_count" in fixture_report:
        unlabeled_doc_count = integer(
            fixture_report["unlabeled_doc_count"], "fixture_report.unlabeled_doc_count"
        )
    source_coverage = fixture_report.get("source_coverage", [])
    if not isinstance(source_coverage, list):
        raise ValueError("fixture_report.source_coverage: expected list")
    if len(source_coverage) != source_count:
        errors.append(
            f"source coverage count {len(source_coverage)} does not match source_count {source_count}"
        )
    if provenance_schema_version == 2 and not source_coverage:
        errors.append("schema v2 source coverage must be a non-empty list")

    if require_real_corpus:
        if provenance is None:
            errors.append("real corpus mode requires a WARC provenance manifest")
        elif provenance_schema_version != 2:
            errors.append("real corpus mode requires provenance schema_version 2")
        if source_count <= 0:
            errors.append("real corpus mode requires at least one WARC source")
        if not 20 <= fixture_query_count <= 50:
            errors.append(
                f"real corpus mode requires query_count between 20 and 50, got {fixture_query_count}"
            )
        if min_hit_rate <= 0.0:
            errors.append("real corpus mode requires min_hit_rate_at_k greater than 0")
        if min_mrr <= 0.0:
            errors.append("real corpus mode requires min_mrr_at_k greater than 0")

    if quality.get("fixture") and fixture_name and quality.get("fixture") != fixture_name:
        errors.append(
            f"quality fixture {quality.get('fixture')} does not match report fixture {fixture_name}"
        )
    if quality_doc_count != fixture_doc_count:
        errors.append(
            f"quality doc_count {quality_doc_count} does not match fixture report {fixture_doc_count}"
        )
    if quality_query_count != fixture_query_count:
        errors.append(
            f"quality query_count {quality_query_count} does not match fixture report {fixture_query_count}"
        )
    if fixture_doc_count < min_docs:
        errors.append(f"doc_count {fixture_doc_count} is below minimum {min_docs}")
    if fixture_query_count < min_queries:
        errors.append(f"query_count {fixture_query_count} is below minimum {min_queries}")
    if expected_label_count < min_expected_labels:
        errors.append(
            f"expected_label_count {expected_label_count} is below minimum {min_expected_labels}"
        )
    if hit_rate < min_hit_rate:
        errors.append(f"hit_rate_at_k {hit_rate:.6f} is below minimum {min_hit_rate:.6f}")
    if mrr < min_mrr:
        errors.append(f"mrr_at_k {mrr:.6f} is below minimum {min_mrr:.6f}")

    fixture_query_names = {
        str(query.get("name"))
        for query in fixture_report.get("queries", [])
        if isinstance(query, dict)
    }
    quality_results = quality.get("results", [])
    if not isinstance(quality_results, list):
        errors.append("quality.results: expected list")
        quality_results = []
    if len(quality_results) != quality_query_count:
        errors.append(
            f"quality results count {len(quality_results)} does not match query_count {quality_query_count}"
        )

    result_summaries = []
    for result in quality_results:
        if not isinstance(result, dict):
            errors.append("quality.results contains non-object entry")
            continue
        name = str(result.get("name"))
        if fixture_query_names and name not in fixture_query_names:
            errors.append(f"quality result query {name!r} is absent from fixture report")
        result_summaries.append(
            {
                "name": name,
                "hit": bool(result.get("hit")),
                "reciprocal_rank": number(
                    result.get("reciprocal_rank", 0.0),
                    f"quality.results.{name}.reciprocal_rank",
                ),
            }
        )

    for warning in fixture_report.get("warnings", []):
        warnings.append(f"fixture report warning: {warning}")

    report = {
        "schema_version": 1,
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "fixture": fixture_name,
        "validation_profile": "real_warc" if require_real_corpus else "default",
        "real_corpus_required": require_real_corpus,
        "fixture_report_path": str(fixture_report_path),
        "quality_path": str(quality_path),
        "provenance": provenance,
        "provenance_schema_version": provenance_schema_version,
        "min_source_docs": min_source_docs,
        "max_source_skipped_ratio": max_source_skipped_ratio,
        "source_count": source_count,
        "source_coverage": source_coverage,
        "thresholds": {
            "min_docs": min_docs,
            "min_queries": min_queries,
            "min_expected_labels": min_expected_labels,
            "min_hit_rate_at_k": min_hit_rate,
            "min_mrr_at_k": min_mrr,
        },
        "doc_count": fixture_doc_count,
        "query_count": fixture_query_count,
        "expected_label_count": expected_label_count,
        "k": k,
        "hit_rate_at_k": hit_rate,
        "mrr_at_k": mrr,
        "results": result_summaries,
    }
    if labeled_doc_count is not None:
        report["labeled_doc_count"] = labeled_doc_count
    if unlabeled_doc_count is not None:
        report["unlabeled_doc_count"] = unlabeled_doc_count
    return report


def error_report(
    *,
    fixture_report_path: Path,
    quality_path: Path,
    error: Exception,
    require_real_corpus: bool = False,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "valid": False,
        "errors": [str(error)],
        "warnings": [],
        "error_count": 1,
        "warning_count": 0,
        "fixture": "",
        "validation_profile": "real_warc" if require_real_corpus else "default",
        "real_corpus_required": require_real_corpus,
        "fixture_report_path": str(fixture_report_path),
        "quality_path": str(quality_path),
        "thresholds": {},
        "doc_count": 0,
        "query_count": 0,
        "expected_label_count": 0,
        "k": 0,
        "hit_rate_at_k": 0.0,
        "mrr_at_k": 0.0,
        "results": [],
    }


def markdown_cell(value: object) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def format_metric(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.6f}"


def render_markdown(report: dict[str, Any]) -> str:
    thresholds = report["thresholds"]
    lines = [
        "# pgwarc_lance Quality Baseline Doctor",
        "",
        f"- Valid: `{str(report['valid']).lower()}`",
        f"- Validation profile: `{report.get('validation_profile', 'default')}`",
        f"- Real corpus required: `{str(report.get('real_corpus_required', False)).lower()}`",
        f"- Fixture: `{report['fixture']}`",
        f"- Docs: `{report['doc_count']}`",
        f"- Queries: `{report['query_count']}`",
        f"- Expected labels: `{report['expected_label_count']}`",
        f"- hit_rate_at_k: `{format_metric(report['hit_rate_at_k'])}`",
        f"- mrr_at_k: `{format_metric(report['mrr_at_k'])}`",
    ]

    if report.get("metrics_available") is False:
        diagnostics = report.get("quality_diagnostics") or {}
        lines.extend(
            [
                "",
                "## Quality Artifact Status",
                "",
                f"- status: `{report.get('quality_status')}`",
                f"- success: `{str(report.get('quality_success')).lower()}`",
                f"- metrics available: `false`",
                f"- completed results: `{diagnostics.get('completed_results')}`",
            ]
        )
        timeout = diagnostics.get("timeout")
        if isinstance(timeout, dict):
            lines.extend(
                [
                    f"- timeout stage: `{timeout.get('stage')}`",
                    f"- timeout reason: `{timeout.get('reason')}`",
                    f"- timeout limit_seconds: `{timeout.get('limit_seconds')}`",
                    f"- timeout client-side only: `{str(timeout.get('client_side_only')).lower()}`",
                    f"- timeout partial result count: `{timeout.get('partial_result_count')}`",
                ]
            )
            observation = timeout.get("timeout_observation")
            if isinstance(observation, dict):
                lines.extend(
                    [
                        f"- timeout observation statement_outcome: `{observation.get('statement_outcome')}`",
                        "- timeout observation backend_state_after_timeout: "
                        f"`{observation.get('backend_state_after_timeout')}`",
                        "- timeout observation cancellation_completion: "
                        f"`{observation.get('cancellation_completion')}`",
                        "- timeout observation transaction_cleanup: "
                        f"`{observation.get('transaction_cleanup')}`",
                    ]
                )
            else:
                lines.append(
                    "- timeout observation: `not recorded "
                    "(backend state and cancellation unverified)`"
                )
        budget = diagnostics.get("budget")
        if isinstance(budget, dict):
            lines.extend(
                [
                    f"- budget limit_kind: `{budget.get('limit_kind')}`",
                    f"- budget used: `{budget.get('used')}`",
                    f"- budget limit: `{budget.get('limit')}`",
                    f"- budget partial result count: `{budget.get('partial_result_count')}`",
                ]
            )

    if thresholds:
        lines.extend(
            [
                "",
                "## Thresholds",
                "",
                f"- Min docs: `{thresholds['min_docs']}`",
                f"- Min queries: `{thresholds['min_queries']}`",
                f"- Min expected labels: `{thresholds['min_expected_labels']}`",
                f"- Min hit_rate_at_k: `{thresholds['min_hit_rate_at_k']:.6f}`",
                f"- Min mrr_at_k: `{thresholds['min_mrr_at_k']:.6f}`",
            ]
        )

    if report["source_coverage"]:
        lines.extend(
            [
                "",
                "## WARC Source Coverage",
                "",
                f"- Source count: `{report['source_count']}`",
                f"- Min source docs: `{report['min_source_docs']}`",
                f"- Max skipped ratio: `{report['max_source_skipped_ratio']:.6f}`",
                "",
                "| Source | Raw | Imported | Skipped | Skip Ratio | Docs | Labeled | Unlabeled |",
                "|--------|-----|----------|---------|------------|------|---------|-----------|",
            ]
        )
        for source in report["source_coverage"]:
            if not isinstance(source, dict):
                continue
            identity = source.get("path") or source.get("url") or source.get("index")
            lines.append(
                "| {identity} | {raw} | {imported} | {skipped} | {ratio:.3f} | "
                "{docs} | {labeled} | {unlabeled} |".format(
                    identity=markdown_cell(identity),
                    raw=source.get("warc_record_count", 0),
                    imported=source.get("imported_record_count", 0),
                    skipped=source.get("skipped_record_count", 0),
                    ratio=float(source.get("skipped_ratio", 0.0)),
                    docs=source.get("doc_count", 0),
                    labeled=source.get("labeled_doc_count", 0),
                    unlabeled=source.get("unlabeled_doc_count", 0),
                )
            )

    lines.extend(["", "| Query | Hit | Reciprocal Rank |", "|-------|-----|-----------------|"])
    for result in report["results"]:
        lines.append(
            "| {name} | {hit} | {rr:.6f} |".format(
                name=markdown_cell(result["name"]),
                hit=str(result["hit"]).lower(),
                rr=result["reciprocal_rank"],
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
    parser.add_argument("--fixture-report-json", type=Path, required=True)
    parser.add_argument("--quality-json", type=Path, required=True)
    parser.add_argument("--min-docs", type=int, default=1)
    parser.add_argument("--min-queries", type=int, default=1)
    parser.add_argument("--min-expected-labels", type=int, default=1)
    parser.add_argument("--min-hit-rate", type=float, default=0.0)
    parser.add_argument("--min-mrr", type=float, default=0.0)
    parser.add_argument(
        "--require-real-corpus",
        action="store_true",
        help="require a schema v2 WARC provenance manifest and a 20-50 query baseline",
    )
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace) -> list[str]:
    errors = []
    for name in ("min_docs", "min_queries", "min_expected_labels"):
        if getattr(args, name) <= 0:
            errors.append(f"{name.replace('_', '-')} must be positive")
    for name in ("min_hit_rate", "min_mrr"):
        value = getattr(args, name)
        if value < 0.0 or value > 1.0:
            errors.append(f"{name.replace('_', '-')} must be between 0 and 1")
    if args.require_real_corpus:
        if args.min_hit_rate <= 0.0:
            errors.append("require-real-corpus needs min-hit-rate greater than 0")
        if args.min_mrr <= 0.0:
            errors.append("require-real-corpus needs min-mrr greater than 0")
    return errors


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    arg_errors = validate_args(args)
    if arg_errors:
        for error in arg_errors:
            print(f"quality baseline doctor failed: {error}", file=sys.stderr)
        return 2

    try:
        report = build_report(
            fixture_report=load_json(args.fixture_report_json),
            quality=load_json(args.quality_json),
            fixture_report_path=args.fixture_report_json,
            quality_path=args.quality_json,
            min_docs=args.min_docs,
            min_queries=args.min_queries,
            min_expected_labels=args.min_expected_labels,
            min_hit_rate=args.min_hit_rate,
            min_mrr=args.min_mrr,
            require_real_corpus=args.require_real_corpus,
        )
    except Exception as exc:
        report = error_report(
            fixture_report_path=args.fixture_report_json,
            quality_path=args.quality_json,
            error=exc,
            require_real_corpus=args.require_real_corpus,
        )

    if args.json_output:
        write_json(args.json_output, report)
    if args.markdown_output:
        write_text(args.markdown_output, render_markdown(report))

    hit_rate = report["hit_rate_at_k"]
    mrr = report["mrr_at_k"]
    hit_rate_text = "n/a" if hit_rate is None else f"{float(hit_rate):.3f}"
    mrr_text = "n/a" if mrr is None else f"{float(mrr):.3f}"
    print(
        "quality baseline doctor: "
        f"fixture={report['fixture'] or 'unknown'} docs={report['doc_count']} "
        f"queries={report['query_count']} hit_rate_at_k={hit_rate_text} "
        f"mrr_at_k={mrr_text} errors={report['error_count']} "
        f"warnings={report['warning_count']}"
    )
    if report["errors"]:
        for error in report["errors"]:
            print(f"quality baseline doctor failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
