#!/usr/bin/env python3
"""Compare WARC importer BM25 indexing modes."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BENCH_WARC_PATH = ROOT / "tools" / "bench_warc.py"
SPEC = importlib.util.spec_from_file_location("bench_warc", BENCH_WARC_PATH)
bench_warc = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = bench_warc
SPEC.loader.exec_module(bench_warc)


def parse_int_list(value: str) -> list[int]:
    values = [int(part.strip()) for part in value.split(",") if part.strip()]
    if not values:
        raise argparse.ArgumentTypeError("expected at least one integer")
    if any(item <= 0 for item in values):
        raise argparse.ArgumentTypeError("all values must be positive")
    return values


def write_json(path: Path | None, payload: dict[str, Any]) -> None:
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_text(path: Path | None, content: str) -> None:
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def fmt_seconds(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def fmt_rate(rows: int, seconds: float | None) -> str:
    if not seconds:
        return "-"
    return f"{rows / seconds:.1f}"


def fmt_speedup(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}x"


def reset_db(psql: str) -> None:
    bench_warc.warc_importer.execute_sql(
        "DROP EXTENSION IF EXISTS pgwarc_lance CASCADE; CREATE EXTENSION pgwarc_lance;\n",
        psql,
    )


def compare_rows(
    rows: int,
    body_words: int,
    vector_dim: int,
    batch_size: int,
    min_text_chars: int,
    include_hash_vectors: bool,
    execute: bool,
    reset_between_modes: bool,
    psql: str,
    lance_uri_prefix: str,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        warc_path = Path(tmp) / f"bench-{rows}.warc.gz"

        started = time.perf_counter()
        warc_bytes = bench_warc.write_synthetic_warc(warc_path, rows, body_words)
        generate_s = time.perf_counter() - started

        started = time.perf_counter()
        records = bench_warc.warc_importer.load_import_records(
            [warc_path], limit=None, min_text_chars=min_text_chars
        )
        parse_s = time.perf_counter() - started

        mode_results = []
        for mode in bench_warc.warc_importer.BM25_MODES:
            lance_uri = f"{lance_uri_prefix}-{rows}-{mode}.lance"
            started = time.perf_counter()
            sql = bench_warc.warc_importer.emit_sql(
                records=records,
                lance_uri=lance_uri,
                vector_dim=vector_dim,
                overwrite_lance=True,
                include_hash_vectors=include_hash_vectors,
                batch_size=batch_size,
                bm25_mode=mode,
            )
            sql_s = time.perf_counter() - started

            execute_s: float | None = None
            if execute:
                if reset_between_modes:
                    reset_db(psql)
                started = time.perf_counter()
                bench_warc.warc_importer.execute_sql(sql, psql)
                execute_s = time.perf_counter() - started

            mode_results.append(
                {
                    "bm25_mode": mode,
                    "sql_s": sql_s,
                    "sql_bytes": len(sql.encode("utf-8")),
                    "execute_s": execute_s,
                    "execute_docs_per_s": None
                    if execute_s is None or execute_s == 0.0
                    else len(records) / execute_s,
                }
            )

    function = next(item for item in mode_results if item["bm25_mode"] == "function")
    for item in mode_results:
        sql_s = float(item["sql_s"])
        item["sql_speedup_vs_function"] = (
            None if sql_s == 0.0 else float(function["sql_s"]) / sql_s
        )
        if item["execute_s"] is None or function["execute_s"] is None or item["execute_s"] == 0.0:
            item["execute_speedup_vs_function"] = None
        else:
            item["execute_speedup_vs_function"] = float(function["execute_s"]) / float(
                item["execute_s"]
            )

    return {
        "rows": len(records),
        "requested_rows": rows,
        "body_words": body_words,
        "vector_dim": vector_dim,
        "batch_size": batch_size,
        "warc_bytes": warc_bytes,
        "generate_s": generate_s,
        "parse_s": parse_s,
        "include_hash_vectors": include_hash_vectors,
        "execute": execute,
        "reset_between_modes": reset_between_modes,
        "modes": mode_results,
    }


def markdown_report(results: list[dict[str, Any]]) -> str:
    lines = [
        "| rows | bm25_mode | warc_kb | generate_s | parse_s | sql_s | sql_kb | "
        "execute_s | execute_docs_per_s | sql_speedup | execute_speedup |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for result in results:
        for mode in result["modes"]:
            lines.append(
                f"| {result['rows']} | {mode['bm25_mode']} | "
                f"{result['warc_bytes'] / 1024:.1f} | "
                f"{result['generate_s']:.3f} | {result['parse_s']:.3f} | "
                f"{mode['sql_s']:.3f} | {mode['sql_bytes'] / 1024:.1f} | "
                f"{fmt_seconds(mode['execute_s'])} | "
                f"{fmt_rate(result['rows'], mode['execute_s'])} | "
                f"{fmt_speedup(mode['sql_speedup_vs_function'])} | "
                f"{fmt_speedup(mode['execute_speedup_vs_function'])} |"
            )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=parse_int_list, default=parse_int_list("100,500,1000"))
    parser.add_argument("--body-words", type=int, default=80)
    parser.add_argument("--vector-dim", type=int, default=384)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--min-text-chars", type=int, default=1)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--no-reset-between-modes",
        action="store_true",
        help="when executing, keep DB state between mode runs instead of recreating the extension",
    )
    parser.add_argument("--no-hash-vectors", action="store_true")
    parser.add_argument("--psql", default=bench_warc.DEFAULT_PSQL)
    parser.add_argument("--lance-uri-prefix", default="/tmp/pgwarc_lance_bm25_modes")
    parser.add_argument("--json-output", type=Path, help="write comparison results as JSON")
    parser.add_argument("--markdown-output", type=Path, help="write comparison table as Markdown")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.body_words <= 0:
        raise SystemExit("--body-words must be positive")
    if args.vector_dim <= 0:
        raise SystemExit("--vector-dim must be positive")
    if args.batch_size <= 0:
        raise SystemExit("--batch-size must be positive")
    if args.min_text_chars <= 0:
        raise SystemExit("--min-text-chars must be positive")

    results = [
        compare_rows(
            rows=rows,
            body_words=args.body_words,
            vector_dim=args.vector_dim,
            batch_size=args.batch_size,
            min_text_chars=args.min_text_chars,
            include_hash_vectors=not args.no_hash_vectors,
            execute=args.execute,
            reset_between_modes=not args.no_reset_between_modes,
            psql=args.psql,
            lance_uri_prefix=args.lance_uri_prefix,
        )
        for rows in args.rows
    ]
    markdown = markdown_report(results)
    print(markdown, end="")
    write_json(
        args.json_output,
        {
            "benchmark": "warc_importer_bm25_mode_comparison",
            "rows": args.rows,
            "body_words": args.body_words,
            "vector_dim": args.vector_dim,
            "batch_size": args.batch_size,
            "execute": args.execute,
            "reset_between_modes": not args.no_reset_between_modes,
            "include_hash_vectors": not args.no_hash_vectors,
            "results": results,
        },
    )
    write_text(args.markdown_output, markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
