"""Synthetic phase and archive checks. No candidate or native signing command runs."""
import contextlib
import io
import json
import os
from pathlib import Path, PureWindowsPath
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

from tools import sign_release as signing


MAC = signing.EXECUTABLES['darwin']
WIN = signing.EXECUTABLES['win32']
MACHO = bytes.fromhex('cffaedfe0c000001') + b'synthetic Mach-O body'
PE = b'MZ' + bytes(58) + (64).to_bytes(4, 'little') + b'PE\0\0\x64\x86' + b'synthetic PE body'
THUMB = 'A' * 40
TEAM = 'ABCD123456'
NOTICES = b'Synthetic third-party notices.\n'
RUNTIME = 'b' * 64
VERSION = '0.1.0-rc.1'
NOTARY_ID = 'a3e214ad-f5c3-4a33-8a7b-13be8e62e662'
REPORT = {'version': VERSION, 'ok': True, 'frozen': True, 'seed': True, 'assistant_kit': True,
          'tcl': True, 'tcl_patch': '8.6.17'}
SDK = PureWindowsPath(r'C:\Synthetic SDK\bin\10.0.12345.0\x64\signtool.exe')
MAC_FILESYSTEM = unittest.skipUnless(os.name == 'posix', 'requires POSIX Mac bundle filesystem semantics')
META = bytes.fromhex(
    '00051607000200004d6163204f5320582020202020202020000200000009000000320000007100000002000000a300000000'
    '000000000000000000000000000000000000000000000000000000000000000000004154545200000000000000a300000098'
    '0000000b00000000000000000000000000000001000000980000000b000015636f6d2e6170706c652e70726f76656e616e636500'
    '0102000000000000000000'
)


def zipped_entry(name, body, kind):
    info = zipfile.ZipInfo(name)
    info.filename = name  # ZipInfo rewrites os.sep to '/' on Windows; keep the exact requested name.
    info.create_system = 3
    info.external_attr = kind << 16
    return info, body


class MockNative:
    def __init__(self, case):
        self.case = case
        self.calls = []
        self.zip_payloads = {}
        self.archive_modes = {}
        self.archive_types = {}
        self.unpacked_modes = {}
        self.staple_directory = 'Contents/_Stapled'
        self.fail_on = None
        self.notary_status = 'Accepted'
        self.signature_metadata = (b'CodeDirectory v=20500 size=42 flags=0x10000(runtime) hashes=7\n'
                                   b'Timestamp=synthetic\nTeamIdentifier=' + TEAM.encode() + b'\n')
        self.signature_team = TEAM
        self.signature_identity = THUMB
        self.sidecar = None
        self.report = REPORT
        self.visible_identity = False

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        self.case.assertIs(kwargs['stdin'], subprocess.DEVNULL)
        self.case.assertLessEqual(kwargs['timeout'], 24 * 60)
        self.case.assertGreater(kwargs['timeout'], 0)
        self.case.assertFalse(kwargs.get('shell', False))
        self.case.assertTrue(kwargs['check'])
        self.case.assertFalse({'PYTHONPATH', 'DYLD_INSERT_LIBRARIES', 'SIGNING_SECRET', 'GH_TOKEN'} & kwargs['env'].keys())
        if self.fail_on in args or (callable(self.fail_on) and self.fail_on(args)):
            raise subprocess.CalledProcessError(1, args)
        out = None
        if '--sign' in args or ('sign' in args and '/sha1' in args):
            path = Path(args[-1])
            if path.is_dir():
                signature = path / 'Contents' / '_CodeSignature' / 'CodeResources'
                signature.parent.mkdir(parents=True, exist_ok=True)
                signature.write_bytes(b'synthetic bundle signature')
            else:
                path.write_bytes(path.read_bytes() + b' signed')
        elif '--verify' in args:
            requirement = next(arg for arg in args if arg.startswith('-R='))
            if (f'certificate leaf = H"{self.signature_identity}"' not in requirement
                    or f'certificate leaf[subject.OU] = "{self.signature_team}"' not in requirement):
                raise subprocess.CalledProcessError(1, args)
        elif '--display' in args:
            self.case.assertIs(kwargs['stderr'], subprocess.STDOUT)
            out = self.signature_metadata
        elif 'find-identity' in args:
            out = f'1) {THUMB}\n1 identity found\n'.encode() if self.visible_identity else b'0 identities found\n'
        elif '--self-test' in args:
            self.case.assertFalse(self.case.keychain.exists())
            for option, leaf in (('--data-dir', 'data'), ('--log-dir', 'logs'), ('--self-test-report', 'report.json')):
                self.case.assertEqual(Path(args[args.index(option) + 1]), kwargs['cwd'] / leaf)
            self.case.assertEqual(args[-2:], ['--port', '0'])
            self.case.assertEqual(kwargs['env']['HOME'], str(kwargs['cwd'] / 'home'))
            (kwargs['cwd'] / 'report.json').write_text(json.dumps(self.report), encoding='utf-8')
        elif 'notarytool' in args:
            self.case.assertIs(kwargs['stdout'], subprocess.PIPE)
            out = json.dumps({'status': self.notary_status, 'id': NOTARY_ID}).encode()
            warning = b'synthetic warning on stderr'
            if kwargs['stderr'] == subprocess.STDOUT:
                out += warning
            return subprocess.CompletedProcess(args, 0, stdout=out, stderr=warning)
        elif 'staple' in args:
            directory = Path(args[-1]) / self.staple_directory
            directory.mkdir(exist_ok=True)
            (directory / 'ticket').write_bytes(b'synthetic ticket')
        elif args[0] == '/usr/bin/ditto':
            if '-c' in args:
                source, destination = map(Path, args[-2:])
                saved = self.case.root / ('zip-copy-' + str(len(self.zip_payloads)))
                shutil.copytree(source, saved, symlinks=True)
                self.zip_payloads[destination] = saved
                with zipfile.ZipFile(destination, 'w') as bundle:
                    for item in [source, *sorted(source.rglob('*'))]:
                        name = item.relative_to(source.parent).as_posix()
                        if item.is_symlink():
                            bundle.writestr(*zipped_entry(name, os.readlink(item).encode(), item.lstat().st_mode))
                        elif name in self.archive_modes or name in self.archive_types:
                            info = zipfile.ZipInfo.from_file(item, name)
                            kind = self.archive_types.get(name, stat.S_IFMT(item.lstat().st_mode))
                            mode = self.archive_modes.get(name, stat.S_IMODE(item.lstat().st_mode))
                            info.external_attr = ((kind | mode)
                                                  << 16) | (info.external_attr & 0xffff)
                            bundle.writestr(info, b'' if item.is_dir() else item.read_bytes())
                        else:
                            bundle.write(item, name)
                    if self.sidecar is not None:
                        bundle.writestr(*zipped_entry('__MACOSX/Resonance.app/Contents/MacOS/._Resonance',
                                                      self.sidecar, stat.S_IFREG | 0o644))
            else:
                archive, destination = map(Path, args[-2:])
                shutil.copytree(self.zip_payloads[archive], destination / 'Resonance.app', symlinks=True)
                for name, mode in self.unpacked_modes.items():
                    (destination / name).chmod(mode)
        return subprocess.CompletedProcess(args, 0, stdout=out)


class Stages(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='synthetic-resonance-signing-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.out = self.root / 'out'
        self.keychain = self.root / 'synthetic.keychain-db'
        self.host = patch.object(signing, 'host_target', return_value=('darwin', 'arm64'))
        self.host_mock = self.host.start()
        self.addCleanup(self.host.stop)
        native = patch.object(signing, 'native', side_effect=self.host_mock)
        native.start()
        self.addCleanup(native.stop)
        windows_root = patch.object(signing, 'windows_system_root', return_value=self.root / 'Windows')
        windows_root.start()
        self.addCleanup(windows_root.stop)
        self.quarantine_check = signing.candidate_quarantine
        quarantine = patch.object(signing, 'candidate_quarantine')
        quarantine.start()
        self.addCleanup(quarantine.stop)

    def candidate(self, files=None, links=None, extra=(), edit=None, system='darwin', arch=None, notices=True,
                  modes=None):
        arch = arch or ('arm64' if system == 'darwin' else 'x86_64')
        self.host_mock.return_value = (system, arch)
        files = files if files is not None else ({MAC: MACHO} if system == 'darwin' else {WIN: PE})
        notice_path = signing.NOTICES[system]
        if notices:
            files = {notice_path: NOTICES, **files}
        links = links or {}
        modes = modes or {}
        label = 'mac' if system == 'darwin' else 'windows'
        archive = self.root / f'Resonance-{VERSION}-{label}-{arch}-candidate.zip'
        manifest = self.root / 'BUILD-MANIFEST.json'
        with zipfile.ZipFile(archive, 'w') as bundle:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', UserWarning)
                for name, contents in files.items():
                    bundle.writestr(*zipped_entry(name, contents, stat.S_IFREG | modes.get(name, 0o755)))
                for name, target in links.items():
                    bundle.writestr(*zipped_entry(name, target.encode(), stat.S_IFLNK | 0o755))
                for entry in extra:
                    bundle.writestr(*entry)
            count = len(bundle.infolist())
        raw = archive.read_bytes()
        inventory = {name: {'sha256': signing.sha(contents), 'bytes': len(contents)} for name, contents in files.items()}
        value = {
            'product': 'Resonance', 'source_version': VERSION, 'runtime_manifest_sha256': RUNTIME,
            'os': 'Darwin' if system == 'darwin' else 'Windows', 'platform': system,
            'architecture': arch, 'target_os': 'macOS' if system == 'darwin' else 'Windows',
            'files': inventory, 'symlinks': links,
            'third_party_notices': {'path': notice_path, 'sha256': signing.sha(NOTICES), 'bytes': len(NOTICES)},
            'archive': {'name': archive.name, 'bytes': len(raw), 'sha256': signing.sha(raw), 'entries': count},
        }
        if edit:
            edit(value)
        manifest.write_bytes(signing.json_bytes(value))
        return archive, manifest, signing.sha(raw), signing.sha(manifest.read_bytes())

    def verify(self, args):
        return signing.verify_candidate(self.out, *args)

    def cli_options(self, **kwargs):
        return type('Options', (), {'identity': THUMB, 'keychain': str(self.keychain), 'signtool': None,
                                    'timestamp_url': None, 'credentials_removed': True,
                                    'keychain_profile': 'synthetic-notary', 'notary_keychain': None, **kwargs})()

    def phase(self, checkpoint, name, **kwargs):
        if name in {'notarize', 'check-notarize'} and hasattr(self, 'anchor'):
            kwargs = {'expected_sign_state_sha256': self.sign_checkpoint,
                      'expected_signed_tree_sha256': self.anchor['tree_sha256'],
                      'expected_identity': self.anchor['identity'], 'expected_team_id': self.anchor['team_id'],
                      **kwargs}
        return signing.phase(self.out, checkpoint, name, self.cli_options(**kwargs))

    def record_anchor(self, checkpoint):
        self.anchor = json.loads(self.phase(checkpoint, 'signed-anchor'))
        self.sign_checkpoint = checkpoint
        return self.anchor

    def anchor_args(self):
        return ['--expected-sign-state-sha256', self.sign_checkpoint,
                '--expected-signed-tree-sha256', self.anchor['tree_sha256'],
                '--expected-identity', self.anchor['identity'], '--expected-team-id', self.anchor['team_id']]

    def assert_refused(self, args, reason):
        with self.assertRaisesRegex(signing.SigningError, reason):
            self.verify(args)
        self.assertFalse(self.out.exists())

    def test_both_digests_required_before_output(self):
        archive, manifest, a_sha, m_sha = self.candidate()
        self.assert_refused((archive, manifest, '0' * 64, m_sha), 'SHA-256 does not match')
        self.assert_refused((archive, manifest, a_sha, '0' * 64), 'SHA-256 does not match')
        manifest.write_bytes(manifest.read_bytes() + b' ')
        self.assert_refused((archive, manifest, a_sha, m_sha), 'SHA-256 does not match')

    def test_declared_zip_metadata_and_tree_must_match(self):
        for field, replacement in [('bytes', 0), ('entries', 42), ('name', 'fake.zip'), ('sha256', '0' * 64)]:
            with self.subTest(field=field):
                args = self.candidate(edit=lambda data: data['archive'].update({field: replacement}))
                self.assert_refused(args, 'archive metadata|entry count')
        args = self.candidate(edit=lambda data: data['files'][MAC].update(sha256='0' * 64))
        self.assert_refused(args, 'file does not match')
        args = self.candidate(extra=[zipped_entry('Resonance.app/extra', b'x', stat.S_IFREG | 0o644)])
        self.assert_refused(args, 'Unexpected ZIP member')
        self.assert_refused(self.candidate(arch='x86_64'), 'launcher binary')
        self.assert_refused(self.candidate(edit=lambda data: data.update(platform='win32')), 'native target')

    def test_traversal_case_collisions_duplicates_and_devices(self):
        names = ['../escape', '/escape', 'Resonance.app/../escape', 'Resonance.app/a\\b',
                 'Resonance.app/CON.log', 'Resonance.app/LPT¹.txt', 'Resonance.app/file.']
        for name in names:
            with self.subTest(name=name):
                args = self.candidate(files={MAC: MACHO, name: b'x'})
                with zipfile.ZipFile(args[0]) as bundle:
                    self.assertIn(name, [info.orig_filename for info in bundle.infolist()])
                self.assert_refused(args, 'Unsafe archive path')
        for left, right in [('Fold/x', 'fold/y'), ('café/x', 'cafe\u0301/y')]:
            args = self.candidate(files={MAC: MACHO, f'Resonance.app/{left}': b'a', f'Resonance.app/{right}': b'b'})
            self.assert_refused(args, 'collision')
        self.assert_refused(self.candidate(extra=[zipped_entry(MAC, MACHO, stat.S_IFREG | 0o755)]), 'duplicate')
        self.assert_refused(self.candidate(extra=[zipped_entry('Resonance.app/device', b'', stat.S_IFCHR)]),
                            'Unexpected ZIP member')

    def test_links_confined_and_links_cannot_be_parents(self):
        for target in ('../out', '/absolute-synthetic', 'Contents/MacOS/Resonance/..', 'missing', 'link'):
            with self.subTest(target=target):
                args = self.candidate(links={'Resonance.app/link': target})
                self.assert_refused(args, 'symbolic link|Unsafe')
        args = self.candidate(links={'Resonance.app/link': 'Contents', 'Resonance.app/other': 'link/../../out'})
        self.assert_refused(args, 'escapes')
        args = self.candidate(files={MAC: MACHO, 'Resonance.app/link/file': b'x'},
                              links={'Resonance.app/link': 'Contents'})
        self.assert_refused(args, 'parent of an archive member')
        args = self.candidate(system='win32', links={'Resonance/link': 'Resonance.exe'})
        self.assert_refused(args, 'symbolic link')

    @MAC_FILESYSTEM
    def test_valid_link_tree_staged_from_original_verified_bytes(self):
        args = self.candidate(links={'Resonance.app/Contents/alias': 'MacOS', 'Resonance.app/launch': 'Contents/alias/Resonance'})
        original = signing.zip_layout

        def replace_input(*positional):
            result = original(*positional)
            args[0].write_bytes(b'mutated after verification')
            return result

        with patch.object(signing, 'zip_layout', side_effect=replace_input):
            checkpoint = self.verify(args)
        self.assertEqual(checkpoint, signing.sha((self.out / signing.STATE).read_bytes()))
        app = self.out / 'dist/Resonance.app'
        self.assertEqual(os.readlink(app / 'launch'), 'Contents/alias/Resonance')
        self.assertEqual((app / 'launch').read_bytes(), MACHO)
        self.assertEqual(json.loads((self.out / signing.STATE).read_bytes())['input']['archive']['sha256'], args[2])

    @MAC_FILESYSTEM
    def test_no_sidecar_extraction_sets_canonical_modes_under_private_umask(self):
        app = self.out / 'dist/Resonance.app'
        notice = signing.NOTICES['darwin']
        args = self.candidate(
            links={'Resonance.app/launch': 'Contents/MacOS/Resonance'},
            extra=[zipped_entry('Resonance.app/Contents/', b'', stat.S_IFDIR | 0o700)],
            modes={MAC: 0o700, notice: 0o666},
        )
        with patch.object(signing, 'extract_mac_metadata') as native_extract:
            mask = os.umask(0o077)
            try:
                self.verify(args)
            finally:
                os.umask(mask)
            native_extract.assert_not_called()
        for path in (self.out, app.parent):
            self.assertEqual(stat.S_IMODE(path.lstat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.out / signing.STATE).lstat().st_mode), 0o600)
        for path in (app, app / 'Contents', app / 'Contents/MacOS', app / 'Contents/Resources'):
            self.assertEqual(stat.S_IMODE(path.lstat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((self.out / 'dist' / MAC).lstat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((self.out / 'dist' / notice).lstat().st_mode), 0o644)
        self.assertEqual(os.readlink(app / 'launch'), 'Contents/MacOS/Resonance')

    def test_mac_metadata_cannot_introduce_unknown_attributes_or_orphan_paths(self):
        signing.apple_double(META)
        bad = META.replace(b'com.apple.provenance', b'com.apple.quarantine')
        for target, data, error in [('Resonance.app/Contents/MacOS/._Resonance', bad, 'Unsupported Mac ZIP attributes'),
                                    ('Resonance.app/._missing', META, 'does not correspond')]:
            args = self.candidate(extra=[zipped_entry(target, data, stat.S_IFREG | 0o644)])
            self.assert_refused(args, error)

    def test_notice_inventory_requires_canonical_regular_bytes(self):
        for system, launcher in [('darwin', MAC), ('win32', WIN)]:
            path = signing.NOTICES[system]
            with self.subTest(system=system):
                self.assert_refused(self.candidate(system=system, notices=False), 'notices are missing')
                self.assert_refused(self.candidate(system=system, edit=lambda data:
                                    data.pop('third_party_notices')), 'evidence fields')
                self.assert_refused(self.candidate(system=system, edit=lambda data:
                                    data['third_party_notices'].update(path=launcher)), 'canonical regular file')
                contents = MACHO if system == 'darwin' else PE
                self.assert_refused(self.candidate(system=system, files={launcher: contents, path: b'altered'}),
                                    'notices are missing or do not match')
        alias = signing.NOTICES['darwin']
        self.assert_refused(self.candidate(notices=False, files={MAC: MACHO, alias + '.real': NOTICES},
                                          links={alias: 'THIRD-PARTY-NOTICES.txt.real'}), 'notices are missing')

    @MAC_FILESYSTEM
    def test_notice_mutation_refused_even_with_updated_tree_digest(self):
        args = self.candidate()
        with patch.object(signing.subprocess, 'run') as run:
            self.verify(args)
            state = self.state()
            (self.out / 'dist' / signing.NOTICES['darwin']).write_bytes(b'changed')
            digest = signing.sha(signing.json_bytes(signing.snapshot(self.out / 'dist', 'darwin')))
            state['tree_sha256'] = state['evidence']['verified-input']['tree_sha256'] = digest
            with self.assertRaisesRegex(signing.SigningError, 'notices are missing or do not match'):
                self.phase(signing.save_state(self.out, state), 'sign')
            run.assert_not_called()

    def state(self):
        return json.loads((self.out / signing.STATE).read_bytes())

    def mac_tested(self, mock):
        self.keychain.touch()
        signed = self.phase(self.verify(self.candidate()), 'sign')
        before = len(mock.calls)
        with self.assertRaisesRegex(signing.SigningError, 'before exporting'):
            self.record_anchor(signed)
        self.keychain.unlink()
        self.record_anchor(signed)
        self.assertEqual(len(mock.calls), before)
        return self.phase(signed, 'self-test')

    @MAC_FILESYSTEM
    def test_recomputed_tree_and_checkpoint_cannot_replace_sign_anchor(self):
        mock = MockNative(self)
        with patch.object(signing.subprocess, 'run', side_effect=mock):
            tested = self.mac_tested(mock)
            state = self.state()
            (self.out / 'dist' / MAC).write_bytes(MACHO + b' different signed tree')
            before = len(mock.calls)
            with self.assertRaisesRegex(signing.SigningError, 'tree has changed'):
                self.phase(tested, 'notarize')
            digest = signing.sha(signing.json_bytes(signing.snapshot(self.out / 'dist', 'darwin')))
            state['tree_sha256'] = digest
            for step in ('signed', 'self-tested'):
                state['evidence'][step]['tree_sha256'] = digest
            checkpoint = signing.save_state(self.out, state)
            for action in ('check-notarize', 'notarize'):
                with self.assertRaisesRegex(signing.SigningError, 'independent SIGN anchor'):
                    self.phase(checkpoint, action)
            self.assertEqual(len(mock.calls), before)

    @MAC_FILESYSTEM
    def test_check_notarize_and_finalize_reject_permission_changes_before_native(self):
        dist = self.out / 'dist'
        cases = [
            ('Resonance.app/Contents', 0o700, 0o755),
            ('Resonance.app/Contents/_CodeSignature/CodeResources', 0o666, 0o644),
            (MAC, 0o644, 0o755),  # Both canonical, so this exercises the checkpoint digest.
        ]
        mock = MockNative(self)
        with patch.object(signing.subprocess, 'run', side_effect=mock):
            checkpoint = self.mac_tested(mock)
            for action in ('check-notarize', 'finalize'):
                before = len(mock.calls)
                for name, bad, good in cases:
                    with self.subTest(action=action, entry=name):
                        path = dist / name
                        path.chmod(bad)
                        with self.assertRaisesRegex(signing.SigningError, 'permissions|tree has changed'):
                            self.phase(checkpoint, action)
                        self.assertEqual(len(mock.calls), before)
                        self.assertEqual(stat.S_IMODE(path.lstat().st_mode), bad)
                        self.assertEqual(signing.sha((self.out / signing.STATE).read_bytes()), checkpoint)
                        self.assertFalse((self.out / 'final').exists())
                        path.chmod(good)
                if action == 'check-notarize':
                    self.assertEqual(self.phase(checkpoint, action), checkpoint)
                    checkpoint = self.phase(checkpoint, 'notarize')
                else:
                    self.phase(checkpoint, action)

    @MAC_FILESYSTEM
    def test_native_additions_get_canonical_modes_before_checkpoints_under_private_umask(self):
        dist = self.out / 'dist'
        signature = 'Resonance.app/Contents/_CodeSignature'
        ticket = 'Resonance.app/Contents/_Stapled'
        added_executable = signature + '/helper'
        outside = self.root / 'outside'
        outside.write_bytes(b'private synthetic data')
        outside.chmod(0o600)
        rewritten = 'Resonance.app/Contents/Resources/rewritten'
        args = self.candidate(files={MAC: MACHO, rewritten: b'original'}, modes={rewritten: 0o644})
        mock = MockNative(self)

        def run(args, **kwargs):
            result = mock(args, **kwargs)
            if '--sign' in args and Path(args[-1]).name == 'Resonance.app':
                (dist / added_executable).write_bytes(b'synthetic helper')
                (dist / added_executable).chmod(0o700)
                (dist / MAC).chmod(0o600)  # A rewrite must retain the original executable classification.
                (dist / rewritten).write_bytes(b'synthetic rewrite')
                (dist / rewritten).chmod(0o700)
            return result

        mask = os.umask(0o077)
        try:
            with patch.object(signing.subprocess, 'run', side_effect=run):
                self.keychain.touch()
                signed = self.phase(self.verify(args), 'sign')
                self.assertEqual(stat.S_IMODE(self.keychain.lstat().st_mode), 0o600)
                self.keychain.unlink()
                self.record_anchor(signed)
                tested = self.phase(signed, 'self-test')
                notarized = self.phase(tested, 'notarize')
                self.phase(notarized, 'finalize')
        finally:
            os.umask(mask)
        final = self.out / 'final'
        manifest = json.loads((final / 'RELEASE-BUILD-MANIFEST.json').read_bytes())
        expected = {
            signature: 0o755, signature + '/CodeResources': 0o644, added_executable: 0o755,
            ticket: 0o755, ticket + '/ticket': 0o644, MAC: 0o755, rewritten: 0o644,
        }
        with zipfile.ZipFile(final / manifest['archive']['name']) as bundle:
            for name, mode in expected.items():
                with self.subTest(entry=name):
                    self.assertEqual(manifest['permissions'][name], mode)
                    self.assertEqual(stat.S_IMODE((dist / name).lstat().st_mode), mode)
                    member = name + '/' if (dist / name).is_dir() else name
                    self.assertEqual(stat.S_IMODE(bundle.getinfo(member).external_attr >> 16), mode)
        tree = signing.snapshot(dist, 'darwin')
        (dist / (signature + '/external')).symlink_to(outside)
        with self.assertRaises(signing.SigningError):
            signing.snapshot(dist, 'darwin', normalize_from=tree)
        self.assertEqual(stat.S_IMODE(outside.lstat().st_mode), 0o600)

    @MAC_FILESYSTEM
    def test_recomputed_self_test_checkpoint_cannot_relabel_signed_evidence(self):
        def relabel_version(state):
            version = '0.1.0-rc.2'
            state['version'] = version
            state['input']['archive']['name'] = signing.candidate_name(version, 'darwin', 'arm64')
            tested = state['evidence']['self-tested']
            tested['report']['version'] = version
            tested['report_sha256'] = signing.sha(signing.json_bytes(tested['report']))

        mock = MockNative(self)
        with patch.object(signing.subprocess, 'run', side_effect=mock):
            self.mac_tested(mock)
            original = (self.out / signing.STATE).read_bytes()
            before = len(mock.calls)
            variants = {
                'source_digest': lambda s: s['input'].update(runtime_manifest_sha256='c' * 64),
                'version_label': relabel_version,
                'signed_count': lambda s: s['evidence']['signed'].update(
                    targets_verified=s['evidence']['signed']['targets_verified'] + 1),
            }
            for name, edit in variants.items():
                state = signing.parse_json(original)
                edit(state)
                checkpoint = signing.save_state(self.out, state)
                for action in ('check-notarize', 'notarize'):
                    with self.subTest(mutation=name, action=action):
                        with self.assertRaisesRegex(signing.SigningError, 'original SIGN checkpoint'):
                            self.phase(checkpoint, action)
                        self.assertEqual(len(mock.calls), before)
                        self.assertEqual((self.out / signing.STATE).read_bytes(), signing.json_bytes(state))
            self.assertFalse((self.out / 'final').exists())

    @MAC_FILESYSTEM
    def test_notary_cli_requires_the_correct_original_sign_checkpoint(self):
        mock = MockNative(self)
        with patch.object(signing.subprocess, 'run', side_effect=mock):
            tested = self.mac_tested(mock)
            before = len(mock.calls)
            for action in ('check-notarize', 'notarize'):
                argv = [action, '--out', str(self.out), '--state-sha256', tested, *self.anchor_args()[2:]]
                if action == 'notarize':
                    argv.extend(['--keychain-profile', 'synthetic-notary'])
                for extra, status, reason in [
                    ([], 2, 'invalid arguments'),
                    (['--expected-sign-state-sha256', 'g' * 64], 1, '64 hex digits'),
                    (['--expected-sign-state-sha256', tested], 1, 'original SIGN checkpoint'),
                ]:
                    with self.subTest(action=action, reason=reason), contextlib.redirect_stderr(io.StringIO()) as stderr:
                        if status == 2:
                            with self.assertRaises(SystemExit) as exit_info:
                                signing.main(argv + extra)
                            self.assertEqual(exit_info.exception.code, status)
                        else:
                            self.assertEqual(signing.main(argv + extra), status)
                        self.assertIn(reason, stderr.getvalue())
                        self.assertEqual(len(mock.calls), before)
                        self.assertEqual(signing.sha((self.out / signing.STATE).read_bytes()), tested)

    @MAC_FILESYSTEM
    def test_notarization_refuses_alternate_leaf_team_and_display_team(self):
        mock = MockNative(self)
        with patch.object(signing.subprocess, 'run', side_effect=mock):
            tested = self.mac_tested(mock)
            before = len(mock.calls)
            for options in ({'expected_identity': 'B' * 40}, {'expected_team_id': 'ZZZZ123456'}):
                with self.assertRaisesRegex(signing.SigningError, 'independent SIGN anchor'):
                    self.phase(tested, 'notarize', **options)
            self.assertEqual(len(mock.calls), before)
            for attr, alternate in [('signature_identity', 'B' * 40), ('signature_team', 'ZZZZ123456'),
                                    ('signature_metadata', mock.signature_metadata.replace(TEAM.encode(), b'ZZZZ123456'))]:
                with self.subTest(attr=attr), patch.object(mock, attr, alternate):
                    with self.assertRaisesRegex(signing.SigningError, 'signature'):
                        self.phase(tested, 'notarize')
            self.assertFalse(any('notarytool' in cmd for cmd, _ in mock.calls))
            self.assertEqual(signing.sha((self.out / signing.STATE).read_bytes()), tested)

    @MAC_FILESYSTEM
    def test_mac_generated_zip_metadata_checked_before_notary_or_ditto_extraction(self):
        mock = MockNative(self)
        quarantine = META.replace(b'com.apple.provenance', b'com.apple.quarantine')
        with patch.object(signing.subprocess, 'run', side_effect=mock):
            tested = self.mac_tested(mock)
            self.assertEqual(self.phase(tested, 'check-notarize'), tested)
            mock.sidecar = quarantine
            with self.assertRaisesRegex(signing.SigningError, 'Unsupported Mac ZIP attributes'):
                self.phase(tested, 'notarize')
            self.assertFalse(any('notarytool' in cmd for cmd, _ in mock.calls))
            mock.sidecar = META
            notarized = self.phase(tested, 'notarize')
            mock.sidecar = quarantine
            with self.assertRaisesRegex(signing.SigningError, 'Unsupported Mac ZIP attributes'):
                self.phase(notarized, 'finalize')
            self.assertFalse(any(cmd[:2] == ['/usr/bin/ditto', '-x'] for cmd, _ in mock.calls))
            self.assertFalse((self.out / 'final').exists())
            mock.sidecar = META
            self.phase(notarized, 'finalize')

    @MAC_FILESYSTEM
    def test_mac_zip_and_final_reopen_refuse_bad_permissions(self):
        mock = MockNative(self)
        cases = [('Resonance.app/Contents', 0o700), (MAC, 0o666)]
        with patch.object(signing.subprocess, 'run', side_effect=mock):
            tested = self.mac_tested(mock)
            for entry, mode in cases:
                with self.subTest(phase='notarize ZIP', entry=entry):
                    mock.archive_modes = {entry: mode}
                    with self.assertRaisesRegex(signing.SigningError, 'Noncanonical Mac bundle permissions'):
                        self.phase(tested, 'notarize')
                    self.assertFalse(any('notarytool' in cmd for cmd, _ in mock.calls))
            mock.archive_modes.clear()
            notarized = self.phase(tested, 'notarize')
            for entry, mode in cases:
                with self.subTest(phase='finalize ZIP', entry=entry):
                    mock.archive_modes = {entry: mode}
                    with self.assertRaisesRegex(signing.SigningError, 'Noncanonical Mac bundle permissions'):
                        self.phase(notarized, 'finalize')
                    self.assertFalse(any(cmd[:2] == ['/usr/bin/ditto', '-x'] for cmd, _ in mock.calls))
            mock.archive_modes.clear()
            for entry, mode in cases:
                with self.subTest(phase='finalize unpacked', entry=entry):
                    mock.unpacked_modes = {entry: mode}
                    with self.assertRaisesRegex(signing.SigningError, 'Noncanonical Mac bundle permissions'):
                        self.phase(notarized, 'finalize')
                    self.assertFalse((self.out / 'final').exists())
                    self.assertEqual(signing.sha((self.out / signing.STATE).read_bytes()), notarized)
            mock.unpacked_modes.clear()
            self.phase(notarized, 'finalize')

    @MAC_FILESYSTEM
    def test_mac_transport_requires_explicit_file_and_directory_types(self):
        mock = MockNative(self)
        with patch.object(signing.subprocess, 'run', side_effect=mock):
            tested = self.mac_tested(mock)
            for entry in ('Resonance.app/Contents', MAC):
                with self.subTest(phase='notarize', entry=entry):
                    mock.archive_types = {entry: 0}
                    with self.assertRaisesRegex(signing.SigningError, 'Unexpected Mac ZIP member type'):
                        self.phase(tested, 'notarize')
                    self.assertFalse(any('notarytool' in cmd for cmd, _ in mock.calls))
            mock.archive_types.clear()
            notarized = self.phase(tested, 'notarize')
            for entry in ('Resonance.app/Contents', MAC):
                with self.subTest(phase='finalize', entry=entry):
                    mock.archive_types = {entry: 0}
                    with self.assertRaisesRegex(signing.SigningError, 'Unexpected Mac ZIP member type'):
                        self.phase(notarized, 'finalize')
                    self.assertFalse(any(cmd[:2] == ['/usr/bin/ditto', '-x'] for cmd, _ in mock.calls))
                    self.assertFalse((self.out / 'final').exists())
            mock.archive_types.clear()
            self.phase(notarized, 'finalize')

    def test_mac_transport_rejects_unknown_local_and_central_extra_fields(self):
        data = io.BytesIO()
        member, _ = zipped_entry(MAC, MACHO, stat.S_IFREG | 0o755)
        member.extra = b'UX\x08\x00' + bytes(8)
        with zipfile.ZipFile(data, 'w') as bundle:
            bundle.writestr(member, MACHO)
        raw = data.getvalue()
        local_start = raw.index(member.extra)
        local_end = local_start + len(member.extra)
        raw = bytearray(raw[:local_end] + bytes(4) + raw[local_end:])
        raw[local_start + 2] = 12
        struct.pack_into('<H', raw, 28, 16)
        central_start, = struct.unpack_from('<I', raw, len(raw) - 6)
        struct.pack_into('<I', raw, len(raw) - 6, central_start + 4)
        with signing.candidate_zip(raw) as bundle:
            signing.check_mac_zip_headers(bundle)
        for local in (True, False):
            with self.subTest(local=local):
                start = local_start if local else raw.rindex(member.extra)
                mutated = raw[:start] + b'ZZ' + raw[start + 2:]
                with signing.candidate_zip(mutated) as bundle, self.assertRaisesRegex(
                        signing.SigningError, 'Unsupported Mac ZIP (local )?extra field'):
                    signing.check_mac_zip_headers(bundle)

    @MAC_FILESYSTEM
    def test_malformed_or_unknown_phase_evidence_refused_before_native(self):
        mock = MockNative(self)
        with patch.object(signing.subprocess, 'run', side_effect=mock):
            tested = self.mac_tested(mock)
            notarized = self.phase(tested, 'notarize')
            original = (self.out / signing.STATE).read_bytes()
            before = len(mock.calls)
            variants = [
                (lambda s: s['input']['archive'].update(name='../outside-candidate.zip'), 'archive basename'),
                (lambda s: s.update(schema=True), 'prior phase'),
                (lambda s: s.update(version=['0.1.0']), 'version evidence'),
                (lambda s: s.update(signing_keychain='/fake/../synthetic.keychain-db'), 'credential or tool path'),
                (lambda s: s['evidence'].update(unknown={}), 'evidence fields'),
                (lambda s: s['evidence'].pop('verified-input'), 'evidence fields'),
                (lambda s: s['evidence']['signed'].update(targets_verified=True), 'size or count'),
                (lambda s: s['evidence']['signed'].update(team_id='abcd123456'), 'Apple Team ID'),
                (lambda s: s['evidence']['signed'].update(identity='z' * 40), 'thumbprint'),
                (lambda s: s['evidence']['self-tested'].update(tree_sha256='f' * 64), 'self-tested evidence'),
                (lambda s: s['evidence']['notarized']['signed_anchor'].update(team_id='ZZZZ123456'),
                 'notarization evidence'),
                (lambda s: s['input'].update(runtime_manifest_sha256='c' * 64), 'original SIGN checkpoint'),
                (lambda s: s['evidence']['notarized'].update(sign_state_sha256='f' * 64), 'original SIGN checkpoint'),
            ]
            for edit, reason in variants:
                with self.subTest(reason=reason):
                    state = signing.parse_json(original)
                    edit(state)
                    with self.assertRaisesRegex(signing.SigningError, reason):
                        self.phase(signing.save_state(self.out, state), 'finalize')
            self.assertEqual(len(mock.calls), before)
            self.assertFalse((self.out / 'final').exists())
            self.assertNotEqual(signing.sha((self.out / signing.STATE).read_bytes()), notarized)

    @MAC_FILESYSTEM
    def test_signed_smoke_requires_exact_tcl_result(self):
        mock = MockNative(self)
        self.keychain.touch()
        with patch.object(signing.subprocess, 'run', side_effect=mock):
            signed = self.phase(self.verify(self.candidate()), 'sign')
            self.keychain.unlink()
            for report in ({k: v for k, v in REPORT.items() if k != 'tcl'}, {**REPORT, 'tcl': 1},
                           {**REPORT, 'tcl_patch': '8.6.17b1'}, {**REPORT, 'tcl_patch': 8617},
                           {**REPORT, 'tk': True}):
                mock.report = report
                with self.assertRaisesRegex(signing.SigningError, 'expected self-test result'):
                    self.phase(signed, 'self-test')
                self.assertEqual(signing.sha((self.out / signing.STATE).read_bytes()), signed)

    @MAC_FILESYSTEM
    def test_staged_tree_and_checkpoint_mutations_stop_before_native_commands(self):
        args = self.candidate()
        with patch.object(signing.subprocess, 'run') as run:
            checkpoint = self.verify(args)
            with self.assertRaisesRegex(signing.SigningError, 'prior phase'):
                self.phase(checkpoint, 'self-test')
            with self.assertRaisesRegex(signing.SigningError, 'prior phase'):
                self.phase(checkpoint, 'finalize')
            state = self.out / signing.STATE
            raw = state.read_bytes()
            state.write_bytes(raw + b' ')
            with self.assertRaisesRegex(signing.SigningError, 'checkpoint'):
                self.phase(checkpoint, 'sign')
            state.write_bytes(raw)
            (self.out / 'dist' / MAC).write_bytes(b'changed')
            with self.assertRaisesRegex(signing.SigningError, 'tree has changed'):
                self.phase(checkpoint, 'sign')
            run.assert_not_called()

    def test_quarantine_is_refused_and_never_changed(self):
        archive, *_ = self.candidate()
        with patch.object(signing.subprocess, 'run', return_value=subprocess.CompletedProcess(
                [], 0, stdout=b'com.apple.quarantine\n')) as run:
            with self.assertRaisesRegex(signing.SigningError, 'left untouched'):
                self.quarantine_check(archive)
        self.assertEqual(run.call_args.args[0], ['/usr/bin/xattr', '-s', str(archive)])
        self.assertFalse(self.out.exists())

    def test_invalid_cli_arguments_do_not_echo_values(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as exc:
            signing.main(['sign', '--password', 'synthetic-secret'])
        self.assertEqual(exc.exception.code, 2)
        self.assertNotIn('synthetic-secret', stderr.getvalue())

    @MAC_FILESYSTEM
    def test_mac_order_hardened_smoke_notary_and_final_hashes(self):
        inner = 'Resonance.app/Contents/Frameworks/Fake.framework'
        library = inner + '/Versions/A/Fake'
        args = self.candidate(files={MAC: MACHO, library: MACHO},
                              links={inner + '/Versions/Current': 'A', inner + '/Fake': 'Versions/Current/Fake'})
        mock = MockNative(self)
        self.keychain.touch()
        with patch.object(signing.subprocess, 'run', side_effect=mock), patch.dict(os.environ, {
                'PYTHONPATH': 'synthetic', 'DYLD_INSERT_LIBRARIES': 'synthetic', 'SIGNING_SECRET': 'synthetic',
                'GH_TOKEN': 'synthetic'}):
            initial = self.verify(args)
            signed = self.phase(initial, 'sign')
            calls = [cmd for cmd, _ in mock.calls]
            self.assertEqual([Path(cmd[-1]).name for cmd in calls if '--sign' in cmd],
                             ['Fake', 'Fake.framework', 'Resonance.app'])
            self.assertTrue(all('--deep' not in cmd and '--timestamp' in cmd and 'runtime' in cmd
                                for cmd in calls if '--sign' in cmd))
            self.assertIn('--bundle-version', calls[1])
            self.assertTrue(any('--deep' in cmd and '--verify' in cmd and '--strict' in cmd for cmd in calls))
            self.assertFalse(any('--self-test' in cmd for cmd in calls))
            with self.assertRaisesRegex(signing.SigningError, 'temporary signing keychain'):
                self.phase(signed, 'self-test')
            self.keychain.unlink()
            anchor = self.record_anchor(signed)
            with self.assertRaisesRegex(signing.SigningError, 'Attest'):
                self.phase(signed, 'self-test', credentials_removed=False)
            mock.visible_identity = True
            with self.assertRaisesRegex(signing.SigningError, 'still visible'):
                self.phase(signed, 'self-test')
            mock.visible_identity = False
            tested = self.phase(signed, 'self-test')
            with self.assertRaisesRegex(signing.SigningError, 'prior phase'):
                self.phase(tested, 'finalize')
            notarized = self.phase(tested, 'notarize')
            self.phase(notarized, 'finalize')
        calls = [cmd for cmd, _ in mock.calls]
        smoke_at = next(i for i, cmd in enumerate(calls) if '--self-test' in cmd)
        submit_at = next(i for i, cmd in enumerate(calls) if 'notarytool' in cmd)
        staple_at = next(i for i, cmd in enumerate(calls) if 'staple' in cmd)
        self.assertLess(smoke_at, submit_at)
        self.assertLess(submit_at, staple_at)
        self.assertIn('12m', calls[submit_at])
        self.assertIn('--keychain-profile', calls[submit_at])
        self.assertNotIn('--keychain', calls[submit_at])
        final = self.out / 'final'
        value = json.loads((final / 'RELEASE-BUILD-MANIFEST.json').read_bytes())
        evidence = value['evidence']
        self.assertEqual(value['signed_self_test'], REPORT)
        self.assertEqual(value['input']['build_manifest_sha256'], args[3])
        self.assertEqual(value['input']['runtime_manifest_sha256'], RUNTIME)
        self.assertEqual(value['input']['third_party_notices'], {
            'path': signing.NOTICES['darwin'], 'sha256': signing.sha(NOTICES), 'bytes': len(NOTICES)})
        self.assertEqual(evidence['notarized']['signed_anchor'], anchor)
        self.assertEqual(evidence['finalized']['signed_anchor'], anchor)
        for record in (evidence, self.state()['evidence']):
            self.assertEqual(record['notarized']['sign_state_sha256'], signed)
            self.assertEqual(record['finalized']['sign_state_sha256'], signed)
        self.assertNotEqual(evidence['verified-input']['tree_sha256'], evidence['signed']['tree_sha256'])
        self.assertNotEqual(evidence['signed']['tree_sha256'], evidence['notarized']['tree_sha256'])
        self.assertEqual(value['archive']['sha256'], signing.sha((final / value['archive']['name']).read_bytes()))
        self.assertNotEqual(value['archive']['sha256'], value['input']['archive']['sha256'])
        self.assertNotEqual(value['archive']['sha256'], evidence['notarized']['submission_archive_sha256'])
        self.assertNotIn(str(self.keychain), json.dumps(value))

    @MAC_FILESYSTEM
    def test_notary_cli_passes_only_an_explicit_ordinary_keychain(self):
        args = self.candidate()
        self.keychain.touch()
        mock = MockNative(self)
        with patch.object(signing.subprocess, 'run', side_effect=mock):
            signed = self.phase(self.verify(args), 'sign')
            self.keychain.unlink()
            self.record_anchor(signed)
            tested = self.phase(signed, 'self-test')
            notary_keychain = self.root / 'synthetic-notary.keychain-db'
            notary_keychain.touch()
            in_stage = self.out / 'inside.keychain-db'
            in_stage.touch()
            directory = self.root / 'directory.keychain-db'
            directory.mkdir()
            wrong_suffix = self.root / 'synthetic.txt'
            wrong_suffix.touch()
            invalid = [Path(notary_keychain.name), in_stage, directory, wrong_suffix,
                       self.root / 'missing.keychain-db']
            if sys.platform != 'win32':
                link = self.root / 'link.keychain-db'
                link.symlink_to(notary_keychain)
                hardlink = self.root / 'hardlink.keychain-db'
                os.link(notary_keychain, hardlink)
                invalid += [link, hardlink]
            argv = ['notarize', '--out', str(self.out), '--state-sha256', tested,
                    *self.anchor_args(), '--keychain-profile', 'synthetic-notary', '--notary-keychain']
            before = len(mock.calls)
            for path in invalid:
                with self.subTest(path=path.name), contextlib.redirect_stderr(io.StringIO()) as stderr:
                    self.assertEqual(signing.main([*argv, str(path)]), 1)
                    self.assertIn('regular temporary notary keychain path outside the stage', stderr.getvalue())
                    self.assertEqual(len(mock.calls), before)
                    self.assertEqual(signing.sha((self.out / signing.STATE).read_bytes()), tested)
            if sys.platform != 'win32':
                hardlink.unlink()
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(signing.main([*argv, str(notary_keychain)]), 0)
            self.assertRegex(stdout.getvalue().strip(), r'^[a-f0-9]{64}$')
        submits = [cmd for cmd, _ in mock.calls if cmd[1:3] == ['notarytool', 'submit']]
        self.assertEqual(len(submits), 1)
        submit = submits[0]
        self.assertEqual(submit[submit.index('--keychain') + 1], str(notary_keychain))
        self.assertEqual(submit[submit.index('--keychain-profile') + 1], 'synthetic-notary')
        self.assertNotIn(str(notary_keychain), (self.out / signing.STATE).read_text())

    @MAC_FILESYSTEM
    def test_ambiguous_framework_is_rejected_before_signing(self):
        args = self.candidate(files={MAC: MACHO, 'Resonance.app/Contents/Frameworks/Fake.framework/Fake': MACHO})
        self.keychain.touch()
        with patch.object(signing.subprocess, 'run') as run:
            with self.assertRaisesRegex(signing.SigningError, 'Framework layout requires native review'):
                self.phase(self.verify(args), 'sign')
            run.assert_not_called()

    @MAC_FILESYSTEM
    def test_bad_signature_or_missing_hardened_metadata_is_not_evidence(self):
        for failure in ('--verify', '--display', 'missing-metadata'):
            with self.subTest(failure=failure):
                args = self.candidate()
                self.keychain.touch()
                mock = MockNative(self)
                mock.fail_on = failure
                if failure == 'missing-metadata':
                    mock.signature_metadata = b'CodeDirectory flags=0x0(none)\nTeamIdentifier=' + TEAM.encode() + b'\n'
                with patch.object(signing.subprocess, 'run', side_effect=mock):
                    initial = self.verify(args)
                    with self.assertRaisesRegex(signing.SigningError, 'signature'):
                        self.phase(initial, 'sign')
                    self.assertEqual(signing.sha((self.out / signing.STATE).read_bytes()), initial)
                    self.assertFalse(any('--self-test' in cmd for cmd, _ in mock.calls))
                shutil.rmtree(self.out)

    @MAC_FILESYSTEM
    def test_failed_notary_and_failed_ticket_do_not_advance(self):
        args = self.candidate()
        self.keychain.touch()
        mock = MockNative(self)
        with patch.object(signing.subprocess, 'run', side_effect=mock):
            signed = self.phase(self.verify(args), 'sign')
            self.keychain.unlink()
            self.record_anchor(signed)
            tested = self.phase(signed, 'self-test')
            mock.notary_status = 'Invalid'
            with self.assertRaisesRegex(signing.SigningError, 'Accepted'):
                self.phase(tested, 'notarize')
            self.assertFalse(any('staple' in cmd for cmd, _ in mock.calls))
            mock.notary_status = 'Accepted'
            mock.fail_on = 'validate'
            with self.assertRaisesRegex(signing.SigningError, 'ticket validation'):
                self.phase(tested, 'notarize')
            self.assertEqual(signing.sha((self.out / signing.STATE).read_bytes()), tested)
            self.assertFalse((self.out / 'final').exists())

    def test_windows_authenticode_timestamp_and_final_hashes(self):
        args = self.candidate(system='win32', files={WIN: PE, 'Resonance/nested/module.pyd': PE})
        mock = MockNative(self)
        sdk = SDK
        with patch.object(signing.subprocess, 'run', side_effect=mock), patch.object(
                signing, 'windows_signtool', return_value=sdk):
            initial = self.verify(args)
            signed = self.phase(initial, 'sign', keychain=None, signtool=str(sdk),
                                timestamp_url='https://timestamp.example.test/')
            tested = self.phase(signed, 'self-test')
            with self.assertRaisesRegex(signing.SigningError, 'only for the native Mac'):
                self.phase(tested, 'notarize')
            self.phase(tested, 'finalize')
        sign_calls = [cmd for cmd, _ in mock.calls if 'sign' in cmd]
        self.assertEqual(len(sign_calls), 2)
        self.assertTrue(all(cmd[cmd.index('/fd') + 1] == cmd[cmd.index('/td') + 1] == 'SHA256'
                            and '/tr' in cmd for cmd in sign_calls))
        self.assertTrue(all('/pa' in cmd and '/tw' in cmd and '/all' in cmd
                            for cmd, _ in mock.calls if 'verify' in cmd))
        final = self.out / 'final'
        value = json.loads((final / 'RELEASE-BUILD-MANIFEST.json').read_bytes())
        self.assertEqual(value['status'], 'Authenticode signed')
        self.assertNotEqual(value['archive']['sha256'], args[2])
        self.assertNotIn('notarized', value['evidence'])
        self.assertNotIn('permissions', value)

    def test_windows_verify_failure_and_timeouts_do_not_create_evidence(self):
        args = self.candidate(system='win32')
        mock = MockNative(self)
        sdk = SDK
        mock.fail_on = 'verify'
        with patch.object(signing, 'windows_signtool', return_value=sdk), patch.object(
                signing.subprocess, 'run', side_effect=mock):
            initial = self.verify(args)
            with self.assertRaisesRegex(signing.SigningError, 'timestamp verification failed'):
                self.phase(initial, 'sign', keychain=None, signtool=str(sdk),
                           timestamp_url='https://timestamp.example.test/')
            self.assertEqual(signing.sha((self.out / signing.STATE).read_bytes()), initial)
            self.assertFalse(any('--self-test' in cmd for cmd, _ in mock.calls))
        with patch.object(signing.subprocess, 'run', side_effect=subprocess.TimeoutExpired(['synthetic'], 2)):
            with self.assertRaisesRegex(signing.SigningError, 'synthetic timeout handled'):
                signing.NativeCommands('win32').run(['synthetic'], 'synthetic timeout handled', timeout=2)

    @unittest.skipUnless(sys.platform == 'darwin', 'requires local ditto, no signing')
    def test_provenance_sidecar_extraction_keeps_private_dist_and_canonical_bundle(self):
        entry = zipped_entry('Resonance.app/Contents/MacOS/._Resonance', META, stat.S_IFREG | 0o600)
        args = self.candidate(extra=[entry], modes={MAC: 0o700, signing.NOTICES['darwin']: 0o666})
        mask = os.umask(0o077)
        try:
            self.verify(args)
        finally:
            os.umask(mask)
        dist = self.out / 'dist'
        tree = signing.snapshot(dist, 'darwin')
        self.assertEqual(tree['permissions'], {
            'Resonance.app': 0o755, 'Resonance.app/Contents': 0o755,
            'Resonance.app/Contents/MacOS': 0o755, 'Resonance.app/Contents/Resources': 0o755,
            MAC: 0o755, signing.NOTICES['darwin']: 0o644,
        })
        self.assertEqual(stat.S_IMODE(dist.lstat().st_mode), 0o700)
        self.assertEqual((dist / MAC).read_bytes(), MACHO)

    @unittest.skipUnless(sys.platform == 'darwin', 'requires local ditto, no signing')
    def test_ditto_candidate_transport_preserves_verified_link_and_tree(self):
        source = self.root / 'source'
        launcher = source / MAC
        launcher.parent.mkdir(parents=True)
        launcher.write_bytes(MACHO)
        launcher.chmod(0o755)
        notice = source / signing.NOTICES['darwin']
        notice.parent.mkdir()
        notice.write_bytes(NOTICES)
        notice.chmod(0o755)
        (source / 'Resonance.app/launch').symlink_to('Contents/MacOS/Resonance')
        archive, manifest, _, _ = self.candidate()
        subprocess.run(['/usr/bin/ditto', '-c', '-k', '--keepParent', source / 'Resonance.app', archive],
                       timeout=15, stdin=subprocess.DEVNULL, check=True)
        raw = archive.read_bytes()
        value = json.loads(manifest.read_bytes())
        with zipfile.ZipFile(archive) as bundle:
            value['archive'].update(entries=len(bundle.infolist()), sha256=signing.sha(raw), bytes=len(raw))
        value['symlinks'] = {'Resonance.app/launch': 'Contents/MacOS/Resonance'}
        manifest.write_bytes(signing.json_bytes(value))
        with patch.object(signing, 'candidate_quarantine', side_effect=self.quarantine_check):
            self.verify((archive, manifest, signing.sha(raw), signing.sha(manifest.read_bytes())))
        self.assertEqual((self.out / 'dist/Resonance.app/launch').read_bytes(), MACHO)
        dist = self.out / 'dist'
        tree = signing.snapshot(dist, 'darwin')
        transport = self.root / 'native-transport.zip'
        signing.make_mac_zip(signing.NativeCommands('darwin').run, dist / 'Resonance.app', transport)
        signing.checked_mac_zip(transport, self.state(), tree)


if __name__ == '__main__':
    unittest.main()
