"""Coverage checks use synthetic ledgers in temporary folders only."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'skills/res-work-safe/scripts/check_coverage.py'
FACEBOOK_ACCOUNT = 'https://www.facebook.com/river_example'
FACEBOOK_POST = FACEBOOK_ACCOUNT + '/posts/1001'
SURFACES = ('posts', 'comments_on_own_posts', 'comments_on_others_posts',
            'replies_on_own_posts', 'replies_on_others_posts', 'media')


def target(item_id, action='review', state='reviewed'):
    return {'id': item_id, 'source_url': 'https://example.invalid/item/' + item_id,
            'author_verified': True, 'text_inspected': True, 'media': 'none',
            'requested_action': action, 'state': state,
            'receipt_url': '', 'receipt_note': ''}


def ledger():
    return {'version': 2, 'account_url': 'https://example.invalid/profile/synthetic',
            'scope_id': 'synthetic-run-1', 'scope': 'Synthetic account posts and replies',
            'surfaces': {name: {'status': 'complete', 'evidence': 'Observed end of synthetic traversal'}
                         for name in SURFACES},
            'targets': [target('post-a')], 'unresolved': []}


class WorkSafeCoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'coverage.json'

    def run_ledger(self, value=None, raw=None):
        self.path.write_bytes(raw if raw is not None else json.dumps(value).encode())
        process = subprocess.run([sys.executable, str(SCRIPT), str(self.path)],
                                 capture_output=True, text=True, check=False, timeout=20)
        self.assertEqual(process.stderr, '')
        return process.returncode, json.loads(process.stdout)

    def test_cli_leaves_verified_helper_files_unchanged_without_bytecode_env(self):
        helpers = Path(self.temp.name) / 'verified-helpers'
        helpers.mkdir()
        contents = {name: (SCRIPT.parent / name).read_bytes()
                    for name in ('check_coverage.py', 'discover_facebook.py')}
        for name, content in contents.items():
            (helpers / name).write_bytes(content)
        self.path.write_text(json.dumps(ledger()))
        environment = os.environ.copy()
        environment.pop('PYTHONDONTWRITEBYTECODE', None)
        environment.pop('PYTHONPYCACHEPREFIX', None)
        result = subprocess.run([sys.executable, str(helpers / SCRIPT.name), str(self.path)],
                                env=environment, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['complete'])
        self.assertEqual({path.name for path in helpers.iterdir()}, set(contents))
        self.assertEqual({name: (helpers / name).read_bytes() for name in contents}, contents)

    def test_verified_parent_does_not_cover_unresolved_independent_reply(self):
        data = ledger()
        data['targets'][0].update(requested_action='remove', state='verified_removed',
                                  receipt_url='https://example.invalid/receipt/post-a',
                                  receipt_note='Observed absent in synthetic replay')
        data['targets'].append(target('reply-other', state='unresolved'))
        data['targets'][1]['source_url'] = ''
        code, result = self.run_ledger(data)
        self.assertEqual(code, 1)
        self.assertFalse(result['review_complete'])
        self.assertFalse(result['complete'])
        self.assertEqual(result['counts']['verified_removals'], 1)
        self.assertIn('target:reply-other:state_unresolved', result['blockers'])

    def test_missing_reply_surface_is_invalid(self):
        data = ledger()
        del data['surfaces']['replies_on_others_posts']
        code, result = self.run_ledger(data)
        self.assertEqual(code, 2)
        self.assertEqual(result['blockers'], ['invalid_surfaces'])

    def test_partial_reply_and_stopped_traversal_are_incomplete(self):
        for name in ('replies_on_others_posts', 'posts'):
            data = ledger()
            data['surfaces'][name] = {'status': 'partial', 'evidence': 'Stopped at synthetic page boundary'}
            code, result = self.run_ledger(data)
            self.assertEqual(code, 1)
            self.assertIn('surface_' + name + '_partial', result['blockers'])

    def test_unknown_user_reported_target_blocks_completion(self):
        data = ledger()
        data['unresolved'] = ['Synthetic unnamed reply']
        code, result = self.run_ledger(data)
        self.assertEqual(code, 1)
        self.assertIn('unresolved_targets', result['blockers'])
        self.assertFalse(result['actions_complete'])
        self.assertNotIn('Synthetic unnamed reply', json.dumps(result))

    def test_attempt_without_receipt_has_zero_removals(self):
        data = ledger()
        data['targets'] = [target('reply-a', 'remove', 'attempted')]
        code, result = self.run_ledger(data)
        self.assertEqual(code, 1)
        self.assertEqual(result['counts']['verified_removals'], 0)
        self.assertEqual(result['counts']['reviewed_targets'], 1)
        self.assertIn('target:reply-a:state_attempted', result['blockers'])

    def test_exact_verified_independent_targets_pass(self):
        data = ledger()
        remove = target('post-a', 'remove', 'verified_removed')
        edit = target('reply-other', 'edit', 'verified_edited')
        for item in (remove, edit):
            item['receipt_url'] = 'https://example.invalid/receipt/' + item['id']
            item['receipt_note'] = 'Observed exact synthetic item outcome'
        data['targets'] = [remove, edit]
        code, result = self.run_ledger(data)
        self.assertEqual(code, 0)
        self.assertTrue(result['complete'])
        self.assertEqual(result['counts'], {'reviewed_targets': 2, 'verified_removals': 1,
                                            'verified_edits': 1})

    def test_media_and_mismatched_result_block_and_do_not_count(self):
        data = ledger()
        item = target('reply-other', 'remove', 'verified_edited')
        item.update(media='unchecked', receipt_url='https://example.invalid/receipt/reply-other',
                    receipt_note='Synthetic observation')
        data['targets'] = [item]
        code, result = self.run_ledger(data)
        self.assertEqual(code, 1)
        self.assertIn('target:reply-other:media', result['blockers'])
        self.assertEqual(result['counts']['verified_edits'], 0)

    def test_verified_action_with_missing_review_evidence_is_not_action_complete(self):
        data = ledger()
        item = target('reply-other', 'remove', 'verified_removed')
        item.update(author_verified=False, receipt_url='https://example.invalid/receipt/reply-other',
                    receipt_note='Synthetic exact outcome')
        data['targets'] = [item]
        code, result = self.run_ledger(data)
        self.assertEqual(code, 1)
        self.assertFalse(result['review_complete'])
        self.assertFalse(result['actions_complete'])
        self.assertEqual(result['counts']['verified_removals'], 0)

    def test_reviewed_target_with_missing_text_is_not_action_complete(self):
        data = ledger()
        data['targets'][0]['text_inspected'] = False
        code, result = self.run_ledger(data)
        self.assertEqual(code, 1)
        self.assertFalse(result['review_complete'])
        self.assertFalse(result['actions_complete'])
        self.assertIn('target:post-a:text_inspected', result['blockers'])
        self.assertEqual(result['counts']['reviewed_targets'], 0)

    def test_duplicate_target_id_and_source_are_invalid(self):
        for duplicate in ('id', 'source_url'):
            data = ledger()
            second = target('reply-other')
            second[duplicate] = data['targets'][0][duplicate]
            data['targets'].append(second)
            code, result = self.run_ledger(data)
            self.assertEqual(code, 2)
            self.assertEqual(result['blockers'], ['duplicate_target_' + duplicate
                                                 if duplicate == 'id' else 'duplicate_source_url'])

    def test_narrow_scope_passes_and_empty_scope_does_not(self):
        data = ledger()
        for name in SURFACES:
            if name != 'posts':
                data['surfaces'][name] = {'status': 'outside_scope', 'evidence': 'User excluded synthetic surface'}
        code, result = self.run_ledger(data)
        self.assertEqual(code, 0)
        self.assertTrue(result['complete'])
        data['surfaces']['posts'] = {'status': 'outside_scope', 'evidence': 'User excluded synthetic surface'}
        code, result = self.run_ledger(data)
        self.assertEqual(code, 1)
        self.assertIn('empty_scope', result['blockers'])

    def test_facebook_comment_or_reply_surfaces_require_discovery_even_without_targets(self):
        for name in SURFACES[1:5]:
            for status in ('complete', 'partial', 'blocked'):
                with self.subTest(surface=name, status=status):
                    data = ledger()
                    data.update(account_url=FACEBOOK_ACCOUNT, scope_id='scope-1', targets=[])
                    for surface in data['surfaces'].values():
                        surface['status'] = 'outside_scope'
                    data['surfaces'][name]['status'] = status
                    code, result = self.run_ledger(data)
                    self.assertEqual(code, 1)
                    self.assertFalse(result['review_complete'])
                    self.assertFalse(result['complete'])
                    self.assertIn('discovery_required', result['blockers'])

        data = ledger()
        data.update(account_url=FACEBOOK_ACCOUNT, scope_id='scope-1')
        data['targets'][0]['source_url'] = FACEBOOK_POST + '?comment_id=2001'
        code, result = self.run_ledger(data)
        self.assertEqual(code, 1)
        self.assertFalse(result['complete'])
        self.assertEqual(result['blockers'], ['discovery_required'])

    def test_facebook_posts_only_passes_without_discovery(self):
        data = ledger()
        data.update(account_url=FACEBOOK_ACCOUNT, scope_id='scope-1')
        data['targets'][0]['source_url'] = FACEBOOK_POST
        for name in SURFACES[1:]:
            data['surfaces'][name]['status'] = 'outside_scope'
        code, result = self.run_ledger(data)
        self.assertEqual(code, 0)
        self.assertTrue(result['complete'])

    def test_facebook_comment_selectors_refuse_posts_only_scope(self):
        data = ledger()
        data['account_url'] = FACEBOOK_ACCOUNT
        for name in SURFACES[1:5]:
            data['surfaces'][name]['status'] = 'outside_scope'
        for query in ('comment_id=2001', 'comment_id', 'comment_id=', 'comment_id=2001&comment_id=2002',
                      'reply_comment_id=3001', 'reply_comment_id=',
                      'reply_comment_id=3001&reply_comment_id=3002',
                      'comment_id=2001&reply_comment_id=3001'):
            with self.subTest(query=query):
                data['targets'][0]['source_url'] = FACEBOOK_POST + '?' + query
                code, result = self.run_ledger(data)
                self.assertEqual(code, 2)
                self.assertFalse(result['complete'])
                self.assertEqual(result['blockers'], ['facebook_comment_outside_scope'])

    def test_non_facebook_comment_selector_passes_with_posts_only_scope(self):
        data = ledger()
        for name in SURFACES[1:5]:
            data['surfaces'][name]['status'] = 'outside_scope'
        data['targets'][0]['source_url'] = 'http://example.invalid/post?comment_id=2001'
        code, result = self.run_ledger(data)
        self.assertEqual(code, 0)
        self.assertTrue(result['complete'])

    def test_facebook_target_requires_a_facebook_account(self):
        for source in (FACEBOOK_POST + '?comment_id=2001',
                       'http://m.facebook.com/other_example/posts/1001?comment_id=2001',
                       'https://www.facebook.com./other_example/posts/1001?comment_id=2001',
                       'https://fb.com/other_example/posts/1001',
                       'http://m.fb.me/other_example/posts/1001',
                       'https://sub.fb.watch./synthetic'):
            with self.subTest(source=source):
                data = ledger()
                data['targets'][0]['source_url'] = source
                code, result = self.run_ledger(data)
                self.assertEqual(code, 2)
                self.assertFalse(result['complete'])
                self.assertEqual(result['blockers'], ['facebook_target_requires_facebook_account'])

    def test_facebook_alias_profiles_are_invalid(self):
        for host in ('fb.com', 'm.fb.me', 'sub.fb.watch.'):
            with self.subTest(host=host):
                data = ledger()
                data['account_url'] = 'https://' + host + '/river_example'
                code, result = self.run_ledger(data)
                self.assertEqual(code, 2)
                self.assertFalse(result['complete'])
                self.assertEqual(result['blockers'], ['invalid_account_url'])

    def test_non_ascii_hosts_are_invalid_in_ledger_urls(self):
        for field in ('account_url', 'source_url', 'receipt_url'):
            for host in ('ｗｗｗ.facebook.com', 'K.example.invalid'):
                with self.subTest(field=field, host=host):
                    data = ledger()
                    fields = data if field == 'account_url' else data['targets'][0]
                    fields[field] = 'https://' + host + '/synthetic'
                    code, result = self.run_ledger(data)
                    self.assertEqual(code, 2)
                    self.assertFalse(result['complete'])
                    self.assertEqual(result['blockers'], ['invalid_' + field])

    def test_v1_requires_explicit_version_upgrade(self):
        data = ledger()
        data['version'] = 1
        del data['account_url'], data['scope_id']
        original = json.dumps(data).encode()
        code, result = self.run_ledger(data)
        self.assertEqual(code, 2)
        self.assertFalse(result['complete'])
        self.assertEqual(result['blockers'], ['invalid_version'])
        self.assertEqual(self.path.read_bytes(), original)

    def test_missing_or_invalid_binding_refuses(self):
        invalid_values = {
            'account_url': (None, ' ', 'https://' + 'x' * 4096, 'http://example.invalid/profile/synthetic',
                            'https://name:password@example.invalid/profile/synthetic',
                            'https://example.invalid:70000/profile/synthetic', FACEBOOK_POST,
                            FACEBOOK_ACCOUNT + '?tracking=synthetic',
                            'https://web.facebook.com/river_example'),
            'scope_id': (None, 1, ' ', 'x' * 101),
        }
        for field, values in invalid_values.items():
            missing = ledger()
            del missing[field]
            cases = [missing] + [{**ledger(), field: value} for value in values]
            for data in cases:
                with self.subTest(field=field, value=repr(data.get(field, 'missing'))[:100]):
                    code, result = self.run_ledger(data)
                    self.assertEqual(code, 2)
                    self.assertFalse(result['complete'])
                    self.assertEqual(result['blockers'], ['invalid_' + field])

    def test_malformed_oversized_and_duplicate_key_json_are_invalid(self):
        for raw in (b'{', b' ' * (2 * 1024 * 1024 + 1),
                    b'{"version":1,"version":1}', b'{"version":NaN}',
                    b'{"version":' + b'9' * 5000 + b'}'):
            code, result = self.run_ledger(raw=raw)
            self.assertEqual(code, 2)
            self.assertFalse(result['complete'])

    def test_unknown_fields_userinfo_and_nonblank_evidence_are_invalid(self):
        cases = []
        data = ledger()
        data['extra'] = 'synthetic'
        cases.append(data)
        data = ledger()
        data['targets'][0]['source_url'] = 'https://name:password@example.invalid/item'
        cases.append(data)
        data = ledger()
        data['surfaces']['posts']['evidence'] = '  '
        cases.append(data)
        for data in cases:
            code, result = self.run_ledger(data)
            self.assertEqual(code, 2)
            self.assertFalse(result['complete'])

    def test_kept_by_user_requires_changed_choice_note(self):
        data = ledger()
        data['targets'] = [target('post-a', 'remove', 'kept_by_user')]
        code, result = self.run_ledger(data)
        self.assertEqual(code, 1)
        self.assertIn('target:post-a:kept_by_user_note', result['blockers'])
        data['targets'][0]['receipt_note'] = 'User chose to keep synthetic post'
        code, result = self.run_ledger(data)
        self.assertEqual(code, 0)
        self.assertTrue(result['complete'])
        data['targets'][0].update(requested_action='review', receipt_note='')
        code, result = self.run_ledger(data)
        self.assertEqual(code, 1)
        self.assertIn('target:post-a:kept_by_user_note', result['blockers'])


if __name__ == '__main__':
    unittest.main()
