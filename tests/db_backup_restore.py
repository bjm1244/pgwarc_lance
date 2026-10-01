#!/usr/bin/env python3
"""Verify SQL and Lance backup/restore using newly-created disposable databases.

No existing database data is dumped or changed. Requires CREATE DATABASE/ROLE
and extension installation privileges. Snapshot directories remain for inspection.
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import uuid
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exec-prefix", default="docker compose exec -T postgres")
    parser.add_argument("--pg-bin", default="/usr/lib/postgresql/16/bin")
    parser.add_argument("--host", default="/var/run/postgresql")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--user", default="pgwarc_lance")
    parser.add_argument("--upgrade-from", help="install this old version, seed data, then ALTER EXTENSION UPDATE")
    parser.add_argument("--legacy-public-execute", action="store_true", help="simulate legacy PUBLIC access and exercise pre-upgrade remediation")
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    prefix = shlex.split(args.exec_prefix)
    connection = ["-h", args.host, "-p", str(args.port), "-U", args.user]
    suffix = uuid.uuid4().hex[:16]
    source_db, restored_db = f"pgwarc_backup_{suffix}", f"pgwarc_restore_{suffix}"
    role = f"pgwarc_backup_reader_{suffix}"
    uri = f"/tmp/pgwarc_backup_{suffix}.lance"
    snapshot = f"/tmp/pgwarc_snapshot_{suffix}.lance"
    restored_uri = f"/tmp/pgwarc_restored_{suffix}.lance"
    databases: list[str] = []
    role_created = False
    evidence: dict[str, object] = {"status": "running", "checks": {}, "snapshot": snapshot}
    checks = evidence["checks"]

    def run(command: list[str], text: str | None = None) -> str:
        result = subprocess.run(prefix + command, input=text, text=True, capture_output=True, timeout=120)
        if result.returncode:
            raise AssertionError(f"command failed: {result.stderr}")
        return result.stdout.strip()

    def tool(name: str) -> list[str]:
        return [str(Path(args.pg_bin) / name)] + connection

    def sql(db: str, text: str) -> str:
        return run(tool("psql") + ["-X", "-q", "-At", "-v", "ON_ERROR_STOP=1", "-d", db], text)

    def expect(actual: str, expected: str, name: str) -> None:
        if actual != expected:
            raise AssertionError(f"{name}: expected {expected!r}, got {actual!r}")
        checks[name] = True

    summary_sql = """SELECT json_build_object(
        'docs', (SELECT count(*) FROM pgwarc_lance.bm25_doc),
        'terms', (SELECT count(*) FROM pgwarc_lance.bm25_term),
        'records', (SELECT count(*) FROM pgwarc_lance.warc_record),
        'writes', (SELECT count(*) FROM pgwarc_lance.lance_write_log),
        'search', (SELECT json_agg(doc_id ORDER BY doc_id) FROM bm25_search('shared', 10)));"""
    try:
        for db in [source_db, restored_db]:
            run(tool("createdb") + [db])
            databases.append(db)
        if args.upgrade_from:
            old_version = args.upgrade_from.replace("'", "''")
            sql(source_db, f"CREATE EXTENSION pgwarc_lance VERSION '{old_version}';")
        else:
            sql(source_db, "CREATE EXTENSION pgwarc_lance;")
        sql(source_db, f"""
            SELECT bm25_index_document(1, 'first shared document');
            SELECT bm25_index_document(2, 'second shared document');
            INSERT INTO pgwarc_lance.warc_record (doc_id,target_uri,text_len,source_file)
                VALUES (1,'https://example.test/one',21,'backup.warc'),
                       (2,'https://example.test/two',22,'backup.warc');
            SELECT lance_create_table('{uri}',4,false);
            SELECT lance_insert_many('{uri}',ARRAY[1,2]::bigint[],
                ARRAY[0,0,0,0,10,0,0,0]::float4[],4,ARRAY['first','second']::text[]);
            SELECT setval('pgwarc_lance.lance_write_log_id_seq',10000,true);
            CREATE ROLE {role} NOLOGIN;
        """)
        role_created = True
        sql(source_db, f"""
            GRANT USAGE ON SCHEMA pgwarc_lance TO {role};
            GRANT SELECT ON pgwarc_lance.bm25_doc, pgwarc_lance.bm25_term TO {role};
            GRANT EXECUTE ON FUNCTION bm25_search(text,integer) TO {role};
        """)
        original_summary = json.loads(sql(source_db, summary_sql))
        original_versions = sql(source_db, f"SELECT string_agg(version::text,',' ORDER BY version) FROM lance_dataset_versions('{uri}');")
        if args.upgrade_from:
            if args.legacy_public_execute:
                sql(source_db, "GRANT EXECUTE ON FUNCTION bm25_search(text,integer) TO PUBLIC;")
                try:
                    sql(source_db, "ALTER EXTENSION pgwarc_lance UPDATE;")
                except AssertionError as exc:
                    if "PUBLIC EXECUTE is still enabled" not in str(exc):
                        raise
                else:
                    raise AssertionError("unsafe legacy ACL did not block upgrade")
                checks["unsafe_legacy_acl_blocks_upgrade"] = True
                remediation = Path(__file__).resolve().parents[1] / "sql/pgwarc_lance-restrict-access.sql"
                sql(source_db, remediation.read_text())
                checks["legacy_acl_remediated_outside_upgrade"] = True
            sql(source_db, "ALTER EXTENSION pgwarc_lance UPDATE;")
            expect(sql(source_db, f"SELECT extversion <> '{old_version}' FROM pg_extension WHERE extname='pgwarc_lance';"), "t", "extension_version_updated")
            if json.loads(sql(source_db, summary_sql)) != original_summary:
                raise AssertionError("upgrade changed SQL data or BM25 results")
            expect(sql(source_db, f"SELECT string_agg(version::text,',' ORDER BY version) FROM lance_dataset_versions('{uri}');"), original_versions, "upgrade_preserves_lance_versions")
            expect(sql(source_db, f"SET ROLE {role}; SELECT count(*) FROM bm25_search('shared',10);"), "2", "upgrade_preserves_selective_grants")
            checks["populated_extension_upgrade"] = True
        expect(sql(source_db, "SELECT cardinality(extconfig) FROM pg_extension WHERE extname='pgwarc_lance';"), "5", "all_tables_and_sequence_registered")
        # The fixture has no concurrent writers. Production must quiesce writers
        # before taking the SQL and filesystem snapshots as one backup set.
        dump = run(tool("pg_dump") + ["--no-owner", "-d", source_db])
        for table in ["bm25_doc", "bm25_term", "warc_record", "lance_write_log"]:
            if f"COPY pgwarc_lance.{table} " not in dump:
                raise AssertionError(f"pg_dump omitted {table} data")
        checks["dump_contains_all_extension_table_data"] = True
        run(["cp", "-a", uri, snapshot])
        run(["cp", "-a", snapshot, restored_uri])
        sql(restored_db, dump)
        if json.loads(sql(restored_db, summary_sql)) != original_summary:
            raise AssertionError("restored SQL data/search results differ")
        checks["sql_data_and_bm25_results_restored"] = True
        expect(sql(restored_db, "SELECT nextval('pgwarc_lance.lance_write_log_id_seq');"), "10001", "write_log_sequence_state_restored")
        expect(sql(restored_db, f"SELECT lance_count('{restored_uri}');"), "2", "lance_row_count_restored")
        expect(sql(restored_db, f"SELECT string_agg(version::text,',' ORDER BY version) FROM lance_dataset_versions('{restored_uri}');"), original_versions, "lance_versions_restored")
        expect(sql(restored_db, f"SELECT id FROM lance_vector_search('{restored_uri}',ARRAY[0,0,0,0]::float4[],1);"), "1", "vector_retrieval_restored")
        expect(sql(restored_db, f"SET ROLE {role}; SELECT count(*) FROM bm25_search('shared',10);"), "2", "selective_reader_grants_restored")
        expect(sql(restored_db, "SELECT count(*) FROM pg_proc p JOIN pg_depend d ON d.classid='pg_proc'::regclass AND d.objid=p.oid JOIN pg_extension e ON d.refclassid='pg_extension'::regclass AND d.refobjid=e.oid CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a WHERE e.extname='pgwarc_lance' AND d.deptype='e' AND a.grantee=0 AND a.privilege_type='EXECUTE';"), "0", "public_execute_remains_revoked_after_restore")
        evidence["status"] = "passed"
        return 0
    except Exception as exc:
        evidence["status"] = "failed"
        evidence["error"] = str(exc)
        raise
    finally:
        for db in reversed(databases):
            run(tool("dropdb") + ["--force", db])
        if role_created:
            sql("postgres", f"DROP ROLE {role};")
        output = json.dumps(evidence, indent=2) + "\n"
        if args.json_output:
            args.json_output.parent.mkdir(parents=True, exist_ok=True)
            args.json_output.write_text(output)
        print(output, end="")


if __name__ == "__main__":
    raise SystemExit(main())
