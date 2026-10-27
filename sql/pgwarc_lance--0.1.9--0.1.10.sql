-- Cover the BM25 corpus-statistics aggregate with a narrow index.
CREATE INDEX bm25_doc_doc_len_idx
    ON pgwarc_lance.bm25_doc (doc_len);
