"""Tests for the desktop environment contract, without installing wheels or starting a GUI."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tools import build_desktop, prepare_build


ROOT = Path(__file__).resolve().parents[1]


def mac_details(**changes):
    return {'implementation': 'cpython', 'version': '3.14.10', 'full_version': '3.14.10 (synthetic build)',
            'cache_tag': 'cpython-314', 'free_threaded': False, 'platform': 'darwin',
            'architecture': 'arm64', 'native': True, 'host_version': '15.0',
            'deployment_target': '10.15', 'venv': False, **changes}


def write_report(path, lock):
    path.write_text(json.dumps({
        'version': '1',
        'install': [{'metadata': {'name': name, 'version': version},
                     'download_info': {'url': f'https://example.invalid/{name}.whl',
                                       'archive_info': {'hashes': {'sha256': wheel_hash}}}}
                    for name, (version, wheel_hash) in lock.packages.items()],
    }), encoding='utf-8')


class BuildEnvironmentTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name).resolve()
        self.target = prepare_build.select_target(mac_details())

    def test_target_requires_exact_version_gil_os_architecture_and_minimum_os(self):
        for changes, reason in (({'version': '3.14.9'}, 'CPython 3.14.10'),
                                ({'implementation': 'pypy'}, 'CPython 3.14.10'),
                                ({'free_threaded': True}, 'GIL'),
                                ({'platform': 'linux'}, 'no desktop target'),
                                ({'platform': 'win32'}, 'no desktop target'),
                                ({'native': False}, 'same host architecture'),
                                ({'host_version': '12.7'}, 'macOS 13.0'),
                                ({'deployment_target': '14.0'}, 'deployment target'),
                                ({'deployment_target': None}, 'unknown')):
            with self.subTest(changes=changes), self.assertRaisesRegex(prepare_build.BuildError, reason):
                prepare_build.select_target(mac_details(**changes))
        with self.assertRaisesRegex(prepare_build.BuildError, 'requires its own native'):
            prepare_build.select_target(mac_details(), 'macos-x86_64')
        intel = prepare_build.select_target(mac_details(architecture='x86_64'))
        windows = prepare_build.select_target(mac_details(platform='win32', architecture='x86_64',
                                                          host_version='10.0.19045', deployment_target=None))
        self.assertEqual((intel.name, intel.minimum_os), ('macos-x86_64', '13.0'))
        self.assertEqual((windows.name, windows.minimum_os), ('windows-x86_64', '10.0'))
        self.assertEqual(intel.platform_lock.digest, self.target.platform_lock.digest)
        self.assertNotEqual(windows.platform_lock.digest, intel.platform_lock.digest)

    def test_locks_pin_every_native_dependency_and_agree_with_runtime_requirements(self):
        direct = {'pyinstaller': build_desktop.PYINSTALLER_VERSION, 'tzdata': build_desktop.TZDATA_VERSION}
        common = set(direct) | {'altgraph', 'packaging', 'pyinstaller-hooks-contrib', 'setuptools'}
        windows = prepare_build.select_target(mac_details(platform='win32', architecture='x86_64',
                                                          host_version='10.0.19045', deployment_target=None))
        for target, extra in ((self.target, {'macholib'}), (windows, {'pefile', 'pywin32-ctypes'})):
            self.assertEqual(set(target.platform_lock.packages), common | extra)
            self.assertEqual(set(target.bootstrap_lock.packages), {'pip'})
            self.assertEqual(target.bootstrap_lock.packages['pip'][0], '26.3')
            self.assertEqual({name: target.platform_lock.packages[name][0] for name in direct}, direct)
        declared = (ROOT / 'desktop' / 'requirements-build.txt').read_text(encoding='utf-8')
        self.assertEqual({line for line in declared.splitlines() if line and not line.startswith('#')},
                         {f'{name}=={version}' for name, version in direct.items()})
        build_desktop.check_runtime_tzdata((ROOT / 'requirements.txt').read_bytes())

    def test_missing_and_mutated_locks_refuse_before_install_and_windows_checkout_is_stable(self):
        desktop = self.directory / 'desktop'
        shutil.copytree(ROOT / 'desktop' / 'locks', desktop / 'locks')
        shutil.copyfile(ROOT / 'desktop' / 'build-targets.json', desktop / 'build-targets.json')
        path = desktop / self.target.platform_lock.file
        original = path.read_bytes().replace(b'\r\n', b'\n')
        path.write_bytes(original.replace(b'\n', b'\r\n'))
        self.assertEqual(prepare_build.select_target(mac_details(), root=self.directory).platform_lock,
                         self.target.platform_lock)
        path.write_bytes(original + b'# changed\n')
        with self.assertRaisesRegex(prepare_build.BuildError, 'digest does not match'):
            prepare_build.select_target(mac_details(), root=self.directory)
        path.unlink()
        with self.assertRaisesRegex(prepare_build.BuildError, 'missing'):
            prepare_build.select_target(mac_details(), root=self.directory)

    def test_plan_never_creates_output_and_dirty_or_source_outputs_are_refused(self):
        out = self.directory / 'venv'
        with mock.patch.object(prepare_build, 'probe_base', return_value=mac_details()) as probe, \
                mock.patch.object(prepare_build, 'run_python') as python:
            result = prepare_build.prepare(out, target='macos-arm64', plan=True)
            self.assertIn(self.target.platform_lock.digest, result)
            self.assertIn(self.target.bootstrap_lock.digest, result)
            self.assertFalse(out.exists())
            python.assert_not_called()
            out.mkdir()
            (out / 'keep.txt').write_text('synthetic')
            with self.assertRaisesRegex(prepare_build.BuildError, 'new folder'):
                prepare_build.prepare(out, plan=True)
            with self.assertRaisesRegex(prepare_build.BuildError, 'outside the source'):
                prepare_build.prepare(ROOT / 'build-venv', plan=True)
            probe.assert_called_once()
            self.assertEqual((out / 'keep.txt').read_text(), 'synthetic')

    def test_prep_refuses_an_existing_venv_as_the_base_interpreter(self):
        result = subprocess.CompletedProcess([], 0, stdout=json.dumps(mac_details(venv=True)))
        with mock.patch.object(prepare_build, 'run_python', return_value=result):
            with self.assertRaisesRegex(prepare_build.BuildError, 'outside an existing virtual environment'):
                prepare_build.prepare(self.directory / 'venv', plan=True)

    def test_inherited_python_and_pip_configuration_is_stripped(self):
        inherited = dict.fromkeys(('PYTHONHOME', 'PythonPath', 'PIP_INDEX_URL', 'PIP_CONFIG_FILE', 'VIRTUAL_ENV',
                                   'VIRTUAL_ENV_PROMPT', 'LD_PRELOAD', 'DYLD_LIBRARY_PATH', 'PYINSTALLER_CONFIG_DIR',
                                   '__PYVENV_LAUNCHER__', 'TK_LIBRARY'), 'synthetic')
        inherited['PATH'] = 'ordinary'
        self.assertEqual(prepare_build.clean_environment(inherited), {'PATH': 'ordinary', 'PYTHONNOUSERSITE': '1'})
        with mock.patch.dict(os.environ, inherited), mock.patch.object(prepare_build.subprocess, 'run') as run:
            prepare_build.run_python(sys.executable, ['-m', 'pip', '--isolated'], self.directory, pip=True)
        command, = run.call_args.args
        self.assertEqual(command[1:3], ['-I', '-B'])
        options = run.call_args.kwargs
        self.assertEqual(options['env']['PIP_CONFIG_FILE'], os.devnull)
        self.assertNotIn('PIP_INDEX_URL', options['env'])
        self.assertEqual(options['stdin'], subprocess.DEVNULL)
        self.assertNotIn('shell', options)

    def test_python_child_ignores_a_dirty_cwd_and_inherited_module_path(self):
        (self.directory / 'json.py').write_text('raise AssertionError("synthetic shadow module was imported")\n')
        with mock.patch.dict(os.environ, {'PYTHONPATH': str(self.directory), 'PYTHONHOME': str(self.directory)}):
            result = prepare_build.run_python(sys.executable, ['-c', 'import json; print(json.dumps(True))'],
                                              self.directory, capture=True)
        self.assertEqual(result.stdout.strip(), 'true')

    def test_prep_runs_hash_checked_wheel_installs_and_keeps_reports_and_lock_digests(self):
        out = self.directory / 'venv'
        locks = {Path(lock.file).name: lock for lock in (self.target.bootstrap_lock, self.target.platform_lock)}
        seen = []

        def fake_python(python, arguments, cwd, **options):
            seen.append(arguments[:3])
            self.assertGreater(options.get('timeout', 0), 0)
            if arguments[:2] == ['-m', 'venv']:
                self.assertEqual(Path(arguments[-1]), out)
            else:
                self.assertEqual(python, out / 'bin/python')
                self.assertEqual(cwd, out / prepare_build.EVIDENCE)
                self.assertTrue(options['pip'])
                lock_path = Path(arguments[arguments.index('-r') + 1])
                lock = locks[lock_path.name]
                self.assertEqual(lock_path.read_bytes(), lock.content)
                self.assertIn('--isolated', arguments)
                self.assertIn('--require-hashes', arguments)
                self.assertIn('--force-reinstall', arguments)
                self.assertEqual(arguments[arguments.index('--only-binary') + 1], ':all:')
                write_report(Path(arguments[arguments.index('--report') + 1]), lock)

        with mock.patch.object(prepare_build, 'probe_base', return_value=mac_details()), \
                mock.patch.object(prepare_build, 'run_python', side_effect=fake_python), \
                mock.patch.object(prepare_build, 'locked_freeze', return_value=[]) as freeze:
            prepare_build.prepare(out)
        self.assertEqual(seen, [['-m', 'venv', str(out)], ['-m', 'pip', '--isolated'], ['-m', 'pip', '--isolated']])
        freeze.assert_called_once()
        evidence = out / prepare_build.EVIDENCE
        receipt = json.loads((evidence / 'environment.json').read_text(encoding='utf-8'))
        self.assertEqual(receipt['environment_lock_sha256'], self.target.platform_lock.digest)
        self.assertEqual(receipt['bootstrap_lock_sha256'], self.target.bootstrap_lock.digest)
        self.assertEqual(receipt['pip_install_report_sha256'],
                         prepare_build.validated_report(evidence / 'pip-install.json', self.target.platform_lock))
        self.assertEqual(receipt['pip_bootstrap_report_sha256'],
                         prepare_build.validated_report(evidence / 'pip-bootstrap.json', self.target.bootstrap_lock))

    def test_installed_packages_must_match_every_pin_with_no_extras(self):
        expected = {name: version for lock in (self.target.bootstrap_lock, self.target.platform_lock)
                    for name, (version, _) in lock.packages.items()}
        expected_freeze = [f'{name}=={version}' for name, version in expected.items()]
        with mock.patch.object(prepare_build, 'run_python') as run:
            run.return_value.stdout = '\n'.join(expected_freeze)
            self.assertEqual(prepare_build.locked_freeze(sys.executable, self.target, self.directory), expected_freeze)
            for packages, reason in ((expected_freeze[1:], 'pip==26.3'),
                                     (expected_freeze + ['unexpected==1'], 'unexpected packages'),
                                     (['macholib==0.1' if line.startswith('macholib==') else line
                                       for line in expected_freeze], 'macholib==1.16.4'),
                                     (expected_freeze + ['pip==26.3'], 'duplicate'),
                                     (expected_freeze + ['dirty @ file:///example.whl'], 'unpinned')):
                run.return_value.stdout = '\n'.join(packages)
                with self.assertRaisesRegex(prepare_build.BuildError, reason):
                    prepare_build.locked_freeze(sys.executable, self.target, self.directory)
        outside = self.directory / 'outside'
        distribution = mock.Mock(metadata={'Name': 'pip'})
        distribution.locate_file.return_value = outside
        with mock.patch.object(prepare_build.importlib.metadata, 'distributions', return_value=[distribution]):
            with self.assertRaisesRegex(prepare_build.BuildError, 'externally installed'):
                prepare_build.check_installed_locations(self.directory / 'venv')

    def test_build_rejects_an_inherited_site_or_stale_target_receipt(self):
        cfg = self.directory / 'pyvenv.cfg'
        evidence = self.directory / prepare_build.EVIDENCE
        evidence.mkdir()
        with mock.patch.object(prepare_build.sys, 'prefix', str(self.directory)), \
                mock.patch.object(prepare_build.sys, 'flags', mock.Mock(isolated=1)), \
                mock.patch.object(prepare_build.site, 'ENABLE_USER_SITE', False):
            cfg.write_text('include-system-site-packages = true\n')
            with self.assertRaisesRegex(prepare_build.BuildError, 'isolated venv'):
                prepare_build.check_environment(self.target, mac_details(venv=True))
            cfg.write_text('include-system-site-packages = false\n')
            with self.assertRaisesRegex(prepare_build.BuildError, 'preparation receipt'):
                prepare_build.check_environment(self.target, mac_details(venv=True))
            write_report(evidence / 'pip-bootstrap.json', self.target.bootstrap_lock)
            write_report(evidence / 'pip-install.json', self.target.platform_lock)
            receipt = self.target.lock_evidence()
            receipt['target'] = 'macos-x86_64'
            (evidence / 'environment.json').write_text(json.dumps(receipt))
            with self.assertRaisesRegex(prepare_build.BuildError, 'receipt does not match'):
                prepare_build.check_environment(self.target, mac_details(venv=True))
            (evidence / 'pip-install.json').write_text('{"version": "1", "install": []}')
            with self.assertRaisesRegex(prepare_build.BuildError, 'pip report'):
                prepare_build.check_environment(self.target, mac_details(venv=True))

    def test_workflows_pin_actions_and_run_synthetic_suites_with_a_venv(self):
        for name in ('checks.yml', 'desktop-candidate.yml'):
            text = (ROOT / '.github' / 'workflows' / name).read_text(encoding='utf-8')
            actions = re.findall(r'(?m)^ +\- uses: (\S+) # (v\d+\.\d+\.\d+)$', text)
            self.assertEqual(len(actions), text.count('uses:'))
            self.assertGreaterEqual(len(actions), 3)
            for action, _ in actions:
                self.assertRegex(action, r'^actions/[a-z-]+@[0-9a-f]{40}$')
            self.assertIn("python-version: '3.14.10'", text)
            self.assertIn("node-version: '24'", text)
            self.assertIn('VENV_PYTHON -E -s -m unittest discover -s tests', text)
            self.assertIn('tools/prepare_build.py', text)
            self.assertIn('$bin >> $env:GITHUB_PATH', text)
            self.assertIn('python3.exe', text)
            self.assertNotRegex(text, r'\bsecrets\s*[.\[]')
            self.assertNotIn('python -m pip install', text)


if __name__ == '__main__':
    unittest.main()
