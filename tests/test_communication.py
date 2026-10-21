import copy
import json
import tempfile
import unittest

import server


class CommunicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = server.Store(self.temp.name)
        self.gmail = self.account('Gmail', 'person@example.test')
        self.outlook = self.account('Outlook', 'person@outlook.test')
        self.social = self.account('X', 'synthetic')

    def tearDown(self):
        self.temp.cleanup()

    def account(self, platform, handle):
        return self.store.mutate('POST', '/api/accounts', {
            'platform': platform, 'handle': handle, 'identity_mode': 'named'})[1]['account']

    def access(self, account=None, **changes):
        record = dict(account_id=(account or self.gmail)['id'], status='verified', method='connector',
                      checked_at='2026-09-15T13:00:00Z', detail='Mailbox listing was available.',
                      capabilities=['read_mail'])
        record.update(changes)
        return self.store.mutate('POST', '/api/account-access', record)

    def sync(self, account=None, checked_at='2026-09-15T13:30:00Z', messages=None, **changes):
        account = account or self.gmail
        if messages is None:
            host = 'mail.google.com' if account['platform'] == 'Gmail' else 'outlook.live.com'
            messages = [dict(provider_id='provider-1', thread_id='thread-1', subject='Synthetic subject',
                             sender='Sender <sender@example.test>', received_at='2026-09-15T12:30:00Z',
                             source_url=f'https://{host}/mail/u/0/#inbox/provider-1',
                             preview='A synthetic preview.', is_read=False)]
        body = dict(account_id=account['id'], checked_at=checked_at, scope='Newest inbox page',
                    has_more=True, total_count=42, unread_count=4, messages=messages)
        body.update(changes)
        return self.store.mutate('POST', '/api/mail-sync', body)

    def test_clean_state_brand_and_mailbox_platforms(self):
        communication = self.store.state()['communication']
        self.assertEqual(communication, {
            'brand': {'description': '', 'audience': '', 'voice': '', 'avoid': '', 'updated_at': None},
            'access': [], 'setup_requests': [], 'mail_syncs': [], 'messages': [], 'drafts': []})
        status, result = self.store.mutate('PATCH', '/api/brand', {
            'description': 'Clear and practical', 'audience': 'Engineering peers',
            'voice': 'Warm, concise', 'avoid': 'Hype'})
        self.assertEqual(status, 200)
        self.assertIsNotNone(result['brand']['updated_at'])
        self.assertEqual(server.Store(self.temp.name).state()['communication']['brand']['voice'], 'Warm, concise')
        with self.assertRaises(server.Invalid):
            self.store.mutate('PATCH', '/api/brand', {'voice': 'x' * 4001})
        with self.assertRaises(server.Invalid):
            self.store.mutate('PATCH', '/api/brand', {'api_key': 'secret'})

    def test_discord_substack_profiles_persist_without_implied_access(self):
        for platform, source in (
                ('Discord', 'https://discord.com/channels/123/456/789'),
                ('Substack', 'https://example.substack.com/p/sample')):
            with self.subTest(platform=platform):
                account = self.store.mutate('POST', '/api/accounts', {
                    'platform': platform, 'handle': 'synthetic-user'})[1]['account']
                self.assertEqual(account['identity_mode'], 'unclassified')
                self.assertTrue(account['sfw_only'])
                self.assertIsNone(account['access_checked_at'])
                self.assertFalse(any(item['account_id'] == account['id']
                                     for item in self.store.state()['communication']['access']))
                with self.assertRaises(server.Invalid):
                    self.sync(account)
                review = self.store.mutate('POST', '/api/reviews', {
                    'account_id': account['id'], 'kind': 'post', 'title': 'Synthetic post',
                    'finding': 'Supplied evidence', 'severity': 'suggestion',
                    'excerpt': 'Sample wording', 'source_url': source})[1]['review']
                # Account support alone must not enable an unimplemented provider action.
                with self.assertRaises(server.Invalid):
                    self.store.mutate('POST', '/api/action-requests', {
                        'review_id': review['id'], 'action': 'delete'})
        backup = self.store.backup()
        self.assertEqual(server.Store(self.temp.name).backup(), backup)
        with tempfile.TemporaryDirectory() as location:
            restored = server.Store(location)
            restored.mutate('POST', '/api/import', backup)
            self.assertEqual(restored.backup()['accounts'], backup['accounts'])
            self.assertEqual(restored.state()['communication']['access'], [])

    def test_access_check_lifecycle_and_evidence_requirements(self):
        status, first = self.store.mutate('POST', '/api/access-checks', {'account_id': self.gmail['id']})
        self.assertEqual(status, 201)
        status, replay = self.store.mutate('POST', '/api/access-checks', {'account_id': self.gmail['id']})
        self.assertEqual(status, 200)
        self.assertEqual(replay['setup_request']['id'], first['setup_request']['id'])
        path = '/api/access-checks/' + first['setup_request']['id']
        with self.assertRaises(server.Invalid):
            self.store.mutate('PATCH', path, {'status': 'completed'})
        completed = self.store.mutate('PATCH', path, {'status': 'completed', 'note': 'Connector mailbox list was visible.'})[1]['setup_request']
        self.assertEqual(completed['status'], 'completed')
        with self.assertRaises(server.Invalid):
            self.store.mutate('PATCH', path, {'status': 'cancelled'})
        second = self.store.mutate('POST', '/api/access-checks', {'account_id': self.gmail['id']})[1]['setup_request']
        self.assertNotEqual(second['id'], completed['id'])
        self.assertEqual(self.store.mutate('PATCH', '/api/access-checks/' + second['id'], {'status': 'cancelled'})[1]['setup_request']['status'], 'cancelled')
        for changes in ({'checked_at': None}, {'detail': ''}, {'capabilities': []}, {'capabilities': ['read_mail', 'read_mail']}):
            with self.assertRaises(server.Invalid):
                self.access(**changes)
        status, result = self.access()
        self.assertEqual(status, 200)
        self.assertEqual(result['access']['status'], 'verified')
        self.assertEqual(server.Store(self.temp.name).state()['communication']['access'][0], result['access'])

    def test_account_identity_change_invalidates_access(self):
        self.access()
        unchanged = self.store.mutate('PATCH', '/api/accounts/' + self.gmail['id'], {'daring': 'bold'})
        self.assertEqual(unchanged[0], 200)
        self.assertEqual(self.store.state()['communication']['access'][0]['status'], 'verified')
        self.store.mutate('PATCH', '/api/accounts/' + self.gmail['id'], {'handle': 'renamed@example.test'})
        access = self.store.state()['communication']['access'][0]
        self.assertEqual((access['status'], access['method'], access['checked_at'], access['capabilities']),
                         ('unverified', 'unknown', None, []))
        self.assertIn('identity changed', access['detail'])

    def test_mailbox_handle_cannot_relabel_saved_mail(self):
        self.access()
        self.sync()
        path = '/api/accounts/' + self.gmail['id']
        with self.assertRaisesRegex(server.Invalid, 'Add a separate mailbox'):
            self.store.mutate('PATCH', path, {'handle': 'different@example.test'})
        self.assertEqual(next(account for account in self.store.state()['accounts'] if account['id'] == self.gmail['id'])['handle'],
                         'person@example.test')
        self.assertEqual(self.store.mutate('PATCH', path, {'handle': 'PERSON@EXAMPLE.TEST'})[0], 200)
        before = self.store.backup()
        changed = copy.deepcopy(before)
        next(account for account in changed['accounts'] if account['id'] == self.gmail['id'])['handle'] = 'other@example.test'
        with self.assertRaisesRegex(server.Invalid, 'Add a separate mailbox'):
            self.store.mutate('POST', '/api/import', changed)
        self.assertEqual(self.store.backup(), before)

    def test_mail_sync_is_stable_bounded_and_does_not_delete_missing_messages(self):
        status, result = self.sync()
        self.assertEqual(status, 201)
        self.assertEqual(result['imported_count'], 1)
        self.assertEqual(result['sync']['imported_count'], 1)
        message = self.store.state()['communication']['messages'][0]
        self.assertEqual(message['id'], server.message_id(self.gmail['id'], 'provider-1'))
        self.assertEqual(message['subject'], 'Synthetic subject')
        hostile = [dict(provider_id='provider-1', subject='<img src=x onerror=alert(1)>',
                        sender='<script>sender</script>', received_at='2026-09-15T12:30:00Z',
                        source_url='https://mail.google.com/mail/u/0/#inbox/provider-1',
                        preview='<script>alert(1)</script>', is_read=None)]
        self.sync(messages=hostile)
        updated = self.store.state()['communication']['messages'][0]
        self.assertEqual(updated['id'], message['id'])
        self.assertEqual(updated['preview'], '<script>alert(1)</script>')
        self.assertEqual(updated['imported_at'], message['imported_at'])
        self.sync(checked_at='2026-09-15T14:00:00Z', messages=[], has_more=False,
                  total_count=None, unread_count=None, scope='Inbox checked; no new previews submitted')
        state = self.store.state()['communication']
        self.assertEqual(len(state['messages']), 1)
        self.assertEqual(state['mail_syncs'][0]['imported_count'], 0)
        self.assertFalse(state['mail_syncs'][0]['has_more'])
        with self.assertRaisesRegex(server.Invalid, 'backwards'):
            self.sync(checked_at='2026-09-15T13:59:59Z', messages=[])
        too_many = [{}] * 1001
        with self.assertRaises(server.Invalid):
            self.sync(checked_at='2026-09-15T15:00:00Z', messages=too_many)

    def test_mail_hosts_account_binding_and_sensitive_fields(self):
        self.assertEqual(self.sync(self.outlook)[0], 201)
        wrong_host = [dict(provider_id='bad-host', subject='Subject', sender='Sender',
                           received_at='2026-09-15T12:30:00Z',
                           source_url='https://outlook.live.com/mail/0/inbox/id/bad-host',
                           preview='Preview', is_read=True)]
        with self.assertRaises(server.Invalid):
            self.sync(self.gmail, messages=wrong_host)
        with self.assertRaises(server.Invalid):
            self.sync(self.social)
        sensitive = [dict(provider_id='secret', subject='Subject', sender='Sender',
                          received_at='2026-09-15T12:30:00Z',
                          source_url='https://mail.google.com/mail/u/0/#inbox/secret',
                          preview='Preview', is_read=True, access_token='secret')]
        with self.assertRaises(server.Invalid):
            self.sync(messages=sensitive)

    def test_authentication_mail_is_metadata_only_without_breaking_drafts(self):
        messages = [
            dict(provider_id='auth-code', subject='314159 is your verification code', sender='Identity Service',
                 received_at='2026-09-15T12:30:00Z',
                 source_url='https://mail.google.com/mail/u/0/#inbox/auth-code',
                 preview='Use 314159 to finish signing in.', is_read=False),
            dict(provider_id='magic-link', subject='Your secure sign-in link', sender='Identity Service',
                 received_at='2026-09-15T12:31:00Z',
                 source_url='https://mail.google.com/mail/u/0/#inbox/magic-link',
                 preview='Use this private link to continue.', is_read=False),
            dict(provider_id='secure-link', subject='Your secure link to Example is here', sender='Example',
                 received_at='2026-09-15T12:31:30Z',
                 source_url='https://mail.google.com/mail/u/0/#inbox/secure-link',
                 preview='Sign in to Example to continue. This link expires in 10 minutes.', is_read=False),
            dict(provider_id='shared-file', subject='Your secure link to the project file', sender='A colleague',
                 received_at='2026-09-15T12:31:45Z',
                 source_url='https://mail.google.com/mail/u/0/#inbox/shared-file',
                 preview='Download the shared project file.', is_read=False),
            dict(provider_id='ordinary-security', subject='Your password was changed', sender='Identity Service',
                 received_at='2026-09-15T12:32:00Z',
                 source_url='https://mail.google.com/mail/u/0/#inbox/ordinary-security',
                 preview='This confirms the password change you requested.', is_read=True),
            dict(provider_id='ordinary-mail', subject='Project correspondence', sender='A colleague',
                 received_at='2026-09-15T12:33:00Z',
                 source_url='https://mail.google.com/mail/u/0/#inbox/ordinary-mail',
                 preview='The project review is ready.', is_read=None),
        ]
        self.sync(messages=messages)
        state = self.store.state()['communication']
        by_provider = {message['provider_id']: message for message in state['messages']}
        for provider_id in ('auth-code', 'magic-link', 'secure-link'):
            self.assertEqual(by_provider[provider_id]['subject'], server.AUTH_SUBJECT)
            self.assertEqual(by_provider[provider_id]['preview'], server.AUTH_PREVIEW)
        self.assertEqual(by_provider['ordinary-security']['subject'], 'Your password was changed')
        self.assertEqual(by_provider['ordinary-security']['preview'], 'This confirms the password change you requested.')
        self.assertEqual(by_provider['ordinary-mail']['preview'], 'The project review is ready.')
        self.assertEqual(by_provider['shared-file']['subject'], 'Your secure link to the project file')
        self.assertNotIn('314159', str(state))
        auth = by_provider['auth-code']
        self.assertEqual(auth['id'], server.message_id(self.gmail['id'], 'auth-code'))
        draft = self.store.mutate('POST', '/api/mail-drafts', {
            'message_id': auth['id'], 'text': 'A local response.'})[1]['draft']
        with self.store.connect() as db:
            raw = json.loads(db.execute('SELECT data FROM messages WHERE id=?', (auth['id'],)).fetchone()[0])
            raw.update(subject='314159 is your verification code', preview='Use 314159 to finish signing in.')
            db.execute('UPDATE messages SET data=? WHERE id=?', (json.dumps(raw), auth['id']))
        self.assertNotIn('314159', str(self.store.state()))
        backup = self.store.backup()
        self.assertNotIn('314159', str(backup))
        self.store.mutate('POST', '/api/import', backup)
        with self.store.connect() as db:
            persisted = json.loads(db.execute('SELECT data FROM messages WHERE id=?', (auth['id'],)).fetchone()[0])
        self.assertEqual((persisted['subject'], persisted['preview']), (server.AUTH_SUBJECT, server.AUTH_PREVIEW))
        restored = self.store.state()['communication']
        self.assertEqual(next(item for item in restored['drafts'] if item['id'] == draft['id'])['message_id'], auth['id'])

    def test_local_draft_upsert_survives_message_refresh_and_has_no_send_route(self):
        self.sync()
        message = self.store.state()['communication']['messages'][0]
        status, result = self.store.mutate('POST', '/api/mail-drafts', {
            'message_id': message['id'], 'text': 'First local reply.'})
        self.assertEqual(status, 201)
        draft = result['draft']
        self.sync(messages=[dict(provider_id='provider-1', subject='Updated subject', sender='Sender',
                                 received_at='2026-09-15T12:30:00Z',
                                 source_url='https://mail.google.com/mail/u/0/#inbox/provider-1',
                                 preview='Updated preview', is_read=True)])
        status, saved = self.store.mutate('POST', '/api/mail-drafts', {
            'message_id': message['id'], 'text': 'Updated local reply.'})
        self.assertEqual(status, 200)
        self.assertEqual(saved['draft']['id'], draft['id'])
        self.assertEqual(saved['draft']['created_at'], draft['created_at'])
        self.assertEqual(server.Store(self.temp.name).state()['communication']['drafts'][0]['text'], 'Updated local reply.')
        with self.assertRaises(server.Invalid):
            self.store.mutate('POST', '/api/mail-drafts', {
                'message_id': message['id'], 'account_id': self.outlook['id'], 'text': 'Wrong binding'})
        with self.assertRaises(LookupError):
            self.store.mutate('POST', '/api/mail-send', {'message_id': message['id']})
        activity = self.store.state()['activity']
        self.assertFalse(any('Updated local reply.' in item['description'] for item in activity))

    def test_backup_roundtrip_downgrades_access_and_legacy_preserves_communication(self):
        self.store.mutate('PATCH', '/api/brand', {'voice': 'Plain and warm'})
        self.access()
        setup = self.store.mutate('POST', '/api/access-checks', {'account_id': self.gmail['id']})[1]['setup_request']
        self.sync()
        message = self.store.state()['communication']['messages'][0]
        self.store.mutate('POST', '/api/mail-drafts', {'message_id': message['id'], 'text': 'Saved locally.'})
        backup = self.store.backup()
        self.assertIn('communication', backup)
        with tempfile.TemporaryDirectory() as location:
            restored = server.Store(location)
            restored.mutate('POST', '/api/import', backup)
            communication = restored.state()['communication']
            self.assertEqual(communication['brand']['voice'], 'Plain and warm')
            self.assertEqual(communication['access'][0]['status'], 'unverified')
            self.assertIn('Imported access evidence', communication['access'][0]['detail'])
            self.assertEqual(communication['setup_requests'][0]['id'], setup['id'])
            self.assertEqual(communication['setup_requests'][0]['status'], 'pending')
            self.assertEqual(communication['messages'][0]['id'], message['id'])
            self.assertEqual(communication['drafts'][0]['text'], 'Saved locally.')
        legacy = copy.deepcopy(backup)
        legacy.pop('communication')
        before = copy.deepcopy(self.store.state()['communication'])
        self.store.mutate('POST', '/api/import', legacy)
        self.assertEqual(self.store.state()['communication'], before)

    def test_import_identity_change_invalidates_access_without_communication(self):
        self.access()
        backup = self.store.backup()
        backup.pop('communication')
        target = next(account for account in backup['accounts'] if account['id'] == self.gmail['id'])
        target['handle'] = 'changed-by-import@example.test'
        self.store.mutate('POST', '/api/import', backup)
        access = self.store.state()['communication']['access'][0]
        self.assertEqual(access['status'], 'unverified')
        self.assertIn('identity changed', access['detail'])

    def test_communication_import_rejects_bad_relationships_atomically(self):
        self.access()
        self.sync()
        message = self.store.state()['communication']['messages'][0]
        self.store.mutate('POST', '/api/mail-drafts', {'message_id': message['id'], 'text': 'Local draft.'})
        baseline = self.store.backup()
        variants = []
        bad = copy.deepcopy(baseline)
        bad['communication']['messages'][0]['source_url'] = 'https://mail.google.com.evil.test/message'
        variants.append(bad)
        bad = copy.deepcopy(baseline)
        bad['communication']['messages'][0]['account_id'] = self.outlook['id']
        variants.append(bad)
        bad = copy.deepcopy(baseline)
        bad['communication']['messages'].append(copy.deepcopy(bad['communication']['messages'][0]))
        variants.append(bad)
        bad = copy.deepcopy(baseline)
        bad['communication']['drafts'][0]['account_id'] = self.outlook['id']
        variants.append(bad)
        bad = copy.deepcopy(baseline)
        bad['communication']['access'][0]['capabilities'] = []
        variants.append(bad)
        bad = copy.deepcopy(baseline)
        bad['communication']['mail_syncs'][0]['unread_count'] = 100
        bad['communication']['mail_syncs'][0]['total_count'] = 2
        variants.append(bad)
        bad = copy.deepcopy(baseline)
        bad['communication']['messages'][0]['authorization'] = 'secret'
        variants.append(bad)
        bad = copy.deepcopy(baseline)
        pending = dict(bad['communication']['setup_requests'][0]) if bad['communication']['setup_requests'] else {
            'id': 'pending-one', 'account_id': self.gmail['id'], 'status': 'pending',
            'created_at': '2026-09-15T10:00:00Z', 'updated_at': '2026-09-15T10:00:00Z', 'note': ''}
        pending['id'] = 'pending-one'
        duplicate_pending = dict(pending, id='pending-two')
        bad['communication']['setup_requests'] = [pending, duplicate_pending]
        variants.append(bad)
        for value in variants:
            with self.assertRaises(server.Invalid):
                self.store.mutate('POST', '/api/import', value)
            self.assertEqual(self.store.backup(), baseline)


if __name__ == '__main__':
    unittest.main()
