"""Build a local Mac source launcher for an audited Resonance export."""
import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from build_desktop import verify_runtime

APP_NAME = 'Resonance Source Launcher'
MINIMUM_MACOS = '13.0'
WRAPPER_SOURCES = ('ResonanceLauncher.m', 'launch.py', 'render-icon.swift')


def bundle_identifier(release_json):
    try:
        if isinstance(release_json, Path):
            release_json = release_json.read_bytes()
        config = json.loads(release_json.decode('utf-8'))
    except FileNotFoundError:
        raise ValueError('release.json is missing.') from None
    except (UnicodeError, ValueError):
        raise ValueError('release.json is not valid JSON.') from None
    owner = config.get('github_owner') if isinstance(config, dict) else None
    if not isinstance(owner, str) or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?', owner):
        raise ValueError('release.json needs a valid github_owner for the app bundle identifier.')
    return f'io.github.{owner.lower()}.resonance.launcher'


def digest(content):
    return hashlib.sha256(content).hexdigest()


def build(runtime, output, data_dir=None):
    runtime, output = Path(runtime).expanduser(), Path(output).expanduser()
    if output.exists() or output.is_symlink():
        raise ValueError('Output already exists. Preserve it before rebuilding: ' + str(output))
    _, manifest_sha, verified = verify_runtime(runtime)
    runtime, output = runtime.resolve(), output.resolve()
    if output.is_relative_to(runtime):
        raise ValueError('Put the launcher outside the audited release export.')
    required = ['mac-launcher/' + name for name in WRAPPER_SOURCES]
    missing = sorted(set(required) - verified.keys())
    if missing:
        raise ValueError('The release export is missing launcher files: ' + ', '.join(missing))
    identifier = bundle_identifier(verified['release.json'])
    version = verified['VERSION'].decode('utf-8').strip()
    # Apple bundle version fields use numeric components; retain the full release version separately.
    numeric_version = version.split('-', 1)[0]
    configuration = {
        'runtime': str(runtime), 'version': version, 'port': 4317,
        'manifest_sha256': manifest_sha,
        'index_sha256': digest(verified['static/index.html']),
        'index_bytes': len(verified['static/index.html']),
        'server_sha256': digest(verified['server.py']),
        'state_dir': str(Path.home() / 'Library' / 'Logs' / APP_NAME),
    }
    if data_dir is not None:
        configuration['data_dir'] = str(Path(data_dir).expanduser().resolve())
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.resonance-build-', dir=output.parent) as temporary:
        work = Path(temporary)
        app = work / (APP_NAME + '.app')
        contents = app / 'Contents'
        binary = contents / 'MacOS'
        resources = contents / 'Resources'
        binary.mkdir(parents=True)
        resources.mkdir()
        for name in WRAPPER_SOURCES:
            target = resources / name if name == 'launch.py' else work / name
            target.write_bytes(verified['mac-launcher/' + name])
        (work / 'icon.svg').write_bytes(verified['static/icon.svg'])
        subprocess.run(['/usr/bin/clang', '-fobjc-arc', '-framework', 'AppKit',
            '-mmacosx-version-min=' + MINIMUM_MACOS, str(work / 'ResonanceLauncher.m'),
            '-o', str(binary / 'Resonance')], check=True, stdin=subprocess.DEVNULL, timeout=60)
        (resources / 'config.json').write_text(json.dumps(configuration, indent=2) + '\n', encoding='utf-8')
        iconset = work / 'Resonance.iconset'
        iconset.mkdir()
        subprocess.run(['/usr/bin/swift', str(work / 'render-icon.swift'),
            str(work / 'icon.svg'), str(iconset)], check=True, stdin=subprocess.DEVNULL, timeout=120)
        subprocess.run(['/usr/bin/iconutil', '-c', 'icns', str(iconset),
            '-o', str(resources / 'Resonance.icns')], check=True, stdin=subprocess.DEVNULL, timeout=30)
        metadata = {
            'CFBundleDisplayName': APP_NAME, 'CFBundleName': APP_NAME,
            'CFBundleExecutable': 'Resonance', 'CFBundleIconFile': 'Resonance.icns',
            'CFBundleIdentifier': identifier,
            'CFBundlePackageType': 'APPL', 'CFBundleInfoDictionaryVersion': '6.0',
            'CFBundleShortVersionString': numeric_version, 'CFBundleVersion': numeric_version,
            'ResonanceReleaseVersion': version, 'LSMinimumSystemVersion': MINIMUM_MACOS,
            'LSUIElement': True, 'NSHighResolutionCapable': True,
        }
        (contents / 'Info.plist').write_bytes(plistlib.dumps(metadata))
        subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(app)],
                       check=True, stdin=subprocess.DEVNULL, timeout=30)
        subprocess.run(['/usr/bin/codesign', '--verify', '--strict', str(app)],
                       check=True, stdin=subprocess.DEVNULL, timeout=30)
        if output.exists() or output.is_symlink():
            raise ValueError('Output already exists. Preserve it before rebuilding: ' + str(output))
        app.rename(output)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path.home() / 'Applications' / (APP_NAME + '.app'))
    parser.add_argument('--data-dir', type=Path, help='pin an existing or new app data location explicitly')
    args = parser.parse_args(argv)
    try:
        output = build(args.runtime, args.output, args.data_dir)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, 'Cannot build the source launcher: ' + str(error) + '\n')
    print('Built ' + str(output))


if __name__ == '__main__':
    main()
