use std::cmp::Ordering;
use std::collections::HashMap;

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

fn sql_escape(s: &str) -> String {
    s.replace('\'', "''")
}

fn doc_count() -> i64 {
    Spi::get_one::<i64>("SELECT count(*) FROM pgwarc_lance.bm25_doc")
        .unwrap_or_report()
        .unwrap_or(0)
}

fn avg_doc_len() -> f64 {
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
    let doc_len = tf.values().map(|&v| v as i32).sum::<i32>();

    let did = doc_id.to_string();

    Spi::run(&format!(
        "DELETE FROM pgwarc_lance.bm25_term WHERE doc_id = {}",
        did
    ))
    .unwrap_or_report();
    Spi::run(&format!(
        "INSERT INTO pgwarc_lance.bm25_doc (doc_id, content, doc_len) VALUES ({}, '{}', {}) \
         ON CONFLICT (doc_id) DO UPDATE SET content = EXCLUDED.content, doc_len = EXCLUDED.doc_len, indexed_at = now()",
        did,
        sql_escape(content),
        doc_len
    ))
    .unwrap_or_report();

    for (term, freq) in &tf {
        Spi::run(&format!(
            "INSERT INTO pgwarc_lance.bm25_term (term, doc_id, tf) VALUES ('{}', {}, {})",
            sql_escape(term),
            did,
            freq
        ))
        .unwrap_or_report();
    }
}

pub struct Bm25Hit {
    pub doc_id: i64,
    pub score: f64,
}

pub fn search(query: &str, k: usize) -> Vec<Bm25Hit> {
    let query_terms = tokenize(query);
    if query_terms.is_empty() {
        return Vec::new();
    }

    let n = doc_count();
    if n == 0 {
        return Vec::new();
    }
    let avgdl = avg_doc_len();
    if avgdl == 0.0 {
        return Vec::new();
    }

    let mut unique_terms: Vec<String> = query_terms
        .iter()
        .cloned()
        .collect::<std::collections::HashSet<_>>()
        .into_iter()
        .collect();
    unique_terms.sort();
    unique_terms.dedup();

    let mut nqi: HashMap<String, i64> = HashMap::new();
    for term in &unique_terms {
        let cnt = Spi::get_one::<i64>(&format!(
            "SELECT count(DISTINCT doc_id) FROM pgwarc_lance.bm25_term WHERE term = '{}'",
            sql_escape(term)
        ))
        .unwrap_or_report()
        .unwrap_or(0);
        nqi.insert(term.clone(), cnt);
    }

    let mut scores: HashMap<i64, f64> = HashMap::new();
    for term in &unique_terms {
        let nq = *nqi.get(term).unwrap_or(&0);
        let idf = ((n as f64 - nq as f64 + 0.5) / (nq as f64 + 0.5) + 1.0).ln();

        let sql = format!(
            "SELECT t.doc_id, t.tf, d.doc_len FROM pgwarc_lance.bm25_term t \
             JOIN pgwarc_lance.bm25_doc d ON d.doc_id = t.doc_id WHERE t.term = '{}'",
            sql_escape(term)
        );

        Spi::connect(|client| {
            let table = client.select(&sql, None, &[]).unwrap_or_report();
            for row in table {
                let doc_id: i64 = row
                    .get_by_name::<i64, _>("doc_id")
                    .unwrap_or_report()
                    .unwrap_or(0);
                let tf: i32 = row
                    .get_by_name::<i32, _>("tf")
                    .unwrap_or_report()
                    .unwrap_or(0);
                let dl: i32 = row
                    .get_by_name::<i32, _>("doc_len")
                    .unwrap_or_report()
                    .unwrap_or(0);

                let tf = tf as f64;
                let dl = dl as f64;

                let denom = tf + K1 * (1.0 - B + B * (dl / avgdl));
                let contribution = idf * (tf * (K1 + 1.0)) / denom;
                *scores.entry(doc_id).or_insert(0.0) += contribution;
            }
            Ok::<(), pgrx::spi::SpiError>(())
        })
        .unwrap_or_report();
    }

    let mut hits: Vec<Bm25Hit> = scores
        .into_iter()
        .map(|(doc_id, score)| Bm25Hit { doc_id, score })
        .collect();
    hits.sort_by(|a, b| {
        let score_order = b.score.partial_cmp(&a.score).unwrap_or(Ordering::Equal);
        score_order.then_with(|| a.doc_id.cmp(&b.doc_id))
    });
    hits.truncate(k);
    hits
}
