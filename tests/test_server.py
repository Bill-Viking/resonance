"""Run: python3 -m unittest discover -s tests -v"""
import copy
from datetime import datetime, timedelta, timezone
import http.client
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class StartupTests(unittest.TestCase):
    def test_loopback_startup_never_resolves_a_hostname(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch('socket.getfqdn', side_effect=AssertionError('unexpected DNS lookup')):
            app = server.make_server(directory, port=0)
            try:
                self.assertEqual(app.server_name, '127.0.0.1')
                self.assertEqual(app.server_port, app.server_address[1])
                self.assertGreater(app.server_port, 0)
            finally:
                app.server_close()


class LocalAPITest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = server.make_server(self.temp.name, port=0)
        self.worker = threading.Thread(target=self.app.serve_forever, daemon=True)
        self.worker.start()
        self.port = self.app.server_address[1]
        self.origin = f'http://127.0.0.1:{self.port}'

    def tearDown(self):
        self.app.shutdown()
        self.app.server_close()
        self.worker.join()
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None, raw=False):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        data = body if raw else json.dumps(body).encode() if body is not None else None
        h = {'Content-Type': 'application/json', 'Origin': self.origin,
             'X-Resonance-Workspace': self.app.store.context()['token']}
        if headers:
            h.update(headers)
        connection.request(method, path, data, h)
        response = connection.getresponse()
        content = response.read()
        status, response_headers = response.status, dict(response.getheaders())
        connection.close()
        return status, json.loads(content) if 'application/json' in response_headers.get('Content-Type', '') else content.decode(), response_headers

    def account(self, **extra):
        payload = dict(platform='Reddit', handle='example', display_name='Example', url='https://example.com/profile')
        payload.update(extra)
        status, body, _ = self.request('POST', '/api/accounts', payload)
        self.assertEqual(status, 201, body)
        return body['account']

    def review(self, account, **extra):
        payload = dict(account_id=account['id'], kind='comment', title='A wording question', finding='Could be read as abrupt', excerpt='Example only', severity='suggestion', observed_at='2026-09-13')
        payload.update(extra)
        status, body, _ = self.request('POST', '/api/reviews', payload)
        self.assertEqual(status, 201, body)
        return body['review']

    def test_empty_and_persistence(self):
        status, body, headers = self.request('GET', '/api/state')
        self.assertEqual(status, 200)
        self.assertEqual(body['accounts'], [])
        self.assertEqual(headers['Cache-Control'], 'no-store')
        account = self.account()
        review = self.review(account)
        self.request('PATCH', '/api/settings', {'display_name': 'A person', 'theme': 'light'})
        recovered = server.Store(self.temp.name).state()
        self.assertEqual(recovered['accounts'][0], account)
        self.assertEqual(recovered['reviews'][0], review)
        self.assertEqual(recovered['settings']['display_name'], 'A person')

    def test_assistant_sentence_uses_the_running_code_folder(self):
        status, body, headers = self.request('GET', '/api/assistant')
        self.assertEqual(status, 200)
        self.assertEqual(body, {'prompt': f'Read ASSISTANT.md in {server.ROOT} and run my Resonance check.'})
        self.assertEqual(headers['Cache-Control'], 'no-store')

    def test_sample_http_cli_isolation_and_stale_tabs(self):
        real = self.account()
        before = self.request('GET', '/api/export')[1]
        database = server.Store(self.temp.name).path
        before_bytes = database.read_bytes()
        old_token = self.app.store.context()['token']
        self.assertEqual(self.request('POST', '/api/samples', {})[0], 201)
        _, practice, _ = self.request('GET', '/api/state')
        self.assertEqual(practice['workspace']['mode'], 'samples')
        self.assertNotIn(real['id'], [account['id'] for account in practice['accounts']])
        sample_token = practice['workspace']['token']
        queued = next(request for request in practice['action_requests'] if request['execution_state'] == 'queued')
        self.assertEqual(self.request('POST', '/api/samples/rehearse', {'request_id':queued['id'],'decision':'yes'})[0], 200)
        for path in ['/api/export','/api/brief']:
            self.assertEqual(self.request('GET', path)[0],400)
        self.assertEqual(self.request('POST','/api/import',before)[0],400)
        self.assertEqual(self.request('POST','/api/accounts',{'platform':'X','handle':'real-again'}, {'X-Resonance-Workspace':old_token})[0],400)
        for args in [['--status'],['--work'],['--request',queued['id']],['--start-request',queued['id']]]:
            result = subprocess.run([sys.executable,str(server.ROOT/'monitor.py'),'--data-dir',self.temp.name,*args],capture_output=True,text=True)
            self.assertEqual(result.returncode,1,result.stdout)
            self.assertEqual(result.stdout,'')
            self.assertIn('Samples are showing',result.stderr)
        self.assertEqual(database.read_bytes(), before_bytes)
        self.assertEqual(self.request('POST','/api/samples/clear',{})[0],200)
        self.assertFalse((Path(self.temp.name)/'demo.sqlite3').exists())
        self.assertEqual(self.request('POST','/api/accounts',{'platform':'X','handle':'sample-again'}, {'X-Resonance-Workspace':sample_token})[0],400)
        self.assertEqual(self.request('POST','/api/import',practice)[0],400)
        self.assertEqual(self.request('GET','/api/export')[1],before)
        self.assertEqual(database.read_bytes(),before_bytes)

    def test_folder_details_and_open_are_guarded(self):
        status, body, _ = self.request('GET', '/api/data-folder')
        self.assertEqual(status, 200)
        self.assertEqual(body['path'], str(self.app.store.directory))
        self.assertGreater(body['size_bytes'], 0)
        with patch.object(self.app.store, 'open_data_folder', return_value={'opened': True}) as opened:
            self.assertEqual(self.request('POST', '/api/data-folder/open', {}, {'Origin': 'https://example.test'})[0], 403)
            self.assertEqual(self.request('POST', '/api/data-folder/open', {'path': '/arbitrary'})[0], 400)
            opened.assert_not_called()
            self.assertEqual(self.request('POST', '/api/data-folder/open', {})[:2], (200, {'opened': True}))
            opened.assert_called_once_with(self.app.store.context()['token'])

    def test_occupied_port_refuses_before_any_database_access(self):
        with patch.object(server, 'Store') as store:
            with self.assertRaises(OSError):
                server.make_server(self.temp.name, port=self.port)
            store.assert_not_called()

    def test_same_origin_and_host_protection(self):
        for host in ['evil.example', f'evil.example:{self.port}', '127.0.0.1:1', f'127.0.0.1.evil.example:{self.port}']:
            self.assertEqual(self.request('GET', '/api/state', headers={'Host': host})[0], 403)
        for origin in ['https://evil.example', 'null', 'http://localhost:1', self.origin + '.evil.example']:
            self.assertEqual(self.request('PATCH', '/api/settings', {'theme': 'light'}, {'Origin': origin})[0], 403)
        self.assertEqual(self.request('PATCH', '/api/settings', {'theme': 'light'}, {'Sec-Fetch-Site': 'cross-site'})[0], 403)
        connection = http.client.HTTPConnection('127.0.0.1', self.port)
        connection.request('PATCH', '/api/settings', '{}', {'Content-Type': 'application/json'})
        response = connection.getresponse()
        self.assertEqual(response.status, 403)
        response.read()
        connection.close()
        self.assertEqual(self.request('GET', '/api/state')[1]['settings']['theme'], 'dark')

    def test_body_security(self):
        self.assertEqual(self.request('PATCH', '/api/settings', '{}', {'Content-Type': 'text/plain'}, raw=True)[0], 415)
        self.assertEqual(self.request('PATCH', '/api/settings', '{"theme":"dark","theme":"light"}', raw=True)[0], 400)
        self.assertEqual(self.request('PATCH', '/api/settings', '{"theme":NaN}', raw=True)[0], 400)
        self.assertEqual(self.request('PATCH', '/api/settings', '{"theme":"\\ud800"}', raw=True)[0], 400)
        self.assertEqual(self.request('POST', '/api/import', b'', {'Content-Length': str(server.MAX_IMPORT_BODY + 1)}, raw=True)[0], 413)
        for path in ['/server.py', '/social-brief.sqlite3', '/../server.py', '/%2e%2e/server.py', '/static/../server.py']:
            self.assertEqual(self.request('GET', path)[0], 404)

    def test_validation_and_literal_text(self):
        for body in [dict(platform='Other', handle='test'), dict(platform='Reddit', handle=''), dict(platform='Reddit', handle=123), dict(platform='Reddit', handle='test', token='secret'), dict(platform='Reddit', handle='test', url='javascript:alert(1)'), dict(platform='Reddit', handle='test', url='https://name:pass@example.com')]:
            self.assertEqual(self.request('POST', '/api/accounts', body)[0], 400)
        account = self.account(display_name='<script>alert(1)</script>')
        self.assertEqual(account['display_name'], '<script>alert(1)</script>')
        self.assertEqual(self.request('PATCH', '/api/accounts/' + account['id'], {'access_checked_at': 'yesterday'})[0], 400)
        self.assertEqual(self.request('POST', '/api/reviews', dict(account_id=account['id'], kind='post', title='Title', severity='strength', finding='Finding', sample_count=True))[0], 400)

    def test_review_lifecycle_and_expiry(self):
        review = self.review(self.account())
        endpoint = '/api/reviews/' + review['id']
        snoozed = self.request('PATCH', endpoint, {'status': 'snoozed'})[1]['review']
        due = datetime.fromisoformat(snoozed['snoozed_until'].replace('Z', '+00:00'))
        self.assertAlmostEqual((due - datetime.now(timezone.utc)).total_seconds(), 86400, delta=3)
        with server.Store(self.temp.name).connect() as db:
            snoozed['snoozed_until'] = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
            server.Store.put(db, 'reviews', snoozed)
        reopened = self.request('GET', '/api/state')[1]['reviews'][0]
        self.assertEqual(reopened['status'], 'open')
        self.assertIsNone(reopened['snoozed_until'])
        for status in ['resolved', 'open']:
            response = self.request('PATCH', endpoint, {'status': status})
            self.assertEqual(response[1]['review']['status'], status)
        self.assertEqual(self.request('PATCH', endpoint, {'status': 'deleted'})[0], 400)

    def test_import_is_atomic_strict_and_idempotent(self):
        self.review(self.account())
        original = self.request('GET', '/api/export')[1]
        variants = []
        bad = copy.deepcopy(original)
        bad['settings']['display_name'] = 'Should not save'
        bad['accounts'][0]['handle'] = 'Should not save'
        bad['reviews'][0]['source_url'] = 'javascript:bad'
        variants.append(bad)
        bad = copy.deepcopy(original)
        bad['reviews'][0]['account_id'] = 'missing'
        variants.append(bad)
        bad = copy.deepcopy(original)
        bad['accounts'].append(copy.deepcopy(bad['accounts'][0]))
        variants.append(bad)
        bad = copy.deepcopy(original)
        bad['reviews'].append(copy.deepcopy(bad['reviews'][0]))
        variants.append(bad)
        bad = copy.deepcopy(original)
        bad['api_key'] = 'not-allowed'
        variants.append(bad)
        bad = copy.deepcopy(original)
        bad['schema_version'] = True
        variants.append(bad)
        bad = copy.deepcopy(original)
        bad['reviews'][0]['observed_at'] = '2026-02-30'
        variants.append(bad)
        for backup in variants:
            self.assertEqual(self.request('POST', '/api/import', backup)[0], 400)
            self.assertEqual(self.request('GET', '/api/export')[1], original)
        for _ in range(2):
            self.assertEqual(self.request('POST', '/api/import', original)[1], {'accounts': 1, 'reviews': 1})
        self.assertEqual(len(self.request('GET', '/api/state')[1]['reviews']), 1)

    def test_identity_policy_guards_and_legacy_backup(self):
        account = self.account()
        self.assertEqual((account['identity_mode'], account['daring'], account['sfw_only']), ('unclassified', 'careful', True))
        for mode in ['named', 'unclassified']:
            self.assertEqual(self.request('POST', '/api/accounts', dict(platform='Reddit', handle='test', identity_mode=mode, sfw_only=False))[0], 400)
        alias = self.account(identity_mode='alias', sfw_only=False, daring='bold')
        endpoint = '/api/accounts/' + alias['id']
        named = self.request('PATCH', endpoint, {'identity_mode': 'named'})[1]['account']
        self.assertTrue(named['sfw_only'])
        self.assertEqual(self.request('PATCH', endpoint, {'sfw_only': False})[0], 400)
        backup = self.request('GET', '/api/export')[1]
        for account in backup['accounts']:
            for key in ('identity_mode', 'sfw_only', 'daring'):
                account.pop(key)
        self.assertEqual(self.request('POST', '/api/import', backup)[0], 200)
        restored = self.request('GET', '/api/export')[1]
        self.assertTrue(all(a['sfw_only'] and a['identity_mode'] == 'unclassified' for a in restored['accounts']))
        restored['accounts'][0]['identity_mode'] = 'named'
        restored['accounts'][0]['sfw_only'] = False
        self.assertEqual(self.request('POST', '/api/import', restored)[0], 400)

    def test_account_aware_draft_check_never_stores_drafts(self):
        account = self.account(identity_mode='named', daring='bold')
        before = self.request('GET', '/api/export')[1]
        status, result, _ = self.request('POST', '/api/draft-check', {'text': 'You are an idiot!!!', 'account_id': account['id']})
        self.assertEqual(status, 200)
        self.assertTrue(result['signals'])
        self.assertEqual(self.request('GET', '/api/export')[1], before)
        self.assertEqual(self.request('POST', '/api/draft-check', {'text': 'Hello.', 'account_id': 'missing'})[0], 404)
        self.assertEqual(self.request('POST', '/api/draft-check', {'text': 'Hello.'})[0], 200)

    def test_brief_truthful_scope_and_open_records(self):
        account = self.account()
        review = self.review(account, title='First', source_url='https://example.com/1')
        other = self.review(account, title='Resolved evidence')
        self.request('PATCH', '/api/reviews/' + other['id'], {'status': 'resolved'})
        status, brief, headers = self.request('GET', '/api/brief')
        self.assertEqual(status, 200)
        self.assertIn('wording feedback', brief)
        self.assertIn('First', brief)
        self.assertIn('https://example.com/1', brief)
        self.assertNotIn('Resolved evidence', brief)
        self.assertIn('attachment', headers['Content-Disposition'])


class DraftTests(unittest.TestCase):
    def test_offsets_and_rules(self):
        text = '😀 You always sound STUPID!!!'
        result = server.draft_check(text)
        self.assertEqual(result['word_count'], 4)
        generalization = next(s for s in result['signals'] if s['title'] == 'Broad generalization')
        self.assertEqual(generalization['start'], 3)
        self.assertEqual(generalization['end'], 13)
        self.assertGreaterEqual(len(result['signals']), 4)
        self.assertEqual(server.draft_check('I see this differently.')['signals'], [])
        self.assertIn('limited', server.draft_check('')['summary'])

    def test_types_and_length(self):
        for value in [None, 3, [], 'x' * 50001]:
            with self.assertRaises(server.Invalid):
                server.draft_check(value)

    def test_data_path_guards(self):
        for path in [server.ROOT, server.ROOT / 'data']:
            with self.assertRaises(server.Invalid):
                server.safe_data_directory(path)


if __name__ == '__main__':
    unittest.main()
