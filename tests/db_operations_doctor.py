#!/usr/bin/env python3
"""Exercise the operational doctor and safe metrics in isolated PostgreSQL 16."""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import check_release_artifacts
import smoke_release_artifacts


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, required=True)
    parser.add_argument("--version", default=check_release_artifacts.cargo_version())
    parser.add_argument("--postgres-image", default="postgres:16")
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    errors = check_release_artifacts.validate_artifacts(
        args.dist_dir, version=args.version, pg_major=16
    )
    if errors:
        parser.error("PG16 artifact validation failed")

    suffix = uuid.uuid4().hex[:12]
    container = f"pgwarc_operations_doctor_{suffix}"
    uri = f"/tmp/pgwarc_operations_doctor_{suffix}.lance"
    role = f"pgwarc_doctor_reader_{suffix}"
    repository = Path(__file__).resolve().parents[1]
    report: dict[str, object] = {
        "status": "running",
        "postgres_image": args.postgres_image,
        "extension_version": args.version,
        "artifact_sha256": _sha256(args.dist_dir / "pgwarc_lance.so"),
        "cargo_lock_sha256": _sha256(repository / "Cargo.lock"),
        "test_harness_sha256": _sha256(Path(__file__)),
        "scope": "isolated PostgreSQL 16; synthetic rows and Lance vectors; no source corpus",
        "checks": {},
    }
    checks = report["checks"]
    created = False
    psql = [
        "docker", "exec", "-i", container,
        "psql", "-X", "-q", "-A", "-t", "-F", "|",
        "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", "postgres",
    ]

    def run(command: list[str], *, sql: str | None = None, timeout: int = 60) -> str:
        result = subprocess.run(
            command, input=sql, text=True, capture_output=True, timeout=timeout, check=False
        )
        if result.returncode:
            raise AssertionError(f"isolated database command failed (exit {result.returncode})")
        return result.stdout.strip()

    def query(sql: str) -> str:
        return run(psql, sql=sql)

    def expect(actual: object, expected: object, name: str) -> None:
        if actual != expected:
            raise AssertionError(f"{name}: expected {expected!r}, got {actual!r}")
        checks[name] = True

    try:
        run([
            "docker", "run", "-d", "--name", container, "--network", "none",
            "--cpus", "2", "--memory", "3g",
            "-e", "POSTGRES_HOST_AUTH_METHOD=trust", args.postgres_image,
        ])
        created = True
        smoke_release_artifacts.wait_until_ready(
            container=container, user="postgres", database="postgres", timeout_seconds=120
        )
        smoke_release_artifacts.install_artifacts(
            container=container, dist_dir=args.dist_dir, version=args.version
        )
        query("CREATE EXTENSION pgwarc_lance;")
        report["postgres_version"] = query("SHOW server_version;")
        expect(
            query("SELECT extversion FROM pg_extension WHERE extname='pgwarc_lance';"),
            args.version,
            "fresh_pg16_install",
        )

        # Deliberately create observable count, duplicate and orphan findings.
        query(f"SELECT public.lance_create_table('{uri}',2,false);")
        query(
            f"SELECT public.lance_insert_many('{uri}',ARRAY[-19020]::bigint[],"
            "ARRAY[1,0]::real[],2,ARRAY['orphan-first']::text[]);"
        )
        query(f"SELECT public.lance_insert('{uri}',-19020,ARRAY[0,1]::real[],'orphan-second');")
        query(
            "INSERT INTO pgwarc_lance.warc_record(doc_id,target_uri,payload_digest,text_len) "
            "VALUES (-19010,'https://example.invalid/doctor-fixture','sha1:fixture',19); "
            "SELECT public.bm25_index_document(-19010,'private body marker');"
        )

        # A three-dimensional committed event against a two-dimensional
        # dataset is an actual Lance failure; the worker must retain it failed.
        failed_id = query(
            f"SELECT public.lance_outbox_enqueue('{uri}',ARRAY[-19030]::bigint[],"
            "ARRAY[1,0,0]::real[],3,ARRAY['failure-fixture']::text[]);"
        )
        # Model an exhausted retry budget so the same real dimension failure
        # becomes terminal immediately in this bounded fixture.
        query(
            f"UPDATE pgwarc_lance.lance_outbox SET attempt_count=7,available_at='-infinity' "
            f"WHERE id={failed_id};"
        )
        failure = query(
            "SELECT outbox_id||'|'||status||'|'||coalesce(applied_version::text,'null') "
            "FROM public.lance_outbox_process_next();"
        )
        expect(failure, f"{failed_id}|failed|null", "injected_lance_dimension_failure")
        expect(
            query(
                f"SELECT state||'|'||(last_error IS NOT NULL)::text "
                f"FROM pgwarc_lance.lance_outbox WHERE id={failed_id};"
            ),
            "failed|true",
            "failed_event_is_not_acknowledged",
        )

        pending_id = query(
            f"SELECT public.lance_outbox_enqueue('{uri}',ARRAY[-19031]::bigint[],"
            "ARRAY[0,1]::real[],2,ARRAY['pending-fixture']::text[]);"
        )
        # These inert marker strings ensure the output path never serializes a
        # private diagnostic, path, source filename or credential value.
        query(
            f"UPDATE pgwarc_lance.lance_outbox SET last_error="
            "'private-path-marker /private/source-marker.warc password=redaction-test-only' "
            f"WHERE id={failed_id};"
        )

        query(f"""
            CREATE ROLE {role} NOLOGIN;
            GRANT USAGE ON SCHEMA public, pgwarc_lance TO {role};
            GRANT SELECT ON pgwarc_lance.bm25_doc, pgwarc_lance.warc_record,
                pgwarc_lance.lance_outbox_status TO {role};
            GRANT SELECT (uri, doc_id, expected_revision, is_deleted)
                ON pgwarc_lance.lance_document_revision TO {role};
            GRANT SELECT (id, uri, state, ids)
                ON pgwarc_lance.lance_outbox TO {role};
            GRANT EXECUTE ON FUNCTION public.lance_scan_document_state(text,integer),
                public.lance_dataset_versions(text) TO {role};
        """)
        reader_psql = [
            "docker", "exec", "-i", "-e", f"PGOPTIONS=-c role={role}", container,
            "psql", "-X", "-q", "-A", "-t", "-F", "|", "-v", "ON_ERROR_STOP=1",
            "-U", "postgres", "-d", "postgres",
        ]
        doctor = repository / "tools" / "doctor_pg_lance.py"
        base = [sys.executable, str(doctor), "--uri", uri,
                "--psql", shlex.join(reader_psql), "--max-rows", "20"]
        before = query(
            f"SELECT lance_count('{uri}')||'|'||"
            f"(SELECT max(version) FROM public.lance_dataset_versions('{uri}'))||'|'||"
            f"(SELECT count(*) FROM pgwarc_lance.lance_outbox WHERE uri='{uri}')||'|'||"
            f"(SELECT count(*) FROM pgwarc_lance.lance_write_log WHERE uri='{uri}');"
        )
        expected_version = query(f"SELECT max(version) FROM public.lance_dataset_versions('{uri}');")
        output = subprocess.run(base, text=True, capture_output=True, timeout=90, check=False)
        expect(output.returncode, 1, "least_privilege_doctor_detects_findings")
        doctor_report = json.loads(output.stdout)
        report["doctor_report"] = doctor_report
        expect(doctor_report["schema_version"], 2, "doctor_report_schema_v2")
        expect(doctor_report["counts"]["postgres_documents"], 1, "doctor_reports_postgres_count")
        expect(doctor_report["counts"]["lance_rows"], 2, "doctor_reports_physical_lance_count")
        expect(doctor_report["counts"]["row_count_delta"], -1, "doctor_reports_count_delta")
        expect(doctor_report["findings"]["row_count_mismatch"], 1, "doctor_detects_count_mismatch")
        expect(doctor_report["findings"]["duplicate_lance_ids"], 1, "doctor_detects_duplicate_id")
        expect(doctor_report["findings"]["orphan_lance_ids"], 1, "doctor_detects_orphan_id")
        expect(doctor_report["findings"]["failed_outbox_jobs"], 1, "doctor_detects_injected_failure")
        expect(doctor_report["findings"]["unfinished_outbox_jobs"], 2, "doctor_detects_pending_and_failed_jobs")
        expect(doctor_report["metrics"]["outbox_queue_depth"], 2, "doctor_reports_queue_depth")
        expect(doctor_report["metrics"]["pending_jobs"], 1, "doctor_reports_pending_count")
        expect(doctor_report["metrics"]["ready_jobs"], 1, "doctor_reports_ready_count")
        expect(doctor_report["metrics"]["failed_jobs"], 1, "doctor_reports_failed_count")
        expect(doctor_report["metrics"]["active_dataset_version"], int(expected_version), "doctor_reports_active_dataset_version")
        guidance = " ".join(doctor_report["recovery_guidance"]).lower()
        if "dry-run" not in guidance or "review the failure detail privately" not in guidance:
            raise AssertionError("doctor omitted safe dry-run guidance for the failed event")
        if "never mark it completed manually" not in guidance:
            raise AssertionError("doctor omitted the no-manual-completion instruction")
        for marker in (uri, "private-path-marker", "source-marker.warc", "redaction-test-only", "private body marker"):
            if marker in output.stdout:
                raise AssertionError("doctor JSON exposed a path, credential, source name or document text")
        checks["doctor_output_redacts_paths_credentials_sources_and_text"] = True

        metrics = subprocess.run(
            [*base, "--format", "prometheus"], text=True, capture_output=True,
            timeout=90, check=False,
        )
        expect(metrics.returncode, 1, "least_privilege_prometheus_metrics_preserve_findings")
        for sample in (
            "pgwarc_lance_doctor_outbox_queue_depth 2",
            "pgwarc_lance_doctor_outbox_failed_jobs 1",
            f"pgwarc_lance_doctor_active_dataset_version {expected_version}",
            'pgwarc_lance_doctor_finding{kind="duplicate_lance_ids"} 1',
        ):
            if sample not in metrics.stdout:
                raise AssertionError("Prometheus metrics omitted a required aggregate sample")
        expect(
            metrics.stdout.count("# HELP pgwarc_lance_doctor_finding "),
            1,
            "prometheus_has_one_finding_family_help",
        )
        expect(
            metrics.stdout.count("# TYPE pgwarc_lance_doctor_finding gauge"),
            1,
            "prometheus_has_one_finding_family_type",
        )
        for marker in (uri, "private-path-marker", "source-marker.warc", "redaction-test-only", "private body marker"):
            if marker in metrics.stdout:
                raise AssertionError("Prometheus metrics exposed a path, credential, source name or document text")
        checks["prometheus_export_is_aggregate_and_path_free"] = True
        expect(
            query(
                f"SELECT lance_count('{uri}')||'|'||"
                f"(SELECT max(version) FROM public.lance_dataset_versions('{uri}'))||'|'||"
                f"(SELECT count(*) FROM pgwarc_lance.lance_outbox WHERE uri='{uri}')||'|'||"
                f"(SELECT count(*) FROM pgwarc_lance.lance_write_log WHERE uri='{uri}');"
            ),
            before,
            "doctor_and_metrics_are_read_only_dry_run",
        )
        expect(
            query(f"SELECT state FROM pgwarc_lance.lance_outbox WHERE id={pending_id};"),
            "pending",
            "doctor_does_not_requeue_or_acknowledge",
        )
        report["status"] = "passed"
        return 0
    except Exception as exc:
        report.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        if created:
            subprocess.run(["docker", "rm", "-f", "-v", container], capture_output=True, timeout=30)
        if args.json_output:
            args.json_output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            args.json_output.parent.chmod(0o700)
            args.json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            args.json_output.chmod(0o600)
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
