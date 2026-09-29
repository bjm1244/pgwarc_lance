\echo '=== pgwarc_lance extension tests ==='

SELECT hello_pgwarc_lance()            AS hello;
SELECT add_numbers(3, 4)          AS sum;
SELECT fibonacci(10)              AS fib10;
SELECT fibonacci(0)               AS fib0;
SELECT array_sum(ARRAY[1,2,3,4,5])AS arr_sum;
SELECT shout('hello world')       AS shout;
SELECT greatest_of(3, 7, 2)       AS greatest;
SELECT is_even(8)                 AS even8;
SELECT is_even(7)                 AS even7;
SELECT word_count('hello world from pgwarc_lance') AS words;
SELECT double_or_none(21)         AS doubled;
SELECT double_or_none(NULL::int4) AS doubled_null;
SELECT greeting('pgwarc_lance')        AS greeting_default;
SELECT greeting('pgwarc_lance', 42)    AS greeting_age;

\echo '=== pgwarc_lance bm25 tests ==='

BEGIN;
DELETE FROM pgwarc_lance.bm25_term;
DELETE FROM pgwarc_lance.bm25_doc;
SELECT bm25_index_document(1, 'PostgreSQL 검색 엔진');
SELECT bm25_index_document(2, 'Lance vector search');
SELECT bm25_doc_count() AS docs, round(bm25_avg_doc_len()::numeric, 2) AS avg_len;
SELECT doc_id, round(score::numeric, 6) AS score FROM bm25_search('검색', 10);
ROLLBACK;

\echo '=== pgwarc_lance lance tests ==='

SELECT lance_create_table('/tmp/pgwarc_lance_smoke.lance', 4, true);
SELECT lance_vector_dim('/tmp/pgwarc_lance_smoke.lance') AS vector_dim;
SELECT lance_insert('/tmp/pgwarc_lance_smoke.lance', 1, ARRAY[0,0,0,0]::float4[], 'doc1');
SELECT lance_insert('/tmp/pgwarc_lance_smoke.lance', 2, ARRAY[10,0,0,0]::float4[], 'doc2');
SELECT lance_insert_many(
    '/tmp/pgwarc_lance_smoke.lance',
    ARRAY[3,4]::bigint[],
    ARRAY[1,0,0,0,2,0,0,0]::float4[],
    4,
    ARRAY['doc3','doc4']::text[]
);
SELECT lance_count('/tmp/pgwarc_lance_smoke.lance') AS lance_rows;
SELECT id, label, round(distance::numeric, 6) AS distance
FROM lance_vector_search('/tmp/pgwarc_lance_smoke.lance', ARRAY[0,0,0,0]::float4[], 2);

\echo '=== pgwarc_lance hybrid tests ==='

BEGIN;
DELETE FROM pgwarc_lance.bm25_term;
DELETE FROM pgwarc_lance.bm25_doc;
DELETE FROM pgwarc_lance.warc_record;
SELECT bm25_index_document(1, 'PostgreSQL 검색 엔진');
SELECT bm25_index_document(2, 'Lance vector search');
INSERT INTO pgwarc_lance.warc_record (
    doc_id,
    target_uri,
    warc_date,
    content_type,
    http_status,
    payload_digest,
    text_len,
    source_file
) VALUES
    (1, 'https://example.test/search', '2026-06-30T00:00:00Z', 'text/html', 200, 'sha1:one', 18, 'sample.warc'),
    (2, 'https://example.test/vector', '2026-06-30T00:01:00Z', 'text/plain', 200, 'sha1:two', 19, 'sample.warc');
SELECT lance_create_table('/tmp/pgwarc_lance_hybrid_smoke.lance', 4, true);
SELECT lance_insert_many(
    '/tmp/pgwarc_lance_hybrid_smoke.lance',
    ARRAY[1,2]::bigint[],
    ARRAY[0,0,0,0,10,0,0,0]::float4[],
    4,
    ARRAY['doc1','doc2']::text[]
);
SELECT doc_id, round(score::numeric, 6) AS score, source
FROM hybrid_search('검색', ARRAY[0,0,0,0]::float4[], '/tmp/pgwarc_lance_hybrid_smoke.lance', 2);
SELECT doc_id, source, target_uri, warc_date, http_status
FROM hybrid_warc_search('검색', ARRAY[0,0,0,0]::float4[], '/tmp/pgwarc_lance_hybrid_smoke.lance', 2)
ORDER BY doc_id;
SELECT doc_id, target_uri
FROM hybrid_warc_search('검색', ARRAY[0,0,0,0]::float4[], '/tmp/pgwarc_lance_hybrid_smoke.lance', 2)
WHERE http_status = 200
  AND content_type ILIKE 'text/html%'
  AND warc_date::timestamptz >= '2026-06-30T00:00:00Z'::timestamptz
  AND source_file = 'sample.warc'
ORDER BY doc_id;
ROLLBACK;
