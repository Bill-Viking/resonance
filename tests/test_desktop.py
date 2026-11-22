"""Desktop launcher and packaging checks. Synthetic temporary data only; no window is ever opened."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import socket
import sqlite3
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
import zipfile
import zlib

import assistant_kit
from desktop import launch
from tools import build_desktop, prepare_build

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
        tcl_modules = {'tkinter', '_tkinter'}
        already_loaded = tcl_modules.intersection(sys.modules)
        report = self.directory / 'result.json'
        bootstrap = ("import runpy, sys; sys.path.insert(0, sys.argv.pop(1)); "
                     "runpy.run_module('desktop.launch', run_name='__main__', alter_sys=True)")
        completed = subprocess.run(
            [sys.executable, '-I', '-B', '-c', bootstrap, str(ROOT),
             '--self-test', '--data-dir', str(self.directory / 'data'), '--port', '0',
             '--log-dir', str(self.directory / 'logs'), '--self-test-report', str(report)],
            cwd=self.directory, env=prepare_build.clean_environment(), stdin=subprocess.DEVNULL,
            capture_output=True, close_fds=True, timeout=30)
        self.assertEqual(tcl_modules.intersection(sys.modules) - already_loaded, set())
        self.assertEqual(completed.returncode, 0, (completed.stdout, completed.stderr))
        self.assertTrue(stat.S_ISREG(report.lstat().st_mode))
        with report.open('rb') as stream:
            raw = stream.read(65537)
        self.assertLessEqual(len(raw), 65536)
        result = json.loads(raw)
        self.assertIsInstance(result, dict)
        self.assertTrue(result['ok'])
        self.assertFalse(result['frozen'])
        self.assertTrue(result['seed'])
        self.assertIs(result['tcl'], True)
        self.assertRegex(result['tcl_patch'], r'\A[0-9]+\.[0-9]+\.[0-9]+\Z')
        self.assertIs(result['assistant_kit'], True)
        self.assertEqual(result['version'], (ROOT / 'VERSION').read_text(encoding='utf-8').strip())

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


def synthetic_icon():
    def chunk(kind, data):
        return (struct.pack('>I', len(data)) + kind + data +
                struct.pack('>I', zlib.crc32(kind + data)))

    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 128, 128, 8, 6, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(bytes(128 * (1 + 128 * 4)))) + chunk(b'IEND', b''))


def write_mac_sidecar(bundle, target, attribute):
    parent, _, leaf = target.rpartition('/')
    info = zipfile.ZipInfo(f'{parent}/._{leaf}')
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    attribute += b'\0'
    value = b'synthetic'
    offset = 131 + len(attribute)
    size = offset + len(value)
    raw = (bytes.fromhex('00051607000200004d6163204f53205820202020202020200002')
           + struct.pack('>6I', 9, 50, size - 50, 2, size, 0) + bytes(34)
           + struct.pack('>4s7I2H', b'ATTR', 0, size, offset, len(value), 0, 0, 0, 0, 1)
           + struct.pack('>IIHB', offset, len(value), 0, len(attribute)) + attribute + value)
    bundle.writestr(info, raw)


def synthetic_runtime(parent, extra=None, version='0.1.0-preview.1'):
    runtime = Path(parent) / 'runtime'
    files = {name: f'# Synthetic {name}\n' for name in build_desktop.REQUIRED | assistant_kit.REQUIRED}
    files['VERSION'] = version + '\n'
    files['release.json'] = '{"github_owner": "example-owner"}\n'
    files['requirements.txt'] = 'tzdata==2026.2; sys_platform == "win32"\n'
    files['examples/practice.seed.json'] = '{"version": 1, "account": {}, "posts": []}\n'
    files['static/index.html'] = '<!doctype html><title>Synthetic fixture</title>\n'
    files['static/icon.svg'] = '<svg xmlns="http://www.w3.org/2000/svg"/>\n'
    files[build_desktop.WINDOWS_ICON_SOURCE] = synthetic_icon()
    files.update(extra or {})
    manifest = {'version': version, 'files': {}}
    for name, content in files.items():
        raw = content.encode() if isinstance(content, str) else content
        path = runtime / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        manifest['files'][name] = {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
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
        with mock.patch.object(build_desktop, 'locked_toolchain',
                               side_effect=build_desktop.BuildError('isolated venv required')), \
                mock.patch.object(build_desktop.subprocess, 'run', side_effect=AssertionError('ran a command')):
            with self.assertRaisesRegex(build_desktop.BuildError, 'isolated venv required'):
                build_desktop.build(self.runtime, out)
        self.assertFalse(out.exists())

    def test_bad_release_metadata_and_mismatched_runtime_tzdata_fail_before_planning(self):
        malformed = [b'', b'{', b'\xff', b'null', b'[]', b'{}', b'{"github_owner": 5}',
                     b'{"github_owner": "bad--owner"}', b'{"github_owner": "-owner"}',
                     b'{"github_owner": "bad.owner"}',
                     b'{"github_owner": "example", "github_owner": "other"}']
        for value in malformed:
            with self.subTest(value=value), self.assertRaisesRegex(build_desktop.BuildError, 'github_owner'):
                build_desktop.bundle_identifier(value)
        for pins in ('', 'tzdata==2026.1; sys_platform == "win32"', 'tzdata>=2026.2',
                     'tzdata==2026.2; sys_platform == "darwin"'):
            with self.subTest(pins=pins), self.assertRaisesRegex(build_desktop.BuildError, 'tzdata==2026.2'):
                build_desktop.check_runtime_tzdata(pins.encode())
        runtime = synthetic_runtime(self.parent / 'second', {'requirements.txt': 'tzdata==2000.1'})
        out = self.parent / 'candidate'
        with self.assertRaisesRegex(build_desktop.BuildError, 'tzdata==2026.2'):
            build_desktop.build(runtime, out, plan=True)
        self.assertFalse(out.exists())

    def toolkit(self, tcl, tk, *, script=None, supplied=None):
        tk_module = mock.Mock(spec=['Tcl', 'TclVersion', 'TkVersion', 'TclError'],
                              TclVersion=float('.'.join(tcl.split('.')[:2])), TkVersion=tk, TclError=RuntimeError)
        tk_module.Tcl.return_value.call.return_value = tcl
        pyinstaller_hook = mock.Mock(tcltk_info=mock.Mock(tk_data_dir=script))
        with mock.patch.dict(sys.modules, {'tkinter': tk_module,
                                          'PyInstaller.utils.hooks.tcl_tk': pyinstaller_hook}):
            result = build_desktop.toolkit_versions(supplied)
        tk_module.Tcl.assert_called_once_with()
        tk_module.Tcl.return_value.call.assert_called_once_with('info', 'patchlevel')
        return result

    def test_toolchain_rejects_missing_tk_and_old_tcl_or_tk_without_making_a_window(self):
        with mock.patch.dict(sys.modules, {'tkinter': None}):
            with self.assertRaisesRegex(build_desktop.BuildError, 'no Tk'):
                build_desktop.toolkit_versions()
        for tcl, tk in (('8.5.9', 8.5), ('8.5.9', 8.6), ('8.6.17', 8.5), ('8.4.1', 8.6), ('8.6.17', 8.0)):
            with self.subTest(tcl=tcl, tk=tk):
                with self.assertRaisesRegex(build_desktop.BuildError, 'own Tcl/Tk 8.6 or newer'):
                    self.toolkit(tcl, tk)

    def test_tk_patch_comes_from_scripts_or_explicit_evidence_and_unknown_stays_unknown(self):
        library = self.parent / 'tk'
        library.mkdir()
        for tcl, tk, declaration in (('8.6.16', 8.6, 'Tk  8.6.17'), ('9.0.3', 9.0, 'tk  9.0.4')):
            (library / 'tk.tcl').write_text('# Synthetic initialization script\npackage require -exact ' + declaration)
            result = self.toolkit(tcl, tk, script=str(library))
            self.assertEqual(result['tcl'], tcl)
            self.assertEqual(result['tk'], declaration.split()[-1])
            self.assertEqual(result['tk_patch_source'], 'tk.tcl exact package requirement')
        with self.assertRaisesRegex(build_desktop.BuildError, 'supplied Tk patch level'):
            self.toolkit('9.0.3', 9.0, script=str(library), supplied='9.0.5')
        with self.assertRaisesRegex(build_desktop.BuildError, 'supplied Tk patch level'):
            self.toolkit('9.0.3', 9.0, supplied='9.0')
        self.assertEqual(self.toolkit('8.6.17', 8.6, supplied='8.6.17')['tk_patch_source'], 'caller supplied')
        result = self.toolkit('8.6.17', 8.6)
        self.assertIsNone(result['tk'])
        self.assertEqual(result['tk_patch_source'], 'unavailable headlessly')

    def test_smoke_requires_the_temporary_json_report_even_without_stdout(self):
        version = '0.1.0-preview.1'
        good = {'ok': True, 'frozen': True, 'seed': True, 'assistant_kit': True, 'version': version,
                'tcl': True, 'tcl_patch': '9.0.4'}
        seen = []

        def fake_run(command, cwd, env, **options):
            self.assertIn('--self-test', command)
            self.assertEqual(command[command.index('--port') + 1], '0')
            self.assertEqual(Path(command[command.index('--data-dir') + 1]), cwd / 'data')
            self.assertEqual(Path(command[command.index('--log-dir') + 1]), cwd / 'logs')
            report = Path(command[command.index('--self-test-report') + 1])
            self.assertEqual(report, cwd / 'result.json')
            self.assertNotIn('RESONANCE_DATA_DIR', env)
            self.assertEqual(options['stdin'], subprocess.DEVNULL)
            seen.append(cwd)
            if payload is not None:
                report.write_text(payload)
            return subprocess.CompletedProcess(command, returncode, stdout=b'', stderr=b'')

        with mock.patch.object(build_desktop.subprocess, 'run', side_effect=fake_run), \
                mock.patch.dict(os.environ, {'RESONANCE_DATA_DIR': str(self.parent / 'unused')}):
            payload, returncode = json.dumps(good), 0
            self.assertEqual(build_desktop.smoke_test(self.parent / 'unused', version), good)
            for payload, returncode in ((None, 0), ('{', 0), ('[]', 0), (json.dumps(good), 1),
                                        (json.dumps(dict(good, frozen=1)), 0),
                                        (json.dumps(dict(good, assistant_kit=False)), 0),
                                        (json.dumps(dict(good, version='9.0.0')), 0)):
                with self.assertRaisesRegex(build_desktop.BuildError, 'self-test'):
                    build_desktop.smoke_test(self.parent / 'unused', version)
            for bad_tcl in ({'tcl': 1}, {'tcl': False}, {'tcl_patch': 904}, {'tcl_patch': '9.0'},
                            {'tcl_patch': '9.0.4\n'}, {'tcl_patch': '9.0.4rc1'}, {'tcl_patch': '９.0.4'}):
                with self.subTest(bad_tcl=bad_tcl), self.assertRaisesRegex(build_desktop.BuildError, 'self-test'):
                    payload, returncode = json.dumps(dict(good, **bad_tcl)), 0
                    build_desktop.smoke_test(self.parent / 'unused', version)
            payload = json.dumps({key: value for key, value in good.items() if not key.startswith('tcl')})
            with self.assertRaisesRegex(build_desktop.BuildError, 'self-test'):
                build_desktop.smoke_test(self.parent / 'unused', version)
        self.assertTrue(all(not path.exists() for path in seen))

    def test_the_build_manifest_records_the_locked_toolchain_and_smoke_evidence(self):
        out = self.parent / 'candidate'
        smoke = {'ok': True, 'seed': True, 'frozen': True, 'assistant_kit': True, 'version': '0.1.0-preview.1',
                 'tcl': True, 'tcl_patch': '8.6.17'}
        notice_path = 'Resonance.app/Contents/Resources/THIRD-PARTY-NOTICES.txt'
        plist_name = 'Resonance.app/Contents/Info.plist'
        source_notice = (self.runtime / build_desktop.NOTICES).read_bytes()
        events = []
        attribute = b'com.apple.provenance'

        def fake_run(command, cwd=None, env=None, **_):
            plist_path = out / 'dist' / plist_name
            if command[0] == '/usr/bin/codesign':
                events.append('sign' if '--sign' in command else 'verify')
                if '--sign' in command:
                    self.assertEqual(command[command.index('--sign') + 1], '-')
                    self.assertNotIn('--deep', command)
                else:
                    self.assertTrue({'--verify', '--strict', '--deep'} <= set(command))
                plist = plistlib.loads(plist_path.read_bytes())
                self.assertEqual(plist['CFBundleIdentifier'], 'io.github.example-owner.resonance')
                self.assertEqual(plist['CFBundleShortVersionString'], '0.1.0')
                self.assertEqual(plist['CFBundleVersion'], '0.1.0')
                self.assertEqual(plist['ResonanceVersion'], smoke['version'])
                self.assertEqual(plist['LSMinimumSystemVersion'], '13.0')
                return
            events.append('build')
            self.assertEqual(list(Path(cwd).iterdir()), [])
            self.assertEqual(command[1:3], ['-E', '-s'])
            self.assertEqual(env['PYTHONNOUSERSITE'], '1')
            self.assertNotIn('PYTHONPATH', env)
            self.assertEqual(Path(env['PYINSTALLER_CONFIG_DIR']).parent, cwd.parent)
            self.assertIn(str(cwd.parent / 'stage' / assistant_kit.MANIFEST_NAME) + ':.', command)
            executable = build_desktop.executable_path(out / 'dist')
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b'synthetic program')
            notice = out / 'dist' / notice_path
            notice.parent.mkdir()
            notice.write_bytes(source_notice)
            plist_path.write_bytes(plistlib.dumps({'CFBundleIdentifier': 'io.github.example-owner.resonance',
                                                  'CFBundleIconFile': 'icon-windowed.icns'}))

        def fake_smoke(executable, version):
            events.append('smoke')
            return smoke

        def fake_zip(dist, archive, platform_name=None):
            events.append('archive')
            with zipfile.ZipFile(archive, 'w') as bundle:
                bundle.write(dist / plist_name, plist_name)
                bundle.write(dist / notice_path, notice_path)
                write_mac_sidecar(bundle, notice_path, attribute)

        details = {'version': '3.14.10', 'implementation': 'cpython', 'deployment_target': '11.0',
                   'host_version': '15.0'}
        target = mock.Mock(os='macOS', minimum_os='13.0', architecture='arm64')
        evidence = {'python_build': '3.14.10 (synthetic full build)', 'python_cache_tag': 'cpython-314',
                    'environment_lock_sha256': 'a' * 64, 'pip_freeze': ['pip==26.3', 'tzdata==2026.2']}
        toolkit = {'tcl': '8.6.17', 'tk': '8.6.16', 'tk_patch_source': 'tk.tcl exact package requirement'}
        with mock.patch.object(sys, 'platform', 'darwin'), \
                mock.patch.object(build_desktop, 'locked_toolchain', return_value=(details, target, evidence)), \
                mock.patch.object(build_desktop, 'probe_toolkit', return_value=toolkit), \
                mock.patch.object(build_desktop.subprocess, 'run', side_effect=fake_run), \
                mock.patch.object(build_desktop, 'smoke_test', side_effect=fake_smoke), \
                mock.patch.object(build_desktop, 'zip_distribution', side_effect=fake_zip), \
                mock.patch.dict(os.environ, {'PYTHONPATH': str(self.parent / 'unused')}):
            folder, _ = build_desktop.build(self.runtime, out, mac_icon=False)
            self.assertEqual(events, ['build', 'sign', 'verify', 'smoke', 'archive'])
            out = self.parent / 'quarantined'
            attribute = b'com.apple.quarantine'
            with self.assertRaisesRegex(build_desktop.BuildError, 'Unsupported Mac ZIP attributes'):
                build_desktop.build(self.runtime, out, mac_icon=False)
            self.assertFalse((out / 'BUILD-MANIFEST.json').exists())
            self.assertTrue((out / 'work').is_dir())
            self.assertTrue((out / build_desktop.archive_name(smoke['version'])).is_file())
        record = json.loads((folder / 'BUILD-MANIFEST.json').read_text())
        for key, value in {**toolkit, **evidence, 'target_os': 'macOS', 'minimum_os': '13.0',
                           'architecture': 'arm64', 'smoke_test': smoke, 'python_implementation': 'cpython',
                           'pyinstaller': build_desktop.PYINSTALLER_VERSION,
                           'tzdata': build_desktop.TZDATA_VERSION}.items():
            self.assertEqual(record[key], value, msg=key)
        metadata = record['native_metadata']
        self.assertEqual(metadata['signature'], 'ad-hoc verified')
        self.assertEqual(metadata['info_plist']['CFBundleIconFile'], 'icon-windowed.icns')
        self.assertEqual(metadata['info_plist_sha256'], hashlib.sha256((folder / 'dist' / plist_name).read_bytes()).hexdigest())
        notice_digest = {'sha256': hashlib.sha256(source_notice).hexdigest(), 'bytes': len(source_notice)}
        self.assertEqual(record['files'][notice_path], notice_digest)
        self.assertEqual(record['third_party_notices'], {'path': notice_path, **notice_digest})

    def test_staging_ships_runtime_data_and_dynamic_modules_but_no_junk(self):
        runtime = synthetic_runtime(self.parent / 'second', {'tests/test_x.py': 'x', 'tools/make_release.py': 'x',
                                    'mac-launcher/README.md': 'x', 'resonance': 'x',
                                    'static/unselected.js': 'x', 'examples/unselected.json': '{}',
                                    'static/app.js': '"Synthetic app";'})
        _, _, contents = build_desktop.verify_runtime(runtime, self.tool_root)
        (runtime / 'static' / 'app.js').write_text('Tampered after verification')
        stage = self.parent / 'stage'
        root_files, directories = build_desktop.stage_sources(contents, stage)
        shipped = {path.relative_to(stage).as_posix() for path in stage.rglob('*') if path.is_file()}
        for name in ('server.py', 'samples.py', 'static/index.html', 'static/icon.svg', 'examples/practice.seed.json',
                     'VERSION', 'LICENSE', 'README.md', 'ASSISTANT.md', 'docs/MONITORING.md', 'desktop/launch.py',
                     'desktop/hooks/hook-workflow.py', 'assistant_kit.py', 'static/app.js',
                     assistant_kit.MANIFEST_NAME, build_desktop.NOTICES):
            self.assertIn(name, shipped)
        for name in ('tests/test_x.py', 'tools/make_release.py', 'mac-launcher/README.md', 'resonance',
                     'RELEASE-MANIFEST.json', 'static/unselected.js', 'examples/unselected.json',
                     build_desktop.WINDOWS_ICON_SOURCE):
            self.assertNotIn(name, shipped)
        self.assertEqual(directories, ['docs', 'examples', 'skills', 'static'])
        self.assertIn('server.py', root_files)
        self.assertIn(assistant_kit.MANIFEST_NAME, root_files)
        kit_manifest = json.loads((stage / assistant_kit.MANIFEST_NAME).read_bytes())
        self.assertEqual(kit_manifest['version'], '0.1.0-preview.1')
        self.assertEqual(set(kit_manifest['files']), assistant_kit.REQUIRED | {'static/app.js'})
        destination = self.parent.resolve() / 'published-kits'
        kit = assistant_kit.ensure(stage, destination, frozen=True)
        self.assertEqual((kit / 'static/app.js').read_bytes(), b'"Synthetic app";')
        self.assertFalse((kit / build_desktop.NOTICES).exists())
        (stage / 'static/app.js').write_bytes(b'X' * len(contents['static/app.js']))
        with self.assertRaises(assistant_kit.KitError):
            assistant_kit.ensure(stage, destination, frozen=True)

    def test_native_command_uses_each_systems_separator_and_only_the_mac_bundle_identifier(self):
        stage, work, dist = Path('/s'), Path('/w'), Path('/d')
        mac = build_desktop.pyinstaller_command(stage, work, dist, ['VERSION'], ['static'], 'darwin', 'python3',
                                                identifier='io.github.example.resonance')
        windows = build_desktop.pyinstaller_command(stage, work, dist, ['VERSION'], ['static'], 'win32', 'python',
                                                    version_file=work / 'version.txt')
        self.assertIn(str(stage / 'VERSION') + ':.', mac)
        self.assertIn(str(stage / 'VERSION') + ';.', windows)
        self.assertIn('--osx-bundle-identifier', mac)
        self.assertNotIn('--osx-bundle-identifier', windows)
        self.assertNotIn('--version-file', mac)
        self.assertEqual(windows[windows.index('--version-file') + 1], str(work / 'version.txt'))
        for command in (mac, windows):
            self.assertEqual(command[command.index('--additional-hooks-dir') + 1], str(stage / 'desktop' / 'hooks'))
            for flag in ('--onedir', '--windowed', '--noupx'):
                self.assertIn(flag, command)
            self.assertIn('server', command)
            self.assertIn('assistant_kit', command)
            self.assertEqual(command[-1], str(stage / 'desktop' / 'launch.py'))
        self.assertEqual(build_desktop.bundle_identifier(b'{"github_owner": "Example-Owner"}'),
                         'io.github.example-owner.resonance')

    def test_windows_metadata_uses_verified_png_and_preserves_an_explicit_icon(self):
        details = {'version': '3.14.10', 'implementation': 'cpython', 'deployment_target': None,
                   'host_version': '10.0'}
        target = mock.Mock(os='Windows', minimum_os='10.0', architecture='x86_64')
        override = self.parent / 'custom.ico'
        override.write_bytes(b'Synthetic override')
        for name, icon in (('default', None), ('override', override)):
            with self.subTest(icon=name):
                out = self.parent / name
                png_path = self.runtime / build_desktop.WINDOWS_ICON_SOURCE
                original_png = png_path.read_bytes()
                audited = self.verify()
                png_path.write_bytes(b'Changed after verification')
                self.addCleanup(png_path.write_bytes, original_png)
                seen = {}

                def fake_run(command, **options):
                    ico = Path(command[command.index('--icon') + 1]).read_bytes()
                    if icon is None:
                        self.assertEqual(struct.unpack('<HHHBBBBHHII', ico[:22]),
                                         (0, 1, 1, 128, 128, 0, 0, 1, 32, len(original_png), 22))
                        self.assertEqual(ico[22:], original_png)
                    else:
                        self.assertEqual(ico, override.read_bytes())
                    resource = Path(command[command.index('--version-file') + 1]).read_bytes()
                    compile(resource, '<synthetic version resource>', 'eval')
                    self.assertIn(b'filevers=(0, 1, 0, 0), prodvers=(0, 1, 0, 0)', resource)
                    self.assertIn(b"StringStruct('ProductName', 'Resonance')", resource)
                    self.assertIn(b"StringStruct('ProductVersion', '0.1.0-preview.1')", resource)
                    seen.update(icon=hashlib.sha256(ico).hexdigest(), resource=hashlib.sha256(resource).hexdigest())
                    executable = build_desktop.executable_path(out / 'dist')
                    executable.parent.mkdir(parents=True)
                    executable.write_bytes(b'synthetic program')
                    notice = executable.parent / '_internal' / 'THIRD-PARTY-NOTICES.txt'
                    notice.parent.mkdir()
                    notice.write_bytes(audited[2][build_desktop.NOTICES])

                with mock.patch.object(sys, 'platform', 'win32'), \
                        mock.patch.object(build_desktop, 'verify_runtime', return_value=audited), \
                        mock.patch.object(build_desktop, 'locked_toolchain', return_value=(details, target, {})), \
                        mock.patch.object(build_desktop, 'probe_toolkit', return_value={}), \
                        mock.patch.object(build_desktop.subprocess, 'run', side_effect=fake_run), \
                        mock.patch.object(build_desktop, 'smoke_test', return_value={'assistant_kit': True}):
                    folder, _ = build_desktop.build(self.runtime, out, icon=icon)
                record = json.loads((folder / 'BUILD-MANIFEST.json').read_bytes())
                notice_path = 'Resonance/_internal/THIRD-PARTY-NOTICES.txt'
                notice_digest = audited[0]['files'][build_desktop.NOTICES]
                self.assertEqual(record['files'][notice_path], notice_digest)
                self.assertEqual(record['third_party_notices'], {'path': notice_path, **notice_digest})
                metadata = record['native_metadata']
                self.assertEqual(metadata['windows_version_resource']['input_sha256'], seen['resource'])
                self.assertEqual(metadata['windows_version_resource']['file_version'], [0, 1, 0, 0])
                self.assertEqual(metadata['windows_executable'], {
                    'path': 'Resonance/Resonance.exe', 'sha256': hashlib.sha256(b'synthetic program').hexdigest()})
                self.assertEqual(metadata['icon']['input_sha256'], seen['icon'])
                self.assertEqual(metadata['icon']['source'],
                                 'explicit override' if icon else build_desktop.WINDOWS_ICON_SOURCE)
                png_path.write_bytes(original_png)
        with self.assertRaisesRegex(build_desktop.BuildError, '16 bits'):
            build_desktop.windows_version_resource('1.65536.0')
        with self.assertRaisesRegex(build_desktop.BuildError, '128px RGBA PNG'):
            build_desktop.windows_icon(b'not an image')

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

    def test_frozen_notice_must_be_at_the_canonical_path_and_match_verified_bytes(self):
        notice = b'synthetic notice'
        dist = self.parent / 'dist'
        for system, name in (('darwin', 'Resonance.app/Contents/Resources/THIRD-PARTY-NOTICES.txt'),
                             ('win32', 'Resonance/_internal/THIRD-PARTY-NOTICES.txt')):
            path = dist / name
            path.parent.mkdir(parents=True)
            decoy = path.parent.parent / build_desktop.NOTICES
            decoy.write_bytes(notice)
            for condition in ('missing', 'tampered', 'symlink'):
                if condition == 'tampered':
                    path.write_bytes(b'Synthetic notice')  # Same size, different digest.
                if condition == 'symlink':
                    if os.name == 'nt':
                        continue  # Creating symlinks requires extra Windows rights.
                    path.unlink()
                    path.symlink_to(decoy)
                with self.subTest(system=system, condition=condition):
                    files, _ = build_desktop.digest_tree(dist)
                    with self.assertRaisesRegex(build_desktop.BuildError, 'regular notice file'):
                        build_desktop.check_packaged_notices(files, notice, system)

    def test_mac_zip_accepts_provenance_and_rejects_other_attributes_on_a_valid_path(self):
        archive = self.parent / 'mac.zip'
        path = 'Resonance.app/Contents/Resources/THIRD-PARTY-NOTICES.txt'
        for attribute in (b'com.apple.provenance', b'com.apple.quarantine', b'com.apple.FinderInfo'):
            with self.subTest(attribute=attribute):
                with zipfile.ZipFile(archive, 'w') as bundle:
                    bundle.writestr(path, b'synthetic notice')
                    write_mac_sidecar(bundle, path, attribute)
                if attribute == b'com.apple.provenance':
                    self.assertEqual(build_desktop.check_archive(archive, 'darwin'), 2)
                else:
                    with self.assertRaisesRegex(build_desktop.BuildError, 'Unsupported Mac ZIP attributes'):
                        build_desktop.check_archive(archive, 'darwin')
                    self.assertTrue(archive.is_file())

    def test_archive_and_build_names_state_the_target_and_architecture(self):
        name = build_desktop.archive_name('0.1.0-preview.1', 'win32')
        self.assertTrue(name.startswith('Resonance-0.1.0-preview.1-windows-'))
        self.assertTrue(name.endswith('-candidate.zip'))
        self.assertIn('-mac-', build_desktop.archive_name('0.1.0-preview.1', 'darwin'))

    def test_workflow_is_manual_unsigned_and_publishes_nothing(self):
        text = (ROOT / '.github' / 'workflows' / 'desktop-candidate.yml').read_text()
        self.assertRegex(text, r'(?m)^on: workflow_dispatch$')
        self.assertIn("python-version: '3.14.10'", text)
        self.assertIn("node-version: '24'", text)
        for runner in ('macos-15', 'macos-15-intel', 'windows-2025'):
            self.assertRegex(text, rf'(?m)^ +(?:- )?os: {runner}$')
        for forbidden in ('secrets.', 'contents: write', 'gh release', 'create-release', 'codesign', 'notarytool'):
            self.assertNotIn(forbidden, text)


if __name__ == '__main__':
    unittest.main()
