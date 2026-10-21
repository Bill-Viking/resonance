"""Build a local Mac app for an explicitly selected, installed Resonance release."""
import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import data_directory


def bundle_identifier(config_path=None):
    path = config_path or Path(__file__).resolve().parents[1] / 'release.json'
    config = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    owner = config.get('github_owner', 'resonance-project')
    if not isinstance(owner, str) or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?', owner):
        raise ValueError('release.json needs a valid github_owner for the app bundle identifier.')
    return f'io.github.{owner.lower()}.resonance'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path.home() / 'Applications' / 'Resonance.app')
    args = parser.parse_args()
    source = Path(__file__).resolve().parent
    runtime = args.runtime.resolve()
    output = args.output.expanduser().resolve()
    identifier = bundle_identifier()
    if output.exists():
        parser.error('Output already exists. Preserve it before rebuilding: ' + str(output))
    if not (runtime / 'server.py').is_file():
        parser.error('Runtime must contain the installed server.py')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.resonance-build-', dir=output.parent) as temporary:
        app = Path(temporary) / 'Resonance.app'
        contents = app / 'Contents'
        binary = contents / 'MacOS'
        resources = contents / 'Resources'
        binary.mkdir(parents=True)
        resources.mkdir()
        subprocess.run(['/usr/bin/clang', '-fobjc-arc', '-framework', 'AppKit',
            str(source / 'ResonanceLauncher.m'), '-o', str(binary / 'Resonance')], check=True)
        shutil.copy2(source / 'launch.py', resources)
        configuration = {
            'runtime': str(runtime), 'port': 4317,
            'index_sha256': hashlib.sha256((runtime / 'static' / 'index.html').read_bytes()).hexdigest(),
            'data_dir': str(data_directory()),
            'state_dir': str(Path.home() / 'Library' / 'Application Support' / 'Resonance'),
        }
        (resources / 'config.json').write_text(json.dumps(configuration, indent=2) + '\n')
        iconset = Path(temporary) / 'Resonance.iconset'
        iconset.mkdir()
        subprocess.run(['/usr/bin/swift', str(source / 'render-icon.swift'),
            str(runtime / 'static' / 'icon.svg'), str(iconset)], check=True)
        subprocess.run(['/usr/bin/iconutil', '-c', 'icns', str(iconset),
            '-o', str(resources / 'Resonance.icns')], check=True)
        metadata = {
            'CFBundleDisplayName': 'Resonance', 'CFBundleName': 'Resonance',
            'CFBundleExecutable': 'Resonance', 'CFBundleIconFile': 'Resonance.icns',
            'CFBundleIdentifier': identifier,
            'CFBundlePackageType': 'APPL', 'CFBundleInfoDictionaryVersion': '6.0',
            'CFBundleShortVersionString': '1.0', 'CFBundleVersion': '1',
            'LSMinimumSystemVersion': '13.0', 'LSUIElement': True,
            'NSHighResolutionCapable': True,
        }
        (contents / 'Info.plist').write_bytes(plistlib.dumps(metadata))
        subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(app)], check=True)
        subprocess.run(['/usr/bin/codesign', '--verify', '--strict', str(app)], check=True)
        app.rename(output)
    print('Built ' + str(output))


if __name__ == '__main__':
    main()
