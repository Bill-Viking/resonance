import copy
from datetime import datetime, timedelta, timezone
import json
import tempfile
import unittest

import server


class BoundaryPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = server.Store(self.temp.name)
        self.account = self.store.mutate('POST', '/api/accounts', {'platform': 'X', 'handle': 'test', 'identity_mode': 'named'})[1]['account']
        self.review = self.store.mutate('POST', '/api/reviews', {'account_id': self.account['id'], 'kind': 'comment', 'title': 'Test excerpt', 'finding': 'A supplied observation', 'severity': 'suggestion', 'excerpt': 'That is bullshit.'})[1]['review']
        self.flag = self.store.state()['policy_flags'][0]

    def tearDown(self):
        self.temp.cleanup()

    def triage(self, status):
        return self.store.mutate('PATCH', '/api/policy-flags/' + self.flag['id'], {'status': status})[1]['flag']

    def test_review_is_durable_and_independent_of_original_observation(self):
        self.triage('resolved')
        state = server.Store(self.temp.name).state()
        self.assertEqual(state['policy_flags'][0]['status'], 'resolved')
        self.assertEqual(state['reviews'][0]['status'], 'resolved')
        self.assertEqual(state['reviews'][0]['excerpt'], 'That is bullshit.')
        self.assertEqual(self.triage('open')['status'], 'open')

    def test_snooze_expires_and_invalid_ids_do_not_create_decisions(self):
        decision = self.triage('snoozed')
        self.assertEqual(decision['status'], 'snoozed')
        with self.store.connect() as db:
            d = json.loads(db.execute('SELECT data FROM policy_decisions').fetchone()[0])
            d['snoozed_until'] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            db.execute('UPDATE policy_decisions SET data=?', (json.dumps(d),))
        self.assertEqual(self.store.state()['policy_flags'][0]['status'], 'open')
        with self.assertRaises(LookupError):
            self.store.mutate('PATCH', '/api/policy-flags/' + 'a' * 32, {'status': 'resolved'})

    def test_changing_boundary_or_excerpt_requires_fresh_review(self):
        self.triage('resolved')
        self.store.mutate('PATCH', '/api/accounts/' + self.account['id'], {'daring': 'bold'})
        flag = self.store.state()['policy_flags'][0]
        self.assertNotEqual(flag['id'], self.flag['id'])
        self.assertEqual(flag['status'], 'open')
        with self.assertRaises(LookupError):
            self.triage('resolved')
        self.store.mutate('PATCH', '/api/accounts/' + self.account['id'], {'identity_mode': 'alias', 'sfw_only': False})
        self.assertEqual(self.store.state()['policy_flags'], [])

    def test_backup_restores_decisions_and_validates_before_changes(self):
        self.triage('resolved')
        backup = self.store.backup()
        self.assertNotIn('policy_flags', backup)
        with tempfile.TemporaryDirectory() as location:
            other = server.Store(location)
            other.mutate('POST', '/api/import', backup)
            self.assertEqual(other.state()['policy_flags'][0]['status'], 'resolved')
            bad = copy.deepcopy(backup)
            bad['accounts'][0]['handle'] = 'Should not save'
            bad['policy_decisions'][0]['status'] = 'deleted'
            with self.assertRaises(server.Invalid):
                other.mutate('POST', '/api/import', bad)
            self.assertEqual(other.backup(), backup)

    def test_daring_changes_optional_flags_but_never_named_sfw(self):
        alias = self.store.mutate('POST', '/api/accounts', {'platform': 'Reddit', 'handle': 'alias', 'identity_mode': 'alias', 'daring': 'bold', 'sfw_only': False})[1]['account']
        text = 'You are an idiot. This is bullshit!!!'
        named = self.store.mutate('POST', '/api/draft-check', {'text': text, 'account_id': self.account['id']})[1]
        bold = self.store.mutate('POST', '/api/draft-check', {'text': text, 'account_id': alias['id']})[1]
        self.assertGreater(len(named['signals']), 0)
        self.assertEqual(bold['signals'], [])
        self.assertIn('not an SFW certification', bold['summary'])

    def test_reassigning_evidence_to_another_account_requires_fresh_review(self):
        self.triage('resolved')
        other = self.store.mutate('POST', '/api/accounts', {'platform': 'Reddit', 'handle': 'another', 'identity_mode': 'named'})[1]['account']
        backup = self.store.backup()
        backup['reviews'][0]['account_id'] = other['id']
        with self.assertRaisesRegex(server.Invalid, 'immutable'):
            self.store.mutate('POST', '/api/import', backup)
        flag = self.store.state()['policy_flags'][0]
        self.assertEqual(flag['id'], self.flag['id'])
        self.assertEqual(flag['status'], 'resolved')

    def test_excerpts_can_be_checked_without_user_written_feedback(self):
        before = len(self.store.state()['reviews'])
        status, result = self.store.mutate('POST', '/api/excerpts', {'account_id': self.account['id'], 'kind': 'reply', 'text': 'You are an idiot.', 'source_url': 'https://example.com/reply'})
        self.assertEqual(status, 201)
        self.assertEqual(result['flag_count'], 1)
        self.assertEqual(result['review']['excerpt'], 'You are an idiot.')
        self.assertEqual(len(self.store.state()['reviews']), before + 1)
        with self.assertRaises(server.Invalid):
            self.store.mutate('POST', '/api/excerpts', {'account_id': self.account['id'], 'kind': 'post', 'text': ' '})


if __name__ == '__main__':
    unittest.main()
