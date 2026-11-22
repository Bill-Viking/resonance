"""Exercise launcher process ownership without touching the user's running app."""
import hashlib
from http.client import HTTPException
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


LAUNCHER = Path(__file__).resolve().parents[1] / 'mac-launcher' / 'launch.py'
FIXTURE = '''
import argparse, os
from pathlib import Path
from http.server import BaseHTTPRequestHandler
from socketserver import TCPServer
p = argparse.ArgumentParser(); p.add_argument('--port', type=int); args = p.parse_args()
with open(Path(os.environ['RESONANCE_DATA_DIR']) / 'starts', 'a') as f:
    f.write(str(os.getpid()) + '\\n')
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = Path('static/index.html').read_bytes()
        self.send_response(200); self.send_header('Content-Length', str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def log_message(self, *args): pass
with TCPServer(('127.0.0.1', args.port), Handler) as server:
    server.timeout = 0.1
    quit_file = Path(os.environ['RESONANCE_DATA_DIR']) / 'finish'
    while not quit_file.exists():
        server.handle_request()
'''

if sys.platform == 'darwin':
    spec = importlib.util.spec_from_file_location('source_launcher', LAUNCHER)
    launch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launch)


@unittest.skipUnless(sys.platform == 'darwin', 'Mac launcher integration')
class MacLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='resonance-launcher-test-')
        self.root = Path(self.temporary.name)
        self.runtime = self.root / 'runtime'
        (self.runtime / 'static').mkdir(parents=True)
        (self.runtime / 'static' / 'index.html').write_text('resonance isolated launcher fixture')
        (self.runtime / 'server.py').write_text(FIXTURE)
        (self.runtime / 'RELEASE-MANIFEST.json').write_text('Synthetic manifest fixture')
        self.data = self.root / 'data'
        self.data.mkdir()
        self.config = dict(runtime=str(self.runtime), port=4317,
            state_dir=str(self.root / 'launcher'), data_dir=str(self.data),
            index_bytes=(self.runtime / 'static' / 'index.html').stat().st_size)
        for name, key in (('RELEASE-MANIFEST.json', 'manifest_sha256'), ('server.py', 'server_sha256'),
                          ('static/index.html', 'index_sha256')):
            self.config[key] = hashlib.sha256((self.runtime / name).read_bytes()).hexdigest()
        self.config_file = self.root / 'config.json'
        self.save_config()

    def use_synthetic_loopback_port(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            self.config['port'] = sock.getsockname()[1]
        self.save_config()

    def tearDown(self):
        (self.data / 'finish').touch()
        owner = Path(self.config['state_dir']) / launch.OWNER_FILE
        deadline = time.monotonic() + 3
        while owner.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.temporary.cleanup()

    def save_config(self):
        self.config_file.write_text(json.dumps(self.config))

    def started(self):
        path = self.data / 'starts'
        return [int(pid) for pid in path.read_text().splitlines()] if path.exists() else []

    def command(self):
        return [sys.executable, str(LAUNCHER), '--config', str(self.config_file), '--no-open']

    def run_launcher(self):
        return subprocess.run(self.command(), capture_output=True, text=True, timeout=25)

    def startup_details(self, result):
        log = Path(self.config['state_dir']) / 'launcher.log'
        return (str(result.stderr) + '\nSynthetic child starts: ' + str(self.started()) +
                '\nSynthetic launcher log: ' + (log.read_text() if log.exists() else 'not created'))

    def test_cold_start_then_reuse_keeps_one_server(self):
        self.use_synthetic_loopback_port()
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, self.startup_details(result))
        first = self.started()
        self.assertEqual(len(first), 1)
        owner = json.loads((Path(self.config['state_dir']) / launch.OWNER_FILE).read_text())
        self.assertEqual(owner['pid'], first[0])
        self.assertTrue(owner['birth'])
        self.assertEqual(owner['executable'], str(Path(sys.executable).resolve()))
        self.assertEqual(owner['runtime'], str(self.runtime))
        self.assertEqual(self.run_launcher().returncode, 0)
        self.assertEqual(self.started(), first)

    def test_concurrent_clicks_start_only_one_server(self):
        self.use_synthetic_loopback_port()
        clicks = [subprocess.Popen(self.command(), stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(4)]
        results = [(click, click.communicate(timeout=25)[1]) for click in clicks]
        for click, errors in results:
            self.assertEqual(click.returncode, 0, self.startup_details(subprocess.CompletedProcess([], click.returncode, stderr=errors)))
        self.assertEqual(len(self.started()), 1)

    def test_other_release_is_left_running(self):
        self.use_synthetic_loopback_port()
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, self.startup_details(result))
        first = self.started()
        self.config['index_sha256'] = '0' * 64
        self.save_config()
        result = self.run_launcher()
        self.assertEqual(result.returncode, 1)
        self.assertIn('Another app or a different Resonance release', result.stderr)
        self.assertEqual(self.started(), first)
        self.assertTrue((Path(self.config['state_dir']) / launch.OWNER_FILE).exists())

    def test_missing_runtime_has_actionable_error(self):
        self.use_synthetic_loopback_port()
        self.config['runtime'] = str(self.root / 'missing')
        self.save_config()
        result = self.run_launcher()
        self.assertEqual(result.returncode, 1)
        self.assertIn('installed Resonance files are missing', result.stderr)
        self.assertEqual(self.started(), [])

    def test_changed_runtime_is_not_started(self):
        self.use_synthetic_loopback_port()
        (self.runtime / 'static' / 'index.html').write_text('changed')
        result = self.run_launcher()
        self.assertEqual(result.returncode, 1)
        self.assertIn('installed release has changed', result.stderr)
        self.assertEqual(self.started(), [])

    def test_failed_start_keeps_error_in_local_log(self):
        self.use_synthetic_loopback_port()
        source = b'raise SystemExit("fixture failed to start")'
        (self.runtime / 'server.py').write_bytes(source)
        self.config['server_sha256'] = hashlib.sha256(source).hexdigest()
        self.save_config()
        result = self.run_launcher()
        self.assertEqual(result.returncode, 1)
        self.assertIn('could not start', result.stderr)
        log = Path(self.config['state_dir']) / 'launcher.log'
        self.assertIn('fixture failed to start', log.read_text())
        self.assertEqual(log.stat().st_mode & 0o777, 0o600)

    def test_http_protocol_error_is_not_ready(self):
        with mock.patch.object(launch, 'HTTPConnection') as connection:
            connection.return_value.getresponse.side_effect = HTTPException('synthetic protocol mismatch')
            self.assertFalse(launch.ready(self.config))
            connection.return_value.close.assert_called_once()

    def test_unowned_listener_and_stale_record_are_not_stopped(self):
        state = launch.private_state(self.config)
        stale = {**launch.release_identity(self.config), 'pid': 12345, 'birth': 'synthetic birth', 'instance': 'a' * 32}
        with mock.patch.object(launch, 'ready', return_value=True) as listener:
            for record in (None, stale):
                if record is not None:
                    launch.write_state(state, launch.OWNER_FILE, record)
                with self.assertRaisesRegex(RuntimeError, 'No stop was sent'):
                    launch.stop(self.config, state)
                self.assertFalse((state / launch.STOP_FILE).exists())
            with mock.patch.object(launch.socket, 'socket') as probe:
                probe.return_value.__enter__.return_value.connect_ex.return_value = 0
                with self.assertRaisesRegex(RuntimeError, 'Another app'):
                    launch.start(self.config, state, self.config_file)
            listener.assert_not_called()

    def test_stop_requires_the_matching_supervisor_receipt(self):
        state = launch.private_state(self.config)
        owner = {**launch.release_identity(self.config), 'pid': 12345, 'birth': 'synthetic birth', 'instance': 'a' * 32}
        write = launch.write_state
        write(state, launch.OWNER_FILE, owner)
        write(state, launch.RECEIPT_FILE, {'id': 'stale receipt'})
        with launch.lock_file(state / 'owner.lock') as lock:
            self.assertTrue(launch.try_lock(lock))
            with mock.patch.object(launch.time, 'monotonic', side_effect=[0, 7]):
                with self.assertRaisesRegex(RuntimeError, 'did not confirm'):
                    launch.stop(self.config, state)
            self.assertFalse((state / launch.STOP_FILE).exists())

            def acknowledge(directory, name, request):
                self.assertEqual(name, launch.STOP_FILE)
                self.assertEqual(request['owner'], owner)
                write(directory, launch.RECEIPT_FILE, {'id': request['id']})

            with mock.patch.object(launch, 'write_state', side_effect=acknowledge):
                launch.stop(self.config, state)

    def test_only_an_exact_live_owned_child_can_receive_the_stop(self):
        owner = dict(pid=12345, executable='/example/python', server='/example/runtime/server.py',
                     runtime='/example/runtime', port=4317, birth='synthetic birth', instance='a' * 32)
        request = {'id': 'b' * 32, 'owner': owner}
        child = mock.Mock(pid=owner['pid'], args=[owner['executable'], '-B', owner['server'], '--port', '4317'])
        child.poll.return_value = None
        with mock.patch.object(launch, 'child_birth', return_value=owner['birth']) as birth:
            for key, value in (('pid', 12346), ('instance', 'f' * 32), ('birth', 'other birth'),
                               ('runtime', '/example/other'), ('executable', '/example/other-python')):
                self.assertFalse(launch.stop_child(child, dict(request, owner=dict(owner, **{key: value})), owner))
            birth.return_value = 'reused PID birth'
            self.assertFalse(launch.stop_child(child, request, owner))
            birth.return_value = owner['birth']
            child.poll.return_value = 0
            self.assertFalse(launch.stop_child(child, request, owner))
            child.poll.return_value = None
            child.terminate.assert_not_called()
            self.assertTrue(launch.stop_child(child, request, owner))
            child.terminate.assert_called_once_with()

    def test_log_rotation_bounds_both_files(self):
        state = launch.private_state(self.config)
        with mock.patch.object(launch, 'LOG_LIMIT', 8):
            launch.append_log(state, b'first')
            launch.append_log(state, b'second')
            self.assertEqual((state / 'launcher.log.1').read_bytes(), b'first')
            launch.append_log(state, b'0123456789')
            self.assertEqual((state / 'launcher.log').read_bytes(), b'23456789')
            self.assertEqual((state / 'launcher.log.1').read_bytes(), b'second')


if __name__ == '__main__':
    unittest.main()
