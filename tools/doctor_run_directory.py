#!/usr/bin/env python3
"""Check whether a pgwarc_lance run directory has expected artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import compare_benchmark_runs
import doctor_import_summary
import eval_quality
import execution_budget


CHUNK_SIZE = 1024 * 1024
PROFILE_REQUIREMENTS = {
    "generic": ["run_metadata.json", "run_manifest.json"],
    "benchmark": ["run_metadata.json", "run_manifest.json"],
    "quality": [
        "run_metadata.json",
        "run_manifest.json",
        "quality_fixture_report.json",
        "quality.json",
    ],
    "import": ["run_metadata.json", "run_manifest.json", "import.summary.json"],
    "packaging": [
        "run_metadata.json",
        "run_manifest.json",
        "release_artifacts_matrix.json",
        "release_smoke_matrix.json",
    ],
}
PROFILE_STATUS_ARTIFACTS = {
    "quality": {
        "quality_fixture_report.json": "valid",
        "quality_baseline_doctor.json": "valid",
    },
    "import": {
        "import_doctor.json": "all_valid",
    },
    "packaging": {
        "release_artifacts_matrix.json": "all_valid",
        "release_smoke_matrix.json": "all_passed",
    },
}
STATUS_ARTIFACT_FIELDS = {
    "benchmark_comparison.json": "passed",
    "quality_fixture_report.json": "valid",
    "quality_baseline_doctor.json": "valid",
    "quality_labels_doctor.json": "valid",
    "import_doctor.json": "all_valid",
    "release_artifacts_matrix.json": "all_valid",
    "release_smoke_matrix.json": "all_passed",
}
BENCHMARK_ARTIFACT_NAMES = {
    "bm25.json": "bm25_index_document",
    "lance.json": "lance_insert_many",
    "warc.json": "warc_importer",
    "warc_bm25_modes.json": "warc_importer_bm25_mode_comparison",
}
QUALITY_FIXTURE_REPORT_ARTIFACT_NAMES = {"quality_fixture_report.json"}
QUALITY_FIXTURE_ARTIFACT_NAMES = {"quality.fixture.json"}
QUALITY_EVAL_ARTIFACT_NAMES = {"quality.json"}
QUALITY_BASELINE_DOCTOR_ARTIFACT_NAMES = {"quality_baseline_doctor.json"}
IMPORT_SUMMARY_ARTIFACT_NAMES = {"import.summary.json"}
IMPORT_DOCTOR_ARTIFACT_NAMES = {"import_doctor.json"}
RELEASE_ARTIFACT_MATRIX_ARTIFACT_NAMES = {"release_artifacts_matrix.json"}
RELEASE_SMOKE_MATRIX_ARTIFACT_NAMES = {"release_smoke_matrix.json"}
BENCHMARK_COMPARISON_ARTIFACT_NAMES = {"benchmark_comparison.json"}
WARC_PROVENANCE_ARTIFACT_NAMES = {"warc_provenance.json"}
RELEASE_ARTIFACT_FILE_LABELS = {"shared_library", "control", "sql"}
BM25_MODE_NAMES = {"function", "bulk", "copy"}
GIT_HEAD_LENGTHS = {40, 64}


def load_json(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        return None, [f"cannot read {path.name}: {error}"]
    except json.JSONDecodeError as error:
        return None, [f"invalid JSON in {path.name}: {error}"]
    if not isinstance(data, dict):
        return None, [f"{path.name} must contain a JSON object"]
    return data, []


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_file_entries(manifest: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if manifest is None:
        return {}
    files = manifest.get("files", [])
    if not isinstance(files, list):
        return {}
    entries = {}
    for file in files:
        if isinstance(file, dict) and isinstance(file.get("path"), str):
            entries[file["path"]] = file
    return entries


def manifest_paths(manifest: dict[str, Any] | None) -> set[str]:
    return set(manifest_file_entries(manifest))


def profile_requirements(profile: str, extra_required: list[str]) -> list[str]:
    requirements = list(PROFILE_REQUIREMENTS[profile])
    for required in extra_required:
        if required not in requirements:
            requirements.append(required)
    return requirements


def duplicate_values(values: list[Any]) -> list[Any]:
    counts = Counter(values)
    return sorted(value for value, count in counts.items() if count > 1)


def status_artifact_fields(profile: str, requirements: list[str]) -> dict[str, str]:
    artifacts = dict(PROFILE_STATUS_ARTIFACTS.get(profile, {}))
    for required in requirements:
        field = STATUS_ARTIFACT_FIELDS.get(required)
        if field is not None:
            artifacts[required] = field
    return artifacts


def check_status_artifacts(run_dir: Path, profile: str, requirements: list[str]) -> list[str]:
    errors = []
    for artifact, field in status_artifact_fields(profile, requirements).items():
        path = run_dir / artifact
        if not path.is_file():
            continue
        payload, load_errors = load_json(path)
        errors.extend(load_errors)
        if payload is None:
            continue
        value = payload.get(field)
        if value is not True:
            errors.append(f"{artifact} does not report {field}=true")
    return errors


def is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def is_non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def is_non_negative_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0


def check_positive_int(payload: dict[str, Any], field: str, label: str) -> list[str]:
    if is_positive_int(payload.get(field)):
        return []
    return [f"{label}.{field} must be a positive integer"]


def check_non_negative_number(payload: dict[str, Any], field: str, label: str) -> list[str]:
    if is_non_negative_number(payload.get(field)):
        return []
    return [f"{label}.{field} must be a non-negative number"]


def check_probability(payload: dict[str, Any], field: str, label: str) -> list[str]:
    value = payload.get(field)
    if is_non_negative_number(value) and float(value) <= 1.0:
        return []
    return [f"{label}.{field} must be a number between 0 and 1"]


def benchmark_results(payload: dict[str, Any], artifact: str) -> tuple[list[Any], list[str]]:
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        return [], [f"{artifact}.results must be a non-empty list"]
    return results, []


def validate_bm25_artifact(payload: dict[str, Any], artifact: str) -> list[str]:
    errors: list[str] = []
    results, result_errors = benchmark_results(payload, artifact)
    errors.extend(result_errors)
    for index, result in enumerate(results):
        label = f"{artifact}.results[{index}]"
        if not isinstance(result, dict):
            errors.append(f"{label} must be a JSON object")
            continue
        errors.extend(check_positive_int(result, "rows", label))
        errors.extend(check_non_negative_number(result, "elapsed_s", label))
        errors.extend(check_non_negative_number(result, "docs_per_s", label))
        errors.extend(check_non_negative_number(result, "term_rows_per_s", label))
    return errors


def validate_lance_artifact(payload: dict[str, Any], artifact: str) -> list[str]:
    errors: list[str] = []
    results, result_errors = benchmark_results(payload, artifact)
    errors.extend(result_errors)
    for index, result in enumerate(results):
        label = f"{artifact}.results[{index}]"
        if not isinstance(result, dict):
            errors.append(f"{label} must be a JSON object")
            continue
        errors.extend(check_positive_int(result, "rows", label))
        errors.extend(check_non_negative_number(result, "batch_s", label))
        errors.extend(check_positive_int(result, "batch_count", label))
    return errors


def validate_warc_artifact(payload: dict[str, Any], artifact: str) -> list[str]:
    errors: list[str] = []
    errors.extend(check_positive_int(payload, "rows", artifact))
    errors.extend(check_positive_int(payload, "requested_rows", artifact))
    errors.extend(check_positive_int(payload, "sql_bytes", artifact))
    errors.extend(check_non_negative_number(payload, "generate_s", artifact))
    errors.extend(check_non_negative_number(payload, "parse_s", artifact))
    errors.extend(check_non_negative_number(payload, "sql_s", artifact))
    execute_s = payload.get("execute_s")
    if execute_s is not None and not is_non_negative_number(execute_s):
        errors.append(f"{artifact}.execute_s must be null or a non-negative number")
    return errors


def validate_warc_bm25_modes_artifact(payload: dict[str, Any], artifact: str) -> list[str]:
    errors: list[str] = []
    results, result_errors = benchmark_results(payload, artifact)
    errors.extend(result_errors)
    for index, result in enumerate(results):
        label = f"{artifact}.results[{index}]"
        if not isinstance(result, dict):
            errors.append(f"{label} must be a JSON object")
            continue
        errors.extend(check_positive_int(result, "rows", label))
        modes = result.get("modes")
        if not isinstance(modes, list) or not modes:
            errors.append(f"{label}.modes must be a non-empty list")
            continue
        mode_names = set()
        for mode_index, mode in enumerate(modes):
            mode_label = f"{label}.modes[{mode_index}]"
            if not isinstance(mode, dict):
                errors.append(f"{mode_label} must be a JSON object")
                continue
            mode_name = mode.get("bm25_mode")
            if isinstance(mode_name, str):
                mode_names.add(mode_name)
            else:
                errors.append(f"{mode_label}.bm25_mode must be a string")
            errors.extend(check_positive_int(mode, "sql_bytes", mode_label))
            errors.extend(check_non_negative_number(mode, "sql_s", mode_label))
            execute_s = mode.get("execute_s")
            if execute_s is not None and not is_non_negative_number(execute_s):
                errors.append(f"{mode_label}.execute_s must be null or a non-negative number")
        if mode_names != BM25_MODE_NAMES:
            expected = ", ".join(sorted(BM25_MODE_NAMES))
            found = ", ".join(sorted(mode_names)) if mode_names else "none"
            errors.append(f"{label}.modes must include {expected}; found {found}")
    return errors


def validate_benchmark_artifact(artifact: str, payload: dict[str, Any]) -> list[str]:
    expected_benchmark = BENCHMARK_ARTIFACT_NAMES[artifact]
    errors: list[str] = []
    if payload.get("benchmark") != expected_benchmark:
        errors.append(f"{artifact}.benchmark must be {expected_benchmark!r}")

    validators = {
        "bm25.json": validate_bm25_artifact,
        "lance.json": validate_lance_artifact,
        "warc.json": validate_warc_artifact,
        "warc_bm25_modes.json": validate_warc_bm25_modes_artifact,
    }
    errors.extend(validators[artifact](payload, artifact))
    return errors


def validate_quality_fixture_artifact(artifact: str, payload: dict[str, Any]) -> list[str]:
    try:
        fixture = eval_quality.fixture_from_dict(payload, artifact)
    except Exception as error:
        return [str(error)]

    errors: list[str] = []
    duplicate_doc_ids = duplicate_values([doc["doc_id"] for doc in fixture["docs"]])
    if duplicate_doc_ids:
        errors.append(
            f"{artifact}: duplicate doc_id values: "
            + ", ".join(str(doc_id) for doc_id in duplicate_doc_ids[:10])
        )
    duplicate_query_names = duplicate_values([query["name"] for query in fixture["queries"]])
    if duplicate_query_names:
        errors.append(
            f"{artifact}: duplicate query names: "
            + ", ".join(str(name) for name in duplicate_query_names[:10])
        )
    return errors


def check_required_benchmark_artifacts(
    run_dir: Path,
    profile: str,
    requirements: list[str],
) -> list[str]:
    if profile != "benchmark":
        return []

    errors: list[str] = []
    for required in requirements:
        if required not in BENCHMARK_ARTIFACT_NAMES:
            continue
        path = run_dir / required
        if not path.is_file():
            continue
        payload, load_errors = load_json(path)
        errors.extend(load_errors)
        if payload is None:
            continue
        errors.extend(validate_benchmark_artifact(required, payload))
    return errors


def check_required_benchmark_comparison_artifacts(
    run_dir: Path,
    requirements: list[str],
) -> list[str]:
    errors: list[str] = []
    for required in requirements:
        if required not in BENCHMARK_COMPARISON_ARTIFACT_NAMES:
            continue
        path = run_dir / required
        if not path.is_file():
            continue
        payload, load_errors = load_json(path)
        errors.extend(load_errors)
        if payload is None:
            continue
        errors.extend(
            compare_benchmark_runs.validate_report(
                payload,
                required,
                verify_input_paths=True,
            )
        )
    return errors


def check_required_quality_fixture_artifacts(run_dir: Path, requirements: list[str]) -> list[str]:
    errors: list[str] = []
    for required in requirements:
        if required not in QUALITY_FIXTURE_ARTIFACT_NAMES:
            continue
        path = run_dir / required
        if not path.is_file():
            continue
        payload, load_errors = load_json(path)
        errors.extend(load_errors)
        if payload is None:
            continue
        errors.extend(validate_quality_fixture_artifact(required, payload))
    return errors


def validate_quality_source_coverage(artifact: str, payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    provenance_schema_version = payload.get("provenance_schema_version")
    has_source_fields = "source_count" in payload or "source_coverage" in payload
    if not has_source_fields and provenance_schema_version != 2:
        return errors

    source_count = payload.get("source_count")
    source_coverage = payload.get("source_coverage")
    if not is_non_negative_int(source_count):
        errors.append(f"{artifact}.source_count must be a non-negative integer")
    if not isinstance(source_coverage, list):
        errors.append(f"{artifact}.source_coverage must be a list")
        return errors
    if is_non_negative_int(source_count) and len(source_coverage) != source_count:
        errors.append(
            f"{artifact}.source_coverage count {len(source_coverage)} does not match "
            f"source_count {source_count}"
        )
    if provenance_schema_version == 2 and not source_coverage:
        errors.append(
            f"{artifact}.source_coverage must be a non-empty list for provenance schema v2"
        )

    min_source_docs = payload.get("min_source_docs")
    if not is_non_negative_int(min_source_docs):
        errors.append(f"{artifact}.min_source_docs must be a non-negative integer")
    errors.extend(check_probability(payload, "max_source_skipped_ratio", artifact))
    max_source_skipped_ratio = payload.get("max_source_skipped_ratio")
    if not source_coverage:
        return errors

    imported_total = 0
    coverage_total = 0
    labeled_total = 0
    unlabeled_total = 0
    count_fields = (
        "warc_record_count",
        "imported_record_count",
        "skipped_record_count",
        "doc_count",
        "labeled_doc_count",
        "unlabeled_doc_count",
    )
    for index, source in enumerate(source_coverage):
        label = f"{artifact}.source_coverage[{index}]"
        if not isinstance(source, dict):
            errors.append(f"{label} must be a JSON object")
            continue
        if not is_positive_int(source.get("index")):
            errors.append(f"{label}.index must be a positive integer")
        valid_counts = True
        for field in count_fields:
            if not is_non_negative_int(source.get(field)):
                errors.append(f"{label}.{field} must be a non-negative integer")
                valid_counts = False
        valid_ratio = is_non_negative_number(source.get("skipped_ratio")) and float(
            source.get("skipped_ratio")
        ) <= 1.0
        if not valid_ratio:
            errors.append(f"{label}.skipped_ratio must be a number between 0 and 1")
        if not valid_counts:
            continue

        raw_count = source["warc_record_count"]
        imported_count = source["imported_record_count"]
        skipped_count = source["skipped_record_count"]
        doc_count = source["doc_count"]
        labeled_count = source["labeled_doc_count"]
        unlabeled_count = source["unlabeled_doc_count"]
        if raw_count != imported_count + skipped_count:
            errors.append(
                f"{label} records counts do not add up: raw={raw_count} "
                f"imported={imported_count} skipped={skipped_count}"
            )
        if imported_count != doc_count:
            errors.append(
                f"{label} imported_record_count {imported_count} does not match "
                f"doc_count {doc_count}"
            )
        if labeled_count + unlabeled_count != doc_count:
            errors.append(f"{label} labeled/unlabeled counts do not add up to doc_count")

        expected_ratio = skipped_count / raw_count if raw_count else 0.0
        if valid_ratio and abs(float(source["skipped_ratio"]) - expected_ratio) > 1e-9:
            errors.append(
                f"{label}.skipped_ratio {source['skipped_ratio']!r} does not match "
                f"skipped/raw {expected_ratio:.6f}"
            )
        if is_non_negative_int(min_source_docs) and doc_count < min_source_docs:
            errors.append(
                f"{label}.doc_count {doc_count} is below minimum {min_source_docs}"
            )
        if (
            is_non_negative_number(max_source_skipped_ratio)
            and float(max_source_skipped_ratio) <= 1.0
            and expected_ratio > float(max_source_skipped_ratio)
        ):
            errors.append(
                f"{label} skipped ratio {expected_ratio:.6f} is above maximum "
                f"{float(max_source_skipped_ratio):.6f}"
            )
        imported_total += imported_count
        coverage_total += doc_count
        labeled_total += labeled_count
        unlabeled_total += unlabeled_count

    doc_count = payload.get("doc_count")
    if is_positive_int(doc_count):
        if imported_total != doc_count:
            errors.append(
                f"{artifact} imported record total {imported_total} does not match "
                f"doc_count {doc_count}"
            )
        if coverage_total != doc_count:
            errors.append(
                f"{artifact} coverage doc total {coverage_total} does not match "
                f"doc_count {doc_count}"
            )
    if is_non_negative_int(payload.get("labeled_doc_count")) and labeled_total != payload[
        "labeled_doc_count"
    ]:
        errors.append(
            f"{artifact} labeled doc total {labeled_total} does not match "
            f"labeled_doc_count {payload['labeled_doc_count']}"
        )
    if is_non_negative_int(payload.get("unlabeled_doc_count")) and unlabeled_total != payload[
        "unlabeled_doc_count"
    ]:
        errors.append(
            f"{artifact} unlabeled doc total {unlabeled_total} does not match "
            f"unlabeled_doc_count {payload['unlabeled_doc_count']}"
        )
    return errors


def validate_quality_fixture_report_artifact(artifact: str, payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload.get("fixture"), str) or not payload.get("fixture"):
        errors.append(f"{artifact}.fixture must be a non-empty string")
    errors.extend(check_positive_int(payload, "vector_dim", artifact))
    errors.extend(check_positive_int(payload, "doc_count", artifact))
    errors.extend(check_positive_int(payload, "query_count", artifact))
    errors.extend(check_positive_int(payload, "expected_label_count", artifact))
    if not is_non_negative_int(payload.get("labeled_doc_count")):
        errors.append(f"{artifact}.labeled_doc_count must be a non-negative integer")
    if not is_non_negative_int(payload.get("unlabeled_doc_count")):
        errors.append(f"{artifact}.unlabeled_doc_count must be a non-negative integer")
    errors.extend(validate_quality_source_coverage(artifact, payload))

    queries = payload.get("queries")
    query_count = payload.get("query_count")
    if not isinstance(queries, list) or not queries:
        errors.append(f"{artifact}.queries must be a non-empty list")
        return errors
    if is_positive_int(query_count) and len(queries) != query_count:
        errors.append(
            f"{artifact}.queries count {len(queries)} does not match query_count {query_count}"
        )
    expected_label_total = 0
    for index, query in enumerate(queries):
        label = f"{artifact}.queries[{index}]"
        if not isinstance(query, dict):
            errors.append(f"{label} must be a JSON object")
            continue
        if not isinstance(query.get("name"), str) or not query.get("name"):
            errors.append(f"{label}.name must be a non-empty string")
        expected_count = query.get("expected_count")
        if not is_positive_int(expected_count):
            errors.append(f"{label}.expected_count must be a positive integer")
        else:
            expected_label_total += expected_count
        expected_doc_ids = query.get("expected_doc_ids")
        if not isinstance(expected_doc_ids, list) or not expected_doc_ids:
            errors.append(f"{label}.expected_doc_ids must be a non-empty list")
        elif any(not is_positive_int(doc_id) for doc_id in expected_doc_ids):
            errors.append(f"{label}.expected_doc_ids must contain positive integers")
    if is_positive_int(payload.get("expected_label_count")) and expected_label_total:
        expected_label_count = payload["expected_label_count"]
        if expected_label_total != expected_label_count:
            errors.append(
                f"{artifact}.expected_label_count {expected_label_count} "
                f"does not match query total {expected_label_total}"
            )
    return errors


def check_required_quality_fixture_report_artifacts(
    run_dir: Path,
    requirements: list[str],
) -> list[str]:
    errors: list[str] = []
    for required in requirements:
        if required not in QUALITY_FIXTURE_REPORT_ARTIFACT_NAMES:
            continue
        path = run_dir / required
        if not path.is_file():
            continue
        payload, load_errors = load_json(path)
        errors.extend(load_errors)
        if payload is None:
            continue
        errors.extend(validate_quality_fixture_report_artifact(required, payload))
    return errors


def validate_quality_eval_artifact(artifact: str, payload: dict[str, Any]) -> list[str]:
    kind = eval_quality.artifact_status_kind(payload)
    if kind == eval_quality.STATUS_KIND_SUCCESS:
        return validate_quality_eval_success_artifact(artifact, payload)
    errors = eval_quality.artifact_status_errors(payload, kind, label=artifact)
    errors.extend(validate_quality_eval_non_success_structure(artifact, payload, kind))
    return errors


def validate_quality_eval_non_success_structure(
    artifact: str,
    payload: dict[str, Any],
    kind: str,
) -> list[str]:
    if kind != eval_quality.STATUS_KIND_NON_SUCCESS:
        return []
    status = payload.get("status")
    results = payload.get("results")
    errors: list[str] = []
    if not isinstance(results, list):
        errors.append(f"{artifact}.results must be a list for status {status!r}")
        results = []
    if status == eval_quality.STATUS_TIMEOUT:
        errors.extend(validate_quality_timeout_diagnostics(artifact, payload, results))
    elif status == execution_budget.STATUS_EXHAUSTED:
        errors.extend(validate_quality_budget_diagnostics(artifact, payload, results))
    return errors


def validate_timeout_observation(
    artifact: str,
    observation: Any,
    errors: list[str],
) -> str | None:
    """Validate a present timeout observation contract.

    Returns the validated ``statement_outcome`` when the observation is a well
    formed object, or ``None`` for malformed input. A malformed observation adds
    structured errors and is never treated as a confirmed server outcome. The
    object must carry exactly the four supported fields
    (``eval_quality.TIMEOUT_OBSERVATION_FIELDS``): any extra key such as a
    backend pid, cancellation flag, or rollback claim is rejected, because the
    runtime cannot observe or guarantee those. This must only be called when the
    ``timeout_observation`` key is present: an absent key is the legacy form and
    is handled by the caller.
    """

    if not isinstance(observation, dict):
        errors.append(f"{artifact}.timeout.timeout_observation must be an object")
        return None

    unsupported_fields = sorted(
        key
        for key in observation
        if key not in eval_quality.TIMEOUT_OBSERVATION_FIELDS
    )
    if unsupported_fields:
        errors.append(
            f"{artifact}.timeout.timeout_observation must not include unsupported "
            "fields: " + ", ".join(unsupported_fields)
        )

    outcome = observation.get("statement_outcome")
    if outcome not in eval_quality.TIMEOUT_STATEMENT_OUTCOMES:
        errors.append(
            f"{artifact}.timeout.timeout_observation.statement_outcome must be one of: "
            + ", ".join(eval_quality.TIMEOUT_STATEMENT_OUTCOMES)
        )
    expected_fields = (
        (
            "backend_state_after_timeout",
            eval_quality.BACKEND_STATE_AFTER_TIMEOUT_UNOBSERVED,
        ),
        ("cancellation_completion", eval_quality.CANCELLATION_COMPLETION_UNVERIFIED),
        ("transaction_cleanup", eval_quality.TRANSACTION_CLEANUP_UNVERIFIED),
    )
    for field, expected in expected_fields:
        if observation.get(field) != expected:
            errors.append(
                f"{artifact}.timeout.timeout_observation.{field} must be {expected!r}"
            )
    if outcome in eval_quality.TIMEOUT_STATEMENT_OUTCOMES:
        return outcome
    return None


def validate_server_timeout_fields(artifact: str, timeout: dict[str, Any]) -> list[str]:
    """Validate the optional server limitation fields for a server timeout."""

    errors: list[str] = []
    requested = timeout.get("requested_statement_timeout_ms")
    if requested is not None and (
        not is_non_negative_number(requested) or float(requested) <= 0
    ):
        errors.append(
            f"{artifact}.timeout.requested_statement_timeout_ms must be null or a positive number"
        )
    if not is_positive_int(timeout.get("effective_statement_timeout_ms")):
        errors.append(
            f"{artifact}.timeout.effective_statement_timeout_ms must be a positive integer"
        )
    sqlstate = timeout.get("sqlstate")
    if sqlstate is not None and not isinstance(sqlstate, str):
        errors.append(f"{artifact}.timeout.sqlstate must be a string or null")
    return errors


def validate_quality_timeout_diagnostics(
    artifact: str,
    payload: dict[str, Any],
    results: list[Any],
) -> list[str]:
    errors: list[str] = []
    timeout = payload.get("timeout")
    if not isinstance(timeout, dict):
        errors.append(
            f"{artifact}.timeout must be an object for status {eval_quality.STATUS_TIMEOUT!r}"
        )
        return errors

    stage = timeout.get("stage")
    if stage not in {"setup", "query"}:
        errors.append(f"{artifact}.timeout.stage must be 'setup' or 'query'")
    if not isinstance(timeout.get("reason"), str) or not timeout.get("reason"):
        errors.append(f"{artifact}.timeout.reason must be a non-empty string")
    limit_seconds = timeout.get("limit_seconds")
    if not is_non_negative_number(limit_seconds) or float(limit_seconds) <= 0:
        errors.append(f"{artifact}.timeout.limit_seconds must be a positive number")

    completed_results = timeout.get("completed_results")
    if not is_non_negative_int(completed_results):
        errors.append(f"{artifact}.timeout.completed_results must be a non-negative integer")
    elif completed_results != len(results):
        errors.append(
            f"{artifact}.timeout.completed_results {completed_results} does not match "
            f"results count {len(results)}"
        )

    partial_preserved = timeout.get("partial_result_preserved")
    if not isinstance(partial_preserved, bool):
        errors.append(f"{artifact}.timeout.partial_result_preserved must be a boolean")
    elif partial_preserved != bool(results):
        errors.append(
            f"{artifact}.timeout.partial_result_preserved must match non-empty results"
        )

    partial_count = timeout.get("partial_result_count")
    if not is_non_negative_int(partial_count):
        errors.append(f"{artifact}.timeout.partial_result_count must be a non-negative integer")
    elif partial_count != len(results):
        errors.append(
            f"{artifact}.timeout.partial_result_count {partial_count} does not match "
            f"results count {len(results)}"
        )

    if "timeout_observation" in timeout:
        observation_outcome = validate_timeout_observation(
            artifact, timeout["timeout_observation"], errors
        )
    else:
        observation_outcome = None
    timeout_source = timeout.get("timeout_source")
    if timeout_source is not None and not isinstance(timeout_source, str):
        errors.append(f"{artifact}.timeout.timeout_source must be a string or null")

    client_side_only = timeout.get("client_side_only")
    if not isinstance(client_side_only, bool):
        errors.append(f"{artifact}.timeout.client_side_only must be a boolean")
    elif client_side_only:
        if timeout_source == eval_quality.TIMEOUT_SOURCE_SERVER:
            errors.append(
                f"{artifact}.timeout.client_side_only=true conflicts with "
                f"timeout_source {eval_quality.TIMEOUT_SOURCE_SERVER!r}"
            )
        if observation_outcome == eval_quality.STATEMENT_OUTCOME_SERVER_TIMEOUT:
            errors.append(
                f"{artifact}.timeout.client_side_only=true conflicts with "
                "timeout_observation.statement_outcome "
                f"{eval_quality.STATEMENT_OUTCOME_SERVER_TIMEOUT!r}"
            )
    else:
        if timeout_source != eval_quality.TIMEOUT_SOURCE_SERVER:
            errors.append(
                f"{artifact}.timeout.client_side_only=false requires timeout_source "
                f"{eval_quality.TIMEOUT_SOURCE_SERVER!r}"
            )
        if observation_outcome != eval_quality.STATEMENT_OUTCOME_SERVER_TIMEOUT:
            errors.append(
                f"{artifact}.timeout.timeout_observation.statement_outcome must be "
                f"{eval_quality.STATEMENT_OUTCOME_SERVER_TIMEOUT!r} when "
                "client_side_only=false"
            )
        errors.extend(validate_server_timeout_fields(artifact, timeout))

    if stage == "setup" and results:
        errors.append(f"{artifact}.timeout.stage=setup must not include results")
    return errors


def validate_quality_budget_diagnostics(
    artifact: str,
    payload: dict[str, Any],
    results: list[Any],
) -> list[str]:
    errors: list[str] = []
    budget = payload.get("budget")
    if not isinstance(budget, dict):
        errors.append(
            f"{artifact}.budget must be an object for status {execution_budget.STATUS_EXHAUSTED!r}"
        )
        return errors

    if budget.get("status") != execution_budget.STATUS_EXHAUSTED:
        errors.append(
            f"{artifact}.budget.status must be {execution_budget.STATUS_EXHAUSTED!r}"
        )
    if budget.get("success") is not False:
        errors.append(f"{artifact}.budget.success must be false")
    if budget.get("limit_kind") not in execution_budget.REQUIRED_LIMITS:
        errors.append(
            f"{artifact}.budget.limit_kind must be one of: "
            + ", ".join(execution_budget.REQUIRED_LIMITS)
        )
    if not is_non_negative_number(budget.get("used")):
        errors.append(f"{artifact}.budget.used must be a non-negative number")
    if not is_non_negative_number(budget.get("limit")):
        errors.append(f"{artifact}.budget.limit must be a non-negative number")

    partial_preserved = budget.get("partial_result_preserved")
    if not isinstance(partial_preserved, bool):
        errors.append(f"{artifact}.budget.partial_result_preserved must be a boolean")

    partial_count = budget.get("partial_result_count")
    if not is_non_negative_int(partial_count):
        errors.append(f"{artifact}.budget.partial_result_count must be a non-negative integer")
    elif partial_count != len(results):
        errors.append(
            f"{artifact}.budget.partial_result_count {partial_count} does not match "
            f"results count {len(results)}"
        )
    if not isinstance(budget.get("detail"), str) or not budget.get("detail"):
        errors.append(f"{artifact}.budget.detail must be a non-empty string")
    return errors


def validate_quality_eval_success_artifact(artifact: str, payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("benchmark") != "retrieval_quality_smoke":
        errors.append(f"{artifact}.benchmark must be 'retrieval_quality_smoke'")
    if not isinstance(payload.get("fixture"), str) or not payload.get("fixture"):
        errors.append(f"{artifact}.fixture must be a non-empty string")
    errors.extend(check_positive_int(payload, "doc_count", artifact))
    errors.extend(check_positive_int(payload, "query_count", artifact))
    errors.extend(check_positive_int(payload, "vector_dim", artifact))
    errors.extend(check_positive_int(payload, "k", artifact))
    errors.extend(check_probability(payload, "hit_rate_at_k", artifact))
    errors.extend(check_probability(payload, "mrr_at_k", artifact))

    results = payload.get("results")
    query_count = payload.get("query_count")
    if not isinstance(results, list) or not results:
        errors.append(f"{artifact}.results must be a non-empty list")
        return errors
    if is_positive_int(query_count) and len(results) != query_count:
        errors.append(
            f"{artifact}.results count {len(results)} does not match query_count {query_count}"
        )
    for index, result in enumerate(results):
        label = f"{artifact}.results[{index}]"
        if not isinstance(result, dict):
            errors.append(f"{label} must be a JSON object")
            continue
        if not isinstance(result.get("name"), str) or not result.get("name"):
            errors.append(f"{label}.name must be a non-empty string")
        expected_doc_ids = result.get("expected_doc_ids")
        if not isinstance(expected_doc_ids, list) or not expected_doc_ids:
            errors.append(f"{label}.expected_doc_ids must be a non-empty list")
        elif any(not is_positive_int(doc_id) for doc_id in expected_doc_ids):
            errors.append(f"{label}.expected_doc_ids must contain positive integers")
        returned_doc_ids = result.get("returned_doc_ids")
        if not isinstance(returned_doc_ids, list):
            errors.append(f"{label}.returned_doc_ids must be a list")
        elif any(not is_positive_int(doc_id) for doc_id in returned_doc_ids):
            errors.append(f"{label}.returned_doc_ids must contain positive integers")
        if not isinstance(result.get("hit"), bool):
            errors.append(f"{label}.hit must be a boolean")
        errors.extend(check_probability(result, "reciprocal_rank", label))
    return errors


def check_required_quality_eval_artifacts(run_dir: Path, requirements: list[str]) -> list[str]:
    errors: list[str] = []
    for required in requirements:
        if required not in QUALITY_EVAL_ARTIFACT_NAMES:
            continue
        path = run_dir / required
        if not path.is_file():
            continue
        payload, load_errors = load_json(path)
        errors.extend(load_errors)
        if payload is None:
            continue
        errors.extend(validate_quality_eval_artifact(required, payload))
    return errors


def validate_quality_baseline_doctor_artifact(artifact: str, payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload.get("fixture"), str) or not payload.get("fixture"):
        errors.append(f"{artifact}.fixture must be a non-empty string")
    errors.extend(check_positive_int(payload, "doc_count", artifact))
    errors.extend(check_positive_int(payload, "query_count", artifact))
    errors.extend(check_positive_int(payload, "expected_label_count", artifact))
    errors.extend(check_positive_int(payload, "k", artifact))
    errors.extend(check_probability(payload, "hit_rate_at_k", artifact))
    errors.extend(check_probability(payload, "mrr_at_k", artifact))

    thresholds = payload.get("thresholds")
    if not isinstance(thresholds, dict) or not thresholds:
        errors.append(f"{artifact}.thresholds must be a non-empty object")
    else:
        for field in ("min_docs", "min_queries", "min_expected_labels"):
            errors.extend(check_positive_int(thresholds, field, f"{artifact}.thresholds"))
        for field in ("min_hit_rate_at_k", "min_mrr_at_k"):
            errors.extend(check_probability(thresholds, field, f"{artifact}.thresholds"))
    errors.extend(validate_quality_source_coverage(artifact, payload))

    results = payload.get("results")
    query_count = payload.get("query_count")
    if not isinstance(results, list) or not results:
        errors.append(f"{artifact}.results must be a non-empty list")
        return errors
    if is_positive_int(query_count) and len(results) != query_count:
        errors.append(
            f"{artifact}.results count {len(results)} does not match query_count {query_count}"
        )
    for index, result in enumerate(results):
        label = f"{artifact}.results[{index}]"
        if not isinstance(result, dict):
            errors.append(f"{label} must be a JSON object")
            continue
        if not isinstance(result.get("name"), str) or not result.get("name"):
            errors.append(f"{label}.name must be a non-empty string")
        if not isinstance(result.get("hit"), bool):
            errors.append(f"{label}.hit must be a boolean")
        errors.extend(check_probability(result, "reciprocal_rank", label))
    return errors


def check_required_quality_baseline_doctor_artifacts(
    run_dir: Path,
    requirements: list[str],
) -> list[str]:
    errors: list[str] = []
    for required in requirements:
        if required not in QUALITY_BASELINE_DOCTOR_ARTIFACT_NAMES:
            continue
        path = run_dir / required
        if not path.is_file():
            continue
        payload, load_errors = load_json(path)
        errors.extend(load_errors)
        if payload is None:
            continue
        errors.extend(validate_quality_baseline_doctor_artifact(required, payload))
    return errors


def validate_import_summary_artifact(artifact: str, payload: dict[str, Any]) -> list[str]:
    report = doctor_import_summary.diagnose_summary(Path(artifact), payload, [])
    return [f"{artifact}: {error}" for error in report["errors"]]


def check_required_import_summary_artifacts(run_dir: Path, requirements: list[str]) -> list[str]:
    errors: list[str] = []
    for required in requirements:
        if required not in IMPORT_SUMMARY_ARTIFACT_NAMES:
            continue
        path = run_dir / required
        if not path.is_file():
            continue
        payload, load_errors = load_json(path)
        errors.extend(load_errors)
        if payload is None:
            continue
        errors.extend(validate_import_summary_artifact(required, payload))
    return errors


def check_string_list(value: Any, label: str) -> tuple[int, list[str]]:
    if not isinstance(value, list):
        return 0, [f"{label} must be a list"]
    if any(not isinstance(item, str) for item in value):
        return len(value), [f"{label} must contain strings"]
    return len(value), []


def is_lower_hex_string(value: Any, lengths: set[int]) -> bool:
    return (
        isinstance(value, str)
        and len(value) in lengths
        and all(character in "0123456789abcdef" for character in value)
    )


def validate_run_metadata_artifact(artifact: str, payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("schema_version") != 1:
        errors.append(f"{artifact}.schema_version must be 1")
    for field in ("generated_at", "run_dir"):
        if not isinstance(payload.get(field), str) or not payload.get(field):
            errors.append(f"{artifact}.{field} must be a non-empty string")
    command = payload.get("command")
    if command is not None and not isinstance(command, str):
        errors.append(f"{artifact}.command must be a string or null")

    environment = payload.get("environment")
    if not isinstance(environment, dict):
        errors.append(f"{artifact}.environment must be a JSON object")
    else:
        for name, value in environment.items():
            if not isinstance(name, str):
                errors.append(f"{artifact}.environment keys must be strings")
                break
            if value is not None and not isinstance(value, str):
                errors.append(f"{artifact}.environment.{name} must be a string or null")

    git = payload.get("git")
    if not isinstance(git, dict):
        errors.append(f"{artifact}.git must be a JSON object")
    else:
        if not isinstance(git.get("root"), str) or not git.get("root"):
            errors.append(f"{artifact}.git.root must be a non-empty string")
        head = git.get("head")
        if head is not None and not is_lower_hex_string(head, GIT_HEAD_LENGTHS):
            errors.append(f"{artifact}.git.head must be a 40- or 64-character lowercase hex string or null")
        head_short = git.get("head_short")
        if head_short is not None and not is_lower_hex_string(head_short, {7}):
            errors.append(f"{artifact}.git.head_short must be a 7-character lowercase hex string or null")
        if isinstance(head, str) and isinstance(head_short, str) and not head.startswith(head_short):
            errors.append(f"{artifact}.git.head_short must match the git head prefix")
        branch = git.get("branch")
        if branch is not None and (not isinstance(branch, str) or not branch):
            errors.append(f"{artifact}.git.branch must be a non-empty string or null")
        if git.get("dirty") is not None and not isinstance(git.get("dirty"), bool):
            errors.append(f"{artifact}.git.dirty must be a boolean or null")
        _, status_errors = check_string_list(git.get("status"), f"{artifact}.git.status")
        errors.extend(status_errors)

    _, warning_errors = check_string_list(payload.get("warnings"), f"{artifact}.warnings")
    errors.extend(warning_errors)
    return errors


def validate_import_doctor_artifact(artifact: str, payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload.get("all_valid"), bool):
        errors.append(f"{artifact}.all_valid must be a boolean")
    if not is_positive_int(payload.get("summary_count")):
        errors.append(f"{artifact}.summary_count must be a positive integer")
    if not is_non_negative_int(payload.get("error_count")):
        errors.append(f"{artifact}.error_count must be a non-negative integer")
    if not is_non_negative_int(payload.get("warning_count")):
        errors.append(f"{artifact}.warning_count must be a non-negative integer")

    summaries = payload.get("summaries")
    if not isinstance(summaries, list) or not summaries:
        errors.append(f"{artifact}.summaries must be a non-empty list")
        return errors
    summary_count = payload.get("summary_count")
    if is_positive_int(summary_count) and len(summaries) != summary_count:
        errors.append(
            f"{artifact}.summary_count {summary_count} does not match summaries count {len(summaries)}"
        )

    total_errors = 0
    total_warnings = 0
    valid_flags: list[bool] = []
    for index, summary_report in enumerate(summaries):
        label = f"{artifact}.summaries[{index}]"
        if not isinstance(summary_report, dict):
            errors.append(f"{label} must be a JSON object")
            continue
        if not isinstance(summary_report.get("path"), str) or not summary_report.get("path"):
            errors.append(f"{label}.path must be a non-empty string")
        valid = summary_report.get("valid")
        if isinstance(valid, bool):
            valid_flags.append(valid)
        else:
            errors.append(f"{label}.valid must be a boolean")

        entry_errors, entry_error_errors = check_string_list(summary_report.get("errors"), f"{label}.errors")
        errors.extend(entry_error_errors)
        total_errors += entry_errors
        entry_warnings, entry_warning_errors = check_string_list(
            summary_report.get("warnings"),
            f"{label}.warnings",
        )
        errors.extend(entry_warning_errors)
        total_warnings += entry_warnings

        summary = summary_report.get("summary")
        if summary is None:
            if valid is True:
                errors.append(f"{label}.summary must be an object when valid=true")
        elif not isinstance(summary, dict):
            errors.append(f"{label}.summary must be an object or null")
        elif valid is True:
            errors.extend(validate_import_summary_artifact(f"{label}.summary", summary))

        if valid is True and entry_errors:
            errors.append(f"{label}.valid=true must not include errors")
        if valid is False and not entry_errors:
            errors.append(f"{label}.valid=false must include at least one error")

    error_count = payload.get("error_count")
    if is_non_negative_int(error_count) and error_count != total_errors:
        errors.append(
            f"{artifact}.error_count {error_count} does not match summaries total {total_errors}"
        )
    warning_count = payload.get("warning_count")
    if is_non_negative_int(warning_count) and warning_count != total_warnings:
        errors.append(
            f"{artifact}.warning_count {warning_count} does not match summaries total {total_warnings}"
        )
    all_valid = payload.get("all_valid")
    if isinstance(all_valid, bool) and len(valid_flags) == len(summaries):
        summaries_all_valid = all(valid_flags)
        if all_valid != summaries_all_valid:
            expected = str(summaries_all_valid).lower()
            errors.append(f"{artifact}.all_valid must be {expected} based on summaries")
    return errors


def check_required_import_doctor_artifacts(run_dir: Path, requirements: list[str]) -> list[str]:
    errors: list[str] = []
    for required in requirements:
        if required not in IMPORT_DOCTOR_ARTIFACT_NAMES:
            continue
        path = run_dir / required
        if not path.is_file():
            continue
        payload, load_errors = load_json(path)
        errors.extend(load_errors)
        if payload is None:
            continue
        errors.extend(validate_import_doctor_artifact(required, payload))
    return errors


def is_sha256_hex(value: Any) -> bool:
    return is_lower_hex_string(value, {64})


def existing_recorded_path(run_dir: Path, value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = Path(value).expanduser()
    candidates = [raw] if raw.is_absolute() else [run_dir / raw, Path.cwd() / raw]
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.exists():
            return resolved
    return None


def validate_recorded_file_hash(
    *,
    run_dir: Path,
    label: str,
    path_value: Any,
    expected_bytes: int | None,
    expected_sha256: Any,
) -> list[str]:
    path = existing_recorded_path(run_dir, path_value)
    if path is None:
        return []
    if not path.is_file():
        return [f"{label} points to a non-file source path: {path_value}"]

    errors: list[str] = []
    actual_bytes = path.stat().st_size
    if expected_bytes is not None and actual_bytes != expected_bytes:
        errors.append(
            f"{label}.bytes mismatch for {path_value}: "
            f"expected {expected_bytes}, got {actual_bytes}"
        )
    if is_sha256_hex(expected_sha256) and sha256_file(path) != expected_sha256:
        errors.append(f"{label}.sha256 mismatch for {path_value}")
    return errors


def parse_warc_identity(
    label: str,
    payload: dict[str, Any],
) -> tuple[str | None, str | None, list[str]]:
    errors: list[str] = []
    path_value = payload.get("path")
    url_value = payload.get("url")
    path_valid = path_value is None or (
        isinstance(path_value, str) and bool(path_value.strip())
    )
    url_valid = url_value is None or (
        isinstance(url_value, str) and bool(url_value.strip())
    )
    if not path_valid:
        errors.append(f"{label}.path must be a non-empty string or null")
    if not url_valid:
        errors.append(f"{label}.url must be a non-empty string or null")
    if path_valid and url_valid and ((path_value is None) == (url_value is None)):
        errors.append(f"{label} must contain exactly one of path or url")
    return (
        path_value if isinstance(path_value, str) and path_value.strip() else None,
        url_value if isinstance(url_value, str) and url_value.strip() else None,
        errors,
    )


def validate_warc_source_stats(label: str, source: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    records = source.get("records")
    coverage = source.get("coverage")
    if not isinstance(records, dict):
        errors.append(f"{label}.records must be a JSON object")
    if not isinstance(coverage, dict):
        errors.append(f"{label}.coverage must be a JSON object")
    if not isinstance(records, dict) or not isinstance(coverage, dict):
        return errors

    record_fields = (
        "warc_record_count",
        "imported_record_count",
        "skipped_record_count",
    )
    coverage_fields = ("doc_count", "labeled_doc_count", "unlabeled_doc_count")
    record_values: dict[str, int] = {}
    coverage_values: dict[str, int] = {}
    for field in record_fields:
        value = records.get(field)
        if not is_non_negative_int(value):
            errors.append(f"{label}.records.{field} must be a non-negative integer")
        else:
            record_values[field] = value
    for field in coverage_fields:
        value = coverage.get(field)
        if not is_non_negative_int(value):
            errors.append(f"{label}.coverage.{field} must be a non-negative integer")
        else:
            coverage_values[field] = value

    if len(record_values) == len(record_fields):
        if record_values["warc_record_count"] != (
            record_values["imported_record_count"]
            + record_values["skipped_record_count"]
        ):
            errors.append(f"{label}.records counts do not add up")
    if len(coverage_values) == len(coverage_fields):
        if record_values.get("imported_record_count") != coverage_values["doc_count"]:
            errors.append(f"{label} imported records do not match coverage.doc_count")
        if coverage_values["labeled_doc_count"] + coverage_values["unlabeled_doc_count"] != coverage_values[
            "doc_count"
        ]:
            errors.append(f"{label}.coverage labeled/unlabeled counts do not add up")
    return errors


def validate_warc_source_entry(
    *,
    run_dir: Path,
    label: str,
    source: Any,
    expected_path: str | None = None,
    expected_url: str | None = None,
    require_stats: bool = False,
) -> list[str]:
    if not isinstance(source, dict):
        return [f"{label} must be a JSON object"]
    errors: list[str] = []
    if not isinstance(source.get("kind"), str) or not source.get("kind"):
        errors.append(f"{label}.kind must be a non-empty string")
    path_value, url_value, identity_errors = parse_warc_identity(label, source)
    errors.extend(identity_errors)
    if expected_path is not None and path_value != expected_path:
        errors.append(f"{label}.path does not match source_spec")
    if expected_url is not None and url_value != expected_url:
        errors.append(f"{label}.url does not match source_spec")
    if expected_path is not None and url_value is not None:
        errors.append(f"{label}.url must be null for a path source")
    if expected_url is not None and path_value is not None:
        errors.append(f"{label}.path must be null for a URL source")

    expected_bytes = source.get("bytes")
    if not is_non_negative_int(expected_bytes):
        errors.append(f"{label}.bytes must be a non-negative integer")
    expected_sha256 = source.get("sha256")
    if not is_sha256_hex(expected_sha256):
        errors.append(f"{label}.sha256 must be a 64-character lowercase hex string")
    downloaded_at = source.get("downloaded_at")
    if downloaded_at is not None and (
        not isinstance(downloaded_at, str) or not downloaded_at
    ):
        errors.append(f"{label}.downloaded_at must be a non-empty string or null")
    if path_value is not None and is_non_negative_int(expected_bytes) and is_sha256_hex(
        expected_sha256
    ):
        errors.extend(
            validate_recorded_file_hash(
                run_dir=run_dir,
                label=label,
                path_value=path_value,
                expected_bytes=expected_bytes,
                expected_sha256=expected_sha256,
            )
        )
    if require_stats:
        errors.extend(validate_warc_source_stats(label, source))
    return errors


def validate_warc_query_spec(
    *,
    run_dir: Path,
    artifact: str,
    payload: Any,
) -> list[str]:
    label = f"{artifact}.query_spec"
    if not isinstance(payload, dict):
        return [f"{label} must be a JSON object"]
    errors: list[str] = []
    kind = payload.get("kind")
    if kind not in {"cli", "file"}:
        errors.append(f"{label}.kind must be 'cli' or 'file'")
    path_value = payload.get("path")
    sha256_value = payload.get("sha256")
    if kind == "file":
        if not isinstance(path_value, str) or not path_value.strip():
            errors.append(f"{label}.path must be a non-empty string for kind=file")
        if not is_sha256_hex(sha256_value):
            errors.append(f"{label}.sha256 must be a 64-character lowercase hex string")
        if isinstance(path_value, str) and path_value.strip() and is_sha256_hex(sha256_value):
            errors.extend(
                validate_recorded_file_hash(
                    run_dir=run_dir,
                    label=label,
                    path_value=path_value,
                    expected_bytes=None,
                    expected_sha256=sha256_value,
                )
            )
    elif kind == "cli":
        if path_value is not None:
            errors.append(f"{label}.path must be null for kind=cli")
        if sha256_value is not None:
            errors.append(f"{label}.sha256 must be null for kind=cli")

    count = payload.get("count")
    if not is_positive_int(count):
        errors.append(f"{label}.count must be a positive integer")
    specs = payload.get("specs")
    if not isinstance(specs, list) or not specs:
        errors.append(f"{label}.specs must be a non-empty list")
        return errors
    if is_positive_int(count) and len(specs) != count:
        errors.append(f"{label}.specs count {len(specs)} does not match count {count}")
    names: set[str] = set()
    for index, spec in enumerate(specs):
        spec_label = f"{label}.specs[{index}]"
        if not isinstance(spec, dict):
            errors.append(f"{spec_label} must be a JSON object")
            continue
        for field in ("name", "query", "label_text"):
            value = spec.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{spec_label}.{field} must be a non-empty string")
        name = spec.get("name")
        if isinstance(name, str) and name:
            if name in names:
                errors.append(f"{spec_label}.name must be unique")
            names.add(name)
    return errors


def validate_warc_fixture_summary(artifact: str, payload: Any) -> list[str]:
    label = f"{artifact}.fixture"
    if not isinstance(payload, dict):
        return [f"{label} must be a JSON object"]
    errors: list[str] = []
    for field in ("name", "output"):
        if not isinstance(payload.get(field), str) or not payload.get(field):
            errors.append(f"{label}.{field} must be a non-empty string")
    for field in ("vector_dim", "doc_count", "query_count"):
        if not is_positive_int(payload.get(field)):
            errors.append(f"{label}.{field} must be a positive integer")
    expected_doc_counts = payload.get("expected_doc_counts")
    if not isinstance(expected_doc_counts, list) or not expected_doc_counts:
        errors.append(f"{label}.expected_doc_counts must be a non-empty list")
    elif any(not is_positive_int(value) for value in expected_doc_counts):
        errors.append(f"{label}.expected_doc_counts must contain positive integers")
    elif is_positive_int(payload.get("query_count")) and len(expected_doc_counts) != payload[
        "query_count"
    ]:
        errors.append(
            f"{label}.expected_doc_counts count {len(expected_doc_counts)} does not match "
            f"query_count {payload['query_count']}"
        )
    return errors


def validate_warc_provenance_artifact(
    artifact: str,
    payload: dict[str, Any],
    run_dir: Path,
) -> list[str]:
    errors: list[str] = []
    schema_version = payload.get("schema_version")
    if schema_version not in {1, 2}:
        errors.append(f"{artifact}.schema_version must be 1 or 2")
        return errors
    if not isinstance(payload.get("generated_at"), str) or not payload.get("generated_at"):
        errors.append(f"{artifact}.generated_at must be a non-empty string")
    errors.extend(validate_warc_query_spec(run_dir=run_dir, artifact=artifact, payload=payload.get("query_spec")))
    errors.extend(validate_warc_fixture_summary(artifact, payload.get("fixture")))

    if schema_version == 1:
        errors.extend(
            validate_warc_source_entry(
                run_dir=run_dir,
                label=f"{artifact}.source",
                source=payload.get("source"),
            )
        )
        return errors

    source_spec = payload.get("source_spec")
    source_specs: list[tuple[str | None, str | None]] = []
    if not isinstance(source_spec, dict):
        errors.append(f"{artifact}.source_spec must be a JSON object")
    else:
        if source_spec.get("kind") != "file":
            errors.append(f"{artifact}.source_spec.kind must be 'file'")
        source_spec_path = source_spec.get("path")
        if not isinstance(source_spec_path, str) or not source_spec_path.strip():
            errors.append(f"{artifact}.source_spec.path must be a non-empty string")
        if not is_sha256_hex(source_spec.get("sha256")):
            errors.append(
                f"{artifact}.source_spec.sha256 must be a 64-character lowercase hex string"
            )
        elif isinstance(source_spec_path, str) and source_spec_path.strip():
            errors.extend(
                validate_recorded_file_hash(
                    run_dir=run_dir,
                    label=f"{artifact}.source_spec",
                    path_value=source_spec_path,
                    expected_bytes=None,
                    expected_sha256=source_spec["sha256"],
                )
            )
        source_specs_payload = source_spec.get("specs")
        if not isinstance(source_specs_payload, list) or not source_specs_payload:
            errors.append(f"{artifact}.source_spec.specs must be a non-empty list")
        else:
            for index, spec in enumerate(source_specs_payload):
                path_value, url_value, identity_errors = parse_warc_identity(
                    f"{artifact}.source_spec.specs[{index}]",
                    spec if isinstance(spec, dict) else {},
                )
                errors.extend(identity_errors)
                source_specs.append((path_value, url_value))
        source_count = source_spec.get("count")
        if not is_positive_int(source_count):
            errors.append(f"{artifact}.source_spec.count must be a positive integer")
        elif source_specs and len(source_specs) != source_count:
            errors.append(
                f"{artifact}.source_spec.specs count {len(source_specs)} does not match "
                f"count {source_count}"
            )

    sources = payload.get("sources")
    if not isinstance(sources, list) or not sources:
        errors.append(f"{artifact}.sources must be a non-empty list")
        return errors
    if source_specs and len(sources) != len(source_specs):
        errors.append(
            f"{artifact}.sources count {len(sources)} does not match source_spec.specs "
            f"count {len(source_specs)}"
        )
    for index, source in enumerate(sources):
        expected_path: str | None = None
        expected_url: str | None = None
        if index < len(source_specs):
            expected_path, expected_url = source_specs[index]
        errors.extend(
            validate_warc_source_entry(
                run_dir=run_dir,
                label=f"{artifact}.sources[{index}]",
                source=source,
                expected_path=expected_path,
                expected_url=expected_url,
                require_stats=True,
            )
        )
    return errors


def check_required_warc_provenance_artifacts(
    run_dir: Path,
    requirements: list[str],
) -> list[str]:
    errors: list[str] = []
    for required in requirements:
        if required not in WARC_PROVENANCE_ARTIFACT_NAMES:
            continue
        path = run_dir / required
        if not path.is_file():
            continue
        payload, load_errors = load_json(path)
        errors.extend(load_errors)
        if payload is None:
            continue
        errors.extend(validate_warc_provenance_artifact(required, payload, run_dir))
    return errors


def validate_release_artifact_file_report(label: str, payload: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return [f"{label} must be a JSON object"]
    exists = payload.get("exists")
    if not isinstance(exists, bool):
        errors.append(f"{label}.exists must be a boolean")
    if exists is True:
        if not isinstance(payload.get("path"), str) or not payload.get("path"):
            errors.append(f"{label}.path must be a non-empty string when exists=true")
        if not is_non_negative_int(payload.get("bytes")):
            errors.append(f"{label}.bytes must be a non-negative integer when exists=true")
        if not is_sha256_hex(payload.get("sha256")):
            errors.append(f"{label}.sha256 must be a 64-character lowercase hex string when exists=true")
    return errors


def validate_release_artifacts_matrix_artifact(artifact: str, payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("extension") != "pgwarc_lance":
        errors.append(f"{artifact}.extension must be 'pgwarc_lance'")
    if not isinstance(payload.get("version"), str) or not payload.get("version"):
        errors.append(f"{artifact}.version must be a non-empty string")
    if not isinstance(payload.get("dist_root"), str) or not payload.get("dist_root"):
        errors.append(f"{artifact}.dist_root must be a non-empty string")
    if not isinstance(payload.get("all_valid"), bool):
        errors.append(f"{artifact}.all_valid must be a boolean")

    pg_majors = payload.get("pg_majors")
    if not isinstance(pg_majors, list) or not pg_majors:
        errors.append(f"{artifact}.pg_majors must be a non-empty list")
        pg_major_values: list[int] = []
    elif any(not is_positive_int(pg_major) for pg_major in pg_majors):
        errors.append(f"{artifact}.pg_majors must contain positive integers")
        pg_major_values = []
    else:
        pg_major_values = list(pg_majors)

    artifact_rows = payload.get("artifacts")
    if not isinstance(artifact_rows, list) or not artifact_rows:
        errors.append(f"{artifact}.artifacts must be a non-empty list")
        return errors
    if pg_major_values and len(artifact_rows) != len(pg_major_values):
        errors.append(
            f"{artifact}.artifacts count {len(artifact_rows)} does not match pg_majors count {len(pg_major_values)}"
        )

    artifact_pg_majors: list[int] = []
    valid_flags: list[bool] = []
    for index, artifact_row in enumerate(artifact_rows):
        label = f"{artifact}.artifacts[{index}]"
        if not isinstance(artifact_row, dict):
            errors.append(f"{label} must be a JSON object")
            continue
        pg_major = artifact_row.get("pg_major")
        if is_positive_int(pg_major):
            artifact_pg_majors.append(pg_major)
        else:
            errors.append(f"{label}.pg_major must be a positive integer")
        if not isinstance(artifact_row.get("dist_dir"), str) or not artifact_row.get("dist_dir"):
            errors.append(f"{label}.dist_dir must be a non-empty string")
        valid = artifact_row.get("valid")
        if isinstance(valid, bool):
            valid_flags.append(valid)
        else:
            errors.append(f"{label}.valid must be a boolean")
        entry_error_count, entry_errors = check_string_list(artifact_row.get("errors"), f"{label}.errors")
        errors.extend(entry_errors)
        if valid is True and entry_error_count:
            errors.append(f"{label}.valid=true must not include errors")
        if valid is False and not entry_error_count:
            errors.append(f"{label}.valid=false must include at least one error")

        files = artifact_row.get("files")
        if not isinstance(files, dict):
            errors.append(f"{label}.files must be a JSON object")
            continue
        for file_label in sorted(RELEASE_ARTIFACT_FILE_LABELS):
            if file_label not in files:
                errors.append(f"{label}.files missing required file report: {file_label}")
                continue
            errors.extend(
                validate_release_artifact_file_report(
                    f"{label}.files.{file_label}",
                    files[file_label],
                )
            )

    if pg_major_values and artifact_pg_majors and artifact_pg_majors != pg_major_values:
        errors.append(
            f"{artifact}.artifacts pg_major list {artifact_pg_majors} does not match pg_majors {pg_major_values}"
        )
    all_valid = payload.get("all_valid")
    if isinstance(all_valid, bool) and len(valid_flags) == len(artifact_rows):
        artifacts_all_valid = all(valid_flags)
        if all_valid != artifacts_all_valid:
            expected = str(artifacts_all_valid).lower()
            errors.append(f"{artifact}.all_valid must be {expected} based on artifacts")
    return errors


def check_required_release_artifacts_matrix_artifacts(
    run_dir: Path,
    requirements: list[str],
) -> list[str]:
    errors: list[str] = []
    for required in requirements:
        if required not in RELEASE_ARTIFACT_MATRIX_ARTIFACT_NAMES:
            continue
        path = run_dir / required
        if not path.is_file():
            continue
        payload, load_errors = load_json(path)
        errors.extend(load_errors)
        if payload is None:
            continue
        errors.extend(validate_release_artifacts_matrix_artifact(required, payload))
    return errors


def is_int_or_none(value: Any) -> bool:
    return value is None or (isinstance(value, int) and not isinstance(value, bool))


def validate_release_smoke_matrix_artifact(artifact: str, payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("schema_version") != 1:
        errors.append(f"{artifact}.schema_version must be 1")
    for field in ("generated_at", "cwd", "make_program", "make_target"):
        if not isinstance(payload.get(field), str) or not payload.get(field):
            errors.append(f"{artifact}.{field} must be a non-empty string")
    _, make_vars_errors = check_string_list(payload.get("make_vars"), f"{artifact}.make_vars")
    errors.extend(make_vars_errors)
    if not is_non_negative_int(payload.get("output_tail_lines")):
        errors.append(f"{artifact}.output_tail_lines must be a non-negative integer")
    if not isinstance(payload.get("all_passed"), bool):
        errors.append(f"{artifact}.all_passed must be a boolean")

    pg_majors = payload.get("pg_majors")
    if not isinstance(pg_majors, list) or not pg_majors:
        errors.append(f"{artifact}.pg_majors must be a non-empty list")
        pg_major_values: list[int] = []
    elif any(not is_positive_int(pg_major) for pg_major in pg_majors):
        errors.append(f"{artifact}.pg_majors must contain positive integers")
        pg_major_values = []
    else:
        pg_major_values = list(pg_majors)

    results = payload.get("results")
    if not isinstance(results, list) or not results:
        errors.append(f"{artifact}.results must be a non-empty list")
        return errors
    if pg_major_values and len(results) != len(pg_major_values):
        errors.append(
            f"{artifact}.results count {len(results)} does not match pg_majors count {len(pg_major_values)}"
        )

    result_pg_majors: list[int] = []
    passed_flags: list[bool] = []
    for index, result in enumerate(results):
        label = f"{artifact}.results[{index}]"
        if not isinstance(result, dict):
            errors.append(f"{label} must be a JSON object")
            continue
        pg_major = result.get("pg_major")
        if is_positive_int(pg_major):
            result_pg_majors.append(pg_major)
        else:
            errors.append(f"{label}.pg_major must be a positive integer")
        for field in ("command", "stdout_tail", "stderr_tail"):
            if not isinstance(result.get(field), str):
                errors.append(f"{label}.{field} must be a string")
        if not is_int_or_none(result.get("exit_code")):
            errors.append(f"{label}.exit_code must be an integer or null")
        if not is_non_negative_number(result.get("duration_seconds")):
            errors.append(f"{label}.duration_seconds must be a non-negative number")
        error = result.get("error")
        if error is not None and not isinstance(error, str):
            errors.append(f"{label}.error must be a string or null")
        passed = result.get("passed")
        if isinstance(passed, bool):
            passed_flags.append(passed)
        else:
            errors.append(f"{label}.passed must be a boolean")

        exit_code = result.get("exit_code")
        if passed is True:
            if exit_code != 0:
                errors.append(f"{label}.passed=true requires exit_code=0")
            if error is not None:
                errors.append(f"{label}.passed=true requires error=null")
        if passed is False and exit_code == 0 and error is None:
            errors.append(f"{label}.passed=false must include non-zero/null exit_code or error")

    if pg_major_values and result_pg_majors and result_pg_majors != pg_major_values:
        errors.append(
            f"{artifact}.results pg_major list {result_pg_majors} does not match pg_majors {pg_major_values}"
        )
    all_passed = payload.get("all_passed")
    if isinstance(all_passed, bool) and len(passed_flags) == len(results):
        results_all_passed = all(passed_flags)
        if all_passed != results_all_passed:
            expected = str(results_all_passed).lower()
            errors.append(f"{artifact}.all_passed must be {expected} based on results")
    return errors


def check_required_release_smoke_matrix_artifacts(
    run_dir: Path,
    requirements: list[str],
) -> list[str]:
    errors: list[str] = []
    for required in requirements:
        if required not in RELEASE_SMOKE_MATRIX_ARTIFACT_NAMES:
            continue
        path = run_dir / required
        if not path.is_file():
            continue
        payload, load_errors = load_json(path)
        errors.extend(load_errors)
        if payload is None:
            continue
        errors.extend(validate_release_smoke_matrix_artifact(required, payload))
    return errors


def manifest_relative_path(run_dir: Path, relative: str) -> tuple[Path | None, str | None]:
    if Path(relative).is_absolute():
        return None, f"run_manifest.json entry has absolute path: {relative}"
    path = (run_dir / relative).resolve()
    try:
        path.relative_to(run_dir.resolve())
    except ValueError:
        return None, f"run_manifest.json entry escapes run directory: {relative}"
    return path, None


def check_manifest_integrity(run_dir: Path, manifest: dict[str, Any] | None) -> list[str]:
    errors: list[str] = []
    for relative, entry in manifest_file_entries(manifest).items():
        path, path_error = manifest_relative_path(run_dir, relative)
        if path_error is not None:
            errors.append(path_error)
            continue
        assert path is not None
        if not path.is_file():
            errors.append(f"run_manifest.json entry points to missing file: {relative}")
            continue

        expected_bytes = entry.get("bytes")
        if expected_bytes is not None:
            if not is_non_negative_int(expected_bytes):
                errors.append(f"run_manifest.json bytes for {relative} must be a non-negative integer")
            else:
                actual_bytes = path.stat().st_size
                if actual_bytes != expected_bytes:
                    errors.append(
                        f"run_manifest.json bytes mismatch for {relative}: "
                        f"expected {expected_bytes}, got {actual_bytes}"
                    )

        expected_sha256 = entry.get("sha256")
        if expected_sha256 is not None:
            if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
                errors.append(f"run_manifest.json sha256 for {relative} must be a 64-character string")
            elif sha256_file(path) != expected_sha256:
                errors.append(f"run_manifest.json sha256 mismatch for {relative}")
    return errors


def build_report(
    *,
    run_dir: Path,
    profile: str,
    extra_required: list[str],
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if profile not in PROFILE_REQUIREMENTS:
        errors.append(
            "profile must be one of: " + ", ".join(sorted(PROFILE_REQUIREMENTS))
        )
        return {
            "run_dir": str(run_dir),
            "profile": profile,
            "valid": False,
            "requirements": [],
            "errors": errors,
            "warnings": warnings,
            "error_count": len(errors),
            "warning_count": len(warnings),
        }

    requirements = profile_requirements(profile, extra_required)
    if not run_dir.exists():
        errors.append(f"run directory does not exist: {run_dir}")
    elif not run_dir.is_dir():
        errors.append(f"run path is not a directory: {run_dir}")

    manifest = None
    metadata = None
    if not errors:
        metadata, metadata_errors = load_json(run_dir / "run_metadata.json")
        errors.extend(metadata_errors)
        manifest, manifest_errors = load_json(run_dir / "run_manifest.json")
        errors.extend(manifest_errors)

    indexed_paths = manifest_paths(manifest)
    if manifest is not None and not indexed_paths:
        warnings.append("run manifest contains no indexed files")

    if not errors:
        for required in requirements:
            path = run_dir / required
            if not path.is_file():
                errors.append(f"missing required artifact: {required}")
            elif required not in indexed_paths and required != "run_manifest.json":
                warnings.append(f"artifact exists but is absent from run_manifest.json: {required}")
        if metadata is not None:
            errors.extend(validate_run_metadata_artifact("run_metadata.json", metadata))
        errors.extend(check_manifest_integrity(run_dir, manifest))
        errors.extend(check_required_benchmark_artifacts(run_dir, profile, requirements))
        errors.extend(check_required_benchmark_comparison_artifacts(run_dir, requirements))
        errors.extend(check_required_quality_fixture_report_artifacts(run_dir, requirements))
        errors.extend(check_required_quality_fixture_artifacts(run_dir, requirements))
        errors.extend(check_required_quality_eval_artifacts(run_dir, requirements))
        errors.extend(check_required_quality_baseline_doctor_artifacts(run_dir, requirements))
        errors.extend(check_required_warc_provenance_artifacts(run_dir, requirements))
        errors.extend(check_required_import_summary_artifacts(run_dir, requirements))
        errors.extend(check_required_import_doctor_artifacts(run_dir, requirements))
        errors.extend(check_required_release_artifacts_matrix_artifacts(run_dir, requirements))
        errors.extend(check_required_release_smoke_matrix_artifacts(run_dir, requirements))
        errors.extend(check_status_artifacts(run_dir, profile, requirements))

    if metadata is not None:
        git = metadata.get("git")
        if isinstance(git, dict) and git.get("dirty") is True:
            warnings.append("run metadata was captured from a dirty git worktree")
        command = metadata.get("command")
        if not command:
            warnings.append("run metadata does not record a command")

    return {
        "run_dir": str(run_dir),
        "profile": profile,
        "valid": not errors,
        "requirements": requirements,
        "errors": errors,
        "warnings": warnings,
        "error_count": len(errors),
        "warning_count": len(warnings),
    }


def markdown_cell(value: object) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# pgwarc_lance Run Directory Doctor",
        "",
        f"- Run dir: `{report['run_dir']}`",
        f"- Profile: `{report['profile']}`",
        f"- Valid: `{str(report['valid']).lower()}`",
        f"- Errors: `{report['error_count']}`",
        f"- Warnings: `{report['warning_count']}`",
        "",
        "## Requirements",
        "",
    ]
    for required in report["requirements"]:
        lines.append(f"- `{markdown_cell(required)}`")

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
    parser.add_argument(
        "--profile",
        choices=sorted(PROFILE_REQUIREMENTS),
        default="generic",
    )
    parser.add_argument(
        "--require",
        action="append",
        default=[],
        help="additional required artifact path relative to run-dir",
    )
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_report(
        run_dir=args.run_dir,
        profile=args.profile,
        extra_required=args.require,
    )
    if args.json_output:
        write_json(args.json_output, report)
    if args.markdown_output:
        write_text(args.markdown_output, render_markdown(report))

    print(
        "run directory doctor: "
        f"profile={report['profile']} errors={report['error_count']} "
        f"warnings={report['warning_count']}"
    )
    if report["errors"]:
        for error in report["errors"]:
            print(f"run directory doctor failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
