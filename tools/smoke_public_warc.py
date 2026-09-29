#!/usr/bin/env python3
"""Download and import the documented public WARC smoke sample."""

from __future__ import annotations

import argparse
import importlib.util
import sys
import tempfile
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IMPORTER_PATH = ROOT / "tools" / "warc_importer.py"
SPEC = importlib.util.spec_from_file_location("warc_importer", IMPORTER_PATH)
warc_importer = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = warc_importer
SPEC.loader.exec_module(warc_importer)

DEFAULT_URL = (
    "https://raw.githubusercontent.com/webrecorder/pywb/main/"
    "sample_archive/warcs/example.warc.gz"
)
DEFAULT_PSQL = (
    "docker compose exec -T postgres "
    "psql -U pgwarc_lance -d pgwarc_lance_test "
    "-v ON_ERROR_STOP=1 -q -At"
)
DEFAULT_EXEC_PSQL = (
    "docker compose exec -T postgres env PGOPTIONS='-c client_min_messages=warning' "
    "psql -U pgwarc_lance -d pgwarc_lance_test "
    "-v ON_ERROR_STOP=1 -q -At -o /dev/null"
)


def download(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=60) as response:
        path.write_bytes(response.read())


def run_sql(psql: str, sql: str) -> str:
    import subprocess

    result = subprocess.run(psql, shell=True, input=sql, text=True, capture_output=True)
    if result.returncode:
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        raise SystemExit(result.returncode)
    return result.stdout.strip()


def sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def sql_bigint_array(values: list[int]) -> str:
    return "ARRAY[" + ",".join(str(value) for value in values) + "]::bigint[]"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--warc-path", type=Path)
    parser.add_argument("--psql", default=DEFAULT_PSQL)
    parser.add_argument("--execute-psql", default=DEFAULT_EXEC_PSQL)
    parser.add_argument("--lance-uri", default="/tmp/pgwarc_lance_pywb_example.lance")
    parser.add_argument("--vector-dim", type=int, default=4)
    parser.add_argument("--bm25-mode", choices=warc_importer.BM25_MODES, default="bulk")
    parser.add_argument("--min-text-chars", type=int, default=1)
    return parser.parse_args()


def run(path: Path, args: argparse.Namespace) -> int:
    records = warc_importer.load_import_records(
        [path], limit=None, min_text_chars=args.min_text_chars
    )
    if not records:
        raise SystemExit("public WARC smoke sample produced no importable records")

    sql = warc_importer.emit_sql(
        records=records,
        lance_uri=args.lance_uri,
        vector_dim=args.vector_dim,
        overwrite_lance=False,
        include_hash_vectors=True,
        batch_size=100,
        lance_mode="overwrite",
        bm25_mode=args.bm25_mode,
    )
    warc_importer.execute_sql(sql, args.execute_psql)

    doc_ids = [record.doc_id for record in records]
    doc_id_array = sql_bigint_array(doc_ids)
    checks = run_sql(
        args.psql,
        f"""
SELECT count(*) FROM pgwarc_lance.warc_record WHERE doc_id = ANY({doc_id_array});
SELECT count(*) FROM pgwarc_lance.bm25_doc WHERE doc_id = ANY({doc_id_array});
SELECT lance_count({sql_string(args.lance_uri)});
SELECT count(*) FROM bm25_search('Example Domain', 5) WHERE doc_id = ANY({doc_id_array});
""",
    ).splitlines()
    expected = str(len(records))
    if checks[0] != expected:
        raise SystemExit(f"warc_record count mismatch: expected {expected}, got {checks[0]}")
    if checks[1] != expected:
        raise SystemExit(f"bm25_doc count mismatch: expected {expected}, got {checks[1]}")
    if checks[2] != expected:
        raise SystemExit(f"lance_count mismatch: expected {expected}, got {checks[2]}")
    if int(checks[3]) < 1:
        raise SystemExit("bm25_search did not return the public sample document")

    print(
        "public_warc_smoke: "
        f"records={len(records)} "
        f"bytes={path.stat().st_size} "
        f"bm25_mode={args.bm25_mode} "
        f"lance_uri={args.lance_uri}"
    )
    return 0


def main() -> int:
    args = parse_args()
    if args.vector_dim <= 0:
        raise SystemExit("--vector-dim must be positive")
    if args.min_text_chars <= 0:
        raise SystemExit("--min-text-chars must be positive")

    if args.warc_path:
        if not args.warc_path.exists():
            download(args.url, args.warc_path)
        return run(args.warc_path, args)

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "example.warc.gz"
        download(args.url, path)
        return run(path, args)


if __name__ == "__main__":
    raise SystemExit(main())
