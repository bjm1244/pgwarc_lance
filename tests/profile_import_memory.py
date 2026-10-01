#!/usr/bin/env python3
"""Measure Linux importer peak RSS on increasingly large synthetic WARC corpora.

These are resource checks, not representative retrieval-quality measurements.
Input/output files stay in --output-dir for inspection. GNU time is required.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--rows", default="1000,10000")
    parser.add_argument("--text-bytes", type=int, default=8192)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--max-rss-mib", type=int, default=256)
    parser.add_argument("--max-growth-mib", type=int, default=32)
    args = parser.parse_args()
    rows = [int(value) for value in args.rows.split(",")]
    if not rows or any(value <= 0 for value in rows) or min(args.text_bytes, args.batch_size, args.max_rss_mib, args.max_growth_mib) <= 0:
        parser.error("rows and resource limits must be positive")
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    importer = Path(__file__).resolve().parents[1] / "tools/warc_importer.py"
    results = []
    for count in rows:
        warc = output / f"synthetic-{count}.warc"
        text = ("benchmark shared document " * (args.text_bytes // 26 + 1))[:args.text_bytes]
        payload = ("HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n" + text).encode()
        with warc.open("wb") as stream:
            for index in range(count):
                header = f"WARC/1.1\r\nWARC-Type: response\r\nWARC-Target-URI: https://example.test/memory/{index}\r\nWARC-Date: 2026-10-01T00:00:00Z\r\nContent-Type: application/http; msgtype=response\r\nContent-Length: {len(payload)}\r\n\r\n"
                stream.write(header.encode() + payload + b"\r\n\r\n")
        stats, sql = output / f"time-{count}.json", output / f"import-{count}.sql"
        summary = output / f"summary-{count}.json"
        started = time.monotonic()
        subprocess.run([
            "/usr/bin/time", "-f", '{"peak_rss_kib":%M}', "-o", str(stats),
            sys.executable, str(importer), str(warc), "--output", str(sql),
            "--summary-json", str(summary), "--no-hash-vectors",
            "--batch-size", str(args.batch_size), "--temp-dir", str(output),
        ], check=True, timeout=600)
        result = json.loads(stats.read_text())
        result.update({"rows": count, "warc_bytes": warc.stat().st_size,
                       "sql_bytes": sql.stat().st_size, "elapsed_seconds": time.monotonic() - started})
        imported = json.loads(summary.read_text())
        if imported["records"] != count or imported["sql_bytes"] != sql.stat().st_size:
            raise AssertionError("import count/output-size verification failed")
        results.append(result)
    peaks = [result["peak_rss_kib"] for result in results]
    growth = max(peaks) - min(peaks)
    passed = max(peaks) <= args.max_rss_mib * 1024 and growth <= args.max_growth_mib * 1024
    evidence = {"status": "passed" if passed else "failed", "synthetic": True,
                "text_bytes": args.text_bytes, "batch_size": args.batch_size,
                "max_rss_mib": args.max_rss_mib, "max_growth_mib": args.max_growth_mib,
                "rss_growth_kib": growth, "results": results}
    (output / "memory-profile.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps(evidence, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
