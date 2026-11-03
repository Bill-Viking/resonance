"""RES-addressed public-comment intake: synthetic accounts, temporary folders, no provider access."""
from contextlib import closing
import copy
from datetime import datetime, timedelta, timezone
import http.client
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest

import samples
import server
import workflow


HANDLE = '@river_example'
PROFILE = 'https://x.com/river_example'
COMMENT = 'https://x.com/river_example/status/2001'
PARENT = 'https://x.com/pond_example/status/1001'
TEXT = 'RES: please suggest a kinder rewrite of this reply.'


def stamp(offset=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=offset)).isoformat(timespec='seconds').replace('+00:00', 'Z')


def evidence(**changes):
    value = {'checked_at': server.now(), 'status': 'signed_in', 'platform': 'X', 'handle': HANDLE, 'profile_url': PROFILE,
             'author_handle': HANDLE, 'author_profile_url': PROFILE,
             'detail': 'Synthetic account switcher and the comment author were inspected in a temporary fixture.'}
    value.update(changes)
    return value


def item(account, **changes):
    value = {'account_id': account['id'], 'kind': 'reply', 'visibility': 'public', 'source_url': COMMENT,
             'parent_url': PARENT, 'text': TEXT, 'observed_at': server.now(), 'evidence': evidence()}
    value.update(changes)
    return value


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'data'
        self.store = server.Store(self.directory)
        self.account = self.store.mutate('POST', '/api/accounts', {
            'platform': 'X', 'handle': HANDLE, 'url': PROFILE, 'display_name': 'River Example',
            'identity_mode': 'alias', 'daring': 'balanced', 'sfw_only': True})[1]['account']

    def intake(self, *items):
        return self.store.mutate('POST', '/api/request-inbox/intake', {'items': list(items)})[1]

    def rows(self):
        with self.store.connect() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT data FROM request_inbox ORDER BY rowid')]

    def refuses(self, body, pattern, path='/api/request-inbox/intake', method='POST'):
        before = self.rows()
        with self.assertRaisesRegex((server.Invalid, samples.SampleError), pattern):
            self.store.mutate(method, path, body)
        self.assertEqual(self.rows(), before, 'a refused intake leaves the inbox untouched')

    def only(self):
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        return rows[0]

    def update(self, record, **body):
        body.setdefault('revision', record['revision'])
        return self.store.mutate('PATCH', '/api/request-inbox/' + record['id'], body)[1]['request']


class AddressMarkerTests(unittest.TestCase):
    def test_leading_address_forms_are_accepted(self):
        for text, request in [('RES: please fix this', 'please fix this'), ('hey RES check the tone', 'check the tone'),
                              ('Hey, RES, soften it', 'soften it'), ('Res, is this fine?', 'is this fine?'),
                              ('@RES shorten it', 'shorten it'), ('hey @res please look', 'please look'),
                              ('  res - rewrite kindly', 'rewrite kindly'), ('RES\nplease review', 'please review')]:
            with self.subTest(text=text):
                self.assertEqual(workflow.res_address(text), request)

    def test_false_positives_are_not_requests(self):
        for text in ['research notes are attached', 'Results: all good', 'RES', 'RES:', '@RES', 'hey RES', 'RES: !!!',
                     'Thanks everyone!\nRES: fix this', 'I asked RES: fix this', '> RES: fix this', '"RES: fix this"',
                     '`RES: fix this`', '— RES', '-- RES · AI-assisted', 'RES-based plan', "RES's idea", 'RES. thanks',
                     'RESonance is nice', 'heyRES fix', 'res@example.test hello', 'reſ: fix this',
                     'RES — Your Personal Brand Assistant\nhttps://example.test/res']:
            with self.subTest(text=text):
                self.assertIsNone(workflow.res_address(text))

    def test_non_text_is_not_addressed(self):
        for value in (None, 7, ['RES: fix']):
            self.assertIsNone(workflow.res_address(value))

    def test_facebook_comment_keys_ignore_parent_routes_and_tracking(self):
        links = [
            'https://www.facebook.com/pond_example/posts/4001/?comment_id=5001&__tn__=R',
            'https://m.facebook.com/groups/3001/posts/4001/?__cft__[0]=noise&comment_id=5001',
            'https://www.facebook.com/groups/3001/permalink/4001/?comment_id=5001',
            'https://www.facebook.com/permalink.php?story_fbid=4001&id=3001&comment_id=5001',
        ]
        self.assertEqual(len({workflow.comment_source_key(link) for link in links}), 1)
        parent = workflow.comment_source_key(links[0])
        first_reply = workflow.comment_source_key(links[0] + '&reply_comment_id=6001')
        second_reply = workflow.comment_source_key(links[0] + '&reply_comment_id=6002')
        self.assertEqual(len({parent, first_reply, second_reply}), 3)
        self.assertEqual(first_reply, workflow.comment_source_key(links[3].replace('5001', '6001')))

    def test_comment_query_order_does_not_duplicate_other_platforms(self):
        self.assertEqual(
            workflow.comment_source_key('https://www.youtube.com/watch?v=synthetic&lc=reply1'),
            workflow.comment_source_key('https://www.youtube.com/watch?lc=reply1&v=synthetic&utm_source=fixture'))


class IntakeTests(Base):
    def test_own_facebook_comment_on_another_authors_post_is_accepted_and_deduplicated(self):
        profile = 'https://www.facebook.com/river_example'
        account = self.store.mutate('POST', '/api/accounts', {
            'platform': 'Facebook', 'handle': HANDLE, 'url': profile})[1]['account']
        observed = evidence(platform='Facebook', profile_url=profile, author_profile_url=profile)
        value = item(account, kind='comment', source_url='https://www.facebook.com/pond_example/posts/4001/?comment_id=5001',
                     parent_url='https://www.facebook.com/pond_example/posts/4001/', evidence=observed)
        self.assertEqual(self.intake(value)['created'], 1)
        variant = dict(value, source_url='https://www.facebook.com/permalink.php?id=3001&comment_id=5001&story_fbid=4001')
        self.assertEqual(self.intake(variant)['unchanged'], 1)
        group_variant = dict(value, source_url='https://www.facebook.com/groups/3001/permalink/4001/?comment_id=5001')
        self.assertEqual(self.intake(group_variant)['unchanged'], 1)
        self.refuses({'items': [dict(group_variant, source_url='https://www.facebook.com/groups/3001/permalink/4001/')]},
                     'permanent link')
        self.assertFalse(workflow.precise_source(group_variant['source_url'], 'Facebook', 'comment'))
        self.assertEqual(len(self.rows()), 1)
        self.refuses({'items': [dict(value, evidence=dict(observed, author_handle='@pond_example',
                                                        author_profile_url='https://www.facebook.com/pond_example'))]},
                     'author does not match')

    def test_intake_saves_a_separate_local_request_with_honest_evidence(self):
        result = self.intake(item(self.account))
        self.assertEqual((result['created'], result['changed'], result['unchanged']), (1, 0, 0))
        record = self.only()
        self.assertEqual((record['state'], record['revision'], record['kind']), ('needs_review', 1, 'reply'))
        self.assertEqual((record['source_url'], record['parent_url'], record['text']), (COMMENT, PARENT, TEXT))
        self.assertEqual(record['source_key'], COMMENT)
        self.assertEqual(record['evidence']['basis'], 'assistant_reported')
        self.assertEqual(record['evidence']['visibility'], 'public')
        self.assertEqual(record['evidence']['expected']['id'], self.account['id'])
        self.assertEqual(record['history'], [])
        state = self.store.state()
        presented = state['request_inbox'][0]
        self.assertEqual(presented['request_text'], 'please suggest a kinder rewrite of this reply.')
        self.assertTrue(presented['needs_attention'])
        self.assertFalse(presented['published_by_resonance'])
        self.assertFalse(presented['grants_action_authority'])
        # Not a criticism finding, a source card or an action request.
        self.assertEqual((state['reviews'], state['source_items'], state['action_requests'], state['attestations']), ([], [], [], []))
        self.assertEqual(self.store.work()['queued'], [])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM reviews').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM action_identity_checks').fetchone()[0], 0)

    def test_parent_link_is_optional_and_normalized_handles_match(self):
        value = item(self.account, evidence=evidence(handle='River_Example', author_handle='river_example'))
        del value['parent_url']
        self.intake(value)
        self.assertEqual(self.only()['parent_url'], '')

    def test_markers_are_enforced_at_intake(self):
        for text in ('This is research for RES', 'results: fine', 'RES', '> RES: do it', 'Thanks!\nRES: do it'):
            with self.subTest(text=text):
                self.refuses({'items': [item(self.account, text=text)]}, 'begin with an address to RES')

    def test_exact_own_identity_boundaries(self):
        elsewhere = 'https://x.com/pond_example'
        cases = {
            'comment link names another account': (item(self.account, source_url='https://x.com/pond_example/status/2001'), 'different account'),
            'comment author is another account': (item(self.account, evidence=evidence(author_handle='@pond_example', author_profile_url=elsewhere)), 'author does not match'),
            'author handle with another profile': (item(self.account, evidence=evidence(author_profile_url=elsewhere)), 'author does not match'),
            'active account is another account': (item(self.account, evidence=evidence(handle='@pond_example', profile_url=elsewhere)), 'active account does not match'),
            'look-alike handle': (item(self.account, evidence=evidence(handle='@river_example2', profile_url='https://x.com/river_example2')), 'active account does not match'),
            'signed out': (item(self.account, evidence=evidence(status='signed_out')), 'signed in'),
            'wrong platform': (item(self.account, evidence=evidence(platform='Reddit')), 'platform'),
            'missing profile link': (item(self.account, evidence=evidence(profile_url='')), 'profile links'),
            'unsupported profile route': (item(self.account, evidence=evidence(profile_url=PROFILE + '/with_replies')), 'cannot be compared'),
            'identity check made long before reading': (item(self.account, evidence=evidence(checked_at='2020-01-01T00:00:00Z')), 'within five minutes'),
            'future reading': (item(self.account, observed_at='2999-01-01T00:00:00Z'), 'future'),
        }
        for name, (value, pattern) in cases.items():
            with self.subTest(name):
                self.refuses({'items': [value]}, pattern)

    def test_evidence_is_required_and_exact(self):
        value = item(self.account)
        del value['evidence']
        self.refuses({'items': [value]}, 'Required fields')
        self.refuses({'items': [item(self.account, evidence={})]}, 'Required fields')
        self.refuses({'items': [item(self.account, evidence=dict(evidence(), extra='x'))]}, 'Unexpected fields')
        self.refuses({'items': [dict(item(self.account), stray='x')]}, 'Unexpected fields')

    def test_private_message_context_mailboxes_and_unknown_accounts_are_refused(self):
        for visibility in ('private', 'direct_message', 'PUBLIC', True, ''):
            with self.subTest(visibility=visibility):
                self.refuses({'items': [item(self.account, visibility=visibility)]}, 'public own comments')
        mailbox = self.store.mutate('POST', '/api/accounts', {'platform': 'Gmail', 'handle': 'mailbox@example.test'})[1]['account']
        self.refuses({'items': [item(mailbox)]}, 'Mailboxes are outside')
        self.refuses({'items': [item(self.account, account_id='nosuchaccount')]}, 'saved account')

    def test_malformed_or_imprecise_comment_links_are_refused(self):
        for source in ('', 'not a url', 'javascript:alert(1)', 'https://user:pw@x.com/river_example/status/2001',
                       'https://x.com/river_example/status/2001 extra', PROFILE, 'https://x.com/river_example/with_replies',
                       'https://x.com/river_example/status', 'https://elsewhere.example/river_example/status/2001',
                       'http://x.com:99999/river_example/status/2001', 'ftp://x.com/river_example/status/2001'):
            with self.subTest(source=source):
                self.refuses({'items': [item(self.account, source_url=source)]}, 'permanent link|URL|Invalid|http|exact')
        self.refuses({'items': [item(self.account, kind='comment')]}, 'permanent link')
        self.refuses({'items': [item(self.account, kind='post')]}, 'comment type')
        self.refuses({'items': [item(self.account, parent_url=COMMENT)]}, 'parent post link')

    def test_comment_links_on_other_supported_platforms(self):
        youtube = self.store.mutate('POST', '/api/accounts', {'platform': 'YouTube', 'handle': '@river_example',
                                                              'url': 'https://www.youtube.com/@river_example'})[1]['account']
        profile = 'https://www.youtube.com/@river_example'
        value = item(youtube, kind='comment', source_url='https://www.youtube.com/watch?v=abc123&lc=Ugx_synthetic1',
                     parent_url='https://www.youtube.com/watch?v=abc123',
                     evidence=evidence(platform='YouTube', profile_url=profile, author_profile_url=profile))
        self.intake(value)
        self.refuses({'items': [dict(value, source_url='https://www.youtube.com/watch?v=abc123')]}, 'permanent link')
        threads = self.store.mutate('POST', '/api/accounts', {'platform': 'Threads', 'handle': '@river_example'})[1]['account']
        self.refuses({'items': [item(threads, evidence=evidence(platform='Threads'))]}, 'permanent link')

    def test_batch_is_atomic_and_bounded(self):
        good = item(self.account)
        bad = item(self.account, source_url='https://x.com/river_example/status/2002', evidence=evidence(status='signed_out'))
        self.refuses({'items': [good, bad]}, 'signed in')
        self.assertEqual(self.store.state()['request_inbox'], [])
        self.refuses({'items': [good, dict(good, text='RES: a different request')]}, 'same comment twice')
        self.refuses({'items': []}, 'between 1 and')
        self.refuses({'items': [good] * 51}, 'between 1 and')
        self.refuses({'items': 'x'}, 'between 1 and')
        self.refuses({}, 'Required fields')
        self.refuses([good], 'Expected a JSON object')

    def test_sample_records_and_sample_workspaces_are_refused(self):
        self.refuses({'items': [item(self.account, account_id=samples.SAMPLE_PREFIX + 'account')]}, 'Sample records')
        demo = samples.Workspace(self.directory.parent / 'practice', server.Store)
        token = demo.context()['token']
        token = demo.mutate('POST', '/api/samples', {}, token)[1]['workspace']['token']
        for method, path in (('POST', '/api/request-inbox/intake'), ('PATCH', '/api/request-inbox/abc')):
            with self.assertRaisesRegex(samples.SampleError, 'unavailable in samples'):
                demo.mutate(method, path, {'items': []}, token)
        self.assertEqual(demo.state()['request_inbox'], [])
        with self.assertRaisesRegex(server.Invalid, 'Practice samples'):
            demo._demo().mutate('POST', '/api/request-inbox/intake', {'items': [item(self.account)]})


class DedupeTests(Base):
    def test_rereading_unchanged_text_reopens_nothing(self):
        self.intake(item(self.account))
        record = self.only()
        prepared = self.update(record, state='prepared', evaluation='A kinder rewrite fits the account voice.',
                               proposal_kind='reply', proposal='Thanks for the question; here is a gentler take.')
        done = self.update(prepared, state='done', handled_note='Handled locally; the user replied by hand.')
        later = server.now()
        variant = COMMENT + '/?utm_source=synthetic#c1'
        result = self.intake(item(self.account, source_url=variant, observed_at=later, evidence=evidence(checked_at=later)))
        self.assertEqual((result['created'], result['changed'], result['unchanged']), (0, 0, 1))
        saved = self.only()
        self.assertEqual((saved['id'], saved['state'], saved['revision'], saved['evaluation']), (done['id'], 'done', 1, done['evaluation']))
        self.assertEqual(saved['handled_note'], done['handled_note'])
        self.assertGreaterEqual(saved['last_seen_at'], record['last_seen_at'])
        self.assertEqual(saved['observed_at'], record['observed_at'])

    def test_same_comment_for_a_different_account_is_a_different_request(self):
        other = self.store.mutate('POST', '/api/accounts', {'platform': 'X', 'handle': '@river_example',
                                                            'url': PROFILE})[1]['account']
        self.intake(item(self.account))
        self.intake(item(other))
        self.assertEqual(len(self.rows()), 2)

    def test_changed_text_supersedes_evaluation_and_needs_review_again(self):
        self.intake(item(self.account))
        first = self.only()
        prepared = self.update(first, state='prepared', evaluation='Evaluated the first wording.',
                               proposal_kind='edit', proposal='A proposed edit for the first wording.')
        done = self.update(prepared, state='done', handled_note='Handled locally.', receipt_url='https://x.com/river_example/status/2001',
                           receipt_note='Synthetic note: the reply was seen on the page.')
        self.assertEqual(done['outcome'], 'Receipt recorded. It is a note, and Resonance did not verify it.')
        later = stamp(10)
        # A reading that is not newer than the saved one cannot rewrite it.
        self.refuses({'items': [item(self.account, text='RES: older words', observed_at=first['last_seen_at'],
                                     evidence=evidence(checked_at=first['last_seen_at']))]}, 'newer reading')
        # A change that drops the address is not a request; the saved one stays for the user to dismiss.
        self.refuses({'items': [item(self.account, text='Thanks, no request here', observed_at=later, evidence=evidence(checked_at=later))]},
                     'begin with an address to RES')
        changed = self.intake(item(self.account, text='Hey RES, please shorten this one instead.', observed_at=later,
                                   evidence=evidence(checked_at=later)))
        self.assertEqual((changed['created'], changed['changed'], changed['unchanged']), (0, 1, 0))
        record = self.only()
        self.assertEqual((record['id'], record['state'], record['revision']), (first['id'], 'needs_review', 2))
        self.assertEqual((record['evaluation'], record['proposal'], record['handled_note'], record['receipt_url']), ('', '', '', ''))
        self.assertEqual(record['text'], 'Hey RES, please shorten this one instead.')
        self.assertEqual(len(record['history']), 1)
        old = record['history'][0]
        self.assertEqual((old['revision'], old['text'], old['state'], old['evaluation']), (1, TEXT, 'done', 'Evaluated the first wording.'))
        self.assertEqual((old['proposal'], old['handled_note']), ('A proposed edit for the first wording.', 'Handled locally.'))
        self.assertTrue(self.store.state()['request_inbox'][0]['needs_attention'])
        # The old revision's tab cannot overwrite the new words.
        self.refuses({'state': 'prepared', 'revision': 1, 'evaluation': 'stale'}, 'changed since you loaded',
                     path='/api/request-inbox/' + record['id'], method='PATCH')

    def test_history_is_bounded(self):
        self.intake(item(self.account))
        for number in range(2, 16):
            seen = stamp(number)
            self.intake(item(self.account, text=f'RES: version {number}', observed_at=seen, evidence=evidence(checked_at=seen)))
        record = self.only()
        self.assertEqual(record['revision'], 15)
        self.assertEqual(len(record['history']), server.INBOX_HISTORY_LIMIT)
        self.assertEqual(record['history'][-1]['text'], 'RES: version 14')


class StateTests(Base):
    def setUp(self):
        super().setUp()
        self.intake(item(self.account))
        self.record = self.only()

    def refuse_update(self, body, pattern):
        self.refuses(dict(body, revision=body.get('revision', 1)), pattern, path='/api/request-inbox/' + self.record['id'], method='PATCH')

    def test_prepared_needs_an_evaluation_and_typed_proposal(self):
        self.refuse_update({'state': 'prepared'}, 'needs an evaluation')
        self.refuse_update({'state': 'prepared', 'evaluation': '  '}, 'needs an evaluation')
        self.refuse_update({'state': 'prepared', 'evaluation': 'ok', 'proposal': 'Some reply'}, 'type')
        self.refuse_update({'state': 'prepared', 'evaluation': 'ok', 'proposal_kind': 'publish', 'proposal': 'x'}, 'proposal type')
        self.refuse_update({'state': 'prepared', 'evaluation': 'ok', 'proposal_kind': 'reply'}, 'type')
        prepared = self.update(self.record, state='prepared', evaluation='Reviewed locally.', proposal_kind='reply', proposal='A gentle reply.')
        self.assertEqual((prepared['state'], prepared['proposal_kind']), ('prepared', 'reply'))
        self.assertEqual(prepared['outcome'], 'Prepared locally. Nothing has been run or published.')
        again = self.update(prepared, state='prepared', evaluation='Reviewed locally, with a second thought.')
        self.assertEqual(again['proposal'], 'A gentle reply.')

    def test_flow_is_minimal_and_done_is_final(self):
        self.refuse_update({'state': 'done', 'handled_note': 'x'}, 'cannot move')
        self.refuse_update({'state': 'needs_review'}, 'cannot move')
        self.refuse_update({'state': 'bogus'}, 'Invalid request state')
        self.refuse_update({'state': 'dismissed', 'revision': 'one'}, 'changed since')
        self.refuse_update({'state': 'dismissed', 'handled_note': 'nope'}, 'Only marking a request done')
        dismissed = self.update(self.record, state='dismissed', evaluation='Not a request for RES to act on.')
        self.assertEqual(dismissed['outcome'], 'Dismissed locally. Nothing was run or published.')
        reopened = self.update(dismissed, state='needs_review')
        prepared = self.update(reopened, state='prepared', evaluation='Evaluated after reopening.')
        self.refuses({'state': 'done', 'revision': 1}, 'how this request was handled', path='/api/request-inbox/' + self.record['id'], method='PATCH')
        self.refuses({'state': 'done', 'revision': 1, 'handled_note': 'ok', 'receipt_url': 'https://x.com/river_example/status/2001'},
                     'receipt needs both', path='/api/request-inbox/' + self.record['id'], method='PATCH')
        self.refuses({'state': 'done', 'revision': 1, 'handled_note': 'ok', 'proposal': 'x', 'proposal_kind': 'reply'},
                     'preparing', path='/api/request-inbox/' + self.record['id'], method='PATCH')
        done = self.update(prepared, state='done', handled_note='I answered by hand.')
        self.assertEqual(done['outcome'], 'Handled locally. Resonance published nothing.')
        for target in ('needs_review', 'prepared', 'dismissed', 'done'):
            self.refuse_update({'state': target, 'evaluation': 'again', 'handled_note': 'again'}, 'cannot move')

    def test_nothing_in_the_inbox_creates_or_unlocks_action_authority(self):
        prepared = self.update(self.record, state='prepared', evaluation='Evaluated.', proposal_kind='edit', proposal='Different words.')
        self.update(prepared, state='done', handled_note='Handled locally.')
        state = self.store.state()
        self.assertEqual((state['action_requests'], state['source_items'], state['reviews']), ([], [], []))
        work = self.store.work()
        self.assertEqual((work['queued'], work['held'], work['verification']['requests']), ([], [], []))
        with self.assertRaises(LookupError):
            self.store.mutate('POST', '/api/action-requests/' + self.record['id'] + '/start', {'identity_check_id': 'x'})
        with self.assertRaises((server.Invalid, LookupError)):
            self.store.mutate('POST', '/api/action-requests', {'review_id': self.record['id'], 'action': 'delete'})
        self.assertFalse(state['request_inbox'][0]['grants_action_authority'])
        self.assertIn('no account access or action authority', state['request_inbox'][0]['scope_note'])

    def test_non_text_handled_notes_are_refused_without_changing_state(self):
        self.update(self.record, state='prepared', evaluation='Reviewed locally.')
        for value in (None, [], {}, 7):
            with self.subTest(value=value):
                self.refuse_update({'state': 'done', 'handled_note': value}, 'Handled note')

    def test_unknown_request_is_not_found(self):
        with self.assertRaises(LookupError):
            self.store.mutate('PATCH', '/api/request-inbox/nosuchrequest', {'state': 'dismissed', 'revision': 1})


class BackupTests(Base):
    def test_backup_comment_identity_must_match_the_current_account(self):
        profile = 'https://www.youtube.com/@river_example'
        account = self.store.mutate('POST', '/api/accounts', {
            'platform': 'YouTube', 'handle': HANDLE, 'url': profile})[1]['account']
        self.intake(item(account, kind='comment', source_url='https://www.youtube.com/watch?v=synthetic&lc=reply1',
                         parent_url='https://www.youtube.com/watch?v=synthetic',
                         evidence=evidence(platform='YouTube', profile_url=profile, author_profile_url=profile)))
        backup = self.store.backup()
        for saved in backup['accounts']:
            if saved['id'] == account['id']:
                saved.update(handle='@pond_example', url='https://www.youtube.com/@pond_example')
        fresh = server.Store(Path(self.temp.name) / 'foreign-account')
        before = fresh.backup()
        with self.assertRaisesRegex(server.Invalid, 'does not belong to the saved account'):
            fresh.mutate('POST', '/api/import', backup)
        self.assertEqual(fresh.backup(), before)

    def test_export_is_portable_readable_json_without_computed_fields(self):
        self.intake(item(self.account))
        backup = self.store.backup()
        self.assertEqual(backup['schema_version'], 3)
        exported = backup['request_inbox']
        self.assertEqual(len(exported), 1)
        self.assertTrue(set(exported[0]) == server.INBOX_FIELDS)
        json.dumps(backup)
        other = server.Store(Path(self.temp.name) / 'restored')
        other.mutate('POST', '/api/import', copy.deepcopy(backup))
        self.assertEqual(other.backup()['request_inbox'], exported)
        self.assertEqual(other.state()['action_requests'], [])
        # Importing again changes nothing, and a backup without the key leaves the inbox alone.
        other.mutate('POST', '/api/import', copy.deepcopy(backup))
        legacy = copy.deepcopy(backup)
        del legacy['request_inbox']
        other.mutate('POST', '/api/import', legacy)
        self.assertEqual(other.backup()['request_inbox'], exported)

    def test_restored_requests_keep_local_notes_and_grant_nothing(self):
        self.intake(item(self.account))
        record = self.only()
        prepared = self.update(record, state='prepared', evaluation='Evaluated.', proposal_kind='reply', proposal='A reply.')
        self.update(prepared, state='done', handled_note='Handled locally.')
        backup = self.store.backup()
        other = server.Store(Path(self.temp.name) / 'restored')
        other.mutate('POST', '/api/import', backup)
        restored = other.state()
        self.assertEqual(restored['request_inbox'][0]['state'], 'done')
        self.assertEqual((restored['action_requests'], restored['source_items']), ([], []))
        self.assertEqual(other.work()['queued'], [])

    def test_older_backup_never_rolls_back_newer_decisions(self):
        self.intake(item(self.account))
        old = self.store.backup()
        record = self.only()
        self.update(record, state='dismissed', evaluation='Not for RES.')
        self.store.mutate('POST', '/api/import', old)
        self.assertEqual(self.only()['state'], 'dismissed')

    def test_invalid_inbox_import_is_atomic(self):
        self.intake(item(self.account))
        backup = self.store.backup()
        fresh = server.Store(Path(self.temp.name) / 'fresh')
        cases = {}
        forged = copy.deepcopy(backup)
        forged['request_inbox'][0]['evidence']['author_handle'] = '@pond_example'
        forged['request_inbox'][0]['evidence']['author_profile_url'] = 'https://x.com/pond_example'
        cases['foreign author'] = (forged, 'author does not match')
        unaddressed = copy.deepcopy(backup)
        unaddressed['request_inbox'][0]['text'] = 'No address here'
        cases['unaddressed text'] = (unaddressed, 'begin with an address')
        foreign_link = copy.deepcopy(backup)
        foreign_link['request_inbox'][0]['source_url'] = 'https://x.com/pond_example/status/2001'
        foreign_link['request_inbox'][0]['source_key'] = 'https://x.com/pond_example/status/2001'
        cases['foreign comment link'] = (foreign_link, 'different account')
        twice = copy.deepcopy(backup)
        twice['request_inbox'].append(dict(twice['request_inbox'][0], id='anotherid'))
        cases['same comment twice'] = (twice, 'twice')
        authority = copy.deepcopy(backup)
        authority['request_inbox'][0]['grants_action_authority'] = True
        cases['extra authority field'] = (authority, 'Unexpected fields')
        done_without_note = copy.deepcopy(backup)
        done_without_note['request_inbox'][0].update(state='done', evaluation='x')
        cases['done without a note'] = (done_without_note, 'how it was handled')
        sample = copy.deepcopy(backup)
        sample['request_inbox'][0]['id'] = samples.SAMPLE_PREFIX + 'request'
        cases['sample identifier'] = (sample, 'Sample records')
        unknown_account = copy.deepcopy(backup)
        unknown_account['accounts'] = []
        cases['unknown account'] = (unknown_account, 'unknown or mailbox account|references')
        for name, (body, pattern) in cases.items():
            with self.subTest(name):
                before_fresh = fresh.backup()
                with self.assertRaisesRegex((server.Invalid, samples.SampleError), pattern):
                    fresh.mutate('POST', '/api/import', copy.deepcopy(body))
                self.assertEqual(fresh.backup(), before_fresh, 'a refused import changes nothing, including accounts')
        # One bad inbox record also keeps the otherwise valid account and review records out.
        mixed = copy.deepcopy(backup)
        mixed['request_inbox'][0]['text'] = 'No address here'
        mixed['settings']['display_name'] = 'Changed by a refused import'
        with self.assertRaises(server.Invalid):
            self.store.mutate('POST', '/api/import', mixed)
        self.assertNotEqual(self.store.state()['settings']['display_name'], 'Changed by a refused import')
        self.assertEqual(len(self.rows()), 1)

    def test_import_limits_and_shape(self):
        backup = self.store.backup()
        for value in ('x', {}, [1], None):
            with self.assertRaises(server.Invalid):
                self.store.mutate('POST', '/api/import', dict(backup, request_inbox=value))


class MigrationTests(Base):
    def test_schema_four_database_upgrades_with_a_backup_and_keeps_everything(self):
        self.assertEqual(len(server.MIGRATIONS), 6)
        with self.store.connect() as db:
            db.execute('DROP TABLE request_inbox')
            db.execute('PRAGMA user_version=4')
            tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            before = {name: [tuple(row) for row in db.execute('SELECT * FROM "' + name + '"')] for name in tables}
        with self.assertRaisesRegex(server.Invalid, 'matching app version'):
            server.Store(self.directory, read_only=True).inbox_snapshot()
        upgraded = server.Store(self.directory)
        with upgraded.connect() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 6)
            self.assertEqual(db.execute('SELECT count(*) FROM request_inbox').fetchone()[0], 0)
            after = {name: [tuple(row) for row in db.execute('SELECT * FROM "' + name + '"')] for name in tables}
        self.assertEqual(after, before)
        snapshot = sorted((self.directory / 'backups' / 'auto').glob('*-before-v5.sqlite3'))[-1]
        with closing(sqlite3.connect(snapshot)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 4)
        self.assertEqual(upgraded.inbox_snapshot()['requests'], [])

    def test_older_app_versions_refuse_the_newer_schema(self):
        with self.store.connect() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], len(server.MIGRATIONS))


class LocalAPITests(Base):
    def setUp(self):
        super().setUp()
        self.app = server.make_server(Path(self.temp.name) / 'http', port=0)
        self.worker = threading.Thread(target=self.app.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.worker.join)
        self.addCleanup(self.app.server_close)
        self.addCleanup(self.app.shutdown)
        self.port = self.app.server_address[1]

    def call(self, method, path, body, origin=True):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        headers = {'Content-Type': 'application/json', 'X-Resonance-Workspace': self.app.store.context()['token']}
        if origin:
            headers['Origin'] = f'http://127.0.0.1:{self.port}'
        connection.request(method, path, json.dumps(body).encode(), headers)
        response = connection.getresponse()
        result = json.loads(response.read())
        connection.close()
        return response.status, result

    def test_same_origin_round_trip_and_cross_origin_refusal(self):
        account = self.call('POST', '/api/accounts', {'platform': 'X', 'handle': HANDLE, 'url': PROFILE})[1]['account']
        self.assertEqual(self.call('POST', '/api/request-inbox/intake', {'items': [item(account)]}, origin=False)[0], 403)
        status, result = self.call('POST', '/api/request-inbox/intake', {'items': [item(account)]})
        self.assertEqual((status, result['created']), (201, 1))
        status, refused = self.call('POST', '/api/request-inbox/intake', {'items': [item(account, visibility='private')]})
        self.assertEqual(status, 400)
        self.assertIn('public own comments', refused['error'])
        request_id = result['results'][0]['id']
        status, updated = self.call('PATCH', '/api/request-inbox/' + request_id, {'state': 'dismissed', 'revision': 1})
        self.assertEqual((status, updated['request']['state']), (200, 'dismissed'))
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        connection.request('GET', '/api/state', headers={})
        state = json.loads(connection.getresponse().read())
        connection.close()
        self.assertEqual([record['state'] for record in state['request_inbox']], ['dismissed'])
        self.assertEqual(self.call('PATCH', '/api/request-inbox/nosuchrequest', {'state': 'dismissed', 'revision': 1})[0], 404)


class CommandTests(Base):
    def run_command(self, *args):
        return subprocess.run([sys.executable, str(server.ROOT / 'monitor.py'), '--data-dir', str(self.directory), *args],
                              capture_output=True, text=True, cwd=self.temp.name)

    def write(self, name, value):
        folder = Path(self.temp.name) / 'private'
        folder.mkdir(exist_ok=True)
        path = folder / name
        path.write_text(json.dumps(value), encoding='utf-8')
        return path

    def test_intake_read_and_update_commands(self):
        path = self.write('intake.json', {'items': [item(self.account)]})
        done = self.run_command('--res-intake', str(path))
        self.assertEqual(done.returncode, 0, done.stderr)
        result = json.loads(done.stdout)
        self.assertEqual((result['created'], result['results'][0]['outcome']), (1, 'created'))
        again = json.loads(self.run_command('--res-intake', str(path)).stdout)
        self.assertEqual((again['created'], again['unchanged']), (0, 1))

        before = (self.directory / server.DATABASE_NAME).read_bytes()
        read = self.run_command('--res-inbox')
        self.assertEqual(read.returncode, 0, read.stderr)
        inbox = json.loads(read.stdout)
        self.assertEqual((inbox['returned'], inbox['counts']['needs_review'], inbox['truncated']), (1, 1, False))
        request = inbox['requests'][0]
        self.assertEqual((request['source_url'], request['text'], request['state'], request['account']['handle']), (COMMENT, TEXT, 'needs_review', HANDLE))
        self.assertIn('Resonance watches nothing', inbox['scope_note'])
        self.assertEqual((self.directory / server.DATABASE_NAME).read_bytes(), before, 'reading the inbox changes nothing')
        self.assertEqual(json.loads(self.run_command('--res-inbox', '--state', 'done').stdout)['returned'], 0)
        self.assertEqual(json.loads(self.run_command('--res-inbox', '--account', self.account['id']).stdout)['returned'], 1)
        self.assertEqual(self.run_command('--res-inbox', '--account', 'nosuchaccount').returncode, 1)

        update = self.write('update.json', {'id': request['id'], 'state': 'prepared', 'revision': 1,
                                            'evaluation': 'The wording is blunt; a softer rewrite fits this alias.',
                                            'proposal_kind': 'reply', 'proposal': 'Good question — here is a gentler way to say it.'})
        updated = self.run_command('--res-update', str(update))
        self.assertEqual(updated.returncode, 0, updated.stderr)
        self.assertEqual(json.loads(updated.stdout)['request']['state'], 'prepared')
        self.assertEqual(json.loads(self.run_command('--res-inbox').stdout)['requests'][0]['proposal_kind'], 'reply')
        hasty = self.write('hasty.json', {'id': request['id'], 'state': 'done', 'revision': 1})
        refused = self.run_command('--res-update', str(hasty))
        self.assertEqual(refused.returncode, 1)
        self.assertIn('Resonance monitoring:', refused.stderr)
        self.assertEqual(json.loads(self.run_command('--res-inbox').stdout)['requests'][0]['state'], 'prepared')

    def test_commands_refuse_bad_documents_and_unsafe_storage(self):
        bad = self.write('bad.json', {'items': [item(self.account, evidence=evidence(author_handle='@pond_example', author_profile_url='https://x.com/pond_example'))]})
        refused = self.run_command('--res-intake', str(bad))
        self.assertEqual(refused.returncode, 1)
        self.assertIn('only your own comments', refused.stderr)
        self.assertEqual(self.rows(), [])
        sample = self.write('sample.json', {'items': [item(self.account, account_id=samples.SAMPLE_PREFIX + 'a')]})
        self.assertEqual(self.run_command('--res-intake', str(sample)).returncode, 1)
        unsafe = Path(self.temp.name) / 'iCloud Drive' / 'private'
        unsafe.mkdir(parents=True)
        (unsafe / 'intake.json').write_text(json.dumps({'items': [item(self.account)]}))
        self.assertEqual(self.run_command('--res-intake', str(unsafe / 'intake.json')).returncode, 1)
        self.assertEqual(self.rows(), [])
        repository = Path(self.temp.name) / 'checkout'
        (repository / '.git').mkdir(parents=True)
        shared = subprocess.run([sys.executable, str(server.ROOT / 'monitor.py'), '--data-dir', str(repository / 'data'), '--res-inbox'],
                                capture_output=True, text=True)
        self.assertEqual(shared.returncode, 1)
        self.assertFalse((repository / 'data').exists())
        for args in (['--state', 'done'], ['--account', 'x']):
            self.assertEqual(self.run_command('--status', *args).returncode, 2)
        self.assertEqual(self.run_command('--res-update', str(self.write('array.json', []))).returncode, 1)

    def test_commands_hold_while_samples_are_showing(self):
        workspace = samples.Workspace(self.directory, server.Store)
        workspace.mutate('POST', '/api/samples', {}, workspace.context()['token'])
        path = self.write('intake.json', {'items': [item(self.account)]})
        for args in (['--res-intake', str(path)], ['--res-inbox']):
            result = self.run_command(*args)
            self.assertEqual(result.returncode, 1)
            self.assertIn('Samples are showing', result.stderr)


if __name__ == '__main__':
    unittest.main()
