-- The install and upgrade scripts revoke PUBLIC EXECUTE from every extension
-- function. Operational roles receive only their documented capabilities.
BEGIN;
CREATE ROLE pgwarc_lance_security_test NOLOGIN;
CREATE ROLE pgwarc_security_reader NOLOGIN;
CREATE ROLE pgwarc_security_writer NOLOGIN;
CREATE ROLE pgwarc_security_worker NOLOGIN;
CREATE ROLE pgwarc_security_doctor NOLOGIN;
CREATE ROLE pgwarc_security_admin NOLOGIN;

GRANT USAGE ON SCHEMA public, pgwarc_lance TO
    pgwarc_security_reader, pgwarc_security_writer,
    pgwarc_security_worker, pgwarc_security_doctor, pgwarc_security_admin;

GRANT SELECT ON pgwarc_lance.bm25_doc, pgwarc_lance.bm25_term,
    pgwarc_lance.bm25_long_term, pgwarc_lance.warc_record TO pgwarc_security_reader;
GRANT SELECT (uri, doc_id, expected_revision, is_deleted)
    ON pgwarc_lance.lance_document_revision TO pgwarc_security_reader;
GRANT SELECT (id, uri, state, ids)
    ON pgwarc_lance.lance_outbox TO pgwarc_security_reader;
GRANT SELECT ON pgwarc_lance.lance_outbox_status TO pgwarc_security_reader;
GRANT EXECUTE ON FUNCTION public.hybrid_warc_search(text,real[],text,integer),
    public.hybrid_warc_search_filtered(text,real[],text,jsonb,integer),
    public.hybrid_search_explain(text,real[],text,integer,integer,double precision,double precision,double precision),
    public.hybrid_warc_search_filtered_explain(text,real[],text,jsonb,integer,integer,double precision,double precision,double precision),
    public.lance_scan_document_state(text,integer),
    public.lance_dataset_versions(text)
    TO pgwarc_security_reader;

GRANT SELECT, INSERT, UPDATE ON pgwarc_lance.bm25_doc,
    pgwarc_lance.warc_record TO pgwarc_security_writer;
GRANT SELECT (uri, doc_id, expected_revision, is_deleted)
    ON pgwarc_lance.lance_document_revision TO pgwarc_security_writer;
GRANT SELECT, INSERT, DELETE ON pgwarc_lance.bm25_term,
    pgwarc_lance.bm25_long_term TO pgwarc_security_writer;
GRANT INSERT ON pgwarc_lance.lance_write_log TO pgwarc_security_writer;
GRANT USAGE ON SEQUENCE pgwarc_lance.lance_write_log_id_seq TO pgwarc_security_writer;
GRANT SELECT (uri, state) ON pgwarc_lance.lance_outbox TO pgwarc_security_writer;
GRANT SELECT ON pgwarc_lance.lance_outbox_status TO pgwarc_security_writer;
GRANT EXECUTE ON FUNCTION public.bm25_index_document(bigint,text),
    public.lance_upsert_many(text,bigint[],real[],integer,text[]),
    public.lance_outbox_enqueue(text,bigint[],real[],integer,text[]),
    public.lance_outbox_enqueue_revision(text,bigint[],real[],integer,text[],text[]),
    public.lance_dataset_write_lock(text),
    public.lance_document_delete(text,bigint[],text[])
    TO pgwarc_security_writer;

GRANT SELECT ON pgwarc_lance.lance_outbox_status,
    pgwarc_lance.lance_outbox_failures TO pgwarc_security_worker;
GRANT EXECUTE ON FUNCTION public.lance_outbox_process_next()
    TO pgwarc_security_worker;

GRANT SELECT ON pgwarc_lance.bm25_doc, pgwarc_lance.warc_record,
    pgwarc_lance.lance_outbox_status TO pgwarc_security_doctor;
GRANT SELECT (uri, doc_id, expected_revision, is_deleted)
    ON pgwarc_lance.lance_document_revision TO pgwarc_security_doctor;
GRANT SELECT (id, uri, state, ids)
    ON pgwarc_lance.lance_outbox TO pgwarc_security_doctor;
GRANT EXECUTE ON FUNCTION public.lance_scan_document_state(text,integer),
    public.lance_dataset_versions(text) TO pgwarc_security_doctor;

GRANT pgwarc_security_reader, pgwarc_security_writer,
    pgwarc_security_worker, pgwarc_security_doctor TO pgwarc_security_admin;
GRANT EXECUTE ON FUNCTION
    public.lance_create_table(text,integer,boolean),
    public.lance_insert(text,bigint,real[],text),
    public.lance_insert_many(text,bigint[],real[],integer,text[]),
    public.lance_count(text),
    public.lance_scan(text,integer),
    public.lance_vector_dim(text),
    public.lance_vector_search(text,real[],integer),
    public.lance_restore_version(text,bigint)
    TO pgwarc_security_admin;

DO $$
DECLARE
    extension_oid oid;
BEGIN
    SELECT oid INTO extension_oid FROM pg_extension WHERE extname = 'pgwarc_lance';
    IF EXISTS (
        SELECT 1
        FROM pg_proc p
        JOIN pg_depend d ON d.classid = 'pg_proc'::regclass AND d.objid = p.oid
        CROSS JOIN LATERAL aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
        WHERE d.refclassid = 'pg_extension'::regclass AND d.refobjid = extension_oid
          AND d.deptype = 'e' AND a.grantee = 0 AND a.privilege_type = 'EXECUTE'
    ) THEN
        RAISE EXCEPTION 'extension functions still allow PUBLIC EXECUTE';
    END IF;

    IF NOT has_function_privilege('pgwarc_security_reader',
        'public.hybrid_warc_search_filtered(text,real[],text,jsonb,integer)', 'EXECUTE')
       OR NOT has_function_privilege('pgwarc_security_reader',
        'public.hybrid_warc_search_filtered_explain(text,real[],text,jsonb,integer,integer,double precision,double precision,double precision)', 'EXECUTE')
       OR has_function_privilege('pgwarc_security_reader',
        'public.lance_outbox_process_next()', 'EXECUTE')
       OR has_function_privilege('pgwarc_security_reader',
        'public.lance_document_delete(text,bigint[],text[])', 'EXECUTE') THEN
        RAISE EXCEPTION 'reader function grants do not match the documented allowlist';
    END IF;

    IF NOT has_function_privilege('pgwarc_security_writer',
        'public.lance_outbox_enqueue_revision(text,bigint[],real[],integer,text[],text[])', 'EXECUTE')
       OR NOT has_function_privilege('pgwarc_security_writer',
        'public.lance_document_delete(text,bigint[],text[])', 'EXECUTE')
       OR has_function_privilege('pgwarc_security_writer',
        'public.lance_create_table(text,integer,boolean)', 'EXECUTE')
       OR has_function_privilege('pgwarc_security_writer',
        'public.lance_restore_version(text,bigint)', 'EXECUTE')
       OR has_function_privilege('pgwarc_security_writer',
        'public.hybrid_warc_search_filtered(text,real[],text,jsonb,integer)', 'EXECUTE') THEN
        RAISE EXCEPTION 'writer function grants do not match the documented allowlist';
    END IF;

    IF NOT has_function_privilege('pgwarc_security_worker',
        'public.lance_outbox_process_next()', 'EXECUTE')
       OR has_function_privilege('pgwarc_security_worker',
        'public.lance_outbox_enqueue(text,bigint[],real[],integer,text[])', 'EXECUTE')
       OR has_table_privilege('pgwarc_security_worker',
        'pgwarc_lance.lance_outbox', 'SELECT') THEN
        RAISE EXCEPTION 'worker can access more than its delivery API and diagnostics';
    END IF;

    IF NOT has_function_privilege('pgwarc_security_doctor',
        'public.lance_scan_document_state(text,integer)', 'EXECUTE')
       OR has_function_privilege('pgwarc_security_doctor',
        'public.bm25_index_document(bigint,text)', 'EXECUTE')
       OR has_function_privilege('pgwarc_security_doctor',
        'public.hybrid_warc_search_filtered(text,real[],text,jsonb,integer)', 'EXECUTE') THEN
        RAISE EXCEPTION 'doctor function grants do not match the read-only allowlist';
    END IF;

    IF NOT has_function_privilege('pgwarc_security_admin',
        'public.lance_create_table(text,integer,boolean)', 'EXECUTE')
       OR NOT has_function_privilege('pgwarc_security_admin',
        'public.lance_restore_version(text,bigint)', 'EXECUTE')
       OR (SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolcanlogin
           FROM pg_roles WHERE rolname = 'pgwarc_security_admin') THEN
        RAISE EXCEPTION 'admin must have only the explicit operational role grants';
    END IF;
END $$;

SET LOCAL pgwarc_lance.allowed_uri_prefix = '/tmp';
SET LOCAL ROLE pgwarc_security_admin;
DO $$
BEGIN
    PERFORM public.lance_create_table('/tmp/pgwarc_lance_security_admin.lance', 3, true);
END $$;
DO $$
BEGIN
    BEGIN
        PERFORM set_config('pgwarc_lance.allowed_uri_prefix', '/tmp', false);
        RAISE EXCEPTION 'operational admin changed a DBA-only GUC';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
END $$;
RESET ROLE;

SET LOCAL ROLE pgwarc_security_reader;
DO $$
BEGIN
    IF (SELECT count(*) FROM public.lance_scan_document_state(
        '/tmp/pgwarc_lance_security_admin.lance', 10)) <> 0 THEN
        RAISE EXCEPTION 'reader did not receive an empty dataset state';
    END IF;
    IF (SELECT max(version) FROM public.lance_dataset_versions(
        '/tmp/pgwarc_lance_security_admin.lance')) IS NULL THEN
        RAISE EXCEPTION 'reader could not inspect the dataset version';
    END IF;
END $$;
DO $$
BEGIN
    BEGIN
        PERFORM hello_pgwarc_lance();
        RAISE EXCEPTION 'reader ran a function without an explicit grant';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
END $$;
RESET ROLE;

SET LOCAL ROLE pgwarc_security_writer;
DO $$
BEGIN
    PERFORM public.bm25_index_document(-9900020, 'security writer grant');
END $$;
DO $$
BEGIN
    BEGIN
        PERFORM public.lance_create_table('/tmp/pgwarc_writer_must_not_create.lance', 3, false);
        RAISE EXCEPTION 'writer created a dataset without an admin grant';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
END $$;
RESET ROLE;

SET LOCAL ROLE pgwarc_security_worker;
DO $$
BEGIN
    BEGIN
        PERFORM public.lance_outbox_enqueue(
            '/tmp/pgwarc_worker_must_not_enqueue.lance', ARRAY[1]::bigint[],
            ARRAY[1,0,0]::real[], 3, ARRAY['blocked']);
        RAISE EXCEPTION 'worker enqueued vector payload';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
END $$;
RESET ROLE;

SET LOCAL ROLE pgwarc_security_doctor;
DO $$
BEGIN
    IF (SELECT count(*) FROM public.lance_scan_document_state(
        '/tmp/pgwarc_lance_security_admin.lance', 10)) <> 0 THEN
        RAISE EXCEPTION 'doctor did not receive an empty dataset state';
    END IF;
    BEGIN
        PERFORM public.bm25_index_document(-9900021, 'doctor must not write');
        RAISE EXCEPTION 'doctor wrote BM25 data';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
END $$;
RESET ROLE;

GRANT EXECUTE ON FUNCTION public.hello_pgwarc_lance()
    TO pgwarc_lance_security_test;
SET LOCAL ROLE pgwarc_lance_security_test;
DO $$
BEGIN
    IF public.hello_pgwarc_lance() <> 'Hello, pgwarc_lance!' THEN
        RAISE EXCEPTION 'explicit function grant did not work';
    END IF;
    BEGIN
        PERFORM public.lance_count('/tmp/pgwarc-untrusted.lance');
        RAISE EXCEPTION 'grant on hello also allowed dataset access';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    BEGIN
        PERFORM 1 FROM public.hybrid_warc_search_filtered(
            'search', ARRAY[0,0,0,0]::real[], '/tmp/pgwarc-untrusted.lance',
            '{"domain":"example.test"}'::jsonb, 1);
        RAISE EXCEPTION 'filtered search was executable without an explicit grant';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    BEGIN
        PERFORM set_config('pgwarc_lance.max_scan_rows', '0', false);
        RAISE EXCEPTION 'untrusted role changed scan limit';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
END $$;
RESET ROLE;

GRANT EXECUTE ON FUNCTION public.lance_create_table(text,integer,boolean)
    TO pgwarc_lance_security_test;
SET LOCAL ROLE pgwarc_lance_security_test;
DO $$
BEGIN
    BEGIN
        PERFORM public.lance_create_table('/tmp/pgwarc_no_log_permission.lance', 3, false);
        RAISE EXCEPTION 'dataset write succeeded without write-log permissions';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
END $$;
RESET ROLE;

DO $$
DECLARE
    candidate text;
BEGIN
    FOREACH candidate IN ARRAY ARRAY[
        '/tmp-sibling/dataset.lance',
        '/tmp/../var/lib/dataset.lance',
        'file:///tmp/%2e%2e/dataset.lance',
        'relative/dataset.lance',
        's3://bucket/dataset.lance'
    ] LOOP
        BEGIN
            PERFORM public.lance_count(candidate);
            RAISE EXCEPTION 'restricted URI was accepted';
        EXCEPTION WHEN insufficient_privilege THEN NULL;
        END;
    END LOOP;
END $$;
ROLLBACK;
