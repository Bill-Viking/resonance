"""Exercise assistant kit handling with synthetic manifests and temporary paths."""
from concurrent.futures import ThreadPoolExecutor
from email.message import Message
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import assistant_kit as kit
import server
from tools import build_desktop


REVIEW_FILES = (
    'docs/SAFE-WORK.md',
    'docs/FACEBOOK-DISCOVERY.md',
    'skills/res-work-safe/SKILL.md',
    'skills/res-work-safe/agents/openai.yaml',
    'skills/res-work-safe/scripts/check_coverage.py',
    'skills/res-work-safe/scripts/discover_facebook.py',
    'skills/res-work-safe/scripts/capture_facebook_activity.js',
)
COVERAGE_HELPER = 'skills/res-work-safe/scripts/check_coverage.py'
DISCOVERY_HELPER = 'skills/res-work-safe/scripts/discover_facebook.py'


class KitTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.bundle = self.home / 'bundle'
        self.output = self.home / 'output'
        self.contents = {name: b'Synthetic file\n' for name in kit.PUBLIC}
        self.contents['VERSION'] = b'1.2.3-preview.1\n'
        self.manifest = self.bundle / kit.MANIFEST_NAME
        self.raw = kit.make_manifest(self.contents)
        for name, content in self.contents.items():
            path = self.bundle / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        self.manifest.write_bytes(self.raw)

    def ensure(self):
        return kit.ensure(self.bundle, self.output, frozen=True)

    def publish_manifest(self, change):
        manifest = json.loads(self.raw)
        change(manifest)
        self.manifest.write_text(json.dumps(manifest), encoding='utf-8')

    def link(self, path, target, directory=False):
        try:
            path.symlink_to(target, target_is_directory=directory)
        except (OSError, NotImplementedError):
            self.skipTest('Creating symlinks is unavailable for this test account.')

    def test_manifest_selects_only_explicit_public_kit_files(self):
        extras = ('tests/test_synthetic.py', 'tools/example.py', 'docs/internal/private.md',
                  'static/private.json', 'examples/private.json', '.git/config', 'data/private.json',
                  'skills/res-work-safe/scripts/private_notes.json', 'skills/other/SKILL.md')
        self.assertEqual(kit.make_manifest(dict(self.contents, **dict.fromkeys(extras, b'example'))), self.raw)
        manifest = json.loads(self.raw)
        self.assertEqual(manifest['format'], 1)
        self.assertEqual(manifest['version'], '1.2.3-preview.1')
        self.assertEqual(set(manifest['files']), set(self.contents))
        self.assertEqual(manifest['files']['VERSION'], {'bytes': len(self.contents['VERSION']),
                         'sha256': hashlib.sha256(self.contents['VERSION']).hexdigest()})
        for bad in ('../ASSISTANT.md', '/ASSISTANT.md', 'docs\\ACTIONS.md', 'C:/notes.md', 'static//app.js'):
            with self.subTest(bad=bad), self.assertRaises(kit.KitError):
                kit.make_manifest({**self.contents, bad: b'example'})
        with self.assertRaises(kit.KitError):
            kit.make_manifest({name: raw for name, raw in self.contents.items() if name != 'ASSISTANT.md'})

    def test_source_returns_original_root_without_touching_the_destination(self):
        missing = self.home / 'missing'
        with mock.patch.dict(os.environ, {'RESONANCE_ASSISTANT_KIT_DIR': str(self.output)}):
            self.assertIs(kit.ensure(missing, frozen=False), missing)
        self.assertFalse(self.output.exists())

    def test_candidate_stages_and_publishes_real_helpers_for_read_only_cli(self):
        root = Path(__file__).resolve().parents[1]
        contents = {name: (root / name).read_bytes() for name in kit.PUBLIC | {build_desktop.NOTICES}}
        stage = self.home / 'stage'
        _, directories = build_desktop.stage_sources(contents, stage)
        self.assertIn('skills', directories)
        manifest = json.loads((stage / kit.MANIFEST_NAME).read_bytes())
        self.assertEqual(set(manifest['files']), kit.PUBLIC)
        target = kit.ensure(stage, self.output, frozen=True)
        for name in REVIEW_FILES:
            self.assertEqual((target / name).read_bytes(), contents[name], name)

        account = 'https://www.facebook.com/kit_example'
        source = 'https://www.facebook.com/parent_example/posts/1001?comment_id=2001'
        scope = 'Synthetic account posts and replies'
        snapshot = {
            'version': 1, 'account_url': account, 'scope_id': 'scope-1', 'scope': scope,
            'batch': {'id': 'batch-1', 'observed_at': '2026-11-12T15:00:00Z',
                      'page_url': 'https://www.facebook.com/123456/allactivity/?category_key=COMMENTS',
                      'category': 'comments', 'account_verified': True, 'people_filter': 'all',
                      'search_filter': 'none', 'boundary': 'end', 'boundary_note': 'Synthetic boundary observed',
                      'loading': False, 'unparsed_rows': 0,
                      'rows': [{'row_key': '2001', 'kind': 'comment', 'author_url': account,
                                'navigation_url': source}]},
        }
        snapshot_path = self.home / 'synthetic-snapshot.json'
        snapshot_bytes = json.dumps(snapshot).encode('utf-8')
        snapshot_path.write_bytes(snapshot_bytes)

        def run_helper(script, *args):
            result = subprocess.run([sys.executable, '-E', script, *args],
                                    cwd=target, stdin=subprocess.DEVNULL, capture_output=True,
                                    timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
            return result.stdout

        inventory = run_helper(DISCOVERY_HELPER, str(snapshot_path))
        self.assertTrue(json.loads(inventory)['discovery_complete'])
        inventory_path = self.home / 'synthetic-inventory.json'
        inventory_path.write_bytes(inventory)
        ledger = {
            'version': 2,
            'account_url': account,
            'scope_id': 'scope-1',
            'scope': scope,
            'surfaces': {name: {'status': 'complete', 'evidence': 'Synthetic boundary observed'}
                         for name in ('posts', 'comments_on_own_posts', 'comments_on_others_posts',
                                      'replies_on_own_posts', 'replies_on_others_posts', 'media')},
            'targets': [{'id': 'comment-1', 'source_url': source,
                         'author_verified': True, 'text_inspected': True, 'media': 'none',
                         'requested_action': 'review', 'state': 'reviewed',
                         'receipt_url': '', 'receipt_note': ''}],
            'unresolved': [],
        }
        input_path = self.home / 'synthetic-ledger.json'
        original = json.dumps(ledger).encode('utf-8')
        input_path.write_bytes(original)
        coverage = json.loads(run_helper(COVERAGE_HELPER, str(input_path), '--discovery',
                                         str(inventory_path)))
        self.assertTrue(coverage['complete'])
        self.assertEqual(coverage['counts']['reviewed_targets'], 1)
        self.assertEqual(snapshot_path.read_bytes(), snapshot_bytes)
        self.assertEqual(inventory_path.read_bytes(), inventory)
        self.assertEqual(input_path.read_bytes(), original)
        self.assertEqual(kit.ensure(stage, self.output, frozen=True), target)

    def test_missing_or_tampered_review_helper_is_rejected(self):
        with self.assertRaisesRegex(kit.KitError, 'required assistant kit file is missing'):
            kit.make_manifest({name: raw for name, raw in self.contents.items() if name != DISCOVERY_HELPER})
        self.publish_manifest(lambda manifest: manifest['files'].pop(DISCOVERY_HELPER))
        with self.assertRaises(kit.KitError):
            self.ensure()
        self.manifest.write_bytes(self.raw)
        helper = self.bundle / DISCOVERY_HELPER
        helper.unlink()
        with self.assertRaises(kit.KitError):
            self.ensure()
        helper.write_bytes(b'X' * len(self.contents[DISCOVERY_HELPER]))
        with self.assertRaises(kit.KitError):
            self.ensure()
        self.assertFalse(self.output.exists())

    def test_frozen_copies_exactly_the_manifest_files_into_digest_directory(self):
        (self.bundle / 'private.json').write_bytes(b'Synthetic private text')
        with mock.patch.dict(os.environ, {'RESONANCE_ASSISTANT_KIT_DIR': str(self.output),
                                         'RESONANCE_DATA_DIR': str(self.home / 'data')}):
            target = kit.ensure(self.bundle, frozen=True)
        self.assertEqual(target, self.output / ('1.2.3-preview.1-' + hashlib.sha256(self.raw).hexdigest()))
        self.assertEqual({file.relative_to(target).as_posix(): file.read_bytes()
                          for file in target.rglob('*') if file.is_file()}, self.contents)
        if os.name != 'nt':
            self.assertEqual(target.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.ensure(), target)
        self.assertFalse((self.home / 'data').exists())

    def test_bundle_hash_mutation_truncation_and_symlink_refuse_before_publication(self):
        path = self.bundle / 'ASSISTANT.md'
        for bad in (b'X' * len(self.contents['ASSISTANT.md']), b'short'):
            with self.subTest(bad=bad):
                path.write_bytes(bad)
                with self.assertRaisesRegex(kit.KitError, 'Repair the assistant folder'):
                    self.ensure()
                self.assertFalse(self.output.exists())
        outside = self.home / 'outside.md'
        outside.write_bytes(self.contents['ASSISTANT.md'])
        path.unlink()
        self.link(path, outside)
        with self.assertRaises(kit.KitError):
            self.ensure()
        self.assertFalse(self.output.exists())

    def test_mac_bundle_resources_can_cross_link_at_the_same_relative_path(self):
        contents = self.home / 'Example.app' / 'Contents'
        frameworks, resources = contents / 'Frameworks', contents / 'Resources'
        frameworks.mkdir(parents=True)
        shutil.move(str(self.bundle), resources)
        for child in resources.iterdir():
            self.link(frameworks / child.name, child, directory=child.is_dir())
        self.assertTrue((kit.ensure(frameworks, self.output, frozen=True) / 'docs' / 'ACTIONS.md').is_file())

    def test_invalid_manifest_entries_fail_without_a_partial_kit(self):
        for name in ('../escape', '/absolute', 'docs\\ACTIONS.md', 'static/unexpected.js', 'tools/monitor.py'):
            with self.subTest(name=name):
                self.publish_manifest(lambda m: m['files'].update({name: m['files']['ASSISTANT.md']}))
                with self.assertRaises(kit.KitError):
                    self.ensure()
        for change in (lambda m: m.update(format=True), lambda m: m.update(version='1.2.4'),
                       lambda m: m['files']['VERSION'].update(bytes=True),
                       lambda m: m['files']['VERSION'].update(sha256='not a digest'),
                       lambda m: m['files'].pop('monitor.py')):
            self.publish_manifest(change)
            with self.assertRaises(kit.KitError):
                self.ensure()
        for malformed in (self.raw[:-6], self.raw.replace(b'"format": 1', b'"format": 1, "format": 1')):
            self.manifest.write_bytes(malformed)
            with self.assertRaises(kit.KitError):
                self.ensure()
        self.assertFalse(self.output.exists())

    def test_changed_existing_kit_is_never_repaired_in_place(self):
        target = self.ensure()
        path = target / 'ASSISTANT.md'
        path.write_bytes(b'Altered')
        with self.assertRaises(kit.KitError):
            self.ensure()
        self.assertEqual(path.read_bytes(), b'Altered')
        path.write_bytes(self.contents['ASSISTANT.md'])
        extra = target / 'docs' / 'extra.md'
        extra.write_bytes(b'Extra')
        with self.assertRaises(kit.KitError):
            self.ensure()
        self.assertEqual(extra.read_bytes(), b'Extra')

    def test_same_version_different_manifests_are_kept_apart(self):
        first = self.ensure()
        content = b'Revised synthetic instructions\n'
        (self.bundle / 'ASSISTANT.md').write_bytes(content)
        raw = kit.make_manifest({**self.contents, 'ASSISTANT.md': content})
        self.manifest.write_bytes(raw)
        second = self.ensure()
        self.assertEqual(second.name, '1.2.3-preview.1-' + hashlib.sha256(raw).hexdigest())
        self.assertNotEqual(first, second)
        self.assertEqual((first / 'ASSISTANT.md').read_bytes(), self.contents['ASSISTANT.md'])
        self.assertEqual((second / 'ASSISTANT.md').read_bytes(), content)

    def test_output_symlink_ancestors_and_target_are_refused(self):
        real = self.home / 'real'
        real.mkdir()
        self.link(self.output, real, directory=True)
        with self.assertRaises(kit.KitError):
            kit.ensure(self.bundle, self.output / 'nested', frozen=True)
        self.assertEqual(list(real.iterdir()), [])
        if os.name == 'nt':
            self.output.rmdir()
        else:
            self.output.unlink()
        target = self.ensure()
        file = target / 'ASSISTANT.md'
        file.unlink()
        self.link(file, self.bundle / 'ASSISTANT.md')
        with self.assertRaises(kit.KitError):
            self.ensure()
        file.unlink()
        file.write_bytes(self.contents['ASSISTANT.md'])
        shutil.move(str(target), real / 'kit')
        self.link(target, real / 'kit', directory=True)
        with self.assertRaises(kit.KitError):
            self.ensure()

    def test_cloud_repository_bundle_and_data_destinations_are_refused(self):
        repo, data = self.home / 'repo', self.home / 'data'
        repo.mkdir()
        (repo / '.git').touch()  # Worktrees have a .git file.
        destinations = (repo / 'kit', self.home / 'OneDrive - Example' / 'kit',
                        self.bundle / 'kit', data / 'kit', self.home)
        for path in destinations:
            with self.subTest(path=path), self.assertRaises(kit.KitError):
                kit.ensure(self.bundle, path, frozen=True, data_directory=data)
        with mock.patch.dict(os.environ, {'RESONANCE_DATA_DIR': str(data)}):
            with self.assertRaises(kit.KitError):
                kit.ensure(self.bundle, data / 'kit', frozen=True)
        if sys.platform in ('darwin', 'win32'):
            with self.assertRaises(kit.KitError):
                kit.ensure(self.bundle, self.home / 'DATA' / 'kit', frozen=True, data_directory=data)

    def test_publish_collision_with_an_empty_directory_does_not_replace_it(self):
        original = kit._publish

        def collide(stage, target):
            target.mkdir()
            original(stage, target)

        with mock.patch.object(kit, '_publish', side_effect=collide), self.assertRaises(kit.KitError):
            self.ensure()
        target, = self.output.iterdir()
        self.assertEqual(list(target.iterdir()), [])

    def guarded_mkdir(self, refused):
        """Make mkdir fail as a Windows drive root does, unless the folder is genuinely new."""
        original = Path.mkdir

        def mkdir(path, *args, **kwargs):
            if path in refused:
                raise PermissionError(13, 'Access is denied', str(path))
            return original(path, *args, **kwargs)

        return mock.patch.object(Path, 'mkdir', autospec=True, side_effect=mkdir)

    def test_existing_ancestors_are_validated_not_created(self):
        destination = self.home / 'existing' / 'ancestor' / 'kits'
        destination.parent.mkdir(parents=True)
        existing = {self.home, destination.parent.parent, destination.parent, *self.home.parents}
        with self.guarded_mkdir(existing):
            target = kit.ensure(self.bundle, destination, frozen=True)
        self.assertEqual(target.parent, destination)
        self.assertEqual({path.relative_to(target).as_posix() for path in target.rglob('*') if path.is_file()},
                         set(self.contents))

    def test_permission_errors_for_missing_folders_still_fail(self):
        destination = self.home / 'existing' / 'missing' / 'kits'
        (self.home / 'existing').mkdir()
        with self.guarded_mkdir({self.home / 'existing' / 'missing'}), self.assertRaises(kit.KitError):
            kit.ensure(self.bundle, destination, frozen=True)
        self.assertFalse((self.home / 'existing' / 'missing').exists())
        # A denied creation must not be worked around by changing permissions.
        with mock.patch.object(Path, 'chmod', side_effect=AssertionError('chmod must not be used')):
            with self.guarded_mkdir({self.home / 'existing' / 'missing'}), self.assertRaises(kit.KitError):
                kit.ensure(self.bundle, destination, frozen=True)

    def test_folder_created_concurrently_is_validated(self):
        base = self.home / 'racing' / 'kits'
        original = Path.mkdir

        def racing(path, *args, **kwargs):
            if path == base.parent:
                original(path)  # Another publisher wins the race for this component.
            return original(path, *args, **kwargs)

        with mock.patch.object(Path, 'mkdir', autospec=True, side_effect=racing):
            target = kit.ensure(self.bundle, base, frozen=True)
        self.assertEqual(target.parent, base)

        # A competing creator that leaves a non-folder or a redirect is refused, not used.
        real = self.home / 'real'
        real.mkdir()
        for name, collide in (
                ('file', lambda path: path.write_bytes(b'x')),
                ('link', lambda path: self.link(path, real, directory=True))):
            destination = self.home / f'{name}-race' / 'kits'

            def collision(path, *args, _parent=destination.parent, _collide=collide, **kwargs):
                if path == _parent:
                    _collide(path)
                    raise FileExistsError(17, 'File exists', str(path))
                return original(path, *args, **kwargs)

            with self.subTest(name=name), mock.patch.object(Path, 'mkdir', autospec=True, side_effect=collision):
                with self.assertRaises(kit.KitError):
                    kit.ensure(self.bundle, destination, frozen=True)
        self.assertEqual(list(real.iterdir()), [])

    def test_existing_non_directory_ancestor_is_refused(self):
        blocker = self.home / 'blocker'
        blocker.write_bytes(b'not a folder')
        with self.assertRaises(kit.KitError):
            kit.ensure(self.bundle, blocker / 'kits', frozen=True)
        self.assertEqual(blocker.read_bytes(), b'not a folder')

    def test_concurrent_publishers_revalidate_the_same_kit(self):
        from threading import Barrier

        rendezvous = Barrier(4, timeout=10)
        original = kit._publish

        def together(stage, target):
            rendezvous.wait()
            original(stage, target)

        with mock.patch.object(kit, '_publish', side_effect=together), ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(self.ensure) for _ in range(4)]
            targets = {future.result(timeout=15) for future in futures}
        self.assertEqual(set(self.output.iterdir()), targets)
        self.assertEqual(len(targets), 1)

    def assistant_response(self, frozen, destination=None):
        handler = object.__new__(server.Handler)
        handler.server = SimpleNamespace(server_address=('127.0.0.1', 4317),
                                         store=SimpleNamespace(directory=self.home / 'data'),
                                         assistant_kit_destination=destination)
        handler.headers = Message()
        handler.headers['Host'] = '127.0.0.1:4317'
        handler.path, handler.command = '/api/assistant', 'GET'
        handler.requestline, handler.request_version = 'GET /api/assistant HTTP/1.1', 'HTTP/1.1'
        handler.wfile = BytesIO()
        with mock.patch.object(sys, 'frozen', frozen, create=True), mock.patch.object(server, 'ROOT', self.bundle):
            handler.do_GET()
        headers, _, body = handler.wfile.getvalue().partition(b'\r\n\r\n')
        return int(headers.split(b' ', 2)[1]), json.loads(body)

    def test_assistant_prompt_uses_source_or_frozen_kit_and_safe_error(self):
        self.assertEqual(self.assistant_response(False),
                         (200, {'prompt': f'Read ASSISTANT.md in {self.bundle} and run my Resonance check.'}))
        self.assertFalse(self.output.exists())
        status, body = self.assistant_response(True, self.output)
        target, = self.output.iterdir()
        self.assertEqual((status, body),
                         (200, {'prompt': f'Read ASSISTANT.md in {target} and run my Resonance check.'}))
        (target / 'ASSISTANT.md').write_bytes(b'Synthetic private text')
        self.assertEqual(self.assistant_response(True, self.output), (500, {'error': kit.REPAIR}))
        self.assertEqual(self.assistant_response(True, self.home / 'data' / 'kit'), (500, {'error': kit.REPAIR}))

    def test_monitor_help_does_not_write_bytecode_even_when_enabled(self):
        root = Path(__file__).resolve().parents[1]
        for name in kit.PUBLIC:
            if name.endswith('.py'):
                shutil.copyfile(root / name, self.bundle / name)
        environment = {key: value for key, value in os.environ.items() if not key.startswith('PYTHON')}
        result = subprocess.run([sys.executable, '-E', '-s', 'monitor.py', '--help'], cwd=self.bundle,
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20,
                                env=environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--profile', result.stdout)
        self.assertFalse(list(self.bundle.rglob('*.pyc')))
