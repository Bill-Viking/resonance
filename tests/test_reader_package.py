"""Exercise the Reader package using temporary audited export fixtures."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile
import zlib

from tools import build_reader


def write_export(runtime, files, version='0.1.0-rc.7'):
    required = build_reader.build_desktop.REQUIRED
    contents = dict.fromkeys(required, b'Synthetic release file.\n')
    contents.update({'VERSION': (version + '\n').encode(),
                     'release.json': b'{"github_owner":"Example-Owner"}'})
    contents.update(files)
    listing = {}
    for name, data in contents.items():
        path = runtime / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        listing[name] = {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
    (runtime / 'RELEASE-MANIFEST.json').write_text(json.dumps({'version': version, 'files': listing}),
                                                 encoding='utf-8')
    return runtime


class ReaderPackageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        # Public Reader sources are included as the extension fixture, with no user data.
        self.files = build_reader.collect(build_reader.SOURCE)
        self.runtime = write_export(self.root / 'runtime', {
            **{'browser-reader/' + name: data for name, data in self.files.items()},
            'browser-reader/README.md': b'Synthetic source-only note.\n',
        })
        self.output = self.root / 'output'

    def cli(self, *arguments):
        return subprocess.run([sys.executable, '-I', '-B', str(Path(build_reader.__file__).resolve()), *arguments],
                              cwd=self.root, text=True, capture_output=True, timeout=20)

    def test_cli_requires_audited_runtime_and_plan_never_writes(self):
        missing = self.cli('--out', str(self.output))
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn('--runtime', missing.stderr)
        self.assertFalse(self.output.exists())
        planned = self.cli('--runtime', str(self.runtime), '--plan', '--out', str(self.output))
        self.assertEqual(planned.returncode, 0, planned.stderr)
        self.assertIn('No files written', planned.stdout)
        self.assertFalse(self.output.exists())
        sources = Path(build_reader.__file__).resolve().parent
        exported = write_export(self.root / 'exported-tool', {
            **{'browser-reader/' + name: data for name, data in self.files.items()},
            **{'tools/' + name: (sources / name).read_bytes()
               for name in ('build_reader.py', 'build_desktop.py', 'make_release.py', 'prepare_build.py',
                            'sign_release.py')},
        })
        before = sorted(exported.rglob('*'))
        result = subprocess.run([sys.executable, '-I', str(exported / 'tools' / 'build_reader.py'),
                                 '--runtime', str(exported), '--plan'], cwd=self.root,
                                text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(exported.rglob('*')), before)

    def test_archive_is_reproducible_and_matches_manifest_and_allowlist(self):
        result = self.cli('--runtime', str(self.runtime), '--out', str(self.output))
        self.assertEqual(result.returncode, 0, result.stderr)
        first = self.output / 'resonance-reader-0.1.0.zip'
        receipt = self.output / 'resonance-reader-0.1.0.sha256.json'
        record = json.loads(receipt.read_bytes())
        second, other_receipt, _ = build_reader.build(self.root / 'second', self.runtime)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(receipt.read_bytes(), other_receipt.read_bytes())
        self.assertEqual(record['zip']['sha256'], hashlib.sha256(first.read_bytes()).hexdigest())
        self.assertEqual(record['source_manifest_sha256'],
                         hashlib.sha256((self.runtime / 'RELEASE-MANIFEST.json').read_bytes()).hexdigest())
        self.assertEqual(record['zlib_version'], zlib.ZLIB_RUNTIME_VERSION)
        repeated = self.cli('--runtime', str(self.runtime), '--out', str(self.output))
        self.assertNotEqual(repeated.returncode, 0)
        self.assertIn('new folder', repeated.stderr)
        with zipfile.ZipFile(first) as archive:
            self.assertEqual(archive.namelist(), sorted(build_reader.SHIPPED))
            for entry in archive.infolist():
                self.assertEqual(entry.compress_type, zipfile.ZIP_STORED)
                self.assertEqual(entry.date_time, build_reader.FIXED_TIME)
                self.assertEqual(archive.read(entry), self.files[entry.filename])

    def test_build_uses_verified_snapshot_instead_of_rereading_reader_files(self):
        original = build_reader.build_desktop.verify_runtime

        def verified_then_changed(runtime):
            result = original(runtime)
            (runtime / 'browser-reader' / 'bridge.js').write_text('Synthetic changed bridge.')
            return result

        with mock.patch.object(build_reader.build_desktop, 'verify_runtime', side_effect=verified_then_changed):
            archive, _, _ = build_reader.build(self.output, self.runtime)
        with zipfile.ZipFile(archive) as reader:
            self.assertEqual(reader.read('bridge.js'), self.files['bridge.js'])

    def test_refuses_invalid_manifest_mutated_and_unlisted_files(self):
        files = {
            'mutated release file': self.runtime / 'server.py',
            'invalid manifest': self.runtime / 'RELEASE-MANIFEST.json',
            'unlisted file': self.runtime / 'browser-reader' / 'notes.txt',
        }
        for case, file in files.items():
            with self.subTest(case=case):
                before = file.read_bytes() if file.exists() else None
                file.write_bytes(b'Synthetic changed input.')
                try:
                    for plan in (False, True):
                        with self.assertRaisesRegex(ValueError, '(?i)manifest'):
                            build_reader.build(self.output, self.runtime, plan=plan)
                        self.assertFalse(self.output.exists())
                finally:
                    if before is None:
                        file.unlink()
                    else:
                        file.write_bytes(before)

    @unittest.skipIf(sys.platform == 'win32', 'Creating symlinks requires Windows privileges')
    def test_refuses_symlink_even_when_target_bytes_match(self):
        listed = self.runtime / 'browser-reader' / 'bridge.js'
        target = self.root / 'synthetic-bridge.js'
        target.write_bytes(listed.read_bytes())
        listed.unlink()
        listed.symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'Symbolic links'):
            build_reader.build(self.output, self.runtime)
        self.assertFalse(self.output.exists())
