#!/usr/bin/env python3
"""Stage a reviewed native desktop candidate for signing. Never builds or publishes."""
import argparse
from contextlib import closing
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import platform
import re
import stat
import struct
import subprocess
import sys
import tempfile
import time
import unicodedata
from urllib.parse import urlsplit
import uuid
import zipfile


class SigningError(Exception):
    pass


SHA256 = re.compile(r'[0-9a-f]{64}')
THUMBPRINT = re.compile(r'[0-9a-fA-F]{40}')
TEAM_ID = re.compile(r'[A-Z0-9]{10}')
VERSION = re.compile(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?')
TCL_PATCH = re.compile(r'[0-9]+\.[0-9]+\.[0-9]+')
NOTARY_PROFILE = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}')
DEVICE = re.compile(r'(?i:CON|PRN|AUX|NUL|COM[0-9¹²³]|LPT[0-9¹²³]|CONIN\$|CONOUT\$)')
ROOTS = {'darwin': 'Resonance.app', 'win32': 'Resonance'}
EXECUTABLES = {'darwin': 'Resonance.app/Contents/MacOS/Resonance', 'win32': 'Resonance/Resonance.exe'}
NOTICES = {'darwin': 'Resonance.app/Contents/Resources/THIRD-PARTY-NOTICES.txt',
           'win32': 'Resonance/_internal/THIRD-PARTY-NOTICES.txt'}
SIGNATURES = {'darwin': 'Developer ID Application; hardened runtime; timestamp',
              'win32': 'Authenticode SHA-256; RFC 3161 SHA-256 timestamp'}
STATE = 'SIGNING-STATE.json'
MAX_ZIP = 256 * 1024 * 1024
MAX_TREE = 768 * 1024 * 1024
MAX_JSON = 8 * 1024 * 1024
MAX_ENTRIES = 20000
MAC_DIR_MODE = 0o755
MAC_FILE_MODE = 0o644
CODESIGN = '/usr/bin/codesign'
DEVELOPER_ID = ('anchor apple generic and certificate 1[field.1.2.840.113635.100.6.2.6] exists '
                'and certificate leaf[field.1.2.840.113635.100.6.1.13] exists')


def fail(message):
    raise SigningError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode()


def parse_json(raw):
    def unique(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                fail('Duplicate JSON key.')
            obj[key] = value
        return obj

    def bad_constant(_):
        fail('Non-finite number in JSON.')

    return json.loads(raw.decode('utf-8'), object_pairs_hook=unique, parse_constant=bad_constant)


def reparse(info):
    return bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def regular_bytes(path, limit):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or reparse(info) or info.st_size > limit:
        fail('Expected a bounded regular input file.')
    with path.open('rb') as source:
        raw = source.read(limit + 1)
    if len(raw) > limit:
        fail('Input exceeds the size limit.')
    return raw


def file_digest(source, target=None):
    digest, size = hashlib.sha256(), 0
    while True:
        chunk = source.read(1024 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > MAX_TREE:
            fail('File exceeds the expanded size limit.')
        digest.update(chunk)
        if target is not None:
            target.write(chunk)
    return {'sha256': digest.hexdigest(), 'bytes': size}


def require_sha(value):
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        fail('Supply a lowercase SHA-256 value (64 hex digits).')
    return value


def host_target():
    system = sys.platform
    machine = platform.machine().lower()
    arch = {'amd64': 'x86_64', 'x64': 'x86_64', 'aarch64': 'arm64'}.get(machine, machine)
    if (system, arch) not in {('darwin', 'arm64'), ('darwin', 'x86_64'), ('win32', 'x86_64')}:
        fail('Use a supported native macOS or Windows x64 host.')
    return system, arch


def native():
    system, arch = host_target()
    if system == 'win32':
        import ctypes
        kernel = ctypes.windll.kernel32
        process_machine, native_machine = ctypes.c_ushort(), ctypes.c_ushort()
        current_process = kernel.GetCurrentProcess
        current_process.restype = ctypes.c_void_p
        if (not kernel.IsWow64Process2(ctypes.c_void_p(current_process()), ctypes.byref(process_machine),
                                       ctypes.byref(native_machine))
                or process_machine.value != 0 or native_machine.value != 0x8664):
            fail('Run on native x64 Windows, outside emulation.')
    if system == 'darwin' and arch == 'x86_64':
        try:
            translated = subprocess.run(['/usr/sbin/sysctl', '-in', 'sysctl.proc_translated'],
                                        stdin=subprocess.DEVNULL, capture_output=True, timeout=5, env=safe_env(system))
        except (OSError, subprocess.SubprocessError):
            fail('Could not establish the native Mac architecture.')
        if translated.returncode != 0 or translated.stdout.strip() not in {b'', b'0'}:
            fail('Run on native Intel macOS, outside Rosetta.')
    return system, arch


def path_parts(name):
    if not isinstance(name, str) or len(name) > 2048:
        fail('Invalid archive path.')
    parts = name.split('/')
    for part in parts:
        if (not part or part in {'.', '..'} or len(part.encode('utf-8')) > 255 or
                part.endswith((' ', '.')) or any(c in '<>:"\\|?*' or ord(c) < 32 or ord(c) == 127 for c in part)
                or DEVICE.fullmatch(part.split('.')[0].rstrip(' '))):
            fail('Unsafe archive path.')
    return parts


def layout(files, links, directories, system):
    """Validate all paths and resolve links as the filesystem would, without touching it."""
    root = ROOTS[system]
    if set(files) & set(links) or (set(files) | set(links)) & set(directories):
        fail('A path is declared with multiple types.')
    if system != 'darwin' and links:
        fail('Windows symbolic links are forbidden.')
    spelled = {}
    all_dirs = set(directories)
    for name in (*files, *links, *directories):
        parts = path_parts(name)
        if parts[0] != root or (len(parts) == 1 and name not in directories):
            fail('Archive member is outside the distribution root.')
        for index in range(1, len(parts) + 1):
            prefix = '/'.join(parts[:index])
            key = unicodedata.normalize('NFD', prefix.casefold())
            if spelled.setdefault(key, prefix) != prefix:
                fail('Case or Unicode collision in archive paths.')
            if index < len(parts):
                all_dirs.add(prefix)
    if all_dirs & (set(files) | set(links)):
        fail('A file or link is the parent of an archive member.')

    def target_parts(target):
        if not isinstance(target, str) or not target or len(target.encode('utf-8')) > 4096:
            fail('Invalid symbolic link target.')
        parts = target.split('/')
        for part in parts:
            if part not in {'.', '..'}:
                path_parts(part)
        return parts

    targets = {name: target_parts(target) for name, target in links.items()}
    for name, target in targets.items():
        current = name.split('/')[:-1]
        pending = list(reversed(target))
        expansions = 0
        while pending:
            part = pending.pop()
            if part == '.':
                continue
            if part == '..':
                if len(current) <= 1:
                    fail('A symbolic link escapes the app bundle.')
                current.pop()
                continue
            current.append(part)
            candidate = '/'.join(current)
            if candidate in targets:
                expansions += 1
                if expansions > 40:
                    fail('Cyclic or overly deep symbolic links.')
                current.pop()
                pending.extend(reversed(targets[candidate]))
            elif candidate not in all_dirs and (pending or candidate not in files):
                fail('A symbolic link has a dangling or non-directory target.')
    if EXECUTABLES[system] not in files:
        fail('The native launcher is missing or is not a regular file.')
    return all_dirs


def valid_version(version):
    return isinstance(version, str) and len(version) <= 64 and VERSION.fullmatch(version)


def candidate_name(version, system, arch):
    label = 'mac' if system == 'darwin' else 'windows'
    return f'Resonance-{version}-{label}-{arch}-candidate.zip'


def shape(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        fail('Invalid release evidence fields.')


def count(value, limit, minimum=1):
    if type(value) is not int or not minimum <= value <= limit:
        fail('Invalid release evidence size or count.')


def check_notices(evidence, system, files=None):
    shape(evidence, ('path', 'sha256', 'bytes'))
    if evidence['path'] != NOTICES[system]:
        fail('Third-party notices must use the canonical regular file path.')
    require_sha(evidence['sha256'])
    count(evidence['bytes'], MAX_TREE)
    if files is not None and files.get(NOTICES[system]) != {k: evidence[k] for k in ('sha256', 'bytes')}:
        fail('Third-party notices are missing or do not match the recorded file.')


def check_manifest(document, archive, archive_raw, expected, system, arch):
    if not isinstance(document, dict):
        fail('Invalid BUILD-MANIFEST.json.')
    version = document.get('source_version')
    if (document.get('product') != 'Resonance' or document.get('platform') != system
            or document.get('architecture') != arch or document.get('os') != {'darwin': 'Darwin', 'win32': 'Windows'}[system]
            or document.get('target_os') != {'darwin': 'macOS', 'win32': 'Windows'}[system]
            or not valid_version(version)):
        fail('Candidate product, native target or version does not match.')
    require_sha(document.get('runtime_manifest_sha256'))
    expected_name = candidate_name(version, system, arch)
    metadata = document.get('archive')
    if (not isinstance(metadata, dict) or metadata.get('name') != archive.name or archive.name != expected_name
            or metadata.get('sha256') != expected or type(metadata.get('bytes')) is not int
            or metadata['bytes'] != len(archive_raw) or type(metadata.get('entries')) is not int
            or not 0 < metadata['entries'] <= MAX_ENTRIES):
        fail('Declared archive metadata does not match the reviewed ZIP.')
    files, links = document.get('files'), document.get('symlinks')
    if not isinstance(files, dict) or not isinstance(links, dict) or len(files) + len(links) > MAX_ENTRIES:
        fail('Invalid candidate file or link inventory.')
    for entry in files.values():
        if (not isinstance(entry, dict) or set(entry) != {'sha256', 'bytes'} or type(entry['bytes']) is not int
                or not 0 <= entry['bytes'] <= MAX_TREE):
            fail('Invalid file digest entry.')
        require_sha(entry['sha256'])
    check_notices(document.get('third_party_notices'), system, files)
    return {key: metadata[key] for key in ('name', 'sha256', 'bytes', 'entries')}, files, links


def apple_double(raw):
    """Allow only ditto's metadata envelope for the host's provenance attribute.

    Refuse quarantine, resource forks, Finder flags, ACLs and unrecognized attributes
    instead of dropping them or passing general AppleDouble input to a native parser.
    """
    if len(raw) < 120 or raw[:26] != bytes.fromhex(
            '00051607000200004d6163204f53205820202020202020200002'):
        fail('Unrecognized Mac ZIP metadata.')
    finder, offset, size, resource, resource_offset, resource_size = struct.unpack_from('>6I', raw, 26)
    if ((finder, offset, size, resource, resource_offset, resource_size) != (9, 50, len(raw) - 50, 2, len(raw), 0)
            or any(raw[50:84])):
        fail('Unsupported Finder or resource metadata in candidate ZIP.')
    magic, debug, length, start, data_size, reserved1, reserved2, reserved3, flags, count = struct.unpack_from(
        '>4s7I2H', raw, 84)
    if ((magic, debug, length, reserved1, reserved2, reserved3, flags, count)
            != (b'ATTR', 0, len(raw), 0, 0, 0, 0, 1)
            or start < 131 or start + data_size != len(raw) or not 0 < data_size <= 256):
        fail('Unrecognized Mac ZIP attributes.')
    value_offset, value_size, attr_flags, name_length = struct.unpack_from('>IIHB', raw, 120)
    name_end = 131 + name_length
    if ((value_offset, value_size, attr_flags) != (start, data_size, 0) or name_end > start
            or raw[131:name_end] != b'com.apple.provenance\x00' or any(raw[name_end:start])):
        fail('Unsupported Mac ZIP attributes; use a build-origin candidate without quarantine metadata.')


def check_mac_zip_headers(bundle):
    """Accept only conventional ditto headers before handing a ZIP to a native parser."""
    stream = bundle.fp
    end = 0
    for info in sorted(bundle.infolist(), key=lambda item: item.header_offset):
        if (info.header_offset != end or info.create_system != 3 or info.comment
                or info.flag_bits & ~0x808 or info.external_attr & 0xffff & ~0x4010
                or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}):
            fail('Unsupported Mac ZIP member header.')
        # ditto's 0x5855 Unix extra field contains times and (locally) uid/gid.
        if info.extra and (len(info.extra) != 12 or info.extra[:4] != b'UX\x08\x00'):
            fail('Unsupported Mac ZIP extra field.')
        stream.seek(end)
        header = stream.read(30)
        if len(header) != 30:
            fail('Truncated Mac ZIP member header.')
        magic, _, flags, method, _, _, crc, compressed, size, name_len, extra_len = struct.unpack(
            '<4s5H3I2H', header)
        if (magic != b'PK\x03\x04' or flags != info.flag_bits or method != info.compress_type
                or extra_len not in (0, 16)):
            fail('Unsupported Mac ZIP local header.')
        stream.seek(name_len, io.SEEK_CUR)
        extra = stream.read(extra_len)
        if extra_len and (len(extra) != 16 or extra[:4] != b'UX\x0c\x00'):
            fail('Unsupported Mac ZIP local extra field.')
        expected = (info.CRC, info.compress_size, info.file_size)
        end += 30 + name_len + extra_len + info.compress_size
        if flags & 8:
            stream.seek(end)
            if ((crc, compressed, size) != (0, 0, 0)
                    or stream.read(16) != struct.pack('<4s3I', b'PK\x07\x08', *expected)):
                fail('Invalid Mac ZIP data descriptor.')
            end += 16
        elif (crc, compressed, size) != expected:
            fail('Mac ZIP local and central metadata differ.')
    if end != bundle.start_dir:
        fail('Unexpected data before the Mac ZIP central directory.')


def mac_zip_metadata(bundle, info, name, kind):
    """Return (target, sidecar) for either native AppleDouble layout."""
    if name == '__MACOSX' or name.startswith('__MACOSX/'):
        relative = name.removeprefix('__MACOSX').removeprefix('/')
        if info.is_dir():
            if kind != stat.S_IFDIR or info.file_size or info.CRC:
                fail('Invalid Mac ZIP metadata directory.')
            with bundle.open(info) as entry:
                entry.read(1)
            return relative, False
        name = relative
        sequestered = True
    else:
        sequestered = False
    parent, _, leaf = name.rpartition('/')
    if not leaf.startswith('._'):
        if sequestered:
            fail('Invalid Mac ZIP metadata member.')
        return None
    if (leaf == '._' or info.is_dir() or kind != stat.S_IFREG
            or not 0 < info.file_size <= 4096 or (not sequestered and not parent)):
        fail('Invalid Mac ZIP metadata member.')
    target = (parent + '/' if parent else '') + leaf[2:]
    path_parts(target)
    apple_double(bundle.read(info))
    return target, True


def mac_file_mode(mode):
    return 0o755 if mode & 0o111 else MAC_FILE_MODE


def check_mac_bundle_mode(mode):
    kind = stat.S_IFMT(mode)
    if ((kind == stat.S_IFDIR and stat.S_IMODE(mode) != MAC_DIR_MODE)
            or (kind == stat.S_IFREG and stat.S_IMODE(mode) != mac_file_mode(mode))):
        fail('Noncanonical Mac bundle permissions.')


def launcher_architecture(source, system, arch):
    header = source.read(64)
    if system == 'win32':
        if len(header) == 64 and header[:2] == b'MZ':
            offset = int.from_bytes(header[60:64], 'little')
            if 64 <= offset <= 1024 * 1024:
                source.seek(offset)
                if source.read(6) == b'PE\x00\x00\x64\x86':
                    return
    else:
        if len(header) >= 8:
            magic = header[:4]
            thin = {b'\xfe\xed\xfa\xcf': 'big', b'\xcf\xfa\xed\xfe': 'little'}
            expected = {'x86_64': 0x01000007, 'arm64': 0x0100000c}[arch]
            # The native PyInstaller target emits a single 64-bit architecture.
            if magic in thin and int.from_bytes(header[4:8], thin[magic]) == expected:
                return
    fail('The launcher binary does not match the reviewed native platform and architecture.')


def zip_layout(bundle, files, links, entry_count, system, arch, *, mac_transport=False):
    infos = bundle.infolist()
    if (len(infos) != entry_count or sum(info.file_size for info in infos) > MAX_TREE):
        fail('ZIP entry count or expanded size does not match the allowed inventory.')
    entries, modes, directories, metadata = {}, {}, set(), {}
    if mac_transport:
        check_mac_zip_headers(bundle)
    for info in infos:
        original = info.orig_filename
        name = original[:-1] if original.endswith('/') else original
        path_parts(name)
        if (original != info.filename or name in entries or info.flag_bits & 1
                or info.external_attr & 0x400 or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}):
            fail('Invalid, duplicate, encrypted or unsupported ZIP entry.')
        entries[name] = info
        mode = info.external_attr >> 16 if info.create_system == 3 else 0
        kind = stat.S_IFMT(mode)
        if mode & 0o7000:
            fail('Privileged archive mode.')
        if mac_transport and (kind not in {stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK}
                              or info.is_dir() != (kind == stat.S_IFDIR)):
            fail('Unexpected Mac ZIP member type.')
        parent, _, leaf = name.rpartition('/')
        transport_metadata = mac_zip_metadata(bundle, info, name, kind) if mac_transport else None
        if transport_metadata is not None:
            if name in files or name in links:
                fail('Mac ZIP metadata shadows a tree entry.')
            metadata[name] = transport_metadata[0]
        elif system == 'darwin' and leaf.startswith('._'):
            if (not parent or leaf == '._' or original.endswith('/') or kind != stat.S_IFREG
                    or not 0 < info.file_size <= 4096 or name in files or name in links):
                fail('Invalid Mac ZIP metadata member.')
            target = parent + '/' + leaf[2:]
            path_parts(target)
            apple_double(bundle.read(info))
            metadata[name] = target
        elif original.endswith('/'):
            if kind not in {0, stat.S_IFDIR} or info.file_size or info.CRC:
                fail('Invalid ZIP directory.')
            if mac_transport:
                check_mac_bundle_mode(mode)
            # Read the local header too, even for a directory entry with no payload.
            with bundle.open(info) as source:
                source.read(1)
            directories.add(name)
        elif kind == stat.S_IFLNK:
            if system != 'darwin' or name not in links or info.file_size > 4096:
                fail('Unexpected symbolic link.')
            if bundle.read(info).decode('utf-8') != links[name]:
                fail('Symbolic link target does not match the manifest.')
        else:
            if (kind not in {0, stat.S_IFREG} or name not in files or info.external_attr & 0x10
                    or files[name]['bytes'] != info.file_size):
                fail('Unexpected ZIP member or file size.')
            if system == 'darwin':
                if mac_transport:
                    check_mac_bundle_mode(mode)
                if name == EXECUTABLES[system] and not mode & 0o100:
                    fail('The Mac launcher is not executable.')
                modes[name] = mac_file_mode(mode)
            else:
                modes[name] = 0o644 | (mode & 0o111)
    if set(modes) != set(files) or set(entries) - directories - set(modes) - set(metadata) != set(links):
        fail('The ZIP tree does not match the declared files and links.')
    if EXECUTABLES[system] not in files:
        fail('The native launcher is missing or is not a regular file.')
    with bundle.open(entries[EXECUTABLES[system]]) as source:
        launcher_architecture(source, system, arch)
    complete_dirs = layout(files, links, directories, system)
    if mac_transport and directories != complete_dirs:
        fail('Mac ZIP bundle directories must have explicit permissions.')
    directories = complete_dirs
    targets = set(files) | set(links) | directories
    sidecars = [target for name, target in metadata.items() if not entries[name].is_dir()]
    metadata_dirs = {target for name, target in metadata.items() if entries[name].is_dir()}
    if (len(set(sidecars)) != len(sidecars) or not set(sidecars) <= targets
            or not metadata_dirs <= directories | {''}):
        fail('Mac ZIP metadata does not correspond to the reviewed tree.')
    # Detect collisions involving metadata or inferred directories as well.
    all_names = directories | set(entries)
    if len({unicodedata.normalize('NFD', name.casefold()) for name in all_names}) != len(all_names):
        fail('Case or Unicode collision involving ZIP metadata.')
    for name in modes:
        with bundle.open(entries[name]) as source:
            if file_digest(source) != files[name]:
                fail('A ZIP file does not match its declared digest.')
    return entries, modes, directories, metadata


def extract_mac_metadata(out, bundle, entries, modes, directories, links, metadata):
    """Re-encode only checked members for ditto; no external ZIP headers reach it."""
    run = NativeCommands('darwin').run
    with tempfile.TemporaryDirectory(prefix='.verified-', dir=out) as folder:
        transport = Path(folder) / 'verified.zip'
        with zipfile.ZipFile(transport, 'x', zipfile.ZIP_DEFLATED) as safe_zip:
            for name in sorted(directories):
                info = zipfile.ZipInfo(name + '/')
                info.create_system = 3
                info.external_attr = (stat.S_IFDIR | MAC_DIR_MODE) << 16
                safe_zip.writestr(info, b'')
            for name in sorted(set(modes) | set(links) | set(metadata)):
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                mode = (stat.S_IFLNK | 0o755) if name in links else (stat.S_IFREG | modes.get(name, 0o644))
                info.external_attr = mode << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                with bundle.open(entries[name]) as source, safe_zip.open(info, 'w') as target:
                    file_digest(source, target)
        run(['/usr/bin/ditto', '-x', '-k', transport, out / 'dist'], 'Mac candidate extraction failed.', timeout=180)


def candidate_quarantine(path):
    run = NativeCommands('darwin').run
    attributes = run(['/usr/bin/xattr', '-s', path.absolute()], 'Cannot inspect the Mac ZIP attributes.', capture=True)
    if b'com.apple.quarantine' in attributes.splitlines():
        fail('Use a build-origin ZIP; the supplied ZIP has quarantine and was left untouched.')


def mac_tree_entries(dist):
    """Open each Mac tree entry relative to a checked directory, without following links."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK

    def walk(parent_fd, prefix):
        for leaf in os.listdir(parent_fd):
            name = prefix + leaf
            info = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
            mode = info.st_mode
            if not prefix and (leaf != ROOTS['darwin'] or not stat.S_ISDIR(mode)):
                fail('Staging entry is outside the Mac app bundle.')
            if stat.S_ISLNK(mode):
                yield name, info, None, os.readlink(leaf, dir_fd=parent_fd)
                continue
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                fail('Unexpected filesystem entry in staged tree.')
            open_flags = flags | os.O_DIRECTORY if stat.S_ISDIR(mode) else flags
            fd = os.open(leaf, open_flags, dir_fd=parent_fd)
            try:
                opened = os.fstat(fd)
                if not os.path.samestat(info, opened) or stat.S_IFMT(opened.st_mode) != stat.S_IFMT(mode):
                    fail('Filesystem entry changed while opening the staged tree.')
                yield name, opened, fd, None
                if stat.S_ISDIR(mode):
                    yield from walk(fd, name + '/')
            finally:
                os.close(fd)

    if not stat.S_ISDIR(dist.lstat().st_mode):
        fail('Staging tree root is missing or redirected.')
    fd = os.open(dist, flags | os.O_DIRECTORY)
    try:
        yield from walk(fd, '')
    finally:
        os.close(fd)


def snapshot_mac(dist, normalize_from):
    files, links, permissions = {}, {}, {}
    directories = set()
    size = 0
    with closing(mac_tree_entries(dist)) as entries:
        for name, info, fd, target in entries:
            mode = info.st_mode
            if stat.S_ISLNK(mode):
                links[name] = target
            else:
                regular = stat.S_ISREG(mode)
                if mode & 0o7000 or (regular and info.st_nlink != 1):
                    fail('Unexpected filesystem entry or privileged mode in staged tree.')
                if normalize_from is not None:
                    # Retain the classification of rewritten files and classify new files by their execute bits.
                    previous = normalize_from['executable_bits'].get(name, mode)
                    wanted = mac_file_mode(previous) if regular else MAC_DIR_MODE
                    if stat.S_IMODE(mode) != wanted:
                        os.fchmod(fd, wanted)
                        mode = os.fstat(fd).st_mode
                        if stat.S_IMODE(mode) != wanted:
                            fail('Could not set canonical Mac bundle permissions.')
                check_mac_bundle_mode(mode)
                permissions[name] = stat.S_IMODE(mode)
                if regular:
                    size += info.st_size
                    if size > MAX_TREE:
                        fail('Staging tree exceeds its size limit.')
                    with os.fdopen(fd, 'rb', closefd=False) as source:
                        files[name] = file_digest(source)
                else:
                    directories.add(name)
            if len(permissions) + len(links) > MAX_ENTRIES:
                fail('Staging tree has too many entries.')
    layout(files, links, directories, 'darwin')
    return {'files': files, 'symlinks': links, 'directories': sorted(directories),
            'executable_bits': {name: permissions[name] & 0o111 for name in files},
            'permissions': permissions}


def snapshot(dist, system, *, normalize_from=None):
    """Record the staged tree without following links, including full bundle permissions on Mac."""
    if system == 'darwin':
        return snapshot_mac(dist, normalize_from)
    if dist.is_symlink() or not dist.is_dir() or reparse(dist.lstat()):
        fail('Staging tree root is missing or redirected.')
    files, links, directories, modes = {}, {}, set(), {}
    size = 0
    pending = [dist]
    while pending:
        for path in pending.pop().iterdir():
            name = path.relative_to(dist).as_posix()
            info = path.lstat()
            mode = info.st_mode
            if stat.S_ISLNK(mode):
                links[name] = os.readlink(path)
            elif reparse(info) or mode & 0o7000:
                fail('Unexpected filesystem entry or privileged mode in staged tree.')
            elif stat.S_ISDIR(mode):
                directories.add(name)
                pending.append(path)
            elif stat.S_ISREG(mode) and info.st_nlink == 1:
                size += info.st_size
                if size > MAX_TREE:
                    fail('Staging tree exceeds its size limit.')
                with path.open('rb') as source:
                    files[name] = file_digest(source)
                modes[name] = mode & 0o111
            else:
                fail('Unexpected filesystem entry in staged tree.')
            if len(files) + len(links) + len(directories) > MAX_ENTRIES:
                fail('Staging tree has too many entries.')
    layout(files, links, directories, system)
    return {'files': files, 'symlinks': links, 'directories': sorted(directories), 'executable_bits': modes}


def save_state(out, state):
    raw = json_bytes(state)
    with tempfile.NamedTemporaryFile(dir=out, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(raw)
    try:
        temporary.replace(out / STATE)
    finally:
        temporary.unlink(missing_ok=True)
    return sha(raw)


def candidate_zip(raw, entry_limit=MAX_ENTRIES):
    # The build writer uses standard single-disk ZIPs below these size/entry limits.
    # Bound the central directory before zipfile allocates objects for its entries.
    if len(raw) < 22 or raw[-22:-18] != b'PK\x05\x06':
        fail('Expected a standard ZIP with no trailing data or archive comment.')
    disk, cd_disk, disk_entries, entries, cd_size, cd_start, comment = struct.unpack_from('<4H2IH', raw, len(raw) - 18)
    if (disk or cd_disk or comment or disk_entries != entries or not 0 < entries <= entry_limit
            or cd_start + cd_size != len(raw) - 22 or cd_size < 46 * entries or cd_size > 64 * 1024 * 1024):
        fail('Unexpected ZIP central directory.')
    # A malformed count must not let ZipFile allocate more than the entry limit.
    position = cd_start
    end = cd_start + cd_size
    for _ in range(entries):
        if position + 46 > end or raw[position:position + 4] != b'PK\x01\x02':
            fail('Invalid ZIP central directory entry.')
        name, extra, note = struct.unpack_from('<3H', raw, position + 28)
        position += 46 + name + extra + note
    if position != end:
        fail('ZIP central directory entry count does not match.')
    return zipfile.ZipFile(io.BytesIO(raw))


def verify_candidate(out, archive, manifest, archive_sha, manifest_sha):
    system, arch = native()
    require_sha(archive_sha)
    require_sha(manifest_sha)
    if out.exists() or out.is_symlink():
        fail('The signing output folder must be new.')
    archive_raw = regular_bytes(archive, MAX_ZIP)
    manifest_raw = regular_bytes(manifest, MAX_JSON)
    if sha(archive_raw) != archive_sha or sha(manifest_raw) != manifest_sha:
        fail('The reviewed archive or BUILD-MANIFEST.json SHA-256 does not match.')
    document = parse_json(manifest_raw)
    metadata, files, links = check_manifest(document, archive, archive_raw, archive_sha, system, arch)
    with candidate_zip(archive_raw) as bundle:
        entries, modes, directories, sidecars = zip_layout(bundle, files, links, metadata['entries'], system, arch)
        if system == 'darwin':
            candidate_quarantine(archive)
        # Everything has passed before the first output write. Extract only from immutable checked bytes.
        out.mkdir(mode=0o700)
        dist = out / 'dist'
        dist.mkdir(mode=0o700 if system == 'darwin' else 0o777)
        if sidecars:
            extract_mac_metadata(out, bundle, entries, modes, directories, links, sidecars)
        else:
            for name in sorted(directories):
                directory = dist / name
                directory.mkdir()
                if system == 'darwin':
                    os.chmod(directory, MAC_DIR_MODE, follow_symlinks=False)
            for name, mode in modes.items():
                path = dist / name
                with bundle.open(entries[name]) as source, path.open('xb') as target:
                    if file_digest(source, target) != files[name]:
                        fail('Extracted file digest mismatch.')
                    if system == 'darwin':
                        os.fchmod(target.fileno(), mode)
                if system != 'darwin':
                    path.chmod(mode)
            for name, target in links.items():
                (dist / name).symlink_to(target)
    tree = snapshot(dist, system)
    if (tree['files'] != files or tree['symlinks'] != links or tree['directories'] != sorted(directories)
            or (system == 'darwin'
                and tree['permissions'] != {**dict.fromkeys(directories, MAC_DIR_MODE), **modes})):
        fail('Extracted candidate does not match the verified ZIP.')
    return save_state(out, {
        'schema': 1, 'platform': system, 'architecture': arch, 'version': document['source_version'],
        'input': {'archive': metadata, 'build_manifest_sha256': manifest_sha,
                  'runtime_manifest_sha256': document['runtime_manifest_sha256'],
                  'third_party_notices': document['third_party_notices']},
        'stage': 'verified-input', 'tree_sha256': sha(json_bytes(tree)),
        'evidence': {'verified-input': {'tree_sha256': sha(json_bytes(tree))}},
    })


def windows_system_root():
    import ctypes
    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetSystemWindowsDirectoryW(buffer, len(buffer))
    if not 0 < length < len(buffer) or not Path(buffer.value).is_absolute():
        fail('Cannot establish the Windows system directory.')
    return Path(buffer.value)


def safe_env(system, temporary=None):
    # No injected Python, dynamic loader, credential, shell, SDK or runner variables.
    if system == 'win32':
        system_root = windows_system_root()
        env = {'SystemRoot': str(system_root), 'WINDIR': str(system_root), 'PATH': str(system_root / 'System32')}
    else:
        env = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LC_ALL': 'C'}
    if temporary is not None:
        home = temporary / 'home'
        home.mkdir()
        env.update({key: str(home) for key in ('HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA',
                                               'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME')})
        env.update({key: str(temporary) for key in ('TMP', 'TEMP', 'TMPDIR')})
    return env


class NativeCommands:
    def __init__(self, system):
        self.system = system
        self.cwd = windows_system_root() if system == 'win32' else Path('/')
        self.deadline = time.monotonic() + 24 * 60

    def run(self, args, failure, *, timeout=60, capture=False, temporary=None):
        # True captures both streams (codesign display writes to stderr).
        # 'stdout' keeps machine-readable output separate from diagnostic text.
        seconds = min(timeout, self.deadline - time.monotonic())
        if seconds <= 0:
            fail('Native phase time budget expired.')
        try:
            result = subprocess.run([str(arg) for arg in args], check=True, timeout=seconds,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                                    stderr=subprocess.STDOUT if capture is True else subprocess.DEVNULL,
                                    cwd=temporary or self.cwd, env=safe_env(self.system, temporary))
        except (OSError, subprocess.SubprocessError):
            fail(failure)
        return result.stdout if capture else None


def macho(path):
    with path.open('rb') as stream:
        header = stream.read(8)
    if len(header) != 8:
        return False
    thin = {b'\xfe\xed\xfa\xce', b'\xfe\xed\xfa\xcf', b'\xce\xfa\xed\xfe', b'\xcf\xfa\xed\xfe'}
    fat = {b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf', b'\xbe\xba\xfe\xca', b'\xbf\xba\xfe\xca'}
    magic = header[:4]
    if magic in thin:
        return True
    if magic in fat:  # Exclude Java class files, which also start with CAFEBABE.
        return 0 < int.from_bytes(header[4:], 'big' if magic[0] == 0xca else 'little') <= 16
    return False


def pe(path):
    with path.open('rb') as stream:
        header = stream.read(64)
        if len(header) != 64 or header[:2] != b'MZ':
            return False
        offset = int.from_bytes(header[60:64], 'little')
        if offset < 64:
            return False
        stream.seek(offset)
        return stream.read(4) == b'PE\x00\x00'


def mac_targets(dist, tree):
    """Return deepest code first, with explicit version selection for standard frameworks."""
    names, links = tree['files'], tree['symlinks']
    directories = set(tree['directories'])
    main = EXECUTABLES['darwin']
    binaries = {name for name in names if macho(dist / name)}
    if main not in binaries:
        fail('The Mac launcher is not Mach-O code.')
    targets = {name: [] for name in binaries - {main}}
    bundle_types = ('.app', '.appex', '.bundle', '.plugin', '.xpc')
    for name in sorted(directories - {ROOTS['darwin']}):
        if name.endswith('.framework'):
            versions = name + '/Versions'
            version = links.get(versions + '/Current')
            # Only a conventional single-version framework is automatic. Do not guess at flattened,
            # unusual multi-version, or synthetic framework layouts in frozen builds.
            actual = {child[len(versions) + 1:] for child in directories if child.rpartition('/')[0] == versions}
            stem = Path(name).stem
            if (not isinstance(version, str) or not re.fullmatch(r'[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*', version)
                    or actual != {version} or f'{versions}/{version}/{stem}' not in binaries
                    or links.get(f'{name}/{stem}') != f'Versions/Current/{stem}'):
                fail('Framework layout requires native review before signing.')
            targets[name] = ['--bundle-version', version]
        elif name.endswith(bundle_types):
            targets[name] = []
    return [(dist / name, targets[name]) for name in sorted(targets, key=lambda p: (-p.count('/'), p))] + [
        (dist / ROOTS['darwin'], [])]


def windows_targets(dist, tree):
    found = []
    for name in sorted(tree['files']):
        binary = pe(dist / name)
        if binary:
            found.append(dist / name)
        elif Path(name).suffix.lower() in {'.exe', '.dll', '.pyd'}:
            fail('A Windows code file is not a PE binary.')
    if dist / EXECUTABLES['win32'] not in found:
        fail('The Windows launcher is not PE code.')
    return found


def windows_signtool(requested):
    # Never use PATH, the artifact, or an arbitrary executable supplied by the caller.
    import winreg
    installed_roots = set()
    for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Windows Kits\Installed Roots',
                                access=winreg.KEY_READ | view) as installed:
                registered, value_type = winreg.QueryValueEx(installed, 'KitsRoot10')
        except FileNotFoundError:
            continue
        if value_type != winreg.REG_SZ or not Path(registered).is_absolute():
            fail('Cannot establish the installed Windows SDK location.')
        installed_roots.add(Path(registered).resolve())
    if len(installed_roots) != 1:
        fail('Cannot establish a single installed Windows SDK location.')
    kits = installed_roots.pop() / 'bin'
    tool = Path(requested)
    if not tool.is_absolute() or tool.resolve() != tool:
        fail('Use the absolute installed x64 Windows SDK signtool.exe path.')
    try:
        relative = tool.relative_to(kits.resolve())
    except ValueError:
        fail('SignTool must come from the installed Windows SDK.')
    if (len(relative.parts) != 3 or not re.fullmatch(r'10\.0\.[0-9]+\.[0-9]+', relative.parts[0])
            or relative.parts[1:] != ('x64', 'signtool.exe') or not stat.S_ISREG(tool.lstat().st_mode)
            or reparse(tool.lstat())):
        fail('Use the installed x64 Windows SDK signtool.exe.')
    return tool


def require_identity(identity):
    if not isinstance(identity, str) or not THUMBPRINT.fullmatch(identity):
        fail('Supply a 40-hex certificate thumbprint.')
    return identity.upper()


def require_team(team):
    if not isinstance(team, str) or not TEAM_ID.fullmatch(team):
        fail('Supply a 10-character uppercase Apple Team ID.')
    return team


def signature_display(run, path, version):
    return run([CODESIGN, '--display', '--verbose=4', *version, path],
               'Cannot inspect Developer ID signature metadata.', capture=True)


def display_team(display):
    teams = re.findall(rb'(?m)^TeamIdentifier=([^\n]*)$', display)
    if len(teams) != 1 or not re.fullmatch(rb'[A-Z0-9]{10}', teams[0]):
        fail('Invalid Developer ID signature TeamIdentifier.')
    return teams[0].decode('ascii')


def verify_mac(run, targets, identity, team):
    requirement = (f'{DEVELOPER_ID} and certificate leaf = H"{require_identity(identity)}"'
                   f' and certificate leaf[subject.OU] = "{require_team(team)}"')
    for path, version in targets:
        deep = ['--deep'] if path.suffix == '.app' else []
        run([CODESIGN, '--verify', '--strict', *deep, *version, '-R=' + requirement, path],
            'Developer ID signature verification failed.')
        display = signature_display(run, path, version)
        if display_team(display) != team:
            fail('Developer ID signature TeamIdentifier does not match the signing anchor.')
        if (not re.search(rb'(?m)\bflags=.*\([^\n)]*\bruntime\b', display)
                or not re.search(rb'(?m)^Timestamp=\S', display)):
            fail('Developer ID signature lacks hardened runtime or a secure timestamp.')


def verify_windows(run, tool, targets):
    for path in targets:
        # /tw emits a warning for a missing timestamp; SignTool returns 2 for warnings.
        run([tool, 'verify', '/pa', '/all', '/tw', path], 'Authenticode or timestamp verification failed.')


def sign(out, state, tree, options, run):
    system, dist = state['platform'], out / 'dist'
    identity = require_identity(options.identity)
    if system == 'darwin':
        if options.signtool or options.timestamp_url or not options.keychain:
            fail('Mac signing requires only the explicit temporary signing keychain and identity.')
        keychain = Path(options.keychain)
        if (not keychain.is_absolute() or keychain.resolve() != keychain or not keychain.name.endswith('.keychain-db')
                or keychain.is_relative_to(out.resolve()) or not stat.S_ISREG(keychain.lstat().st_mode)
                or reparse(keychain.lstat()) or keychain.lstat().st_nlink != 1):
            fail('Supply an absolute regular temporary signing keychain path.')
        targets = mac_targets(dist, tree)
        for path, version in targets:
            run([CODESIGN, '--force', '--sign', identity, '--keychain', keychain,
                 '--options', 'runtime', '--timestamp', *version, path],
                'Developer ID signing failed; check the identity and frozen-code layout.', timeout=120)
        after = snapshot(dist, system, normalize_from=tree)
        team = display_team(signature_display(run, *targets[-1]))
        verify_mac(run, targets, identity, team)
        if snapshot(dist, system) != after:
            fail('The signed tree changed during verification.')
        evidence = {'identity': identity, 'team_id': team, 'signature': SIGNATURES[system],
                    'targets_verified': len(targets)}
        state['signing_keychain'] = str(keychain)  # Local state only, never in final output.
    else:
        if not options.signtool or not options.timestamp_url or options.keychain:
            fail('Windows signing requires only the installed SignTool, identity and RFC 3161 timestamp URL.')
        tool = windows_signtool(options.signtool)
        url = options.timestamp_url
        parsed = urlsplit(url)
        if (not re.fullmatch(r'https://[A-Za-z0-9.-]+(?::[0-9]+)?(?:/[A-Za-z0-9._/-]*)?', url)
                or not parsed.hostname or parsed.username or parsed.password):
            fail('Supply a public HTTPS timestamp URL without credentials or query parameters.')
        targets = windows_targets(dist, tree)
        for path in targets:
            run([tool, 'sign', '/sha1', identity, '/fd', 'SHA256', '/tr', url, '/td', 'SHA256', path],
                'Authenticode signing or timestamping failed.', timeout=120)
        verify_windows(run, tool, targets)
        evidence = {'identity': identity, 'signature': SIGNATURES[system],
                    'targets_verified': len(targets)}
        state['signtool'] = str(tool)
        after = snapshot(dist, system)
    check_notices(state['input']['third_party_notices'], system, after['files'])
    if after == tree:
        fail('Signing did not change the unsigned candidate tree.')
    return advance(out, state, 'signed', after, evidence)


def credential_check(state, run):
    system = state['platform']
    identity = state['evidence']['signed']['identity']
    if system == 'darwin':
        keychain = Path(state['signing_keychain'])
        if keychain.exists() or keychain.is_symlink():
            fail('Remove the temporary signing keychain before executing the candidate.')
        found = run(['/usr/bin/security', 'find-identity', '-p', 'codesigning'],
                    'Could not check visible signing identities.', capture=True)
        if not re.search(rb'\d+ (?:identity|identities) found', found):
            fail('Could not interpret the signing identity inventory.')
        if identity.encode() in found.upper():
            fail('The signing identity is still visible.')
    else:
        if not THUMBPRINT.fullmatch(identity):
            fail('Invalid signing identity evidence.')
        root = windows_system_root()
        ps = root / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
        script = ("$ErrorActionPreference='Stop'; "
                  "$certs=@(Get-ChildItem Cert:\\CurrentUser\\My; Get-ChildItem Cert:\\LocalMachine\\My); "
                  f"if ($certs.Thumbprint -contains '{identity}') {{ exit 1 }}; exit 0")
        run([ps, '-NoProfile', '-NonInteractive', '-Command', script],
            'Remove the signing certificate from the Personal certificate stores and verify access.')


def verify_current(out, state, tree, run):
    dist = out / 'dist'
    system = state['platform']
    targets = mac_targets(dist, tree) if system == 'darwin' else windows_targets(dist, tree)
    if len(targets) != state['evidence']['signed']['targets_verified']:
        fail('The signing target count does not match the recorded evidence.')
    if system == 'darwin':
        anchor = signed_anchor(state)
        verify_mac(run, targets, anchor['identity'], anchor['team_id'])
    else:
        verify_windows(run, windows_signtool(state['signtool']), targets)


def check_self_test_report(report, version):
    if (not isinstance(report, dict)
            or set(report) != {'ok', 'frozen', 'seed', 'assistant_kit', 'version', 'tcl', 'tcl_patch'}
            or report.get('version') != version
            or not all(report.get(key) is True for key in ('ok', 'frozen', 'seed', 'assistant_kit', 'tcl'))
            or not isinstance(report['tcl_patch'], str) or len(report['tcl_patch']) > 32
            or not TCL_PATCH.fullmatch(report['tcl_patch'])):
        fail('The signed executable did not return the expected self-test result.')


def self_test(out, state, tree, options, run):
    if not options.credentials_removed:
        fail('Attest that signing credential access is gone before self-test.')
    credential_check(state, run)
    verify_current(out, state, tree, run)
    if snapshot(out / 'dist', state['platform']) != tree:
        fail('The signed tree changed before self-test.')
    with tempfile.TemporaryDirectory(prefix='resonance-signed-test-') as folder:
        temporary = Path(folder).resolve()
        report_path = temporary / 'report.json'
        run([out / 'dist' / EXECUTABLES[state['platform']], '--self-test', '--data-dir', temporary / 'data',
             '--log-dir', temporary / 'logs', '--self-test-report', report_path, '--port', '0'],
            'The signed executable failed its no-window self-test.', timeout=120, temporary=temporary)
        report = parse_json(regular_bytes(report_path, 65536))
    check_self_test_report(report, state['version'])
    if snapshot(out / 'dist', state['platform']) != tree:
        fail('Self-test changed the signed tree.')
    return advance(out, state, 'self-tested', tree, {
        'signing_credentials_removed': True, 'port': 0, 'report': report,
        'report_sha256': sha(json_bytes(report)),
    })


def make_mac_zip(run, app, archive):
    run(['/usr/bin/ditto', '-c', '-k', '--keepParent', '--sequesterRsrc', app, archive],
        'Could not archive the Mac app.', timeout=180)


def checked_mac_zip(archive, state, tree):
    raw = regular_bytes(archive, MAX_ZIP)
    with candidate_zip(raw) as bundle:
        entries = len(bundle.infolist())
        _, modes, dirs, _ = zip_layout(bundle, tree['files'], tree['symlinks'], entries,
                                        'darwin', state['architecture'], mac_transport=True)
        if (sorted(dirs) != tree['directories']
                or {**dict.fromkeys(dirs, MAC_DIR_MODE), **modes} != tree['permissions']):
            fail('The Mac ZIP does not reproduce the checked tree.')
    return raw, entries


def validate_ticket(run, app):
    run(['/usr/bin/xcrun', 'stapler', 'validate', app], 'Stapled ticket validation failed.', timeout=90)


def notarize(out, state, tree, options, run):
    profile = options.keychain_profile
    if not NOTARY_PROFILE.fullmatch(profile):
        fail('Supply the explicit notarytool keychain profile name.')
    keychain_args = []
    if options.notary_keychain is not None:
        keychain = options.notary_keychain
        if (not keychain.is_absolute() or keychain.resolve() != keychain or not keychain.name.endswith('.keychain-db')
                or keychain.is_relative_to(out.resolve()) or not keychain.is_file()
                or reparse(keychain.lstat()) or keychain.lstat().st_nlink != 1):
            fail('Supply an absolute regular temporary notary keychain path outside the stage.')
        keychain_args = ['--keychain', keychain]
    dist = out / 'dist'
    app = dist / ROOTS['darwin']
    credential_check(state, run)
    verify_current(out, state, tree, run)
    with tempfile.TemporaryDirectory(prefix='resonance-notary-') as folder:
        upload = Path(folder) / 'notary-input.zip'
        make_mac_zip(run, app, upload)
        raw, _ = checked_mac_zip(upload, state, tree)
        archive_sha = sha(raw)
        del raw
        if snapshot(dist, 'darwin') != tree:
            fail('The signed tree changed before notarization.')
        result = run(['/usr/bin/xcrun', 'notarytool', 'submit', upload, '--keychain-profile', profile,
                      *keychain_args, '--wait', '--timeout', '12m', '--output-format', 'json'],
                     'Notary submission failed or timed out; check the Apple submission before retrying.',
                     timeout=780, capture='stdout')
    receipt = parse_json(result)
    if not isinstance(receipt, dict) or receipt.get('status') != 'Accepted':
        fail('Apple did not return an Accepted notarization result.')
    try:
        submission = str(uuid.UUID(receipt['id']))
    except (KeyError, TypeError, AttributeError, ValueError):
        fail('The Accepted notarization result is missing a valid submission ID.')
    if snapshot(dist, 'darwin') != tree:
        fail('The signed tree changed before stapling.')
    run(['/usr/bin/xcrun', 'stapler', 'staple', app], 'Stapling failed.', timeout=90)
    stapled = snapshot(dist, 'darwin', normalize_from=tree)
    validate_ticket(run, app)
    check_notices(state['input']['third_party_notices'], 'darwin', stapled['files'])
    verify_current(out, state, stapled, run)
    if snapshot(dist, 'darwin') != stapled:
        fail('The stapled tree changed during verification.')
    return advance(out, state, 'notarized', stapled, {
        'status': 'Accepted', 'submission_id': submission, 'keychain_profile': profile,
        'submission_archive_sha256': archive_sha, 'ticket_validated': True, 'signed_anchor': signed_anchor(state),
        'sign_state_sha256': options.expected_sign_state_sha256,
    })


def finalize(out, state, tree, options, run):
    system = state['platform']
    dist = out / 'dist'
    credential_check(state, run)
    verify_current(out, state, tree, run)
    release = out / 'final'
    if release.exists() or release.is_symlink():
        fail('The final output already exists.')
    with tempfile.TemporaryDirectory(prefix='.final-', dir=out) as folder:
        stage = Path(folder) / 'payload'
        stage.mkdir()
        name = state['input']['archive']['name'].removesuffix('-candidate.zip') + '-signed.zip'
        archive = stage / name
        if system == 'darwin':
            app = dist / ROOTS[system]
            validate_ticket(run, app)
            make_mac_zip(run, app, archive)
            raw, entries = checked_mac_zip(archive, state, tree)
            # Test the actual archive only after its metadata passes the same policy as the carrier.
            unpacked = Path(folder) / 'unpacked'
            unpacked.mkdir()
            run(['/usr/bin/ditto', '-x', '-k', archive, unpacked], 'Could not reopen the final Mac ZIP.', timeout=180)
            if snapshot(unpacked, system) != tree:
                fail('The final Mac ZIP does not reproduce the signed/stapled tree.')
            anchor = state['evidence']['notarized']['signed_anchor']
            verify_mac(run, mac_targets(unpacked, tree), anchor['identity'], anchor['team_id'])
            validate_ticket(run, unpacked / ROOTS[system])
            if snapshot(unpacked, system) != tree:
                fail('The unpacked Mac ZIP tree changed during verification.')
        else:
            with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED) as bundle:
                for directory in tree['directories']:
                    bundle.write(dist / directory, directory)
                for filename in tree['files']:
                    bundle.write(dist / filename, filename)
            with zipfile.ZipFile(archive) as bundle:
                _, _, dirs, _ = zip_layout(bundle, tree['files'], {},
                                        len(tree['files']) + len(tree['directories']), system, state['architecture'])
                if sorted(dirs) != tree['directories']:
                    fail('The final Windows ZIP does not reproduce the signed tree.')
            raw = regular_bytes(archive, MAX_ZIP)
            with candidate_zip(raw) as bundle:
                entries = len(bundle.infolist())
        if snapshot(dist, system) != tree:
            fail('The staged tree changed while archiving.')
        evidence = {'tree_sha256': sha(json_bytes(tree)), 'archive_rechecked': True}
        if system == 'darwin':
            evidence['signed_anchor'] = anchor
            evidence['sign_state_sha256'] = state['evidence']['notarized']['sign_state_sha256']
        record = {
            'schema': 1, 'product': 'Resonance', 'source_version': state['version'],
            'platform': system, 'architecture': state['architecture'],
            'status': 'Developer ID signed, notarized and stapled' if system == 'darwin' else 'Authenticode signed',
            'input': state['input'],
            'archive': {'name': name, 'sha256': sha(raw), 'bytes': len(raw), 'entries': entries},
            **tree, 'signed_self_test': state['evidence']['self-tested']['report'],
            'evidence': {**state['evidence'], 'finalized': evidence},
        }
        manifest_raw = json_bytes(record)
        (stage / 'RELEASE-BUILD-MANIFEST.json').write_bytes(manifest_raw)
        stage.rename(release)
    return advance(out, state, 'finalized', tree, {**evidence, 'release_manifest_sha256': sha(manifest_raw),
                                                    'release_archive_sha256': record['archive']['sha256']})


def advance(out, state, stage, tree, evidence):
    digest = sha(json_bytes(tree))
    state['stage'] = stage
    state['tree_sha256'] = digest
    state['evidence'][stage] = {**evidence, 'tree_sha256': digest}
    return save_state(out, state)


def signed_anchor(state):
    return {key: state['evidence']['signed'][key] for key in ('tree_sha256', 'identity', 'team_id')}


def check_sign_checkpoint(state, expected):
    require_sha(expected)
    evidence = state['evidence']
    # Recreate the signed state before self-test or stapling changed the stage.
    signed_state = {**state, 'stage': 'signed', 'tree_sha256': evidence['signed']['tree_sha256'],
                    'evidence': {step: evidence[step] for step in ('verified-input', 'signed')}}
    if sha(json_bytes(signed_state)) != expected:
        fail('Release evidence does not match the original SIGN checkpoint.')


def recorded_path(value, path_type):
    if (not isinstance(value, str) or not 0 < len(value) <= 4096
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        fail('Invalid recorded credential or tool path.')
    path = path_type(value)
    if not path.is_absolute() or str(path) != value or '..' in path.parts:
        fail('Invalid recorded credential or tool path.')
    return path


def check_state(state, system, arch, stage):
    stages = ['verified-input', 'signed', 'self-tested']
    if system == 'darwin':
        stages.append('notarized')
    stages = stages[:stages.index(stage) + 1]
    tool_path = 'signing_keychain' if system == 'darwin' else 'signtool'
    keys = ['schema', 'platform', 'architecture', 'version', 'input', 'stage', 'tree_sha256', 'evidence']
    if 'signed' in stages:
        keys.append(tool_path)
    if (not isinstance(state, dict) or type(state.get('schema')) is not int or state['schema'] != 1
            or state.get('platform') != system or state.get('architecture') != arch or state.get('stage') != stage):
        fail('Required prior phase evidence or native platform does not match.')
    shape(state, keys)
    version = state['version']
    if not valid_version(version):
        fail('Invalid release version evidence.')
    inputs = state['input']
    shape(inputs, ('archive', 'build_manifest_sha256', 'runtime_manifest_sha256', 'third_party_notices'))
    require_sha(inputs['build_manifest_sha256'])
    require_sha(inputs['runtime_manifest_sha256'])
    check_notices(inputs['third_party_notices'], system)
    archive = inputs['archive']
    shape(archive, ('name', 'sha256', 'bytes', 'entries'))
    if archive['name'] != candidate_name(version, system, arch):
        fail('Invalid candidate archive basename in release evidence.')
    require_sha(archive['sha256'])
    count(archive['bytes'], MAX_ZIP)
    count(archive['entries'], MAX_ENTRIES)

    evidence = state['evidence']
    shape(evidence, stages)
    fields = {
        'verified-input': (),
        'signed': ('identity', 'signature', 'targets_verified') + (('team_id',) if system == 'darwin' else ()),
        'self-tested': ('signing_credentials_removed', 'port', 'report', 'report_sha256'),
        'notarized': ('status', 'submission_id', 'keychain_profile', 'submission_archive_sha256',
                      'ticket_validated', 'signed_anchor', 'sign_state_sha256'),
    }
    for step in stages:
        shape(evidence[step], ('tree_sha256', *fields[step]))
        require_sha(evidence[step]['tree_sha256'])
    if require_sha(state['tree_sha256']) != evidence[stage]['tree_sha256']:
        fail('The stage tree digest does not match its phase evidence.')

    if 'signed' in stages:
        signed = evidence['signed']
        if (signed['identity'] != require_identity(signed['identity']) or signed['signature'] != SIGNATURES[system]
                or signed['tree_sha256'] == evidence['verified-input']['tree_sha256']):
            fail('Invalid signed phase evidence.')
        count(signed['targets_verified'], MAX_ENTRIES)
        if system == 'darwin':
            require_team(signed['team_id'])
            keychain = recorded_path(state[tool_path], PurePosixPath)
            if not keychain.name.endswith('.keychain-db'):
                fail('Invalid recorded signing keychain path.')
        else:
            sdk = recorded_path(state[tool_path], PureWindowsPath)
            if (len(sdk.parts) < 5 or sdk.parts[-4] != 'bin' or sdk.parts[-2:] != ('x64', 'signtool.exe')
                    or not re.fullmatch(r'10\.0\.[0-9]+\.[0-9]+', sdk.parts[-3])):
                fail('Invalid recorded Windows SDK path.')
    if 'self-tested' in stages:
        tested = evidence['self-tested']
        check_self_test_report(tested['report'], version)
        if (tested['signing_credentials_removed'] is not True or type(tested['port']) is not int or tested['port'] != 0
                or require_sha(tested['report_sha256']) != sha(json_bytes(tested['report']))
                or tested['tree_sha256'] != evidence['signed']['tree_sha256']):
            fail('The self-tested evidence does not match the signed phase.')
    if 'notarized' in stages:
        receipt = evidence['notarized']
        require_sha(receipt['submission_archive_sha256'])
        submission, profile = receipt['submission_id'], receipt['keychain_profile']
        if (receipt['status'] != 'Accepted' or receipt['ticket_validated'] is not True
                or not isinstance(profile, str) or not NOTARY_PROFILE.fullmatch(profile)
                or not isinstance(submission, str)
                or not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', submission)
                or receipt['signed_anchor'] != signed_anchor(state)):
            fail('Invalid notarization evidence or signing anchor.')
        check_sign_checkpoint(state, receipt['sign_state_sha256'])


def check_notary_anchor(state, options):
    expected = {
        'tree_sha256': require_sha(options.expected_signed_tree_sha256),
        'identity': require_identity(options.expected_identity),
        'team_id': require_team(options.expected_team_id),
    }
    if signed_anchor(state) != expected or state['evidence']['self-tested']['tree_sha256'] != expected['tree_sha256']:
        fail('The self-tested stage does not match the independent SIGN anchor.')
    check_sign_checkpoint(state, options.expected_sign_state_sha256)


def load_phase(out, expected_state, action, options):
    system, arch = host_target()
    require_sha(expected_state)
    if out.is_symlink() or not out.is_dir() or reparse(out.lstat()):
        fail('Signing output folder is missing or redirected.')
    before = {
        'sign': 'verified-input', 'self-test': 'signed', 'notarize': 'self-tested',
        'finalize': 'notarized' if system == 'darwin' else 'self-tested',
        'signed-anchor': 'signed', 'check-notarize': 'self-tested',
    }
    if action in {'notarize', 'check-notarize'} and system != 'darwin':
        fail('Notarization is only for the native Mac candidate.')
    if action == 'signed-anchor' and system != 'darwin':
        fail('The signed anchor export is only for the native Mac candidate.')
    raw = regular_bytes(out / STATE, MAX_JSON)
    if sha(raw) != expected_state:
        fail('Signing checkpoint digest does not match.')
    state = parse_json(raw)
    check_state(state, system, arch, before[action])
    if action in {'notarize', 'check-notarize'}:
        check_notary_anchor(state, options)
    if system == 'darwin' and 'signing_keychain' in state:
        keychain = Path(state['signing_keychain'])
        if keychain.resolve() != keychain or keychain.is_relative_to(out.resolve()):
            fail('Invalid recorded signing keychain path.')
    return state


def checked_tree(out, state):
    tree = snapshot(out / 'dist', state['platform'])
    check_notices(state['input']['third_party_notices'], state['platform'], tree['files'])
    if sha(json_bytes(tree)) != state['tree_sha256']:
        fail('The staged tree has changed since the preceding phase.')
    return tree


def phase(out, expected_state, action, options):
    state = load_phase(out, expected_state, action, options)
    if action in {'signed-anchor', 'check-notarize'}:
        checked_tree(out, state)
        if action == 'signed-anchor':
            keychain = Path(state['signing_keychain'])
            if keychain.exists() or keychain.is_symlink():
                fail('Remove the temporary signing keychain before exporting the signed anchor.')
            return json.dumps(signed_anchor(state), sort_keys=True)
        return expected_state

    lock = out / '.phase-lock'
    try:
        lock.mkdir(mode=0o700)
    except FileExistsError:
        fail('Signing stage is locked; use a new output folder after an interrupted run.')
    try:
        if sha(regular_bytes(out / STATE, MAX_JSON)) != expected_state:
            fail('Signing checkpoint digest does not match.')
        tree = checked_tree(out, state)
        system, _ = native()
        function = {'sign': sign, 'self-test': self_test, 'notarize': notarize, 'finalize': finalize}[action]
        return function(out, state, tree, options, NativeCommands(system).run)
    finally:
        lock.rmdir()


class ReleaseParser(argparse.ArgumentParser):
    def error(self, _message):
        # A mistyped option must not cause an accidental password argument to be echoed.
        self.exit(2, 'Signing stopped: invalid arguments. See --help for the selected phase.\n')


def main(argv=None):
    parser = ReleaseParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    verify = sub.add_parser('verify', help='verify both reviewed digests and stage the exact candidate')
    verify.add_argument('--archive', type=Path, required=True)
    verify.add_argument('--manifest', type=Path, required=True)
    verify.add_argument('--archive-sha256', required=True)
    verify.add_argument('--manifest-sha256', required=True)
    verify.add_argument('--out', type=Path, required=True)
    phases = {}
    for action in ('sign', 'signed-anchor', 'self-test', 'check-notarize', 'notarize', 'finalize'):
        item = sub.add_parser(action)
        item.add_argument('--out', type=Path, required=True)
        item.add_argument('--state-sha256', required=True)
        phases[action] = item
    phases['sign'].add_argument('--identity', required=True, help='certificate SHA-1 thumbprint, not a password')
    phases['sign'].add_argument('--keychain', help='Mac temporary signing keychain path')
    phases['sign'].add_argument('--signtool', help='Windows absolute SDK signtool.exe path')
    phases['sign'].add_argument('--timestamp-url', help='public Windows RFC 3161 timestamp URL')
    phases['self-test'].add_argument('--credentials-removed', action='store_true', required=True)
    for action in ('check-notarize', 'notarize'):
        phases[action].add_argument('--expected-sign-state-sha256', required=True)
        phases[action].add_argument('--expected-signed-tree-sha256', required=True)
        phases[action].add_argument('--expected-identity', required=True)
        phases[action].add_argument('--expected-team-id', required=True)
    phases['notarize'].add_argument('--keychain-profile', required=True)
    phases['notarize'].add_argument('--notary-keychain', type=Path, help='Mac temporary notary keychain path')
    options = parser.parse_args(argv)
    try:
        out = options.out.absolute()
        if options.action == 'verify':
            digest = verify_candidate(out, options.archive, options.manifest, options.archive_sha256,
                                      options.manifest_sha256)
        else:
            digest = phase(out, options.state_sha256, options.action, options)
    except SigningError as exc:
        print(f'Signing stopped: {exc}', file=sys.stderr)
        return 1
    except (OSError, KeyError, TypeError, ValueError, AttributeError, RuntimeError, RecursionError,
            NotImplementedError, zipfile.BadZipFile):
        # Native output, archive content and caller paths are not copied to the log.
        print('Signing stopped: invalid, missing or unreadable release evidence.', file=sys.stderr)
        return 1
    print(digest)
    return 0


if __name__ == '__main__':
    sys.exit(main())
