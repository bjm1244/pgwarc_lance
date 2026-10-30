import json
import subprocess
import sys
import tempfile
import time
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

from tools import collection_manifest as cm
from tools import warc_importer


class CollectionManifestTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.path = self.root / "collection.json"
        self.base = {
            "collection_id": "archive",
            "version_id": "e5-v1",
            "dataset_path": "datasets/e5-v1.lance",
            "model": "example/e5-small",
            "model_version": "sha256:fixture-model",
            "dimensions": 3,
            "expected_vectors": 1,
            "normalization": "l2",
            "normalization_version": "l2-f32-v1",
            "chunking_version": "whole-document-v1",
        }

    def tearDown(self):
        self.temporary.cleanup()

    def profile_args(self, **changes):
        values = {**self.base, **changes}
        return Namespace(
            manifest=self.path,
            collection_id=values["collection_id"],
            version_id=values["version_id"],
            dataset_path=values["dataset_path"],
            model=values["model"],
            model_version=values["model_version"],
            dimensions=values["dimensions"],
            expected_vectors=values["expected_vectors"],
            normalization=values["normalization"],
            normalization_version=values["normalization_version"],
            chunking_version=values["chunking_version"],
            chunking_strategy=values.get("chunking_strategy", "whole_document"),
            chunk_size_chars=values.get("chunk_size_chars"),
            chunk_overlap_chars=values.get("chunk_overlap_chars", 0),
        )

    def create(self):
        cm._create(self.profile_args())
        return cm.load_manifest(self.path)

    def activate_initial(self):
        raw = self.create()
        (self.root / raw["versions"]["e5-v1"]["dataset_path"]).mkdir(parents=True)
        cm.record_ready(self.path, "e5-v1", {
            "complete": True, "execute": True, "lance_mode": "upsert",
            "records": 1, "vector_rows": 1, "input_sha256": "a" * 64,
        })
        with mock.patch.object(cm, "_dataset_dimension", return_value=3):
            cm._switch(Namespace(manifest=self.path, version_id="e5-v1", psql="psql"), "activate")
        return cm.load_manifest(self.path)

    def test_initial_profile_is_staged_and_dataset_path_is_manifest_relative(self):
        raw = self.create()
        self.assertIsNone(raw["active_version"])
        self.assertEqual(raw["staged_version"], "e5-v1")
        self.assertEqual(
            cm.import_profile(raw, self.path, "e5-v1")[1],
            self.root / "datasets/e5-v1.lance",
        )
        self.assertEqual(raw["authorization"]["tenant_isolation"], "not_supported")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_creation_refuses_existing_manifest_and_dataset_path(self):
        self.create()
        with self.assertRaisesRegex(cm.ManifestError, "existing manifest"):
            cm._create(self.profile_args())

        path = self.root / "other.json"
        (self.root / "datasets/e5-v1.lance").mkdir(parents=True)
        args = self.profile_args()
        args.manifest = path
        with self.assertRaisesRegex(cm.ManifestError, "already exists"):
            cm._create(args)

    def test_escaping_and_reused_dataset_paths_are_rejected(self):
        args = self.profile_args(dataset_path="../outside.lance")
        with self.assertRaisesRegex(cm.ManifestError, "below the manifest"):
            cm._create(args)
        self.activate_initial()
        stage = self.profile_args(version_id="e5-v2", dataset_path="datasets/e5-v1.lance")
        with self.assertRaisesRegex(cm.ManifestError, "reused"):
            cm._stage(stage)

    def test_symlink_dataset_component_is_rejected(self):
        self.activate_initial()
        outside = self.root.parent / (self.root.name + "-outside")
        outside.mkdir()
        try:
            (self.root / "linked").symlink_to(outside, target_is_directory=True)
            args = self.profile_args(version_id="e5-v2", dataset_path="linked/e5-v2.lance")
            with self.assertRaisesRegex(cm.ManifestError, "symbolic links"):
                cm._stage(args)
        finally:
            outside.rmdir()

    def test_profile_checksum_detects_edits(self):
        raw = self.create()
        raw["versions"]["e5-v1"]["embedding"]["model_version"] = "different"
        with self.assertRaisesRegex(cm.ManifestError, "checksum"):
            cm.validate_manifest(raw, self.path)

    def test_char_window_profile_preserves_size_overlap_and_version(self):
        cm._create(self.profile_args(
            chunking_strategy="char_window", chunk_size_chars=512,
            chunk_overlap_chars=64, chunking_version="char-window-v1",
        ))
        raw = cm.load_manifest(self.path)
        self.assertEqual(raw["versions"]["e5-v1"]["chunking"], {
            "strategy": "char_window", "version": "char-window-v1",
            "size_chars": 512, "overlap_chars": 64,
        })

    def test_char_window_profile_rejects_invalid_boundaries(self):
        for size, overlap in ((None, 0), (10, 10), (10, -1), (True, 0)):
            with self.subTest(size=size, overlap=overlap), self.assertRaisesRegex(
                cm.ManifestError, "char_window"
            ):
                cm._create(self.profile_args(
                    chunking_strategy="char_window", chunk_size_chars=size,
                    chunk_overlap_chars=overlap, chunking_version="char-window-v1",
                ))

    def test_abandon_keeps_candidate_metadata_and_dataset_files(self):
        self.activate_initial()
        stage = self.profile_args(version_id="e5-v2", dataset_path="datasets/e5-v2.lance")
        cm._stage(stage)
        staged_path = self.root / "datasets/e5-v2.lance"
        staged_path.mkdir(parents=True)
        cm._abandon(Namespace(manifest=self.path, version_id="e5-v2"))
        raw = cm.load_manifest(self.path)
        self.assertIsNone(raw["staged_version"])
        self.assertIn("e5-v2", raw["versions"])
        self.assertTrue(staged_path.is_dir())

    def test_prepare_uses_registered_dimension_and_checks_pg_result(self):
        self.create()
        args = Namespace(manifest=self.path, version_id="e5-v1", psql="psql -d test")
        with mock.patch.object(cm, "_run_psql") as run_sql, mock.patch.object(
            cm, "_dataset_dimension", return_value=3
        ) as inspect:
            cm._prepare(args)
        self.assertIn("lance_create_table", run_sql.call_args.args[0])
        self.assertIn("'" + str(self.root / "datasets/e5-v1.lance") + "', 3, false", run_sql.call_args.args[0])
        inspect.assert_called_once()

    def test_failed_activation_preserves_manifest_and_active_path(self):
        raw = self.create()
        dataset = self.root / raw["versions"]["e5-v1"]["dataset_path"]
        dataset.mkdir(parents=True)
        cm.record_ready(self.path, "e5-v1", {
            "complete": True, "execute": True, "lance_mode": "create",
            "records": 1, "vector_rows": 1, "input_sha256": "a" * 64,
        })
        before = self.path.read_bytes()
        args = Namespace(manifest=self.path, version_id="e5-v1", psql="psql -d test")
        with mock.patch.object(cm, "_dataset_dimension", return_value=4):
            with self.assertRaisesRegex(cm.ManifestError, "requires 3"):
                cm._switch(args, "activate")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertIsNone(cm.load_manifest(self.path)["active_version"])

    def test_empty_or_wrong_count_candidate_cannot_be_marked_ready_or_activated(self):
        raw = self.create()
        dataset = self.root / raw["versions"]["e5-v1"]["dataset_path"]
        dataset.mkdir(parents=True)
        with self.assertRaisesRegex(cm.ManifestError, "produced 0 vectors"):
            cm.record_ready(self.path, "e5-v1", {
                "complete": True, "execute": True, "lance_mode": "upsert",
                "records": 1, "vector_rows": 0, "input_sha256": "a" * 64,
            })
        with mock.patch.object(cm, "_dataset_dimension", return_value=3):
            with self.assertRaisesRegex(cm.ManifestError, "no complete import receipt"):
                cm._switch(Namespace(manifest=self.path, version_id="e5-v1", psql="psql"), "activate")
        self.assertIsNone(cm.load_manifest(self.path)["active_version"])

    def test_staged_reindex_activation_and_rollback_preserve_both_datasets(self):
        raw = self.create()
        first_path = self.root / raw["versions"]["e5-v1"]["dataset_path"]
        first_path.mkdir(parents=True)
        cm.record_ready(self.path, "e5-v1", {
            "complete": True, "execute": True, "lance_mode": "create",
            "records": 1, "vector_rows": 1, "input_sha256": "a" * 64,
        })
        with mock.patch.object(cm, "_dataset_dimension", return_value=3):
            cm._switch(Namespace(manifest=self.path, version_id="e5-v1", psql="psql"), "activate")

        stage_args = self.profile_args(
            version_id="e5-v2", dataset_path="datasets/e5-v2.lance",
            model="example/e5-large", model_version="sha256:fixture-model-2",
            dimensions=4, normalization="none", normalization_version="none-v1",
            chunking_version="whole-document-v2",
        )
        cm._stage(stage_args)
        staged_path = self.root / "datasets/e5-v2.lance"
        staged_path.mkdir(parents=True)
        cm.record_ready(self.path, "e5-v2", {
            "complete": True, "execute": True, "lance_mode": "upsert",
            "records": 1, "vector_rows": 1, "input_sha256": "b" * 64,
        })
        with mock.patch.object(cm, "_dataset_dimension", return_value=4):
            cm._switch(Namespace(manifest=self.path, version_id="e5-v2", psql="psql"), "activate")
        self.assertEqual(cm.load_manifest(self.path)["active_version"], "e5-v2")

        with mock.patch.object(cm, "_dataset_dimension", return_value=3):
            cm._switch(Namespace(manifest=self.path, psql="psql"), "rollback")
        restored = cm.load_manifest(self.path)
        self.assertEqual(restored["active_version"], "e5-v1")
        self.assertTrue(first_path.is_dir())
        self.assertTrue(staged_path.is_dir())
        self.assertEqual([event["action"] for event in restored["activation_history"]], ["activate", "activate", "rollback"])

    def test_import_profile_rejects_versions_other_than_active_or_staged(self):
        raw = self.create()
        with self.assertRaisesRegex(cm.ManifestError, "active version"):
            cm.import_profile(raw, self.path, None)
        with self.assertRaisesRegex(cm.ManifestError, "active version or the one staged"):
            cm.import_profile(raw, self.path, "unregistered")

    def test_importer_binds_all_profile_attributes_and_blocks_active_recreate(self):
        raw = self.create()
        args = Namespace(
            collection_manifest=self.path,
            collection_version="e5-v1",
            embedding_model="example/e5-small",
            embedding_model_version="sha256:fixture-model",
            embedding_normalization="l2",
            normalization_version="l2-f32-v1",
            chunking_version="whole-document-v1",
            chunking_strategy=None,
            chunk_size_chars=None,
            chunk_overlap_chars=None,
            vector_dim=3,
            audit_manifest_json=Path("audit.json"),
            checkpoint_db=None,
            lance_uri=None,
            embedding_command=None,
            embedding_jsonl=Path("vectors.jsonl"),
            no_hash_vectors=False,
            lance_mode="create",
            overwrite_lance=False,
        )
        warc_importer._bind_collection_profile(args, raw)
        self.assertEqual(args.lance_uri, str(self.root / "datasets/e5-v1.lance"))
        self.assertEqual(args.collection_id, "archive")
        self.assertEqual(args.collection_profile_sha256, raw["versions"]["e5-v1"]["profile_sha256"])

        args.embedding_model_version = "mutable-latest"
        with self.assertRaisesRegex(SystemExit, "does not match"):
            warc_importer._bind_collection_profile(args, raw)

        cases = [
            {"vector_dim": 4},
            {"embedding_model": "example/other"},
            {"embedding_normalization": "none"},
            {"normalization_version": "l2-v2"},
            {"chunking_version": "whole-document-v2"},
            {"lance_uri": str(self.root / "outside.lance")},
        ]
        for change in cases:
            changed = Namespace(**{**vars(args),
                                   "embedding_model_version": "sha256:fixture-model",
                                   **change})
            with self.subTest(change=change), self.assertRaisesRegex(SystemExit, "does not match|dimension|managed"):
                warc_importer._bind_collection_profile(changed, raw)

    def test_manifest_mutations_wait_until_import_shared_lock_is_released(self):
        self.activate_initial()
        args = self.profile_args(version_id="e5-v2", dataset_path="datasets/e5-v2.lance")
        command = [
            sys.executable, "tools/collection_manifest.py", "stage", str(self.path),
            "--version-id", args.version_id, "--dataset-path", args.dataset_path,
            "--model", args.model, "--model-version", args.model_version,
            "--dimensions", str(args.dimensions), "--expected-vectors", str(args.expected_vectors),
            "--normalization", args.normalization,
            "--normalization-version", args.normalization_version,
            "--chunking-version", args.chunking_version,
        ]
        with cm.import_lock(self.path):
            child = subprocess.Popen(
                command,
                cwd=Path(__file__).resolve().parents[1],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            time.sleep(0.15)
            self.assertIsNone(child.poll(), "exclusive manifest update should wait behind import lock")
        stdout, stderr = child.communicate(timeout=5)
        self.assertEqual(child.returncode, 0, stderr or stdout)
        self.assertEqual(cm.load_manifest(self.path)["staged_version"], "e5-v2")

    def test_normalized_vectors_must_be_unit_length(self):
        content = json.dumps({"doc_id": 1, "vector": [1, 0, 0]})
        self.assertEqual(
            warc_importer.parse_embedding_jsonl(content, {1}, 3, "fixture", "l2")[1],
            [1.0, 0.0, 0.0],
        )
        content = json.dumps({"doc_id": 1, "vector": [2, 0, 0]})
        with self.assertRaisesRegex(ValueError, "L2 norm"):
            warc_importer.parse_embedding_jsonl(content, {1}, 3, "fixture", "l2")
        self.assertEqual(
            warc_importer.parse_embedding_jsonl(content, {1}, 3, "fixture", "none")[1],
            [2.0, 0.0, 0.0],
        )


if __name__ == "__main__":
    unittest.main()
