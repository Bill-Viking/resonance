"""Desktop launcher and packaging checks. Synthetic temporary data only; no window is ever opened."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
import zipfile

from desktop import launch
from tools import build_desktop

ROOT = Path(__file__).resolve().parents[1]


class FakeUi:
    def __init__(self, events):
        self.events, self.errors, self.callbacks = events, [], None

    def show_error(self, message):
        self.errors.append(message)
        self.events.append('error')

    def show_running(self, url, open_callback, quit_callback):
        self.events.append('window')
        self.url, self.callbacks = url, (open_callback, quit_callback)

    def mainloop(self):
        self.events.append('mainloop')
        self.callbacks[1]()  # The person presses Quit.

    def close(self):
        self.events.append('close')


class FakeServer:
    server_address = ('127.0.0.1', 4317)

    def __init__(self, events):
        self.events, self.gate = events, threading.Event()

    def serve_forever(self):
        self.events.append('serving')
        self.gate.wait(5)

    def shutdown(self):
        self.events.append('shutdown')
        self.gate.set()

    def server_close(self):
        self.events.append('closed')


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.events = []
        self.log = launch.Log(self.directory / 'logs' / 'launcher.log')

    def controller(self, **options):
        options.setdefault('opener', lambda url: self.events.append('open ' + url) or True)
        return launch.Controller(FakeUi(self.events), self.log, **options)

    def test_browser_opens_only_after_the_server_is_bound_and_quit_stops_it(self):
        def make_server(directory, port):
            self.events.append(f'bind {directory} {port}')
            return FakeServer(self.events)
        controller = self.controller(make_server=make_server)
        self.assertEqual(controller.run(), 0)
        self.assertEqual(self.events[0], 'bind None 4317')  # Data folder is resolved by the server, at run.
        self.assertLess(self.events.index('bind None 4317'), self.events.index('open http://127.0.0.1:4317'))
        self.assertLess(self.events.index('window'), self.events.index('mainloop'))
        self.assertEqual(self.events[-2:], ['shutdown', 'closed'])
        controller.shutdown()  # A second stop does nothing.
        self.assertEqual(self.events.count('shutdown'), 1)
        self.assertIn('stopped', self.log.path.read_text())

    def test_failed_start_shows_a_plain_message_and_never_opens_the_browser(self):
        def make_server(directory, port):
            raise sqlite3.DatabaseError('SYNTHETIC post text that must stay out of the log')
        controller = self.controller(make_server=make_server)
        self.assertEqual(controller.run(), 1)
        self.assertEqual(self.events, ['error'])
        message = controller.ui.errors[0]
        self.assertIn(str(self.log.path), message)
        self.assertNotIn('Traceback', message)
        self.assertNotIn('SYNTHETIC', message)
        logged = self.log.path.read_text()
        self.assertIn('DatabaseError', logged)
        self.assertNotIn('SYNTHETIC', logged)

    def test_occupied_address_is_reported_and_the_other_listener_is_left_alone(self):
        with socket.socket() as other:
            other.bind(('127.0.0.1', 0))
            other.listen()
            port = other.getsockname()[1]
            controller = self.controller(data_dir=self.directory / 'data', port=port)
            self.assertEqual(controller.run(), 1)
            self.assertEqual(self.events, ['error'])
            self.assertIn('already in use', controller.ui.errors[0])
            self.assertFalse((self.directory / 'data').exists())  # Data is touched only after binding.
            with socket.create_connection(('127.0.0.1', port), timeout=2):
                pass  # The unknown listener still answers.

    def test_a_browser_that_does_not_open_or_raises_leaves_the_app_running(self):
        for opener in (lambda url: False, mock.Mock(side_effect=RuntimeError('SYNTHETIC'))):
            events = []
            controller = launch.Controller(FakeUi(events), self.log, opener=opener,
                                           make_server=lambda directory, port: FakeServer(events))
            self.assertEqual(controller.run(), 0)
            self.assertNotIn('error', events)
        self.assertNotIn('SYNTHETIC', self.log.path.read_text())

    def test_real_server_serves_a_synthetic_folder_then_releases_the_address_and_keeps_data(self):
        data = self.directory / 'data'
        controller = self.controller(data_dir=data, port=0)
        self.assertEqual(controller.run(), 0)
        port = controller.port
        self.assertEqual(self.events[-1], 'close')
        self.assertTrue(any(data.glob('*.sqlite3')))  # Saved data is retained after shutdown.
        with self.assertRaises(OSError):
            socket.create_connection(('127.0.0.1', port), timeout=1)
        self.assertEqual(self.events.count('open http://127.0.0.1:' + str(port)), 1)

    def test_headless_self_test_uses_only_the_given_synthetic_folder(self):
        result = launch.self_test(self.directory / 'data', 0, self.log)
        self.assertTrue(result['ok'])
        self.assertFalse(result['frozen'])
        self.assertTrue(result['seed'])

    def test_command_line_refuses_real_data_paths_without_the_self_test_flag(self):
        for argv in (['--data-dir', str(self.directory)], ['--port', '1'], ['--self-test']):
            with self.assertRaises(SystemExit) as stop, mock.patch.object(launch, 'TkUi') as window, \
                    contextlib.redirect_stderr(io.StringIO()):
                launch.main(argv)
            self.assertEqual(stop.exception.code, 2)
            window.assert_not_called()

    def test_importing_the_launcher_loads_no_window_toolkit_and_no_server(self):
        code = ('import sys; sys.path.insert(0, sys.argv[1]); import desktop.launch; '
                'print("tkinter" in sys.modules, "server" in sys.modules)')
        output = subprocess.check_output([sys.executable, '-c', code, str(ROOT)], cwd=self.directory, text=True)
        self.assertEqual(output.strip(), 'False False')

    def test_log_location_is_outside_the_data_folder_and_source(self):
        home = Path('/Users/example')
        self.assertEqual(launch.log_directory('darwin', {}, home), home / 'Library' / 'Logs' / 'Resonance')
        self.assertEqual(launch.log_directory('win32', {'LOCALAPPDATA': 'C:/Local'}, home),
                         Path('C:/Local') / 'Resonance Launcher')
        self.assertEqual(launch.log_directory('linux', {'XDG_STATE_HOME': '/state'}, home),
                         Path('/state') / 'resonance-launcher')


def synthetic_runtime(parent, extra=None, version='0.1.0-preview.1'):
    runtime = Path(parent) / 'runtime'
    files = {name: f'Synthetic {name}\n' for name in build_desktop.REQUIRED}
    files['VERSION'] = version + '\n'
    files['release.json'] = '{"github_owner": "example-owner"}\n'
    files.update(extra or {})
    manifest = {'version': version, 'files': {}}
    for name, text in files.items():
        path = runtime / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode())
        manifest['files'][name] = {'sha256': hashlib.sha256(text.encode()).hexdigest(), 'bytes': len(text.encode())}
    (runtime / 'RELEASE-MANIFEST.json').write_text(json.dumps(manifest))
    return runtime


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)
        self.runtime = synthetic_runtime(self.parent)
        self.tool_root = self.parent / 'tool-checkout'
        self.tool_root.mkdir()

    def verify(self):
        return build_desktop.verify_runtime(self.runtime, self.tool_root)

    def rewrite_manifest(self, change):
        path = self.runtime / 'RELEASE-MANIFEST.json'
        manifest = json.loads(path.read_text())
        change(manifest)
        path.write_text(json.dumps(manifest))

    def test_a_good_runtime_is_verified_and_git_and_caches_are_ignored(self):
        (self.runtime / '.git').mkdir()
        (self.runtime / '.git' / 'config').write_text('x')
        (self.runtime / '__pycache__').mkdir()
        (self.runtime / '__pycache__' / 'a.pyc').write_bytes(b'x')
        manifest, digest, contents = self.verify()
        self.assertEqual(manifest['version'], '0.1.0-preview.1')
        self.assertEqual(len(digest), 64)
        self.assertEqual(contents['VERSION'], b'0.1.0-preview.1\n')

    def test_refuses_changed_content_wrong_size_and_missing_files(self):
        path = self.runtime / 'static' / 'index.html'
        original = path.read_bytes()
        path.write_bytes(b'X' * len(original))
        with self.assertRaisesRegex(build_desktop.BuildError, 'does not match'):
            self.verify()
        path.write_bytes(original + b'!')
        with self.assertRaisesRegex(build_desktop.BuildError, 'does not match'):
            self.verify()
        path.unlink()
        with self.assertRaisesRegex(build_desktop.BuildError, 'missing'):
            self.verify()

    def test_refuses_a_missing_or_invalid_manifest(self):
        manifest = self.runtime / 'RELEASE-MANIFEST.json'
        for text in ('{', '[]', '{"version": "1", "files": {}}', '{"version": "0.1.0", "files": {}}',
                     '{"version": "0.1.0", "files": {}, "extra": 1}'):
            manifest.write_text(text)
            with self.assertRaisesRegex(build_desktop.BuildError, 'not valid'):
                self.verify()
        manifest.unlink()
        with self.assertRaisesRegex(build_desktop.BuildError, 'RELEASE-MANIFEST'):
            self.verify()

    def test_refuses_bad_entries_unsafe_paths_and_private_or_unlisted_names(self):
        base = json.loads((self.runtime / 'RELEASE-MANIFEST.json').read_text())
        entry = base['files']['VERSION']
        for name in ('../escape.txt', '/absolute.txt', 'static\\evil.js', 'C:/evil.js', 'static//x.js',
                     'static/./x.js', 'docs/internal/notes.md', 'experiments/x.py', 'STATUS.md',
                     'tools/other.py', 'unlisted.txt', 'static/x.sqlite3'):
            changed = json.loads(json.dumps(base))
            changed['files'][name] = entry
            (self.runtime / 'RELEASE-MANIFEST.json').write_text(json.dumps(changed))
            with self.assertRaises(build_desktop.BuildError, msg=name):
                self.verify()
        for bad in ({'sha256': 'ABC', 'bytes': 1}, {'sha256': entry['sha256']}, {'sha256': entry['sha256'], 'bytes': True},
                    {'sha256': entry['sha256'], 'bytes': -1}):
            changed = json.loads(json.dumps(base))
            changed['files']['VERSION'] = bad
            (self.runtime / 'RELEASE-MANIFEST.json').write_text(json.dumps(changed))
            with self.assertRaisesRegex(build_desktop.BuildError, 'invalid entry'):
                self.verify()

    def test_refuses_files_the_manifest_does_not_list_and_private_files_on_disk(self):
        for name in ('STATUS.md', 'docs/internal/AGENT-NOTES.md', 'static/extra.js'):
            path = self.runtime / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('Synthetic private note.')
            with self.assertRaisesRegex(build_desktop.BuildError, 'does not list'):
                self.verify()
            path.unlink()
        self.verify()

    @unittest.skipIf(os.name == 'nt', 'Creating symlinks requires extra Windows rights')
    def test_refuses_symlinks_to_files_and_folders_and_a_symlinked_runtime(self):
        target = self.parent / 'outside.txt'
        target.write_text('Synthetic outside file.')
        (self.runtime / 'static' / 'link.js').symlink_to(target)
        with self.assertRaisesRegex(build_desktop.BuildError, 'does not list|Symbolic'):
            self.verify()
        (self.runtime / 'static' / 'link.js').unlink()
        (self.runtime / 'linkdir').symlink_to(self.parent)
        with self.assertRaisesRegex(build_desktop.BuildError, 'Symbolic'):
            self.verify()
        (self.runtime / 'linkdir').unlink()
        version = self.runtime / 'VERSION'
        content = version.read_bytes()
        version.unlink()
        version.symlink_to(target)
        target.write_bytes(content)
        with self.assertRaisesRegex(build_desktop.BuildError, 'Symbolic'):
            self.verify()
        version.unlink()
        version.write_bytes(content)
        alias = self.parent / 'alias'
        alias.symlink_to(self.runtime)
        with self.assertRaisesRegex(build_desktop.BuildError, 'symbolic link'):
            build_desktop.verify_runtime(alias, self.tool_root)

    def test_refuses_the_source_working_checkout(self):
        with self.assertRaisesRegex(build_desktop.BuildError, 'source working checkout'):
            build_desktop.verify_runtime(self.tool_root, self.tool_root)
        nested = self.tool_root / 'inside'
        nested.mkdir()
        inside = synthetic_runtime(nested)
        with self.assertRaisesRegex(build_desktop.BuildError, 'source working checkout'):
            build_desktop.verify_runtime(inside, self.tool_root)
        # An export that carries its own manifest may build itself.
        build_desktop.verify_runtime(self.runtime, self.runtime)

    def test_plan_creates_nothing_and_shows_the_native_command(self):
        out = self.parent / 'candidate'
        lines = build_desktop.build(self.runtime, out, plan=True)
        text = '\n'.join(lines)
        for word in ('-m PyInstaller', '--onedir', '--windowed', '--noupx', '--collect-data tzdata', 'launch.py'):
            self.assertIn(word, text)
        self.assertNotIn('--upx', text.replace('--noupx', ''))
        self.assertFalse(out.exists())

    def test_refuses_overwrite_and_an_output_inside_the_runtime_or_checkout(self):
        existing = self.parent / 'existing'
        existing.mkdir()
        (existing / 'keep.txt').write_text('Synthetic original.')
        for plan in (True, False):
            with self.assertRaisesRegex(build_desktop.BuildError, 'new folder'):
                build_desktop.build(self.runtime, existing, plan=plan)
            with self.assertRaisesRegex(build_desktop.BuildError, 'outside'):
                build_desktop.build(self.runtime, self.runtime / 'out', plan=plan)
        self.assertEqual((existing / 'keep.txt').read_text(), 'Synthetic original.')
        with self.assertRaisesRegex(build_desktop.BuildError, 'outside'):
            build_desktop.build(self.runtime, ROOT / 'build-output', plan=True)

    def test_a_real_build_stops_before_writing_anything_when_tools_are_missing(self):
        out = self.parent / 'candidate'
        with mock.patch.object(build_desktop, 'toolchain_problems', return_value=['pyinstaller missing']), \
                mock.patch.object(build_desktop.subprocess, 'run', side_effect=AssertionError('ran a command')):
            with self.assertRaisesRegex(build_desktop.BuildError, 'pyinstaller missing'):
                build_desktop.build(self.runtime, out)
        self.assertFalse(out.exists())

    def test_toolchain_requires_the_pinned_versions(self):
        with mock.patch.object(build_desktop.importlib.metadata, 'version', return_value='0.0.1'):
            problems = '\n'.join(build_desktop.toolchain_problems())
        self.assertIn('pyinstaller==6.22.3', problems)
        self.assertIn('tzdata==2026.2', problems)
        pins = (ROOT / 'desktop' / 'requirements-build.txt').read_text()
        self.assertIn('pyinstaller==' + build_desktop.PYINSTALLER_VERSION, pins)
        self.assertIn('tzdata==' + build_desktop.TZDATA_VERSION, pins)

    def toolkit_problems(self, tkinter_module, platform_name='darwin'):
        """toolchain_problems() with every other pin satisfied and tkinter replaced by a stand-in."""
        pins = {'pyinstaller': build_desktop.PYINSTALLER_VERSION, 'tzdata': build_desktop.TZDATA_VERSION}
        with mock.patch.object(build_desktop.importlib.metadata, 'version', side_effect=pins.__getitem__), \
                mock.patch.object(build_desktop, 'target_label', return_value=platform_name), \
                mock.patch.dict(sys.modules, {'tkinter': tkinter_module}):
            return build_desktop.toolchain_problems()

    @staticmethod
    def fake_tkinter(tcl, tk):
        return mock.Mock(spec=['TclVersion', 'TkVersion'], TclVersion=tcl, TkVersion=tk)

    def test_toolchain_rejects_a_missing_tk_and_old_tcl_or_tk_such_as_apples_system_tk(self):
        self.assertEqual(self.toolkit_problems(self.fake_tkinter(8.6, 8.6)), [])
        # None in sys.modules makes `import tkinter` raise ImportError, as a Python built without Tk does.
        self.assertTrue(any('no Tk' in problem for problem in self.toolkit_problems(None)))
        for tcl, tk in ((8.5, 8.5), (8.5, 8.6), (8.6, 8.5), (8.4, 8.6), (8.6, 8.0)):
            problems = self.toolkit_problems(self.fake_tkinter(tcl, tk))
            self.assertEqual(len(problems), 1, msg=(tcl, tk))
            self.assertIn('own Tcl/Tk 8.6 or newer', problems[0])
            self.assertIn("not Apple's system", problems[0])
            found = problems[0].split('(', 1)[1].split(')', 1)[0]
            self.assertEqual(found, ', '.join(f'{name} {version}' for name, version in (('Tcl', tcl), ('Tk', tk))
                                              if version < 8.6), msg=(tcl, tk))
        for tcl, tk in ((8.6, 8.6), (8.6, 9.0), (9.0, 9.0), (9.0, 8.6)):
            self.assertEqual(self.toolkit_problems(self.fake_tkinter(tcl, tk)), [], msg=(tcl, tk))

    def test_toolkit_check_keeps_the_other_guards_and_never_builds_a_window(self):
        tk = self.fake_tkinter(8.6, 8.6)
        with mock.patch.object(build_desktop.importlib.metadata, 'version', return_value='0.0.1'), \
                mock.patch.object(build_desktop, 'target_label', return_value='mac'), \
                mock.patch.dict(sys.modules, {'tkinter': tk}):
            problems = '\n'.join(build_desktop.toolchain_problems())
        self.assertIn('pyinstaller==', problems)
        self.assertIn('tzdata==', problems)
        self.assertNotIn('Tcl/Tk', problems)
        # The stand-in has only the two version constants, so any Tk() or Tcl() call would have raised.
        self.assertEqual(tk.method_calls, [])

    def test_the_build_manifest_records_the_toolkit_versions(self):
        out = self.parent / 'candidate'

        def fake_run(command, cwd=None, **_):
            executable = build_desktop.executable_path(Path(cwd).parent / 'dist')
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b'synthetic program')

        def fake_zip(dist, archive, platform_name=None):
            with zipfile.ZipFile(archive, 'w') as bundle:
                bundle.writestr(('Resonance.app/' if sys.platform == 'darwin' else 'Resonance/') + 'x', 'x')

        for tcl, tk in ((8.6, 8.6), (9.0, 9.0)):
            with mock.patch.object(build_desktop, 'toolchain_problems', return_value=[]), \
                    mock.patch.dict(sys.modules, {'tkinter': self.fake_tkinter(tcl, tk)}), \
                    mock.patch.object(build_desktop.subprocess, 'run', side_effect=fake_run), \
                    mock.patch.object(build_desktop, 'smoke_test', return_value={}), \
                    mock.patch.object(build_desktop, 'zip_distribution', side_effect=fake_zip):
                folder, _ = build_desktop.build(self.runtime, out, mac_icon=False)
            record = json.loads((folder / 'BUILD-MANIFEST.json').read_text())
            self.assertEqual((record['tcl'], record['tk']), (str(tcl), str(tk)))
            self.assertEqual((record['pyinstaller'], record['tzdata']),
                             (build_desktop.PYINSTALLER_VERSION, build_desktop.TZDATA_VERSION))
            shutil.rmtree(out)

    def test_staging_ships_runtime_data_and_dynamic_modules_but_no_junk(self):
        runtime = synthetic_runtime(self.parent / 'second', {'tests/test_x.py': 'x', 'tools/make_release.py': 'x',
                                                          'mac-launcher/README.md': 'x', 'resonance': 'x'})
        _, _, contents = build_desktop.verify_runtime(runtime, self.tool_root)
        stage = self.parent / 'stage'
        root_files, directories = build_desktop.stage_sources(contents, stage)
        shipped = {path.relative_to(stage).as_posix() for path in stage.rglob('*') if path.is_file()}
        for name in ('server.py', 'samples.py', 'static/index.html', 'static/icon.svg', 'examples/practice.seed.json',
                     'VERSION', 'LICENSE', 'README.md', 'ASSISTANT.md', 'docs/MONITORING.md', 'desktop/launch.py',
                     'desktop/hooks/hook-workflow.py'):
            self.assertIn(name, shipped)
        for name in ('tests/test_x.py', 'tools/make_release.py', 'mac-launcher/README.md', 'resonance',
                     'RELEASE-MANIFEST.json'):
            self.assertNotIn(name, shipped)
        self.assertEqual(directories, ['docs', 'examples', 'static'])
        self.assertIn('server.py', root_files)

    def test_native_command_uses_each_systems_separator_and_only_the_mac_bundle_identifier(self):
        stage, work, dist = Path('/s'), Path('/w'), Path('/d')
        mac = build_desktop.pyinstaller_command(stage, work, dist, ['VERSION'], ['static'], 'darwin', 'python3',
                                                identifier='io.github.example.resonance')
        windows = build_desktop.pyinstaller_command(stage, work, dist, ['VERSION'], ['static'], 'win32', 'python')
        self.assertIn(str(stage / 'VERSION') + ':.', mac)
        self.assertIn(str(stage / 'VERSION') + ';.', windows)
        self.assertIn('--osx-bundle-identifier', mac)
        self.assertNotIn('--osx-bundle-identifier', windows)
        for command in (mac, windows):
            self.assertEqual(command[command.index('--additional-hooks-dir') + 1], str(stage / 'desktop' / 'hooks'))
            for flag in ('--onedir', '--windowed', '--noupx'):
                self.assertIn(flag, command)
            self.assertIn('server', command)
            self.assertEqual(command[-1], str(stage / 'desktop' / 'launch.py'))
        self.assertEqual(build_desktop.bundle_identifier(b'{"github_owner": "Example-Owner"}'),
                         'io.github.example-owner.resonance')

    def test_archive_keeps_paths_and_rejects_unexpected_ones(self):
        dist = self.parent / 'dist'
        (dist / 'Resonance' / '_internal').mkdir(parents=True)
        (dist / 'Resonance' / 'Resonance.exe').write_bytes(b'synthetic')
        (dist / 'Resonance' / '_internal' / 'data.bin').write_bytes(b'synthetic')
        archive = self.parent / 'out.zip'
        build_desktop.zip_distribution(dist, archive, 'win32')
        with zipfile.ZipFile(archive) as bundle:
            self.assertEqual(sorted(bundle.namelist()), ['Resonance/Resonance.exe', 'Resonance/_internal/data.bin'])
        self.assertEqual(build_desktop.check_archive(archive, 'win32'), 2)
        bad = self.parent / 'bad.zip'
        with zipfile.ZipFile(bad, 'w') as bundle:
            bundle.writestr('../escape.txt', 'x')
        with self.assertRaisesRegex(build_desktop.BuildError, 'unexpected paths'):
            build_desktop.check_archive(bad, 'win32')
        files, links = build_desktop.digest_tree(dist)
        self.assertEqual(files['Resonance/Resonance.exe']['bytes'], 9)
        self.assertEqual(links, {})

    def test_archive_and_build_names_state_the_target_and_architecture(self):
        name = build_desktop.archive_name('0.1.0-preview.1', 'win32')
        self.assertTrue(name.startswith('Resonance-0.1.0-preview.1-windows-'))
        self.assertTrue(name.endswith('-candidate.zip'))
        self.assertIn('-mac-', build_desktop.archive_name('0.1.0-preview.1', 'darwin'))

    def test_workflow_is_manual_unsigned_and_publishes_nothing(self):
        text = (ROOT / '.github' / 'workflows' / 'desktop-candidate.yml').read_text()
        self.assertIn('workflow_dispatch', text)
        self.assertNotRegex(text, r'(?m)^on:.*(push|pull_request|schedule)')
        self.assertIn("python-version: '3.11'", text)
        self.assertIn('macos-latest', text)
        self.assertIn('windows-latest', text)
        for forbidden in ('secrets.', 'contents: write', 'gh release', 'create-release', 'codesign', 'notarytool'):
            self.assertNotIn(forbidden, text)


if __name__ == '__main__':
    unittest.main()
