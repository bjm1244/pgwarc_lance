#!/usr/bin/env python3
"""Build a labeled quality fixture from the documented public pywb WARC sample."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_URL = (
    "https://raw.githubusercontent.com/webrecorder/pywb/main/"
    "sample_archive/warcs/example.warc.gz"
)


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


warc_importer = load_module("warc_importer", ROOT / "tools" / "warc_importer.py")
eval_quality = load_module("eval_quality", ROOT / "tools" / "eval_quality.py")


def download(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=60) as response:
        path.write_bytes(response.read())


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_records(path: Path, min_text_chars: int) -> list[Any]:
    records = warc_importer.load_import_records(
        [path], limit=None, min_text_chars=min_text_chars
    )
    if not records:
        raise ValueError(f"{path}: produced no importable records")
    return records


def load_records_from_paths(
    paths: list[Path], min_text_chars: int
) -> tuple[list[Any], list[dict[str, int]]]:
    records: list[Any] = []
    source_stats: list[dict[str, int]] = []
    for path in paths:
        warc_record_count = 0
        imported_record_count = 0
        skipped_record_count = 0
        for warc_record in warc_importer.iter_warc_records(path):
            warc_record_count += 1
            record = warc_importer.import_record_from_warc(warc_record)
            if record is None or len(record.text) < min_text_chars:
                skipped_record_count += 1
                continue
            imported_record_count += 1
            records.append(record)
        source_stats.append(
            {
                "warc_record_count": warc_record_count,
                "imported_record_count": imported_record_count,
                "skipped_record_count": skipped_record_count,
            }
        )
    if not records:
        joined = ", ".join(str(path) for path in paths)
        raise ValueError(f"{joined}: produced no importable records")
    doc_ids = [record.doc_id for record in records]
    if len(doc_ids) != len(set(doc_ids)):
        raise ValueError("multiple WARC inputs produced duplicate doc_id values")
    return records, source_stats


def average_vectors(vectors: list[list[float]]) -> list[float]:
    if not vectors:
        raise ValueError("expected at least one vector")
    dim = len(vectors[0])
    totals = [0.0] * dim
    for vector in vectors:
        if len(vector) != dim:
            raise ValueError("cannot average vectors with different dimensions")
        for index, value in enumerate(vector):
            totals[index] += value
    averaged = [value / len(vectors) for value in totals]
    norm = sum(value * value for value in averaged) ** 0.5
    if norm:
        return [value / norm for value in averaged]
    return averaged


def record_matches(record: Any, label_text: str) -> bool:
    haystack = " ".join(
        [
            str(record.target_uri or ""),
            str(record.content_type or ""),
            record.text,
        ]
    ).lower()
    return label_text.lower() in haystack


def load_query_specs(path: Path) -> list[dict[str, str]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{path}: expected a non-empty JSON array of query specs")

    specs: list[dict[str, str]] = []
    names: set[str] = set()
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"{path}[{index}]: expected an object")
        values: dict[str, str] = {}
        for field in ("name", "query", "label_text"):
            value = item.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{path}[{index}].{field}: expected a non-empty string")
            values[field] = value.strip()
        if values["name"] in names:
            raise ValueError(f"{path}[{index}].name: duplicate query name {values['name']!r}")
        names.add(values["name"])
        specs.append(values)
    return specs


def load_source_specs(path: Path) -> list[dict[str, str | None]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{path}: expected a non-empty JSON array of source specs")

    specs: list[dict[str, str | None]] = []
    seen: set[tuple[str | None, str | None]] = set()
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"{path}[{index}]: expected an object")
        raw_path = item.get("path")
        raw_url = item.get("url")
        if raw_path is not None and (not isinstance(raw_path, str) or not raw_path.strip()):
            raise ValueError(f"{path}[{index}].path: expected a non-empty string")
        if raw_url is not None and (not isinstance(raw_url, str) or not raw_url.strip()):
            raise ValueError(f"{path}[{index}].url: expected a non-empty string")
        source_path = raw_path.strip() if isinstance(raw_path, str) else None
        source_url = raw_url.strip() if isinstance(raw_url, str) else None
        if source_path is None and source_url is None:
            raise ValueError(f"{path}[{index}]: expected path or url")
        if source_path is not None and source_url is not None:
            raise ValueError(f"{path}[{index}]: expected exactly one of path or url")
        key = (source_path, source_url)
        if key in seen:
            raise ValueError(f"{path}[{index}]: duplicate source spec")
        seen.add(key)
        specs.append({"path": source_path, "url": source_url})
    return specs


def download_name(url: str, index: int) -> str:
    name = Path(urlparse(url).path).name
    if not name:
        name = f"source-{index}.warc"
    elif not name.endswith((".warc", ".warc.gz", ".gz")):
        name += ".warc"
    return f"{index:03d}-{name}"


def prepare_source_specs(
    specs: list[dict[str, str | None]], temp_dir: Path
) -> tuple[list[Path], list[dict[str, Any]]]:
    paths: list[Path] = []
    sources: list[dict[str, Any]] = []
    for index, spec in enumerate(specs, start=1):
        requested_path = Path(spec["path"]) if spec["path"] else None
        source_url = spec["url"]
        path = requested_path or (temp_dir / download_name(source_url or "", index))
        downloaded_at = None
        source_kind = "local"
        if not path.exists():
            if not source_url:
                raise ValueError(f"source {index}: path does not exist and has no url: {path}")
            download(source_url, path)
            downloaded_at = utc_now()
            source_kind = "download"
        if not path.is_file():
            raise ValueError(f"source {index}: expected a regular file: {path}")
        paths.append(path)
        sources.append(
            {
                "kind": source_kind,
                "url": source_url,
                "path": str(requested_path) if requested_path else None,
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "downloaded_at": downloaded_at,
            }
        )
    return paths, sources


def fixture_from_records(
    records: list[Any],
    *,
    name: str,
    vector_dim: int,
    query_name: str | None,
    query: str | None,
    label_text: str | None,
    query_specs: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    docs = []
    vectors_by_doc_id: dict[int, list[float]] = {}
    for record in records:
        vector = warc_importer.hash_embedding(record.text, vector_dim)
        vectors_by_doc_id[record.doc_id] = vector
        docs.append(
            {
                "doc_id": record.doc_id,
                "target_uri": record.target_uri,
                "warc_date": record.warc_date,
                "content_type": record.content_type,
                "http_status": record.http_status,
                "source_file": record.source_file,
                "text": record.text,
                "vector": vector,
            }
        )

    if query_specs is None:
        if query_name is None or query is None or label_text is None:
            raise ValueError("query_name, query, and label_text are required without query_specs")
        query_specs = [{"name": query_name, "query": query, "label_text": label_text}]
    if not query_specs:
        raise ValueError("expected at least one query spec")

    queries = []
    for spec in query_specs:
        expected_doc_ids = [
            record.doc_id for record in records if record_matches(record, spec["label_text"])
        ]
        if not expected_doc_ids:
            raise ValueError(
                f"no records matched --label-text {spec['label_text']!r} "
                f"for query {spec['name']!r}"
            )
        query_vector = average_vectors([vectors_by_doc_id[doc_id] for doc_id in expected_doc_ids])
        queries.append(
            {
                "name": spec["name"],
                "query": spec["query"],
                "vector": query_vector,
                "expected_doc_ids": expected_doc_ids,
            }
        )

    raw = {
        "name": name,
        "vector_dim": vector_dim,
        "docs": docs,
        "queries": queries,
    }
    return eval_quality.fixture_from_dict(raw, "public-warc-quality")


def write_json(path: Path | None, payload: Any) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_artifacts(
    fixture: dict[str, Any],
    records: list[Any],
    records_jsonl: Path | None,
    embedding_jsonl: Path | None,
    queries_json: Path | None,
) -> None:
    if records_jsonl:
        records_jsonl.parent.mkdir(parents=True, exist_ok=True)
        warc_importer.write_records_jsonl(records, records_jsonl)

    if embedding_jsonl:
        embedding_jsonl.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            {"doc_id": doc["doc_id"], "vector": doc["vector"]}
            for doc in fixture["docs"]
        ]
        embedding_jsonl.write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
            encoding="utf-8",
        )

    write_json(queries_json, {"name": fixture["name"], "queries": fixture["queries"]})


def fixture_summary(fixture: dict[str, Any], output_path: Path) -> dict[str, Any]:
    return {
        "name": fixture["name"],
        "output": str(output_path),
        "vector_dim": fixture["vector_dim"],
        "doc_count": len(fixture["docs"]),
        "query_count": len(fixture["queries"]),
        "expected_doc_counts": [
            len(query["expected_doc_ids"]) for query in fixture["queries"]
        ],
    }


def add_source_coverage(
    sources: list[dict[str, Any]],
    source_paths: list[Path],
    source_stats: list[dict[str, int]],
    fixture: dict[str, Any],
) -> None:
    if not (len(sources) == len(source_paths) == len(source_stats)):
        raise ValueError("source provenance and record statistics are out of sync")
    labeled_doc_ids = {
        doc_id
        for query in fixture["queries"]
        for doc_id in query["expected_doc_ids"]
    }
    for source, path, stats in zip(sources, source_paths, source_stats):
        source_docs = [
            doc for doc in fixture["docs"] if doc["source_file"] == str(path)
        ]
        source_doc_ids = {doc["doc_id"] for doc in source_docs}
        source["records"] = stats
        source["coverage"] = {
            "doc_count": len(source_docs),
            "labeled_doc_count": len(source_doc_ids.intersection(labeled_doc_ids)),
            "unlabeled_doc_count": len(source_doc_ids.difference(labeled_doc_ids)),
        }


def query_spec_summary(
    query_spec_path: Path | None, query_specs: list[dict[str, str]]
) -> dict[str, Any]:
    return {
        "kind": "file" if query_spec_path else "cli",
        "path": str(query_spec_path) if query_spec_path else None,
        "sha256": sha256_file(query_spec_path) if query_spec_path else None,
        "count": len(query_specs),
        "specs": query_specs,
    }


def build_provenance_manifest(
    *,
    source_path: Path,
    source_path_record: str | None,
    source_kind: str,
    source_url: str | None,
    downloaded_at: str | None,
    query_spec_path: Path | None,
    query_specs: list[dict[str, str]],
    fixture: dict[str, Any],
    output_path: Path,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": utc_now(),
        "source": {
            "kind": source_kind,
            "url": source_url,
            "path": source_path_record,
            "bytes": source_path.stat().st_size,
            "sha256": sha256_file(source_path),
            "downloaded_at": downloaded_at,
        },
        "query_spec": query_spec_summary(query_spec_path, query_specs),
        "fixture": fixture_summary(fixture, output_path),
    }


def build_corpus_provenance_manifest(
    *,
    source_spec_path: Path,
    source_specs: list[dict[str, str | None]],
    sources: list[dict[str, Any]],
    query_spec_path: Path | None,
    query_specs: list[dict[str, str]],
    fixture: dict[str, Any],
    output_path: Path,
) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "generated_at": utc_now(),
        "source_spec": {
            "kind": "file",
            "path": str(source_spec_path),
            "sha256": sha256_file(source_spec_path),
            "count": len(source_specs),
            "specs": source_specs,
        },
        "sources": sources,
        "query_spec": query_spec_summary(query_spec_path, query_specs),
        "fixture": fixture_summary(fixture, output_path),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument(
        "--warc-path",
        type=Path,
        help="local WARC/WARC.GZ path; downloaded from --url when missing",
    )
    parser.add_argument(
        "--source-spec-json",
        type=Path,
        help="JSON array of {path, url} WARC inputs for a corpus fixture",
    )
    parser.add_argument("--output", "-o", type=Path, required=True)
    parser.add_argument(
        "--manifest-json",
        type=Path,
        help="write WARC source/query-spec/fixture provenance JSON",
    )
    parser.add_argument("--name", default="pywb-public-warc-smoke")
    parser.add_argument("--query-name", default="example_domain")
    parser.add_argument("--query", default="Example Domain")
    parser.add_argument("--label-text", default="Example Domain")
    parser.add_argument(
        "--query-spec-json",
        type=Path,
        help="JSON array of {name, query, label_text} specs for multiple labeled queries",
    )
    parser.add_argument("--vector-dim", type=int, default=4)
    parser.add_argument("--min-text-chars", type=int, default=1)
    parser.add_argument("--records-jsonl", type=Path)
    parser.add_argument("--embedding-jsonl", type=Path)
    parser.add_argument("--queries-json", type=Path)
    return parser.parse_args(argv)


def build_from_path(
    path: Path,
    args: argparse.Namespace,
    query_specs: list[dict[str, str]] | None,
) -> tuple[dict[str, Any], list[Any]]:
    records = load_records(path, args.min_text_chars)
    fixture = fixture_from_records(
        records,
        name=args.name,
        vector_dim=args.vector_dim,
        query_name=args.query_name,
        query=args.query,
        label_text=args.label_text,
        query_specs=query_specs,
    )
    return fixture, records


def build_from_paths(
    paths: list[Path],
    args: argparse.Namespace,
    query_specs: list[dict[str, str]] | None,
) -> tuple[dict[str, Any], list[Any], list[dict[str, int]]]:
    records, source_stats = load_records_from_paths(paths, args.min_text_chars)
    fixture = fixture_from_records(
        records,
        name=args.name,
        vector_dim=args.vector_dim,
        query_name=args.query_name,
        query=args.query,
        label_text=args.label_text,
        query_specs=query_specs,
    )
    return fixture, records, source_stats


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.vector_dim <= 0:
        raise SystemExit("--vector-dim must be positive")
    if args.min_text_chars <= 0:
        raise SystemExit("--min-text-chars must be positive")
    if args.warc_path and args.source_spec_json:
        raise SystemExit("--warc-path and --source-spec-json are mutually exclusive")
    query_specs = load_query_specs(args.query_spec_json) if args.query_spec_json else None
    effective_query_specs = query_specs or [
        {"name": args.query_name, "query": args.query, "label_text": args.label_text}
    ]

    if args.source_spec_json:
        source_specs = load_source_specs(args.source_spec_json)
        with tempfile.TemporaryDirectory() as tmp:
            paths, sources = prepare_source_specs(source_specs, Path(tmp))
            fixture, records, source_stats = build_from_paths(paths, args, query_specs)
            add_source_coverage(sources, paths, source_stats, fixture)
            provenance = build_corpus_provenance_manifest(
                source_spec_path=args.source_spec_json,
                source_specs=source_specs,
                sources=sources,
                query_spec_path=args.query_spec_json,
                query_specs=effective_query_specs,
                fixture=fixture,
                output_path=args.output,
            )
    elif args.warc_path:
        downloaded_at = None
        source_kind = "local"
        source_url = None
        if not args.warc_path.exists():
            download(args.url, args.warc_path)
            downloaded_at = utc_now()
            source_kind = "download"
            source_url = args.url
        fixture, records = build_from_path(args.warc_path, args, query_specs)
        provenance = build_provenance_manifest(
            source_path=args.warc_path,
            source_path_record=str(args.warc_path),
            source_kind=source_kind,
            source_url=source_url,
            downloaded_at=downloaded_at,
            query_spec_path=args.query_spec_json,
            query_specs=effective_query_specs,
            fixture=fixture,
            output_path=args.output,
        )
    else:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "example.warc.gz"
            download(args.url, path)
            downloaded_at = utc_now()
            fixture, records = build_from_path(path, args, query_specs)
            provenance = build_provenance_manifest(
                source_path=path,
                source_path_record=None,
                source_kind="download",
                source_url=args.url,
                downloaded_at=downloaded_at,
                query_spec_path=args.query_spec_json,
                query_specs=effective_query_specs,
                fixture=fixture,
                output_path=args.output,
            )

    write_json(args.output, fixture)
    write_artifacts(
        fixture,
        records,
        args.records_jsonl,
        args.embedding_jsonl,
        args.queries_json,
    )
    write_json(args.manifest_json, provenance)
    if len(fixture["queries"]) == 1:
        expected = fixture["queries"][0]["expected_doc_ids"]
    else:
        expected = [query["expected_doc_ids"] for query in fixture["queries"]]
    print(
        f"wrote {args.output} docs={len(fixture['docs'])} "
        f"queries={len(fixture['queries'])} expected_doc_ids={expected}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
