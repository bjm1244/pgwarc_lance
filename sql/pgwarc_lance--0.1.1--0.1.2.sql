-- Document replacement and cascading document deletion must use an index.
CREATE INDEX IF NOT EXISTS bm25_term_doc_id_idx ON pgwarc_lance.bm25_term (doc_id);

CREATE FUNCTION lance_upsert_many(uri text, ids bigint[], flat_vectors real[], vector_dim integer, labels text[])
RETURNS bigint STRICT LANGUAGE c AS 'MODULE_PATHNAME', 'lance_upsert_many_wrapper';
REVOKE EXECUTE ON FUNCTION lance_upsert_many(text,bigint[],real[],integer,text[]) FROM PUBLIC;
