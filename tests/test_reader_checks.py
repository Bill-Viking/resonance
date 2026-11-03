"""Reader-check persistence: synthetic accounts and temporary folders only. No browser, network or real data."""
from contextlib import closing
import copy
from datetime import datetime, timedelta, timezone
import http.client
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid

import reader_checks
import samples
import server
import workflow

CHANNEL = 'UC' + 'a' * 22
OTHER_CHANNEL = 'UC' + 'b' * 22
COMMENT = 'UgxExampleComment01'
VIDEO = 'abcdefghijk'
CLEAN = 'A calm note about tide pools.'
RUDE = 'This is bullshit and I am done.'


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def coverage(count, skipped=(), more=False, notes=(), examined=None, **changes):
    value = {'limit': 3, 'accepted': count, 'examined': count + sum(n for _, n in skipped) if examined is None else examined,
             'skipped': [{'reason': reason, 'count': n} for reason, n in sorted(skipped)], 'more_available': more,
             'partial': count < 3 or bool(skipped) or more, 'notes': sorted(notes)}
    value.update(changes)
    return value


def x_item(text=CLEAN, sid='2001', handle='river_example', **changes):
    value = {'provider': 'x', 'source_id': sid, 'url': f'https://x.com/{handle}/status/{sid}', 'author': handle,
             'channel_id': None, 'text': text, 'date_iso': '2026-10-30T12:00:00.000Z', 'date_label': 'Oct 30',
             'date_unknown': False, 'links_shortened': False}
    value.update(changes)
    return value


def yt_item(text=CLEAN, lc=COMMENT, handle='kelp_example', channel=CHANNEL, **changes):
    value = {'provider': 'youtube', 'source_id': lc, 'url': f'https://www.youtube.com/watch?v={VIDEO}&lc={lc}',
             'author': handle, 'channel_id': channel, 'text': text, 'date_iso': None, 'date_label': '2 days ago',
             'date_unknown': True, 'links_shortened': False}
    value.update(changes)
    return value


def ok_result(provider, items, handle, channel=None, **changes):
    notes = ['activity_text_not_used'] if provider == 'youtube' else []
    notes += ['dates_relative_or_unknown'] if any(i['date_unknown'] for i in items) else []
    notes += ['link_display_shortened'] if any(i['links_shortened'] for i in items) else []
    value = {'type': 'RESULT', 'requestId': uuid.uuid4().hex, 'provider': provider, 'mode': 'live', 'status': 'ok',
             'tabs': [4, 5], 'items': items, 'coverage': coverage(len(items), notes=notes),
             'identity': {'handle': handle, 'channel_id': channel}}
    value.update(changes)
    return value


def x_ok(items=None, handle='river_example', **changes):
    return ok_result('x', items or [x_item()], handle, **changes)


def yt_ok(items=None, handle='kelp_example', channel=CHANNEL, **changes):
    return ok_result('youtube', items or [yt_item()], handle, channel, **changes)


def not_ok(provider, status='held', reason='sign_in_needed', **changes):
    value = {'type': 'RESULT', 'requestId': uuid.uuid4().hex, 'provider': provider, 'mode': 'live', 'status': status,
             'tabs': [], 'reason': reason}
    value.update(changes)
    return value


def row(account, status, result):
    return {'account_id': account['id'], 'status': status, 'result': result}


class Rig(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'data'
        self.ws = samples.Workspace(self.directory, server.Store)
        self.token = self.ws.context()['token']
        self.x = self.account('X', '@river_example', 'https://x.com/river_example')
        self.yt = self.account('YouTube', '@kelp_example', 'https://www.youtube.com/@kelp_example')
        self.fb = self.account('Facebook', 'moss.example', 'https://www.facebook.com/moss.example')

    def post(self, path, body, method='POST'):
        return self.ws.mutate(method, path, body, self.token)

    def account(self, platform, handle, url, **changes):
        body = {'platform': platform, 'handle': handle, 'url': url, 'display_name': 'Example', 'identity_mode': 'alias',
                'daring': 'balanced', 'sfw_only': True}
        body.update(changes)
        return self.post('/api/accounts', body)[1]['account']

    def begin(self, *accounts):
        status, job = self.post('/api/checks/begin', {'account_ids': [a['id'] for a in accounts]})
        self.assertEqual(status, 201)
        return job

    def payload(self, job, *rows, **changes):
        value = {'check_id': job['check_id'], 'reader_version': '0.1.0', 'completed_at': stamp(), 'accounts': list(rows)}
        value.update(changes)
        return value

    def commit(self, body):
        return self.post('/api/checks', body)

    def save(self, *pairs):
        """pairs: (account, status, result). Returns (job, receipt)."""
        job = self.begin(*[pair[0] for pair in pairs])
        status, result = self.commit(self.payload(job, *[row(*pair) for pair in pairs]))
        self.assertEqual(status, 201)
        return job, result['receipt']

    def tables(self):
        with server.Store(self.directory).connect() as db:
            return {name: db.execute(f'SELECT count(*) FROM {name}').fetchone()[0]
                    for name in ('reader_checks', 'reader_sources', 'reader_revisions', 'reviews', 'flag_evidence')}

    def sources(self, account):
        return self.ws.read_corpus({'account_id': [account['id']], 'limit': ['50']}, self.token)['sources']

    def refused(self, body, pattern, job=None):
        before = self.tables()
        with self.assertRaisesRegex(server.Invalid, pattern):
            self.commit(body)
        self.assertEqual(self.tables(), before)
        if job is not None:  # An invalid attempt leaves the job waiting, so the reader may retry.
            self.assertEqual(self.ws.read_check(job['check_id'], self.token)['status'], 'pending')


class SuccessTests(Rig):
    def test_x_success_begin_receipt_corpus_review_and_bounded_state(self):
        job = self.begin(self.x)
        self.assertRegex(job['check_id'], r'^[a-f0-9]{32}$')
        self.assertEqual(job['accounts'][0]['account']['handle'], '@river_example')
        self.assertEqual(job['accounts'][0]['expected'], {'handle': 'river_example', 'channel_id': None})
        status, saved = self.commit(self.payload(job, row(self.x, 'ok', x_ok([x_item(CLEAN), x_item(RUDE, '2002')]))))
        receipt = saved['receipt']
        self.assertEqual((status, saved['replayed'], receipt['origin']), (201, False, 'live'))
        self.assertIn('not a cryptographic attestation', receipt['statement'])
        account = receipt['accounts'][0]
        self.assertEqual((account['source_count'], account['new_source_count'], account['finding_count'],
                          account['flagged_source_count'], account['new_review_count']), (2, 2, 1, 1, 1))
        self.assertEqual(account['coverage']['partial'], True)
        self.assertEqual(self.ws.read_check(job['check_id'], self.token), receipt)
        sources = self.sources(self.x)
        self.assertEqual({source['kind'] for source in sources}, {'post'})
        self.assertEqual({source['current_revision']['text'] for source in sources}, {CLEAN, RUDE})
        state = self.ws.state()
        self.assertEqual([review['excerpt'] for review in state['reviews']], [RUDE])  # Only findings become reviews.
        self.assertNotIn(CLEAN, json.dumps(state))                                   # Clean text stays out of state.
        summary = state['reader_check_summary']
        self.assertEqual((summary['count'], summary['source_count'], summary['revision_count']), (1, 2, 2))
        self.assertNotIn('items', json.dumps(summary))
        self.assertTrue(state['policy_flags'])

    def test_youtube_success_requires_handle_and_channel_and_uses_comment_kind(self):
        _, receipt = self.save((self.yt, 'ok', yt_ok()))
        self.assertEqual(receipt['accounts'][0]['identity'], {'handle': 'kelp_example', 'channel_id': CHANNEL})
        source = self.sources(self.yt)[0]
        self.assertEqual((source['kind'], source['date_iso'], source['date_label'], source['date_unknown']),
                         ('comment', None, '2 days ago', True))
        self.assertEqual(source['channel_id'], CHANNEL)

    def test_youtube_channel_url_identifies_expected_channel_and_contradictions_cannot_succeed(self):
        by_id = self.account('YouTube', '@kelp_two', f'https://www.youtube.com/channel/{CHANNEL}')
        job = self.begin(by_id)
        self.assertEqual(job['accounts'][0]['expected'], {'handle': 'kelp_two', 'channel_id': CHANNEL})
        self.commit(self.payload(job, row(by_id, 'ok', yt_ok([yt_item(handle='kelp_two')], 'kelp_two'))))
        job = self.begin(by_id)
        self.refused(self.payload(job, row(by_id, 'ok', yt_ok([yt_item(handle='kelp_two', channel=OTHER_CHANNEL)], 'kelp_two', OTHER_CHANNEL))),
                     'channel does not match', job)
        bad = self.account('YouTube', '@kelp_three', 'https://www.youtube.com/@someone_else')
        job = self.begin(bad)
        self.assertIsNone(job['accounts'][0]['expected'])
        self.assertIn('different accounts', job['accounts'][0]['expected_problem'])
        self.refused(self.payload(job, row(bad, 'ok', yt_ok([yt_item(handle='kelp_three')], 'kelp_three'))), 'no usable handle', job)

    def test_unsupported_held_and_error_save_receipts_without_corpus(self):
        job = self.begin(self.x, self.yt, self.fb)
        body = self.payload(job, row(self.x, 'held', not_ok('x', 'held', 'no_items_accepted')),
                            row(self.yt, 'error', not_ok('youtube', 'error', 'reader_unresponsive')),
                            row(self.fb, 'unsupported', {'reason': 'provider_not_supported'}))
        status, saved = self.commit(body)
        self.assertEqual(status, 201)
        self.assertEqual(saved['receipt']['totals'], {'accounts': 3, 'ok': 0, 'held': 1, 'error': 1, 'unsupported': 1,
                                                      'sources': 0, 'findings': 0})
        self.assertEqual(self.tables()['reader_sources'], 0)
        self.assertEqual(saved['receipt']['accounts'][2]['coverage']['notes'], ['not_read'])

    def test_unsupported_and_status_rules(self):
        for account, status, result, pattern in [
                (self.fb, 'ok', ok_result('facebook', [x_item()], 'moss.example'), 'Only X and YouTube'),
                (self.fb, 'held', not_ok('facebook'), 'Only X and YouTube'),
                (self.x, 'unsupported', {'reason': 'provider_not_supported'}, 'cannot be reported as unsupported'),
                (self.fb, 'unsupported', {'reason': 'other'}, 'provider_not_supported'),
                (self.x, 'cancelled', not_ok('x', 'cancelled', 'cancelled'), 'ok, held, error or unsupported'),
                (self.x, 'error', not_ok('x', 'held'), 'disagree'),
                (self.yt, 'held', not_ok('youtube', 'held', 'ownership_not_confirmed', diagnostic={'x': 1}), 'Diagnostic'),
                (self.x, 'held', not_ok('x', 'held', 'ownership_not_confirmed', diagnostic={}), 'only valid for a held YouTube')]:
            job = self.begin(account)
            self.refused(self.payload(job, row(account, status, result)), pattern, job)
        job = self.begin(self.yt)
        diagnostic = {'document_ready': 'complete', 'visibility': 'visible', 'avatar': True, 'editing_link': False,
                      'content_link': False, 'channels': 1}
        self.assertEqual(self.commit(self.payload(job, row(self.yt, 'held', not_ok(
            'youtube', 'held', 'ownership_not_confirmed', diagnostic=diagnostic))))[0], 201)


class ValidationTests(Rig):
    def check(self, mutate, pattern, account=None, result=None):
        account = account or self.x
        result = result or (x_ok() if account is self.x else yt_ok())
        job = self.begin(account)
        body = self.payload(job, row(account, 'ok', result))
        mutate(body, body['accounts'][0]['result'])
        self.refused(body, pattern, job)

    def test_envelope_and_time_rules(self):
        job = self.begin(self.x)
        good = row(self.x, 'ok', x_ok())
        for changes, pattern in [({'reader_version': '0.0.5'}, 'reader version'), ({'reader_version': 6}, 'reader version'),
                                 ({'completed_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}, 'future'),
                                 ({'completed_at': (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()}, 'before it began'),
                                 ({'completed_at': '2026-10-30 12:00'}, 'ISO timestamp'), ({'extra': 1}, 'exactly these fields'),
                                 ({'accounts': []}, 'wrong size'), ({'accounts': [good, good]}, 'twice'),
                                 ({'check_id': uuid.uuid4().hex}, 'not waiting or has expired'), ({'check_id': 'x'}, 'Invalid check')]:
            self.refused(self.payload(job, good, **changes), pattern)
        self.refused(self.payload(job, dict(good, extra=1)), 'exactly these fields')
        self.refused(self.payload(job, row(self.yt, 'ok', yt_ok())), 'not selected')
        self.refused(self.payload(job, row(self.x, 'ok', x_ok(mode='fixture'))), 'live reader results')
        self.refused(self.payload(job, row(self.x, 'ok', yt_ok())), 'different platform')
        self.refused(self.payload(job, row(self.x, 'ok', x_ok(requestId='short'))), 'request ID')
        self.refused(self.payload(job, row(self.x, 'ok', x_ok(tabs=[-1]))), 'tab list')
        self.refused(self.payload(job, row(self.x, 'ok', x_ok(extra=1))), 'exactly these fields')
        both = self.begin(self.x, self.yt)
        self.refused(self.payload(both, row(self.x, 'ok', x_ok())), 'exactly one result')

    def test_identity_rules(self):
        self.check(lambda b, r: r['identity'].update(handle='someone_else'), 'observed handle does not match')
        self.check(lambda b, r: r['identity'].update(channel_id=CHANNEL), 'observed X identity')
        self.check(lambda b, r: r['identity'].pop('channel_id'), 'exactly these fields')
        self.check(lambda b, r: r['identity'].update(channel_id=None), 'current handle and stable channel', self.yt)
        self.check(lambda b, r: r['identity'].update(handle='other_channel'), 'observed handle does not match', self.yt)
        self.check(lambda b, r: r['identity'].update(channel_id='UCshort'), 'current handle and stable channel', self.yt)

    def test_source_and_author_rules(self):
        cases = [
            (lambda b, r: r['items'][0].update(author='other'), 'observed X account'),
            (lambda b, r: r['items'][0].update(url='https://x.com/other/status/2001'), 'observed X account'),
            (lambda b, r: r['items'][0].update(url='https://x.com/river_example/status/2001?s=1'), 'observed X account'),
            (lambda b, r: r['items'][0].update(url='http://x.com/river_example/status/2001'), 'observed X account'),
            (lambda b, r: r['items'][0].update(source_id='9'), 'observed X account'),
            (lambda b, r: r['items'][0].update(channel_id=CHANNEL), 'observed X account'),
            (lambda b, r: r['items'][0].update(provider='youtube'), 'wrong platform'),
            (lambda b, r: r['items'][0].update(text=''), 'blank'),
            (lambda b, r: r['items'][0].update(text='  \n\t '), 'blank'),
            (lambda b, r: r['items'][0].update(text='x' * 16385), '16 KiB'),
            (lambda b, r: r['items'][0].update(text='bad\x00text'), 'cannot keep exactly'),
            (lambda b, r: r['items'][0].update(text='lone \ud800'), 'cannot keep exactly'),
            (lambda b, r: r['items'][0].update(date_unknown=True), 'unknown-date flag'),
            (lambda b, r: r['items'][0].update(date_iso='2099-01-01T00:00:00.000Z'), 'after the check'),
            (lambda b, r: r['items'][0].update(date_iso='2026-13-01T00:00:00.000Z'), 'valid UTC|real date'),
            (lambda b, r: r['items'][0].update(date_label='x' * 81), 'label'),
            (lambda b, r: r['items'][0].update(links_shortened='no'), 'true or false'),
            (lambda b, r: r['items'][0].pop('author'), 'exactly these fields'),
            (lambda b, r: r['items'][0].update(extra=1), 'exactly these fields'),
            (lambda b, r: r.update(items=[]), 'between 1 and 3'),
            (lambda b, r: r.update(items=[x_item(sid=str(n)) for n in range(1, 5)]), 'between 1 and 3'),
            (lambda b, r: r.update(items=[x_item(), x_item()]), 'repeat a source'),
        ]
        for mutate, pattern in cases:
            self.check(mutate, pattern)
        job = self.begin(self.x)
        big = [x_item('a' * 16384, str(n)) for n in range(1, 4)] + [x_item('b', '9')]
        self.refused(self.payload(job, row(self.x, 'ok', x_ok(big))), 'between 1 and 3', job)
        # Three 16 KiB items (48 KiB) stay under the 64 KiB total and are saved.
        self.assertEqual(self.commit(self.payload(job, row(self.x, 'ok', x_ok(big[:3]))))[0], 201)

    def test_youtube_source_rules(self):
        for mutate, pattern in [
                (lambda b, r: r['items'][0].update(url=f'https://www.youtube.com/watch?lc={COMMENT}&v={VIDEO}'), 'observed YouTube'),
                (lambda b, r: r['items'][0].update(url=f'https://www.youtube.com/watch?v={VIDEO}&lc={COMMENT}&pp=x'), 'observed YouTube'),
                (lambda b, r: r['items'][0].update(url=f'https://www.youtube.com/watch?v={VIDEO}'), 'observed YouTube'),
                (lambda b, r: r['items'][0].update(url=f'https://m.youtube.com/watch?v={VIDEO}&lc={COMMENT}'), 'observed YouTube'),
                (lambda b, r: r['items'][0].update(source_id='Other0000000'), 'observed YouTube'),
                (lambda b, r: r['items'][0].update(author='someone'), 'observed YouTube'),
                (lambda b, r: r['items'][0].update(channel_id=OTHER_CHANNEL), 'observed YouTube'),
                (lambda b, r: r['items'][0].update(channel_id=None), 'observed YouTube')]:
            self.check(mutate, pattern, self.yt)

    def test_coverage_rules(self):
        for mutate, pattern in [
                (lambda b, r: r['coverage'].update(accepted=2), 'Coverage counts'),
                (lambda b, r: r['coverage'].update(limit=4), 'Coverage counts'),
                (lambda b, r: r['coverage'].update(examined=65), 'Coverage counts'),
                (lambda b, r: r['coverage'].update(examined=0), 'Coverage counts'),
                (lambda b, r: r['coverage'].update(partial=False), 'partial flag'),
                (lambda b, r: r['coverage'].update(more_available='no'), 'Coverage counts'),
                (lambda b, r: r['coverage'].update(skipped=[{'reason': 'Bad Reason', 'count': 1}]), 'lowercase token'),
                (lambda b, r: r['coverage'].update(skipped=[{'reason': 'a', 'count': 0}]), 'out of range'),
                (lambda b, r: r['coverage'].update(notes=['dates_relative_or_unknown']), 'unknown dates'),
                (lambda b, r: r['coverage'].update(notes=['link_display_shortened']), 'shortened links'),
                (lambda b, r: r['coverage'].pop('notes'), 'exactly these fields')]:
            self.check(mutate, pattern)
        self.check(lambda b, r: r['coverage'].update(notes=['dates_relative_or_unknown']), 'history text was not used', self.yt)

    def test_duplicate_request_ids_and_oversized_messages(self):
        job = self.begin(self.x, self.yt)
        same = uuid.uuid4().hex
        body = self.payload(job, row(self.x, 'ok', x_ok(requestId=same)), row(self.yt, 'ok', yt_ok(requestId=same)))
        self.refused(body, 'own request ID', job)


class JobTests(Rig):
    def test_begin_validation_and_limits(self):
        for body, pattern in [({}, 'Required'), ({'account_ids': []}, 'between 1 and 20'), ({'account_ids': 'a'}, 'between 1 and 20'),
                              ({'account_ids': [self.x['id']] * 2}, 'only once'), ({'account_ids': ['missing']}, 'saved in Resonance'),
                              ({'account_ids': [1]}, 'identifier'), ({'account_ids': ['a'] * 21}, 'between 1 and 20'),
                              ({'account_ids': [self.x['id']], 'extra': 1}, 'Unexpected')]:
            with self.assertRaisesRegex(server.Invalid, pattern):
                self.post('/api/checks/begin', body)
        many = [self.account('Gmail', f'person{n}@example.com', '') for n in range(21)]
        self.assertEqual(self.post('/api/checks/begin', {'account_ids': [a['id'] for a in many[:20]]})[0], 201)
        with self.assertRaisesRegex(server.Invalid, 'between 1 and 20'):
            self.post('/api/checks/begin', {'account_ids': [a['id'] for a in many]})

    def test_expiry_capacity_and_restart(self):
        job = self.begin(self.x)
        later = datetime.now(timezone.utc) + timedelta(minutes=11)
        with patch.object(reader_checks, 'utcnow', lambda: later):
            self.refused(self.payload(job, row(self.x, 'ok', x_ok()), completed_at=later.isoformat()), 'expired')
            with self.assertRaises(LookupError):
                self.ws.read_check(job['check_id'], self.token)
        waiting = [self.begin(self.x) for _ in range(32)]  # The expired job was purged above.
        with self.assertRaisesRegex(server.Invalid, 'Too many checks'):
            self.post('/api/checks/begin', {'account_ids': [self.x['id']]})
        with patch.object(reader_checks, 'utcnow', lambda: later):  # Expired entries free their places.
            self.assertEqual(self.post('/api/checks/begin', {'account_ids': [self.x['id']]})[0], 201)
        self.assertEqual(len(waiting), 32)
        restarted = samples.Workspace(self.directory, server.Store)
        with self.assertRaises(LookupError):
            restarted.read_check(waiting[0]['check_id'], self.token)
        with self.assertRaisesRegex(server.Invalid, 'not waiting'):
            restarted.mutate('POST', '/api/checks', self.payload(waiting[0], row(self.x, 'ok', x_ok())), self.token)
        self.assertEqual(self.tables()['reader_checks'], 0)
        with self.assertRaisesRegex(samples.SampleError, 'workspace changed'):
            restarted.read_check(waiting[0]['check_id'], 'stale')

    def test_pending_is_not_persisted_exported_or_in_state(self):
        job = self.begin(self.x)
        store = server.Store(self.directory)
        self.assertNotIn(job['check_id'], json.dumps(store.backup()))
        self.assertNotIn(job['check_id'], json.dumps(self.ws.state()))
        with closing(sqlite3.connect(store.path)) as db:
            self.assertNotIn(job['check_id'], ''.join(line for line in db.iterdump()))

    def test_stale_account_snapshot_is_refused_for_each_identity_and_boundary_field(self):
        for changes in [{'handle': '@renamed_example'}, {'display_name': 'Renamed'}, {'url': 'https://x.com/renamed_example'},
                        {'daring': 'bold'}, {'identity_mode': 'named', 'sfw_only': True}, {'sfw_only': False}]:
            account = self.account('X', f'@snap_{len(changes)}{abs(hash(str(changes))) % 999}', '')
            job = self.begin(account)
            handle = account['handle'].lstrip('@')
            self.post('/api/accounts/' + account['id'], changes, 'PATCH')
            result = x_ok([x_item(handle=handle)], handle)
            self.refused(self.payload(job, row(account, 'ok', result)), 'changed while it was being read', job)

    def test_cancel_forgets_job_idempotently_and_late_results_are_refused(self):
        job = self.begin(self.x)
        for _ in range(2):
            self.assertEqual(self.post('/api/checks/cancel', {'check_id': job['check_id']}), (200, {'check_id': job['check_id'], 'pending': False}))
        with self.assertRaises(LookupError):
            self.ws.read_check(job['check_id'], self.token)
        self.refused(self.payload(job, row(self.x, 'ok', x_ok())), 'not waiting')
        self.assertEqual(self.tables()['reader_checks'], 0)
        with self.assertRaisesRegex(server.Invalid, 'Invalid check'):
            self.post('/api/checks/cancel', {'check_id': 'nope'})

    def test_samples_and_workspace_changes(self):
        job = self.begin(self.x)
        self.post('/api/samples', {})
        for call in (lambda: self.post('/api/checks/begin', {'account_ids': ['x']}), lambda: self.post('/api/checks/cancel', {'check_id': job['check_id']}),
                     lambda: self.post('/api/checks', {}), lambda: self.ws.read_check(job['check_id'], self.ws.context()['token']),
                     lambda: self.ws.read_corpus({'account_id': ['x']}, self.ws.context()['token'])):
            self.token = self.ws.context()['token']
            with self.assertRaises(samples.SampleError):
                call()
        self.post('/api/samples/clear', {})
        self.token = self.ws.context()['token']
        with self.assertRaises(LookupError):  # The pending job did not survive the workspace change.
            self.ws.read_check(job['check_id'], self.token)
        with self.assertRaisesRegex(server.Invalid, 'not waiting'):
            self.commit(self.payload(job, row(self.x, 'ok', x_ok())))


class IdempotencyTests(Rig):
    def test_replay_returns_original_receipt_even_after_pending_is_gone(self):
        job = self.begin(self.x)
        body = self.payload(job, row(self.x, 'ok', x_ok()))
        status, first = self.commit(body)
        with self.assertRaises(LookupError):  # Saved: the pending entry is gone but the receipt is readable.
            self.ws.pending_checks.jobs[job['check_id']]
        status, again = self.commit(copy.deepcopy(body))
        self.assertEqual((status, again['replayed'], again['receipt']), (200, True, first['receipt']))
        self.assertEqual(self.tables()['reader_checks'], 1)
        self.assertEqual(self.post('/api/checks/cancel', {'check_id': job['check_id']})[0], 200)  # No receipt change.
        self.assertEqual(self.ws.read_check(job['check_id'], self.token), first['receipt'])

    def test_same_id_with_altered_payload_is_refused(self):
        job = self.begin(self.x)
        body = self.payload(job, row(self.x, 'ok', x_ok()))
        self.commit(body)
        altered = copy.deepcopy(body)
        altered['accounts'][0]['result']['items'][0]['text'] += '!'
        before = self.tables()
        with self.assertRaisesRegex(server.Invalid, 'different content'):
            self.commit(altered)
        self.assertEqual(self.tables(), before)

    def test_receipts_and_revisions_cannot_be_changed_or_deleted(self):
        job, receipt = self.save((self.x, 'ok', x_ok()))
        with server.Store(self.directory).connect() as db:
            for statement in ("UPDATE reader_checks SET digest='x'", 'DELETE FROM reader_checks',
                              "UPDATE reader_revisions SET content_hash='x'", 'DELETE FROM reader_revisions'):
                with self.assertRaisesRegex(sqlite3.DatabaseError, 'immutable'):
                    db.execute(statement)


class AtomicityTests(Rig):
    def test_failure_after_writes_rolls_everything_back(self):
        job = self.begin(self.x, self.yt)
        body = self.payload(job, row(self.x, 'ok', x_ok([x_item(RUDE)])), row(self.yt, 'ok', yt_ok([yt_item(RUDE)])))
        before = self.tables()
        with patch.object(server.Store, 'backup_in', side_effect=sqlite3.OperationalError('disk full')):
            with self.assertRaises(sqlite3.OperationalError):
                self.commit(body)
        self.assertEqual(self.tables(), before)
        self.assertEqual(self.ws.read_check(job['check_id'], self.token)['status'], 'pending')
        self.assertEqual(self.commit(body)[0], 201)  # The same payload then saves normally.

    def test_one_invalid_account_saves_nothing_for_the_others(self):
        job = self.begin(self.x, self.yt)
        bad = yt_ok([yt_item(RUDE)], channel=CHANNEL)
        bad['items'][0]['channel_id'] = OTHER_CHANNEL
        self.refused(self.payload(job, row(self.x, 'ok', x_ok([x_item(RUDE)])), row(self.yt, 'ok', bad)), 'observed YouTube', job)

    def test_backup_capacity_refuses_atomically_and_never_evicts(self):
        self.save((self.x, 'ok', x_ok()))
        before = self.tables()
        job = self.begin(self.x)
        body = self.payload(job, row(self.x, 'ok', x_ok([x_item(RUDE, '3001')])))
        with patch.object(server, 'MAX_IMPORT_BODY', len(json.dumps(server.Store(self.directory).backup())) + reader_checks.BACKUP_MARGIN + 100):
            self.refused(body, 'cannot save more history', job)
        self.assertEqual(self.tables(), before)
        self.assertEqual(self.commit(body)[0], 201)

    def test_backup_with_the_new_history_stays_restorable_under_the_real_limit(self):
        self.save((self.x, 'ok', x_ok([x_item(RUDE)])))
        size = len(json.dumps(server.Store(self.directory).backup(), ensure_ascii=False).encode('utf-8'))
        self.assertLess(size, server.MAX_IMPORT_BODY - reader_checks.BACKUP_MARGIN)


class TextAndRevisionTests(Rig):
    def test_exact_whitespace_and_hash_are_preserved(self):
        text = '  leading\n\n  middle  line\t\n\ntrailing  \n'
        self.save((self.x, 'ok', x_ok([x_item(text)])))
        revision = self.sources(self.x)[0]['current_revision']
        self.assertEqual(revision['text'], text)
        self.assertEqual(revision['byte_length'], len(text.encode('utf-8')))
        self.assertEqual(revision['content_hash'], __import__('hashlib').sha256(text.encode('utf-8')).hexdigest())
        # Whitespace-only differences are real changes and append a revision.
        self.save((self.x, 'ok', x_ok([x_item(text.rstrip('\n'))])))
        source = self.sources(self.x)[0]
        self.assertEqual((source['revision_count'], source['current_revision']['sequence']), (2, 2))

    def test_unchanged_reread_reuses_revision_review_and_decisions(self):
        self.save((self.x, 'ok', x_ok([x_item(RUDE)])))
        state = self.ws.state()
        review = state['reviews'][0]
        flag = state['policy_flags'][0]
        self.post('/api/policy-flags/' + flag['id'], {'status': 'resolved'}, 'PATCH')  # Keep
        kept = self.ws.state()
        _, receipt = self.save((self.x, 'ok', x_ok([x_item(RUDE, handle='River_Example')], handle='River_Example')))
        account = receipt['accounts'][0]
        self.assertEqual((account['unchanged_source_count'], account['new_source_count'], account['new_review_count'],
                          account['finding_count']), (1, 0, 0, 1))
        after = self.ws.state()
        self.assertEqual(after['reviews'], kept['reviews'])
        self.assertEqual(after['reviews'][0]['id'], review['id'])
        self.assertEqual([f['status'] for f in after['policy_flags']], ['resolved'])
        self.assertEqual(self.tables()['reader_revisions'], 1)
        self.assertEqual(len(after['source_decisions']), len(kept['source_decisions']))

    def test_changed_text_appends_revision_and_old_evidence_and_actions_survive(self):
        self.save((self.x, 'ok', x_ok([x_item(RUDE)])))
        review = self.ws.state()['reviews'][0]
        request = self.post('/api/action-requests', {'review_id': review['id'], 'action': 'delete'})[1]['action_request']
        self.save((self.x, 'ok', x_ok([x_item(CLEAN)])))          # Newly clean text.
        source = self.sources(self.x)[0]
        self.assertEqual((source['revision_count'], source['current_revision']['text']), (2, CLEAN))
        history = self.ws.read_corpus({}, self.token, source['id'])['revisions']
        self.assertEqual([item['text'] for item in history], [CLEAN, RUDE])
        state = self.ws.state()
        self.assertEqual([r['excerpt'] for r in state['reviews']], [RUDE])   # Old evidence unchanged; no review for clean text.
        outstanding = next(r for r in state['action_requests'] if r['id'] == request['id'])
        self.assertEqual((outstanding['status'], outstanding['execution_state']), (request['status'], request['execution_state']))
        self.assertEqual(state['attestations'], [])
        self.save((self.x, 'ok', x_ok([x_item(RUDE)])))           # Back to the first text: the revision is reused.
        self.assertEqual(self.tables()['reader_revisions'], 2)
        self.assertEqual(self.sources(self.x)[0]['current_revision']['sequence'], 1)
        self.assertEqual(self.tables()['reviews'], 1)

    def test_known_date_and_original_label_are_preserved(self):
        self.save((self.x, 'ok', x_ok([x_item(date_iso=None, date_unknown=True, date_label='3h')])))
        self.assertEqual(self.sources(self.x)[0]['date_label'], '3h')
        self.save((self.x, 'ok', x_ok([x_item(date_iso=None, date_unknown=True, date_label='4h')])))
        self.assertEqual(self.sources(self.x)[0]['date_label'], '3h')
        self.save((self.x, 'ok', x_ok([x_item()])))
        self.save((self.x, 'ok', x_ok([x_item(date_iso=None, date_unknown=True, date_label='5h')])))
        source = self.sources(self.x)[0]
        self.assertEqual((source['date_iso'], source['date_unknown']), ('2026-10-30T12:00:00.000Z', False))
        self.assertLessEqual(source['first_seen_at'], source['last_seen_at'])

    def test_boundary_change_makes_a_previously_clean_text_flag_through_existing_fingerprints(self):
        loud = 'Nobody ever listens!!!'
        careful = self.account('X', '@careful_example', 'https://x.com/careful_example', daring='bold')
        self.save((careful, 'ok', x_ok([x_item(loud, handle='careful_example')], 'careful_example')))
        self.assertEqual(self.tables()['reviews'], 0)
        self.post('/api/accounts/' + careful['id'], {'daring': 'careful'}, 'PATCH')
        self.save((careful, 'ok', x_ok([x_item(loud, handle='careful_example')], 'careful_example')))
        self.assertEqual(self.tables()['reviews'], 1)
        self.assertEqual(self.tables()['reader_revisions'], 1)


class ReadTests(Rig):
    def seed(self, count):
        for number in range(count):
            self.save((self.x, 'ok', x_ok([x_item(f'Note {number}', str(5000 + number))])))

    def test_corpus_pagination_is_bounded_validated_and_account_scoped(self):
        self.seed(5)
        seen, cursor = [], None
        while True:
            query = {'account_id': [self.x['id']], 'limit': ['2']}
            if cursor:
                query['cursor'] = [cursor]
            page = self.ws.read_corpus(query, self.token)
            self.assertLessEqual(len(page['sources']), 2)
            seen += [source['url'] for source in page['sources']]
            cursor = page['next_cursor']
            if not cursor:
                break
        self.assertEqual(len(seen), 5)
        self.assertEqual(len(set(seen)), 5)
        self.assertEqual(len(self.ws.read_corpus({'account_id': [self.x['id']]}, self.token)['sources']), 5)
        first = self.ws.read_corpus({'account_id': [self.x['id']], 'limit': ['2']}, self.token)['next_cursor']
        for query, pattern in [({'account_id': [self.yt['id']], 'cursor': [first]}, 'cursor'), ({'account_id': [self.x['id']], 'cursor': [first[:-3] + 'AAA']}, 'cursor'),
                               ({'account_id': [self.x['id']], 'cursor': ['!!']}, 'cursor'), ({'account_id': [self.x['id']], 'limit': ['51']}, 'limit'),
                               ({'account_id': [self.x['id']], 'limit': ['0']}, 'limit'), ({'account_id': [self.x['id']], 'limit': ['2', '3']}, 'only once'),
                               ({'account_id': [self.x['id']], 'limit': ['-1']}, 'limit'), ({}, 'account_id'), ({'account_id': [self.x['id']], 'order': ['x']}, 'Unknown')]:
            with self.assertRaisesRegex(server.Invalid, pattern):
                self.ws.read_corpus(query, self.token)
        with self.assertRaises(LookupError):
            self.ws.read_corpus({'account_id': ['missing']}, self.token)
        with self.assertRaisesRegex(samples.SampleError, 'workspace changed'):
            self.ws.read_corpus({'account_id': [self.x['id']]}, 'stale')

    def test_revision_history_is_paginated_on_its_own_route(self):
        for number in range(4):
            self.save((self.x, 'ok', x_ok([x_item(f'Version {number}')])))
        source = self.sources(self.x)[0]
        page = self.ws.read_corpus({'limit': ['3']}, self.token, source['id'])
        self.assertEqual([r['sequence'] for r in page['revisions']], [4, 3, 2])
        rest = self.ws.read_corpus({'limit': ['3'], 'cursor': [page['next_cursor']]}, self.token, source['id'])
        self.assertEqual(([r['sequence'] for r in rest['revisions']], rest['next_cursor']), ([1], None))
        with self.assertRaises(LookupError):
            self.ws.read_corpus({}, self.token, 'rs_missing')

    def test_state_carries_only_twenty_concise_summaries_and_no_corpus(self):
        self.seed(22)
        state = self.ws.state()
        summary = state['reader_check_summary']
        self.assertEqual((summary['count'], len(summary['recent']), summary['source_count']), (22, 20, 22))
        self.assertEqual(summary['recent'][0]['id'], max(summary['recent'], key=lambda s: s['completed_at'])['id'])
        encoded = json.dumps(state)
        self.assertNotIn('Note 3', encoded)
        self.assertNotIn('reader_sources', state)
        self.assertNotIn('current_revision', encoded)
        self.assertLess(len(json.dumps(summary)), 40000)

    def test_unknown_check_is_not_found(self):
        with self.assertRaises(LookupError):
            self.ws.read_check(uuid.uuid4().hex, self.token)
        with self.assertRaises(LookupError):
            self.ws.read_check('not-an-id', self.token)


class BackupTests(Rig):
    def history(self):
        self.save((self.x, 'ok', x_ok([x_item(RUDE), x_item(CLEAN, '2002')])), (self.yt, 'ok', yt_ok()))
        self.save((self.x, 'ok', x_ok([x_item(CLEAN + ' edited')])), (self.fb, 'unsupported', {'reason': 'provider_not_supported'}))
        return server.Store(self.directory).backup()

    def fresh(self):
        folder = Path(self.temp.name) / ('other' + uuid.uuid4().hex[:6])
        return server.Store(folder)

    def table_rows(self, store, name):
        with store.connect() as db:
            return [json.loads(r['data']) for r in db.execute(f'SELECT data FROM {name} ORDER BY id')]

    def test_format_three_round_trips_with_historical_receipts(self):
        backup = self.history()
        self.assertEqual(backup['schema_version'], 3)
        self.assertEqual((len(backup['reader_checks']), len(backup['reader_sources']), len(backup['reader_revisions'])), (2, 3, 4))
        self.assertNotIn('reader_check_summary', backup)
        other = self.fresh()
        self.assertEqual(other.mutate('POST', '/api/import', copy.deepcopy(backup))[0], 200)
        for name in ('reader_sources', 'reader_revisions'):
            self.assertEqual(self.table_rows(other, name), self.table_rows(server.Store(self.directory), name))
        restored = other.backup()
        self.assertEqual({r['origin'] for r in restored['reader_checks']}, {'historical'})
        self.assertEqual([r['receipt_hash'] for r in restored['reader_checks']], [r['receipt_hash'] for r in backup['reader_checks']])
        again = other.mutate('POST', '/api/import', copy.deepcopy(restored))
        self.assertEqual(again[0], 200)
        self.assertEqual(other.backup(), restored)  # Idempotent.
        original = server.Store(self.directory)
        original.mutate('POST', '/api/import', copy.deepcopy(backup))
        self.assertEqual({r['origin'] for r in original.backup()['reader_checks']}, {'live'})  # Never downgraded.

    def test_older_formats_import_with_new_fields_empty_and_new_fields_need_format_three(self):
        backup = self.history()
        old = copy.deepcopy(backup)
        for key in ('reader_checks', 'reader_sources', 'reader_revisions'):
            old.pop(key)
        for version in (1, 2):
            other = self.fresh()
            self.assertEqual(other.mutate('POST', '/api/import', dict(old, schema_version=version))[0], 200)
            self.assertEqual(other.backup()['reader_checks'], [])
        with self.assertRaisesRegex(server.Invalid, 'format 3'):
            self.fresh().mutate('POST', '/api/import', dict(backup, schema_version=2))
        self.assertEqual(self.fresh().mutate('POST', '/api/import', {k: v for k, v in backup.items() if not k.startswith('reader_')})[0], 200)

    def test_every_tampered_record_is_refused_before_any_mutation(self):
        backup = self.history()
        mutations = {
            'revision text': lambda b: b['reader_revisions'][0].update(text='changed'),
            'revision hash': lambda b: b['reader_revisions'][0].update(content_hash='0' * 64),
            'revision source': lambda b: b['reader_revisions'][0].update(source_id='rs_unknown'),
            'revision id': lambda b: b['reader_revisions'][0].update(id='rv_' + 'f' * 24),
            'revision extra': lambda b: b['reader_revisions'][0].update(extra=1),
            'source pointer': lambda b: b['reader_sources'][0].update(current_revision_id='rv_' + 'f' * 24),
            'source key': lambda b: b['reader_sources'][0].update(source_key='https://x.com/x/status/1', url='https://x.com/x/status/1'),
            'source account': lambda b: b['reader_sources'][0].update(account_id='missing'),
            'source id list': lambda b: b['reader_sources'][0].update(id=['x']),
            'source version': lambda b: b['reader_sources'][0].update(reader_version='9.9.9'),
            'receipt hash': lambda b: b['reader_checks'][0].update(receipt_hash='0' * 64),
            'receipt count': lambda b: b['reader_checks'][0]['accounts'][0].update(finding_count=99),
            'receipt statement': lambda b: b['reader_checks'][0].update(statement='Cryptographically attested.'),
            'receipt source ref': lambda b: b['reader_checks'][0]['accounts'][0]['source_ids'].__setitem__(0, 'rs_unknown'),
            'receipt account': lambda b: b['reader_checks'][0]['accounts'][0].update(account_id='missing'),
            'receipt origin': lambda b: b['reader_checks'][0].update(origin='forged'),
            'receipt list': lambda b: b.update(reader_checks={}),
            'duplicate revision': lambda b: b['reader_revisions'].append(copy.deepcopy(b['reader_revisions'][0])),
            'duplicate receipt': lambda b: b['reader_checks'].append(copy.deepcopy(b['reader_checks'][0])),
        }
        for name, mutate in mutations.items():
            other = self.fresh()
            body = copy.deepcopy(backup)
            mutate(body)
            before = other.backup()
            with self.subTest(name):
                with self.assertRaises(server.Invalid):
                    other.mutate('POST', '/api/import', body)
                self.assertEqual(other.backup(), before)

    def test_a_different_receipt_for_a_saved_check_id_is_refused(self):
        backup = self.history()
        body = copy.deepcopy(backup)
        receipt = body['reader_checks'][0]
        receipt['digest'] = '0' * 64
        receipt['receipt_hash'] = reader_checks.sha256_hex(workflow.canonical_json(
            {key: value for key, value in receipt.items() if key not in ('origin', 'receipt_hash')}))
        with self.assertRaisesRegex(server.Invalid, 'receipt already uses'):
            server.Store(self.directory).mutate('POST', '/api/import', body)

    def test_imported_receipts_cannot_authorize_or_be_replayed_as_live_work(self):
        job = self.begin(self.x)
        body = self.payload(job, row(self.x, 'ok', x_ok()))
        self.commit(body)
        backup = server.Store(self.directory).backup()
        other_dir = Path(self.temp.name) / 'restored'
        other = samples.Workspace(other_dir, server.Store)
        token = other.context()['token']
        other.mutate('POST', '/api/import', backup, token)
        with self.assertRaisesRegex(server.Invalid, 'historical receipt'):
            other.mutate('POST', '/api/checks', copy.deepcopy(body), token)
        self.assertEqual(other.read_check(job['check_id'], token)['origin'], 'historical')
        with self.assertRaisesRegex(server.Invalid, 'saved in Resonance|not waiting'):
            other.mutate('POST', '/api/checks', dict(body, check_id=uuid.uuid4().hex), token)

    def test_existing_backups_without_reader_history_still_round_trip(self):
        store = server.Store(Path(self.temp.name) / 'plain')
        store.mutate('POST', '/api/accounts', {'platform': 'X', 'handle': '@plain_example', 'url': 'https://x.com/plain_example'})
        backup = store.backup()
        self.assertEqual((backup['reader_checks'], backup['reader_sources'], backup['reader_revisions']), ([], [], []))
        self.assertEqual(self.fresh().mutate('POST', '/api/import', backup)[0], 200)


def reseal(backup):
    """Recompute every receipt seal, so a forgery is stopped by semantic validation and not by a stale hash."""
    for receipt in backup['reader_checks']:
        reader_checks.seal(receipt)
    return backup


class ImportValidationTests(Rig):
    def history(self):
        self.save((self.x, 'ok', x_ok([x_item(RUDE), x_item(CLEAN, '2002')])), (self.yt, 'ok', yt_ok()))
        self.save((self.x, 'ok', x_ok([x_item(CLEAN + ' edited')])), (self.fb, 'unsupported', {'reason': 'provider_not_supported'}))
        return server.Store(self.directory).backup()

    def fresh(self):
        return server.Store(Path(self.temp.name) / ('other' + uuid.uuid4().hex[:6]))

    def rows(self, store, name):
        with store.connect() as db:
            return [json.loads(r['data']) for r in db.execute(f'SELECT data FROM {name} ORDER BY id')]

    def reader_state(self, store):
        return {name: self.rows(store, name) for name in ('reader_checks', 'reader_sources', 'reader_revisions')}

    def refuses(self, backup, store=None):
        store = store or self.fresh()
        before = store.backup()
        with self.assertRaises(server.Invalid) as caught:
            store.mutate('POST', '/api/import', backup)
        self.assertNotIn('recorded hash', str(caught.exception))  # Refused on meaning, not on a stale seal.
        self.assertEqual(store.backup(), before)
        return str(caught.exception)

    def test_resealed_negative_coverage_is_refused_on_meaning(self):
        self.save((self.x, 'ok', x_ok()))
        backup = server.Store(self.directory).backup()
        backup['reader_checks'][0]['accounts'][0]['coverage']['accepted'] = -10
        stale = copy.deepcopy(backup)
        with self.assertRaisesRegex(server.Invalid, 'recorded hash'):
            self.fresh().mutate('POST', '/api/import', stale)  # An unrecomputed edit is the accidental case.
        reseal(backup)
        self.assertRegex(self.refuses(backup), 'Coverage counts')
        untouched = self.fresh()
        self.assertEqual(untouched.mutate('POST', '/api/import', server.Store(self.directory).backup())[0], 200)

    def test_every_resealed_forgery_is_refused_before_any_write(self):
        backup = self.history()
        x_source = next(s for s in backup['reader_sources'] if s['source_key'].endswith('/2001'))
        yt_source = next(s for s in backup['reader_sources'] if s['kind'] == 'comment')
        x_two = next(s for s in backup['reader_sources'] if s['source_key'].endswith('/2002'))
        yt_revision = next(r for r in backup['reader_revisions'] if r['source_id'] == yt_source['id'])

        def row(b, i=0, j=0):
            return b['reader_checks'][i]['accounts'][j]

        def cov(b, i=0, j=0):
            return row(b, i, j)['coverage']

        def source(b, picked):
            return next(s for s in b['reader_sources'] if s['id'] == picked['id'])

        def revisions(b, picked):
            return sorted((r for r in b['reader_revisions'] if r['source_id'] == picked['id']), key=lambda r: r['sequence'])

        def orphan_revision(b):
            record = copy.deepcopy(revisions(b, x_two)[0])
            record.update(text='extra words', sequence=2)
            record.update(content_hash=reader_checks.sha256_hex('extra words'), byte_length=len('extra words'))
            record['id'] = 'rv_' + workflow.digest([record['source_id'], record['content_hash']])[:24]
            b['reader_revisions'].append(record)

        def swap_revisions(b):
            ids = row(b)['revision_ids']
            ids.reverse()

        def cross_account(b):
            row(b)['source_ids'][0] = yt_source['id']
            row(b)['revision_ids'][0] = yt_revision['id']

        forgeries = {
            'accepted negative': lambda b: cov(b).update(accepted=-10),
            'accepted over count': lambda b: cov(b).update(accepted=3),
            'examined below accepted': lambda b: cov(b).update(examined=0),
            'examined float': lambda b: cov(b).update(examined=2.0),
            'partial flipped': lambda b: cov(b).update(partial=not cov(b)['partial']),
            'limit': lambda b: cov(b).update(limit=5),
            'skipped unsorted': lambda b: cov(b).update(skipped=[{'reason': 'z', 'count': 1}, {'reason': 'a', 'count': 1}], examined=4),
            'youtube history note dropped': lambda b: cov(b, 0, 1).update(notes=['dates_relative_or_unknown']),
            'unknown dates note dropped': lambda b: cov(b, 0, 1).update(notes=['activity_text_not_used']),
            'shortened note invented': lambda b: cov(b).update(notes=['link_display_shortened']),
            'count sum': lambda b: row(b).update(unchanged_source_count=1),
            'new count contradicts revisions': lambda b: row(b).update(new_source_count=0, changed_source_count=2),
            'flagged over sources': lambda b: row(b).update(flagged_source_count=3),
            'new reviews over flagged': lambda b: row(b).update(new_review_count=2),
            'findings vanish': lambda b: row(b).update(finding_count=0),
            'negative counter': lambda b: row(b).update(finding_count=-1),
            'float counter': lambda b: row(b).update(source_count=2.0),
            'bool counter': lambda b: row(b).update(flagged_source_count=True),
            'huge counter': lambda b: row(b).update(finding_count=10 ** 9),
            'identity handle': lambda b: row(b).update(identity={'handle': 'someone_else', 'channel_id': None}),
            'identity channel on x': lambda b: row(b).update(identity={'handle': 'river_example', 'channel_id': CHANNEL}),
            'identity channel': lambda b: row(b, 0, 1).update(identity={'handle': 'kelp_example', 'channel_id': OTHER_CHANNEL}),
            'identity missing': lambda b: row(b).update(identity=None),
            'saved handle contradicts observation': lambda b: row(b).update(handle='@someone_else'),
            'platform unknown': lambda b: row(b).update(platform='Nope'),
            'platform swapped': lambda b: row(b).update(platform='YouTube'),
            'handle not text': lambda b: row(b).update(handle=5),
            'ok turned held': lambda b: row(b).update(status='held'),
            'ok with reason': lambda b: row(b).update(reason='sign_in_needed'),
            'ok without request': lambda b: row(b).update(request_id=None),
            'malformed request': lambda b: row(b).update(request_id='XYZ'),
            'duplicate request in receipt': lambda b: row(b, 0, 1).update(request_id=row(b)['request_id']),
            'duplicate request across receipts': lambda b: row(b, 1, 0).update(request_id=row(b)['request_id']),
            'unsupported reason': lambda b: row(b, 1, 1).update(reason='other'),
            'unsupported request': lambda b: row(b, 1, 1).update(request_id='a' * 32),
            'unsupported identity': lambda b: row(b, 1, 1).update(identity={'handle': 'x', 'channel_id': None}),
            'unsupported coverage': lambda b: cov(b, 1, 1).update(accepted=1),
            'unsupported sources': lambda b: row(b, 1, 1).update(source_count=1, source_ids=[x_source['id']], revision_ids=['rv_x']),
            'unsupported gaps': lambda b: row(b, 1, 1).update(gaps=[]),
            'unsupported diagnostic': lambda b: row(b, 1, 1).update(diagnostic={'document_ready': 'complete', 'visibility': 'visible', 'avatar': True, 'editing_link': True, 'content_link': True, 'channels': 1}),
            'unreadable platform held': lambda b: row(b, 1, 1).update(status='held'),
            'gaps': lambda b: row(b).update(gaps=['invented']),
            'totals': lambda b: b['reader_checks'][0]['totals'].update(sources=99),
            'created after completed': lambda b: b['reader_checks'][0].update(created_at='2099-01-01T00:00:00Z'),
            'committed before completed': lambda b: b['reader_checks'][0].update(committed_at='2000-01-01T00:00:00Z'),
            'impossible receipt date': lambda b: b['reader_checks'][0].update(completed_at='2026-13-40T00:00:00Z'),
            'receipt time to the millisecond': lambda b: b['reader_checks'][0].update(completed_at='2026-10-30T12:00:00.500Z'),
            'receipt version': lambda b: b['reader_checks'][0].update(reader_version='9.9.9'),
            'row source unknown': lambda b: row(b)['source_ids'].__setitem__(0, 'rs_unknown'),
            'row revision unknown': lambda b: row(b)['revision_ids'].__setitem__(0, 'rv_unknown'),
            'row pairs mismatch': swap_revisions,
            'row from another account': cross_account,
            'row repeats a source': lambda b: row(b)['source_ids'].__setitem__(1, row(b)['source_ids'][0]),
            'receipt removed leaves orphans': lambda b: b['reader_checks'].pop(0),
            'revision not reported': orphan_revision,
            'revision check unknown': lambda b: revisions(b, x_source)[0].update(check_id='f' * 32),
            'revision check does not report it': lambda b: revisions(b, x_two)[0].update(check_id=b['reader_checks'][1]['id']),
            'revision time': lambda b: revisions(b, x_source)[0].update(observed_at='2026-01-01T00:00:00Z'),
            'revision version': lambda b: revisions(b, x_source)[0].update(reader_version='0.0.6'),
            'revision sequence gap': lambda b: revisions(b, x_source)[1].update(sequence=3),
            'source last check unknown': lambda b: source(b, x_source).update(last_check_id='f' * 32),
            'source last check does not report it': lambda b: source(b, x_two).update(last_check_id=b['reader_checks'][1]['id']),
            'source pointer disagrees with last check': lambda b: source(b, x_source).update(current_revision_id=revisions(b, x_source)[0]['id']),
            'source version': lambda b: source(b, x_source).update(reader_version='0.0.6'),
            'source first after last': lambda b: source(b, x_source).update(first_seen_at='2099-01-01T00:00:00Z'),
            'source first unreported': lambda b: source(b, x_source).update(first_seen_at='2000-01-01T00:00:00Z'),
            'source last unreported': lambda b: source(b, x_source).update(last_seen_at='2099-01-01T00:00:00Z'),
            'source date impossible': lambda b: source(b, x_two).update(date_iso='2026-02-31T00:00:00.000Z'),
            'source dated after seen': lambda b: source(b, x_two).update(date_iso='2099-01-01T00:00:00.000Z'),
            'channel author disagrees with last check': lambda b: source(b, yt_source).update(author='other_name'),
            'channel id disagrees with last check': lambda b: source(b, yt_source).update(channel_id=OTHER_CHANNEL),
        }
        for name, forge in forgeries.items():
            body = copy.deepcopy(backup)
            forge(body)
            reseal(body)
            with self.subTest(name):
                self.refuses(body)

    def test_a_forged_graph_is_also_refused_when_merged_into_an_existing_history(self):
        backup = self.history()
        target = server.Store(self.directory)
        before = self.reader_state(target)
        body = copy.deepcopy(backup)
        body['reader_checks'][0]['accounts'][0]['coverage']['accepted'] = -1
        reseal(body)
        self.refuses(body, target)
        self.assertEqual(self.reader_state(target), before)

    def test_generated_histories_round_trip_including_reread_revert_and_later_changes(self):
        self.save((self.x, 'ok', x_ok([x_item(CLEAN), x_item(RUDE, '2002')])))
        self.save((self.x, 'ok', x_ok([x_item(CLEAN + ' edited'), x_item(RUDE, '2002')])))
        self.save((self.x, 'ok', x_ok([x_item(CLEAN)])))  # A -> B -> A reuses the first revision.
        self.save((self.x, 'ok', x_ok([x_item(CLEAN)])))  # An unchanged reread.
        self.save((self.yt, 'ok', yt_ok()))
        known = dict(date_iso='2026-10-30T12:00:00.000Z', date_unknown=False, date_label='Oct 30')
        self.save((self.yt, 'ok', yt_ok([yt_item(CLEAN, links_shortened=True, **known)])))  # Date learned, older text reread.
        self.save((self.yt, 'ok', yt_ok([yt_item(CLEAN)])))  # A later reread with the date unknown again.
        self.save((self.yt, 'ok', yt_ok([yt_item(CLEAN + ' links https://x.example/…', links_shortened=True)])))
        self.save((self.yt, 'held', not_ok('youtube', 'held', 'ownership_not_confirmed', diagnostic={
            'document_ready': 'complete', 'visibility': 'visible', 'avatar': True, 'editing_link': False, 'content_link': True, 'channels': 1})),
                  (self.fb, 'unsupported', {'reason': 'provider_not_supported'}), (self.x, 'error', not_ok('x', 'error', 'read_failed')))
        self.post(f'/api/accounts/{self.x["id"]}', {'handle': '@river_renamed'}, method='PATCH')  # Identity moved on since.
        original = server.Store(self.directory)
        backup = original.backup()
        first = self.fresh()
        self.assertEqual(first.mutate('POST', '/api/import', copy.deepcopy(backup))[0], 200)
        again = first.backup()
        self.assertEqual(first.mutate('POST', '/api/import', copy.deepcopy(again))[0], 200)  # B reread into itself.
        self.assertEqual(first.backup(), again)
        self.assertEqual(original.mutate('POST', '/api/import', copy.deepcopy(again))[0], 200)  # A <- B's export.
        self.assertEqual({r['origin'] for r in original.backup()['reader_checks']}, {'live'})
        third = self.fresh()
        self.assertEqual(third.mutate('POST', '/api/import', copy.deepcopy(again))[0], 200)
        for store in (first, original, third):
            for name in ('reader_sources', 'reader_revisions'):
                self.assertEqual(self.rows(store, name), self.rows(server.Store(self.directory), name))
            self.assertEqual([r['receipt_hash'] for r in store.backup()['reader_checks']],
                             [r['receipt_hash'] for r in backup['reader_checks']])

    def test_older_and_newer_exports_merge_without_losing_history(self):
        old = self.history()
        server_store = server.Store(self.directory)
        time.sleep(1.1)  # Sightings are ordered to the second; a same-second tie keeps what the store already holds.
        self.save((self.x, 'ok', x_ok([x_item(CLEAN + ' edited again'), x_item(CLEAN, '2002')])))
        new = server_store.backup()
        before = self.reader_state(server_store)
        self.assertEqual(server_store.mutate('POST', '/api/import', copy.deepcopy(old))[0], 200)  # Older into newer.
        self.assertEqual(self.reader_state(server_store), before)
        restored = self.fresh()
        restored.mutate('POST', '/api/import', copy.deepcopy(old))
        self.assertEqual(restored.mutate('POST', '/api/import', copy.deepcopy(new))[0], 200)  # Newer into older.
        for name in ('reader_sources', 'reader_revisions'):
            self.assertEqual(self.rows(restored, name), before[name])
        self.assertEqual({r['id'] for r in restored.backup()['reader_checks']}, {r['id'] for r in new['reader_checks']})

    def test_a_newer_sighting_keeps_the_earliest_first_seen_and_any_known_date(self):
        old = {'first_seen_at': '2026-10-01T00:00:00Z', 'last_seen_at': '2026-10-02T00:00:00Z', 'current_revision_id': 'rv_a',
               'date_iso': '2026-09-30T00:00:00.000Z', 'date_label': 'Sep 30', 'date_unknown': False}
        new = dict(old, first_seen_at='2026-10-03T00:00:00Z', last_seen_at='2026-10-04T00:00:00Z', current_revision_id='rv_b',
                   date_iso=None, date_label='yesterday', date_unknown=True)
        merged = reader_checks.merged_source(old, new)
        self.assertEqual((merged['current_revision_id'], merged['last_seen_at']), ('rv_b', '2026-10-04T00:00:00Z'))
        self.assertEqual((merged['first_seen_at'], merged['date_iso'], merged['date_unknown']), ('2026-10-01T00:00:00Z', old['date_iso'], False))
        self.assertIs(reader_checks.merged_source(new, old), new)  # An older sighting changes nothing.

    def test_the_new_reader_version_saves_and_the_historical_one_restores_only(self):
        job = self.begin(self.x)
        with self.assertRaisesRegex(server.Invalid, 'reader version 0.1.0'):
            self.commit(self.payload(job, row(self.x, 'ok', x_ok()), reader_version='0.0.6'))
        self.assertEqual(self.commit(self.payload(job, row(self.x, 'ok', x_ok())))[0], 201)
        backup = server.Store(self.directory).backup()
        self.assertEqual({r['reader_version'] for r in backup['reader_checks']}, {'0.1.0'})
        for name in ('reader_checks', 'reader_sources', 'reader_revisions'):
            for record in backup[name]:
                record['reader_version'] = '0.0.6'
        reseal(backup)
        other = self.fresh()
        self.assertEqual(other.mutate('POST', '/api/import', backup)[0], 200)
        self.assertEqual({r['reader_version'] for r in other.backup()['reader_checks']}, {'0.0.6'})

    def second_history(self):
        folder = Path(self.temp.name) / 'second'
        workspace = samples.Workspace(folder, server.Store)
        token = workspace.context()['token']
        account = workspace.mutate('POST', '/api/accounts', {'platform': 'X', 'handle': '@second_example', 'url': 'https://x.com/second_example',
                                                             'display_name': 'Second', 'identity_mode': 'alias', 'daring': 'balanced',
                                                             'sfw_only': True}, token)[1]['account']
        job = workspace.mutate('POST', '/api/checks/begin', {'account_ids': [account['id']]}, token)[1]
        body = {'check_id': job['check_id'], 'reader_version': '0.1.0', 'completed_at': stamp(),
                'accounts': [row(account, 'ok', x_ok([x_item(RUDE, '7001', handle='second_example')], handle='second_example'))]}
        self.assertEqual(workspace.mutate('POST', '/api/checks', body, token)[0], 201)
        return folder

    def test_merge_import_cannot_make_an_unrestorable_backup_and_rolls_back_whole(self):
        self.history()
        target = server.Store(self.directory)
        source_folder = self.second_history()
        incoming = server.Store(source_folder).backup()
        tables = ('reader_checks', 'reader_sources', 'reader_revisions', 'accounts', 'reviews', 'flag_evidence')

        def counts(folder):
            with server.Store(folder).connect() as db:
                return {name: [r[0] for r in db.execute(f'SELECT data FROM {name} ORDER BY rowid')] if name != 'flag_evidence'
                        else db.execute('SELECT count(*) FROM flag_evidence').fetchone()[0] for name in tables}

        old_before, new_before = counts(self.directory), counts(source_folder)
        old_backup = target.backup()
        size = len(json.dumps(old_backup, ensure_ascii=False).encode('utf-8'))
        with patch.object(server, 'MAX_IMPORT_BODY', size + reader_checks.BACKUP_MARGIN + 50):
            with self.assertRaisesRegex(server.Invalid, 'too large to restore'):
                target.mutate('POST', '/api/import', copy.deepcopy(incoming))
        self.assertEqual(counts(self.directory), old_before)
        self.assertEqual(counts(source_folder), new_before)
        self.assertEqual(target.backup(), old_backup)
        self.assertEqual(target.mutate('POST', '/api/import', copy.deepcopy(incoming))[0], 200)  # Fine under the real limit.
        merged = target.backup()
        self.assertLess(len(json.dumps(merged, ensure_ascii=False).encode('utf-8')), server.MAX_IMPORT_BODY - reader_checks.BACKUP_MARGIN)
        self.assertEqual(len(merged['reader_sources']), len(old_backup['reader_sources']) + len(incoming['reader_sources']))

    def test_imports_without_reader_history_do_not_pay_for_the_capacity_check(self):
        self.save((self.x, 'ok', x_ok()))
        plain = server.Store(Path(self.temp.name) / 'plain')
        backup = plain.backup()
        with patch.object(reader_checks, 'ensure_capacity', side_effect=AssertionError('not needed')):
            self.assertEqual(self.fresh().mutate('POST', '/api/import', backup)[0], 200)


class HttpTests(Rig):
    def setUp(self):
        super().setUp()
        self.app = server.make_server(Path(self.temp.name) / 'http', port=0)
        self.worker = threading.Thread(target=self.app.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.worker.join)
        self.addCleanup(self.app.server_close)
        self.addCleanup(self.app.shutdown)
        self.port = self.app.server_address[1]

    def call(self, method, path, body=None, origin=True, token=True):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['X-Resonance-Workspace'] = self.app.store.context()['token']
        if origin and method != 'GET':
            headers['Origin'] = f'http://127.0.0.1:{self.port}'
        connection.request(method, path, None if body is None else json.dumps(body).encode(), headers)
        response = connection.getresponse()
        result = json.loads(response.read())
        connection.close()
        return response.status, result

    def test_routes_enforce_same_origin_workspace_and_end_to_end_flow(self):
        account = self.call('POST', '/api/accounts', {'platform': 'X', 'handle': '@river_example', 'url': 'https://x.com/river_example',
                                                      'identity_mode': 'alias', 'daring': 'balanced', 'sfw_only': True})[1]['account']
        self.assertEqual(self.call('POST', '/api/checks/begin', {'account_ids': [account['id']]}, origin=False)[0], 403)
        self.assertEqual(self.call('POST', '/api/checks/begin', {'account_ids': [account['id']]}, token=False)[0], 400)
        status, job = self.call('POST', '/api/checks/begin', {'account_ids': [account['id']]})
        self.assertEqual(status, 201)
        self.assertEqual(self.call('GET', '/api/checks/' + job['check_id'])[1]['status'], 'pending')
        self.assertEqual(self.call('GET', '/api/checks/' + job['check_id'], token=False)[0], 400)
        body = {'check_id': job['check_id'], 'reader_version': '0.1.0', 'completed_at': stamp(),
                'accounts': [row(account, 'ok', x_ok([x_item(RUDE)]))]}
        self.assertEqual(self.call('POST', '/api/checks', body, origin=False)[0], 403)
        self.assertEqual(self.call('POST', '/api/checks', dict(body, reader_version='0.0.4'))[0], 400)
        status, saved = self.call('POST', '/api/checks', body)
        self.assertEqual(status, 201)
        self.assertEqual(self.call('POST', '/api/checks', body)[0], 200)
        self.assertEqual(self.call('GET', '/api/checks/' + job['check_id'])[1], saved['receipt'])
        self.assertEqual(self.call('GET', '/api/checks/' + uuid.uuid4().hex)[0], 404)
        status, page = self.call('GET', '/api/corpus?account_id=' + account['id'] + '&limit=1')
        self.assertEqual((status, len(page['sources'])), (200, 1))
        self.assertEqual(self.call('GET', '/api/corpus?account_id=' + account['id'] + '&limit=99')[0], 400)
        self.assertEqual(self.call('GET', '/api/corpus?account_id=' + account['id'], token=False)[0], 400)
        source = page['sources'][0]['id']
        self.assertEqual(len(self.call('GET', f'/api/corpus/sources/{source}/revisions')[1]['revisions']), 1)
        state = self.call('GET', '/api/state')[1]
        self.assertNotIn('current_revision', json.dumps(state))
        self.assertEqual(self.call('POST', '/api/samples', {})[0], 201)
        self.assertEqual(self.call('POST', '/api/checks/begin', {'account_ids': [account['id']]})[0], 400)
        self.assertEqual(self.call('GET', '/api/corpus?account_id=' + account['id'])[0], 400)


if __name__ == '__main__':
    unittest.main()
