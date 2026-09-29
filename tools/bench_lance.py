#!/usr/bin/env python3
"""Benchmark row-by-row Lance inserts against lance_insert_many."""

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


def write_json(path: Path | None, payload: dict[str, object]) -> None:
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_text(path: Path | None, content: str) -> None:
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def parse_int_list(value: str) -> list[int]:
    values = [int(part.strip()) for part in value.split(",") if part.strip()]
    if not values:
        raise argparse.ArgumentTypeError("expected at least one integer")
    if any(v <= 0 for v in values):
        raise argparse.ArgumentTypeError("all values must be positive")
    return values


def vector_values(row: int, dim: int) -> list[str]:
    return [f"{((row + offset) % 97) / 97:.6f}" for offset in range(dim)]


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


def row_by_row_sql(uri: str, rows: int, dim: int) -> str:
    lines = [
        f"SELECT lance_create_table('{uri}', {dim}, true);",
        "BEGIN;",
    ]
    for row in range(rows):
        vector = ",".join(vector_values(row, dim))
        lines.append(
            f"SELECT lance_insert('{uri}', {row + 1}, "
            f"ARRAY[{vector}]::float4[], 'doc{row + 1}');"
        )
    lines.extend(["COMMIT;", f"SELECT lance_count('{uri}');"])
    return "\n".join(lines) + "\n"


def batch_sql(uri: str, rows: int, dim: int, batch_size: int) -> str:
    lines = [
        f"SELECT lance_create_table('{uri}', {dim}, true);",
        "BEGIN;",
    ]
    for start in range(0, rows, batch_size):
        end = min(rows, start + batch_size)
        ids = ",".join(str(row + 1) for row in range(start, end))
        labels = ",".join(f"'doc{row + 1}'" for row in range(start, end))
        flat = ",".join(value for row in range(start, end) for value in vector_values(row, dim))
        lines.append(
            f"SELECT lance_insert_many('{uri}', ARRAY[{ids}]::bigint[], "
            f"ARRAY[{flat}]::float4[], {dim}, ARRAY[{labels}]::text[]);"
        )
    lines.extend(["COMMIT;", f"SELECT lance_count('{uri}');"])
    return "\n".join(lines) + "\n"


def last_output_line(output: str) -> str:
    lines = [line for line in output.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=parse_int_list, default=parse_int_list("100,500,1000"))
    parser.add_argument("--dim", type=int, default=384)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--psql", default=DEFAULT_PSQL)
    parser.add_argument("--uri-prefix", default="/tmp/pgwarc_lance_bench")
    parser.add_argument(
        "--batch-only",
        action="store_true",
        help="skip row-by-row timings for larger exploratory runs",
    )
    parser.add_argument("--json-output", type=Path, help="write benchmark results as JSON")
    parser.add_argument("--markdown-output", type=Path, help="write benchmark table as Markdown")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.dim <= 0:
        raise SystemExit("--dim must be positive")
    if args.batch_size <= 0:
        raise SystemExit("--batch-size must be positive")

    output_lines = [
        f"dim={args.dim} batch_size={args.batch_size}",
        "| rows | row_by_row_s | batch_s | speedup | row_count | batch_count |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    results: list[dict[str, object]] = []

    for rows in args.rows:
        row_elapsed: float | None = None
        row_count = "-"
        if not args.batch_only:
            sql = row_by_row_sql(f"{args.uri_prefix}_row_{rows}.lance", rows, args.dim)
            row_elapsed, row_output = run_sql(args.psql, sql)
            row_count = last_output_line(row_output)

        sql = batch_sql(
            f"{args.uri_prefix}_batch_{rows}.lance",
            rows,
            args.dim,
            args.batch_size,
        )
        batch_elapsed, batch_output = run_sql(args.psql, sql)
        batch_count = last_output_line(batch_output)
        speedup_value = None if row_elapsed is None else row_elapsed / batch_elapsed
        speedup = "-" if speedup_value is None else f"{speedup_value:.2f}x"
        row_display = "-" if row_elapsed is None else f"{row_elapsed:.3f}"
        output_lines.append(
            f"| {rows} | {row_display} | {batch_elapsed:.3f} | "
            f"{speedup} | {row_count} | {batch_count} |"
        )
        results.append(
            {
                "rows": rows,
                "dim": args.dim,
                "batch_size": args.batch_size,
                "row_by_row_s": row_elapsed,
                "batch_s": batch_elapsed,
                "speedup": speedup_value,
                "row_count": None if row_count == "-" else int(row_count),
                "batch_count": int(batch_count),
            }
        )

    markdown = "\n".join(output_lines) + "\n"
    print(markdown, end="")
    payload = {
        "benchmark": "lance_insert_many",
        "dim": args.dim,
        "batch_size": args.batch_size,
        "batch_only": args.batch_only,
        "results": results,
    }
    write_json(args.json_output, payload)
    write_text(args.markdown_output, markdown)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
