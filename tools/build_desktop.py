#!/usr/bin/env python3
"""Build a self-contained desktop candidate from an audited release export. Never publishes.

Run it on the operating system you are building for: PyInstaller cannot cross-compile,
so a Mac builds the Mac app and a Windows computer builds the Windows folder.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import plistlib
import re
import shlex
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import zipfile

if __name__ == '__main__':
    sys.dont_write_bytecode = True
if __package__:
    from . import make_release, prepare_build, sign_release
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.path.insert(1, str(Path(__file__).resolve().parents[1]))
    import make_release
    import prepare_build
    import sign_release

PYINSTALLER_VERSION, TZDATA_VERSION = '6.22.3', '2026.2'
MINIMUM_TK = (8, 6, 0)  # Apple's system Tcl/Tk 8.5 can draw blank labels.
PATCH_LEVEL = re.compile(r'[0-9]+\.[0-9]+\.[0-9]+')
APP_NAME = 'Resonance'
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
VERSION_PATTERN = re.compile(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?')
SKIPPED_DIRECTORIES = frozenset(('.git', '__pycache__'))
IGNORED_FILES = frozenset(('.DS_Store',))
# Names never allowed in a runtime, whatever the allowlist says.
PRIVATE_NAME = re.compile(r'(?:^|/)(?:docs/internal|experiments|private|data|backups|reports|scans)(?:/|$)|'
                          r'(?:^|/)(?:STATUS\.md|OPENER[^/]*\.md|\.env[^/]*|[^/]*\.sqlite[^/]*|[^/]*\.db[^/]*|[^/]*\.log)$')
# The packaged files. Python modules are frozen into the app; the same source ships as data
# only because the assistant commands (python3 monitor.py ...) run from the app's data root.
SOURCE_MODULES = ('assistant_kit.py', 'image_post.py', 'monitor.py', 'policy.py', 'reader_checks.py', 'samples.py', 'server.py', 'workflow.py')
NOTICES = 'THIRD-PARTY-NOTICES.txt'
NOTICE_PATHS = {'darwin': f'{APP_NAME}.app/Contents/Resources/{NOTICES}',
                'win32': f'{APP_NAME}/_internal/{NOTICES}'}
DATA_ROOT_FILES = ('AGENTS.md', 'ASSISTANT.md', 'AUTHORING.md', 'CHANGELOG.md', 'INTEGRATIONS.md', 'LICENSE',
                   'PRODUCT.md', 'README.md', 'SECURITY.md', 'VERSION', NOTICES) + SOURCE_MODULES
DATA_DOCS = ('docs/ACTIONS.md', 'docs/MONITORING.md')
REQUIRED = frozenset(('desktop/launch.py', 'desktop/hooks/hook-workflow.py', 'release.json', 'requirements.txt',
                      'static/index.html', 'static/icon.svg', 'examples/practice.seed.json') + DATA_ROOT_FILES + DATA_DOCS)
HIDDEN_IMPORTS = ('assistant_kit', 'server', 'policy', 'workflow', 'samples', 'reader_checks', 'tkinter', 'tzdata')
ICON_RENDERER = 'mac-launcher/render-icon.swift'
WINDOWS_ICON_SOURCE = 'browser-reader/icon-128.png'


BuildError = prepare_build.BuildError


def sha256(content):
    return hashlib.sha256(content).hexdigest()


def valid_name(name):
    if not isinstance(name, str) or not name or name == 'RELEASE-MANIFEST.json':
        raise BuildError('The release manifest lists an invalid path.')
    parts = name.split('/')
    if ('\\' in name or ':' in name or name.startswith('/') or '' in parts or '.' in parts or '..' in parts or
            any(ord(character) < 32 for character in name) or PurePosixPath(name).as_posix() != name):
        raise BuildError('The release manifest lists an unsafe path: ' + repr(name))
    if PRIVATE_NAME.search(name):
        raise BuildError('The runtime lists a private or internal file: ' + name)
    if not make_release.public_path(name):
        raise BuildError('The runtime lists a file outside the public allowlist: ' + name)
    return name


def read_manifest(runtime):
    path = runtime / 'RELEASE-MANIFEST.json'
    try:
        status = os.lstat(path)
        if not stat.S_ISREG(status.st_mode) or status.st_size > MAX_MANIFEST_BYTES:
            raise BuildError('RELEASE-MANIFEST.json must be an ordinary file of reasonable size.')
        raw = path.read_bytes()
        manifest = json.loads(raw.decode('utf-8'))
    except FileNotFoundError:
        raise BuildError('This is not an audited release export: RELEASE-MANIFEST.json is missing.') from None
    except (OSError, UnicodeError, ValueError) as error:
        if isinstance(error, BuildError):
            raise
        raise BuildError('RELEASE-MANIFEST.json is not valid.') from None
    if (not isinstance(manifest, dict) or set(manifest) != {'version', 'files'} or
            not isinstance(manifest['version'], str) or not VERSION_PATTERN.fullmatch(manifest['version']) or
            not isinstance(manifest['files'], dict) or not manifest['files']):
        raise BuildError('RELEASE-MANIFEST.json is not valid.')
    for name, item in manifest['files'].items():
        valid_name(name)
        if (not isinstance(item, dict) or set(item) != {'sha256', 'bytes'} or
                not isinstance(item['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', item['sha256']) or
                type(item['bytes']) is not int or item['bytes'] < 0):
            raise BuildError('RELEASE-MANIFEST.json has an invalid entry for ' + name)
    missing = REQUIRED - manifest['files'].keys()
    if missing:
        raise BuildError('The release export is missing: ' + ', '.join(sorted(missing)))
    return manifest, sha256(raw)


def read_listed_file(runtime, name, item):
    """Read one listed file without following links, and check its size and digest."""
    current = runtime
    for part in name.split('/'):
        current = current / part
        try:
            status = os.lstat(current)
        except OSError:
            raise BuildError('A listed file is missing: ' + name) from None
        if stat.S_ISLNK(status.st_mode):
            raise BuildError('Symbolic links are not allowed in the runtime: ' + name)
    if not stat.S_ISREG(status.st_mode):
        raise BuildError('A listed file is not an ordinary file: ' + name)
    content = current.read_bytes()
    if status.st_size != item['bytes'] or len(content) != item['bytes'] or sha256(content) != item['sha256']:
        raise BuildError('A listed file does not match the release manifest: ' + name)
    return content


def verify_runtime(runtime, tool_root=None):
    """Return (manifest, manifest digest, {name: verified bytes}) or raise BuildError."""
    tool_root = Path(tool_root or Path(__file__).resolve().parents[1]).resolve()
    supplied = Path(runtime)
    if supplied.is_symlink():
        raise BuildError('The runtime folder must not be a symbolic link.')
    if not supplied.is_dir():
        raise BuildError('The runtime folder does not exist.')
    runtime = supplied.resolve()
    inside_tool = runtime == tool_root or tool_root in runtime.parents
    if inside_tool and not (tool_root / 'RELEASE-MANIFEST.json').is_file():
        raise BuildError('This is the source working checkout. Build from an audited export made by '
                         'tools/make_release.py, outside the checkout.')
    manifest, manifest_digest = read_manifest(runtime)
    contents = {name: read_listed_file(runtime, name, item) for name, item in sorted(manifest['files'].items())}
    if contents['VERSION'].decode('utf-8', 'replace').strip() != manifest['version']:
        raise BuildError('VERSION does not match the release manifest.')
    for directory, names, files in os.walk(runtime, followlinks=False):
        here = Path(directory)
        kept = []
        for name in names:
            child = here / name
            if child.is_symlink():
                raise BuildError('Symbolic links are not allowed in the runtime: ' + str(child.relative_to(runtime)))
            if name not in SKIPPED_DIRECTORIES:
                kept.append(name)
        names[:] = kept  # .git and caches are neither read nor copied.
        for name in files:
            relative = (here / name).relative_to(runtime).as_posix()
            if name in IGNORED_FILES or relative == 'RELEASE-MANIFEST.json':
                continue
            if relative not in manifest['files']:
                raise BuildError('The runtime holds a file the manifest does not list: ' + relative)
    return manifest, manifest_digest, contents


def bundle_identifier(release_json):
    def read_object(pairs):
        if len(dict(pairs)) != len(pairs):
            raise ValueError('Duplicate release metadata key.')
        return dict(pairs)

    try:
        metadata = json.loads(release_json.decode('utf-8'), object_pairs_hook=read_object)
        owner = metadata['github_owner']
    except (ValueError, TypeError, KeyError, RecursionError):
        owner = None
    if (not isinstance(owner, str) or '--' in owner or
            not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?', owner)):
        raise BuildError('release.json needs a valid github_owner for the app bundle identifier.')
    return f'io.github.{owner.lower()}.resonance'


def check_runtime_tzdata(requirements):
    try:
        lines = [line.split('#', 1)[0].strip() for line in requirements.decode('utf-8').splitlines()]
    except UnicodeError:
        raise BuildError('Runtime requirements.txt must be valid UTF-8.') from None
    pins = [line for line in lines if re.match(r'(?i)^tzdata\b', line)]
    marker = r'\s*;\s*sys_platform\s*==\s*([\'"])win32\1'
    if len(pins) != 1 or not re.fullmatch(r'tzdata==' + re.escape(TZDATA_VERSION) + marker, pins[0]):
        raise BuildError(f'Runtime requirements.txt must pin tzdata=={TZDATA_VERSION} for Windows.')


def packaged_data(contents):
    """Select the public kit and notices from the verified export snapshot."""
    import assistant_kit

    selected = {name: contents[name] for name in assistant_kit.PUBLIC & contents.keys()}
    try:
        selected[assistant_kit.MANIFEST_NAME] = assistant_kit.make_manifest(selected)
    except assistant_kit.KitError as error:
        raise BuildError(str(error)) from error
    selected[NOTICES] = contents[NOTICES]
    return selected


def data_selection(data):
    """Root files and top-level directories for PyInstaller's --add-data."""
    return (sorted(name for name in data if '/' not in name),
            sorted({name.split('/')[0] for name in data if '/' in name}))


def stage_sources(contents, stage, *, data=None):
    """Write only the audited data and the build inputs that need real files."""
    data = packaged_data(contents) if data is None else data
    build_inputs = {'desktop/launch.py', 'desktop/hooks/hook-workflow.py', 'release.json', ICON_RENDERER}
    staged = {**{name: contents[name] for name in build_inputs & contents.keys()}, **data}
    for name, raw in sorted(staged.items()):
        destination = stage / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
    return data_selection(data)


def target_label(platform_name=None):
    return {'darwin': 'mac', 'win32': 'windows'}.get(platform_name or sys.platform)


def architecture():
    return prepare_build.architecture()


def archive_name(version, platform_name=None):
    label = target_label(platform_name) or 'unsupported'
    return f'{APP_NAME}-{version}-{label}-{architecture()}-candidate.zip'


def pyinstaller_command(stage, work, dist, root_files, directories, platform_name=None, python=None,
                        identifier=None, icon=None, target_arch=None, version_file=None):
    """The native build command. Paths are absolute; the environment is not consulted."""
    platform_name = platform_name or sys.platform
    separator = ';' if platform_name == 'win32' else ':'
    command = [python or sys.executable, '-E', '-s', '-P', '-B', '-m', 'PyInstaller',
               '--noconfirm', '--clean', '--onedir', '--windowed',
               '--noupx', '--log-level', 'WARN', '--name', APP_NAME, '--distpath', str(dist),
               '--workpath', str(work / 'build'), '--specpath', str(work / 'spec'), '--paths', str(stage),
               '--additional-hooks-dir', str(stage / 'desktop' / 'hooks')]
    for module in HIDDEN_IMPORTS:
        command += ['--hidden-import', module]
    command += ['--collect-data', 'tzdata']
    for name in root_files:
        command += ['--add-data', f'{stage / name}{separator}.']
    for directory in directories:
        command += ['--add-data', f'{stage / directory}{separator}{directory}']
    if platform_name == 'darwin' and identifier:
        command += ['--osx-bundle-identifier', identifier]
    if platform_name == 'darwin' and target_arch:
        command += ['--target-arch', target_arch]
    if platform_name == 'win32' and version_file:
        command += ['--version-file', str(version_file)]
    if icon:
        command += ['--icon', str(icon)]
    return command + [str(stage / 'desktop' / 'launch.py')]


def numeric_version(version):
    if not VERSION_PATTERN.fullmatch(version):
        raise BuildError('VERSION is invalid for native metadata.')
    return '.'.join(part.lstrip('0') or '0' for part in version.partition('-')[0].split('.'))


def windows_version_resource(version):
    numeric = numeric_version(version)
    parts = numeric.split('.')
    if any(len(part) > 5 or int(part) > 65535 for part in parts):
        raise BuildError('Windows VERSION components must fit in 16 bits.')
    quad = tuple(int(part) for part in parts) + (0,)
    strings = {'FileDescription': APP_NAME, 'FileVersion': '.'.join(map(str, quad)),
               'InternalName': APP_NAME, 'OriginalFilename': APP_NAME + '.exe',
               'ProductName': APP_NAME, 'ProductVersion': version}
    string_table = ',\n'.join(f'      StringStruct({key!r}, {value!r})' for key, value in strings.items())
    prerelease = 0x02 if '-' in version else 0
    resource = (
        'VSVersionInfo(\n'
        f'  ffi=FixedFileInfo(filevers={quad!r}, prodvers={quad!r}, mask=0x3f, flags={prerelease},\n'
        '                    OS=0x40004, fileType=0x1, subtype=0, date=(0, 0)),\n'
        "  kids=[StringFileInfo([StringTable('040904b0', [\n"
        f'{string_table}\n'
        "  ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])]\n"
        ')\n'
    )
    return quad, resource.encode('ascii')


def windows_icon(png):
    # One 128px, 32-bit ICO entry. The PNG itself is copied without conversion.
    png_header = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'
    if (not png or len(png) < 45 or not png.startswith(png_header) or
            png[16:26] != struct.pack('>IIBB', 128, 128, 8, 6)):
        raise BuildError(f'The default Windows icon requires a verified 128px RGBA PNG at {WINDOWS_ICON_SOURCE}.')
    return (struct.pack('<HHH', 0, 1, 1) +
            struct.pack('<BBBBHHII', 128, 128, 0, 0, 1, 32, len(png), 22) + png)


def finish_mac_metadata(dist, version, minimum_os, environment):
    """Update the fresh candidate plist and renew the outer ad-hoc signature."""
    bundle = dist / f'{APP_NAME}.app'
    path = bundle / 'Contents' / 'Info.plist'
    if not stat.S_ISREG(path.lstat().st_mode):
        raise BuildError('The macOS bundle Info.plist must be a regular file.')
    try:
        plist = plistlib.loads(path.read_bytes())
    except plistlib.InvalidFileException:
        raise BuildError('The macOS bundle Info.plist is invalid.') from None
    if not isinstance(plist, dict):
        raise BuildError('The macOS bundle Info.plist must be a dictionary.')
    numeric = numeric_version(version)
    fields = {'CFBundleShortVersionString': numeric, 'CFBundleVersion': numeric,
              'ResonanceVersion': version, 'LSMinimumSystemVersion': minimum_os}
    plist.update(fields)
    raw = plistlib.dumps(plist)
    path.write_bytes(raw)
    for args in (['--force', '--sign', '-'], ['--verify', '--all-architectures', '--deep', '--strict']):
        subprocess.run(['/usr/bin/codesign', *args, str(bundle)], check=True, timeout=120,
                       env=environment, stdin=subprocess.DEVNULL)
    actual = path.read_bytes()
    if actual != raw:
        raise BuildError('The signed macOS candidate Info.plist differs from the intended metadata.')
    return {'info_plist_path': path.relative_to(dist).as_posix(),
            'info_plist': {**fields, 'CFBundleIconFile': plist.get('CFBundleIconFile')},
            'info_plist_sha256': sha256(actual), 'signature': 'ad-hoc verified'}


def locked_toolchain(requested):
    details = prepare_build.probe_interpreter(sys.executable, Path(__file__).resolve().parent)
    target = prepare_build.select_target(details, requested)
    for package, version in (('pyinstaller', PYINSTALLER_VERSION), ('tzdata', TZDATA_VERSION)):
        if target.platform_lock.packages.get(package, (None,))[0] != version:
            raise BuildError(f'The desktop wheel lock must pin {package}=={version}.')
    return details, target, prepare_build.check_environment(target, details)


def tk_script_patch(directory):
    if not directory or directory.startswith('//zipfs:/'):
        return None
    try:
        script = (Path(directory) / 'tk.tcl').read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        return None
    match = re.search(r'(?mi)^package require -exact tk[ \t]+([0-9]+\.[0-9]+\.[0-9]+)[ \t]*$', script)
    return match[1] if match else None


def toolkit_versions(supplied_tk=None):
    """Inspect Tcl and the Tk scripts without initializing Tk."""
    try:
        import tkinter
    except ImportError:
        raise BuildError('This Python has no Tk. Use a CPython build that includes Tcl/Tk.') from None
    try:
        interpreter = tkinter.Tcl()
        tcl = str(interpreter.call('info', 'patchlevel'))
    except tkinter.TclError:
        raise BuildError('This Python could not start its headless Tcl interpreter.') from None
    tk_major_minor = str(tkinter.TkVersion)
    if not PATCH_LEVEL.fullmatch(tcl):
        raise BuildError('Could not determine the exact Tcl patch level.')
    if any(prepare_build.version_tuple(version) < MINIMUM_TK for version in (tcl, tk_major_minor)):
        raise BuildError(f'Use a Python build with its own Tcl/Tk 8.6 or newer (found Tcl {tcl}, '
                         f'Tk {tk_major_minor}), not Apple\'s system Tcl/Tk.')

    # Use the same data-directory selection as the locked PyInstaller Tk hook.
    from PyInstaller.utils.hooks.tcl_tk import tcltk_info
    tk = tk_script_patch(tcltk_info.tk_data_dir)
    if supplied_tk is not None and (not PATCH_LEVEL.fullmatch(supplied_tk) or
                                    not supplied_tk.startswith(tk_major_minor + '.') or
                                    (tk is not None and supplied_tk != tk)):
        raise BuildError('The supplied Tk patch level must match this interpreter and any readable Tk scripts.')
    if tk is not None and not tk.startswith(tk_major_minor + '.'):
        raise BuildError('The Tk scripts do not match the Tk library version this Python was built against.')
    source = 'tk.tcl exact package requirement' if tk else 'caller supplied' if supplied_tk else 'unavailable headlessly'
    return {'tcl': tcl, 'tk': tk or supplied_tk, 'tk_major_minor': tk_major_minor, 'tk_patch_source': source}


def probe_toolkit(workdir, supplied_tk):
    code = ('import json, runpy, sys; probe = runpy.run_path(sys.argv[1])["toolkit_versions"]; '
            'print(json.dumps(probe(sys.argv[2] or None)))')
    try:
        result = prepare_build.run_python(sys.executable, ['-c', code, str(Path(__file__).resolve()), supplied_tk or ''],
                                          workdir, capture=True)
        return json.loads(result.stdout)
    except subprocess.CalledProcessError as error:
        message = error.stderr.strip().splitlines()[-1] if error.stderr.strip() else 'Tcl/Tk inspection failed.'
        raise BuildError(message.removeprefix('prepare_build.BuildError: ')) from None
    except ValueError:
        raise BuildError('The headless Tcl/Tk inspection returned an invalid result.') from None


def render_mac_icon(stage, work, environment):
    """Turn the existing static/icon.svg into an .icns with the repository's own renderer."""
    iconset = work / 'Resonance.iconset'
    iconset.mkdir(parents=True)
    icns = work / 'Resonance.icns'
    subprocess.run(['/usr/bin/swift', str(stage / ICON_RENDERER), str(stage / 'static' / 'icon.svg'), str(iconset)],
                   check=True, timeout=300, env=environment, stdin=subprocess.DEVNULL)
    subprocess.run(['/usr/bin/iconutil', '-c', 'icns', str(iconset), '-o', str(icns)],
                   check=True, timeout=60, env=environment, stdin=subprocess.DEVNULL)
    return icns


def digest_tree(folder):
    files, links = {}, {}
    for directory, names, filenames in os.walk(folder, followlinks=False):
        for name in sorted(names + filenames):
            path = Path(directory) / name
            relative = path.relative_to(folder).as_posix()
            if path.is_symlink():
                links[relative] = os.readlink(path)
            elif path.is_file():
                content = path.read_bytes()
                files[relative] = {'sha256': sha256(content), 'bytes': len(content)}
    return dict(sorted(files.items())), dict(sorted(links.items()))


def check_packaged_notices(files, verified_notice, platform_name):
    path = NOTICE_PATHS[platform_name]
    expected = {'sha256': sha256(verified_notice), 'bytes': len(verified_notice)}
    if files.get(path) != expected:
        raise BuildError('The built tree needs a regular notice file matching the verified export at ' + path)
    return {'path': path, **expected}


def zip_distribution(dist, archive, platform_name=None):
    """Zip the app with paths preserved. A Mac app uses ditto, which keeps links and attributes."""
    platform_name = platform_name or sys.platform
    if platform_name == 'darwin':
        subprocess.run(['/usr/bin/ditto', '-c', '-k', '--keepParent', str(dist / f'{APP_NAME}.app'), str(archive)],
                       check=True, timeout=600, env=prepare_build.clean_environment(), stdin=subprocess.DEVNULL)
        return
    folder = dist / APP_NAME
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for directory, names, filenames in os.walk(folder, followlinks=False):
            names.sort()
            for name in sorted(filenames) + names:
                path = Path(directory) / name
                if path.is_symlink():
                    raise BuildError('A symbolic link cannot go into the Windows archive: ' + name)
                if path.is_file():
                    bundle.write(path, path.relative_to(dist).as_posix())


def check_archive(archive, platform_name=None):
    mac = (platform_name or sys.platform) == 'darwin'
    expected = f'{APP_NAME}.app/' if mac else f'{APP_NAME}/'
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        if not names or any(not name.startswith(expected) or '..' in name.split('/') for name in names):
            raise BuildError('The archive has unexpected paths.')
        if mac:
            check_mac_zip_metadata(bundle)
        if bundle.testzip() is not None:
            raise BuildError('The archive failed its integrity check.')
    return len(names)


def check_mac_zip_metadata(bundle):
    # ditto writes AppleDouble sidecars beside their targets without --sequesterRsrc.
    paths = {info.filename.rstrip('/') for info in bundle.infolist()}
    sidecars = set()
    for info in bundle.infolist():
        name = info.filename.rstrip('/')
        parent, _, leaf = name.rpartition('/')
        if not leaf.startswith('._'):
            continue
        mode = info.external_attr >> 16
        if (leaf == '._' or name in sidecars or parent + '/' + leaf[2:] not in paths
                or info.orig_filename != info.filename or info.flag_bits & 1
                or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                or info.is_dir() or info.create_system != 3 or mode & 0o7000
                or not stat.S_ISREG(mode) or not 0 < info.file_size <= 4096):
            raise BuildError('Invalid Mac ZIP metadata member.')
        try:
            sign_release.apple_double(bundle.read(info))
        except sign_release.SigningError as error:
            raise BuildError(f'{error} ZIP member: {name}') from None
        sidecars.add(name)


def executable_path(dist, platform_name=None):
    if (platform_name or sys.platform) == 'darwin':
        return dist / f'{APP_NAME}.app' / 'Contents' / 'MacOS' / APP_NAME
    return dist / APP_NAME / f'{APP_NAME}.exe'


def tidy_dist(dist, platform_name=None):
    """Keep only the deliverable; a Mac windowed build also leaves a plain command-line folder."""
    executable = executable_path(dist, platform_name)
    if not executable.is_file():
        raise BuildError('The build finished without producing ' + str(executable.relative_to(dist)))
    if (platform_name or sys.platform) == 'darwin':
        extra = dist / APP_NAME
        if extra.is_dir() and not extra.is_symlink():
            shutil.rmtree(extra)
    return executable


def smoke_test(executable, version):
    """Run the built program once without a window, using a throwaway synthetic data folder."""
    with tempfile.TemporaryDirectory(prefix='resonance-smoke-') as folder:
        temporary = Path(folder)
        report_file = temporary / 'result.json'
        result = subprocess.run([str(executable), '--self-test', '--data-dir', str(temporary / 'data'), '--port', '0',
                                 '--log-dir', str(temporary / 'logs'), '--self-test-report', str(report_file)],
                                capture_output=True, timeout=120, env=prepare_build.clean_environment(),
                                stdin=subprocess.DEVNULL, cwd=temporary)
        try:
            report_stat = report_file.lstat()
            if not stat.S_ISREG(report_stat.st_mode) or report_stat.st_size > 65536:
                raise ValueError
            report = json.loads(report_file.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise BuildError('The built program did not write a valid no-window self-test report.') from None
    patch = report.get('tcl_patch') if isinstance(report, dict) else None
    if (result.returncode != 0 or not isinstance(report, dict) or report.get('version') != version or
            not all(report.get(key) is True for key in ('ok', 'frozen', 'seed', 'assistant_kit', 'tcl')) or
            not isinstance(patch, str) or not PATCH_LEVEL.fullmatch(patch)):
        raise BuildError('The built program failed its no-window self-test.')
    return report


def plan_lines(runtime, out, manifest, command, icon_step, platform_name=None):
    label = target_label(platform_name)
    lines = [f'Resonance {manifest["version"]} desktop candidate plan (nothing is built or written)',
             f'  runtime: {runtime} ({len(manifest["files"])} verified files)',
             f'  output : {out}',
             f'  target : {label or "unsupported on this computer"} / {architecture()} (native build only)']
    if label is None:
        lines.append('  note   : build on macOS or Windows; PyInstaller cannot cross-compile.')
    if icon_step:
        lines.append('  icon   : ' + icon_step)
    if label == 'mac':
        lines.append('  metadata: set the numeric and full versions and minimum OS in Info.plist, then renew ad-hoc signing')
    elif label == 'windows':
        lines.append('  metadata: generate a Windows version resource from VERSION')
    lines += ['  command: ' + (subprocess.list2cmdline(command) if (platform_name or sys.platform) == 'win32'
                              else shlex.join(command)),
              '  then   : tidy dist, run a no-window self-test, verify the archive and packaged notices',
              f'  output : {archive_name(manifest["version"], platform_name)} and BUILD-MANIFEST.json']
    return lines


def build(runtime, out, plan=False, icon=None, mac_icon=True, target=None, tk_patch_level=None):
    out = Path(out).absolute()
    tool_root = Path(__file__).resolve().parents[1]
    manifest, manifest_digest, contents = verify_runtime(runtime, tool_root)
    runtime = Path(runtime).resolve()
    if out.exists() or out.is_symlink():
        raise BuildError('The output must be a new folder; nothing was overwritten.')
    out = out.resolve()
    for protected in (runtime, tool_root):
        if out.is_relative_to(protected):
            raise BuildError('Put the output outside the runtime and the source checkout.')
    version = manifest['version']
    mac, windows = sys.platform == 'darwin', sys.platform == 'win32'
    valid_icons = ('.icns',) if mac else ('.ico',) if windows else ('.icns', '.ico')
    if icon is not None and (not Path(icon).is_file() or Path(icon).suffix.lower() not in valid_icons):
        raise BuildError('--icon must be an existing .icns (Mac) or .ico (Windows) file.')
    use_mac_icon = icon is None and mac_icon and mac and ICON_RENDERER in contents
    use_windows_icon = icon is None and windows
    identifier = bundle_identifier(contents['release.json'])
    check_runtime_tzdata(contents['requirements.txt'])
    work, stage, dist = out / 'work', out / 'work' / 'stage', out / 'dist'
    build_cwd = work / 'cwd'
    data = packaged_data(contents)
    root_files, directories = data_selection(data)
    planned_icon, icon_source, icon_raw, icon_step = None, 'PyInstaller default', None, None
    if icon is not None:
        supplied = Path(icon).resolve()
        planned_icon = work / ('Resonance' + Path(icon).suffix.lower())
        icon_source = 'explicit override'
        icon_step = f'copy the explicit override to {planned_icon.name}'
        if supplied.is_relative_to(runtime):
            icon_raw = contents.get(supplied.relative_to(runtime).as_posix())
            if icon_raw is None:
                raise BuildError('An icon inside the runtime must be listed in its release manifest.')
        elif not plan:
            icon_raw = supplied.read_bytes()
    elif use_mac_icon:
        planned_icon, icon_source = work / 'Resonance.icns', 'static/icon.svg'
        icon_step = 'render static/icon.svg with mac-launcher/render-icon.swift'
    elif use_windows_icon:
        planned_icon, icon_source = work / 'Resonance.ico', WINDOWS_ICON_SOURCE
        icon_raw = windows_icon(contents.get(WINDOWS_ICON_SOURCE))
        icon_step = f'wrap {WINDOWS_ICON_SOURCE} in ICO'
    version_file = work / 'version-resource.txt' if windows else None
    metadata = {'product_name': APP_NAME, 'numeric_version': numeric_version(version),
                'release_version': version, 'icon': {'source': icon_source}}
    if windows:
        quad, resource = windows_version_resource(version)
        metadata['windows_version_resource'] = {'file_version': list(quad), 'product_version': list(quad),
                                                'input_sha256': sha256(resource)}
    command = pyinstaller_command(stage, work, dist, root_files, directories, identifier=identifier,
                                  icon=planned_icon, target_arch=architecture(), version_file=version_file)
    if plan:
        return plan_lines(runtime, out, manifest, command, icon_step)
    details, native, lock_evidence = locked_toolchain(target)
    toolkit = probe_toolkit(Path(sys.prefix) / prepare_build.EVIDENCE, tk_patch_level)
    environment = prepare_build.clean_environment()
    environment['PYINSTALLER_CONFIG_DIR'] = str(work / 'pyinstaller-cache')
    if mac:
        environment['MACOSX_DEPLOYMENT_TARGET'] = native.minimum_os
    out.mkdir(parents=True)  # Fresh by the check above; mkdir fails if it appeared since.
    stage_sources(contents, stage, data=data)
    if use_mac_icon:
        icon_raw = render_mac_icon(stage, work, environment).read_bytes()
    elif planned_icon is not None:
        planned_icon.write_bytes(icon_raw)
    if planned_icon is not None:
        metadata['icon'].update(input=planned_icon.name, input_sha256=sha256(icon_raw))
    if windows:
        version_file.write_bytes(resource)
    # An empty working folder keeps the current directory from supplying modules to the build.
    build_cwd.mkdir()
    subprocess.run(command, cwd=build_cwd, env=environment, stdin=subprocess.DEVNULL, check=True, timeout=1200)
    executable = tidy_dist(dist)
    if mac:
        metadata.update(finish_mac_metadata(dist, version, native.minimum_os, environment))
    smoke = smoke_test(executable, version)
    archive = out / archive_name(version)
    zip_distribution(dist, archive)
    entries = check_archive(archive)
    files, links = digest_tree(dist)
    notices = check_packaged_notices(files, data[NOTICES], sys.platform)
    if windows:
        output_executable = executable.relative_to(dist).as_posix()
        metadata['windows_executable'] = {'path': output_executable, 'sha256': files[output_executable]['sha256']}
    record = {
        'product': APP_NAME, 'source_version': version,
        'runtime_manifest_sha256': manifest_digest, 'runtime_files': len(manifest['files']),
        'os': platform.system(), 'os_release': platform.release(), 'platform': sys.platform,
        'host_os_version': details['host_version'],
        'architecture': native.architecture, 'python': details['version'],
        'python_implementation': details['implementation'],
        'python_macos_deployment_target': details['deployment_target'],
        'target_os': native.os, 'minimum_os': native.minimum_os,
        'minimum_os_status': 'build target; native compatibility unverified',
        'native_metadata': metadata,
        **lock_evidence,
        'pyinstaller': PYINSTALLER_VERSION, 'tzdata': TZDATA_VERSION,
        **toolkit,
        'smoke_test': smoke,
        'third_party_notices': notices,
        'status': 'candidate: not Developer ID signed or notarized (Mac) and not Authenticode signed (Windows); '
                  'no physical first-user proof',
        'archive': {'name': archive.name, 'sha256': sha256(archive.read_bytes()),
                    'bytes': archive.stat().st_size, 'entries': entries},
        'files': files, 'symlinks': links,
    }
    (out / 'BUILD-MANIFEST.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    shutil.rmtree(work)
    return out, archive


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', required=True, type=Path, help='Audited export made by tools/make_release.py')
    parser.add_argument('--out', required=True, type=Path, help='A new folder for the candidate')
    parser.add_argument('--plan', action='store_true', help='Verify the runtime and show the native command only')
    parser.add_argument('--icon', type=Path, help='An existing .icns (Mac) or .ico (Windows) icon')
    parser.add_argument('--no-mac-icon', action='store_true', help='Skip rendering static/icon.svg on a Mac')
    parser.add_argument('--target', help='Require the named native build target')
    parser.add_argument('--tk-patch-level', help='Exact Tk patch level verified independently for this Python')
    args = parser.parse_args(argv)
    try:
        result = build(args.runtime, args.out, plan=args.plan, icon=args.icon, mac_icon=not args.no_mac_icon,
                       target=args.target, tk_patch_level=args.tk_patch_level)
    except (OSError, BuildError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        parser.exit(1, str(error) + '\nNothing was published. Any partial output is kept for inspection.\n')
    if args.plan:
        print('\n'.join(result))
    else:
        print(f'Candidate built: {result[1]} (not signed, not published; see docs/DESKTOP.md).')


if __name__ == '__main__':
    main()
