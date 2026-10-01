#!/usr/bin/env python3
"""Cancel a locked upsert, observe storage, and retry in a new disposable DB.

This does not promise atomic rollback. The cancellation is synchronized with
an active upsert holding its dataset advisory lock; file commit may or may not
have occurred. Dataset files are retained, while the new database is removed.
"""
from __future__ import annotations
import argparse
import json
import shlex
import subprocess
import time
import uuid
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exec-prefix', default='docker compose exec -T postgres')
    parser.add_argument('--pg-bin', default='/usr/lib/postgresql/16/bin')
    parser.add_argument('--host', default='/var/run/postgresql')
    parser.add_argument('--port', type=int, default=5432)
    parser.add_argument('--user', default='pgwarc_lance')
    parser.add_argument('--json-output', type=Path)
    args = parser.parse_args()
    prefix = shlex.split(args.exec_prefix)
    connection = ['-h',args.host,'-p',str(args.port),'-U',args.user]
    suffix = uuid.uuid4().hex[:16]
    database, application = f'pgwarc_cancel_{suffix}', f'pgwarc_cancel_writer_{suffix}'
    uri = f'/tmp/pgwarc_cancel_{suffix}.lance'
    command = prefix + [str(Path(args.pg_bin)/'psql')] + connection + ['-d',database,'-X','-q','-At','-v','ON_ERROR_STOP=1','-v','VERBOSITY=verbose']
    report = {'status':'running','dataset':uri,'rows':10000,'vector_dim':384,'checks':{}}
    checks = report['checks']
    writer, created = None, False

    def sql(text: str, timeout: float = 60) -> str:
        result = subprocess.run(command,input=text,text=True,capture_output=True,timeout=timeout)
        if result.returncode:
            raise AssertionError(result.stderr)
        return result.stdout.strip()

    def expect(actual: str, expected: str, name: str) -> None:
        if actual != expected:
            raise AssertionError(f'{name}: expected {expected!r}, got {actual!r}')
        checks[name] = True

    try:
        subprocess.run(prefix+[str(Path(args.pg_bin)/'createdb')]+connection+[database],check=True,capture_output=True,timeout=30)
        created = True
        sql(f"CREATE EXTENSION pgwarc_lance; SELECT lance_create_table('{uri}',384,false);")
        initial_version = sql(f"SELECT max(version) FROM lance_dataset_versions('{uri}');")
        writer = subprocess.Popen(command+['-v','ON_ERROR_STOP=0'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        # Build argument arrays before the statement being cancelled. Reuse them
        # in the same session for retry, independent of SQL input parsing time.
        writer.stdin.write(f"""
SET application_name='{application}';
CREATE TEMP TABLE inputs AS SELECT ARRAY(SELECT x::bigint FROM generate_series(1,10000) x) AS ids,
    ARRAY(SELECT (x % 23)::float4 / 23::float4 FROM generate_series(1,3840000) x)::float4[] AS vectors,
    ARRAY(SELECT 'cancel-'||x||repeat('q',4096) FROM generate_series(1,10000) x) AS labels;
BEGIN;
SELECT bm25_index_document(1,'writecancel');
SELECT lance_upsert_many('{uri}',ids,vectors,384,labels) FROM inputs;
COMMIT;
SELECT 'backend-reusable-after-cancel';
BEGIN;
SELECT bm25_index_document(1,'writecancel');
SELECT lance_upsert_many('{uri}',ids,vectors,384,array_fill('retry'::text,ARRAY[10000])) FROM inputs;
COMMIT;
SELECT 'retry-complete';
SELECT pg_sleep(10);
""")
        writer.stdin.close()
        deadline = time.monotonic()+30
        backend = ''
        while not backend:
            backend = sql(f"SELECT a.pid FROM pg_stat_activity a WHERE a.application_name='{application}' AND a.state='active' AND a.query LIKE 'SELECT lance_upsert_many%' AND EXISTS (SELECT 1 FROM pg_locks l WHERE l.pid=a.pid AND l.locktype='advisory' AND l.granted);")
            if writer.poll() is not None or time.monotonic()>deadline:
                detail = writer.stderr.read()[-2000:] if writer.poll() is not None else 'writer still running'
                raise AssertionError('did not observe active locked upsert; cancellation was not exercised: ' + detail)
            if not backend:
                time.sleep(.01)
        checks['active_upsert_with_dataset_lock_observed'] = True
        expect(sql(f'SELECT pg_cancel_backend({int(backend)});'),'t','observed_writer_cancel_requested')
        # The writer script immediately retries in the reused backend. Observe
        # its sleep after that retry, rather than trusting client-only output.
        deadline = time.monotonic()+60
        while sql(f"SELECT count(*) FROM pg_stat_activity WHERE application_name='{application}' AND wait_event='PgSleep';") != '1':
            if writer.poll() is not None or time.monotonic()>deadline:
                raise AssertionError('writer did not complete retry in reused backend')
            time.sleep(.05)
        checks['writer_reused_backend_for_retry'] = True
        expect(sql(f"SELECT lance_count('{uri}');"),'10000','retry_has_exact_unique_row_count')
        expect(sql(f"SELECT count(*)||'|'||count(DISTINCT id)||'|'||bool_and(label='retry') FROM lance_scan('{uri}',10000);"),'10000|10000|true','retry_has_unique_ids_and_current_labels')
        expect(sql('SELECT content FROM pgwarc_lance.bm25_doc WHERE doc_id=1;'),'writecancel','retry_sql_document_committed')
        expect(sql(f"SELECT count(*) FROM pgwarc_lance.lance_write_log WHERE uri='{uri}' AND op='upsert_many';"),'1','only_successful_retry_write_log_committed')
        version = sql(f"SELECT max(version) FROM lance_dataset_versions('{uri}');")
        report['initial_version'],report['version_after_retry'] = int(initial_version),int(version)
        # Observe an interval after retry for a late older commit overwriting
        # the retried label. This is a fixture observation, not a proof that all
        # storage hardware or task schedules have been exhausted.
        time.sleep(2)
        expect(sql(f"SELECT max(version) FROM lance_dataset_versions('{uri}');"),version,'no_late_version_change_observed')
        expect(sql(f"SELECT bool_and(label='retry') FROM lance_scan('{uri}',10000);"),'t','no_late_stale_label_observed')
        writer.wait(timeout=20)
        stdout,stderr=writer.stdout.read(),writer.stderr.read()
        report['writer_stdout_tail'],report['writer_stderr_tail']=stdout[-4000:],stderr[-4000:]
        if writer.returncode or '57014' not in stderr or 'backend-reusable-after-cancel' not in stdout or 'retry-complete' not in stdout:
            raise AssertionError(f'cancellation/reuse diagnostic missing: {stdout[-1000:]} {stderr[-1000:]}')
        checks['server_cancellation_and_client_reuse_confirmed'] = True
        report['status']='passed'
        return 0
    except Exception as exc:
        report.update(status='failed',error=str(exc))
        raise
    finally:
        if writer and writer.poll() is None and created:
            sql(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name='{application}';")
            writer.wait(timeout=15)
        if writer:
            report['writer_returncode'] = writer.returncode
            if 'writer_stdout_tail' not in report:
                report['writer_stdout_tail'] = writer.stdout.read()[-4000:]
                report['writer_stderr_tail'] = writer.stderr.read()[-4000:]
        if created:
            subprocess.run(prefix+[str(Path(args.pg_bin)/'dropdb')]+connection+['--force',database],check=True,capture_output=True,timeout=30)
        text=json.dumps(report,indent=2)+'\n'
        if args.json_output:
            args.json_output.parent.mkdir(parents=True,exist_ok=True)
            args.json_output.write_text(text)
        print(text,end='')


if __name__=='__main__':
    raise SystemExit(main())
