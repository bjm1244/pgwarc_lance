import contextlib
import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "tools" / "make_public_warc_quality_fixture.py"
SPEC = importlib.util.spec_from_file_location("make_public_warc_quality_fixture", TOOL_PATH)
public_quality = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(public_quality)


def warc_response(uri: str, date: str, html: str) -> bytes:
    http = (
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        "\r\n"
        f"{html}"
    ).encode("utf-8")
    headers = (
        "WARC/1.1\r\n"
        "WARC-Type: response\r\n"
        f"WARC-Target-URI: {uri}\r\n"
        f"WARC-Date: {date}\r\n"
        "WARC-Record-ID: <urn:uuid:00000000-0000-0000-0000-000000000001>\r\n"
        "Content-Type: application/http; msgtype=response\r\n"
        f"Content-Length: {len(http)}\r\n"
        "\r\n"
    ).encode("ascii")
    return headers + http + b"\r\n\r\n"


class PublicWarcQualityFixtureTests(unittest.TestCase):
    def test_builds_labeled_fixture_from_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.warc"
            path.write_bytes(
                warc_response(
                    "https://example.test/domain",
                    "2026-07-05T00:00:00Z",
                    "<html><body><h1>Example Domain</h1><p>For documentation.</p></body></html>",
                )
                + warc_response(
                    "https://example.test/other",
                    "2026-07-05T00:00:01Z",
                    "<html><body><h1>Unrelated Page</h1><p>Noise text.</p></body></html>",
                )
            )
            records = public_quality.load_records(path, min_text_chars=1)

        fixture = public_quality.fixture_from_records(
            records,
            name="local-public-smoke",
            vector_dim=4,
            query_name="example_domain",
            query="Example Domain",
            label_text="Example Domain",
        )

        self.assertEqual(fixture["name"], "local-public-smoke")
        self.assertEqual(fixture["vector_dim"], 4)
        self.assertEqual(len(fixture["docs"]), 2)
        self.assertEqual(len(fixture["queries"]), 1)
        expected_doc_id = fixture["queries"][0]["expected_doc_ids"][0]
        expected_doc = next(doc for doc in fixture["docs"] if doc["doc_id"] == expected_doc_id)
        self.assertEqual(expected_doc["target_uri"], "https://example.test/domain")

    def test_builds_multiple_labeled_queries(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.warc"
            path.write_bytes(
                warc_response(
                    "https://example.test/domain",
                    "2026-07-05T00:00:00Z",
                    "<html><body><h1>Example Domain</h1></body></html>",
                )
                + warc_response(
                    "https://example.test/other",
                    "2026-07-05T00:00:01Z",
                    "<html><body><h1>Other Page</h1></body></html>",
                )
            )
            records = public_quality.load_records(path, min_text_chars=1)

        fixture = public_quality.fixture_from_records(
            records,
            name="multi-query",
            vector_dim=4,
            query_name=None,
            query=None,
            label_text=None,
            query_specs=[
                {
                    "name": "example_domain",
                    "query": "Example Domain",
                    "label_text": "Example Domain",
                },
                {"name": "other_page", "query": "Other Page", "label_text": "Other Page"},
            ],
        )

        self.assertEqual([query["name"] for query in fixture["queries"]], ["example_domain", "other_page"])
        self.assertEqual(len(fixture["queries"]), 2)
        self.assertEqual(
            [
                next(doc for doc in fixture["docs"] if doc["doc_id"] == query["expected_doc_ids"][0])["target_uri"]
                for query in fixture["queries"]
            ],
            ["https://example.test/domain", "https://example.test/other"],
        )

    def test_query_spec_json_rejects_duplicate_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "query-specs.json"
            path.write_text(
                json.dumps(
                    [
                        {"name": "same", "query": "one", "label_text": "one"},
                        {"name": "same", "query": "two", "label_text": "two"},
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "duplicate query name"):
                public_quality.load_query_specs(path)

    def test_source_spec_rejects_path_and_url_together(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source-specs.json"
            path.write_text(
                json.dumps([{"path": "first.warc", "url": "https://example.test/first.warc"}]),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "expected exactly one of path or url"):
                public_quality.load_source_specs(path)

    def test_cli_writes_fixture_and_intermediate_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            warc_path = tmp_path / "sample.warc"
            output = tmp_path / "fixture.json"
            records_jsonl = tmp_path / "records.jsonl"
            embedding_jsonl = tmp_path / "vectors.jsonl"
            queries_json = tmp_path / "queries.json"
            warc_path.write_bytes(
                warc_response(
                    "https://example.test/domain",
                    "2026-07-05T00:00:00Z",
                    "<html><body><h1>Example Domain</h1></body></html>",
                )
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = public_quality.main(
                    [
                        "--warc-path",
                        str(warc_path),
                        "--output",
                        str(output),
                        "--records-jsonl",
                        str(records_jsonl),
                        "--embedding-jsonl",
                        str(embedding_jsonl),
                        "--queries-json",
                        str(queries_json),
                    ]
                )

            fixture = json.loads(output.read_text(encoding="utf-8"))
            query_labels = json.loads(queries_json.read_text(encoding="utf-8"))
            records_lines = records_jsonl.read_text(encoding="utf-8").splitlines()
            embedding_lines = embedding_jsonl.read_text(encoding="utf-8").splitlines()

        self.assertEqual(result, 0)
        self.assertIn("docs=1", stdout.getvalue())
        self.assertEqual(fixture["name"], "pywb-public-warc-smoke")
        self.assertEqual(len(records_lines), 1)
        self.assertEqual(len(embedding_lines), 1)
        self.assertEqual(query_labels["queries"][0]["query"], "Example Domain")

    def test_cli_accepts_query_spec_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            warc_path = tmp_path / "sample.warc"
            spec_path = tmp_path / "query-specs.json"
            output = tmp_path / "fixture.json"
            warc_path.write_bytes(
                warc_response(
                    "https://example.test/domain",
                    "2026-07-05T00:00:00Z",
                    "<html><body><h1>Example Domain</h1></body></html>",
                )
                + warc_response(
                    "https://example.test/other",
                    "2026-07-05T00:00:01Z",
                    "<html><body><h1>Other Page</h1></body></html>",
                )
            )
            spec_path.write_text(
                json.dumps(
                    [
                        {
                            "name": "example_domain",
                            "query": "Example Domain",
                            "label_text": "Example Domain",
                        },
                        {"name": "other_page", "query": "Other Page", "label_text": "Other Page"},
                    ]
                ),
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = public_quality.main(
                    [
                        "--warc-path",
                        str(warc_path),
                        "--query-spec-json",
                        str(spec_path),
                        "--output",
                        str(output),
                    ]
                )
            fixture = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(result, 0)
        self.assertIn("queries=2", stdout.getvalue())
        self.assertEqual([query["name"] for query in fixture["queries"]], ["example_domain", "other_page"])

    def test_cli_writes_provenance_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            warc_path = tmp_path / "sample.warc"
            spec_path = tmp_path / "query-specs.json"
            output = tmp_path / "fixture.json"
            manifest_path = tmp_path / "warc-provenance.json"
            warc_path.write_bytes(
                warc_response(
                    "https://example.test/domain",
                    "2026-07-05T00:00:00Z",
                    "<html><body><h1>Example Domain</h1></body></html>",
                )
            )
            spec_path.write_text(
                json.dumps(
                    [
                        {
                            "name": "example_domain",
                            "query": "Example Domain",
                            "label_text": "Example Domain",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            expected_bytes = warc_path.stat().st_size
            expected_sha256 = hashlib.sha256(warc_path.read_bytes()).hexdigest()

            result = public_quality.main(
                [
                    "--warc-path",
                    str(warc_path),
                    "--query-spec-json",
                    str(spec_path),
                    "--output",
                    str(output),
                    "--manifest-json",
                    str(manifest_path),
                ]
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(result, 0)
        self.assertEqual(manifest["schema_version"], 1)
        self.assertTrue(manifest["generated_at"].endswith("Z"))
        self.assertEqual(manifest["source"]["kind"], "local")
        self.assertEqual(manifest["source"]["path"], str(warc_path))
        self.assertEqual(manifest["source"]["bytes"], expected_bytes)
        self.assertEqual(manifest["source"]["sha256"], expected_sha256)
        self.assertIsNone(manifest["source"]["downloaded_at"])
        self.assertEqual(manifest["query_spec"]["kind"], "file")
        self.assertEqual(manifest["query_spec"]["path"], str(spec_path))
        self.assertEqual(manifest["query_spec"]["count"], 1)
        self.assertEqual(manifest["fixture"]["doc_count"], 1)
        self.assertEqual(manifest["fixture"]["query_count"], 1)

    def test_cli_writes_multi_source_corpus_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            first_path = tmp_path / "first.warc"
            second_path = tmp_path / "second.warc"
            source_spec_path = tmp_path / "sources.json"
            query_spec_path = tmp_path / "query-specs.json"
            output = tmp_path / "fixture.json"
            manifest_path = tmp_path / "corpus-manifest.json"
            first_path.write_bytes(
                warc_response(
                    "https://example.test/domain",
                    "2026-07-05T00:00:00Z",
                    "<html><body><h1>Example Domain</h1></body></html>",
                )
                + warc_response(
                    "https://example.test/empty",
                    "2026-07-05T00:00:01Z",
                    "<html><body></body></html>",
                )
            )
            second_path.write_bytes(
                warc_response(
                    "https://example.test/other",
                    "2026-07-05T00:00:01Z",
                    "<html><body><h1>Other Page</h1></body></html>",
                )
            )
            source_spec_path.write_text(
                json.dumps(
                    [
                        {"path": str(first_path)},
                        {"url": second_path.as_uri()},
                    ]
                ),
                encoding="utf-8",
            )
            query_spec_path.write_text(
                json.dumps(
                    [
                        {
                            "name": "example_domain",
                            "query": "Example Domain",
                            "label_text": "Example Domain",
                        },
                        {"name": "other_page", "query": "Other Page", "label_text": "Other Page"},
                    ]
                ),
                encoding="utf-8",
            )

            result = public_quality.main(
                [
                    "--source-spec-json",
                    str(source_spec_path),
                    "--query-spec-json",
                    str(query_spec_path),
                    "--output",
                    str(output),
                    "--manifest-json",
                    str(manifest_path),
                ]
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

            first_sha256 = hashlib.sha256(first_path.read_bytes()).hexdigest()
            second_sha256 = hashlib.sha256(second_path.read_bytes()).hexdigest()
            source_spec_sha256 = hashlib.sha256(source_spec_path.read_bytes()).hexdigest()

        self.assertEqual(result, 0)
        self.assertEqual(manifest["schema_version"], 2)
        self.assertTrue(manifest["generated_at"].endswith("Z"))
        self.assertEqual(manifest["source_spec"]["count"], 2)
        self.assertEqual(manifest["source_spec"]["path"], str(source_spec_path))
        self.assertEqual(manifest["source_spec"]["sha256"], source_spec_sha256)
        self.assertEqual(len(manifest["sources"]), 2)
        self.assertEqual(manifest["sources"][0]["kind"], "local")
        self.assertEqual(manifest["sources"][0]["path"], str(first_path))
        self.assertEqual(manifest["sources"][0]["sha256"], first_sha256)
        self.assertIsNone(manifest["sources"][0]["downloaded_at"])
        self.assertEqual(
            manifest["sources"][0]["records"],
            {
                "warc_record_count": 2,
                "imported_record_count": 1,
                "skipped_record_count": 1,
            },
        )
        self.assertEqual(
            manifest["sources"][0]["coverage"],
            {"doc_count": 1, "labeled_doc_count": 1, "unlabeled_doc_count": 0},
        )
        self.assertEqual(manifest["sources"][1]["kind"], "download")
        self.assertEqual(manifest["sources"][1]["url"], second_path.as_uri())
        self.assertEqual(manifest["sources"][1]["sha256"], second_sha256)
        self.assertTrue(manifest["sources"][1]["downloaded_at"].endswith("Z"))
        self.assertEqual(manifest["sources"][1]["records"]["warc_record_count"], 1)
        self.assertEqual(manifest["sources"][1]["records"]["imported_record_count"], 1)
        self.assertEqual(manifest["sources"][1]["coverage"]["labeled_doc_count"], 1)
        self.assertEqual(manifest["query_spec"]["count"], 2)
        self.assertEqual(manifest["fixture"]["doc_count"], 2)
        self.assertEqual(manifest["fixture"]["query_count"], 2)

    def test_cli_rejects_duplicate_doc_ids_across_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            first_path = tmp_path / "first.warc"
            second_path = tmp_path / "second.warc"
            source_spec_path = tmp_path / "sources.json"
            output = tmp_path / "fixture.json"
            payload = warc_response(
                "https://example.test/domain",
                "2026-07-05T00:00:00Z",
                "<html><body><h1>Example Domain</h1></body></html>",
            )
            first_path.write_bytes(payload)
            second_path.write_bytes(payload)
            source_spec_path.write_text(
                json.dumps([{"path": str(first_path)}, {"path": str(second_path)}]),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "duplicate doc_id"):
                public_quality.main(
                    [
                        "--source-spec-json",
                        str(source_spec_path),
                        "--output",
                        str(output),
                    ]
                )

    def test_rejects_missing_label_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.warc"
            path.write_bytes(
                warc_response(
                    "https://example.test/other",
                    "2026-07-05T00:00:00Z",
                    "<html><body><h1>Other Page</h1></body></html>",
                )
            )
            records = public_quality.load_records(path, min_text_chars=1)

        with self.assertRaisesRegex(ValueError, "no records matched"):
            public_quality.fixture_from_records(
                records,
                name="local-public-smoke",
                vector_dim=4,
                query_name="missing",
                query="missing",
                label_text="Example Domain",
            )


if __name__ == "__main__":
    unittest.main()
