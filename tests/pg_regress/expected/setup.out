-- run once after the regression database is (re)created
CREATE EXTENSION pgwarc_lance;
DO $$
BEGIN
    IF to_regclass('pgwarc_lance.bm25_term_doc_id_idx') IS NULL THEN
        RAISE EXCEPTION 'BM25 document posting index is missing';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_index i
        WHERE i.indexrelid = 'pgwarc_lance.bm25_term_frequency_idx'::regclass
          AND NOT i.indisunique AND i.indisvalid AND i.indisready
          AND i.indnkeyatts = 1 AND i.indnatts = 1
          AND pg_catalog.pg_get_indexdef(i.indexrelid, 1, false) = 'term'
    ) THEN
        RAISE EXCEPTION 'BM25 document-frequency index must cover term';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_depend AS index_dep
        JOIN pg_catalog.pg_depend AS table_dep
          ON table_dep.classid = 'pg_catalog.pg_class'::regclass
         AND table_dep.objid = index_dep.refobjid
        JOIN pg_catalog.pg_extension AS e
          ON table_dep.refclassid = 'pg_catalog.pg_extension'::regclass
         AND table_dep.refobjid = e.oid
        WHERE e.extname = 'pgwarc_lance'
          AND index_dep.classid = 'pg_catalog.pg_class'::regclass
          AND index_dep.objid = 'pgwarc_lance.bm25_term_frequency_idx'::regclass
          AND index_dep.refclassid = 'pg_catalog.pg_class'::regclass
          AND index_dep.refobjid = 'pgwarc_lance.bm25_term'::regclass
          AND index_dep.deptype = 'a'
          AND table_dep.deptype = 'e'
    ) THEN
        RAISE EXCEPTION 'BM25 document-frequency index must belong to the extension table';
    END IF;
    IF to_regclass('pgwarc_lance.bm25_long_term') IS NULL THEN
        RAISE EXCEPTION 'BM25 oversized-term dictionary is missing';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_index i
        WHERE i.indexrelid = 'pgwarc_lance.bm25_doc_pkey'::regclass
          AND i.indisprimary AND i.indisvalid AND i.indisready
          AND i.indnkeyatts = 1 AND i.indnatts = 2
          AND pg_catalog.pg_get_indexdef(i.indexrelid, 1, false) = 'doc_id'
          AND pg_catalog.pg_get_indexdef(i.indexrelid, 2, false) = 'doc_len'
    ) THEN
        RAISE EXCEPTION 'BM25 document primary key must cover doc_len';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_index i
        WHERE i.indexrelid = 'pgwarc_lance.bm25_doc_doc_len_idx'::regclass
          AND i.indisvalid AND i.indisready
          AND i.indnkeyatts = 1 AND i.indnatts = 1
          AND pg_catalog.pg_get_indexdef(i.indexrelid, 1, false) = 'doc_len'
    ) THEN
        RAISE EXCEPTION 'BM25 corpus statistics index must cover doc_len';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_index
        WHERE indexrelid = 'pgwarc_lance.bm25_term_pkey'::regclass
          AND indisprimary AND indnkeyatts = 2 AND indnatts = 3
    ) THEN
        RAISE EXCEPTION 'BM25 primary key must cover tf for posting scans';
    END IF;
    IF to_regclass('pgwarc_lance.bm25_term_term_idx') IS NOT NULL THEN
        RAISE EXCEPTION 'redundant BM25 term index remains';
    END IF;
END $$;
