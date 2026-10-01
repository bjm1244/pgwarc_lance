#!/usr/bin/env python3
"""Exercise idempotent ingestion and backend termination in a new disposable DB.

Requires PostgreSQL 16 binaries and CREATE DATABASE/extension privileges. No
existing database is modified. Lance directories remain for inspection.
"""
from __future__ import annotations
import argparse
import json
import shlex
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exec-prefix", default="docker compose exec -T postgres")
    parser.add_argument("--pg-bin", default="/usr/lib/postgresql/16/bin")
    parser.add_argument("--host", default="/var/run/postgresql")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--user", default="pgwarc_lance")
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    prefix = shlex.split(args.exec_prefix)
    connection = ["-h", args.host, "-p", str(args.port), "-U", args.user]
    suffix = uuid.uuid4().hex[:16]
    database = f"pgwarc_replay_{suffix}"
    uri = f"/tmp/pgwarc_replay_{suffix}.lance"
    application = f"pgwarc_replay_writer_{suffix}"
    writer_role, reader_role = f"pgwarc_writer_{suffix}", f"pgwarc_reader_{suffix}"
    roles_created = False
    command = prefix + [str(Path(args.pg_bin) / "psql")] + connection + ["-X", "-q", "-At", "-v", "ON_ERROR_STOP=1", "-d", database]
    evidence: dict[str, object] = {"status": "running", "dataset": uri, "checks": {}}
    checks = evidence["checks"]
    created, writer = False, None
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import warc_importer

    def sql(text: str) -> str:
        result = subprocess.run(command, input=text, text=True, capture_output=True, timeout=60)
        if result.returncode:
            raise AssertionError(result.stderr)
        return result.stdout.strip()

    def expect(actual: str, expected: str, name: str) -> None:
        if actual != expected:
            raise AssertionError(f"{name}: expected {expected!r}, got {actual!r}")
        checks[name] = True

    def rejected(text: str, message: str) -> None:
        result = subprocess.run(command, input=text, text=True, capture_output=True, timeout=60)
        if result.returncode == 0 or message not in result.stderr:
            raise AssertionError(f"expected {message!r}: {result.stdout} {result.stderr}")

    def write_sql(doc_id: int, value: int, label: str) -> str:
        return f"SELECT lance_upsert_many('{uri}',ARRAY[{doc_id}]::bigint[],ARRAY[{value},0,0,0]::float4[],4,ARRAY['{label}']::text[]);"

    def start_writer(text: str) -> None:
        nonlocal writer
        writer = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        writer.stdin.write(f"SET application_name='{application}'; BEGIN; {text} SELECT pg_sleep(30); COMMIT;\n")
        writer.stdin.close()
        deadline = time.monotonic() + 15
        while sql(f"SELECT count(*) FROM pg_stat_activity WHERE application_name='{application}' AND wait_event='PgSleep';") != "1":
            if time.monotonic() >= deadline or writer.poll() is not None:
                raise AssertionError("writer did not reach synchronization point")
            time.sleep(0.05)

    try:
        subprocess.run(prefix + [str(Path(args.pg_bin) / "createdb")] + connection + [database], check=True, capture_output=True, timeout=30)
        created = True
        sql("CREATE EXTENSION pgwarc_lance;")
        expect(sql("SELECT to_regclass('pgwarc_lance.bm25_term_doc_id_idx') IS NOT NULL;"), "t", "document_posting_index_installed")
        sql(f"SELECT lance_create_table('{uri}',4,false);")
        sql(f"CREATE ROLE {writer_role} NOLOGIN; CREATE ROLE {reader_role} NOLOGIN;")
        roles_created = True
        sql(f"""
            GRANT USAGE ON SCHEMA pgwarc_lance TO {writer_role}, {reader_role};
            GRANT SELECT, INSERT, UPDATE ON pgwarc_lance.bm25_doc, pgwarc_lance.warc_record TO {writer_role};
            GRANT SELECT, INSERT, DELETE ON pgwarc_lance.bm25_term TO {writer_role};
            GRANT INSERT ON pgwarc_lance.lance_write_log TO {writer_role};
            GRANT USAGE ON SEQUENCE pgwarc_lance.lance_write_log_id_seq TO {writer_role};
            GRANT EXECUTE ON FUNCTION bm25_index_document(bigint,text), lance_upsert_many(text,bigint[],real[],integer,text[]) TO {writer_role};
            GRANT SELECT ON pgwarc_lance.bm25_doc, pgwarc_lance.bm25_term, pgwarc_lance.warc_record TO {reader_role};
            GRANT EXECUTE ON FUNCTION hybrid_warc_search(text,real[],text,integer) TO {reader_role};
        """)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "replay.warc"
            with path.open("wb") as output:
                for index in range(2):
                    body = f"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\nreplay shared document {index}".encode()
                    header = f"WARC/1.1\r\nWARC-Type: response\r\nWARC-Target-URI: https://example.test/replay/{suffix}/{index}\r\nWARC-Date: 2026-10-01T00:00:00Z\r\nContent-Type: application/http\r\nContent-Length: {len(body)}\r\n\r\n"
                    output.write(header.encode() + body + b"\r\n\r\n")
            records = warc_importer.load_import_records([path], None, 1)
            importer_args = [str(path), "--execute", "--psql", shlex.join(command + ["-o", "/dev/null"]),
                             "--lance-uri", uri, "--lance-mode", "upsert", "--vector-dim", "4", "--batch-size", "1"]
            warc_importer.main(importer_args)
            warc_importer.main(importer_args)
            expect(sql(f"SELECT lance_count('{uri}');"), "2", "repeated_cli_import_keeps_two_vectors")
            expect(sql(f"SET ROLE {writer_role}; SELECT current_user = '{writer_role}' AND NOT has_schema_privilege(current_user,'pgwarc_lance','CREATE') AND NOT (SELECT rolsuper FROM pg_roles WHERE rolname=current_user);"), "t", "writer_has_no_schema_creation_or_superuser")
            writer_command = ["env", f"PGOPTIONS=-c role={writer_role}"] + command + ["-o", "/dev/null"]
            limited_args = importer_args[:]
            limited_args[limited_args.index("--psql") + 1] = shlex.join(writer_command)
            warc_importer.main(limited_args + ["--skip-schema"])
            expect(sql(f"SELECT lance_count('{uri}');"), "2", "least_privilege_writer_cli_replay_succeeds")
            expect(sql(f"SET ROLE {reader_role}; SELECT count(*) FROM hybrid_warc_search('replay',ARRAY[0,0,0,0]::float4[],'{uri}',2);"), "2", "least_privilege_reader_hybrid_search_succeeds")
            rejected(f"SET ROLE {reader_role}; SELECT lance_upsert_many('{uri}',ARRAY[1]::bigint[],ARRAY[0,0,0,0]::float4[],4,ARRAY['denied']);", "permission denied")
            rejected(f"SET ROLE {writer_role}; SELECT lance_create_table('{uri}',4,true);", "permission denied")
            checks["reader_cannot_write_and_writer_cannot_create"] = True
            expect(sql("SELECT bm25_doc_count();"), "2", "repeated_cli_import_keeps_two_bm25_docs")
            expect(sql("SELECT count(*) FROM pgwarc_lance.warc_record;"), "2", "repeated_cli_import_keeps_two_metadata_rows")
        doc_id = records[0].doc_id
        sql(write_sql(doc_id, 4, "updated"))
        expect(sql(f"SELECT label || '|' || vector[1]::text FROM lance_scan('{uri}',10) WHERE id={doc_id};"), "updated|4", "upsert_replaces_vector_and_label")
        version = sql(f"SELECT max(version) FROM lance_dataset_versions('{uri}');")
        rejected(f"SELECT lance_upsert_many('{uri}',ARRAY[{doc_id},{doc_id}]::bigint[],array_fill(0::float4,ARRAY[8]),4,ARRAY['a','b']);", "unique document IDs")
        rejected(f"SELECT lance_upsert_many('{uri}',ARRAY[{doc_id}]::bigint[],ARRAY['Infinity',0,0,0]::float4[],4,ARRAY['bad']);", "finite vector values")
        expect(sql(f"SELECT max(version) FROM lance_dataset_versions('{uri}');"), version, "invalid_source_does_not_mutate_dataset")

        # A concurrent upsert must wait for the first SQL transaction's URI lock.
        writer = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        writer.stdin.write(f"SET application_name='{application}'; BEGIN; {write_sql(doc_id, 5, 'first')} SELECT pg_sleep(2); COMMIT;\n")
        writer.stdin.close()
        deadline = time.monotonic() + 15
        while sql(f"SELECT count(*) FROM pg_stat_activity WHERE application_name='{application}' AND wait_event='PgSleep';") != "1":
            if time.monotonic() >= deadline or writer.poll() is not None:
                raise AssertionError("concurrent writer did not reach synchronization point")
            time.sleep(0.05)
        sql(write_sql(doc_id, 6, "second"))
        writer.wait(timeout=15)
        if writer.returncode:
            raise AssertionError(writer.stderr.read())
        expect(sql(f"SELECT label || '|' || vector[1]::text FROM lance_scan('{uri}',10) WHERE id={doc_id};"), "second|6", "concurrent_upsert_last_writer_wins")
        expect(sql(f"SELECT lance_count('{uri}');"), "2", "concurrent_upsert_keeps_unique_ids")

        crash_id = -int(suffix, 16) % (2**60) - 2**60
        start_writer(f"SELECT bm25_index_document({crash_id},'crashreplay'); {write_sql(crash_id, 9, 'crash')}")
        expect(sql(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name='{application}';"), "t", "uniquely_tagged_writer_terminated")
        writer.wait(timeout=15)
        expect(sql(f"SELECT count(*) FROM pgwarc_lance.bm25_doc WHERE doc_id={crash_id};"), "0", "terminated_writer_sql_rolls_back")
        expect(sql(f"SELECT lance_count('{uri}');"), "3", "terminated_writer_external_version_survives")
        sql(f"BEGIN; SELECT bm25_index_document({crash_id},'crashreplay'); {write_sql(crash_id, 9, 'recovered')} COMMIT;")
        expect(sql(f"SELECT lance_count('{uri}');"), "3", "retry_after_termination_does_not_duplicate_vectors")
        expect(sql(f"SELECT doc_id FROM hybrid_search('crashreplay',ARRAY[9,0,0,0]::float4[],'{uri}',1);"), str(crash_id), "recovered_record_retrievable")

        legacy_id = crash_id - 1
        sql(f"SELECT lance_insert_many('{uri}',ARRAY[{legacy_id},{legacy_id}]::bigint[],array_fill(1::float4,ARRAY[8]),4,ARRAY['old','duplicate']);")
        version = sql(f"SELECT max(version) FROM lance_dataset_versions('{uri}');")
        rejected(write_sql(legacy_id, 7, "bad"), "duplicate/null document IDs")
        expect(sql(f"SELECT max(version) FROM lance_dataset_versions('{uri}');"), version, "legacy_duplicates_rejected_without_mutation")
        evidence["status"] = "passed"
        return 0
    except Exception as exc:
        evidence["status"], evidence["error"] = "failed", str(exc)
        raise
    finally:
        if created:
            if writer and writer.poll() is None:
                sql(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name='{application}';")
                writer.wait(timeout=15)
            subprocess.run(prefix + [str(Path(args.pg_bin) / "dropdb")] + connection + ["--force", database], check=True, capture_output=True, timeout=30)
        if roles_created:
            admin_command = prefix + [str(Path(args.pg_bin) / "psql")] + connection + ["-X", "-q", "-v", "ON_ERROR_STOP=1", "-d", "postgres"]
            subprocess.run(admin_command, input=f"DROP ROLE {writer_role}; DROP ROLE {reader_role};", text=True, check=True, capture_output=True, timeout=30)
        text = json.dumps(evidence, indent=2) + "\n"
        if args.json_output:
            args.json_output.parent.mkdir(parents=True, exist_ok=True)
            args.json_output.write_text(text)
        print(text, end="")


if __name__ == "__main__":
    raise SystemExit(main())
