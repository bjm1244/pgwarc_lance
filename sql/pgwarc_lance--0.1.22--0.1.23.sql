-- Versioned deletes share the ordered vector outbox with revisioned upserts.
ALTER TABLE pgwarc_lance.lance_outbox
    ADD COLUMN operation text NOT NULL DEFAULT 'upsert';

ALTER TABLE pgwarc_lance.lance_outbox
    ADD CONSTRAINT lance_outbox_operation_check
    CHECK (operation IN ('upsert', 'delete'));

-- Replace the 0.1.22 upsert-only payload shape check. Delete events have no
-- vector payload and carry one tombstone revision for each affected ID.
ALTER TABLE pgwarc_lance.lance_outbox
    DROP CONSTRAINT IF EXISTS lance_outbox_check;

ALTER TABLE pgwarc_lance.lance_outbox
    ADD CONSTRAINT lance_outbox_payload_shape_check
    CHECK (
        (state = 'completed' AND cardinality(ids) = 0
         AND cardinality(flat_vectors) = 0 AND cardinality(labels) = 0)
        OR (state <> 'completed' AND cardinality(ids) BETWEEN 1 AND 10000
            AND ((operation = 'upsert'
                  AND cardinality(ids) = cardinality(labels)
                  AND cardinality(flat_vectors) = cardinality(ids) * vector_dim)
                 OR (operation = 'delete'
                     AND cardinality(flat_vectors) = 0
                     AND cardinality(labels) = 0
                     AND revisions IS NOT NULL
                     AND cardinality(revisions) = cardinality(ids))))
    );

ALTER TABLE pgwarc_lance.lance_document_revision
    ADD COLUMN is_deleted boolean NOT NULL DEFAULT false;

CREATE OR REPLACE VIEW pgwarc_lance.lance_outbox_status AS
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
       max(completed_at) FILTER (WHERE state = 'completed') AS last_completed_at,
       count(*) FILTER (WHERE operation = 'delete' AND state = 'pending') AS pending_delete_jobs,
       count(*) FILTER (WHERE operation = 'delete' AND state = 'failed') AS failed_delete_jobs
FROM pgwarc_lance.lance_outbox
GROUP BY uri;

CREATE OR REPLACE VIEW pgwarc_lance.lance_outbox_failures AS
SELECT id, uri, attempt_count, last_attempt_at, available_at, last_error, operation
FROM pgwarc_lance.lance_outbox
WHERE state = 'failed';

CREATE FUNCTION lance_dataset_write_lock(uri text)
RETURNS void STRICT LANGUAGE c
AS 'MODULE_PATHNAME', 'lance_dataset_write_lock_wrapper';
REVOKE EXECUTE ON FUNCTION lance_dataset_write_lock(text) FROM PUBLIC;

CREATE FUNCTION lance_document_delete(uri text, ids bigint[], expected_revisions text[])
RETURNS bigint STRICT LANGUAGE c
AS 'MODULE_PATHNAME', 'lance_document_delete_wrapper';
ALTER FUNCTION lance_document_delete(text,bigint[],text[])
    SECURITY DEFINER SET search_path = pg_catalog, pg_temp;
REVOKE EXECUTE ON FUNCTION lance_document_delete(text,bigint[],text[]) FROM PUBLIC;
