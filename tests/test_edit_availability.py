"""Exact-post edit availability; synthetic accounts and temporary stores only."""
import copy
from contextlib import closing
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import server
from identity_helpers import observation


class EditAvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'data'
        self.store = server.Store(self.directory)
        self.account = self.store.mutate('POST', '/api/accounts', dict(
            platform='X', handle='@river_example', url='https://x.com/river_example',
            identity_mode='named'))[1]['account']
        self.source = 'https://x.com/river_example/status/1001'
        self.review = self.store.mutate('POST', '/api/reviews', dict(
            account_id=self.account['id'], kind='post', title='Synthetic source',
            excerpt='An original statement.', source_url=self.source,
            severity='attention', finding='A concrete concern.'))[1]['review']
        self.request = self.store.mutate('POST', '/api/action-requests', dict(
            review_id=self.review['id'], action='edit', proposed_text='A considered replacement.'))[1]['action_request']
        self.path = '/api/action-requests/' + self.request['id']

    def checked(self, status='available', **changes):
        value = observation(self.account, self.request)
        value['edit_availability'].update(status=status, **changes)
        return self.store.mutate('POST', self.path + '/identity-check', value)[1]['action_request']

    def start(self, checked):
        return self.store.mutate('POST', self.path + '/start',
                                {'identity_check_id': checked['identity_check']['id']})[1]['action_request']

    def current(self):
        return self.store.request_snapshot(self.request['id'])

    def report(self, identifier, at, status, reason='Synthetic controls inspected.'):
        # The first report materializes its own review; later availability-only
        # changes deliberately preserve the same authored evidence and meaning.
        return self.store.mutate('POST', '/api/monitor-runs', dict(
            id=identifier, started_at=at, completed_at=at, summary='Synthetic inspection.',
            accounts=[dict(account_id=self.account['id'], status='checked', checked_at=at,
                summary='One synthetic source.', coverage='Only this source.', items_reviewed=1,
                deleted_count=0, pending_deletion_count=0, findings=[dict(
                    severity='attention', title='Synthetic source', detail='A concrete concern.',
                    source_url=self.source, kind='post', text='An original statement.',
                    quotes=['original'], edit_availability=dict(status=status, checked_at=at,
                        source_url=self.source, reason=reason, available_until=None))])], requests=[]))

    def test_identity_match_without_edit_controls_cannot_start_legacy_edit(self):
        value = observation(self.account, self.request)
        value.pop('edit_availability')
        checked = self.store.mutate('POST', self.path + '/identity-check', value)[1]['action_request']
        self.assertEqual(checked['identity_preflight']['status'], 'matched')
        self.assertEqual(checked['edit_preflight']['status'], 'unknown')
        self.assertTrue(checked['can_prepare'])
        self.assertFalse(checked['can_execute'])
        with self.assertRaises(server.Invalid): self.start(checked)
        self.assertIsNone(self.current()['action_request']['attempt_id'])

    def test_unavailable_and_unknown_preserve_request_without_attempt(self):
        for status in ('unavailable', 'unknown'):
            checked = self.checked(status, reason='No supported text-edit controls were available.')
            self.assertEqual(checked['edit_preflight']['status'], status)
            self.assertFalse(checked['can_execute'])
            with self.assertRaises(server.Invalid): self.start(checked)
            self.assertIsNone(self.current()['action_request']['attempt_id'])
            if status == 'unavailable':
                self.assertFalse(checked['can_prepare'])
                self.assertEqual(self.store.work()['held'][0]['id'], self.request['id'])
        self.assertEqual(self.current()['action_request']['proposed_text'], 'A considered replacement.')

    def test_available_check_starts_once_and_last_look_expires(self):
        checked = self.checked()
        self.assertTrue(checked['can_execute'])
        started = self.start(checked)
        self.assertEqual(started['execution_state'], 'running')
        self.assertTrue(self.current()['edit_current'])
        with self.assertRaises(server.Invalid): self.start(checked)
        later = server.instant(checked['identity_check']['checked_at']) + timedelta(seconds=301)
        with patch('server.now', return_value=later.isoformat()):
            self.assertFalse(self.current()['edit_current'])
        with self.assertRaises(server.Invalid): self.checked()

    def test_observed_deadline_can_close_before_identity_expires(self):
        checked_at = server.now()
        deadline = (server.instant(checked_at) + timedelta(seconds=2)).isoformat()
        with patch('server.now', return_value=checked_at):
            checked = self.checked(available_until=deadline)
        with patch('server.now', return_value=(server.instant(checked_at) + timedelta(seconds=3)).isoformat()):
            current = self.current()
            self.assertTrue(current['identity_current'])
            self.assertFalse(current['edit_current'])
            self.assertEqual(current['action_request']['edit_preflight']['status'], 'expired')
            with self.assertRaises(server.Invalid): self.start(checked)
        self.assertIsNone(self.current()['action_request']['attempt_id'])

    def test_new_observation_revokes_previous_availability_id(self):
        available = self.checked()
        unavailable = self.checked('unavailable', reason='Edit is absent from the current post menu.')
        with self.assertRaises(server.Invalid): self.start(available)
        with self.assertRaises(server.Invalid): self.start(unavailable)
        fresh = self.checked()
        self.assertNotEqual(fresh['identity_check']['id'], available['identity_check']['id'])
        self.assertEqual(self.start(fresh)['execution_state'], 'running')

    def test_malformed_wrong_source_or_time_observations_are_atomic(self):
        for changes in ({'source_url':'https://x.com/river_example/status/1002'},
                        {'checked_at':'2020-01-01T00:00:00Z'}, {'status':'yes'},
                        {'reason':''}, {'available_until':'2020-01-01T00:00:00Z'}):
            value = observation(self.account, self.request)
            value['edit_availability'].update(changes)
            before = self.store.backup()
            with self.assertRaises(server.Invalid):
                self.store.mutate('POST', self.path + '/identity-check', value)
            self.assertEqual(self.store.backup(), before)
        bad = observation(self.account, self.request)
        bad['edit_availability']['unexpected'] = True
        with self.assertRaises(server.Invalid):
            self.store.mutate('POST', self.path + '/identity-check', bad)

    def test_report_history_shows_reason_but_cannot_authorize_start(self):
        self.report('available-report', server.now(), 'available')
        # New content evidence needs a new user choice; a report is never a start guard.
        self.store.mutate('PATCH', self.path, {'status':'cancelled'})
        review = next(r for r in self.store.state()['reviews'] if r['origin'] == 'import')
        request = self.store.mutate('POST', '/api/action-requests', dict(
            review_id=review['id'], action='edit', proposed_text='A separate considered replacement.'))[1]['action_request']
        self.assertFalse(request['can_execute'])
        self.assertIsNone(request['identity_check'])
        item = next(i for i in self.store.state()['source_items'] if i['source_url'] == self.source)
        self.assertEqual(item['edit_availability']['status'], 'available')

    def test_unavailable_report_blocks_new_edit_but_keep_and_delete_remain_choices(self):
        self.store.mutate('PATCH', self.path, {'status':'cancelled'})
        self.report('unavailable-report', server.now(), 'unavailable', 'The provider says this post can no longer be edited.')
        with self.assertRaises(server.Invalid):
            self.store.mutate('POST', '/api/action-requests', dict(
                review_id=self.review['id'], action='edit', proposed_text='Another proposal.'))
        kept = self.store.mutate('POST', '/api/action-requests', dict(
            review_id=self.review['id'], action='keep'))[1]['action_request']
        self.assertEqual(kept['status'], 'completed')
        deleted = self.store.mutate('POST', '/api/action-requests', dict(
            review_id=self.review['id'], action='delete'))[1]['action_request']
        self.assertEqual(deleted['action'], 'delete')
        self.assertEqual(deleted['edit_preflight']['status'], 'not_required')

    def test_new_unavailability_does_not_change_meaning_but_stops_running_last_look(self):
        first = server.now()
        self.report('first-report', first, 'available')
        self.store.mutate('PATCH', self.path, {'status':'cancelled'})
        review = next(r for r in self.store.state()['reviews'] if r['origin'] == 'import')
        self.request = self.store.mutate('POST', '/api/action-requests', dict(
            review_id=review['id'], action='edit', proposed_text='A considered replacement.'))[1]['action_request']
        self.path = '/api/action-requests/' + self.request['id']
        checked = self.checked()
        self.start(checked)
        fingerprint = self.request['evidence_fingerprint']
        later = (server.instant(first) + timedelta(seconds=2)).isoformat()
        with patch('server.now', return_value=later):
            self.report('later-report', later, 'unavailable', 'The edit window has ended.')
            before = self.store.backup()
            current = self.current()
            self.assertTrue(current['evidence_current'])
            self.assertFalse(current['identity_current'])
            self.assertFalse(current['edit_current'])
            self.assertEqual(current['action_request']['execution_state'], 'running')
            self.assertEqual(current['action_request']['evidence_fingerprint'], fingerprint)
            self.assertEqual(self.store.backup(), before)

    def test_availability_changes_preserve_keep_and_attention_only_for_active_edit(self):
        first = server.now()
        self.report('keep-first', first, 'available')
        self.store.mutate('PATCH', self.path, {'status':'cancelled'})
        kept = self.store.mutate('POST', '/api/action-requests', dict(
            review_id=self.review['id'], action='keep'))[1]['action_request']
        fingerprint = kept['evidence_fingerprint']
        later = (server.instant(first) + timedelta(seconds=2)).isoformat()
        with patch('server.now', return_value=later):
            self.report('keep-later', later, 'unavailable', 'The provider no longer offers text editing.')
            item = next(i for i in self.store.state()['source_items'] if i['source_url'] == self.source)
            self.assertEqual(item['evidence_fingerprint'], fingerprint)
            self.assertEqual(item['status'], 'kept')
            self.assertFalse(item['needs_attention'])

    def test_unavailable_queued_edit_is_visible_under_needs_you_with_reason(self):
        self.checked('unavailable', reason='This exact post has no text-edit control.')
        item = next(i for i in self.store.state()['source_items'] if i['source_url'] == self.source)
        self.assertEqual(item['status'], 'needs_you')
        self.assertEqual(item['status_label'], 'Editing unavailable')
        self.assertTrue(item['needs_attention'])
        work = self.store.work()['held'][0]
        self.assertIn('no text-edit control', work['blocker_reason'])
        self.assertEqual(work['next_move'], 'open_source')

    def test_later_available_report_cannot_revive_a_revoked_start_check(self):
        first = server.now()
        self.report('initial-controls', first, 'available')
        self.store.mutate('PATCH', self.path, {'status':'cancelled'})
        review = next(r for r in self.store.state()['reviews'] if r['origin'] == 'import')
        self.request = self.store.mutate('POST', '/api/action-requests', dict(
            review_id=review['id'], action='edit', proposed_text='A considered replacement.'))[1]['action_request']
        self.path = '/api/action-requests/' + self.request['id']
        checked = self.checked()
        for offset,status in ((1,'unavailable'),(2,'available')):
            at = (server.instant(first) + timedelta(seconds=offset)).isoformat()
            with patch('server.now', return_value=at):
                self.report('controls-' + str(offset), at, status)
                self.assertFalse(self.current()['edit_current'])
                self.assertFalse(self.current()['action_request']['can_execute'])
                with self.assertRaises(server.Invalid):self.start(checked)
        with patch('server.now', return_value=(server.instant(first) + timedelta(seconds=3)).isoformat()):
            self.assertEqual(self.start(self.checked())['execution_state'], 'running')

    def test_report_rejects_other_source_account_finding_or_out_of_run_time(self):
        report = self.report('valid-report', server.now(), 'unknown')[1]['run']
        for change in ('source','kind','time'):
            bad = copy.deepcopy(report);bad['id'] = 'bad-' + change
            finding = bad['accounts'][0]['findings'][0]
            if change == 'source':finding['edit_availability']['source_url'] = 'https://x.com/river_example/status/1002'
            if change == 'kind':finding['kind'] = 'account'
            if change == 'time':finding['edit_availability']['checked_at'] = '2020-01-01T00:00:00Z'
            with self.assertRaises(server.Invalid):server.monitor_run_record(bad, {self.account['id']})

    def test_backup_preserves_history_without_authority_even_after_reconfirmation(self):
        checked = self.checked()
        backup = self.store.backup()
        self.assertNotIn('edit_preflight', backup['action_requests'][0])
        self.store.mutate('POST', '/api/import', backup)
        current = self.current()
        self.assertFalse(current['edit_current'])
        self.assertEqual(current['action_request']['identity_check']['edit_availability'], checked['identity_check']['edit_availability'])
        self.store.mutate('POST', '/api/action-requests', dict(
            review_id=self.review['id'], action='edit', proposed_text=self.request['proposed_text']))
        with self.assertRaises(server.Invalid): self.start(checked)
        self.assertEqual(self.start(self.checked())['execution_state'], 'running')

    def test_schema_three_upgrade_preserves_every_table_and_saves_backup(self):
        with self.store.connect() as db:
            db.execute('PRAGMA user_version=3')
            tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            before = {t:[tuple(row) for row in db.execute('SELECT * FROM "'+t+'"')] for t in tables}
        upgraded = server.Store(self.directory)
        with upgraded.connect() as db:
            after = {t:[tuple(row) for row in db.execute('SELECT * FROM "'+t+'"')] for t in tables}
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 4)
        self.assertEqual(after, before)
        snapshot = sorted((self.directory/'backups/auto').glob('*-before-v4.sqlite3'))[-1]
        with closing(sqlite3.connect(snapshot)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 3)
            self.assertEqual({t:list(db.execute('SELECT * FROM "'+t+'"')) for t in tables}, before)

    def test_cli_observation_and_last_look_follow_edit_gate(self):
        value = observation(self.account, self.request)
        value['edit_availability']['status'] = 'unavailable'
        path = Path(self.temp.name)/'observation.json';path.write_text(json.dumps(value))
        command = [sys.executable,str(server.ROOT/'monitor.py'),'--data-dir',str(self.directory)]
        result = subprocess.run(command+['--check-identity',self.request['id'],'--observation',str(path)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        checked = json.loads(result.stdout)['action_request']
        self.assertEqual(checked['edit_preflight']['status'],'unavailable')
        started = subprocess.run(command+['--start-request',self.request['id'],'--identity-check-id',checked['identity_check']['id']],capture_output=True,text=True)
        self.assertNotEqual(started.returncode,0)
        read = subprocess.run(command+['--request',self.request['id']],capture_output=True,text=True)
        self.assertFalse(json.loads(read.stdout)['edit_current'])
        self.assertIsNone(self.current()['action_request']['attempt_id'])


if __name__ == '__main__':
    unittest.main()
