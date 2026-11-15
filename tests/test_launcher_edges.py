"""Exercise launcher edge cases with synthetic Tcl, server and data fixtures."""
import contextlib
import errno
import io
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

import assistant_kit
from desktop import launch
import server


class LauncherEdges(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.data = self.root / 'data'

    def test_self_test_default_log_is_under_synthetic_data(self):
        def check(data, port, log):
            self.assertEqual((data, port, log.path), (self.data, 0, self.data / launch.LOG_NAME))
            log.write('synthetic test')
            return {'ok': True}

        with mock.patch.object(launch, 'self_test', side_effect=check), \
                mock.patch.object(launch, 'log_directory') as regular_log, \
                mock.patch.object(launch, 'TkUi') as window, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(launch.main(['--self-test', '--data-dir', str(self.data), '--port', '0', '-psn_0_1']), 0)
        regular_log.assert_not_called()
        window.assert_not_called()
        self.assertIn('synthetic test', (self.data / launch.LOG_NAME).read_text(encoding='utf-8'))

    def test_windowed_self_test_writes_complete_file_report_without_stdout(self):
        log_dir = self.root / 'logs'
        report = self.root / 'result.json'
        result = {'ok': True, 'frozen': True, 'assistant_kit': True, 'version': '1.2.3'}
        with mock.patch.object(launch, 'self_test', return_value=result) as smoke, \
                mock.patch.object(launch, 'log_directory') as regular_log, \
                mock.patch.object(launch, 'TkUi') as window, mock.patch.object(sys, 'stdout', None):
            try:
                status = launch.main(['--self-test', '--data-dir', str(self.data), '--port', '0',
                                      '--log-dir', str(log_dir), '--self-test-report', str(report),
                                      '--stray-os-flag'])
            finally:
                if sys.stdout is not None:
                    sys.stdout.close()
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(report.read_bytes()), result)
        self.assertEqual(smoke.call_args.args[2].path, log_dir / launch.LOG_NAME)
        self.assertEqual(list(self.root.iterdir()), [report])
        regular_log.assert_not_called()
        window.assert_not_called()

    def test_report_is_published_only_after_json_is_complete(self):
        report = self.root / 'result.json'
        report.write_text('{"ok": false}', encoding='utf-8')

        def fail_publish(temporary, destination):
            self.assertEqual(temporary.parent, report.parent)
            self.assertEqual(destination, report)
            self.assertEqual(json.loads(temporary.read_bytes()), {'ok': True})
            self.assertEqual(json.loads(report.read_bytes()), {'ok': False})
            raise OSError('Synthetic publication failure')

        with mock.patch.object(launch.os, 'replace', side_effect=fail_publish), self.assertRaises(OSError):
            launch.write_report(report, {'ok': True})
        self.assertEqual(json.loads(report.read_bytes()), {'ok': False})
        self.assertEqual(list(self.root.iterdir()), [report])

    def test_stray_arguments_do_not_hide_known_errors_or_grant_test_flags(self):
        arguments = (['--port', '4317'], ['--data-dir', str(self.data)], ['--log-dir', str(self.root / 'logs')],
                     ['--self-test-report', str(self.root / 'result.json')],
                     ['--self-test', '--data-dir', str(self.data), '--port', 'bad'],
                     ['--self-test', '--data-dir', str(self.data), '--log-dir'],
                     ['--self-test', '--data-dir', str(self.data), '--port', '65536'])
        for argv in arguments:
            with self.subTest(argv=argv), mock.patch.object(launch, 'TkUi') as window, \
                    mock.patch.object(launch, 'log_directory') as regular_log, \
                    mock.patch.object(launch, 'self_test') as smoke, \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stop:
                launch.main(['--stray-os-flag', *argv])
            self.assertEqual(stop.exception.code, 2)
            window.assert_not_called()
            regular_log.assert_not_called()
            smoke.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_address_reservation_and_occupied_port_have_different_instructions(self):
        log = self.root / launch.LOG_NAME
        for code in (errno.EACCES, 10013):
            with self.subTest(code=code):
                message = launch.plain_message(OSError(code, 'Synthetic private text'), 4317, log)
                self.assertIn('reserved or restricted', message)
                self.assertNotIn('already in use', message)
                self.assertNotIn('Synthetic private text', message)
        for code in (errno.EADDRINUSE, 10048):
            with self.subTest(code=code):
                message = launch.plain_message(OSError(code, 'Synthetic private text'), 4317, log)
                self.assertIn('already in use', message)
                self.assertIn('older Resonance source launcher', message)
                self.assertNotIn('reserved or restricted', message)
        error = OSError(errno.EINVAL, 'Synthetic Windows socket failure')
        error.winerror = 10013
        self.assertIn('reserved or restricted', launch.plain_message(error, 4317, log))

    def test_translocation_refuses_before_window_bind_or_log(self):
        executable = '/private/var/folders/example/AppTranslocation/id/d/Resonance.app/Contents/MacOS/Resonance'
        self.assertTrue(launch.is_translocated(executable, 'darwin', True))
        self.assertFalse(launch.is_translocated(executable, 'darwin', False))
        self.assertFalse(launch.is_translocated(executable, 'linux', True))
        self.assertFalse(launch.is_translocated('/Applications/Resonance.app/Contents/MacOS/Resonance', 'darwin', True))
        with mock.patch.object(launch, 'is_translocated', return_value=True), \
                mock.patch.object(launch, 'show_move_instruction') as move_alert, \
                mock.patch.object(launch, 'TkUi') as window, mock.patch.object(launch, 'log_directory') as log, \
                mock.patch.object(server, 'make_server') as bind, contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(launch.main(['-psn_0_1']), 1)
            move_alert.assert_called_once_with()
            self.assertEqual(launch.main(['--self-test', '--data-dir', str(self.data), '--port', '0']), 1)
            move_alert.assert_called_once_with()
        self.assertIn('Move Resonance.app to Applications', error.getvalue())
        window.assert_not_called()
        log.assert_not_called()
        bind.assert_not_called()
        self.assertFalse(self.data.exists())

    def test_frozen_smoke_publishes_temp_kit_before_probe_and_always_stops_server(self):
        toolkit = ModuleType('tkinter')
        interpreter = mock.Mock()
        interpreter.call.return_value = '9.0.4'
        toolkit.Tcl = mock.Mock(return_value=interpreter)

        bundle = self.root / 'bundle'
        contents = dict.fromkeys(assistant_kit.REQUIRED, b'Synthetic file\n')
        contents['VERSION'] = b'1.2.3\n'
        for name, raw in contents.items():
            path = bundle / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        (bundle / assistant_kit.MANIFEST_NAME).write_bytes(assistant_kit.make_manifest(contents))
        destinations = []

        class FakeController:
            url = 'http://127.0.0.1:4317'

            def __init__(self, ui, log, data_dir, port, make_server):
                self.server = make_server(data_dir, port)

            def start(self):
                destinations.append(self.server.assistant_kit_destination)
                return True

        def open_page(url, timeout):
            target, = destinations[-1].iterdir()
            self.assertTrue((target / 'ASSISTANT.md').is_file())
            self.assertNotIn(self.data, target.parents)
            if url.endswith('/api/assistant'):
                return io.BytesIO(json.dumps({'prompt': f'Read ASSISTANT.md in {target} and run my Resonance check.'}).encode())
            page = io.BytesIO(b'<!doctype html><html>')
            page.status = 200
            return page

        stopped = mock.Mock()
        FakeController.shutdown = stopped
        with mock.patch.object(sys, 'frozen', True, create=True), mock.patch.object(server, 'ROOT', bundle), \
                mock.patch.dict(sys.modules, {'tkinter': toolkit}), \
                mock.patch.object(server, 'make_server', side_effect=lambda *args: SimpleNamespace()), \
                mock.patch.object(launch, 'Controller', FakeController), mock.patch('zoneinfo.ZoneInfo'), \
                mock.patch('urllib.request.build_opener', return_value=SimpleNamespace(open=open_page)) as opener:
            result = launch.self_test(self.data, 0, launch.Log(self.root / launch.LOG_NAME))
            opener.return_value.open = mock.Mock(side_effect=OSError('Synthetic HTTP failure'))
            failed = launch.self_test(self.data, 0, launch.Log(self.root / launch.LOG_NAME))
        self.assertEqual(toolkit.Tcl.call_args_list, [mock.call(), mock.call()])
        self.assertEqual(interpreter.call.call_args_list, [mock.call('info', 'patchlevel')] * 2)
        self.assertEqual(result, {'ok': True, 'assistant_kit': True, 'version': '1.2.3',
                                  'seed': True, 'frozen': True, 'tcl': True,
                                  'tcl_patch': '9.0.4'})
        self.assertRegex(result['tcl_patch'], r'\A[0-9]+\.[0-9]+\.[0-9]+\Z')
        self.assertEqual(failed, {'ok': False, 'problem': 'check'})
        self.assertEqual(stopped.call_args_list, [mock.call(), mock.call()])
        self.assertTrue(all(not destination.exists() for destination in destinations))

    def test_tcl_failure_returns_fixed_problem_before_starting_the_server(self):
        interpreter = mock.Mock()
        interpreter.call.return_value = '9.0.4\n'
        for toolkit in (None, SimpleNamespace(Tcl=mock.Mock(side_effect=OSError('Synthetic private path'))),
                        SimpleNamespace(Tcl=mock.Mock(return_value=interpreter))):
            with self.subTest(toolkit=toolkit), mock.patch.dict(sys.modules, {'tkinter': toolkit}), \
                    mock.patch.object(assistant_kit, 'ensure') as kit, mock.patch.object(server, 'make_server') as bind:
                log = launch.Log(self.root / launch.LOG_NAME)
                self.assertEqual(launch.self_test(self.data, 0, log), {'ok': False, 'problem': 'tcl'})
                kit.assert_not_called()
                bind.assert_not_called()
                self.assertFalse(log.path.exists())
        interpreter.call.assert_called_once_with('info', 'patchlevel')
