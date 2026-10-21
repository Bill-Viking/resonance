import copy
import tempfile
import unittest

import server
import workflow


class SourceLinkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = server.Store(self.temp.name)
        self.account = self.store.mutate('POST', '/api/accounts', {
            'platform': 'X', 'handle': 'synthetic', 'url': 'https://x.com/synthetic'})[1]['account']

    def tearDown(self):
        self.temp.cleanup()

    def review(self, **changes):
        body = dict(account_id=self.account['id'], kind='post', title='A saved item',
                    excerpt='A synthetic observation.', source_url='', severity='attention',
                    finding='A synthetic concern.')
        body.update(changes)
        return self.store.mutate('POST', '/api/reviews', body)[1]['review']

    def item(self, review_id):
        return next(value for value in self.store.state()['source_items'] if review_id in value['review_ids'])

    def link(self, item, source_url='https://x.com/synthetic/status/123'):
        return self.store.mutate('POST', '/api/source-items/' + item['id'] + '/link', {
            'source_url': source_url, 'evidence_fingerprint': item['evidence_fingerprint']})[1]['source_item']

    def test_exact_link_preserves_evidence_and_new_requests_use_corrected_target(self):
        review = self.review()
        before = self.item(review['id'])
        corrected = self.link(before)
        self.assertTrue(corrected['precise_source'])
        self.assertNotEqual(corrected['id'], before['id'])
        self.assertNotEqual(corrected['evidence_fingerprint'], before['evidence_fingerprint'])
        saved = self.store.state()['reviews'][0]
        self.assertEqual(saved['source_url'], '')
        self.assertEqual(saved['excerpt'], review['excerpt'])
        self.assertEqual(saved['source_url_correction'], corrected['source_url'])
        self.assertTrue(saved['source_url_corrected_at'])
        request = self.store.mutate('POST', '/api/action-requests', {
            'review_id': review['id'], 'action': 'delete'})[1]['action_request']
        self.assertEqual(request['source_url'], corrected['source_url'])
        self.assertEqual(request['source_key'], corrected['source_key'])
        self.assertTrue(self.store.request_snapshot(request['id'])['evidence_current'])
        with tempfile.TemporaryDirectory() as directory:
            restored = server.Store(directory)
            restored.mutate('POST', '/api/import', self.store.backup())
            self.assertEqual(restored.state()['source_items'][0]['source_url'], corrected['source_url'])
            self.assertFalse(restored.state()['action_requests'][0]['can_execute'])

    def test_changed_account_cannot_reuse_a_conflicting_corrected_author(self):
        review = self.review()
        self.link(self.item(review['id']))
        self.store.mutate('PATCH', '/api/accounts/' + self.account['id'], {
            'handle': 'other', 'url': 'https://x.com/other'})
        current = self.item(review['id'])
        self.assertFalse(current['precise_source'])
        self.assertIn('different account', current['identity_note'])
        with self.assertRaises(server.Invalid):
            self.store.mutate('POST', '/api/action-requests', {'review_id': review['id'], 'action': 'delete'})

    def test_new_attestation_uses_corrected_target_and_backup_roundtrips(self):
        review = self.review()
        corrected = self.link(self.item(review['id']))
        attestation = self.store.mutate('POST', '/api/attestations', {
            'review_id': review['id'], 'action': 'delete', 'note': 'Synthetic self-completion.'})[1]['attestation']
        self.assertEqual(attestation['source_url'], corrected['source_url'])
        with tempfile.TemporaryDirectory() as directory:
            restored = server.Store(directory)
            restored.mutate('POST', '/api/import', self.store.backup())
            self.assertEqual(restored.state()['attestations'][0], attestation)

    def test_report_finding_rekeys_with_its_materialized_review_without_rewriting_report(self):
        stamp = server.now()
        report = dict(id='synthetic-report', started_at=stamp, completed_at=stamp,
                      summary='Synthetic report.', accounts=[dict(
                          account_id=self.account['id'], status='checked', checked_at=stamp,
                          summary='Synthetic evidence.', coverage='One synthetic item.',
                          items_reviewed=1, deleted_count=0, pending_deletion_count=0,
                          findings=[dict(kind='post', severity='attention', title='A saved item',
                                         detail='A synthetic concern.', source_url='https://x.com/synthetic',
                                         text='A synthetic observation.', quotes=['synthetic'])])])
        self.store.mutate('POST', '/api/monitor-runs', report)
        saved_report = copy.deepcopy(self.store.backup()['monitoring']['runs'][0])
        review = self.store.state()['reviews'][0]
        corrected = self.link(self.item(review['id']))
        state = self.store.state()
        self.assertEqual(len(state['source_items']), 1)
        self.assertEqual(state['source_items'][0]['id'], corrected['id'])
        self.assertEqual(state['source_items'][0]['findings'][0]['quotes'], ['synthetic'])
        self.assertIn('monitoring report', state['source_items'][0]['findings'][0]['provenance'])
        self.assertEqual(self.store.backup()['monitoring']['runs'][0], saved_report)
        self.assertEqual(state['reviews'][0]['source_url'], 'https://x.com/synthetic')
        with tempfile.TemporaryDirectory() as directory:
            restored = server.Store(directory)
            restored.mutate('POST', '/api/import', self.store.backup())
            self.assertEqual(len(restored.state()['source_items']), 1)
            self.assertEqual(restored.state()['source_items'][0]['id'], corrected['id'])

    def test_rejects_stale_aggregate_profile_wrong_platform_and_wrong_author(self):
        review = self.review()
        item = self.item(review['id'])
        stale = dict(item, evidence_fingerprint='0' * 32)
        with self.assertRaises(server.Invalid):
            self.link(stale)
        for source_url in ('', 'https://x.com/synthetic', 'https://www.youtube.com/watch?v=synthetic',
                           'https://x.com/other/status/123', 'https://x.com.evil.test/synthetic/status/123'):
            with self.subTest(source_url=source_url), self.assertRaises(server.Invalid):
                self.link(item, source_url)
        for changes in ({'kind': 'sample'}, {'sample_count': 2}):
            aggregate = self.review(**changes)
            with self.subTest(changes=changes), self.assertRaises(server.Invalid):
                self.link(self.item(aggregate['id']))
        self.assertNotIn('source_url_correction', next(value for value in self.store.state()['reviews'] if value['id'] == review['id']))

    def test_rejects_collision_and_already_precise_source(self):
        exact = self.review(source_url='https://x.com/synthetic/status/123')
        missing = self.review()
        before = self.store.backup()
        with self.assertRaises(server.Invalid):
            self.link(self.item(missing['id']))
        with self.assertRaises(server.Invalid):
            self.link(self.item(exact['id']), 'https://x.com/synthetic/status/456')
        self.assertEqual(self.store.backup(), before)

    def test_account_bound_collision_does_not_merge_other_account(self):
        other = self.store.mutate('POST', '/api/accounts', {'platform': 'X', 'handle': 'synthetic'})[1]['account']
        self.review(account_id=other['id'], source_url='https://x.com/synthetic/status/123')
        missing = self.review()
        corrected = self.link(self.item(missing['id']))
        self.assertEqual(corrected['account_id'], self.account['id'])
        self.assertEqual(len(self.store.state()['source_items']), 2)

    def test_unresolved_request_or_attestation_refuses_rekey_and_rolls_back(self):
        # Such old imprecise targets can arrive from legacy backups. Save the
        # synthetic lifecycle directly to exercise every guard without a provider.
        review = self.review(source_url='https://x.com/synthetic')
        item = self.item(review['id'])
        stamp = server.now()
        for state in ('queued', 'running', 'needs_verification', 'blocked'):
            with self.subTest(state=state):
                request = server.action_request_record(dict(
                    id='legacy-request', review_id=review['id'], account_id=self.account['id'],
                    action='delete', status='pending', original_excerpt=review['excerpt'],
                    source_url=review['source_url'], proposed_text='', created_at=stamp, updated_at=stamp,
                    completed_at=None, receipt_url='', receipt_note='', source_key=item['source_key'],
                    evidence_fingerprint=item['evidence_fingerprint'], execution_state=state,
                    started_at=stamp if state in ('running', 'needs_verification') else None,
                    attempt_id='synthetic-attempt' if state in ('running', 'needs_verification') else None,
                    blocker_reason='Synthetic reason.' if state == 'blocked' else '', next_move='none'))
                with self.store.connect() as db:
                    self.store.put_action_request(db, request)
                before = self.store.backup()
                with self.assertRaises(server.Invalid):
                    self.link(item)
                self.assertEqual(self.store.backup(), before)
        with self.store.connect() as db:
            db.execute('DELETE FROM action_requests')
        for state in ('needs_verification', 'blocked'):
            with self.subTest(attestation=state):
                attestation = server.attestation_record(dict(
                    id='legacy-attestation', review_id=review['id'], account_id=self.account['id'],
                    action='delete', note='Synthetic self-completion.', proposed_text='', result_url='',
                    original_excerpt=review['excerpt'], source_url=review['source_url'], created_at=stamp, done_at=stamp,
                    execution_state=state, source_key=item['source_key'], evidence_fingerprint=item['evidence_fingerprint'],
                    blocker_reason='Synthetic reason.' if state == 'blocked' else '', next_move='none',
                    verified_at=None, receipt_url='', receipt_note=''))
                with self.store.connect() as db:
                    self.store.put_attestation(db, attestation)
                before = self.store.backup()
                with self.assertRaises(server.Invalid):
                    self.link(item)
                self.assertEqual(self.store.backup(), before)

    def test_old_backup_preserves_correction_conflicting_correction_refused(self):
        review = self.review()
        old_backup = self.store.backup()
        corrected = self.link(self.item(review['id']))
        self.store.mutate('POST', '/api/import', old_backup)
        saved = self.store.state()['reviews'][0]
        self.assertEqual(saved['source_url_correction'], corrected['source_url'])
        self.assertNotIn('source_url_correction', old_backup['reviews'][0])
        conflict = self.store.backup()
        conflict['reviews'][0]['source_url_correction'] = 'https://x.com/synthetic/status/456'
        before = self.store.backup()
        with self.assertRaises(server.Invalid):
            self.store.mutate('POST', '/api/import', conflict)
        self.assertEqual(self.store.backup(), before)
        # An imported patch cannot introduce a correction on an existing review.
        with tempfile.TemporaryDirectory() as directory:
            uncorrected = server.Store(directory)
            uncorrected.mutate('POST', '/api/import', old_backup)
            with self.assertRaises(server.Invalid):
                uncorrected.mutate('POST', '/api/import', before)

    def test_historical_keep_stays_historical_and_roundtrips(self):
        review = self.review()
        old_item = self.item(review['id'])
        kept = self.store.mutate('POST', '/api/action-requests', {
            'review_id': review['id'], 'action': 'keep'})[1]['action_request']
        corrected = self.link(self.item(review['id']))
        self.assertEqual(corrected['status'], 'needs_you')
        self.assertNotEqual(corrected['source_key'], kept['source_key'])
        request = self.store.state()['action_requests'][0]
        self.assertEqual(request['source_url'], '')
        self.assertEqual(request['source_key'], old_item['source_key'])
        with tempfile.TemporaryDirectory() as directory:
            restored = server.Store(directory)
            restored.mutate('POST', '/api/import', self.store.backup())
            self.assertEqual(restored.state()['source_items'][0]['status'], 'needs_you')
            self.assertEqual(restored.state()['action_requests'][0]['source_key'], old_item['source_key'])

    def test_historical_targets_keep_matching_url_and_key_even_in_legacy_backups(self):
        review = self.review()
        original = self.item(review['id'])
        self.store.mutate('POST', '/api/action-requests', {'review_id': review['id'], 'action': 'keep'})
        corrected = self.link(self.item(review['id']))
        backup = self.store.backup()
        mixed = copy.deepcopy(backup)
        mixed['action_requests'][0]['source_key'] = corrected['source_key']
        with tempfile.TemporaryDirectory() as directory:
            restored = server.Store(directory)
            with self.assertRaises(server.Invalid):
                restored.mutate('POST', '/api/import', mixed)
        legacy = copy.deepcopy(backup)
        del legacy['action_requests'][0]['source_key']
        with tempfile.TemporaryDirectory() as directory:
            restored = server.Store(directory)
            restored.mutate('POST', '/api/import', legacy)
            self.assertEqual(restored.state()['action_requests'][0]['source_key'], original['source_key'])
            self.assertEqual(restored.state()['source_items'][0]['status'], 'needs_you')

    def test_correction_pair_and_provider_validation_on_import(self):
        review = self.review()
        self.link(self.item(review['id']))
        backup = self.store.backup()
        for change in ('missing_time', 'profile_link', 'wrong_author', 'changed_original'):
            malformed = copy.deepcopy(backup)
            record = malformed['reviews'][0]
            if change == 'missing_time':
                del record['source_url_corrected_at']
            elif change == 'profile_link':
                record['source_url_correction'] = 'https://x.com/synthetic'
            elif change == 'wrong_author':
                record['source_url_correction'] = 'https://x.com/other/status/123'
            else:
                record['source_url'] = 'https://x.com/synthetic/status/456'
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                restored = server.Store(directory)
                with self.assertRaises(server.Invalid):
                    restored.mutate('POST', '/api/import', malformed)
                self.assertEqual(restored.state()['reviews'], [])

    def test_explicit_author_hints_are_checked_without_claiming_handle_free_identity(self):
        cases = (
            ({'platform': 'TikTok', 'handle': '@synthetic'}, 'https://www.tiktok.com/@other/video/123', False),
            ({'platform': 'Facebook', 'handle': 'synthetic'}, 'https://www.facebook.com/other/posts/123', False),
            ({'platform': 'Facebook', 'handle': 'synthetic'}, 'https://www.facebook.com/groups/group/posts/123', True),
            ({'platform': 'LinkedIn', 'handle': 'synthetic'}, 'https://www.linkedin.com/posts/other_activity-123', False),
            ({'platform': 'YouTube', 'handle': 'synthetic'}, 'https://www.youtube.com/watch?v=video', True),
            ({'platform': 'X', 'handle': 'synthetic', 'url': 'https://x.com/other'}, 'https://x.com/synthetic/status/123', False),
        )
        for account, source_url, expected in cases:
            with self.subTest(source_url=source_url):
                self.assertEqual(workflow.source_author_matches(account, source_url), expected)


if __name__ == '__main__':
    unittest.main()
