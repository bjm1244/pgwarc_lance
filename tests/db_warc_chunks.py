#!/usr/bin/env python3
"""Exercise chunk provenance and lifecycle against disposable PostgreSQL 16."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import check_release_artifacts
import smoke_release_artifacts
import warc_importer


def response(uri: str, date: str, text: str) -> bytes:
    body = text.encode("utf-8")
    http = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n" + body
    header = (
        "WARC/1.1\r\nWARC-Type: response\r\n"
        f"WARC-Target-URI: {uri}\r\nWARC-Date: {date}\r\n"
        f"Content-Length: {len(http)}\r\n\r\n"
    ).encode("ascii")
    return header + http + b"\r\n\r\n"


def drain_worker(container: str, uri: str, psql: list[str], script: Path) -> int:
    processed = 0
    for _ in range(100):
        pending = subprocess.run(
            psql + ["-c", "SELECT count(*) FROM pgwarc_lance.lance_outbox "
                    f"WHERE uri={warc_importer.sql_string(uri)} AND state <> 'completed';"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        if pending == "0":
            return processed
        result = subprocess.run(
            [sys.executable, str(script), "--once", "--psql", shlex.join(psql)],
            check=True, capture_output=True, text=True, timeout=90,
        )
        if json.loads(result.stdout).get("status") != "completed":
            raise AssertionError("outbox worker did not process a pending chunk job")
        processed += 1
    raise AssertionError("outbox did not drain within 100 jobs")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, required=True)
    parser.add_argument("--version", default="0.1.23")
    parser.add_argument("--postgres-image", default="postgres:16-bookworm")
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    errors = check_release_artifacts.validate_artifacts(args.dist_dir, version=args.version, pg_major=16)
    if errors:
        raise ValueError("; ".join(errors))

    suffix = uuid.uuid4().hex[:10]
    container = f"pgwarc_chunks_{suffix}"
    uri = f"/tmp/pgwarc_chunks_{suffix}.lance"
    url = f"https://example.test/archive/{suffix}"
    date = "2026-10-01T00:00:00Z"
    psql = ["docker", "exec", "-i", container, "psql", "-X", "-q", "-A", "-t",
            "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", "postgres"]
    writer_psql = ["docker", "exec", "-i", "-e", "PGOPTIONS=-c role=pgwarc_writer",
                   container, "psql", "-X", "-q", "-A", "-t", "-v",
                   "ON_ERROR_STOP=1", "-U", "postgres", "-d", "postgres"]
    report: dict[str, object] = {
        "status": "running",
        "scope": "network-isolated disposable PostgreSQL 16; synthetic WARC fixture; externally supplied fixture vectors",
        "postgres_image": args.postgres_image,
        "extension_version": args.version,
        "artifact_sha256": hashlib.sha256((args.dist_dir / "pgwarc_lance.so").read_bytes()).hexdigest(),
        "cargo_lock_sha256": hashlib.sha256((ROOT / "Cargo.lock").read_bytes()).hexdigest(),
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "checks": {},
    }
    checks = report["checks"]
    created = False

    def sql(statement: str) -> str:
        result = subprocess.run(psql, input=statement, capture_output=True, text=True, timeout=90)
        if result.returncode:
            raise AssertionError(f"PostgreSQL command failed: {result.stderr[-3000:]}")
        return result.stdout.strip()

    def expect(actual: str, expected: str, name: str) -> None:
        if actual != expected:
            raise AssertionError(f"{name}: expected {expected!r}, got {actual!r}")
        checks[name] = True

    def write_warc(path: Path, text: str) -> None:
        path.write_bytes(response(url, date, text))

    def import_chunks(path: Path, vectors: Path, summary: Path, *, writer: bool = False) -> None:
        result = warc_importer.main([
            str(path), "--execute", "--psql", shlex.join(writer_psql if writer else psql),
            "--lance-uri", uri, "--lance-mode", "outbox", "--vector-dim", "4",
            "--embedding-jsonl", str(vectors), "--chunking-strategy", "char_window",
            "--chunk-size-chars", "100", "--chunk-overlap-chars", "10",
            "--chunking-version", "char-window-v1", "--summary-json", str(summary),
            "--batch-size", "100", *( ["--skip-schema"] if writer else [] ),
        ])
        if result != 0:
            raise AssertionError(f"chunk importer returned {result}")

    def prepare_vectors(path: Path, output: Path) -> list[warc_importer.ImportRecord]:
        records = warc_importer.load_import_records(
            [path], None, 1, chunking_strategy="char_window", chunk_size_chars=100,
            chunk_overlap_chars=10, chunking_version="char-window-v1",
        )
        with output.open("w", encoding="utf-8") as stream:
            for record in records:
                words = record.text.lower().split()
                values = [float(words.count(token)) for token in ("alpha", "beta", "gamma")]
                values.append(float(len(words)))
                if not any(values):
                    values[-1] = 1.0
                norm = math.sqrt(sum(value * value for value in values))
                vector = [value / norm for value in values]
                stream.write(json.dumps({"doc_id": record.doc_id, "vector": vector}) + "\n")
        return records

    worker = ROOT / "tools" / "lance_outbox_worker.py"
    try:
        subprocess.run([
            "docker", "run", "-d", "--name", container, "--network", "none",
            "--tmpfs", "/var/lib/postgresql/data:rw,nosuid,nodev,size=268435456",
            "-e", "POSTGRES_USER=postgres", "-e", "POSTGRES_HOST_AUTH_METHOD=trust",
            args.postgres_image,
        ], check=True, capture_output=True, text=True, timeout=60)
        created = True
        smoke_release_artifacts.wait_until_ready(
            container, user="postgres", database="postgres", timeout_seconds=90,
        )
        smoke_release_artifacts.install_artifacts(
            container=container, dist_dir=args.dist_dir, version=args.version,
        )
        report["postgres_container_image_id"] = subprocess.run(
            ["docker", "image", "inspect", args.postgres_image, "--format", "{{.Id}}"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        sql("CREATE EXTENSION pgwarc_lance; "
            "ALTER DATABASE postgres SET pgwarc_lance.allowed_uri_prefix='/tmp'; "
            "ALTER DATABASE postgres SET pgwarc_lance.allow_destructive_ops='on';")
        report["postgres_version"] = sql("SHOW server_version;")
        report["installed_extension_version"] = sql(
            "SELECT extversion FROM pg_extension WHERE extname='pgwarc_lance';"
        )
        sql(f"SELECT lance_create_table({warc_importer.sql_string(uri)},4,false);")
        sql("""
CREATE ROLE pgwarc_writer NOLOGIN;
GRANT USAGE ON SCHEMA public, pgwarc_lance TO pgwarc_writer;
GRANT SELECT, INSERT, UPDATE ON pgwarc_lance.bm25_doc, pgwarc_lance.warc_record TO pgwarc_writer;
GRANT SELECT, INSERT, DELETE ON pgwarc_lance.bm25_term, pgwarc_lance.bm25_long_term TO pgwarc_writer;
GRANT INSERT ON pgwarc_lance.lance_write_log TO pgwarc_writer;
GRANT USAGE ON SEQUENCE pgwarc_lance.lance_write_log_id_seq TO pgwarc_writer;
GRANT SELECT (uri, doc_id, expected_revision, is_deleted)
  ON pgwarc_lance.lance_document_revision TO pgwarc_writer;
GRANT SELECT (uri, state) ON pgwarc_lance.lance_outbox TO pgwarc_writer;
GRANT EXECUTE ON FUNCTION public.bm25_index_document(bigint,text),
  public.lance_outbox_enqueue_revision(text,bigint[],real[],integer,text[],text[]),
  public.lance_dataset_write_lock(text),
  public.lance_document_delete(text,bigint[],text[]) TO pgwarc_writer;
""")

        args.work_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(args.work_dir, 0o700)
        fixture_root = args.work_dir / "fixture"
        fixture_root.mkdir(mode=0o700, exist_ok=True)
        embedding = fixture_root / "vectors.jsonl"
        original, update = fixture_root / "initial.warc", fixture_root / "updated.warc"
        initial_text = " ".join(("alpha beta gamma archiveword" for _ in range(90)))
        write_warc(original, initial_text)
        initial_records = prepare_vectors(original, embedding)
        replay_records = warc_importer.load_import_records(
            [original], None, 1, chunking_strategy="char_window", chunk_size_chars=100,
            chunk_overlap_chars=10, chunking_version="char-window-v1",
        )
        if [row.doc_id for row in replay_records] != [row.doc_id for row in initial_records]:
            raise AssertionError("replaying the same source changed its chunk IDs")
        report["inputs"] = {
            "initial_warc_sha256": hashlib.sha256(original.read_bytes()).hexdigest(),
            "initial_embedding_jsonl_sha256": hashlib.sha256(embedding.read_bytes()).hexdigest(),
        }
        intervals = [False] * len(" ".join(initial_text.split()))
        for record in initial_records:
            if len(record.text) > 100:
                raise AssertionError("chunk exceeded its configured character bound")
            for offset in range(record.char_start, record.char_end):
                intervals[offset] = True
        if not intervals or not all(intervals):
            raise AssertionError("chunk offsets left extracted text uncovered")
        checks["long_document_is_fully_chunked_without_truncation"] = True
        checks["chunk_ids_parent_offsets_and_version_are_stable"] = True

        initial_summary = fixture_root / "initial-summary.json"
        import_chunks(original, embedding, initial_summary)
        expect(sql(f"SELECT count(*) FROM pgwarc_lance.warc_record WHERE parent_doc_id={initial_records[0].parent_doc_id};"),
               str(len(initial_records)), "initial_chunk_metadata_count")
        expect(sql(f"SELECT count(*) FROM pgwarc_lance.lance_outbox WHERE uri={warc_importer.sql_string(uri)} AND state='pending';"),
               "1", "external_vectors_queued_by_chunk_id")
        drain_worker(container, uri, psql, worker)
        expect(sql(f"SELECT lance_count({warc_importer.sql_string(uri)});"),
               str(len(initial_records)), "external_embedding_vectors_applied")

        duplicate_summary = fixture_root / "duplicate-summary.json"
        duplicate_output = fixture_root / "duplicate.sql"
        try:
            warc_importer.main([
                str(original), str(original), "--output", str(duplicate_output),
                "--chunking-strategy", "char_window", "--chunk-size-chars", "100",
                "--chunk-overlap-chars", "10", "--chunking-version", "char-window-v1",
            ])
        except ValueError as error:
            if "duplicate imported doc_id" not in str(error):
                raise
            checks["duplicate_chunk_input_fails_before_sql"] = True
        else:
            raise AssertionError("duplicate chunk input was accepted")
        if duplicate_output.exists():
            raise AssertionError("duplicate input created an SQL artifact")

        shorter_text = "alpha beta gamma updated_marker " * 8
        write_warc(update, shorter_text)
        update_vectors = fixture_root / "update-vectors.jsonl"
        update_records = prepare_vectors(update, update_vectors)
        report["inputs"]["updated_warc_sha256"] = hashlib.sha256(update.read_bytes()).hexdigest()
        report["inputs"]["updated_embedding_jsonl_sha256"] = hashlib.sha256(update_vectors.read_bytes()).hexdigest()
        if update_records[0].parent_doc_id != initial_records[0].parent_doc_id:
            raise AssertionError("corrected capture did not retain its stable parent identity")
        update_summary = fixture_root / "update-summary.json"
        import_chunks(update, update_vectors, update_summary, writer=True)
        deleted_count = drain_worker(container, uri, psql, worker)
        expect(sql(f"SELECT lance_count({warc_importer.sql_string(uri)});"),
               str(len(update_records)), "shorter_update_removes_stale_vectors")
        expect(sql(f"SELECT count(*) FROM pgwarc_lance.warc_record WHERE parent_doc_id={update_records[0].parent_doc_id};"),
               str(len(update_records)), "shorter_update_removes_stale_metadata_and_bm25")
        expect(sql("SELECT count(*) FROM bm25_search('archiveword',1000);"),
               "0", "shorter_update_removes_stale_search_terms")
        checks["document_update_outbox_jobs_drained"] = deleted_count >= 2

        result = sql(
            f"SELECT h.doc_id || '|' || h.target_uri || '|' || h.warc_date || '|' || "
            "r.parent_doc_id || '|' || r.chunk_index || '|' || r.char_start || '|' || "
            "r.char_end || '|' || r.parent_text_len || '|' || r.chunking_version "
            f"FROM hybrid_warc_search('updated_marker',ARRAY[1,0,0,0]::real[],{warc_importer.sql_string(uri)},5) h "
            "JOIN pgwarc_lance.warc_record r ON r.doc_id=h.doc_id ORDER BY r.chunk_index LIMIT 1;"
        )
        fields = result.split("|")
        if len(fields) != 9 or fields[1] != url or fields[2] != date:
            raise AssertionError(f"search provenance was incomplete: {result!r}")
        if int(fields[5]) < 0 or int(fields[6]) <= int(fields[5]) or int(fields[7]) != len(" ".join(shorter_text.split())):
            raise AssertionError(f"search chunk location was invalid: {result!r}")
        if fields[8] != "char-window-v1":
            raise AssertionError(f"search chunk version was lost: {result!r}")
        checks["hybrid_search_returns_url_capture_and_chunk_location"] = True
        checks["unknown_pdf_page_is_not_invented"] = "pdf_page" not in result.lower()

        parent_id = update_records[0].parent_doc_id
        subprocess.run(writer_psql, input=f"""
SELECT public.lance_document_delete(
    {warc_importer.sql_string(uri)},
    array_agg(chunk.doc_id ORDER BY chunk.doc_id),
    array_agg(revision.expected_revision ORDER BY chunk.doc_id)
)
FROM pgwarc_lance.warc_record AS chunk
JOIN pgwarc_lance.lance_document_revision AS revision
  ON revision.uri={warc_importer.sql_string(uri)} AND revision.doc_id=chunk.doc_id
WHERE chunk.parent_doc_id={parent_id} AND NOT revision.is_deleted;
""", text=True, capture_output=True, check=True, timeout=90)
        drain_worker(container, uri, psql, worker)
        expect(sql(f"SELECT count(*) FROM pgwarc_lance.warc_record WHERE parent_doc_id={parent_id};"),
               "0", "explicit_parent_delete_removes_chunk_metadata")
        expect(sql(f"SELECT lance_count({warc_importer.sql_string(uri)});"),
               "0", "explicit_parent_delete_removes_chunk_vectors")
        expect(sql("SELECT count(*) FROM bm25_search('updated_marker',10);"),
               "0", "explicit_parent_delete_removes_bm25_postings")

        report["status"] = "passed"
        report["fixture"] = {
            "initial_chunks": len(initial_records),
            "updated_chunks": len(update_records),
            "stale_vectors_removed": len(initial_records) - len(update_records),
            "chunk_size_chars": 100,
            "chunk_overlap_chars": 10,
            "offset_unit": "Unicode codepoints in normalized extracted text",
            "embedding": "externally supplied normalized fixture vectors; no retrieval-quality claim",
        }
        return 0
    except Exception as error:
        report["status"] = "failed"
        report["error"] = str(error)
        raise
    finally:
        if created:
            subprocess.run(["docker", "rm", "-f", "-v", container], capture_output=True, timeout=60)
        if args.json_output:
            args.json_output.parent.mkdir(parents=True, exist_ok=True)
            args.json_output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
