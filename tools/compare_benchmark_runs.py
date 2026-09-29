#!/usr/bin/env python3
"""Compare two finalized WARC BM25 mode benchmark artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any


BENCHMARK_NAME = "warc_importer_bm25_mode_comparison"
MODE_NAMES = ("function", "bulk", "copy")
HASH_CHUNK_SIZE = 1024 * 1024
CONFIG_FIELDS = (
    "benchmark",
    "body_words",
    "vector_dim",
    "batch_size",
    "execute",
    "include_hash_vectors",
    "reset_between_modes",
)


def is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def is_finite_non_negative_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and value >= 0
    )


def load_json(path: Path, label: str, errors: list[str]) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        errors.append(f"{label} cannot be read: {error}")
        return None
    except json.JSONDecodeError as error:
        errors.append(f"{label} is not valid JSON: {error}")
        return None
    if not isinstance(value, dict):
        errors.append(f"{label} must contain a JSON object")
        return None
    return value


def validate_payload(
    payload: dict[str, Any], label: str
) -> tuple[list[str], dict[int, dict[str, dict[str, Any]]]]:
    errors: list[str] = []
    results_by_rows: dict[int, dict[str, dict[str, Any]]] = {}

    if payload.get("benchmark") != BENCHMARK_NAME:
        errors.append(f"{label}.benchmark must be {BENCHMARK_NAME!r}")

    for field in ("body_words", "vector_dim", "batch_size"):
        if not is_positive_int(payload.get(field)):
            errors.append(f"{label}.{field} must be a positive integer")

    for field in ("execute", "include_hash_vectors", "reset_between_modes"):
        if not isinstance(payload.get(field), bool):
            errors.append(f"{label}.{field} must be a boolean")
    if payload.get("execute") is not True:
        errors.append(f"{label}.execute must be true to compare execution time")

    rows = payload.get("rows")
    row_values: list[int] = []
    if not isinstance(rows, list) or not rows:
        errors.append(f"{label}.rows must be a non-empty list")
    else:
        for index, row in enumerate(rows):
            if not is_positive_int(row):
                errors.append(f"{label}.rows[{index}] must be a positive integer")
            else:
                row_values.append(row)
        if len(row_values) != len(set(row_values)):
            errors.append(f"{label}.rows must not contain duplicates")

    results = payload.get("results")
    if not isinstance(results, list) or not results:
        errors.append(f"{label}.results must be a non-empty list")
        return errors, results_by_rows

    for index, result in enumerate(results):
        result_label = f"{label}.results[{index}]"
        if not isinstance(result, dict):
            errors.append(f"{result_label} must be a JSON object")
            continue
        result_rows = result.get("rows")
        if not is_positive_int(result_rows):
            errors.append(f"{result_label}.rows must be a positive integer")
            continue
        if result_rows in results_by_rows:
            errors.append(f"{label}.results contains duplicate rows={result_rows}")
            continue
        if row_values and result_rows not in row_values:
            errors.append(f"{result_label}.rows is not declared in {label}.rows")

        modes = result.get("modes")
        if not isinstance(modes, list) or not modes:
            errors.append(f"{result_label}.modes must be a non-empty list")
            continue
        mode_by_name: dict[str, dict[str, Any]] = {}
        for mode_index, mode in enumerate(modes):
            mode_label = f"{result_label}.modes[{mode_index}]"
            if not isinstance(mode, dict):
                errors.append(f"{mode_label} must be a JSON object")
                continue
            mode_name = mode.get("bm25_mode")
            if not isinstance(mode_name, str) or not mode_name:
                errors.append(f"{mode_label}.bm25_mode must be a non-empty string")
                continue
            if mode_name in mode_by_name:
                errors.append(f"{result_label}.modes contains duplicate {mode_name!r}")
                continue
            if mode_name not in MODE_NAMES:
                errors.append(f"{mode_label}.bm25_mode is unsupported: {mode_name!r}")
            for field in ("sql_bytes", "sql_s", "execute_s"):
                value = mode.get(field)
                if field == "sql_bytes":
                    valid = is_positive_int(value)
                else:
                    valid = is_finite_non_negative_number(value)
                if not valid:
                    errors.append(
                        f"{mode_label}.{field} must be "
                        + ("a positive integer" if field == "sql_bytes" else "a finite non-negative number")
                    )
            mode_by_name[mode_name] = mode

        if set(mode_by_name) != set(MODE_NAMES):
            expected = ", ".join(MODE_NAMES)
            found = ", ".join(sorted(mode_by_name)) if mode_by_name else "none"
            errors.append(f"{result_label}.modes must include {expected}; found {found}")
        results_by_rows[result_rows] = mode_by_name

    if row_values and set(results_by_rows) != set(row_values):
        expected = ", ".join(str(row) for row in row_values)
        found = ", ".join(str(row) for row in sorted(results_by_rows)) if results_by_rows else "none"
        errors.append(f"{label}.results rows must match {expected}; found {found}")

    return errors, results_by_rows


def regression_ratio(baseline: float, candidate: float) -> float | None:
    if baseline == 0:
        return 0.0 if candidate == 0 else None
    return candidate / baseline - 1.0


def build_report(
    baseline_path: Path,
    candidate_path: Path,
    max_regression_ratio: Any,
) -> dict[str, Any]:
    errors: list[str] = []
    comparisons: list[dict[str, Any]] = []

    threshold: float | None = None
    if (
        isinstance(max_regression_ratio, (int, float))
        and not isinstance(max_regression_ratio, bool)
        and math.isfinite(float(max_regression_ratio))
        and max_regression_ratio >= 0
    ):
        threshold = float(max_regression_ratio)
    else:
        errors.append("max_regression_ratio must be a finite non-negative number")

    baseline = load_json(baseline_path, "baseline", errors)
    candidate = load_json(candidate_path, "candidate", errors)
    input_provenance = {
        "baseline": build_input_provenance(baseline_path, "baseline", errors),
        "candidate": build_input_provenance(candidate_path, "candidate", errors),
    }
    baseline_rows: dict[int, dict[str, dict[str, Any]]] = {}
    candidate_rows: dict[int, dict[str, dict[str, Any]]] = {}
    if baseline is not None:
        payload_errors, baseline_rows = validate_payload(baseline, "baseline")
        errors.extend(payload_errors)
    if candidate is not None:
        payload_errors, candidate_rows = validate_payload(candidate, "candidate")
        errors.extend(payload_errors)

    if baseline is not None and candidate is not None:
        for field in CONFIG_FIELDS:
            if baseline.get(field) != candidate.get(field):
                errors.append(
                    f"configuration mismatch for {field}: "
                    f"baseline={baseline.get(field)!r}, candidate={candidate.get(field)!r}"
                )
        if set(baseline_rows) != set(candidate_rows):
            errors.append(
                "configuration mismatch for rows: "
                f"baseline={sorted(baseline_rows)}, candidate={sorted(candidate_rows)}"
            )

    if not errors and threshold is not None:
        for rows in sorted(baseline_rows):
            for mode_name in MODE_NAMES:
                baseline_value = float(baseline_rows[rows][mode_name]["execute_s"])
                candidate_value = float(candidate_rows[rows][mode_name]["execute_s"])
                change = regression_ratio(baseline_value, candidate_value)
                regressed = (
                    candidate_value > baseline_value
                    if change is None
                    else change > threshold
                )
                comparison = {
                    "rows": rows,
                    "bm25_mode": mode_name,
                    "metric": "execute_s",
                    "baseline": baseline_value,
                    "candidate": candidate_value,
                    "change_ratio": change,
                    "regressed": regressed,
                }
                comparisons.append(comparison)
                if regressed:
                    if change is None:
                        rendered_change = "infinite"
                    else:
                        rendered_change = f"{change:.6f}"
                    errors.append(
                        f"rows={rows} bm25_mode={mode_name} execute_s regression "
                        f"{rendered_change} exceeds max {threshold:.6f}"
                    )

    return {
        "schema_version": 2,
        "benchmark": BENCHMARK_NAME,
        "baseline": str(baseline_path),
        "candidate": str(candidate_path),
        "input_provenance": input_provenance,
        "metric": "execute_s",
        "max_regression_ratio": threshold,
        "valid": not errors,
        "passed": not errors,
        "errors": errors,
        "warnings": [],
        "comparison_count": len(comparisons),
        "regression_count": sum(1 for comparison in comparisons if comparison["regressed"]),
        "comparisons": comparisons,
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_input_provenance(
    path: Path, label: str, errors: list[str]
) -> dict[str, Any]:
    provenance: dict[str, Any] = {"path": str(path), "bytes": None, "sha256": None}
    try:
        provenance["bytes"] = path.stat().st_size
        provenance["sha256"] = sha256_file(path)
    except OSError as error:
        if path.exists():
            errors.append(f"{label} cannot be hashed: {error}")
    return provenance


def is_finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def is_non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def is_sha256_hex(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def validate_report(
    report: dict[str, Any],
    artifact: str = "benchmark_comparison.json",
    *,
    verify_input_paths: bool = False,
) -> list[str]:
    """Validate a persisted comparison report without rerunning either benchmark."""

    errors: list[str] = []
    schema_version = report.get("schema_version")
    if schema_version not in (1, 2):
        errors.append(f"{artifact}.schema_version must be 1 or 2")
    if report.get("benchmark") != BENCHMARK_NAME:
        errors.append(f"{artifact}.benchmark must be {BENCHMARK_NAME!r}")
    for field in ("baseline", "candidate"):
        if not isinstance(report.get(field), str) or not report.get(field):
            errors.append(f"{artifact}.{field} must be a non-empty string")
    if report.get("metric") != "execute_s":
        errors.append(f"{artifact}.metric must be 'execute_s'")

    input_provenance = report.get("input_provenance")
    if schema_version == 2 and not isinstance(input_provenance, dict):
        errors.append(f"{artifact}.input_provenance must be a JSON object for schema 2")
    if input_provenance is not None and not isinstance(input_provenance, dict):
        errors.append(f"{artifact}.input_provenance must be a JSON object")
        input_provenance = None
    if isinstance(input_provenance, dict):
        for role in ("baseline", "candidate"):
            label = f"{artifact}.input_provenance.{role}"
            source = input_provenance.get(role)
            if not isinstance(source, dict):
                errors.append(f"{label} must be a JSON object")
                continue
            source_path = source.get("path")
            if not isinstance(source_path, str) or not source_path:
                errors.append(f"{label}.path must be a non-empty string")
            elif source_path != report.get(role):
                errors.append(f"{label}.path must match {artifact}.{role}")
            source_bytes = source.get("bytes")
            if not is_non_negative_int(source_bytes):
                errors.append(f"{label}.bytes must be a non-negative integer")
            source_sha256 = source.get("sha256")
            if not is_sha256_hex(source_sha256):
                errors.append(f"{label}.sha256 must be a 64-character lowercase hex string")
            if (
                verify_input_paths
                and isinstance(source_path, str)
                and is_non_negative_int(source_bytes)
                and is_sha256_hex(source_sha256)
            ):
                source_file = Path(source_path)
                if source_file.is_file():
                    try:
                        actual_bytes = source_file.stat().st_size
                        actual_sha256 = sha256_file(source_file)
                    except OSError as error:
                        errors.append(f"{label} cannot be rehashed: {error}")
                    else:
                        if actual_bytes != source_bytes:
                            errors.append(
                                f"{label}.bytes drift: expected {source_bytes}, got {actual_bytes}"
                            )
                        if actual_sha256 != source_sha256:
                            errors.append(f"{label}.sha256 drift from recorded input")

    threshold = report.get("max_regression_ratio")
    if not is_finite_non_negative_number(threshold):
        errors.append(
            f"{artifact}.max_regression_ratio must be a finite non-negative number"
        )
    threshold_value = float(threshold) if is_finite_non_negative_number(threshold) else None

    for field in ("valid", "passed"):
        if not isinstance(report.get(field), bool):
            errors.append(f"{artifact}.{field} must be a boolean")

    report_errors = report.get("errors")
    if not isinstance(report_errors, list) or any(
        not isinstance(error, str) for error in report_errors
    ):
        errors.append(f"{artifact}.errors must be a list of strings")
    report_warnings = report.get("warnings")
    if not isinstance(report_warnings, list) or any(
        not isinstance(warning, str) for warning in report_warnings
    ):
        errors.append(f"{artifact}.warnings must be a list of strings")

    comparison_count = report.get("comparison_count")
    if not is_non_negative_int(comparison_count):
        errors.append(f"{artifact}.comparison_count must be a non-negative integer")
    regression_count = report.get("regression_count")
    if not is_non_negative_int(regression_count):
        errors.append(f"{artifact}.regression_count must be a non-negative integer")

    comparisons = report.get("comparisons")
    if not isinstance(comparisons, list):
        errors.append(f"{artifact}.comparisons must be a list")
        comparisons = []
    if is_non_negative_int(comparison_count) and comparison_count != len(comparisons):
        errors.append(
            f"{artifact}.comparison_count {comparison_count} does not match "
            f"comparisons length {len(comparisons)}"
        )

    seen: set[tuple[int, str]] = set()
    modes_by_rows: dict[int, set[str]] = {}
    actual_regression_count = 0
    for index, comparison in enumerate(comparisons):
        label = f"{artifact}.comparisons[{index}]"
        if not isinstance(comparison, dict):
            errors.append(f"{label} must be a JSON object")
            continue
        rows = comparison.get("rows")
        if not is_positive_int(rows):
            errors.append(f"{label}.rows must be a positive integer")
            rows = None
        mode = comparison.get("bm25_mode")
        if not isinstance(mode, str) or mode not in MODE_NAMES:
            errors.append(f"{label}.bm25_mode must be one of: {', '.join(MODE_NAMES)}")
            mode = None
        if rows is not None and mode is not None:
            key = (rows, mode)
            if key in seen:
                errors.append(f"{label} duplicates rows={rows} bm25_mode={mode}")
            seen.add(key)
            modes_by_rows.setdefault(rows, set()).add(mode)

        if comparison.get("metric") != "execute_s":
            errors.append(f"{label}.metric must be 'execute_s'")
        baseline = comparison.get("baseline")
        candidate = comparison.get("candidate")
        if not is_finite_non_negative_number(baseline):
            errors.append(f"{label}.baseline must be a finite non-negative number")
        if not is_finite_non_negative_number(candidate):
            errors.append(f"{label}.candidate must be a finite non-negative number")
        change_ratio = comparison.get("change_ratio")
        if change_ratio is not None and not is_finite_number(change_ratio):
            errors.append(f"{label}.change_ratio must be null or a finite number")
        regressed = comparison.get("regressed")
        if not isinstance(regressed, bool):
            errors.append(f"{label}.regressed must be a boolean")
        elif regressed:
            actual_regression_count += 1

        if (
            is_finite_non_negative_number(baseline)
            and is_finite_non_negative_number(candidate)
            and threshold_value is not None
        ):
            expected_change = regression_ratio(float(baseline), float(candidate))
            if expected_change is None:
                expected_regressed = float(candidate) > float(baseline)
            else:
                expected_regressed = expected_change > threshold_value
                if change_ratio is None or abs(float(change_ratio) - expected_change) > 1e-9:
                    errors.append(f"{label}.change_ratio does not match baseline/candidate")
            if isinstance(regressed, bool) and regressed != expected_regressed:
                errors.append(f"{label}.regressed does not match the configured threshold")

    for rows, modes in sorted(modes_by_rows.items()):
        if modes != set(MODE_NAMES):
            expected = ", ".join(MODE_NAMES)
            found = ", ".join(sorted(modes)) if modes else "none"
            errors.append(
                f"{artifact}.comparisons rows={rows} must include {expected}; found {found}"
            )
    if is_non_negative_int(regression_count) and regression_count != actual_regression_count:
        errors.append(
            f"{artifact}.regression_count {regression_count} does not match "
            f"comparisons {actual_regression_count}"
        )

    if isinstance(report.get("valid"), bool) and isinstance(report_errors, list):
        if report["valid"] is True and report_errors:
            errors.append(f"{artifact}.valid=true requires errors=[]")
        if report["valid"] is False and not report_errors:
            errors.append(f"{artifact}.valid=false requires at least one error")
    if isinstance(report.get("passed"), bool) and isinstance(report_errors, list):
        if report["passed"] is True and report_errors:
            errors.append(f"{artifact}.passed=true requires errors=[]")
        if report["passed"] is False and not report_errors:
            errors.append(f"{artifact}.passed=false requires at least one error")
    if (
        isinstance(report.get("valid"), bool)
        and isinstance(report.get("passed"), bool)
        and report["valid"] != report["passed"]
    ):
        errors.append(f"{artifact}.valid and passed must agree")
    if report.get("passed") is True and not comparisons:
        errors.append(f"{artifact}.passed=true requires at least one comparison")

    return errors


def markdown_cell(value: object) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# pgwarc_lance Benchmark Comparison",
        "",
        f"- Benchmark: `{report['benchmark']}`",
        f"- Baseline: `{report['baseline']}`",
        f"- Candidate: `{report['candidate']}`",
        f"- Metric: `{report['metric']}`",
        f"- Max regression ratio: `{report['max_regression_ratio']}`",
        f"- Valid: `{str(report['valid']).lower()}`",
        f"- Passed: `{str(report['passed']).lower()}`",
        f"- Comparisons: `{report['comparison_count']}`",
        f"- Regressions: `{report['regression_count']}`",
        "",
        "| Rows | Mode | Metric | Baseline | Candidate | Change ratio | Regressed |",
        "|---:|---|---|---:|---:|---:|---|",
    ]
    input_provenance = report.get("input_provenance")
    if isinstance(input_provenance, dict):
        for index, role in enumerate(("baseline", "candidate")):
            source = input_provenance.get(role)
            if isinstance(source, dict):
                lines.insert(
                    5 + index,
                    f"- {role.title()} input: `{source.get('bytes')}` bytes, "
                    f"SHA-256 `{source.get('sha256')}`",
                )
    for comparison in report["comparisons"]:
        change = comparison["change_ratio"]
        rendered_change = "infinite" if change is None else f"{float(change):+.2%}"
        lines.append(
            "| {rows} | {mode} | {metric} | {baseline:.6f} | {candidate:.6f} | {change} | {regressed} |".format(
                rows=comparison["rows"],
                mode=markdown_cell(comparison["bm25_mode"]),
                metric=markdown_cell(comparison["metric"]),
                baseline=comparison["baseline"],
                candidate=comparison["candidate"],
                change=rendered_change,
                regressed=str(comparison["regressed"]).lower(),
            )
        )

    if report["errors"]:
        lines.extend(["", "## Errors", ""])
        lines.extend(f"- {markdown_cell(error)}" for error in report["errors"])
    return "\n".join(lines).rstrip() + "\n"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, report: dict[str, Any]) -> None:
    write_text(path, json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-json", type=Path, required=True)
    parser.add_argument("--candidate-json", type=Path, required=True)
    parser.add_argument("--max-regression-ratio", type=float, required=True)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_report(
        args.baseline_json,
        args.candidate_json,
        args.max_regression_ratio,
    )
    if args.json_output:
        write_json(args.json_output, report)
    if args.markdown_output:
        write_text(args.markdown_output, render_markdown(report))

    print(
        "benchmark comparison: "
        f"valid={str(report['valid']).lower()} "
        f"passed={str(report['passed']).lower()} "
        f"comparisons={report['comparison_count']} "
        f"regressions={report['regression_count']}"
    )
    if report["errors"]:
        for error in report["errors"]:
            print(f"benchmark comparison failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
