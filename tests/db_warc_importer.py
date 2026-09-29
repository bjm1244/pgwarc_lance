#!/usr/bin/env python3
"""Live-DB smoke test for WARC importer generated SQL."""

from __future__ import annotations

import gzip
import importlib.util
import json
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

PSQL = (
    "docker compose exec -T postgres "
    "psql -U pgwarc_lance -d pgwarc_lance_test "
    "-v ON_ERROR_STOP=1 -q -At"
)
PSQL_EXEC = PSQL + " -o /dev/null"
LANCE_URI = "/tmp/pgwarc_lance_warc_importer_db.lance"


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
    html = f"<html><body><h1>Importer DB {row}</h1><p>shared search term {row}</p></body></html>"
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
        f"WARC-Date: 2026-06-30T00:0{row}:00Z\r\n"
        f"WARC-Record-ID: <urn:uuid:00000000-0000-0000-0000-{row:012d}>\r\n"
        "Content-Type: application/http; msgtype=response\r\n"
        f"Content-Length: {len(http)}\r\n"
        "\r\n"
    ).encode("ascii")
    return headers + http + b"\r\n\r\n"


def write_warc(path: Path) -> None:
    with gzip.open(path, "wb") as stream:
        for row in range(1, 4):
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

        records = warc_importer.load_import_records(
            [warc_path], limit=None, min_text_chars=1
        )
        sql = warc_importer.emit_sql(
            records=records,
            lance_uri=LANCE_URI,
            vector_dim=4,
            overwrite_lance=False,
            include_hash_vectors=True,
            batch_size=2,
            lance_mode="overwrite",
            bm25_mode="copy",
        )
        if "-- pgwarc_lance import batch 1/2: records 1-2" not in sql:
            raise AssertionError("generated SQL missing batch 1 contract comment")
        if "-- pgwarc_lance import batch 2/2: records 3-3" not in sql:
            raise AssertionError("generated SQL missing batch 2 contract comment")

        rc = warc_importer.main(
            [
                str(warc_path),
                "--execute",
                "--psql",
                PSQL_EXEC,
                "--lance-uri",
                LANCE_URI,
                "--vector-dim",
                "4",
                "--lance-mode",
                "overwrite",
                "--batch-size",
                "2",
                "--bm25-mode",
                "copy",
                "--summary-json",
                str(summary_path),
            ]
        )
        if rc != 0:
            raise AssertionError(f"importer returned {rc}")

        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        assert_equal(str(summary["records"]), "3", "summary records")
        assert_equal(summary["lance_mode"], "overwrite", "summary lance_mode")
        assert_equal(summary["bm25_mode"], "copy", "summary bm25_mode")

    checks = run_sql(
        f"""
SELECT count(*) FROM pgwarc_lance.warc_record;
SELECT bm25_doc_count();
SELECT lance_count('{LANCE_URI}');
SELECT count(*) FROM hybrid_warc_search('shared search', ARRAY[0,0,0,0]::float4[], '{LANCE_URI}', 3)
WHERE target_uri LIKE 'https://example.test/importer-db/%';
"""
    ).splitlines()
    assert_equal(checks[0], "3", "warc_record count")
    assert_equal(checks[1], "3", "bm25_doc_count")
    assert_equal(checks[2], "3", "lance_count")
    assert_equal(checks[3], "3", "hybrid_warc_search count")
    print("db_warc_importer: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
