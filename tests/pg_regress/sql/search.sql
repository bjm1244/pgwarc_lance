-- search core tests for pgwarc_lance
SELECT lance_create_table('/tmp/pgwarc_lance_regress.lance', 4, true);

SELECT lance_vector_dim('/tmp/pgwarc_lance_regress.lance') AS dim;

SELECT lance_insert('/tmp/pgwarc_lance_regress.lance', 1, ARRAY[0,0,0,0]::float4[], 'doc1');

SELECT lance_insert_many(
    '/tmp/pgwarc_lance_regress.lance',
    ARRAY[2,3]::bigint[],
    ARRAY[10,0,0,0,1,0,0,0]::float4[],
    4,
    ARRAY['doc2','doc3']::text[]
);

SELECT lance_count('/tmp/pgwarc_lance_regress.lance') AS rows;

COPY (
    SELECT doc_id::text || '|' || label || '|' || (revision IS NULL)::text
    FROM lance_scan_document_state('/tmp/pgwarc_lance_regress.lance', 10)
    ORDER BY doc_id
) TO STDOUT;

SELECT id, label, distance < 0.0001 AS near
FROM lance_vector_search('/tmp/pgwarc_lance_regress.lance', ARRAY[0,0,0,0]::float4[], 1);

BEGIN;
DELETE FROM pgwarc_lance.bm25_term;
DELETE FROM pgwarc_lance.bm25_long_term;
DELETE FROM pgwarc_lance.bm25_doc;
DELETE FROM pgwarc_lance.warc_record;

SELECT bm25_index_document(1, 'PostgreSQL 검색 엔진');
SELECT bm25_index_document(2, 'Lance vector search');

-- Match the longest exact token observed in the audited Common Crawl slice.
COPY (
    SELECT bm25_index_document(3, repeat('x', 2298914)) IS NOT NULL
) TO STDOUT;
COPY (
    SELECT doc_id::text
    FROM bm25_search(repeat('x', 2298914), 10)
    ORDER BY doc_id
) TO STDOUT;

DO $$
BEGIN
    UPDATE pgwarc_lance.bm25_long_term
    SET term = 'collision fixture'
    WHERE term_key = (
        SELECT term FROM pgwarc_lance.bm25_term WHERE doc_id = 3
    );
    BEGIN
        PERFORM 1 FROM bm25_search(repeat('x', 2298914), 10);
        RAISE EXCEPTION 'long-term hash collision was not detected';
    EXCEPTION WHEN data_exception THEN
        NULL;
    END;
    UPDATE pgwarc_lance.bm25_long_term
    SET term = repeat('x', 2298914)
    WHERE term_key = (
        SELECT term FROM pgwarc_lance.bm25_term WHERE doc_id = 3
    );
END $$;

DO $$
BEGIN
    BEGIN
        PERFORM bm25_index_document(4, repeat('x', 2500001));
        RAISE EXCEPTION 'token above the searchable limit was indexed';
    EXCEPTION WHEN program_limit_exceeded THEN
        NULL;
    END;
END $$;

-- Reindexing removes the posting even though the dictionary may retain its key.
COPY (
    SELECT bm25_index_document(3, 'replacement ordinary') IS NOT NULL
) TO STDOUT;
COPY (
    SELECT doc_id::text
    FROM bm25_search(repeat('x', 2298914), 10)
    ORDER BY doc_id
) TO STDOUT;

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

SELECT lance_create_table('/tmp/pgwarc_lance_hybrid_regress.lance', 4, true);

SELECT lance_insert_many(
    '/tmp/pgwarc_lance_hybrid_regress.lance',
    ARRAY[1,2]::bigint[],
    ARRAY[0,0,0,0,10,0,0,0]::float4[],
    4,
    ARRAY['doc1','doc2']::text[]
);

-- This result has no WARC metadata row; it must keep default metadata values.
DO $$
BEGIN
    PERFORM lance_insert(
        '/tmp/pgwarc_lance_hybrid_regress.lance',
        4,
        ARRAY[1000,0,0,0]::float4[],
        'no-metadata'
    );
END $$;

SELECT doc_id, source
FROM hybrid_search('검색', ARRAY[0,0,0,0]::float4[], '/tmp/pgwarc_lance_hybrid_regress.lance', 2)
ORDER BY doc_id;

COPY (
    SELECT doc_id::text || '|' || source || '|' || target_uri || '|' || warc_date || '|' || http_status::text AS result
    FROM hybrid_warc_search('검색', ARRAY[0,0,0,0]::float4[], '/tmp/pgwarc_lance_hybrid_regress.lance', 2)
    ORDER BY doc_id
) TO STDOUT;

COPY (
    SELECT doc_id::text || '|' || target_uri AS result
    FROM hybrid_warc_search('검색', ARRAY[0,0,0,0]::float4[], '/tmp/pgwarc_lance_hybrid_regress.lance', 2)
    WHERE http_status = 200
      AND content_type ILIKE 'text/html%'
      AND warc_date::timestamptz >= '2026-06-30T00:00:00Z'::timestamptz
      AND source_file = 'sample.warc'
    ORDER BY doc_id
) TO STDOUT;

COPY (
    SELECT doc_id, target_uri IS NULL, text_len
    FROM hybrid_warc_search('검색', ARRAY[0,0,0,0]::float4[], '/tmp/pgwarc_lance_hybrid_regress.lance', 10)
    WHERE doc_id = 4
) TO STDOUT;

-- Candidate eligibility is applied to BM25 and Lance before final top-k.
DO $$
BEGIN
    PERFORM bm25_index_document(10, 'filterneedle common');
    PERFORM bm25_index_document(11, 'filterneedle common');
    PERFORM bm25_index_document(12, 'filterneedle common');
    PERFORM bm25_index_document(13, 'filterneedle common');
END $$;

INSERT INTO pgwarc_lance.warc_record (
    doc_id, target_uri, warc_date, content_type, http_status, text_len,
    source_file, domain, content_language, media_type, collection_id
) VALUES
    (10, 'https://blocked.test/closest', '2026-06-30T00:00:00Z', 'application/pdf', 200, 10, 'filter.warc', 'blocked.test', 'en-us', 'application/pdf', 'archive.v1'),
    (11, 'https://example.test/match', '2026-06-30T00:00:01Z', 'text/html; charset=utf-8', 200, 10, 'filter.warc', 'example.test', 'en-us,fr-ca', 'text/html', 'archive.v1'),
    (12, 'https://example.test/after', '2026-07-01T00:00:00Z', 'text/html', 200, 10, 'filter.warc', 'example.test', 'en-us', 'text/html', 'archive.v1'),
    (13, 'https://example.test/other-language', '2026-06-30T00:00:02Z', 'text/plain', 200, 10, 'filter.warc', 'example.test', 'fr', 'text/plain', 'archive.v1');

DO $$
BEGIN
    PERFORM lance_create_table('/tmp/pgwarc_lance_filter_regress.lance', 4, true);
    PERFORM lance_insert_many(
        '/tmp/pgwarc_lance_filter_regress.lance',
        ARRAY[10,11,12,13]::bigint[],
        ARRAY[0,0,0,0, 1,0,0,0, 2,0,0,0, 3,0,0,0]::float4[],
        4,
        ARRAY['blocked','match','after','other-language']::text[]
    );
END $$;

-- The legacy top-1 candidates contain only the ineligible closest document.
COPY (
    SELECT count(*)::text
    FROM hybrid_warc_search(
        'filterneedle', ARRAY[0,0,0,0]::float4[],
        '/tmp/pgwarc_lance_filter_regress.lance', 1
    ) AS hit
    JOIN pgwarc_lance.warc_record AS wr ON wr.doc_id = hit.doc_id
    WHERE wr.domain = 'example.test'
) TO STDOUT;

-- Every individual metadata dimension filters before either source ranks.
DO $$
DECLARE
    actual bigint[];
BEGIN
    SELECT array_agg(doc_id ORDER BY doc_id) INTO actual
    FROM hybrid_warc_search_filtered(
        'filterneedle', ARRAY[0,0,0,0]::float4[],
        '/tmp/pgwarc_lance_filter_regress.lance', '{"domain":"example.test"}'::jsonb, 4
    );
    IF actual IS DISTINCT FROM ARRAY[11,12,13]::bigint[] THEN
        RAISE EXCEPTION 'domain eligibility mismatch: %', actual;
    END IF;

    SELECT array_agg(doc_id ORDER BY doc_id) INTO actual
    FROM hybrid_warc_search_filtered(
        'filterneedle', ARRAY[0,0,0,0]::float4[],
        '/tmp/pgwarc_lance_filter_regress.lance', '{"language":"en-US"}'::jsonb, 4
    );
    IF actual IS DISTINCT FROM ARRAY[10,11,12]::bigint[] THEN
        RAISE EXCEPTION 'language eligibility mismatch: %', actual;
    END IF;

    SELECT array_agg(doc_id ORDER BY doc_id) INTO actual
    FROM hybrid_warc_search_filtered(
        'filterneedle', ARRAY[0,0,0,0]::float4[],
        '/tmp/pgwarc_lance_filter_regress.lance', '{"format":"TEXT/HTML"}'::jsonb, 4
    );
    IF actual IS DISTINCT FROM ARRAY[11,12]::bigint[] THEN
        RAISE EXCEPTION 'format eligibility mismatch: %', actual;
    END IF;

    SELECT array_agg(doc_id ORDER BY doc_id) INTO actual
    FROM hybrid_warc_search_filtered(
        'filterneedle', ARRAY[0,0,0,0]::float4[],
        '/tmp/pgwarc_lance_filter_regress.lance', '{"collection_id":"archive.v1"}'::jsonb, 4
    );
    IF actual IS DISTINCT FROM ARRAY[10,11,12,13]::bigint[] THEN
        RAISE EXCEPTION 'collection eligibility mismatch: %', actual;
    END IF;

    SELECT array_agg(doc_id ORDER BY doc_id) INTO actual
    FROM hybrid_warc_search_filtered(
        'filterneedle', ARRAY[0,0,0,0]::float4[],
        '/tmp/pgwarc_lance_filter_regress.lance',
        '{"from":"2026-06-30T00:00:01Z","to":"2026-06-30T00:00:02Z"}'::jsonb, 4
    );
    IF actual IS DISTINCT FROM ARRAY[11,13]::bigint[] THEN
        RAISE EXCEPTION 'inclusive date eligibility mismatch: %', actual;
    END IF;
END $$;

-- Exact language-tag membership has a valid GIN expression index.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_index AS i
        JOIN pg_catalog.pg_class AS index_relation ON index_relation.oid = i.indexrelid
        JOIN pg_catalog.pg_am AS access_method ON access_method.oid = index_relation.relam
        WHERE i.indexrelid = 'pgwarc_lance.warc_record_language_gin_idx'::regclass
          AND i.indisvalid AND i.indisready AND i.indnatts = 1
          AND access_method.amname = 'gin'
          AND pg_catalog.pg_get_indexdef(i.indexrelid, 1, false)
              LIKE 'string_to_array(content_language%'
    ) THEN
        RAISE EXCEPTION 'language membership GIN index is missing or invalid';
    END IF;
END $$;

-- Combined inclusive filters return the eligible document even though it is
-- below the old unfiltered top-1; k larger than the eligible set returns one.
COPY (
    SELECT doc_id::text || '|' || source || '|' || domain
    FROM hybrid_warc_search_filtered(
        'filterneedle', ARRAY[0,0,0,0]::float4[],
        '/tmp/pgwarc_lance_filter_regress.lance',
        '{"domain":"EXAMPLE.TEST","from":"2026-06-30T00:00:01Z","to":"2026-06-30T00:00:01Z","language":"EN-us","format":"TEXT/HTML","collection_id":"archive.v1"}'::jsonb,
        1
    )
) TO STDOUT;

COPY (
    SELECT count(*)::text
    FROM hybrid_warc_search_filtered(
        'filterneedle', ARRAY[0,0,0,0]::float4[],
        '/tmp/pgwarc_lance_filter_regress.lance',
        '{"domain":"example.test","from":"2026-06-30T00:00:01Z","to":"2026-06-30T00:00:01Z","language":"en-us","format":"text/html","collection_id":"archive.v1"}'::jsonb,
        10
    )
) TO STDOUT;

-- With no BM25 postings, the filtered vector path returns exact nearest
-- neighbors from the eligible set, ordered by their vector distance.
COPY (
    SELECT doc_id::text || '|' || source
    FROM hybrid_warc_search_filtered(
        'unindexedvectoronlyterm', ARRAY[0,0,0,0]::float4[],
        '/tmp/pgwarc_lance_filter_regress.lance', '{"domain":"example.test"}'::jsonb, 10
    )
) TO STDOUT;

-- Invalid filter keys/values, reversed dates, oversized JSON and fan-out fail
-- closed before candidate generation instead of truncating an eligibility set.
DO $$
BEGIN
    BEGIN
        PERFORM 1 FROM hybrid_warc_search_filtered(
            'filterneedle', ARRAY[0,0,0,0]::float4[],
            '/tmp/pgwarc_lance_filter_regress.lance', '{"domain":"example.test'' OR TRUE --"}'::jsonb, 1
        );
        RAISE EXCEPTION 'SQL-shaped domain input was accepted';
    EXCEPTION WHEN invalid_parameter_value THEN NULL;
    END;
    BEGIN
        PERFORM 1 FROM hybrid_warc_search_filtered(
            'filterneedle', ARRAY[0,0,0,0]::float4[],
            '/tmp/pgwarc_lance_filter_regress.lance', '{"other":"value"}'::jsonb, 1
        );
        RAISE EXCEPTION 'unknown search filter was accepted';
    EXCEPTION WHEN invalid_parameter_value THEN NULL;
    END;
    BEGIN
        PERFORM 1 FROM hybrid_warc_search_filtered(
            'filterneedle', ARRAY[0,0,0,0]::float4[],
            '/tmp/pgwarc_lance_filter_regress.lance',
            '{"from":"2026-07-01T00:00:00Z","to":"2026-06-30T00:00:00Z"}'::jsonb, 1
        );
        RAISE EXCEPTION 'reversed date range was accepted';
    EXCEPTION WHEN invalid_parameter_value THEN NULL;
    END;
    BEGIN
        PERFORM 1 FROM hybrid_warc_search_filtered(
            'filterneedle', ARRAY[0,0,0,0]::float4[],
            '/tmp/pgwarc_lance_filter_regress.lance',
            jsonb_build_object('domain', repeat('a', 2050)), 1
        );
        RAISE EXCEPTION 'oversized filters JSON was accepted';
        EXCEPTION WHEN invalid_parameter_value THEN NULL;
    END;
END $$;

INSERT INTO pgwarc_lance.warc_record (doc_id, domain)
SELECT 1000000 + value, 'many.test' FROM generate_series(1, 10001) AS value;
DO $$
BEGIN
    BEGIN
        PERFORM 1 FROM hybrid_warc_search_filtered(
            'filterneedle', ARRAY[0,0,0,0]::float4[],
            '/tmp/pgwarc_lance_filter_regress.lance', '{"domain":"many.test"}'::jsonb, 1
        );
        RAISE EXCEPTION 'over-limit eligible set was silently truncated';
    EXCEPTION WHEN program_limit_exceeded THEN NULL;
    END;
END $$;

ROLLBACK;
