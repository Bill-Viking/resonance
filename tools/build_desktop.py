#!/usr/bin/env python3
"""Build a self-contained desktop candidate from an audited release export. Never publishes.

Run it on the operating system you are building for: PyInstaller cannot cross-compile,
so a Mac builds the Mac app and a Windows computer builds the Windows folder.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_release

PYINSTALLER_VERSION, TZDATA_VERSION = '6.22.3', '2026.2'
MINIMUM_TK = 8.6  # Apple's system Tcl/Tk 8.5 is deprecated and can draw blank labels.
APP_NAME = 'Resonance'
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
VERSION_PATTERN = re.compile(r'\d+\.\d+\.\d+(?:-[a-z0-9.-]+)?')
SKIPPED_DIRECTORIES = frozenset(('.git', '__pycache__'))
IGNORED_FILES = frozenset(('.DS_Store',))
# Names never allowed in a runtime, whatever the allowlist says.
PRIVATE_NAME = re.compile(r'(?:^|/)(?:docs/internal|experiments|private|data|backups|reports|scans)(?:/|$)|'
                          r'(?:^|/)(?:STATUS\.md|OPENER[^/]*\.md|\.env[^/]*|[^/]*\.sqlite[^/]*|[^/]*\.db[^/]*|[^/]*\.log)$')
# The packaged files. Python modules are frozen into the app; the same source ships as data
# only because the assistant commands (python3 monitor.py ...) run from the app's data root.
SOURCE_MODULES = ('monitor.py', 'policy.py', 'reader_checks.py', 'samples.py', 'server.py', 'workflow.py')
DATA_ROOT_FILES = ('AGENTS.md', 'ASSISTANT.md', 'AUTHORING.md', 'CHANGELOG.md', 'INTEGRATIONS.md', 'LICENSE',
                   'PRODUCT.md', 'README.md', 'SECURITY.md', 'VERSION') + SOURCE_MODULES
DATA_DIRECTORIES = ('static/', 'examples/')
DATA_DOCS = ('docs/ACTIONS.md', 'docs/MONITORING.md')
REQUIRED = frozenset(('desktop/launch.py', 'desktop/hooks/hook-workflow.py', 'release.json', 'static/index.html', 'static/icon.svg',
                      'examples/practice.seed.json') + DATA_ROOT_FILES + DATA_DOCS)
HIDDEN_IMPORTS = ('server', 'policy', 'workflow', 'samples', 'reader_checks', 'tkinter', 'tzdata')
ICON_RENDERER = 'mac-launcher/render-icon.swift'


class BuildError(ValueError):
    """A refusal with a plain sentence. Nothing is published or overwritten."""


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
    owner = json.loads(release_json.decode('utf-8')).get('github_owner', 'resonance-project')
    if not isinstance(owner, str) or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?', owner):
        raise BuildError('release.json needs a valid github_owner for the app bundle identifier.')
    return f'io.github.{owner.lower()}.resonance'


def data_selection(names):
    """(root files, top-level directories) that ship as data files."""
    root = [name for name in DATA_ROOT_FILES if name in names]
    directories = sorted({name.split('/')[0] for name in names
                          if name.startswith(DATA_DIRECTORIES) or name in DATA_DOCS})
    return root, directories


def stage_sources(contents, stage):
    """Write only the verified files the app needs, so no build junk can be packaged."""
    root, directories = data_selection(contents)
    wanted = set(root) | {'desktop/launch.py', 'desktop/hooks/hook-workflow.py', 'release.json', ICON_RENDERER} | {
        name for name in contents if name.startswith(DATA_DIRECTORIES) or name in DATA_DOCS}
    for name in sorted(wanted & contents.keys()):
        destination = stage / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(contents[name])
    return root, directories


def target_label(platform_name=None):
    return {'darwin': 'mac', 'win32': 'windows'}.get(platform_name or sys.platform)


def architecture():
    return platform.machine().lower() or 'unknown'


def archive_name(version, platform_name=None):
    label = target_label(platform_name) or 'unsupported'
    return f'{APP_NAME}-{version}-{label}-{architecture()}-candidate.zip'


def pyinstaller_command(stage, work, dist, root_files, directories, platform_name=None, python=None,
                        identifier=None, icon=None):
    """The native build command. Paths are absolute; the environment is not consulted."""
    platform_name = platform_name or sys.platform
    separator = ';' if platform_name == 'win32' else ':'
    command = [python or sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--windowed',
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
    if icon:
        command += ['--icon', str(icon)]
    return command + [str(stage / 'desktop' / 'launch.py')]


def toolchain_problems():
    """Why this Python cannot build the candidate; empty when it can."""
    problems = []
    if target_label() is None:
        problems.append('This computer is not a build target. Build on macOS or Windows.')
    if sys.version_info < (3, 9):
        problems.append('Python 3.9 or newer is required; 3.11 is what the candidate workflow uses.')
    for package, wanted in (('pyinstaller', PYINSTALLER_VERSION), ('tzdata', TZDATA_VERSION)):
        try:
            found = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            found = None
        if found != wanted:
            problems.append(f'{package}=={wanted} is required (found {found or "nothing"}). '
                            'Install desktop/requirements-build.txt.')
    try:
        toolkit = toolkit_versions()
    except ImportError:
        problems.append('This Python has no Tk. Use a Python that includes Tk, such as the python.org installer.')
    else:
        old = [f'{name.capitalize()} {version}' for name, version in toolkit.items() if float(version) < MINIMUM_TK]
        if old:
            problems.append(f'This Python is linked to Tcl/Tk older than {MINIMUM_TK} ({", ".join(old)}); '
                            f'the window can come up blank. Use a Python build that brings its own Tcl/Tk '
                            f'{MINIMUM_TK} or newer, such as the python.org installer, not Apple\'s system Tcl/Tk.')
    return problems


def toolkit_versions():
    """The Tcl and Tk versions this Python is linked to, read from tkinter's constants (no window is made)."""
    import tkinter
    return {'tcl': str(tkinter.TclVersion), 'tk': str(tkinter.TkVersion)}


def render_mac_icon(stage, work):
    """Turn the existing static/icon.svg into an .icns with the repository's own renderer."""
    iconset = work / 'Resonance.iconset'
    iconset.mkdir(parents=True)
    icns = work / 'Resonance.icns'
    subprocess.run(['/usr/bin/swift', str(stage / ICON_RENDERER), str(stage / 'static' / 'icon.svg'), str(iconset)],
                   check=True, timeout=300)
    subprocess.run(['/usr/bin/iconutil', '-c', 'icns', str(iconset), '-o', str(icns)], check=True, timeout=60)
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


def zip_distribution(dist, archive, platform_name=None):
    """Zip the app with paths preserved. A Mac app uses ditto, which keeps links and attributes."""
    platform_name = platform_name or sys.platform
    if platform_name == 'darwin':
        subprocess.run(['/usr/bin/ditto', '-c', '-k', '--keepParent', str(dist / f'{APP_NAME}.app'), str(archive)],
                       check=True, timeout=600)
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
    expected = f'{APP_NAME}.app/' if (platform_name or sys.platform) == 'darwin' else f'{APP_NAME}/'
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip() is not None:
            raise BuildError('The archive failed its integrity check.')
        names = bundle.namelist()
    if not names or any(not name.startswith(expected) or '..' in name.split('/') for name in names):
        raise BuildError('The archive has unexpected paths.')
    return len(names)


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
    environment = {key: value for key, value in os.environ.items()
                   if key not in ('RESONANCE_DATA_DIR', 'SOCIAL_BRIEF_DATA_DIR')}
    with tempfile.TemporaryDirectory(prefix='resonance-smoke-') as data:
        result = subprocess.run([str(executable), '--self-test', '--data-dir', data, '--port', '0'],
                                capture_output=True, text=True, timeout=120, env=environment,
                                stdin=subprocess.DEVNULL)
    try:
        report = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        report = {}
    if result.returncode != 0 or not (report.get('ok') and report.get('frozen') and report.get('seed') and
                                      report.get('version') == version):
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
    lines += ['  command: ' + (subprocess.list2cmdline(command) if (platform_name or sys.platform) == 'win32'
                              else shlex.join(command)),
              f'  then   : tidy dist, run a no-window self-test, write {archive_name(manifest["version"], platform_name)}'
              ' and BUILD-MANIFEST.json']
    return lines


def build(runtime, out, plan=False, icon=None, mac_icon=True):
    out = Path(out).absolute()
    tool_root = Path(__file__).resolve().parents[1]
    manifest, manifest_digest, contents = verify_runtime(runtime, tool_root)
    runtime = Path(runtime).resolve()
    if out.exists() or out.is_symlink():
        raise BuildError('The output must be a new folder; nothing was overwritten.')
    resolved_out = out.parent.resolve() / out.name if out.parent.exists() else out
    for protected in (runtime, tool_root):
        if resolved_out == protected or protected in resolved_out.parents:
            raise BuildError('Put the output outside the runtime and the source checkout.')
    if icon is not None and (not Path(icon).is_file() or Path(icon).suffix.lower() not in ('.icns', '.ico')):
        raise BuildError('--icon must be an existing .icns (Mac) or .ico (Windows) file.')
    use_mac_icon = icon is None and mac_icon and sys.platform == 'darwin' and ICON_RENDERER in contents
    identifier = bundle_identifier(contents['release.json'])
    work, stage, dist = out / 'work', out / 'work' / 'stage', out / 'dist'
    root_files, directories = data_selection(contents)
    planned_icon = Path(icon) if icon else (work / 'Resonance.icns' if use_mac_icon else None)
    command = pyinstaller_command(stage, work, dist, root_files, directories, identifier=identifier, icon=planned_icon)
    if plan:
        return plan_lines(runtime, out, manifest, command,
                          'render static/icon.svg with mac-launcher/render-icon.swift' if use_mac_icon else
                          (str(icon) if icon else None))
    problems = toolchain_problems()
    if problems:
        raise BuildError('Cannot build here:\n  ' + '\n  '.join(problems))
    toolkit = toolkit_versions()
    out.mkdir(parents=True)  # Fresh by the check above; mkdir fails if it appeared since.
    stage_sources(contents, stage)
    if use_mac_icon:
        render_mac_icon(stage, work)
    # An empty working folder keeps the current directory from supplying modules to the build.
    subprocess.run(command, cwd=work, check=True, timeout=1800)
    executable = tidy_dist(dist)
    smoke_test(executable, manifest['version'])
    archive = out / archive_name(manifest['version'])
    zip_distribution(dist, archive)
    entries = check_archive(archive)
    files, links = digest_tree(dist)
    record = {
        'product': APP_NAME, 'source_version': manifest['version'],
        'runtime_manifest_sha256': manifest_digest, 'runtime_files': len(manifest['files']),
        'os': platform.system(), 'os_release': platform.release(), 'platform': sys.platform,
        'architecture': architecture(), 'python': platform.python_version(),
        'pyinstaller': PYINSTALLER_VERSION, 'tzdata': TZDATA_VERSION,
        'tcl': toolkit['tcl'], 'tk': toolkit['tk'],
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
    args = parser.parse_args(argv)
    try:
        result = build(args.runtime, args.out, plan=args.plan, icon=args.icon, mac_icon=not args.no_mac_icon)
    except (OSError, BuildError, subprocess.SubprocessError) as error:
        parser.exit(1, str(error) + '\nNothing was published. Any partial output is kept for inspection.\n')
    if args.plan:
        print('\n'.join(result))
    else:
        print(f'Candidate built: {result[1]} (not signed, not published; see docs/DESKTOP.md).')


if __name__ == '__main__':
    main()
