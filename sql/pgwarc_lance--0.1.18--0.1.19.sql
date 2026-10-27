-- Add a narrow index for BM25 document-frequency scans.
CREATE INDEX IF NOT EXISTS bm25_term_frequency_idx
    ON pgwarc_lance.bm25_term (term);
