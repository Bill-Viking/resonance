"""Public exports use only synthetic files in temporary repositories."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from tools import make_release


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'source'
        self.root.mkdir()
        self.output = self.root.parent / 'public'
        self.git('init')
        self.git('config', 'user.name', 'Example contributor')
        self.git('config', 'user.email', 'example@example.invalid')
        for name in make_release.ROOT_FILES:
            self.write(name, '0.1.0-preview.1\n' if name == 'VERSION' else 'Synthetic public file.\n')
        for name in ('STATUS.md', 'OPENER.md', 'docs/internal/notes.md', 'unlisted.txt'):
            self.write(name, 'Synthetic internal note.\n')
        self.write('static/app.js', '"Synthetic app";\n')
        self.commit()

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args], stderr=subprocess.STDOUT)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def commit(self):
        self.git('add', '.')
        self.git('commit', '-m', 'Synthetic fixture')

    def test_exports_only_committed_public_files_and_a_verifiable_manifest(self):
        output, count = make_release.export(self.root, self.output, '0.1.0-preview.1')
        manifest = json.loads((output / 'RELEASE-MANIFEST.json').read_text())
        self.assertEqual(count, len(manifest['files']))
        self.assertEqual((output / 'static/app.js').read_bytes(), self.git('show', 'HEAD:static/app.js'))
        for name in ('STATUS.md', 'OPENER.md', 'docs/internal/notes.md', 'unlisted.txt', '.git'):
            self.assertFalse((output / name).exists())
        for name, item in manifest['files'].items():
            content = (output / name).read_bytes()
            self.assertEqual(len(content), item['bytes'])
            self.assertEqual(make_release.hashlib.sha256(content).hexdigest(), item['sha256'])
        self.assertFalse(make_release.public_path('static/../STATUS.md'))

    def test_refuses_dirty_overwrite_nested_and_version_mismatch_before_creating_files(self):
        self.write('new.txt', 'Uncommitted synthetic file.')
        with self.assertRaisesRegex(ValueError, 'Commit'):
            make_release.export(self.root, self.output, '0.1.0-preview.1')
        self.commit()
        for destination, version in ((self.root / 'nested', '0.1.0-preview.1'),
                                     (self.output, '0.1.0-preview.2'), (self.output, '../invalid')):
            with self.assertRaises(ValueError):
                make_release.export(self.root, destination, version)
        self.assertFalse(self.output.exists())
        self.output.mkdir()
        (self.output / 'keep.txt').write_text('Synthetic original.')
        with self.assertRaisesRegex(ValueError, 'new folder'):
            make_release.export(self.root, self.output, '0.1.0-preview.1')
        self.assertEqual((self.output / 'keep.txt').read_text(), 'Synthetic original.')

    def test_refuses_private_home_path_and_missing_required_files(self):
        for marker in ('/Users/' + 'fixture/private', 'C:' + '\\Users\\' + 'fixture\\private',
                       'C:' + '\\\\Users\\\\' + 'fixture\\\\private'):
            self.write('README.md', marker)
            self.commit()
            with self.assertRaisesRegex(ValueError, 'private-system marker'):
                make_release.export(self.root, self.output, '0.1.0-preview.1')
            self.assertFalse(self.output.exists())
        self.write('README.md', 'Synthetic public file.\n')
        self.git('rm', 'LICENSE')
        self.commit()
        with self.assertRaisesRegex(ValueError, 'Missing public files'):
            make_release.export(self.root, self.output, '0.1.0-preview.1')

    @unittest.skipIf(os.name == 'nt', 'Creating symlinks requires extra Windows rights')
    def test_refuses_a_public_symlink_instead_of_following_it(self):
        (self.root / 'static/link.js').symlink_to('../STATUS.md')
        self.commit()
        with self.assertRaisesRegex(ValueError, 'ordinary files'):
            make_release.export(self.root, self.output, '0.1.0-preview.1')
        self.assertFalse(self.output.exists())
