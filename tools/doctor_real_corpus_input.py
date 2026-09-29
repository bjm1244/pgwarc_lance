#!/usr/bin/env python3
"""Validate a real-corpus evaluation input manifest before any real baseline run.

This doctor exists because a real labeled WARC baseline cannot be faked with a
synthetic or public smoke fixture. It requires an explicit manifest that records
corpus source identity, usage rights, checksums, query/qrels versioning,
independence of the relevance labels, evaluation commit, PostgreSQL version and
search settings, and it re-verifies local file checksums against the recorded
bytes/SHA-256. A synthetic or self-labeled manifest is rejected outright.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

MAX_DOC_ID = (1 << 63) - 1
HEX64 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{7,40}$")

TOP_KEYS = {"schema_version", "corpus", "labels", "evaluation"}
CORPUS_KEYS = {"name", "origin", "sources"}
SOURCE_KEYS = {
    "name",
    "kind",
    "path",
    "url",
    "bytes",
    "sha256",
    "license",
    "license_url",
    "redistribution",
}
LABELS_KEYS = {
    "query_set_version",
    "qrels_version",
    "independence",
    "method",
    "labels_path",
    "labels_sha256",
    "query_count",
}
EVALUATION_KEYS = {"commit", "postgres_version", "search_settings"}

REAL_ORIGINS = {"external"}
REAL_INDEPENDENCE = {"external"}
REDISTRIBUTION = {"permitted", "restricted"}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def non_empty_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and 0 < value <= MAX_DOC_ID:
        return value
    return None


def sha256_bytes(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def unknown_keys(value: dict[str, Any], allowed: set[str], label: str, errors: list[str]) -> None:
    extra = sorted(set(value) - allowed)
    if extra:
        errors.append(f"{label} has unknown keys: {', '.join(extra)}")


def resolve(base_dir: Path, raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else base_dir / path


def validate_source(
    *,
    source: Any,
    index: int,
    base_dir: Path,
    errors: list[str],
    warnings: list[str],
) -> dict[str, Any] | None:
    label = f"corpus.sources[{index}]"
    if not isinstance(source, dict):
        errors.append(f"{label} must be an object")
        return None
    unknown_keys(source, SOURCE_KEYS, label, errors)

    name = non_empty_str(source.get("name"))
    if name is None:
        errors.append(f"{label}.name must be a non-empty string")
        name = ""

    kind = source.get("kind")
    if kind not in {"path", "url"}:
        errors.append(f"{label}.kind must be 'path' or 'url'")
        kind = ""

    raw_path = non_empty_str(source.get("path"))
    raw_url = non_empty_str(source.get("url"))
    if kind == "path":
        if raw_path is None:
            errors.append(f"{label}.path is required for kind 'path'")
        if raw_url is not None:
            errors.append(f"{label}.url must be empty for kind 'path'")
    elif kind == "url":
        if raw_url is None:
            errors.append(f"{label}.url is required for kind 'url'")
        if raw_path is not None:
            errors.append(f"{label}.path must be empty for kind 'url'")

    raw_bytes = source.get("bytes")
    if isinstance(raw_bytes, bool) or not isinstance(raw_bytes, int) or raw_bytes <= 0:
        errors.append(f"{label}.bytes must be a positive integer")
        recorded_bytes = 0
    else:
        recorded_bytes = raw_bytes

    raw_sha = source.get("sha256")
    if not isinstance(raw_sha, str) or not HEX64.match(raw_sha):
        errors.append(f"{label}.sha256 must be a 64-character lowercase hex digest")
        recorded_sha = ""
    else:
        recorded_sha = raw_sha

    license_name = non_empty_str(source.get("license"))
    if license_name is None:
        errors.append(f"{label}.license must be a non-empty string")

    license_url = non_empty_str(source.get("license_url"))
    if license_url is None or not license_url.startswith(("http://", "https://")):
        errors.append(f"{label}.license_url must be an http(s) URL")

    redistribution = source.get("redistribution")
    if redistribution not in REDISTRIBUTION:
        errors.append(
            f"{label}.redistribution must be one of {sorted(REDISTRIBUTION)}"
        )

    verified = False
    if raw_path is not None:
        resolved = resolve(base_dir, raw_path)
        if not resolved.is_file():
            errors.append(f"{label}.path does not exist: {raw_path}")
        else:
            actual_bytes = resolved.stat().st_size
            actual_sha = sha256_bytes(resolved)
            verified = True
            if recorded_bytes and actual_bytes != recorded_bytes:
                errors.append(
                    f"{label}.path bytes drift: expected {recorded_bytes}, found {actual_bytes}"
                )
            if recorded_sha and actual_sha != recorded_sha:
                errors.append(f"{label}.path sha256 drift: expected {recorded_sha}, found {actual_sha}")
    elif raw_url is not None:
        warnings.append(
            f"{label} is URL-only; current bytes/SHA-256 were not independently re-verified"
        )

    return {
        "name": name,
        "kind": kind,
        "path": raw_path or "",
        "url": raw_url or "",
        "bytes": recorded_bytes,
        "sha256": recorded_sha,
        "license": license_name or "",
        "license_url": license_url or "",
        "redistribution": redistribution if isinstance(redistribution, str) else "",
        "checksum_verified": verified,
    }


def parse_query_labels(raw: Any, labels_path: Path) -> list[dict[str, Any]] | None:
    if isinstance(raw, list):
        queries = raw
    elif isinstance(raw, dict):
        queries = raw.get("queries")
    else:
        return None
    if not isinstance(queries, list):
        return None
    parsed = []
    for index, query in enumerate(queries, start=1):
        parsed.append({"index": index, "query": query})
    return parsed


def validate_labels(
    *,
    labels: Any,
    base_dir: Path,
    min_queries: int,
    max_queries: int,
    errors: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    label = "labels"
    if not isinstance(labels, dict):
        errors.append(f"{label} must be an object")
        return {
            "query_set_version": "",
            "qrels_version": "",
            "independence": "",
            "method": "",
            "labels_path": "",
            "labels_sha256": "",
            "query_count": 0,
            "labels_checksum_verified": False,
        }
    unknown_keys(labels, LABELS_KEYS, label, errors)

    query_set_version = non_empty_str(labels.get("query_set_version"))
    if query_set_version is None:
        errors.append(f"{label}.query_set_version must be a non-empty string")

    qrels_version = non_empty_str(labels.get("qrels_version"))
    if qrels_version is None:
        errors.append(f"{label}.qrels_version must be a non-empty string")

    independence = labels.get("independence")
    if independence not in REAL_INDEPENDENCE:
        errors.append(
            f"{label}.independence must be 'external' "
            "(self-labeled or pipeline-labeled relevance is not a real baseline)"
        )

    method = non_empty_str(labels.get("method"))
    if method is None:
        errors.append(f"{label}.method must describe how relevance was judged")

    raw_query_count = labels.get("query_count")
    query_count = positive_int(raw_query_count)
    if query_count is None:
        errors.append(f"{label}.query_count must be a positive integer")
        query_count = 0
    elif not (min_queries <= query_count <= max_queries):
        errors.append(
            f"{label}.query_count {query_count} is outside the real baseline range "
            f"{min_queries}-{max_queries}"
        )

    labels_path_raw = non_empty_str(labels.get("labels_path"))
    labels_sha = labels.get("labels_sha256")
    if not isinstance(labels_sha, str) or not HEX64.match(labels_sha):
        errors.append(f"{label}.labels_sha256 must be a 64-character lowercase hex digest")
        labels_sha = ""

    verified = False
    if labels_path_raw is not None and labels_sha:
        resolved = resolve(base_dir, labels_path_raw)
        if not resolved.is_file():
            errors.append(f"{label}.labels_path does not exist: {labels_path_raw}")
        else:
            actual_sha = sha256_bytes(resolved)
            if actual_sha != labels_sha:
                errors.append(
                    f"{label}.labels_path sha256 drift: expected {labels_sha}, found {actual_sha}"
                )
            else:
                verified = True
            try:
                parsed = parse_query_labels(load_json(resolved), resolved)
            except json.JSONDecodeError:
                errors.append(f"{label}.labels_path is not valid JSON: {labels_path_raw}")
                parsed = None
            if parsed is None:
                errors.append(
                    f"{label}.labels_path must be a query list or an object with a 'queries' list"
                )
            else:
                if query_count and len(parsed) != query_count:
                    errors.append(
                        f"{label}.query_count {query_count} does not match "
                        f"{len(parsed)} queries in labels_path"
                    )
                names: list[str] = []
                for entry in parsed:
                    query = entry["query"]
                    where = f"{label}.labels_path queries[{entry['index']}]"
                    if not isinstance(query, dict):
                        errors.append(f"{where} must be an object")
                        continue
                    qname = non_empty_str(query.get("name"))
                    if qname is None:
                        errors.append(f"{where}.name must be a non-empty string")
                    else:
                        names.append(qname)
                    if non_empty_str(query.get("query")) is None:
                        errors.append(f"{where}.query must be a non-empty string")
                    expected = query.get("expected_doc_ids")
                    if not isinstance(expected, list) or not expected:
                        errors.append(f"{where}.expected_doc_ids must be a non-empty list")
                        continue
                    seen: set[int] = set()
                    for doc_index, raw_doc_id in enumerate(expected, start=1):
                        doc_id = positive_int(raw_doc_id)
                        if doc_id is None:
                            errors.append(
                                f"{where}.expected_doc_ids[{doc_index}] must be a positive integer"
                            )
                        elif doc_id in seen:
                            errors.append(
                                f"{where}.expected_doc_ids has duplicate value {doc_id}"
                            )
                        else:
                            seen.add(doc_id)
                duplicates = sorted({name for name in names if names.count(name) > 1})
                if duplicates:
                    errors.append(
                        f"{label}.labels_path has duplicate query names: "
                        + ", ".join(duplicates[:10])
                    )
    elif labels_path_raw is not None and not labels_sha:
        warnings.append(f"{label}.labels_path was not checksummed because labels_sha256 is invalid")
    else:
        errors.append(f"{label}.labels_path must point to the independent query label JSON")

    return {
        "query_set_version": query_set_version or "",
        "qrels_version": qrels_version or "",
        "independence": independence if isinstance(independence, str) else "",
        "method": method or "",
        "labels_path": labels_path_raw or "",
        "labels_sha256": labels_sha,
        "query_count": query_count,
        "labels_checksum_verified": verified,
    }


def validate_evaluation(*, evaluation: Any, errors: list[str]) -> dict[str, Any]:
    label = "evaluation"
    if not isinstance(evaluation, dict):
        errors.append(f"{label} must be an object")
        return {"commit": "", "postgres_version": "", "search_settings": {}}
    unknown_keys(evaluation, EVALUATION_KEYS, label, errors)

    commit = non_empty_str(evaluation.get("commit"))
    if commit is None or not COMMIT.match(commit):
        errors.append(f"{label}.commit must be a 7-40 character lowercase hex git revision")

    postgres_version = non_empty_str(evaluation.get("postgres_version"))
    if postgres_version is None:
        errors.append(f"{label}.postgres_version must be a non-empty string")

    search_settings = evaluation.get("search_settings")
    if not isinstance(search_settings, dict) or not search_settings:
        errors.append(f"{label}.search_settings must be a non-empty object")
        search_settings = {}

    return {
        "commit": commit or "",
        "postgres_version": postgres_version or "",
        "search_settings": search_settings,
    }


def build_report(*, manifest_json: Path, min_queries: int, max_queries: int) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    raw = load_json(manifest_json)
    if not isinstance(raw, dict):
        raise ValueError(f"{manifest_json}: expected JSON object")
    unknown_keys(raw, TOP_KEYS, "manifest", errors)

    schema_version = raw.get("schema_version")
    if schema_version != 1:
        errors.append(f"manifest.schema_version must be 1, found {schema_version!r}")

    base_dir = manifest_json.parent
    corpus = raw.get("corpus")
    corpus_name = ""
    corpus_origin = ""
    sources: list[dict[str, Any]] = []
    if not isinstance(corpus, dict):
        errors.append("corpus must be an object")
    else:
        unknown_keys(corpus, CORPUS_KEYS, "corpus", errors)
        corpus_name = non_empty_str(corpus.get("name")) or ""
        if not corpus_name:
            errors.append("corpus.name must be a non-empty string")
        corpus_origin = corpus.get("origin") if isinstance(corpus.get("origin"), str) else ""
        if corpus_origin not in REAL_ORIGINS:
            errors.append(
                "corpus.origin must be 'external' "
                "(synthetic and public-smoke corpora are not real baselines)"
            )
        raw_sources = corpus.get("sources")
        if not isinstance(raw_sources, list) or not raw_sources:
            errors.append("corpus.sources must be a non-empty list")
        else:
            for index, source in enumerate(raw_sources, start=1):
                parsed_source = validate_source(
                    source=source,
                    index=index,
                    base_dir=base_dir,
                    errors=errors,
                    warnings=warnings,
                )
                if parsed_source is not None:
                    sources.append(parsed_source)

    source_names = [source["name"] for source in sources if source["name"]]
    duplicate_sources = sorted({name for name in source_names if source_names.count(name) > 1})
    if duplicate_sources:
        errors.append("corpus.sources has duplicate names: " + ", ".join(duplicate_sources[:10]))

    labels_report = validate_labels(
        labels=raw.get("labels"),
        base_dir=base_dir,
        min_queries=min_queries,
        max_queries=max_queries,
        errors=errors,
        warnings=warnings,
    )
    evaluation_report = validate_evaluation(evaluation=raw.get("evaluation"), errors=errors)

    return {
        "schema_version": 1,
        "valid": not errors,
        "manifest_json": str(manifest_json),
        "corpus_name": corpus_name,
        "corpus_origin": corpus_origin,
        "source_count": len(sources),
        "total_bytes": sum(source["bytes"] for source in sources),
        "min_queries": min_queries,
        "max_queries": max_queries,
        "query_count": labels_report["query_count"],
        "query_set_version": labels_report["query_set_version"],
        "qrels_version": labels_report["qrels_version"],
        "label_independence": labels_report["independence"],
        "labels_checksum_verified": labels_report["labels_checksum_verified"],
        "evaluation_commit": evaluation_report["commit"],
        "postgres_version": evaluation_report["postgres_version"],
        "search_settings": evaluation_report["search_settings"],
        "sources": sources,
        "errors": errors,
        "warnings": warnings,
        "error_count": len(errors),
        "warning_count": len(warnings),
    }


def error_report(
    *, manifest_json: Path, error: Exception, min_queries: int, max_queries: int
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "valid": False,
        "manifest_json": str(manifest_json),
        "corpus_name": "",
        "corpus_origin": "",
        "source_count": 0,
        "total_bytes": 0,
        "min_queries": min_queries,
        "max_queries": max_queries,
        "query_count": 0,
        "query_set_version": "",
        "qrels_version": "",
        "label_independence": "",
        "labels_checksum_verified": False,
        "evaluation_commit": "",
        "postgres_version": "",
        "search_settings": {},
        "sources": [],
        "errors": [str(error)],
        "warnings": [],
        "error_count": 1,
        "warning_count": 0,
    }


def markdown_cell(value: object) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# pgwarc_lance Real Corpus Input Doctor",
        "",
        f"- Manifest: `{report['manifest_json']}`",
        f"- Valid: `{str(report['valid']).lower()}`",
        f"- Corpus: `{report['corpus_name']}` ({report['corpus_origin'] or 'unset'})",
        f"- Sources: `{report['source_count']}`",
        f"- Total bytes: `{report['total_bytes']}`",
        f"- Queries: `{report['query_count']}` (required {report['min_queries']}-{report['max_queries']})",
        f"- Query set version: `{report['query_set_version']}`",
        f"- Qrels version: `{report['qrels_version']}`",
        f"- Label independence: `{report['label_independence']}`",
        f"- Labels checksum verified: `{str(report['labels_checksum_verified']).lower()}`",
        f"- Evaluation commit: `{report['evaluation_commit']}`",
        f"- PostgreSQL version: `{report['postgres_version']}`",
        "",
        "| Source | Kind | Bytes | SHA-256 | License | Redistribution | Verified |",
        "|--------|------|------:|---------|---------|----------------|----------|",
    ]
    for source in report["sources"]:
        lines.append(
            "| {name} | {kind} | {bytes} | `{sha}` | {license} | {redistribution} | {verified} |".format(
                name=markdown_cell(source["name"]),
                kind=markdown_cell(source["kind"]),
                bytes=source["bytes"],
                sha=markdown_cell(source["sha256"]),
                license=markdown_cell(source["license"]),
                redistribution=markdown_cell(source["redistribution"]),
                verified=str(source["checksum_verified"]).lower(),
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
    parser.add_argument("--manifest-json", type=Path, required=True)
    parser.add_argument("--min-queries", type=int, default=20)
    parser.add_argument("--max-queries", type=int, default=50)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.min_queries <= 0 or args.max_queries <= 0 or args.min_queries > args.max_queries:
        print(
            "real corpus input doctor failed: min-queries/max-queries must be positive and ordered",
            file=sys.stderr,
        )
        return 2

    try:
        report = build_report(
            manifest_json=args.manifest_json,
            min_queries=args.min_queries,
            max_queries=args.max_queries,
        )
    except Exception as exc:
        report = error_report(
            manifest_json=args.manifest_json,
            error=exc,
            min_queries=args.min_queries,
            max_queries=args.max_queries,
        )

    if args.json_output:
        write_json(args.json_output, report)
    if args.markdown_output:
        write_text(args.markdown_output, render_markdown(report))

    print(
        "real corpus input doctor: "
        f"sources={report['source_count']} queries={report['query_count']} "
        f"errors={report['error_count']} warnings={report['warning_count']}"
    )
    if report["errors"]:
        for error in report["errors"]:
            print(f"real corpus input doctor failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
