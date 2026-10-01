#!/usr/bin/env python3
"""Check the predeclared public quality gate and independent exact vector ranking.

Requires numpy. Run only against the populated disposable SciFact benchmark DB.
"""
from __future__ import annotations
import argparse
import json
import shlex
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-dir", type=Path, required=True)
    parser.add_argument("--psql-command", required=True)
    parser.add_argument("--lance-uri", required=True)
    args = parser.parse_args()
    import numpy as np
    root = args.baseline_dir
    fixture = json.loads((root / "fixture.json").read_text())
    spec = json.loads((root / "baseline-spec.json").read_text())
    quality = json.loads((root / "quality.json").read_text())
    if quality.get("doc_count") != spec["documents"] or quality.get("query_count") != len(spec["queries"]) or quality.get("k") != spec["k"] or len(quality.get("results", [])) != len(spec["queries"]):
        raise ValueError("quality run did not complete the predeclared fixture")
    if [row["name"] for row in quality["results"]] != [f"scifact-test-{key}" for key in spec["queries"]]:
        raise ValueError("quality result query identities differ from the predeclared selection")
    # Derive metrics from retrieved IDs and original qrels, independently of
    # eval_quality's stored hit/reciprocal-rank fields.
    reciprocal_ranks = []
    for query, row in zip(fixture["queries"], quality["results"]):
        returned = row["returned_doc_ids"]
        expected = set(query["expected_doc_ids"])
        if row["expected_doc_ids"] != query["expected_doc_ids"] or row["name"] != query["name"]:
            raise ValueError("result labels/identity differ from fixture")
        if len(returned) != spec["k"] or len(set(returned)) != len(returned):
            raise ValueError("hybrid search returned wrong count or duplicate IDs")
        if returned != [hit["doc_id"] for hit in row["hits"]]:
            raise ValueError("hybrid result IDs differ from recorded hits")
        rank = next((index for index, doc_id in enumerate(returned, 1) if doc_id in expected), None)
        reciprocal = 1 / rank if rank is not None else 0
        if bool(row["hit"]) != (rank is not None) or abs(reciprocal - float(row["reciprocal_rank"])) > 1e-9:
            raise ValueError("stored quality metrics disagree with original labels/retrieved IDs")
        reciprocal_ranks.append(reciprocal)
    hit_rate = sum(value > 0 for value in reciprocal_ranks) / len(reciprocal_ranks)
    mrr = sum(reciprocal_ranks) / len(reciprocal_ranks)
    if abs(hit_rate - quality["hit_rate_at_k"]) > 1e-9 or abs(mrr - quality["mrr_at_k"]) > 1e-9:
        raise ValueError("stored aggregate quality metrics do not match independently derived results")
    if hit_rate < spec["min_hit_rate"] or mrr < spec["min_mrr"]:
        raise ValueError(f"public baseline below predefined thresholds: hit@{spec['k']}={hit_rate}, MRR={mrr}")
    # eval_quality writes six-decimal SQL literals. Compare exactly that stored
    # float4 dataset/query representation, not the pre-serialization vectors.
    vectors = np.array([[float(f"{value:.6f}") for value in row["vector"]] for row in fixture["docs"]], dtype=np.float32)
    ids = np.array([row["doc_id"] for row in fixture["docs"]], dtype=np.int64)
    by_id = {int(doc_id): index for index, doc_id in enumerate(ids)}
    command = shlex.split(args.psql_command) + ["-X", "-q", "-At", "-v", "ON_ERROR_STOP=1"]
    uri = "'" + args.lance_uri.replace("'", "''") + "'"
    checked = 0
    for query in fixture["queries"]:
        vector = np.array([float(f"{value:.6f}") for value in query["vector"]], dtype=np.float32)
        distances = ((vectors - vector) ** 2).sum(axis=1)
        boundary = float(np.sort(distances)[spec["k"] - 1])
        literal = "ARRAY[" + ",".join(f"{float(value):.9g}" for value in vector) + "]::float4[]"
        sql = f"SET statement_timeout='2s'; SELECT id,distance FROM lance_vector_search({uri},{literal},{spec['k']});"
        result = subprocess.run(command, input=sql, text=True, capture_output=True, timeout=5)
        if result.returncode:
            raise ValueError(f"vector check query failed: {result.stderr}")
        hits = [line.split("|") for line in result.stdout.splitlines() if line.strip()]
        if len(hits) != spec["k"] or len({doc_id for doc_id, _ in hits}) != spec["k"]:
            raise ValueError("vector search returned wrong count or repeated document IDs")
        for doc_id, distance in hits:
            expected = float(distances[by_id[int(doc_id)]])
            if expected > boundary + 1e-5 or abs(float(distance) - expected) > 1e-5:
                raise ValueError(f"Lance result differs from exact NumPy L2 reference: {query['name']}")
        checked += 1
    report = {"status": "passed", "scope": "public regression baseline; operational corpus gate remains open",
              "documents": len(ids), "queries": checked, "k": spec["k"], "hit_rate_at_k": hit_rate, "mrr_at_k": mrr,
              "min_hit_rate": spec["min_hit_rate"], "min_mrr": spec["min_mrr"],
              "vector_reference": "exact NumPy float4 squared L2; serialization matched; tie tolerance 1e-5"}
    (root / "baseline-validation.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
