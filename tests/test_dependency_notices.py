from __future__ import annotations
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import collect_dependency_notices as notices


class DependencyNoticesTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.crate = self.root / 'crate'
        self.crate.mkdir()
        self.lock = self.root / 'Cargo.lock'
        self.lock.write_text('[[package]]\nname="dep"\nversion="1.0.0"\nchecksum="abc"\n')
        self.overrides = self.root / 'manifest.json'
        self.overrides.write_text('{}')
        self.metadata = {
            'resolve': {'root': 'root', 'nodes': [
                {'id': 'root', 'deps': [
                    {'pkg': 'dep', 'dep_kinds': [{'kind': None}]},
                    {'pkg': 'dev', 'dep_kinds': [{'kind': 'dev'}]}]},
                {'id': 'dep', 'deps': []}, {'id': 'dev', 'deps': []}]},
            'packages': [
                {'id': 'root', 'name': 'root', 'version': '1'},
                {'id': 'dep', 'name': 'dep', 'version': '1.0.0', 'license': 'MIT',
                 'manifest_path': str(self.crate / 'Cargo.toml'), 'repository': 'https://example.test/dep'},
                {'id': 'dev', 'name': 'dev', 'version': '1'}]}

    def collect(self):
        return notices.collect(self.metadata, self.lock, self.overrides, self.root / 'output', 'x86_64-unknown-linux-gnu')

    def test_header_does_not_substitute_for_full_license(self):
        (self.crate / 'protos').mkdir()
        (self.crate / 'protos/license_header.txt').write_text('Apache license reference')
        report = self.collect()
        self.assertEqual(report['missing_primary_texts'], ['dep@1.0.0'])
        self.assertEqual(report['status'], 'needs_review')

    def test_text_and_embedded_notice_are_preserved_and_dev_excluded(self):
        (self.crate / 'LICENSE').write_text('License copyright text\n')
        (self.crate / 'native').mkdir()
        (self.crate / 'native/NOTICE').write_text('Native attribution\n')
        report = self.collect()
        self.assertEqual(report['package_count'], 1)
        self.assertEqual(report['missing_primary_texts'], [])
        self.assertEqual(report['cargo_lock_sha256'], hashlib.sha256(self.lock.read_bytes()).hexdigest())
        saved = [(self.root / 'output' / row['file']).read_text() for row in report['packages'][0]['texts']]
        self.assertCountEqual(saved, ['License copyright text\n', 'Native attribution\n'])

    def test_modified_upstream_override_is_rejected(self):
        path = self.root / 'upstream-license.txt'
        path.write_text('Modified text')
        self.overrides.write_text(json.dumps({'dep@1.0.0': {'files': [
            {'path': path.name, 'url': 'https://example.test/pinned/LICENSE', 'sha256': '0' * 64}]}}))
        with self.assertRaisesRegex(ValueError, 'checksum differs'):
            self.collect()

    def test_override_cannot_read_outside_reviewed_directory(self):
        self.overrides.write_text(json.dumps({'dep@1.0.0': {'files': [
            {'path': '../outside-license.txt', 'sha256': '0' * 64}]}}))
        with self.assertRaisesRegex(ValueError, 'escapes reviewed directory'):
            self.collect()

    def add_override(self, **fields):
        data = b'Original word-list attribution\n'
        path = self.root / 'data-notice.txt'
        path.write_bytes(data)
        item = dict(path=path.name, url='https://example.test/pinned/NOTICE',
                    sha256=hashlib.sha256(data).hexdigest(), **fields)
        self.overrides.write_text(json.dumps({'dep@1.0.0': {'files': [item]}}))

    def test_bundled_data_notice_cannot_clear_package_license_gap(self):
        self.add_override(coverage='bundled-data', covered_path='src/br/en.br')
        report = self.collect()
        self.assertEqual(report['status'], 'needs_review')
        self.assertEqual(report['missing_primary_texts'], ['dep@1.0.0'])
        item = report['packages'][0]['texts'][0]
        self.assertEqual(item['coverage'], 'bundled-data')
        self.assertEqual(item['covered_path'], 'src/br/en.br')
        self.assertEqual((self.root/'output'/item['file']).read_bytes(), b'Original word-list attribution\n')
        self.assertIn('Coverage: bundled-data (src/br/en.br)', (self.root/'output/DEPENDENCY_NOTICES.txt').read_text())
        self.assertIn('REVIEW REQUIRED: primary license text missing', (self.root/'output/DEPENDENCY_NOTICES.txt').read_text())

    def test_legacy_package_override_still_supplies_primary_text(self):
        self.add_override()
        report = self.collect()
        self.assertEqual(report['missing_primary_texts'], [])
        self.assertEqual(report['packages'][0]['texts'][0]['coverage'], 'package')

    def test_unknown_coverage_is_rejected_instead_of_silently_clearing_gap(self):
        self.add_override(coverage='bundled-dtaa', covered_path='src/br/en.br')
        with self.assertRaisesRegex(ValueError, 'unknown override coverage'):
            self.collect()

    def test_data_coverage_requires_exact_asset_path(self):
        self.add_override(coverage='bundled-data')
        with self.assertRaisesRegex(ValueError, 'needs covered_path'):
            self.collect()

    def test_native_spdx_copyrights_are_preserved_and_deduplicated(self):
        (self.crate/'LICENSE').write_text('Full package license\n')
        (self.crate/'native.c').write_text('// SPDX-FileCopyrightText: Copyright Native Authors\n')
        (self.crate/'header.h').write_text('// SPDX-FileCopyrightText: Copyright Native Authors\n')
        (self.crate/'fast.S').write_text('# SPDX-FileCopyrightText: Copyright Assembly Authors\n')
        (self.crate/'lib.rs').write_text('// SPDX-FileCopyrightText: Copyright Rust Authors\n')
        report = self.collect()
        item = next(x for x in report['packages'][0]['texts'] if x['origin']=='SPDX-FileCopyrightText in crate sources')
        self.assertEqual((self.root/'output'/item['file']).read_text(),
                         'Copyright Assembly Authors\nCopyright Native Authors\nCopyright Rust Authors\n')

    def test_invalid_native_source_encoding_is_rejected(self):
        (self.crate/'native.c').write_bytes(b'// SPDX-FileCopyrightText: invalid \xff\n')
        with self.assertRaises(UnicodeDecodeError):
            self.collect()
