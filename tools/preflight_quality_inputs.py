#!/usr/bin/env python3
"""Read-only preflight for explicit WARC/query/qrels quality inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import doctor_quality_labels
import doctor_real_corpus_input

SCHEMA_VERSION = 1
MAX_DOC_ID = (1 << 63) - 1
REAL_CORPUS_MIN_QUERIES = 20
REAL_CORPUS_MAX_QUERIES = 50
RESUME_REQUIREMENTS = [
    "Provide provenance identifying the corpus source and independent label origin.",
    "For a real baseline, provide a valid real-corpus manifest and pass it through the manifest doctor.",
    "Make an authorized real WARC corpus and independently authored query/qrels labels available.",
    "Run doctor-real-corpus-input before building a fixture or measuring a baseline.",
]


def positive_doc_id(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and 0 < value <= MAX_DOC_ID:
        return value
    return None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_file(path: Path, role: str) -> tuple[dict[str, Any], list[str]]:
    report = {
        "role": role, "path": str(path), "exists": path.exists(),
        "is_file": path.is_file(), "readable": False, "bytes": None, "sha256": None,
    }
    if not path.exists():
        return report, [f"{role} path does not exist: {path}"]
    if not path.is_file():
        return report, [f"{role} path is not a regular file: {path}"]
    try:
        report["bytes"] = path.stat().st_size
        report["sha256"] = sha256_file(path)
        report["readable"] = True
    except OSError as exc:
        return report, [f"{role} path is not readable: {path}: {exc}"]
    return report, []


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: invalid JSON: {exc.msg}") from exc
    except OSError as exc:
        raise ValueError(f"{path}: unable to read JSON: {exc}") from exc


def run_real_corpus_manifest_doctor(path: Path) -> dict[str, Any]:
    try:
        return doctor_real_corpus_input.build_report(
            manifest_json=path,
            min_queries=REAL_CORPUS_MIN_QUERIES,
            max_queries=REAL_CORPUS_MAX_QUERIES,
        )
    except Exception as exc:
        return doctor_real_corpus_input.error_report(
            manifest_json=path,
            error=exc,
            min_queries=REAL_CORPUS_MIN_QUERIES,
            max_queries=REAL_CORPUS_MAX_QUERIES,
        )


def parse_qrels(path: Path) -> tuple[list[str], int]:
    raw = load_json(path)
    rows = raw if isinstance(raw, list) else raw.get("queries") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        raise ValueError(f"{path}: qrels must be a query list or object with a queries list")
    if not rows:
        raise ValueError(f"{path}: qrels list must not be empty")
    names: list[str] = []
    errors: list[str] = []
    for index, row in enumerate(rows, start=1):
        label = f"{path}:queries[{index}]"
        if not isinstance(row, dict):
            errors.append(f"{label} must be an object")
            continue
        name = row.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{label}.name must be a non-empty string")
        else:
            names.append(name)
        expected = row.get("expected_doc_ids")
        if not isinstance(expected, list) or not expected:
            errors.append(f"{label}.expected_doc_ids must be a non-empty list")
            continue
        seen: set[int] = set()
        for position, raw_doc_id in enumerate(expected, start=1):
            doc_id = positive_doc_id(raw_doc_id)
            if doc_id is None:
                errors.append(f"{label}.expected_doc_ids[{position}] must be a positive integer")
            elif doc_id in seen:
                errors.append(f"{label}.expected_doc_ids has duplicate value {doc_id}")
            else:
                seen.add(doc_id)
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        errors.append(f"{path}: duplicate qrels query names: {', '.join(duplicates[:10])}")
    if errors:
        raise ValueError("; ".join(errors))
    return names, len(rows)


def add_check(
    checks: list[dict[str, Any]], errors: list[str], warnings: list[str],
    name: str, ok: bool, detail: str, *, blocking: bool = True,
) -> None:
    checks.append({"name": name, "ok": bool(ok), "detail": detail, "blocking": blocking})
    if not ok:
        (errors if blocking else warnings).append(f"{name}: {detail}")


def build_report(
    *,
    corpus_paths: list[Path],
    queries_json: Path,
    qrels_json: Path,
    records_jsonl: Path | None = None,
    provenance_json: Path | None = None,
    real_corpus_manifest_json: Path | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    checks: list[dict[str, Any]] = []
    corpus = []
    for index, path in enumerate(corpus_paths, start=1):
        item, item_errors = inspect_file(path, f"corpus[{index}]")
        corpus.append(item)
        add_check(
            checks, errors, warnings, f"corpus[{index}]_readable", not item_errors,
            "; ".join(item_errors) if item_errors else f"readable file with {item['bytes']} bytes",
        )

    queries, query_path_errors = inspect_file(queries_json, "queries")
    labels: dict[str, Any] = {"queries": [], "query_count": 0, "errors": []}
    if query_path_errors:
        add_check(checks, errors, warnings, "queries_readable", False, "; ".join(query_path_errors))
    else:
        try:
            labels = doctor_quality_labels.build_report(
                queries_json=queries_json, records_jsonl=records_jsonl,
                min_queries=1, min_expected_labels=1,
            )
            label_errors = list(labels["errors"])
            add_check(
                checks, errors, warnings, "queries_supported_label_shape", not label_errors,
                "; ".join(label_errors) if label_errors else f"parsed {labels['query_count']} query labels",
            )
        except Exception as exc:
            labels["errors"] = [str(exc)]
            add_check(checks, errors, warnings, "queries_supported_label_shape", False, str(exc))

    records = None
    if records_jsonl is None:
        add_check(
            checks, errors, warnings, "query_record_reference_check", False,
            "records JSONL was not supplied; expected_doc_ids were not checked against corpus records",
            blocking=False,
        )
    else:
        records, record_errors = inspect_file(records_jsonl, "records_jsonl")
        if record_errors:
            add_check(checks, errors, warnings, "records_jsonl_readable", False, "; ".join(record_errors))
        else:
            label_errors = list(labels.get("errors", []))
            add_check(
                checks, errors, warnings, "query_record_reference_check", not label_errors,
                "; ".join(label_errors) if label_errors
                else "query expected_doc_ids were checked against records JSONL",
            )

    qrels, qrels_path_errors = inspect_file(qrels_json, "qrels")
    qrels_names: list[str] = []
    qrels_count = 0
    if qrels_path_errors:
        add_check(checks, errors, warnings, "qrels_readable", False, "; ".join(qrels_path_errors))
    else:
        try:
            qrels_names, qrels_count = parse_qrels(qrels_json)
            add_check(
                checks, errors, warnings, "qrels_supported_label_shape", True,
                f"parsed {qrels_count} qrels rows using the existing query-label shape",
            )
        except Exception as exc:
            add_check(checks, errors, warnings, "qrels_supported_label_shape", False, str(exc))

    query_names = [
        item.get("name") for item in labels.get("queries", [])
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    ]
    if qrels_names and query_names:
        missing_qrels = sorted(set(query_names) - set(qrels_names))
        missing_queries = sorted(set(qrels_names) - set(query_names))
        detail = (
            f"query/qrels name mismatch; missing_from_qrels={missing_qrels[:10]} "
            f"missing_from_queries={missing_queries[:10]}"
        )
        add_check(
            checks, errors, warnings, "query_qrels_reference_check",
            not missing_qrels and not missing_queries,
            "query and qrels names match" if not missing_qrels and not missing_queries else detail,
        )
    else:
        add_check(
            checks, errors, warnings, "query_qrels_reference_check", False,
            "reference comparison was not available because one input did not parse",
            blocking=False,
        )

    provenance = None
    if provenance_json is None and real_corpus_manifest_json is not None:
        add_check(
            checks, errors, warnings, "provenance_supplied", True,
            "real-corpus manifest supplied; source and label provenance are delegated to its doctor",
        )
    elif provenance_json is None:
        add_check(checks, errors, warnings, "provenance_supplied", False,
                  "no provenance/label-origin manifest was supplied")
        add_check(checks, errors, warnings, "provenance_label_origin_reference", False,
                  "label origin is unavailable because the provenance manifest is missing",
                  blocking=False)
    else:
        provenance, provenance_errors = inspect_file(provenance_json, "provenance")
        if provenance_errors:
            add_check(checks, errors, warnings, "provenance_readable", False, "; ".join(provenance_errors))
        else:
            try:
                raw = load_json(provenance_json)
                if not isinstance(raw, dict):
                    raise ValueError("provenance manifest must be a JSON object")
                provenance["sections"] = [
                    key for key in (
                        "corpus", "source", "sources", "source_spec",
                        "labels", "query_spec", "fixture", "evaluation",
                    ) if key in raw
                ]
                add_check(checks, errors, warnings, "provenance_readable", True,
                          f"readable JSON manifest with sections={provenance['sections']}")
                source_ok = any(key in raw for key in ("corpus", "source", "sources", "source_spec"))
                label_ok = any(key in raw for key in ("labels", "query_spec", "fixture"))
                add_check(checks, errors, warnings, "provenance_source_reference", source_ok,
                          "corpus/source provenance section is available"
                          if source_ok else "corpus/source provenance section is missing",
                          blocking=False)
                add_check(checks, errors, warnings, "provenance_label_origin_reference", label_ok,
                          "query/label-origin section is available"
                          if label_ok else "query/label-origin section is missing",
                          blocking=False)
            except Exception as exc:
                add_check(checks, errors, warnings, "provenance_readable", False, str(exc))

    real_corpus_manifest = None
    real_corpus_manifest_doctor = None
    if real_corpus_manifest_json is None:
        add_check(
            checks, errors, warnings, "real_corpus_manifest_check", False,
            "no real-corpus manifest was supplied; the existing manifest doctor was not run",
            blocking=False,
        )
    else:
        real_corpus_manifest, manifest_path_errors = inspect_file(
            real_corpus_manifest_json, "real_corpus_manifest"
        )
        if manifest_path_errors:
            add_check(
                checks, errors, warnings, "real_corpus_manifest_check", False,
                "; ".join(manifest_path_errors),
            )
        else:
            real_corpus_manifest_doctor = run_real_corpus_manifest_doctor(
                real_corpus_manifest_json
            )
            doctor_errors = list(real_corpus_manifest_doctor.get("errors", []))
            doctor_warnings = list(real_corpus_manifest_doctor.get("warnings", []))
            warnings.extend(
                f"real_corpus_manifest: {warning}" for warning in doctor_warnings
            )
            add_check(
                checks, errors, warnings, "real_corpus_manifest_check", not doctor_errors,
                "existing real-corpus manifest doctor passed"
                if not doctor_errors else "; ".join(doctor_errors),
            )

    add_check(
        checks, errors, warnings, "qrels_independence_and_authenticity", False,
        "not established by this read-only preflight; use the real-corpus doctor",
        blocking=False,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "ready" if not errors else "blocked",
        "valid": not errors,
        "evidence_scope": "preflight_only",
        "quality_measured": False,
        "real_evidence_established": False,
        "inputs": {
            "corpus": corpus, "queries": queries, "qrels": qrels,
            "records_jsonl": records, "provenance": provenance,
            "real_corpus_manifest": real_corpus_manifest,
        },
        "real_corpus_manifest_doctor": real_corpus_manifest_doctor,
        "query_count": int(labels.get("query_count", 0)),
        "qrels_count": qrels_count,
        "checks": checks,
        "completed_checks": [item["name"] for item in checks if item["ok"]],
        "unresolved_checks": [
            item["name"] for item in checks if not item["ok"] and not item["blocking"]
        ],
        "errors": errors, "warnings": warnings,
        "error_count": len(errors), "warning_count": len(warnings),
        "resume_requirements": RESUME_REQUIREMENTS,
    }


def error_report(error: Exception) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "blocked", "valid": False, "evidence_scope": "preflight_only",
        "quality_measured": False, "real_evidence_established": False, "inputs": {},
        "query_count": 0, "qrels_count": 0, "checks": [], "completed_checks": [],
        "unresolved_checks": [], "errors": [str(error)], "warnings": [],
        "real_corpus_manifest_doctor": None,
        "error_count": 1, "warning_count": 0, "resume_requirements": RESUME_REQUIREMENTS,
    }


def markdown_cell(value: object) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Quality Input Preflight", "",
        f"- Status: {report.get('status')}",
        f"- Valid: {str(report.get('valid')).lower()}",
        f"- Evidence scope: {report.get('evidence_scope')}",
        "- Quality measured: false", "- Real evidence established: false", "",
        "## Inspected files", "",
        "| Role | Path | Exists | Readable | Bytes | SHA-256 |",
        "|------|------|--------|----------|------:|---------|",
    ]
    inputs = report.get("inputs", {})
    items: list[dict[str, Any]] = []
    if isinstance(inputs, dict):
        items.extend(item for item in inputs.get("corpus", []) if isinstance(item, dict))
        for key in ("queries", "qrels", "records_jsonl", "provenance", "real_corpus_manifest"):
            if isinstance(inputs.get(key), dict):
                items.append(inputs[key])
    for item in items:
        lines.append(
            "| {role} | {path} | {exists} | {readable} | {bytes} | {sha} |".format(
                role=markdown_cell(item.get("role", "")),
                path=markdown_cell(item.get("path", "")),
                exists=str(bool(item.get("exists"))).lower(),
                readable=str(bool(item.get("readable"))).lower(),
                bytes=item.get("bytes") if item.get("bytes") is not None else "",
                sha=markdown_cell(item.get("sha256") or ""),
            )
        )
    manifest_doctor = report.get("real_corpus_manifest_doctor")
    if isinstance(manifest_doctor, dict):
        lines += ["", "## Real corpus manifest doctor", ""]
        lines += [
            f"- Valid: {str(bool(manifest_doctor.get('valid'))).lower()}",
            f"- Queries: {manifest_doctor.get('query_count', 0)}",
            f"- Sources: {manifest_doctor.get('source_count', 0)}",
        ]
        if manifest_doctor.get("errors"):
            lines += [f"- Doctor error: {markdown_cell(value)}" for value in manifest_doctor["errors"]]
        if manifest_doctor.get("warnings"):
            lines += [f"- Doctor warning: {markdown_cell(value)}" for value in manifest_doctor["warnings"]]
    lines += ["", "## Completed checks"]
    lines += [f"- {markdown_cell(name)}" for name in report.get("completed_checks", [])]
    lines += ["", "## Unresolved checks"]
    lines += [f"- {markdown_cell(name)}" for name in report.get("unresolved_checks", [])]
    for title, key in (("Errors", "errors"), ("Warnings", "warnings")):
        if report.get(key):
            lines += ["", f"## {title}"]
            lines += [f"- {markdown_cell(value)}" for value in report[key]]
    lines += ["", "## Resume requirements"]
    lines += [f"- {markdown_cell(value)}" for value in report.get("resume_requirements", [])]
    return "\n".join(lines).rstrip() + "\n"


def write_json(path: Path | None, report: dict[str, Any]) -> None:
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_markdown(path: Path | None, report: dict[str, Any]) -> None:
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_markdown(report), encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-path", action="append", required=True)
    parser.add_argument("--queries-json", type=Path, required=True)
    parser.add_argument("--qrels-json", type=Path, required=True)
    parser.add_argument("--records-jsonl", type=Path)
    parser.add_argument("--provenance-json", type=Path)
    parser.add_argument("--manifest-json", type=Path)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        report = build_report(
            corpus_paths=[Path(path) for path in args.corpus_path],
            queries_json=args.queries_json, qrels_json=args.qrels_json,
            records_jsonl=args.records_jsonl, provenance_json=args.provenance_json,
            real_corpus_manifest_json=args.manifest_json,
        )
    except Exception as exc:
        report = error_report(exc)
    write_json(args.json_output, report)
    write_markdown(args.markdown_output, report)
    print(
        "quality input preflight: "
        f"status={report['status']} errors={report['error_count']} "
        f"warnings={report['warning_count']}"
    )
    if report["errors"]:
        for error in report["errors"]:
            print(f"quality input preflight blocked: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
