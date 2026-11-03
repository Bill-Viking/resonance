"""Public exports use only synthetic files in temporary repositories."""
import json
import os
from pathlib import Path
import re
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
        for name in ('desktop/launch.py', 'desktop/requirements-build.txt', 'docs/DESKTOP.md',
                     'tools/build_desktop.py', 'tools/make_release.py', '.github/workflows/desktop-candidate.yml'):
            self.write(name, 'Synthetic desktop file.\n')
        for name in ('desktop/private-notes.md', 'desktop/build/Resonance.spec', 'tools/other_tool.py',
                     'experiments/trial.py', '.github/workflows/other.yml', 'docs/other.md'):
            self.write(name, 'Synthetic unlisted file.\n')
        for name in sorted(make_release.PUBLIC_READER) + ['tools/build_reader.py', 'tests/reader-fixtures/fake_browser.cjs',
                                                          'tests/reader-fixtures/fixtures/routes.json']:
            self.write(name, 'Synthetic reader file.\n')
        for name in ('browser-reader/icons/icon-16.png', 'browser-reader/NOTES.md', 'browser-reader/extra.js',
                     'browser-reader/nested/worker.js', 'experiments/live-reader-spike/tests/fake_browser.cjs',
                     'experiments/live-reader-spike/extension/worker.js', 'docs/internal/READER-PIPELINE-PROOF-BRIEF.md'):
            self.write(name, 'Synthetic unlisted reader-adjacent file.\n')
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

    def test_desktop_files_are_listed_one_by_one_and_the_reader_module_is_required(self):
        output, _ = make_release.export(self.root, self.output, '0.1.0-preview.1')
        for name in ('reader_checks.py', 'desktop/launch.py', 'desktop/requirements-build.txt', 'docs/DESKTOP.md',
                     'tools/build_desktop.py', 'tools/make_release.py', '.github/workflows/desktop-candidate.yml'):
            self.assertTrue((output / name).is_file(), name)
        for name in ('desktop/private-notes.md', 'desktop/build/Resonance.spec', 'tools/other_tool.py',
                     'experiments/trial.py', '.github/workflows/other.yml', 'docs/other.md', 'docs/internal/notes.md'):
            self.assertFalse((output / name).exists(), name)
            self.assertFalse(make_release.public_path(name), name)
        self.git('rm', 'reader_checks.py')
        self.commit()
        with self.assertRaisesRegex(ValueError, 'reader_checks.py'):
            make_release.export(self.root, self.root.parent / 'second', '0.1.0-preview.1')

    def test_reader_package_is_listed_file_by_file_and_experiments_stay_private(self):
        listed = {'browser-reader/' + name for name in ('README.md', 'bridge.js', 'manifest.json', 'options.html', 'options.js',
                                                        'popup.html', 'popup.js', 'reader.js', 'shared.js', 'worker.js',
                                                        'icon-16.png', 'icon-32.png', 'icon-128.png')}
        self.assertEqual(make_release.PUBLIC_READER, listed)
        output, _ = make_release.export(self.root, self.output, '0.1.0-preview.1')
        exported = {path.relative_to(output).as_posix() for path in output.rglob('*') if path.is_file()}
        self.assertEqual({name for name in exported if name.startswith('browser-reader/')}, listed)
        for name in sorted(listed) + ['tools/build_reader.py', 'tests/reader-fixtures/fake_browser.cjs',
                                      'tests/reader-fixtures/fixtures/routes.json']:
            self.assertTrue(make_release.public_path(name), name)
            self.assertTrue((output / name).is_file(), name)
        for name in ('browser-reader/icons/icon-16.png', 'browser-reader/NOTES.md', 'browser-reader/extra.js',
                     'browser-reader/nested/worker.js', 'experiments/live-reader-spike/tests/fake_browser.cjs',
                     'experiments/live-reader-spike/extension/worker.js', 'docs/internal/READER-PIPELINE-PROOF-BRIEF.md'):
            self.assertFalse(make_release.public_path(name), name)
            self.assertFalse((output / name).exists(), name)
        for name in ('browser-reader', 'browser-reader/', 'browser-reader/../STATUS.md', '/browser-reader/worker.js',
                     'tools/build_reader.pyc', 'tools/build_reader.py.bak', 'tools'):
            self.assertFalse(make_release.public_path(name), name)
        self.assertFalse(any(name.startswith(('experiments/', 'docs/internal/')) for name in exported))
        # The reader is required by name only through the explicit lists: nothing broadened to a whole tools/ or browser-reader/.
        self.assertFalse(make_release.public_path('tools/other_tool.py'))
        self.assertFalse(any(entry.endswith('/') or '*' in entry for entry in make_release.PUBLIC_READER | make_release.PUBLIC_TOOLS))

    def test_every_node_suite_runs_in_the_export_and_is_a_real_public_file(self):
        root = Path(__file__).resolve().parents[1]
        suites = make_release.NODE_SUITES
        self.assertEqual(len(suites), len(set(suites)))
        for name in ('tests/test_ui_actions.cjs', 'tests/test_ui_communication.cjs', 'tests/test_ui_today.cjs',
                     'tests/test_ui_reader.cjs', 'tests/test_browser_reader.cjs', 'tests/test_reader_discovery.cjs',
                     'tests/test_reader_pipeline.cjs'):
            self.assertIn(name, suites)
        for name in suites:
            self.assertTrue(make_release.public_path(name), name)
            self.assertTrue((root / name).is_file(), name + ' is missing from the source tree')
        # A new top-level Node suite cannot be added without being added to the export validation.
        on_disk = {'tests/' + path.name for path in (root / 'tests').glob('test_*.cjs')}
        self.assertEqual(on_disk - set(suites), set())
        commands = make_release.validation_commands()
        self.assertEqual(commands[0][1:], ['-m', 'unittest', 'discover', '-s', 'tests'])
        self.assertEqual([command for command in commands[1:]], [['node', name] for name in suites])
        # Nothing the suites need is outside the export: the harness is vendored under tests/, not read
        # from experiments/ (which a public export leaves out), and no suite quietly skips itself.
        for name in suites:
            source = (root / name).read_text(encoding='utf-8')
            # Report only the name: a regex assertion would print the whole suite source.
            self.assertFalse(re.search(r'''["']experiments["']''', source), name + ' reads its harness from experiments/')
            self.assertFalse(re.search(r'''console\.log\(\s*["'`]SKIP''', source), name + ' can skip silently')
        for name in ('tests/reader-fixtures/fake_browser.cjs', 'tests/reader-fixtures/dom_adapter.cjs', 'tests/reader-fixtures/fx.js',
                     'tests/reader-fixtures/fixtures/routes.json'):
            self.assertTrue((root / name).is_file(), name)
        for name in make_release.PUBLIC_READER:
            self.assertTrue((root / name).is_file(), name + ' is listed but missing')

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
