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
              'scope': 'isolated tmpfs ENOSPC, OS permission error and SIGKILL container restart; not a hardware power-loss test',
              'checks': {}}
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

        uri = '/fault/full.lance'
        sql(f"SELECT lance_create_table('{uri}',4,false);")
        before = sql(f"SELECT max(version) FROM lance_dataset_versions('{uri}');")
        filler = subprocess.run(['docker', 'exec', container, 'dd', 'if=/dev/zero', 'of=/fault/filler', 'bs=65536', 'status=none'],
                                text=True, capture_output=True, timeout=30)
        if not filler.returncode or 'No space left on device' not in filler.stderr:
            raise AssertionError('isolated tmpfs was not filled to ENOSPC')
        checks['tmpfs_enospc_injected'] = True
        failed = subprocess.run(command + ['-v', 'ON_ERROR_STOP=0'], input=f"""BEGIN;
SELECT bm25_index_document(1,'mustrollback');
SELECT lance_upsert_many('{uri}',ARRAY[1]::bigint[],ARRAY[1,0,0,0]::float4[],4,ARRAY[repeat('q',4096)]);
ROLLBACK;
SELECT 'backend-reusable-after-enospc';
""", text=True, capture_output=True, timeout=60)
        if failed.returncode or 'No space left on device' not in failed.stderr or 'backend-reusable-after-enospc' not in failed.stdout:
            raise AssertionError(f'ENOSPC server error/backend reuse missing: {failed.stdout} {failed.stderr}')
        report['enospc_stderr'] = failed.stderr[-4000:]
        checks['server_enospc_and_same_backend_reuse'] = True
        expect(sql("SELECT count(*) FROM pgwarc_lance.bm25_doc WHERE doc_id=1;"), '0', 'enospc_sql_transaction_rolled_back')
        expect(sql(f"SELECT count(*) FROM pgwarc_lance.lance_write_log WHERE uri='{uri}' AND op='upsert_many';"), '0', 'enospc_write_log_rolled_back')
        run(['docker', 'exec', container, 'rm', '/fault/filler'])
        expect(sql(f"SELECT max(version) FROM lance_dataset_versions('{uri}');"), before, 'enospc_no_new_committed_dataset_version')
        expect(sql(f"SELECT lance_count('{uri}');"), '0', 'enospc_original_dataset_readable')
        sql(f"BEGIN; SELECT bm25_index_document(1,'retry'); SELECT lance_upsert_many('{uri}',ARRAY[1]::bigint[],ARRAY[1,0,0,0]::float4[],4,ARRAY['retry']); COMMIT;")
        expect(sql(f"SELECT count(*)||'|'||bool_and(label='retry') FROM lance_scan('{uri}',10);"), '1|true', 'enospc_retry_restores_one_current_row')

        # This dataset persists across container restart. Observe a completed
        # external write inside a still-open SQL transaction before SIGKILL.
        crash_uri = '/tmp/pgwarc_crash.lance'
        sql(f"SELECT lance_create_table('{crash_uri}',4,false); SELECT bm25_index_document(10,'beforecrash'); SELECT lance_upsert_many('{crash_uri}',ARRAY[10]::bigint[],ARRAY[0,0,0,0]::float4[],4,ARRAY['beforecrash']);")
        writer = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        writer.stdin.write(f"SET application_name='pgwarc_storage_crash_writer'; BEGIN; SELECT bm25_index_document(10,'uncommitted'); SELECT lance_upsert_many('{crash_uri}',ARRAY[10]::bigint[],ARRAY[1,0,0,0]::float4[],4,ARRAY['externalcommitted']); SELECT pg_sleep(60); COMMIT;\n")
        writer.stdin.close()
        deadline = time.monotonic() + 30
        while sql("SELECT count(*) FROM pg_stat_activity WHERE application_name='pgwarc_storage_crash_writer' AND wait_event='PgSleep';") != '1':
            if writer.poll() is not None or time.monotonic() > deadline:
                raise AssertionError('did not observe completed external write in open SQL transaction')
            time.sleep(.05)
        expect(sql(f"SELECT label FROM lance_scan('{crash_uri}',10);"), 'externalcommitted', 'external_commit_observed_before_crash')
        version = sql(f"SELECT max(version) FROM lance_dataset_versions('{crash_uri}');")
        run(['docker', 'kill', '--signal', 'KILL', container])
        writer.wait(timeout=20)
        expect(run(['docker', 'inspect', '--format', '{{.State.ExitCode}}', container]), '137', 'container_exit_confirms_sigkill')
        report['crash_writer_returncode'] = writer.returncode
        run(['docker', 'start', container])
        ready()
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
        sql(f"BEGIN; SELECT bm25_index_document(10,'replayed'); SELECT lance_upsert_many('{crash_uri}',ARRAY[10]::bigint[],ARRAY[2,0,0,0]::float4[],4,ARRAY['replayed']); COMMIT;")
        expect(sql(f"SELECT count(*)||'|'||count(DISTINCT id)||'|'||bool_and(label='replayed') FROM lance_scan('{crash_uri}',10);"), '1|1|true', 'crash_replay_has_unique_current_row')
        expect(sql("SELECT content FROM pgwarc_lance.bm25_doc WHERE doc_id=10;"), 'replayed', 'crash_replay_sql_and_external_content_agree')
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
