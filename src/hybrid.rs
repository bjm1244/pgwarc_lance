use std::cmp::Ordering;
use std::collections::HashMap;

use crate::bm25;
use crate::lance_store;

const RRF_K: f64 = 60.0;

pub struct HybridHit {
    pub doc_id: i64,
    pub score: f64,
    pub source: String,
}

pub fn search(
    query: &str,
    vector_query: Vec<f32>,
    lance_uri: &str,
    k: usize,
) -> Result<Vec<HybridHit>, Box<dyn std::error::Error>> {
    let bm25_hits = bm25::search(query, k);
    let vec_hits = lance_store::vector_search(lance_uri, vector_query, k)?;

    let bm25_rank: HashMap<i64, usize> = bm25_hits
        .iter()
        .enumerate()
        .map(|(i, h)| (h.doc_id, i + 1))
        .collect();
    let vec_rank: HashMap<i64, usize> = vec_hits
        .iter()
        .enumerate()
        .map(|(i, h)| (h.id, i + 1))
        .collect();

    let mut all_ids: std::collections::HashSet<i64> = std::collections::HashSet::new();
    all_ids.extend(bm25_rank.keys().copied());
    all_ids.extend(vec_rank.keys().copied());

    let mut fused: Vec<HybridHit> = Vec::new();
    for id in all_ids {
        let mut score = 0.0;
        let mut sources: Vec<&str> = Vec::new();

        if let Some(&rank) = bm25_rank.get(&id) {
            score += 1.0 / (rank as f64 + RRF_K);
            sources.push("bm25");
        }
        if let Some(&rank) = vec_rank.get(&id) {
            score += 1.0 / (rank as f64 + RRF_K);
            sources.push("vector");
        }

        fused.push(HybridHit {
            doc_id: id,
            score,
            source: sources.join("+"),
        });
    }

    fused.sort_by(|a, b| {
        let score_order = b.score.partial_cmp(&a.score).unwrap_or(Ordering::Equal);
        score_order.then_with(|| a.doc_id.cmp(&b.doc_id))
    });
    fused.truncate(k);
    Ok(fused)
}
