import copy
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
from identity_helpers import observation, start_checked


class ActionRequestAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = server.make_server(self.temp.name, port=0)
        self.worker = threading.Thread(target=self.app.serve_forever, daemon=True)
        self.worker.start()
        self.port = self.app.server_address[1]
        self.origin = f'http://127.0.0.1:{self.port}'
        self.account = self.create_account()
        self.review = self.create_review()

    def tearDown(self):
        self.app.shutdown()
        self.app.server_close()
        self.worker.join()
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        data = json.dumps(body).encode() if body is not None else None
        request_headers = {'Content-Type': 'application/json', 'Origin': self.origin,
                           'X-Resonance-Workspace': self.app.store.context()['token']}
        if headers:
            request_headers.update(headers)
        connection.request(method, path, data, request_headers)
        response = connection.getresponse()
        content = response.read()
        status = response.status
        response_headers = dict(response.getheaders())
        connection.close()
        return status, json.loads(content) if 'application/json' in response_headers.get('Content-Type', '') else content.decode()

    def start_action(self, request_id):
        state = self.request('GET', '/api/state')[1]
        request = next(value for value in state['action_requests'] if value['id'] == request_id)
        account = next(value for value in state['accounts'] if value['id'] == request['account_id'])
        path = '/api/action-requests/' + request_id
        status, result = self.request('POST', path + '/identity-check', observation(account, request))
        self.assertEqual(status, 200, result)
        return self.request('POST', path + '/start', {'identity_check_id': result['action_request']['identity_check']['id']})

    def create_account(self, **changes):
        body = dict(platform='X', handle='synthetic', identity_mode='named')
        body.update(changes)
        status, result = self.request('POST', '/api/accounts', body)
        self.assertEqual(status, 201, result)
        return result['account']

    def create_review(self, **changes):
        body = dict(account_id=self.account['id'], kind='post', title='Synthetic review',
                    excerpt='This is bullshit.', source_url='https://example.test/items/1',
                    severity='attention', finding='Synthetic finding')
        body.update(changes)
        status, result = self.request('POST', '/api/reviews', body)
        self.assertEqual(status, 201, result)
        return result['review']

    def create_action(self, action, review=None, **changes):
        body = {'review_id': (review or self.review)['id'], 'action': action}
        body.update(changes)
        return self.request('POST', '/api/action-requests', body)

    def test_http_identity_mismatch_cannot_start_or_consume_an_attempt(self):
        action = self.create_action('delete')[1]['action_request']
        path = '/api/action-requests/' + action['id']
        status, result = self.request('POST', path + '/identity-check', observation(
            self.account, action, handle='other_example', profile_url='https://x.com/other_example'))
        self.assertEqual(status, 200, result)
        checked = result['action_request']
        self.assertFalse(checked['can_execute'])
        self.assertEqual(checked['identity_preflight']['status'], 'mismatch')
        self.assertEqual(self.request('POST', path + '/start', {'identity_check_id': checked['identity_check']['id']})[0], 400)
        saved = self.request('GET', '/api/state')[1]['action_requests'][0]
        self.assertIsNone(saved['attempt_id'])
        self.assertEqual(saved['execution_state'], 'queued')

    def test_keep_is_durable_and_resolves_review_and_current_flags(self):
        self.assertEqual(self.request('GET', '/api/state')[1]['policy_flags'][0]['status'], 'open')
        status, result = self.create_action('keep')
        self.assertEqual(status, 201, result)
        action = result['action_request']
        self.assertTrue(result['created'])
        self.assertFalse(action['confirmation_required'])
        self.assertEqual(action['status'], 'completed')
        self.assertEqual(action['account_id'], self.account['id'])
        self.assertEqual(action['original_excerpt'], self.review['excerpt'])
        self.assertEqual(action['source_url'], self.review['source_url'])
        self.assertEqual(action['proposed_text'], '')
        self.assertEqual((action['receipt_url'], action['receipt_note']), ('', ''))
        state = server.Store(self.temp.name).state()
        self.assertEqual(state['action_requests'][0], action)
        self.assertEqual(state['reviews'][0]['status'], 'resolved')
        self.assertTrue(state['policy_flags'])
        self.assertTrue(all(flag['status'] == 'resolved' for flag in state['policy_flags'] if flag['review_id'] == self.review['id']))
        replay_status, replay = self.create_action('keep')
        self.assertEqual(replay_status, 200)
        self.assertFalse(replay['created'])
        self.assertEqual(replay['action_request']['id'], action['id'])
        self.assertEqual(self.request('PATCH', '/api/action-requests/' + action['id'], {'status': 'cancelled'})[0], 400)

    def test_edit_validation_exact_replay_and_duplicate_guards(self):
        for body in ({}, {'proposed_text': ' '}, {'proposed_text': self.review['excerpt']}, {'proposed_text': 'x' * 20001}):
            self.assertEqual(self.create_action('edit', **body)[0], 400)
        status, result = self.create_action('edit', proposed_text='A calmer replacement.')
        self.assertEqual(status, 201, result)
        action = result['action_request']
        self.assertEqual(action['status'], 'pending')
        state = self.request('GET', '/api/state')[1]
        self.assertEqual(state['reviews'][0]['status'], 'open')
        self.assertTrue(all(flag['status'] == 'open' for flag in state['policy_flags']))
        replay_status, replay = self.create_action('edit', proposed_text='A calmer replacement.')
        self.assertEqual(replay_status, 200)
        self.assertEqual(replay['action_request']['id'], action['id'])
        self.assertFalse(replay['created'])
        self.assertEqual(self.create_action('delete')[0], 400)
        same_item = self.create_review(title='Same item, second observation')
        status, error = self.create_action('delete', same_item)
        self.assertEqual(status, 400)
        self.assertIn('source already has action work', error['error'])
        other_account = self.create_account(handle='other')
        other_review = self.create_review(account_id=other_account['id'], title='Other account, same URL')
        self.assertEqual(self.create_action('delete', other_review)[0], 201)

    def test_missing_source_and_caller_snapshots_are_rejected(self):
        no_source = self.create_review(title='No source', source_url='')
        self.assertEqual(self.create_action('edit', no_source, proposed_text='Changed.')[0], 400)
        self.assertEqual(self.create_action('delete', no_source)[0], 400)
        self.assertEqual(self.create_action('keep', no_source)[0], 201)
        supplied = {'review_id': self.review['id'], 'action': 'delete',
                    'account_id': self.account['id'], 'source_url': 'https://evil.test/item'}
        self.assertEqual(self.request('POST', '/api/action-requests', supplied)[0], 400)
        self.assertEqual(self.request('POST', '/api/action-requests', {'review_id': 'missing', 'action': 'keep'})[0], 404)
        self.assertEqual(self.request('POST', '/api/action-requests', {'review_id': self.review['id'], 'action': 'delete'}, {'Origin': 'https://evil.test'})[0], 403)

    def test_cancel_is_terminal_and_allows_a_different_request(self):
        action = self.create_action('delete')[1]['action_request']
        status, result = self.request('PATCH', '/api/action-requests/' + action['id'], {'status': 'cancelled'})
        self.assertEqual(status, 200, result)
        self.assertEqual(result['action_request']['status'], 'cancelled')
        self.assertIsNone(result['action_request']['completed_at'])
        self.assertEqual(self.request('PATCH', '/api/action-requests/' + action['id'], {
            'status': 'completed', 'receipt_url': 'https://example.test/receipt', 'receipt_note': 'Verified'})[0], 400)
        status, replacement = self.create_action('delete')
        self.assertEqual(status, 201, replacement)
        self.assertNotEqual(replacement['action_request']['id'], action['id'])

    def test_remote_completion_requires_a_real_receipt_and_resolves(self):
        action = self.create_action('edit', proposed_text='Replacement text.')[1]['action_request']
        path = '/api/action-requests/' + action['id']
        self.assertEqual(self.start_action(action['id'])[0], 200)
        for body in ({'status': 'completed'},
                     {'status': 'completed', 'receipt_url': '', 'receipt_note': 'Verified'},
                     {'status': 'completed', 'receipt_url': 'javascript:bad', 'receipt_note': 'Verified'},
                     {'status': 'completed', 'receipt_url': 'https://example.test/receipt', 'receipt_note': ' '}):
            self.assertEqual(self.request('PATCH', path, body)[0], 400)
            self.assertEqual(self.request('GET', '/api/state')[1]['action_requests'][0]['status'], 'pending')
        status, result = self.request('PATCH', path, {
            'status': 'completed', 'receipt_url': 'https://example.test/receipt/1',
            'receipt_note': 'Verified against the visible replacement.'})
        self.assertEqual(status, 200, result)
        completed = result['action_request']
        self.assertEqual(completed['status'], 'completed')
        self.assertIsNotNone(completed['completed_at'])
        state = self.request('GET', '/api/state')[1]
        self.assertEqual(state['reviews'][0]['status'], 'resolved')
        self.assertTrue(all(flag['status'] == 'resolved' for flag in state['policy_flags']))
        replay_status, replay = self.create_action('edit', proposed_text='Replacement text.')
        self.assertEqual(replay_status, 201)
        self.assertTrue(replay['created'])
        self.assertNotEqual(replay['action_request']['id'], action['id'])
        self.assertEqual(self.request('PATCH', path, {'status': 'cancelled'})[0], 400)

    def test_keep_cancels_pending_request_for_the_same_social_item(self):
        related = self.create_review(title='Related observation')
        pending = self.create_action('delete', related)[1]['action_request']
        kept = self.create_action('keep')[1]['action_request']
        state = self.request('GET', '/api/state')[1]
        by_id = {request['id']: request for request in state['action_requests']}
        self.assertEqual(by_id[pending['id']]['status'], 'cancelled')
        self.assertEqual(by_id[kept['id']]['status'], 'completed')
        own_review = next(review for review in state['reviews'] if review['id'] == self.review['id'])
        related_review = next(review for review in state['reviews'] if review['id'] == related['id'])
        self.assertEqual(own_review['status'], 'resolved')
        self.assertEqual(related_review['status'], 'resolved')

    def test_changed_evidence_blocks_completion_without_mutation(self):
        action = self.create_action('delete')[1]['action_request']
        self.assertEqual(self.start_action(action['id'])[0], 200)
        with server.Store(self.temp.name).connect() as db:
            changed = server.Store.get(db, 'reviews', self.review['id'])
            changed['source_url'] = 'https://example.test/items/changed'
            server.Store.put(db, 'reviews', changed)
        status, error = self.request('PATCH', '/api/action-requests/' + action['id'], {
            'status': 'completed', 'receipt_url': 'https://example.test/receipt/1',
            'receipt_note': 'Synthetic verification.'})
        self.assertEqual(status, 400)
        self.assertIn('changed', error['error'])
        self.assertEqual(self.request('GET', '/api/state')[1]['action_requests'][0]['status'], 'pending')

    def test_backup_roundtrip_legacy_preservation_and_atomic_rejection(self):
        action = self.create_action('delete')[1]['action_request']
        backup = self.request('GET', '/api/export')[1]
        canonical_action = dict(action)
        for field in ('confirmation_required', 'scheduled_for', 'can_cancel', 'can_execute', 'can_prepare', 'identity_preflight'):
            canonical_action.pop(field)
        self.assertEqual(backup['action_requests'][0], canonical_action)
        with tempfile.TemporaryDirectory() as location:
            other = server.Store(location)
            other.mutate('POST', '/api/import', backup)
            self.assertEqual(other.backup()['action_requests'], backup['action_requests'])
            self.assertTrue(other.state()['action_requests'][0]['confirmation_required'])
            receipt = {'status': 'completed', 'receipt_url': 'https://example.test/receipt/imported',
                       'receipt_note': 'Synthetic verification after import.'}
            with self.assertRaisesRegex(server.Invalid, 'Choose Keep, Edit, or Delete again'):
                other.mutate('PATCH', '/api/action-requests/' + action['id'], receipt)
            replay_status, replay = other.mutate('POST', '/api/action-requests', {
                'review_id': self.review['id'], 'action': 'delete'})
            self.assertEqual(replay_status, 200)
            self.assertFalse(replay['created'])
            self.assertFalse(replay['action_request']['confirmation_required'])
            # Reconfirmation updates updated_at. Reimport the current snapshot rather
            # than assuming both operations happened in the same wall-clock second.
            confirmed_backup = other.backup()
            other.mutate('POST', '/api/import', confirmed_backup)
            self.assertEqual(other.backup(), confirmed_backup)
            self.assertFalse(other.state()['action_requests'][0]['confirmation_required'])
            start_checked(other, action['id'])
            self.assertEqual(other.mutate('PATCH', '/api/action-requests/' + action['id'], receipt)[0], 200)
        legacy = copy.deepcopy(backup)
        legacy.pop('action_requests')
        self.assertEqual(self.request('POST', '/api/import', legacy)[0], 200)
        self.assertEqual(self.request('GET', '/api/export')[1]['action_requests'][0], canonical_action)
        baseline = self.request('GET', '/api/export')[1]
        bad_backups = []
        changed_review = copy.deepcopy(legacy)
        changed_review['reviews'][0]['source_url'] = 'https://example.test/items/rebound'
        bad_backups.append(changed_review)
        missing_receipt = copy.deepcopy(baseline)
        missing_receipt['action_requests'][0].update(status='completed', completed_at=server.now())
        bad_backups.append(missing_receipt)
        pending_keep = copy.deepcopy(baseline)
        pending_keep['action_requests'][0].update(action='keep', status='pending', completed_at=None)
        bad_backups.append(pending_keep)
        bad_chronology = copy.deepcopy(baseline)
        bad_chronology['action_requests'][0].update(
            status='completed', completed_at='2026-09-15T12:00:01Z',
            updated_at='2026-09-15T12:00:00Z', receipt_url='https://example.test/receipt',
            receipt_note='Synthetic verification.')
        bad_backups.append(bad_chronology)
        unknown_review = copy.deepcopy(baseline)
        unknown_review['action_requests'][0]['review_id'] = 'unknown'
        bad_backups.append(unknown_review)
        for bad in bad_backups:
            self.assertEqual(self.request('POST', '/api/import', bad)[0], 400)
            self.assertEqual(self.request('GET', '/api/export')[1], baseline)

    def test_daily_schedule_fields_are_validated_and_legacy_safe(self):
        config = self.request('GET', '/api/state')[1]['monitoring']['config']
        self.assertIsNone(config['daily_time'])
        self.assertEqual(config['timezone'], '')
        status, result = self.request('PATCH', '/api/monitoring', {
            'daily_time': '06:00', 'timezone': 'America/Los_Angeles'})
        self.assertEqual(status, 200, result)
        self.assertEqual(result['config']['daily_time'], '06:00')
        self.assertEqual(result['config']['timezone'], 'America/Los_Angeles')
        self.assertEqual(server.Store(self.temp.name).state()['monitoring']['config']['daily_time'], '06:00')
        for bad in ({'daily_time': '24:00'}, {'daily_time': '6:00'}, {'timezone': '../Bad'}, {'timezone': 3}):
            self.assertEqual(self.request('PATCH', '/api/monitoring', bad)[0], 400)
        legacy = self.request('GET', '/api/export')[1]
        legacy['monitoring']['config'].pop('daily_time')
        legacy['monitoring']['config'].pop('timezone')
        with tempfile.TemporaryDirectory() as location:
            restored = server.Store(location)
            restored.mutate('POST', '/api/import', legacy)
            restored_config = restored.state()['monitoring']['config']
            self.assertIsNone(restored_config['daily_time'])
            self.assertEqual(restored_config['timezone'], '')


if __name__ == '__main__':
    unittest.main()
