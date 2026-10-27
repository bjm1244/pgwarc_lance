-- Preserve oversized BM25 tokens outside the posting B-tree key.
CREATE TABLE pgwarc_lance.bm25_long_term (
    term_key text PRIMARY KEY,
    term     text NOT NULL
);
SELECT pg_catalog.pg_extension_config_dump('pgwarc_lance.bm25_long_term', '');

-- Re-key tokens that could fit in the old posting B-tree. The replacement
-- extension binary is already loaded when an upgrade script invokes this
-- function, so it rewrites only documents that actually contain such terms.
DO $pgwarc_lance_long_term_migration$
DECLARE
    affected_doc_ids bigint[];
    affected_doc_id bigint;
    document_content text;
BEGIN
    SELECT array_agg(DISTINCT doc_id) INTO affected_doc_ids
    FROM pgwarc_lance.bm25_term
    WHERE octet_length(term) > 2000;

    FOREACH affected_doc_id IN ARRAY COALESCE(affected_doc_ids, ARRAY[]::bigint[])
    LOOP
        SELECT content INTO document_content
        FROM pgwarc_lance.bm25_doc
        WHERE doc_id = affected_doc_id;

        IF FOUND THEN
            PERFORM public.bm25_index_document(affected_doc_id, document_content);
        END IF;
    END LOOP;
END
$pgwarc_lance_long_term_migration$;
