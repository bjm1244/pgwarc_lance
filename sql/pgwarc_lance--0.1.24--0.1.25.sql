CREATE FUNCTION hybrid_search_explain(
    query text,
    vector_query real[],
    lance_uri text,
    k integer DEFAULT 10,
    candidate_k integer DEFAULT 100,
    bm25_weight double precision DEFAULT 1.0,
    vector_weight double precision DEFAULT 1.0,
    rrf_k double precision DEFAULT 60.0
)
RETURNS TABLE(
    doc_id bigint,
    score double precision,
    source text,
    bm25_rank integer,
    vector_rank integer,
    bm25_contribution double precision,
    vector_contribution double precision
)
STRICT LANGUAGE c
AS 'MODULE_PATHNAME', 'hybrid_search_explain_wrapper';

REVOKE EXECUTE ON FUNCTION hybrid_search_explain(
    text, real[], text, integer, integer, double precision,
    double precision, double precision
) FROM PUBLIC;

CREATE FUNCTION hybrid_warc_search_filtered_explain(
    query text,
    vector_query real[],
    lance_uri text,
    filters jsonb,
    k integer DEFAULT 10,
    candidate_k integer DEFAULT 100,
    bm25_weight double precision DEFAULT 1.0,
    vector_weight double precision DEFAULT 1.0,
    rrf_k double precision DEFAULT 60.0
)
RETURNS TABLE(
    doc_id bigint,
    score double precision,
    source text,
    bm25_rank integer,
    vector_rank integer,
    bm25_contribution double precision,
    vector_contribution double precision
)
STRICT LANGUAGE c
AS 'MODULE_PATHNAME', 'hybrid_warc_search_filtered_explain_wrapper';

REVOKE EXECUTE ON FUNCTION hybrid_warc_search_filtered_explain(
    text, real[], text, jsonb, integer, integer, double precision,
    double precision, double precision
) FROM PUBLIC;
