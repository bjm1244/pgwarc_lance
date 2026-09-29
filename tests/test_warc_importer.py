import importlib.util
import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IMPORTER_PATH = ROOT / "tools" / "warc_importer.py"
SPEC = importlib.util.spec_from_file_location("warc_importer", IMPORTER_PATH)
warc_importer = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = warc_importer
SPEC.loader.exec_module(warc_importer)


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


class WarcImporterTests(unittest.TestCase):
    def test_dechunks_valid_chunks_and_trailers(self):
        body = (
            b"4\r\nWiki\r\n"
            b"5;source=test\r\npedia\r\n"
            b"0\r\nX-Archive: sample\r\n\r\n"
        )

        self.assertEqual(warc_importer.dechunk(body), b"Wikipedia")

    def test_dechunk_rejects_truncated_chunk_data(self):
        with self.assertRaisesRegex(ValueError, "truncated HTTP chunk data"):
            warc_importer.dechunk(b"5\r\nabc")

    def test_dechunk_rejects_missing_terminal_chunk(self):
        with self.assertRaisesRegex(ValueError, "missing terminating zero-size chunk"):
            warc_importer.dechunk(b"4\r\nWiki\r\n")

    def test_dechunk_rejects_invalid_chunk_size(self):
        with self.assertRaisesRegex(ValueError, "invalid HTTP chunk size"):
            warc_importer.dechunk(b"not-hex\r\n")

    def test_decode_body_decompresses_http_gzip_payload(self):
        body = gzip.compress("Compressed HTTP text.".encode("utf-8"))

        self.assertEqual(
            warc_importer.decode_body(
                body,
                {"content-encoding": "GZip"},
                "text/plain; charset=utf-8",
            ),
            "Compressed HTTP text.",
        )

    def test_decode_body_rejects_invalid_http_gzip_payload(self):
        with self.assertRaisesRegex(ValueError, "invalid gzip HTTP payload"):
            warc_importer.decode_body(
                b"not-gzip",
                {"content-encoding": "gzip"},
                "text/plain; charset=utf-8",
            )

    def test_decode_body_rejects_truncated_http_gzip_payload(self):
        body = gzip.compress(b"truncated")[:-1]

        with self.assertRaisesRegex(ValueError, "invalid gzip HTTP payload"):
            warc_importer.decode_body(
                body,
                {"content-encoding": "gzip"},
                "text/plain; charset=utf-8",
            )

    def test_decode_body_parses_charset_parameter_spacing(self):
        body = "café".encode("iso-8859-1")

        self.assertEqual(
            warc_importer.decode_body(
                body,
                {},
                "text/plain; charset = iso-8859-1",
            ),
            "café",
        )

    def test_import_limit_zero_returns_no_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.warc"
            path.write_bytes(
                warc_response(
                    "https://example.test/limit-zero",
                    "2026-06-30T00:00:00Z",
                    "<html><body>Should not be imported.</body></html>",
                )
            )

            records = warc_importer.load_import_records([path], limit=0, min_text_chars=1)

        self.assertEqual(records, [])

    def test_import_selection_limits_reject_negative_values(self):
        with self.assertRaisesRegex(ValueError, "limit must be non-negative"):
            warc_importer.load_import_records([], limit=-1, min_text_chars=1)
        with self.assertRaisesRegex(ValueError, "min_text_chars must be non-negative"):
            warc_importer.load_import_records([], limit=None, min_text_chars=-1)

    def test_cli_rejects_negative_selection_limits(self):
        with self.assertRaisesRegex(SystemExit, "--limit must be non-negative"):
            warc_importer.main(["unused.warc", "--limit", "-1"])
        with self.assertRaisesRegex(SystemExit, "--min-text-chars must be non-negative"):
            warc_importer.main(["unused.warc", "--min-text-chars", "-1"])

    def test_duplicate_import_doc_ids_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "duplicate.warc"
            response = warc_response(
                "https://example.test/duplicate",
                "2026-06-30T00:00:00Z",
                "<html><body>Duplicate record.</body></html>",
            )
            path.write_bytes(response + response)

            with self.assertRaisesRegex(ValueError, "duplicate imported doc_id"):
                warc_importer.load_import_records([path], limit=None, min_text_chars=1)

    def test_missing_warc_content_length_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing-length.warc"
            path.write_bytes(
                b"WARC/1.1\r\n"
                b"WARC-Type: response\r\n"
                b"WARC-Target-URI: https://example.test/missing\r\n"
                b"\r\n"
                b"payload"
            )

            with self.assertRaisesRegex(ValueError, "missing WARC Content-Length"):
                list(warc_importer.iter_warc_records(path))

    def test_duplicate_warc_content_length_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "duplicate-length.warc"
            path.write_bytes(
                b"WARC/1.1\r\n"
                b"WARC-Type: response\r\n"
                b"Content-Length: 0\r\n"
                b"Content-Length: 0\r\n"
                b"\r\n"
            )

            with self.assertRaisesRegex(ValueError, "duplicate WARC Content-Length"):
                list(warc_importer.iter_warc_records(path))

    def test_negative_warc_content_length_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "negative-length.warc"
            path.write_bytes(
                b"WARC/1.1\r\n"
                b"WARC-Type: response\r\n"
                b"Content-Length: -1\r\n"
                b"\r\n"
            )

            with self.assertRaisesRegex(ValueError, "must be non-negative"):
                list(warc_importer.iter_warc_records(path))

    def test_extracts_html_response_text_and_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.warc"
            path.write_bytes(
                warc_response(
                    "https://example.test/",
                    "2026-06-30T00:00:00Z",
                    "<html><head><title>x</title><script>skip()</script></head>"
                    "<body><h1>Hello WARC</h1><p>Search text.</p></body></html>",
                )
            )

            records = warc_importer.load_import_records([path], limit=None, min_text_chars=1)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].target_uri, "https://example.test/")
        self.assertEqual(records[0].http_status, 200)
        self.assertEqual(records[0].content_type, "text/html; charset=utf-8")
        self.assertIn("Hello WARC", records[0].text)
        self.assertIn("Search text.", records[0].text)
        self.assertNotIn("skip", records[0].text)

    def test_extracts_gzip_warc(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.warc.gz"
            payload = warc_response(
                "https://example.test/gzip",
                "2026-06-30T00:00:00Z",
                "<html><body><p>Compressed WARC text.</p></body></html>",
            )
            path.write_bytes(gzip.compress(payload))

            records = warc_importer.load_import_records([path], limit=None, min_text_chars=1)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].target_uri, "https://example.test/gzip")
        self.assertIn("Compressed WARC text.", records[0].text)

    def test_warc_fixture_generates_metadata_bm25_and_lance_sql(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.warc"
            path.write_bytes(
                warc_response(
                    "https://example.test/e2e",
                    "2026-06-30T00:00:00Z",
                    "<html><body><h1>End to end WARC</h1><p>Hybrid search text.</p></body></html>",
                )
            )

            [record] = warc_importer.load_import_records(
                [path], limit=None, min_text_chars=1
            )

        sql = warc_importer.emit_sql(
            [record],
            lance_uri="/tmp/warc_e2e.lance",
            vector_dim=4,
            overwrite_lance=False,
            include_hash_vectors=True,
            lance_mode="overwrite",
        )

        self.assertIn("INSERT INTO pgwarc_lance.warc_record", sql)
        self.assertIn("https://example.test/e2e", sql)
        self.assertIn("SELECT bm25_index_document(", sql)
        self.assertIn("End to end WARC", sql)
        self.assertIn("SELECT lance_create_table('/tmp/warc_e2e.lance', 4, true);", sql)
        self.assertIn("SELECT lance_insert_many('/tmp/warc_e2e.lance'", sql)

    def test_sql_emits_metadata_bm25_and_lance_calls(self):
        record = warc_importer.ImportRecord(
            doc_id=123,
            target_uri="https://example.test/",
            warc_date="2026-06-30T00:00:00Z",
            content_type="text/plain",
            http_status=200,
            payload_digest="sha1:abc",
            text="hello search",
            source_file="sample.warc",
        )

        sql = warc_importer.emit_sql(
            [record],
            lance_uri="/tmp/warc.lance",
            vector_dim=4,
            overwrite_lance=True,
            include_hash_vectors=True,
        )

        self.assertIn("INSERT INTO pgwarc_lance.warc_record", sql)
        self.assertIn("SELECT bm25_index_document(123, 'hello search');", sql)
        self.assertIn("SELECT lance_create_table('/tmp/warc.lance', 4, true);", sql)
        self.assertIn("SELECT lance_insert_many('/tmp/warc.lance', ARRAY[123]::bigint[], ARRAY[", sql)

    def test_bm25_tokenizer_matches_extension_policy(self):
        tokens = warc_importer.tokenize_bm25("A PostgreSQL 검색엔진 x 한 中國")

        self.assertEqual(
            tokens,
            ["postgresql", "검색", "색엔", "엔진", "한", "中國"],
        )

    def test_sql_bulk_bm25_mode_emits_direct_table_inserts(self):
        records = [
            warc_importer.ImportRecord(
                doc_id=123,
                target_uri="https://example.test/one",
                warc_date="2026-06-30T00:00:00Z",
                content_type="text/plain",
                http_status=200,
                payload_digest="sha1:abc",
                text="Hello hello 검색",
                source_file="sample.warc",
            ),
            warc_importer.ImportRecord(
                doc_id=124,
                target_uri="https://example.test/two",
                warc_date="2026-06-30T00:00:01Z",
                content_type="text/plain",
                http_status=200,
                payload_digest="sha1:def",
                text="Vector search",
                source_file="sample.warc",
            ),
        ]

        sql = warc_importer.emit_sql(
            records,
            lance_uri=None,
            vector_dim=4,
            overwrite_lance=False,
            include_hash_vectors=False,
            bm25_mode="bulk",
        )

        self.assertNotIn("bm25_index_document", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS pgwarc_lance.bm25_doc", sql)
        self.assertIn(
            "DELETE FROM pgwarc_lance.bm25_term WHERE doc_id = ANY(ARRAY[123,124]::bigint[]);",
            sql,
        )
        self.assertIn("(123, 'Hello hello 검색', 3)", sql)
        self.assertIn("('hello', 123, 2)", sql)
        self.assertIn("('검색', 123, 1)", sql)
        self.assertIn("('vector', 124, 1)", sql)

    def test_sql_copy_bm25_mode_emits_copy_stream(self):
        records = [
            warc_importer.ImportRecord(
                doc_id=123,
                target_uri="https://example.test/one",
                warc_date="2026-06-30T00:00:00Z",
                content_type="text/plain",
                http_status=200,
                payload_digest="sha1:abc",
                text="Hello\tcopy\n검색",
                source_file="sample.warc",
            )
        ]

        sql = warc_importer.emit_sql(
            records,
            lance_uri=None,
            vector_dim=4,
            overwrite_lance=False,
            include_hash_vectors=False,
            bm25_mode="copy",
        )

        self.assertNotIn("bm25_index_document", sql)
        self.assertIn(
            "DELETE FROM pgwarc_lance.bm25_doc WHERE doc_id = ANY(ARRAY[123]::bigint[]);",
            sql,
        )
        self.assertIn(
            "COPY pgwarc_lance.bm25_doc (doc_id, content, doc_len) FROM stdin;",
            sql,
        )
        self.assertIn(r"123	Hello\tcopy\n검색	3", sql)
        self.assertIn("COPY pgwarc_lance.bm25_term (term, doc_id, tf) FROM stdin;", sql)
        self.assertIn(r"\.", sql)

    def test_sql_uses_precomputed_embedding_vectors(self):
        record = warc_importer.ImportRecord(
            doc_id=123,
            target_uri="https://example.test/",
            warc_date="2026-06-30T00:00:00Z",
            content_type="text/plain",
            http_status=200,
            payload_digest="sha1:abc",
            text="hello search",
            source_file="sample.warc",
        )

        sql = warc_importer.emit_sql(
            [record],
            lance_uri="/tmp/warc.lance",
            vector_dim=4,
            overwrite_lance=False,
            include_hash_vectors=False,
            vectors_by_doc_id={123: [0.25, 0.5, 0.75, 1.0]},
        )

        self.assertIn("SELECT lance_create_table('/tmp/warc.lance', 4, false);", sql)
        self.assertIn(
            "SELECT lance_insert_many('/tmp/warc.lance', ARRAY[123]::bigint[], "
            "ARRAY[0.25000000,0.50000000,0.75000000,1.00000000]::float4[], 4, "
            "ARRAY['https://example.test/']::text[]",
            sql,
        )

    def test_sql_append_mode_skips_lance_create_table(self):
        record = warc_importer.ImportRecord(
            doc_id=123,
            target_uri="https://example.test/",
            warc_date="2026-06-30T00:00:00Z",
            content_type="text/plain",
            http_status=200,
            payload_digest="sha1:abc",
            text="hello search",
            source_file="sample.warc",
        )

        sql = warc_importer.emit_sql(
            [record],
            lance_uri="/tmp/warc.lance",
            vector_dim=4,
            overwrite_lance=False,
            include_hash_vectors=False,
            vectors_by_doc_id={123: [0.25, 0.5, 0.75, 1.0]},
            lance_mode="append",
        )

        self.assertNotIn("lance_create_table", sql)
        self.assertIn("does not deduplicate by doc_id", sql)
        self.assertIn("SELECT lance_insert_many('/tmp/warc.lance'", sql)

    def test_summary_marks_append_duplicate_policy(self):
        record = warc_importer.ImportRecord(
            doc_id=123,
            target_uri="https://example.test/",
            warc_date="2026-06-30T00:00:00Z",
            content_type="text/plain",
            http_status=200,
            payload_digest="sha1:abc",
            text="hello search",
            source_file="sample.warc",
        )
        sql = warc_importer.emit_sql(
            [record],
            lance_uri="/tmp/warc.lance",
            vector_dim=4,
            overwrite_lance=False,
            include_hash_vectors=True,
            lance_mode="append",
        )

        summary = warc_importer.summary_data(
            records=[record],
            sql=sql,
            vectors_by_doc_id=None,
            lance_uri="/tmp/warc.lance",
            no_hash_vectors=False,
            lance_mode="append",
            bm25_mode="function",
            execute=False,
            psql=warc_importer.DEFAULT_PSQL,
        )

        self.assertEqual(summary["lance_append_duplicates"], "possible")
        self.assertIn("lance_append_duplicates=possible", warc_importer.format_summary(summary))

    def test_summary_json_is_written_by_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            warc_path = Path(tmp) / "sample.warc"
            summary_path = Path(tmp) / "summary.json"
            warc_path.write_bytes(
                warc_response(
                    "https://example.test/summary",
                    "2026-06-30T00:00:00Z",
                    "<html><body><p>Summary text.</p></body></html>",
                )
            )

            rc = warc_importer.main(
                [
                    str(warc_path),
                    "--lance-uri",
                    "/tmp/warc.lance",
                    "--lance-mode",
                    "append",
                    "--summary-json",
                    str(summary_path),
                    "--output",
                    str(Path(tmp) / "import.sql"),
                ]
            )
            summary = json.loads(summary_path.read_text(encoding="utf-8"))

        self.assertEqual(rc, 0)
        self.assertEqual(summary["records"], 1)
        self.assertEqual(summary["lance_mode"], "append")
        self.assertEqual(summary["lance_append_duplicates"], "possible")
        self.assertEqual(summary["bm25_mode"], "function")

    def test_invalid_lance_mode_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "lance_mode"):
            warc_importer.emit_sql(
                [],
                lance_uri="/tmp/warc.lance",
                vector_dim=4,
                overwrite_lance=False,
                include_hash_vectors=True,
                lance_mode="reuse",
            )

    def test_embedding_command_receives_records_and_returns_vectors(self):
        record = warc_importer.ImportRecord(
            doc_id=123,
            target_uri="https://example.test/",
            warc_date="2026-06-30T00:00:00Z",
            content_type="text/plain",
            http_status=200,
            payload_digest="sha1:abc",
            text="hello search",
            source_file="sample.warc",
        )

        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "embed.py"
            script.write_text(
                "import json, sys\n"
                "for line in sys.stdin:\n"
                "    row = json.loads(line)\n"
                "    assert row['text'] == 'hello search'\n"
                "    print(json.dumps({'doc_id': row['doc_id'], 'vector': [1, 2, 3, 4]}))\n",
                encoding="utf-8",
            )

            vectors = warc_importer.run_embedding_command([record], f"python3 {script}", 4)

        self.assertEqual(vectors, {123: [1.0, 2.0, 3.0, 4.0]})

    def test_embedding_command_nonzero_exit_is_rejected(self):
        record = warc_importer.ImportRecord(
            doc_id=123,
            target_uri="https://example.test/",
            warc_date="2026-06-30T00:00:00Z",
            content_type="text/plain",
            http_status=200,
            payload_digest="sha1:abc",
            text="hello search",
            source_file="sample.warc",
        )

        with self.assertRaises(SystemExit) as ctx:
            warc_importer.run_embedding_command(
                [record],
                "python3 -c 'import sys; sys.exit(7)'",
                4,
            )

        self.assertEqual(ctx.exception.code, 7)

    def test_embedding_command_missing_doc_id_is_rejected(self):
        record = warc_importer.ImportRecord(
            doc_id=123,
            target_uri="https://example.test/",
            warc_date="2026-06-30T00:00:00Z",
            content_type="text/plain",
            http_status=200,
            payload_digest="sha1:abc",
            text="hello search",
            source_file="sample.warc",
        )

        with self.assertRaisesRegex(ValueError, "missing vectors"):
            warc_importer.run_embedding_command(
                [record],
                "python3 -c 'pass'",
                4,
            )

    def test_embedding_command_dimension_mismatch_is_rejected(self):
        record = warc_importer.ImportRecord(
            doc_id=123,
            target_uri="https://example.test/",
            warc_date="2026-06-30T00:00:00Z",
            content_type="text/plain",
            http_status=200,
            payload_digest="sha1:abc",
            text="hello search",
            source_file="sample.warc",
        )

        with self.assertRaisesRegex(ValueError, "expected 4"):
            warc_importer.run_embedding_command(
                [record],
                (
                    "python3 -c \"import json; "
                    "print(json.dumps({'doc_id': 123, 'vector': [1, 2]}))\""
                ),
                4,
            )

    def test_embedding_jsonl_requires_expected_dimension(self):
        record = warc_importer.ImportRecord(
            doc_id=123,
            target_uri=None,
            warc_date=None,
            content_type="text/plain",
            http_status=None,
            payload_digest="sha1:abc",
            text="hello search",
            source_file="sample.warc",
        )
        content = json.dumps({"doc_id": 123, "vector": [1, 2]})

        with self.assertRaisesRegex(ValueError, "expected 4"):
            warc_importer.parse_embedding_jsonl(content, {record.doc_id}, 4, "test")

    def test_embedding_jsonl_rejects_nonfinite_vector_values(self):
        for raw_value in ("NaN", "Infinity", "-Infinity"):
            content = f'{{"doc_id": 123, "vector": [1, {raw_value}, 3, 4]}}'

            with self.assertRaisesRegex(ValueError, "non-finite"):
                warc_importer.parse_embedding_jsonl(content, {123}, 4, "test")

    def test_embedding_jsonl_rejects_out_of_range_integer_values(self):
        content = json.dumps({"doc_id": 123, "vector": [10**1000, 1, 2, 3]})

        with self.assertRaisesRegex(ValueError, "out-of-range"):
            warc_importer.parse_embedding_jsonl(content, {123}, 4, "test")

    def test_embedding_jsonl_rejects_duplicate_required_doc_ids(self):
        content = "\n".join(
            [
                json.dumps({"doc_id": 123, "vector": [1, 2, 3, 4]}),
                json.dumps({"doc_id": 123, "vector": [4, 3, 2, 1]}),
            ]
        )

        with self.assertRaisesRegex(ValueError, "duplicate vector for doc_id=123"):
            warc_importer.parse_embedding_jsonl(content, {123}, 4, "vectors.jsonl")

    def test_records_jsonl_contains_embedding_input_fields(self):
        record = warc_importer.ImportRecord(
            doc_id=123,
            target_uri="https://example.test/",
            warc_date="2026-06-30T00:00:00Z",
            content_type="text/plain",
            http_status=200,
            payload_digest="sha1:abc",
            text="hello search",
            source_file="sample.warc",
        )

        [row] = [json.loads(line) for line in warc_importer.records_jsonl([record]).splitlines()]

        self.assertEqual(row["doc_id"], 123)
        self.assertEqual(row["text"], "hello search")
        self.assertEqual(row["text_len"], 12)

    def test_sql_batches_records_into_multiple_transactions(self):
        records = [
            warc_importer.ImportRecord(
                doc_id=doc_id,
                target_uri=None,
                warc_date=None,
                content_type="text/plain",
                http_status=None,
                payload_digest=f"sha1:{doc_id}",
                text=f"doc {doc_id}",
                source_file="sample.warc",
            )
            for doc_id in (1, 2, 3)
        ]

        sql = warc_importer.emit_sql(
            records,
            lance_uri=None,
            vector_dim=4,
            overwrite_lance=False,
            include_hash_vectors=False,
            batch_size=2,
        )

        self.assertEqual(sql.count("BEGIN;"), 2)
        self.assertEqual(sql.count("COMMIT;"), 2)
        self.assertIn("-- pgwarc_lance import batch 1/2: records 1-2", sql)
        self.assertIn("-- pgwarc_lance import batch 2/2: records 3-3", sql)
        self.assertLess(
            sql.index("-- pgwarc_lance import batch 1/2: records 1-2"),
            sql.index("BEGIN;"),
        )

    def test_sql_batches_lance_inserts(self):
        records = [
            warc_importer.ImportRecord(
                doc_id=doc_id,
                target_uri=f"https://example.test/{doc_id}",
                warc_date=None,
                content_type="text/plain",
                http_status=None,
                payload_digest=f"sha1:{doc_id}",
                text=f"doc {doc_id}",
                source_file="sample.warc",
            )
            for doc_id in (1, 2, 3)
        ]

        sql = warc_importer.emit_sql(
            records,
            lance_uri="/tmp/warc.lance",
            vector_dim=2,
            overwrite_lance=False,
            include_hash_vectors=False,
            vectors_by_doc_id={
                1: [0.1, 0.2],
                2: [0.3, 0.4],
                3: [0.5, 0.6],
            },
            batch_size=2,
        )

        self.assertEqual(sql.count("lance_insert_many"), 2)
        self.assertIn(
            "ARRAY[1,2]::bigint[], ARRAY[0.10000000,0.20000000,0.30000000,0.40000000]::float4[], 2",
            sql,
        )
        self.assertIn("ARRAY[3]::bigint[], ARRAY[0.50000000,0.60000000]::float4[], 2", sql)


if __name__ == "__main__":
    unittest.main()
