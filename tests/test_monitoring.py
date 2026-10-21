import copy
from datetime import datetime, timedelta, timezone
import http.client
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class MonitoringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name) / 'data'
        self.store = server.Store(self.data)
        self.account = self.store.mutate('POST', '/api/accounts', {'platform': 'YouTube', 'handle': 'example'})[1]['account']

    def tearDown(self):
        self.temp.cleanup()

    def report(self, run_id='run-1', minute=0):
        start = datetime(2026, 9, 14, 10, minute, tzinfo=timezone.utc)
        end = start + timedelta(minutes=1)
        return dict(id=run_id, started_at=start.isoformat(), completed_at=end.isoformat(), summary='One account checked.', accounts=[dict(account_id=self.account['id'], status='checked', checked_at=end.isoformat(), summary='Reviewed the visible comments.', coverage='Two visible comments; older history not inspected.', items_reviewed=2, deleted_count=0, pending_deletion_count=1, findings=[dict(severity='attention', title='Review a reply', detail='Requires user review of its context.', source_url='https://www.youtube.com/watch?v=example', kind='reply')])])

    def insert(self, report=None):
        return self.store.mutate('POST', '/api/monitor-runs', report if report is not None else self.report())

    def test_default_config_and_persistence(self):
        monitoring = self.store.state()['monitoring']
        self.assertFalse(monitoring['config']['enabled'])
        self.assertEqual(monitoring['runs'], [])
        status, result = self.insert()
        self.assertEqual(status, 201)
        self.assertTrue(result['created'])
        restored = server.Store(self.data).state()['monitoring']['runs']
        self.assertEqual(restored, [self.report()])
        # A report records results without silently modifying prior verification snapshots.
        self.assertIsNone(self.store.state()['accounts'][0]['access_checked_at'])

    def test_dedup_conflicts_and_replay_after_account_added(self):
        self.insert()
        activities = self.store.state()['activity']
        reordered = json.loads(json.dumps(self.report(), sort_keys=True))
        status, result = self.insert(reordered)
        self.assertEqual(status, 200)
        self.assertFalse(result['created'])
        self.assertEqual(self.store.state()['activity'], activities)
        changed = self.report()
        changed['summary'] = 'Conflicting report'
        with self.assertRaises(server.Invalid):
            self.insert(changed)
        self.store.mutate('POST', '/api/accounts', {'platform': 'Reddit', 'handle': 'another'})
        self.assertFalse(self.insert()[1]['created'])
        with self.assertRaises(server.Invalid):
            self.insert(self.report('new-run'))
        self.assertEqual(len(self.store.state()['monitoring']['runs']), 1)

    def test_all_accounts_and_blocked_attempt(self):
        other = self.store.mutate('POST', '/api/accounts', {'platform': 'X', 'handle': 'another'})[1]['account']
        report = self.report()
        blocked = copy.deepcopy(report['accounts'][0])
        blocked.update(account_id=other['id'], status='blocked', checked_at=None, summary='Sign-in unavailable.', coverage='No content accessible.', items_reviewed=0, deleted_count=0, pending_deletion_count=0, findings=[])
        report['accounts'].append(blocked)
        self.insert(report)
        result = self.store.state()['monitoring']['runs'][0]
        self.assertIsNone(result['accounts'][1]['checked_at'])
        self.assertEqual(result['started_at'], report['started_at'])

    def test_atomic_rejection_for_bad_rows(self):
        original = self.store.backup()
        variants = []
        def variant():
            value = self.report()
            variants.append(value)
            return value
        variant()['accounts'].append(copy.deepcopy(self.report()['accounts'][0]))
        variant()['accounts'][0]['account_id'] = 'unknown'
        variant()['accounts'][0]['findings'][0]['source_url'] = 'javascript:alert(1)'
        variant()['accounts'][0]['findings'][0]['source_url'] = 'https://user:password@example.com'
        variant()['accounts'][0]['items_reviewed'] = -1
        variant()['accounts'][0]['deleted_count'] = True
        variant()['accounts'][0]['pending_deletion_count'] = 1.5
        variant()['accounts'][0]['status'] = 'connected'
        variant()['accounts'][0]['checked_at'] = None
        variant()['accounts'][0]['checked_at'] = '2026-09-14T10:10:00Z'
        variant()['completed_at'] = '2026-09-13T10:00:00Z'
        variant()['accounts'][0]['cookies'] = 'not-allowed'
        variant()['summary'] = 'x' * 4001
        for payload in variants:
            with self.subTest(payload=variants.index(payload)), self.assertRaises(server.Invalid):
                self.insert(payload)
            self.assertEqual(self.store.backup(), original)

    def test_config_partial_strict_and_no_side_effect(self):
        old = self.store.state()['monitoring']['config']
        status, result = self.store.mutate('PATCH', '/api/monitoring', {'enabled': True, 'cadence_minutes': 30, 'automation_id': 'external-task-id', 'ends_at': '2026-09-15T12:00:00Z'})
        self.assertEqual(status, 200)
        self.assertEqual(result['config']['scope'], old['scope'])
        self.assertEqual(self.store.state()['monitoring']['runs'], [])
        for body in [{'enabled': 'true'}, {'cadence_minutes': False}, {'cadence_minutes': 0}, {'cadence_minutes': 10081}, {'ends_at': 'yesterday'}, {'cookie': 'not-allowed'}, {'scope': ''}]:
            with self.assertRaises(server.Invalid):
                self.store.mutate('PATCH', '/api/monitoring', body)
        self.assertEqual(self.store.state()['monitoring']['config'], result['config'])

    def test_state_newest_30_backup_retains_all(self):
        for index in range(32):
            self.insert(self.report('run-' + str(index), minute=index))
        visible = self.store.state()['monitoring']['runs']
        self.assertEqual(len(visible), 30)
        self.assertEqual(visible[0]['id'], 'run-31')
        self.assertEqual(visible[-1]['id'], 'run-2')
        backup = self.store.backup()
        self.assertEqual(len(backup['monitoring']['runs']), 32)
        with tempfile.TemporaryDirectory() as other_dir:
            other = server.Store(other_dir)
            other.mutate('POST', '/api/import', backup)
            self.assertEqual(other.backup(), backup)

    def test_backup_validation_and_old_backup_preservation(self):
        self.insert()
        before = self.store.backup()
        variants = []
        for kind in ('duplicate', 'conflict', 'foreign', 'config', 'null'):
            bad = copy.deepcopy(before)
            bad['accounts'][0]['handle'] = 'must-not-save'
            if kind == 'duplicate':
                bad['monitoring']['runs'].append(copy.deepcopy(bad['monitoring']['runs'][0]))
            elif kind == 'conflict':
                bad['monitoring']['runs'][0]['summary'] = 'changed'
            elif kind == 'foreign':
                bad['monitoring']['runs'][0]['accounts'][0]['account_id'] = 'unknown'
            elif kind == 'config':
                bad['monitoring']['config']['enabled'] = 'false'
            else:
                bad['monitoring'] = None
            variants.append(bad)
        for bad in variants:
            with self.assertRaises(server.Invalid):
                self.store.mutate('POST', '/api/import', bad)
            self.assertEqual(self.store.backup(), before)
        old = copy.deepcopy(before)
        old.pop('monitoring')
        self.store.mutate('POST', '/api/import', old)
        self.assertEqual(self.store.backup(), before)

    def saved_text_report(self, run_id='run-text', text='A whole saved comment: the loud part and the quiet part.', quotes=('the loud part',)):
        report = self.report(run_id)
        finding = report['accounts'][0]['findings'][0]
        finding['text'] = text
        finding['quotes'] = list(quotes)
        return report

    def test_optional_saved_text_and_verbatim_quotes(self):
        status, result = self.insert(self.saved_text_report())
        self.assertEqual(status, 201)
        saved = self.store.state()['monitoring']['runs'][0]['accounts'][0]['findings'][0]
        self.assertEqual(saved['text'], 'A whole saved comment: the loud part and the quiet part.')
        self.assertEqual(saved['quotes'], ['the loud part'])
        self.assertTrue(result['created'])
        # Absent keys stay absent, so an older stored report keeps its exact shape and digests.
        plain = self.report('run-plain', minute=5)
        self.insert(plain)
        stored = next(run for run in self.store.state()['monitoring']['runs'] if run['id'] == 'run-plain')
        self.assertEqual(stored, plain)
        self.assertNotIn('text', stored['accounts'][0]['findings'][0])
        self.assertNotIn('quotes', stored['accounts'][0]['findings'][0])
        # An identical replay of the older shape is still harmless.
        self.assertFalse(self.insert(self.report('run-plain', minute=5))[1]['created'])

    def test_saved_text_and_quote_rules_reject_atomically(self):
        original = self.store.backup()
        variants = []

        def variant(**changes):
            value = self.report('run-bad')
            value['accounts'][0]['findings'][0].update(changes)
            variants.append(value)

        variant(quotes=['orphan quote'])
        variant(text='', quotes=['orphan quote'])
        variant(text='the saved text', quotes=['not in the text'])
        variant(text='the saved text', quotes=['the saved', 'the saved'])
        variant(text='the saved text', quotes=[''])
        variant(text='the saved text', quotes=['the saved', 5])
        variant(text='the saved text', quotes='the saved')
        variant(text='the saved text', quotes={'0': 'the saved'})
        variant(text='word ' * 5000, quotes=['word'])
        variant(text='x' * (server.MAX_FINDING_TEXT + 1))
        variant(text=12)
        variant(text='y' * 600, quotes=['y' * (server.MAX_FINDING_QUOTE + 1)])
        long_text = ' '.join('q%02d' % index for index in range(server.MAX_FINDING_QUOTES + 1))
        variant(text=long_text, quotes=['q%02d' % index for index in range(server.MAX_FINDING_QUOTES + 1)])
        for payload in variants:
            with self.subTest(payload=variants.index(payload)), self.assertRaises(server.Invalid):
                self.insert(payload)
            self.assertEqual(self.store.backup(), original)
        # Exactly at the limits, the same shapes are accepted.
        ok = self.report('run-ok')
        ok['accounts'][0]['findings'][0]['text'] = long_text
        ok['accounts'][0]['findings'][0]['quotes'] = ['q%02d' % index for index in range(server.MAX_FINDING_QUOTES)]
        self.assertEqual(self.insert(ok)[0], 201)

    def test_saved_text_becomes_the_materialized_excerpt_and_backup_round_trip(self):
        self.insert(self.saved_text_report())
        review = next(value for value in self.store.state()['reviews'] if value['origin'] == 'import')
        self.assertEqual(review['excerpt'], 'A whole saved comment: the loud part and the quiet part.')
        item = next(value for value in self.store.state()['source_items'] if review['id'] in value['review_ids'])
        self.assertEqual(item['excerpt'], review['excerpt'])
        report_finding = next(value for value in item['findings'] if value['id'].startswith('report_'))
        self.assertEqual(report_finding['quotes'], ['the loud part'])
        for value in item['findings']:
            self.assertIn('quotes', value)
        backup = self.store.backup()
        with tempfile.TemporaryDirectory() as other_dir:
            other = server.Store(other_dir)
            other.mutate('POST', '/api/import', backup)
            self.assertEqual(other.backup(), backup)
            restored = other.state()['monitoring']['runs'][0]['accounts'][0]['findings'][0]
            self.assertEqual(restored['text'], 'A whole saved comment: the loud part and the quiet part.')
            self.assertEqual(restored['quotes'], ['the loud part'])

    def test_repeated_findings_union_quotes_without_moving_the_fingerprint(self):
        text = 'A whole saved comment: the loud part and the quiet part.'
        exact = 'https://www.youtube.com/watch?v=example&lc=comment'

        def sourced(run_id, minute, quotes):
            value = self.report(run_id, minute=minute)
            value['accounts'][0]['findings'][0].update(source_url=exact, text=text, quotes=list(quotes))
            return value

        self.insert(sourced('run-a', 0, ['the loud part']))
        self.insert(sourced('run-b', 7, ['the quiet part', 'the loud part']))
        item = next(value for value in self.store.state()['source_items'] if value['source_key'] == exact)
        self.assertTrue(item['precise_source'])
        combined = [value for value in item['findings'] if value['title'] == 'Review a reply']
        self.assertEqual(len(combined), 1, 'one semantic finding survives two reports')
        # Reports are read newest first, so the newer report's quotes lead the union.
        self.assertEqual(combined[0]['quotes'], ['the quiet part', 'the loud part'], 'quotes union in first-seen order, no duplicates')

        # The same reviews and the same findings fingerprint the same with or without quotes.
        account = server.account_record(dict(self.account))
        reviews = [dict(kind='comment', account_id=account['id'], title='Review this', excerpt=text,
                        source_url='https://www.youtube.com/watch?v=example&lc=one', sample_count=1,
                        severity='attention', finding='A concern.', suggestion='', origin='import')]
        bare = [dict(id='f1', title='Review a reply', detail='Requires user review of its context.',
                     severity='attention', kind='reply', review_ids=[], flag_ids=[], quotes=[])]
        quoted = [dict(bare[0], quotes=['the loud part', 'the quiet part'])]
        self.assertEqual(server.workflow.evidence_fingerprint(account, 'key', reviews, bare),
                         server.workflow.evidence_fingerprint(account, 'key', reviews, quoted))
        self.assertEqual(server.workflow.semantic_finding(quoted[0]), server.workflow.semantic_finding(bare[0]))
        self.assertNotIn('quotes', server.workflow.semantic_finding(quoted[0]))

    def test_theme_choices_include_the_two_new_palettes(self):
        self.assertEqual(server.THEME_CHOICES, ('dark', 'light', 'system', 'phosphor', 'midnight'))
        for theme in server.THEME_CHOICES:
            self.assertEqual(self.store.mutate('PATCH', '/api/settings', {'theme': theme})[1]['settings']['theme'], theme)
        for theme in ('amber', '', 'Dark', 'phosphor ', None):
            with self.assertRaises(server.Invalid):
                self.store.mutate('PATCH', '/api/settings', {'theme': theme})
        self.assertEqual(self.store.state()['settings']['theme'], 'midnight')

    def test_cli_without_running_server(self):
        report_file = Path(self.temp.name) / 'report.json'
        report_file.write_text(json.dumps(self.report()))
        command = [sys.executable, str(server.ROOT / 'monitor.py'), '--data-dir', str(self.data)]
        response = subprocess.run(command + ['--report', str(report_file)], capture_output=True, text=True)
        self.assertEqual(response.returncode, 0, response.stderr)
        self.assertTrue(json.loads(response.stdout)['created'])
        response = subprocess.run(command + ['--status'], capture_output=True, text=True)
        self.assertEqual(response.returncode, 0, response.stderr)
        self.assertEqual(json.loads(response.stdout)['run_count'], 1)
        config_file = Path(self.temp.name) / 'config.json'
        config_file.write_text(json.dumps({'cadence_minutes': 120}))
        response = subprocess.run(command + ['--config', str(config_file)], capture_output=True, text=True)
        self.assertEqual(response.returncode, 0, response.stderr)
        self.assertEqual(json.loads(response.stdout)['config']['cadence_minutes'], 120)
        report_file.write_bytes(b'x' * (server.MAX_BODY + 1))
        response = subprocess.run(command + ['--report', str(report_file)], capture_output=True, text=True)
        self.assertEqual(response.returncode, 1)
        self.assertIn('2 MB', response.stderr)

    def test_private_directory_guards(self):
        for path in [server.ROOT / 'data', Path(self.temp.name) / 'OneDrive' / 'data', Path(self.temp.name) / 'Dropbox' / 'data', Path(self.temp.name) / 'Library' / 'CloudStorage' / 'data']:
            with self.assertRaises(server.Invalid):
                server.safe_data_directory(path)
        repo = Path(self.temp.name) / 'other-repository'
        (repo / '.git').mkdir(parents=True)
        with self.assertRaises(server.Invalid):
            server.safe_data_directory(repo / 'data')

    def test_large_backup_route_has_separate_bounded_limit(self):
        self.insert()
        backup = self.store.backup()
        review = self.store.mutate('POST', '/api/reviews', {'account_id': self.account['id'], 'kind': 'comment', 'title': 'Example', 'finding': 'x' * 4000, 'excerpt': 'x' * 4000, 'severity': 'suggestion'})[1]['review']
        backup['reviews'] = [dict(review, id='large-' + str(index)) for index in range(300)]
        payload = json.dumps(backup).encode()
        self.assertGreater(len(payload), server.MAX_BODY)
        self.assertLess(len(payload), server.MAX_IMPORT_BODY)
        app = server.make_server(self.data, port=0)
        worker = threading.Thread(target=app.serve_forever, daemon=True)
        worker.start()
        try:
            port = app.server_address[1]
            def request(path, body, declared=None):
                connection = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
                headers = {'Content-Type': 'application/json', 'Origin': f'http://127.0.0.1:{port}', 'X-Resonance-Workspace': app.store.context()['token']}
                if declared is not None:
                    headers['Content-Length'] = str(declared)
                connection.request('POST', path, body, headers)
                response = connection.getresponse()
                status = response.status
                response.read()
                connection.close()
                return status
            self.assertEqual(request('/api/import', payload), 200)
            self.assertEqual(len(self.store.backup()['reviews']), 302)
            before = self.store.backup()
            self.assertEqual(request('/api/import', b'', server.MAX_IMPORT_BODY + 1), 413)
            self.assertEqual(request('/api/monitor-runs', b'', server.MAX_BODY + 1), 413)
            self.assertEqual(self.store.backup(), before)
            self.assertEqual(len(before['monitoring']['runs']), 1)
        finally:
            app.shutdown()
            app.server_close()
            worker.join()

    def test_http_mutations_require_origin_and_accept_reports(self):
        app = server.make_server(self.data, port=0)
        worker = threading.Thread(target=app.serve_forever, daemon=True)
        worker.start()
        try:
            port = app.server_address[1]
            for origin, expected in [('https://evil.example', 403), (f'http://127.0.0.1:{port}', 201)]:
                connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
                connection.request('POST', '/api/monitor-runs', json.dumps(self.report()), {'Content-Type': 'application/json', 'Origin': origin, 'X-Resonance-Workspace': app.store.context()['token']})
                response = connection.getresponse()
                self.assertEqual(response.status, expected)
                response.read()
                connection.close()
            self.assertEqual(len(self.store.state()['monitoring']['runs']), 1)
        finally:
            app.shutdown()
            app.server_close()
            worker.join()


if __name__ == '__main__':
    unittest.main()
