"""Exercise launcher process ownership without touching the user's running app."""
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import unittest


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
TCPServer(('127.0.0.1', args.port), Handler).serve_forever()
'''


@unittest.skipUnless(sys.platform == 'darwin', 'Mac launcher integration')
class MacLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='resonance-launcher-test-')
        self.root = Path(self.temporary.name)
        self.runtime = self.root / 'runtime'
        (self.runtime / 'static').mkdir(parents=True)
        (self.runtime / 'static' / 'index.html').write_text('resonance isolated launcher fixture')
        (self.runtime / 'server.py').write_text(FIXTURE)
        self.data = self.root / 'data'
        self.data.mkdir()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        self.config = dict(runtime=str(self.runtime), port=port,
            state_dir=str(self.root / 'launcher'), data_dir=str(self.data),
            index_sha256=hashlib.sha256((self.runtime / 'static' / 'index.html').read_bytes()).hexdigest())
        self.config_file = self.root / 'config.json'
        self.save_config()

    def tearDown(self):
        for pid in self.started():
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
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
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, self.startup_details(result))
        first = self.started()
        self.assertEqual(len(first), 1)
        self.assertEqual(self.run_launcher().returncode, 0)
        self.assertEqual(self.started(), first)

    def test_concurrent_clicks_start_only_one_server(self):
        clicks = [subprocess.Popen(self.command(), stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(4)]
        try:
            results = [(click, click.communicate(timeout=25)[1]) for click in clicks]
            for click, errors in results:
                self.assertEqual(click.returncode, 0, self.startup_details(subprocess.CompletedProcess([], click.returncode, stderr=errors)))
        finally:
            for click in clicks:
                if click.poll() is None:
                    click.kill()
                click.communicate()
        self.assertEqual(len(self.started()), 1)

    def test_other_release_is_left_running(self):
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, self.startup_details(result))
        first = self.started()
        self.config['index_sha256'] = '0' * 64
        self.save_config()
        result = self.run_launcher()
        self.assertEqual(result.returncode, 1)
        self.assertIn('Another app or a different Resonance release', result.stderr)
        self.assertEqual(self.started(), first)
        os.kill(first[0], 0)

    def test_missing_runtime_has_actionable_error(self):
        self.config['runtime'] = str(self.root / 'missing')
        self.save_config()
        result = self.run_launcher()
        self.assertEqual(result.returncode, 1)
        self.assertIn('installed Resonance files are missing', result.stderr)
        self.assertEqual(self.started(), [])

    def test_changed_runtime_is_not_started(self):
        (self.runtime / 'static' / 'index.html').write_text('changed')
        result = self.run_launcher()
        self.assertEqual(result.returncode, 1)
        self.assertIn('installed release has changed', result.stderr)
        self.assertEqual(self.started(), [])

    def test_failed_start_keeps_error_in_local_log(self):
        (self.runtime / 'server.py').write_text('raise SystemExit("fixture failed to start")')
        result = self.run_launcher()
        self.assertEqual(result.returncode, 1)
        self.assertIn('could not start', result.stderr)
        log = Path(self.config['state_dir']) / 'launcher.log'
        self.assertIn('fixture failed to start', log.read_text())
        self.assertEqual(log.stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
