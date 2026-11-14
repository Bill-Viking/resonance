"""Start or stop the audited source release owned by this Mac launcher."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
from http.client import HTTPConnection, HTTPException
import json
import os
from pathlib import Path
import select
import socket
import stat
import subprocess
import sys
import tempfile
import time
import uuid

LOG_LIMIT = 256 * 1024
OWNER_FILE = 'owned-child.json'
STOP_FILE = 'stop-request.json'
RECEIPT_FILE = 'stop-receipt.json'


def ready(config):
    connection = HTTPConnection('127.0.0.1', config['port'], timeout=1)
    try:
        connection.request('GET', '/')
        with connection.getresponse() as response:
            if response.status != 200:
                return False
            remaining = config['index_bytes']
            digest = hashlib.sha256()
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                chunk = response.read1(min(remaining + 1, 65536))
                if not chunk:
                    return remaining == 0 and digest.hexdigest() == config['index_sha256']
                remaining -= len(chunk)
                if remaining < 0:
                    break
                digest.update(chunk)
            return False
    except (HTTPException, OSError):
        return False
    finally:
        connection.close()


def private_state(config):
    state = Path(config['state_dir'])
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = state.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError('The source launcher needs a private log folder owned by you.')
    return state


@contextmanager
def lock_file(path):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, 'a') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise RuntimeError('The source launcher lock must be an ordinary file.')
        yield stream


def try_lock(lock):
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def read_state(state, name):
    try:
        fd = os.open(state / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            if stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return json.loads(stream.read(8192))
    except (OSError, ValueError):
        pass
    return None


def write_state(state, name, value):
    with tempfile.NamedTemporaryFile(mode='w', dir=state, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(value, stream)
            stream.close()
            temporary.replace(state / name)
        finally:
            temporary.unlink(missing_ok=True)


def append_log(state, data):
    path = state / 'launcher.log'
    previous = state / 'launcher.log.1'
    data = data[-LOG_LIMIT:]
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise RuntimeError('The source launcher log must be an ordinary file.')
        if info.st_size + len(data) > LOG_LIMIT:
            if info.st_size > LOG_LIMIT:
                path.unlink()
            else:
                path.replace(previous)
    except FileNotFoundError:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'ab') as stream:
        stream.write(data)


def check_runtime(config):
    runtime = Path(config['runtime'])
    if not (runtime / 'server.py').is_file():
        raise RuntimeError('The installed Resonance files are missing. Rebuild the source launcher from an audited export.')
    if runtime.is_symlink() or (runtime / 'static').is_symlink():
        raise RuntimeError('The installed release has changed. Rebuild the source launcher from an audited export.')
    for name, key in (('RELEASE-MANIFEST.json', 'manifest_sha256'), ('server.py', 'server_sha256'),
                      ('static/index.html', 'index_sha256')):
        path = runtime / name
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != config[key]:
            raise RuntimeError('The installed release has changed. Rebuild the source launcher from an audited export.')
    return runtime


def child_birth(pid):
    try:
        result = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'lstart='], env={'LC_ALL': 'C'},
                                capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return ''
    return result.stdout.strip() if result.returncode == 0 else ''


def matching_request(request, owner):
    return (isinstance(request, dict) and request.get('owner') == owner
            and isinstance(request.get('id'), str) and len(request['id']) == 32)


def release_identity(config):
    return {key: config.get(key) for key in ('runtime', 'port', 'manifest_sha256', 'index_sha256', 'data_dir')}


def live_owner(config, state):
    owner = read_state(state, OWNER_FILE)
    if (not isinstance(owner, dict) or type(owner.get('pid')) is not int or owner['pid'] <= 0
            or not owner.get('birth') or not owner.get('instance')
            or any(owner.get(key) != value for key, value in release_identity(config).items())):
        return None
    with lock_file(state / 'owner.lock') as lock:
        return None if try_lock(lock) else owner


def server_command(executable, script, port):
    return [executable, '-B', script, '--port', str(port)]


def stop_child(child, request, owner):
    if (not matching_request(request, owner) or child.pid != owner['pid']
            or list(child.args) != server_command(owner['executable'], owner['server'], owner['port'])
            or child.poll() is not None or not owner['birth'] or child_birth(child.pid) != owner['birth']):
        return False
    # Only this supervisor can reap its direct child. Even if the child exits
    # between poll and terminate, its PID cannot be reused while it is unreaped.
    # The CLI never signals a PID read from disk.
    child.terminate()
    return True


def supervise(config, state):
    with lock_file(state / 'owner.lock') as lock:
        if not try_lock(lock):
            raise RuntimeError('The source launcher already owns a server.')
        runtime = check_runtime(config)
        environment = dict(os.environ)
        if 'data_dir' in config:
            environment.update(RESONANCE_DATA_DIR=config['data_dir'], SOCIAL_BRIEF_DATA_DIR=config['data_dir'])
        executable, script = str(Path(sys.executable).resolve()), str(runtime / 'server.py')
        command = server_command(executable, script, config['port'])
        # Check log access before starting a detached child.
        append_log(state, b'')
        child = subprocess.Popen(command, cwd=runtime, env=environment, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            owner = {**release_identity(config), 'pid': child.pid, 'birth': child_birth(child.pid),
                     'instance': uuid.uuid4().hex, 'executable': executable, 'server': script}
            if owner['birth'] and child.poll() is None:
                write_state(state, OWNER_FILE, owner)
            elif child.poll() is None:
                child.terminate()  # This exact direct child could not be registered.
            stopped = None
            while child.poll() is None:
                request = read_state(state, STOP_FILE)
                if stopped is None and stop_child(child, request, owner):
                    stopped = request['id']
                drain_log(state, child.stdout, 0.15)
            # Drain the remaining pipe buffer; never wait on a closed child.
            for _ in range(64):
                if not drain_log(state, child.stdout, 0):
                    break
            if stopped is not None:
                write_state(state, RECEIPT_FILE, {'id': stopped})
        finally:
            child.stdout.close()
            if child.poll() is None:
                # The direct child must not be orphaned on a supervisor I/O error.
                child.terminate()
                child.wait(timeout=5)
            (state / OWNER_FILE).unlink(missing_ok=True)
    return 0


def drain_log(state, stream, timeout):
    if select.select([stream], [], [], timeout)[0]:
        data = os.read(stream.fileno(), 65536)
        if data:
            append_log(state, data)
            return True
    return False


def start(config, state, config_path):
    if live_owner(config, state):
        if ready(config):
            return
        raise RuntimeError('The source launcher server is still starting or stopping. Try again in a moment.')
    with socket.socket() as probe:
        probe.settimeout(0.5)
        if probe.connect_ex(('127.0.0.1', config['port'])) == 0:
            raise RuntimeError('Another app or a different Resonance release is using its local address. Close that copy, then open Resonance again.')
    check_runtime(config)
    with lock_file(state / 'owner.lock') as lock:
        if not try_lock(lock):
            raise RuntimeError('The source launcher server is still starting or stopping. Try again in a moment.')
        for name in (OWNER_FILE, STOP_FILE, RECEIPT_FILE):
            (state / name).unlink(missing_ok=True)
    log_path = state / 'launcher.log'
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), '--config', str(config_path), '--supervise'],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True, close_fds=True,
    )
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('Resonance could not start. Your saved data is kept. Details: ' + str(log_path))
        if live_owner(config, state) and ready(config):
            return
        time.sleep(0.15)
    raise RuntimeError('Resonance is taking longer to start. Try its icon again in a moment. Details: ' + str(log_path))


def stop(config, state):
    owner = live_owner(config, state)
    if owner is None:
        raise RuntimeError('No running server is verified as owned by this source launcher. No stop was sent.')
    request_id = uuid.uuid4().hex
    write_state(state, STOP_FILE, {'owner': owner, 'id': request_id})
    deadline = time.monotonic() + 6
    try:
        while time.monotonic() < deadline:
            if read_state(state, RECEIPT_FILE) == {'id': request_id}:
                return
            time.sleep(0.1)
    finally:
        (state / STOP_FILE).unlink(missing_ok=True)
    raise RuntimeError('The owned server did not confirm that it stopped. No other process was signaled.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path(__file__).with_name('config.json'))
    parser.add_argument('--no-open', action='store_true', help='Verify startup without opening a browser')
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--stop', action='store_true', help='Stop only the source launcher’s own server')
    action.add_argument('--supervise', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        try:
            config = json.loads(args.config.read_text(encoding='utf-8'))
        except (UnicodeError, ValueError):
            raise ValueError('The source launcher configuration is not valid JSON.') from None
        state = private_state(config)
        if args.supervise:
            return supervise(config, state)
        with lock_file(state / 'launcher.lock') as lock:
            deadline = time.monotonic() + 20
            while not try_lock(lock):
                if time.monotonic() >= deadline:
                    raise RuntimeError('The source launcher is already busy. Try again in a moment.')
                time.sleep(0.1)
            if args.stop:
                stop(config, state)
                return 0
            start(config, state, args.config.absolute())
        if not args.no_open:
            subprocess.run(['/usr/bin/open', 'http://127.0.0.1:' + str(config['port'])], check=True, timeout=10)
        return 0
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
