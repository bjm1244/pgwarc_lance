import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
TOOL_PATH = TOOLS / "doctor_real_corpus_input.py"
SPEC = importlib.util.spec_from_file_location("doctor_real_corpus_input", TOOL_PATH)
doctor_real_corpus_input = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(doctor_real_corpus_input)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def valid_labels(query_count: int = 2) -> dict[str, object]:
    return {
        "name": "real-labels",
        "queries": [
            {
                "name": f"query-{index}",
                "query": f"search {index}",
                "expected_doc_ids": [index],
            }
            for index in range(1, query_count + 1)
        ],
    }


def valid_manifest(tmp_path: Path) -> dict[str, object]:
    corpus = tmp_path / "corpus.warc.gz"
    corpus.write_bytes(b"fake warc bytes for checksum test")
    labels = tmp_path / "queries.json"
    write_json(labels, valid_labels(2))
    return {
        "schema_version": 1,
        "corpus": {
            "name": "controlled-real-corpus",
            "origin": "external",
            "sources": [
                {
                    "name": "part-000",
                    "kind": "path",
                    "path": "corpus.warc.gz",
                    "url": "",
                    "bytes": corpus.stat().st_size,
                    "sha256": sha256(corpus),
                    "license": "CC-BY-4.0",
                    "license_url": "https://creativecommons.org/licenses/by/4.0/",
                    "redistribution": "permitted",
                }
            ],
        },
        "labels": {
            "query_set_version": "v1",
            "qrels_version": "v1",
            "independence": "external",
            "method": "manual relevance judging by domain expert",
            "labels_path": "queries.json",
            "labels_sha256": sha256(labels),
            "query_count": 2,
        },
        "evaluation": {
            "commit": "abc1234",
            "postgres_version": "16.4",
            "search_settings": {"function": "hybrid_warc_search", "k": 10},
        },
    }


class RealCorpusInputDoctorTests(unittest.TestCase):
    def build(self, tmp_path: Path, payload: dict[str, object], **kwargs):
        manifest = tmp_path / "manifest.json"
        write_json(manifest, payload)
        return doctor_real_corpus_input.build_report(
            manifest_json=manifest,
            min_queries=kwargs.get("min_queries", 1),
            max_queries=kwargs.get("max_queries", 5),
        )

    def test_valid_manifest_verifies_source_and_label_checksums(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            report = self.build(tmp_path, valid_manifest(tmp_path))

        self.assertTrue(report["valid"])
        self.assertEqual(report["source_count"], 1)
        self.assertEqual(report["query_count"], 2)
        self.assertEqual(report["label_independence"], "external")
        self.assertTrue(report["sources"][0]["checksum_verified"])
        self.assertTrue(report["labels_checksum_verified"])
        self.assertEqual(report["errors"], [])

    def test_synthetic_origin_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            payload = valid_manifest(tmp_path)
            payload["corpus"]["origin"] = "synthetic"  # type: ignore[index]
            report = self.build(tmp_path, payload)

        self.assertFalse(report["valid"])
        self.assertTrue(any("corpus.origin" in error for error in report["errors"]))

    def test_self_labeled_relevance_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            payload = valid_manifest(tmp_path)
            payload["labels"]["independence"] = "self"  # type: ignore[index]
            report = self.build(tmp_path, payload)

        self.assertFalse(report["valid"])
        self.assertTrue(any("independence" in error for error in report["errors"]))

    def test_corpus_checksum_drift_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            payload = valid_manifest(tmp_path)
            (tmp_path / "corpus.warc.gz").write_bytes(b"tampered")
            report = self.build(tmp_path, payload)

        self.assertFalse(report["valid"])
        self.assertTrue(any("bytes drift" in error for error in report["errors"]))
        self.assertTrue(any("sha256 drift" in error for error in report["errors"]))

    def test_label_checksum_drift_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            payload = valid_manifest(tmp_path)
            write_json(tmp_path / "queries.json", valid_labels(3))
            report = self.build(tmp_path, payload)

        self.assertFalse(report["valid"])
        self.assertTrue(any("labels_path sha256 drift" in error for error in report["errors"]))

    def test_query_count_outside_range_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            report = self.build(tmp_path, valid_manifest(tmp_path), min_queries=20, max_queries=50)

        self.assertFalse(report["valid"])
        self.assertTrue(any("outside the real baseline range" in error for error in report["errors"]))

    def test_labels_query_count_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            payload = valid_manifest(tmp_path)
            payload["labels"]["query_count"] = 5  # type: ignore[index]
            report = self.build(tmp_path, payload)

        self.assertFalse(report["valid"])
        self.assertTrue(any("does not match" in error for error in report["errors"]))

    def test_missing_source_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            payload = valid_manifest(tmp_path)
            (tmp_path / "corpus.warc.gz").unlink()
            report = self.build(tmp_path, payload)

        self.assertFalse(report["valid"])
        self.assertTrue(any("does not exist" in error for error in report["errors"]))

    def test_missing_license_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            payload = valid_manifest(tmp_path)
            del payload["corpus"]["sources"][0]["license"]  # type: ignore[index]
            report = self.build(tmp_path, payload)

        self.assertFalse(report["valid"])
        self.assertTrue(any(".license must be" in error for error in report["errors"]))

    def test_unknown_top_level_key_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            payload = valid_manifest(tmp_path)
            payload["notes"] = "extra"
            report = self.build(tmp_path, payload)

        self.assertFalse(report["valid"])
        self.assertTrue(any("unknown keys" in error for error in report["errors"]))

    def test_url_only_source_warns_without_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            payload = valid_manifest(tmp_path)
            source = payload["corpus"]["sources"][0]  # type: ignore[index]
            source["kind"] = "url"
            source["path"] = ""
            source["url"] = "https://example.test/corpus.warc.gz"
            report = self.build(tmp_path, payload)

        self.assertTrue(report["valid"])
        self.assertTrue(any("URL-only" in warning for warning in report["warnings"]))
        self.assertFalse(report["sources"][0]["checksum_verified"])

    def test_cli_exit_code_and_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            manifest = tmp_path / "manifest.json"
            write_json(manifest, valid_manifest(tmp_path))
            json_out = tmp_path / "doctor.json"
            md_out = tmp_path / "doctor.md"
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = doctor_real_corpus_input.main(
                    [
                        "--manifest-json",
                        str(manifest),
                        "--min-queries",
                        "1",
                        "--max-queries",
                        "5",
                        "--json-output",
                        str(json_out),
                        "--markdown-output",
                        str(md_out),
                    ]
                )
            report = json.loads(json_out.read_text(encoding="utf-8"))
            markdown_written = md_out.is_file()

        self.assertEqual(code, 0)
        self.assertTrue(report["valid"])
        self.assertEqual(stderr.getvalue(), "")
        self.assertTrue(markdown_written)

    def test_cli_rejects_invalid_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            payload = valid_manifest(tmp_path)
            payload["corpus"]["origin"] = "public-sample"  # type: ignore[index]
            manifest = tmp_path / "manifest.json"
            write_json(manifest, payload)
            stderr = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                code = doctor_real_corpus_input.main(["--manifest-json", str(manifest)])

        self.assertEqual(code, 1)
        self.assertIn("real corpus input doctor failed", stderr.getvalue())

    def test_ordered_query_range_is_required(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = doctor_real_corpus_input.main(
                ["--manifest-json", "missing.json", "--min-queries", "50", "--max-queries", "20"]
            )

        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
