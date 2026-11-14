#!/usr/bin/env python3
"""Build a bounded, read-only Facebook activity navigation inventory."""

import hashlib
import json
from datetime import datetime
from pathlib import Path
import re
import sys
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


MAX_BYTES = 2 * 1024 * 1024
HOSTS = {'facebook.com', 'www.facebook.com', 'm.facebook.com'}
SEMANTIC = {'id', 'story_fbid', 'fbid', 'v', 'comment_id', 'reply_comment_id'}
SNAPSHOT_FIELDS = {'version', 'account_url', 'scope_id', 'scope', 'batch'}
BATCH_FIELDS = {'id', 'observed_at', 'page_url', 'category', 'account_verified',
                'people_filter', 'search_filter', 'boundary', 'boundary_note',
                'loading', 'unparsed_rows', 'rows'}
ROW_FIELDS = {'row_key', 'kind', 'author_url', 'navigation_url'}
STORED_BATCH_FIELDS = BATCH_FIELDS - {'rows'} | {'row_count', 'new_count',
                                                  'duplicate_count', 'conflict_count', 'row_refs'}
CANDIDATE_FIELDS = {'id', 'row_key', 'kind', 'author_url', 'navigation_url',
                    'first_batch', 'last_batch', 'resolved_source_url', 'resolution_note'}
INVENTORY_FIELDS = {'version', 'account_url', 'scope_id', 'scope', 'batches',
                    'candidates', 'discovery_complete', 'blockers'}
BOUNDARIES = {'more', 'end', 'scope_boundary', 'blocked', 'stalled', 'user_stopped'}


class Invalid(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def pairs_unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Invalid('duplicate_key')
        result[key] = value
    return result


def reject_constant(_value):
    raise Invalid('nonfinite_number')


def read_json(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise Invalid('oversized_json')
    return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs_unique,
                      parse_constant=reject_constant)


def fields(value, expected, code):
    if not isinstance(value, dict) or value.keys() != expected:
        raise Invalid('invalid_' + code)


def string(value, code, maximum=4096, nonblank=False):
    if not isinstance(value, str) or len(value) > maximum or (nonblank and not value.strip()):
        raise Invalid('invalid_' + code)


def integer(value, code, maximum):
    if type(value) is not int or value < 0 or value > maximum:
        raise Invalid('invalid_' + code)


def timestamp(value):
    string(value, 'observed_at', 100, True)
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as error:
        raise Invalid('invalid_observed_at') from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise Invalid('invalid_observed_at')


def url_parts(value):
    string(value, 'url', 4096, True)
    if any(char.isspace() or ord(char) < 32 or char == '\\' for char in value):
        raise Invalid('invalid_url')
    try:
        part = urlsplit(value)
        if (part.scheme != 'https' or part.hostname not in HOSTS or part.username is not None or
                part.password is not None or part.port is not None or part.fragment):
            raise Invalid('invalid_url')
        return part
    except ValueError as error:
        raise Invalid('invalid_url') from error


def semantic_query(part):
    values = {}
    for key, value in parse_qsl(part.query, keep_blank_values=True):
        if key in SEMANTIC:
            if key in values or not value.strip():
                raise Invalid('invalid_selector')
            values[key] = value
    return values


def profile_url(value):
    part = url_parts(value)
    query = semantic_query(part)
    path = part.path.rstrip('/') or '/'
    if path == '/profile.php' and set(query) == {'id'} and query['id'].isdigit():
        return 'https://www.facebook.com/profile.php?id=' + query['id']
    if (re.fullmatch(r'/[A-Za-z0-9_.]+', path) and
            path not in {'/me', '/groups', '/watch', '/reel', '/photo.php',
                         '/permalink.php', '/profile.php', '/allactivity'} and
            not part.query):
        return 'https://www.facebook.com' + path
    raise Invalid('invalid_profile_url')


def page_url(value):
    part = url_parts(value)
    if not re.fullmatch(r'/[^/]+/allactivity/?', part.path, re.IGNORECASE):
        raise Invalid('invalid_page_url')
    if semantic_query(part):
        raise Invalid('invalid_page_url')
    return urlunsplit(('https', 'www.facebook.com', part.path.rstrip('/') or '/', '', ''))


def navigation_url(value):
    """Canonical supported navigation URL, or Invalid for an unsupported row."""
    part = url_parts(value)
    query = semantic_query(part)
    path = part.path.rstrip('/') or '/'
    base = bool(re.fullmatch(r'/[^/]+/posts/[^/]+', path) or
                re.fullmatch(r'/groups/[^/]+/(?:posts|permalink)/[^/]+', path) or
                re.fullmatch(r'/(?:[^/]+/)?(?:photos|videos)/[^/]+(?:/[^/]+)?', path) or
                re.fullmatch(r'/reel/[^/]+', path) or
                path == '/permalink.php' and {'story_fbid', 'id'} <= set(query) or
                path in ('/photo.php', '/photo') and 'fbid' in query or
                path == '/watch' and 'v' in query)
    if not base:
        raise Invalid('unsupported_navigation_url')
    selectors = [(key, query[key]) for key in sorted(query)]
    return urlunsplit(('https', 'www.facebook.com', path, urlencode(selectors), ''))


def safe_profile(value):
    if not value:
        return ''
    try:
        return profile_url(value)
    except Invalid:
        return ''


def safe_navigation(value):
    if not value:
        return ''
    try:
        return navigation_url(value)
    except Invalid:
        return ''


def candidate_id(account_url, scope_id, row_key):
    raw = json.dumps([account_url, scope_id, row_key], ensure_ascii=False,
                     separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()[:24]


def row_fingerprint(row):
    raw = json.dumps([row['kind'], row['author_url'], row['navigation_url']],
                     ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()[:24]


def validate_snapshot(value):
    fields(value, SNAPSHOT_FIELDS, 'snapshot')
    if type(value['version']) is not int or value['version'] != 1:
        raise Invalid('invalid_version')
    value = dict(value)
    value['account_url'] = profile_url(value['account_url'])
    string(value['scope_id'], 'scope_id', 100, True)
    string(value['scope'], 'scope', 4096, True)
    batch = value['batch']
    fields(batch, BATCH_FIELDS, 'batch')
    batch = dict(batch)
    string(batch['id'], 'batch_id', 100, True)
    timestamp(batch['observed_at'])
    batch['page_url'] = page_url(batch['page_url'])
    if batch['category'] != 'comments':
        raise Invalid('invalid_category')
    if type(batch['account_verified']) is not bool or type(batch['loading']) is not bool:
        raise Invalid('invalid_batch_bool')
    if batch['people_filter'] not in ('all', 'restricted') or batch['search_filter'] not in ('none', 'restricted'):
        raise Invalid('invalid_filter')
    if not isinstance(batch['boundary'], str) or batch['boundary'] not in BOUNDARIES:
        raise Invalid('invalid_boundary')
    string(batch['boundary_note'], 'boundary_note', 4096, True)
    integer(batch['unparsed_rows'], 'unparsed_rows', 2000)
    if not isinstance(batch['rows'], list) or len(batch['rows']) > 2000:
        raise Invalid('invalid_rows')
    rows = []
    for row in batch['rows']:
        fields(row, ROW_FIELDS, 'row')
        string(row['row_key'], 'row_key', 500, True)
        if row['kind'] not in ('comment', 'reply', 'unknown'):
            raise Invalid('invalid_kind')
        string(row['author_url'], 'author_url')
        string(row['navigation_url'], 'navigation_url')
        rows.append(dict(row, author_url=safe_profile(row['author_url']),
                         navigation_url=safe_navigation(row['navigation_url'])))
    batch['rows'] = rows
    value['batch'] = batch
    return value


def derived(value):
    blockers = []
    batches = value['batches']
    candidates = value['candidates']
    if not batches:
        blockers.append('no_batches')
    if not candidates:
        blockers.append('no_candidates')
    terminal = bool(batches and batches[-1]['boundary'] in ('end', 'scope_boundary'))
    for index, batch in enumerate(batches):
        bid = batch['id']
        recovered = terminal and any(later['new_count'] > 0 for later in batches[index + 1:])
        for field, allowed in (('account_verified', True), ('category', 'comments'),
                               ('people_filter', 'all'), ('search_filter', 'none')):
            if batch[field] != allowed:
                blockers.append('batch:' + bid + ':' + field)
        for field in ('unparsed_rows', 'conflict_count'):
            if batch[field]:
                blockers.append('batch:' + bid + ':' + field)
        if batch['loading'] and not recovered:
            blockers.append('batch:' + bid + ':loading')
        if batch['boundary'] in ('blocked', 'user_stopped') or batch['boundary'] == 'stalled' and not recovered:
            blockers.append('batch:' + bid + ':boundary_' + batch['boundary'])
        if batch['boundary'] == 'more' and batch['new_count'] == 0 and not recovered:
            blockers.append('batch:' + bid + ':no_progress')
    if batches and batches[-1]['boundary'] not in ('end', 'scope_boundary'):
        blockers.append('boundary_incomplete')
    for candidate in candidates:
        cid = candidate['id']
        if candidate['kind'] == 'unknown':
            blockers.append('candidate:' + cid + ':unknown_kind')
        if not candidate['author_url']:
            blockers.append('candidate:' + cid + ':author_url')
        elif candidate['author_url'] != value['account_url']:
            blockers.append('candidate:' + cid + ':foreign_author')
        if not candidate['navigation_url']:
            blockers.append('candidate:' + cid + ':navigation_url')
    return sorted(set(blockers))


def validate_inventory(value):
    fields(value, INVENTORY_FIELDS, 'inventory')
    if type(value['version']) is not int or value['version'] != 1:
        raise Invalid('invalid_version')
    account = profile_url(value['account_url'])
    if account != value['account_url']:
        raise Invalid('invalid_account_url')
    string(value['scope_id'], 'scope_id', 100, True)
    string(value['scope'], 'scope', 4096, True)
    batches, candidates = value['batches'], value['candidates']
    if not isinstance(batches, list) or len(batches) > 1000:
        raise Invalid('invalid_batches')
    if not isinstance(candidates, list) or len(candidates) > 10000:
        raise Invalid('invalid_candidates')
    seen_batches = set()
    for batch in batches:
        fields(batch, STORED_BATCH_FIELDS, 'stored_batch')
        string(batch['id'], 'batch_id', 100, True)
        if batch['id'] in seen_batches:
            raise Invalid('duplicate_batch_id')
        seen_batches.add(batch['id'])
        timestamp(batch['observed_at'])
        if page_url(batch['page_url']) != batch['page_url']:
            raise Invalid('invalid_page_url')
        if batch['category'] != 'comments' or type(batch['account_verified']) is not bool or type(batch['loading']) is not bool:
            raise Invalid('invalid_batch_metadata')
        if batch['people_filter'] not in ('all', 'restricted') or batch['search_filter'] not in ('none', 'restricted'):
            raise Invalid('invalid_filter')
        if not isinstance(batch['boundary'], str) or batch['boundary'] not in BOUNDARIES:
            raise Invalid('invalid_boundary')
        string(batch['boundary_note'], 'boundary_note', 4096, True)
        for name in ('unparsed_rows', 'row_count', 'new_count', 'duplicate_count', 'conflict_count'):
            integer(batch[name], name, 2000)
        if batch['new_count'] + batch['duplicate_count'] + batch['conflict_count'] != batch['row_count']:
            raise Invalid('invalid_batch_counts')
    seen_ids = set()
    by_id = {}
    for candidate in candidates:
        fields(candidate, CANDIDATE_FIELDS, 'candidate')
        string(candidate['row_key'], 'row_key', 500, True)
        if candidate['kind'] not in ('comment', 'reply', 'unknown'):
            raise Invalid('invalid_kind')
        if candidate['id'] != candidate_id(account, value['scope_id'], candidate['row_key']) or candidate['id'] in seen_ids:
            raise Invalid('invalid_candidate_id')
        seen_ids.add(candidate['id'])
        by_id[candidate['id']] = candidate
        for key in ('author_url', 'navigation_url', 'resolved_source_url'):
            string(candidate[key], key)
        if candidate['author_url'] and profile_url(candidate['author_url']) != candidate['author_url']:
            raise Invalid('invalid_author_url')
        if candidate['navigation_url'] and navigation_url(candidate['navigation_url']) != candidate['navigation_url']:
            raise Invalid('invalid_navigation_url')
        if candidate['resolved_source_url']:
            navigation_url(candidate['resolved_source_url'])
        string(candidate['resolution_note'], 'resolution_note')
        if candidate['first_batch'] not in seen_batches or candidate['last_batch'] not in seen_batches:
            raise Invalid('invalid_candidate_batch')
    seen_rows = {}
    last_equal = {}
    for batch in batches:
        refs = batch['row_refs']
        if not isinstance(refs, list) or len(refs) > 2000 or len(refs) != batch['row_count']:
            raise Invalid('invalid_row_refs')
        counts = {'new_count': 0, 'duplicate_count': 0, 'conflict_count': 0}
        for ref in refs:
            fields(ref, {'id', 'fingerprint'}, 'row_ref')
            if (not isinstance(ref['id'], str) or ref['id'] not in by_id or
                    not isinstance(ref['fingerprint'], str) or
                    not re.fullmatch(r'[0-9a-f]{24}', ref['fingerprint'])):
                raise Invalid('invalid_row_ref')
            cid = ref['id']
            if cid not in seen_rows:
                if (ref['fingerprint'] != row_fingerprint(by_id[cid]) or
                        by_id[cid]['first_batch'] != batch['id']):
                    raise Invalid('invalid_row_history')
                seen_rows[cid] = ref['fingerprint']
                counts['new_count'] += 1
            elif ref['fingerprint'] == seen_rows[cid]:
                counts['duplicate_count'] += 1
            else:
                counts['conflict_count'] += 1
            if ref['fingerprint'] == seen_rows[cid]:
                last_equal[cid] = batch['id']
        if any(batch[key] != count for key, count in counts.items()):
            raise Invalid('invalid_batch_counts')
    if set(seen_rows) != seen_ids or any(by_id[cid]['last_batch'] != last_equal[cid] for cid in seen_ids):
        raise Invalid('invalid_row_history')
    if type(value['discovery_complete']) is not bool or not isinstance(value['blockers'], list):
        raise Invalid('invalid_derived')
    expected = derived(value)
    if value['blockers'] != expected or value['discovery_complete'] != (not expected):
        raise Invalid('forged_derived')
    return value


def update(snapshot, prior=None):
    snapshot = validate_snapshot(snapshot)
    if prior is None:
        value = {'version': 1, 'account_url': snapshot['account_url'],
                 'scope_id': snapshot['scope_id'], 'scope': snapshot['scope'],
                 'batches': [], 'candidates': []}
    else:
        validate_inventory(prior)
        if any(prior[key] != snapshot[key] for key in ('account_url', 'scope_id', 'scope')):
            raise Invalid('scope_mismatch')
        value = json.loads(json.dumps(prior))
    batch = snapshot['batch']
    if len(value['batches']) >= 1000 or any(item['id'] == batch['id'] for item in value['batches']):
        raise Invalid('duplicate_or_excess_batch')
    by_key = {candidate['row_key']: candidate for candidate in value['candidates']}
    counts = {'row_count': len(batch['rows']), 'new_count': 0,
              'duplicate_count': 0, 'conflict_count': 0}
    refs = []
    for row in batch['rows']:
        refs.append({'id': candidate_id(value['account_url'], value['scope_id'], row['row_key']),
                     'fingerprint': row_fingerprint(row)})
        previous = by_key.get(row['row_key'])
        if previous is None:
            if len(value['candidates']) >= 10000:
                raise Invalid('excess_candidates')
            candidate = {'id': candidate_id(value['account_url'], value['scope_id'], row['row_key']),
                         **row, 'first_batch': batch['id'], 'last_batch': batch['id'],
                         'resolved_source_url': '', 'resolution_note': ''}
            value['candidates'].append(candidate)
            by_key[row['row_key']] = candidate
            counts['new_count'] += 1
        elif all(previous[key] == row[key] for key in ('kind', 'author_url', 'navigation_url')):
            previous['last_batch'] = batch['id']
            counts['duplicate_count'] += 1
        else:
            counts['conflict_count'] += 1
    value['batches'].append({key: batch[key] for key in BATCH_FIELDS - {'rows'}} | counts | {'row_refs': refs})
    value['blockers'] = derived(value)
    value['discovery_complete'] = not value['blockers']
    validate_inventory(value)
    return value


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        if len(argv) not in (1, 3) or len(argv) == 3 and argv[1] != '--prior':
            raise Invalid('invalid_arguments')
        snapshot = read_json(argv[0])
        prior = read_json(argv[2]) if len(argv) == 3 else None
        result = update(snapshot, prior)
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 0
    except (OSError, UnicodeError, ValueError, RecursionError, TypeError) as error:
        print(json.dumps({'error': error.code if isinstance(error, Invalid) else 'invalid_json'}))
        return 2


if __name__ == '__main__':
    sys.exit(main())
