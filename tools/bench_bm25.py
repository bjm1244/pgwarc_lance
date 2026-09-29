#!/usr/bin/env python3
"""Benchmark bm25_index_document() over synthetic documents."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path


DEFAULT_PSQL = (
    "docker compose exec -T postgres "
    "psql -U pgwarc_lance -d pgwarc_lance_test "
    "-v ON_ERROR_STOP=1 -q -At"
)


def parse_int_list(value: str) -> list[int]:
    values = [int(part.strip()) for part in value.split(",") if part.strip()]
    if not values:
        raise argparse.ArgumentTypeError("expected at least one integer")
    if any(v <= 0 for v in values):
        raise argparse.ArgumentTypeError("all values must be positive")
    return values


def sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def doc_text(row: int, body_words: int) -> str:
    words = [f"term{(row + offset) % 997}" for offset in range(body_words)]
    return " ".join(words)


def run_sql(psql: str, sql: str) -> tuple[float, str]:
    started = time.perf_counter()
    result = subprocess.run(psql, shell=True, input=sql, text=True, capture_output=True)
    elapsed = time.perf_counter() - started
    if result.returncode:
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="")
        raise SystemExit(result.returncode)
    return elapsed, result.stdout.strip()


def bm25_sql(rows: int, body_words: int) -> str:
    lines = [
        "CREATE EXTENSION IF NOT EXISTS pgwarc_lance;",
        "BEGIN;",
        "DELETE FROM pgwarc_lance.bm25_term;",
        "DELETE FROM pgwarc_lance.bm25_doc;",
    ]
    for row in range(1, rows + 1):
        lines.append(f"SELECT bm25_index_document({row}, {sql_string(doc_text(row, body_words))});")
    lines.extend(
        [
            "COMMIT;",
            "SELECT bm25_doc_count();",
            "SELECT count(*) FROM pgwarc_lance.bm25_term;",
        ]
    )
    return "\n".join(lines) + "\n"


def output_lines(output: str) -> list[str]:
    return [line for line in output.splitlines() if line.strip()]


def write_json(path: Path | None, payload: dict[str, object]) -> None:
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_text(path: Path | None, content: str) -> None:
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=parse_int_list, default=parse_int_list("100,500,1000"))
    parser.add_argument("--body-words", type=int, default=80)
    parser.add_argument("--psql", default=DEFAULT_PSQL)
    parser.add_argument("--json-output", type=Path, help="write benchmark results as JSON")
    parser.add_argument("--markdown-output", type=Path, help="write benchmark table as Markdown")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.body_words <= 0:
        raise SystemExit("--body-words must be positive")

    markdown_lines = [
        f"body_words={args.body_words}",
        "| rows | elapsed_s | docs_per_s | term_rows_per_s | doc_count | term_rows |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    results: list[dict[str, object]] = []
    for rows in args.rows:
        elapsed, output = run_sql(args.psql, bm25_sql(rows, args.body_words))
        lines = output_lines(output)
        doc_count = int(lines[-2])
        term_rows = int(lines[-1])
        docs_per_s = rows / elapsed if elapsed else 0.0
        term_rows_per_s = term_rows / elapsed if elapsed else 0.0
        markdown_lines.append(
            f"| {rows} | {elapsed:.3f} | {docs_per_s:.1f} | "
            f"{term_rows_per_s:.1f} | {doc_count} | {term_rows} |"
        )
        results.append(
            {
                "rows": rows,
                "body_words": args.body_words,
                "elapsed_s": elapsed,
                "docs_per_s": docs_per_s,
                "term_rows_per_s": term_rows_per_s,
                "doc_count": doc_count,
                "term_rows": term_rows,
            }
        )

    markdown = "\n".join(markdown_lines) + "\n"
    print(markdown, end="")
    write_json(
        args.json_output,
        {
            "benchmark": "bm25_index_document",
            "body_words": args.body_words,
            "results": results,
        },
    )
    write_text(args.markdown_output, markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
