-- Persist vector upserts in the same PostgreSQL transaction as imported SQL data.
CREATE TABLE pgwarc_lance.lance_outbox (
    id              bigserial PRIMARY KEY,
    uri             text NOT NULL,
    ids             bigint[] NOT NULL,
    flat_vectors    real[] NOT NULL,
    vector_dim      integer NOT NULL CHECK (vector_dim > 0),
    labels          text[] NOT NULL,
    state           text NOT NULL DEFAULT 'pending'
                    CHECK (state IN ('pending', 'processing', 'completed', 'failed')),
    attempt_count   integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    enqueued_at     timestamptz NOT NULL DEFAULT clock_timestamp(),
    available_at    timestamptz NOT NULL DEFAULT clock_timestamp(),
    last_attempt_at timestamptz,
    completed_at    timestamptz,
    applied_version bigint,
    last_error      text,
    CHECK (
        (state = 'completed' AND cardinality(ids) = 0
         AND cardinality(flat_vectors) = 0 AND cardinality(labels) = 0)
        OR (state <> 'completed' AND cardinality(ids) BETWEEN 1 AND 10000
            AND cardinality(ids) = cardinality(labels)
            AND cardinality(flat_vectors) = cardinality(ids) * vector_dim)
    ),
    CHECK ((state = 'completed') = (completed_at IS NOT NULL)),
    CHECK (state <> 'completed' OR applied_version IS NOT NULL)
);

CREATE INDEX lance_outbox_uri_id_idx
    ON pgwarc_lance.lance_outbox (uri, id);
CREATE INDEX lance_outbox_available_idx
    ON pgwarc_lance.lance_outbox (available_at, id)
    WHERE state = 'pending';

CREATE VIEW pgwarc_lance.lance_outbox_status AS
SELECT uri,
       count(*) FILTER (WHERE state = 'pending') AS pending_jobs,
       count(*) FILTER (WHERE state = 'pending' AND available_at <= clock_timestamp()) AS ready_jobs,
       count(*) FILTER (WHERE state = 'failed') AS failed_jobs,
       min(enqueued_at) FILTER (WHERE state <> 'completed') AS oldest_unfinished_at,
       COALESCE(
           floor(extract(epoch FROM (clock_timestamp() -
               min(enqueued_at) FILTER (WHERE state <> 'completed'))) * 1000)::bigint,
           0
       ) AS oldest_unfinished_ms,
       max(applied_version) FILTER (WHERE state = 'completed') AS latest_applied_version,
       max(completed_at) FILTER (WHERE state = 'completed') AS last_completed_at
FROM pgwarc_lance.lance_outbox
GROUP BY uri;

CREATE VIEW pgwarc_lance.lance_outbox_failures AS
SELECT id, uri, attempt_count, last_attempt_at, available_at, last_error
FROM pgwarc_lance.lance_outbox
WHERE state = 'failed';

SELECT pg_catalog.pg_extension_config_dump('pgwarc_lance.lance_outbox', '');
SELECT pg_catalog.pg_extension_config_dump('pgwarc_lance.lance_outbox_id_seq', '');

CREATE FUNCTION lance_outbox_enqueue(uri text, ids bigint[], flat_vectors real[], vector_dim integer, labels text[])
RETURNS bigint STRICT LANGUAGE c AS 'MODULE_PATHNAME', 'lance_outbox_enqueue_wrapper';
CREATE FUNCTION lance_outbox_process_next()
RETURNS TABLE(outbox_id bigint, status text, applied_version bigint, queue_lag_ms bigint)
LANGUAGE c AS 'MODULE_PATHNAME', 'lance_outbox_process_next_wrapper';

ALTER FUNCTION lance_outbox_enqueue(text,bigint[],real[],integer,text[])
    SECURITY DEFINER SET search_path = pg_catalog, pg_temp;
ALTER FUNCTION lance_outbox_process_next()
    SECURITY DEFINER SET search_path = pg_catalog, pg_temp;

-- Upgrade functions must retain the extension's restricted EXECUTE policy.
REVOKE EXECUTE ON FUNCTION lance_outbox_enqueue(text,bigint[],real[],integer,text[]) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION lance_outbox_process_next() FROM PUBLIC;
