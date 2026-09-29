import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = ROOT / "tools" / "make_quality_fixture.py"
SPEC = importlib.util.spec_from_file_location("make_quality_fixture", BUILDER_PATH)
make_quality_fixture = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = make_quality_fixture
SPEC.loader.exec_module(make_quality_fixture)


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


class MakeQualityFixtureTests(unittest.TestCase):
    def test_builds_fixture_from_records_embeddings_and_queries(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            records_path = tmp_path / "records.jsonl"
            embeddings_path = tmp_path / "embeddings.jsonl"
            queries_path = ROOT / "tests" / "fixtures" / "quality_queries.json"
            write_jsonl(
                records_path,
                [
                    {
                        "doc_id": 2001,
                        "target_uri": "https://fixture.example/korean",
                        "warc_date": "2026-07-01T00:00:00Z",
                        "content_type": "text/plain",
                        "http_status": 200,
                        "source_file": "records.jsonl",
                        "text": "한국어 검색 품질 fixture document",
                    },
                    {
                        "doc_id": 2002,
                        "target_uri": "https://fixture.example/vector",
                        "warc_date": "2026-07-01T00:00:00Z",
                        "content_type": "text/plain",
                        "http_status": 200,
                        "source_file": "records.jsonl",
                        "text": "vector embedding fixture document",
                    },
                ],
            )
            write_jsonl(
                embeddings_path,
                [
                    {"doc_id": 2001, "vector": [1.0, 0.0, 0.0, 0.0]},
                    {"doc_id": 2002, "vector": [0.0, 1.0, 0.0, 0.0]},
                ],
            )

            fixture = make_quality_fixture.build_fixture(
                name="built",
                records_path=records_path,
                embeddings_path=embeddings_path,
                queries_path=queries_path,
                vector_dim=4,
            )

        self.assertEqual(fixture["name"], "built")
        self.assertEqual(fixture["vector_dim"], 4)
        self.assertEqual([doc["doc_id"] for doc in fixture["docs"]], [2001, 2002])
        self.assertEqual([query["name"] for query in fixture["queries"]], ["built_korean", "built_vector"])

    def test_rejects_missing_embedding_for_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            records_path = tmp_path / "records.jsonl"
            embeddings_path = tmp_path / "embeddings.jsonl"
            queries_path = ROOT / "tests" / "fixtures" / "quality_queries.json"
            write_jsonl(records_path, [{"doc_id": 2001, "text": "doc"}])
            write_jsonl(embeddings_path, [{"doc_id": 2002, "vector": [1.0, 0.0, 0.0, 0.0]}])

            with self.assertRaisesRegex(ValueError, "missing vectors"):
                make_quality_fixture.build_fixture(
                    name="bad",
                    records_path=records_path,
                    embeddings_path=embeddings_path,
                    queries_path=queries_path,
                    vector_dim=4,
                )

    def test_rejects_query_labels_for_unknown_docs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            records_path = tmp_path / "records.jsonl"
            embeddings_path = tmp_path / "embeddings.jsonl"
            queries_path = tmp_path / "queries.json"
            write_jsonl(records_path, [{"doc_id": 1, "text": "doc"}])
            write_jsonl(embeddings_path, [{"doc_id": 1, "vector": [1.0, 0.0]}])
            queries_path.write_text(
                json.dumps(
                    {
                        "queries": [
                            {
                                "name": "bad",
                                "query": "doc",
                                "vector": [1.0, 0.0],
                                "expected_doc_ids": [2],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "unknown expected doc_id"):
                make_quality_fixture.build_fixture(
                    name="bad",
                    records_path=records_path,
                    embeddings_path=embeddings_path,
                    queries_path=queries_path,
                    vector_dim=2,
                )

    def test_rejects_duplicate_query_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            records_path = tmp_path / "records.jsonl"
            embeddings_path = tmp_path / "embeddings.jsonl"
            queries_path = tmp_path / "queries.json"
            write_jsonl(records_path, [{"doc_id": 1, "text": "doc"}])
            write_jsonl(embeddings_path, [{"doc_id": 1, "vector": [1.0]}])
            queries_path.write_text(
                json.dumps(
                    {
                        "queries": [
                            {
                                "name": "same",
                                "query": "first",
                                "vector": [1.0],
                                "expected_doc_ids": [1],
                            },
                            {
                                "name": "same",
                                "query": "second",
                                "vector": [1.0],
                                "expected_doc_ids": [1],
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "duplicate name"):
                make_quality_fixture.build_fixture(
                    name="bad",
                    records_path=records_path,
                    embeddings_path=embeddings_path,
                    queries_path=queries_path,
                    vector_dim=1,
                )

    def test_rejects_doc_ids_outside_postgresql_bigint_domain(self):
        for raw_id in (0, -1, True, "1", 2**63):
            with self.subTest(raw_id=raw_id), tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                records_path = tmp_path / "records.jsonl"
                embeddings_path = tmp_path / "embeddings.jsonl"
                queries_path = ROOT / "tests" / "fixtures" / "quality_queries.json"
                write_jsonl(records_path, [{"doc_id": raw_id, "text": "doc"}])
                write_jsonl(embeddings_path, [{"doc_id": 1, "vector": [1.0]}])

                with self.assertRaisesRegex(
                    ValueError,
                    "positive integer within PostgreSQL bigint range",
                ):
                    make_quality_fixture.build_fixture(
                        name="bad",
                        records_path=records_path,
                        embeddings_path=embeddings_path,
                        queries_path=queries_path,
                        vector_dim=1,
                    )


if __name__ == "__main__":
    unittest.main()
