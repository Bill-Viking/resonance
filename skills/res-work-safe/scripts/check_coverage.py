#!/usr/bin/env python3
"""Check a private Work Safe coverage ledger without opening application data."""

import json
from pathlib import Path
import sys
from urllib.parse import parse_qsl, urlsplit

# The same files ship in a verified desktop assistant kit. A read-only check
# must not add a cache entry that changes that kit's exact file inventory.
sys.dont_write_bytecode = True

from discover_facebook import Invalid as InvalidDiscovery
from discover_facebook import navigation_url, profile_url, validate_inventory
from discover_facebook import read_json as read_discovery_json


MAX_BYTES = 2 * 1024 * 1024
MAX_STRING = 4096
FACEBOOK_DOMAINS = ('facebook.com', 'fb.com', 'fb.me', 'fb.watch')
DISCOVERY_SURFACES = frozenset(('comments_on_own_posts', 'comments_on_others_posts',
                               'replies_on_own_posts', 'replies_on_others_posts'))
SURFACES = DISCOVERY_SURFACES | {'posts', 'media'}
SURFACE_STATES = frozenset(('complete', 'partial', 'blocked', 'outside_scope'))
MEDIA_STATES = frozenset(('none', 'inspected', 'unchecked', 'unavailable'))
ACTIONS = frozenset(('review', 'remove', 'edit'))
TARGET_STATES = frozenset(('unresolved', 'reviewed', 'pending', 'attempted',
                           'verified_removed', 'verified_edited', 'kept_by_user'))
TOP_FIELDS = frozenset(('version', 'account_url', 'scope_id', 'scope', 'surfaces', 'targets', 'unresolved'))
TARGET_FIELDS = frozenset(('id', 'source_url', 'author_verified', 'text_inspected', 'media',
                           'requested_action', 'state', 'receipt_url', 'receipt_note'))


class InvalidLedger(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidLedger('duplicate_key')
        result[key] = value
    return result


def reject_constant(_value):
    raise InvalidLedger('nonfinite_number')


def require_string(value, field, *, maximum=MAX_STRING, nonblank=False):
    if not isinstance(value, str) or len(value) > maximum or (nonblank and not value.strip()):
        raise InvalidLedger('invalid_' + field)


def require_fields(value, fields, field):
    if not isinstance(value, dict) or value.keys() != fields:
        raise InvalidLedger('invalid_' + field)


def valid_url(value, *, https_only=False):
    if not value or any(char.isspace() or ord(char) < 32 or char == '\\' for char in value):
        return False
    try:
        parts = urlsplit(value)
        schemes = ('https',) if https_only else ('http', 'https')
        # Check the original authority before hostname lowercases Unicode.
        return (parts.scheme in schemes and parts.netloc.isascii() and
                bool(parts.hostname) and parts.username is None and parts.password is None and
                (parts.port is None or 0 < parts.port <= 65535))
    except ValueError:
        return False


def is_facebook_url(value):
    host = (urlsplit(value).hostname or '').rstrip('.')
    return any(host == domain or host.endswith('.' + domain) for domain in FACEBOOK_DOMAINS)


def account_profile(value):
    require_string(value, 'account_url', nonblank=True)
    if not valid_url(value, https_only=True):
        raise InvalidLedger('invalid_account_url')
    if is_facebook_url(value):
        try:
            return profile_url(value)
        except InvalidDiscovery as error:
            raise InvalidLedger('invalid_account_url') from error
    return value


def validate(data):
    if not isinstance(data, dict):
        raise InvalidLedger('invalid_top_fields')
    version = data.get('version')
    if type(version) is not int or version != 2:
        raise InvalidLedger('invalid_version')
    account = account_profile(data.get('account_url'))
    require_string(data.get('scope_id'), 'scope_id', maximum=100, nonblank=True)
    require_fields(data, TOP_FIELDS, 'top_fields')
    require_string(data['scope'], 'scope', nonblank=True)
    require_fields(data['surfaces'], SURFACES, 'surfaces')
    for name, surface in data['surfaces'].items():
        require_fields(surface, frozenset(('status', 'evidence')), 'surface_' + name)
        if not isinstance(surface['status'], str) or surface['status'] not in SURFACE_STATES:
            raise InvalidLedger('invalid_surface_' + name + '_status')
        require_string(surface['evidence'], 'surface_' + name + '_evidence', nonblank=True)
    if not isinstance(data['targets'], list) or len(data['targets']) > 10000:
        raise InvalidLedger('invalid_targets')
    if not isinstance(data['unresolved'], list) or len(data['unresolved']) > 1000:
        raise InvalidLedger('invalid_unresolved')
    for name in data['unresolved']:
        require_string(name, 'unresolved', nonblank=True)
    facebook_account = is_facebook_url(account)
    comments_outside_scope = all(data['surfaces'][name]['status'] == 'outside_scope'
                                 for name in DISCOVERY_SURFACES)
    ids, urls = set(), set()
    for target in data['targets']:
        require_fields(target, TARGET_FIELDS, 'target_fields')
        require_string(target['id'], 'target_id', nonblank=True)
        if target['id'] in ids:
            raise InvalidLedger('duplicate_target_id')
        ids.add(target['id'])
        for field in ('source_url', 'receipt_url'):
            require_string(target[field], field)
            if target[field] and not valid_url(target[field]):
                raise InvalidLedger('invalid_' + field)
        source = target['source_url']
        if source:
            if is_facebook_url(source):
                if not facebook_account:
                    raise InvalidLedger('facebook_target_requires_facebook_account')
                if comments_outside_scope and any(
                        key in ('comment_id', 'reply_comment_id')
                        for key, _ in parse_qsl(urlsplit(source).query, keep_blank_values=True)):
                    raise InvalidLedger('facebook_comment_outside_scope')
            if source in urls:
                raise InvalidLedger('duplicate_source_url')
            urls.add(source)
        for field in ('author_verified', 'text_inspected'):
            if type(target[field]) is not bool:
                raise InvalidLedger('invalid_' + field)
        if not isinstance(target['media'], str) or target['media'] not in MEDIA_STATES:
            raise InvalidLedger('invalid_media')
        if not isinstance(target['requested_action'], str) or target['requested_action'] not in ACTIONS:
            raise InvalidLedger('invalid_requested_action')
        if not isinstance(target['state'], str) or target['state'] not in TARGET_STATES:
            raise InvalidLedger('invalid_state')
        if not target['source_url'] and target['state'] != 'unresolved':
            raise InvalidLedger('invalid_source_url')
        require_string(target['receipt_note'], 'receipt_note')


def check(data):
    validate(data)
    blockers = []
    review_ok = True
    action_ok = True
    counts = {'reviewed_targets': 0, 'verified_removals': 0, 'verified_edits': 0}
    in_scope = [name for name, item in data['surfaces'].items() if item['status'] != 'outside_scope']
    if not in_scope:
        blockers.append('empty_scope')
        review_ok = False
    for name in sorted(in_scope):
        if data['surfaces'][name]['status'] != 'complete':
            blockers.append('surface_' + name + '_' + data['surfaces'][name]['status'])
            review_ok = False
    if data['unresolved']:
        blockers.append('unresolved_targets')
        review_ok = False
        action_ok = False
    for target in data['targets']:
        row = target['id']
        issues = []
        for field in ('source_url', 'author_verified', 'text_inspected'):
            if not target[field]:
                issues.append(field)
        if target['media'] not in ('none', 'inspected'):
            issues.append('media')
        if target['state'] == 'unresolved':
            issues.append('state_unresolved')
        if issues:
            review_ok = False
            action_ok = False
            blockers.extend('target:' + row + ':' + issue for issue in issues)
        receipt_ok = bool(target['receipt_url'] and target['receipt_note'].strip())
        state = target['state']
        action = target['requested_action']
        if state in ('pending', 'attempted'):
            blockers.append('target:' + row + ':state_' + state)
            action_ok = False
        elif state == 'verified_removed' and (action != 'remove' or not receipt_ok):
            blockers.append('target:' + row + ':verified_removed_evidence')
            action_ok = False
        elif state == 'verified_edited' and (action != 'edit' or not receipt_ok):
            blockers.append('target:' + row + ':verified_edited_evidence')
            action_ok = False
        elif state == 'kept_by_user':
            if not target['receipt_note'].strip():
                blockers.append('target:' + row + ':kept_by_user_note')
                action_ok = False
        elif action == 'remove' and state != 'verified_removed':
            blockers.append('target:' + row + ':remove_unverified')
            action_ok = False
        elif action == 'edit' and state != 'verified_edited':
            blockers.append('target:' + row + ':edit_unverified')
            action_ok = False
        elif action == 'review' and state not in ('reviewed', 'kept_by_user'):
            blockers.append('target:' + row + ':review_unverified')
            action_ok = False
        if not issues:
            counts['reviewed_targets'] += 1
        if not issues and receipt_ok and action == 'remove' and state == 'verified_removed':
            counts['verified_removals'] += 1
        if not issues and receipt_ok and action == 'edit' and state == 'verified_edited':
            counts['verified_edits'] += 1
    return {'scope': data['scope'], 'review_complete': review_ok,
            'actions_complete': action_ok, 'complete': review_ok and action_ok,
            'blockers': blockers, 'counts': counts}


def reconcile(result, coverage, inventory):
    validate_inventory(inventory)
    if account_profile(coverage['account_url']) != inventory['account_url']:
        raise InvalidLedger('account_mismatch')
    if coverage['scope_id'] != inventory['scope_id']:
        raise InvalidLedger('scope_id_mismatch')
    if inventory['scope'] != coverage['scope']:
        raise InvalidLedger('scope_mismatch')
    blockers = ['discovery:' + code for code in inventory['blockers']]
    by_source = {}
    for target in coverage['targets']:
        try:
            source = navigation_url(target['source_url'])
        except (InvalidDiscovery, ValueError):
            continue
        by_source.setdefault(source, []).append(target)
    resolved = 0
    mapping = {}
    for candidate in inventory['candidates']:
        cid = candidate['id']
        if candidate['kind'] == 'unknown':
            blockers.append('candidate:' + cid + ':unknown_kind')
            continue
        source = candidate['resolved_source_url'] or candidate['navigation_url']
        if candidate['resolved_source_url'] and not candidate['resolution_note'].strip():
            blockers.append('candidate:' + cid + ':resolution_note')
            continue
        if not source:
            blockers.append('candidate:' + cid + ':unresolved_source')
            continue
        try:
            canonical = navigation_url(source)
            query = dict(parse_qsl(urlsplit(canonical).query, keep_blank_values=True))
        except (InvalidDiscovery, ValueError):
            blockers.append('candidate:' + cid + ':unresolved_source')
            continue
        if (not query.get('comment_id') or
                candidate['kind'] == 'reply' and not query.get('reply_comment_id') or
                candidate['kind'] == 'comment' and query.get('reply_comment_id')):
            blockers.append('candidate:' + cid + ':kind_source_mismatch')
            continue
        resolved += 1
        targets = by_source.get(canonical, [])
        if len(targets) != 1:
            blockers.append('candidate:' + cid + ':coverage_target')
        else:
            mapping.setdefault(canonical, []).append(candidate)
    for group in mapping.values():
        if len(group) > 1 and any(not item['resolution_note'].strip() for item in group):
            blockers.extend('candidate:' + item['id'] + ':duplicate_resolution_note' for item in group
                            if not item['resolution_note'].strip())
    result['counts'].update(activity_rows=sum(batch['row_count'] for batch in inventory['batches']),
                            candidates=len(inventory['candidates']), resolved_candidates=resolved)
    result['blockers'] = sorted(set(result['blockers'] + blockers))
    result['review_complete'] = result['review_complete'] and inventory['discovery_complete'] and not blockers
    result['complete'] = result['review_complete'] and result['actions_complete']
    return result


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        if len(argv) not in (1, 3) or len(argv) == 3 and argv[1] != '--discovery':
            raise InvalidLedger('invalid_arguments')
        with Path(argv[0]).open('rb') as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise InvalidLedger('oversized_json')
        data = json.loads(raw.decode('utf-8'), object_pairs_hook=no_duplicates,
                          parse_constant=reject_constant)
        result = check(data)
        if len(argv) == 3:
            result = reconcile(result, data, read_discovery_json(argv[2]))
        elif is_facebook_url(data['account_url']) and any(
                data['surfaces'][name]['status'] != 'outside_scope' for name in DISCOVERY_SURFACES):
            result['blockers'].append('discovery_required')
            result['review_complete'] = result['complete'] = False
        code = 0 if result['complete'] else 1
    except (OSError, UnicodeError, ValueError, RecursionError, TypeError) as error:
        reason = error.code if isinstance(error, (InvalidLedger, InvalidDiscovery)) else 'unreadable_json'
        result = {'scope': '', 'review_complete': False, 'actions_complete': False,
                  'complete': False, 'blockers': [reason],
                  'counts': {'reviewed_targets': 0, 'verified_removals': 0, 'verified_edits': 0}}
        code = 2
    print(json.dumps(result, ensure_ascii=True))
    return code


if __name__ == '__main__':
    sys.exit(main())
