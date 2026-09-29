import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
SPEC = importlib.util.spec_from_file_location(
    "preflight_quality_inputs", TOOLS / "preflight_quality_inputs.py"
)
preflight_quality_inputs = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(preflight_quality_inputs)


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_inputs(root: Path, *, mismatch: bool = False) -> dict[str, Path]:
    corpus = root / "source.warc.gz"
    corpus.write_bytes(b"read-only warc bytes")
    records = root / "records.jsonl"
    records.write_text(
        json.dumps({"doc_id": 2 if mismatch else 1, "text": "record"}) + "\n",
        encoding="utf-8",
    )
    queries = root / "queries.json"
    write_json(queries, {
        "name": "external-labels",
        "queries": [{
            "name": "q1", "query": "quality", "vector": [1.0, 0.0],
            "expected_doc_ids": [1],
        }],
    })
    qrels = root / "qrels.json"
    write_json(qrels, {"queries": [{"name": "q1", "expected_doc_ids": [1]}]})
    provenance = root / "provenance.json"
    write_json(provenance, {
        "schema_version": 2,
        "sources": [{"path": str(corpus), "sha256": "external-reference"}],
        "labels": {"origin": "external-reference"},
    })
    return {
        "corpus": corpus, "records": records, "queries": queries,
        "qrels": qrels, "provenance": provenance,
    }


def make_real_manifest_inputs(root: Path) -> dict[str, Path]:
    corpus = root / "real-source.warc.gz"
    corpus.write_bytes(b"authorized external corpus bytes")
    queries = root / "real-queries.json"
    query_rows = [
        {
            "name": f"q{index}",
            "query": f"query {index}",
            "vector": [1.0, 0.0],
            "expected_doc_ids": [index],
        }
        for index in range(1, 21)
    ]
    write_json(queries, {"name": "external-real-labels", "queries": query_rows})
    qrels = root / "real-qrels.json"
    write_json(
        qrels,
        {"queries": [{"name": row["name"], "expected_doc_ids": row["expected_doc_ids"]} for row in query_rows]},
    )
    manifest = root / "real-corpus-input.json"
    write_json(
        manifest,
        {
            "schema_version": 1,
            "corpus": {
                "name": "external-test-corpus",
                "origin": "external",
                "sources": [{
                    "name": "real-source",
                    "kind": "path",
                    "path": corpus.name,
                    "bytes": corpus.stat().st_size,
                    "sha256": sha256_file(corpus),
                    "license": "test license",
                    "license_url": "https://example.test/license",
                    "redistribution": "permitted",
                }],
            },
            "labels": {
                "query_set_version": "queries-v1",
                "qrels_version": "qrels-v1",
                "independence": "external",
                "method": "independent assessor labels",
                "labels_path": queries.name,
                "labels_sha256": sha256_file(queries),
                "query_count": len(query_rows),
            },
            "evaluation": {
                "commit": "e1fa2c5",
                "postgres_version": "16",
                "search_settings": {"k": 10},
            },
        },
    )
    return {"corpus": corpus, "queries": queries, "qrels": qrels, "manifest": manifest}


class QualityInputPreflightTests(unittest.TestCase):
    def test_missing_inputs_are_blocked_and_do_not_get_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = preflight_quality_inputs.build_report(
                corpus_paths=[root / "missing.warc.gz"],
                queries_json=root / "missing-queries.json",
                qrels_json=root / "missing-qrels.json",
            )
        self.assertFalse(report["valid"])
        self.assertEqual(report["status"], "blocked")
        self.assertFalse(report["quality_measured"])
        self.assertTrue(any("provenance_supplied" in error for error in report["errors"]))
        self.assertTrue(any("does not exist" in error for error in report["errors"]))
        self.assertIsNone(report["inputs"]["corpus"][0]["sha256"])
        self.assertFalse(report["real_evidence_established"])

    def test_structural_fixture_preflight_is_ready_but_not_quality_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_inputs(Path(tmp))
            before = paths["corpus"].read_bytes()
            report = preflight_quality_inputs.build_report(
                corpus_paths=[paths["corpus"]], queries_json=paths["queries"],
                qrels_json=paths["qrels"], records_jsonl=paths["records"],
                provenance_json=paths["provenance"],
            )
            after = paths["corpus"].read_bytes()
        self.assertTrue(report["valid"])
        self.assertEqual(report["status"], "ready")
        self.assertFalse(report["quality_measured"])
        self.assertFalse(report["real_evidence_established"])
        self.assertIn("qrels_independence_and_authenticity", report["unresolved_checks"])
        self.assertIn("real_corpus_manifest_check", report["unresolved_checks"])
        self.assertIsNone(report["real_corpus_manifest_doctor"])
        self.assertEqual(before, after)

    def test_valid_real_manifest_is_reused_without_claiming_quality(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_real_manifest_inputs(Path(tmp))
            before = {path.name: path.read_bytes() for path in paths.values()}
            report = preflight_quality_inputs.build_report(
                corpus_paths=[paths["corpus"]], queries_json=paths["queries"],
                qrels_json=paths["qrels"], real_corpus_manifest_json=paths["manifest"],
            )
            after = {path.name: path.read_bytes() for path in paths.values()}
        self.assertTrue(report["valid"])
        self.assertEqual(report["status"], "ready")
        self.assertTrue(report["real_corpus_manifest_doctor"]["valid"])
        self.assertIn("real_corpus_manifest_check", report["completed_checks"])
        self.assertFalse(report["quality_measured"])
        self.assertFalse(report["real_evidence_established"])
        self.assertIn("qrels_independence_and_authenticity", report["unresolved_checks"])
        self.assertEqual(before, after)

    def test_invalid_real_manifest_errors_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_real_manifest_inputs(Path(tmp))
            write_json(paths["manifest"], {"schema_version": 99})
            report = preflight_quality_inputs.build_report(
                corpus_paths=[paths["corpus"]], queries_json=paths["queries"],
                qrels_json=paths["qrels"], real_corpus_manifest_json=paths["manifest"],
            )
        self.assertFalse(report["valid"])
        self.assertTrue(any("real_corpus_manifest_check" in error for error in report["errors"]))
        self.assertTrue(any("manifest.schema_version must be 1" in error for error in report["errors"]))
        self.assertTrue(
            any(
                "manifest.schema_version must be 1" in error
                for error in report["real_corpus_manifest_doctor"]["errors"]
            )
        )

    def test_record_reference_mismatch_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_inputs(Path(tmp), mismatch=True)
            report = preflight_quality_inputs.build_report(
                corpus_paths=[paths["corpus"]], queries_json=paths["queries"],
                qrels_json=paths["qrels"], records_jsonl=paths["records"],
                provenance_json=paths["provenance"],
            )
        self.assertFalse(report["valid"])
        self.assertTrue(any("unknown doc_id values" in error for error in report["errors"]))
        self.assertTrue(any("query_record_reference_check" in error for error in report["errors"]))

    def test_query_qrels_name_mismatch_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_inputs(Path(tmp))
            write_json(paths["qrels"], {"queries": [{"name": "other", "expected_doc_ids": [1]}]})
            report = preflight_quality_inputs.build_report(
                corpus_paths=[paths["corpus"]], queries_json=paths["queries"],
                qrels_json=paths["qrels"], records_jsonl=paths["records"],
                provenance_json=paths["provenance"],
            )
        self.assertFalse(report["valid"])
        self.assertTrue(any("query_qrels_reference_check" in error for error in report["errors"]))
        self.assertTrue(any("mismatch" in error for error in report["errors"]))


    def test_empty_qrels_are_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_inputs(Path(tmp))
            write_json(paths["qrels"], {"queries": []})
            report = preflight_quality_inputs.build_report(
                corpus_paths=[paths["corpus"]], queries_json=paths["queries"],
                qrels_json=paths["qrels"], records_jsonl=paths["records"],
                provenance_json=paths["provenance"],
            )
        self.assertFalse(report["valid"])
        self.assertTrue(any("qrels list must not be empty" in error for error in report["errors"]))
    def test_cli_writes_blocked_json_and_markdown_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            json_output, markdown_output = root / "report.json", root / "report.md"
            code = preflight_quality_inputs.main([
                "--corpus-path", str(root / "missing.warc.gz"),
                "--queries-json", str(root / "missing-queries.json"),
                "--qrels-json", str(root / "missing-qrels.json"),
                "--json-output", str(json_output),
                "--markdown-output", str(markdown_output),
            ])
            payload = json.loads(json_output.read_text(encoding="utf-8"))
            markdown = markdown_output.read_text(encoding="utf-8")
        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "blocked")
        self.assertIn("## Resume requirements", markdown)
        self.assertNotIn("Traceback", markdown)



class QualityFinalizationPreflightMakeTests(unittest.TestCase):
    def run_make(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["make", *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_real_baseline_gate_blocks_before_quality_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "quality-run"
            missing_corpus = Path(tmp) / "missing.warc.gz"
            missing_queries = Path(tmp) / "missing-queries.json"
            missing_qrels = Path(tmp) / "missing-qrels.json"
            completed = self.run_make(
                "QUALITY_BASELINE_REQUIRE_REAL_CORPUS=1",
                f"QUALITY_INPUT_PREFLIGHT_CORPUS={missing_corpus}",
                f"QUALITY_INPUT_PREFLIGHT_QUERIES_JSON={missing_queries}",
                f"QUALITY_INPUT_PREFLIGHT_QRELS_JSON={missing_qrels}",
                f"BENCH_OUT_DIR={run_dir}",
                "finalize-quality-run",
            )
            preflight_json = run_dir / "quality_input_preflight.json"
            self.assertTrue(preflight_json.is_file())
            payload = json.loads(preflight_json.read_text(encoding="utf-8"))

        self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(payload["status"], "blocked")
        self.assertFalse(payload["valid"])
        for name in (
            "quality_fixture_report.json",
            "quality.json",
            "quality_baseline_doctor.json",
            "run_metadata.json",
            "run_manifest.json",
            "run_doctor.json",
        ):
            self.assertFalse((run_dir / name).exists(), name)

    def test_opt_in_gate_precedes_finalization_and_forwards_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = self.run_make(
                "-n",
                "QUALITY_INPUT_PREFLIGHT_REQUIRED=1",
                f"QUALITY_INPUT_PREFLIGHT_CORPUS={root / 'corpus.warc.gz'}",
                f"QUALITY_INPUT_PREFLIGHT_QUERIES_JSON={root / 'queries.json'}",
                f"QUALITY_INPUT_PREFLIGHT_QRELS_JSON={root / 'qrels.json'}",
                f"QUALITY_INPUT_PREFLIGHT_MANIFEST_JSON={root / 'manifest.json'}",
                f"BENCH_OUT_DIR={root / 'quality-run'}",
                "QUALITY_FIXTURE=quality.fixture.json",
                "finalize-quality-run",
            )

        self.assertEqual(output.returncode, 0, output.stdout + output.stderr)
        self.assertIn("make preflight-quality-inputs", output.stdout)
        self.assertIn(f'--manifest-json "{root / "manifest.json"}"', output.stdout)
        self.assertLess(
            output.stdout.index("make preflight-quality-inputs"),
            output.stdout.index("make stage-quality-provenance"),
        )

if __name__ == "__main__":
    unittest.main()
