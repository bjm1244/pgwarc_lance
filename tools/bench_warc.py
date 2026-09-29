#!/usr/bin/env python3
"""Benchmark synthetic WARC parsing and importer SQL generation."""

from __future__ import annotations

import argparse
import gzip
import importlib.util
import json
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IMPORTER_PATH = ROOT / "tools" / "warc_importer.py"
SPEC = importlib.util.spec_from_file_location("warc_importer", IMPORTER_PATH)
warc_importer = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = warc_importer
SPEC.loader.exec_module(warc_importer)

DEFAULT_PSQL = (
    "docker compose exec -T postgres env PGOPTIONS='-c client_min_messages=warning' "
    "psql -U pgwarc_lance -d pgwarc_lance_test "
    "-v ON_ERROR_STOP=1 -q -At -o /dev/null"
)


def write_json(path: Path | None, payload: dict[str, object]) -> None:
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_text(path: Path | None, content: str) -> None:
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def warc_response(row: int, body_words: int) -> bytes:
    uri = f"https://example.test/doc/{row}"
    date = "2026-06-30T00:00:00Z"
    words = " ".join(f"term{(row + offset) % 97}" for offset in range(body_words))
    html = (
        "<html><head><title>bench</title><script>skip()</script></head>"
        f"<body><h1>Document {row}</h1><p>{words}</p></body></html>"
    )
    http = (
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        "\r\n"
        f"{html}"
    ).encode("utf-8")
    headers = (
        "WARC/1.1\r\n"
        "WARC-Type: response\r\n"
        f"WARC-Target-URI: {uri}\r\n"
        f"WARC-Date: {date}\r\n"
        f"WARC-Record-ID: <urn:uuid:00000000-0000-0000-0000-{row:012d}>\r\n"
        "Content-Type: application/http; msgtype=response\r\n"
        f"Content-Length: {len(http)}\r\n"
        "\r\n"
    ).encode("ascii")
    return headers + http + b"\r\n\r\n"


def write_synthetic_warc(path: Path, rows: int, body_words: int) -> int:
    with gzip.open(path, "wb") as stream:
        for row in range(1, rows + 1):
            stream.write(warc_response(row, body_words))
    return path.stat().st_size


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=1000)
    parser.add_argument("--body-words", type=int, default=80)
    parser.add_argument("--vector-dim", type=int, default=384)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--bm25-mode", choices=warc_importer.BM25_MODES, default="function")
    parser.add_argument("--min-text-chars", type=int, default=1)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--psql", default=DEFAULT_PSQL)
    parser.add_argument("--lance-uri", default="/tmp/pgwarc_lance_bench_warc.lance")
    parser.add_argument("--keep-warc", type=Path)
    parser.add_argument("--json-output", type=Path, help="write benchmark results as JSON")
    parser.add_argument("--markdown-output", type=Path, help="write benchmark table as Markdown")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.rows <= 0:
        raise SystemExit("--rows must be positive")
    if args.body_words <= 0:
        raise SystemExit("--body-words must be positive")
    if args.vector_dim <= 0:
        raise SystemExit("--vector-dim must be positive")
    if args.batch_size <= 0:
        raise SystemExit("--batch-size must be positive")

    with tempfile.TemporaryDirectory() as tmp:
        warc_path = args.keep_warc or Path(tmp) / "bench.warc.gz"

        started = time.perf_counter()
        warc_bytes = write_synthetic_warc(warc_path, args.rows, args.body_words)
        generate_s = time.perf_counter() - started

        started = time.perf_counter()
        records = warc_importer.load_import_records(
            [warc_path], limit=None, min_text_chars=args.min_text_chars
        )
        parse_s = time.perf_counter() - started

        started = time.perf_counter()
        sql = warc_importer.emit_sql(
            records=records,
            lance_uri=args.lance_uri,
            vector_dim=args.vector_dim,
            overwrite_lance=True,
            include_hash_vectors=True,
            batch_size=args.batch_size,
            bm25_mode=args.bm25_mode,
        )
        sql_s = time.perf_counter() - started
        sql_bytes = len(sql.encode("utf-8"))

        execute_s: float | None = None
        if args.execute:
            started = time.perf_counter()
            warc_importer.execute_sql(sql, args.psql)
            execute_s = time.perf_counter() - started

    execute_value = "-" if execute_s is None else f"{execute_s:.3f}"
    markdown = "\n".join(
        [
            "| rows | warc_kb | generate_s | parse_s | sql_s | sql_kb | execute_s |",
            "|---:|---:|---:|---:|---:|---:|---:|",
            (
                f"| {len(records)} | {warc_bytes / 1024:.1f} | {generate_s:.3f} | "
                f"{parse_s:.3f} | {sql_s:.3f} | {sql_bytes / 1024:.1f} | {execute_value} |"
            ),
        ]
    ) + "\n"
    print(markdown, end="")
    payload = {
        "benchmark": "warc_importer",
        "rows": len(records),
        "requested_rows": args.rows,
        "body_words": args.body_words,
        "vector_dim": args.vector_dim,
        "batch_size": args.batch_size,
        "bm25_mode": args.bm25_mode,
        "warc_bytes": warc_bytes,
        "sql_bytes": sql_bytes,
        "generate_s": generate_s,
        "parse_s": parse_s,
        "sql_s": sql_s,
        "execute_s": execute_s,
        "execute": args.execute,
    }
    write_json(args.json_output, payload)
    write_text(args.markdown_output, markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
