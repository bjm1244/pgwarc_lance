use std::collections::HashMap;

use pgrx::check_for_interrupts;
use pgrx::datum::DatumWithOid;
use pgrx::pg_sys::panic::ErrorReportable;
use pgrx::spi::Spi;
use sha2::{Digest, Sha256};

const K1: f64 = 1.2;
const B: f64 = 0.75;
const MAX_BTREE_TERM_BYTES: usize = 2000;
const MAX_SEARCHABLE_TERM_BYTES: usize = crate::MAX_QUERY_BYTES;
const LONG_TERM_KEY_PREFIX: &str = "\u{241f}pgwarc-long:";

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

fn long_term_key(term: &str) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let digest = Sha256::digest(term.as_bytes());
    let mut key = String::with_capacity(LONG_TERM_KEY_PREFIX.len() + digest.len() * 2);
    key.push_str(LONG_TERM_KEY_PREFIX);
    for byte in digest {
        key.push(HEX[(byte >> 4) as usize] as char);
        key.push(HEX[(byte & 0x0f) as usize] as char);
    }
    key
}

fn check_long_term_keys(keys: &[String], terms: &[String], register: bool) {
    if keys.is_empty() {
        return;
    }

    if register {
        check_for_interrupts!();
        Spi::run_with_args(
            "INSERT INTO pgwarc_lance.bm25_long_term (term_key, term) \
             SELECT incoming.term_key, incoming.term \
             FROM unnest($1::text[], $2::text[]) AS incoming(term_key, term) \
             ON CONFLICT (term_key) DO NOTHING",
            &[
                DatumWithOid::from(keys.to_vec()),
                DatumWithOid::from(terms.to_vec()),
            ],
        )
        .unwrap_or_report();
    }

    check_for_interrupts!();
    let collision = Spi::get_one_with_args::<bool>(
        "SELECT EXISTS (\
             SELECT 1 \
             FROM unnest($1::text[], $2::text[]) AS incoming(term_key, term) \
             JOIN pgwarc_lance.bm25_long_term stored USING (term_key) \
             WHERE stored.term <> incoming.term\
         )",
        &[
            DatumWithOid::from(keys.to_vec()),
            DatumWithOid::from(terms.to_vec()),
        ],
    )
    .unwrap_or_report()
    .unwrap_or(false);
    if collision {
        crate::fail(
            pgrx::prelude::PgSqlErrorCode::ERRCODE_DATA_EXCEPTION,
            "SHA-256 collision between distinct oversized BM25 tokens; operation aborted",
        );
    }
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
    if tf.keys().any(|term| term.len() > MAX_SEARCHABLE_TERM_BYTES) {
        crate::fail(
            pgrx::prelude::PgSqlErrorCode::ERRCODE_PROGRAM_LIMIT_EXCEEDED,
            "BM25 token exceeds the maximum searchable length of 2500000 UTF-8 bytes",
        );
    }
    let doc_len: i32 = tf.values().map(|&v| v as i32).sum();

    let mut terms: Vec<String> = Vec::with_capacity(tf.len());
    let mut freqs: Vec<i32> = Vec::with_capacity(tf.len());
    let mut long_term_keys = Vec::new();
    let mut long_terms = Vec::new();
    for (term, freq) in &tf {
        if term.len() > MAX_BTREE_TERM_BYTES {
            let key = long_term_key(term);
            long_term_keys.push(key.clone());
            long_terms.push(term.clone());
            terms.push(key);
        } else {
            terms.push(term.clone());
        }
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

    check_long_term_keys(&long_term_keys, &long_terms, true);

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

    let mut posting_terms = Vec::with_capacity(unique_terms.len());
    let mut long_term_keys = Vec::new();
    let mut long_terms = Vec::new();
    for term in unique_terms {
        if term.len() > MAX_BTREE_TERM_BYTES {
            long_term_keys.push(long_term_key(&term));
            long_terms.push(term);
        } else {
            posting_terms.push(term);
        }
    }
    let has_long_terms = !long_term_keys.is_empty();
    if has_long_terms {
        check_long_term_keys(&long_term_keys, &long_terms, false);
        posting_terms.extend(long_term_keys);
        posting_terms.sort();
    }

    // Aggregate and rank inside PostgreSQL. Its executor can spill to temp
    // files according to work_mem, rather than copying every matching posting
    // and every document score into unbounded Rust allocations. Only k rows
    // cross SPI. Statistics and postings share one statement snapshot, also
    // after a prior transaction write makes pgrx SPI execution writable.
    // Bind every value; no query text is interpolated into SQL.
    let standard_sql = r#"
        -- The doc_len-only index lets PostgreSQL count rows and average their
        -- non-null lengths without reading the wider content-bearing heap.
        WITH statistics AS MATERIALIZED (
            SELECT count(*)::float8 AS n, avg(doc_len)::float8 AS avgdl
            FROM pgwarc_lance.bm25_doc
        ), frequencies AS MATERIALIZED (
            SELECT term, count(*)::float8 AS nq
            FROM pgwarc_lance.bm25_term
            WHERE term = ANY($1::text[])
            GROUP BY term
        ), term_weights AS MATERIALIZED (
            -- IDF is constant per query term; materialize it once instead of
            -- evaluating ln() again for every matching posting.
            SELECT f.term,
                ln((s.n - f.nq + 0.5::float8) / (f.nq + 0.5::float8) + 1.0::float8) AS idf
            FROM frequencies f
            CROSS JOIN statistics s
            WHERE s.n > 0
        ), contributions AS (
            SELECT t.doc_id, t.term,
                f.idf
                * (t.tf::float8 * ($2::float8 + 1.0::float8))
                / nullif(t.tf::float8 + $2::float8 *
                    (1.0::float8 - $3::float8 + $3::float8 *
                    (d.doc_len::float8 / nullif(s.avgdl, 0.0::float8))), 0.0::float8) AS contribution
            FROM term_weights f
            JOIN pgwarc_lance.bm25_term t ON t.term = f.term
            JOIN pgwarc_lance.bm25_doc d ON d.doc_id = t.doc_id
            CROSS JOIN statistics s
            -- Keep the bound query restriction visible on this scan too.
            -- The materialized frequency CTE hides its values from the
            -- planner; the join alone can overestimate matching postings
            -- enough to select expensive scans and unnecessary JIT work.
            WHERE t.term = ANY($1::text[]) AND s.n > 0 AND s.avgdl > 0
        )
        SELECT doc_id, sum(contribution ORDER BY term) AS score
        FROM contributions
        GROUP BY doc_id
        HAVING count(contribution) > 0
        ORDER BY score DESC, doc_id ASC
        LIMIT $4::bigint
    "#;
    let long_term_sql = r#"
        WITH statistics AS MATERIALIZED (
            SELECT count(*)::float8 AS n, avg(doc_len)::float8 AS avgdl
            FROM pgwarc_lance.bm25_doc
        ), frequencies AS MATERIALIZED (
            SELECT term, count(*)::float8 AS nq
            FROM pgwarc_lance.bm25_term
            WHERE term = ANY($1::text[])
            GROUP BY term
        ), term_weights AS MATERIALIZED (
            -- IDF is constant per query term; materialize it once instead of
            -- evaluating ln() again for every matching posting.
            SELECT f.term,
                ln((s.n - f.nq + 0.5::float8) / (f.nq + 0.5::float8) + 1.0::float8) AS idf
            FROM frequencies f
            CROSS JOIN statistics s
            WHERE s.n > 0
        ), contributions AS (
            SELECT t.doc_id, COALESCE(long_term.term, t.term) AS sort_term,
                f.idf
                * (t.tf::float8 * ($2::float8 + 1.0::float8))
                / nullif(t.tf::float8 + $2::float8 *
                    (1.0::float8 - $3::float8 + $3::float8 *
                    (d.doc_len::float8 / nullif(s.avgdl, 0.0::float8))), 0.0::float8) AS contribution
            FROM term_weights f
            JOIN pgwarc_lance.bm25_term t ON t.term = f.term
            LEFT JOIN pgwarc_lance.bm25_long_term long_term ON long_term.term_key = t.term
            JOIN pgwarc_lance.bm25_doc d ON d.doc_id = t.doc_id
            CROSS JOIN statistics s
            WHERE t.term = ANY($1::text[]) AND s.n > 0 AND s.avgdl > 0
        )
        SELECT doc_id, sum(contribution ORDER BY sort_term) AS score
        FROM contributions
        GROUP BY doc_id
        HAVING count(contribution) > 0
        ORDER BY score DESC, doc_id ASC
        LIMIT $4::bigint
    "#;
    let sql = if has_long_terms {
        long_term_sql
    } else {
        standard_sql
    };
    let mut hits = Vec::new();
    check_for_interrupts!();
    Spi::connect(|client| {
        let rows = client
            .select(
                sql,
                None,
                &[
                    DatumWithOid::from(posting_terms),
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
