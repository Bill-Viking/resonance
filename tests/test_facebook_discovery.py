"""Synthetic Facebook activity inventory and DOM capture regressions."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/res-work-safe/scripts'
DISCOVER = SCRIPTS / 'discover_facebook.py'
COVERAGE = SCRIPTS / 'check_coverage.py'
CAPTURE = SCRIPTS / 'capture_facebook_activity.js'
sys.path.insert(0, str(SCRIPTS))
sys.dont_write_bytecode = True
import discover_facebook as discovery  # noqa: E402


ACCOUNT = 'https://www.facebook.com/river_example'
PARENT = 'https://www.facebook.com/other_example/posts/1001'
SCOPE = 'Synthetic account posts and replies'


def row(key, kind='comment', url=None, author=ACCOUNT):
    return {'row_key': key, 'kind': kind, 'author_url': author,
            'navigation_url': url if url is not None else PARENT + '?comment_id=' + key}


def snapshot(batch_id='batch-1', rows=None, boundary='end', **changes):
    batch = {'id': batch_id, 'observed_at': '2026-11-12T15:00:00Z',
             'page_url': 'https://www.facebook.com/123456/allactivity/?category_key=COMMENTS',
             'category': 'comments', 'account_verified': True,
             'people_filter': 'all', 'search_filter': 'none', 'boundary': boundary,
             'boundary_note': 'Observed synthetic list boundary', 'loading': False,
             'unparsed_rows': 0,
             'rows': [row('2001')] if rows is None else rows}
    batch.update(changes)
    return {'version': 1, 'account_url': ACCOUNT, 'scope_id': 'scope-1',
            'scope': SCOPE, 'batch': batch}


def coverage(targets):
    names = ('posts', 'comments_on_own_posts', 'comments_on_others_posts',
             'replies_on_own_posts', 'replies_on_others_posts', 'media')
    return {'version': 2, 'account_url': ACCOUNT, 'scope_id': 'scope-1', 'scope': SCOPE,
            'surfaces': {name: {'status': 'complete', 'evidence': 'Synthetic observed boundary'}
                         for name in names},
            'targets': [{'id': str(index), 'source_url': source, 'author_verified': True,
                         'text_inspected': True, 'media': 'none', 'requested_action': 'review',
                         'state': 'reviewed', 'receipt_url': '', 'receipt_note': ''}
                        for index, source in enumerate(targets)], 'unresolved': []}


NODE_HARNESS = r'''
const fs = require('fs');
const capture = eval(fs.readFileSync(process.argv[1], 'utf8'));
class Element {
  constructor(tag, attrs={}, text='') {
    this.tag=tag; this.tagName=tag.toUpperCase(); this.attrs=attrs; this.innerText=text; this.textContent=text;
    this.children=[]; this.parentElement=null; this.hidden=false; this.value=attrs.value||'';
    this.href=attrs.href||'';
  }
  add(child) { child.parentElement=this; this.children.push(child); return child; }
  getAttribute(name) { return this.attrs[name] ?? null; }
  getClientRects() { return [1]; }
  querySelectorAll(selector) {
    const all=[];
    const visit=(node)=>{for(const child of node.children){all.push(child);visit(child)}};
    visit(this);
    return all.filter(node => selector.split(',').some(part => {
      if(part==='h2'||part==='main'||part==='button') return node.tag===part;
      if(part==='a[href]') return node.tag==='a' && !!node.href;
      if(part==='input[type="checkbox"]') return node.tag==='input'&&node.attrs.type==='checkbox';
      if(part==='input[type="search"]') return node.tag==='input'&&node.attrs.type==='search';
      if(part==='[role="button"]') return node.attrs.role==='button';
      if(part==='[role="checkbox"]') return node.attrs.role==='checkbox';
      if(part==='[role="link"]') return node.attrs.role==='link';
      if(part==='[role="status"][aria-label="Loading..."]')
        return node.attrs.role==='status'&&node.attrs['aria-label']==='Loading...';
      return false;
    }));
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0]||null; }
}
const body=new Element('body'), heading=body.add(new Element('h2',{},'Comments'));
const main=body.add(new Element('main'));
main.add(new Element('button',{},'People\nAll'));
main.add(new Element('input',{type:'search','aria-label':'Search activity log',value:''}));
const globalAll=main.add(new Element('input',{type:'checkbox'}));
globalAll.labels=[{innerText:'All'}];
function addRow(key, label, href) {
  const container=main.add(new Element('div'));
  container.add(new Element('input',{type:'checkbox','aria-label':key}));
  container.add(new Element('a',{href:'https://www.facebook.com/river_example'}));
  container.add(new Element('a',{href,'aria-label':label},label));
}
addRow('Select post from November 12, 10:00, post 1','View activity you commented on',
  'https://www.facebook.com/other_example/posts/1001?comment_id=2001');
addRow('Select post from November 12, 10:01, post 2','View activity you replied to',
  'https://www.facebook.com/other_example/posts/1001?comment_id=2001&reply_comment_id=3001');
global.document={body, querySelectorAll:(selector)=>body.querySelectorAll(selector),
                 querySelector:(selector)=>body.querySelector(selector)};
global.location={href:'https://www.facebook.com/123456/allactivity/?category_key=COMMENTS',
                 pathname:'/123456/allactivity/'};
global.getComputedStyle=()=>({display:'block',visibility:'visible'});
const input={account_url:'https://www.facebook.com/river_example',scope_id:'scope-1',
             scope:'Synthetic account posts and replies',id:'batch-1',
             observed_at:'2026-11-12T15:00:00Z',account_verified:true,
             boundary:'end',boundary_note:'Observed synthetic end'};
const good=capture(input);
const unmatchedRoleView=main.add(new Element('div'));
unmatchedRoleView.add(new Element('div',{role:'button','aria-label':'View activity you commented on'}));
const mixedRoleView=capture(input);
main.children=main.children.filter(node => node!==unmatchedRoleView);
const unsafeAccount=capture({...input,
  account_url:'https://synthetic-user:synthetic-secret@www.facebook.com/river_example'});
location.href='https://synthetic-user:synthetic-secret@www.facebook.com/123456/allactivity/?private_note=synthetic-private-text';
const unsafePage=capture(input);
location.href='https://www.facebook.com/123456/allactivity/?category_key=COMMENTS';
const first=main.children[3];
first.children[1].href='https://synthetic-user:synthetic-secret@www.facebook.com/river_example';
first.children[2].href='https://www.facebook.com/other_example/posts/1001?comment_id=2001&private_note=synthetic-private-text';
const unsafe=capture(input);
first.children[2].href='https://www.facebook.com:8443/other_example/posts/1001?comment_id=2001';
const unsafePort=capture(input);
first.children[1].href='https://www.facebook.com/river_example';
first.children[2].href='https://www.facebook.com/other_example/posts/1001?comment_id=2001';
main.add(new Element('input',{type:'search','aria-label':'Search activity log',value:'synthetic-filter'}));
main.add(new Element('button',{},'People Friends'));
const ambiguous=capture(input);
main.children=main.children.filter(node => !(node.tag==='button'&&node.innerText==='People Friends') &&
  !(node.tag==='input'&&node.attrs.type==='search'&&node.value==='synthetic-filter'));
const orphan=main.add(new Element('div'));
orphan.add(new Element('a',{href:'https://www.facebook.com/other_example/posts/1002?comment_id=2002',
                             'aria-label':'View activity you commented on'},'View activity you commented on'));
const unmatchedView=capture(input);
heading.innerText='Posts'; heading.textContent='Posts';
main.add(new Element('input',{type:'checkbox'}));
main.querySelector('input[type="search"]').value='synthetic query';
main.querySelector('button').innerText='People Friends';
const drift=capture(input);
heading.innerText='Comments'; heading.textContent='Comments';
main.children=main.children.filter(node => node.tag!=='div' &&
  !(node.tag==='input'&&node.attrs.type==='checkbox'&&node!==globalAll));
const empty=capture(input);
const alternate=main.add(new Element('div'));
alternate.add(new Element('button',{role:'checkbox','aria-label':'Select post from November 12, 10:03, post 4'}));
alternate.add(new Element('button',{},'View activity you commented on'));
const alternateResult=capture(input);
process.stdout.write(JSON.stringify({good,mixedRoleView,unsafeAccount,unsafePage,unsafe,unsafePort,ambiguous,unmatchedView,drift,empty,alternateResult}));
'''


class FacebookDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def run_discover(self, value, prior=None, raw=None):
        source = self.folder / 'snapshot.json'
        source.write_bytes(raw if raw is not None else json.dumps(value).encode())
        command = [sys.executable, str(DISCOVER), str(source)]
        if prior is not None:
            path = self.folder / 'prior.json'
            path.write_text(json.dumps(prior))
            command += ['--prior', str(path)]
        process = subprocess.run(command, capture_output=True, text=True, check=False, timeout=20)
        self.assertEqual(process.stderr, '')
        return process.returncode, json.loads(process.stdout)

    def run_coverage(self, ledger, inventory):
        ledger_path = self.folder / 'coverage.json'
        inventory_path = self.folder / 'inventory.json'
        ledger_path.write_text(json.dumps(ledger))
        inventory_path.write_text(json.dumps(inventory))
        process = subprocess.run([sys.executable, str(COVERAGE), str(ledger_path),
                                  '--discovery', str(inventory_path)], capture_output=True,
                                 text=True, check=False, timeout=20)
        self.assertEqual(process.stderr, '')
        return process.returncode, json.loads(process.stdout)

    def test_two_exact_rows_on_foreign_parent_remain_distinct_and_reconcile(self):
        comment = PARENT + '?comment_id=2001&fbclid=tracking'
        reply = PARENT + '?comment_id=2001&reply_comment_id=3001'
        code, inventory = self.run_discover(snapshot(rows=[row('row-1', url=comment),
                                                             row('row-2', 'reply', reply)]))
        self.assertEqual(code, 0)
        self.assertTrue(inventory['discovery_complete'])
        self.assertEqual(len(inventory['candidates']), 2)
        self.assertEqual(inventory['candidates'][0]['navigation_url'], PARENT + '?comment_id=2001')
        ledger = coverage([PARENT + '?fbclid=other&comment_id=2001', reply])
        ledger['account_url'] = 'https://m.facebook.com/river_example/'
        code, checked = self.run_coverage(ledger, inventory)
        self.assertEqual(code, 0)
        self.assertTrue(checked['complete'])
        self.assertEqual(checked['counts']['resolved_candidates'], 2)
        self.assertEqual(checked['counts']['activity_rows'], 2)

    def test_same_row_across_pages_dedups_but_distinct_parent_rows_do_not(self):
        first = discovery.update(snapshot(boundary='more', rows=[row('row-1', url=PARENT + '?comment_id=2001')]))
        second = discovery.update(snapshot('batch-2', rows=[row('row-1', url=PARENT + '?comment_id=2001'),
                                                          row('row-2', url=PARENT + '?comment_id=2002')]), first)
        self.assertEqual(len(second['candidates']), 2)
        self.assertEqual(second['batches'][1]['duplicate_count'], 1)
        self.assertEqual(second['batches'][1]['new_count'], 1)
        self.assertTrue(second['discovery_complete'])
        self.assertEqual(second['candidates'][0]['last_batch'], 'batch-2')

    def test_unsupported_row_and_missing_author_remain_candidates_and_gaps(self):
        data = snapshot(rows=[row('row-1', url='https://example.invalid/post/1', author=''),
                              row('row-2', url=PARENT)])
        code, inventory = self.run_discover(data)
        self.assertEqual(code, 0)
        self.assertEqual(len(inventory['candidates']), 2)
        self.assertFalse(inventory['discovery_complete'])
        self.assertEqual(inventory['candidates'][0]['navigation_url'], '')
        self.assertIn('candidate:' + inventory['candidates'][0]['id'] + ':author_url', inventory['blockers'])
        code, checked = self.run_coverage(coverage([]), inventory)
        self.assertEqual(code, 1)
        self.assertTrue(checked['actions_complete'])
        self.assertFalse(checked['review_complete'])

    def test_identity_filters_loading_unparsed_and_category_fail_closed(self):
        for field, value in (('account_verified', False), ('people_filter', 'restricted'),
                             ('search_filter', 'restricted'), ('loading', True), ('unparsed_rows', 1)):
            with self.subTest(field=field):
                code, inventory = self.run_discover(snapshot(**{field: value}))
                self.assertEqual(code, 0)
                self.assertFalse(inventory['discovery_complete'])
        code, result = self.run_discover(snapshot(category='unknown'))
        self.assertEqual(code, 2)
        self.assertEqual(result['error'], 'invalid_category')

    def test_no_progress_or_loading_can_recover_with_later_advancing_terminal_batch(self):
        first = discovery.update(snapshot(boundary='more', rows=[]))
        self.assertIn('batch:batch-1:no_progress', first['blockers'])
        later = discovery.update(snapshot('batch-2', rows=[row('row-1')]), first)
        self.assertTrue(later['discovery_complete'])
        loading = discovery.update(snapshot(boundary='more', rows=[], loading=True))
        recovered = discovery.update(snapshot('batch-2', rows=[row('row-1')]), loading)
        self.assertTrue(recovered['discovery_complete'])
        no_advance = discovery.update(snapshot('batch-2', rows=[]), first)
        self.assertFalse(no_advance['discovery_complete'])
        stalled = discovery.update(snapshot(boundary='stalled', rows=[row('row-1')]))
        resumed = discovery.update(snapshot('batch-2', rows=[row('row-2')]), stalled)
        self.assertTrue(resumed['discovery_complete'])

    def test_interrupted_resume_and_scope_boundary(self):
        first = discovery.update(snapshot(boundary='more', rows=[row('row-1')]))
        self.assertFalse(first['discovery_complete'])
        for boundary in ('end', 'scope_boundary'):
            with self.subTest(boundary=boundary):
                code, second = self.run_discover(snapshot('batch-2', boundary=boundary, rows=[]), first)
                self.assertEqual(code, 0)
                self.assertTrue(second['discovery_complete'])
                code, result = self.run_coverage(coverage([row('row-1')['navigation_url']]), second)
                self.assertEqual(code, 0)
                self.assertTrue(result['complete'])

    def test_empty_terminal_snapshot_blocks_coverage_even_with_boundary_evidence(self):
        code, empty = self.run_discover(snapshot(rows=[]))
        self.assertEqual(code, 0)
        self.assertFalse(empty['discovery_complete'])
        self.assertEqual(empty['blockers'], ['no_candidates'])
        code, result = self.run_coverage(coverage([]), empty)
        self.assertEqual(code, 1)
        self.assertFalse(result['complete'])
        self.assertEqual(result['blockers'], ['discovery:no_candidates'])

        empty.update(discovery_complete=True, blockers=[])
        code, result = self.run_coverage(coverage([]), empty)
        self.assertEqual(code, 2)
        self.assertFalse(result['complete'])
        self.assertEqual(result['blockers'], ['forged_derived'])

    def test_wrong_scope_account_duplicate_batch_and_conflict(self):
        first = discovery.update(snapshot())
        for change in ({'scope_id': 'different'}, {'scope': 'Different synthetic scope'},
                       {'account_url': 'https://www.facebook.com/other_example'}):
            with self.subTest(change=change):
                altered = snapshot('batch-2')
                altered.update(change)
                code, result = self.run_discover(altered, first)
                self.assertEqual(code, 2)
                self.assertEqual(result['error'], 'scope_mismatch')
        code, result = self.run_discover(snapshot(), first)
        self.assertEqual(code, 2)
        self.assertEqual(result['error'], 'duplicate_or_excess_batch')
        conflicted = discovery.update(snapshot('batch-2', rows=[row('2001', url=PARENT + '?comment_id=9999')]), first)
        self.assertEqual(conflicted['batches'][1]['conflict_count'], 1)
        self.assertFalse(conflicted['discovery_complete'])

    def test_parent_cannot_satisfy_child_and_kind_resolution_must_match(self):
        reply = row('reply-1', 'reply', PARENT)
        inventory = discovery.update(snapshot(rows=[reply]))
        code, result = self.run_coverage(coverage([PARENT]), inventory)
        self.assertEqual(code, 1)
        self.assertEqual(result['counts']['resolved_candidates'], 0)
        candidate = inventory['candidates'][0]
        candidate['resolved_source_url'] = PARENT + '?comment_id=2001'
        candidate['resolution_note'] = 'Observed synthetic thread item'
        code, result = self.run_coverage(coverage([candidate['resolved_source_url']]), inventory)
        self.assertEqual(code, 1)
        self.assertEqual(result['counts']['resolved_candidates'], 0)

    def test_duplicate_exact_mapping_requires_evidence_for_each(self):
        source = PARENT + '?comment_id=2001'
        inventory = discovery.update(snapshot(rows=[row('row-1', url=source), row('row-2', url=source)]))
        code, result = self.run_coverage(coverage([source]), inventory)
        self.assertEqual(code, 1)
        self.assertIn('candidate:' + inventory['candidates'][0]['id'] + ':duplicate_resolution_note', result['blockers'])
        for candidate in inventory['candidates']:
            candidate['resolution_note'] = 'Observed same synthetic exact item twice'
        code, result = self.run_coverage(coverage([source]), inventory)
        self.assertEqual(code, 0)

    def test_missing_target_and_forged_derived_flags_fail_closed(self):
        inventory = discovery.update(snapshot())
        code, result = self.run_coverage(coverage([]), inventory)
        self.assertEqual(code, 1)
        self.assertTrue(result['actions_complete'])
        inventory['discovery_complete'] = False
        code, result = self.run_coverage(coverage([]), inventory)
        self.assertEqual(code, 2)
        self.assertEqual(result['blockers'], ['forged_derived'])
        inventory = discovery.update(snapshot())
        inventory['batches'][0]['new_count'] = 0
        code, result = self.run_discover(snapshot('batch-2'), inventory)
        self.assertEqual(code, 2)
        self.assertEqual(result['error'], 'invalid_batch_counts')

    def test_coverage_requires_matching_account_scope_id_and_scope_text(self):
        inventory = discovery.update(snapshot())
        for field, value, blocker in (
                ('account_url', 'https://www.facebook.com/other_example', 'account_mismatch'),
                ('scope_id', 'scope-2', 'scope_id_mismatch'),
                ('scope_id', 'scope-1 ', 'scope_id_mismatch'),
                ('scope', SCOPE + ' ', 'scope_mismatch')):
            with self.subTest(field=field, value=value):
                ledger = coverage([PARENT + '?comment_id=2001'])
                ledger[field] = value
                code, result = self.run_coverage(ledger, inventory)
                self.assertEqual(code, 2)
                self.assertFalse(result['complete'])
                self.assertEqual(result['blockers'], [blocker])

    def test_row_refs_reject_impossible_duplicate_history_and_malformed_refs(self):
        first = discovery.update(snapshot(boundary='more'))
        second = discovery.update(snapshot('batch-2', rows=[]), first)
        forged = json.loads(json.dumps(second))
        forged['batches'][1].update(row_count=1, duplicate_count=1,
                                    row_refs=[dict(first['batches'][0]['row_refs'][0])])
        code, result = self.run_discover(snapshot('batch-3'), forged)
        self.assertEqual(code, 2)
        self.assertEqual(result['error'], 'invalid_row_history')
        for mutation in ('missing', 'extra', 'bad_fingerprint', 'unknown_field'):
            with self.subTest(mutation=mutation):
                altered = json.loads(json.dumps(first))
                refs = altered['batches'][0]['row_refs']
                if mutation == 'missing':
                    refs.clear()
                elif mutation == 'extra':
                    refs.append(dict(refs[0]))
                elif mutation == 'bad_fingerprint':
                    refs[0]['fingerprint'] = 'not-a-hash'
                else:
                    refs[0]['private_text'] = 'synthetic secret'
                code, result = self.run_discover(snapshot('batch-2'), altered)
                self.assertEqual(code, 2)
                self.assertIn('error', result)
                self.assertNotIn('synthetic secret', json.dumps(result))
        self.assertEqual(set(first['batches'][0]['row_refs'][0]), {'id', 'fingerprint'})

    def test_helpers_have_no_outgoing_database_or_write_capability(self):
        sources = [DISCOVER.read_text(), COVERAGE.read_text(), CAPTURE.read_text()]
        for source in sources:
            for forbidden in ('fetch(', 'XMLHttpRequest', 'sqlite3', 'localStorage',
                              'sessionStorage', 'document.cookie', '.write_text(', '.write_bytes('):
                self.assertNotIn(forbidden, source)
        path = self.folder / 'snapshot.json'
        path.write_text(json.dumps(snapshot()))
        before = path.read_bytes()
        files_before = set(self.folder.iterdir())
        process = subprocess.run([sys.executable, str(DISCOVER), str(path)],
                                 capture_output=True, text=True, check=False, timeout=20)
        self.assertEqual(process.returncode, 0)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(set(self.folder.iterdir()), files_before)

    def test_strict_json_bounds_and_selector_errors(self):
        for raw in (b'{', b' ' * (2 * 1024 * 1024 + 1),
                    b'{"version":1,"version":1}', b'{"version":NaN}',
                    b'{"version":' + b'9' * 5000 + b'}'):
            code, result = self.run_discover(None, raw=raw)
            self.assertEqual(code, 2)
            self.assertIn('error', result)
        for value in ('comment_id=1&comment_id=2', 'comment_id=',
                      'comment_id=1&reply_comment_id='):
            with self.subTest(value=value):
                code, inventory = self.run_discover(snapshot(rows=[row('bad', url=PARENT + '?' + value)]))
                self.assertEqual(code, 0)
                self.assertEqual(inventory['candidates'][0]['navigation_url'], '')
        for bad in ('https://evil.invalid/river_example',
                    'https://name:password@www.facebook.com/river_example'):
            code, result = self.run_discover(dict(snapshot(), account_url=bad))
            self.assertEqual(code, 2)
            self.assertIn('error', result)
        code, result = self.run_discover(snapshot(boundary=[]))
        self.assertEqual(code, 2)
        self.assertEqual(result['error'], 'invalid_boundary')
        code, result = self.run_discover(snapshot(page_url='https://www.facebook.com/123456/allactivity/extra'))
        self.assertEqual(code, 2)
        self.assertEqual(result['error'], 'invalid_page_url')
        self.assertEqual(discovery.navigation_url('https://www.facebook.com/photo/?fbid=123&fbclid=track'),
                         'https://www.facebook.com/photo?fbid=123')

    def test_capture_function_reads_only_structural_dom_metadata(self):
        process = subprocess.run(['node', '-e', NODE_HARNESS, str(CAPTURE)],
                                 capture_output=True, text=True, check=False, timeout=20)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stderr, '')
        value = json.loads(process.stdout)
        good, drift = value['good'], value['drift']
        self.assertEqual(good['batch']['category'], 'comments')
        self.assertEqual(good['batch']['people_filter'], 'all')
        self.assertEqual(good['batch']['search_filter'], 'none')
        self.assertEqual(good['batch']['unparsed_rows'], 0)
        self.assertEqual([item['kind'] for item in good['batch']['rows']], ['comment', 'reply'])
        self.assertEqual(len(good['batch']['rows']), 2)
        self.assertEqual(len(value['mixedRoleView']['batch']['rows']), 2)
        self.assertEqual(value['mixedRoleView']['batch']['unparsed_rows'], 1)
        self.assertEqual(value['unmatchedView']['batch']['unparsed_rows'], 1)
        self.assertEqual(value['ambiguous']['batch']['people_filter'], 'restricted')
        self.assertEqual(value['ambiguous']['batch']['search_filter'], 'restricted')
        self.assertEqual(value['unsafe']['batch']['rows'][0]['author_url'], '')
        self.assertEqual(value['unsafe']['batch']['rows'][0]['navigation_url'], PARENT + '?comment_id=2001')
        self.assertEqual(value['unsafePort']['batch']['rows'][0]['navigation_url'], '')
        self.assertEqual(value['unsafeAccount']['account_url'], '')
        self.assertEqual(value['unsafePage']['batch']['page_url'], '')
        self.assertNotIn('category_key', json.dumps(good))
        self.assertNotIn('synthetic-secret', json.dumps(value['unsafe']))
        self.assertNotIn('synthetic-private-text', json.dumps(value['unsafe']))
        self.assertNotIn('synthetic-secret', json.dumps(value['unsafePage']))
        self.assertGreaterEqual(value['empty']['batch']['unparsed_rows'], 1)
        self.assertEqual(value['empty']['batch']['category'], 'comments')
        self.assertEqual(value['alternateResult']['batch']['rows'], [])
        self.assertGreaterEqual(value['alternateResult']['batch']['unparsed_rows'], 1)
        self.assertEqual(drift['batch']['category'], 'unknown')
        self.assertEqual(drift['batch']['people_filter'], 'restricted')
        self.assertEqual(drift['batch']['search_filter'], 'restricted')
        self.assertEqual(drift['batch']['unparsed_rows'], 1)


if __name__ == '__main__':
    unittest.main()
