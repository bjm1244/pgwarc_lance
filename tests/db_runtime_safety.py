#!/usr/bin/env python3
"""Runtime safety checks against a disposable, already-installed PostgreSQL DB.

Run only on a test database: this writes temporary BM25 documents and Lance
datasets. The dataset files are retained for inspection after the run.
"""
from __future__ import annotations

import argparse
import json
import shlex
import sys
import subprocess
import time
import uuid
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--psql-command", default="docker compose exec -T postgres psql -U pgwarc_lance -d pgwarc_lance_test")
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    command = shlex.split(args.psql_command) + ["-X", "-q", "-At", "-v", "ON_ERROR_STOP=1", "-v", "VERBOSITY=verbose"]
    tools = Path(__file__).resolve().parents[1] / "tools"
    sys.path.insert(0, str(tools))
    import warc_importer
    import eval_quality

    suffix = uuid.uuid4().hex[:12]
    application = f"pgwarc_safety_{suffix}"
    uri = f"/tmp/pgwarc_safety_{suffix}.lance"
    denied_uri = f"/tmp/pgwarc_denied_{suffix}.lance"
    doc_id = -(int(suffix, 16) % (2**62))
    evidence: dict[str, object] = {"status": "running", "dataset": uri, "checks": {}}
    checks = evidence["checks"]

    def sql(text: str, *, timeout: float = 90) -> str:
        result = subprocess.run(command, input=text, text=True, capture_output=True, timeout=timeout)
        if result.returncode:
            raise AssertionError(f"psql failed: {result.stderr}")
        return result.stdout.strip()

    def expect(actual: str, expected: str, label: str) -> None:
        if actual != expected:
            raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")
        checks[label] = True

    writer = None
    try:
        sql(f"SELECT lance_create_table('{uri}', 4, false);")
        # This log entry was inserted before external creation, then committed.
        expect(sql(f"SELECT count(*) FROM pgwarc_lance.lance_write_log WHERE uri='{uri}' AND op='create';"), "1", "create_logged")
        sql(f"SELECT lance_insert('{uri}', 1, ARRAY[1,0,0,0]::float4[], 'committed');")
        version = sql(f"SELECT max(version) FROM lance_dataset_versions('{uri}');")
        # Document the actual transaction boundary and verify compensation.
        sql(f"BEGIN; SELECT lance_insert('{uri}', 2, ARRAY[2,0,0,0]::float4[], 'rolled-back'); ROLLBACK;")
        expect(sql(f"SELECT lance_count('{uri}');"), "2", "rollback_keeps_external_append")
        expect(sql(f"SELECT count(*) FROM pgwarc_lance.lance_write_log WHERE uri='{uri}' AND op='insert';"), "1", "rolled_back_log_absent")
        sql(f"SELECT lance_restore_version('{uri}', {int(version)});")
        expect(sql(f"SELECT lance_count('{uri}');"), "1", "restore_compensates_append")
        expect(sql(f"SELECT count(*) FROM pgwarc_lance.lance_write_log WHERE uri='{uri}' AND op='restore';"), "1", "restore_logged")

        # Reject malformed vectors before any write log or external mutation,
        # and reject malformed query vectors consistently at all entry points.
        version_before_invalid = sql(f"SELECT max(version) FROM lance_dataset_versions('{uri}');")
        log_before_invalid = sql(f"SELECT count(*) FROM pgwarc_lance.lance_write_log WHERE uri='{uri}';")
        invalid_calls = [
            f"SELECT lance_insert('{uri}',99,ARRAY['NaN',0,0,0]::float4[],'invalid');",
            f"SELECT lance_insert_many('{uri}',ARRAY[99]::bigint[],ARRAY['Infinity',0,0,0]::float4[],4,ARRAY['invalid']);",
            f"SELECT count(*) FROM lance_vector_search('{uri}',ARRAY['NaN',0,0,0]::float4[],10);",
            f"SELECT count(*) FROM hybrid_search('invalid',ARRAY['Infinity',0,0,0]::float4[],'{uri}',10);",
            f"SELECT count(*) FROM hybrid_warc_search('invalid',ARRAY['-Infinity',0,0,0]::float4[],'{uri}',10);",
        ]
        for call in invalid_calls:
            rejected_vector = subprocess.run(command,input=call,text=True,capture_output=True,timeout=30)
            if rejected_vector.returncode == 0 or "22023" not in rejected_vector.stderr or "finite vector values" not in rejected_vector.stderr:
                raise AssertionError(f"invalid vector accepted or incorrect SQLSTATE: {rejected_vector.stdout} {rejected_vector.stderr}")
        checks["nonfinite_vectors_rejected_at_all_entry_points"] = True
        expect(sql(f"SELECT max(version) FROM lance_dataset_versions('{uri}');"), version_before_invalid, "invalid_vectors_do_not_change_external_version")
        expect(sql(f"SELECT count(*) FROM pgwarc_lance.lance_write_log WHERE uri='{uri}';"), log_before_invalid, "invalid_vectors_do_not_insert_write_log")

        # Ensure a SQL permission error cannot create a dataset as a side effect.
        role = f"pgwarc_denied_{suffix}"
        sql(f"CREATE ROLE {role} NOLOGIN; GRANT EXECUTE ON FUNCTION lance_create_table(text, integer, boolean) TO {role};")
        try:
            rejected = subprocess.run(command, input=f"SET ROLE {role}; SELECT lance_create_table('{denied_uri}', 4, false);", text=True, capture_output=True, timeout=30)
            if rejected.returncode == 0 or "42501" not in rejected.stderr:
                raise AssertionError("write without log permissions was not rejected")
            # A successful Create (not Overwrite) proves the denied call did not create it.
            sql(f"SELECT lance_create_table('{denied_uri}', 4, false);")
            checks["permission_failure_has_no_dataset_side_effect"] = True
        finally:
            sql(f"DROP OWNED BY {role}; DROP ROLE {role};")

        # Generated literals must preserve hostile backslashes/quotes even if
        # the connection originally disabled standard_conforming_strings.
        hostile_text = "safe \\'; SELECT pg_sleep(30); -- shared"
        record = warc_importer.ImportRecord(
            doc_id=doc_id - 1, target_uri="https://example.test/safety",
            warc_date=None, content_type="text/plain", http_status=200,
            payload_digest=None, text=hostile_text, source_file="synthetic-safety.warc",
        )
        expect(sql("SET standard_conforming_strings=off; SELECT " + eval_quality.sql_string(hostile_text) + ";", timeout=10), hostile_text, "quality_literal_roundtrip_with_nonstandard_strings")
        for mode in ["function", "bulk", "copy"]:
            generated = warc_importer.emit_sql([record], None, 4, False, False, bm25_mode=mode)
            sql("SET standard_conforming_strings=off;" + generated, timeout=10)
            expect(sql(f"SELECT content FROM pgwarc_lance.bm25_doc WHERE doc_id={doc_id - 1};"), hostile_text, f"literal_roundtrip_{mode}")

        # Concurrent first inserts of the same document must replace postings.
        writer = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        writer.stdin.write(f"SET application_name='{application}'; BEGIN; SELECT bm25_index_document({doc_id}, 'first shared'); SELECT pg_sleep(2); COMMIT;\n")
        writer.stdin.close()
        deadline = time.monotonic() + 15
        while sql(f"SELECT count(*) FROM pg_stat_activity WHERE application_name='{application}' AND wait_event='PgSleep';") != "1":
            if time.monotonic() >= deadline or writer.poll() is not None:
                raise AssertionError("first concurrent writer did not reach synchronization point")
            time.sleep(0.05)
        sql(f"SELECT bm25_index_document({doc_id}, 'second shared');")
        writer.wait(timeout=30)
        if writer.returncode:
            raise AssertionError(f"first writer failed: {writer.stderr.read()}")
        expect(sql(f"SELECT string_agg(term, ',' ORDER BY term) FROM pgwarc_lance.bm25_term WHERE doc_id={doc_id};"), "second,shared", "concurrent_reindex_has_no_stale_postings")
        expect(sql(f"SELECT content FROM pgwarc_lance.bm25_doc WHERE doc_id={doc_id};"), "second shared", "concurrent_reindex_content_matches")

        # Scan enough vectors to exercise Lance cancellation, then reuse the same
        # backend connection. ON_ERROR_STOP=off permits the post-cancel query.
        sql(f"SELECT lance_insert_many('{uri}', ARRAY(SELECT x FROM generate_series(10,50009) x)::bigint[], ARRAY(SELECT 0::float4 FROM generate_series(1,200000)), 4, ARRAY(SELECT 'scan' FROM generate_series(1,50000)));")
        resumed = subprocess.run(command + ["-v", "ON_ERROR_STOP=0"], input=f"SET statement_timeout='10ms'; SELECT count(*) FROM lance_scan('{uri}',50001); RESET statement_timeout; SELECT 'backend-reusable';", text=True, capture_output=True, timeout=30)
        if "57014" not in resumed.stderr or "backend-reusable" not in resumed.stdout:
            raise AssertionError(f"Lance scan cancellation/reuse failed: {resumed.stdout} {resumed.stderr}")
        checks["lance_scan_timeout_and_backend_reuse"] = True
        # GUC placeholder values set before the library is first loaded must
        # not override the administrator's database-wide directory policy.
        database = sql("SELECT current_database();")
        quoted_database = '"' + database.replace('"', '""') + '"'
        original_prefix = json.loads(sql("SELECT to_json((SELECT substring(setting FROM position('=' IN setting) + 1) FROM pg_db_role_setting CROSS JOIN LATERAL unnest(setconfig) AS setting WHERE setdatabase=(SELECT oid FROM pg_database WHERE datname=current_database()) AND setrole=0 AND setting LIKE 'pgwarc_lance.allowed_uri_prefix=%'));") or "null")
        first_role = f"pgwarc_first_{suffix}"
        sql(f"CREATE ROLE {first_role} NOLOGIN; GRANT EXECUTE ON FUNCTION hello_pgwarc_lance(), lance_count(text) TO {first_role};")
        try:
            sql(f"ALTER DATABASE {quoted_database} SET pgwarc_lance.allowed_uri_prefix='/tmp';")
            first = subprocess.run(command + ["-v", "ON_ERROR_STOP=0"], input=f"SET ROLE {first_role}; SET pgwarc_lance.allowed_uri_prefix='/'; SELECT hello_pgwarc_lance(); SHOW pgwarc_lance.allowed_uri_prefix; SELECT lance_count('/var/lib/outside.lance');", text=True, capture_output=True, timeout=30)
            if "/tmp" not in first.stdout.splitlines() or "42501" not in first.stderr or "outside allowed_uri_prefix" not in first.stderr:
                raise AssertionError(f"first-load policy bypass: {first.stdout} {first.stderr}")
            checks["first_load_guc_cannot_override_database_policy"] = True
        finally:
            if original_prefix is None:
                sql(f"ALTER DATABASE {quoted_database} RESET pgwarc_lance.allowed_uri_prefix;")
            else:
                literal = original_prefix.replace("'", "''")
                sql(f"SET standard_conforming_strings=on; ALTER DATABASE {quoted_database} SET pgwarc_lance.allowed_uri_prefix='{literal}';")
            sql(f"DROP OWNED BY {first_role}; DROP ROLE {first_role};")

        evidence["status"] = "passed"
        return 0
    except Exception as exc:
        evidence["status"] = "failed"
        evidence["error"] = str(exc)
        raise
    finally:
        if writer and writer.poll() is None:
            # Terminate only the uniquely tagged test session, never other work.
            sql(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name='{application}';")
            writer.wait(timeout=10)
        sql(f"DELETE FROM pgwarc_lance.bm25_doc WHERE doc_id IN ({doc_id},{doc_id - 1}); DELETE FROM pgwarc_lance.warc_record WHERE doc_id={doc_id - 1};")
        output = json.dumps(evidence, indent=2) + "\n"
        if args.json_output:
            args.json_output.parent.mkdir(parents=True, exist_ok=True)
            args.json_output.write_text(output)
        print(output, end="")


if __name__ == "__main__":
    raise SystemExit(main())
