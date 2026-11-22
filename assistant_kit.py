"""Validate and publish the public files an external assistant can use."""
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile


MANIFEST_NAME = 'ASSISTANT-KIT-MANIFEST.json'
REQUIRED = frozenset('''AGENTS.md ASSISTANT.md AUTHORING.md CHANGELOG.md INTEGRATIONS.md
LICENSE PRODUCT.md README.md SECURITY.md VERSION assistant_kit.py image_post.py monitor.py policy.py
reader_checks.py samples.py server.py workflow.py docs/ACTIONS.md docs/MONITORING.md
docs/SAFE-WORK.md docs/FACEBOOK-DISCOVERY.md skills/res-work-safe/SKILL.md
skills/res-work-safe/agents/openai.yaml skills/res-work-safe/scripts/check_coverage.py
skills/res-work-safe/scripts/discover_facebook.py skills/res-work-safe/scripts/capture_facebook_activity.js
static/index.html static/icon.svg static/guide/codex.html static/guide/manual.css
static/guide/copy-request.jpg static/guide/choose-account.jpg static/guide/triage-results.jpg
static/guide/example-chat.jpg static/guide/project-chat.jpg static/guide/USING-RES.md
examples/practice.seed.json'''.split())
PUBLIC = REQUIRED | frozenset('''static/account-discovery.js static/app.js
static/manifest.webmanifest static/reader-client.js static/styles.css static/sw.js
examples/learned.sample.json examples/report.context.sample.json
examples/report.privacy.sample.json examples/report.sample.json'''.split())
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_KIT_BYTES = 64 * 1024 * 1024
VERSION_PATTERN = re.compile(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?')
HASH_PATTERN = re.compile(r'[a-f0-9]{64}')
REPAIR = ('The Resonance assistant files could not be verified or saved. '
          'Repair the assistant folder or reinstall Resonance, then try again.')


class KitError(ValueError):
    """The public assistant kit could not be verified or placed safely."""


def _digest(content):
    return hashlib.sha256(content).hexdigest()


def _safe_name(name):
    return (isinstance(name, str) and all(part and part not in ('.', '..') and
            not part.endswith(('.', ' ')) and re.fullmatch(r'[a-zA-Z0-9_.-]+', part)
            for part in name.split('/')))


def _valid_version(version):
    return (isinstance(version, str) and len(version) <= 80 and
            VERSION_PATTERN.fullmatch(version))


def _version(raw):
    version = raw.decode('utf-8').strip()
    if not _valid_version(version):
        raise KitError('The assistant kit VERSION is invalid.')
    return version


def make_manifest(contents: dict) -> bytes:
    """Select only kit files from the audited staging input (names to bytes)."""
    if not all(_safe_name(name) for name in contents):
        raise KitError('The assistant kit staging input has an unsafe path.')
    if not REQUIRED <= contents.keys():
        raise KitError('A required assistant kit file is missing.')
    selected = {name: contents[name] for name in PUBLIC & contents.keys()}
    if (any(type(raw) is not bytes for raw in selected.values()) or
            sum(map(len, selected.values())) > MAX_KIT_BYTES):
        raise KitError('The assistant kit staging input is invalid or too large.')
    try:
        version = _version(selected['VERSION'])
    except UnicodeError:
        raise KitError('The assistant kit VERSION is invalid.') from None
    manifest = {'format': 1, 'version': version,
                'files': {name: {'sha256': _digest(raw), 'bytes': len(raw)}
                          for name, raw in selected.items()}}
    return (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode('utf-8')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise KitError(REPAIR)
        result[key] = value
    return result


def _manifest_int(value):
    if len(value) > len(str(MAX_KIT_BYTES)):
        raise KitError(REPAIR)
    return int(value)


def _parse_manifest(raw):
    manifest = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object, parse_int=_manifest_int)
    if (type(manifest) is not dict or set(manifest) != {'format', 'version', 'files'} or
            type(manifest['format']) is not int or manifest['format'] != 1 or
            not _valid_version(manifest['version'])):
        raise KitError(REPAIR)
    files = manifest['files']
    if type(files) is not dict or not REQUIRED <= files.keys() <= PUBLIC:
        raise KitError(REPAIR)
    for entry in files.values():
        if (type(entry) is not dict or set(entry) != {'sha256', 'bytes'} or
                type(entry['sha256']) is not str or not HASH_PATTERN.fullmatch(entry['sha256']) or
                type(entry['bytes']) is not int or entry['bytes'] < 0):
            raise KitError(REPAIR)
    if sum(entry['bytes'] for entry in files.values()) > MAX_KIT_BYTES:
        raise KitError(REPAIR)
    return manifest


def _is_link(status):
    return stat.S_ISLNK(status.st_mode) or bool(
        getattr(status, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _read_file(path, limit):
    status = path.lstat()
    if _is_link(status) or not stat.S_ISREG(status.st_mode) or status.st_size > limit:
        raise KitError(REPAIR)
    with path.open('rb') as file:
        raw = file.read(limit + 1)
    if len(raw) > limit:
        raise KitError(REPAIR)
    return raw


def _bundle_file(root, name):
    # PyInstaller on macOS cross-links corresponding Resources/Frameworks data.
    # Accept those in-bundle aliases, but no redirects to a different file or outside.
    path = (root / name).resolve(strict=True)
    permitted = {root / name}
    if (root.name in ('Resources', 'Frameworks') and root.parent.name == 'Contents' and
            root.parent.parent.suffix.casefold() == '.app'):
        sibling = 'Frameworks' if root.name == 'Resources' else 'Resources'
        permitted.add(root.parent / sibling / name)
    if path not in permitted:
        raise KitError(REPAIR)
    return path


def _verified_file(path, entry):
    raw = _read_file(path, entry['bytes'])
    if len(raw) != entry['bytes'] or _digest(raw) != entry['sha256']:
        raise KitError(REPAIR)
    return raw


def _user_locations():
    home = Path.home()
    if sys.platform == 'darwin':
        parent = home / 'Library' / 'Application Support'
        return parent, parent / 'Resonance Assistant'
    if sys.platform == 'win32':
        parent = Path(os.environ.get('LOCALAPPDATA') or home / 'AppData' / 'Local')
        return parent, parent / 'Resonance Assistant'
    parent = Path(os.environ.get('XDG_DATA_HOME') or home / '.local' / 'share')
    return parent, parent / 'resonance-assistant'


def _overlaps(left, right):
    if sys.platform in ('darwin', 'win32'):
        left, right = Path(str(left).casefold()), Path(str(right).casefold())
    return left == right or left in right.parents or right in left.parents


def _excluded_path(path):
    for part in (part.casefold() for part in path.parts):
        if ('icloud' in part or part in ('.git', 'mobile documents', 'cloudstorage',
                                        'dropbox', 'google drive', 'googledrive') or
                part.startswith('onedrive') or part.endswith('.app')):
            return True
    return False


def _destination(root, supplied, data_directory):
    user_parent, default = _user_locations()
    path = Path(supplied if supplied is not None else
                os.environ.get('RESONANCE_ASSISTANT_KIT_DIR') or default).expanduser()
    if (not path.is_absolute() or '..' in path.parts or path.drive.startswith('\\\\') or
            _excluded_path(path) or (os.name == 'nt' and any(
                part.endswith(('.', ' ')) or ':' in part for part in path.parts[1:]))):
        raise KitError(REPAIR)
    app = root.parent if root.name == '_internal' else root
    protected = [app] + [user_parent / name for name in ('Resonance', 'Social Brief', 'social-brief')]
    protected += [os.environ.get(key) for key in ('RESONANCE_DATA_DIR', 'SOCIAL_BRIEF_DATA_DIR')]
    protected.append(data_directory)
    for folder in (*reversed(path.parents), path):
        try:
            status = folder.lstat()
        except FileNotFoundError:
            continue
        if _is_link(status) or not stat.S_ISDIR(status.st_mode):
            raise KitError(REPAIR)
        try:
            (folder / '.git').lstat()
        except FileNotFoundError:
            continue
        raise KitError(REPAIR)
    canonical = path.resolve()
    if _excluded_path(canonical) or any(_overlaps(canonical, Path(other).expanduser().resolve())
                                       for other in protected if other):
        raise KitError(REPAIR)
    return canonical


def _private_parents(path):
    """Validate every existing ancestor and create only the missing folders."""
    for folder in (*reversed(path.parents), path):
        try:
            status = folder.lstat()
        except FileNotFoundError:
            try:
                folder.mkdir(mode=0o700)
            except FileExistsError:
                pass  # Another publisher created it first; validate it below.
            status = folder.lstat()
        if _is_link(status) or not stat.S_ISDIR(status.st_mode):
            raise KitError(REPAIR)


def _validate_published(target, entries):
    expected = {Path(): set()}
    for name in entries:
        path = Path(name)
        expected.setdefault(path.parent, set()).add(path.name)
        for parent in path.parents:
            if parent != Path():
                expected.setdefault(parent.parent, set()).add(parent.name)
    for relative, children in sorted(expected.items(), key=lambda entry: len(entry[0].parts)):
        folder = target / relative
        status = folder.lstat()
        if (_is_link(status) or not stat.S_ISDIR(status.st_mode) or
                {child.name for child in folder.iterdir()} != children):
            raise KitError(REPAIR)
    for name, entry in entries.items():
        _verified_file(target / name, entry)


def _publish(stage, target):
    """Atomically rename without replacing even an empty destination directory."""
    if os.name == 'nt':
        os.rename(stage, target)  # Windows rename fails if the destination exists.
        return
    libc = ctypes.CDLL(None, use_errno=True)
    source, destination = os.fsencode(stage), os.fsencode(target)
    if sys.platform == 'darwin':
        result = libc.renamex_np(source, destination, 0x00000004)  # RENAME_EXCL
    elif sys.platform.startswith('linux') and hasattr(libc, 'renameat2'):
        result = libc.renameat2(-100, source, -100, destination, 1)  # AT_FDCWD, RENAME_NOREPLACE
    else:
        raise KitError(REPAIR)
    if result != 0:
        code = ctypes.get_errno()
        raise OSError(code or errno.EIO, 'Could not publish the assistant kit.')


def ensure(root: Path, destination=None, frozen=None, *, data_directory=None) -> Path:
    """Return the source root or a verified, stable kit outside a frozen bundle."""
    if not (getattr(sys, 'frozen', False) if frozen is None else frozen):
        return root
    try:
        root = Path(root).resolve(strict=True)
        raw = _read_file(_bundle_file(root, MANIFEST_NAME), MAX_MANIFEST_BYTES)
        manifest = _parse_manifest(raw)
        entries = manifest['files']
        contents = {name: _verified_file(_bundle_file(root, name), entry)
                    for name, entry in entries.items()}
        if _version(contents['VERSION']) != manifest['version']:
            raise KitError(REPAIR)
        base = _destination(root, destination, data_directory)
        target = base / (manifest['version'] + '-' + _digest(raw))
        if not os.path.lexists(target):
            _private_parents(base)
            with tempfile.TemporaryDirectory(prefix='.kit-', dir=base) as temporary:
                stage = Path(temporary) / 'kit'
                stage.mkdir(mode=0o700)
                for name, content in contents.items():
                    file = stage / name
                    file.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                    file.write_bytes(content)
                try:
                    _publish(stage, target)
                except OSError:
                    if not os.path.lexists(target):
                        raise
        _validate_published(target, entries)
        return target
    except (OSError, ValueError, RuntimeError):
        raise KitError(REPAIR) from None
