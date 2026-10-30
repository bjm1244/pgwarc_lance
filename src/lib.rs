use std::borrow::Cow;
use std::collections::HashMap;
use std::ffi::CString;

use pgrx::datum::{DatumWithOid, JsonB};
use pgrx::guc::{GucContext, GucFlags, GucRegistry, GucSetting};
use pgrx::iter::TableIterator;
use pgrx::pg_sys::panic::ErrorReportable;
use pgrx::prelude::*;
use pgrx::spi::Spi;

mod bm25;
mod hybrid;
mod lance_store;
mod search_filter;
mod tokenizer;
mod uri_policy;

::pgrx::pg_module_magic!(name, version);

/// When set, Lance dataset paths must resolve inside this existing local
/// directory. Empty/NULL allows unrestricted URIs for explicitly trusted roles.
static ALLOWED_URI_PREFIX: GucSetting<Option<CString>> = GucSetting::<Option<CString>>::new(None);

/// Upper bound for `lance_scan` row counts. `0` disables the guard.
static MAX_SCAN_ROWS: GucSetting<i32> = GucSetting::<i32>::new(0);

/// Kill switch for operations that destroy or rewrite existing Lance data
/// (`lance_create_table(overwrite => true)`, `lance_restore_version`).
static ALLOW_DESTRUCTIVE_OPS: GucSetting<bool> = GucSetting::<bool>::new(true);

/// Keep individual search inputs bounded while allowing the longest observed
/// Common Crawl token (2,298,914 characters) to be searched exactly.
const MAX_QUERY_BYTES: usize = 2_500_000;
/// Maximum metadata-eligible IDs materialized for an exact filtered search.
const MAX_FILTERED_DOC_IDS: usize = 10_000;
const OUTBOX_MAX_ATTEMPTS: i32 = 8;

#[pg_guard]
pub extern "C-unwind" fn _PG_init() {
    GucRegistry::define_string_guc(
        c"pgwarc_lance.allowed_uri_prefix",
        c"Restrict which Lance dataset paths SQL functions may open",
        c"When set, restrict Lance paths to this existing local directory after \
              resolving symlinks. Parent traversal and nonlocal URIs are rejected. \
              NULL/empty allows unrestricted URIs for explicitly trusted roles.",
        &ALLOWED_URI_PREFIX,
        GucContext::Suset,
        GucFlags::default(),
    );
    GucRegistry::define_int_guc(
        c"pgwarc_lance.max_scan_rows",
        c"Maximum rows lance_scan may materialize per call (0 = unlimited)",
        c"Guards server memory use. Calls requesting more rows than this fail with \
              program_limit_exceeded instead of buffering them in the backend.",
        &MAX_SCAN_ROWS,
        0,
        i32::MAX,
        GucContext::Suset,
        GucFlags::default(),
    );
    GucRegistry::define_bool_guc(
        c"pgwarc_lance.allow_destructive_ops",
        c"Allow operations that delete documents or restore/overwrite Lance dataset history",
        c"When off, lance_document_delete(), lance_create_table(overwrite => true) and lance_restore_version() \
              are rejected. PostgreSQL transactions cannot undo these operations, so keeping \
              them off is recommended once a dataset holds production data.",
        &ALLOW_DESTRUCTIVE_OPS,
        GucContext::Suset,
        GucFlags::default(),
    );
}

/// Aborts the current statement with a SQLSTATE instead of a Rust panic.
fn fail(code: PgSqlErrorCode, message: &str) -> ! {
    ereport!(ERROR, code, message.to_string());
}

fn fail_operation(operation: &str, error: Box<dyn std::error::Error>) -> ! {
    fail(
        PgSqlErrorCode::ERRCODE_INTERNAL_ERROR,
        &format!("{operation} failed: {error}"),
    )
}

fn require_finite_vector(values: &[f32]) {
    if values.iter().any(|value| !value.is_finite()) {
        fail(
            PgSqlErrorCode::ERRCODE_INVALID_PARAMETER_VALUE,
            "input requires finite vector values (NaN and infinity are rejected)",
        );
    }
}

fn ensure_no_pending_outbox(uri: &str) {
    let has_pending = Spi::get_one_with_args::<bool>(
        "SELECT EXISTS (\
            SELECT 1 FROM pgwarc_lance.lance_outbox \
            WHERE uri = $1 AND state <> 'completed'\
        )",
        &[DatumWithOid::from(uri)],
    )
    .unwrap_or_report()
    .unwrap_or(false);
    if has_pending {
        fail(
            PgSqlErrorCode::ERRCODE_OBJECT_NOT_IN_PREREQUISITE_STATE,
            "dataset has unfinished Lance outbox work; drain or reconcile it before direct writes",
        );
    }
}

fn bounded_search_k(value: i32, name: &str) -> usize {
    if value < 0 {
        fail(
            PgSqlErrorCode::ERRCODE_INVALID_PARAMETER_VALUE,
            &format!("{name} must be non-negative"),
        );
    }
    if value > 10_000 {
        fail(
            PgSqlErrorCode::ERRCODE_PROGRAM_LIMIT_EXCEEDED,
            "search k exceeds the maximum of 10000",
        );
    }
    value as usize
}

fn ranking_options(
    candidate_k: i32,
    bm25_weight: f64,
    vector_weight: f64,
    rrf_k: f64,
) -> hybrid::RankingOptions {
    let candidate_k = bounded_search_k(candidate_k, "candidate_k");
    if !bm25_weight.is_finite()
        || !(0.0..=1000.0).contains(&bm25_weight)
        || !vector_weight.is_finite()
        || !(0.0..=1000.0).contains(&vector_weight)
        || (bm25_weight == 0.0 && vector_weight == 0.0)
    {
        fail(
            PgSqlErrorCode::ERRCODE_INVALID_PARAMETER_VALUE,
            "ranking weights must be finite values from 0 through 1000, with at least one source enabled",
        );
    }
    if !rrf_k.is_finite() || !(1.0..=10_000.0).contains(&rrf_k) {
        fail(
            PgSqlErrorCode::ERRCODE_INVALID_PARAMETER_VALUE,
            "rrf_k must be finite and between 1 and 10000",
        );
    }
    hybrid::RankingOptions {
        candidate_k,
        bm25_weight,
        vector_weight,
        rrf_k,
    }
}

fn require_query_text(query: &str) {
    if query.len() > MAX_QUERY_BYTES {
        fail(
            PgSqlErrorCode::ERRCODE_PROGRAM_LIMIT_EXCEEDED,
            "search query exceeds the maximum of 2500000 UTF-8 bytes",
        );
    }
}

fn nonnegative_i64(value: i32, name: &str) -> i64 {
    if value < 0 {
        fail(
            PgSqlErrorCode::ERRCODE_INVALID_PARAMETER_VALUE,
            &format!("{name} must be non-negative"),
        );
    }
    value as i64
}

fn positive_i32(value: i32, name: &str) -> i32 {
    if value <= 0 {
        fail(
            PgSqlErrorCode::ERRCODE_INVALID_PARAMETER_VALUE,
            &format!("{name} must be positive"),
        );
    }
    value
}

fn require_destructive_op(operation: &str) {
    if !ALLOW_DESTRUCTIVE_OPS.get() {
        fail(
            PgSqlErrorCode::ERRCODE_INSUFFICIENT_PRIVILEGE,
            &format!(
                "{operation} is destructive and is disabled by pgwarc_lance.allow_destructive_ops = off"
            ),
        );
    }
}

/// Return the authorized path used for I/O, logging and writer serialization.
fn authorize_uri(uri: &str) -> Cow<'_, str> {
    let Some(prefix) = ALLOWED_URI_PREFIX.get() else {
        return Cow::Borrowed(uri);
    };
    let prefix = prefix.to_string_lossy();
    if prefix.trim().is_empty() {
        return Cow::Borrowed(uri);
    }
    match uri_policy::restricted_path(&prefix, uri) {
        Ok(path) => Cow::Owned(path),
        Err(reason) => fail(PgSqlErrorCode::ERRCODE_INSUFFICIENT_PRIVILEGE, &reason),
    }
}

/// Stable 64-bit hash used as an advisory lock key so that concurrent writers
/// cannot append to the same dataset at the same time.
fn advisory_key(uri: &str) -> i64 {
    let mut hash: u64 = 0xcbf2_9ce4_8422_2325;
    for byte in uri.as_bytes() {
        hash ^= *byte as u64;
        hash = hash.wrapping_mul(0x0000_0100_0000_01b3);
    }
    hash as i64
}

/// Serializes writers per dataset for the lifetime of the calling transaction.
///
/// Lance datasets are files on disk that PostgreSQL cannot lock for us, so two
/// concurrent sessions appending would race and lose or corrupt data. The lock
/// is released on COMMIT and ROLLBACK.
fn lock_dataset(uri: &str) {
    Spi::run_with_args(
        "SELECT pg_advisory_xact_lock($1)",
        &[DatumWithOid::from(advisory_key(uri))],
    )
    .unwrap_or_report();
}

struct OutboxJob {
    id: i64,
    operation: String,
    uri: String,
    ids: Vec<i64>,
    flat_vectors: Vec<f32>,
    vector_dim: i32,
    labels: Vec<String>,
    revisions: Option<Vec<String>>,
    attempts: i32,
}

fn take_outbox_job() -> Option<OutboxJob> {
    Spi::connect_mut(|client| {
        let rows = client
            // The locking clause requires writable SPI execution. `select`
            // requests a read-only SPI snapshot and PostgreSQL rejects its
            // `FOR UPDATE SKIP LOCKED` clause even though this C function is
            // declared VOLATILE.
            .update(
                "SELECT job.id, job.operation, job.uri, job.ids, job.flat_vectors, job.vector_dim, \
                        job.labels, COALESCE(job.revisions, ARRAY[]::text[]) AS revisions, \
                        job.attempt_count \
                 FROM pgwarc_lance.lance_outbox AS job \
                 WHERE job.state = 'pending' \
                   AND (pg_current_xact_id_if_assigned() IS NULL \
                        OR job.xmin <> pg_current_xact_id_if_assigned()::xid) \
                   AND job.available_at <= clock_timestamp() \
                   AND NOT EXISTS ( \
                       SELECT 1 FROM pgwarc_lance.lance_outbox AS older \
                       WHERE older.uri = job.uri AND older.id < job.id \
                         AND older.state <> 'completed' \
                   ) \
                 ORDER BY job.id \
                 LIMIT 1 FOR UPDATE OF job SKIP LOCKED",
                Some(1),
                &[],
            )
            .unwrap_or_report();
        if rows.is_empty() {
            return None;
        }
        let row = rows.first();
        let revisions = row
            .get_by_name::<Vec<String>, _>("revisions")
            .unwrap_or_report()
            .unwrap();
        let revisions = (!revisions.is_empty()).then_some(revisions);
        Some(OutboxJob {
            id: row.get_by_name::<i64, _>("id").unwrap_or_report().unwrap(),
            operation: row
                .get_by_name::<String, _>("operation")
                .unwrap_or_report()
                .unwrap(),
            uri: row
                .get_by_name::<String, _>("uri")
                .unwrap_or_report()
                .unwrap(),
            ids: row
                .get_by_name::<Vec<i64>, _>("ids")
                .unwrap_or_report()
                .unwrap(),
            flat_vectors: row
                .get_by_name::<Vec<f32>, _>("flat_vectors")
                .unwrap_or_report()
                .unwrap(),
            vector_dim: row
                .get_by_name::<i32, _>("vector_dim")
                .unwrap_or_report()
                .unwrap(),
            labels: row
                .get_by_name::<Vec<String>, _>("labels")
                .unwrap_or_report()
                .unwrap(),
            revisions,
            attempts: row
                .get_by_name::<i32, _>("attempt_count")
                .unwrap_or_report()
                .unwrap(),
        })
    })
}

fn outbox_result(
    job_id: i64,
    status: &str,
    applied_version: Option<i64>,
) -> (i64, String, Option<i64>, i64) {
    let queue_lag_ms = Spi::get_one_with_args::<i64>(
        "SELECT GREATEST(0, floor(extract(epoch FROM \
            (clock_timestamp() - enqueued_at)) * 1000)::bigint) \
         FROM pgwarc_lance.lance_outbox WHERE id = $1",
        &[DatumWithOid::from(job_id)],
    )
    .unwrap_or_report()
    .unwrap_or(0);
    (job_id, status.to_string(), applied_version, queue_lag_ms)
}

fn fail_outbox_attempt(job: &OutboxJob, error: &str) -> (i64, String, Option<i64>, i64) {
    let attempt_count = job.attempts.saturating_add(1);
    let state = if attempt_count >= OUTBOX_MAX_ATTEMPTS {
        "failed"
    } else {
        "pending"
    };
    let bounded_error: String = error.chars().take(2048).collect();
    Spi::run_with_args(
        "UPDATE pgwarc_lance.lance_outbox \
         SET state = $2, attempt_count = $3, last_attempt_at = clock_timestamp(), \
             available_at = CASE WHEN $2 = 'failed' THEN available_at \
               ELSE clock_timestamp() + make_interval(secs => \
                 LEAST(3600, power(2, LEAST(12, $3 - 1))::integer)) END, \
             last_error = $4 \
         WHERE id = $1",
        &[
            DatumWithOid::from(job.id),
            DatumWithOid::from(state),
            DatumWithOid::from(attempt_count),
            DatumWithOid::from(bounded_error.as_str()),
        ],
    )
    .unwrap_or_report();
    outbox_result(job.id, state, None)
}

/// Records a Lance mutation in the current SQL transaction. Direct writes call
/// this before file mutation; outbox workers call it after the idempotent upsert.
/// A rolled-back row cannot establish whether an external Lance version survived.
fn record_write(uri: &str, operation: &str, row_count: i64) {
    Spi::run_with_args(
        "INSERT INTO pgwarc_lance.lance_write_log (uri, op, row_count) VALUES ($1, $2, $3)",
        &[
            DatumWithOid::from(uri),
            DatumWithOid::from(operation),
            DatumWithOid::from(row_count),
        ],
    )
    .unwrap_or_report();
}

fn is_hex_revision(revision: &str) -> bool {
    revision.len() == 64
        && revision
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn ensure_documents_untracked(uri: &str, ids: &[i64]) {
    if ids.is_empty() {
        return;
    }
    let tracked = Spi::get_one_with_args::<bool>(
        "SELECT EXISTS (SELECT 1 FROM pgwarc_lance.lance_document_revision \
         WHERE uri = $1 AND doc_id = ANY($2::bigint[]))",
        &[DatumWithOid::from(uri), DatumWithOid::from(ids.to_vec())],
    )
    .unwrap_or_report()
    .unwrap_or(false);
    if tracked {
        fail(
            PgSqlErrorCode::ERRCODE_OBJECT_NOT_IN_PREREQUISITE_STATE,
            "revision-tracked document IDs require revision-aware outbox operations",
        );
    }
}

/// Filter Lance candidates against the PostgreSQL revision/tombstone state.
/// Rows without a revision mapping remain visible for legacy datasets.
pub(crate) fn filter_vector_results(
    uri: &str,
    results: Vec<lance_store::SearchResult>,
) -> Vec<lance_store::SearchResult> {
    if results.is_empty() {
        return results;
    }
    let ids = results.iter().map(|result| result.id).collect::<Vec<_>>();
    let mut tracked = HashMap::<i64, (Option<String>, bool)>::new();
    Spi::connect(|client| {
        let rows = client
            .select(
                "SELECT requested.doc_id, state.expected_revision, state.is_deleted, \
                        state.doc_id IS NOT NULL AS tracked \
                 FROM unnest($1::bigint[]) AS requested(doc_id) \
                 LEFT JOIN pgwarc_lance.lance_document_revision AS state \
                   ON state.uri = $2 AND state.doc_id = requested.doc_id",
                None,
                &[DatumWithOid::from(ids), DatumWithOid::from(uri)],
            )
            .unwrap_or_report();
        for row in rows {
            if row
                .get_by_name::<bool, _>("tracked")
                .unwrap_or_report()
                .unwrap_or(false)
            {
                let id = row
                    .get_by_name::<i64, _>("doc_id")
                    .unwrap_or_report()
                    .unwrap();
                let expected = row
                    .get_by_name::<String, _>("expected_revision")
                    .unwrap_or_report();
                let deleted = row
                    .get_by_name::<bool, _>("is_deleted")
                    .unwrap_or_report()
                    .unwrap_or(true);
                tracked.insert(id, (expected, deleted));
            }
        }
        Ok::<(), pgrx::spi::SpiError>(())
    })
    .unwrap_or_report();

    results
        .into_iter()
        .filter(|result| match tracked.get(&result.id) {
            None => true,
            Some((expected, deleted)) => {
                !deleted && expected.as_deref() == result.revision.as_deref()
            }
        })
        .collect()
}

fn enforce_scan_limit(limit: i64) -> i64 {
    let max = MAX_SCAN_ROWS.get();
    if max > 0 && limit > max as i64 {
        fail(
            PgSqlErrorCode::ERRCODE_PROGRAM_LIMIT_EXCEEDED,
            &format!(
                "lance_scan limit {limit} exceeds pgwarc_lance.max_scan_rows ({max}); lower the limit or raise the setting"
            ),
        );
    }
    limit
}

#[derive(Default)]
struct WarcMetadata {
    target_uri: Option<String>,
    warc_date: Option<String>,
    content_type: Option<String>,
    http_status: Option<i32>,
    payload_digest: Option<String>,
    text_len: i32,
    source_file: Option<String>,
    domain: Option<String>,
    content_language: Option<String>,
    media_type: Option<String>,
    collection_id: Option<String>,
}

fn warc_metadata_for_docs(doc_ids: Vec<i64>) -> HashMap<i64, WarcMetadata> {
    let mut metadata_by_doc = HashMap::with_capacity(doc_ids.len());
    if doc_ids.is_empty() {
        return metadata_by_doc;
    }

    let sql = "SELECT
                   doc_id,
                   target_uri,
                   to_char(warc_date AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') AS warc_date,
                   content_type,
                   http_status,
                   payload_digest,
                   text_len,
                   source_file,
                   domain,
                   content_language,
                   media_type,
                   collection_id
               FROM pgwarc_lance.warc_record
               WHERE doc_id = ANY($1::bigint[])";

    Spi::connect(|client| {
        let rows = client
            .select(sql, None, &[DatumWithOid::from(doc_ids)])
            .unwrap_or_report();
        for row in rows {
            let doc_id = row
                .get_by_name::<i64, _>("doc_id")
                .unwrap_or_report()
                .unwrap();
            let metadata = WarcMetadata {
                target_uri: row
                    .get_by_name::<String, _>("target_uri")
                    .unwrap_or_report(),
                warc_date: row.get_by_name::<String, _>("warc_date").unwrap_or_report(),
                content_type: row
                    .get_by_name::<String, _>("content_type")
                    .unwrap_or_report(),
                http_status: row.get_by_name::<i32, _>("http_status").unwrap_or_report(),
                payload_digest: row
                    .get_by_name::<String, _>("payload_digest")
                    .unwrap_or_report(),
                text_len: row
                    .get_by_name::<i32, _>("text_len")
                    .unwrap_or_report()
                    .unwrap_or(0),
                source_file: row
                    .get_by_name::<String, _>("source_file")
                    .unwrap_or_report(),
                domain: row.get_by_name::<String, _>("domain").unwrap_or_report(),
                content_language: row
                    .get_by_name::<String, _>("content_language")
                    .unwrap_or_report(),
                media_type: row
                    .get_by_name::<String, _>("media_type")
                    .unwrap_or_report(),
                collection_id: row
                    .get_by_name::<String, _>("collection_id")
                    .unwrap_or_report(),
            };
            metadata_by_doc.insert(doc_id, metadata);
        }
        Ok::<(), pgrx::spi::SpiError>(())
    })
    .unwrap_or_report();

    metadata_by_doc
}

pgrx::extension_sql!(
    r#"
    CREATE SCHEMA IF NOT EXISTS pgwarc_lance;

    -- Cover both document lookups and BM25 statistics when visibility permits.
    CREATE TABLE IF NOT EXISTS pgwarc_lance.bm25_doc (
        doc_id     bigint NOT NULL,
        content    text,
        doc_len    integer NOT NULL DEFAULT 0,
        indexed_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (doc_id) INCLUDE (doc_len)
    );

    -- Keep corpus-wide BM25 count/average scans narrow and index-only after
    -- vacuum has marked the imported corpus pages all-visible.
    CREATE INDEX IF NOT EXISTS bm25_doc_doc_len_idx
        ON pgwarc_lance.bm25_doc (doc_len);

    CREATE TABLE IF NOT EXISTS pgwarc_lance.bm25_term (
        term    text NOT NULL,
        doc_id  bigint NOT NULL REFERENCES pgwarc_lance.bm25_doc(doc_id) ON DELETE CASCADE,
        tf      integer NOT NULL,
        PRIMARY KEY (term, doc_id) INCLUDE (tf)
    );

    CREATE INDEX IF NOT EXISTS bm25_term_doc_id_idx ON pgwarc_lance.bm25_term (doc_id);

    -- BM25 query setup counts matching postings by term before scoring them.
    -- Keep that document-frequency scan narrow instead of walking the
    -- (term, doc_id) primary key, whose INCLUDE(tf) payload is unnecessary.
    CREATE INDEX IF NOT EXISTS bm25_term_frequency_idx
        ON pgwarc_lance.bm25_term (term);

    -- Full oversized tokens stay in unindexed TOAST storage. Their short
    -- SHA-256 keys can safely participate in the existing posting B-tree.
    CREATE TABLE IF NOT EXISTS pgwarc_lance.bm25_long_term (
        term_key text PRIMARY KEY,
        term     text NOT NULL
    );

    CREATE TABLE IF NOT EXISTS pgwarc_lance.warc_record (
        doc_id         bigint PRIMARY KEY,
        target_uri     text,
        warc_date      timestamptz,
        content_type   text,
        http_status    integer,
        payload_digest text,
        text_len       integer NOT NULL DEFAULT 0,
        source_file    text,
        domain         text,
        content_language text,
        media_type     text,
        collection_id  text,
        imported_at    timestamptz NOT NULL DEFAULT now()
    );

    ALTER TABLE pgwarc_lance.warc_record ADD COLUMN IF NOT EXISTS domain text;
    ALTER TABLE pgwarc_lance.warc_record ADD COLUMN IF NOT EXISTS content_language text;
    ALTER TABLE pgwarc_lance.warc_record ADD COLUMN IF NOT EXISTS media_type text;
    ALTER TABLE pgwarc_lance.warc_record ADD COLUMN IF NOT EXISTS collection_id text;

    CREATE INDEX IF NOT EXISTS warc_record_target_uri_idx ON pgwarc_lance.warc_record (target_uri);
    CREATE INDEX IF NOT EXISTS warc_record_warc_date_idx ON pgwarc_lance.warc_record (warc_date);
    CREATE INDEX IF NOT EXISTS warc_record_domain_idx ON pgwarc_lance.warc_record (domain);
    CREATE INDEX IF NOT EXISTS warc_record_language_gin_idx
        ON pgwarc_lance.warc_record USING gin (string_to_array(content_language, ','));
    CREATE INDEX IF NOT EXISTS warc_record_media_type_idx ON pgwarc_lance.warc_record (media_type);
    CREATE INDEX IF NOT EXISTS warc_record_collection_idx ON pgwarc_lance.warc_record (collection_id);

    CREATE TABLE IF NOT EXISTS pgwarc_lance.lance_write_log (
        id         bigserial PRIMARY KEY,
        uri        text NOT NULL,
        op         text NOT NULL,
        row_count  bigint NOT NULL DEFAULT 0,
        wrote_at   timestamptz NOT NULL DEFAULT now()
    );

    CREATE INDEX IF NOT EXISTS lance_write_log_uri_idx ON pgwarc_lance.lance_write_log (uri);

    -- Vector payloads are committed atomically with BM25/WARC metadata. A
    -- worker later applies them to Lance using retry-safe ID upserts.
    CREATE TABLE IF NOT EXISTS pgwarc_lance.lance_outbox (
        id             bigserial PRIMARY KEY,
        operation      text NOT NULL DEFAULT 'upsert'
                       CHECK (operation IN ('upsert', 'delete')),
        uri            text NOT NULL,
        ids            bigint[] NOT NULL,
        flat_vectors   real[] NOT NULL,
        vector_dim     integer NOT NULL CHECK (vector_dim > 0),
        labels         text[] NOT NULL,
        revisions      text[],
        state          text NOT NULL DEFAULT 'pending'
                       CHECK (state IN ('pending', 'processing', 'completed', 'failed')),
        attempt_count  integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
        enqueued_at    timestamptz NOT NULL DEFAULT clock_timestamp(),
        available_at   timestamptz NOT NULL DEFAULT clock_timestamp(),
        last_attempt_at timestamptz,
        completed_at   timestamptz,
        applied_version bigint,
        last_error     text,
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
        ),
        CHECK (state = 'completed' OR revisions IS NULL
               OR cardinality(revisions) = cardinality(ids)),
        CHECK (revisions IS NULL OR cardinality(revisions) BETWEEN 1 AND 10000),
        CHECK ((state = 'completed') = (completed_at IS NOT NULL)),
        CHECK (state <> 'completed' OR applied_version IS NOT NULL)
    );

    CREATE INDEX IF NOT EXISTS lance_outbox_uri_id_idx
        ON pgwarc_lance.lance_outbox (uri, id);
    CREATE INDEX IF NOT EXISTS lance_outbox_available_idx
        ON pgwarc_lance.lance_outbox (available_at, id)
        WHERE state = 'pending';

    -- URI-scoped mapping of expected document content revisions. The actual
    -- applied revision is stored on the Lance row and inspected by the doctor.
    CREATE TABLE IF NOT EXISTS pgwarc_lance.lance_document_revision (
        uri               text NOT NULL,
        doc_id            bigint NOT NULL,
        expected_revision text NOT NULL CHECK (expected_revision ~ '^[0-9a-f]{64}$'),
        is_deleted        boolean NOT NULL DEFAULT false,
        updated_at        timestamptz NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY (uri, doc_id)
    );

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

    -- Extension member tables are otherwise omitted from pg_dump. All indexed
    -- documents, postings, archive metadata and write history are user data.
    SELECT pg_catalog.pg_extension_config_dump('pgwarc_lance.bm25_doc', '');
    SELECT pg_catalog.pg_extension_config_dump('pgwarc_lance.bm25_term', '');
    SELECT pg_catalog.pg_extension_config_dump('pgwarc_lance.bm25_long_term', '');
    SELECT pg_catalog.pg_extension_config_dump('pgwarc_lance.warc_record', '');
    SELECT pg_catalog.pg_extension_config_dump('pgwarc_lance.lance_write_log', '');
    SELECT pg_catalog.pg_extension_config_dump('pgwarc_lance.lance_write_log_id_seq', '');
    SELECT pg_catalog.pg_extension_config_dump('pgwarc_lance.lance_outbox', '');
    SELECT pg_catalog.pg_extension_config_dump('pgwarc_lance.lance_outbox_id_seq', '');
    SELECT pg_catalog.pg_extension_config_dump('pgwarc_lance.lance_document_revision', '');
    "#,
    name = "bm25_tables",
);

// PostgreSQL grants EXECUTE on new functions to PUBLIC by default. All extension
// functions are restricted during CREATE EXTENSION, in the same transaction, so
// an administrator can grant only the functions needed by each trusted role.
pgrx::extension_sql!(
    r#"
    DO $pgwarc_lance_privileges$
    DECLARE
        extension_function regprocedure;
        function_count integer := 0;
    BEGIN
        FOR extension_function IN
            SELECT p.oid::regprocedure
            FROM pg_proc AS p
            JOIN pg_depend AS d
              ON d.classid = 'pg_proc'::regclass
             AND d.objid = p.oid
             AND d.refclassid = 'pg_extension'::regclass
             AND d.deptype = 'e'
            JOIN pg_extension AS e ON e.oid = d.refobjid
            WHERE e.extname = 'pgwarc_lance'
              AND p.proname IN ('lance_outbox_enqueue', 'lance_outbox_enqueue_revision',
                                'lance_outbox_process_next', 'lance_document_delete')
        LOOP
            EXECUTE format(
                'ALTER FUNCTION %s SECURITY DEFINER SET search_path = pg_catalog, pg_temp',
                extension_function
            );
        END LOOP;
        FOR extension_function IN
            SELECT p.oid::regprocedure
            FROM pg_proc AS p
            JOIN pg_depend AS d
              ON d.classid = 'pg_proc'::regclass
             AND d.objid = p.oid
             AND d.refclassid = 'pg_extension'::regclass
             AND d.deptype = 'e'
            JOIN pg_extension AS e ON e.oid = d.refobjid
            WHERE e.extname = 'pgwarc_lance'
        LOOP
            EXECUTE format(
                'REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC',
                extension_function
            );
            function_count := function_count + 1;
        END LOOP;
        IF function_count = 0 THEN
            RAISE EXCEPTION 'pgwarc_lance function privilege lockdown found no functions';
        END IF;
    END
    $pgwarc_lance_privileges$;
    "#,
    name = "lockdown_function_privileges",
    finalize,
);

#[pg_extern]
fn hello_pgwarc_lance() -> &'static str {
    "Hello, pgwarc_lance!"
}

#[pg_extern]
fn add_numbers(a: i32, b: i32) -> i32 {
    a.saturating_add(b)
}

#[pg_extern]
fn fibonacci(n: i32) -> i64 {
    if n <= 0 {
        return 0;
    }
    let mut a: i64 = 0;
    let mut b: i64 = 1;
    for _ in 0..n {
        let next = a.saturating_add(b);
        a = b;
        b = next;
    }
    a
}

#[pg_extern]
fn array_sum(values: Vec<i32>) -> i32 {
    values.iter().fold(0i32, |acc, v| acc.saturating_add(*v))
}

#[pg_extern]
fn shout(text: &str) -> String {
    text.to_uppercase() + "!"
}

#[pg_extern]
fn greatest_of(a: i32, b: i32, c: i32) -> i32 {
    a.max(b).max(c)
}

#[pg_extern]
fn is_even(n: i32) -> bool {
    n % 2 == 0
}

#[pg_extern]
fn word_count(text: &str) -> i32 {
    text.split_whitespace().count() as i32
}

#[pg_extern]
fn double_or_none(n: Option<i32>) -> Option<i32> {
    n.map(|x| x.saturating_mul(2))
}

#[pg_extern]
fn greeting(name: &str, age: default!(i32, 20)) -> String {
    format!("Hello {}, you are {} years old!", name, age)
}

#[pg_extern]
fn tokenize_korean(text: &str) -> Vec<String> {
    bm25::tokenize(text)
}

#[pg_extern]
fn bm25_index_document(doc_id: i64, content: &str) {
    bm25::index_document(doc_id, content);
}

#[pg_extern]
fn bm25_doc_count() -> i64 {
    bm25::doc_count_pub()
}

#[pg_extern]
fn bm25_avg_doc_len() -> f64 {
    bm25::avg_doc_len_pub()
}

#[pg_extern]
fn bm25_search(
    query: &str,
    k: default!(i32, 10),
) -> TableIterator<'static, (name!(doc_id, i64), name!(score, f64))> {
    let hits = bm25::search(query, bounded_search_k(k, "k"));
    TableIterator::new(hits.into_iter().map(|h| (h.doc_id, h.score)))
}

#[pg_extern]
fn lance_create_table(uri: &str, vector_dim: default!(i32, 3), overwrite: default!(bool, false)) {
    let vector_dim = positive_i32(vector_dim, "vector_dim");
    let authorized_uri = authorize_uri(uri);
    let uri = authorized_uri.as_ref();
    if overwrite {
        require_destructive_op("lance_create_table(overwrite => true)");
    }
    lock_dataset(uri);
    ensure_no_pending_outbox(uri);
    record_write(
        uri,
        if overwrite {
            "create_overwrite"
        } else {
            "create"
        },
        0,
    );
    if let Err(e) = lance_store::create_dataset(uri, vector_dim, overwrite) {
        fail_operation("lance_create_table", e);
    }
}

#[pg_extern]
fn lance_insert(uri: &str, id: i64, vector: Vec<f32>, label: Option<String>) {
    require_finite_vector(&vector);
    let authorized_uri = authorize_uri(uri);
    let uri = authorized_uri.as_ref();
    lock_dataset(uri);
    ensure_no_pending_outbox(uri);
    ensure_documents_untracked(uri, &[id]);
    record_write(uri, "insert", 1);
    if let Err(e) = lance_store::insert_rows(uri, vec![id], vec![vector], vec![label]) {
        fail_operation("lance_insert", e);
    }
}

#[pg_extern]
fn lance_insert_many(
    uri: &str,
    ids: Vec<i64>,
    flat_vectors: Vec<f32>,
    vector_dim: i32,
    labels: Vec<String>,
) -> i64 {
    let vector_dim = positive_i32(vector_dim, "vector_dim");
    require_finite_vector(&flat_vectors);
    let authorized_uri = authorize_uri(uri);
    let uri = authorized_uri.as_ref();
    lock_dataset(uri);
    ensure_no_pending_outbox(uri);
    if ids.len() != labels.len() {
        fail(
            PgSqlErrorCode::ERRCODE_INVALID_PARAMETER_VALUE,
            &format!(
                "ids and labels must have the same length: {} ids, {} labels",
                ids.len(),
                labels.len()
            ),
        );
    }
    ensure_documents_untracked(uri, &ids);
    let row_count = ids.len() as i64;
    let labels = labels.into_iter().map(Some).collect();
    record_write(uri, "insert_many", row_count);
    if let Err(e) = lance_store::insert_flat_rows(uri, ids, flat_vectors, vector_dim, labels) {
        fail_operation("lance_insert_many", e);
    }
    row_count
}

/// Retry-safe merge for unique document IDs. Repeated writes update the existing
/// vector/label instead of appending another row; existing legacy duplicates in
/// the affected IDs are rejected before external mutation.
#[pg_extern]
fn lance_upsert_many(
    uri: &str,
    ids: Vec<i64>,
    flat_vectors: Vec<f32>,
    vector_dim: i32,
    labels: Vec<String>,
) -> i64 {
    let vector_dim = positive_i32(vector_dim, "vector_dim");
    let expected = ids.len().checked_mul(vector_dim as usize);
    if ids.len() > 10_000 || ids.len() != labels.len() || expected != Some(flat_vectors.len()) {
        fail(PgSqlErrorCode::ERRCODE_INVALID_PARAMETER_VALUE,
             "upsert requires at most 10000 matching IDs/labels and exactly rows * vector_dim values");
    }
    require_finite_vector(&flat_vectors);
    let unique: std::collections::HashSet<i64> = ids.iter().copied().collect();
    if unique.len() != ids.len() {
        fail(
            PgSqlErrorCode::ERRCODE_INVALID_PARAMETER_VALUE,
            "upsert requires unique document IDs and finite vector values",
        );
    }
    let authorized_uri = authorize_uri(uri);
    let uri = authorized_uri.as_ref();
    lock_dataset(uri);
    ensure_no_pending_outbox(uri);
    ensure_documents_untracked(uri, &ids);
    let row_count = ids.len() as i64;
    record_write(uri, "upsert_many", row_count);
    if let Err(error) = lance_store::upsert_flat_rows(
        uri,
        ids.clone(),
        flat_vectors,
        vector_dim,
        labels.into_iter().map(Some).collect(),
        vec![None; ids.len()],
    ) {
        fail_operation("lance_upsert_many", error);
    }
    row_count
}

/// Acquire the dataset writer lock before importer metadata/BM25 mutations.
/// Call it first in every transaction that changes one document set.
#[pg_extern]
fn lance_dataset_write_lock(uri: &str) {
    let authorized_uri = authorize_uri(uri);
    lock_dataset(authorized_uri.as_ref());
}

/// Persist a retry-safe vector upsert in PostgreSQL. This row commits or rolls
/// back with caller metadata; no Lance file is touched in the caller's txn.
#[pg_extern]
fn lance_outbox_enqueue(
    uri: &str,
    ids: Vec<i64>,
    flat_vectors: Vec<f32>,
    vector_dim: i32,
    labels: Vec<String>,
) -> i64 {
    enqueue_outbox(uri, ids, flat_vectors, vector_dim, labels, None)
}

/// Persist importer vectors and their expected document revisions atomically.
/// This is separate from the original unversioned API to preserve compatibility.
#[pg_extern]
fn lance_outbox_enqueue_revision(
    uri: &str,
    ids: Vec<i64>,
    flat_vectors: Vec<f32>,
    vector_dim: i32,
    labels: Vec<String>,
    revisions: Vec<String>,
) -> i64 {
    enqueue_outbox(uri, ids, flat_vectors, vector_dim, labels, Some(revisions))
}

fn enqueue_outbox(
    uri: &str,
    ids: Vec<i64>,
    flat_vectors: Vec<f32>,
    vector_dim: i32,
    labels: Vec<String>,
    revisions: Option<Vec<String>>,
) -> i64 {
    let vector_dim = positive_i32(vector_dim, "vector_dim");
    let expected_values = ids.len().checked_mul(vector_dim as usize);
    if ids.is_empty()
        || ids.len() > 10_000
        || ids.len() != labels.len()
        || expected_values != Some(flat_vectors.len())
    {
        fail(
            PgSqlErrorCode::ERRCODE_INVALID_PARAMETER_VALUE,
            "outbox enqueue requires 1..10000 matching IDs/labels and exactly rows * vector_dim values",
        );
    }
    let unique: std::collections::HashSet<i64> = ids.iter().copied().collect();
    if unique.len() != ids.len() {
        fail(
            PgSqlErrorCode::ERRCODE_INVALID_PARAMETER_VALUE,
            "outbox enqueue requires unique document IDs",
        );
    }
    if let Some(revisions) = &revisions {
        if revisions.len() != ids.len() || revisions.iter().any(|r| !is_hex_revision(r)) {
            fail(
                PgSqlErrorCode::ERRCODE_INVALID_PARAMETER_VALUE,
                "revision enqueue requires one 64-character hexadecimal revision per document ID",
            );
        }
    }
    require_finite_vector(&flat_vectors);
    let authorized_uri = authorize_uri(uri);
    let uri = authorized_uri.as_ref();
    // Serializing enqueue transactions gives each dataset a stable committed
    // order and prevents mixing an in-flight direct Lance write with a new job.
    lock_dataset(uri);
    if revisions.is_none() {
        ensure_documents_untracked(uri, &ids);
    }
    let job_id = Spi::get_one_with_args::<i64>(
        "INSERT INTO pgwarc_lance.lance_outbox \
         (uri, ids, flat_vectors, vector_dim, labels, revisions) \
         VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
        &[
            DatumWithOid::from(uri),
            DatumWithOid::from(ids.clone()),
            DatumWithOid::from(flat_vectors),
            DatumWithOid::from(vector_dim),
            DatumWithOid::from(labels),
            DatumWithOid::from(revisions.clone()),
        ],
    )
    .unwrap_or_report()
    .unwrap_or_else(|| {
        fail(
            PgSqlErrorCode::ERRCODE_INTERNAL_ERROR,
            "outbox insert returned no job ID",
        )
    });
    if let Some(revisions) = revisions {
        Spi::run_with_args(
            "INSERT INTO pgwarc_lance.lance_document_revision \
             (uri, doc_id, expected_revision) \
             SELECT $1, items.doc_id, items.revision \
             FROM unnest($2::bigint[], $3::text[]) AS items(doc_id, revision) \
             ON CONFLICT (uri, doc_id) DO UPDATE \
             SET expected_revision = EXCLUDED.expected_revision, is_deleted = false, \
                 updated_at = clock_timestamp()",
            &[
                DatumWithOid::from(uri),
                DatumWithOid::from(ids),
                DatumWithOid::from(revisions),
            ],
        )
        .unwrap_or_report();
    }
    job_id
}

/// Delete one or more documents by their current content revisions.
/// Metadata/BM25 removal, a durable Lance delete event, and the tombstone commit
/// together. The tombstone keeps the last content revision and sets is_deleted;
/// repeating the same request for it is a no-op.
#[pg_extern]
fn lance_document_delete(uri: &str, ids: Vec<i64>, expected_revisions: Vec<String>) -> i64 {
    if ids.is_empty()
        || ids.len() > 10_000
        || ids.len() != expected_revisions.len()
        || expected_revisions
            .iter()
            .any(|revision| !is_hex_revision(revision))
    {
        fail(
            PgSqlErrorCode::ERRCODE_INVALID_PARAMETER_VALUE,
            "document delete requires 1..10000 unique IDs and one 64-character lowercase hexadecimal content revision per ID",
        );
    }
    let unique: std::collections::HashSet<i64> = ids.iter().copied().collect();
    if unique.len() != ids.len() {
        fail(
            PgSqlErrorCode::ERRCODE_INVALID_PARAMETER_VALUE,
            "document delete requires unique document IDs",
        );
    }
    let authorized_uri = authorize_uri(uri);
    let uri = authorized_uri.as_ref();
    require_destructive_op("lance_document_delete");
    lock_dataset(uri);

    let other_uri_has_ids = Spi::get_one_with_args::<bool>(
        "SELECT EXISTS (SELECT 1 FROM pgwarc_lance.lance_document_revision \
         WHERE uri <> $1 AND doc_id = ANY($2::bigint[]))",
        &[DatumWithOid::from(uri), DatumWithOid::from(ids.clone())],
    )
    .unwrap_or_report()
    .unwrap_or(false);
    if other_uri_has_ids {
        fail(
            PgSqlErrorCode::ERRCODE_OBJECT_NOT_IN_PREREQUISITE_STATE,
            "document IDs are globally scoped and cannot be deleted through a different Lance URI",
        );
    }

    let mut current = HashMap::<i64, (String, bool)>::new();
    Spi::connect(|client| {
        let rows = client
            .select(
                "SELECT doc_id, expected_revision, is_deleted \
                 FROM pgwarc_lance.lance_document_revision \
                 WHERE uri = $1 AND doc_id = ANY($2::bigint[])",
                None,
                &[DatumWithOid::from(uri), DatumWithOid::from(ids.clone())],
            )
            .unwrap_or_report();
        for row in rows {
            current.insert(
                row.get_by_name::<i64, _>("doc_id")
                    .unwrap_or_report()
                    .unwrap(),
                (
                    row.get_by_name::<String, _>("expected_revision")
                        .unwrap_or_report()
                        .unwrap(),
                    row.get_by_name::<bool, _>("is_deleted")
                        .unwrap_or_report()
                        .unwrap(),
                ),
            );
        }
        Ok::<(), pgrx::spi::SpiError>(())
    })
    .unwrap_or_report();

    let mut delete_ids = Vec::with_capacity(ids.len());
    let mut delete_revisions = Vec::with_capacity(ids.len());
    for (doc_id, content_revision) in ids.iter().zip(expected_revisions.iter()) {
        match current.get(doc_id) {
            Some((current_revision, true)) if current_revision == content_revision => continue,
            Some((_, true)) => {
                fail(
                    PgSqlErrorCode::ERRCODE_OBJECT_NOT_IN_PREREQUISITE_STATE,
                    &format!("document {doc_id} is already deleted at a different revision"),
                );
            }
            Some((current_revision, false)) if current_revision != content_revision => {
                fail(
                    PgSqlErrorCode::ERRCODE_OBJECT_NOT_IN_PREREQUISITE_STATE,
                    &format!("document {doc_id} revision changed before delete"),
                );
            }
            None => {
                fail(
                    PgSqlErrorCode::ERRCODE_OBJECT_NOT_IN_PREREQUISITE_STATE,
                    &format!("document {doc_id} has no tracked revision; perform a revisioned outbox upsert before delete"),
                );
            }
            Some(_) => {}
        }
        delete_ids.push(*doc_id);
        delete_revisions.push(content_revision.clone());
    }
    if delete_ids.is_empty() {
        return 0;
    }

    Spi::run_with_args(
        "DELETE FROM pgwarc_lance.bm25_doc WHERE doc_id = ANY($1::bigint[])",
        &[DatumWithOid::from(delete_ids.clone())],
    )
    .unwrap_or_report();
    Spi::run_with_args(
        "DELETE FROM pgwarc_lance.warc_record WHERE doc_id = ANY($1::bigint[])",
        &[DatumWithOid::from(delete_ids.clone())],
    )
    .unwrap_or_report();
    Spi::run_with_args(
        "INSERT INTO pgwarc_lance.lance_document_revision \
         (uri, doc_id, expected_revision, is_deleted) \
         SELECT $1, items.doc_id, items.revision, true \
         FROM unnest($2::bigint[], $3::text[]) AS items(doc_id, revision) \
         ON CONFLICT (uri, doc_id) DO UPDATE \
         SET expected_revision = EXCLUDED.expected_revision, is_deleted = true, \
             updated_at = clock_timestamp()",
        &[
            DatumWithOid::from(uri),
            DatumWithOid::from(delete_ids.clone()),
            DatumWithOid::from(delete_revisions.clone()),
        ],
    )
    .unwrap_or_report();
    Spi::get_one_with_args::<i64>(
        "INSERT INTO pgwarc_lance.lance_outbox \
         (operation, uri, ids, flat_vectors, vector_dim, labels, revisions) \
         VALUES ('delete', $1, $2, ARRAY[]::real[], 1, ARRAY[]::text[], $3) \
         RETURNING id",
        &[
            DatumWithOid::from(uri),
            DatumWithOid::from(delete_ids),
            DatumWithOid::from(delete_revisions),
        ],
    )
    .unwrap_or_report()
    .unwrap_or_else(|| {
        fail(
            PgSqlErrorCode::ERRCODE_INTERNAL_ERROR,
            "document delete outbox insert returned no job ID",
        )
    })
}

/// Apply the oldest committed outbox event whose predecessor is complete.
/// The event lock, Lance mutation and completion update share one SQL transaction;
/// a crash after the Lance commit causes an idempotent replay, never a lost job.
#[pg_extern]
fn lance_outbox_process_next() -> TableIterator<
    'static,
    (
        name!(outbox_id, i64),
        name!(status, String),
        name!(applied_version, Option<i64>),
        name!(queue_lag_ms, i64),
    ),
> {
    if unsafe { pg_sys::IsTransactionBlock() } {
        fail(
            PgSqlErrorCode::ERRCODE_ACTIVE_SQL_TRANSACTION,
            "lance_outbox_process_next must run as a standalone statement after enqueue transactions commit",
        );
    }
    let Some(job) = take_outbox_job() else {
        return TableIterator::new(std::iter::empty::<(i64, String, Option<i64>, i64)>());
    };
    let uri = authorize_uri(&job.uri).into_owned();
    // Lock the same URI used by direct writes and by other queue workers.
    lock_dataset(&uri);
    Spi::run_with_args(
        "UPDATE pgwarc_lance.lance_outbox \
         SET state = 'processing', attempt_count = attempt_count + 1, \
             last_attempt_at = clock_timestamp() \
         WHERE id = $1",
        &[DatumWithOid::from(job.id)],
    )
    .unwrap_or_report();

    let delivery = match job.operation.as_str() {
        "upsert" => lance_store::upsert_flat_rows(
            &uri,
            job.ids.clone(),
            job.flat_vectors.clone(),
            job.vector_dim,
            job.labels.iter().cloned().map(Some).collect(),
            job.revisions
                .as_ref()
                .map(|revisions| revisions.iter().cloned().map(Some).collect())
                .unwrap_or_else(|| vec![None; job.ids.len()]),
        )
        .map(|row_count| (row_count, "outbox_upsert_many")),
        "delete" => {
            lance_store::delete_rows(&uri, &job.ids).map(|row_count| (row_count, "outbox_delete"))
        }
        other => Err(Box::new(std::io::Error::other(format!(
            "unsupported Lance outbox operation: {other}"
        ))) as Box<dyn std::error::Error>),
    };
    match delivery {
        Ok((row_count, operation)) => match lance_store::versions(&uri) {
            Ok(versions) => match versions.iter().map(|version| version.version).max() {
                Some(applied_version) => {
                    record_write(&uri, operation, row_count as i64);
                    Spi::run_with_args(
                        "UPDATE pgwarc_lance.lance_outbox \
                         SET state = 'completed', completed_at = clock_timestamp(), \
                             applied_version = $2, last_error = NULL, \
                             ids = ARRAY[]::bigint[], flat_vectors = ARRAY[]::real[], \
                             labels = ARRAY[]::text[] \
                         WHERE id = $1",
                        &[
                            DatumWithOid::from(job.id),
                            DatumWithOid::from(applied_version),
                        ],
                    )
                    .unwrap_or_report();
                    let result = outbox_result(job.id, "completed", Some(applied_version));
                    TableIterator::new(std::iter::once(result))
                }
                None => {
                    let result = fail_outbox_attempt(
                        &job,
                        "Lance returned no dataset versions after applying the outbox operation",
                    );
                    TableIterator::new(std::iter::once(result))
                }
            },
            Err(error) => {
                let message = error.to_string();
                let result = fail_outbox_attempt(&job, &message);
                TableIterator::new(std::iter::once(result))
            }
        },
        Err(error) => {
            let message = error.to_string();
            let result = fail_outbox_attempt(&job, &message);
            TableIterator::new(std::iter::once(result))
        }
    }
}

#[pg_extern]
fn lance_count(uri: &str) -> i64 {
    let authorized_uri = authorize_uri(uri);
    let uri = authorized_uri.as_ref();
    match lance_store::count_rows(uri) {
        Ok(count) => count as i64,
        Err(e) => fail_operation("lance_count", e),
    }
}

#[pg_extern]
fn lance_vector_dim(uri: &str) -> i32 {
    let authorized_uri = authorize_uri(uri);
    let uri = authorized_uri.as_ref();
    match lance_store::vector_dim(uri) {
        Ok(dim) => dim,
        Err(e) => fail_operation("lance_vector_dim", e),
    }
}

#[pg_extern]
fn lance_vector_search(
    uri: &str,
    query: Vec<f32>,
    k: default!(i32, 10),
) -> TableIterator<
    'static,
    (
        name!(id, i64),
        name!(label, Option<String>),
        name!(distance, f32),
    ),
> {
    let k = bounded_search_k(k, "k");
    require_finite_vector(&query);
    let authorized_uri = authorize_uri(uri);
    let uri = authorized_uri.as_ref();
    if k == 0 {
        return TableIterator::new(std::iter::empty::<(i64, Option<String>, f32)>());
    }
    match lance_store::vector_search(uri, query, k) {
        Ok(results) => TableIterator::new(
            filter_vector_results(uri, results)
                .into_iter()
                .map(|r| (r.id, r.label, r.distance)),
        ),
        Err(e) => fail_operation("lance_vector_search", e),
    }
}

#[pg_extern]
fn lance_scan(
    uri: &str,
    limit: default!(i32, 100),
) -> TableIterator<
    'static,
    (
        name!(id, i64),
        name!(vector, Vec<f32>),
        name!(label, Option<String>),
    ),
> {
    let limit = nonnegative_i64(limit, "limit");
    let authorized_uri = authorize_uri(uri);
    let uri = authorized_uri.as_ref();
    let limit = enforce_scan_limit(limit);
    if limit == 0 {
        return TableIterator::new(std::iter::empty::<(i64, Vec<f32>, Option<String>)>());
    }
    match lance_store::scan_rows(uri, limit) {
        Ok(rows) => TableIterator::new(rows.into_iter().map(|r| (r.id, r.vector, r.label))),
        Err(e) => fail_operation("lance_scan", e),
    }
}

/// Returns document IDs and ingestion revisions for read-only reconciliation.
/// Older Lance datasets expose a NULL revision until a revision-aware upsert.
#[pg_extern]
fn lance_scan_document_state(
    uri: &str,
    limit: default!(i32, 100),
) -> TableIterator<
    'static,
    (
        name!(doc_id, i64),
        name!(label, Option<String>),
        name!(revision, Option<String>),
    ),
> {
    let limit = nonnegative_i64(limit, "limit");
    let authorized_uri = authorize_uri(uri);
    let uri = authorized_uri.as_ref();
    let limit = enforce_scan_limit(limit);
    if limit == 0 {
        return TableIterator::new(std::iter::empty::<(i64, Option<String>, Option<String>)>());
    }
    match lance_store::scan_document_states(uri, limit) {
        Ok(rows) => TableIterator::new(
            rows.into_iter()
                .map(|row| (row.id, row.label, row.revision)),
        ),
        Err(error) => fail_operation("lance_scan_document_state", error),
    }
}

/// Lists every version of a Lance dataset, newest last.
///
/// Combined with `pgwarc_lance.lance_write_log` this is how an operator finds
/// the version to undo a write that PostgreSQL rolled back but Lance kept.
#[pg_extern]
fn lance_dataset_versions(
    uri: &str,
) -> TableIterator<'static, (name!(version, i64), name!(created_ms, i64))> {
    let authorized_uri = authorize_uri(uri);
    let uri = authorized_uri.as_ref();
    match lance_store::versions(uri) {
        Ok(versions) => TableIterator::new(versions.into_iter().map(|v| (v.version, v.created_ms))),
        Err(e) => fail_operation("lance_dataset_versions", e),
    }
}

/// Repromotes `version` as the dataset tip (see `lance_dataset_versions`).
#[pg_extern]
fn lance_restore_version(uri: &str, version: i64) -> i64 {
    let authorized_uri = authorize_uri(uri);
    let uri = authorized_uri.as_ref();
    require_destructive_op("lance_restore_version");
    lock_dataset(uri);
    ensure_no_pending_outbox(uri);
    if version < 0 {
        fail(
            PgSqlErrorCode::ERRCODE_INVALID_PARAMETER_VALUE,
            "version must be non-negative",
        );
    }
    record_write(uri, "restore", 0);
    match lance_store::restore_version(uri, version) {
        Ok(restored) => restored,
        Err(e) => fail_operation("lance_restore_version", e),
    }
}

#[pg_extern]
fn hybrid_search(
    query: &str,
    vector_query: Vec<f32>,
    lance_uri: &str,
    k: default!(i32, 10),
) -> TableIterator<'static, (name!(doc_id, i64), name!(score, f64), name!(source, String))> {
    let k = bounded_search_k(k, "k");
    require_query_text(query);
    require_finite_vector(&vector_query);
    let authorized_uri = authorize_uri(lance_uri);
    let lance_uri = authorized_uri.as_ref();
    if k == 0 {
        return TableIterator::new(std::iter::empty::<(i64, f64, String)>());
    }
    match hybrid::search(query, vector_query, lance_uri, k) {
        Ok(hits) => TableIterator::new(hits.into_iter().map(|h| (h.doc_id, h.score, h.source))),
        Err(e) => fail_operation("hybrid_search", e),
    }
}

/// Return the final ranking and each source's rank and weighted RRF contribution.
#[pg_extern]
fn hybrid_search_explain(
    query: &str,
    vector_query: Vec<f32>,
    lance_uri: &str,
    k: default!(i32, 10),
    candidate_k: default!(i32, 100),
    bm25_weight: default!(f64, 1.0),
    vector_weight: default!(f64, 1.0),
    rrf_k: default!(f64, 60.0),
) -> TableIterator<
    'static,
    (
        name!(doc_id, i64),
        name!(score, f64),
        name!(source, String),
        name!(bm25_rank, Option<i32>),
        name!(vector_rank, Option<i32>),
        name!(bm25_contribution, f64),
        name!(vector_contribution, f64),
    ),
> {
    let k = bounded_search_k(k, "k");
    let options = ranking_options(candidate_k, bm25_weight, vector_weight, rrf_k);
    require_query_text(query);
    require_finite_vector(&vector_query);
    let authorized_uri = authorize_uri(lance_uri);
    if k == 0 || options.candidate_k == 0 {
        return TableIterator::new(std::iter::empty::<(
            i64,
            f64,
            String,
            Option<i32>,
            Option<i32>,
            f64,
            f64,
        )>());
    }
    let hits = hybrid::search_with_options(
        query,
        vector_query,
        authorized_uri.as_ref(),
        k,
        options,
        None,
    )
    .unwrap_or_else(|error| fail_operation("hybrid_search_explain", error));
    TableIterator::new(hits.into_iter().map(|hit| {
        (
            hit.doc_id,
            hit.score,
            hit.source,
            hit.bm25_rank.map(|rank| rank as i32),
            hit.vector_rank.map(|rank| rank as i32),
            hit.bm25_contribution,
            hit.vector_contribution,
        )
    }))
}

#[pg_extern]
fn hybrid_warc_search(
    query: &str,
    vector_query: Vec<f32>,
    lance_uri: &str,
    k: default!(i32, 10),
) -> TableIterator<
    'static,
    (
        name!(doc_id, i64),
        name!(score, f64),
        name!(source, String),
        name!(target_uri, Option<String>),
        name!(warc_date, Option<String>),
        name!(content_type, Option<String>),
        name!(http_status, Option<i32>),
        name!(payload_digest, Option<String>),
        name!(text_len, i32),
        name!(source_file, Option<String>),
    ),
> {
    let k = bounded_search_k(k, "k");
    require_query_text(query);
    require_finite_vector(&vector_query);
    let authorized_uri = authorize_uri(lance_uri);
    let lance_uri = authorized_uri.as_ref();
    if k == 0 {
        return TableIterator::new(std::iter::empty::<(
            i64,
            f64,
            String,
            Option<String>,
            Option<String>,
            Option<String>,
            Option<i32>,
            Option<String>,
            i32,
            Option<String>,
        )>());
    }

    let hits = hybrid::search(query, vector_query, lance_uri, k)
        .unwrap_or_else(|e| fail_operation("hybrid_warc_search", e));
    // Resolve metadata in one SPI query instead of one query per returned hit.
    let doc_ids = hits.iter().map(|hit| hit.doc_id).collect::<Vec<_>>();
    let mut metadata_by_doc = warc_metadata_for_docs(doc_ids);
    let rows = hits.into_iter().map(move |h| {
        let metadata = metadata_by_doc.remove(&h.doc_id).unwrap_or_default();
        (
            h.doc_id,
            h.score,
            h.source,
            metadata.target_uri,
            metadata.warc_date,
            metadata.content_type,
            metadata.http_status,
            metadata.payload_digest,
            metadata.text_len,
            metadata.source_file,
        )
    });
    TableIterator::new(rows)
}

/// Search WARC records using one shared eligibility set for BM25 and vectors.
/// The vector path is an exact prefiltered scan, bounded to 10,000 eligible
/// metadata rows; it does not use an ANN index or post-filter top-k candidates.
#[pg_extern]
fn hybrid_warc_search_filtered(
    query: &str,
    vector_query: Vec<f32>,
    lance_uri: &str,
    filters: JsonB,
    k: default!(i32, 10),
) -> TableIterator<
    'static,
    (
        name!(doc_id, i64),
        name!(score, f64),
        name!(source, String),
        name!(target_uri, Option<String>),
        name!(warc_date, Option<String>),
        name!(content_type, Option<String>),
        name!(http_status, Option<i32>),
        name!(payload_digest, Option<String>),
        name!(text_len, i32),
        name!(source_file, Option<String>),
        name!(domain, Option<String>),
        name!(content_language, Option<String>),
        name!(media_type, Option<String>),
        name!(collection_id, Option<String>),
    ),
> {
    let k = bounded_search_k(k, "k");
    require_query_text(query);
    require_finite_vector(&vector_query);
    let filters = search_filter::SearchFilters::parse(filters);
    let authorized_uri = authorize_uri(lance_uri);
    let lance_uri = authorized_uri.as_ref();
    if k == 0 {
        return TableIterator::new(std::iter::empty::<(
            i64,
            f64,
            String,
            Option<String>,
            Option<String>,
            Option<String>,
            Option<i32>,
            Option<String>,
            i32,
            Option<String>,
            Option<String>,
            Option<String>,
            Option<String>,
            Option<String>,
        )>());
    }

    let eligible_doc_ids = filters.matching_document_ids();
    let hits = hybrid::search_filtered(query, vector_query, lance_uri, k, eligible_doc_ids)
        .unwrap_or_else(|error| fail_operation("hybrid_warc_search_filtered", error));
    let doc_ids = hits.iter().map(|hit| hit.doc_id).collect::<Vec<_>>();
    let mut metadata_by_doc = warc_metadata_for_docs(doc_ids);
    let rows = hits.into_iter().map(move |hit| {
        let metadata = metadata_by_doc.remove(&hit.doc_id).unwrap_or_default();
        (
            hit.doc_id,
            hit.score,
            hit.source,
            metadata.target_uri,
            metadata.warc_date,
            metadata.content_type,
            metadata.http_status,
            metadata.payload_digest,
            metadata.text_len,
            metadata.source_file,
            metadata.domain,
            metadata.content_language,
            metadata.media_type,
            metadata.collection_id,
        )
    });
    TableIterator::new(rows)
}

/// Filter WARC candidates before ranking and explain the per-source RRF terms.
/// Join the returned `doc_id` to `pgwarc_lance.warc_record` for metadata.
#[pg_extern]
fn hybrid_warc_search_filtered_explain(
    query: &str,
    vector_query: Vec<f32>,
    lance_uri: &str,
    filters: JsonB,
    k: default!(i32, 10),
    candidate_k: default!(i32, 100),
    bm25_weight: default!(f64, 1.0),
    vector_weight: default!(f64, 1.0),
    rrf_k: default!(f64, 60.0),
) -> TableIterator<
    'static,
    (
        name!(doc_id, i64),
        name!(score, f64),
        name!(source, String),
        name!(bm25_rank, Option<i32>),
        name!(vector_rank, Option<i32>),
        name!(bm25_contribution, f64),
        name!(vector_contribution, f64),
    ),
> {
    let k = bounded_search_k(k, "k");
    let options = ranking_options(candidate_k, bm25_weight, vector_weight, rrf_k);
    require_query_text(query);
    require_finite_vector(&vector_query);
    let filters = search_filter::SearchFilters::parse(filters);
    let authorized_uri = authorize_uri(lance_uri);
    if k == 0 || options.candidate_k == 0 {
        return TableIterator::new(std::iter::empty::<(
            i64,
            f64,
            String,
            Option<i32>,
            Option<i32>,
            f64,
            f64,
        )>());
    }
    let eligible_doc_ids = filters.matching_document_ids();
    let hits = hybrid::search_with_options(
        query,
        vector_query,
        authorized_uri.as_ref(),
        k,
        options,
        Some(eligible_doc_ids),
    )
    .unwrap_or_else(|error| fail_operation("hybrid_warc_search_filtered_explain", error));
    TableIterator::new(hits.into_iter().map(|hit| {
        (
            hit.doc_id,
            hit.score,
            hit.source,
            hit.bm25_rank.map(|rank| rank as i32),
            hit.vector_rank.map(|rank| rank as i32),
            hit.bm25_contribution,
            hit.vector_contribution,
        )
    }))
}

#[cfg(test)]
pub mod pg_test {
    pub fn setup(_options: Vec<&str>) {}

    #[must_use]
    pub fn postgresql_conf_options() -> Vec<&'static str> {
        vec![]
    }
}
