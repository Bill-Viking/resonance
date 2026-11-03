#!/usr/bin/env python3
"""Package the Resonance Reader browser extension into a deterministic ZIP.

Writes a ZIP and a SHA256 manifest into a new folder. It validates the
extension first and refuses anything unexpected. It never publishes, signs,
uploads or contacts anything: store submission is a separate, manual step."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import struct
import sys
import zipfile


SOURCE = Path(__file__).resolve().parent.parent / 'browser-reader'
NAME = 'Resonance Reader'
VERSION = '0.1.0'
PROTOCOL = 1
EVENT_PREFIX = 'resonance:reader:'
APP_ORIGIN = 'http://127.0.0.1:4317'
# Explicit allowlist: a new file is private until someone adds it here.
SHIPPED = ('bridge.js', 'manifest.json', 'options.html', 'options.js', 'popup.html', 'popup.js',
           'reader.js', 'shared.js', 'worker.js', 'icon-16.png', 'icon-32.png', 'icon-128.png')
ICONS = {str(size): 'icon-%s.png' % size for size in (16, 32, 128)}
# Source-only files that live beside the extension but never ship.
SOURCE_ONLY = frozenset(('README.md',))
IGNORED = frozenset(('.DS_Store',))
FIXED_TIME = (1980, 1, 1, 0, 0, 0)
ALLOWED_HOSTS = frozenset((APP_ORIGIN, 'https://x.com', 'https://www.youtube.com', 'https://myactivity.google.com',
                           'https://studio.youtube.com', 'https://accounts.google.com'))
FORBIDDEN = tuple(re.compile(pattern) for pattern in (
    r'(?i)facebook', r'(?i)fixture', r'4322', r'ResonanceLive\b', r'\bfetch\s*\(', r'XMLHttpRequest', r'WebSocket',
    r'\beval\s*\(', r'new Function', r'chrome\.storage', r'chrome\.cookies', r'chrome\.history', r'chrome\.debugger',
    r'chrome\.webRequest', r'chrome\.downloads', r'localStorage|sessionStorage|indexedDB', r'document\.cookie',
    r'chrome://|edge://', r'chromewebstore|microsoftedge\.microsoft\.com',
    r'\brequire\s*\(', r'\bimport\s*[({]', r'\bimport\s+[\w{*]', r'\.\./'))
EXPECTED_MANIFEST = {
    'manifest_version': 3,
    'name': NAME,
    'version': VERSION,
    'minimum_chrome_version': '106',
    'incognito': 'not_allowed',
    'icons': ICONS,
    'permissions': ['scripting'],
    'host_permissions': [APP_ORIGIN + '/*'],
    'optional_host_permissions': ['https://x.com/*', 'https://www.youtube.com/*', 'https://myactivity.google.com/*'],
    'options_page': 'options.html',
    'background': {'service_worker': 'worker.js'},
    'action': {'default_title': NAME, 'default_popup': 'popup.html'},
    'content_scripts': [{'matches': [APP_ORIGIN + '/'], 'js': ['shared.js', 'bridge.js'],
                         'run_at': 'document_idle', 'all_frames': False}],
}
OPTIONAL_MANIFEST_KEYS = frozenset(('description',))


def fail(message):
    raise ValueError(message)


def collect(source):
    """The shipped files, after checking the folder holds nothing unexpected."""
    if not source.is_dir() or source.is_symlink():
        fail('The reader source folder is missing or is a link: %s' % source)
    found = {}
    for entry in sorted(source.iterdir(), key=lambda item: item.name):
        mode = entry.lstat().st_mode
        if stat.S_ISLNK(mode):
            fail('Refusing a symbolic link: %s' % entry.name)
        if entry.name in IGNORED:
            continue
        if not stat.S_ISREG(mode):
            fail('Refusing a non-file entry: %s' % entry.name)
        if entry.name in SOURCE_ONLY:
            continue
        if entry.name not in SHIPPED:
            fail('Refusing a file that is not on the allowlist: %s' % entry.name)
        found[entry.name] = entry.read_bytes()
    missing = sorted(set(SHIPPED) - set(found))
    if missing:
        fail('Missing files: %s' % ', '.join(missing))
    return found


def validate(files):
    try:
        manifest = json.loads(files['manifest.json'].decode('utf-8'))
    except (UnicodeDecodeError, ValueError) as error:
        fail('manifest.json is not valid JSON: %s' % error)
    extra = set(manifest) - set(EXPECTED_MANIFEST) - OPTIONAL_MANIFEST_KEYS
    if extra:
        fail('Unexpected manifest keys: %s' % ', '.join(sorted(extra)))
    for key, expected in EXPECTED_MANIFEST.items():
        if manifest.get(key) != expected:
            fail('Manifest %s is not the expected value.' % key)
    if not isinstance(manifest.get('description', ''), str) or len(manifest.get('description', '')) > 132:
        fail('The manifest description must be a short string.')
    for key in ('background', 'action'):
        for value in manifest[key].values():
            if value.endswith('.js') or value.endswith('.html'):
                if value not in SHIPPED:
                    fail('The manifest names a file that does not ship: %s' % value)
    bridge = files['bridge.js'].decode('utf-8')
    if not re.search(r'const PROTOCOL = %d;' % PROTOCOL, bridge):
        fail('bridge.js does not declare protocol %d.' % PROTOCOL)
    if 'const PREFIX = "%s";' % EVENT_PREFIX not in bridge:
        fail('bridge.js does not use the %s event prefix.' % EVENT_PREFIX)
    if 'const PORT_NAME = "resonance-reader:root";' not in bridge:
        fail('bridge.js does not use the production port name.')
    # Own-account discovery: its one button, its two page events and its own
    # worker message types must be present together, never half shipped.
    for marker in ('"res-find-accounts": "discover"', '"discover"', '"cancel-discovery"', 'discovery-accepted',
                   'discovery-result', 'discovery-done', 'discovery-cancelled'):
        if marker not in bridge:
            fail('bridge.js is missing the discovery marker %s.' % marker)
    worker = files['worker.js'].decode('utf-8')
    if re.findall(r'importScripts\(([^)]*)\)', worker) != ['"shared.js"']:
        fail('worker.js may import only shared.js.')
    for marker in ('"DISCOVER"', '"CANCEL_DISCOVERY"', '"DISCOVERY_RESULT"', 'validDiscoveryDestination'):
        if marker not in worker:
            fail('worker.js is missing the discovery marker %s.' % marker)
    # Discovery must not widen the ordinary read routes or the open-window count.
    if len(re.findall(r'tabs\.create\(', worker)) != 2 or len(re.findall(r'windows\.create\(', worker)) != 1:
        fail('worker.js opens tabs or windows in a new place.')
    for name, data in files.items():
        if name in ICONS.values():
            size = int(name.split('-')[1].split('.')[0])
            if (len(data) < 33 or len(data) > 256 * 1024 or data[:8] != b'\x89PNG\r\n\x1a\n'
                    or data[12:16] != b'IHDR' or struct.unpack('>II', data[16:24]) != (size, size)):
                fail('%s is not the expected PNG icon.' % name)
            continue
        try:
            text = data.decode('utf-8')
        except UnicodeDecodeError:
            fail('%s is not UTF-8 text.' % name)
        for pattern in FORBIDDEN:
            if pattern.search(text):
                fail('%s contains forbidden content (%s).' % (name, pattern.pattern))
        for host in re.findall(r'https?://[A-Za-z0-9.:-]+', text):
            if host not in ALLOWED_HOSTS:
                fail('%s names an unexpected host: %s' % (name, host))
        if name.endswith('.html'):
            if re.search(r'<script(?![^>]*\ssrc="[a-z.]+\.js")[^>]*>', text):
                fail('%s has an inline or non-local script.' % name)
    return manifest


def digest(data):
    return hashlib.sha256(data).hexdigest()


def make_zip(files, target):
    with zipfile.ZipFile(target, 'w') as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, files[name], compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def build(output, source=SOURCE):
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        fail('The output must be a new folder that does not exist yet: %s' % output)
    if output.resolve().is_relative_to(Path(source).resolve()):
        fail('Put the output outside the extension source folder.')
    files = collect(Path(source))
    manifest = validate(files)
    output.mkdir(parents=True)
    archive_name = 'resonance-reader-%s.zip' % manifest['version']
    archive = output / archive_name
    make_zip(files, archive)
    data = archive.read_bytes()
    record = {
        'name': manifest['name'],
        'version': manifest['version'],
        'protocol': PROTOCOL,
        'zip': {'name': archive_name, 'sha256': digest(data), 'bytes': len(data)},
        'files': [{'path': name, 'sha256': digest(files[name]), 'bytes': len(files[name])} for name in sorted(files)],
    }
    listing = output / ('resonance-reader-%s.sha256.json' % manifest['version'])
    listing.write_text(json.dumps(record, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return archive, listing, record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, help='a new folder for the ZIP and its SHA256 manifest')
    args = parser.parse_args()
    try:
        archive, listing, record = build(args.out)
    except ValueError as error:
        print('Cannot build the reader package: %s' % error, file=sys.stderr)
        return 1
    print('Built %s (%d bytes)' % (archive, record['zip']['bytes']))
    print('sha256 %s' % record['zip']['sha256'])
    print('manifest %s' % listing)
    print('Not published. Store submission, signing and review are separate manual steps.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
