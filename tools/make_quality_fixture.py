#!/usr/bin/env python3
"""Build an eval_quality fixture from importer records, embeddings, and labels."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EVAL_PATH = ROOT / "tools" / "eval_quality.py"
SPEC = importlib.util.spec_from_file_location("eval_quality", EVAL_PATH)
eval_quality = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = eval_quality
SPEC.loader.exec_module(eval_quality)


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


def load_records(path: Path) -> list[dict[str, Any]]:
    records = []
    seen_doc_ids: set[int] = set()
    for index, row in enumerate(load_jsonl(path), start=1):
        doc_id = eval_quality.require_doc_id(row.get("doc_id"), f"{path}:{index}.doc_id")
        if doc_id in seen_doc_ids:
            raise ValueError(f"{path}:{index}: duplicate doc_id {doc_id}")
        seen_doc_ids.add(doc_id)
        records.append(
            {
                "doc_id": doc_id,
                "target_uri": row.get("target_uri"),
                "warc_date": row.get("warc_date"),
                "content_type": row.get("content_type"),
                "http_status": row.get("http_status"),
                "source_file": row.get("source_file"),
                "text": str(row["text"]),
            }
        )
    return records


def load_embeddings(path: Path) -> tuple[dict[int, list[float]], int]:
    embeddings: dict[int, list[float]] = {}
    vector_dim: int | None = None
    for index, row in enumerate(load_jsonl(path), start=1):
        raw_doc_id = row.get("doc_id", row.get("id"))
        if raw_doc_id is None:
            raise ValueError(f"{path}:{index}: missing doc_id")
        doc_id = eval_quality.require_doc_id(raw_doc_id, f"{path}:{index}.doc_id")
        raw_vector = row.get("vector")
        if not isinstance(raw_vector, list) or not raw_vector:
            raise ValueError(f"{path}:{index}: vector must be a non-empty list")
        vector = eval_quality.require_vector(raw_vector, len(raw_vector), f"{path}:{index}")
        if vector_dim is None:
            vector_dim = len(vector)
        elif len(vector) != vector_dim:
            raise ValueError(f"{path}:{index}: vector has dim {len(vector)}, expected {vector_dim}")
        if doc_id in embeddings:
            raise ValueError(f"{path}:{index}: duplicate doc_id {doc_id}")
        embeddings[doc_id] = vector
    if vector_dim is None:
        raise ValueError(f"{path}: expected at least one embedding")
    return embeddings, vector_dim


def load_queries(path: Path) -> tuple[str | None, list[dict[str, Any]]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return None, raw
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected JSON object or query list")
    queries = raw.get("queries")
    if not isinstance(queries, list) or not queries:
        raise ValueError(f"{path}: expected non-empty queries list")
    name = raw.get("name")
    return str(name) if name else None, queries


def build_fixture(
    name: str,
    records_path: Path,
    embeddings_path: Path,
    queries_path: Path,
    vector_dim: int | None,
) -> dict[str, Any]:
    records = load_records(records_path)
    embeddings, detected_dim = load_embeddings(embeddings_path)
    if vector_dim is None:
        vector_dim = detected_dim
    elif vector_dim != detected_dim:
        raise ValueError(
            f"--vector-dim {vector_dim} does not match embedding dim {detected_dim}"
        )

    docs = []
    missing_vectors = []
    for record in records:
        doc_id = int(record["doc_id"])
        vector = embeddings.get(doc_id)
        if vector is None:
            missing_vectors.append(doc_id)
            continue
        docs.append({**record, "vector": vector})
    if missing_vectors:
        sample = ", ".join(str(doc_id) for doc_id in missing_vectors[:5])
        raise ValueError(f"{embeddings_path}: missing vectors for doc_id(s): {sample}")

    query_name, queries = load_queries(queries_path)
    fixture = {
        "name": name or query_name or records_path.stem,
        "vector_dim": vector_dim,
        "docs": docs,
        "queries": queries,
    }
    return eval_quality.fixture_from_dict(
        fixture,
        "quality-fixture",
        reject_duplicate_identities=True,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records-jsonl", type=Path, required=True)
    parser.add_argument("--embedding-jsonl", type=Path, required=True)
    parser.add_argument("--queries-json", type=Path, required=True)
    parser.add_argument("--name", default="")
    parser.add_argument("--vector-dim", type=int)
    parser.add_argument("--output", "-o", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    fixture = build_fixture(
        name=args.name,
        records_path=args.records_jsonl,
        embeddings_path=args.embedding_jsonl,
        queries_path=args.queries_json,
        vector_dim=args.vector_dim,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(fixture, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"wrote {args.output} docs={len(fixture['docs'])} "
        f"queries={len(fixture['queries'])} vector_dim={fixture['vector_dim']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
