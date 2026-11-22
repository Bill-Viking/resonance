"""Public exports use only synthetic files in temporary repositories."""
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock

from tools import make_release


REQUIRED_DESKTOP_ADDITIONS = (
    'THIRD-PARTY-NOTICES.txt', 'desktop/third-party-sources.json', 'assistant_kit.py', 'tools/prepare_build.py',
    'desktop/build-targets.json', 'desktop/locks/bootstrap.txt', 'desktop/locks/macos-universal2.txt',
    'desktop/locks/windows-x86_64.txt', 'tools/sign_release.py', 'docs/SIGNING.md',
    '.github/workflows/desktop-sign.yml',
)
REVIEW_SKILL_FILES = frozenset((
    'skills/res-work-safe/SKILL.md',
    'skills/res-work-safe/agents/openai.yaml',
    'skills/res-work-safe/scripts/check_coverage.py',
    'skills/res-work-safe/scripts/discover_facebook.py',
    'skills/res-work-safe/scripts/capture_facebook_activity.js',
))
REVIEW_FILES = REVIEW_SKILL_FILES | {'docs/SAFE-WORK.md', 'docs/FACEBOOK-DISCOVERY.md'}


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'source'
        self.root.mkdir()
        self.output = self.root.parent / 'public'
        make_release.release_git(self.root, 'init', '-b', 'main', '--template=')
        for name in make_release.ROOT_FILES:
            self.write(name, '0.1.0-preview.1\n' if name == 'VERSION' else 'Synthetic public file.\n')
        for name in ('STATUS.md', 'OPENER.md', 'docs/internal/notes.md', 'unlisted.txt',
                     'skills/res-work-safe/SKILL.md', 'skills/res-work-safe/agents/openai.yaml',
                     'skills/res-work-safe/agents/extra.yaml', 'skills/res-work-safe/scripts/check_coverage.py',
                     'skills/res-work-safe/scripts/discover_facebook.py',
                     'skills/res-work-safe/scripts/capture_facebook_activity.js',
                     'skills/res-work-safe/scripts/private_helper.py',
                     'skills/res-work-safe/scripts/private_notes.json', 'skills/other/SKILL.md'):
            self.write(name, 'Synthetic internal note.\n')
        self.write('docs/SAFE-WORK.md', 'Synthetic public procedure.\n')
        self.write('docs/FACEBOOK-DISCOVERY.md', 'Synthetic public discovery procedure.\n')
        self.write('static/app.js', '"Synthetic app";\n')
        for name in ('desktop/launch.py', 'desktop/requirements-build.txt', 'docs/DESKTOP.md',
                     'tools/build_desktop.py', 'tools/make_release.py', '.github/workflows/desktop-candidate.yml',
                     '.claude/skills/resonance/SKILL.md', *REQUIRED_DESKTOP_ADDITIONS):
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
        return make_release.release_git(self.root, *args)

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
                     'tools/build_desktop.py', 'tools/make_release.py', '.github/workflows/desktop-candidate.yml',
                     *REQUIRED_DESKTOP_ADDITIONS):
            self.assertTrue((output / name).is_file(), name)
        for name in ('desktop/private-notes.md', 'desktop/build/Resonance.spec', 'tools/other_tool.py',
                     'experiments/trial.py', '.github/workflows/other.yml', 'docs/other.md', 'docs/internal/notes.md'):
            self.assertFalse((output / name).exists(), name)
            self.assertFalse(make_release.public_path(name), name)
        self.git('rm', 'reader_checks.py')
        self.commit()
        with self.assertRaisesRegex(ValueError, 'reader_checks.py'):
            make_release.export(self.root, self.root.parent / 'second', '0.1.0-preview.1')

    def test_missing_signing_inputs_prevent_export(self):
        for name in ('tools/sign_release.py', 'docs/SIGNING.md', '.github/workflows/desktop-sign.yml'):
            self.git('rm', name)
        self.commit()
        with self.assertRaises(ValueError) as error:
            make_release.export(self.root, self.output, '0.1.0-preview.1')
        for name in ('tools/sign_release.py', 'docs/SIGNING.md', '.github/workflows/desktop-sign.yml'):
            self.assertIn(name, str(error.exception))
        self.assertFalse(self.output.exists())

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

    def test_work_safe_skill_and_procedure_are_explicitly_packaged(self):
        self.assertEqual(make_release.PUBLIC_SKILLS, REVIEW_SKILL_FILES)
        output, _ = make_release.export(self.root, self.output, '0.1.0-preview.1')
        exported = {path.relative_to(output).as_posix() for path in output.rglob('*') if path.is_file()}
        self.assertTrue(REVIEW_FILES.issubset(exported))
        for name in REVIEW_FILES:
            self.assertTrue(make_release.public_path(name), name)
        for name in ('skills/res-work-safe/agents/extra.yaml', 'skills/res-work-safe/scripts/private_helper.py',
                     'skills/res-work-safe/scripts/private_notes.json', 'skills/other/SKILL.md',
                     'skills/res-work-safe', 'skills/res-work-safe/', 'skills/res-work-safe/../other/SKILL.md'):
            self.assertFalse(make_release.public_path(name), name)
            self.assertNotIn(name, exported)

    def test_missing_review_files_prevent_export(self):
        self.git('rm', *sorted(REVIEW_FILES))
        self.commit()
        with self.assertRaisesRegex(ValueError, 'Missing public files') as error:
            make_release.export(self.root, self.output, '0.1.0-preview.1')
        for name in REVIEW_FILES:
            self.assertIn(name, str(error.exception))
        self.assertFalse(self.output.exists())

    def test_export_ignores_ambient_identities_global_ignore_hooks_and_templates(self):
        self.git('update-index', '--chmod=+x', 'resonance')
        self.git('commit', '-m', 'Set synthetic executable mode')
        # core.filemode may be true on the source checkout, so align the fixture on POSIX.
        (self.root / 'resonance').chmod(0o755)
        version = '0.1.0-preview.1'
        home = self.root.parent / 'synthetic-home'
        template = home / 'template'
        hooks = template / 'hooks'
        hooks.mkdir(parents=True)
        hook = hooks / 'pre-commit'
        hook.write_text('#!/bin/sh\nexit 71\n')
        hook.chmod(0o755)
        (template / 'description').write_text('Synthetic external template must not be copied.\n')
        excludes = home / 'ignore'
        excludes.write_text('.claude/\n')
        config = home / '.gitconfig'
        config.write_text(f'[init]\n\ttemplateDir = "{template.as_posix()}"\n'
                          f'[core]\n\texcludesFile = "{excludes.as_posix()}"\n'
                          f'\thooksPath = "{hooks.as_posix()}"\n'
                          '[user]\n\tname = Synthetic Global\n\temail = global@example.invalid\n')
        with mock.patch.dict(os.environ, {
            'HOME': str(home), 'USERPROFILE': str(home), 'XDG_CONFIG_HOME': str(home / 'xdg'),
            'GIT_CONFIG_GLOBAL': str(config), 'GIT_CONFIG_SYSTEM': str(config),
            'GIT_TEMPLATE_DIR': str(template),
            'GIT_AUTHOR_NAME': 'Synthetic Author', 'GIT_AUTHOR_EMAIL': 'author@example.invalid',
            'GIT_COMMITTER_NAME': 'Synthetic Committer', 'GIT_COMMITTER_EMAIL': 'committer@example.invalid',
        }):
            output, expected = make_release._export(self.root, self.output, version)
            make_release.commit_export(output, version, expected)
        self.assertFalse((output / '.git' / 'hooks' / 'pre-commit').exists())
        self.assertFalse((output / '.git' / 'description').exists())
        self.assertEqual(make_release.git(output, 'remote'), b'')
        self.assertEqual(make_release.git(output, 'tag'), b'v0.1.0-preview.1\n')
        self.assertEqual(make_release.git(output, 'rev-list', '--count', 'HEAD'), b'1\n')
        tree = {name: mode for name, mode, _ in make_release.tree_entries(output)}
        self.assertIn('.claude/skills/resonance/SKILL.md', tree)
        self.assertEqual(tree['resonance'], b'100755')
        self.assertEqual(tree[make_release.MANIFEST_NAME], b'100644')
        self.assertEqual(make_release.git(output, 'show', '-s', '--format=%an <%ae>%n%cn <%ce>', 'HEAD'),
                         b'Resonance contributors <contributors@example.invalid>\n' * 2)

    def test_different_commit_is_never_tagged(self):
        version = '0.1.0-preview.1'
        original = make_release.release_git
        for case, message in (('extra', 'extra: unlisted.txt'), ('missing', 'missing: LICENSE'),
                              ('mode', 'bytes or mode differ: LICENSE'),
                              ('bytes', 'bytes or mode differ: RELEASE-MANIFEST.json'),
                              ('identity', 'synthetic author and committer')):
            with self.subTest(case=case):
                output, expected = make_release._export(self.root, self.root.parent / case, version)

                def changed_commit(repo, *args, **kwargs):
                    if args[0] == 'commit':
                        if case == 'extra':
                            (repo / 'unlisted.txt').write_text('Synthetic extra.')
                            original(repo, 'add', '--force', 'unlisted.txt')
                        elif case == 'missing':
                            original(repo, 'update-index', '--force-remove', 'LICENSE')
                        elif case == 'mode':
                            original(repo, 'update-index', '--chmod=+x', 'LICENSE')
                        elif case == 'bytes':
                            (repo / make_release.MANIFEST_NAME).write_text('Synthetic changed manifest.')
                            original(repo, 'add', '--force', make_release.MANIFEST_NAME)
                        else:
                            return original(repo, *args, '--author=Synthetic Override <override@example.invalid>')
                    return original(repo, *args, **kwargs)

                with mock.patch.object(make_release, 'release_git', side_effect=changed_commit):
                    with self.assertRaisesRegex(ValueError, message):
                        make_release.commit_export(output, version, expected)
                self.assertEqual(make_release.git(output, 'tag'), b'')

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


class ReleaseValidationFlowTests(unittest.TestCase):
    """Drive the real main() with a fake runner: no git, subprocesses or sleeping."""

    def run_main(self, platform, runner):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'public'
            output.mkdir()
            calls = []

            def fake_run(command, **kwargs):
                calls.append((command, kwargs))
                runner(command, kwargs)

            argv = ['make_release.py', '--version', '0.1.0-preview.1', '--out', str(output)]
            with mock.patch.object(make_release.sys, 'argv', argv), \
                    mock.patch.object(make_release.sys, 'platform', platform), \
                    mock.patch.object(make_release, '_export', return_value=(output, {'VERSION': ('x', False)})), \
                    mock.patch.object(make_release, 'commit_export') as commit, \
                    mock.patch.object(make_release.subprocess, 'run', side_effect=fake_run), \
                    mock.patch('sys.stderr'), mock.patch('sys.stdout'):
                try:
                    make_release.main()
                    code = 0
                except SystemExit as error:
                    code = error.code
            return code, calls, commit, output

    @staticmethod
    def modeled(python_seconds, node_seconds=1):
        def runner(command, kwargs):
            seconds = python_seconds if command[0] == make_release.sys.executable else node_seconds
            if seconds > kwargs['timeout']:
                raise make_release.subprocess.TimeoutExpired(command, kwargs['timeout'])
        return runner

    def test_python_suite_allowance_is_finite_and_platform_specific(self):
        for platform, expected in (('win32', 900), ('darwin', 300), ('linux', 300)):
            with self.subTest(platform=platform):
                code, calls, commit, output = self.run_main(platform, lambda command, kwargs: None)
                self.assertEqual(code, 0)
                self.assertEqual(len(make_release.NODE_SUITES), 7)
                self.assertEqual(len(calls), 8)
                self.assertEqual(calls[0][1]['timeout'], expected)
                self.assertEqual([call[1]['timeout'] for call in calls[1:]], [300] * 7)
                for command, kwargs in calls:
                    self.assertEqual(kwargs['cwd'], output)
                    self.assertIs(kwargs['stdin'], make_release.subprocess.DEVNULL)
                    self.assertIs(kwargs['check'], True)
                commit.assert_called_once()

    def test_slow_python_suite_passes_only_on_native_windows(self):
        code, calls, commit, _ = self.run_main('win32', self.modeled(430))
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 8)
        commit.assert_called_once()
        for platform in ('darwin', 'linux'):
            with self.subTest(platform=platform):
                code, calls, commit, _ = self.run_main(platform, self.modeled(430))
                self.assertEqual(code, 1)
                self.assertEqual(len(calls), 1)
                commit.assert_not_called()

    def test_timeouts_and_failures_never_reach_the_release_commit(self):
        def fail_on(is_python):
            def runner(command, kwargs):
                if (command[0] == make_release.sys.executable) == is_python:
                    raise make_release.subprocess.CalledProcessError(1, command)
            return runner

        cases = (('windows python over 900', 'win32', self.modeled(901), 1),
                 ('windows node over 300', 'win32', self.modeled(1, 301), 2),
                 ('mac node over 300', 'darwin', self.modeled(1, 301), 2),
                 ('windows python failure', 'win32', fail_on(True), 1),
                 ('linux node failure', 'linux', fail_on(False), 2))
        for name, platform, runner, expected_calls in cases:
            with self.subTest(name):
                code, calls, commit, _ = self.run_main(platform, runner)
                self.assertEqual(code, 1)
                self.assertEqual(len(calls), expected_calls)
                commit.assert_not_called()
