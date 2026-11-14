"""Audit the fixed signing workflow with stdlib only; never dispatch it."""
import hashlib
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'tools/sign_release.py'
WORKFLOW = (ROOT / '.github/workflows/desktop-sign.yml').read_text(encoding='utf-8')
PINS = {
    'checkout': '3d3c42e5aac5ba805825da76410c181273ba90b1',
    'download-artifact': '9000827ccba6bdab643e8b6fd33ac0654aef8333',
    'upload-artifact': 'cf430e030ddbb5b0abf93d22962f4752f3646cd9',
}
PROVENANCE = bytes.fromhex(
    '00051607000200004d6163204f5320582020202020202020000200000009000000320000007100000002000000a300000000'
    '000000000000000000000000000000000000000000000000000000000000000000004154545200000000000000a300000098'
    '0000000b00000000000000000000000000000001000000980000000b000015636f6d2e6170706c652e70726f76656e616e636500'
    '0102000000000000000000'
)


def workflow_blocks():
    """Split this workflow's fixed job/step blocks and resolve its step aliases."""
    _, source = WORKFLOW.split('\njobs:\n', 1)
    _, *parts = re.split(r'^  (\w+):\n', source, flags=re.M)
    jobs, anchors = {}, {}
    for name, body in zip(parts[::2], parts[1::2]):
        head, source = body.split('    steps:\n', 1)
        steps = []
        for step in re.split(r'^      - ', source, flags=re.M)[1:]:
            if step.startswith('&'):
                anchor, step = step.split('\n', 1)
                step = step.lstrip()
                anchors[anchor[1:]] = step
            elif step.startswith('*'):
                step = anchors[step.strip()[1:]]
            steps.append(step)
        jobs[name] = head, steps
    return jobs


def run_blocks(steps):
    for step in steps:
        body = re.search(r'^        run: (?:&\w+ )?\|\n(.*)', step, re.M | re.S)
        if body:
            yield textwrap.dedent(body[1])
        elif re.search(r'^        run: \*restore_stage$', step, re.M):
            yield RESTORE


def python_blocks(script):
    return re.findall(r"<<'PY'\n(.*?)^PY$", script, re.M | re.S)


def step_with(steps, text):
    matches = [step for step in steps if text in step]
    if len(matches) != 1:
        raise AssertionError(f'Expected one step containing {text!r}, found {len(matches)}')
    return matches[0]


JOBS = workflow_blocks()
RESTORE = next(run_blocks([step_with(JOBS['self_test'][1], 'run: &restore_stage')]))


class SigningWorkflow(unittest.TestCase):
    def test_manual_protected_provenance_and_exact_tool(self):
        self.assertEqual(set(JOBS), {'sign', 'self_test', 'notarize'})
        self.assertEqual(re.search(r'^on:\n(.*?)^permissions:', WORKFLOW, re.M | re.S)[1].count('  workflow_dispatch:'), 1)
        triggers = re.findall(r'^  ([a-z_]+):', WORKFLOW.split('\npermissions:', 1)[0], re.M)
        self.assertEqual(triggers, ['workflow_dispatch'])
        self.assertIn('\npermissions:\n  contents: read\n', WORKFLOW)
        self.assertNotRegex(WORKFLOW, r': write\s')
        sign, steps = JOBS['sign']
        for gate in (
            "github.event_name == 'workflow_dispatch'", 'github.event.repository.private == true',
            "github.ref_type == 'branch' && github.ref_protected",
            "github.ref == format('refs/heads/{0}', github.event.repository.default_branch)",
            'github.workflow_sha == github.sha',
            "github.workflow_ref == format('{0}/.github/workflows/desktop-sign.yml@{1}', github.repository, github.ref)",
        ):
            self.assertIn(gate, sign)
        self.assertIn('actions: read', sign)
        self.assertIn("'macos-15-intel' || 'macos-15'", sign)
        provenance = steps[0]
        for gate in (
            '"$RUN_ID" =~ ^[1-9][0-9]*$', '"$ARCHIVE_SHA" =~ ^[a-f0-9]{64}$',
            '"$MANIFEST_SHA" =~ ^[a-f0-9]{64}$', 'resonance-candidate-macos-arm64|resonance-candidate-macos-x86_64',
            '(.repository.id | tostring) == $repo', '(.head_repository.id | tostring) == $repo',
            '.head_sha == $sha', '.head_branch == $branch', '.path == ".github/workflows/desktop-candidate.yml"',
            '.event == "workflow_dispatch"', '.status == "completed"', '.conclusion == "success"',
        ):
            self.assertIn(gate, provenance)
        self.assertIn('run-id: ${{ inputs.candidate_run_id }}', steps[3])
        self.assertIn('name: ${{ inputs.candidate_artifact }}', steps[3])
        self.assertIn('--archive-sha256 "$ARCHIVE_SHA" --manifest-sha256 "$MANIFEST_SHA"', steps[4])
        for name, (header, steps) in JOBS.items():
            checkout = step_with(steps, 'uses: actions/checkout@')
            self.assertIn('ref: ${{ github.workflow_sha }}', checkout)
            self.assertIn('persist-credentials: false', checkout)
            prepare = step_with(steps, 'git rev-parse HEAD')
            self.assertIn('= "$GITHUB_SHA"', prepare)
            self.assertIn('/usr/bin/uname -m', prepare)
            self.assertRegex(header, r'(?m)^    timeout-minutes: [1-9]\d*$')
            if name != 'sign':
                self.assertIn('runs-on: *native_runner', header)
        uses = re.findall(r'\buses: (\S+)', WORKFLOW)
        self.assertCountEqual(uses, [f'actions/{action}@{sha}' for action, sha in PINS.items()
                                     for _ in range(1 if action == 'checkout' else 3)])

    def test_strict_chain_and_step_local_credentials(self):
        phase_order = {
            'sign': ('verify', 'sign', 'signed-anchor'),
            'self_test': ('self-test',),
            'notarize': ('check-notarize', 'notarize', 'finalize'),
        }
        for name, (header, steps) in JOBS.items():
            with self.subTest(job=name):
                phases = re.findall(r'/usr/bin/python3 -I -B "\$GITHUB_WORKSPACE/tools/sign_release\.py" ([a-z-]+)',
                                    '\n'.join(run_blocks(steps)))
                self.assertEqual(tuple(phases), phase_order[name])
                self.assertNotRegex(header, r'(?m)^    (env|continue-on-error):')
                # No stage can bypass its predecessor; always() is for cleanup alone.
                self.assertEqual(re.findall(r'(?m)^    if:.*$', header), ['    if: >-'] if name == 'sign' else [])
                for step in steps:
                    if 'if: ' in step:
                        self.assertIn('Always remove the temporary keychain', step)
        self.assertIn('environment: resonance-signing', JOBS['sign'][0])
        self.assertIn('needs: sign\n', JOBS['self_test'][0])
        self.assertIn('needs: [sign, self_test]\n', JOBS['notarize'][0])
        self.assertIn('environment: resonance-notarization', JOBS['notarize'][0])
        self.assertNotIn('environment:', JOBS['self_test'][0])
        self.assertNotIn('secrets', '\n'.join(JOBS['self_test'][1]))
        self.assertIn('--credentials-removed', step_with(JOBS['self_test'][1], 'id: phase'))
        credentials = {
            'sign': (('MAC_CERTIFICATE_P12_BASE64', 'MAC_CERTIFICATE_PASSWORD'), ('MAC_SIGNING_IDENTITY',)),
            'notarize': (('MAC_NOTARY_KEY_P8_BASE64',), ('MAC_NOTARY_KEY_ID', 'MAC_NOTARY_ISSUER')),
        }
        found = []
        for name, (secrets, variables) in credentials.items():
            _, steps = JOBS[name]
            step = step_with(steps, 'id: phase')
            for context, names in [('secrets', secrets), ('vars', variables)]:
                for key in names:
                    found.append(f'{context}.{key}')
                    self.assertIn(f'{key}: ${{{{ {context}.{key} }}}}', step)
            self.assertIn('trap cleanup EXIT', step)
            self.assertIn("trap 'exit 130' INT", step)
            self.assertIn("trap 'exit 143' TERM", step)
            self.assertIn('cleanup || status=1', step)
            # Hosted VMs can have identical temp paths. Notary must not create
            # a keychain at the signing path recorded in the checkpoint.
            auth_path = '$RUNNER_TEMP/resonance-auth-$GITHUB_JOB'
            self.assertIn(auth_path, step_with(steps, 'name: Prepare disposable keychain cleanup'))
            self.assertIn(auth_path, step)
            cleanup = step_with(steps, 'if: ${{ always() }}')
            self.assertGreater(steps.index(cleanup), steps.index(step))
            self.assertLess(steps.index(cleanup), steps.index(step_with(steps, 'uses: actions/upload-artifact@')))
            self.assertIn('keychain.py" cleanup', cleanup)
            self.assertIn(auth_path, cleanup)
        self.assertCountEqual(re.findall(r'\b(?:secrets|vars)\.[A-Z_0-9]+', WORKFLOW), found)
        signing = step_with(JOBS['sign'][1], 'id: phase')
        self.assertEqual(signing.count('-passin env:MAC_CERTIFICATE_PASSWORD'), 2)
        self.assertIn("-s -k ''", signing)
        notary = step_with(JOBS['notarize'][1], 'id: phase')
        self.assertIn('notarytool store-credentials "$profile" --key "$auth/notary.p8"', notary)
        self.assertIn('--keychain-profile "$profile" --notary-keychain "$keychain"', notary)
        self.assertNotRegex(notary, r'--(?:no-validate|sync|password|apple-id)\b')
        self.assertNotRegex(WORKFLOW, r'\b(?:set-default-keychain|pip|npm|unittest|pyinstaller|gh release|brew|'
                                      r'build_desktop\.py|make_release\.py|prepare_build\.py)\b')
        self.assertNotRegex(WORKFLOW, r'(?m)^ +continue-on-error:')

    def test_raw_digest_and_checkpoint_handoffs(self):
        for name, previous in [('self_test', 'sign'), ('notarize', 'self_test')]:
            previous_job, previous_steps = JOBS[previous]
            steps = JOBS[name][1]
            download = step_with(steps, 'uses: actions/download-artifact@')
            self.assertIn(f'artifact-ids: ${{{{ needs.{previous}.outputs.artifact_id }}}}', download)
            self.assertNotRegex(download, r'github-token:|\brun-id:|\brepository:')
            self.assertIn('skip-decompress: true', download)
            restore = step_with(steps, f'CARRIER_SHA: ${{{{ needs.{previous}.outputs.carrier_sha256 }}}}')
            phase = step_with(steps, 'id: phase')
            self.assertIn(f'CHECKPOINT: ${{{{ needs.{previous}.outputs.checkpoint }}}}', phase)
            self.assertLess(steps.index(download), steps.index(restore))
            self.assertLess(steps.index(restore), steps.index(phase))
            for out, source in [('artifact_id', 'upload.outputs.artifact-id'),
                                ('carrier_sha256', 'pack.outputs.carrier_sha256'),
                                ('checkpoint', 'pack.outputs.checkpoint')]:
                self.assertIn(f'{out}: ${{{{ steps.{source} }}}}', previous_job)
            pack = step_with(previous_steps, 'id: pack')
            self.assertIn('CHECKPOINT: ${{ steps.phase.outputs.checkpoint }}', pack)
            self.assertIn('ditto -c -k --keepParent --sequesterRsrc "$work/stage" "$carrier"', pack)
            self.assertIn('shasum -a 256 < "$carrier"', pack)
            self.assertIn('"$digest" "$CHECKPOINT" >> "$GITHUB_OUTPUT"', pack)
            upload = step_with(previous_steps, 'uses: actions/upload-artifact@')
            self.assertIn(f'path: ${{{{ runner.temp }}}}/resonance-work/carrier/{previous}-stage-', upload)
            self.assertIn('archive: false', upload)
            self.assertIn('retention-days: 1', upload)
        verify = step_with(JOBS['sign'][1], 'id: verify')
        sign = step_with(JOBS['sign'][1], 'id: phase')
        self.assertIn('CHECKPOINT: ${{ steps.verify.outputs.checkpoint }}', sign)
        self.assertLess(JOBS['sign'][1].index(verify), JOBS['sign'][1].index(sign))
        final_steps = JOBS['notarize'][1]
        finalize = step_with(final_steps, 'sign_release.py" finalize')
        self.assertIn('CHECKPOINT: ${{ steps.phase.outputs.checkpoint }}', finalize)
        self.assertLess(final_steps.index(step_with(final_steps, 'if: ${{ always() }}')), final_steps.index(finalize))
        self.assertEqual(re.findall(r'(?m)^            \$\{\{ runner.temp \}\}(/[^\n]+)$', final_steps[-1]),
                         ['/resonance-work/stage/final/*.zip', '/resonance-work/stage/final/RELEASE-BUILD-MANIFEST.json'])
        for step in (verify, sign, step_with(JOBS['self_test'][1], 'id: phase'), step_with(final_steps, 'id: phase')):
            self.assertRegex(step, r'checkpoint="\$\(/usr/bin/python3 -I -B')
            self.assertLess(step.index('[[ "$checkpoint" =~ ^[a-f0-9]{64}$ ]]'),
                            step.index('>> "$GITHUB_OUTPUT"'))
        self.assertNotRegex(WORKFLOW, r'\b(?:SIGNING-STATE\.json|\$GITHUB_ENV)\b')

    def test_notary_uses_sign_anchor_after_cleanup_and_before_credentials(self):
        sign_head, sign_steps = JOBS['sign']
        anchor = step_with(sign_steps, 'id: anchor')
        cleanup = step_with(sign_steps, 'if: ${{ always() }}')
        self.assertLess(sign_steps.index(cleanup), sign_steps.index(anchor))
        self.assertIn('CHECKPOINT: ${{ steps.phase.outputs.checkpoint }}', anchor)
        self.assertIn('signed-anchor', anchor)
        self.assertNotIn('secrets.', anchor)
        _, notary_steps = JOBS['notarize']
        preflight = step_with(notary_steps, 'sign_release.py" check-notarize')
        phase = step_with(notary_steps, 'id: phase')
        self.assertLess(notary_steps.index(step_with(notary_steps, 'run: *restore_stage')),
                        notary_steps.index(preflight))
        self.assertLess(notary_steps.index(preflight),
                        notary_steps.index(step_with(notary_steps, 'Prepare disposable keychain cleanup')))
        for step in (preflight, phase):
            self.assertRegex(step, r'(?m)^          SIGN_CHECKPOINT: \$\{\{ needs\.sign\.outputs\.checkpoint \}\}$')
            self.assertIn('--expected-sign-state-sha256 "$SIGN_CHECKPOINT"', step)
        for out, var, flag in [('signed_tree_sha256', 'SIGNED_TREE_SHA', '--expected-signed-tree-sha256'),
                               ('signing_identity', 'SIGNING_IDENTITY', '--expected-identity'),
                               ('signing_team_id', 'SIGNING_TEAM_ID', '--expected-team-id')]:
            self.assertIn(f'{out}: ${{{{ steps.anchor.outputs.{out} }}}}', sign_head)
            for step in (preflight, phase):
                self.assertIn(f'{var}: ${{{{ needs.sign.outputs.{out} }}}}', step)
                self.assertIn(f'{flag} "${var}"', step)
        self.assertNotIn('secrets.', preflight)

    def test_all_run_blocks_parse(self):
        # On Windows, a "bash" on PATH might be a WSL launcher.
        bash = shutil.which('bash') if sys.platform != 'win32' else None
        scripts = [s for _, steps in JOBS.values() for s in run_blocks(steps)]
        self.assertEqual(len(scripts), 19)  # Includes scripts used through aliases.
        for script in scripts:
            with self.subTest(script=script.splitlines()[1]):
                if bash:
                    result = subprocess.run([bash, '--noprofile', '--norc', '-n'], input=script, text=True,
                                            capture_output=True, timeout=5, env={'PATH': os.defpath})
                    self.assertEqual(result.returncode, 0, result.stderr)
                for block in python_blocks(script):
                    compile(block, '<workflow Python>', 'exec')

    def test_cleanup_restores_search_list_even_if_keychain_deletion_fails(self):
        step = step_with(JOBS['sign'][1], 'name: Prepare disposable keychain cleanup')
        script, = python_blocks(next(run_blocks([step])))
        with tempfile.TemporaryDirectory(prefix='synthetic-workflow-') as path:
            root = Path(path).resolve()
            original = root / 'existing.keychain-db'
            original.touch()
            scratch = root / 'temporary.keychain-db'
            search = [str(original)]
            calls = []
            fail_delete = False

            def security(args, **kwargs):
                self.assertEqual(args[0], '/usr/bin/security')
                command = args[1]
                calls.append(command)
                output = ''
                if command == 'default-keychain':
                    output = f'  "{original}"\n'
                elif command == 'list-keychains':
                    if '-s' in args:
                        search[:] = args[args.index('-s') + 1:]
                    output = '\n'.join(f'  "{keychain}"' for keychain in search)
                elif command == 'create-keychain':
                    self.assertEqual(args[2:], ['-p', '', str(scratch)])
                    scratch.touch()
                    search.append(str(scratch))
                elif command == 'delete-keychain':
                    self.assertEqual(args[2:], [str(scratch)])
                    if fail_delete:
                        raise subprocess.CalledProcessError(1, args)
                    scratch.unlink()
                else:
                    self.fail(f'Unexpected security command {command}')
                return subprocess.CompletedProcess(args, 0, stdout=output)

            def invoke(command):
                with patch.object(sys, 'argv', ['keychain.py', command]):
                    exec(compile(script, '<keychain workflow>', 'exec'), {'__file__': str(root / 'keychain.py')})

            with patch.object(subprocess, 'run', side_effect=security):
                invoke('create')
                self.assertTrue(scratch.is_file())
                self.assertEqual(search, [str(original)])
                self.assertEqual(calls[2:4], ['create-keychain', 'list-keychains'])
                inputs = [root / name for name in ('certificate.p12', 'certificate.pem', 'key.pem', 'notary.p8')]
                for input_file in inputs:
                    input_file.write_bytes(b'synthetic')
                fail_delete = True
                search.append(str(scratch))
                with self.assertRaisesRegex(SystemExit, 'Disposable keychain operation failed'):
                    invoke('cleanup')
                self.assertTrue(scratch.is_file())
                self.assertEqual(search, [str(original)])
                self.assertFalse(any(p.exists() for p in inputs))
                fail_delete = False
                invoke('cleanup')
                invoke('cleanup')
                self.assertFalse(scratch.exists())
                self.assertTrue(original.exists())

    def test_carrier_guard_rejects_redirected_or_unexpected_entries(self):
        guard, = python_blocks(RESTORE)
        with tempfile.TemporaryDirectory(prefix='synthetic-workflow-') as path:
            carrier = Path(path) / 'stage.zip'

            def create(extra):
                entries = {
                    'stage/': (stat.S_IFDIR | 0o700, b''),
                    'stage/SIGNING-STATE.json': (stat.S_IFREG | 0o600, b'{}'),
                    'stage/dist/': (stat.S_IFDIR | 0o700, b''),
                    'stage/dist/Resonance.app/': (stat.S_IFDIR | 0o755, b''),
                    'stage/dist/Resonance.app/Contents/': (stat.S_IFDIR | 0o755, b''),
                    'stage/dist/Resonance.app/Contents/MacOS/': (stat.S_IFDIR | 0o755, b''),
                    'stage/dist/Resonance.app/Contents/MacOS/Resonance': (stat.S_IFREG | 0o755, b'synthetic'),
                }
                entries.update(extra)
                with zipfile.ZipFile(carrier, 'w') as archive:
                    for name, (mode, data) in entries.items():
                        info = zipfile.ZipInfo(name)
                        info.create_system = 3
                        info.external_attr = mode << 16
                        archive.writestr(info, data)

            variants = [
                ({'stage/dist/Resonance.app/link': (stat.S_IFLNK | 0o755, b'Contents/MacOS/Resonance')}, True),
                ({'stage/dist/Resonance.app/link': (stat.S_IFLNK | 0o755, b'../../outside')}, False),
                ({'stage/../../outside': (stat.S_IFREG | 0o644, b'x')}, False),
                ({'stage/SIGNING-STATE.json': (stat.S_IFLNK | 0o755, b'outside')}, False),
                ({'stage/SIGNING-STATE.json': (stat.S_IFREG | 0o644, b'{}')}, False),
                ({'stage/dist/': (stat.S_IFDIR | 0o755, b'')}, False),
                ({'__MACOSX/stage/._unknown': (stat.S_IFREG | 0o644, b'x')}, False),
            ]
            for sidecar in ('__MACOSX/stage/dist/Resonance.app/Contents/MacOS/._Resonance',
                            'stage/dist/Resonance.app/Contents/MacOS/._Resonance'):
                variants.extend([
                    ({sidecar: (stat.S_IFREG | 0o644, PROVENANCE)}, True),
                    ({sidecar: (stat.S_IFREG | 0o644, PROVENANCE.replace(
                        b'com.apple.provenance', b'com.apple.quarantine'))}, False),
                    ({sidecar: (stat.S_IFREG | 0o644, b'x' * 4097)}, False),
                ])
            for extra, ok in variants:
                with self.subTest(member=next(iter(extra))):
                    create(extra)
                    result = subprocess.run([sys.executable, '-I', '-B', '-c', guard, str(TOOL), str(carrier)],
                                            capture_output=True, timeout=10)
                    self.assertEqual(result.returncode == 0, ok, result.stderr)

    @unittest.skipUnless(sys.platform == 'darwin', 'Mac ditto transport check')
    def test_carrier_roundtrip_checks_digest_and_rejects_non_provenance_before_ditto(self):
        bash = shutil.which('bash')
        with tempfile.TemporaryDirectory(prefix='synthetic-workflow-') as path:
            root = Path(path)
            src = root / 'stage/dist/Resonance.app'
            launcher = src / 'Contents/MacOS/Resonance'
            launcher.parent.mkdir(parents=True)
            resources = src / 'Contents/Resources'
            resources.mkdir()
            resource = resources / 'data'
            resource.write_bytes(b'synthetic data')
            resource.chmod(0o644)
            launcher.write_bytes(b'synthetic')
            launcher.chmod(0o755)
            for directory in (src, src / 'Contents', launcher.parent, resources):
                directory.chmod(0o755)
            for directory in (root / 'stage', src.parent):
                directory.chmod(0o700)
            (src / 'launch').symlink_to('Contents/MacOS/Resonance')
            state = root / 'stage/SIGNING-STATE.json'
            state.write_text('{}')
            state.chmod(0o600)
            work = root / 'resonance-work'
            (work / 'carrier').mkdir(parents=True)
            carrier = work / 'carrier/stage.zip'
            good = root / 'provenance-only.zip'
            archive = ['/usr/bin/ditto', '-c', '-k', '--keepParent', '--sequesterRsrc', root / 'stage']
            subprocess.run([*archive, good], check=True, timeout=10)
            bad_dir = root / 'private-bundle-dir.zip'
            resources.chmod(0o700)
            subprocess.run([*archive, bad_dir], check=True, timeout=10)
            resources.chmod(0o755)
            bad_file = root / 'writable-bundle-file.zip'
            resource.chmod(0o666)
            subprocess.run([*archive, bad_file], check=True, timeout=10)
            resource.chmod(0o644)
            subprocess.run(['/usr/bin/xattr', '-w', 'com.example.synthetic', 'unsafe', launcher],
                           check=True, timeout=5)
            bad = root / 'unknown-metadata.zip'
            subprocess.run([*archive, bad], check=True, timeout=10)
            for job in ('self_test', 'notarize'):
                restore, = run_blocks([step_with(JOBS[job][1], 'name: Verify and restore')])
                restore = restore.replace('/usr/bin/python3', shlex.quote(sys.executable))
                variants = [(good, False, False), (bad, True, False), (bad_dir, True, False),
                            (bad_file, True, False), (good, True, True)]
                for source, valid_digest, accepted in variants:
                    with self.subTest(job=job, carrier=source.name, valid_digest=valid_digest):
                        shutil.copyfile(source, carrier)
                        expected = hashlib.sha256(carrier.read_bytes()).hexdigest() if valid_digest else '0' * 64
                        script = restore
                        if not accepted:
                            # An extraction attempt would leave a marker, even if it then failed.
                            script = restore.replace('/usr/bin/ditto -x -k',
                                                     'touch "$work/native-called" && /usr/bin/ditto -x -k')
                        result = subprocess.run(
                            [bash, '--noprofile', '--norc', '-c', script],
                            env={'RUNNER_TEMP': str(root), 'GITHUB_WORKSPACE': str(ROOT), 'CARRIER_SHA': expected,
                                 'PATH': os.defpath}, capture_output=True, timeout=15,
                        )
                        self.assertEqual(result.returncode == 0, accepted, result.stderr)
                        self.assertFalse((work / 'native-called').exists())
                        self.assertEqual((work / 'stage').exists(), accepted)
                        if valid_digest and not accepted:
                            self.assertIn(b'Invalid signing stage carrier', result.stderr)
                restored_stage = work / 'stage'
                restored = restored_stage / 'dist/Resonance.app'
                self.assertEqual(os.readlink(restored / 'launch'), 'Contents/MacOS/Resonance')
                self.assertEqual((restored / 'Contents/MacOS/Resonance').read_bytes(), b'synthetic')
                for relative, mode in {
                    '': 0o700, 'dist': 0o700, 'SIGNING-STATE.json': 0o600,
                    'dist/Resonance.app': 0o755, 'dist/Resonance.app/Contents/MacOS/Resonance': 0o755,
                    'dist/Resonance.app/Contents/Resources': 0o755,
                    'dist/Resonance.app/Contents/Resources/data': 0o644,
                }.items():
                    self.assertEqual(stat.S_IMODE((restored_stage / relative).lstat().st_mode), mode)
                shutil.rmtree(restored_stage)


if __name__ == '__main__':
    unittest.main()
