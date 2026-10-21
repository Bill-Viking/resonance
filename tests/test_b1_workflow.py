import copy
from datetime import datetime, timezone
import tempfile
import unittest

import server
import workflow


class B1WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = server.Store(self.temp.name)
        self.account = self.store.mutate('POST', '/api/accounts', {
            'platform': 'YouTube', 'handle': 'synthetic', 'display_name': 'Synthetic account',
            'url': 'https://www.youtube.com/@synthetic'})[1]['account']

    def tearDown(self):
        self.temp.cleanup()

    def review(self, **changes):
        body = dict(account_id=self.account['id'], kind='comment', title='Review this comment',
                    excerpt='A supplied partial excerpt.',
                    source_url='https://www.youtube.com/watch?v=video&lc=comment',
                    severity='attention', finding='A concrete concern.')
        body.update(changes)
        return self.store.mutate('POST', '/api/reviews', body)[1]['review']

    def source(self, review_id):
        return next(item for item in self.store.state()['source_items'] if review_id in item['review_ids'])

    def outcome(self, target, target_type, result, **changes):
        value = dict(id=target['id'], target_type=target_type, account_id=target['account_id'],
                     source_key=target['source_key'], evidence_fingerprint=target['evidence_fingerprint'],
                     outcome=result, reason='Synthetic outcome.',
                     receipt_url=target['source_url'] if result == 'verified' else '',
                     receipt_note='Observed the exact source.' if result == 'verified' else '',
                     next_move='none')
        value.update(changes)
        return value

    def report(self, run_id, outcomes, findings=None):
        stamp = server.now()
        return dict(id=run_id, started_at=stamp, completed_at=stamp, summary='Synthetic isolated report.',
                    accounts=[dict(account_id=self.account['id'], status='checked', checked_at=stamp,
                                   summary='Checked synthetic evidence.', coverage='No provider accessed.',
                                   items_reviewed=0, deleted_count=0, pending_deletion_count=0,
                                   findings=findings or [])], requests=outcomes)

    def test_identity_grouping_preserves_comment_and_account_boundaries(self):
        first = self.review(source_url='HTTPS://WWW.YOUTUBE.COM/watch?v=video&lc=one&utm_source=test')
        sibling = self.review(source_url='https://www.youtube.com/watch?v=video&lc=one&gclid=tracking')
        other_comment = self.review(source_url='https://www.youtube.com/watch?v=video&lc=two')
        parent_only = self.review(source_url='https://www.youtube.com/watch?v=video')
        sample = self.review(kind='sample', sample_count=3,
                             source_url='https://www.youtube.com/watch?v=video&lc=one')
        evil = self.review(source_url='https://evil.test/watch?v=video&lc=one')
        items = self.store.state()['source_items']
        grouped = next(value for value in items if first['id'] in value['review_ids'])
        self.assertEqual(set(grouped['review_ids']), {first['id'], sibling['id']})
        self.assertTrue(grouped['precise_source'])
        for review in (other_comment, parent_only, sample, evil):
            item = next(value for value in items if review['id'] in value['review_ids'])
            self.assertNotEqual(item['id'], grouped['id'])
        self.assertFalse(next(value for value in items if parent_only['id'] in value['review_ids'])['precise_source'])
        self.assertFalse(next(value for value in items if sample['id'] in value['review_ids'])['precise_source'])
        other_account = self.store.mutate('POST', '/api/accounts', {
            'platform': 'YouTube', 'handle': 'other'})[1]['account']
        other = self.review(account_id=other_account['id'],
                            source_url='https://www.youtube.com/watch?v=video&lc=one')
        self.assertNotEqual(self.source(other['id'])['id'], grouped['id'])
        x_account = self.store.mutate('POST', '/api/accounts', {'platform': 'X', 'handle': 'x-user'})[1]['account']
        x_post = self.review(account_id=x_account['id'], kind='post', source_url='https://x.com/x-user/status/123')
        x_comment = self.review(account_id=x_account['id'], kind='comment', source_url='https://x.com/x-user/status/123')
        self.assertTrue(self.source(x_post['id'])['precise_source'])
        x_reply = self.review(account_id=x_account['id'], kind='reply', source_url='https://x.com/x-user/status/123')
        self.assertTrue(self.source(x_reply['id'])['precise_source'])
        self.assertEqual(self.source(x_reply['id'])['id'], self.source(x_post['id'])['id'])
        self.assertFalse(self.source(x_comment['id'])['precise_source'])
        self.assertNotEqual(self.source(x_post['id'])['id'], self.source(x_comment['id'])['id'])

    def test_semantic_fingerprint_replay_and_identity_change(self):
        review = self.review()
        original = self.source(review['id'])
        self.store.mutate('POST', '/api/action-requests', {'review_id': review['id'], 'action': 'keep'})
        duplicate = self.review()
        unchanged = self.source(review['id'])
        self.assertEqual(unchanged['evidence_fingerprint'], original['evidence_fingerprint'])
        self.assertEqual(unchanged['status'], 'kept')
        self.assertIn(duplicate['id'], unchanged['review_ids'])
        new_concern = self.review(title='A different concern', finding='New semantic evidence.')
        changed = self.source(review['id'])
        self.assertNotEqual(changed['evidence_fingerprint'], original['evidence_fingerprint'])
        self.assertTrue(changed['source_changed'])
        self.assertEqual(changed['status'], 'needs_you')
        request = self.store.mutate('POST', '/api/action-requests', {
            'review_id': new_concern['id'], 'action': 'delete'})[1]['action_request']
        self.assertTrue(next(value for value in self.store.state()['action_requests'] if value['id'] == request['id'])['can_execute'])
        self.store.mutate('PATCH', '/api/accounts/' + self.account['id'], {'display_name': 'Changed identity'})
        current = next(value for value in self.store.state()['action_requests'] if value['id'] == request['id'])
        self.assertFalse(current['can_execute'])
        self.assertTrue(self.source(review['id'])['source_changed'])

    def test_attestation_is_idempotent_and_does_not_cancel_started_attempt(self):
        review = self.review()
        queued = self.store.mutate('POST', '/api/action-requests', {
            'review_id': review['id'], 'action': 'edit', 'proposed_text': 'Replacement.'})[1]['action_request']
        body = {'review_id': review['id'], 'action': 'edit', 'note': 'I changed this myself.',
                'proposed_text': 'Replacement.'}
        created = self.store.mutate('POST', '/api/attestations', body)[1]
        replay = self.store.mutate('POST', '/api/attestations', body)[1]
        self.assertTrue(created['created'])
        self.assertFalse(replay['created'])
        self.assertEqual(created['attestation']['id'], replay['attestation']['id'])
        self.assertIn(queued['id'], created['cancelled_request_ids'])
        with self.assertRaises(server.Invalid):
            self.store.mutate('POST', '/api/action-requests', {'review_id': review['id'], 'action': 'delete'})

        reopened = self.report('att-reopened', [self.outcome(created['attestation'], 'attestation', 'reopened')])
        self.store.mutate('POST', '/api/monitor-runs', reopened)
        running_request = self.store.mutate('POST', '/api/action-requests', {
            'review_id': review['id'], 'action': 'delete'})[1]['action_request']
        self.store.mutate('POST', '/api/action-requests/' + running_request['id'] + '/start', {})
        result = self.store.mutate('POST', '/api/attestations', {
            'review_id': review['id'], 'action': 'delete', 'note': 'I also removed it myself.'})[1]
        self.assertIn(running_request['id'], result['running_request_ids'])
        saved = next(value for value in self.store.state()['action_requests'] if value['id'] == running_request['id'])
        self.assertEqual(saved['status'], 'pending')
        self.assertFalse(saved['can_cancel'])

    def test_interrupted_and_blocked_attempt_cannot_reauthorize_itself(self):
        review = self.review()
        request = self.store.mutate('POST', '/api/action-requests', {
            'review_id': review['id'], 'action': 'delete'})[1]['action_request']
        with self.assertRaises(server.Invalid):
            self.store.mutate('PATCH', '/api/action-requests/' + request['id'], {
                'status': 'completed', 'receipt_url': request['source_url'], 'receipt_note': 'No durable start.'})
        started = self.store.mutate('POST', '/api/action-requests/' + request['id'] + '/start', {})[1]['action_request']
        self.assertIsNotNone(started['attempt_id'])
        work = self.store.work()
        unknown = work['verification']['requests'][0]
        self.assertEqual(unknown['execution_state'], 'needs_verification')
        with self.assertRaises(server.Invalid):
            self.store.mutate('POST', '/api/action-requests/' + request['id'] + '/start', {})
        blocked = self.report('blocked-attempt', [self.outcome(
            unknown, 'request', 'blocked', reason='Sign-in needed.', next_move='open_source')])
        self.store.mutate('POST', '/api/monitor-runs', blocked)
        saved = next(value for value in self.store.state()['action_requests'] if value['id'] == request['id'])
        self.assertEqual(saved['execution_state'], 'blocked')
        self.assertFalse(saved['can_cancel'])
        with self.assertRaises(server.Invalid):
            self.store.mutate('POST', '/api/action-requests', {'review_id': review['id'], 'action': 'delete'})
        reopened = self.report('reconciled-open', [self.outcome(saved, 'request', 'reopened')])
        self.store.mutate('POST', '/api/monitor-runs', reopened)
        fresh = self.store.mutate('POST', '/api/action-requests', {
            'review_id': review['id'], 'action': 'delete'})[1]['action_request']
        self.assertNotEqual(fresh['id'], request['id'])

    def test_report_is_atomic_replay_safe_and_stale_reconciliation_preserves_new_concern(self):
        review = self.review()
        request = self.store.mutate('POST', '/api/action-requests', {
            'review_id': review['id'], 'action': 'delete'})[1]['action_request']
        self.store.mutate('POST', '/api/action-requests/' + request['id'] + '/start', {})
        target = next(value for value in self.store.state()['action_requests'] if value['id'] == request['id'])
        invalid = self.report('atomic', [self.outcome(target, 'request', 'verified'),
                                         dict(self.outcome(target, 'request', 'verified'), id='missing')])
        before = self.store.backup()
        with self.assertRaises(server.Invalid):
            self.store.mutate('POST', '/api/monitor-runs', invalid)
        self.assertEqual(self.store.backup(), before)

        self.review(title='Newer concern', finding='Evidence changed after the attempt.')
        stale = self.report('stale-default', [self.outcome(target, 'request', 'verified')])
        with self.assertRaises(server.Invalid):
            self.store.mutate('POST', '/api/monitor-runs', stale)
        reconciled_outcome = self.outcome(target, 'request', 'verified', reconcile=True,
                                          reason='Observed the old attempt after evidence changed.')
        reconciled = self.report('stale-reconciled', [reconciled_outcome])
        status, result = self.store.mutate('POST', '/api/monitor-runs', reconciled)
        self.assertEqual(status, 201)
        after = self.store.backup()
        replay_status, replay = self.store.mutate('POST', '/api/monitor-runs', reconciled)
        self.assertEqual(replay_status, 200)
        self.assertFalse(replay['created'])
        self.assertEqual(self.store.backup(), after)
        self.assertEqual(self.source(review['id'])['status'], 'needs_you')

    def test_import_holds_duplicates_and_report_findings_get_review_path(self):
        review = self.review()
        request = self.store.mutate('POST', '/api/action-requests', {
            'review_id': review['id'], 'action': 'delete'})[1]['action_request']
        backup = self.store.backup()
        duplicate = copy.deepcopy(backup['action_requests'][0])
        duplicate['id'] = 'duplicate-request'
        backup['action_requests'].append(duplicate)
        with tempfile.TemporaryDirectory() as location:
            other = server.Store(location)
            other.mutate('POST', '/api/import', backup)
            imported = other.state()['action_requests']
            self.assertEqual(len(imported), 2)
            self.assertFalse(any(value['can_execute'] for value in imported))
            self.assertTrue(all(value['confirmation_required'] for value in imported))
            with other.connect() as db:
                db.execute('UPDATE action_requests SET confirmed=1')
            conflicted = other.state()
            item = next(value for value in conflicted['source_items'] if review['id'] in value['review_ids'])
            self.assertEqual(item['status'], 'needs_you')
            matching = [value for value in conflicted['action_requests'] if value['source_key'] == item['source_key']]
            self.assertTrue(all('Multiple action requests' in value['blocker_reason'] for value in matching))

        finding = dict(severity='attention', title='Report-only concern', detail='Only report evidence is available.',
                       source_url='https://www.youtube.com/watch?v=other', kind='post')
        self.store.mutate('POST', '/api/monitor-runs', self.report('report-only', [], [finding]))
        report_item = next(value for value in self.store.state()['source_items'] if value['title'] == 'Report-only concern')
        self.assertIsNotNone(report_item['review_id'])
        derived = next(value for value in self.store.state()['reviews'] if value['id'] == report_item['review_id'])
        self.assertEqual(derived['excerpt'], '')
        self.assertEqual(derived['origin'], 'import')
        kept = self.store.mutate('POST', '/api/action-requests', {
            'review_id': derived['id'], 'action': 'keep'})[1]
        self.assertTrue(kept['created'])

    def test_import_rejects_cross_linked_request_and_attestation_identity(self):
        first = self.review()
        second = self.review(title='Other source', source_url='https://www.youtube.com/watch?v=other&lc=other')
        request = self.store.mutate('POST', '/api/action-requests', {
            'review_id': first['id'], 'action': 'delete'})[1]['action_request']
        first_item = self.source(first['id'])
        second_item = self.source(second['id'])
        backup = self.store.backup()
        target = next(value for value in backup['action_requests'] if value['id'] == request['id'])
        target['source_key'] = second_item['source_key']
        target['evidence_fingerprint'] = second_item['evidence_fingerprint']
        with tempfile.TemporaryDirectory() as location:
            other = server.Store(location)
            with self.assertRaises(server.Invalid):
                other.mutate('POST', '/api/import', backup)
        attestation = self.store.mutate('POST', '/api/attestations', {
            'review_id': second['id'], 'action': 'delete', 'note': 'Removed the second synthetic item.'})[1]['attestation']
        backup = self.store.backup()
        saved_attestation = next(value for value in backup['attestations'] if value['id'] == attestation['id'])
        saved_attestation['source_key'] = first_item['source_key']
        saved_attestation['evidence_fingerprint'] = first_item['evidence_fingerprint']
        with tempfile.TemporaryDirectory() as location:
            other = server.Store(location)
            with self.assertRaises(server.Invalid):
                other.mutate('POST', '/api/import', backup)
        saved_attestation['source_key'] = second_item['source_key']
        saved_attestation['evidence_fingerprint'] = second_item['evidence_fingerprint']
        saved_attestation['source_url'] = 'https://www.youtube.com/watch?v=forged&lc=forged'
        saved_attestation['original_excerpt'] = 'Forged excerpt.'
        with tempfile.TemporaryDirectory() as location:
            other = server.Store(location)
            with self.assertRaisesRegex(server.Invalid, 'snapshot'):
                other.mutate('POST', '/api/import', backup)

    def test_legacy_authority_is_held_and_cannot_hide_new_evidence(self):
        review = self.review()
        queued = self.store.mutate('POST', '/api/action-requests', {
            'review_id': review['id'], 'action': 'delete'})[1]['action_request']
        with self.store.connect() as db:
            raw = server.Store.get(db, 'action_requests', queued['id'])
            for field in ('source_key', 'evidence_fingerprint', 'execution_state', 'started_at',
                          'attempt_id', 'blocker_reason', 'next_move'):
                raw.pop(field)
            db.execute('UPDATE action_requests SET data=?,confirmed=1 WHERE id=?',
                       (server.canonical_json(raw), queued['id']))
        migrated = next(value for value in self.store.state()['action_requests'] if value['id'] == queued['id'])
        self.assertEqual(migrated['execution_state'], 'blocked')
        self.assertTrue(migrated['confirmation_required'])
        self.assertEqual(migrated['next_move'], 'confirm_request')
        self.assertIn('legacy', migrated['blocker_reason'])
        self.assertEqual(self.source(review['id'])['status'], 'needs_you')

        # A completed legacy Keep stays history; a new concern remains visible.
        with tempfile.TemporaryDirectory() as location:
            other = server.Store(location)
            account = other.mutate('POST', '/api/accounts', {'platform': 'X', 'handle': 'legacy'})[1]['account']
            old = other.mutate('POST', '/api/reviews', dict(
                account_id=account['id'], kind='post', title='Old review', excerpt='Old text',
                source_url='https://example.test/posts/legacy', severity='attention', finding='Old concern'))[1]['review']
            kept = other.mutate('POST', '/api/action-requests', {'review_id': old['id'], 'action': 'keep'})[1]['action_request']
            with other.connect() as db:
                db.execute('DELETE FROM source_decisions')
                raw = server.Store.get(db, 'action_requests', kept['id'])
                for field in ('source_key', 'evidence_fingerprint', 'execution_state', 'started_at',
                              'attempt_id', 'blocker_reason', 'next_move'):
                    raw.pop(field)
                db.execute('UPDATE action_requests SET data=? WHERE id=?', (server.canonical_json(raw), kept['id']))
            other.mutate('POST', '/api/reviews', dict(
                account_id=account['id'], kind='post', title='New concern', excerpt='New text',
                source_url='https://example.test/posts/legacy', severity='attention', finding='New evidence'))
            item = next(value for value in other.state()['source_items'] if old['id'] in value['review_ids'])
            self.assertEqual(item['status'], 'needs_you')
            self.assertTrue(item['needs_attention'])

    def test_running_history_and_historical_flags_remain_visible(self):
        review = self.review(excerpt='This is bullshit.')
        initial = self.source(review['id'])
        flag_title = 'Profanity may cross your SFW boundary'
        self.assertTrue(any(value['title'] == flag_title for value in initial['findings']))
        request = self.store.mutate('POST', '/api/action-requests', {
            'review_id': review['id'], 'action': 'delete'})[1]['action_request']
        self.store.mutate('POST', '/api/action-requests/' + request['id'] + '/start', {})
        newer = self.review(title='New concern after start', finding='Changed evidence after attempt.')
        self.store.mutate('POST', '/api/action-requests', {'review_id': newer['id'], 'action': 'keep'})
        item = self.source(review['id'])
        self.assertEqual(item['status'], 'verification_pending')
        self.assertEqual(item['active_request_id'], request['id'])

        self.store.mutate('PATCH', '/api/accounts/' + self.account['id'], {
            'identity_mode': 'alias', 'sfw_only': False})
        after = self.source(review['id'])
        self.assertTrue(any(value['title'] == flag_title for value in after['findings']))
        self.assertFalse(any(value['id'] in {flag['id'] for flag in self.store.state()['policy_flags']}
                             for value in after['findings'] if value['title'] == flag_title))
        self.assertTrue(self.store.state()['flag_evidence'])

    def test_missing_report_finding_is_one_actionable_item(self):
        finding = dict(severity='attention', title='Missing-source report concern', detail='Report excerpt is partial.',
                       source_url='', kind='comment')
        self.store.mutate('POST', '/api/monitor-runs', self.report('missing-report', [], [finding]))
        matches = [value for value in self.store.state()['source_items'] if value['title'] == finding['title']]
        self.assertEqual(len(matches), 1)
        self.assertIsNotNone(matches[0]['review_id'])
        self.store.mutate('POST', '/api/action-requests', {'review_id': matches[0]['review_id'], 'action': 'keep'})
        matches = [value for value in self.store.state()['source_items'] if value['title'] == finding['title']]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]['status'], 'kept')

    def test_newer_report_finding_keeps_saved_excerpt_as_representative(self):
        review = self.review(excerpt='The original saved partial excerpt.')
        before = self.source(review['id'])
        finding = dict(severity='attention', title='Newer report concern',
                       detail='A newer report finding with no captured original text.',
                       source_url=review['source_url'], kind='comment')
        self.store.mutate('POST', '/api/monitor-runs', self.report('newer-empty-report', [], [finding]))
        after = self.source(review['id'])
        self.assertEqual(after['id'], before['id'])
        self.assertEqual(after['source_key'], before['source_key'])
        self.assertEqual(after['excerpt'], review['excerpt'])
        self.assertEqual(after['review_id'], review['id'])
        self.assertTrue(any(value['title'] == finding['title'] for value in after['findings']))

    def test_schedule_handles_disabled_expiry_dst_gap_and_fold(self):
        base = {'enabled': True, 'daily_time': '02:30', 'timezone': 'America/Los_Angeles', 'ends_at': None}
        gap = workflow.next_scheduled_check(base, datetime(2026, 3, 8, 9, 0, tzinfo=timezone.utc))
        self.assertEqual(gap, '2026-03-08T10:00:00Z')
        after_gap = workflow.next_scheduled_check(base, datetime(2026, 3, 8, 10, 10, tzinfo=timezone.utc))
        self.assertEqual(after_gap, '2026-03-09T09:30:00Z')
        fold_config = dict(base, daily_time='01:30')
        fold = workflow.next_scheduled_check(fold_config, datetime(2026, 11, 1, 7, 0, tzinfo=timezone.utc))
        self.assertEqual(fold, '2026-11-01T08:30:00Z')
        self.assertIsNone(workflow.next_scheduled_check(dict(base, enabled=False)))
        self.assertIsNone(workflow.next_scheduled_check(dict(base, ends_at='2026-03-08T09:30:00Z'),
                                                        datetime(2026, 3, 8, 9, 0, tzinfo=timezone.utc)))


if __name__ == '__main__':
    unittest.main()
