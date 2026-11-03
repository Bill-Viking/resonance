"""Reader-check intake: pending jobs, strict validation, corpus revisions and receipts.

Reader results are the extension's own observations, checked here for internal
consistency. They are never cryptographic attestation and never provider-action
authority. This module has no network access; storage is the caller's open
SQLite transaction. Server helpers arrive through `api` (the server module) so
that this file never imports it.
"""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import threading
import uuid

import policy
import workflow

READER_VERSIONS = ('0.1.0',)  # Accepted for new live saves.
HISTORICAL_VERSIONS = ('0.0.6', '0.1.0')  # Earlier records stay readable and restorable.
PENDING_LIMIT = 32
PENDING_SECONDS = 600
BEGIN_ACCOUNTS = 20
ITEM_LIMIT = 3
ITEM_BYTES = 16384
TOTAL_BYTES = 65536
MESSAGE_CHARS = 262144
PAGE_DEFAULT, PAGE_MAX = 20, 50
SUMMARY_LIMIT = 20
BACKUP_MARGIN = 64 * 1024
SNAPSHOT_FIELDS = ('id', 'platform', 'handle', 'display_name', 'url', 'identity_mode', 'daring', 'sfw_only')
PROVIDERS = {'X': 'x', 'YouTube': 'youtube'}
STATEMENT = ('Reader-reported evidence from the local browser reader, checked by Resonance for consistency. '
             'It is not a cryptographic attestation and grants no permission to change anything on a platform.')
SCOPE_NOTE = ('Each account read covers at most 3 recent items. Images and video are not inspected. '
              'No rule match does not mean an account is safe.')
UNSUPPORTED_REASON = 'provider_not_supported'
STATUSES = ('ok', 'held', 'error', 'unsupported')
CHECK_ID = re.compile(r'[a-f0-9]{32}')
REQUEST_ID = re.compile(r'[a-f0-9]{32}')
TOKEN = re.compile(r'[a-z_]{1,48}')
X_HANDLE = re.compile(r'[A-Za-z0-9_]{1,15}')
X_RESERVED = {'home', 'explore', 'notifications', 'messages', 'search', 'settings', 'i', 'compose', 'login', 'logout',
              'signup', 'tos', 'privacy', 'intent', 'share', 'hashtag', 'account', 'jobs', 'help', 'about'}
YT_HANDLE = re.compile(r'[A-Za-z0-9._-]{3,30}')
YT_CHANNEL = re.compile(r'UC[A-Za-z0-9_-]{22}')
VIDEO_ID = re.compile(r'[A-Za-z0-9_-]{11}')
COMMENT_ID = re.compile(r'[A-Za-z0-9_-][A-Za-z0-9_.-]{8,78}[A-Za-z0-9_-]')
STATUS_ID = re.compile(r'[0-9]{1,25}')
X_STATUS = re.compile(r'https://x\.com/([A-Za-z0-9_]{1,15})/status/([0-9]{1,25})')
YT_WATCH = re.compile(r'https://www\.youtube\.com/watch\?v=([A-Za-z0-9_-]{11})&lc=([A-Za-z0-9_.-]{10,80})')
CANON_TIME = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ')
ISO_MS = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,3})?Z')
HEX64 = re.compile(r'[a-f0-9]{64}')
DIAG_KEYS = {'document_ready', 'visibility', 'avatar', 'editing_link', 'content_link', 'channels'}
ITEM_KEYS = {'provider', 'source_id', 'url', 'author', 'channel_id', 'text', 'date_iso', 'date_label',
             'date_unknown', 'links_shortened'}
COVERAGE_KEYS = {'limit', 'accepted', 'examined', 'skipped', 'more_available', 'partial', 'notes'}
ACCOUNT_RECEIPT_KEYS = {'account_id', 'platform', 'handle', 'status', 'reason', 'diagnostic', 'request_id', 'identity',
                        'coverage', 'gaps', 'source_count', 'new_source_count', 'changed_source_count',
                        'unchanged_source_count', 'finding_count', 'flagged_source_count', 'new_review_count',
                        'source_ids', 'revision_ids'}
RECEIPT_KEYS = {'id', 'origin', 'statement', 'scope_note', 'reader_version', 'created_at', 'completed_at',
                'committed_at', 'digest', 'accounts', 'totals', 'receipt_hash'}
SOURCE_KEYS = {'id', 'account_id', 'source_key', 'kind', 'url', 'author', 'channel_id', 'date_iso', 'date_label',
               'date_unknown', 'first_seen_at', 'last_seen_at', 'last_check_id', 'reader_version',
               'current_revision_id'}
REVISION_KEYS = {'id', 'source_id', 'account_id', 'sequence', 'content_hash', 'byte_length', 'text', 'links_shortened',
                 'observed_at', 'check_id', 'reader_version'}


def utcnow():
    return datetime.now(timezone.utc)


def iso(value):
    return value.isoformat(timespec='seconds').replace('+00:00', 'Z')


def parse_time(value, api, name):
    """A strict ISO-8601 instant with an explicit zone, or Invalid."""
    if type(value) is not str or len(value) > 40 or not re.fullmatch(
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})', value):
        raise api.Invalid(f'{name} must be an ISO timestamp with a timezone.')
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)
    except ValueError:
        raise api.Invalid(f'{name} must be a real date and time.') from None


def exact(value, keys, api, name):
    if type(value) is not dict or set(value) != set(keys):
        raise api.Invalid(f'{name} must have exactly these fields: {", ".join(sorted(keys))}.')
    return value


def plain_int(value):
    return type(value) is int


def sha256_hex(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def payload_digest(body):
    # ASCII escaping keeps the digest computable even for text that validation will refuse.
    return sha256_hex(json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(',', ':')))


def exact_text(value, api, name):
    """Characters are preserved as received. Only unencodable or control text is refused."""
    if type(value) is not str or any(ord(c) < 32 and c not in '\n\r\t' or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise api.Invalid(f'{name} contains characters Resonance cannot keep exactly.')
    return value


# ---- tables ---------------------------------------------------------------------------------------
def create_tables(db):
    for statement in '''
        CREATE TABLE IF NOT EXISTS reader_checks (id TEXT PRIMARY KEY, digest TEXT NOT NULL, origin TEXT NOT NULL CHECK(origin IN ('live','historical')), data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS reader_sources (id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id), source_key TEXT NOT NULL, current_revision_id TEXT NOT NULL, data TEXT NOT NULL, UNIQUE(account_id,source_key));
        CREATE TABLE IF NOT EXISTS reader_revisions (id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES reader_sources(id), account_id TEXT NOT NULL REFERENCES accounts(id), sequence INTEGER NOT NULL, content_hash TEXT NOT NULL, data TEXT NOT NULL, UNIQUE(source_id,sequence), UNIQUE(source_id,content_hash));
        CREATE TRIGGER IF NOT EXISTS reader_checks_no_update BEFORE UPDATE ON reader_checks BEGIN SELECT RAISE(ABORT,'Reader receipts are immutable.'); END;
        CREATE TRIGGER IF NOT EXISTS reader_checks_no_delete BEFORE DELETE ON reader_checks BEGIN SELECT RAISE(ABORT,'Reader receipts are immutable.'); END;
        CREATE TRIGGER IF NOT EXISTS reader_revisions_no_update BEFORE UPDATE ON reader_revisions BEGIN SELECT RAISE(ABORT,'Reader revisions are immutable.'); END;
        CREATE TRIGGER IF NOT EXISTS reader_revisions_no_delete BEFORE DELETE ON reader_revisions BEGIN SELECT RAISE(ABORT,'Reader revisions are immutable.'); END;
    '''.split(';\n'):
        if statement.strip():
            db.execute(statement)


# ---- expected account identity ----------------------------------------------------------------------
def snapshot(account):
    return {key: account[key] for key in SNAPSHOT_FIELDS}


def valid_x_handle(value):
    return type(value) is str and X_HANDLE.fullmatch(value) is not None and value.lower() not in X_RESERVED


def expected_identity(account):
    """What the saved handle and link say the current handle/channel is: (provider, expected, problem)."""
    provider = PROVIDERS.get(account['platform'])
    if provider is None:
        return None, None, None
    handle = account['handle'].strip()
    handle = handle[1:] if handle.startswith('@') else handle
    handles, channels = set(), set()
    problem = None
    if provider == 'youtube' and YT_CHANNEL.fullmatch(handle):
        channels.add(handle)
    elif provider == 'x' and valid_x_handle(handle):
        handles.add(handle.lower())
    elif provider == 'youtube' and YT_HANDLE.fullmatch(handle):
        handles.add(handle.lower())
    else:
        problem = 'The saved handle is not a valid handle for this platform.'
    if account['url']:
        key = workflow.profile_key(account['platform'], account['url'])
        if key is None:
            problem = problem or 'The saved profile link is not a recognized profile address.'
        elif key[1] == 'id':
            if YT_CHANNEL.fullmatch(key[2]):
                channels.add(key[2])
            else:
                problem = problem or 'The saved channel link is not a stable channel ID.'
        elif provider == 'x' and valid_x_handle(key[2]) or provider == 'youtube' and YT_HANDLE.fullmatch(key[2]):
            handles.add(key[2].lower())
        else:
            problem = problem or 'The saved profile link does not hold a valid handle.'
    if len(handles) > 1 or len(channels) > 1:
        problem = problem or 'The saved handle and profile link name different accounts.'
    if not handles and not channels:
        problem = problem or 'The saved account has no handle or channel to compare.'
    if problem:
        return provider, None, problem
    return provider, {'handle': next(iter(handles), None), 'channel_id': next(iter(channels), None)}, None


# ---- pending jobs ---------------------------------------------------------------------------------
class PendingChecks:
    """In-memory only, owned by one Workspace. Never persisted, exported or restored."""

    def __init__(self):
        self.lock = threading.Lock()
        self.jobs = {}

    def _expire(self, at):
        for check_id in [key for key, job in self.jobs.items() if job['expires'] <= at]:
            del self.jobs[check_id]

    def begin(self, api, token, accounts, existing_receipt):
        at = utcnow()
        with self.lock:
            self._expire(at)
            if len(self.jobs) >= PENDING_LIMIT:
                raise api.Invalid('Too many checks are waiting. Finish or cancel one, then try again.')
            check_id = uuid.uuid4().hex
            while check_id in self.jobs or existing_receipt(check_id):
                check_id = uuid.uuid4().hex
            expires = at + timedelta(seconds=PENDING_SECONDS)
            entries = []
            for account in accounts:
                provider, expected, problem = expected_identity(account)
                entries.append({'account': snapshot(account), 'provider': provider, 'expected': expected,
                                'expected_problem': problem})
            job = {'check_id': check_id, 'token': token, 'at': at, 'expires': expires, 'entries': entries}
            self.jobs[check_id] = job
            return job

    def get(self, check_id, token):
        at = utcnow()
        with self.lock:
            self._expire(at)
            job = self.jobs.get(check_id)
            return job if job is not None and job['token'] == token else None

    def forget(self, check_id):
        with self.lock:
            self.jobs.pop(check_id, None)

    def clear(self):
        with self.lock:
            self.jobs.clear()


def public_job(job):
    return {'check_id': job['check_id'], 'created_at': iso(job['at']), 'expires_at': iso(job['expires']),
            'accounts': [dict(entry) for entry in job['entries']]}


def begin(api, registry, token, body, load_accounts, existing_receipt):
    api.fields(body, {'account_ids'}, {'account_ids'})
    ids = body['account_ids']
    if type(ids) is not list or not 1 <= len(ids) <= BEGIN_ACCOUNTS:
        raise api.Invalid(f'Choose between 1 and {BEGIN_ACCOUNTS} accounts.')
    for value in ids:
        api.identifier(value)
    if len(set(ids)) != len(ids):
        raise api.Invalid('Each account can be selected only once.')
    accounts = load_accounts(ids)
    return public_job(registry.begin(api, token, accounts, existing_receipt))


def cancel(api, registry, body):
    api.fields(body, {'check_id'}, {'check_id'})
    check_id = check_id_value(api, body['check_id'])
    registry.forget(check_id)
    return {'check_id': check_id, 'pending': False}


def check_id_value(api, value):
    if type(value) is not str or not CHECK_ID.fullmatch(value):
        raise api.Invalid('Invalid check identifier.')
    return value


# ---- payload validation -----------------------------------------------------------------------------
def token_value(value, api, name):
    if type(value) is not str or not TOKEN.fullmatch(value):
        raise api.Invalid(f'{name} must be a short lowercase token.')
    return value


def validate_diagnostic(value, api):
    exact(value, DIAG_KEYS, api, 'Diagnostic')
    if (value['document_ready'] not in ('loading', 'interactive', 'complete', 'unknown') or
            value['visibility'] not in ('visible', 'hidden', 'unknown') or
            any(type(value[key]) is not bool for key in ('avatar', 'editing_link', 'content_link')) or
            type(value['channels']) is not int or value['channels'] not in (0, 1, 2)):
        raise api.Invalid('Diagnostic has an unexpected value.')
    return value


def result_envelope(result, entry, api, seen_requests):
    if type(result) is not dict:
        raise api.Invalid('Each reader result must be an object.')
    if len(workflow.canonical_json(result)) > MESSAGE_CHARS:
        raise api.Invalid('A reader result is too large.')
    if result.get('type') != 'RESULT' or result.get('mode') != 'live':
        raise api.Invalid('Only live reader results are accepted.')
    if result.get('provider') != entry['provider']:
        raise api.Invalid('The reader result is for a different platform than the selected account.')
    request_id = result.get('requestId')
    if type(request_id) is not str or not REQUEST_ID.fullmatch(request_id) or request_id in seen_requests:
        raise api.Invalid('Each reader result needs its own request ID.')
    seen_requests.add(request_id)
    tabs = result.get('tabs')
    if type(tabs) is not list or len(tabs) > 16 or any(not plain_int(tab) or tab < 0 for tab in tabs):
        raise api.Invalid('The reader result has an invalid tab list.')
    return request_id


def validate_item(item, entry, identity, completed, api):
    exact(item, ITEM_KEYS, api, 'Item')
    provider = entry['provider']
    text = exact_text(item['text'], api, 'Item text')
    size = len(text.encode('utf-8'))
    if item['provider'] != provider or not text.strip() or size > ITEM_BYTES:
        raise api.Invalid('An item has the wrong platform, is blank or is over 16 KiB.')
    for key in ('date_unknown', 'links_shortened'):
        if type(item[key]) is not bool:
            raise api.Invalid('An item flag is not true or false.')
    if item['date_iso'] is not None:
        if type(item['date_iso']) is not str or not ISO_MS.fullmatch(item['date_iso']):
            raise api.Invalid('An item date is not a valid UTC time.')
        if parse_time(item['date_iso'], api, 'Item date') > completed:
            raise api.Invalid('An item is dated after the check finished.')
    if item['date_label'] is not None and (type(item['date_label']) is not str or len(item['date_label']) > 80):
        raise api.Invalid('An item date label is too long.')
    if (item['date_iso'] is None) != item['date_unknown']:
        raise api.Invalid('An item date and its unknown-date flag disagree.')
    url, author = item['url'], item['author']
    if type(url) is not str or type(author) is not str or type(item['source_id']) is not str:
        raise api.Invalid('An item has a malformed link, author or source ID.')
    if provider == 'x':
        match = X_STATUS.fullmatch(url)
        if (not match or not valid_x_handle(match[1]) or match[2] != item['source_id'] or item['channel_id'] is not None
                or author.lower() != match[1].lower() or author.lower() != identity['handle'].lower()
                or not workflow.precise_source(url, 'X', 'post')):
            raise api.Invalid('An item does not belong to the observed X account and status.')
        key = f'https://x.com/{match[1].lower()}/status/{match[2]}'
        return {'kind': 'post', 'source_key': key, 'url': key, 'author': match[1], 'channel_id': None,
                'source_id': match[2], 'text': text, 'item': item}
    match = YT_WATCH.fullmatch(url)
    if (not match or not COMMENT_ID.fullmatch(match[2]) or '..' in match[2] or match[2] != item['source_id']
            or author.lower() != identity['handle'].lower() or item['channel_id'] != identity['channel_id']
            or not workflow.precise_source(url, 'YouTube', 'comment')):
        raise api.Invalid('An item does not belong to the observed YouTube channel and comment.')
    return {'kind': 'comment', 'source_key': url, 'url': url, 'author': author, 'channel_id': item['channel_id'],
            'source_id': match[2], 'text': text, 'item': item}


def coverage_shape(coverage, count, api):
    """The counts, flags and tokens of a coverage block for `count` accepted items. Returns it normalized."""
    exact(coverage, COVERAGE_KEYS, api, 'Coverage')
    skipped = coverage['skipped']
    notes = coverage['notes']
    if (not plain_int(coverage['limit']) or coverage['limit'] != ITEM_LIMIT or not plain_int(coverage['accepted'])
            or coverage['accepted'] != count or not plain_int(coverage['examined']) or not coverage['accepted'] <= coverage['examined'] <= 64
            or type(coverage['more_available']) is not bool or type(coverage['partial']) is not bool
            or type(skipped) is not list or len(skipped) > 16 or type(notes) is not list or len(notes) > 16):
        raise api.Invalid('Coverage counts are inconsistent.')
    reasons = []
    for row in skipped:
        exact(row, {'reason', 'count'}, api, 'Skipped row')
        token_value(row['reason'], api, 'Skipped reason')
        if not plain_int(row['count']) or not 1 <= row['count'] <= 64:
            raise api.Invalid('A skipped count is out of range.')
        reasons.append(row['reason'])
    if len(set(reasons)) != len(reasons) or reasons != sorted(reasons):
        raise api.Invalid('Skipped reasons must be unique and sorted.')
    for note in notes:
        token_value(note, api, 'Coverage note')
    if len(set(notes)) != len(notes):
        raise api.Invalid('Coverage notes must be unique.')
    if coverage['partial'] != (count < ITEM_LIMIT or bool(skipped) or coverage['more_available']):
        raise api.Invalid('Coverage partial flag disagrees with its counts.')
    return {'limit': ITEM_LIMIT, 'accepted': count, 'examined': coverage['examined'],
            'skipped': [dict(row) for row in skipped], 'more_available': coverage['more_available'],
            'partial': coverage['partial'], 'notes': list(notes)}


def validate_coverage(coverage, items, api):
    value = coverage_shape(coverage, len(items), api)
    if ('link_display_shortened' in value['notes']) != any(item['links_shortened'] for item in items):
        raise api.Invalid('Coverage notes disagree with the shortened links.')
    if ('dates_relative_or_unknown' in value['notes']) != any(item['date_unknown'] for item in items):
        raise api.Invalid('Coverage notes disagree with the unknown dates.')
    return value


def not_read_coverage():
    return {'limit': ITEM_LIMIT, 'accepted': 0, 'examined': 0, 'skipped': [], 'more_available': False,
            'partial': True, 'notes': ['not_read']}


def validate_ok(result, entry, completed, api):
    exact(result, {'type', 'requestId', 'provider', 'mode', 'status', 'tabs', 'items', 'coverage', 'identity'},
          api, 'Reader result')
    if entry['expected'] is None:
        raise api.Invalid('The saved account has no usable handle to compare; its result cannot be saved as read.')
    identity = exact(result['identity'], {'handle', 'channel_id'}, api, 'Identity')
    observed, expected = identity['handle'], entry['expected']
    if type(observed) is not str:
        raise api.Invalid('The observed handle is malformed.')
    if entry['provider'] == 'x':
        if not valid_x_handle(observed) or identity['channel_id'] is not None:
            raise api.Invalid('The observed X identity is malformed.')
    elif (not YT_HANDLE.fullmatch(observed) or type(identity['channel_id']) is not str
          or not YT_CHANNEL.fullmatch(identity['channel_id'])):
        raise api.Invalid('A YouTube result needs the observed current handle and stable channel ID.')
    if expected['handle'] is not None and observed.lower() != expected['handle']:
        raise api.Invalid('The observed handle does not match the selected account.')
    if expected['channel_id'] is not None and identity['channel_id'] != expected['channel_id']:
        raise api.Invalid('The observed channel does not match the selected account.')
    items = result['items']
    if type(items) is not list or not 1 <= len(items) <= ITEM_LIMIT:
        raise api.Invalid('A successful read needs between 1 and 3 items.')
    checked = [validate_item(item, entry, identity, completed, api) for item in items]
    if (len({value['source_key'] for value in checked}) != len(checked) or
            len({value['source_id'] for value in checked}) != len(checked) or
            sum(len(value['text'].encode('utf-8')) for value in checked) > TOTAL_BYTES):
        raise api.Invalid('Items repeat a source or exceed the 64 KiB total.')
    coverage = validate_coverage(result['coverage'], items, api)
    if entry['provider'] == 'youtube' and 'activity_text_not_used' not in coverage['notes']:
        raise api.Invalid('A YouTube result must say its history text was not used.')
    return {'identity': {'handle': observed, 'channel_id': identity['channel_id']}, 'items': checked,
            'coverage': coverage}


def validate_not_ok(status, result, api):
    base = {'type', 'requestId', 'provider', 'mode', 'status', 'tabs', 'reason'}
    if result.get('status') != status:
        raise api.Invalid('The account status and its reader result disagree.')
    if 'diagnostic' in result:
        exact(result, base | {'diagnostic'}, api, 'Reader result')
        if (result['provider'] != 'youtube' or status != 'held' or result['reason'] != 'ownership_not_confirmed'):
            raise api.Invalid('A diagnostic is only valid for a held YouTube ownership result.')
        validate_diagnostic(result['diagnostic'], api)
    else:
        exact(result, base, api, 'Reader result')
    token_value(result['reason'], api, 'Reason')
    return result['reason'], result.get('diagnostic')


def validate_payload(api, body, job, now):
    exact(body, {'check_id', 'reader_version', 'completed_at', 'accounts'}, api, 'Check')
    if body['reader_version'] not in READER_VERSIONS:
        raise api.Invalid('This reader version is not accepted for saving. Use reader version ' + READER_VERSIONS[0] + '.')
    completed = parse_time(body['completed_at'], api, 'Completion time')
    if completed > now:
        raise api.Invalid('The completion time is in the future.')
    if completed < job['at'].replace(microsecond=0):
        raise api.Invalid('The check finished before it began.')
    entries = {entry['account']['id']: entry for entry in job['entries']}
    listed = body['accounts']
    if type(listed) is not list or not 1 <= len(listed) <= BEGIN_ACCOUNTS:
        raise api.Invalid('The account list is the wrong size.')
    seen_accounts, seen_requests, accounts = set(), set(), []
    for row in listed:
        exact(row, {'account_id', 'status', 'result'}, api, 'Account result')
        account_id = api.identifier(row['account_id'])
        if account_id in seen_accounts:
            raise api.Invalid('An account appears twice in this check.')
        if account_id not in entries:
            raise api.Invalid('This check includes an account that was not selected.')
        seen_accounts.add(account_id)
        entry, status = entries[account_id], row['status']
        if status not in STATUSES:
            raise api.Invalid('Account status must be ok, held, error or unsupported.')
        result = row['result']
        value = {'account_id': account_id, 'entry': entry, 'status': status, 'reason': None, 'diagnostic': None,
                 'request_id': None, 'identity': None, 'items': [], 'coverage': None}
        if entry['provider'] is None:
            if status != 'unsupported':
                raise api.Invalid('Only X and YouTube accounts can be read in this edition.')
        elif status == 'unsupported':
            raise api.Invalid('A supported platform cannot be reported as unsupported.')
        if status == 'unsupported':
            if result != {'reason': UNSUPPORTED_REASON}:
                raise api.Invalid('An unsupported account must carry the reason provider_not_supported.')
            value['reason'] = UNSUPPORTED_REASON
        else:
            value['request_id'] = result_envelope(result, entry, api, seen_requests)
            if status == 'ok':
                value.update(validate_ok(result, entry, completed, api))
            else:
                value['reason'], value['diagnostic'] = validate_not_ok(status, result, api)
        accounts.append(value)
    if seen_accounts != set(entries):
        raise api.Invalid('Every selected account needs exactly one result.')
    return completed, accounts


# ---- comparing and storing ------------------------------------------------------------------------
def stable_ids(account_id, source_key, content_hash=None):
    source_id = 'rs_' + workflow.digest([account_id, source_key])[:24]
    if content_hash is None:
        return source_id
    return source_id, 'rv_' + workflow.digest([source_id, content_hash])[:24]


def review_id(account_id, source_key, content_hash):
    return 'reader_' + workflow.digest([account_id, source_key, content_hash])[:24]


def gaps_for(status, reason, coverage):
    if status != 'ok':
        return [reason]
    out = [row['reason'] for row in coverage['skipped']]
    if coverage['more_available']:
        out.append('more_available')
    if len(out) == 0 and coverage['partial']:
        out.append('fewer_than_limit')
    return out


def apply_account(api, store, db, value, account, completed, check_id, version, committed_at):
    entry = value['entry']
    receipt = {'account_id': account['id'], 'platform': account['platform'], 'handle': account['handle'],
               'status': value['status'], 'reason': value['reason'], 'diagnostic': value['diagnostic'],
               'request_id': value['request_id'], 'identity': value['identity'], 'coverage': value['coverage'],
               'source_count': 0, 'new_source_count': 0, 'changed_source_count': 0, 'unchanged_source_count': 0,
               'finding_count': 0, 'flagged_source_count': 0, 'new_review_count': 0, 'source_ids': [],
               'revision_ids': []}
    if value['coverage'] is None:
        receipt['coverage'] = not_read_coverage()
    receipt['gaps'] = gaps_for(value['status'], value['reason'], receipt['coverage'])
    observed_at = iso(completed)
    for item in value['items']:
        text, raw = item['text'], item['item']
        content_hash = sha256_hex(text)
        source_id, revision_id = stable_ids(account['id'], item['source_key'], content_hash)
        row = db.execute('SELECT data FROM reader_sources WHERE id=?', (source_id,)).fetchone()
        source = json.loads(row['data']) if row else None
        revision = db.execute('SELECT 1 FROM reader_revisions WHERE id=?', (revision_id,)).fetchone()
        if source is None:
            source = {'id': source_id, 'account_id': account['id'], 'source_key': item['source_key'],
                      'kind': item['kind'], 'url': item['url'], 'author': item['author'],
                      'channel_id': item['channel_id'], 'date_iso': raw['date_iso'], 'date_label': raw['date_label'],
                      'date_unknown': raw['date_unknown'], 'first_seen_at': observed_at, 'last_seen_at': observed_at,
                      'last_check_id': check_id, 'reader_version': version, 'current_revision_id': revision_id}
            receipt['new_source_count'] += 1
        else:
            if source['current_revision_id'] == revision_id:
                receipt['unchanged_source_count'] += 1
            else:
                receipt['changed_source_count'] += 1
            if source['date_iso'] is None and raw['date_iso'] is not None:
                source.update(date_iso=raw['date_iso'], date_label=raw['date_label'], date_unknown=False)
            source.update(last_seen_at=max(source['last_seen_at'], observed_at), last_check_id=check_id,
                          reader_version=version, current_revision_id=revision_id, author=item['author'],
                          channel_id=item['channel_id'])
        if row is None:
            db.execute('INSERT INTO reader_sources(id,account_id,source_key,current_revision_id,data) VALUES(?,?,?,?,?)',
                       (source_id, account['id'], item['source_key'], revision_id, workflow.canonical_json(source)))
        else:
            db.execute('UPDATE reader_sources SET current_revision_id=?,data=? WHERE id=?',
                       (revision_id, workflow.canonical_json(source), source_id))
        if revision is None:
            sequence = db.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM reader_revisions WHERE source_id=?',
                                  (source_id,)).fetchone()[0]
            record = {'id': revision_id, 'source_id': source_id, 'account_id': account['id'], 'sequence': sequence,
                      'content_hash': content_hash, 'byte_length': len(text.encode('utf-8')), 'text': text,
                      'links_shortened': raw['links_shortened'], 'observed_at': observed_at, 'check_id': check_id,
                      'reader_version': version}
            db.execute('INSERT INTO reader_revisions(id,source_id,account_id,sequence,content_hash,data) VALUES(?,?,?,?,?,?)',
                       (revision_id, source_id, account['id'], sequence, content_hash, workflow.canonical_json(record)))
        receipt['source_count'] += 1
        receipt['source_ids'].append(source_id)
        receipt['revision_ids'].append(revision_id)
        signals = policy.check(text, account)
        if signals:
            receipt['finding_count'] += len(signals)
            receipt['flagged_source_count'] += 1
            rid = review_id(account['id'], item['source_key'], content_hash)
            if not db.execute('SELECT 1 FROM reviews WHERE id=?', (rid,)).fetchone():
                finding = ('Local checks matched: ' + '; '.join(s['title'] for s in signals) +
                           '. These are possible issues for you to review in context.')
                review = api.review_record(dict(
                    id=rid, account_id=account['id'], kind=item['kind'], title=signals[0]['title'], excerpt=text,
                    source_url=item['url'], observed_at=raw['date_iso'] or observed_at, reviewed_at=committed_at,
                    sample_count=1, severity='attention' if any(s['category'] in {'sfw', 'risk'} for s in signals) else 'suggestion',
                    finding=finding, suggestion='Open the original and check the surrounding conversation and any images or video. Decide whether the wording fits this account.',
                    origin='local', status='open', snoozed_until=None, updated_at=committed_at))
                store.put(db, 'reviews', review)
                receipt['new_review_count'] += 1
    return receipt


def seal(receipt):
    body = {key: value for key, value in receipt.items() if key not in ('origin', 'receipt_hash')}
    receipt['receipt_hash'] = sha256_hex(workflow.canonical_json(body))
    return receipt


def totals_for(accounts):
    return {'accounts': len(accounts), **{status: sum(1 for a in accounts if a['status'] == status) for status in STATUSES},
            'sources': sum(a['source_count'] for a in accounts), 'findings': sum(a['finding_count'] for a in accounts)}


def stored_receipt(db, check_id):
    row = db.execute('SELECT digest,origin,data FROM reader_checks WHERE id=?', (check_id,)).fetchone()
    return None if row is None else dict(json.loads(row['data']), origin=row['origin'])


def ensure_capacity(api, store, db, message):
    """Inside the caller's transaction: refuse (and so roll back) a state whose portable backup could not be restored."""
    size = len(json.dumps(store.backup_in(db), ensure_ascii=False).encode('utf-8'))
    if size > api.MAX_IMPORT_BODY - BACKUP_MARGIN:
        raise api.Invalid(message)


def commit(api, store, db, job, body):
    """Everything below runs inside the caller's single BEGIN IMMEDIATE transaction."""
    if type(body) is not dict or 'check_id' not in body:
        raise api.Invalid('Check must have exactly these fields: accounts, check_id, completed_at, reader_version.')
    check_id = check_id_value(api, body['check_id'])
    digest = payload_digest(body)
    saved = db.execute('SELECT digest,origin FROM reader_checks WHERE id=?', (check_id,)).fetchone()
    if saved is not None:
        if saved['origin'] != 'live':
            raise api.Invalid('This check ID belongs to an imported historical receipt and cannot be reused.')
        if saved['digest'] != digest:
            raise api.Invalid('This check was already saved with different content. Start a new check.')
        return 200, {'receipt': stored_receipt(db, check_id), 'replayed': True}
    at = utcnow()
    if job is None or job['check_id'] != check_id or job['expires'] <= at:
        raise api.Invalid('This check is not waiting or has expired. Start a new check.')
    completed, accounts = validate_payload(api, body, job, at)
    current = {}
    for value in accounts:
        row = db.execute('SELECT data FROM accounts WHERE id=?', (value['account_id'],)).fetchone()
        if row is None or snapshot(api.account_record(json.loads(row['data']))) != value['entry']['account']:
            raise api.Invalid('An account changed while it was being read. Start a new check.')
        current[value['account_id']] = api.account_record(json.loads(row['data']))
    committed_at = api.now()
    receipts = [apply_account(api, store, db, value, current[value['account_id']], completed, check_id,
                              body['reader_version'], committed_at) for value in accounts]
    receipt = seal({'id': check_id, 'statement': STATEMENT, 'scope_note': SCOPE_NOTE,
                    'reader_version': body['reader_version'], 'created_at': iso(job['at']), 'completed_at': iso(completed),
                    'committed_at': committed_at, 'digest': digest, 'accounts': receipts, 'totals': totals_for(receipts)})
    db.execute('INSERT INTO reader_checks(id,digest,origin,data) VALUES(?,?,?,?)',
               (check_id, digest, 'live', workflow.canonical_json(receipt)))
    store.materialize_flag_evidence(db)
    store.log(db, 'Saved a reader check.')
    ensure_capacity(api, store, db, 'This edition cannot save more history: your portable backup would be too large to restore. '
                                    'Nothing from this check was saved.')
    return 201, {'receipt': dict(receipt, origin='live'), 'replayed': False}


# ---- reads ---------------------------------------------------------------------------------------
def summaries(db):
    rows = db.execute('SELECT data,origin FROM reader_checks ORDER BY rowid DESC LIMIT ?', (SUMMARY_LIMIT,)).fetchall()
    recent = []
    for row in rows:
        receipt = json.loads(row['data'])
        recent.append({'id': receipt['id'], 'origin': row['origin'], 'completed_at': receipt['completed_at'],
                       'reader_version': receipt['reader_version'], 'totals': receipt['totals'],
                       'accounts': [{key: account[key] for key in ('account_id', 'status', 'reason', 'source_count', 'finding_count')} |
                                    {'partial': account['coverage']['partial']} for account in receipt['accounts']]})
    return {'count': db.execute('SELECT count(*) FROM reader_checks').fetchone()[0],
            'source_count': db.execute('SELECT count(*) FROM reader_sources').fetchone()[0],
            'revision_count': db.execute('SELECT count(*) FROM reader_revisions').fetchone()[0],
            'recent': recent, 'limit': SUMMARY_LIMIT}


def encode_cursor(scope, rowid):
    body = json.dumps({'v': 1, 's': scope, 'r': rowid}, separators=(',', ':'), sort_keys=True)
    packed = body + '.' + hashlib.sha256(body.encode()).hexdigest()[:12]
    return base64.urlsafe_b64encode(packed.encode()).decode().rstrip('=')


def decode_cursor(api, cursor, scope):
    if cursor is None:
        return None
    try:
        if type(cursor) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,300}', cursor):
            raise ValueError()
        packed = base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4)).decode()
        body, check = packed.rsplit('.', 1)
        value = json.loads(body)
        if (check != hashlib.sha256(body.encode()).hexdigest()[:12] or set(value) != {'v', 's', 'r'} or value['v'] != 1
                or value['s'] != scope or not plain_int(value['r']) or value['r'] < 1):
            raise ValueError()
    except (ValueError, UnicodeError, KeyError):
        raise api.Invalid('The page cursor is not valid for this list. Start from the first page.') from None
    return value['r']


def page_limit(api, raw):
    if raw is None:
        return PAGE_DEFAULT
    if type(raw) is not str or not re.fullmatch(r'[0-9]{1,3}', raw) or not 1 <= int(raw) <= PAGE_MAX:
        raise api.Invalid(f'limit must be a whole number from 1 to {PAGE_MAX}.')
    return int(raw)


def one_param(api, query, name):
    values = query.get(name, [])
    if len(values) > 1:
        raise api.Invalid(f'Use {name} only once.')
    return values[0] if values else None


def corpus_page(api, db, query):
    unknown = set(query) - {'account_id', 'cursor', 'limit', 'workspace'}
    if unknown:
        raise api.Invalid('Unknown corpus option.')
    account_id = one_param(api, query, 'account_id')
    if account_id is None:
        raise api.Invalid('Choose an account_id.')
    api.identifier(account_id)
    if db.execute('SELECT 1 FROM accounts WHERE id=?', (account_id,)).fetchone() is None:
        raise LookupError('Account not found.')
    limit = page_limit(api, one_param(api, query, 'limit'))
    after = decode_cursor(api, one_param(api, query, 'cursor'), 'corpus:' + account_id)
    args = [account_id] + ([after] if after else []) + [limit + 1]
    rows = db.execute('SELECT s.rowid AS pos,s.data AS source,r.data AS revision FROM reader_sources s '
                      'JOIN reader_revisions r ON r.id=s.current_revision_id WHERE s.account_id=?' +
                      (' AND s.rowid<?' if after else '') + ' ORDER BY s.rowid DESC LIMIT ?', args).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    sources = []
    for row in rows:
        source, revision = json.loads(row['source']), json.loads(row['revision'])
        count = db.execute('SELECT count(*) FROM reader_revisions WHERE source_id=?', (source['id'],)).fetchone()[0]
        sources.append(dict(source, current_revision=revision, revision_count=count))
    return {'account_id': account_id, 'limit': limit, 'sources': sources,
            'next_cursor': encode_cursor('corpus:' + account_id, rows[-1]['pos']) if more and rows else None}


def revision_page(api, db, source_id, query):
    unknown = set(query) - {'cursor', 'limit', 'workspace'}
    if unknown:
        raise api.Invalid('Unknown revision option.')
    api.identifier(source_id)
    if db.execute('SELECT 1 FROM reader_sources WHERE id=?', (source_id,)).fetchone() is None:
        raise LookupError('Source not found.')
    limit = page_limit(api, one_param(api, query, 'limit'))
    after = decode_cursor(api, one_param(api, query, 'cursor'), 'revisions:' + source_id)
    args = [source_id] + ([after] if after else []) + [limit + 1]
    rows = db.execute('SELECT rowid AS pos,data FROM reader_revisions WHERE source_id=?' +
                      (' AND rowid<?' if after else '') + ' ORDER BY rowid DESC LIMIT ?', args).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    return {'source_id': source_id, 'limit': limit, 'revisions': [json.loads(row['data']) for row in rows],
            'next_cursor': encode_cursor('revisions:' + source_id, rows[-1]['pos']) if more and rows else None}


# ---- portable backup (format 3) ------------------------------------------------------------------------
def export_tables(db):
    return {'reader_checks': [dict(json.loads(row['data']), origin=row['origin'])
                              for row in db.execute('SELECT data,origin FROM reader_checks ORDER BY rowid')],
            'reader_sources': [json.loads(row['data']) for row in db.execute('SELECT data FROM reader_sources ORDER BY rowid')],
            'reader_revisions': [json.loads(row['data']) for row in db.execute('SELECT data FROM reader_revisions ORDER BY rowid')]}


def canonical_time(value, api, name):
    """Resonance writes every stored instant as YYYY-MM-DDTHH:MM:SSZ, so text order is time order."""
    if type(value) is not str or not CANON_TIME.fullmatch(value):
        raise api.Invalid(f'{name} must be a UTC timestamp written to the second.')
    return parse_time(value, api, name)


def matches(value, pattern):
    return type(value) is str and pattern.fullmatch(value) is not None


def source_provider(key):
    """The provider a source key belongs to. It is read from the key, not the account's current platform,
    because an account's platform is editable and a source is history."""
    if type(key) is not str:
        return None
    return 'x' if X_STATUS.fullmatch(key) else 'youtube' if YT_WATCH.fullmatch(key) else None


def validate_source_record(api, record, accounts):
    exact(record, SOURCE_KEYS, api, 'Corpus source')
    for key in ('id', 'account_id', 'current_revision_id'):
        api.identifier(record[key])
    if record['account_id'] not in accounts:
        raise api.Invalid('A corpus source references an unknown account.')
    key = record['source_key']
    provider = source_provider(key)
    if provider is None or key != record['url'] or record['id'] != stable_ids(record['account_id'], key):
        raise api.Invalid('A corpus source identifier does not match its account and link.')
    if provider == 'x':
        match = X_STATUS.fullmatch(key)
        if not valid_x_handle(match[1]) or key != f'https://x.com/{match[1].lower()}/status/{match[2]}' \
                or record['kind'] != 'post' or record['channel_id'] is not None \
                or type(record['author']) is not str or record['author'].lower() != match[1].lower():
            raise api.Invalid('A corpus source is not a valid X status for its account.')
    else:
        match = YT_WATCH.fullmatch(key)
        if not COMMENT_ID.fullmatch(match[2]) or '..' in match[2] or record['kind'] != 'comment' \
                or not matches(record['channel_id'], YT_CHANNEL) \
                or type(record['author']) is not str or not YT_HANDLE.fullmatch(record['author']):
            raise api.Invalid('A corpus source is not a valid YouTube comment for its account.')
    first = canonical_time(record['first_seen_at'], api, 'First seen')
    last = canonical_time(record['last_seen_at'], api, 'Last seen')
    if first > last:
        raise api.Invalid('A corpus source was first seen after it was last seen.')
    if type(record['date_unknown']) is not bool or (record['date_iso'] is None) != record['date_unknown'] \
            or (record['date_label'] is not None and (type(record['date_label']) is not str or len(record['date_label']) > 80)):
        raise api.Invalid('A corpus source has an invalid date.')
    if record['date_iso'] is not None:
        if not matches(record['date_iso'], ISO_MS):
            raise api.Invalid('A corpus source has an invalid date.')
        # An item cannot be dated after the check that read it; instants are stored to the second.
        if parse_time(record['date_iso'], api, 'Source date').replace(microsecond=0) > last:
            raise api.Invalid('A corpus source is dated after it was last seen.')
    if record['reader_version'] not in HISTORICAL_VERSIONS or not matches(record['last_check_id'], CHECK_ID):
        raise api.Invalid('A corpus source has an unsupported reader version or check ID.')
    return record


def validate_revision_record(api, record):
    exact(record, REVISION_KEYS, api, 'Corpus revision')
    for key in ('id', 'source_id', 'account_id'):
        api.identifier(record[key])
    text = exact_text(record['text'], api, 'Revision text')
    if (not matches(record['content_hash'], HEX64) or record['content_hash'] != sha256_hex(text)
            or not plain_int(record['byte_length']) or record['byte_length'] != len(text.encode('utf-8'))
            or not text.strip() or record['byte_length'] > ITEM_BYTES or not plain_int(record['sequence'])
            or not 1 <= record['sequence'] <= 1000000 or type(record['links_shortened']) is not bool
            or record['reader_version'] not in HISTORICAL_VERSIONS or not matches(record['check_id'], CHECK_ID)):
        raise api.Invalid('A corpus revision does not match its recorded hash or size.')
    if record['id'] != 'rv_' + workflow.digest([record['source_id'], record['content_hash']])[:24]:
        raise api.Invalid('A corpus revision identifier does not match its source and text.')
    canonical_time(record['observed_at'], api, 'Revision time')
    return record


def saved_handle(row):
    handle = row['handle'].strip()
    return handle[1:] if handle.startswith('@') else handle


def validate_receipt_row(api, row, accounts):
    """Everything a receipt account row says that can be checked from the row alone."""
    exact(row, ACCOUNT_RECEIPT_KEYS, api, 'Receipt account')
    if type(row['account_id']) is not str or row['account_id'] not in accounts or type(row['status']) is not str \
            or row['status'] not in STATUSES:
        raise api.Invalid('A reader receipt references an unknown account or status.')
    if type(row['platform']) is not str or row['platform'] not in api.PLATFORMS or type(row['handle']) is not str \
            or not 1 <= len(row['handle'].strip()) <= 200 or len(row['handle']) > 200:
        raise api.Invalid('A reader receipt has an invalid platform or handle.')
    status, provider = row['status'], PROVIDERS.get(row['platform'])
    ints = ('source_count', 'new_source_count', 'changed_source_count', 'unchanged_source_count', 'finding_count',
            'flagged_source_count', 'new_review_count')
    if any(not plain_int(row[key]) or not 0 <= row[key] <= 1000 for key in ints):
        raise api.Invalid('A reader receipt has inconsistent counts.')
    for key in ('source_ids', 'revision_ids'):
        if type(row[key]) is not list or len(row[key]) != row['source_count']:
            raise api.Invalid('A reader receipt has inconsistent counts.')
        for item in row[key]:
            api.identifier(item)
        if len(set(row[key])) != len(row[key]):
            raise api.Invalid('A reader receipt repeats a source or revision.')
    if type(row['gaps']) is not list or any(not matches(gap, TOKEN) for gap in row['gaps']):
        raise api.Invalid('A reader receipt has malformed gaps.')
    if status == 'unsupported':
        if provider is not None or row['request_id'] is not None or row['reason'] != UNSUPPORTED_REASON:
            raise api.Invalid('An unsupported receipt row is inconsistent.')
    else:
        if provider is None or not matches(row['request_id'], REQUEST_ID):
            raise api.Invalid('A receipt row is for a platform or request that cannot have been read.')
        if status != 'ok':
            token_value(row['reason'], api, 'Reason')
    if row['diagnostic'] is not None:
        if provider != 'youtube' or status != 'held' or row['reason'] != 'ownership_not_confirmed':
            raise api.Invalid('A diagnostic is only valid for a held YouTube ownership result.')
        validate_diagnostic(row['diagnostic'], api)
    if status != 'ok':
        if (row['identity'] is not None or row['coverage'] != not_read_coverage() or row['source_ids'] or row['revision_ids']
                or any(row[key] for key in ints) or row['gaps'] != [row['reason']]):
            raise api.Invalid('A receipt row that was not read carries identity, coverage or sources.')
        return
    if row['reason'] is not None or not 1 <= row['source_count'] <= ITEM_LIMIT:
        raise api.Invalid('A successful receipt row needs between 1 and 3 sources and no failure reason.')
    identity = exact(row['identity'], {'handle', 'channel_id'}, api, 'Receipt identity')
    observed = identity['handle']
    if provider == 'x':
        fits = valid_x_handle(observed) and identity['channel_id'] is None
    else:
        fits = matches(observed, YT_HANDLE) and matches(identity['channel_id'], YT_CHANNEL)
    if not fits:
        raise api.Invalid('A receipt row has a malformed observed identity.')
    # The handle saved when the check ran must agree with what was observed then. It may differ from the handle
    # saved today, and nothing here says anything about whichever browser is signed in now.
    saved = saved_handle(row)
    if provider == 'x':
        fits = valid_x_handle(saved) and saved.lower() == observed.lower()
    elif YT_CHANNEL.fullmatch(saved):
        fits = saved == identity['channel_id']
    else:
        fits = YT_HANDLE.fullmatch(saved) is not None and saved.lower() == observed.lower()
    if not fits:
        raise api.Invalid('A receipt row observed an identity that does not match the account it was saved for.')
    coverage = coverage_shape(row['coverage'], row['source_count'], api)
    if provider == 'youtube' and 'activity_text_not_used' not in coverage['notes']:
        raise api.Invalid('A YouTube receipt row must say its history text was not used.')
    if (row['new_source_count'] + row['changed_source_count'] + row['unchanged_source_count'] != row['source_count']
            or row['flagged_source_count'] > row['source_count'] or row['new_review_count'] > row['flagged_source_count']
            or (row['finding_count'] == 0) != (row['flagged_source_count'] == 0)
            or row['finding_count'] < row['flagged_source_count']):
        raise api.Invalid('A reader receipt has inconsistent counts.')
    if row['gaps'] != gaps_for('ok', None, coverage):
        raise api.Invalid('A reader receipt has gaps that disagree with its coverage.')


def validate_receipt_record(api, record, accounts):
    """Shape and internal consistency of one receipt. Its links to sources and revisions are checked separately."""
    exact(record, RECEIPT_KEYS, api, 'Reader receipt')
    if (not matches(record['id'], CHECK_ID) or record['origin'] not in ('live', 'historical') or
            record['statement'] != STATEMENT or record['scope_note'] != SCOPE_NOTE or
            record['reader_version'] not in HISTORICAL_VERSIONS or not matches(record['digest'], HEX64)):
        raise api.Invalid('A reader receipt has an unexpected shape.')
    created, completed, committed = (canonical_time(record[key], api, 'Receipt time')
                                     for key in ('created_at', 'completed_at', 'committed_at'))
    if not created <= completed <= committed:
        raise api.Invalid('A reader receipt has times out of order.')
    body = {key: value for key, value in record.items() if key not in ('origin', 'receipt_hash')}
    if not matches(record['receipt_hash'], HEX64) or record['receipt_hash'] != sha256_hex(workflow.canonical_json(body)):
        raise api.Invalid('A reader receipt does not match its recorded hash.')
    rows = record['accounts']
    if type(rows) is not list or not 1 <= len(rows) <= BEGIN_ACCOUNTS or any(type(row) is not dict for row in rows):
        raise api.Invalid('A reader receipt has an invalid account list.')
    for row in rows:
        validate_receipt_row(api, row, accounts)
    ids = [row['account_id'] for row in rows]
    requests = [row['request_id'] for row in rows if row['request_id'] is not None]
    if len(set(ids)) != len(ids) or len(set(requests)) != len(requests):
        raise api.Invalid('A reader receipt repeats an account or a request ID.')
    if record['totals'] != totals_for(rows):
        raise api.Invalid('A reader receipt has inconsistent totals.')
    return record


def merged_source(old, new):
    """What a restore keeps when it meets a source it already holds. A newer sighting wins the current pointer and
    check, but the earliest first-seen time and a known date are never lost."""
    if new['last_seen_at'] <= old['last_seen_at']:
        return old
    value = dict(new, first_seen_at=min(old['first_seen_at'], new['first_seen_at']))
    if value['date_iso'] is None and old['date_iso'] is not None:
        value.update(date_iso=old['date_iso'], date_label=old['date_label'], date_unknown=False)
    return value


def row_for(receipt, account_id):
    return next((row for row in receipt['accounts'] if row['account_id'] == account_id), None) if receipt else None


def check_graph(api, sources, revisions, final_sources, all_revisions, all_receipts):
    """Every incoming fact must tie back to a receipt that reports it. `final_sources` is the source table after the
    merge; `all_*` hold what is present now plus what arrives. Mutable source fields may have moved on since a
    receipt was written, so they are compared only where the receipt is that source's own last sighting."""
    when = {check_id: parse_time(receipt['completed_at'], api, 'Receipt time') for check_id, receipt in all_receipts.items()}
    references = {}  # source id -> the receipts that report it
    for receipt in all_receipts.values():
        for row in receipt['accounts']:
            for source_id in row['source_ids']:
                references.setdefault(source_id, []).append(receipt)
    for record in revisions.values():
        receipt = all_receipts.get(record['check_id'])
        row = row_for(receipt, record['account_id'])
        if (row is None or record['id'] not in row['revision_ids']
                or row['source_ids'][row['revision_ids'].index(record['id'])] != record['source_id']):
            raise api.Invalid('A corpus revision is not reported by the check it names.')
        if (parse_time(record['observed_at'], api, 'Revision time') != when[record['check_id']]
                or record['reader_version'] != receipt['reader_version']):
            raise api.Invalid('A corpus revision disagrees with the time or reader version of its check.')
    for record in sources.values():
        receipt = all_receipts.get(record['last_check_id'])
        row = row_for(receipt, record['account_id'])
        if row is None or record['id'] not in row['source_ids']:
            raise api.Invalid('A corpus source is not reported by its last check.')
        if (row['revision_ids'][row['source_ids'].index(record['id'])] != record['current_revision_id']
                or record['reader_version'] != receipt['reader_version']):
            raise api.Invalid('A corpus source disagrees with the revision or reader version of its last check.')
        seen = {when[other['id']] for other in references[record['id']]}
        arriving = [when[other['id']] for other in references[record['id']] if other.get('in_body')]
        first, last = parse_time(record['first_seen_at'], api, 'First seen'), parse_time(record['last_seen_at'], api, 'Last seen')
        if (first not in seen or last not in seen or last < when[receipt['id']] or last < max(arriving, default=last)
                or (receipt.get('in_body') and last != max(arriving))):
            raise api.Invalid('A corpus source has first or last seen times that no receipt reports.')
        identity = row['identity']
        fits = identity['handle'].lower() == record['author'].lower()
        if source_provider(record['source_key']) == 'youtube':
            fits = fits and identity['channel_id'] == record['channel_id']
        if not fits:
            raise api.Invalid('A corpus source disagrees with the identity its last check observed.')
    by_source = {}
    for record in all_revisions.values():
        by_source.setdefault(record['source_id'], []).append(record['sequence'])
    for source_id in {record['source_id'] for record in revisions.values()} | set(sources):
        sequences = sorted(by_source.get(source_id, []))
        if not sequences or sequences != list(range(1, len(sequences) + 1)):
            raise api.Invalid('A corpus source has revisions that are missing or out of sequence.')
    for receipt in all_receipts.values():
        if receipt.get('incoming'):
            for row in receipt['accounts']:
                check_receipt_references(api, receipt, row, final_sources, all_revisions)


def check_receipt_references(api, receipt, row, final_sources, all_revisions):
    if row['status'] != 'ok':
        return
    provider = PROVIDERS[row['platform']]
    notes = row['coverage']['notes']
    created, unknown_now = [], False
    for source_id, revision_id in zip(row['source_ids'], row['revision_ids']):
        source, revision = final_sources.get(source_id), all_revisions.get(revision_id)
        if (source is None or revision is None or source['account_id'] != row['account_id']
                or revision['source_id'] != source_id or revision['account_id'] != row['account_id']
                or source_provider(source['source_key']) != provider):
            raise api.Invalid('A reader receipt references a source or revision that is not in the backup.')
        if provider == 'x' and X_STATUS.fullmatch(source['source_key'])[1].lower() != row['identity']['handle'].lower():
            raise api.Invalid('A reader receipt reports sources that belong to a different account than it observed.')
        if source['last_check_id'] == receipt['id'] and provider == 'youtube' and (
                source['author'].lower() != row['identity']['handle'].lower()
                or source['channel_id'] != row['identity']['channel_id']):
            raise api.Invalid('A reader receipt reports sources that belong to a different channel than it observed.')
        if revision['check_id'] == receipt['id']:
            created.append(revision)
        unknown_now = unknown_now or source['date_unknown']
    # A revision first made by this check was read by it, so its flag is this check's own observation. A re-read of
    # older text may have been displayed differently, which the stored revision does not record.
    shortened = any(revision['links_shortened'] for revision in created)
    if (shortened and 'link_display_shortened' not in notes) or (
            'link_display_shortened' in notes and not shortened and len(created) == row['source_count']):
        raise api.Invalid('A reader receipt coverage disagrees with the shortened links it reported.')
    # A known date is never replaced by an unknown one, so a source that is unknown now was unknown in every reading.
    if unknown_now and 'dates_relative_or_unknown' not in notes:
        raise api.Invalid('A reader receipt coverage disagrees with the unknown dates it reported.')
    # A source is new exactly when this check made its first revision; an unchanged source made none here.
    firsts = sum(1 for revision in created if revision['sequence'] == 1)
    if (row['new_source_count'] != firsts or row['unchanged_source_count'] > row['source_count'] - len(created)
            or row['changed_source_count'] < len(created) - firsts):
        raise api.Invalid('A reader receipt counts new, changed and unchanged sources that its revisions contradict.')


def validate_import(api, db, body, accounts):
    """Return normalized (receipts, sources, revisions) to write; raises before any write."""
    lists = {}
    for name, limit in (('reader_checks', 10000), ('reader_sources', 100000), ('reader_revisions', 200000)):
        value = body.get(name, [])
        if type(value) is not list or len(value) > limit:
            raise api.Invalid(f'Backup {name} must be a list of at most {limit} records.')
        lists[name] = value
    existing_sources = {row['id']: json.loads(row['data']) for row in db.execute('SELECT id,data FROM reader_sources')}
    existing_revisions = {row['id']: json.loads(row['data']) for row in db.execute('SELECT id,data FROM reader_revisions')}
    sources, revisions, seen = {}, {}, set()
    for value in lists['reader_sources']:
        record = validate_source_record(api, value, accounts)
        if record['id'] in sources:
            raise api.Invalid('Backup repeats a corpus source.')
        sources[record['id']] = record
    for value in lists['reader_revisions']:
        record = validate_revision_record(api, value)
        if record['id'] in revisions or (record['source_id'], record['sequence']) in seen:
            raise api.Invalid('Backup repeats a corpus revision.')
        seen.add((record['source_id'], record['sequence']))
        revisions[record['id']] = record
    all_revisions = dict(existing_revisions, **revisions)
    for record in revisions.values():
        source = sources.get(record['source_id']) or existing_sources.get(record['source_id'])
        if source is None or source['account_id'] != record['account_id']:
            raise api.Invalid('A corpus revision references an unknown source or the wrong account.')
        if record['id'] in existing_revisions and existing_revisions[record['id']] != record:
            raise api.Invalid('A different corpus revision already uses this identifier.')
        clash = db.execute('SELECT id FROM reader_revisions WHERE source_id=? AND sequence=?',
                           (record['source_id'], record['sequence'])).fetchone()
        if clash is not None and clash['id'] != record['id']:
            raise api.Invalid('A different corpus revision already uses this sequence number.')
    final_sources = dict(existing_sources)
    for record in sources.values():
        pointer = all_revisions.get(record['current_revision_id'])
        if pointer is None or pointer['source_id'] != record['id']:
            raise api.Invalid('A corpus source points at a revision that is not in the backup.')
        old = existing_sources.get(record['id'])
        if old is not None and any(old[key] != record[key] for key in ('account_id', 'source_key', 'kind')):
            raise api.Invalid('A different corpus source already uses this identifier.')
        final_sources[record['id']] = record if old is None else merged_source(old, record)
    receipts = {}
    existing = {row['id']: row for row in db.execute('SELECT id,digest,data FROM reader_checks')}
    all_receipts = {check_id: json.loads(row['data']) for check_id, row in existing.items()}
    for value in lists['reader_checks']:
        record = validate_receipt_record(api, value, accounts)
        if record['id'] in receipts:
            raise api.Invalid('Backup repeats a reader receipt.')
        receipts[record['id']] = record
        saved = existing.get(record['id'])
        if saved is None:
            all_receipts[record['id']] = dict(record, incoming=True, in_body=True)
        elif saved['digest'] != record['digest'] or all_receipts[record['id']]['receipt_hash'] != record['receipt_hash']:
            raise api.Invalid('A different reader receipt already uses this check ID.')
        else:
            all_receipts[record['id']] = dict(all_receipts[record['id']], in_body=True)
    if receipts or sources or revisions:
        owners = {}  # A request ID belongs to one check, wherever that check is kept.
        for receipt in all_receipts.values():
            for row in receipt['accounts']:
                if row['request_id'] is not None and owners.setdefault(row['request_id'], receipt['id']) != receipt['id']:
                    raise api.Invalid('Two reader receipts claim the same request ID.')
        check_graph(api, sources, revisions, final_sources, all_revisions, all_receipts)
    return receipts, sources, revisions, existing_sources


def apply_import(db, receipts, sources, revisions, existing_sources):
    """Imported receipts are always historical; an existing live receipt is left exactly as it was."""
    for record in sources.values():
        old = existing_sources.get(record['id'])
        if old is None:
            db.execute('INSERT INTO reader_sources(id,account_id,source_key,current_revision_id,data) VALUES(?,?,?,?,?)',
                       (record['id'], record['account_id'], record['source_key'], record['current_revision_id'],
                        workflow.canonical_json(record)))
    for record in revisions.values():
        db.execute('INSERT OR IGNORE INTO reader_revisions(id,source_id,account_id,sequence,content_hash,data) VALUES(?,?,?,?,?,?)',
                   (record['id'], record['source_id'], record['account_id'], record['sequence'], record['content_hash'],
                    workflow.canonical_json(record)))
    for record in sources.values():
        old = existing_sources.get(record['id'])
        if old is not None and record['last_seen_at'] > old['last_seen_at']:
            value = merged_source(old, record)
            db.execute('UPDATE reader_sources SET current_revision_id=?,data=? WHERE id=?',
                       (value['current_revision_id'], workflow.canonical_json(value), record['id']))
    for record in receipts.values():
        stored = {key: value for key, value in record.items() if key != 'origin'}
        db.execute('INSERT OR IGNORE INTO reader_checks(id,digest,origin,data) VALUES(?,?,?,?)',
                   (record['id'], record['digest'], 'historical', workflow.canonical_json(stored)))
