"""Isolated practice workspace. No provider access, real-data reads, or migrations.

HTTP must use Workspace for every data route. The command-line tool must hold
real_workspace around its entire Store operation, including Store creation.
Both use the same process lock and persistent mode marker.
"""
from contextlib import contextmanager
import importlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import uuid


SAMPLE_PREFIX = 'resonance-sample-'
DEMO_NAME = 'demo.sqlite3'
MARKER_NAME = '.workspace.json'
LOCK_NAME = '.workspace.lock'
# Both supported real filenames must stay distinct from the practice file.
REAL_DATABASE_NAMES = ('resonance.sqlite3', 'social-brief.sqlite3')
SEED = Path(__file__).resolve().parent / 'examples' / 'practice.seed.json'
_locks = {}
_locks_guard = threading.Lock()


class SampleError(ValueError):
    """A workspace boundary failed closed before touching a data store."""


def has_sample_data(value):
    """Recognize reserved identities even inside a captured report or backup."""
    if isinstance(value, str):
        return value.startswith(SAMPLE_PREFIX)
    if isinstance(value, dict):
        return any(has_sample_data(key) or has_sample_data(item) for key, item in value.items())
    if isinstance(value, list):
        return any(has_sample_data(item) for item in value)
    return False


def _ordinary_file(path, missing_ok=True):
    try:
        info = path.lstat()
    except FileNotFoundError:
        if missing_ok:
            return
        raise SampleError('The sample workspace is missing. Clear samples and try again.') from None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise SampleError('Workspace files must be ordinary files, not links or shared aliases.')


@contextmanager
def workspace_lock(directory):
    """Serialize HTTP switches with complete CLI operations on Mac and Windows."""
    directory = Path(directory).resolve()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with _locks_guard:
        lock = _locks.setdefault(str(directory), threading.RLock())
    with lock:
        path = directory / LOCK_NAME
        _ordinary_file(path)
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(descriptor, 'r+b') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise SampleError('The workspace lock must be an ordinary private file.')
            if os.name == 'nt':
                import msvcrt
                # Windows byte-range locks work past EOF. Keep the lock file's
                # inode stable: never replace it when writing the mode marker.
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _save_marker(directory, mode):
    marker = {'mode': mode, 'token': uuid.uuid4().hex}
    target = directory / MARKER_NAME
    _ordinary_file(target)
    descriptor, temporary = tempfile.mkstemp(prefix='.workspace-', suffix='.tmp', dir=directory)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(marker, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return marker


def _read_marker(directory, create=False):
    target = directory / MARKER_NAME
    _ordinary_file(target)
    if not target.exists():
        # A missing marker beside a demo database is ambiguous. Never expose
        # real data by assuming that this was an ordinary real-data restart.
        if (directory / DEMO_NAME).exists() or (directory / DEMO_NAME).is_symlink():
            raise SampleError('The sample workspace marker is missing; restore it before continuing.')
        return _save_marker(directory, 'real') if create else {'mode': 'real', 'token': None}
    try:
        marker = json.loads(target.read_text(encoding='utf-8'))
    except (ValueError, UnicodeError):
        raise SampleError('The workspace marker cannot be read. No data was opened.') from None
    if (not isinstance(marker, dict) or set(marker) != {'mode', 'token'} or
            marker['mode'] not in ('real', 'samples') or
            not isinstance(marker['token'], str) or not re.fullmatch(r'[a-f0-9]{32}', marker['token'])):
        raise SampleError('The workspace marker is invalid. No data was opened.')
    return marker


def _check_real_boundary(directory):
    demo = directory / DEMO_NAME
    _ordinary_file(demo)
    for name in REAL_DATABASE_NAMES:
        if (directory / name).resolve() == demo.resolve():
            raise SampleError('The sample database cannot share a path with a real database.')


@contextmanager
def real_workspace(directory):
    """Fail closed for every CLI read and write while the samples are showing."""
    directory = Path(directory).resolve()
    with workspace_lock(directory):
        if _read_marker(directory)['mode'] != 'real':
            raise SampleError('Samples are showing. Clear samples in Resonance before using the command-line tool.')
        _check_real_boundary(directory)
        yield


@contextmanager
def read_only_real_workspace(directory):
    """Guard --request without creating a directory, lock, or mode marker.

    Older stores may have no workspace lock. For those, compare the complete
    marker before and after the read and discard the answer if a switch raced
    it. Callers must not print the result until this context exits.
    """
    directory = Path(directory).resolve()
    with _locks_guard:
        lock = _locks.setdefault(str(directory), threading.RLock())
    with lock:
        path = directory / LOCK_NAME
        _ordinary_file(path)
        if path.exists():
            descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
            with os.fdopen(descriptor, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise SampleError('The workspace lock must be an ordinary private file.')
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                try:
                    marker = _read_marker(directory)
                    if marker['mode'] != 'real':
                        raise SampleError('Samples are showing. Clear samples in Resonance before using the command-line tool.')
                    _check_real_boundary(directory)
                    yield
                    if _read_marker(directory) != marker:
                        raise SampleError('The workspace changed during the read. Try the command again.')
                finally:
                    if os.name == 'nt':
                        stream.seek(0)
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        else:
            marker = _read_marker(directory)
            if marker['mode'] != 'real':
                raise SampleError('Samples are showing. Clear samples in Resonance before using the command-line tool.')
            _check_real_boundary(directory)
            yield
            if _read_marker(directory) != marker:
                raise SampleError('The workspace changed during the read. Try the command again.')


class Workspace:
    """Lazy router; a samples-mode process never constructs the real Store."""
    def __init__(self, directory, store_type):
        self.store_type = store_type
        self.api = importlib.import_module(store_type.__module__)
        self.directory = self.api.safe_data_directory(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with workspace_lock(self.directory):
            marker = _read_marker(self.directory, create=True)
            if marker['mode'] == 'real':
                _check_real_boundary(self.directory)
                # Preserve the normal startup upgrade/readiness gate, while a
                # restart in samples mode never constructs the real Store.
                self.store_type(self.directory)

    def context(self):
        with workspace_lock(self.directory):
            return dict(_read_marker(self.directory, create=True))

    def _checked(self, token):
        marker = _read_marker(self.directory, create=True)
        if not isinstance(token, str) or token != marker['token']:
            raise SampleError('The workspace changed. Refresh Resonance before making another change.')
        return marker

    def _check_demo_files(self, require=False):
        path = self.directory / DEMO_NAME
        _ordinary_file(path, missing_ok=not require)
        for suffix in ('-journal', '-wal', '-shm'):
            _ordinary_file(self.directory / (DEMO_NAME + suffix))
        for name in (self.api.DATABASE_NAME, self.api.LEGACY_DATABASE_NAME):
            if (self.directory / name).resolve() == path.resolve():
                raise SampleError('The sample database cannot share a path with a real database.')

    def _demo(self, create=False):
        self._check_demo_files(require=not create)
        owner = self

        class DemoStore(self.store_type):
            def __init__(self):
                self.directory = owner.directory
                self.path = self.directory / DEMO_NAME
                self.read_only = False
                self.is_sample = True

            @contextmanager
            def connect(self):
                owner._check_demo_files(require=True)
                with super().connect() as db:
                    yield db

            def upgrade_database(self):
                raise SampleError('Sample databases are created from the seed and never migrated.')

            def migration_backup(self, target):
                raise SampleError('Samples cannot be backed up.')

            def backup(self):
                raise SampleError('Samples cannot be exported or backed up. Clear samples to use your own data.')

            def import_backup(self, db, body):
                raise SampleError('Backups cannot be imported into samples.')

            def work(self):
                raise SampleError('Sample work is available only in the on-page rehearsal.')

        if create:
            descriptor = os.open(self.directory / DEMO_NAME,
                                 os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            os.close(descriptor)
        store = DemoStore()
        if create:
            with store.connect() as db:
                store.initialize_schema(db)
                db.execute(f'PRAGMA user_version = {len(self.api.MIGRATIONS)}')
            self._seed(store)
        return store

    def _store(self, marker):
        if marker['mode'] == 'samples':
            return self._demo()
        _check_real_boundary(self.directory)
        return self.store_type(self.directory)

    def state(self, **kwargs):
        with workspace_lock(self.directory):
            marker = _read_marker(self.directory, create=True)
            result = self._store(marker).state(**kwargs)
            result['workspace'] = dict(marker)
            return result

    def _remove_demo(self):
        self._check_demo_files()
        for suffix in ('-journal', '-wal', '-shm', ''):
            (self.directory / (DEMO_NAME + suffix)).unlink(missing_ok=True)

    def mutate(self, method, path, body, token=None):
        with workspace_lock(self.directory):
            marker = self._checked(token)
            if method == 'POST' and path == '/api/samples':
                self.api.fields(body, set())
                if marker['mode'] == 'samples':
                    return 200, {'workspace': dict(marker)}
                self._remove_demo()
                try:
                    self._demo(create=True)
                    marker = _save_marker(self.directory, 'samples')
                except Exception:
                    self._remove_demo()
                    raise
                return 201, {'workspace': dict(marker)}
            if method == 'POST' and path == '/api/samples/clear':
                self.api.fields(body, set())
                if marker['mode'] == 'samples':
                    self._remove_demo()
                    marker = _save_marker(self.directory, 'real')
                return 200, {'workspace': dict(marker)}
            if path == '/api/samples/rehearse':
                if marker['mode'] != 'samples' or method != 'POST':
                    raise SampleError('The rehearsal is available only while samples are showing.')
                return 200, self._rehearse(self._demo(), body)
            if marker['mode'] == 'samples':
                allowed = (method == 'POST' and path == '/api/action-requests') or (
                    method == 'PATCH' and re.fullmatch(r'/api/(?:source-items|reviews|policy-flags|action-requests)/[A-Za-z0-9_-]{1,100}', path))
                if not allowed:
                    raise SampleError('This control is unavailable in samples. Clear samples to use your own data.')
                # Only the scripted rehearsal may write a sample proof.
                if path.startswith('/api/action-requests/') and body != {'status': 'cancelled'}:
                    raise SampleError('Use the sample rehearsal to verify a sample request.')
            elif has_sample_data(body):
                raise SampleError('Sample records cannot be saved with your own data.')
            return self._store(marker).mutate(method, path, body)

    def _rehearse(self, store, body):
        self.api.fields(body, {'request_id', 'decision'}, {'request_id', 'decision'})
        self.api.identifier(body['request_id'])
        self.api.choice(body['decision'], ('yes', 'not_now'), 'sample decision')
        state = store.state(activity=False)
        request = next((item for item in state['action_requests'] if item['id'] == body['request_id']), None)
        if request is None or not request['can_prepare']:
            raise SampleError('Choose a queued sample request before playing the next check.')
        path = '/api/action-requests/' + request['id']
        if body['decision'] == 'not_now':
            result = store.mutate('PATCH', path, {'status': 'cancelled'})[1]
            source = next(item for item in state['source_items'] if request['id'] in item['request_ids'])
            store.mutate('PATCH', '/api/source-items/' + source['id'], {'status': 'open'})
            return dict(result, sample_proof='Not now. The sample post needs your choice again.')
        account = next(value for value in state['accounts'] if value['id'] == request['account_id'])
        observation = {key: request[key] for key in ('account_id', 'source_key', 'evidence_fingerprint')}
        observation.update(checked_at=self.api.now(), status='signed_in', platform=account['platform'],
                           handle=account['handle'], profile_url=account['url'], author_handle=account['handle'],
                           author_profile_url=account['url'], source_url=request['source_url'],
                           detail='Scripted sample identity comparison. No browser or platform inspected.')
        if request['action'] == 'edit':
            observation['edit_availability'] = dict(status='available', checked_at=observation['checked_at'],
                source_url=request['source_url'], available_until=None,
                reason='Scripted sample text-edit controls. No browser or platform inspected.')
        checked = store.mutate('POST', path + '/identity-check', observation)[1]['action_request']
        store.mutate('POST', path + '/start', {'identity_check_id': checked['identity_check']['id']})
        result = store.mutate('PATCH', path, {
            'status': 'completed', 'receipt_url': request['source_url'],
            'receipt_note': 'Sample proof: the rehearsal checked this sample result. No platform was contacted or changed.'})[1]
        return dict(result, sample_proof=result['action_request']['receipt_note'])

    def _seed(self, store):
        seed = self.api.strict_json(SEED.read_bytes())
        self.api.fields(seed, {'version', 'account', 'posts'}, {'version', 'account', 'posts'})
        if seed['version'] != 1 or not seed['account']['id'].startswith(SAMPLE_PREFIX):
            raise SampleError('The shipped sample seed is invalid.')
        account = self.api.account_record(seed['account'])
        with store.connect() as db:
            store.put(db, 'accounts', account)
            for post in seed['posts']:
                review = self.api.review_record(post['review'])
                if not review['id'].startswith(SAMPLE_PREFIX) or review['account_id'] != account['id']:
                    raise SampleError('The shipped sample seed has an invalid identity.')
                store.put(db, 'reviews', review)
        for post in seed['posts']:
            state = post['state']
            if state == 'needs_you':
                continue
            request = store.mutate('POST', '/api/action-requests', {
                'review_id': post['review']['id'], 'action': 'keep' if state == 'kept' else 'delete'})[1]['action_request']
            if state == 'verified':
                self._rehearse(store, {'request_id': request['id'], 'decision': 'yes'})
            elif state == 'blocked':
                outcome = dict(id=request['id'], target_type='request', account_id=request['account_id'],
                               source_key=request['source_key'], evidence_fingerprint=request['evidence_fingerprint'],
                               outcome='blocked', reason=post['reason'], receipt_url='', receipt_note='',
                               next_move='open_source', reconcile=False)
                with store.connect() as db:
                    store.apply_outcomes(db, store.validate_outcomes(db, [outcome]), self.api.now())
            elif state not in ('waiting', 'kept'):
                raise SampleError('The shipped sample seed has an invalid practice state.')

    def backup(self, token=None):
        with workspace_lock(self.directory):
            marker = self._checked(token)
            if marker['mode'] == 'samples':
                raise SampleError('Samples cannot be exported or backed up. Clear samples to use your own data.')
            return self._store(marker).backup()

    def brief(self, token=None):
        with workspace_lock(self.directory):
            marker = self._checked(token)
            if marker['mode'] == 'samples':
                raise SampleError('Samples cannot be exported. Clear samples to export your own brief.')
            return self.api.markdown_brief(self._store(marker).state())

    def data_folder_details(self):
        with workspace_lock(self.directory):
            marker = _read_marker(self.directory, create=True)
            if marker['mode'] == 'samples':
                self._check_demo_files(require=True)
                return {'path': str(self.directory), 'size_bytes': (self.directory / DEMO_NAME).stat().st_size,
                        'last_automatic_backup': None, 'samples': True}
            return self._store(marker).data_folder_details()

    def open_data_folder(self, token=None):
        with workspace_lock(self.directory):
            marker = self._checked(token)
            if marker['mode'] == 'samples':
                raise SampleError('Clear samples before opening your data folder.')
            return self._store(marker).open_data_folder()
