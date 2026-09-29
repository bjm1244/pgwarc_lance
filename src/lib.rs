use pgrx::iter::TableIterator;
use pgrx::pg_sys::panic::ErrorReportable;
use pgrx::prelude::*;
use pgrx::spi::Spi;

mod bm25;
mod hybrid;
mod lance_store;
mod tokenizer;

::pgrx::pg_module_magic!(name, version);

fn nonnegative_usize(value: i32, name: &str) -> usize {
    if value < 0 {
        panic!("{name} must be non-negative");
    }
    value as usize
}

fn nonnegative_i64(value: i32, name: &str) -> i64 {
    if value < 0 {
        panic!("{name} must be non-negative");
    }
    value as i64
}

fn positive_i32(value: i32, name: &str) -> i32 {
    if value <= 0 {
        panic!("{name} must be positive");
    }
    value
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
    let sql = format!(
        r#"SELECT
               target_uri,
               to_char(warc_date AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS warc_date,
               content_type,
               http_status,
               payload_digest,
               text_len,
               source_file
           FROM pgwarc_lance.warc_record
           WHERE doc_id = {}"#,
        doc_id
    );

    let mut metadata = WarcMetadata::default();
    Spi::connect(|client| {
        let row = client.select(&sql, Some(1), &[]).unwrap_or_report().first();
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
    a + b
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
    values.iter().sum()
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
    n.map(|x| x * 2)
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
    let hits = bm25::search(query, nonnegative_usize(k, "k"));
    TableIterator::new(hits.into_iter().map(|h| (h.doc_id, h.score)))
}

#[pg_extern]
fn lance_create_table(uri: &str, vector_dim: default!(i32, 3), overwrite: default!(bool, false)) {
    let vector_dim = positive_i32(vector_dim, "vector_dim");
    lance_store::create_dataset(uri, vector_dim, overwrite)
        .unwrap_or_else(|e| panic!("lance_create_table failed: {e}"));
}

#[pg_extern]
fn lance_insert(uri: &str, id: i64, vector: Vec<f32>, label: Option<String>) {
    lance_store::insert_rows(uri, vec![id], vec![vector], vec![label])
        .unwrap_or_else(|e| panic!("lance_insert failed: {e}"));
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
    let labels = labels.into_iter().map(Some).collect();
    lance_store::insert_flat_rows(uri, ids, flat_vectors, vector_dim, labels)
        .unwrap_or_else(|e| panic!("lance_insert_many failed: {e}")) as i64
}

#[pg_extern]
fn lance_count(uri: &str) -> i64 {
    lance_store::count_rows(uri).unwrap_or_else(|e| panic!("lance_count failed: {e}")) as i64
}

#[pg_extern]
fn lance_vector_dim(uri: &str) -> i32 {
    lance_store::vector_dim(uri).unwrap_or_else(|e| panic!("lance_vector_dim failed: {e}"))
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
    let k = nonnegative_usize(k, "k");
    if k == 0 {
        return TableIterator::new(std::iter::empty::<(i64, Option<String>, f32)>());
    }
    let results = lance_store::vector_search(uri, query, k)
        .unwrap_or_else(|e| panic!("lance_vector_search failed: {e}"));
    TableIterator::new(results.into_iter().map(|r| (r.id, r.label, r.distance)))
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
    if limit == 0 {
        return TableIterator::new(std::iter::empty::<(i64, Vec<f32>, Option<String>)>());
    }
    let rows =
        lance_store::scan_rows(uri, limit).unwrap_or_else(|e| panic!("lance_scan failed: {e}"));
    TableIterator::new(rows.into_iter().map(|r| (r.id, r.vector, r.label)))
}

#[pg_extern]
fn hybrid_search(
    query: &str,
    vector_query: Vec<f32>,
    lance_uri: &str,
    k: default!(i32, 10),
) -> TableIterator<'static, (name!(doc_id, i64), name!(score, f64), name!(source, String))> {
    let k = nonnegative_usize(k, "k");
    if k == 0 {
        return TableIterator::new(std::iter::empty::<(i64, f64, String)>());
    }
    let hits = hybrid::search(query, vector_query, lance_uri, k)
        .unwrap_or_else(|e| panic!("hybrid_search failed: {e}"));
    TableIterator::new(hits.into_iter().map(|h| (h.doc_id, h.score, h.source)))
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
    let k = nonnegative_usize(k, "k");
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
        .unwrap_or_else(|e| panic!("hybrid_warc_search failed: {e}"));
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
