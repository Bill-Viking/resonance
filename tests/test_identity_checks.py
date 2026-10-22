"""Identity guards use synthetic accounts and temporary databases only."""
import copy
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import monitor
import server
import workflow
from identity_helpers import observation


class IdentityCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = server.Store(Path(self.temp.name) / 'data')
        self.account = self.store.mutate('POST', '/api/accounts', dict(
            platform='X', handle='@river_example', url='https://x.com/river_example',
            identity_mode='unclassified'))[1]['account']
        self.review = self.store.mutate('POST', '/api/reviews', dict(
            account_id=self.account['id'], kind='post', title='Synthetic source',
            excerpt='A synthetic observation.', source_url='https://x.com/river_example/status/1001',
            severity='attention', finding='A concrete concern.'))[1]['review']
        self.request = self.queue()
        self.path = '/api/action-requests/' + self.request['id']

    def queue(self, action='delete', **changes):
        body = dict(review_id=self.review['id'], action=action)
        if action == 'edit':
            body['proposed_text'] = 'A synthetic replacement.'
        body.update(changes)
        return self.store.mutate('POST', '/api/action-requests', body)[1]['action_request']

    def checked(self, **changes):
        return self.store.mutate('POST', self.path + '/identity-check',
                                 observation(self.account, self.request, **changes))[1]['action_request']

    def start(self, checked):
        return self.store.mutate('POST', self.path + '/start',
                                 {'identity_check_id': checked['identity_check']['id']})[1]['action_request']

    def current(self):
        return self.store.request_snapshot(self.request['id'])

    def test_missing_check_cannot_start_but_is_available_for_preparation(self):
        self.assertFalse(self.request['can_execute'])
        self.assertTrue(self.request['can_prepare'])
        self.assertEqual(len(self.store.work()['queued']), 1)
        for body in ({}, {'identity_check_id': 'invented'}):
            with self.subTest(body=body), self.assertRaises(server.Invalid):
                self.store.mutate('POST', self.path + '/start', body)
        self.assertIsNone(self.current()['action_request']['attempt_id'])

    def test_match_records_expected_observed_boundaries_and_one_attempt(self):
        checked = self.checked()
        self.assertTrue(checked['can_execute'])
        self.assertEqual(checked['identity_preflight']['expected']['identity_mode'], 'unclassified')
        self.assertTrue(checked['identity_preflight']['expected']['sfw_only'])
        self.assertEqual(checked['identity_preflight']['observation']['handle'], '@river_example')
        started = self.start(checked)
        self.assertEqual(started['execution_state'], 'running')
        self.assertTrue(self.current()['identity_current'])
        with self.assertRaises(server.Invalid):
            self.start(checked)
        with self.assertRaises(server.Invalid):
            self.checked()
        self.assertIsNotNone(started['attempt_id'])

    def test_wrong_account_even_with_the_same_display_name_cannot_start(self):
        checked = self.checked(handle='@other_example', profile_url='https://x.com/other_example')
        self.assertEqual(checked['identity_preflight']['status'], 'mismatch')
        self.assertFalse(checked['can_execute'])
        with self.assertRaises(server.Invalid):
            self.start(checked)
        current = self.current()['action_request']
        self.assertEqual(current['execution_state'], 'queued')
        self.assertIsNone(current['attempt_id'])
        self.assertEqual(current['identity_check']['handle'], '@other_example')

    def test_wrong_source_author_cannot_start_an_edit_or_delete(self):
        for action in ('delete', 'edit'):
            with self.subTest(action=action):
                if action == 'edit':
                    self.store.mutate('PATCH', self.path, {'status': 'cancelled'})
                    self.request = self.queue('edit'); self.path = '/api/action-requests/' + self.request['id']
                checked = self.checked(author_handle='@other_example', author_profile_url='https://x.com/other_example')
                self.assertIn('source author', checked['identity_preflight']['reason'])
                with self.assertRaises(server.Invalid):
                    self.start(checked)

    def test_signed_out_or_unavailable_does_not_substitute_an_alternate(self):
        for status in ('signed_out', 'unavailable'):
            with self.subTest(status=status):
                checked = self.checked(status=status, handle='', profile_url='', author_handle='', author_profile_url='')
                self.assertEqual(checked['identity_preflight']['status'], status)
                self.assertFalse(checked['can_execute'])
                with self.assertRaises(server.Invalid):self.start(checked)
        self.assertEqual(self.store.state()['accounts'][0], self.account)

    def test_profile_link_and_handle_must_both_match_and_use_the_provider(self):
        for profile in ('https://x.com/other_example', 'https://x.com.evil.example/river_example',
                        'https://example.test/river_example', 'https://x.com/river_example/status/1001',
                        'https://x.com/river_example?ref=other', ''):
            with self.subTest(profile=profile):
                checked = self.checked(profile_url=profile)
                self.assertFalse(checked['can_execute'])
                with self.assertRaises(server.Invalid):self.start(checked)

    def test_missing_profile_is_unknown_without_claiming_an_account_mismatch(self):
        checked = self.checked(profile_url='')
        self.assertEqual(checked['identity_preflight']['status'], 'unavailable')
        self.assertIn('missing or cannot be compared', checked['identity_preflight']['reason'])
        self.assertNotIn('does not match', checked['identity_preflight']['reason'])
        self.assertFalse(checked['can_execute'])
        with self.assertRaises(server.Invalid):self.start(checked)
        self.assertEqual(self.store.state()['accounts'][0], self.account)

    def test_target_fields_cannot_be_rebound_and_failure_is_atomic(self):
        for changes in ({'account_id': 'other'}, {'source_key': 'other'},
                        {'evidence_fingerprint': 'other'}, {'source_url': 'https://x.com/river_example/status/1002'},
                        {'checked_at': '2999-01-01T00:00:00Z'}):
            with self.subTest(changes=changes):
                before = self.store.backup()
                with self.assertRaises(server.Invalid):self.checked(**changes)
                self.assertEqual(self.store.backup(), before)

    def test_expired_observation_and_post_start_last_look_fail_closed(self):
        checked = self.checked()
        later = server.instant(checked['identity_check']['checked_at']) + timedelta(seconds=301)
        with patch('server.now', return_value=later.isoformat()):
            self.assertEqual(self.current()['action_request']['identity_preflight']['status'], 'expired')
            self.assertFalse(self.current()['identity_current'])
            with self.assertRaises(server.Invalid):self.start(checked)
        self.start(checked)
        with patch('server.now', return_value=later.isoformat()):
            self.assertFalse(self.current()['identity_current'])
            self.assertEqual(self.current()['action_request']['execution_state'], 'running')

    def test_newer_failed_check_revokes_the_previous_matching_check(self):
        first = self.checked()
        self.checked(handle='@other_example', profile_url='https://x.com/other_example')
        with self.assertRaises(server.Invalid):self.start(first)
        replacement = self.checked()
        with self.assertRaises(server.Invalid):self.start(first)
        self.start(replacement)

    def test_account_or_boundary_change_invalidates_a_check_without_merging(self):
        checked = self.checked()
        self.store.mutate('PATCH', '/api/accounts/' + self.account['id'], {'daring': 'bold'})
        current = self.current()
        self.assertFalse(current['identity_current'])
        self.assertFalse(current['action_request']['can_execute'])
        self.assertEqual(current['action_request']['identity_preflight']['status'], 'changed')
        self.assertTrue(current['action_request']['identity_preflight']['expected']['sfw_only'])
        with self.assertRaises(server.Invalid):self.start(checked)

    def test_evidence_change_and_user_keep_still_stop_a_matched_request(self):
        checked = self.checked()
        self.store.mutate('POST', '/api/reviews', dict(account_id=self.account['id'], kind='post',
            title='New concern', excerpt='Changed words.', source_url=self.request['source_url'],
            severity='attention', finding='Different evidence.'))
        with self.assertRaises(server.Invalid):self.start(checked)
        self.assertFalse(self.current()['evidence_current'])

    def test_keep_after_check_withdraws_without_a_provider_attempt(self):
        checked = self.checked()
        self.queue('keep')
        with self.assertRaises(server.Invalid):self.start(checked)
        self.assertEqual(self.current()['action_request']['status'], 'cancelled')

    def test_restore_preserves_observation_history_but_no_execution_authority(self):
        checked = self.checked()
        backup = self.store.backup()
        self.assertEqual(backup['action_requests'][0]['identity_check'], checked['identity_check'])
        self.assertNotIn('identity_preflight', backup['action_requests'][0])
        other = server.Store(Path(self.temp.name) / 'restored')
        other.mutate('POST', '/api/import', backup)
        current = other.request_snapshot(self.request['id'])
        self.assertFalse(current['identity_current'])
        self.assertEqual(current['action_request']['identity_preflight']['status'], 'historical')
        other.mutate('POST', '/api/action-requests', {'review_id': self.review['id'], 'action': 'delete'})
        with self.assertRaises(server.Invalid):
            other.mutate('POST', self.path + '/start', {'identity_check_id': checked['identity_check']['id']})
        fresh = other.mutate('POST', self.path + '/identity-check', observation(self.account, self.request))[1]['action_request']
        other.mutate('POST', self.path + '/start', {'identity_check_id': fresh['identity_check']['id']})

    def test_import_rejects_identity_history_from_a_different_account_or_source(self):
        self.checked()
        backup = self.store.backup()
        for field in ('account_id', 'source_key', 'evidence_fingerprint', 'expected_id'):
            with self.subTest(field=field):
                altered = copy.deepcopy(backup)
                check = altered['action_requests'][0]['identity_check']
                if field == 'expected_id':check['expected']['id'] = 'other'
                else:check[field] = 'other'
                other = server.Store(Path(self.temp.name) / ('invalid-' + field))
                before = other.backup()
                with self.assertRaises(server.Invalid):other.mutate('POST', '/api/import', altered)
                self.assertEqual(other.backup(), before)

    def test_same_store_import_also_revokes_local_check_without_erasing_history(self):
        checked = self.checked()
        self.store.mutate('POST', '/api/import', self.store.backup())
        self.assertEqual(self.current()['action_request']['identity_check']['id'], checked['identity_check']['id'])
        self.assertFalse(self.current()['identity_current'])
        with self.assertRaises(server.Invalid):self.start(checked)

    def test_preflight_cannot_override_a_conflicting_source_author_in_its_url(self):
        self.store.mutate('PATCH', self.path, {'status': 'cancelled'})
        review = self.store.mutate('POST', '/api/reviews', dict(account_id=self.account['id'], kind='post',
            title='Wrong author link', excerpt='Synthetic.', source_url='https://x.com/other_example/status/1001',
            severity='attention', finding='Concrete concern.'))[1]['review']
        self.review = review;self.request = self.queue();self.path = '/api/action-requests/' + self.request['id']
        checked = self.checked()
        self.assertFalse(checked['can_execute'])
        with self.assertRaises(server.Invalid):self.start(checked)

    def test_older_observation_cannot_replace_a_newer_mismatch(self):
        first = self.checked()
        newer = server.instant(first['identity_check']['checked_at']) + timedelta(seconds=10)
        with patch('server.now', return_value=newer.isoformat()):
            mismatch = self.checked(handle='other_example', profile_url='https://x.com/other_example')
            with self.assertRaises(server.Invalid):
                self.checked(checked_at=first['identity_check']['checked_at'])
            with self.assertRaises(server.Invalid):self.start(mismatch)

    def test_version_two_upgrade_preserves_old_tables_and_adds_no_authority(self):
        import sqlite3
        directory = Path(self.temp.name) / 'version-two'
        old = server.Store(directory)
        account = old.mutate('POST', '/api/accounts', {'platform': 'X', 'handle': 'example'})[1]['account']
        with old.connect() as db:
            db.execute('DROP TABLE action_identity_checks')
            db.execute('PRAGMA user_version=2')
            before = list(db.execute('SELECT * FROM accounts'))
        upgraded = server.Store(directory)
        with upgraded.connect() as db:
            self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM accounts')], [tuple(row) for row in before])
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 3)
            self.assertEqual(db.execute('SELECT count(*) FROM action_identity_checks').fetchone()[0], 0)
        snapshots = sorted((directory / 'backups/auto').glob('*-before-v3.sqlite3'))
        with sqlite3.connect(snapshots[-1]) as saved:
            self.assertEqual(saved.execute('PRAGMA user_version').fetchone()[0], 2)
            self.assertIsNone(saved.execute("SELECT name FROM sqlite_master WHERE name='action_identity_checks'").fetchone())

    def test_profile_namespaces_and_extra_handle_markers_never_alias(self):
        expected = dict(self.account, platform='LinkedIn', handle='river_example', url='https://www.linkedin.com/in/river_example')
        self.assertTrue(workflow.observed_identity_matches(expected, 'river_example', 'https://linkedin.com/in/river_example'))
        self.assertFalse(workflow.observed_identity_matches(expected, 'river_example', 'https://linkedin.com/company/river_example'))
        self.assertFalse(workflow.observed_identity_matches(dict(expected, url=''), 'river_example', 'https://linkedin.com/company/river_example'))
        self.assertFalse(workflow.observed_identity_matches(self.account, '@@river_example', self.account['url']))
        self.assertIsNone(workflow.profile_key('YouTube', 'https://youtube.com/@@river_example'))

    def test_profile_comparison_is_conservative_for_stable_provider_ids(self):
        expected = dict(self.account, platform='YouTube', handle='river_example', url='https://www.youtube.com/channel/UCExample')
        self.assertTrue(workflow.observed_identity_matches(expected, '@river_example', 'https://youtube.com/channel/UCExample'))
        self.assertFalse(workflow.observed_identity_matches(expected, '@river_example', 'https://youtube.com/channel/ucexample'))
        self.assertFalse(workflow.observed_identity_matches(expected, '@river_example', 'https://youtube.com/@river_example'))


if __name__ == '__main__':
    unittest.main()
