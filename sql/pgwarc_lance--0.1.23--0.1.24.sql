-- Persist normalized WARC metadata required for candidate-level eligibility.
ALTER TABLE pgwarc_lance.warc_record
    ADD COLUMN IF NOT EXISTS domain text;
ALTER TABLE pgwarc_lance.warc_record
    ADD COLUMN IF NOT EXISTS content_language text;
ALTER TABLE pgwarc_lance.warc_record
    ADD COLUMN IF NOT EXISTS media_type text;
ALTER TABLE pgwarc_lance.warc_record
    ADD COLUMN IF NOT EXISTS collection_id text;

-- Recover host and media type for already imported rows where those values can
-- be derived without reopening the source archive. Language and collection are
-- intentionally left NULL unless the importer recorded them.
WITH authority AS (
    SELECT doc_id,
           regexp_replace(
               regexp_replace(
                   regexp_replace(target_uri, '^[A-Za-z][A-Za-z0-9.+-]*://', ''),
                   '[/?#].*$', ''
               ),
               '^.*@', ''
           ) AS value
    FROM pgwarc_lance.warc_record
    WHERE domain IS NULL
      AND target_uri ~ '^[A-Za-z][A-Za-z0-9.+-]*://[^/?#]+'
), hosts AS (
    SELECT doc_id,
           CASE WHEN value LIKE '[%'
                THEN substring(value FROM '^\[([0-9A-Fa-f:.]+)\]')
                ELSE regexp_replace(value, ':[0-9]+$', '')
           END AS value
    FROM authority
)
UPDATE pgwarc_lance.warc_record AS record
SET domain = NULLIF(rtrim(lower(hosts.value), '.'), '')
FROM hosts
WHERE record.doc_id = hosts.doc_id;

UPDATE pgwarc_lance.warc_record
SET media_type = NULLIF(lower(split_part(content_type, ';', 1)), '')
WHERE media_type IS NULL AND content_type IS NOT NULL;

CREATE INDEX IF NOT EXISTS warc_record_domain_idx
    ON pgwarc_lance.warc_record (domain);
CREATE INDEX IF NOT EXISTS warc_record_language_gin_idx
    ON pgwarc_lance.warc_record USING gin (string_to_array(content_language, ','));
CREATE INDEX IF NOT EXISTS warc_record_media_type_idx
    ON pgwarc_lance.warc_record (media_type);
CREATE INDEX IF NOT EXISTS warc_record_collection_idx
    ON pgwarc_lance.warc_record (collection_id);

CREATE FUNCTION hybrid_warc_search_filtered(
    query text,
    vector_query real[],
    lance_uri text,
    filters jsonb,
    k integer DEFAULT 10
)
RETURNS TABLE(
    doc_id bigint,
    score double precision,
    source text,
    target_uri text,
    warc_date text,
    content_type text,
    http_status integer,
    payload_digest text,
    text_len integer,
    source_file text,
    domain text,
    content_language text,
    media_type text,
    collection_id text
)
STRICT LANGUAGE c
AS 'MODULE_PATHNAME', 'hybrid_warc_search_filtered_wrapper';

REVOKE EXECUTE ON FUNCTION hybrid_warc_search_filtered(text,real[],text,jsonb,integer)
    FROM PUBLIC;
