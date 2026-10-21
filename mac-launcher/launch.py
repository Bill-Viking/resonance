"""Start the installed local release and open it, without a Terminal window."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener


def ready(config):
    address = 'http://127.0.0.1:' + str(config['port'])
    try:
        with build_opener(ProxyHandler({})).open(address, timeout=1) as response:
            body = response.read(1024 * 1024)
            return hashlib.sha256(body).hexdigest() == config['index_sha256']
    except (URLError, OSError):
        return False


def start(config):
    if ready(config):
        return
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1', config['port'])) == 0:
            raise RuntimeError('Another app or a different Resonance release is using its local address. Close that copy, then open Resonance again.')
    runtime = Path(config['runtime'])
    if not (runtime / 'server.py').is_file():
        raise RuntimeError('The installed Resonance files are missing. Ask your assistant to repair the Mac launcher.')
    index = runtime / 'static' / 'index.html'
    if hashlib.sha256(index.read_bytes()).hexdigest() != config['index_sha256']:
        raise RuntimeError('The installed release has changed. Ask your assistant to update the Mac launcher.')
    # Both names let this launcher pin the data folder for older installed releases.
    environment = dict(os.environ, RESONANCE_DATA_DIR=config['data_dir'],
                       SOCIAL_BRIEF_DATA_DIR=config['data_dir'])
    log_path = Path(config['state_dir']) / 'launcher.log'
    with open(log_path, 'ab', buffering=0) as log:
        process = subprocess.Popen(
            [sys.executable, str(runtime / 'server.py'), '--port', str(config['port'])],
            cwd=runtime, env=environment, stdin=subprocess.DEVNULL,
            stdout=log, stderr=log, start_new_session=True, close_fds=True,
        )
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if ready(config):
            return
        if process.poll() is not None:
            raise RuntimeError('Resonance could not start. Your saved data is kept. Ask your assistant to check ' + str(log_path))
        time.sleep(0.15)
    raise RuntimeError('Resonance is taking longer to start. Try its icon again in a moment. Details: ' + str(log_path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path(__file__).with_name('config.json'))
    parser.add_argument('--no-open', action='store_true', help='Verify startup without opening a browser')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        config = json.loads(args.config.read_text())
        state = Path(config['state_dir'])
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        with open(state / 'launcher.lock', 'a') as lock:
            deadline = time.monotonic() + 20
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Resonance is already starting. Try its icon again in a moment.')
                    time.sleep(0.1)
            start(config)
        if not args.no_open:
            subprocess.run(['/usr/bin/open', 'http://127.0.0.1:' + str(config['port'])], check=True)
        return 0
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
