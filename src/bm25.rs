use std::collections::HashMap;

use pgrx::check_for_interrupts;
use pgrx::datum::DatumWithOid;
use pgrx::pg_sys::panic::ErrorReportable;
use pgrx::spi::Spi;

const K1: f64 = 1.2;
const B: f64 = 0.75;

pub fn tokenize(text: &str) -> Vec<String> {
    crate::tokenizer::tokenize(text)
}

pub fn term_frequencies(text: &str) -> HashMap<String, u32> {
    let mut tf = HashMap::new();
    for tok in tokenize(text) {
        *tf.entry(tok).or_insert(0u32) += 1;
    }
    tf
}

fn doc_count() -> i64 {
    check_for_interrupts!();
    Spi::get_one::<i64>("SELECT count(*) FROM pgwarc_lance.bm25_doc")
        .unwrap_or_report()
        .unwrap_or(0)
}

fn avg_doc_len() -> f64 {
    check_for_interrupts!();
    Spi::get_one::<f64>("SELECT coalesce(avg(doc_len), 0)::float8 FROM pgwarc_lance.bm25_doc")
        .unwrap_or_report()
        .unwrap_or(0.0)
}

pub fn doc_count_pub() -> i64 {
    doc_count()
}

pub fn avg_doc_len_pub() -> f64 {
    avg_doc_len()
}

pub fn index_document(doc_id: i64, content: &str) {
    let tf = term_frequencies(content);
    let doc_len: i32 = tf.values().map(|&v| v as i32).sum();

    let mut terms: Vec<String> = Vec::with_capacity(tf.len());
    let mut freqs: Vec<i32> = Vec::with_capacity(tf.len());
    for (term, freq) in &tf {
        terms.push(term.clone());
        freqs.push(*freq as i32);
    }

    check_for_interrupts!();
    Spi::run_with_args(
        "INSERT INTO pgwarc_lance.bm25_doc (doc_id, content, doc_len) \
         VALUES ($1, $2, $3) \
         ON CONFLICT (doc_id) DO UPDATE \
         SET content = EXCLUDED.content, doc_len = EXCLUDED.doc_len, indexed_at = now()",
        &[
            DatumWithOid::from(doc_id),
            DatumWithOid::from(content),
            DatumWithOid::from(doc_len),
        ],
    )
    .unwrap_or_report();

    // The upsert holds the document row lock until transaction end. Delete
    // postings only after taking that lock, including concurrent first inserts.
    check_for_interrupts!();
    Spi::run_with_args(
        "DELETE FROM pgwarc_lance.bm25_term WHERE doc_id = $1",
        &[DatumWithOid::from(doc_id)],
    )
    .unwrap_or_report();

    if terms.is_empty() {
        return;
    }

    // One set-based insert instead of one round trip per posting.
    check_for_interrupts!();
    Spi::run_with_args(
        "INSERT INTO pgwarc_lance.bm25_term (term, doc_id, tf) \
         SELECT u.term, $2, u.tf \
         FROM unnest($1::text[], $3::int[]) AS u(term, tf)",
        &[
            DatumWithOid::from(terms),
            DatumWithOid::from(doc_id),
            DatumWithOid::from(freqs),
        ],
    )
    .unwrap_or_report();
}

pub struct Bm25Hit {
    pub doc_id: i64,
    pub score: f64,
}

pub fn search(query: &str, k: usize) -> Vec<Bm25Hit> {
    crate::require_query_text(query);
    if k == 0 {
        return Vec::new();
    }
    let query_terms = tokenize(query);
    if query_terms.is_empty() {
        return Vec::new();
    }
    let mut unique_terms: Vec<String> = query_terms
        .into_iter()
        .collect::<std::collections::HashSet<_>>()
        .into_iter()
        .collect();
    unique_terms.sort();

    // Aggregate and rank inside PostgreSQL. Its executor can spill to temp
    // files according to work_mem, rather than copying every matching posting
    // and every document score into unbounded Rust allocations. Only k rows
    // cross SPI. Statistics and postings share one statement snapshot, also
    // after a prior transaction write makes pgrx SPI execution writable.
    // Bind every value; no query text is interpolated into SQL.
    let sql = r#"
        WITH statistics AS MATERIALIZED (
            SELECT count(*)::float8 AS n, avg(doc_len)::float8 AS avgdl
            FROM pgwarc_lance.bm25_doc
        ), frequencies AS MATERIALIZED (
            SELECT term, count(*)::float8 AS nq
            FROM pgwarc_lance.bm25_term
            WHERE term = ANY($1::text[])
            GROUP BY term
        ), contributions AS (
            SELECT t.doc_id, t.term,
                ln((s.n - f.nq + 0.5::float8) / (f.nq + 0.5::float8) + 1.0::float8)
                * (t.tf::float8 * ($2::float8 + 1.0::float8))
                / nullif(t.tf::float8 + $2::float8 *
                    (1.0::float8 - $3::float8 + $3::float8 *
                    (d.doc_len::float8 / nullif(s.avgdl, 0.0::float8))), 0.0::float8) AS contribution
            FROM frequencies f
            JOIN pgwarc_lance.bm25_term t ON t.term = f.term
            JOIN pgwarc_lance.bm25_doc d ON d.doc_id = t.doc_id
            CROSS JOIN statistics s
            WHERE s.n > 0 AND s.avgdl > 0
        )
        SELECT doc_id, sum(contribution ORDER BY term) AS score
        FROM contributions
        GROUP BY doc_id
        HAVING count(contribution) > 0
        ORDER BY score DESC, doc_id ASC
        LIMIT $4::bigint
    "#;
    let mut hits = Vec::new();
    check_for_interrupts!();
    Spi::connect(|client| {
        let rows = client
            .select(
                sql,
                None,
                &[
                    DatumWithOid::from(unique_terms),
                    DatumWithOid::from(K1),
                    DatumWithOid::from(B),
                    DatumWithOid::from(k as i64),
                ],
            )
            .unwrap_or_report();
        for row in rows {
            check_for_interrupts!();
            hits.push(Bm25Hit {
                doc_id: row
                    .get_by_name::<i64, _>("doc_id")
                    .unwrap_or_report()
                    .unwrap(),
                score: row
                    .get_by_name::<f64, _>("score")
                    .unwrap_or_report()
                    .unwrap(),
            });
        }
        Ok::<(), pgrx::spi::SpiError>(())
    })
    .unwrap_or_report();
    hits
}
