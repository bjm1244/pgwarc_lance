use std::borrow::Cow;
use std::ffi::CString;

use pgrx::datum::DatumWithOid;
use pgrx::guc::{GucContext, GucFlags, GucRegistry, GucSetting};
use pgrx::iter::TableIterator;
use pgrx::pg_sys::panic::ErrorReportable;
use pgrx::prelude::*;
use pgrx::spi::Spi;

mod bm25;
mod hybrid;
mod lance_store;
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
        c"Allow operations that overwrite or restore Lance dataset history",
        c"When off, lance_create_table(overwrite => true) and lance_restore_version() \
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

fn require_query_text(query: &str) {
    if query.len() > 65_536 {
        fail(
            PgSqlErrorCode::ERRCODE_PROGRAM_LIMIT_EXCEEDED,
            "search query exceeds the maximum of 65536 UTF-8 bytes",
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

/// Records a committed Lance write next to the SQL data.
///
/// Insert the log row before external mutation so missing SQL privileges,
/// sequence access, or SQL-side insert errors cannot leave unlogged file changes.
/// Rows in this table live in the same transaction as the write itself, so they
/// represent writes that PostgreSQL committed. A rolled back transaction cannot
/// undo the already-committed Lance append, so this log plus
/// `lance_dataset_versions`/`lance_restore_version` is the reconciliation path.
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
}

fn warc_metadata_for_doc(doc_id: i64) -> WarcMetadata {
    let sql = "SELECT
                   target_uri,
                   to_char(warc_date AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') AS warc_date,
                   content_type,
                   http_status,
                   payload_digest,
                   text_len,
                   source_file
               FROM pgwarc_lance.warc_record
               WHERE doc_id = $1";

    let mut metadata = WarcMetadata::default();
    Spi::connect(|client| {
        let row = client
            .select(sql, Some(1), &[DatumWithOid::from(doc_id)])
            .unwrap_or_report()
            .first();
        if !row.is_empty() {
            metadata.target_uri = row
                .get_by_name::<String, _>("target_uri")
                .unwrap_or_report();
            metadata.warc_date = row.get_by_name::<String, _>("warc_date").unwrap_or_report();
            metadata.content_type = row
                .get_by_name::<String, _>("content_type")
                .unwrap_or_report();
            metadata.http_status = row.get_by_name::<i32, _>("http_status").unwrap_or_report();
            metadata.payload_digest = row
                .get_by_name::<String, _>("payload_digest")
                .unwrap_or_report();
            metadata.text_len = row
                .get_by_name::<i32, _>("text_len")
                .unwrap_or_report()
                .unwrap_or(0);
            metadata.source_file = row
                .get_by_name::<String, _>("source_file")
                .unwrap_or_report();
        }
        Ok::<(), pgrx::spi::SpiError>(())
    })
    .unwrap_or_report();

    metadata
}

pgrx::extension_sql!(
    r#"
    CREATE SCHEMA IF NOT EXISTS pgwarc_lance;

    CREATE TABLE IF NOT EXISTS pgwarc_lance.bm25_doc (
        doc_id     bigint PRIMARY KEY,
        content    text,
        doc_len    integer NOT NULL DEFAULT 0,
        indexed_at timestamptz NOT NULL DEFAULT now()
    );

    CREATE TABLE IF NOT EXISTS pgwarc_lance.bm25_term (
        term    text NOT NULL,
        doc_id  bigint NOT NULL REFERENCES pgwarc_lance.bm25_doc(doc_id) ON DELETE CASCADE,
        tf      integer NOT NULL,
        PRIMARY KEY (term, doc_id)
    );

    CREATE INDEX IF NOT EXISTS bm25_term_term_idx ON pgwarc_lance.bm25_term (term);
    CREATE INDEX IF NOT EXISTS bm25_term_doc_id_idx ON pgwarc_lance.bm25_term (doc_id);

    CREATE TABLE IF NOT EXISTS pgwarc_lance.warc_record (
        doc_id         bigint PRIMARY KEY,
        target_uri     text,
        warc_date      timestamptz,
        content_type   text,
        http_status    integer,
        payload_digest text,
        text_len       integer NOT NULL DEFAULT 0,
        source_file    text,
        imported_at    timestamptz NOT NULL DEFAULT now()
    );

    CREATE INDEX IF NOT EXISTS warc_record_target_uri_idx ON pgwarc_lance.warc_record (target_uri);
    CREATE INDEX IF NOT EXISTS warc_record_warc_date_idx ON pgwarc_lance.warc_record (warc_date);

    CREATE TABLE IF NOT EXISTS pgwarc_lance.lance_write_log (
        id         bigserial PRIMARY KEY,
        uri        text NOT NULL,
        op         text NOT NULL,
        row_count  bigint NOT NULL DEFAULT 0,
        wrote_at   timestamptz NOT NULL DEFAULT now()
    );

    CREATE INDEX IF NOT EXISTS lance_write_log_uri_idx ON pgwarc_lance.lance_write_log (uri);

    -- Extension member tables are otherwise omitted from pg_dump. All indexed
    -- documents, postings, archive metadata and write history are user data.
    SELECT pg_catalog.pg_extension_config_dump('pgwarc_lance.bm25_doc', '');
    SELECT pg_catalog.pg_extension_config_dump('pgwarc_lance.bm25_term', '');
    SELECT pg_catalog.pg_extension_config_dump('pgwarc_lance.warc_record', '');
    SELECT pg_catalog.pg_extension_config_dump('pgwarc_lance.lance_write_log', '');
    SELECT pg_catalog.pg_extension_config_dump('pgwarc_lance.lance_write_log_id_seq', '');
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
    let row_count = ids.len() as i64;
    record_write(uri, "upsert_many", row_count);
    if let Err(error) = lance_store::upsert_flat_rows(
        uri,
        ids,
        flat_vectors,
        vector_dim,
        labels.into_iter().map(Some).collect(),
    ) {
        fail_operation("lance_upsert_many", error);
    }
    row_count
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
        Ok(results) => TableIterator::new(results.into_iter().map(|r| (r.id, r.label, r.distance))),
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
    let rows = hits.into_iter().map(|h| {
        let metadata = warc_metadata_for_doc(h.doc_id);
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

#[cfg(test)]
pub mod pg_test {
    pub fn setup(_options: Vec<&str>) {}

    #[must_use]
    pub fn postgresql_conf_options() -> Vec<&'static str> {
        vec![]
    }
}
