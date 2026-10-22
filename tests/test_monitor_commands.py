import copy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import monitor
import server
from identity_helpers import observation, start_checked


class MonitorCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / 'data'
        self.report = json.loads((server.ROOT / 'examples/report.sample.json').read_text())
        self.learned = json.loads((server.ROOT / 'examples/learned.sample.json').read_text())

    def cli(self, *args):
        return subprocess.run([sys.executable, str(server.ROOT / 'monitor.py'),
                               '--data-dir', str(self.data), *map(str, args)],
                              capture_output=True, text=True)

    def files(self):
        return {str(path.relative_to(self.root)): (path.read_bytes(), path.stat().st_mtime_ns,
                                                    path.stat().st_mode)
                for path in self.root.rglob('*') if path.is_file()}

    def document(self, value):
        path = self.root / 'document.json'
        path.write_text(json.dumps(value))
        return path

    def fixture(self, start=True):
        with patch('server.now', return_value='2026-10-01T11:00:00Z'):
            self.store = server.Store(self.data)
            self.account = self.store.mutate('POST', '/api/accounts', {
                'platform': 'YouTube', 'handle': 'synthetic'})[1]['account']
            self.review = self.store.mutate('POST', '/api/reviews', {
                'account_id': self.account['id'], 'kind': 'comment', 'title': 'Review this comment',
                'excerpt': 'A synthetic saved comment.', 'severity': 'attention',
                'finding': 'A concrete concern.',
                'source_url': 'https://www.youtube.com/watch?v=example&lc=comment'})[1]['review']
        if start:
            self.start_request()

    def start_request(self):
        with patch('server.now', return_value='2026-10-01T12:00:00Z'):
            request = self.store.mutate('POST', '/api/action-requests', {
                'review_id': self.review['id'], 'action': 'delete'})[1]['action_request']
            self.request = start_checked(self.store, request['id'])[1]['action_request']
        return self.request

    def attest(self, at='2026-10-01T12:05:00Z', verified=True):
        with patch('server.now', return_value=at):
            attestation = self.store.mutate('POST', '/api/attestations', {
                'review_id': self.review['id'], 'action': 'delete',
                'note': 'I removed this synthetic comment myself.'})[1]['attestation']
            if verified:
                outcome = dict(id=attestation['id'], target_type='attestation',
                               account_id=self.account['id'], source_key=attestation['source_key'],
                               evidence_fingerprint=attestation['evidence_fingerprint'], outcome='verified',
                               reason='', receipt_url=attestation['source_url'],
                               receipt_note='The synthetic source shows the requested result.', next_move='none')
                self.store.mutate('POST', '/api/monitor-runs', dict(
                    id='verify-attestation', started_at=at, completed_at=at, summary='Synthetic verification.',
                    accounts=[dict(account_id=self.account['id'], status='checked', checked_at=at,
                                   summary='Verified synthetic result.', coverage='One synthetic source.',
                                   items_reviewed=1, deleted_count=1, pending_deletion_count=0, findings=[])],
                    requests=[outcome]))
        with self.store.connect() as db:
            return self.store.get(db, 'attestations', attestation['id'])

    def read_request(self):
        before = self.files()
        result = self.cli('--request', self.request['id'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.files(), before, 'a last look must leave every data file unchanged')
        return json.loads(result.stdout)

    def test_identity_commands_match_then_start_and_last_look_is_read_only(self):
        self.fixture(start=False)
        self.request = self.store.mutate('POST', '/api/action-requests', {
            'review_id': self.review['id'], 'action': 'delete'})[1]['action_request']
        document = self.document(observation(self.account, self.request))
        checked = self.cli('--check-identity', self.request['id'], '--observation', document)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        checked = json.loads(checked.stdout)['action_request']
        self.assertTrue(checked['can_execute'])
        started = self.cli('--start-request', self.request['id'], '--identity-check-id', checked['identity_check']['id'])
        self.assertEqual(started.returncode, 0, started.stderr)
        self.assertEqual(json.loads(started.stdout)['action_request']['execution_state'], 'running')
        self.assertTrue(self.read_request()['identity_current'])

    def test_identity_command_records_a_mismatch_without_starting(self):
        self.fixture(start=False)
        self.request = self.store.mutate('POST', '/api/action-requests', {
            'review_id': self.review['id'], 'action': 'delete'})[1]['action_request']
        document = self.document(observation(self.account, self.request,
                                  handle='other_example', profile_url='https://www.youtube.com/@other_example'))
        checked = self.cli('--check-identity', self.request['id'], '--observation', document)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        checked = json.loads(checked.stdout)['action_request']
        self.assertFalse(checked['can_execute'])
        started = self.cli('--start-request', self.request['id'], '--identity-check-id', checked['identity_check']['id'])
        self.assertEqual(started.returncode, 1)
        self.assertIn('does not match', started.stderr)
        self.assertIsNone(self.read_request()['action_request']['attempt_id'])

    def test_identity_option_errors_do_not_open_or_create_data(self):
        for args in (('--start-request', 'missing'), ('--check-identity', 'missing'),
                     ('--status', '--identity-check-id', 'unused'),
                     ('--status', '--observation', self.root / 'missing.json')):
            with self.subTest(args=args):
                result = self.cli(*args)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(self.data.exists())

    def test_examples_validate_without_creating_or_opening_data(self):
        for name in ('report', 'learned'):
            result = self.cli('--validate', server.ROOT / 'examples' / (name + '.sample.json'))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['scope'], 'shape_only')
            self.assertFalse(self.data.exists())
        # Validation cannot even consult a configured private directory.
        with patch('monitor.data_directory', side_effect=AssertionError('data folder read')), \
                patch('monitor.Store', side_effect=AssertionError('store opened')), redirect_stdout(io.StringIO()):
            self.assertEqual(monitor.main(['--validate', str(server.ROOT / 'examples/report.sample.json')]), 0)

    def test_profile_is_read_only_filters_accounts_and_never_loads_mail(self):
        self.fixture()
        self.store.mutate('POST', '/api/accounts', {'platform': 'Gmail', 'handle': 'example@example.test'})
        self.store.mutate('PATCH', '/api/brand', {'voice': 'Plain and specific.'})
        before = self.files()
        with patch.object(server.Store, 'state', side_effect=AssertionError('full workspace read')), \
                patch.object(server.Store, 'upgrade_database', side_effect=AssertionError('upgrade')):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(monitor.main(['--data-dir', str(self.data), '--profile', '--account', self.account['id']]), 0)
        profile = json.loads(output.getvalue())
        self.assertEqual([a['id'] for a in profile['accounts']], [self.account['id']])
        self.assertEqual(profile['brand']['voice'], 'Plain and specific.')
        self.assertFalse(profile['learning']['available'])
        self.assertEqual(profile['precedents'], {'kept': [], 'changed': []})
        self.assertEqual(before, self.files())
        with self.store.connect() as db:
            self.assertEqual(json.loads(db.execute('SELECT data FROM action_requests').fetchone()[0])['execution_state'], 'running')
        all_accounts = json.loads(self.cli('--profile').stdout)['accounts']
        self.assertEqual(len(all_accounts), 2)
        self.assertNotIn('access_note', all_accounts[0])

    def test_profile_past_choices_use_snapshotted_words_not_a_later_review(self):
        self.fixture(start=False)
        self.store.mutate('POST', '/api/action-requests', {'review_id': self.review['id'], 'action': 'keep'})
        with self.store.connect() as db:
            self.store.put(db, 'reviews', server.review_record(dict(self.review, excerpt='Later different words.')))
        before = self.files()
        profile = json.loads(self.cli('--profile').stdout)
        self.assertEqual(profile['precedents']['kept'][0]['excerpt'], self.review['excerpt'])
        self.assertEqual(before, self.files())
        self.attest()
        profile = json.loads(self.cli('--profile', '--account', self.account['id']).stdout)
        self.assertEqual(profile['precedents']['changed'][0]['action'], 'delete')

    def test_profile_refusals_and_missing_workspace_do_not_create_or_modify_files(self):
        result = self.cli('--profile')
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.data.exists())
        self.assertEqual(self.cli('--account', 'unknown', '--status').returncode, 2)
        self.assertFalse(self.data.exists())
        self.fixture(start=False)
        before = self.files()
        self.assertEqual(self.cli('--profile', '--account', 'unknown').returncode, 1)
        self.assertEqual(before, self.files())
        with self.store.connect() as db:
            db.execute('PRAGMA user_version=999')
        before = self.files()
        self.assertEqual(self.cli('--profile').returncode, 1)
        self.assertEqual(before, self.files())

    def test_profile_refuses_samples_without_touching_the_real_store(self):
        self.fixture(start=False)
        workspace = server.samples.Workspace(self.data, server.Store)
        workspace.mutate('POST', '/api/samples', {}, token=workspace.state()['workspace']['token'])
        before = self.files()
        result = self.cli('--profile')
        self.assertEqual(result.returncode, 1)
        self.assertIn('Samples are showing', result.stderr)
        self.assertEqual(before, self.files())

    def test_validation_is_shape_only_but_report_keeps_saved_account_and_path_checks(self):
        server.Store(self.data)
        before = self.files()
        result = self.cli('--validate', server.ROOT / 'examples/report.sample.json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, self.files())
        result = self.cli('--report', server.ROOT / 'examples/report.sample.json')
        self.assertEqual(result.returncode, 1)
        self.assertIn('outside repositories', result.stderr)
        result = self.cli('--report', self.document(self.report))
        self.assertEqual(result.returncode, 1)
        self.assertIn('unknown account', result.stderr)
        with server.Store(self.data).connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM monitor_runs').fetchone()[0], 0)

    def test_learned_shape_and_optional_report_field_without_ingestion(self):
        value = dict(self.report, learned=self.learned)
        result = self.cli('--validate', self.document(value))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['learned'], 1)
        invalid = []
        for change in ({'scope': 'all'}, {'scope': ''}, {'kind': 'diagnosis'}, {'statement': ''},
                       {'statement': 'x' * 301}, {'evidence': []}, {'evidence': self.learned[0]['evidence'] * 6},
                       {'evidence': [{'source_url': '', 'quote': 'Words'}]},
                       {'evidence': [{'source_url': 'https://social.example/post/1', 'quote': ''}]},
                       {'evidence': [{'source_url': 'https://social.example/post/1', 'quote': 'x' * 301}]},
                       {'extra': 'unexpected'}):
            proposal = copy.deepcopy(self.learned[0]); proposal.update(change)
            invalid.append([proposal])
        invalid += [self.learned * 21, [None], {'learned': self.learned}]
        for value in invalid:
            with self.subTest(value=value):
                result = self.cli('--validate', self.document(value))
                self.assertEqual(result.returncode, 1)
                self.assertTrue(result.stderr.startswith('Resonance monitoring:'))
                self.assertEqual(result.stdout, '')
        self.assertFalse(self.data.exists())
        with self.assertRaises(server.Invalid):
            server.monitor_run_record(dict(self.report, learned=self.learned), account_ids=None)

    def test_validation_refuses_bad_report_shapes_and_json_without_side_effects(self):
        variants = []
        def variant():
            result = copy.deepcopy(self.report); variants.append(result); return result
        variant()['accounts'].append(copy.deepcopy(self.report['accounts'][0]))
        variant()['accounts'][0]['items_reviewed'] = True
        variant()['accounts'][0]['findings'][0]['quotes'] = ['Not copied from the text.']
        variant()['accounts'][0]['findings'][0]['unexpected'] = 'extra'
        variant()['completed_at'] = '2026-10-01T17:59:59Z'
        variant()['requests'] = [None]
        for value in variants:
            with self.subTest(value=value):
                result = self.cli('--validate', self.document(value))
                self.assertEqual(result.returncode, 1)
                self.assertNotIn('Traceback', result.stderr)
        path = self.root / 'bad.json'
        for raw in (b'{"id":"first","id":"second"}', b'NaN', b'\xff', b'x' * (server.MAX_BODY + 1)):
            path.write_bytes(raw)
            result = self.cli('--validate', path)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, '')
        self.assertFalse(self.data.exists())

    def test_request_running_snapshot_preserves_attempt_and_work_presentation(self):
        self.fixture()
        expected = self.store.state(activity=False)['action_requests'][0]
        result = self.read_request()
        self.assertEqual(result['action_request'], expected)
        self.assertEqual(result['action_request']['execution_state'], 'running')
        self.assertEqual(result['action_request']['attempt_id'], self.request['attempt_id'])
        self.assertTrue(result['evidence_current'])
        self.assertFalse(result['user_completed'])
        # The existing work command remains deliberately different.
        with patch('server.now', return_value='2026-10-01T12:10:00Z'):
            self.assertEqual(self.store.work()['verification']['requests'][0]['execution_state'], 'needs_verification')

    def test_newly_verified_user_action_still_stops_running_attempt(self):
        self.fixture()
        self.assertEqual(self.attest()['execution_state'], 'verified')
        result = self.read_request()
        self.assertTrue(result['evidence_current'])
        self.assertTrue(result['user_completed'])
        self.assertEqual(result['action_request']['execution_state'], 'running')

    def test_older_resolved_user_action_does_not_stop_fresh_attempt(self):
        self.fixture(start=False)
        self.attest(at='2026-10-01T11:30:00Z')
        self.start_request()
        result = self.read_request()
        self.assertTrue(result['evidence_current'])
        self.assertFalse(result['user_completed'])

    def test_attestation_times_are_parsed_with_equality_and_both_time_fields(self):
        self.fixture()
        attestation = self.attest()
        old = '2026-10-01T13:59:59+02:00'  # 11:59:59Z, despite the later-looking hour.
        equal = '2026-10-01T07:00:00-05:00'  # Exactly the attempt's 12:00:00Z.
        for created, done, expected in ((old, old, False), (old, equal, True), (equal, old, True)):
            with self.subTest(created=created, done=done):
                value = dict(attestation, created_at=created, done_at=done)
                with self.store.connect() as db:
                    self.store.put_attestation(db, value)
                self.assertEqual(self.read_request()['user_completed'], expected)
        for state in ('needs_verification', 'blocked'):
            value = dict(attestation, created_at=old, done_at=old, execution_state=state,
                         blocker_reason='Could not look.' if state == 'blocked' else '',
                         verified_at=None, receipt_url='', receipt_note='')
            with self.store.connect() as db:
                self.store.put_attestation(db, value)
            self.assertTrue(self.read_request()['user_completed'])

    def test_attestation_for_another_source_or_account_does_not_stop_attempt(self):
        self.fixture()
        attestation = self.attest()
        other = self.store.mutate('POST', '/api/accounts', {'platform': 'YouTube', 'handle': 'other-synthetic'})[1]['account']
        for changes in ({'source_key': 'https://www.youtube.com/watch?v=example&lc=other'},
                        {'account_id': other['id']}):
            with self.subTest(changes=changes):
                with self.store.connect() as db:
                    self.store.put_attestation(db, dict(attestation, **changes))
                self.assertFalse(self.read_request()['user_completed'])

    def test_changed_missing_and_imprecise_evidence_are_not_current(self):
        self.fixture()
        original = self.review
        for change in ({'excerpt': 'Different saved evidence.'}, {'source_url': ''}, {'kind': 'sample'}):
            with self.subTest(change=change):
                with self.store.connect() as db:
                    self.store.put(db, 'reviews', dict(original, **change))
                self.assertFalse(self.read_request()['evidence_current'])
        with self.store.connect() as db:
            self.store.put(db, 'reviews', original)
        self.assertTrue(self.read_request()['evidence_current'])

    def test_request_never_creates_upgrades_or_changes_database_permissions(self):
        result = self.cli('--request', 'missing')
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.data.exists())
        self.fixture()
        self.store.path.chmod(0o644)
        self.read_request()
        for version in (0, 1, len(server.MIGRATIONS) + 1):
            with self.store.connect() as db:
                db.execute('PRAGMA user_version = ' + str(version))
            before = self.files()
            result = self.cli('--request', self.request['id'])
            self.assertEqual(result.returncode, 1)
            self.assertIn('matching app version', result.stderr)
            self.assertEqual(before, self.files())

    def test_request_refuses_malformed_request_source_and_attestations_without_writing(self):
        self.fixture()
        attestation = self.attest()
        original = self.store.path.read_bytes()
        broken = [
            ('action_requests', self.request['id'], {'started_at': 'not a timestamp'}),
            ('action_requests', self.request['id'], {'id': 'wrong-row'}),
            ('action_requests', self.request['id'], {'attempt_id': None}),
            ('action_requests', self.request['id'], {'execution_state': None}),
            ('reviews', self.review['id'], {'excerpt': None}),
            ('reviews', self.review['id'], {'account_id': 'unknown'}),
            ('attestations', attestation['id'], {'done_at': 'not a timestamp'}),
            ('attestations', attestation['id'], {'source_key': 'different-from-index'}),
        ]
        for table, record_id, changes in broken:
            with self.subTest(table=table, changes=changes):
                self.store.path.write_bytes(original)
                with self.store.connect() as db:
                    value = self.store.get(db, table, record_id); value.update(changes)
                    db.execute('UPDATE ' + table + ' SET data=? WHERE id=?', (json.dumps(value), record_id))
                before = self.files()
                result = self.cli('--request', self.request['id'])
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertEqual(result.stdout, '')
                self.assertTrue(result.stderr.startswith('Resonance monitoring:'))
                self.assertNotIn('Traceback', result.stderr)
                self.assertEqual(before, self.files())
        self.store.path.write_bytes(original)
        with self.store.connect() as db:
            db.execute('DROP TABLE attestations')
        before = self.files()
        result = self.cli('--request', self.request['id'])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(before, self.files())

    def test_request_reads_one_consistent_snapshot_during_a_concurrent_update(self):
        self.fixture()
        original = server.Store.readonly_records
        changed = []
        def read(db, table, validator, order='rowid'):
            records = original(db, table, validator, order)
            if table == 'action_requests' and not changed:
                changed.append(True)
                with self.store.connect() as writer:
                    self.store.put(writer, 'reviews', dict(self.review, excerpt='A concurrent evidence change.'))
            return records
        with self.store.connect() as keeper:
            keeper.execute('PRAGMA journal_mode=WAL').fetchall()
            keeper.execute('SELECT count(*) FROM accounts').fetchone()
            with patch.object(server.Store, 'readonly_records', side_effect=read):
                snapshot = server.Store(self.data, read_only=True).request_snapshot(self.request['id'])
            self.assertTrue(snapshot['evidence_current'], 'the read must not mix two committed versions')
            self.assertFalse(server.Store(self.data, read_only=True).request_snapshot(self.request['id'])['evidence_current'])


if __name__ == '__main__':
    unittest.main()
