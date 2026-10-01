-- Install-time privileges must restrict every extension function to its owner.
BEGIN;
CREATE ROLE pgwarc_lance_security_test NOLOGIN;
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_proc p
        JOIN pg_depend d ON d.classid = 'pg_proc'::regclass AND d.objid = p.oid
        JOIN pg_extension e ON d.refclassid = 'pg_extension'::regclass AND d.refobjid = e.oid
        CROSS JOIN LATERAL aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
        WHERE e.extname = 'pgwarc_lance' AND d.deptype = 'e'
          AND a.grantee = 0 AND a.privilege_type = 'EXECUTE'
    ) THEN
        RAISE EXCEPTION 'extension functions still allow PUBLIC EXECUTE';
    END IF;
END $$;
SET LOCAL ROLE pgwarc_lance_security_test;
DO $$
BEGIN
    BEGIN
        PERFORM hello_pgwarc_lance();
        RAISE EXCEPTION 'ungranted function was executable';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
END $$;
RESET ROLE;
GRANT EXECUTE ON FUNCTION hello_pgwarc_lance() TO pgwarc_lance_security_test;
SET LOCAL ROLE pgwarc_lance_security_test;
DO $$
BEGIN
    IF hello_pgwarc_lance() <> 'Hello, pgwarc_lance!' THEN
        RAISE EXCEPTION 'explicit function grant did not work';
    END IF;
END $$;
DO $$
BEGIN
    BEGIN
        PERFORM lance_count('/tmp/pgwarc-untrusted.lance');
        RAISE EXCEPTION 'grant on hello also allowed dataset access';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    BEGIN
        PERFORM set_config('pgwarc_lance.max_scan_rows', '0', false);
        RAISE EXCEPTION 'untrusted role changed scan limit';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
END $$;
RESET ROLE;
GRANT EXECUTE ON FUNCTION lance_create_table(text, integer, boolean) TO pgwarc_lance_security_test;
SET LOCAL ROLE pgwarc_lance_security_test;
DO $$
BEGIN
    BEGIN
        PERFORM lance_create_table('/tmp/pgwarc-no-log-permission.lance', 3, false);
        RAISE EXCEPTION 'dataset write succeeded without write-log permissions';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
END $$;
RESET ROLE;
SET LOCAL pgwarc_lance.allowed_uri_prefix = '/tmp';
DO $$
DECLARE
    candidate text;
BEGIN
    FOREACH candidate IN ARRAY ARRAY[
        '/tmp-sibling/dataset.lance',
        '/tmp/../var/lib/dataset.lance',
        'file:///tmp/%2e%2e/dataset.lance',
        's3://bucket/dataset.lance'
    ] LOOP
        BEGIN
            PERFORM lance_count(candidate);
            RAISE EXCEPTION 'restricted URI was accepted: %', candidate;
        EXCEPTION WHEN insufficient_privilege THEN NULL;
        END;
    END LOOP;
END $$;
ROLLBACK;
