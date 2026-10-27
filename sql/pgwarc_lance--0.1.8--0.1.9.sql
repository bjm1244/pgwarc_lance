-- Cover BM25 scoring postings with the primary key and remove the redundant term-only index.
CREATE UNIQUE INDEX bm25_term_cover_idx
    ON pgwarc_lance.bm25_term (term, doc_id) INCLUDE (tf);
ALTER TABLE pgwarc_lance.bm25_term
    DROP CONSTRAINT bm25_term_pkey;
ALTER TABLE pgwarc_lance.bm25_term
    ADD CONSTRAINT bm25_term_pkey PRIMARY KEY USING INDEX bm25_term_cover_idx;
DROP INDEX pgwarc_lance.bm25_term_term_idx;
