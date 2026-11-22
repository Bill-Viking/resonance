"""Verify source-launcher build inputs without running any native tools."""
import hashlib
import importlib.util
import json
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest import mock

from test_reader_package import write_export

BUILDER = Path(__file__).resolve().parents[1] / 'mac-launcher' / 'build.py'
spec = importlib.util.spec_from_file_location('legacy_builder', BUILDER)
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)


class LegacyBuildTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.files = {'mac-launcher/' + name: f'Synthetic wrapper fixture: {name}\n'.encode()
                      for name in legacy.WRAPPER_SOURCES}
        self.runtime = write_export(self.root / 'runtime', self.files)
        self.output = self.root / 'output' / 'Source Launcher.app'

    def test_uses_verified_inputs_metadata_and_macos_target(self):
        calls = []
        original_verify = legacy.verify_runtime

        def verified_then_changed(runtime):
            result = original_verify(runtime)
            (runtime / 'mac-launcher' / 'ResonanceLauncher.m').write_bytes(b'Synthetic mutation.')
            return result

        def native_stub(command, **kwargs):
            calls.append(command)
            if command[0] == '/usr/bin/clang':
                source = next(Path(arg) for arg in command if arg.endswith('.m'))
                self.assertEqual(source.read_bytes(), self.files['mac-launcher/ResonanceLauncher.m'])
                self.assertIn('-mmacosx-version-min=13.0', command)
            self.assertTrue(kwargs['check'])
            self.assertGreater(kwargs['timeout'], 0)

        with mock.patch.object(legacy, 'verify_runtime', side_effect=verified_then_changed), \
                mock.patch.object(legacy.subprocess, 'run', side_effect=native_stub), \
                mock.patch.object(legacy.Path, 'home', return_value=self.root / 'home'):
            self.assertEqual(legacy.build(self.runtime, self.output, self.root / 'data'), self.output.resolve())
        self.assertEqual(len(calls), 5)
        contents = self.output / 'Contents'
        plist = plistlib.loads((contents / 'Info.plist').read_bytes())
        self.assertEqual(plist['CFBundleIdentifier'], 'io.github.example-owner.resonance.launcher')
        self.assertEqual(plist['CFBundleDisplayName'], 'Resonance Source Launcher')
        self.assertEqual(plist['LSMinimumSystemVersion'], '13.0')
        self.assertEqual(plist['CFBundleShortVersionString'], '0.1.0')
        self.assertEqual(plist['ResonanceReleaseVersion'], '0.1.0-rc.7')
        config = json.loads((contents / 'Resources' / 'config.json').read_bytes())
        self.assertEqual(config['version'], '0.1.0-rc.7')
        self.assertEqual(config['runtime'], str(self.runtime.resolve()))
        self.assertEqual(config['state_dir'], str(self.root / 'home' / 'Library' / 'Logs' / legacy.APP_NAME))
        self.assertEqual(config['data_dir'], str((self.root / 'data').resolve()))
        self.assertEqual(config['manifest_sha256'],
                         hashlib.sha256((self.runtime / 'RELEASE-MANIFEST.json').read_bytes()).hexdigest())
        self.assertEqual((contents / 'Resources' / 'launch.py').read_bytes(), self.files['mac-launcher/launch.py'])

    def test_refuses_unaudited_and_changed_exports_before_creating_output(self):
        absent = self.root / 'unaudited'
        absent.mkdir()
        (absent / 'server.py').write_text('Synthetic server.')
        malformed = write_export(self.root / 'invalid', self.files)
        (malformed / 'RELEASE-MANIFEST.json').write_text('Synthetic invalid manifest.')
        (self.runtime / 'static' / 'icon.svg').write_bytes(b'Synthetic mutation.')
        with mock.patch.object(legacy.subprocess, 'run') as native:
            for runtime in (absent, malformed, self.runtime):
                with self.subTest(runtime=runtime.name), self.assertRaisesRegex(ValueError, '(?i)manifest'):
                    legacy.build(runtime, self.output)
                self.assertFalse(self.output.parent.exists())
            native.assert_not_called()

    def test_malformed_or_missing_owner_has_a_plain_cli_error(self):
        for index, (release_json, message) in enumerate(((b'{', 'not valid JSON'), (b'{}', 'valid github_owner'),
                                                        (b'null', 'valid github_owner'), (b'{"github_owner":"../bad"}',
                                                                                           'valid github_owner'))):
            runtime = write_export(self.root / str(index), dict(self.files, **{'release.json': release_json}))
            with self.subTest(release_json=release_json), mock.patch.object(legacy.argparse.ArgumentParser, 'exit') as stop:
                stop.side_effect = SystemExit(1)
                with self.assertRaises(SystemExit):
                    legacy.main(['--runtime', str(runtime), '--output', str(self.output)])
                self.assertIn(message, stop.call_args.args[1])
                self.assertFalse(self.output.exists())
        with self.assertRaisesRegex(ValueError, 'release.json is missing'):
            legacy.bundle_identifier(self.root / 'missing.json')
