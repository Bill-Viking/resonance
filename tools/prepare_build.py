#!/usr/bin/env python3
"""Prepare a clean, hash-locked venv for a native Resonance desktop build."""
import argparse
from dataclasses import dataclass
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import site
import subprocess
import sys
import sysconfig


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = 'build-evidence'
LOCK_LINE = re.compile(r'([a-z0-9][a-z0-9-]*)==([0-9.]+) --hash=sha256:([0-9a-f]{64})')
PYPI_INDEX = 'https://pypi.org/simple'


class BuildError(ValueError):
    """A build refusal with a plain explanation."""


@dataclass(frozen=True)
class Lock:
    file: str
    digest: str
    content: bytes
    packages: dict


@dataclass(frozen=True)
class BuildTarget:
    name: str
    os: str
    architecture: str
    minimum_os: str
    bootstrap_lock: Lock
    platform_lock: Lock

    def lock_evidence(self):
        return {
            'target': self.name,
            'bootstrap_lock_sha256': self.bootstrap_lock.digest,
            'environment_lock_sha256': self.platform_lock.digest,
        }


def clean_environment(inherited=None):
    inherited = os.environ if inherited is None else inherited
    prefixes = ('PYTHON', 'PIP', 'VIRTUAL_ENV', 'LD_', 'DYLD_', 'PYINSTALLER_', '_PYI_')
    excluded = {'__PYVENV_LAUNCHER__', 'TCL_LIBRARY', 'TK_LIBRARY', 'TCLLIBPATH', 'MACOSX_DEPLOYMENT_TARGET',
                'RESONANCE_DATA_DIR', 'SOCIAL_BRIEF_DATA_DIR', '_PYTHON_HOST_PLATFORM', '_PYTHON_SYSCONFIGDATA_NAME',
                '_MEIPASS2'}
    environment = {key: value for key, value in inherited.items()
                   if not key.upper().startswith(prefixes) and key.upper() not in excluded}
    environment['PYTHONNOUSERSITE'] = '1'
    return environment


def architecture():
    machine = platform.machine().lower()
    return {'amd64': 'x86_64', 'aarch64': 'arm64'}.get(machine, machine) or 'unknown'


def interpreter_details():
    machine = architecture()
    if sys.platform == 'win32':
        interpreter_machine = {'win-amd64': 'x86_64', 'win-arm64': 'arm64'}.get(sysconfig.get_platform())
        native = machine == interpreter_machine
    elif sys.platform == 'darwin' and machine == 'x86_64':
        translated = subprocess.run(['/usr/sbin/sysctl', '-in', 'sysctl.proc_translated'],
                                    env=clean_environment(), capture_output=True, text=True,
                                    stdin=subprocess.DEVNULL, timeout=5)
        native = translated.returncode == 0 and translated.stdout.strip() != '1'
    else:
        native = True
    host_version = platform.mac_ver()[0] if sys.platform == 'darwin' else platform.version()
    return {
        'implementation': sys.implementation.name,
        'version': platform.python_version(),
        'full_version': sys.version,
        'cache_tag': sys.implementation.cache_tag,
        'free_threaded': bool(sysconfig.get_config_var('Py_GIL_DISABLED')),
        'platform': sys.platform,
        'architecture': machine,
        'native': native,
        'host_version': host_version,
        'deployment_target': sysconfig.get_config_var('MACOSX_DEPLOYMENT_TARGET'),
        'venv': sys.prefix != sys.base_prefix,
    }


def version_tuple(version):
    if not isinstance(version, str) or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)*', version):
        return ()
    parts = tuple(map(int, version.split('.')))
    return (parts + (0, 0))[:3]


def read_lock(desktop, entry):
    try:
        filename, expected = entry['file'], entry['sha256']
        if not re.fullmatch(r'locks/[a-z0-9_-]+\.txt', filename):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise BuildError('The build target names an invalid wheel lock.') from None
    path = desktop / filename
    if path.is_symlink() or path.parent.is_symlink() or not path.is_file():
        raise BuildError(f'The build wheel lock is missing or is a link: {filename}.')
    # Git on Windows may check out text with CRLF. Hash the canonical LF bytes.
    content = path.read_bytes().replace(b'\r\n', b'\n')
    digest = hashlib.sha256(content).hexdigest()
    if digest != expected:
        raise BuildError(f'The build wheel lock digest does not match: {filename}.')
    packages = {}
    for line in content.decode('utf-8').splitlines():
        if not line or line.startswith('#'):
            continue
        match = LOCK_LINE.fullmatch(line)
        if not match or match[1] in packages:
            raise BuildError(f'The build wheel lock contains an invalid or repeated pin: {filename}.')
        name, version, wheel_hash = match.groups()
        packages[name] = (version, wheel_hash)
    if not packages:
        raise BuildError(f'The build wheel lock is empty: {filename}.')
    return Lock(filename, digest, content, packages)


def select_target(details, requested=None, root=ROOT):
    desktop = root / 'desktop'
    try:
        config = json.loads((desktop / 'build-targets.json').read_text(encoding='utf-8'))
        python_version, bootstrap, targets = config['python'], config['bootstrap'], config['targets']
        name = next((name for name, target in targets.items()
                     if (target['platform'], target['architecture']) ==
                     (details['platform'], details['architecture'])), None)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise BuildError('desktop/build-targets.json is missing or invalid.') from None
    if details['version'] != python_version or details['implementation'] != 'cpython' or details['free_threaded']:
        raise BuildError(f'Use standard CPython {python_version} with the GIL for desktop packaging '
                         f'(found {details["implementation"]} {details["version"]}).')
    if name is None:
        raise BuildError(f'There is no desktop target for {details["platform"]} / {details["architecture"]}.')
    if not details['native']:
        raise BuildError(f'Target {name} requires a native interpreter on the same host architecture.')
    if requested is not None and requested != name:
        raise BuildError(f'Target {requested} requires its own native interpreter and host (found {name}).')
    try:
        target = targets[name]
        os_name, minimum, package_entry = target['os'], target['minimum_os'], target['lock']
    except (KeyError, TypeError):
        raise BuildError(f'The build target is invalid: {name}.') from None
    if not version_tuple(minimum):
        raise BuildError(f'The build target has an invalid minimum OS: {name}.')
    if version_tuple(details['host_version']) < version_tuple(minimum):
        raise BuildError(f'Target {name} requires a host running {os_name} {minimum} or newer.')
    if details['platform'] == 'darwin':
        deployment = version_tuple(details['deployment_target'])
        if not deployment or deployment > version_tuple(minimum):
            raise BuildError(f'Use a CPython build with a macOS deployment target of {minimum} or older '
                             f'(found {details["deployment_target"] or "unknown"}).')
    bootstrap_lock, package_lock = read_lock(desktop, bootstrap), read_lock(desktop, package_entry)
    if set(bootstrap_lock.packages) != {'pip'} or 'pip' in package_lock.packages:
        raise BuildError('The bootstrap lock must pin pip separately from the desktop packages.')
    return BuildTarget(name, os_name, details['architecture'], minimum, bootstrap_lock, package_lock)


def run_python(python, arguments, cwd, *, capture=False, timeout=60, pip=False):
    environment = clean_environment()
    if pip:
        # pip --isolated ignores user config and option variables. This also disables global and site config.
        environment['PIP_CONFIG_FILE'] = os.devnull
    return subprocess.run([str(python), '-I', '-B', *arguments], cwd=cwd, env=environment,
                          stdin=subprocess.DEVNULL, capture_output=capture, text=True,
                          check=True, timeout=timeout)


def probe_interpreter(python, cwd):
    code = "import json, runpy, sys; print(json.dumps(runpy.run_path(sys.argv[1])['interpreter_details']()))"
    try:
        result = run_python(python, ['-c', code, str(Path(__file__).resolve())], cwd, capture=True, timeout=15)
        return json.loads(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        raise BuildError('Could not inspect the selected Python executable.') from None


def probe_base(python, cwd):
    details = probe_interpreter(python, cwd)
    if details['venv']:
        raise BuildError('Select a base Python executable outside an existing virtual environment.')
    return details


def install_lock(python, lock, report, evidence):
    # Install the checked bytes, even if the checkout changes while pip runs.
    copy = evidence / Path(lock.file).name
    copy.write_bytes(lock.content)
    try:
        # Replace ensurepip's bundled pip and verify the wheel even when its version already matches.
        run_python(python, ['-m', 'pip', '--isolated', 'install', '--disable-pip-version-check', '--no-input',
                            '--no-cache-dir', '--index-url', PYPI_INDEX, '--timeout', '15', '--retries', '1',
                            '--only-binary', ':all:', '--require-hashes', '--force-reinstall',
                            '--report', str(report), '-r', str(copy)], evidence, timeout=300, pip=True)
    except subprocess.SubprocessError:
        raise BuildError(f'pip could not install the hash-locked wheels in {lock.file}.') from None


def package_name(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def validated_report(path, lock):
    try:
        if path.is_symlink() or not path.is_file():
            raise ValueError
        raw = path.read_bytes()
        report = json.loads(raw)
        found = {}
        for item in report['install']:
            name = package_name(item['metadata']['name'])
            download = item['download_info']
            if name in found or not download['url'].split('?', 1)[0].endswith('.whl'):
                raise ValueError
            found[name] = (item['metadata']['version'], download['archive_info']['hashes']['sha256'])
        if report['version'] != '1' or found != lock.packages:
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise BuildError(f'The pip report is missing or does not match the wheel lock: {path.name}.') from None
    return hashlib.sha256(raw).hexdigest()


def locked_freeze(python, target, cwd):
    freeze = run_python(python, ['-m', 'pip', '--isolated', 'freeze', '--all', '--disable-pip-version-check'],
                        cwd, capture=True, pip=True).stdout.splitlines()
    expected = {name: pin[0] for name, pin in {**target.bootstrap_lock.packages, **target.platform_lock.packages}.items()}
    found = {}
    for line in freeze:
        name, separator, version = line.partition('==')
        name = package_name(name)
        if not separator or name in found:
            raise BuildError('The build venv contains an unpinned or duplicate installed package.')
        found[name] = version
    changes = [f'{name}=={expected[name]} (found {found.get(name, "nothing")})'
               for name in sorted(expected) if found.get(name) != expected[name]]
    extras = sorted(found.keys() - expected.keys())
    if extras:
        changes.append('unexpected packages: ' + ', '.join(extras))
    if changes:
        raise BuildError('The installed packages do not match the build locks: ' + ', '.join(changes) + '.')
    return freeze


def prepare(out, python=sys.executable, target=None, plan=False, root=ROOT):
    root = root.resolve()
    out = Path(out).absolute()
    if out.exists() or out.is_symlink():
        raise BuildError('The build venv must be a new folder.')
    out = out.resolve()
    if out.is_relative_to(root):
        raise BuildError('Put the build venv outside the source checkout.')
    if not out.parent.is_dir():
        raise BuildError('The parent folder for the build venv must already exist.')

    python = Path(python).absolute()
    details = probe_base(python, out.parent)
    native = select_target(details, target, root)
    venv_python = out / ('Scripts/python.exe' if details['platform'] == 'win32' else 'bin/python')
    if plan:
        return '\n'.join((f'Build environment plan for {native.name}',
                          f'  base Python: {python} ({details["version"]})',
                          f'  new venv: {out}',
                          f'  bootstrap lock: {native.bootstrap_lock.file} ({native.bootstrap_lock.digest})',
                          f'  platform lock: {native.platform_lock.file} ({native.platform_lock.digest})',
                          '  pip installs hash-verified wheels and writes two install reports.'))

    out.mkdir()
    run_python(python, ['-m', 'venv', str(out)], out, timeout=120)
    evidence = out / EVIDENCE
    evidence.mkdir()
    bootstrap_report = evidence / 'pip-bootstrap.json'
    package_report = evidence / 'pip-install.json'
    install_lock(venv_python, native.bootstrap_lock, bootstrap_report, evidence)
    bootstrap_digest = validated_report(bootstrap_report, native.bootstrap_lock)
    install_lock(venv_python, native.platform_lock, package_report, evidence)
    package_digest = validated_report(package_report, native.platform_lock)
    locked_freeze(venv_python, native, evidence)
    receipt = {
        **native.lock_evidence(),
        'pip_bootstrap_report_sha256': bootstrap_digest,
        'pip_install_report_sha256': package_digest,
        'python_build': details['full_version'],
        'python_cache_tag': details['cache_tag'],
    }
    (evidence / 'environment.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    return f'Locked build Python: {venv_python}\nBuild environment evidence: {evidence}'


def check_installed_locations(prefix):
    names = set()
    for distribution in importlib.metadata.distributions():
        name = package_name(distribution.metadata['Name'] or '')
        if not name or name in names or not Path(distribution.locate_file('')).resolve().is_relative_to(prefix):
            raise BuildError('The build venv has duplicate, invalid or externally installed package metadata.')
        names.add(name)


def check_environment(target, details):
    prefix = Path(sys.prefix).resolve()
    config = prefix / 'pyvenv.cfg'
    if (not details['venv'] or not sys.flags.isolated or site.ENABLE_USER_SITE is not False or
            config.is_symlink() or not config.is_file() or re.findall(
                r'(?mi)^include-system-site-packages[ \t]*=[ \t]*(.*)$',
                config.read_text(encoding='utf-8').lower()) != ['false']):
        raise BuildError('Run the build with -I using the isolated venv made by tools/prepare_build.py.')
    evidence = prefix / EVIDENCE
    receipt_file = evidence / 'environment.json'
    try:
        if evidence.is_symlink() or receipt_file.is_symlink():
            raise ValueError
        receipt = json.loads(receipt_file.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise BuildError('The build venv has no valid preparation receipt. Make a new venv with tools/prepare_build.py.') from None
    expected = {
        **target.lock_evidence(),
        'pip_bootstrap_report_sha256': validated_report(evidence / 'pip-bootstrap.json', target.bootstrap_lock),
        'pip_install_report_sha256': validated_report(evidence / 'pip-install.json', target.platform_lock),
        'python_build': details['full_version'],
        'python_cache_tag': details['cache_tag'],
    }
    if receipt != expected:
        raise BuildError('The build venv receipt does not match the Python build, target and locks.')
    check_installed_locations(prefix)
    return {**expected, 'pip_freeze': locked_freeze(sys.executable, target, evidence)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path, help='A new venv folder outside the source checkout')
    parser.add_argument('--python', default=sys.executable, type=Path, help='Path to the installed base CPython executable')
    parser.add_argument('--target', help='Require the named native build target')
    parser.add_argument('--plan', action='store_true', help='Inspect the target and locks without creating the venv')
    args = parser.parse_args()
    try:
        print(prepare(args.out, args.python, args.target, args.plan))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, f'Cannot prepare the build environment. {error}\n')


if __name__ == '__main__':
    main()
