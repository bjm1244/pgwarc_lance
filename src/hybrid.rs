use std::cmp::Ordering;
use std::collections::HashMap;

use crate::bm25;
use crate::lance_store;

const RRF_K: f64 = 60.0;

pub struct HybridHit {
    pub doc_id: i64,
    pub score: f64,
    pub source: String,
    pub bm25_rank: Option<usize>,
    pub vector_rank: Option<usize>,
    pub bm25_contribution: f64,
    pub vector_contribution: f64,
}

#[derive(Clone, Copy)]
pub struct RankingOptions {
    pub candidate_k: usize,
    pub bm25_weight: f64,
    pub vector_weight: f64,
    pub rrf_k: f64,
}

pub fn search(
    query: &str,
    vector_query: Vec<f32>,
    lance_uri: &str,
    k: usize,
) -> Result<Vec<HybridHit>, Box<dyn std::error::Error>> {
    let mut vector_task = lance_store::spawn_vector_search(lance_uri, vector_query, k);
    let bm25_hits = bm25::search(query, k);
    let vec_hits = crate::filter_vector_results(lance_uri, vector_task.wait()?);

    Ok(fuse_hits(bm25_hits, vec_hits, k, 1.0, 1.0, RRF_K))
}

/// Apply the same PostgreSQL eligibility set before BM25 scoring and vector
/// nearest-neighbor ranking. The exact vector scan is bounded by the caller's
/// maximum eligibility-set size; stale/tombstoned rows are removed before the
/// final vector top-k is selected.
pub fn search_filtered(
    query: &str,
    vector_query: Vec<f32>,
    lance_uri: &str,
    k: usize,
    eligible_doc_ids: Vec<i64>,
) -> Result<Vec<HybridHit>, Box<dyn std::error::Error>> {
    if eligible_doc_ids.is_empty() || k == 0 {
        return Ok(Vec::new());
    }
    let mut vector_task = lance_store::spawn_filtered_vector_search(
        lance_uri,
        vector_query,
        eligible_doc_ids.clone(),
    );
    let bm25_hits = bm25::search_filtered(query, k, &eligible_doc_ids);
    let vec_hits = crate::filter_vector_results(lance_uri, vector_task.wait()?)
        .into_iter()
        .take(k)
        .collect();

    Ok(fuse_hits(bm25_hits, vec_hits, k, 1.0, 1.0, RRF_K))
}

/// Search with independent per-source candidate limits and weighted RRF.
/// A zero source weight disables that source; errors from an enabled source
/// propagate so a partial result is never mistaken for a complete hybrid rank.
pub fn search_with_options(
    query: &str,
    vector_query: Vec<f32>,
    lance_uri: &str,
    final_k: usize,
    options: RankingOptions,
    eligible_doc_ids: Option<Vec<i64>>,
) -> Result<Vec<HybridHit>, Box<dyn std::error::Error>> {
    if final_k == 0 || options.candidate_k == 0 {
        return Ok(Vec::new());
    }

    let mut vector_task = if options.vector_weight > 0.0 {
        match eligible_doc_ids.as_ref() {
            Some(ids) if ids.is_empty() => None,
            Some(ids) => Some(lance_store::spawn_filtered_vector_search(
                lance_uri,
                vector_query,
                ids.clone(),
            )),
            None => Some(lance_store::spawn_vector_search(
                lance_uri,
                vector_query,
                options.candidate_k,
            )),
        }
    } else {
        None
    };

    let bm25_hits = if options.bm25_weight == 0.0 {
        Vec::new()
    } else {
        match eligible_doc_ids.as_ref() {
            Some(ids) => bm25::search_filtered(query, options.candidate_k, ids),
            None => bm25::search(query, options.candidate_k),
        }
    };

    let vec_hits = match vector_task.as_mut() {
        Some(task) => {
            let mut hits = crate::filter_vector_results(lance_uri, task.wait()?);
            hits.truncate(options.candidate_k);
            hits
        }
        None => Vec::new(),
    };

    Ok(fuse_hits(
        bm25_hits,
        vec_hits,
        final_k,
        options.bm25_weight,
        options.vector_weight,
        options.rrf_k,
    ))
}

fn fuse_hits(
    bm25_hits: Vec<bm25::Bm25Hit>,
    vec_hits: Vec<lance_store::SearchResult>,
    k: usize,
    bm25_weight: f64,
    vector_weight: f64,
    rrf_k: f64,
) -> Vec<HybridHit> {
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

    let mut all_ids: std::collections::BTreeSet<i64> = std::collections::BTreeSet::new();
    all_ids.extend(bm25_rank.keys().copied());
    all_ids.extend(vec_rank.keys().copied());

    let mut fused: Vec<HybridHit> = Vec::new();
    for id in all_ids {
        let bm25_rank = bm25_rank.get(&id).copied();
        let vector_rank = vec_rank.get(&id).copied();
        let bm25_contribution = bm25_rank
            .map(|rank| bm25_weight / (rank as f64 + rrf_k))
            .unwrap_or(0.0);
        let vector_contribution = vector_rank
            .map(|rank| vector_weight / (rank as f64 + rrf_k))
            .unwrap_or(0.0);
        let score = bm25_contribution + vector_contribution;
        let mut sources: Vec<&str> = Vec::new();

        if bm25_rank.is_some() {
            sources.push("bm25");
        }
        if vector_rank.is_some() {
            sources.push("vector");
        }

        fused.push(HybridHit {
            doc_id: id,
            score,
            source: sources.join("+"),
            bm25_rank,
            vector_rank,
            bm25_contribution,
            vector_contribution,
        });
    }

    fused.sort_by(|a, b| {
        let score_order = b.score.partial_cmp(&a.score).unwrap_or(Ordering::Equal);
        score_order.then_with(|| a.doc_id.cmp(&b.doc_id))
    });
    fused.truncate(k);
    fused
}
