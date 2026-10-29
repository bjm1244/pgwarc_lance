-- Keep the importer-generated document revision beside its queued vector and
-- persist the expected URI/document mapping for read-only reconciliation.
ALTER TABLE pgwarc_lance.lance_outbox
    ADD COLUMN revisions text[];

ALTER TABLE pgwarc_lance.lance_outbox
    ADD CONSTRAINT lance_outbox_revision_shape_check
    CHECK (
        (state = 'completed' OR revisions IS NULL
         OR cardinality(revisions) = cardinality(ids))
        AND (revisions IS NULL OR cardinality(revisions) BETWEEN 1 AND 10000)
    );

CREATE TABLE pgwarc_lance.lance_document_revision (
    uri               text NOT NULL,
    doc_id            bigint NOT NULL,
    expected_revision text NOT NULL CHECK (expected_revision ~ '^[0-9a-f]{64}$'),
    updated_at        timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (uri, doc_id)
);

SELECT pg_catalog.pg_extension_config_dump('pgwarc_lance.lance_document_revision', '');

CREATE FUNCTION lance_outbox_enqueue_revision(
    uri text,
    ids bigint[],
    flat_vectors real[],
    vector_dim integer,
    labels text[],
    revisions text[]
)
RETURNS bigint STRICT LANGUAGE c
AS 'MODULE_PATHNAME', 'lance_outbox_enqueue_revision_wrapper';

ALTER FUNCTION lance_outbox_enqueue_revision(text,bigint[],real[],integer,text[],text[])
    SECURITY DEFINER SET search_path = pg_catalog, pg_temp;
REVOKE EXECUTE ON FUNCTION lance_outbox_enqueue_revision(text,bigint[],real[],integer,text[],text[])
    FROM PUBLIC;

CREATE FUNCTION lance_scan_document_state(uri text, "limit" integer DEFAULT 100)
RETURNS TABLE(doc_id bigint, label text, revision text)
LANGUAGE c AS 'MODULE_PATHNAME', 'lance_scan_document_state_wrapper';
REVOKE EXECUTE ON FUNCTION lance_scan_document_state(text,integer) FROM PUBLIC;
