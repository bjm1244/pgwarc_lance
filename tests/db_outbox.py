#!/usr/bin/env python3
"""Verify committed-only Lance outbox delivery and PG16 crash recovery.

All PostgreSQL state and Lance files live in a generated network-isolated
container. The fixture contains no source corpus or embedding material.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import shlex
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import check_release_artifacts
import doctor_pg_lance
import smoke_release_artifacts
import warc_importer


def fixture_warc(
    uri: str,
    text: str = "outbox consistency fixture",
    payload_digest: str | None = None,
    content_type: str = "text/plain",
) -> bytes:
    body = (
        f"HTTP/1.1 200 OK\r\nContent-Type: {content_type}\r\n\r\n"
        + text + "\r\n"
    ).encode("utf-8")
    digest_header = f"WARC-Payload-Digest: {payload_digest}\r\n" if payload_digest else ""
    header = (
        "WARC/1.1\r\nWARC-Type: response\r\n"
        f"WARC-Target-URI: {uri}\r\nWARC-Date: 2026-10-28T00:00:00Z\r\n"
        f"WARC-Record-ID: <urn:uuid:{uuid.uuid4()}>\r\n"
        f"{digest_header}"
        "Content-Type: application/http; msgtype=response\r\n"
        f"Content-Length: {len(body)}\r\n\r\n"
    ).encode("ascii")
    return header + body + b"\r\n\r\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, required=True)
    parser.add_argument("--version", default=check_release_artifacts.cargo_version())
    parser.add_argument("--postgres-image", default="postgres:16")
    parser.add_argument("--postgres-data-dir", type=Path)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    errors = check_release_artifacts.validate_artifacts(args.dist_dir, version=args.version, pg_major=16)
    if errors:
        raise ValueError("; ".join(errors))

    suffix = uuid.uuid4().hex[:12]
    container = f"pgwarc_outbox_{suffix}"
    app = f"pgwarc_outbox_crash_{suffix}"
    uri = f"/tmp/pgwarc_outbox_{suffix}.lance"
    cte_uri = f"/tmp/pgwarc_outbox_cte_{suffix}.lance"
    bad_uri = f"/tmp/pgwarc_outbox_bad_{suffix}.lance"
    concurrent_uri = f"/tmp/pgwarc_outbox_concurrent_{suffix}.lance"
    doctor_uri = f"/tmp/pgwarc_doctor_{suffix}.lance"
    report: dict[str, object] = {
        "status": "running",
        "container": container,
        "version": args.version,
        "postgres_image": args.postgres_image,
        "artifact_sha256": hashlib.sha256((args.dist_dir / "pgwarc_lance.so").read_bytes()).hexdigest(),
        "cargo_lock_sha256": hashlib.sha256((Path(__file__).resolve().parents[1] / "Cargo.lock").read_bytes()).hexdigest(),
        "test_harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "scope": "isolated PostgreSQL 16 and disposable Lance datasets; synthetic importer fixture; no production data",
        "checks": {},
    }
    data_mount: list[str] = []
    if args.postgres_data_dir is not None:
        data_dir = args.postgres_data_dir.resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        if any(data_dir.iterdir()):
            raise ValueError(f"PostgreSQL data directory must be empty: {data_dir}")
        report["postgres_data_dir"] = str(data_dir)
        data_mount = [
            "--mount",
            f"type=bind,src={data_dir},dst=/var/lib/postgresql/data",
        ]
    checks = report["checks"]
    created = False
    writer: subprocess.Popen[str] | None = None
    race_processes: list[subprocess.Popen[str]] = []
    race_app_names: list[str] = []
    base_psql = ["docker", "exec", "-i", container, "psql", "-U", "postgres", "-d", "postgres"]
    sql_command = base_psql + ["-X", "-q", "-A", "-t", "-F", "|", "-v", "ON_ERROR_STOP=1", "-v", "VERBOSITY=verbose"]
    worker_script = Path(__file__).resolve().parents[1] / "tools" / "lance_outbox_worker.py"

    def run(command: list[str], *, text: str | None = None, timeout: float = 60) -> str:
        result = subprocess.run(command, input=text, text=True, capture_output=True, timeout=timeout)
        if result.returncode:
            raise AssertionError(f"command failed: {result.stderr[-3000:]}")
        return result.stdout.strip()

    def sql(statement: str, *, timeout: float = 60) -> str:
        return run(sql_command, text=statement, timeout=timeout)

    def rejected(statement: str, sqlstate: str, message: str) -> None:
        result = subprocess.run(sql_command, input=statement, text=True, capture_output=True, timeout=30)
        if result.returncode == 0 or sqlstate not in result.stderr or message not in result.stderr:
            raise AssertionError(f"expected SQLSTATE {sqlstate} / {message}: {result.stdout} {result.stderr}")

    def expect(actual: str, expected: str, name: str) -> None:
        if actual != expected:
            raise AssertionError(f"{name}: expected {expected!r}, got {actual!r}")
        checks[name] = True

    def worker_once(role: str | None = None) -> dict[str, object] | None:
        worker_psql = base_psql
        if role:
            worker_psql = [
                "docker", "exec", "-i", "-e", f"PGOPTIONS=-c role={role}", container,
                "psql", "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1",
                "-U", "postgres", "-d", "postgres",
            ]
        result = subprocess.run(
            [sys.executable, str(worker_script), "--once", "--psql", shlex.join(worker_psql),
             "--statement-timeout-seconds", "60"],
            text=True, capture_output=True, timeout=90,
        )
        if result.returncode:
            raise AssertionError(f"outbox worker failed: {result.stdout} {result.stderr}")
        return json.loads(result.stdout)

    def wait_for_sleep(application: str, process: subprocess.Popen[str], timeout: float = 30) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if sql(
                "SELECT count(*) FROM pg_stat_activity "
                f"WHERE application_name='{application}' AND wait_event='PgSleep';"
            ) == "1":
                return
            if process.poll() is not None:
                raise AssertionError(f"tagged worker exited before synchronization: {process.returncode}")
            time.sleep(0.05)
        raise AssertionError("tagged worker did not reach its SQL synchronization point")

    def begin_paused_delivery(application: str) -> subprocess.Popen[str]:
        worker_psql = [
            "docker", "exec", "-i", "-e", f"PGAPPNAME={application}", container,
            "psql", "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1",
            "-U", "postgres", "-d", "postgres",
        ]
        process = subprocess.Popen(
            [sys.executable, str(worker_script), "--once", "--psql", shlex.join(worker_psql),
             "--statement-timeout-seconds", "60"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        wait_for_sleep(application, process)
        return process

    def install_ack_pause() -> None:
        sql("""
            CREATE FUNCTION public.issue5_pause_outbox_ack() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.state = 'completed' THEN PERFORM pg_sleep(60); END IF;
                RETURN NEW;
            END $$;
            CREATE TRIGGER issue5_pause_outbox_ack
                BEFORE UPDATE OF state ON pgwarc_lance.lance_outbox
                FOR EACH ROW EXECUTE FUNCTION public.issue5_pause_outbox_ack();
        """)

    def remove_ack_pause() -> None:
        sql("DROP TRIGGER issue5_pause_outbox_ack ON pgwarc_lance.lance_outbox; DROP FUNCTION public.issue5_pause_outbox_ack();")

    try:
        run([
            "docker", "run", "-d", "--name", container, "--network", "none",
            "--cpus", "2", "--memory", "3g", *data_mount,
            "-e", "POSTGRES_HOST_AUTH_METHOD=trust",
            args.postgres_image,
        ])
        created = True
        smoke_release_artifacts.wait_until_ready(container=container, user="postgres", database="postgres", timeout_seconds=120)
        smoke_release_artifacts.install_artifacts(container=container, dist_dir=args.dist_dir, version=args.version)
        sql("CREATE EXTENSION pgwarc_lance;")
        expect(sql("SELECT extversion FROM pg_extension WHERE extname='pgwarc_lance';"), args.version, "fresh_pg16_extension_install")
        report["postgres_version"] = sql("SHOW server_version;")
        expect(
            sql("SELECT count(*)=4 FROM pg_proc WHERE proname IN ('lance_outbox_enqueue','lance_outbox_enqueue_revision','lance_outbox_process_next','lance_document_delete') "
                "AND prosecdef AND proconfig @> ARRAY['search_path=pg_catalog, pg_temp']::text[];"),
            "t",
            "outbox_functions_use_locked_security_definer_search_path",
        )
        sql("""
            CREATE ROLE pgwarc_outbox_ingest NOLOGIN;
            CREATE ROLE pgwarc_outbox_worker NOLOGIN;
            CREATE ROLE pgwarc_outbox_unprivileged NOLOGIN;
            GRANT USAGE ON SCHEMA public, pgwarc_lance
                TO pgwarc_outbox_ingest, pgwarc_outbox_worker, pgwarc_outbox_unprivileged;
            GRANT EXECUTE ON FUNCTION public.lance_outbox_enqueue(text,bigint[],real[],integer,text[])
                TO pgwarc_outbox_ingest;
            GRANT EXECUTE ON FUNCTION public.lance_outbox_enqueue_revision(text,bigint[],real[],integer,text[],text[])
                TO pgwarc_outbox_ingest;
            GRANT EXECUTE ON FUNCTION public.lance_document_delete(text,bigint[],text[]),
                public.lance_dataset_write_lock(text) TO pgwarc_outbox_ingest;
            GRANT SELECT ON pgwarc_lance.lance_outbox_status,
                pgwarc_lance.lance_outbox_failures TO pgwarc_outbox_worker;
            GRANT EXECUTE ON FUNCTION public.lance_outbox_process_next() TO pgwarc_outbox_worker;
        """)
        rejected(
            "SET ROLE pgwarc_outbox_unprivileged; SELECT lance_outbox_enqueue('/tmp/denied.lance',"
            "ARRAY[-810000]::bigint[],ARRAY[1,0,0,0]::real[],4,ARRAY['denied']::text[]);",
            "42501",
            "permission denied",
        )
        checks["outbox_enqueue_is_not_publicly_executable"] = True
        rejected(
            "SET ROLE pgwarc_outbox_unprivileged; SELECT * FROM lance_outbox_process_next();",
            "42501",
            "permission denied",
        )
        checks["outbox_worker_is_not_publicly_executable"] = True

        # A data-modifying CTE tries to drain its own uncommitted enqueue. The
        # worker must exclude the current XID, and the job commits still pending.
        sql(f"SELECT lance_create_table('{uri}',4,false);")
        sql(f"SELECT lance_create_table('{cte_uri}',4,false);")
        rejected(
            f"SELECT lance_document_delete('{uri}',ARRAY[-839099]::bigint[],"
            + "ARRAY['" + "e" * 64 + "']::text[]);",
            "55000",
            "has no tracked revision",
        )
        checks["delete_requires_a_current_revision_mapping"] = True
        cte = f"""WITH enqueued AS MATERIALIZED (
    SELECT lance_outbox_enqueue('{cte_uri}',ARRAY[-810001]::bigint[],
        ARRAY[1,0,0,0]::real[],4,ARRAY['uncommitted']::text[]) AS id
), processed AS MATERIALIZED (
    SELECT count(*) AS n FROM enqueued CROSS JOIN LATERAL lance_outbox_process_next()
)
SELECT enqueued.id || '|' || processed.n FROM enqueued CROSS JOIN processed;"""
        cte_result = sql(cte)
        cte_id, processed_count = cte_result.split("|")
        expect(processed_count, "0", "same_statement_uncommitted_job_not_applied")
        expect(sql(f"SELECT state||'|'||attempt_count FROM pgwarc_lance.lance_outbox WHERE id={cte_id};"), "pending|0", "same_statement_job_commits_pending")
        expect(sql(f"SELECT lance_count('{cte_uri}');"), "0", "same_statement_never_mutates_lance")
        # Rollback of a standalone enqueue removes only SQL state and cannot
        # create an external vector row.
        rollback_id = sql(
            f"BEGIN; SELECT lance_outbox_enqueue('{cte_uri}',ARRAY[-810002]::bigint[],"
            "ARRAY[2,0,0,0]::real[],4,ARRAY['rollback']::text[]); ROLLBACK;"
        )
        expect(sql(f"SELECT count(*) FROM pgwarc_lance.lance_outbox WHERE id={rollback_id};"), "0", "sql_rollback_removes_uncommitted_outbox")
        expect(sql(f"SELECT lance_count('{cte_uri}');"), "0", "sql_rollback_has_no_lance_write")
        # Explicit transaction workers are rejected, even when older committed
        # work is available, so a caller cannot ack a write with its own rollback.
        rejected(
            "BEGIN; SELECT * FROM lance_outbox_process_next(); ROLLBACK;",
            "25001",
            "must run as a standalone statement",
        )
        checks["worker_requires_standalone_transaction"] = True
        # Clear the CTE test event by applying it as a separate autocommit worker.
        cte_delivery = worker_once("pgwarc_outbox_worker")
        assert cte_delivery is not None
        expect(str(cte_delivery["status"]), "completed", "committed_event_delivered_by_worker")
        expect(sql(f"SELECT lance_count('{cte_uri}');"), "1", "committed_event_materialized_once")
        role_job_id = sql(
            f"SET ROLE pgwarc_outbox_ingest; SELECT lance_outbox_enqueue('{uri}',"
            "ARRAY[-810099]::bigint[],ARRAY[9,0,0,0]::real[],4,ARRAY['least-privilege']::text[]); RESET ROLE;"
        )
        expect(sql(f"SELECT state FROM pgwarc_lance.lance_outbox WHERE id={role_job_id};"), "pending", "ingest_role_can_enqueue_committed_job")
        rejected(
            "SET ROLE pgwarc_outbox_ingest; SELECT count(*) FROM pgwarc_lance.lance_outbox;",
            "42501",
            "permission denied",
        )
        checks["ingest_role_cannot_read_vector_payloads"] = True
        rejected(
            "SET ROLE pgwarc_outbox_worker; SELECT count(*) FROM pgwarc_lance.lance_outbox;",
            "42501",
            "permission denied",
        )
        checks["worker_role_cannot_read_vector_payload_table_directly"] = True
        role_delivery = worker_once("pgwarc_outbox_worker")
        assert role_delivery is not None
        expect(str(role_delivery["outbox_id"]), role_job_id, "least_privilege_worker_claims_oldest_job")
        expect(sql(f"SELECT id||'|'||label FROM lance_scan('{uri}',10) WHERE id=-810099;"), "-810099|least-privilege", "least_privilege_worker_applies_vector")

        # Fail the outbox insert in the actual importer chunk transaction. The
        # error must roll back metadata, BM25 and the event without a Lance IO.
        rollback_uri = f"/tmp/pgwarc_outbox_rollback_{suffix}.lance"
        rollback_warc_uri = f"https://example.test/outbox-rollback/{suffix}"
        sql(f"SELECT lance_create_table('{rollback_uri}',4,false);")
        sql("""
            CREATE FUNCTION public.issue5_reject_outbox_insert() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'issue5 injected outbox insert failure';
            END $$;
            CREATE TRIGGER issue5_reject_outbox_insert
                BEFORE INSERT ON pgwarc_lance.lance_outbox
                FOR EACH ROW EXECUTE FUNCTION public.issue5_reject_outbox_insert();
        """)
        with tempfile.TemporaryDirectory(prefix="pgwarc-outbox-rollback-fixture-") as tmp:
            rollback_warc = Path(tmp) / "rollback.warc"
            rollback_fixture = fixture_warc(rollback_warc_uri)
            rollback_warc.write_bytes(rollback_fixture)
            rollback_record = next(warc_importer.iter_import_records([rollback_warc], None, 1))
            try:
                warc_importer.main([
                    str(rollback_warc), "--execute", "--skip-schema", "--psql", shlex.join(sql_command),
                    "--lance-uri", rollback_uri, "--lance-mode", "outbox", "--vector-dim", "4",
                ])
            except SystemExit as exc:
                if exc.code == 0:
                    raise AssertionError("injected outbox insert failure returned success")
            else:
                raise AssertionError("injected outbox insert failure did not stop importer SQL")
            report["rollback_fixture_sha256"] = hashlib.sha256(rollback_fixture).hexdigest()
        sql("DROP TRIGGER issue5_reject_outbox_insert ON pgwarc_lance.lance_outbox; DROP FUNCTION public.issue5_reject_outbox_insert();")
        expect(sql(f"SELECT count(*) FROM pgwarc_lance.warc_record WHERE doc_id={rollback_record.doc_id};"), "0", "importer_error_rolls_back_warc_metadata")
        expect(sql(f"SELECT count(*) FROM pgwarc_lance.bm25_doc WHERE doc_id={rollback_record.doc_id};"), "0", "importer_error_rolls_back_bm25_document")
        expect(sql(f"SELECT count(*) FROM pgwarc_lance.bm25_term WHERE doc_id={rollback_record.doc_id};"), "0", "importer_error_rolls_back_bm25_postings")
        expect(sql(f"SELECT count(*) FROM pgwarc_lance.lance_outbox WHERE ids=ARRAY[{rollback_record.doc_id}]::bigint[];"), "0", "importer_error_rolls_back_outbox_job")
        expect(sql(f"SELECT lance_count('{rollback_uri}');"), "0", "importer_error_performs_no_lance_write")

        # Exercise the actual importer write path: SQL/BM25/WARC commit first,
        # then the worker applies the vector and records its exact Lance version.
        write_log_before = int(sql(
            f"SELECT count(*) FROM pgwarc_lance.lance_write_log WHERE uri='{uri}' AND op='outbox_upsert_many';"
        ))
        warc_uri = f"https://example.test/outbox/{suffix}"
        with tempfile.TemporaryDirectory(prefix="pgwarc-outbox-fixture-") as tmp:
            warc_path = Path(tmp) / "fixture.warc"
            summary_path = Path(tmp) / "summary.json"
            fixture = fixture_warc(warc_uri)
            warc_path.write_bytes(fixture)
            report["import_fixture_sha256"] = hashlib.sha256(fixture).hexdigest()
            imported = next(warc_importer.iter_import_records([warc_path], None, 1))
            warc_importer.main([
                str(warc_path), "--execute", "--skip-schema", "--psql", shlex.join(sql_command),
                "--lance-uri", uri, "--lance-mode", "outbox", "--vector-dim", "4",
                "--summary-json", str(summary_path),
            ])
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        doc_id = imported.doc_id
        expect(summary["lance_index_consistency"], "eventual_after_commit", "importer_reports_eventual_contract")
        expect(sql(f"SELECT state||'|'||attempt_count FROM pgwarc_lance.lance_outbox WHERE ids=ARRAY[{doc_id}]::bigint[];"), "pending|0", "imported_vector_job_committed_pending")
        expect(sql(f"SELECT count(*) FROM pgwarc_lance.warc_record WHERE doc_id={doc_id};"), "1", "warc_metadata_commits_with_job")
        bm25_before = sql(f"SELECT string_agg(doc_id::text||':'||score::text,',' ORDER BY doc_id) FROM bm25_search('outbox',10) WHERE doc_id={doc_id};")
        expect(bm25_before.split(":", 1)[0], str(doc_id), "bm25_visible_before_vector_delivery")
        expect(sql(f"SELECT lance_count('{uri}');"), "1", "lance_still_lags_committed_bm25")
        imported_event_id = sql(f"SELECT id FROM pgwarc_lance.lance_outbox WHERE ids=ARRAY[{doc_id}]::bigint[];")
        time.sleep(0.05)
        status = sql(
            f"SELECT pending_jobs||'|'||ready_jobs||'|'||failed_jobs||'|'||"
            f"(oldest_unfinished_ms>=25)::text FROM pgwarc_lance.lance_outbox_status WHERE uri='{uri}';"
        )
        expect(status, "1|1|0|true", "pending_delay_and_failure_metrics_visible")
        rejected(
            f"SELECT lance_upsert_many('{uri}',ARRAY[-810003]::bigint[],"
            "ARRAY[3,0,0,0]::real[],4,ARRAY['mixed-writer']::text[]);",
            "55000",
            "unfinished Lance outbox work",
        )
        checks["direct_writer_cannot_overtake_pending_outbox"] = True
        delivery = worker_once()
        assert delivery is not None
        expect(str(delivery["status"]), "completed", "worker_returns_completed_status")
        version = int(delivery["applied_version"])
        expect(sql(f"SELECT state||'|'||applied_version::text||'|'||(completed_at IS NOT NULL)::text FROM pgwarc_lance.lance_outbox WHERE id={imported_event_id};"), f"completed|{version}|true", "completion_persists_applied_version")
        expect(sql(f"SELECT cardinality(ids)||'|'||cardinality(flat_vectors)||'|'||cardinality(labels) FROM pgwarc_lance.lance_outbox WHERE id={imported_event_id};"), "0|0|0", "completed_event_releases_vector_payload")
        expect(sql(f"SELECT id||'|'||label FROM lance_scan('{uri}',10) WHERE id={doc_id};"), f"{doc_id}|{warc_uri}", "vector_visible_after_outbox_completion")
        expected_revision = warc_importer.record_revision(imported)
        expect(
            sql(f"SELECT revision FROM lance_scan_document_state('{uri}',10) WHERE doc_id={doc_id};"),
            expected_revision,
            "lance_persists_import_document_revision",
        )
        expect(
            sql(f"SELECT expected_revision FROM pgwarc_lance.lance_document_revision WHERE uri='{uri}' AND doc_id={doc_id};"),
            expected_revision,
            "postgres_persists_uri_document_revision_mapping",
        )
        bm25_after = sql(f"SELECT string_agg(doc_id::text||':'||score::text,',' ORDER BY doc_id) FROM bm25_search('outbox',10) WHERE doc_id={doc_id};")
        expect(bm25_after, bm25_before, "same_fixture_bm25_fingerprint_unchanged_after_vector_delivery")
        expect(sql(f"SELECT max(latest_applied_version)||'|'||bool_or(last_completed_at IS NOT NULL)::text FROM pgwarc_lance.lance_outbox_status WHERE uri='{uri}';"), f"{version}|true", "dataset_status_reports_version_and_completion")
        if int(delivery["queue_lag_ms"]) < 25:
            raise AssertionError("worker queue delay did not include the controlled enqueue wait")
        checks["worker_reports_queue_delay"] = True
        expect(
            sql(f"SELECT count(*) FROM pgwarc_lance.lance_write_log WHERE uri='{uri}' AND op='outbox_upsert_many';"),
            str(write_log_before + 1),
            "successful_delivery_logged",
        )

        # Delete is committed as a tombstone plus an ordered vector delete.
        # Search hides the vector immediately, even while the Lance write is pending.
        revision_v1 = warc_importer.record_revision(imported)
        vector_v1 = warc_importer.hash_embedding(imported.text, 4)
        vector_v1_sql = warc_importer.sql_vector(vector_v1)

        def queue_revisioned_record(record, revision: str, vector: list[float]) -> str:
            return f"""BEGIN;
SELECT lance_dataset_write_lock('{uri}');
INSERT INTO pgwarc_lance.warc_record
    (doc_id,target_uri,warc_date,content_type,http_status,payload_digest,text_len,source_file)
VALUES ({record.doc_id},{warc_importer.sql_string(record.target_uri)},
    {warc_importer.sql_timestamp(record.warc_date)},{warc_importer.sql_string(record.content_type)},
    {warc_importer.sql_int(record.http_status)},{warc_importer.sql_string(record.payload_digest)},
    {len(record.text)},{warc_importer.sql_string(record.source_file)})
ON CONFLICT (doc_id) DO UPDATE SET target_uri=EXCLUDED.target_uri,
    warc_date=EXCLUDED.warc_date,content_type=EXCLUDED.content_type,
    http_status=EXCLUDED.http_status,payload_digest=EXCLUDED.payload_digest,
    text_len=EXCLUDED.text_len,source_file=EXCLUDED.source_file,imported_at=now();
SELECT bm25_index_document({record.doc_id},{warc_importer.sql_string(record.text)});
SELECT lance_outbox_enqueue_revision('{uri}',ARRAY[{record.doc_id}]::bigint[],
    {warc_importer.sql_vector(vector)},4,
    ARRAY[{warc_importer.sql_string(record.target_uri)}]::text[],
    ARRAY['{revision}']::text[]);
COMMIT;"""

        before_delete_version = int(sql(
            f"SELECT max(version) FROM lance_dataset_versions('{uri}');"
        ))
        rejected(
            "SET pgwarc_lance.allow_destructive_ops=off; "
            f"SELECT lance_document_delete('{uri}',ARRAY[{doc_id}]::bigint[],"
            f"ARRAY['{revision_v1}']::text[]);",
            "42501",
            "disabled by pgwarc_lance.allow_destructive_ops = off",
        )
        checks["delete_obeys_destructive_operation_guard"] = True
        delete_event_id = sql(
            f"BEGIN; SELECT lance_document_delete('{uri}',ARRAY[{doc_id}]::bigint[],"
            f"ARRAY['{revision_v1}']::text[]); COMMIT;"
        )
        expect(
            sql(f"SELECT operation||'|'||state FROM pgwarc_lance.lance_outbox WHERE id={delete_event_id};"),
            "delete|pending",
            "delete_commits_as_pending_ordered_event",
        )
        expect(
            sql(f"SELECT pending_delete_jobs||'|'||failed_delete_jobs FROM pgwarc_lance.lance_outbox_status WHERE uri='{uri}';"),
            "1|0",
            "outbox_status_reports_pending_delete_work",
        )
        expect(
            sql(f"SELECT count(*) FROM pgwarc_lance.warc_record WHERE doc_id={doc_id};"),
            "0",
            "delete_removes_warc_metadata_at_sql_commit",
        )
        expect(
            sql(f"SELECT count(*) FROM pgwarc_lance.bm25_doc WHERE doc_id={doc_id};"),
            "0",
            "delete_removes_bm25_document_and_cascades_postings",
        )
        expect(
            sql(f"SELECT is_deleted::text FROM pgwarc_lance.lance_document_revision WHERE uri='{uri}' AND doc_id={doc_id};"),
            "true",
            "delete_persists_document_tombstone",
        )
        expect(
            sql(f"SELECT count(*) FROM bm25_search('outbox',10) WHERE doc_id={doc_id};"),
            "0",
            "deleted_document_is_absent_from_bm25_search",
        )
        expect(
            sql(f"SELECT count(*) FROM lance_vector_search('{uri}',{vector_v1_sql},10) WHERE id={doc_id};"),
            "0",
            "tombstone_hides_vector_before_async_delete_delivery",
        )
        expect(
            sql(f"SELECT count(*) FROM hybrid_search('outbox',{vector_v1_sql},'{uri}',10) WHERE doc_id={doc_id};"),
            "0",
            "tombstone_hides_document_from_hybrid_search",
        )
        expect(
            sql(f"SELECT count(*) FROM lance_scan('{uri}',100) WHERE id={doc_id};"),
            "1",
            "pending_delete_retains_physical_vector_until_worker",
        )
        delete_doctor = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parents[1] / "tools" / "doctor_pg_lance.py"),
             "--uri", uri, "--psql", shlex.join(sql_command), "--max-rows", "20"],
            text=True, capture_output=True, timeout=90,
        )
        if delete_doctor.returncode != 1:
            raise AssertionError(f"doctor did not expose a pending tombstone vector: {delete_doctor.stderr[-2000:]}")
        delete_doctor_report = json.loads(delete_doctor.stdout)
        expect(str(delete_doctor_report["findings"]["deleted_vector_ids"]), "1", "doctor_classifies_pending_delete_vector")
        expect(str(delete_doctor_report["findings"]["orphan_lance_ids"]), "1", "doctor_preserves_unrelated_untracked_vector_finding")
        expect(
            ",".join(map(str, delete_doctor_report["sample_doc_ids"]["orphan_lance"])),
            "-810099",
            "doctor_does_not_misclassify_tombstone_as_orphan",
        )

        # The Lance delete commits before PostgreSQL acknowledges the event.
        # Inject a later SQL failure, then retry the same delete idempotently.
        sql("""
            CREATE FUNCTION public.issue8_fail_delete_ack() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.op = 'outbox_delete' THEN
                    RAISE EXCEPTION 'issue8 injected delete acknowledgment failure';
                END IF;
                RETURN NEW;
            END $$;
            CREATE TRIGGER issue8_fail_delete_ack
                BEFORE INSERT ON pgwarc_lance.lance_write_log
                FOR EACH ROW EXECUTE FUNCTION public.issue8_fail_delete_ack();
        """)
        delete_ack_failure = subprocess.run(
            sql_command, input="SELECT * FROM lance_outbox_process_next();",
            text=True, capture_output=True, timeout=90,
        )
        if (delete_ack_failure.returncode == 0
                or "P0001" not in delete_ack_failure.stderr
                or "issue8 injected delete acknowledgment failure" not in delete_ack_failure.stderr):
            raise AssertionError(
                "injected delete acknowledgment failure was not observed: "
                f"{delete_ack_failure.stdout} {delete_ack_failure.stderr}"
            )
        checks["delete_external_commit_before_ack_failure_injected"] = True
        expect(
            sql(f"SELECT count(*) FROM lance_scan('{uri}',100) WHERE id={doc_id};"),
            "0",
            "failed_ack_leaves_external_delete_committed",
        )
        expect(
            sql(f"SELECT state||'|'||attempt_count::text FROM pgwarc_lance.lance_outbox WHERE id={delete_event_id};"),
            "pending|0",
            "failed_ack_keeps_delete_event_retryable",
        )
        sql("DROP TRIGGER issue8_fail_delete_ack ON pgwarc_lance.lance_write_log; DROP FUNCTION public.issue8_fail_delete_ack();")
        delete_replay = worker_once()
        assert delete_replay is not None
        expect(str(delete_replay["outbox_id"]), delete_event_id, "retry_replays_same_delete_event")
        expect(str(delete_replay["status"]), "completed", "delete_retry_completes_after_external_write")
        expect(
            sql(f"SELECT state||'|'||cardinality(ids)::text FROM pgwarc_lance.lance_outbox WHERE id={delete_event_id};"),
            "completed|0",
            "completed_delete_releases_ids_but_keeps_event_history",
        )
        expect(
            sql(f"SELECT count(*) FROM lance_dataset_versions('{uri}') WHERE version={before_delete_version};"),
            "1",
            "delete_preserves_previous_dataset_version",
        )
        delete_jobs_before_repeat = sql(
            f"SELECT count(*) FROM pgwarc_lance.lance_outbox WHERE uri='{uri}' AND operation='delete';"
        )
        expect(
            sql(f"SELECT lance_document_delete('{uri}',ARRAY[{doc_id}]::bigint[],ARRAY['{revision_v1}']::text[]);"),
            "0",
            "repeated_delete_of_same_tombstone_is_idempotent",
        )
        expect(
            sql(f"SELECT count(*) FROM pgwarc_lance.lance_outbox WHERE uri='{uri}' AND operation='delete';"),
            delete_jobs_before_repeat,
            "idempotent_delete_does_not_duplicate_outbox_jobs",
        )

        # Restoring the old Lance version alone must not undo PostgreSQL's
        # committed tombstone or expose the old vector through search.
        sql(f"SELECT lance_restore_version('{uri}',{before_delete_version});")
        expect(sql(f"SELECT count(*) FROM lance_scan('{uri}',100) WHERE id={doc_id};"), "1", "explicit_restore_repromotes_old_physical_row")
        expect(sql(f"SELECT count(*) FROM lance_vector_search('{uri}',{vector_v1_sql},10) WHERE id={doc_id};"), "0", "restored_old_vector_remains_hidden_by_tombstone")
        recreated = dataclasses.replace(
            imported,
            text=imported.text + " recreated",
            payload_digest="sha1:issue8-recreated",
        )
        revision_v2 = warc_importer.record_revision(recreated)
        vector_v2 = [0.0, 1.0, 0.0, 0.0]
        sql(queue_revisioned_record(recreated, revision_v2, vector_v2))
        expect(
            sql(f"SELECT is_deleted::text FROM pgwarc_lance.lance_document_revision WHERE uri='{uri}' AND doc_id={doc_id};"),
            "false",
            "revisioned_recreation_clears_tombstone",
        )
        rejected(
            f"SELECT lance_document_delete('{uri}',ARRAY[{doc_id}]::bigint[],"
            f"ARRAY['{revision_v1}']::text[]);",
            "55000",
            "revision changed before delete",
        )
        checks["stale_revision_cannot_delete_recreated_document"] = True
        expect(
            sql(f"SELECT count(*) FROM lance_vector_search('{uri}',{warc_importer.sql_vector(vector_v2)},10) WHERE id={doc_id};"),
            "0",
            "stale_predelete_revision_is_hidden_during_recreation",
        )
        recreated_delivery = worker_once()
        assert recreated_delivery is not None
        expect(str(recreated_delivery["status"]), "completed", "recreated_vector_delivery_completes")
        expect(sql(f"SELECT count(*)||'|'||count(DISTINCT id) FROM lance_scan('{uri}',100) WHERE id={doc_id};"), "1|1", "recreation_keeps_one_vector_row")
        expect(sql(f"SELECT count(*) FROM pgwarc_lance.warc_record WHERE doc_id={doc_id};"), "1", "recreation_keeps_one_metadata_row")
        expect(sql(f"SELECT count(*) FROM pgwarc_lance.bm25_doc WHERE doc_id={doc_id};"), "1", "recreation_keeps_one_bm25_document")
        expect(sql(f"SELECT revision FROM lance_scan_document_state('{uri}',100) WHERE doc_id={doc_id};"), revision_v2, "recreation_persists_new_revision")
        rejected(
            f"SELECT lance_upsert_many('{uri}',ARRAY[{doc_id}]::bigint[],"
            "ARRAY[0,0,0,1]::real[],4,ARRAY['unversioned-update']::text[]);",
            "55000",
            "revision-tracked document IDs require revision-aware outbox operations",
        )
        rejected(
            f"SELECT lance_insert('{uri}',{doc_id},ARRAY[0,0,0,1]::real[],'unversioned-insert');",
            "55000",
            "revision-tracked document IDs require revision-aware outbox operations",
        )
        rejected(
            f"SELECT lance_insert_many('{uri}',ARRAY[{doc_id}]::bigint[],"
            "ARRAY[0,0,0,1]::real[],4,ARRAY['unversioned-insert-many']::text[]);",
            "55000",
            "revision-tracked document IDs require revision-aware outbox operations",
        )
        rejected(
            f"SELECT lance_outbox_enqueue('{uri}',ARRAY[{doc_id}]::bigint[],"
            "ARRAY[0,0,0,1]::real[],4,ARRAY['unversioned-outbox']::text[]);",
            "55000",
            "revision-tracked document IDs require revision-aware outbox operations",
        )
        checks["unversioned_direct_upsert_cannot_bypass_revision_mapping"] = True

        # Competing delete and upsert transactions take the same URI lock before
        # metadata writes. The later upsert is ordered after the delete and wins.
        race_delete_app = f"pgwarc_issue8_delete_{suffix}"
        race_upsert_app = f"pgwarc_issue8_upsert_{suffix}"
        race_app_names.extend([race_delete_app, race_upsert_app])
        race_base_id = int(sql("SELECT COALESCE(max(id),0) FROM pgwarc_lance.lance_outbox;"))
        delete_sql = f"BEGIN; SELECT lance_document_delete('{uri}',ARRAY[{doc_id}]::bigint[],ARRAY['{revision_v2}']::text[]); SELECT pg_sleep(1.2); COMMIT;"
        delete_process = subprocess.Popen(
            ["docker", "exec", "-i", "-e", f"PGAPPNAME={race_delete_app}", container,
             "psql", "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", "postgres"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        race_processes.append(delete_process)
        delete_process.stdin.write(delete_sql)
        delete_process.stdin.close()
        delete_process.stdin = None
        wait_for_sleep(race_delete_app, delete_process)
        race_record = dataclasses.replace(
            recreated,
            text=recreated.text + " concurrent",
            payload_digest="sha1:issue8-concurrent",
        )
        race_revision = warc_importer.record_revision(race_record)
        race_write_sql = queue_revisioned_record(race_record, race_revision, [0.0, 0.0, 1.0, 0.0])
        upsert_process = subprocess.Popen(
            ["docker", "exec", "-i", "-e", f"PGAPPNAME={race_upsert_app}", container,
             "psql", "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", "postgres"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        race_processes.append(upsert_process)
        upsert_process.stdin.write(race_write_sql)
        upsert_process.stdin.close()
        upsert_process.stdin = None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if sql(f"SELECT count(*) FROM pg_stat_activity WHERE application_name='{race_upsert_app}' AND wait_event_type='Lock';") == "1":
                break
            if upsert_process.poll() is not None:
                raise AssertionError("concurrent upsert completed before acquiring the delete-held URI lock")
            time.sleep(0.05)
        else:
            raise AssertionError("concurrent upsert did not block on the URI writer lock")
        expect(sql(f"SELECT payload_digest FROM pgwarc_lance.warc_record WHERE doc_id={doc_id};"), "sha1:issue8-recreated", "blocked_upsert_has_not_mutated_metadata")
        delete_stdout, delete_stderr = delete_process.communicate(timeout=30)
        if delete_process.returncode:
            raise AssertionError(f"concurrent delete transaction failed: {delete_stdout} {delete_stderr}")
        upsert_stdout, upsert_stderr = upsert_process.communicate(timeout=30)
        if upsert_process.returncode:
            raise AssertionError(f"concurrent upsert transaction failed: {upsert_stdout} {upsert_stderr}")
        race_processes.clear()
        expect(
            sql(f"SELECT string_agg(operation,',' ORDER BY id) FROM pgwarc_lance.lance_outbox WHERE uri='{uri}' AND id>{race_base_id};"),
            "delete,upsert",
            "concurrent_delete_upsert_events_follow_uri_lock_order",
        )
        race_delete_delivery = worker_once()
        race_upsert_delivery = worker_once()
        assert race_delete_delivery is not None and race_upsert_delivery is not None
        expect(str(race_delete_delivery["status"]), "completed", "concurrent_delete_delivers_before_recreation")
        expect(str(race_upsert_delivery["status"]), "completed", "concurrent_upsert_delivers_after_delete")
        expect(sql(f"SELECT count(*)||'|'||count(DISTINCT id) FROM lance_scan('{uri}',100) WHERE id={doc_id};"), "1|1", "concurrent_delete_upsert_converges_to_one_vector")
        expect(sql(f"SELECT count(*) FROM pgwarc_lance.warc_record WHERE doc_id={doc_id};"), "1", "concurrent_delete_upsert_converges_to_one_metadata_row")
        expect(sql(f"SELECT content FROM pgwarc_lance.bm25_doc WHERE doc_id={doc_id};"), race_record.text, "concurrent_delete_upsert_converges_to_latest_bm25_content")
        expect(sql(f"SELECT expected_revision||'|'||is_deleted::text FROM pgwarc_lance.lance_document_revision WHERE uri='{uri}' AND doc_id={doc_id};"), f"{race_revision}|false", "concurrent_delete_upsert_leaves_latest_active_revision")
        checks["concurrent_delete_upsert_serialization_and_retry_are_idempotent"] = True

        # Race two importer transactions for one stable ID with distinct content
        # revisions. Queue order must leave the last committed input on Lance.
        revision_uri = f"/tmp/pgwarc_revision_{suffix}.lance"
        sql(f"SELECT lance_create_table('{revision_uri}',4,false);")
        with tempfile.TemporaryDirectory(prefix="pgwarc-concurrent-revision-") as tmp:
            root = Path(tmp)
            paths = [root / "alpha.warc", root / "beta.warc"]
            texts = ["revision alpha body", "revision beta body"]
            for path, text, content_type in zip(
                paths, texts, ["text/plain;variant=alpha", "text/plain;variant=beta"], strict=True
            ):
                path.write_bytes(fixture_warc(
                    f"https://example.test/revision/{suffix}", text, "sha1:stable-capture", content_type
                ))
            records = [next(warc_importer.iter_import_records([path], None, 1)) for path in paths]
            if records[0].doc_id != records[1].doc_id:
                raise AssertionError("same capture identity produced different document IDs")
            if warc_importer.record_revision(records[0]) == warc_importer.record_revision(records[1]):
                raise AssertionError("changed indexed content did not produce a new revision")

            def start_import(path: Path, index: int) -> subprocess.Popen[str]:
                return subprocess.Popen(
                    [
                        sys.executable, str(Path(__file__).resolve().parents[1] / "tools" / "warc_importer.py"),
                        str(path), "--execute", "--skip-schema", "--psql", shlex.join(sql_command),
                        "--lance-uri", revision_uri, "--lance-mode", "outbox", "--vector-dim", "4",
                        "--summary-json", str(root / f"summary-{index}.json"),
                    ],
                    text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )

            imports = [start_import(path, index) for index, path in enumerate(paths)]
            for process in imports:
                stdout, stderr = process.communicate(timeout=180)
                if process.returncode:
                    raise AssertionError(f"concurrent importer failed: {stdout[-1000:]} {stderr[-2000:]}")
        revision_doc_id = records[0].doc_id
        expect(
            sql(f"SELECT count(*) FROM pgwarc_lance.lance_outbox WHERE uri='{revision_uri}' AND ids=ARRAY[{revision_doc_id}]::bigint[] AND state='pending';"),
            "2",
            "concurrent_revision_updates_commit_two_ordered_jobs",
        )
        expect(
            sql(
                f"SELECT expected_revision=(SELECT revisions[1] FROM pgwarc_lance.lance_outbox "
                f"WHERE uri='{revision_uri}' ORDER BY id DESC LIMIT 1) FROM pgwarc_lance.lance_document_revision "
                f"WHERE uri='{revision_uri}' AND doc_id={revision_doc_id};"
            ),
            "t",
            "latest_committed_revision_is_the_expected_revision",
        )
        latest_revision = sql(
            f"SELECT revisions[1] FROM pgwarc_lance.lance_outbox WHERE uri='{revision_uri}' ORDER BY id DESC LIMIT 1;"
        )
        latest_record = next(record for record in records if warc_importer.record_revision(record) == latest_revision)
        expect(
            sql(f"SELECT content FROM pgwarc_lance.bm25_doc WHERE doc_id={revision_doc_id};"),
            latest_record.text,
            "concurrent_bm25_state_matches_latest_document_revision",
        )
        expect(
            sql(f"SELECT content_type FROM pgwarc_lance.warc_record WHERE doc_id={revision_doc_id};"),
            latest_record.content_type or "",
            "concurrent_metadata_matches_latest_document_revision",
        )
        first_revision_delivery = worker_once()
        assert first_revision_delivery is not None
        partial_revision_doctor = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parents[1] / "tools" / "doctor_pg_lance.py"),
             "--uri", revision_uri, "--psql", shlex.join(sql_command), "--max-rows", "20"],
            text=True, capture_output=True, timeout=90,
        )
        if partial_revision_doctor.returncode != 1:
            raise AssertionError(f"doctor did not detect the queued stale revision: {partial_revision_doctor.stderr[-2000:]}")
        partial_report = json.loads(partial_revision_doctor.stdout)
        expect(str(partial_report["findings"]["stale_vector_revisions"]), "1", "doctor_finds_concurrent_update_revision_lag")
        expect(sql(f"SELECT lance_count('{revision_uri}');"), "1", "same_document_concurrent_updates_keep_one_vector_row")
        final_revision_delivery = worker_once()
        assert final_revision_delivery is not None
        expected_revision = sql(
            f"SELECT expected_revision FROM pgwarc_lance.lance_document_revision WHERE uri='{revision_uri}' AND doc_id={revision_doc_id};"
        )
        expect(
            sql(f"SELECT revision FROM lance_scan_document_state('{revision_uri}',10) WHERE doc_id={revision_doc_id};"),
            expected_revision,
            "concurrent_revisions_converge_to_latest_expected_revision",
        )
        expect(sql(f"SELECT lance_count('{revision_uri}');"), "1", "concurrent_revision_replay_is_idempotent")

        role_uri = f"/tmp/pgwarc_revision_role_{suffix}.lance"
        role_revision = "d" * 64
        sql(f"SELECT lance_create_table('{role_uri}',4,false);")
        role_job = sql(
            f"SET ROLE pgwarc_outbox_ingest; SELECT lance_outbox_enqueue_revision('{role_uri}',"
            f"ARRAY[-839001]::bigint[],ARRAY[5,0,0,0]::real[],4,ARRAY['writer-role']::text[],"
            f"ARRAY['{role_revision}']::text[]); RESET ROLE;"
        )
        if not role_job.isdecimal():
            raise AssertionError(f"scoped writer role did not enqueue a job: {role_job!r}")
        checks["revisioned_enqueue_accepts_only_the_granted_writer_role"] = True
        expect(
            sql(f"SELECT expected_revision FROM pgwarc_lance.lance_document_revision WHERE uri='{role_uri}' AND doc_id=-839001;"),
            role_revision,
            "revisioned_enqueue_maintains_mapping_for_scoped_writer_role",
        )
        expect(sql("SELECT status FROM lance_outbox_process_next();"), "completed", "scoped_writer_job_delivered")
        expect(
            sql(f"SELECT revision FROM lance_scan_document_state('{role_uri}',10) WHERE doc_id=-839001;"),
            role_revision,
            "scoped_writer_revision_reaches_lance",
        )

        # Simulate the cross-store crash window: Lance has accepted the upsert,
        # but PostgreSQL cannot acknowledge the job. The read-only doctor must
        # leave both stores unchanged, and an explicit worker retry must
        # converge to the same single row without destructive repair.
        recovery_uri = f"/tmp/pgwarc_recovery_{suffix}.lance"
        recovery_revision = "c" * 64
        sql(f"SELECT lance_create_table('{recovery_uri}',4,false);")
        recovery_id = sql(
            f"SELECT lance_outbox_enqueue_revision('{recovery_uri}',ARRAY[-840001]::bigint[],"
            f"ARRAY[4,0,0,0]::real[],4,ARRAY['recoverable']::text[],"
            f"ARRAY['{recovery_revision}']::text[]);"
        )
        sql("""
            CREATE FUNCTION public.issue6_fail_outbox_ack() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.state = 'completed' THEN
                    RAISE EXCEPTION 'injected PostgreSQL acknowledgment failure'
                        USING ERRCODE = 'P0001';
                END IF;
                RETURN NEW;
            END $$;
            CREATE TRIGGER issue6_fail_outbox_ack
                BEFORE UPDATE OF state ON pgwarc_lance.lance_outbox
                FOR EACH ROW EXECUTE FUNCTION public.issue6_fail_outbox_ack();
        """)
        partial_failure = subprocess.run(
            sql_command,
            input="SELECT * FROM lance_outbox_process_next();",
            text=True,
            capture_output=True,
            timeout=90,
        )
        if partial_failure.returncode == 0 or "P0001" not in partial_failure.stderr or "injected PostgreSQL acknowledgment failure" not in partial_failure.stderr:
            raise AssertionError(
                f"injected cross-store acknowledgment failure was not observed: "
                f"{partial_failure.stdout} {partial_failure.stderr}"
            )
        checks["partial_external_write_database_ack_failure_injected"] = True
        expect(sql(f"SELECT lance_count('{recovery_uri}');"), "1", "partial_failure_left_one_external_vector")
        expect(
            sql(f"SELECT state||'|'||attempt_count::text FROM pgwarc_lance.lance_outbox WHERE id={recovery_id};"),
            "pending|0",
            "partial_failure_kept_database_job_retryable",
        )
        recovery_before_doctor = sql(
            f"SELECT lance_count('{recovery_uri}')||'|'||"
            f"(SELECT count(*) FROM lance_dataset_versions('{recovery_uri}'))||'|'||"
            f"(SELECT state FROM pgwarc_lance.lance_outbox WHERE id={recovery_id})||'|'||"
            f"(SELECT count(*) FROM pgwarc_lance.lance_write_log WHERE uri='{recovery_uri}');"
        )
        recovery_doctor = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parents[1] / "tools" / "doctor_pg_lance.py"),
             "--uri", recovery_uri, "--psql", shlex.join(sql_command), "--max-rows", "20"],
            text=True,
            capture_output=True,
            timeout=90,
        )
        if recovery_doctor.returncode != 1:
            raise AssertionError(f"doctor did not report retryable partial failure: {recovery_doctor.stderr[-2000:]}")
        recovery_report = json.loads(recovery_doctor.stdout)
        expect(str(recovery_report["findings"]["unfinished_outbox_jobs"]), "1", "doctor_reports_partial_failure_for_operator")
        recovery_after_doctor = sql(
            f"SELECT lance_count('{recovery_uri}')||'|'||"
            f"(SELECT count(*) FROM lance_dataset_versions('{recovery_uri}'))||'|'||"
            f"(SELECT state FROM pgwarc_lance.lance_outbox WHERE id={recovery_id})||'|'||"
            f"(SELECT count(*) FROM pgwarc_lance.lance_write_log WHERE uri='{recovery_uri}');"
        )
        expect(recovery_after_doctor, recovery_before_doctor, "partial_failure_doctor_is_read_only")
        sql("DROP TRIGGER issue6_fail_outbox_ack ON pgwarc_lance.lance_outbox; DROP FUNCTION public.issue6_fail_outbox_ack();")
        recovery_delivery = sql("SELECT status FROM lance_outbox_process_next();")
        expect(recovery_delivery, "completed", "explicit_retry_recovers_partial_cross_store_write")
        expect(sql(f"SELECT lance_count('{recovery_uri}');"), "1", "explicit_retry_does_not_duplicate_vector")
        expect(
            sql(f"SELECT revision FROM lance_scan_document_state('{recovery_uri}',10) WHERE doc_id=-840001;"),
            recovery_revision,
            "explicit_retry_converges_to_expected_revision",
        )

        # Dimension mismatch is a real Lance-side failure: the worker records it,
        # never marks it complete, and exposes it in the status view.
        sql(f"SELECT lance_create_table('{bad_uri}',4,false);")
        failed_id = sql(
            f"SELECT lance_outbox_enqueue('{bad_uri}',ARRAY[-820001]::bigint[],"
            "ARRAY[1,0,0]::real[],3,ARRAY['bad-dimension']::text[]);"
        )
        sql(f"UPDATE pgwarc_lance.lance_outbox SET attempt_count=7,available_at='-infinity' WHERE id={failed_id};")
        failure = sql("SELECT outbox_id||'|'||status||'|'||coalesce(applied_version::text,'null') FROM lance_outbox_process_next();")
        expect(failure, f"{failed_id}|failed|null", "lance_failure_is_not_completed")
        expect(sql(f"SELECT state||'|'||(last_error IS NOT NULL)::text||'|'||(completed_at IS NULL)::text FROM pgwarc_lance.lance_outbox WHERE id={failed_id};"), "failed|true|true", "failure_reason_and_noncompletion_persist")
        expect(sql(f"SELECT pending_jobs||'|'||failed_jobs FROM pgwarc_lance.lance_outbox_status WHERE uri='{bad_uri}';"), "0|1", "terminal_failure_visible_to_operator")

        # The read-only doctor inspects a disposable mixed-state dataset. It
        # reports observable row mismatches and an unfinished job without
        # acknowledging the job, adding a Lance version, or changing write logs.
        sql(f"SELECT lance_create_table('{doctor_uri}',2,false);")
        # These legacy Lance rows predate the PostgreSQL revision mapping.
        # The two tracked rows below intentionally remain revision-less so the
        # doctor can report stale legacy vectors while an unrelated orphan is
        # still visible in the same fixture.
        sql(
            f"SELECT lance_insert_many('{doctor_uri}',ARRAY[-851001,-851004,-851006]::bigint[],"
            "ARRAY[1,0,0,1,1,1]::real[],2,"
            "ARRAY['https://example.test/doctor/ok','https://example.test/doctor/old','orphan']::text[]);"
        )
        sql(
            f"SELECT lance_insert('{doctor_uri}',-851006,ARRAY[2,2]::real[],'duplicate orphan');"
        )
        sql("""
            INSERT INTO pgwarc_lance.warc_record
                (doc_id,target_uri,payload_digest,text_len)
            VALUES
                (-851001,'https://example.test/doctor/ok','sha1:ok',5),
                (-851002,'https://example.test/doctor/metadata-only','sha1:metadata',0),
                (-851003,'https://example.test/doctor/missing','sha1:missing',7),
                (-851004,'https://example.test/doctor/stale','sha1:stale',4),
                (-851005,'https://example.test/doctor/pending','sha1:pending',6);
            SELECT bm25_index_document(-851001,'doctor expected content');
            SELECT bm25_index_document(-851002,'');
            SELECT bm25_index_document(-851003,'missing vector content');
            SELECT bm25_index_document(-851004,'stale label content');
            SELECT bm25_index_document(-851005,'pending vector content');
        """)
        doctor_revisions = []
        doctor_revision_by_id = {}
        for doc_id, digest, content, target_uri, text_len in [
            (-851001, "sha1:ok", "doctor expected content", "https://example.test/doctor/ok", 5),
            (-851003, "sha1:missing", "missing vector content", "https://example.test/doctor/missing", 7),
            (-851004, "sha1:stale", "stale label content", "https://example.test/doctor/stale", 4),
            (-851005, "sha1:pending", "pending vector content", "https://example.test/doctor/pending", 6),
        ]:
            rev = hashlib.sha256(json.dumps({
                "doc_id": doc_id,
                "target_uri": target_uri,
                "content_type": None,
                "http_status": None,
                "text_len": text_len,
                "payload_digest": digest,
                "bm25_content_md5": hashlib.md5(content.encode("utf-8")).hexdigest(),
            }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
            doctor_revision_by_id[doc_id] = rev
            doctor_revisions.append(f"('{doctor_uri}',{doc_id},'{rev}')")
        sql(
            "INSERT INTO pgwarc_lance.lance_document_revision(uri,doc_id,expected_revision) VALUES "
            + ",".join(doctor_revisions) + ";"
        )
        sql(
            f"SELECT lance_outbox_enqueue_revision('{doctor_uri}',ARRAY[-851005]::bigint[],"
            "ARRAY[3,3]::real[],2,ARRAY['https://example.test/doctor/pending']::text[],"
            f"ARRAY['{doctor_revision_by_id[-851005]}']::text[]);"
        )
        doctor_role = f"pgwarc_doctor_{suffix}"
        sql(f"""
            CREATE ROLE {doctor_role} NOLOGIN;
            GRANT USAGE ON SCHEMA public, pgwarc_lance TO {doctor_role};
            GRANT SELECT ON pgwarc_lance.bm25_doc, pgwarc_lance.warc_record TO {doctor_role};
            GRANT SELECT (uri, doc_id, expected_revision, is_deleted)
                ON pgwarc_lance.lance_document_revision TO {doctor_role};
            GRANT SELECT (id, uri, state, ids) ON pgwarc_lance.lance_outbox TO {doctor_role};
            GRANT EXECUTE ON FUNCTION public.lance_scan_document_state(text,integer) TO {doctor_role};
        """)
        doctor_sql_command = [
            "docker", "exec", "-i", "-e", f"PGOPTIONS=-c role={doctor_role}", container,
            "psql", "-X", "-q", "-A", "-t", "-F", "|", "-v", "ON_ERROR_STOP=1",
            "-v", "VERBOSITY=verbose", "-U", "postgres", "-d", "postgres",
        ]
        doctor_before = sql(
            f"SELECT lance_count('{doctor_uri}')||'|'||"
            f"(SELECT count(*) FROM lance_dataset_versions('{doctor_uri}'))||'|'||"
            f"(SELECT count(*) FROM pgwarc_lance.lance_outbox WHERE uri='{doctor_uri}')||'|'||"
            f"(SELECT count(*) FROM pgwarc_lance.lance_write_log WHERE uri='{doctor_uri}');"
        )
        doctor = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parents[1] / "tools" / "doctor_pg_lance.py"),
             "--uri", doctor_uri, "--psql", shlex.join(doctor_sql_command), "--max-rows", "20"],
            text=True, capture_output=True, timeout=90,
        )
        if doctor.returncode != 1:
            raise AssertionError(f"read-only doctor failed: {doctor.stderr[-2000:]}")
        doctor_report = json.loads(doctor.stdout)
        report["doctor_report"] = doctor_report
        expect(str(doctor.returncode), "1", "doctor_exit_code_reports_observed_findings")
        expect(doctor_report["status"], "findings", "doctor_reports_reconciliation_findings")
        expect(str(doctor_report["read_only"]), "True", "doctor_marks_read_only_mode")
        expect(str(doctor_report["findings"]["duplicate_lance_ids"]), "1", "doctor_detects_duplicate_lance_id")
        expect(str(doctor_report["findings"]["missing_vector_candidates"]), "1", "doctor_detects_missing_vector_candidates")
        expect(str(doctor_report["findings"]["orphan_lance_ids"]), "1", "doctor_detects_orphan_lance_id")
        expect(str(doctor_report["findings"]["stale_vector_revisions"]), "2", "doctor_detects_stale_vector_revisions")
        expect(str(doctor_report["findings"]["stale_label_ids"]), "1", "doctor_detects_stale_metadata_label")
        expect(str(doctor_report["findings"]["unfinished_outbox_jobs"]), "1", "doctor_detects_unfinished_outbox")
        expect(str(doctor_report["findings"]["unfinished_outbox_document_ids"]), "1", "doctor_counts_pending_document_ids")
        expect(str(doctor_report["reconciliation_complete"]), "False", "doctor_does_not_claim_complete_reconciliation")
        expect(str(doctor_report["revision_check"]["postgres_documents_scoped_to_lance_uri"]), "True", "doctor_uses_uri_document_revision_mapping")
        expect(str(doctor_report["revision_check"]["lance_revision_persisted"]), "False", "doctor_discloses_missing_lance_revision")
        expect(str(doctor_report["snapshot"]["cross_store_atomic_snapshot"]), "False", "doctor_discloses_cross_store_snapshot_limit")
        checks["read_only_doctor_runs_with_column_scoped_reader_grants"] = True
        doctor_after = sql(
            f"SELECT lance_count('{doctor_uri}')||'|'||"
            f"(SELECT count(*) FROM lance_dataset_versions('{doctor_uri}'))||'|'||"
            f"(SELECT count(*) FROM pgwarc_lance.lance_outbox WHERE uri='{doctor_uri}')||'|'||"
            f"(SELECT count(*) FROM pgwarc_lance.lance_write_log WHERE uri='{doctor_uri}');"
        )
        expect(doctor_after, doctor_before, "doctor_does_not_mutate_pg_or_lance")
        doctor_drain = worker_once()
        if doctor_drain is None:
            raise AssertionError("doctor fixture outbox job was not available for cleanup")
        expect(str(doctor_drain["status"]), "completed", "doctor_fixture_outbox_cleaned_after_read_only_assertion")

        # Two concurrent workers cannot overtake a locked predecessor for the
        # same URI. The first worker commits after a controlled SQL sleep.
        sql(f"SELECT lance_create_table('{concurrent_uri}',4,false);")
        ids = sql(
            f"BEGIN; SELECT lance_outbox_enqueue('{concurrent_uri}',ARRAY[-830001]::bigint[],ARRAY[1,0,0,0]::real[],4,ARRAY['first']::text[]); "
            f"SELECT lance_outbox_enqueue('{concurrent_uri}',ARRAY[-830002]::bigint[],ARRAY[2,0,0,0]::real[],4,ARRAY['second']::text[]); COMMIT;"
        ).splitlines()
        first_id, second_id = map(int, ids)
        install_ack_pause()
        writer = begin_paused_delivery(app)
        expect(sql("SELECT count(*) FROM lance_outbox_process_next();"), "0", "second_worker_skips_locked_same_dataset_predecessor")
        expect(sql(f"SELECT lance_count('{concurrent_uri}');"), "1", "only_first_concurrent_event_reached_lance")
        expect(sql(f"SELECT count(*) FROM pgwarc_lance.lance_outbox WHERE id={first_id} AND state='pending';"), "1", "uncommitted_completion_not_visible")
        sql(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name='{app}';")
        writer.wait(timeout=20)
        if writer.returncode == 0:
            raise AssertionError("synchronized concurrency worker unexpectedly committed")
        writer = None
        remove_ack_pause()
        expect(sql(f"SELECT state FROM pgwarc_lance.lance_outbox WHERE id={first_id};"), "pending", "concurrent_backend_termination_rolls_back_completion")
        expect(sql(f"SELECT count(*) FROM lance_scan('{concurrent_uri}',10) WHERE id=-830001;"), "1", "lance_write_survives_sql_backend_termination")
        replay = worker_once()
        assert replay is not None
        expect(str(replay["outbox_id"]), str(first_id), "worker_restart_replays_oldest_event")
        expect(sql(f"SELECT count(*) FROM lance_outbox_process_next();"), "1", "next_event_waits_until_predecessor_commits")
        expect(sql(f"SELECT lance_count('{concurrent_uri}');"), "2", "concurrent_events_both_materialized_once")
        expect(sql(f"SELECT count(*) FROM lance_scan('{concurrent_uri}',10) WHERE id IN (-830001,-830002);"), "2", "concurrent_delivery_has_no_lost_rows")
        checks["two_worker_concurrency_is_ordered_and_recoverable"] = True

        # Kill a worker backend after Lance has changed but before the SQL
        # transaction commits. The old event reappears and a new worker replays
        # the same ID through an idempotent upsert without adding a duplicate.
        crash_id = sql(
            f"SELECT lance_outbox_enqueue('{concurrent_uri}',ARRAY[-830003]::bigint[],"
            "ARRAY[3,0,0,0]::real[],4,ARRAY['crash-replay']::text[]);"
        )
        install_ack_pause()
        writer = begin_paused_delivery(app)
        expect(sql(f"SELECT state||'|'||coalesce(applied_version::text,'null') FROM pgwarc_lance.lance_outbox WHERE id={crash_id};"), "pending|null", "completion_not_acknowledged_before_commit")
        expect(sql(f"SELECT count(*) FROM lance_scan('{concurrent_uri}',10) WHERE id=-830003 AND label='crash-replay';"), "1", "external_lance_commit_precedes_sql_ack")
        sql(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name='{app}';")
        writer.wait(timeout=20)
        if writer.returncode == 0:
            raise AssertionError("crash-window worker unexpectedly committed")
        writer = None
        remove_ack_pause()
        expect(sql(f"SELECT state||'|'||attempt_count FROM pgwarc_lance.lance_outbox WHERE id={crash_id};"), "pending|0", "terminated_worker_keeps_job_retryable")
        replay = worker_once()
        assert replay is not None
        expect(str(replay["status"]), "completed", "restarted_worker_acknowledges_replay")
        expect(sql(f"SELECT count(*)||'|'||count(DISTINCT id) FROM lance_scan('{concurrent_uri}',10) WHERE id=-830003;"), "1|1", "duplicate_delivery_is_idempotent")
        expect(sql(f"SELECT state||'|'||applied_version::text FROM pgwarc_lance.lance_outbox WHERE id={crash_id};"), f"completed|{replay['applied_version']}", "replay_completion_version_is_recorded")

        # A committed pending row survives a real PostgreSQL container crash and
        # server restart, then drains through a fresh worker process.
        restart_id = sql(
            f"SELECT lance_outbox_enqueue('{concurrent_uri}',ARRAY[-830004]::bigint[],"
            "ARRAY[4,0,0,0]::real[],4,ARRAY['postgres-restart']::text[]);"
        )
        run(["docker", "kill", "--signal", "KILL", container], timeout=30)
        run(["docker", "start", container], timeout=30)
        smoke_release_artifacts.wait_until_ready(container=container, user="postgres", database="postgres", timeout_seconds=120)
        expect(sql(f"SELECT state||'|'||attempt_count FROM pgwarc_lance.lance_outbox WHERE id={restart_id};"), "pending|0", "committed_outbox_survives_postgres_restart")
        expect(sql(f"SELECT count(*) FROM lance_scan('{concurrent_uri}',10) WHERE id=-830004;"), "0", "unapplied_job_remains_pending_after_restart")
        restart_delivery = worker_once()
        assert restart_delivery is not None
        expect(str(restart_delivery["status"]), "completed", "worker_drains_durable_job_after_restart")
        expect(sql(f"SELECT count(*)||'|'||count(DISTINCT id) FROM lance_scan('{concurrent_uri}',10) WHERE id=-830004;"), "1|1", "restart_delivery_materializes_single_vector")
        checks["postgres_crash_restart_retains_durable_outbox"] = True

        report["status"] = "passed"
        return 0
    except Exception as exc:
        report.update(status="failed", error=str(exc))
        raise
    finally:
        if created:
            if race_app_names:
                app_names = ",".join("'" + name.replace("'", "''") + "'" for name in race_app_names)
                subprocess.run(
                    sql_command,
                    input=f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name IN ({app_names});\n",
                    text=True, capture_output=True, timeout=15,
                )
            for process in race_processes:
                if process.poll() is None:
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)
            if writer and writer.poll() is None:
                subprocess.run(
                    sql_command,
                    input=f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name='{app}';\n",
                    text=True, capture_output=True, timeout=15,
                )
                try:
                    writer.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    writer.kill()
                    writer.wait(timeout=10)
            subprocess.run(["docker", "rm", "-f", "-v", container], capture_output=True, timeout=30)
        if args.json_output:
            args.json_output.parent.mkdir(parents=True, exist_ok=True)
            args.json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
