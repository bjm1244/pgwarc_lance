-- Preserve extension user data in pg_dump and verify restricted function access.
-- Existing explicit role grants and data are retained.
SELECT pg_catalog.pg_extension_config_dump('pgwarc_lance.bm25_doc', '');
SELECT pg_catalog.pg_extension_config_dump('pgwarc_lance.bm25_term', '');
SELECT pg_catalog.pg_extension_config_dump('pgwarc_lance.warc_record', '');
SELECT pg_catalog.pg_extension_config_dump('pgwarc_lance.lance_write_log', '');
SELECT pg_catalog.pg_extension_config_dump('pgwarc_lance.lance_write_log_id_seq', '');

-- Do not issue GRANT/REVOKE in an upgrade script: it can promote existing
-- user ACLs into pg_init_privs and make pg_dump omit them after an upgrade.
DO $pgwarc_upgrade_security$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_proc p
        JOIN pg_depend d ON d.classid='pg_proc'::regclass AND d.objid=p.oid
            AND d.refclassid='pg_extension'::regclass AND d.deptype='e'
        JOIN pg_extension e ON e.oid=d.refobjid
        CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
        WHERE e.extname='pgwarc_lance' AND a.grantee=0 AND a.privilege_type='EXECUTE'
    ) THEN
        RAISE EXCEPTION 'Run pgwarc_lance-restrict-access.sql outside ALTER EXTENSION before upgrading: PUBLIC EXECUTE is still enabled';
    END IF;
END
$pgwarc_upgrade_security$;
