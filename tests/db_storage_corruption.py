#!/usr/bin/env python3
"""Damage generated Lance copies and restore a stopped-writer backup.

Uses two new network-isolated PostgreSQL containers, no published ports and no host
data mounts. Tests truncated manifests, missing/truncated data files, backend
reuse, absence of partial hybrid results, rejection of an incomplete dataset
copy and a clean-instance SQL/Lance restore.
The generated NOLOGIN reader is restored from a separate role dump without passwords.
This is not a host power-loss or real storage-stack certification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import check_release_artifacts
import smoke_release_artifacts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist-dir', type=Path, required=True)
    parser.add_argument('--version', default=check_release_artifacts.cargo_version())
    parser.add_argument('--postgres-image', default='postgres:16')
    parser.add_argument('--json-output', type=Path)
    args = parser.parse_args()
    errors = check_release_artifacts.validate_artifacts(args.dist_dir, version=args.version, pg_major=16)
    if errors:
        raise ValueError('; '.join(errors))
    suffix = uuid.uuid4().hex[:16]
    container = 'pgwarc_storage_corrupt_' + suffix
    restore_container = 'pgwarc_storage_restore_' + suffix
    source_db, restored_db = 'pgwarc_source_' + suffix, 'pgwarc_restored_' + suffix
    reader = 'pgwarc_corrupt_reader_' + suffix
    root = '/tmp/pgwarc_corrupt_' + suffix
    uri, snapshot = root + '/source.lance', root + '/snapshot.lance'
    manifest_uri, data_uri = root + '/bad_manifest.lance', root + '/bad_data.lance'
    recovered_uri = root + '/recovered.lance'
    report = {'status': 'running', 'container': container, 'version': args.version,
              'restore_container': restore_container,
              'postgres_image': args.postgres_image, 'checks': {}, 'error_observations': {},
              'timing_seconds': {},
              'scope': 'generated local Lance copies and quiescent backup; no host power-loss, active-writer backup or real storage-stack claim'}
    checks, created = report['checks'], []

    def run(argv: list[str], text: str | None = None, timeout: float = 60) -> str:
        result = subprocess.run(argv, input=text, text=True, capture_output=True, timeout=timeout)
        if result.returncode:
            raise AssertionError(f'{argv}: {result.stderr[-4000:]}')
        return result.stdout.strip()

    def command(db: str, target: str = container) -> list[str]:
        return ['docker', 'exec', '-i', target, 'psql', '-U', 'postgres', '-d', db,
                '-X', '-q', '-At', '-v', 'ON_ERROR_STOP=1', '-v', 'VERBOSITY=verbose']

    def sql(db: str, text: str, target: str = container) -> str:
        return run(command(db, target), text)

    def restored_sql(text: str) -> str:
        return sql(restored_db, text, restore_container)

    def expect(actual: str, expected: str, name: str) -> None:
        if actual != expected:
            raise AssertionError(f'{name}: expected {expected!r}, got {actual!r}')
        checks[name] = True

    def copy(source: str, destination: str) -> None:
        run(['docker', 'exec', container, 'cp', '-a', source, destination])

    def inventory(path: str) -> list[str]:
        return run(['docker', 'exec', container, 'find', path, '-type', 'f', '-printf', '%P\n']).splitlines()

    def file_hashes(path: str, target: str = container) -> dict[str, str]:
        # Copy only this fixture's small dataset into a generated host temporary
        # directory. No existing host dataset is read or modified.
        with tempfile.TemporaryDirectory(prefix='pgwarc_corrupt_inventory_') as directory:
            run(['docker', 'cp', target + ':' + path + '/.', directory])
            base = Path(directory)
            return {str(f.relative_to(base)): hashlib.sha256(f.read_bytes()).hexdigest()
                    for f in sorted(base.rglob('*')) if f.is_file()}

    def error_reuses_backend(expression: str, name: str) -> None:
        result = subprocess.run(command(source_db) + ['-v', 'ON_ERROR_STOP=0'],
            input=f"SET ROLE {reader}; SET statement_timeout='5s'; SELECT pg_backend_pid(); "
                  f"SELECT * FROM {expression}; SELECT pg_backend_pid(); SELECT 'backend-reusable';\n",
            text=True, capture_output=True, timeout=15)
        pids = [line for line in result.stdout.splitlines() if re.fullmatch(r'\d+', line)]
        if result.returncode or 'ERROR:  XX000:' not in result.stderr or len(pids) != 2 \
                or pids[0] != pids[1] or result.stdout.splitlines() != [pids[0], pids[1], 'backend-reusable']:
            raise AssertionError(f'{name}: no bounded internal-error/backend reuse: {result.stdout} {result.stderr}')
        report['error_observations'][name] = {'sqlstate': 'XX000', 'backend_pid': pids[0],
                                             'stderr': result.stderr[-4000:]}
        checks[name] = True

    summary = """SELECT json_build_object(
        'docs', (SELECT count(*) FROM pgwarc_lance.bm25_doc),
        'terms', (SELECT count(*) FROM pgwarc_lance.bm25_term),
        'records', (SELECT count(*) FROM pgwarc_lance.warc_record),
        'writes', (SELECT count(*) FROM pgwarc_lance.lance_write_log),
        'revisions', (SELECT json_agg(json_build_array(doc_id,expected_revision,is_deleted) ORDER BY doc_id) FROM pgwarc_lance.lance_document_revision),
        'outbox', (SELECT count(*) FROM pgwarc_lance.lance_outbox),
        'bm25_ids', (SELECT json_agg(doc_id ORDER BY doc_id) FROM bm25_search('shared',10)));"""
    try:
        run(['docker', 'run', '-d', '--name', container, '--network', 'none',
             '--cpus', '2', '--memory', '2g', '-e', 'POSTGRES_HOST_AUTH_METHOD=trust', args.postgres_image])
        created.append(container)
        smoke_release_artifacts.wait_until_ready(container=container, user='postgres', database='postgres', timeout_seconds=90)
        smoke_release_artifacts.install_artifacts(container=container, dist_dir=args.dist_dir, version=args.version)
        for db in [source_db]:
            run(['docker', 'exec', container, 'createdb', '-U', 'postgres', db])
            sql('postgres', f"ALTER DATABASE {db} SET pgwarc_lance.allowed_uri_prefix='{root}'; ALTER DATABASE {db} SET pgwarc_lance.max_scan_rows=100; ALTER DATABASE {db} SET pgwarc_lance.allow_destructive_ops=off;")
        run(['docker', 'exec', container, 'mkdir', root])
        run(['docker', 'exec', container, 'chown', 'postgres:postgres', root])
        revisions = ['1' * 64, '2' * 64, '3' * 64]
        sql(source_db, f"""CREATE EXTENSION pgwarc_lance;
            SELECT bm25_index_document(1,'first shared');
            SELECT bm25_index_document(2,'second shared');
            SELECT bm25_index_document(3,'third shared');
            INSERT INTO pgwarc_lance.warc_record(doc_id,target_uri,text_len,source_file)
                VALUES(1,'https://example.test/one',12,'recovery.warc'),
                      (2,'https://example.test/two',13,'recovery.warc'),
                      (3,'https://example.test/three',12,'recovery.warc');
            SELECT lance_create_table('{uri}',4,false);
            SELECT lance_outbox_enqueue_revision('{uri}',ARRAY[1,2,3]::bigint[],
                ARRAY[0,0,0,0,1,0,0,0,2,0,0,0]::float4[],4,
                ARRAY['first','second','third']::text[],ARRAY['{revisions[0]}','{revisions[1]}','{revisions[2]}']::text[]);
            SELECT status FROM lance_outbox_process_next();
            SELECT setval('pgwarc_lance.lance_write_log_id_seq',12345,true);
            CREATE ROLE {reader} NOLOGIN;
            ALTER ROLE {reader} SET work_mem='4MB';
            ALTER ROLE {reader} SET statement_timeout='7s';
            GRANT USAGE ON SCHEMA public,pgwarc_lance TO {reader};
            GRANT SELECT ON pgwarc_lance.bm25_doc,pgwarc_lance.bm25_term,pgwarc_lance.bm25_long_term,pgwarc_lance.warc_record TO {reader};
            GRANT SELECT (uri,doc_id,expected_revision,is_deleted) ON pgwarc_lance.lance_document_revision TO {reader};
            GRANT EXECUTE ON FUNCTION bm25_search(text,integer),lance_count(text),
                lance_scan(text,integer),lance_vector_search(text,real[],integer),
                hybrid_warc_search(text,real[],text,integer) TO {reader};""")
        pending_revision = '4' * 64
        sql(source_db, f"""BEGIN;
            SELECT bm25_index_document(4,'fourth shared pending');
            INSERT INTO pgwarc_lance.warc_record(doc_id,target_uri,text_len,source_file)
                VALUES(4,'https://example.test/four',21,'recovery.warc');
            SELECT lance_outbox_enqueue_revision('{uri}',ARRAY[4]::bigint[],
                ARRAY[3,0,0,0]::real[],4,ARRAY['fourth-pending']::text[],
                ARRAY['{pending_revision}']::text[]);
            COMMIT;""")
        expect(sql(source_db, "SELECT extversion FROM pg_extension WHERE extname='pgwarc_lance';"),
               args.version, 'tested_artifact_installed')
        original = json.loads(sql(source_db, summary))
        pending_event_id = sql(source_db, f"SELECT id FROM pgwarc_lance.lance_outbox WHERE uri='{uri}' AND state='pending';")
        expect(sql(source_db, f"SELECT state||'|'||cardinality(ids)||'|'||labels[1] FROM pgwarc_lance.lance_outbox WHERE id={pending_event_id};"),
               'pending|1|fourth-pending', 'backup_fixture_has_committed_pending_revision_job')
        expect(sql(source_db, f"SELECT lance_count('{uri}');"), '3', 'healthy_fixture_has_three_vectors')
        outbox_next_id = sql(source_db, "SELECT last_value+1 FROM pgwarc_lance.lance_outbox_id_seq;")
        vector = 'ARRAY[0,0,0,0]::float4[]'
        hybrid = lambda path: f"hybrid_warc_search('shared',{vector},'{path}',3)"
        expected_hybrid = sql(source_db, f'SELECT json_agg(t) FROM {hybrid(uri)} t;')
        expected_vector_ids = sql(source_db, f"SELECT string_agg(id::text,',' ORDER BY id) FROM lance_vector_search('{uri}',{vector},3);")
        expected_bm25_ids = sql(source_db, "SELECT string_agg(doc_id::text,',' ORDER BY doc_id) FROM bm25_search('shared',10);")
        expected_scan = sql(source_db, f"SELECT json_agg(t ORDER BY id) FROM lance_scan('{uri}',10) t;")
        expected_states = sql(source_db, f"SELECT json_agg(json_build_array(doc_id,revision) ORDER BY doc_id) FROM lance_scan_document_state('{uri}',10);")
        versions = sql(source_db, f"SELECT string_agg(version::text,',' ORDER BY version) FROM lance_dataset_versions('{uri}');")
        latest = int(sql(source_db, f"SELECT max(version) FROM lance_dataset_versions('{uri}');"))

        # No writers exist during this fixture. Snapshot SQL and all Lance files
        # together before inducing any damage; record independent byte hashes.
        capture_started = time.monotonic()
        dump = run(['docker', 'exec', container, 'pg_dump', '-U', 'postgres', '--no-owner', source_db])
        for table in ['bm25_doc', 'bm25_term', 'bm25_long_term', 'warc_record', 'lance_write_log', 'lance_outbox', 'lance_document_revision']:
            if f'COPY pgwarc_lance.{table} ' not in dump:
                raise AssertionError('backup omitted ' + table)
        checks['coordinated_backup_contains_all_sql_tables'] = True
        settings_query = "SELECT json_build_object('allowed_uri_prefix',current_setting('pgwarc_lance.allowed_uri_prefix'),'max_scan_rows',current_setting('pgwarc_lance.max_scan_rows'),'allow_destructive_ops',current_setting('pgwarc_lance.allow_destructive_ops'));"
        database_settings = json.loads(sql(source_db, settings_query))
        roles_dump = run(['docker', 'exec', container, 'pg_dumpall', '-U', 'postgres',
                          '--roles-only', '--no-role-passwords'])
        role_creates = re.findall(r'^CREATE ROLE (\w+);$', roles_dump, re.MULTILINE)
        if sorted(role_creates) != sorted(['postgres', reader]):
            raise AssertionError('unexpected fixture roles in global backup: ' + repr(role_creates))
        if re.search(r'\bPASSWORD\b', roles_dump):
            raise AssertionError('role backup must not contain passwords')
        role_query = f"SELECT json_build_object('super',rolsuper,'inherit',rolinherit,'create_role',rolcreaterole,'create_db',rolcreatedb,'login',rolcanlogin,'replication',rolreplication,'bypass_rls',rolbypassrls,'limit',rolconnlimit,'settings',rolconfig) FROM pg_roles WHERE rolname='{reader}';"
        expected_role = sql('postgres', role_query)
        # PostgreSQL initializes the bootstrap role in every fresh cluster.
        # Omit only its exact CREATE, retaining attributes and the custom role.
        roles_restore = roles_dump.replace('CREATE ROLE postgres;\n', '', 1)
        checks['separate_global_role_backup_has_no_passwords'] = True
        copy(uri, snapshot)
        hashes = file_hashes(snapshot)
        if not hashes or hashes != file_hashes(uri):
            raise AssertionError('snapshot bytes do not match quiescent source')
        report['timing_seconds']['backup_capture'] = round(time.monotonic() - capture_started, 3)
        report['backup'] = {'sql_dump_sha256': hashlib.sha256(dump.encode()).hexdigest(),
                            'lance_file_sha256': hashes, 'versions': versions,
                            'outbox_sequence_next': outbox_next_id,
                            'lance_revisions': json.loads(expected_states),
                            'roles_dump_sha256': hashlib.sha256(roles_dump.encode()).hexdigest(),
                            'database_settings': database_settings,
                            'roles_restore_adjustment': 'omit only CREATE ROLE postgres; fresh cluster already has bootstrap role'}
        checks['snapshot_bytes_match_quiescent_source'] = True
        copy(snapshot, manifest_uri)
        copy(snapshot, data_uri)
        candidates = [n for n in inventory(manifest_uri) if Path(n).name in
                      [f'{latest}.manifest', f'{((1 << 64) - 1 - latest):020}.manifest']]
        if len(candidates) != 1:
            raise AssertionError('cannot identify exact latest manifest: ' + repr(candidates))
        manifest = manifest_uri + '/' + candidates[0]
        run(['docker', 'exec', container, 'truncate', '-s', '7', manifest])
        expect(run(['docker', 'exec', container, 'stat', '-c', '%s', manifest]), '7', 'latest_manifest_truncation_injected')
        error_reuses_backend(f"lance_count('{manifest_uri}')", 'truncated_manifest_error_and_backend_reuse')
        error_reuses_backend(hybrid(manifest_uri), 'truncated_manifest_hybrid_fails_without_partial_results')

        files = [n for n in inventory(data_uri) if n.endswith('.lance')]
        if len(files) != 1:
            raise AssertionError('tiny fixture must contain exactly one data object: ' + repr(files))
        data = data_uri + '/' + files[0]
        preserved = root + '/preserved-data-object'
        run(['docker', 'exec', container, 'mv', data, preserved])
        checks['data_object_removal_injected'] = True
        # A manifest row count is not a content-integrity check.
        expect(sql(source_db, f"SET ROLE {reader}; SELECT lance_count('{data_uri}');"), '3', 'metadata_count_cannot_detect_missing_data')
        error_reuses_backend(f"lance_scan('{data_uri}',10)", 'missing_data_scan_error_and_backend_reuse')
        error_reuses_backend(hybrid(data_uri), 'missing_data_hybrid_fails_without_partial_results')
        run(['docker', 'exec', container, 'mv', preserved, data])
        run(['docker', 'exec', container, 'truncate', '-s', '7', data])
        expect(run(['docker', 'exec', container, 'stat', '-c', '%s', data]), '7', 'data_object_truncation_injected')
        error_reuses_backend(f"lance_vector_search('{data_uri}',{vector},3)", 'truncated_data_vector_error_and_backend_reuse')
        error_reuses_backend(hybrid(data_uri), 'truncated_data_hybrid_fails_without_partial_results')
        if json.loads(sql(source_db, summary)) != original:
            raise AssertionError('read corruption errors changed SQL data/write log')
        checks['read_corruption_errors_leave_sql_and_write_log_unchanged'] = True
        expect(sql(source_db, f"SELECT lance_count('{uri}');"), '3', 'healthy_dataset_is_unchanged')
        expect(sql(source_db, f"SELECT string_agg(id::text,',' ORDER BY id) FROM lance_vector_search('{uri}',{vector},3);"), expected_vector_ids, 'healthy_vector_retrieval_is_unchanged')
        expect(sql(source_db, f"SELECT json_agg(t) FROM {hybrid(uri)} t;"), expected_hybrid, 'healthy_hybrid_retrieval_is_unchanged')
        expect(sql(source_db, f"SELECT json_agg(json_build_array(doc_id,revision) ORDER BY doc_id) FROM lance_scan_document_state('{uri}',10);"), expected_states, 'healthy_lance_revisions_are_unchanged')
        expect(sql(source_db, f"SET ROLE {reader}; SELECT string_agg(doc_id::text,',' ORDER BY doc_id) FROM bm25_search('shared',10);"), expected_bm25_ids, 'corruption_fault_preserves_reader_search_grant')
        expect(sql(source_db, f"SET ROLE {reader}; SELECT string_agg(id::text,',' ORDER BY id) FROM lance_vector_search('{uri}',{vector},3);"), expected_vector_ids, 'corruption_fault_preserves_reader_vector_grant')
        if hashes != file_hashes(snapshot):
            raise AssertionError('corruption leaked into the backup snapshot')
        checks['backup_snapshot_unchanged_after_faults'] = True

        # Restore into a separately initialized cluster. Its role catalog and
        # filesystem do not share any source-cluster state or mounted data.
        restore_started = time.monotonic()
        run(['docker', 'run', '-d', '--name', restore_container, '--network', 'none',
             '--cpus', '2', '--memory', '2g', '-e', 'POSTGRES_HOST_AUTH_METHOD=trust', args.postgres_image])
        created.append(restore_container)
        smoke_release_artifacts.wait_until_ready(container=restore_container, user='postgres', database='postgres', timeout_seconds=90)
        report['timing_seconds']['fresh_cluster_ready'] = round(time.monotonic() - restore_started, 3)
        smoke_release_artifacts.install_artifacts(container=restore_container, dist_dir=args.dist_dir, version=args.version)
        expect(sql('postgres', f"SELECT count(*) FROM pg_roles WHERE rolname='{reader}';", restore_container), '0', 'reader_is_absent_in_fresh_cluster')
        expect(sql('postgres', "SELECT count(*) FROM pg_roles WHERE rolname='postgres' AND rolsuper;", restore_container), '1', 'fresh_cluster_bootstrap_role_is_verified')
        sql('postgres', roles_restore, restore_container)
        expect(sql('postgres', role_query, restore_container), expected_role, 'reader_role_attributes_restored_from_global_backup')
        run(['docker', 'exec', restore_container, 'createdb', '-U', 'postgres', restored_db])
        # Database-specific extension settings are an explicit recovery input,
        # not something a plain single-database pg_dump guarantees to preserve.
        for key, value in database_settings.items():
            escaped = value.replace("'", "''")
            sql('postgres', f"ALTER DATABASE {restored_db} SET pgwarc_lance.{key}='{escaped}';", restore_container)
        if json.loads(restored_sql(settings_query)) != database_settings:
            raise AssertionError('database URI/scan/destructive policy settings differ')
        checks['restored_database_uri_scan_and_destructive_policy_is_applied'] = True
        binary = args.dist_dir / 'pgwarc_lance.so'
        installed_sha = run(['docker', 'exec', restore_container, 'sha256sum', '/usr/lib/postgresql/16/lib/pgwarc_lance.so']).split()[0]
        expect(installed_sha, hashlib.sha256(binary.read_bytes()).hexdigest(), 'fresh_instance_uses_identical_release_binary')
        restored_sql(dump)
        if json.loads(restored_sql(summary)) != original:
            raise AssertionError('restored SQL summary differs from stopped-writer backup')
        checks['clean_instance_restores_sql_and_bm25_results'] = True

        # Model an interrupted filesystem restore: SQL and metadata exist, but
        # the Lance data object was not copied. This destination must fail
        # closed; discard it and retry from the immutable complete snapshot.
        interrupted_uri = root + '/interrupted.lance'
        run(['docker', 'exec', restore_container, 'mkdir', '-p', interrupted_uri])
        with tempfile.TemporaryDirectory(prefix='pgwarc_interrupted_restore_') as directory:
            run(['docker', 'cp', container + ':' + snapshot + '/.', directory])
            data_directory = Path(directory) / 'data'
            omitted_data_files = sorted(path.name for path in data_directory.iterdir() if path.is_file())
            for data_file in data_directory.iterdir():
                if data_file.is_file():
                    data_file.unlink()
            run(['docker', 'cp', directory + '/.', restore_container + ':' + interrupted_uri])
        run(['docker', 'exec', restore_container, 'chown', '-R', 'postgres:postgres', interrupted_uri])
        partial_scan = subprocess.run(
            command(restored_db, restore_container) + ['-v', 'ON_ERROR_STOP=0'],
            input=f"SELECT id FROM lance_scan('{interrupted_uri}',10);\n",
            text=True, capture_output=True, timeout=30,
        )
        if 'ERROR:  XX000:' not in partial_scan.stderr \
                or re.search(r'^\d+$', partial_scan.stdout, re.MULTILINE):
            raise AssertionError(f'incomplete restore was not rejected without rows: {partial_scan.stdout} {partial_scan.stderr}')
        report['error_observations']['interrupted_restore_copy'] = {
            'sqlstate': 'XX000', 'psql_exit_code': partial_scan.returncode,
            'stderr': partial_scan.stderr[-4000:],
            'omitted_data_objects': omitted_data_files,
        }
        checks['interrupted_lance_copy_rejected_without_partial_results'] = True
        expect(json.loads(restored_sql(summary)), original, 'interrupted_restore_keeps_restored_sql_counts_and_revisions')

        run(['docker', 'exec', restore_container, 'rm', '-rf', interrupted_uri])
        run(['docker', 'exec', restore_container, 'mkdir', '-p', recovered_uri])
        with tempfile.TemporaryDirectory(prefix='pgwarc_clean_restore_') as directory:
            run(['docker', 'cp', container + ':' + snapshot + '/.', directory])
            run(['docker', 'cp', directory + '/.', restore_container + ':' + recovered_uri])
        run(['docker', 'exec', restore_container, 'chown', '-R', 'postgres:postgres', recovered_uri])
        report['timing_seconds']['fresh_restore_ready_and_validated'] = round(time.monotonic() - restore_started, 3)
        expect(restored_sql("SELECT nextval('pgwarc_lance.lance_write_log_id_seq');"), '12346', 'restored_write_log_sequence_is_correct')
        expect(restored_sql("SELECT nextval('pgwarc_lance.lance_outbox_id_seq');"), outbox_next_id, 'restored_outbox_sequence_is_correct')
        expect(restored_sql(f"SELECT string_agg(version::text,',' ORDER BY version) FROM lance_dataset_versions('{recovered_uri}');"), versions, 'restored_lance_versions_match_backup')
        expect(restored_sql(f"SELECT json_agg(t ORDER BY id) FROM lance_scan('{recovered_uri}',10) t;"), expected_scan, 'restored_ids_vectors_and_labels_match_backup')
        expect(restored_sql(f"SELECT json_agg(json_build_array(doc_id,revision) ORDER BY doc_id) FROM lance_scan_document_state('{recovered_uri}',10);"), expected_states, 'restored_lance_document_revisions_match_backup')
        expect(restored_sql(f"SET ROLE {reader}; SELECT json_agg(t) FROM {hybrid(recovered_uri)} t;"), expected_hybrid, 'restored_selective_reader_hybrid_and_metadata_match_backup')
        expect(restored_sql(f"SELECT state||'|'||cardinality(ids)||'|'||labels[1] FROM pgwarc_lance.lance_outbox WHERE id={pending_event_id};"), 'pending|1|fourth-pending', 'fresh_instance_restores_pending_outbox_payload')
        expect(restored_sql(f"SELECT lance_count('{recovered_uri}');"), '3', 'pending_restore_keeps_lance_at_matching_pre_delivery_version')
        expect(restored_sql("SELECT count(*) FROM pg_proc p JOIN pg_depend d ON d.classid='pg_proc'::regclass AND d.objid=p.oid JOIN pg_extension e ON d.refclassid='pg_extension'::regclass AND d.refobjid=e.oid CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a WHERE e.extname='pgwarc_lance' AND d.deptype='e' AND a.grantee=0 AND a.privilege_type='EXECUTE';"), '0', 'restored_public_function_execution_stays_revoked')
        if hashes != file_hashes(recovered_uri, restore_container):
            raise AssertionError('restored dataset bytes differ from backup')
        checks['restored_dataset_bytes_match_backup'] = True
        expect(restored_sql("SELECT extversion FROM pg_extension WHERE extname='pgwarc_lance';"), args.version, 'fresh_instance_extension_version_matches_backup')
        # Check grants for every extension function and configuration table,
        # not merely the operations the generated reader happened to exercise.
        acl_query = """SELECT json_build_object(
          'functions',(SELECT json_agg(json_build_array(p.proname,pg_get_function_identity_arguments(p.oid),p.proacl::text) ORDER BY p.proname,pg_get_function_identity_arguments(p.oid)) FROM pg_proc p JOIN pg_depend d ON d.classid='pg_proc'::regclass AND d.objid=p.oid JOIN pg_extension e ON d.refclassid='pg_extension'::regclass AND d.refobjid=e.oid WHERE e.extname='pgwarc_lance' AND d.deptype='e'),
          'tables',(SELECT json_agg(json_build_array(c.relname,c.relacl::text) ORDER BY c.relname) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='pgwarc_lance' AND c.relkind IN ('r','S')));"""
        expect(restored_sql(acl_query), sql(source_db, acl_query), 'fresh_instance_function_table_and_sequence_grants_match_source')
        restored_sql(f"UPDATE pgwarc_lance.lance_document_revision SET uri='{recovered_uri}' WHERE uri='{uri}'; UPDATE pgwarc_lance.lance_outbox SET uri='{recovered_uri}' WHERE uri='{uri}' AND state='pending';")
        expect(restored_sql("SELECT status FROM lance_outbox_process_next();"), 'completed', 'fresh_instance_replays_pending_outbox_job')
        expect(restored_sql(f"SELECT count(*)||'|'||count(DISTINCT id) FROM lance_scan('{recovered_uri}',10);"), '4|4', 'pending_outbox_recovery_materializes_unique_vector')
        expect(restored_sql(f"SELECT expected_revision FROM pgwarc_lance.lance_document_revision WHERE uri='{recovered_uri}' AND doc_id=4;"), pending_revision, 'pending_restore_keeps_expected_revision')
        expect(restored_sql(f"SELECT revision FROM lance_scan_document_state('{recovered_uri}',10) WHERE doc_id=4;"), pending_revision, 'pending_restore_applies_lance_revision')
        expect(restored_sql(f"SELECT doc_id FROM bm25_search('pending',10) WHERE doc_id=4;"), '4', 'pending_restore_preserves_bm25_retrieval')
        expect(restored_sql(f"SELECT id FROM lance_vector_search('{recovered_uri}',ARRAY[3,0,0,0]::real[],10) WHERE id=4;"), '4', 'pending_restore_preserves_vector_retrieval')
        expected_hybrid = restored_sql(f'SELECT json_agg(t) FROM {hybrid(recovered_uri)} t;')
        post_restore_revision = '4' * 64
        restored_sql(f"""SELECT bm25_index_document(1,'first shared');
            SELECT lance_outbox_enqueue_revision('{recovered_uri}',ARRAY[1]::bigint[],
                ARRAY[0,0,0,0]::real[],4,ARRAY['first']::text[],ARRAY['{post_restore_revision}']::text[]);
            SELECT status FROM lance_outbox_process_next();""")
        expect(restored_sql(f"SET ROLE {reader}; SELECT json_agg(t) FROM {hybrid(recovered_uri)} t;"), expected_hybrid, 'post_restore_write_and_reader_query_succeed')
        expect(restored_sql(f"SELECT lance_count('{recovered_uri}');"), '4', 'post_restore_upsert_preserves_unique_ids')
        expect(restored_sql(f"SELECT max(version) FROM lance_dataset_versions('{recovered_uri}');"), str(latest + 2), 'post_restore_write_creates_new_lance_version')
        expect(restored_sql(f"SELECT expected_revision FROM pgwarc_lance.lance_document_revision WHERE uri='{recovered_uri}' AND doc_id=1;"), post_restore_revision, 'post_restore_revision_mapping_accepts_new_write')
        expect(restored_sql(f"SELECT revision FROM lance_scan_document_state('{recovered_uri}',10) WHERE doc_id=1;"), post_restore_revision, 'post_restore_revisioned_outbox_write_reaches_lance')
        if hashes != file_hashes(snapshot):
            raise AssertionError('fresh-instance writes changed the original backup')
        checks['original_backup_unchanged_after_fresh_instance_write'] = True
        report['timing_seconds']['fresh_restore_complete_including_post_restore_write'] = round(time.monotonic() - restore_started, 3)
        report['status'] = 'passed'
        return 0
    except Exception as exc:
        report.update(status='failed', error=str(exc))
        raise
    finally:
        for owned in reversed(created):
            subprocess.run(['docker', 'rm', '-f', '-v', owned], capture_output=True, timeout=30, check=True)
        output = json.dumps(report, indent=2) + '\n'
        if args.json_output:
            args.json_output.parent.mkdir(parents=True, exist_ok=True)
            args.json_output.write_text(output)
        print(output, end='')


if __name__ == '__main__':
    raise SystemExit(main())
