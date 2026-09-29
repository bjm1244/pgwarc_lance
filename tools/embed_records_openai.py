#!/usr/bin/env python3
"""Embed WARC importer record JSONL with an OpenAI-compatible endpoint."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any


DEFAULT_URL = "https://api.openai.com/v1/embeddings"
DEFAULT_MODEL = "text-embedding-3-small"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--no-auth", action="store_true")
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--dimensions", type=int)
    parser.add_argument("--expect-dim", type=int)
    parser.add_argument("--max-text-chars", type=int)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--user")
    return parser.parse_args()


def require_positive(value: int | None, name: str) -> None:
    if value is not None and value <= 0:
        raise SystemExit(f"{name} must be positive")


def read_records(max_text_chars: int | None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_no, line in enumerate(sys.stdin, start=1):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"invalid JSON on stdin line {line_no}: {exc}") from exc
        if "doc_id" not in record:
            raise SystemExit(f"missing doc_id on stdin line {line_no}")
        text = str(record.get("text", ""))
        if not text.strip():
            raise SystemExit(f"empty text for doc_id={record['doc_id']}")
        if max_text_chars is not None:
            text = text[:max_text_chars]
        records.append({"doc_id": int(record["doc_id"]), "text": text})
    return records


def request_json(url: str, payload: dict[str, Any], api_key: str | None, timeout: float) -> Any:
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"embedding request failed: HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"embedding request failed: {exc}") from exc


def parse_vectors(response: Any, expected_count: int, expect_dim: int | None) -> list[list[float]]:
    if not isinstance(response, dict) or not isinstance(response.get("data"), list):
        raise SystemExit("embedding response must contain a data array")

    vectors_by_index: dict[int, list[float]] = {}
    for fallback_index, item in enumerate(response["data"]):
        if not isinstance(item, dict):
            raise SystemExit("embedding response data items must be objects")
        index = int(item.get("index", fallback_index))
        vector = item.get("embedding")
        if not isinstance(vector, list) or not all(isinstance(v, (int, float)) for v in vector):
            raise SystemExit(f"embedding response item {index} has no numeric vector")
        if expect_dim is not None and len(vector) != expect_dim:
            raise SystemExit(
                f"embedding response item {index} has dim {len(vector)}, expected {expect_dim}"
            )
        vectors_by_index[index] = [float(v) for v in vector]

    if len(vectors_by_index) != expected_count:
        raise SystemExit(
            f"embedding response returned {len(vectors_by_index)} vectors, expected {expected_count}"
        )
    return [vectors_by_index[index] for index in range(expected_count)]


def batched(records: list[dict[str, Any]], batch_size: int) -> list[list[dict[str, Any]]]:
    return [records[start : start + batch_size] for start in range(0, len(records), batch_size)]


def main() -> int:
    args = parse_args()
    require_positive(args.batch_size, "--batch-size")
    require_positive(args.dimensions, "--dimensions")
    require_positive(args.expect_dim, "--expect-dim")
    require_positive(args.max_text_chars, "--max-text-chars")

    api_key = None if args.no_auth else os.environ.get(args.api_key_env)
    if not args.no_auth and not api_key:
        raise SystemExit(f"{args.api_key_env} is required unless --no-auth is set")

    records = read_records(args.max_text_chars)
    for batch in batched(records, args.batch_size):
        payload: dict[str, Any] = {
            "model": args.model,
            "input": [record["text"] for record in batch],
            "encoding_format": "float",
        }
        if args.dimensions is not None:
            payload["dimensions"] = args.dimensions
        if args.user:
            payload["user"] = args.user
        response = request_json(args.url, payload, api_key, args.timeout)
        vectors = parse_vectors(response, len(batch), args.expect_dim)
        for record, vector in zip(batch, vectors):
            print(json.dumps({"doc_id": record["doc_id"], "vector": vector}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
