#!/usr/bin/env python3
"""Download and verify public SciFact/MiniLM assets for the optional quality test.

Nothing is uploaded. Keep dataset/model attribution with derived artifacts.
Sources: https://github.com/beir-cellar/beir/wiki/Datasets-available
https://huggingface.co/datasets/BeIR/scifact
https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2
"""
import argparse
import hashlib
import json
import urllib.request
import zipfile
from pathlib import Path

MODEL = "sentence-transformers/all-MiniLM-L6-v2"
REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
MODEL_FILES = {
    "onnx/model.onnx": "6fd5d72fe4589f189f8ebc006442dbb529bb7ce38f8082112682524616046452",
    "tokenizer.json": "be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037",
    "sentence_bert_config.json": "fc1993fde0a95c24ec6c022539d41cf6e2f7c9721e5415d6fb6897472a9cd4b7",
    "1_Pooling/config.json": "4be450dde3b0273bb9787637cfbd28fe04a7ba6ab9d36ac48e92b11e350ffc23",
    "README.md": "dcd602d2fd35c203a247304a06fec6654a12f7941b739f9221a064fe8dc3b7f0",
}
DATASET_URL = "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip"
DATASET_SHA256 = "536e14446a0ba56ed1398ab1055f39fe852686ecad24a6306c80c490fa8e0165"


def fetch(url: str, path: Path, expected: str, max_bytes: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    temporary = path.with_name(path.name + ".download")
    try:
        with urllib.request.urlopen(url, timeout=90) as source, temporary.open("wb") as output:
            total = 0
            while block := source.read(65536):
                total += len(block)
                if total > max_bytes:
                    raise ValueError("download size limit exceeded")
                digest.update(block)
                output.write(block)
        if digest.hexdigest() != expected:
            raise ValueError(f"checksum mismatch: {path.name}")
        temporary.replace(path)
        print(f"verified {path.name}: {total} bytes", flush=True)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.output_dir
    root.mkdir(parents=True, exist_ok=True)
    for name, expected in MODEL_FILES.items():
        limit = 128 * 1024 * 1024 if name.endswith(".onnx") else 1024 * 1024
        fetch(f"https://huggingface.co/{MODEL}/resolve/{REVISION}/{name}", root / "model" / name, expected, limit)
    fetch(DATASET_URL, root / "scifact.zip", DATASET_SHA256, 64 * 1024 * 1024)
    with zipfile.ZipFile(root / "scifact.zip") as archive:
        for name in ["scifact/corpus.jsonl", "scifact/queries.jsonl", "scifact/qrels/test.tsv"]:
            # Extract only known entries; archive paths cannot escape this root.
            data = archive.read(name)
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    assets = {"model": MODEL, "revision": REVISION, "model_sha256": MODEL_FILES,
              "dataset_url": DATASET_URL, "dataset_sha256": DATASET_SHA256,
              "dataset_license": "CC-BY-SA-4.0", "dataset_card": "https://huggingface.co/datasets/BeIR/scifact",
              "model_license": "Apache-2.0"}
    (root / "assets.json").write_text(json.dumps(assets, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
