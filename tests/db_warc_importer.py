#!/usr/bin/env python3
"""Live-DB smoke test for WARC importer generated SQL."""

from __future__ import annotations

import gzip
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IMPORTER_PATH = ROOT / "tools" / "warc_importer.py"
SPEC = importlib.util.spec_from_file_location("warc_importer", IMPORTER_PATH)
warc_importer = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = warc_importer
SPEC.loader.exec_module(warc_importer)

PSQL = os.environ.get("PGWARC_PSQL", (
    "docker compose exec -T postgres "
    "psql -U pgwarc_lance -d pgwarc_lance_test "
    "-v ON_ERROR_STOP=1 -q -At"
))
PSQL_EXEC = PSQL + " -o /dev/null"
LANCE_URI = os.environ.get("PGWARC_TEST_LANCE_URI", "/tmp/pgwarc_lance_warc_importer_db.lance")


def run_sql(sql: str) -> str:
    result = subprocess.run(
        PSQL,
        shell=True,
        input=sql,
        text=True,
        capture_output=True,
        cwd=ROOT,
        check=False,
    )
    if result.returncode:
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        raise SystemExit(result.returncode)
    return result.stdout.strip()


def warc_response(row: int) -> bytes:
    uri = f"https://example.test/importer-db/{row}"
    long_token = "".join(
        warc_importer.hashlib.sha256(str(index).encode()).hexdigest()
        for index in range(120)
    ) if row == 4 else ""
    html = f"<html><body><h1>Importer DB {row}</h1><p>shared search term {row} {long_token}</p></body></html>"
    http = (
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        "Content-Language: EN-us\r\n"
        "\r\n"
        f"{html}"
    ).encode("utf-8")
    headers = (
        "WARC/1.1\r\n"
        "WARC-Type: response\r\n"
        f"WARC-Target-URI: {uri}\r\n"
        f"WARC-Date: 2026-06-30T00:0{row}:00Z\r\n"
        f"WARC-Record-ID: <urn:uuid:00000000-0000-0000-0000-{row:012d}>\r\n"
        "Content-Type: application/http; msgtype=response\r\n"
        f"Content-Length: {len(http)}\r\n"
        "\r\n"
    ).encode("ascii")
    return headers + http + b"\r\n\r\n"


def write_warc(path: Path) -> None:
    with gzip.open(path, "wb") as stream:
        for row in range(1, 5):
            stream.write(warc_response(row))


def assert_equal(actual: str, expected: str, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def main() -> int:
    run_sql("DROP EXTENSION IF EXISTS pgwarc_lance CASCADE; CREATE EXTENSION pgwarc_lance;")

    with tempfile.TemporaryDirectory() as tmp:
        warc_path = Path(tmp) / "sample.warc.gz"
        summary_path = Path(tmp) / "summary.json"
        write_warc(warc_path)

        records = warc_importer.load_import_records([warc_path], limit=None, min_text_chars=1)
        long_token = "".join(
            warc_importer.hashlib.sha256(str(index).encode()).hexdigest()
            for index in range(120)
        )
        expected_mixed_scores: str | None = None
        for mode in warc_importer.BM25_MODES:
            run_sql("DROP EXTENSION IF EXISTS pgwarc_lance CASCADE; CREATE EXTENSION pgwarc_lance;")
            lance_uri = f"{LANCE_URI}-{mode}.lance"
            sql = warc_importer.emit_sql(
                records=records,
                lance_uri=lance_uri,
                vector_dim=4,
                overwrite_lance=False,
                include_hash_vectors=True,
                batch_size=2,
                lance_mode="overwrite",
                bm25_mode=mode,
            )
            if "-- pgwarc_lance import batch 1/2: records 1-2" not in sql:
                raise AssertionError(f"{mode}: generated SQL missing batch 1 contract comment")
            if "-- pgwarc_lance import batch 2/2: records 3-4" not in sql:
                raise AssertionError(f"{mode}: generated SQL missing batch 2 contract comment")

            rc = warc_importer.main(
                [
                    str(warc_path), "--execute", "--psql", PSQL_EXEC,
                    "--lance-uri", lance_uri, "--vector-dim", "4",
                    "--lance-mode", "overwrite", "--batch-size", "2",
                    "--bm25-mode", mode, "--summary-json", str(summary_path),
                ]
            )
            if rc != 0:
                raise AssertionError(f"{mode}: importer returned {rc}")

            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            assert_equal(str(summary["records"]), "4", f"{mode}: summary records")
            assert_equal(summary["lance_mode"], "overwrite", f"{mode}: summary lance_mode")
            assert_equal(summary["bm25_mode"], mode, f"{mode}: summary bm25_mode")
            assert_equal(summary["oversized_ascii_terms"], 1, f"{mode}: long term count")
            assert_equal(summary["max_oversized_ascii_term_bytes"], 7680, f"{mode}: long term size")
            checks = run_sql(
                f"""
SELECT count(*) FROM pgwarc_lance.warc_record;
SELECT bm25_doc_count();
SELECT lance_count('{lance_uri}');
SELECT count(*) FROM hybrid_warc_search('shared search', ARRAY[0,0,0,0]::float4[], '{lance_uri}', 3)
WHERE target_uri LIKE 'https://example.test/importer-db/%';
SELECT count(*) FROM pgwarc_lance.warc_record
WHERE domain = 'example.test' AND content_language = 'en-us' AND media_type = 'text/html';
SELECT count(*) FROM hybrid_warc_search_filtered(
    'shared search', ARRAY[0,0,0,0]::float4[], '{lance_uri}',
    '{{"domain":"example.test","language":"en-US","format":"text/html"}}'::jsonb, 10
);
SELECT doc_id FROM bm25_search('{long_token}', 10) ORDER BY doc_id;
SELECT json_agg(t ORDER BY doc_id) FROM bm25_search('shared {long_token}', 10) t;
"""
            ).splitlines()
            assert_equal(checks[0], "4", f"{mode}: warc_record count")
            assert_equal(checks[1], "4", f"{mode}: bm25_doc_count")
            assert_equal(checks[2], "4", f"{mode}: lance_count")
            assert_equal(checks[3], "3", f"{mode}: hybrid_warc_search count")
            assert_equal(checks[4], "4", f"{mode}: imported filter metadata count")
            assert_equal(checks[5], "4", f"{mode}: candidate-filtered search count")
            assert_equal(checks[6], str(records[-1].doc_id), f"{mode}: exact long-token search")
            if expected_mixed_scores is None:
                expected_mixed_scores = checks[7]
            else:
                assert_equal(
                    checks[7], expected_mixed_scores,
                    f"{mode}: mixed short/long BM25 scores match function mode",
                )
    print("db_warc_importer: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
