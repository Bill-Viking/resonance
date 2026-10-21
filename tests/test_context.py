import base64
import copy
import tempfile
import unittest

import server
import workflow


PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jm1sAAAAASUVORK5CYII='


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = server.Store(self.temp.name)
        self.account = self.store.mutate('POST', '/api/accounts', {
            'platform': 'X', 'handle': '@example_writer', 'url': 'https://x.com/example_writer'})[1]['account']
        self.context = dict(kind='parent_media', inspection='inspected',
                            source_url='https://social.example/post/parent',
                            summary='An abstract shape in the parent image.', checked_at='2026-10-01T12:00:00Z',
                            image=dict(mime_type='image/png', base64=PNG, alt='A synthetic pixel.'))

    def report(self, name, context=True, at='2026-10-01T12:00:00Z'):
        finding = dict(severity='suggestion', kind='reply', title='Consider the context',
                       detail='The thread gives this short reply another possible meaning.',
                       source_url='https://x.com/example_writer/status/1001', text='Interesting shape.', quotes=['shape'])
        if context:
            finding['context'] = [copy.deepcopy(self.context)]
        return dict(id=name, started_at=at, completed_at=at, summary='Synthetic context review.',
                    accounts=[dict(account_id=self.account['id'], status='checked', checked_at=at,
                                   summary='One synthetic reply.', coverage='Fixture only.', items_reviewed=1,
                                   deleted_count=0, pending_deletion_count=0, findings=[finding])])

    def save(self, report):
        self.store.mutate('POST', '/api/monitor-runs', report)
        return self.store.state()['source_items'][0]

    def test_context_survives_intake_and_backup_without_becoming_authored_text(self):
        item = self.save(self.report('context'))
        self.assertEqual(item['excerpt'], 'Interesting shape.')
        self.assertEqual(item['findings'][0]['quotes'], ['shape'])
        self.assertEqual(item['findings'][0]['context'], [self.context])
        backup = self.store.backup()
        with tempfile.TemporaryDirectory() as folder:
            restored = server.Store(folder)
            restored.mutate('POST', '/api/import', backup)
            self.assertEqual(restored.state()['source_items'][0]['findings'][0]['context'], [self.context])

    def test_legacy_reports_do_not_gain_context_or_change_the_old_fingerprint_shape(self):
        item = self.save(self.report('legacy', context=False))
        self.assertNotIn('context', item['findings'][0])
        self.assertEqual(workflow.semantic_finding(item['findings'][0]), {
            key: item['findings'][0][key] for key in ('title', 'detail', 'severity', 'kind')})

    def test_rechecking_unchanged_context_preserves_keep_but_a_changed_parent_reopens(self):
        first = self.save(self.report('first'))
        self.store.mutate('POST', '/api/action-requests', {'review_id': first['review_id'], 'action': 'keep'})
        self.context['checked_at'] = '2026-10-01T12:01:00Z'
        repeated = self.save(self.report('repeat', at=self.context['checked_at']))
        self.assertEqual(repeated['status'], 'kept')
        self.assertEqual(first['evidence_fingerprint'], repeated['evidence_fingerprint'])
        self.assertEqual(repeated['findings'][0]['context'][0]['checked_at'], self.context['checked_at'])
        self.context['summary'] = 'The parent now contains a different visual context.'
        changed = self.save(self.report('changed', at='2026-10-01T12:02:00Z'))
        self.assertEqual(changed['status'], 'needs_you')
        self.assertTrue(changed['source_changed'])
        self.assertNotEqual(first['evidence_fingerprint'], changed['evidence_fingerprint'])
        self.assertEqual(changed['findings'][0]['context'][0]['summary'], self.context['summary'])

    def test_changed_context_blocks_a_queued_action(self):
        first = self.save(self.report('first'))
        request = self.store.mutate('POST', '/api/action-requests', {
            'review_id': first['review_id'], 'action': 'delete'})[1]['action_request']
        self.context['inspection'] = 'unavailable'
        self.context.pop('image')
        self.context['summary'] = 'The parent can no longer be inspected.'
        self.save(self.report('changed', at='2026-10-01T12:01:00Z'))
        current = next(r for r in self.store.state()['action_requests'] if r['id'] == request['id'])
        self.assertFalse(current['can_execute'])

    def test_uninspected_media_has_no_image_and_does_not_claim_inspection(self):
        self.context.update(inspection='not_inspected', summary='The video was not played.')
        self.context.pop('image')
        item = self.save(self.report('uninspected'))
        self.assertEqual(item['findings'][0]['context'][0]['inspection'], 'not_inspected')

    def test_invalid_context_and_unsafe_images_are_refused(self):
        account_report = self.report('account-context')
        account_report['accounts'][0]['findings'][0]['kind'] = 'account'
        with self.assertRaises(server.Invalid):
            server.monitor_run_record(account_report, {self.account['id']})
        variants = []
        for change in ({'kind': 'profile'}, {'inspection': 'guessed'}, {'summary': ''},
                       {'checked_at': '2026-10-02T00:00:00Z'}, {'source_url': 'file:///secret'},
                       {'inspection': 'not_inspected'}, {'kind': 'parent_text'}, {'extra': True}):
            value = copy.deepcopy(self.context); value.update(change); variants.append([value])
        for change in ({'mime_type': 'image/svg+xml'}, {'mime_type': 'image/jpeg'},
                       {'base64': '<svg onload=alert(1)>'}, {'base64': base64.b64encode(b'<html>').decode()},
                       {'base64': base64.b64encode(b'\x89PNG\r\n\x1a\n' + b'x' * 262144).decode()}, {'alt': ''}):
            value = copy.deepcopy(self.context); value['image'].update(change); variants.append([value])
        variants += [[], [self.context] * 5, 'not a list']
        for context in variants:
            with self.subTest(context=str(context)[:100]):
                report = self.report('invalid'); report['accounts'][0]['findings'][0]['context'] = context
                with self.assertRaises(server.Invalid):
                    server.monitor_run_record(report, {self.account['id']})
