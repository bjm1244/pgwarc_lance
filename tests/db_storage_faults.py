#!/usr/bin/env python3
"""Exercise storage errors and a PostgreSQL crash in a new isolated container.

An 8 MiB tmpfs is filled only inside the generated container. The PostgreSQL
data directory and crash-test Lance dataset are outside that tmpfs. No ports
are published, network access is disabled, and no existing container is used.
The generated container and its anonymous volumes are removed after the test.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import check_release_artifacts
import smoke_release_artifacts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist-dir', required=True, type=Path)
    parser.add_argument('--version', default=check_release_artifacts.cargo_version())
    parser.add_argument('--postgres-image', default='postgres:16')
    parser.add_argument('--json-output', type=Path)
    args = parser.parse_args()
    errors = check_release_artifacts.validate_artifacts(args.dist_dir, version=args.version, pg_major=16)
    if errors:
        raise ValueError('; '.join(errors))
    container = 'pgwarc_storage_fault_' + uuid.uuid4().hex[:16]
    command = ['docker', 'exec', '-i', container, 'psql', '-U', 'postgres', '-d', 'postgres',
               '-X', '-q', '-At', '-v', 'ON_ERROR_STOP=1', '-v', 'VERBOSITY=verbose']
    report = {'status': 'running', 'container': container, 'version': args.version,
              'scope': 'isolated tmpfs ENOSPC, OS permission error, clean SIGTERM and SIGKILL restart; not a hardware power-loss test',
              'checks': {}, 'timing_seconds': {}}
    checks = report['checks']
    created, writer = False, None

    def run(argv: list[str], *, text: str | None = None, timeout: float = 60) -> str:
        result = subprocess.run(argv, input=text, text=True, capture_output=True, timeout=timeout)
        if result.returncode:
            raise AssertionError(f'{argv}: {result.stderr[-2000:]}')
        return result.stdout.strip()

    def sql(text: str) -> str:
        return run(command, text=text)

    def expect(actual: str, expected: str, name: str) -> None:
        if actual != expected:
            raise AssertionError(f'{name}: expected {expected!r}, got {actual!r}')
        checks[name] = True

    def ready() -> None:
        smoke_release_artifacts.wait_until_ready(container=container, user='postgres', database='postgres', timeout_seconds=90)

    try:
        run(['docker', 'run', '-d', '--name', container, '--network', 'none',
             '--cpus', '2', '--memory', '2g', '--tmpfs', '/fault:size=8m,mode=1777',
             '-e', 'POSTGRES_HOST_AUTH_METHOD=trust', args.postgres_image])
        created = True
        ready()
        smoke_release_artifacts.install_artifacts(container=container, dist_dir=args.dist_dir, version=args.version)
        sql('CREATE EXTENSION pgwarc_lance;')
        # Keep durable test data outside the disposable ENOSPC tmpfs so it
        # survives the later container stop/start cycles.
        uri = '/tmp/pgwarc_fault_full.lance'
        stable_doc_id = 90
        stable_revision = 'a' * 64
        sql(f"""
            CREATE ROLE pgwarc_fault_reader NOLOGIN;
            ALTER ROLE pgwarc_fault_reader SET work_mem='4MB';
            ALTER ROLE pgwarc_fault_reader SET statement_timeout='7s';
            GRANT USAGE ON SCHEMA public, pgwarc_lance TO pgwarc_fault_reader;
            GRANT SELECT ON pgwarc_lance.bm25_doc, pgwarc_lance.bm25_term,
                pgwarc_lance.bm25_long_term TO pgwarc_fault_reader;
            GRANT SELECT (uri,doc_id,expected_revision,is_deleted)
                ON pgwarc_lance.lance_document_revision TO pgwarc_fault_reader;
            GRANT EXECUTE ON FUNCTION public.bm25_search(text,integer),
                public.lance_scan_document_state(text,integer),
                public.lance_vector_search(text,real[],integer)
                TO pgwarc_fault_reader;
            SELECT lance_create_table('{uri}',4,false);
            SELECT bm25_index_document({stable_doc_id},'stable recovery checkpoint');
            SELECT lance_outbox_enqueue_revision('{uri}',ARRAY[{stable_doc_id}]::bigint[],
                ARRAY[0,0,0,0]::real[],4,ARRAY['stable-checkpoint']::text[],
                ARRAY['{stable_revision}']::text[]);
            SELECT status FROM lance_outbox_process_next();
        """)
        expect(sql(f"SELECT expected_revision FROM pgwarc_lance.lance_document_revision WHERE uri='{uri}' AND doc_id={stable_doc_id};"),
               stable_revision, 'initial_revision_mapping_committed')
        expect(sql(f"SELECT revision FROM lance_scan_document_state('{uri}',10) WHERE doc_id={stable_doc_id};"),
               stable_revision, 'initial_lance_revision_committed')
        expect(sql(f"SET ROLE pgwarc_fault_reader; SELECT doc_id FROM bm25_search('checkpoint',10);"),
               str(stable_doc_id), 'reader_grant_works_before_faults')

        # PostgreSQL runs as the postgres OS user, while this fixture directory
        # is owned by root. Restore access only after observing a server error.
        run(['docker', 'exec', container, 'mkdir', '/fault/denied'])
        run(['docker', 'exec', container, 'chmod', '700', '/fault/denied'])
        denied = subprocess.run(command, input="SELECT lance_create_table('/fault/denied/data.lance',4,false);",
                                text=True, capture_output=True, timeout=30)
        if not denied.returncode or 'Permission denied' not in denied.stderr:
            raise AssertionError(f'OS permission fault was not exercised: {denied.stderr}')
        report['permission_stderr'] = denied.stderr[-4000:]
        checks['server_os_permission_error_observed'] = True
        expect(sql("SELECT count(*) FROM pgwarc_lance.lance_write_log WHERE uri='/fault/denied/data.lance';"), '0', 'permission_failure_log_rolled_back')
        run(['docker', 'exec', container, 'chmod', '777', '/fault/denied'])
        sql("SELECT lance_create_table('/fault/denied/data.lance',4,false);")
        expect(sql("SELECT lance_count('/fault/denied/data.lance');"), '0', 'create_retry_after_permission_recovery')

        enospc_uri = '/fault/full.lance'
        sql(f"SELECT lance_create_table('{enospc_uri}',4,false);")
        before = sql(f"SELECT max(version) FROM lance_dataset_versions('{enospc_uri}');")
        filler = subprocess.run(['docker', 'exec', container, 'dd', 'if=/dev/zero', 'of=/fault/filler', 'bs=65536', 'status=none'],
                                text=True, capture_output=True, timeout=30)
        if not filler.returncode or 'No space left on device' not in filler.stderr:
            raise AssertionError('isolated tmpfs was not filled to ENOSPC')
        checks['tmpfs_enospc_injected'] = True
        failed = subprocess.run(command + ['-v', 'ON_ERROR_STOP=0'], input=f"""BEGIN;
SELECT bm25_index_document(1,'mustrollback');
SELECT lance_upsert_many('{enospc_uri}',ARRAY[1]::bigint[],ARRAY[1,0,0,0]::float4[],4,ARRAY[repeat('q',4096)]);
ROLLBACK;
SELECT 'backend-reusable-after-enospc';
""", text=True, capture_output=True, timeout=60)
        if failed.returncode or 'No space left on device' not in failed.stderr or 'backend-reusable-after-enospc' not in failed.stdout:
            raise AssertionError(f'ENOSPC server error/backend reuse missing: {failed.stdout} {failed.stderr}')
        report['enospc_stderr'] = failed.stderr[-4000:]
        checks['server_enospc_and_same_backend_reuse'] = True
        enospc_recovery_started = time.monotonic()
        expect(sql("SELECT count(*) FROM pgwarc_lance.bm25_doc WHERE doc_id=1;"), '0', 'enospc_sql_transaction_rolled_back')
        expect(sql(f"SELECT count(*) FROM pgwarc_lance.lance_write_log WHERE uri='{enospc_uri}' AND op='upsert_many';"), '0', 'enospc_write_log_rolled_back')
        expect(sql(f"SELECT expected_revision FROM pgwarc_lance.lance_document_revision WHERE uri='{uri}' AND doc_id={stable_doc_id};"), stable_revision, 'enospc_preserves_committed_revision_mapping')
        expect(sql(f"SELECT revision FROM lance_scan_document_state('{uri}',10) WHERE doc_id={stable_doc_id};"), stable_revision, 'enospc_preserves_lance_revision')
        expect(sql(f"SELECT doc_id FROM bm25_search('checkpoint',10);"), str(stable_doc_id), 'enospc_preserves_bm25_retrieval')
        expect(sql(f"SELECT id FROM lance_vector_search('{uri}',ARRAY[0,0,0,0]::real[],10) WHERE id={stable_doc_id};"), str(stable_doc_id), 'enospc_preserves_vector_retrieval')
        expect(sql("SET ROLE pgwarc_fault_reader; SELECT doc_id FROM bm25_search('checkpoint',10);"), str(stable_doc_id), 'enospc_preserves_reader_grant')
        run(['docker', 'exec', container, 'rm', '/fault/filler'])
        expect(sql(f"SELECT max(version) FROM lance_dataset_versions('{enospc_uri}');"), before, 'enospc_no_new_committed_dataset_version')
        expect(sql(f"SELECT lance_count('{enospc_uri}');"), '0', 'enospc_original_dataset_readable')
        sql(f"BEGIN; SELECT bm25_index_document(1,'retry'); SELECT lance_upsert_many('{enospc_uri}',ARRAY[1]::bigint[],ARRAY[1,0,0,0]::float4[],4,ARRAY['retry']); COMMIT;")
        expect(sql(f"SELECT count(*)||'|'||bool_and(label='retry') FROM lance_scan('{enospc_uri}',10) WHERE id=1;"), '1|true', 'enospc_retry_restores_one_current_row')
        expect(sql(f"SELECT count(*) FROM lance_scan('{enospc_uri}',10);"), '1', 'enospc_retry_creates_one_unique_row')
        report['timing_seconds']['enospc_free_and_retry'] = round(time.monotonic() - enospc_recovery_started, 3)

        # A committed revision-aware update survives orderly SIGTERM shutdown.
        term_doc_id, term_revision = 91, 'b' * 64
        sql(f"SELECT bm25_index_document({term_doc_id},'term shutdown checkpoint');")
        sql(f"SELECT lance_outbox_enqueue_revision('{uri}',ARRAY[{term_doc_id}]::bigint[],"
            f"ARRAY[1,0,0,0]::real[],4,ARRAY['term-checkpoint']::text[],"
            f"ARRAY['{term_revision}']::text[]);")
        expect(sql("SELECT status FROM lance_outbox_process_next();"), 'completed', 'sigterm_fixture_event_completed')
        term_started = time.monotonic()
        run(['docker', 'stop', '--time', '30', container], timeout=40)
        report['timing_seconds']['sigterm_stop'] = round(time.monotonic() - term_started, 3)
        graceful_logs = subprocess.run(['docker', 'logs', '--tail', '100', container], text=True, capture_output=True, timeout=30, check=True)
        graceful_text = graceful_logs.stdout + graceful_logs.stderr
        report['sigterm_shutdown_logs'] = graceful_text[-12000:]
        if 'received fast shutdown request' not in graceful_text or 'database system is shut down' not in graceful_text:
            raise AssertionError('PostgreSQL clean SIGTERM shutdown was not observed')
        checks['postgres_clean_sigterm_shutdown_observed'] = True
        run(['docker', 'start', container])
        ready_started = time.monotonic()
        ready()
        report['timing_seconds']['sigterm_restart_ready'] = round(time.monotonic() - ready_started, 3)
        expect(sql("SELECT current_setting('server_version_num')::int / 10000;"), '16', 'sigterm_restart_remains_pg16')
        expect(sql(f"SELECT count(*) FROM pgwarc_lance.bm25_doc WHERE doc_id IN ({stable_doc_id},{term_doc_id});"), '2', 'sigterm_committed_bm25_rows_survive')
        expect(sql(f"SELECT expected_revision FROM pgwarc_lance.lance_document_revision WHERE uri='{uri}' AND doc_id={term_doc_id};"), term_revision, 'sigterm_revision_mapping_survives')
        expect(sql(f"SELECT revision FROM lance_scan_document_state('{uri}',10) WHERE doc_id={term_doc_id};"), term_revision, 'sigterm_lance_revision_survives')
        expect(sql(f"SELECT doc_id FROM bm25_search('shutdown',10);"), str(term_doc_id), 'sigterm_bm25_retrieval_survives')
        expect(sql(f"SELECT id FROM lance_vector_search('{uri}',ARRAY[1,0,0,0]::real[],10) WHERE id={term_doc_id};"), str(term_doc_id), 'sigterm_vector_retrieval_survives')
        expect(sql("SET ROLE pgwarc_fault_reader; SELECT doc_id FROM bm25_search('stable',10);"), str(stable_doc_id), 'sigterm_selective_reader_grant_survives')
        expect(sql("SELECT count(*) FROM pg_proc p JOIN pg_depend d ON d.classid='pg_proc'::regclass AND d.objid=p.oid JOIN pg_extension e ON d.refclassid='pg_extension'::regclass AND d.refobjid=e.oid CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a WHERE e.extname='pgwarc_lance' AND d.deptype='e' AND a.grantee=0 AND a.privilege_type='EXECUTE';"), '0', 'sigterm_public_execute_remains_revoked')
        report['timing_seconds']['sigterm_total_recovery_and_verified_state'] = round(time.monotonic() - term_started, 3)

        # This dataset persists across container restart. Observe a completed
        # external write inside a still-open SQL transaction before SIGKILL.
        crash_uri = '/tmp/pgwarc_crash.lance'
        sql(f"SELECT lance_create_table('{crash_uri}',4,false); SELECT bm25_index_document(10,'beforecrash'); SELECT lance_upsert_many('{crash_uri}',ARRAY[10]::bigint[],ARRAY[0,0,0,0]::float4[],4,ARRAY['beforecrash']);")
        revision_uri = '/tmp/pgwarc_revision_crash.lance'
        revision_doc_id, revision_before, revision_after = 92, 'c' * 64, 'd' * 64
        sql(f"""SELECT lance_create_table('{revision_uri}',4,false);
            SELECT bm25_index_document({revision_doc_id},'committed revision baseline');
            SELECT lance_outbox_enqueue_revision('{revision_uri}',ARRAY[{revision_doc_id}]::bigint[],
                ARRAY[0,1,0,0]::real[],4,ARRAY['committed-revision']::text[],ARRAY['{revision_before}']::text[]);
            SELECT status FROM lance_outbox_process_next();""")
        expect(sql(f"SELECT revision FROM lance_scan_document_state('{revision_uri}',10) WHERE doc_id={revision_doc_id};"),
               revision_before, 'sigkill_fixture_revision_baseline_committed')
        writer = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        writer.stdin.write(f"SET application_name='pgwarc_storage_crash_writer'; BEGIN; SELECT bm25_index_document(10,'uncommitted'); SELECT lance_upsert_many('{crash_uri}',ARRAY[10]::bigint[],ARRAY[1,0,0,0]::float4[],4,ARRAY['externalcommitted']); SELECT bm25_index_document({revision_doc_id},'uncommitted revision update'); SELECT lance_outbox_enqueue_revision('{revision_uri}',ARRAY[{revision_doc_id}]::bigint[],ARRAY[0,2,0,0]::real[],4,ARRAY['uncommitted-revision']::text[],ARRAY['{revision_after}']::text[]); SELECT pg_sleep(60); COMMIT;\n")
        writer.stdin.close()
        deadline = time.monotonic() + 30
        while sql("SELECT count(*) FROM pg_stat_activity WHERE application_name='pgwarc_storage_crash_writer' AND wait_event='PgSleep';") != '1':
            if writer.poll() is not None or time.monotonic() > deadline:
                raise AssertionError('did not observe completed external write in open SQL transaction')
            time.sleep(.05)
        expect(sql(f"SELECT label FROM lance_scan('{crash_uri}',10);"), 'externalcommitted', 'external_commit_observed_before_crash')
        version = sql(f"SELECT max(version) FROM lance_dataset_versions('{crash_uri}');")
        crash_started = time.monotonic()
        run(['docker', 'kill', '--signal', 'KILL', container])
        writer.wait(timeout=20)
        expect(run(['docker', 'inspect', '--format', '{{.State.ExitCode}}', container]), '137', 'container_exit_confirms_sigkill')
        report['crash_writer_returncode'] = writer.returncode
        run(['docker', 'start', container])
        crash_ready_started = time.monotonic()
        ready()
        report['timing_seconds']['sigkill_restart_ready'] = round(time.monotonic() - crash_ready_started, 3)
        logs = subprocess.run(['docker', 'logs', '--tail', '80', container], text=True, capture_output=True, timeout=30, check=True)
        report['postgres_recovery_logs'] = (logs.stdout + logs.stderr)[-12000:]
        if 'automatic recovery in progress' not in report['postgres_recovery_logs']:
            raise AssertionError('PostgreSQL did not report automatic crash recovery')
        checks['postgres_automatic_crash_recovery_observed'] = True
        checks['isolated_postgres_sigkill_and_restart'] = True
        expect(sql("SELECT content FROM pgwarc_lance.bm25_doc WHERE doc_id=10;"), 'beforecrash', 'crash_recovery_sql_uncommitted_update_absent')
        expect(sql(f"SELECT max(version) FROM lance_dataset_versions('{crash_uri}');"), version, 'crash_recovery_external_version_retained')
        expect(sql(f"SELECT label FROM lance_scan('{crash_uri}',10);"), 'externalcommitted', 'crash_recovery_external_write_retained')
        expect(sql(f"SELECT count(*) FROM pgwarc_lance.lance_write_log WHERE uri='{crash_uri}' AND op='upsert_many';"), '1', 'crash_recovery_uncommitted_write_log_absent')
        expect(sql(f"SELECT count(*) FROM pgwarc_lance.bm25_doc WHERE doc_id IN ({stable_doc_id},{term_doc_id},{revision_doc_id});"), '3', 'sigkill_committed_bm25_counts_survive')
        expect(sql(f"SELECT expected_revision FROM pgwarc_lance.lance_document_revision WHERE uri='{revision_uri}' AND doc_id={revision_doc_id};"), revision_before, 'sigkill_uncommitted_revision_mapping_rolls_back')
        expect(sql(f"SELECT revision FROM lance_scan_document_state('{revision_uri}',10) WHERE doc_id={revision_doc_id};"), revision_before, 'sigkill_uncommitted_revision_vector_never_reaches_lance')
        expect(sql(f"SELECT doc_id FROM bm25_search('baseline',10) WHERE doc_id={revision_doc_id};"), str(revision_doc_id), 'sigkill_committed_revision_bm25_retrieval_survives')
        expect(sql(f"SELECT id FROM lance_vector_search('{revision_uri}',ARRAY[0,1,0,0]::real[],10) WHERE id={revision_doc_id};"), str(revision_doc_id), 'sigkill_committed_revision_vector_retrieval_survives')
        expect(sql("SET ROLE pgwarc_fault_reader; SELECT doc_id FROM bm25_search('stable',10);"), str(stable_doc_id), 'sigkill_selective_reader_grant_survives')
        expect(sql("SELECT count(*) FROM pgwarc_lance.lance_outbox WHERE uri='/tmp/pgwarc_revision_crash.lance' AND state='pending';"), '0', 'sigkill_uncommitted_outbox_event_is_absent')
        sql(f"BEGIN; SELECT bm25_index_document(10,'replayed'); SELECT lance_upsert_many('{crash_uri}',ARRAY[10]::bigint[],ARRAY[2,0,0,0]::float4[],4,ARRAY['replayed']); COMMIT;")
        expect(sql(f"SELECT count(*)||'|'||count(DISTINCT id)||'|'||bool_and(label='replayed') FROM lance_scan('{crash_uri}',10);"), '1|1|true', 'crash_replay_has_unique_current_row')
        expect(sql("SELECT content FROM pgwarc_lance.bm25_doc WHERE doc_id=10;"), 'replayed', 'crash_replay_sql_and_external_content_agree')
        sql(f"""BEGIN;
            SELECT bm25_index_document({revision_doc_id},'recovered revision');
            SELECT lance_outbox_enqueue_revision('{revision_uri}',ARRAY[{revision_doc_id}]::bigint[],
                ARRAY[0,2,0,0]::real[],4,ARRAY['recovered-revision']::text[],ARRAY['{revision_after}']::text[]);
            COMMIT;
            SELECT status FROM lance_outbox_process_next();""")
        expect(sql(f"SELECT expected_revision FROM pgwarc_lance.lance_document_revision WHERE uri='{revision_uri}' AND doc_id={revision_doc_id};"), revision_after, 'sigkill_revision_retry_advances_mapping')
        expect(sql(f"SELECT revision FROM lance_scan_document_state('{revision_uri}',10) WHERE doc_id={revision_doc_id};"), revision_after, 'sigkill_revision_retry_advances_vector')
        expect(sql(f"SELECT doc_id FROM bm25_search('recovered',10) WHERE doc_id={revision_doc_id};"), str(revision_doc_id), 'sigkill_revision_retry_restores_bm25_retrieval')
        expect(sql(f"SELECT id FROM lance_vector_search('{revision_uri}',ARRAY[0,2,0,0]::real[],10) WHERE id={revision_doc_id};"), str(revision_doc_id), 'sigkill_revision_retry_restores_vector_retrieval')
        expect(sql("SET ROLE pgwarc_fault_reader; SELECT doc_id FROM bm25_search('stable',10);"), str(stable_doc_id), 'sigkill_reader_grant_survives_revision_retry')
        report['timing_seconds']['sigkill_total_reconciliation_and_verified_state'] = round(time.monotonic() - crash_started, 3)
        report['status'] = 'passed'
        return 0
    except Exception as exc:
        report.update(status='failed', error=str(exc))
        raise
    finally:
        if created:
            subprocess.run(['docker', 'rm', '-f', '-v', container], capture_output=True, timeout=30, check=True)
        if writer:
            writer.wait(timeout=20)
            report['crash_writer_stderr'] = writer.stderr.read()[-4000:]
        text = json.dumps(report, indent=2) + '\n'
        if args.json_output:
            args.json_output.parent.mkdir(parents=True, exist_ok=True)
            args.json_output.write_text(text)
        print(text, end='')


if __name__ == '__main__':
    raise SystemExit(main())
