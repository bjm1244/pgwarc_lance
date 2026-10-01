#!/usr/bin/env python3
"""Build a public retrieval regression fixture from BEIR SciFact and MiniLM ONNX.

Requires numpy, onnxruntime and tokenizers in a separate benchmark environment.
Assets must be downloaded beforehand. No private corpus or paid API is used.
The BEIR dataset card declares CC-BY-SA-4.0; the model card declares Apache-2.0. Keep their attribution
with derived benchmark artifacts. This baseline is not an operational WARC
corpus, and its metrics do not establish production quality for another domain.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time
import zipfile
from pathlib import Path
from download_scifact_onnx_assets import MODEL_FILES, REVISION

DATASET_SHA256 = "536e14446a0ba56ed1398ab1055f39fe852686ecad24a6306c80c490fa8e0165"
MODEL_SHA256 = "6fd5d72fe4589f189f8ebc006442dbb529bb7ce38f8082112682524616046452"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(65536):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--queries", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    if not 20 <= args.queries <= 50 or min(args.batch_size, args.threads) <= 0:
        parser.error("choose 20-50 queries and positive batch/thread counts")
    import numpy as np
    import onnxruntime as ort
    from tokenizers import Tokenizer

    root, output = args.asset_dir, args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    assets = json.loads((root / "assets.json").read_text())
    model_dir = root / "model"
    if assets["model"] != "sentence-transformers/all-MiniLM-L6-v2" or sha256(model_dir / "onnx/model.onnx") != MODEL_SHA256:
        raise ValueError("model identity/hash does not match the reviewed ONNX asset")
    if assets["model_sha256"] != MODEL_FILES or assets["revision"] != REVISION:
        raise ValueError("asset manifest differs from pinned model revision/checksums")
    for name, expected in MODEL_FILES.items():
        if sha256(model_dir / name) != expected:
            raise ValueError(f"model asset checksum mismatch: {name}")
    if sha256(root / "scifact.zip") != DATASET_SHA256 or assets["dataset_sha256"] != DATASET_SHA256:
        raise ValueError("dataset checksum mismatch")
    with zipfile.ZipFile(root / "scifact.zip") as archive:
        for name in ["scifact/corpus.jsonl", "scifact/queries.jsonl", "scifact/qrels/test.tsv"]:
            if sha256(root / name) != hashlib.sha256(archive.read(name)).hexdigest():
                raise ValueError(f"extracted dataset content differs from archive: {name}")
    pooling = json.loads((model_dir / "1_Pooling/config.json").read_text())
    if not pooling["pooling_mode_mean_tokens"] or any(value for key, value in pooling.items() if key.startswith("pooling_mode_") and key != "pooling_mode_mean_tokens"):
        raise ValueError("this fixture builder requires mean-token pooling only")
    max_length = json.loads((model_dir / "sentence_bert_config.json").read_text())["max_seq_length"]
    if max_length != 256 or pooling["word_embedding_dimension"] != 384:
        raise ValueError("unexpected embedding dimension or truncation policy")
    corpus = [json.loads(line) for line in (root / "scifact/corpus.jsonl").read_text().splitlines() if line.strip()]
    queries = {row["_id"]: row for row in (json.loads(line) for line in (root / "scifact/queries.jsonl").read_text().splitlines() if line.strip())}
    relevance: dict[str, set[int]] = {}
    with (root / "scifact/qrels/test.tsv").open() as source:
        for row in csv.DictReader(source, delimiter="\t"):
            if int(row["score"]) > 0:
                relevance.setdefault(row["query-id"], set()).add(int(row["corpus-id"]))
    selected = sorted(relevance, key=int)[:args.queries]
    corpus_ids = {int(row["_id"]) for row in corpus}
    if len(corpus) != 5183 or len(corpus_ids) != len(corpus) or len(selected) != args.queries:
        raise ValueError("unexpected corpus size/identity or insufficient queries")
    if any(not relevance[key] <= corpus_ids for key in selected):
        raise ValueError("relevance labels refer to missing corpus documents")
    spec = {"baseline": "beir-scifact-minilm-onnx", "operational_corpus": False,
            "documents": len(corpus), "queries": selected, "selection": "first test qrel IDs in numeric order; fixed before evaluation",
            "k": 10, "min_hit_rate": 0.60, "min_mrr": 0.40,
            "assets": assets, "max_seq_length": max_length,
            "embedding_batch_size": args.batch_size, "embedding_threads": args.threads,
            "model_training_overlap_with_benchmark": "not audited; this is a regression baseline, not a zero-shot generalization claim"}
    (output / "baseline-spec.json").write_text(json.dumps(spec, indent=2) + "\n")
    tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
    tokenizer.enable_truncation(max_length=max_length)
    tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
    options = ort.SessionOptions()
    options.intra_op_num_threads, options.inter_op_num_threads = args.threads, 1
    session = ort.InferenceSession(str(model_dir / "onnx/model.onnx"), sess_options=options, providers=["CPUExecutionProvider"])
    names = {item.name for item in session.get_inputs()}
    if names != {"input_ids", "attention_mask", "token_type_ids"}:
        raise ValueError(f"unexpected ONNX inputs: {names}")

    def embed(texts: list[str]) -> list[list[float]]:
        result = []
        for start in range(0, len(texts), args.batch_size):
            encodings = tokenizer.encode_batch(texts[start:start + args.batch_size])
            inputs = {"input_ids": np.array([e.ids for e in encodings], dtype=np.int64),
                      "attention_mask": np.array([e.attention_mask for e in encodings], dtype=np.int64),
                      "token_type_ids": np.array([e.type_ids for e in encodings], dtype=np.int64)}
            tokens = session.run(None, inputs)[0]
            if tokens.shape != (len(encodings), inputs["input_ids"].shape[1], 384):
                raise ValueError(f"unexpected ONNX output shape: {tokens.shape}")
            mask = inputs["attention_mask"].astype(np.float32)[..., None]
            pooled = (tokens * mask).sum(axis=1) / np.maximum(mask.sum(axis=1), 1e-9)
            norm = np.linalg.norm(pooled, axis=1, keepdims=True)
            if not np.isfinite(pooled).all() or np.any(norm < 1e-9):
                raise ValueError("invalid embedding output")
            normalized = pooled / norm
            if not np.allclose(np.linalg.norm(normalized, axis=1), 1, atol=1e-5):
                raise ValueError("normalization verification failed")
            result.extend(normalized.tolist())
            if start % (args.batch_size * 20) == 0:
                print(f"embedded {min(start + args.batch_size, len(texts))}/{len(texts)}", flush=True)
        return result

    started = time.monotonic()
    texts = [(row["title"] + " " + row["text"]).strip() for row in corpus]
    vectors = embed(texts)
    query_texts = [queries[key]["text"] for key in selected]
    query_vectors = embed(query_texts)
    fixture = {"name": "beir-scifact-minilm-onnx", "vector_dim": 384,
               "docs": [{"doc_id": int(row["_id"]), "target_uri": f"urn:beir:scifact:{row['_id']}", "text": text,
                         "vector": vector, "warc_date": None, "content_type": "text/plain", "http_status": 200,
                         "source_file": "BEIR/SciFact/corpus.jsonl"} for row, text, vector in zip(corpus, texts, vectors)],
               "queries": [{"name": f"scifact-test-{key}", "query": text, "vector": vector,
                            "expected_doc_ids": sorted(relevance[key])} for key, text, vector in zip(selected, query_texts, query_vectors)]}
    path = output / "fixture.json"
    path.write_text(json.dumps(fixture, ensure_ascii=False) + "\n")
    result = {"status": "passed", "fixture_sha256": sha256(path), "documents": len(corpus),
              "queries": len(selected), "embedding_seconds": time.monotonic() - started,
              "truncation": "256 word pieces", "pooling": "attention-masked mean, L2 normalization"}
    (output / "fixture-build.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
