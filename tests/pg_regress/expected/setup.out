-- run once after the regression database is (re)created
CREATE EXTENSION pgwarc_lance;
DO $$
BEGIN
    IF to_regclass('pgwarc_lance.bm25_term_doc_id_idx') IS NULL THEN
        RAISE EXCEPTION 'BM25 document posting index is missing';
    END IF;
END $$;
