-- Make repeated BM25 document lookups index-only after vacuum visibility is set.
CREATE UNIQUE INDEX bm25_doc_pkey_cover_idx
    ON pgwarc_lance.bm25_doc (doc_id) INCLUDE (doc_len);

-- Rebind the extension-owned foreign key so PostgreSQL can replace the PK index.
ALTER TABLE pgwarc_lance.bm25_term
    DROP CONSTRAINT bm25_term_doc_id_fkey;

ALTER TABLE pgwarc_lance.bm25_doc
    DROP CONSTRAINT bm25_doc_pkey;

ALTER TABLE pgwarc_lance.bm25_doc
    ADD CONSTRAINT bm25_doc_pkey
    PRIMARY KEY USING INDEX bm25_doc_pkey_cover_idx;

ALTER TABLE pgwarc_lance.bm25_term
    ADD CONSTRAINT bm25_term_doc_id_fkey
    FOREIGN KEY (doc_id)
    REFERENCES pgwarc_lance.bm25_doc (doc_id)
    ON DELETE CASCADE
    NOT VALID;

ALTER TABLE pgwarc_lance.bm25_term
    VALIDATE CONSTRAINT bm25_term_doc_id_fkey;
