import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

import samples
import server


class SampleWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.workspace = samples.Workspace(self.directory, server.Store)
        self.real_path = self.directory / server.DATABASE_NAME
        self.real_initial = self.real_path.read_bytes()
        self.initial_backups = sorted(str(path) for path in (self.directory / 'backups').rglob('*'))

    def tearDown(self):
        self.temp.cleanup()

    def start(self):
        result = self.workspace.mutate('POST', '/api/samples', {}, self.workspace.context()['token'])
        self.assertEqual(result[1]['workspace']['mode'], 'samples')
        return result[1]['workspace']['token']

    def call(self, method, path, body):
        return self.workspace.mutate(method, path, body, self.workspace.context()['token'])[1]

    def source(self, state):
        return next(item for item in self.workspace.state()['source_items']
                    if item['review_id'] == samples.SAMPLE_PREFIX + state)

    def queue(self, state='needs_you', action='delete'):
        body = {'review_id': self.source(state)['review_id'], 'action': action}
        if action == 'edit':
            body['proposed_text'] = 'The sample update could explain this change more clearly.'
        return self.call('POST', '/api/action-requests', body)['action_request']

    def test_shipped_seed_uses_real_lifecycles_without_changing_real_database(self):
        self.start()
        state = self.workspace.state()
        self.assertEqual({item['status'] for item in state['source_items']},
                         {'needs_you', 'waiting', 'verified', 'kept'})
        blocked = next(item for item in state['action_requests'] if item['execution_state'] == 'blocked')
        self.assertIn('sign-in', blocked['blocker_reason'])
        self.assertTrue(blocked['account_id'].startswith(samples.SAMPLE_PREFIX))
        verified = next(item for item in state['action_requests']
                        if item['execution_state'] == 'verified' and item['action'] == 'delete')
        self.assertTrue(verified['attempt_id'])
        self.assertIn('Sample proof', verified['receipt_note'])
        self.assertEqual(self.real_path.read_bytes(), self.real_initial)
        self.assertFalse((self.directory / server.LEGACY_DATABASE_NAME).exists())
        self.assertEqual(sorted(str(path) for path in (self.directory / 'backups').rglob('*')), self.initial_backups)

    def test_every_practice_action_and_proof_leaves_real_database_unchanged(self):
        real = server.Store(self.directory)
        account = real.mutate('POST', '/api/accounts', {'platform': 'X', 'handle': 'synthetic_real'})[1]['account']
        before = (hashlib.sha256(real.path.read_bytes()).hexdigest(), real.path.stat().st_mtime_ns)
        self.start()
        request = self.queue(action='edit')
        result = self.call('POST', '/api/samples/rehearse', {'request_id': request['id'], 'decision': 'yes'})
        self.assertEqual(result['action_request']['execution_state'], 'verified')
        self.assertEqual(self.source('needs_you')['status'], 'verified')
        waiting = self.source('waiting')
        self.call('POST', '/api/samples/rehearse', {'request_id': waiting['active_request_id'], 'decision': 'not_now'})
        self.assertEqual(self.source('waiting')['status'], 'needs_you')
        self.queue('waiting', 'keep')
        self.assertEqual(self.source('waiting')['status'], 'kept')
        kept = self.source('kept')
        self.call('PATCH', '/api/source-items/' + kept['id'], {'status': 'snoozed'})
        self.assertEqual(self.source('kept')['status'], 'snoozed')
        self.call('PATCH', '/api/source-items/' + kept['id'], {'status': 'open'})
        deletion = self.queue('kept')
        self.call('POST', '/api/samples/rehearse', {'request_id': deletion['id'], 'decision': 'yes'})
        self.assertEqual(self.source('kept')['status'], 'verified')
        self.assertEqual((hashlib.sha256(real.path.read_bytes()).hexdigest(), real.path.stat().st_mtime_ns), before)
        self.call('POST', '/api/samples/clear', {})
        self.assertEqual((hashlib.sha256(real.path.read_bytes()).hexdigest(), real.path.stat().st_mtime_ns), before)
        self.assertEqual(real.state()['accounts'], [account])

    def test_restart_and_sample_state_never_construct_or_read_real_store(self):
        self.start()
        self.queue(action='keep')
        token = self.workspace.context()['token']
        with mock.patch.object(server.Store, '__init__', side_effect=AssertionError('real Store opened')):
            restarted = samples.Workspace(self.directory, server.Store)
            state = restarted.state()
            self.assertEqual(state['workspace']['token'], token)
            self.assertEqual(next(item['status'] for item in state['source_items']
                                  if item['review_id'] == samples.SAMPLE_PREFIX + 'needs_you'), 'kept')
            self.assertTrue(restarted.data_folder_details()['samples'])
        self.assertEqual(sorted(str(path) for path in (self.directory / 'backups').rglob('*')), self.initial_backups)

    def test_stale_tab_tokens_cannot_cross_either_switch(self):
        real_token = self.workspace.context()['token']
        sample_token = self.start()
        body = {'platform': 'X', 'handle': 'synthetic_stale'}
        with self.assertRaisesRegex(samples.SampleError, 'Refresh'):
            self.workspace.mutate('POST', '/api/accounts', body, real_token)
        self.call('POST', '/api/samples/clear', {})
        self.assertNotEqual(real_token, self.workspace.context()['token'])
        with self.assertRaisesRegex(samples.SampleError, 'Refresh'):
            self.workspace.mutate('POST', '/api/accounts', body, sample_token)
        with self.assertRaisesRegex(samples.SampleError, 'Refresh'):
            self.workspace.backup(sample_token)
        with self.assertRaisesRegex(samples.SampleError, 'Refresh'):
            self.workspace.brief(real_token)
        self.assertEqual(self.real_path.read_bytes(), self.real_initial)

    def test_samples_cannot_import_export_backup_or_escape_through_monitor(self):
        token = self.start()
        for method in (self.workspace.backup, self.workspace.brief):
            with self.assertRaises(samples.SampleError):
                method(token)
        for path in ('/api/import', '/api/monitor-runs', '/api/accounts', '/api/mail-sync'):
            with self.assertRaises(samples.SampleError):
                self.call('POST', path, {})
        with self.assertRaisesRegex(samples.SampleError, 'Clear samples'):
            with samples.real_workspace(self.directory):
                self.fail('The CLI guard opened samples.')
        with self.assertRaises(samples.SampleError):
            self.workspace._demo().backup()
        with self.assertRaises(samples.SampleError):
            self.workspace._demo().work()
        capture = self.workspace.state()
        self.assertTrue(samples.has_sample_data(capture))
        self.call('POST', '/api/samples/clear', {})
        with self.assertRaisesRegex(samples.SampleError, 'Sample records'):
            self.call('POST', '/api/import', capture)

    def test_rehearsal_requires_current_queued_sample_and_has_no_real_route(self):
        with self.assertRaises(samples.SampleError):
            self.call('POST', '/api/samples/rehearse', {'request_id': 'synthetic', 'decision': 'yes'})
        self.start()
        request = self.queue()
        with self.assertRaises(samples.SampleError):
            self.call('POST', '/api/action-requests/' + request['id'] + '/start', {})
        with self.assertRaises(server.Invalid):
            self.call('POST', '/api/samples/rehearse', {'request_id': request['id'], 'decision': 'maybe'})
        self.call('POST', '/api/samples/rehearse', {'request_id': request['id'], 'decision': 'not_now'})
        with self.assertRaises(samples.SampleError):
            self.call('POST', '/api/samples/rehearse', {'request_id': request['id'], 'decision': 'yes'})

    def test_clear_removes_only_demo_and_new_practice_has_new_epoch(self):
        token = self.start()
        sentinel = self.directory / 'synthetic-unrelated.txt'
        sentinel.write_text('preserved', encoding='utf-8')
        for suffix in ('-journal', '-wal', '-shm'):
            (self.directory / (samples.DEMO_NAME + suffix)).write_bytes(b'')
        self.call('POST', '/api/samples/clear', {})
        for suffix in ('', '-journal', '-wal', '-shm'):
            self.assertFalse((self.directory / (samples.DEMO_NAME + suffix)).exists())
        self.assertEqual(sentinel.read_text(encoding='utf-8'), 'preserved')
        self.assertNotEqual(self.start(), token)
        self.assertEqual(self.source('needs_you')['status'], 'needs_you')

    def test_missing_or_corrupt_marker_does_not_fall_back_to_real_store(self):
        self.start()
        marker = self.directory / samples.MARKER_NAME
        marker.write_text('{broken', encoding='utf-8')
        with self.assertRaises(samples.SampleError):
            self.workspace.state()
        with self.assertRaises(samples.SampleError):
            with samples.real_workspace(self.directory):
                pass
        marker.unlink()
        with self.assertRaises(samples.SampleError):
            samples.Workspace(self.directory, server.Store)
        self.assertEqual(self.real_path.read_bytes(), self.real_initial)

    @unittest.skipUnless(hasattr(os, 'symlink'), 'Symlinks unavailable')
    def test_demo_links_aliases_and_sidecars_are_refused_without_touching_targets(self):
        target = self.directory / 'synthetic-target'
        target.write_bytes(b'preserved')
        demo = self.directory / samples.DEMO_NAME
        try:
            demo.symlink_to(target)
        except OSError:
            self.skipTest('This account cannot create symlinks.')
        with self.assertRaises(samples.SampleError):
            self.start()
        self.assertEqual(target.read_bytes(), b'preserved')
        demo.unlink()
        os.link(target, demo)
        with self.assertRaises(samples.SampleError):
            self.start()
        demo.unlink()
        real_alias = self.directory / server.DATABASE_NAME
        real_alias.unlink()
        real_alias.symlink_to(demo)
        with self.assertRaises(samples.SampleError):
            self.start()
        real_alias.unlink()
        self.start()
        journal = self.directory / (samples.DEMO_NAME + '-journal')
        journal.symlink_to(target)
        with self.assertRaises(samples.SampleError):
            self.workspace.state()
        with self.assertRaises(samples.SampleError):
            self.call('POST', '/api/samples/clear', {})
        self.assertEqual(target.read_bytes(), b'preserved')

    def test_cli_guard_and_http_switch_serialize_across_processes(self):
        script = ('import sys, samples\n'
                  'with samples.real_workspace(sys.argv[1]):\n'
                  ' print("locked", flush=True)\n'
                  ' sys.stdin.readline()\n')
        token = self.workspace.context()['token']
        child = subprocess.Popen([sys.executable, '-u', '-c', script, str(self.directory)],
                                 cwd=server.ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
        errors = []
        done = threading.Event()
        def switch():
            try:
                self.workspace.mutate('POST', '/api/samples', {}, token)
            except Exception as error:
                errors.append(error)
            finally:
                done.set()
        worker = threading.Thread(target=switch)
        try:
            self.assertEqual(child.stdout.readline().strip(), 'locked')
            worker.start()
            self.assertFalse(done.wait(0.15), 'The sample switch passed a held CLI lock.')
            child.communicate('\n', timeout=5)
            worker.join(5)
            self.assertTrue(done.is_set())
            self.assertEqual(errors, [])
            self.assertEqual(child.returncode, 0)
            self.assertEqual(self.workspace.context()['mode'], 'samples')
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=5)
            if worker.ident is not None:
                worker.join(5)

    def test_read_only_guard_leaves_entire_folder_unchanged_with_or_without_lock(self):
        def snapshot(folder):
            return {str(path.relative_to(folder)): (path.stat().st_mtime_ns, path.read_bytes())
                    for path in folder.rglob('*') if path.is_file()}
        before = snapshot(self.directory)
        with samples.read_only_real_workspace(self.directory):
            pass
        self.assertEqual(snapshot(self.directory), before)
        with tempfile.TemporaryDirectory() as empty:
            folder = Path(empty)
            (folder / 'synthetic-sentinel').write_bytes(b'preserved')
            before = snapshot(folder)
            with samples.read_only_real_workspace(folder):
                pass
            self.assertEqual(snapshot(folder), before)
            self.assertFalse((folder / samples.LOCK_NAME).exists())
            self.assertFalse((folder / samples.MARKER_NAME).exists())
            absent = folder / 'absent'
            with samples.read_only_real_workspace(absent):
                pass
            self.assertFalse(absent.exists())
        self.start()
        before = snapshot(self.directory)
        with self.assertRaises(samples.SampleError):
            with samples.read_only_real_workspace(self.directory):
                self.fail('Read-only guard exposed the samples.')
        self.assertEqual(snapshot(self.directory), before)

    def test_read_only_guard_without_lock_refuses_a_concurrent_switch(self):
        with tempfile.TemporaryDirectory() as empty:
            with self.assertRaisesRegex(samples.SampleError, 'changed during'):
                with samples.read_only_real_workspace(empty):
                    # Simulate the first-ever app opening during an older
                    # store's optimistic read. Its epoch now differs.
                    (Path(empty) / samples.MARKER_NAME).write_text(json.dumps({
                        'mode': 'real', 'token': '0' * 32}), encoding='utf-8')


if __name__ == '__main__':
    unittest.main()
