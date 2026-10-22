#!/usr/bin/env python3
"""Resonance local edition. Python 3.9+; Windows also uses tzdata."""
import argparse
import base64
import binascii
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import policy
import workflow
import samples
from pathlib import Path
import re
import socket
from socketserver import TCPServer
import sqlite3
import subprocess
import sys
import threading
from urllib.parse import parse_qs, urlsplit
import uuid
import webbrowser

ROOT = Path(__file__).resolve().parent
MAX_BODY = 2 * 1024 * 1024
MAX_IMPORT_BODY = 32 * 1024 * 1024
PLATFORMS = ('YouTube', 'Facebook', 'Instagram', 'X', 'Reddit', 'TikTok', 'LinkedIn', 'Discord', 'Substack', 'Gmail', 'Outlook')
ACCOUNT_FIELDS = {'id', 'platform', 'handle', 'display_name', 'url', 'access_checked_at', 'access_note', 'created_at', 'identity_mode', 'daring', 'sfw_only'}
REVIEW_FIELDS = {'id', 'account_id', 'kind', 'title', 'excerpt', 'source_url', 'observed_at', 'reviewed_at', 'sample_count', 'severity', 'finding', 'suggestion', 'origin', 'status', 'snoozed_until', 'updated_at'}
REVIEW_CORRECTION_FIELDS = {'source_url_correction', 'source_url_corrected_at'}
ACTION_REQUEST_FIELDS = {'id', 'review_id', 'account_id', 'action', 'status', 'original_excerpt', 'source_url', 'proposed_text', 'created_at', 'updated_at', 'completed_at', 'receipt_url', 'receipt_note', 'source_key', 'evidence_fingerprint', 'execution_state', 'started_at', 'attempt_id', 'blocker_reason', 'next_move', 'identity_check'}
ATTESTATION_FIELDS = {'id', 'review_id', 'account_id', 'source_key', 'evidence_fingerprint', 'action', 'note', 'proposed_text', 'result_url', 'original_excerpt', 'source_url', 'created_at', 'done_at', 'execution_state', 'blocker_reason', 'next_move', 'verified_at', 'receipt_url', 'receipt_note'}
SOURCE_DECISION_FIELDS = {'id', 'account_id', 'source_key', 'evidence_fingerprint', 'status', 'snoozed_until', 'created_at', 'updated_at'}
FLAG_EVIDENCE_FIELDS = {'id', 'review_id', 'account_id', 'title', 'detail', 'excerpt', 'source_url', 'rule', 'category', 'captured_at'}
BRAND_FIELDS = {'description', 'audience', 'voice', 'avoid', 'updated_at'}
ACCESS_FIELDS = {'account_id', 'status', 'method', 'checked_at', 'detail', 'capabilities'}
SETUP_REQUEST_FIELDS = {'id', 'account_id', 'status', 'created_at', 'updated_at', 'note'}
MAIL_SYNC_FIELDS = {'account_id', 'checked_at', 'scope', 'imported_count', 'has_more', 'total_count', 'unread_count'}
MESSAGE_FIELDS = {'id', 'account_id', 'provider_id', 'thread_id', 'subject', 'sender', 'received_at', 'source_url', 'preview', 'is_read', 'imported_at'}
DRAFT_FIELDS = {'id', 'message_id', 'account_id', 'text', 'created_at', 'updated_at'}
FINDING_FIELDS = {'severity', 'title', 'detail', 'source_url', 'kind'}
FINDING_OPTIONAL_FIELDS = {'text', 'quotes', 'context', 'edit_availability'}
MAX_FINDING_TEXT = 20000
MAX_FINDING_QUOTES = 20
MAX_FINDING_QUOTE = 500
THEME_CHOICES = ('dark', 'light', 'system', 'phosphor', 'midnight')
STATIC = {'/': ('index.html', 'text/html'), '/index.html': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'), '/styles.css': ('styles.css', 'text/css'), '/manifest.webmanifest': ('manifest.webmanifest', 'application/manifest+json'), '/sw.js': ('sw.js', 'text/javascript'), '/icon.svg': ('icon.svg', 'image/svg+xml')}


class Invalid(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def uid():
    return uuid.uuid4().hex


def fields(value, allowed, required=()):
    if type(value) is not dict:
        raise Invalid('Expected a JSON object.')
    if set(value) - set(allowed):
        raise Invalid('Unexpected fields. Use the Resonance backup format.')
    if set(required) - set(value):
        raise Invalid('Required fields are missing.')


def string(value, name, limit, required=False):
    if type(value) is not str or len(value) > limit or any(ord(c) < 32 and c not in '\n\r\t' or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise Invalid(f'{name} must be text of at most {limit} characters.')
    if required and not value.strip():
        raise Invalid(f'{name} is required.')
    return value


def identifier(value):
    if type(value) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value):
        raise Invalid('Invalid record identifier.')
    return value


def choice(value, options, name):
    if type(value) is not str or value not in options:
        raise Invalid(f'Invalid {name}.')
    return value


def timestamp(value, nullable=False, date_ok=False):
    if value is None and nullable:
        return value
    string(value, 'Date', 40, True)
    try:
        if date_ok and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            datetime.strptime(value, '%Y-%m-%d')
        else:
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})', value):
                raise ValueError()
            datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise Invalid('Use a valid ISO date or timestamp with a timezone.') from None
    return value


def url(value):
    string(value, 'URL', 2048)
    if not value:
        return value
    try:
        part = urlsplit(value)
        if part.scheme not in ('http', 'https') or not part.hostname or part.username is not None or part.password is not None or any(c.isspace() for c in value) or '\\' in value:
            raise ValueError()
        part.port
    except ValueError:
        raise Invalid('URLs must use http or https and cannot contain credentials.') from None
    return value


def settings_record(data, partial=False):
    fields(data, {'display_name', 'theme'}, () if partial else {'display_name', 'theme'})
    if 'display_name' in data:
        string(data['display_name'], 'Display name', 120)
    if 'theme' in data:
        choice(data['theme'], THEME_CHOICES, 'theme')
    return data


def account_record(data):
    data = dict(data) if type(data) is dict else data
    if type(data) is dict:
        data.setdefault('identity_mode', 'unclassified')
        data.setdefault('daring', 'careful')
        data.setdefault('sfw_only', True)
    fields(data, ACCOUNT_FIELDS, ACCOUNT_FIELDS)
    choice(data['identity_mode'], ('named', 'alias', 'unclassified'), 'identity mode')
    choice(data['daring'], ('careful', 'balanced', 'bold'), 'daring level')
    if type(data['sfw_only']) is not bool or (data['identity_mode'] != 'alias' and not data['sfw_only']):
        raise Invalid('Named and unclassified accounts must always be SFW only.')
    identifier(data['id'])
    choice(data['platform'], PLATFORMS, 'platform')
    string(data['handle'], 'Handle', 200, True)
    string(data['display_name'], 'Display name', 200)
    url(data['url'])
    timestamp(data['access_checked_at'], nullable=True)
    string(data['access_note'], 'Access note', 2000)
    timestamp(data['created_at'])
    return data


def review_record(data):
    fields(data, REVIEW_FIELDS | REVIEW_CORRECTION_FIELDS, REVIEW_FIELDS)
    identifier(data['id'])
    identifier(data['account_id'])
    choice(data['kind'], ('post', 'comment', 'reply', 'sample'), 'evidence type')
    choice(data['severity'], ('attention', 'suggestion', 'strength'), 'priority')
    choice(data['origin'], ('manual', 'import', 'local'), 'origin')
    choice(data['status'], ('open', 'resolved', 'snoozed'), 'status')
    for key, limit, required in [('title', 240, True), ('excerpt', 20000, False), ('finding', 5000, True), ('suggestion', 5000, False)]:
        string(data[key], key.capitalize(), limit, required)
    url(data['source_url'])
    timestamp(data['observed_at'], date_ok=True)
    timestamp(data['reviewed_at'])
    timestamp(data['updated_at'])
    timestamp(data['snoozed_until'], nullable=True)
    if (data['status'] == 'snoozed') != (data['snoozed_until'] is not None):
        raise Invalid('Snoozed reviews require a snooze date; other reviews cannot have one.')
    if type(data['sample_count']) is not int or not 1 <= data['sample_count'] <= 100000:
        raise Invalid('Sample count must be a whole number from 1 to 100000.')
    if REVIEW_CORRECTION_FIELDS & set(data):
        if not REVIEW_CORRECTION_FIELDS <= set(data):
            raise Invalid('A source correction needs its exact link and recorded time.')
        url(data['source_url_correction'])
        if not data['source_url_correction']:
            raise Invalid('A source correction requires an exact link.')
        timestamp(data['source_url_corrected_at'])
        if data['kind'] == 'sample' or data['sample_count'] != 1:
            raise Invalid('An aggregate or sample cannot be given a single action link.')
    return data


def validate_source_correction(account, review):
    if 'source_url_correction' not in review:
        return
    if (workflow.precise_source(review['source_url'], account['platform'], review['kind']) or
            not workflow.precise_source(review['source_url_correction'], account['platform'], review['kind'])):
        raise Invalid('Add an exact supported link for this account and item type to evidence missing one.')
    if not workflow.source_author_matches(account, review['source_url_correction']):
        raise Invalid('This link names a different account. Add the exact link for the saved account.')


def decision_record(data):
    fields(data, {'id', 'status', 'snoozed_until', 'updated_at'}, {'id', 'status', 'snoozed_until', 'updated_at'})
    if type(data['id']) is not str or not re.fullmatch(r'[a-f0-9]{32}', data['id']):
        raise Invalid('Invalid boundary flag identifier.')
    choice(data['status'], ('open', 'resolved', 'snoozed'), 'status')
    timestamp(data['updated_at'])
    timestamp(data['snoozed_until'], nullable=True)
    if (data['status'] == 'snoozed') != (data['snoozed_until'] is not None):
        raise Invalid('Snoozed flags require a snooze date; other flags cannot have one.')
    return data


IDENTITY_FIELDS = {'account_id', 'source_key', 'evidence_fingerprint', 'checked_at', 'status',
                   'platform', 'handle', 'profile_url', 'author_handle', 'author_profile_url', 'source_url', 'detail'}
IDENTITY_ACCOUNT_FIELDS = {'id', 'platform', 'handle', 'display_name', 'url', 'identity_mode', 'daring', 'sfw_only'}
IDENTITY_TTL_SECONDS = 300
EDIT_OBSERVATION_FIELDS = {'status', 'checked_at', 'source_url', 'reason', 'available_until'}


def edit_observation_record(data):
    fields(data, EDIT_OBSERVATION_FIELDS, EDIT_OBSERVATION_FIELDS)
    choice(data['status'], ('available', 'unavailable', 'unknown'), 'edit availability')
    timestamp(data['checked_at'])
    url(data['source_url'])
    if not data['source_url']:
        raise Invalid('Edit availability requires the exact source URL.')
    string(data['reason'], 'Edit availability reason', 2000, True)
    timestamp(data['available_until'], nullable=True)
    if data['available_until'] is not None:
        if data['status'] != 'available' or instant(data['available_until']) <= instant(data['checked_at']):
            raise Invalid('An observed edit deadline must follow an available check.')
    return data


def edit_availability(observation=None):
    """A dated observation, never a guessed platform rule or execution authority."""
    result = {'status': 'unknown', 'reason': 'Your assistant has not checked whether this exact post can be edited.',
              'observation': observation}
    if observation is None:
        return result
    age = (instant(now()) - instant(observation['checked_at'])).total_seconds()
    deadline = observation['available_until']
    if deadline is not None and instant(now()) >= instant(deadline):
        result.update(status='expired', reason='The recorded edit window has ended. Check the post again or choose a follow-up draft.')
    elif age < 0 or age > IDENTITY_TTL_SECONDS:
        result.update(status='expired', reason='This edit check is out of date. Check the post again before planning an edit.')
    else:
        result.update(status=observation['status'], reason=observation['reason'])
    return result


def request_edit_preflight(request, identity, latest=None):
    if request['action'] != 'edit':
        return {'status': 'not_required', 'reason': 'This action does not edit a post.', 'observation': None}
    observed = (request.get('identity_check') or {}).get('edit_availability')
    result = edit_availability(observed)
    if observed is None:
        if latest and latest['status'] == 'unavailable':
            return latest
        return result
    if identity['status'] != 'matched' and result['status'] != 'expired':
        result.update(status='historical' if identity['status'] == 'historical' else 'unknown',
                      reason='A fresh matching account and source-author check must include the edit controls.')
    elif latest and latest.get('observation'):
        newer = latest['observation']
        changed = any(newer[key] != observed[key] for key in ('status', 'available_until'))
        if instant(newer['checked_at']) >= instant(observed['checked_at']) and changed:
            result.update(status='changed', reason='A newer or conflicting edit observation is saved. Inspect the post again.',
                          observation=newer)
    return result


def identity_observation_comparable(account, observation):
    return (bool(observation['handle'].strip() and observation['author_handle'].strip()) and
            (bool(account['url']) or account['platform'] != 'LinkedIn') and
            (not account['url'] or workflow.profile_key(account['platform'], account['url']) is not None) and
            all(workflow.profile_key(account['platform'], observation[key]) is not None
                for key in ('profile_url', 'author_profile_url')))


def identity_observation_record(data):
    fields(data, IDENTITY_FIELDS | {'edit_availability'}, IDENTITY_FIELDS)
    identifier(data['account_id'])
    timestamp(data['checked_at'])
    choice(data['status'], ('signed_in', 'signed_out', 'unavailable'), 'observed sign-in status')
    choice(data['platform'], PLATFORMS, 'observed platform')
    for key in ('handle', 'author_handle'):
        string(data[key], key.replace('_', ' ').capitalize(), 200)
    for key in ('profile_url', 'author_profile_url', 'source_url'):
        url(data[key])
    string(data['source_key'], 'Source key', 4096, True)
    string(data['evidence_fingerprint'], 'Evidence fingerprint', 64, True)
    string(data['detail'], 'Identity observation', 2000, True)
    if 'edit_availability' in data:
        edit_observation_record(data['edit_availability'])
        edit = data['edit_availability']
        if edit['checked_at'] != data['checked_at'] or workflow.normalize_source(edit['source_url']) != workflow.normalize_source(data['source_url']):
            raise Invalid('Check edit controls on the same exact source and at the same time as identity.')
    return data


def identity_check_record(data):
    extra = {'id', 'recorded_at', 'expected', 'matched', 'reason'}
    fields(data, IDENTITY_FIELDS | extra | {'edit_availability'}, IDENTITY_FIELDS | extra)
    identity_observation_record({key: data[key] for key in IDENTITY_FIELDS | {'edit_availability'} if key in data})
    identifier(data['id'])
    timestamp(data['recorded_at'])
    if instant(data['checked_at']) > instant(data['recorded_at']):
        raise Invalid('An identity observation cannot be recorded before it was checked.')
    fields(data['expected'], IDENTITY_ACCOUNT_FIELDS, IDENTITY_ACCOUNT_FIELDS)
    # Reuse the account validator without inventing a second boundary contract.
    account_record(dict(data['expected'], access_checked_at=None, access_note='', created_at=data['recorded_at']))
    if type(data['matched']) is not bool:
        raise Invalid('The identity result must be a boolean.')
    string(data['reason'], 'Identity check reason', 2000, True)
    return data


def action_request_record(data):
    data = dict(data) if type(data) is dict else data
    if type(data) is dict:
        legacy_state = {'pending': 'queued', 'completed': 'verified', 'cancelled': 'cancelled'}.get(data.get('status'))
        data.setdefault('source_key', workflow.normalize_source(data.get('source_url', '')))
        data.setdefault('evidence_fingerprint', '')
        data.setdefault('execution_state', legacy_state)
        data.setdefault('started_at', None)
        data.setdefault('attempt_id', None)
        data.setdefault('blocker_reason', '')
        data.setdefault('next_move', 'none')
        data.setdefault('identity_check', None)
    fields(data, ACTION_REQUEST_FIELDS, ACTION_REQUEST_FIELDS)
    identifier(data['id'])
    identifier(data['review_id'])
    identifier(data['account_id'])
    if data['identity_check'] is not None:
        identity_check_record(data['identity_check'])
        check = data['identity_check']
        if check['account_id'] != data['account_id'] or check['expected']['id'] != data['account_id']:
            raise Invalid('Identity history must belong to the request account.')
        if check['source_key'] != data['source_key'] or check['evidence_fingerprint'] != data['evidence_fingerprint']:
            raise Invalid('Identity history must belong to the exact request evidence.')
    choice(data['action'], ('keep', 'edit', 'delete'), 'action')
    choice(data['status'], ('pending', 'completed', 'cancelled'), 'action status')
    choice(data['execution_state'], ('queued', 'running', 'needs_verification', 'blocked', 'verified', 'reopened', 'cancelled'), 'execution state')
    string(data['original_excerpt'], 'Original excerpt', 20000)
    url(data['source_url'])
    string(data['proposed_text'], 'Proposed text', 20000)
    timestamp(data['created_at'])
    timestamp(data['updated_at'])
    timestamp(data['completed_at'], nullable=True)
    timestamp(data['started_at'], nullable=True)
    if data['attempt_id'] is not None:
        identifier(data['attempt_id'])
    string(data['source_key'], 'Source key', 4096)
    string(data['evidence_fingerprint'], 'Evidence fingerprint', 64)
    string(data['blocker_reason'], 'Blocker reason', 5000)
    choice(data['next_move'], ('open_source', 'fix_now', 'confirm_request', 'none'), 'next move')
    url(data['receipt_url'])
    string(data['receipt_note'], 'Receipt note', 5000)
    if instant(data['updated_at']) < instant(data['created_at']):
        raise Invalid('An action request cannot be updated before it was created.')
    if data['action'] == 'edit':
        if not data['proposed_text'].strip():
            raise Invalid('Proposed text is required for an edit.')
        if data['proposed_text'] == data['original_excerpt']:
            raise Invalid('Proposed text must differ from the saved excerpt.')
    elif data['proposed_text']:
        raise Invalid('Proposed text is only allowed for edit requests.')
    if data['action'] in ('edit', 'delete') and not data['source_url']:
        raise Invalid('A saved source URL is required for edit and delete requests.')
    if data['action'] == 'keep' and data['status'] != 'completed':
        raise Invalid('Keep decisions must complete locally when they are created.')
    if data['status'] == 'completed':
        if data['completed_at'] is None:
            raise Invalid('Completed actions require a completion time.')
        if instant(data['completed_at']) < instant(data['created_at']):
            raise Invalid('An action cannot complete before it was created.')
        if instant(data['completed_at']) > instant(data['updated_at']):
            raise Invalid('An action cannot be updated before its completion receipt.')
        if data['action'] in ('edit', 'delete'):
            if not data['receipt_url'] or not data['receipt_note'].strip():
                raise Invalid('Completed edit and delete requests require a receipt URL and verification note.')
        elif data['receipt_url'] or data['receipt_note']:
            raise Invalid('Keep decisions do not use provider receipts.')
    elif data['execution_state'] == 'reopened':
        if data['completed_at'] is not None or bool(data['receipt_url']) != bool(data['receipt_note'].strip()):
            raise Invalid('Reopened actions may preserve only a complete reconciliation receipt pair.')
    elif data['completed_at'] is not None or data['receipt_url'] or data['receipt_note']:
        raise Invalid('Pending and cancelled actions cannot have completion receipts.')
    if data['execution_state'] == 'running' and (not data['started_at'] or not data['attempt_id']):
        raise Invalid('Running actions require a durable start time and attempt ID.')
    if data['execution_state'] in ('needs_verification', 'blocked') and data['attempt_id'] is None and data['status'] == 'pending':
        # A blocker discovered before a provider attempt is allowed; unknown outcomes are not.
        if data['execution_state'] == 'needs_verification':
            raise Invalid('Verification-only requests require a recorded attempt.')
    if data['execution_state'] == 'blocked' and not data['blocker_reason'].strip():
        raise Invalid('Blocked actions require a reason.')
    if data['execution_state'] == 'verified' and data['status'] != 'completed':
        raise Invalid('Verified execution requires a completed request.')
    if data['execution_state'] in ('cancelled', 'reopened') and data['status'] != 'cancelled':
        raise Invalid('Cancelled or reopened execution cannot retain mutation authority.')
    return data


def attestation_record(data):
    fields(data, ATTESTATION_FIELDS, ATTESTATION_FIELDS)
    identifier(data['id']); identifier(data['review_id']); identifier(data['account_id'])
    choice(data['action'], ('edit', 'delete'), 'attestation action')
    choice(data['execution_state'], ('needs_verification', 'blocked', 'verified', 'reopened'), 'attestation state')
    for key, limit, required in [('source_key', 4096, True), ('evidence_fingerprint', 64, True),
                                 ('note', 5000, True), ('proposed_text', 20000, False),
                                 ('original_excerpt', 20000, False), ('blocker_reason', 5000, False),
                                 ('receipt_note', 5000, False)]:
        string(data[key], key.replace('_', ' ').capitalize(), limit, required)
    url(data['source_url']); url(data['result_url']); url(data['receipt_url'])
    timestamp(data['created_at']); timestamp(data['done_at']); timestamp(data['verified_at'], nullable=True)
    choice(data['next_move'], ('open_source', 'fix_now', 'confirm_request', 'none'), 'next move')
    if data['execution_state'] == 'blocked' and not data['blocker_reason'].strip():
        raise Invalid('Blocked attestations require a reason.')
    if data['execution_state'] == 'verified' and (data['verified_at'] is None or not data['receipt_url'] or not data['receipt_note'].strip()):
        raise Invalid('Verified attestations require a result URL, verification note, and time.')
    if data['execution_state'] == 'reopened' and (bool(data['receipt_url']) != bool(data['receipt_note'].strip()) or
                                                  bool(data['receipt_url']) != (data['verified_at'] is not None)):
        raise Invalid('Reopened attestations may preserve only a complete reconciliation receipt.')
    if data['execution_state'] not in ('verified', 'reopened') and (data['verified_at'] is not None or data['receipt_url'] or data['receipt_note']):
        raise Invalid('Only verified attestations carry verification receipts.')
    return data


def source_decision_record(data):
    fields(data, SOURCE_DECISION_FIELDS, SOURCE_DECISION_FIELDS)
    identifier(data['id']); identifier(data['account_id'])
    string(data['source_key'], 'Source key', 4096, True)
    string(data['evidence_fingerprint'], 'Evidence fingerprint', 64, True)
    choice(data['status'], ('open', 'kept', 'snoozed'), 'source decision')
    timestamp(data['snoozed_until'], nullable=True); timestamp(data['created_at']); timestamp(data['updated_at'])
    if (data['status'] == 'snoozed') != (data['snoozed_until'] is not None):
        raise Invalid('Snoozed source decisions require a wake time.')
    return data


def flag_evidence_record(data):
    fields(data, FLAG_EVIDENCE_FIELDS, FLAG_EVIDENCE_FIELDS)
    if type(data['id']) is not str or not re.fullmatch(r'[a-f0-9]{32}', data['id']):
        raise Invalid('Invalid durable flag evidence identifier.')
    identifier(data['review_id']); identifier(data['account_id'])
    for key, limit in [('title', 240), ('detail', 5000), ('excerpt', 20000), ('rule', 200), ('category', 100)]:
        string(data[key], key.capitalize(), limit, key in ('title', 'detail', 'rule', 'category'))
    url(data['source_url']); timestamp(data['captured_at'])
    return data


def brand_defaults():
    return {'description': '', 'audience': '', 'voice': '', 'avoid': '', 'updated_at': None}


def brand_record(data, partial=False):
    fields(data, BRAND_FIELDS, () if partial else BRAND_FIELDS)
    for key in ('description', 'audience', 'voice', 'avoid'):
        if key in data:
            string(data[key], key.capitalize(), 4000)
    if 'updated_at' in data:
        timestamp(data['updated_at'], nullable=True)
    return data


def capabilities(value):
    if type(value) is not list or len(value) > 100:
        raise Invalid('Capabilities must be a list of at most 100 items.')
    seen = set()
    for item in value:
        string(item, 'Capability', 200, True)
        if item in seen:
            raise Invalid('Capabilities cannot contain duplicates.')
        seen.add(item)
    return value


def access_record(data):
    fields(data, ACCESS_FIELDS, ACCESS_FIELDS)
    identifier(data['account_id'])
    choice(data['status'], ('verified', 'blocked', 'needs_login', 'unverified'), 'access status')
    choice(data['method'], ('connector', 'browser', 'unknown'), 'access method')
    timestamp(data['checked_at'], nullable=True)
    string(data['detail'], 'Access detail', 4000)
    capabilities(data['capabilities'])
    if data['status'] == 'verified' and (data['checked_at'] is None or not data['detail'].strip() or not data['capabilities']):
        raise Invalid('Verified access requires a check time, evidence detail, and at least one capability.')
    return data


def setup_request_record(data):
    fields(data, SETUP_REQUEST_FIELDS, SETUP_REQUEST_FIELDS)
    identifier(data['id'])
    identifier(data['account_id'])
    choice(data['status'], ('pending', 'completed', 'cancelled'), 'setup request status')
    timestamp(data['created_at'])
    timestamp(data['updated_at'])
    string(data['note'], 'Setup request note', 4000)
    if instant(data['updated_at']) < instant(data['created_at']):
        raise Invalid('A setup request cannot be updated before it was created.')
    if data['status'] == 'completed' and not data['note'].strip():
        raise Invalid('Completed access checks require a note describing the evidence.')
    return data


def count(value, name, nullable=False):
    if value is None and nullable:
        return value
    if type(value) is not int or not 0 <= value <= 1000000:
        raise Invalid(f'{name} must be a whole number from 0 to 1000000' + (' or null.' if nullable else '.'))
    return value


def mail_sync_record(data):
    fields(data, MAIL_SYNC_FIELDS, MAIL_SYNC_FIELDS)
    identifier(data['account_id'])
    timestamp(data['checked_at'])
    string(data['scope'], 'Mail sync scope', 4000, True)
    count(data['imported_count'], 'Imported count')
    if type(data['has_more']) is not bool:
        raise Invalid('Has more must be true or false.')
    count(data['total_count'], 'Mailbox total', nullable=True)
    count(data['unread_count'], 'Unread count', nullable=True)
    if data['total_count'] is not None and data['unread_count'] is not None and data['unread_count'] > data['total_count']:
        raise Invalid('Unread count cannot exceed the mailbox total.')
    return data


def message_id(account_id, provider_id):
    return hashlib.sha256(canonical_json([account_id, provider_id]).encode('utf-8')).hexdigest()[:32]


def mail_source_url(value, platform):
    string(value, 'Message source URL', 2048, True)
    allowed = {'Gmail': {'mail.google.com'},
               'Outlook': {'outlook.live.com', 'outlook.office.com', 'outlook.office365.com'}}
    try:
        part = urlsplit(value)
        if (part.scheme != 'https' or part.hostname not in allowed.get(platform, set()) or
                part.username is not None or part.password is not None or
                part.port not in (None, 443) or any(c.isspace() for c in value) or '\\' in value):
            raise ValueError()
    except ValueError:
        raise Invalid('Message links must use the official HTTPS mail host for the saved mailbox.') from None
    return value


AUTH_SUBJECT = 'Authentication message'
AUTH_PREVIEW = 'Authentication details are not saved in Resonance. Open the original in your mailbox.'
AUTH_CODE_LABEL = re.compile(r'\b(?:verification|security|authentication|sign[ -]?in|log[ -]?in|one[ -]?time)(?:\s+(?:access|confirmation))?\s+(?:code|passcode|password)\b', re.IGNORECASE)
AUTH_CODE_BEFORE_LABEL = re.compile(r'\b\d{4,10}\b.{0,32}\b(?:verification|security|authentication|sign[ -]?in|log[ -]?in)\b', re.IGNORECASE)
AUTH_LINK_LABEL = re.compile(r'\b(?:magic\s+(?:(?:sign[ -]?in|log[ -]?in|authentication)\s+)?link|(?:secure\s+)?(?:sign[ -]?in|log[ -]?in|authentication)\s+link)\b', re.IGNORECASE)
GENERIC_SECURITY_SUBJECT = re.compile(r'\b(?:password\s+(?:was\s+)?changed|security\s+(?:notification|notice|alert))\b', re.IGNORECASE)


def authentication_message(subject, preview):
    combined = subject + '\n' + preview
    if AUTH_CODE_LABEL.search(combined) or AUTH_CODE_BEFORE_LABEL.search(combined) or AUTH_LINK_LABEL.search(combined):
        return True
    if re.search(r'\bsecure\s+link\b', subject, re.IGNORECASE) and re.search(r'\b(?:sign[ -]?in|log[ -]?in|authentication)\b', combined, re.IGNORECASE):
        return True
    return bool(GENERIC_SECURITY_SUBJECT.search(subject) and re.search(r'https://|\b\d{4,10}\b', preview, re.IGNORECASE))


def sanitize_message(record):
    if authentication_message(record['subject'], record['preview']):
        return dict(record, subject=AUTH_SUBJECT, preview=AUTH_PREVIEW)
    return record


def message_record(data, account_platforms):
    fields(data, MESSAGE_FIELDS, MESSAGE_FIELDS)
    identifier(data['id'])
    identifier(data['account_id'])
    if data['account_id'] not in account_platforms or account_platforms[data['account_id']] not in ('Gmail', 'Outlook'):
        raise Invalid('Messages require a saved Gmail or Outlook account.')
    string(data['provider_id'], 'Provider message ID', 1000, True)
    if data['id'] != message_id(data['account_id'], data['provider_id']):
        raise Invalid('Message ID does not match its account and provider identity.')
    string(data['thread_id'], 'Provider thread ID', 1000)
    string(data['subject'], 'Message subject', 1000)
    string(data['sender'], 'Message sender', 1000, True)
    timestamp(data['received_at'])
    mail_source_url(data['source_url'], account_platforms[data['account_id']])
    string(data['preview'], 'Message preview', 12000)
    if data['is_read'] is not None and type(data['is_read']) is not bool:
        raise Invalid('Read state must be true, false, or null.')
    timestamp(data['imported_at'])
    return data


def draft_record(data):
    fields(data, DRAFT_FIELDS, DRAFT_FIELDS)
    identifier(data['id'])
    identifier(data['message_id'])
    identifier(data['account_id'])
    string(data['text'], 'Draft', 20000, True)
    timestamp(data['created_at'])
    timestamp(data['updated_at'])
    if instant(data['updated_at']) < instant(data['created_at']):
        raise Invalid('A draft cannot be updated before it was created.')
    return data



MONITOR_CONFIG_FIELDS = {'enabled', 'cadence_minutes', 'ends_at', 'scope', 'action_scope', 'automation_id', 'updated_at', 'daily_time', 'timezone'}


def monitor_defaults():
    return dict(enabled=False, cadence_minutes=60, ends_at=None,
                scope='All saved accounts; browser-assisted checks only',
                action_scope='No provider actions are performed by this app',
                automation_id='', updated_at=now(), daily_time=None, timezone='')


def monitor_config_record(data, partial=False):
    data = dict(data) if type(data) is dict else data
    # Accept the former key for one release, including old backup documents.
    if type(data) is dict and 'youtube_action' in data:
        legacy = data.pop('youtube_action')
        if 'action_scope' in data and data['action_scope'] != legacy:
            raise Invalid('Use action_scope once; the old and new action-scope values disagree.')
        data['action_scope'] = legacy
    if type(data) is dict and not partial:
        data.setdefault('daily_time', None)
        data.setdefault('timezone', '')
    fields(data, MONITOR_CONFIG_FIELDS, () if partial else MONITOR_CONFIG_FIELDS)
    if 'enabled' in data and type(data['enabled']) is not bool:
        raise Invalid('Monitoring enabled must be true or false.')
    if 'cadence_minutes' in data and (type(data['cadence_minutes']) is not int or not 1 <= data['cadence_minutes'] <= 10080):
        raise Invalid('Monitoring cadence must be a whole number from 1 to 10080 minutes.')
    for key in ('ends_at', 'updated_at'):
        if key in data:
            timestamp(data[key], nullable=key == 'ends_at')
    for key in ('scope', 'action_scope', 'automation_id'):
        if key in data:
            string(data[key], key.replace('_', ' ').capitalize(), 1000 if key != 'automation_id' else 200, key != 'automation_id')
    if 'daily_time' in data and (data['daily_time'] is not None and (type(data['daily_time']) is not str or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', data['daily_time']))):
        raise Invalid('Daily time must use 24-hour HH:MM or be null.')
    if 'timezone' in data:
        string(data['timezone'], 'Timezone', 100)
        if data['timezone'] and (not re.fullmatch(r'[A-Za-z0-9._+-]+(?:/[A-Za-z0-9._+-]+)*', data['timezone']) or '..' in data['timezone'] or not workflow.valid_timezone(data['timezone'])):
            raise Invalid('Timezone must be empty or use an IANA-style name such as America/Los_Angeles.')
    return data


def finding_quotes(finding):
    """Validate the optional saved text and the verbatim quotes taken from it."""
    text = finding.get('text', '')
    string(text, 'Finding text', MAX_FINDING_TEXT)
    quotes = finding.get('quotes', [])
    if type(quotes) is not list or len(quotes) > MAX_FINDING_QUOTES:
        raise Invalid(f'A finding can carry a list of at most {MAX_FINDING_QUOTES} quotes.')
    if quotes and not text:
        raise Invalid('Quotes need the finding text they were copied from. Save the text you saw, or leave the quotes out.')
    seen = set()
    for quote in quotes:
        string(quote, 'Finding quote', MAX_FINDING_QUOTE)
        if not quote:
            raise Invalid('A finding quote cannot be empty.')
        if quote in seen:
            raise Invalid('A finding cannot repeat the same quote.')
        seen.add(quote)
        if quote not in text:
            raise Invalid('Each quote must be copied verbatim from the finding text.')
    return finding


def instant(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def finding_context(finding, completed_at):
    """Validate optional context without turning parent material into authored text."""
    if 'context' not in finding:
        return
    if finding['kind'] == 'account':
        raise Invalid('Context belongs to a post, comment or reply, not an account finding.')
    context = finding['context']
    if type(context) is not list or not 1 <= len(context) <= 4:
        raise Invalid('Finding context must contain one to four observations.')
    for item in context:
        required = {'kind', 'inspection', 'source_url', 'summary', 'checked_at'}
        fields(item, required | {'image'}, required)
        choice(item['kind'], ('own_media', 'parent_text', 'parent_media', 'thread'), 'context kind')
        choice(item['inspection'], ('inspected', 'not_inspected', 'unavailable'), 'context inspection')
        url(item['source_url'])
        string(item['summary'], 'Context summary', 1200, True)
        timestamp(item['checked_at'])
        if instant(item['checked_at']) > instant(completed_at):
            raise Invalid('Context cannot be checked after the report completed.')
        if 'image' not in item:
            continue
        if item['inspection'] != 'inspected' or item['kind'] not in ('own_media', 'parent_media'):
            raise Invalid('A context image requires inspected media.')
        picture = item['image']
        fields(picture, {'mime_type', 'base64', 'alt'}, {'mime_type', 'base64', 'alt'})
        choice(picture['mime_type'], ('image/png', 'image/jpeg', 'image/webp'), 'context image type')
        string(picture['alt'], 'Context image description', 1000, True)
        string(picture['base64'], 'Context image', 349528, True)
        try:
            raw = base64.b64decode(picture['base64'], validate=True)
        except (ValueError, binascii.Error):
            raise Invalid('A context image must contain valid base64.') from None
        if not 1 <= len(raw) <= 262144:
            raise Invalid('A context image must be at most 256 KiB.')
        signatures = {'image/png': raw.startswith(b'\x89PNG\r\n\x1a\n'),
                      'image/jpeg': raw.startswith(b'\xff\xd8\xff'),
                      'image/webp': raw.startswith(b'RIFF') and raw[8:12] == b'WEBP'}
        if not signatures[picture['mime_type']]:
            raise Invalid('Context image bytes do not match the declared type.')


def monitor_run_record(data, account_ids):
    data = json.loads(canonical_json(data)) if type(data) is dict else data
    if type(data) is dict:
        data.setdefault('requests', [])
    fields(data, {'id', 'started_at', 'completed_at', 'summary', 'accounts', 'requests'}, {'id', 'started_at', 'completed_at', 'summary', 'accounts', 'requests'})
    identifier(data['id'])
    timestamp(data['started_at'])
    timestamp(data['completed_at'])
    if instant(data['completed_at']) < instant(data['started_at']):
        raise Invalid('A monitoring run cannot complete before it started.')
    string(data['summary'], 'Run summary', 4000, True)
    if type(data['accounts']) is not list or len(data['accounts']) > 1000:
        raise Invalid('A run must contain a list of at most 1000 account reports.')
    seen = set()
    for account in data['accounts']:
        keys = {'account_id', 'status', 'checked_at', 'summary', 'coverage', 'items_reviewed', 'deleted_count', 'pending_deletion_count', 'findings'}
        fields(account, keys, keys)
        identifier(account['account_id'])
        if account_ids is not None and account['account_id'] not in account_ids:
            raise Invalid('Monitoring report references an unknown account.')
        if account['account_id'] in seen:
            raise Invalid('Each account can appear only once in a monitoring run.')
        seen.add(account['account_id'])
        choice(account['status'], ('checked', 'partial', 'blocked'), 'monitoring status')
        timestamp(account['checked_at'], nullable=account['status'] == 'blocked')
        if account['checked_at'] is not None and not instant(data['started_at']) <= instant(account['checked_at']) <= instant(data['completed_at']):
            raise Invalid('Account check time must fall within the monitoring run.')
        string(account['summary'], 'Account summary', 2000, True)
        string(account['coverage'], 'Check coverage', 2000, True)
        for key in ('items_reviewed', 'deleted_count', 'pending_deletion_count'):
            if type(account[key]) is not int or not 0 <= account[key] <= 1000000:
                raise Invalid('Monitoring counts must be whole numbers from 0 to 1000000.')
        if type(account['findings']) is not list or len(account['findings']) > 100:
            raise Invalid('An account report must contain at most 100 findings.')
        for finding in account['findings']:
            # text/quotes are optional; absent keys stay absent so stored reports keep their digests.
            fields(finding, FINDING_FIELDS | FINDING_OPTIONAL_FIELDS, FINDING_FIELDS)
            choice(finding['severity'], ('attention', 'suggestion', 'strength'), 'finding priority')
            choice(finding['kind'], ('post', 'comment', 'reply', 'account'), 'finding kind')
            string(finding['title'], 'Finding title', 240, True)
            string(finding['detail'], 'Finding detail', 4000, True)
            url(finding['source_url'])
            finding_quotes(finding)
            finding_context(finding, data['completed_at'])
            if 'edit_availability' in finding:
                edit = edit_observation_record(finding['edit_availability'])
                if (finding['kind'] == 'account' or
                        workflow.normalize_source(edit['source_url']) != workflow.normalize_source(finding['source_url']) or
                        not instant(data['started_at']) <= instant(edit['checked_at']) <= instant(data['completed_at'])):
                    raise Invalid('An edit observation must describe this exact finding source during the report.')
    if type(data['requests']) is not list or len(data['requests']) > 1000:
        raise Invalid('A monitoring report must contain at most 1000 request outcomes.')
    outcome_ids, targets = set(), set()
    keys = {'id', 'target_type', 'account_id', 'source_key', 'evidence_fingerprint', 'outcome',
            'reason', 'receipt_url', 'receipt_note', 'next_move'}
    for outcome in data['requests']:
        fields(outcome, keys | {'reconcile'}, keys)
        outcome.setdefault('reconcile', False)
        keys_with_reconcile = keys | {'reconcile'}
        fields(outcome, keys_with_reconcile, keys_with_reconcile)
        identifier(outcome['id']); identifier(outcome['account_id'])
        choice(outcome['target_type'], ('request', 'attestation'), 'outcome target type')
        choice(outcome['outcome'], ('verified', 'blocked', 'reopened', 'unknown'), 'request outcome')
        choice(outcome['next_move'], ('open_source', 'fix_now', 'confirm_request', 'none'), 'next move')
        string(outcome['source_key'], 'Source key', 4096, True)
        string(outcome['evidence_fingerprint'], 'Evidence fingerprint', 64, True)
        string(outcome['reason'], 'Outcome reason', 5000)
        url(outcome['receipt_url'])
        string(outcome['receipt_note'], 'Receipt note', 5000)
        if type(outcome['reconcile']) is not bool:
            raise Invalid('Reconcile must be true or false.')
        if outcome['id'] in outcome_ids:
            raise Invalid('A report contains duplicate outcome identifiers.')
        outcome_ids.add(outcome['id'])
        target = (outcome['target_type'], outcome['id'])
        if target in targets:
            raise Invalid('A report contains contradictory outcomes for one target.')
        targets.add(target)
        if outcome['outcome'] == 'verified' and (not outcome['receipt_url'] or not outcome['receipt_note'].strip()):
            raise Invalid('Verified outcomes require an observed result URL and verification note.')
        if outcome['outcome'] == 'blocked' and not outcome['reason'].strip():
            raise Invalid('Blocked outcomes require a reason.')
        if outcome['outcome'] == 'unknown' and outcome['next_move'] != 'none':
            raise Invalid('Unknown outcomes enter reconciliation without another action.')
        if outcome['reconcile'] and (outcome['outcome'] not in ('verified', 'reopened') or
                                     not outcome['reason'].strip() or not outcome['receipt_url'] or
                                     not outcome['receipt_note'].strip()):
            raise Invalid('Explicit reconciliation requires verified or reopened plus a reason, result URL, and note.')
    return data


def learned_proposals_record(data):
    """Validate proposal shape only; ingestion and saved evidence checks are separate."""
    if type(data) is not list or len(data) > 20:
        raise Invalid('Learned proposals must be a list of at most 20 lines.')
    for proposal in data:
        keys = {'kind', 'scope', 'statement', 'evidence'}
        fields(proposal, keys, keys)
        choice(proposal['kind'], ('style', 'memory', 'preference'), 'learned kind')
        identifier(proposal['scope'])
        if proposal['scope'] == 'all':
            raise Invalid('A learned proposal must name one account; only the user can widen its scope.')
        string(proposal['statement'], 'Learned statement', 300, True)
        evidence = proposal['evidence']
        if type(evidence) is not list or not 1 <= len(evidence) <= 5:
            raise Invalid('A learned proposal needs one to five evidence items.')
        for item in evidence:
            fields(item, {'source_url', 'quote'}, {'source_url', 'quote'})
            url(item['source_url'])
            if not item['source_url']:
                raise Invalid('Learned evidence needs a source URL.')
            string(item['quote'], 'Learned evidence quote', 300, True)
    return data


def validate_monitor_document(data):
    """Check a public example or private report without consulting any saved data."""
    if type(data) is list:
        learned_proposals_record(data)
        return {'valid': True, 'kind': 'learned', 'learned': len(data), 'scope': 'shape_only'}
    fields(data, {'id', 'started_at', 'completed_at', 'summary', 'accounts', 'requests', 'learned'})
    report = dict(data)
    learned = learned_proposals_record(report.pop('learned', []))
    monitor_run_record(report, account_ids=None)
    return {'valid': True, 'kind': 'report', 'accounts': len(report['accounts']),
            'learned': len(learned), 'scope': 'shape_only'}


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def durable_request(value):
    result = dict(value)
    result.pop('_confirmed', None)
    return result


def draft_check(text):
    string(text, 'Draft', 50000)
    rules = [
        (r'\b(?:idiot|stupid|moron|loser|shut up)\b', 'Personal attack wording', 'Consider describing the action or idea instead of labeling a person.'),
        (r'\b(?:you always|you never|everyone knows|nobody cares)\b', 'Broad generalization', 'A specific example or a narrower claim may make your point easier to hear.'),
        (r'[!?]{3,}', 'Repeated punctuation', 'One punctuation mark can keep emphasis without making the line feel heated.'),
        (r'\b[A-Z]{5,}\b', 'All-capitals emphasis', 'Check whether this is an acronym; otherwise sentence case may read more calmly.'),
    ]
    signals = []
    for pattern, title, detail in rules:
        for match in re.finditer(pattern, text, 0 if title == 'All-capitals emphasis' else re.IGNORECASE):
            # Browser string offsets are UTF-16 code units, including emoji.
            start = len(text[:match.start()].encode('utf-16-le')) // 2
            end = len(text[:match.end()].encode('utf-16-le')) // 2
            signals.append({'title': title, 'detail': detail, 'start': start, 'end': end})
    signals.sort(key=lambda s: (s['start'], s['end']))
    return {'signals': signals[:200], 'word_count': len(re.findall(r"\b[\w]+(?:['’][\w]+)*\b", text)), 'summary': 'Local writing checks found wording to consider. Context and intent still matter.' if signals else 'These limited local writing checks found no flags. They do not predict audience response.'}


DATABASE_NAME, LEGACY_DATABASE_NAME = 'resonance.sqlite3', 'social-brief.sqlite3'
DATABASE_NAMES = (DATABASE_NAME, LEGACY_DATABASE_NAME)


def data_directory():
    # The old environment variable remains supported for existing installations.
    custom = os.environ.get('RESONANCE_DATA_DIR') or os.environ.get('SOCIAL_BRIEF_DATA_DIR')
    if custom:
        return safe_data_directory(custom)
    if sys.platform == 'darwin':
        parent = Path.home() / 'Library' / 'Application Support'
    elif sys.platform == 'win32':
        parent = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData' / 'Local')))
    else:
        parent = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local' / 'share')))
    # A folder is this app's data folder when it holds a database. The Mac launcher
    # keeps its runtimes in a folder of the new name beside a legacy data folder, so
    # an empty folder must never win over the folder that holds the user's data.
    names = ('Resonance', 'Social Brief', 'social-brief')
    for name in names:
        if any((parent / name / database).is_file() for database in DATABASE_NAMES):
            return safe_data_directory(parent / name)
    # Legacy folders are used in place; Linux originally used the hyphenated name.
    for name in names:
        candidate = parent / name
        if candidate.is_dir():
            return safe_data_directory(candidate)
    return safe_data_directory(parent / 'Resonance')


def safe_data_directory(path):
    path = Path(path).expanduser().resolve()
    # Refuse common cloud-sync destinations, even when reached through a symlink.
    lowered = [part.casefold() for part in path.parts]
    if ROOT == path or ROOT in path.parents or any('icloud' in p or p in ('.git', 'mobile documents', 'cloudstorage', 'dropbox', 'google drive', 'googledrive') or p.startswith('onedrive') for p in lowered) or any((parent / '.git').exists() for parent in (path, *path.parents)):
        raise Invalid('Choose a private app-data folder outside repositories and cloud-synced folders.')
    # Optional personal exclusions: one directory per line, beside the data folder.
    # Ancestor configs also protect reports and backups stored below that folder.
    for parent in path.parents:
        config = parent / 'never-store-here.txt'
        if not config.is_file():
            continue
        try:
            lines = config.read_text(encoding='utf-8').splitlines()
        except (OSError, UnicodeDecodeError):
            raise Invalid(f'{config} could not be read; fix its permissions or remove it.') from None
        for line in lines:
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            excluded = Path(line.strip()).expanduser()
            if not excluded.is_absolute():
                excluded = parent / excluded
            excluded = excluded.resolve()
            if path == excluded or excluded in path.parents:
                raise Invalid('Choose a data folder outside the locations in never-store-here.txt.')
    return path


def initialize_database(store, db):
    store.initialize_schema(db)


def rename_action_scope(store, db):
    row = db.execute('SELECT data FROM monitor_config WHERE id=1').fetchone()
    config = monitor_config_record(json.loads(row[0]))
    db.execute('UPDATE monitor_config SET data=? WHERE id=1', (canonical_json(config),))


# Append migrations here; each index is its destination PRAGMA user_version.
def identity_check_guards(store, db):
    # Local authority is deliberately absent from portable backups.
    db.execute('CREATE TABLE IF NOT EXISTS action_identity_checks (request_id TEXT PRIMARY KEY REFERENCES action_requests(id) ON DELETE CASCADE, check_id TEXT NOT NULL, binding TEXT NOT NULL)')


def edit_availability_contract(store, db):
    # A format barrier: old versions must refuse the new optional observations.
    # Existing rows and tables are unchanged; the normal migration backup applies.
    pass


MIGRATIONS = (initialize_database, rename_action_scope, identity_check_guards, edit_availability_contract)


class Store:
    def __init__(self, directory, read_only=False):
        self.directory = safe_data_directory(directory)
        self.read_only = read_only
        self.path = self.directory / DATABASE_NAME
        # Keep the legacy database in place; never rename or silently replace it.
        legacy = self.directory / LEGACY_DATABASE_NAME
        if legacy.exists():
            if self.path.exists():
                raise Invalid('Two databases are present; choose the intended data folder before starting Resonance.')
            self.path = legacy
        if read_only:
            if not self.path.is_file():
                raise Invalid('No saved database exists in this data folder.')
            return
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.upgrade_database()
        if os.name != 'nt':
            self.path.chmod(0o600)

    def initialize_schema(self, db):
        schema = '''
            CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS reviews (id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id), data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS activity (id TEXT PRIMARY KEY, at TEXT NOT NULL, description TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS policy_decisions (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS action_requests (id TEXT PRIMARY KEY, review_id TEXT NOT NULL REFERENCES reviews(id), account_id TEXT NOT NULL REFERENCES accounts(id), source_url TEXT NOT NULL, status TEXT NOT NULL, confirmed INTEGER NOT NULL DEFAULT 1 CHECK(confirmed IN (0,1)), data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS attestations (id TEXT PRIMARY KEY, review_id TEXT NOT NULL REFERENCES reviews(id), account_id TEXT NOT NULL REFERENCES accounts(id), source_key TEXT NOT NULL, execution_state TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS source_decisions (id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id), source_key TEXT NOT NULL, evidence_fingerprint TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS flag_evidence (id TEXT PRIMARY KEY, review_id TEXT NOT NULL REFERENCES reviews(id), account_id TEXT NOT NULL REFERENCES accounts(id), data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS monitor_config (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS monitor_runs (id TEXT PRIMARY KEY, completed_epoch REAL NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS communication_brand (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS account_access (account_id TEXT PRIMARY KEY REFERENCES accounts(id), data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS setup_requests (id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id), status TEXT NOT NULL, data TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS one_pending_setup_per_account ON setup_requests(account_id) WHERE status='pending';
            CREATE TABLE IF NOT EXISTS mail_syncs (account_id TEXT PRIMARY KEY REFERENCES accounts(id), checked_epoch REAL NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id), provider_id TEXT NOT NULL, data TEXT NOT NULL, UNIQUE(account_id,provider_id));
            CREATE TABLE IF NOT EXISTS mail_drafts (id TEXT PRIMARY KEY, message_id TEXT NOT NULL UNIQUE REFERENCES messages(id), account_id TEXT NOT NULL REFERENCES accounts(id), data TEXT NOT NULL);
            DROP INDEX IF EXISTS one_pending_action_per_review;
            DROP INDEX IF EXISTS one_pending_action_per_source;
        '''
        for statement in schema.split(';'):
            if statement.strip():
                db.execute(statement)
        columns = {row[1] for row in db.execute('PRAGMA table_info(action_requests)')}
        if 'source_key' not in columns:
            db.execute("ALTER TABLE action_requests ADD COLUMN source_key TEXT NOT NULL DEFAULT ''")
        db.execute('CREATE INDEX IF NOT EXISTS action_requests_by_source ON action_requests(account_id,source_key,status)')
        db.execute('CREATE INDEX IF NOT EXISTS attestations_by_source ON attestations(account_id,source_key,execution_state)')
        db.execute('CREATE INDEX IF NOT EXISTS decisions_by_source ON source_decisions(account_id,source_key,evidence_fingerprint)')
        identity_check_guards(self, db)
        db.execute('INSERT OR IGNORE INTO settings VALUES (1, ?)', (json.dumps({'display_name': '', 'theme': 'dark'}),))
        db.execute('INSERT OR IGNORE INTO monitor_config VALUES(1, ?)', (canonical_json(monitor_defaults()),))
        db.execute('INSERT OR IGNORE INTO communication_brand VALUES(1, ?)', (canonical_json(brand_defaults()),))
        self.materialize_saved_report_reviews(db)
        self.materialize_flag_evidence(db)

    @contextmanager
    def connect(self):
        db = (sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=10)
              if self.read_only else sqlite3.connect(self.path, timeout=10))
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys = ON')
        if self.read_only:
            db.execute('PRAGMA query_only = ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def upgrade_database(self):
        # Inspect an existing database read-only before chmod, schema work or backup.
        # A newer app may have changed its shape; an older app must leave it alone.
        if self.path.exists():
            with closing(sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True)) as probe:
                version = probe.execute('PRAGMA user_version').fetchone()[0]
            if version > len(MIGRATIONS):
                raise Invalid('This data needs a newer version of Resonance; update the app before opening it.')
            if version == len(MIGRATIONS):
                return
        else:
            # Restrict permissions before SQLite writes any private bytes.
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        with self.connect() as db:
            while True:
                db.execute('BEGIN IMMEDIATE')
                version = db.execute('PRAGMA user_version').fetchone()[0]
                if version > len(MIGRATIONS):
                    raise Invalid('This data needs a newer version of Resonance; update the app before opening it.')
                if version == len(MIGRATIONS):
                    db.rollback()
                    return
                target = version + 1
                try:
                    snapshot = self.migration_backup(target)
                except (OSError, sqlite3.Error):
                    raise Invalid('Resonance could not save an upgrade backup; check free space and folder permissions, then try again.') from None
                try:
                    MIGRATIONS[version](self, db)
                    db.execute(f'PRAGMA user_version = {target}')
                    if db.execute('PRAGMA foreign_key_check').fetchone() is not None:
                        raise Invalid('Migration left an invalid reference.')
                    db.commit()
                except Exception:
                    db.rollback()
                    try:
                        # SQLite's backup API restores safely even if the source used WAL.
                        with closing(sqlite3.connect(snapshot.as_uri() + '?mode=ro', uri=True)) as saved:
                            saved.backup(db)
                    except (OSError, sqlite3.Error):
                        raise Invalid(f'Resonance could not restore its upgrade backup; keep {snapshot} and ask for help before reopening the app.') from None
                    raise Invalid('Resonance could not upgrade your data and restored its backup; use your previous app version and report the failed upgrade.') from None

    def migration_backup(self, target):
        folder = self.directory / 'backups' / 'auto'
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        snapshot = folder / f'{stamp}-before-v{target}.sqlite3'
        descriptor = os.open(snapshot, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        try:
            # A separate reader can copy the committed snapshot while the upgrader
            # holds BEGIN IMMEDIATE, excluding other writers until migration ends.
            with closing(sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True)) as source:
                with closing(sqlite3.connect(snapshot)) as destination:
                    source.backup(destination)
            for old in sorted(folder.glob('*-before-v*.sqlite3'))[:-5]:
                old.unlink()
        except (OSError, sqlite3.Error):
            snapshot.unlink(missing_ok=True)
            raise
        return snapshot

    def data_folder_details(self):
        size = 0
        last_backup = None
        automatic = self.directory / 'backups' / 'auto'
        for current, folders, files in os.walk(self.directory, followlinks=False):
            folders[:] = [name for name in folders if not (Path(current) / name).is_symlink()]
            for name in files:
                file = Path(current) / name
                if file.is_symlink():
                    continue
                try:
                    stat = file.stat()
                except FileNotFoundError:
                    continue  # A SQLite journal can disappear during the walk.
                size += stat.st_size
                if file.parent == automatic and re.fullmatch(r'\d{8}T\d{12}Z-before-v\d+\.sqlite3', name):
                    last_backup = max(last_backup or 0, stat.st_mtime)
        return {'path': str(self.directory), 'size_bytes': size,
                'last_automatic_backup': datetime.fromtimestamp(last_backup, timezone.utc).isoformat() if last_backup is not None else None}

    def open_data_folder(self):
        directory = safe_data_directory(self.directory)
        try:
            if sys.platform == 'win32':
                os.startfile(str(directory))
            else:
                command = ['/usr/bin/open' if sys.platform == 'darwin' else 'xdg-open', str(directory)]
                subprocess.run(command, check=True, timeout=5, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            raise Invalid('The folder could not be opened; copy its path into your file manager.') from None
        return {'opened': True}

    @staticmethod
    def get(db, table, record_id):
        row = db.execute(f'SELECT data FROM {table} WHERE id=?', (record_id,)).fetchone()
        if row is None:
            raise LookupError('Record not found.')
        return json.loads(row['data'])

    @staticmethod
    def put(db, table, record):
        blob = json.dumps(record, ensure_ascii=False)
        if table == 'reviews':
            db.execute('INSERT INTO reviews(id,account_id,data) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET account_id=excluded.account_id,data=excluded.data', (record['id'], record['account_id'], blob))
        elif table == 'accounts':
            db.execute('INSERT INTO accounts(id,data) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data', (record['id'], blob))
        else:
            raise ValueError('Unsupported record table.')

    @staticmethod
    def put_action_request(db, request, confirmed=None):
        request = durable_request(request)
        if confirmed is None:
            row = db.execute('SELECT confirmed FROM action_requests WHERE id=?', (request['id'],)).fetchone()
            confirmed = bool(row[0]) if row is not None else True
        db.execute('INSERT INTO action_requests(id,review_id,account_id,source_url,status,confirmed,data,source_key) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET review_id=excluded.review_id,account_id=excluded.account_id,source_url=excluded.source_url,status=excluded.status,confirmed=excluded.confirmed,data=excluded.data,source_key=excluded.source_key',
                   (request['id'], request['review_id'], request['account_id'], request['source_url'], request['status'], int(confirmed), canonical_json(request), request['source_key']))

    @staticmethod
    def present_action_request(request, confirmed=True, scheduled_for=None, current_fingerprint=None, precise=True, conflict=False, identity=None, edit=None):
        queued = request['status'] == 'pending' and request['execution_state'] == 'queued'
        current = bool(request['evidence_fingerprint']) and request['evidence_fingerprint'] == current_fingerprint
        eligible = queued and confirmed and precise and current and not conflict
        if request['action'] == 'keep':
            identity = {'status': 'not_required', 'reason': 'Keep is a local choice; no provider action starts.', 'expected': None, 'observation': None}
        identity = identity or {'status': 'required', 'reason': 'A fresh account identity check is required before starting.', 'expected': None, 'observation': request.get('identity_check')}
        edit = request_edit_preflight(request, identity, edit)
        result = dict(request,
                      identity_preflight=identity, edit_preflight=edit,
                      can_prepare=eligible and not (edit['status'] == 'unavailable' or
                          (edit['status'] == 'changed' and (edit['observation'] or {}).get('status') == 'unavailable')),
                      confirmation_required=request['status'] == 'pending' and not confirmed,
                      scheduled_for=scheduled_for,
                      can_cancel=request['status'] == 'pending' and (request['execution_state'] == 'queued' or
                                                                    (request['execution_state'] == 'blocked' and request['attempt_id'] is None)),
                      can_execute=eligible and identity['status'] == 'matched' and edit['status'] in ('available', 'not_required'))
        if conflict:
            result['blocker_reason'] = 'Multiple action requests target this source. Cancel or reconcile the extra requests before execution.'
            result['next_move'] = 'none'
        return result

    @staticmethod
    def identity_binding(request, account):
        return workflow.digest({'request': {key: request[key] for key in
                               ('id', 'account_id', 'action', 'source_key', 'evidence_fingerprint', 'proposed_text')},
                                'account': {key: account[key] for key in IDENTITY_ACCOUNT_FIELDS}}, 64)

    def identity_preflight(self, db, request):
        if request['action'] == 'keep':
            return {'status': 'not_required', 'reason': 'Keep is a local choice; no provider action starts.', 'expected': None, 'observation': None}
        account = account_record(self.get(db, 'accounts', request['account_id']))
        expected = {key: account[key] for key in IDENTITY_ACCOUNT_FIELDS}
        check = request.get('identity_check')
        result = {'expected': expected, 'observation': check, 'status': 'required',
                  'reason': 'Your assistant must check the active account and the author of this exact source.'}
        if not check:
            return result
        guard = db.execute('SELECT check_id,binding FROM action_identity_checks WHERE request_id=?', (request['id'],)).fetchone()
        if not guard or guard['check_id'] != check['id']:
            result.update(status='historical', reason='Saved history only. A new local identity check is required.')
        elif guard['binding'] != self.identity_binding(request, account):
            result.update(status='changed', reason='The account, boundary or requested change differs from this check. Review the current request.')
        elif not check['matched']:
            status = check['status']
            if status == 'signed_in':
                status = ('unavailable' if check['platform'] == account['platform'] and
                          not identity_observation_comparable(account, check) else 'mismatch')
            result.update(status=status, reason=check['reason'])
        else:
            age = (instant(now()) - instant(check['checked_at'])).total_seconds()
            if age < 0 or age > IDENTITY_TTL_SECONDS:
                result.update(status='expired', reason='The identity check expired. Check the active account again before starting.')
            else:
                result.update(status='matched', reason='The assistant-reported active account and source author match the saved account.')
        return result

    @staticmethod
    def put_attestation(db, record):
        db.execute('INSERT INTO attestations(id,review_id,account_id,source_key,execution_state,data) VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET review_id=excluded.review_id,account_id=excluded.account_id,source_key=excluded.source_key,execution_state=excluded.execution_state,data=excluded.data',
                   (record['id'], record['review_id'], record['account_id'], record['source_key'], record['execution_state'], canonical_json(record)))

    @staticmethod
    def put_source_decision(db, record):
        db.execute('INSERT INTO source_decisions(id,account_id,source_key,evidence_fingerprint,status,data) VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET account_id=excluded.account_id,source_key=excluded.source_key,evidence_fingerprint=excluded.evidence_fingerprint,status=excluded.status,data=excluded.data',
                   (record['id'], record['account_id'], record['source_key'], record['evidence_fingerprint'], record['status'], canonical_json(record)))

    @staticmethod
    def put_access(db, record):
        db.execute('INSERT INTO account_access(account_id,data) VALUES(?,?) ON CONFLICT(account_id) DO UPDATE SET data=excluded.data',
                   (record['account_id'], canonical_json(record)))

    @staticmethod
    def put_setup_request(db, record):
        db.execute('INSERT INTO setup_requests(id,account_id,status,data) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET account_id=excluded.account_id,status=excluded.status,data=excluded.data',
                   (record['id'], record['account_id'], record['status'], canonical_json(record)))

    @staticmethod
    def put_mail_sync(db, record):
        db.execute('INSERT INTO mail_syncs(account_id,checked_epoch,data) VALUES(?,?,?) ON CONFLICT(account_id) DO UPDATE SET checked_epoch=excluded.checked_epoch,data=excluded.data',
                   (record['account_id'], instant(record['checked_at']).timestamp(), canonical_json(record)))

    @staticmethod
    def put_message(db, record):
        record = sanitize_message(record)
        db.execute('INSERT INTO messages(id,account_id,provider_id,data) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET account_id=excluded.account_id,provider_id=excluded.provider_id,data=excluded.data',
                   (record['id'], record['account_id'], record['provider_id'], canonical_json(record)))

    @staticmethod
    def put_draft(db, record):
        db.execute('INSERT INTO mail_drafts(id,message_id,account_id,data) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET message_id=excluded.message_id,account_id=excluded.account_id,data=excluded.data',
                   (record['id'], record['message_id'], record['account_id'], canonical_json(record)))

    def invalidate_access(self, db, account_id, detail='Account identity changed. Request a fresh access check.'):
        if db.execute('SELECT 1 FROM account_access WHERE account_id=?', (account_id,)).fetchone():
            self.put_access(db, access_record(dict(account_id=account_id, status='unverified', method='unknown',
                                                    checked_at=None, detail=detail, capabilities=[])))

    def materialize_saved_report_reviews(self, db, reports=None):
        """Make report-only social findings locally actionable without inventing original text."""
        accounts = {account['id']: account for account in
                    (account_record(json.loads(row[0])) for row in db.execute('SELECT data FROM accounts'))}
        if reports is None:
            reports = [json.loads(row[0]) for row in db.execute('SELECT data FROM monitor_runs ORDER BY rowid')]
        for report in reports:
            for account_report in report.get('accounts', []):
                account = accounts.get(account_report.get('account_id'))
                if account is None or account['platform'] in ('Gmail', 'Outlook'):
                    continue
                for index, finding in enumerate(account_report.get('findings', [])):
                    if finding.get('kind') == 'account':
                        continue
                    review_id = 'report_' + workflow.digest([report['id'], account['id'], index, finding])[:24]
                    if db.execute('SELECT 1 FROM reviews WHERE id=?', (review_id,)).fetchone():
                        continue
                    reviewed_at = report['completed_at']
                    review = review_record(dict(
                        id=review_id, account_id=account['id'], kind=finding['kind'], title=finding['title'],
                        excerpt=finding.get('text', ''), source_url=finding['source_url'], observed_at=reviewed_at,
                        reviewed_at=reviewed_at, sample_count=1, severity=finding['severity'],
                        finding=finding['detail'], suggestion='', origin='import',
                        status='resolved' if finding['severity'] == 'strength' else 'open',
                        snoozed_until=None, updated_at=reviewed_at))
                    self.put(db, 'reviews', review)

    def materialize_flag_evidence(self, db, accounts=None, reviews=None, decisions=None):
        accounts = accounts or [account_record(json.loads(row[0])) for row in db.execute('SELECT data FROM accounts')]
        reviews = reviews or [review_record(json.loads(row[0])) for row in db.execute('SELECT data FROM reviews')]
        decisions = decisions or [decision_record(json.loads(row[0])) for row in db.execute('SELECT data FROM policy_decisions')]
        for flag in self.flags(accounts, reviews, decisions):
            record = flag_evidence_record(dict(
                id=flag['id'], review_id=flag['review_id'], account_id=flag['account_id'],
                title=flag['title'], detail=flag['detail'], excerpt=flag['excerpt'],
                source_url=flag['source_url'], rule=flag['rule'], category=flag['category'],
                captured_at=flag.get('updated_at') or now()))
            db.execute('INSERT OR IGNORE INTO flag_evidence(id,review_id,account_id,data) VALUES(?,?,?,?)',
                       (record['id'], record['review_id'], record['account_id'], canonical_json(record)))

    def assemble_source_items(self, db, accounts, reviews, decisions, requests, attestations, runs):
        account_map = {account['id']: account for account in accounts}
        review_map = {review['id']: review for review in reviews}
        flags = self.flags(accounts, reviews, decisions)
        flag_map = {}
        for flag in flags:
            flag['_historical'] = False
            flag_map.setdefault(flag['review_id'], []).append(flag)
        current_flag_ids = {flag['id'] for flag in flags}
        decision_map = {decision['id']: decision for decision in decisions}
        for row in db.execute('SELECT data FROM flag_evidence ORDER BY rowid'):
            snapshot = flag_evidence_record(json.loads(row[0]))
            if snapshot['id'] in current_flag_ids:
                continue
            historical = dict(snapshot, status=decision_map.get(snapshot['id'], {}).get('status', 'resolved'),
                              snoozed_until=decision_map.get(snapshot['id'], {}).get('snoozed_until'),
                              updated_at=decision_map.get(snapshot['id'], {}).get('updated_at', snapshot['captured_at']),
                              _historical=True)
            flag_map.setdefault(snapshot['review_id'], []).append(historical)
        groups = {}

        def group_for(account_id, source_url, hint, precise_allowed=True, kind='post'):
            account = account_map[account_id]
            identity = workflow.source_identity(account, source_url, hint, kind)
            if not precise_allowed and identity['precise_source']:
                identity.update(source_key='uncertain:' + workflow.digest([identity['source_key'], hint]),
                                precise_source=False,
                                identity_note='This evidence covers a sample or aggregate, not one actionable source item.')
            key = (account_id, identity['source_key'])
            if key not in groups:
                groups[key] = dict(identity, account_id=account_id, reviews=[], raw_findings=[], report_findings=[])
            return groups[key]

        for review in reviews:
            identity = workflow.review_source_identity(account_map[review['account_id']], review)
            key = (review['account_id'], identity['source_key'])
            if key not in groups:
                groups[key] = dict(identity, account_id=review['account_id'], reviews=[], raw_findings=[], report_findings=[])
            group = groups[key]
            group['reviews'].append(review)
            group['raw_findings'].append({
                'id': review['id'], 'title': review['title'], 'detail': review['finding'],
                'severity': review['severity'], 'kind': review['kind'], 'review_ids': [review['id']],
                'flag_ids': [], 'quotes': [], 'provenance': review['origin'] + ':review', '_status': review['status']})
            for flag in flag_map.get(review['id'], []):
                # A saved boundary flag records no exact matched substring, so it highlights nothing.
                group['raw_findings'].append({
                    'id': flag['id'], 'title': flag['title'], 'detail': flag['detail'],
                    'severity': 'attention' if flag.get('category') in ('sfw', 'risk') else 'suggestion',
                    'kind': review['kind'], 'review_ids': [review['id']], 'flag_ids': [flag['id']], 'quotes': [],
                    'provenance': 'local boundary rule:' + flag.get('rule', ''), '_status': flag['status'],
                    '_rule': flag.get('rule', ''), '_historical': flag.get('_historical', False)})

        for run in runs:
            for account_report in run['accounts']:
                for index, finding in enumerate(account_report['findings']):
                    if finding['kind'] == 'account' or account_map[account_report['account_id']]['platform'] in ('Gmail', 'Outlook'):
                        continue
                    finding_id = 'report_' + workflow.digest([run['id'], account_report['account_id'], index, finding])[:24]
                    saved_review = review_map.get(finding_id)
                    if saved_review is not None and saved_review['account_id'] == account_report['account_id']:
                        identity = workflow.review_source_identity(account_map[account_report['account_id']], saved_review)
                        group = groups[(account_report['account_id'], identity['source_key'])]
                    else:
                        group = group_for(account_report['account_id'], finding['source_url'],
                                          ['review', finding_id], True, finding['kind'])
                    group['report_findings'].append({
                        'id': finding_id, 'title': finding['title'], 'detail': finding['detail'],
                        'severity': finding['severity'], 'kind': finding['kind'], 'review_ids': [],
                        'flag_ids': [], 'quotes': list(finding.get('quotes', [])),
                        'provenance': 'monitoring report ' + run['id'] + ' finding ' + finding_id,
                        '_status': 'open', '_historical': False,
                        **({'context': finding['context'], '_context_at': run['completed_at']} if 'context' in finding else {}),
                        **({'edit_availability': finding['edit_availability']} if 'edit_availability' in finding else {})})

        decision_rows = [source_decision_record(json.loads(row[0])) for row in db.execute('SELECT data FROM source_decisions ORDER BY rowid DESC')]
        item_list, item_by_key = [], {}
        current = datetime.now(timezone.utc)
        for (account_id, source_key), group in groups.items():
            # Repeated policy/report semantics become one finding while preserving all supporting IDs.
            combined = {}
            for finding in group['raw_findings'] + group['report_findings']:
                semantic = workflow.canonical_json({key: finding.get(key) for key in ('title', 'detail', 'severity', 'kind', '_rule')})
                if semantic not in combined:
                    combined[semantic] = dict(finding)
                    combined[semantic]['_provenance'] = [finding['provenance']]
                else:
                    saved = combined[semantic]
                    saved['review_ids'] = sorted(set(saved['review_ids']) | set(finding['review_ids']))
                    saved['flag_ids'] = sorted(set(saved['flag_ids']) | set(finding['flag_ids']))
                    # Quotes union in first-seen order; highlighting must not reorder the saved words.
                    saved['quotes'] = saved['quotes'] + [quote for quote in finding['quotes'] if quote not in saved['quotes']]
                    saved['_provenance'].append(finding['provenance'])
                    if finding['_status'] == 'open':
                        saved['_status'] = 'open'
                    saved['_historical'] = saved.get('_historical', False) and finding.get('_historical', False)
                    if 'context' in finding and ('context' not in saved or
                            instant(finding['_context_at']) > instant(saved['_context_at'])):
                        saved['context'] = finding['context']
                        saved['_context_at'] = finding['_context_at']
            findings = []
            for finding in combined.values():
                public = {key: finding[key] for key in ('id', 'title', 'detail', 'severity', 'kind', 'review_ids', 'flag_ids', 'quotes')}
                public['provenance'] = '; '.join(finding['_provenance'])
                if 'context' in finding:
                    public['context'] = finding['context']
                findings.append(public)
            findings.sort(key=lambda value: ({'attention': 0, 'suggestion': 1, 'strength': 2}[value['severity']], value['title'], value['id']))
            current_findings = []
            for semantic, finding in combined.items():
                if finding.get('_historical'):
                    continue
                current_findings.append(next(value for value in findings if value['id'] == finding['id']))
            fingerprint = workflow.evidence_fingerprint(account_map[account_id], source_key, group['reviews'], current_findings)
            group_review_ids = {review['id'] for review in group['reviews']}
            related_requests = [request for request in requests if request['account_id'] == account_id and
                                (request['source_key'] == source_key or request['review_id'] in group_review_ids)]
            related_attestations = [attestation for attestation in attestations if attestation['account_id'] == account_id and
                                    (attestation['source_key'] == source_key or attestation['review_id'] in group_review_ids)]
            related_decisions = [decision for decision in decision_rows if decision['account_id'] == account_id and decision['source_key'] == source_key]
            related_decisions = [decision for decision in related_decisions if decision['status'] != 'snoozed' or instant(decision['snoozed_until']) > current]
            current_requests = [request for request in related_requests if not request['evidence_fingerprint'] or request['evidence_fingerprint'] == fingerprint]
            current_attestations = [attestation for attestation in related_attestations if attestation['evidence_fingerprint'] == fingerprint]
            current_decisions = [decision for decision in related_decisions if decision['evidence_fingerprint'] == fingerprint]
            active_states = {'queued', 'running', 'needs_verification', 'blocked'}
            active_requests = [request for request in current_requests if request['status'] == 'pending' and request['execution_state'] in active_states]
            active_requests.sort(key=lambda value: value['created_at'], reverse=True)
            attempted_requests = [request for request in related_requests if request['status'] == 'pending' and
                                  (request['execution_state'] in ('running', 'needs_verification') or
                                   (request['execution_state'] == 'blocked' and request['attempt_id'] is not None))]
            unresolved_attestations = [value for value in related_attestations
                                       if value['execution_state'] in ('needs_verification', 'blocked')]
            excerpt_reviews = [review for review in group['reviews'] if review['excerpt'].strip()]
            representative_pool = excerpt_reviews or group['reviews']
            representative = max(representative_pool, key=lambda value: (value['reviewed_at'], value['id'])) if representative_pool else None
            def concern(finding):
                if finding.get('_historical') or finding.get('_status') != 'open' or finding['severity'] == 'strength':
                    return False
                if (finding.get('provenance') == 'local:review' and not finding.get('flag_ids') and
                        finding.get('detail', '').startswith('No local boundary rules matched')):
                    return False
                return True
            open_concern = any(concern(finding) for finding in combined.values())
            current_decision = current_decisions[0] if current_decisions else None
            snoozed_until = current_decision['snoozed_until'] if current_decision and current_decision['status'] == 'snoozed' else None
            if attempted_requests or unresolved_attestations:
                status = 'verification_pending'
            elif len(active_requests) > 1:
                status = 'needs_you'
            elif any(value['execution_state'] in ('blocked', 'reopened') for value in active_requests + current_attestations):
                status = 'needs_you'
            elif current_decision and current_decision['status'] == 'kept':
                status = 'kept'
            elif current_decision and current_decision['status'] == 'snoozed':
                status = 'snoozed'
            elif active_requests:
                status = 'waiting' if all(value.get('_confirmed', True) for value in active_requests) else 'needs_you'
            elif (any(value['execution_state'] == 'verified' and value.get('evidence_fingerprint') and
                      value.get('action') != 'keep' for value in current_requests) or
                  any(value['execution_state'] == 'verified' for value in current_attestations)):
                status = 'verified'
            elif open_concern:
                status = 'needs_you'
            elif any(review['origin'] == 'local' for review in group['reviews']):
                status = 'checked'
            else:
                status = 'resolved'
            labels = {'needs_you': 'Needs you', 'waiting': 'Waiting for assistant',
                      'verification_pending': 'Verification pending', 'kept': 'Kept',
                      'verified': 'Verified', 'snoozed': 'Snoozed', 'checked': 'Checked',
                      'resolved': 'Resolved'}
            source_changed = any(value.get('evidence_fingerprint') and value['evidence_fingerprint'] != fingerprint
                                 for value in related_requests + related_attestations + related_decisions)
            edit_observations = [finding['edit_availability'] for finding in group['report_findings'] if 'edit_availability' in finding]
            edit_observations += [request['identity_check']['edit_availability'] for request in related_requests
                                  if request['evidence_fingerprint'] == fingerprint and (request.get('identity_check') or {}).get('matched') and
                                  'edit_availability' in request['identity_check']]
            latest_edit = max(edit_observations, key=lambda value: (instant(value['checked_at']),
                              {'available': 0, 'unknown': 1, 'unavailable': 2}[value['status']])) if edit_observations else None
            edit_state = edit_availability(latest_edit)
            edit_hold = status == 'waiting' and edit_state['status'] == 'unavailable' and any(
                value['action'] == 'edit' and value['execution_state'] == 'queued' for value in active_requests)
            if edit_hold:
                status = 'needs_you'
            item = {
                'id': 'src_' + workflow.digest([account_id, source_key])[:24], 'source_key': source_key,
                'account_id': account_id, 'source_url': group['source_url'],
                'precise_source': group['precise_source'], 'identity_note': group['identity_note'],
                'review_ids': [review['id'] for review in group['reviews']],
                'review_id': representative['id'] if representative else None,
                'title': representative['title'] if representative else (findings[0]['title'] if findings else 'Saved evidence'),
                'excerpt': representative['excerpt'] if representative else '', 'findings': findings,
                'evidence_fingerprint': fingerprint, 'status': status,
                'status_label': 'Editing unavailable' if edit_hold else labels[status],
                'needs_attention': status == 'needs_you',
                'request_ids': [request['id'] for request in related_requests],
                'active_request_id': (sorted(attempted_requests, key=lambda value: value['created_at'], reverse=True)[0]['id']
                                      if attempted_requests else (active_requests[0]['id'] if active_requests else None)),
                'attestation_ids': [attestation['id'] for attestation in related_attestations],
                'snoozed_until': snoozed_until, 'source_changed': source_changed,
                'edit_availability': edit_state,
            }
            item_list.append(item); item_by_key[(account_id, source_key)] = item
        item_list.sort(key=lambda value: (not value['needs_attention'], value['title'].casefold(), value['id']))
        return item_list, item_by_key

    def source_snapshot(self, db):
        accounts = [account_record(json.loads(row[0])) for row in db.execute('SELECT data FROM accounts ORDER BY rowid')]
        reviews = [review_record(json.loads(row[0])) for row in db.execute('SELECT data FROM reviews ORDER BY rowid DESC')]
        decisions = [decision_record(json.loads(row[0])) for row in db.execute('SELECT data FROM policy_decisions')]
        self.materialize_flag_evidence(db, accounts, reviews, decisions)
        requests = []
        for row in db.execute('SELECT data,confirmed FROM action_requests ORDER BY rowid DESC'):
            request = action_request_record(json.loads(row['data']))
            confirmed = bool(row['confirmed'])
            if request['status'] == 'pending' and request['execution_state'] == 'queued' and not request['evidence_fingerprint']:
                request.update(execution_state='blocked',
                               blocker_reason='This legacy request needs a fresh source and evidence confirmation before execution.',
                               next_move='confirm_request', updated_at=now())
                self.put_action_request(db, request, confirmed=False)
                confirmed = False
            request['_confirmed'] = confirmed
            requests.append(request)
        attestations = [attestation_record(json.loads(row[0])) for row in db.execute('SELECT data FROM attestations ORDER BY rowid DESC')]
        monitoring = self.monitoring(db, limit=None)
        items, by_key = self.assemble_source_items(db, accounts, reviews, decisions, requests, attestations, monitoring['runs'])
        by_review = {review_id: item for item in items for review_id in item['review_ids']}
        return items, by_key, by_review, requests, attestations

    def validate_communication_import(self, db, payload, account_platforms):
        fields(payload, {'brand', 'access', 'setup_requests', 'mail_syncs', 'messages', 'drafts'},
               {'brand', 'access', 'setup_requests', 'mail_syncs', 'messages', 'drafts'})
        normalized = {'brand': brand_record(payload['brand'])}
        limits = {'access': 10000, 'setup_requests': 10000, 'mail_syncs': 10000,
                  'messages': 100000, 'drafts': 10000}
        for key, limit in limits.items():
            if type(payload[key]) is not list or len(payload[key]) > limit:
                raise Invalid(f'Communication {key.replace("_", " ")} must be a list of at most {limit} records.')

        access_rows, access_ids = [], set()
        for value in payload['access']:
            record = access_record(value)
            if record['account_id'] not in account_platforms:
                raise Invalid('Access snapshot references an unknown account.')
            if record['account_id'] in access_ids:
                raise Invalid('Backup contains duplicate access snapshots for an account.')
            access_ids.add(record['account_id'])
            access_rows.append(access_record(dict(account_id=record['account_id'], status='unverified', method='unknown',
                                                    checked_at=None,
                                                    detail='Imported access evidence must be checked again before it can be treated as current.',
                                                    capabilities=[])))
        normalized['access'] = access_rows

        existing_setup = {record['id']: setup_request_record(record) for record in
                          (json.loads(row[0]) for row in db.execute('SELECT data FROM setup_requests'))}
        setup_rows, setup_ids = [], set()
        merged_setup = dict(existing_setup)
        for value in payload['setup_requests']:
            record = setup_request_record(value)
            if record['account_id'] not in account_platforms:
                raise Invalid('Setup request references an unknown account.')
            if record['id'] in setup_ids:
                raise Invalid('Backup contains duplicate setup request identifiers.')
            setup_ids.add(record['id'])
            if record['id'] in existing_setup and existing_setup[record['id']]['account_id'] != record['account_id']:
                raise Invalid('A setup request ID already belongs to another account.')
            merged_setup[record['id']] = record
            setup_rows.append(record)
        pending_setup_accounts = set()
        for record in merged_setup.values():
            if record['status'] == 'pending':
                if record['account_id'] in pending_setup_accounts:
                    raise Invalid('Each account can have at most one pending access check.')
                pending_setup_accounts.add(record['account_id'])
        normalized['setup_requests'] = setup_rows

        existing_syncs = {record['account_id']: mail_sync_record(record) for record in
                          (json.loads(row[0]) for row in db.execute('SELECT data FROM mail_syncs'))}
        sync_rows, sync_ids = [], set()
        for value in payload['mail_syncs']:
            record = mail_sync_record(value)
            if account_platforms.get(record['account_id']) not in ('Gmail', 'Outlook'):
                raise Invalid('Mail sync references an unknown or non-mail account.')
            if record['account_id'] in sync_ids:
                raise Invalid('Backup contains duplicate mail sync records for an account.')
            sync_ids.add(record['account_id'])
            if record['account_id'] in existing_syncs and instant(record['checked_at']) < instant(existing_syncs[record['account_id']]['checked_at']):
                raise Invalid('Imported mail sync time cannot move backwards.')
            sync_rows.append(record)
        normalized['mail_syncs'] = sync_rows

        existing_messages = {}
        for row in db.execute('SELECT data FROM messages'):
            record = message_record(json.loads(row[0]), account_platforms)
            existing_messages[record['id']] = record
        message_rows, message_ids = [], set()
        merged_messages = dict(existing_messages)
        for value in payload['messages']:
            record = message_record(value, account_platforms)
            if record['id'] in message_ids:
                raise Invalid('Backup contains duplicate message identifiers.')
            message_ids.add(record['id'])
            if record['id'] in existing_messages and any(existing_messages[record['id']][key] != record[key] for key in ('account_id', 'provider_id')):
                raise Invalid('A message ID already belongs to another account or provider identity.')
            record = sanitize_message(record)
            merged_messages[record['id']] = record
            message_rows.append(record)
        normalized['messages'] = message_rows

        existing_drafts = {record['id']: draft_record(record) for record in
                           (json.loads(row[0]) for row in db.execute('SELECT data FROM mail_drafts'))}
        merged_drafts = dict(existing_drafts)
        draft_rows, draft_ids = [], set()
        for value in payload['drafts']:
            record = draft_record(value)
            message = merged_messages.get(record['message_id'])
            if message is None or record['account_id'] != message['account_id']:
                raise Invalid('Draft references an unknown message or the wrong account.')
            if record['id'] in draft_ids:
                raise Invalid('Backup contains duplicate draft identifiers.')
            draft_ids.add(record['id'])
            if record['id'] in existing_drafts and any(existing_drafts[record['id']][key] != record[key] for key in ('message_id', 'account_id')):
                raise Invalid('A draft ID already belongs to another message.')
            merged_drafts[record['id']] = record
            draft_rows.append(record)
        seen_message_drafts = set()
        for record in merged_drafts.values():
            if record['message_id'] in seen_message_drafts:
                raise Invalid('Each message can have at most one local draft.')
            seen_message_drafts.add(record['message_id'])
        normalized['drafts'] = draft_rows
        return normalized

    def apply_communication_import(self, db, communication):
        db.execute('UPDATE communication_brand SET data=? WHERE id=1', (canonical_json(communication['brand']),))
        for record in communication['access']:
            self.put_access(db, record)
        for record in communication['setup_requests']:
            self.put_setup_request(db, record)
        for record in communication['mail_syncs']:
            self.put_mail_sync(db, record)
        for record in communication['messages']:
            self.put_message(db, record)
        for record in communication['drafts']:
            self.put_draft(db, record)

    @staticmethod
    def log(db, description):
        db.execute('INSERT INTO activity VALUES(?,?,?)', (uid(), now(), description))
        db.execute('DELETE FROM activity WHERE id NOT IN (SELECT id FROM activity ORDER BY at DESC,rowid DESC LIMIT 100)')

    def state(self, activity=True, monitor_limit=30):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            accounts = [account_record(json.loads(r[0])) for r in db.execute('SELECT data FROM accounts ORDER BY rowid')]
            reviews = [json.loads(r[0]) for r in db.execute('SELECT data FROM reviews ORDER BY rowid DESC')]
            current = datetime.now(timezone.utc)
            for review in reviews:
                if review['status'] == 'snoozed' and datetime.fromisoformat(review['snoozed_until'].replace('Z', '+00:00')) <= current:
                    review.update(status='open', snoozed_until=None, updated_at=now())
                    self.put(db, 'reviews', review)
            requests = []
            for row in db.execute('SELECT data,confirmed FROM action_requests ORDER BY rowid DESC'):
                request = action_request_record(json.loads(row['data']))
                confirmed = bool(row['confirmed'])
                if request['status'] == 'pending' and request['execution_state'] == 'queued' and not request['evidence_fingerprint']:
                    request.update(execution_state='blocked',
                                   blocker_reason='This legacy request needs a fresh source and evidence confirmation before execution.',
                                   next_move='confirm_request', updated_at=now())
                    self.put_action_request(db, request, confirmed=False)
                    confirmed = False
                request['_confirmed'] = confirmed
                requests.append(request)
            attestations = [attestation_record(json.loads(row[0])) for row in db.execute('SELECT data FROM attestations ORDER BY rowid DESC')]
            decisions = [json.loads(r[0]) for r in db.execute('SELECT data FROM policy_decisions')]
            for decision in decisions:
                if decision['status'] == 'snoozed' and datetime.fromisoformat(decision['snoozed_until'].replace('Z', '+00:00')) <= current:
                    decision.update(status='open', snoozed_until=None, updated_at=now())
                    db.execute('UPDATE policy_decisions SET data=? WHERE id=?', (json.dumps(decision), decision['id']))
            self.materialize_flag_evidence(db, accounts, reviews, decisions)
            monitoring = self.monitoring(db, limit=monitor_limit)
            monitoring['next_scheduled_check'] = workflow.next_scheduled_check(monitoring['config'])
            evidence_runs = monitoring['runs'] if monitor_limit is None else self.monitoring(db, limit=None)['runs']
            source_items, item_by_key = self.assemble_source_items(db, accounts, reviews, decisions, requests, attestations, evidence_runs)
            scheduled_for = monitoring['next_scheduled_check']
            presented = []
            for request in requests:
                item = item_by_key.get((request['account_id'], request['source_key']))
                active = [value for value in requests if value['account_id'] == request['account_id'] and
                          value['source_key'] == request['source_key'] and value['status'] == 'pending' and
                          value['execution_state'] in ('queued', 'running', 'needs_verification', 'blocked')]
                raw = dict(request); confirmed = raw.pop('_confirmed')
                presented.append(self.present_action_request(
                    raw, confirmed, scheduled_for if raw['status'] == 'pending' and raw['execution_state'] == 'queued' else None,
                    item['evidence_fingerprint'] if item else None,
                    bool(item and item['precise_source']), len(active) > 1, self.identity_preflight(db, request),
                    item.get('edit_availability') if item else None))
            result = {'schema_version': 2, 'settings': json.loads(db.execute('SELECT data FROM settings WHERE id=1').fetchone()[0]),
                      'accounts': accounts, 'reviews': reviews, 'action_requests': presented,
                      'attestations': attestations, 'source_decisions': [source_decision_record(json.loads(row[0])) for row in db.execute('SELECT data FROM source_decisions ORDER BY rowid DESC')],
                      'flag_evidence': [flag_evidence_record(json.loads(row[0])) for row in db.execute('SELECT data FROM flag_evidence ORDER BY rowid')],
                      'source_items': source_items}
            result['monitoring'] = monitoring
            result['communication'] = self.communication(db, accounts)
            result['policy_decisions'] = decisions
            result['policy_flags'] = self.flags(accounts, reviews, decisions)
            if activity:
                result['activity'] = [dict(r) for r in db.execute('SELECT id,at,description FROM activity ORDER BY at DESC,rowid DESC LIMIT 100')]
            return result

    @staticmethod
    def flags(accounts, reviews, decisions):
        by_id = {d['id']: d for d in decisions}
        flags = policy.account_flags(accounts, reviews)
        for flag in flags:
            if flag['id'] in by_id:
                flag.update(by_id[flag['id']])
        return flags

    def backup(self):
        state = self.state(activity=False, monitor_limit=None)
        state.pop('policy_flags')
        state.pop('source_items')
        state['reviews'].sort(key=lambda value: (value['reviewed_at'], value['id']), reverse=True)
        for request in state['action_requests']:
            for field in ('confirmation_required', 'scheduled_for', 'can_cancel', 'can_execute', 'can_prepare', 'identity_preflight', 'edit_preflight'):
                request.pop(field)
        state['monitoring'].pop('next_scheduled_check', None)
        return state

    def work(self):
        """Expose executor work, moving abandoned running attempts into reconciliation."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for row in db.execute("SELECT data FROM action_requests WHERE status='pending'"):
                request = action_request_record(json.loads(row[0]))
                if request['execution_state'] == 'running':
                    request.update(execution_state='needs_verification', updated_at=now(),
                                   blocker_reason='A prior attempt started without a recorded outcome. Reconcile the live source before any retry.',
                                   next_move='none')
                    self.put_action_request(db, request)
        state = self.state(activity=False, monitor_limit=1)
        queued = [request for request in state['action_requests'] if request['can_prepare']]
        held = [request for request in state['action_requests'] if request['status'] == 'pending' and
                request['execution_state'] in ('queued', 'blocked') and not request['can_prepare']]
        for request in held:
            if request['action'] == 'edit' and request['edit_preflight']['status'] in ('unavailable', 'changed'):
                request.update(blocker_reason=request['edit_preflight']['reason'], next_move='open_source')
        verification_requests = [request for request in state['action_requests'] if request['status'] == 'pending' and
                                 (request['execution_state'] == 'needs_verification' or
                                  request['execution_state'] == 'running' or
                                  (request['execution_state'] == 'blocked' and request['attempt_id'] is not None))]
        verification_attestations = [record for record in state['attestations']
                                     if record['execution_state'] in ('needs_verification', 'blocked')]
        return {'queued': queued, 'held': held,
                'verification': {'requests': verification_requests, 'attestations': verification_attestations}}

    @staticmethod
    def readonly_records(db, table, validator, order='rowid'):
        """Read validated records, refusing disagreement with their indexed columns."""
        records = []
        for row in db.execute(f'SELECT * FROM {table} ORDER BY {order}'):
            if type(row['data']) is not str:
                raise Invalid('Saved record details are unreadable; no action can proceed.')
            record = strict_json(row['data'].encode('utf-8'))
            # The normal loader fills legacy fields for compatibility. A last look
            # must not invent a missing lifecycle or source field instead.
            if table == 'action_requests':
                fields(record, ACTION_REQUEST_FIELDS, ACTION_REQUEST_FIELDS)
            record = validator(record)
            for column in row.keys():
                if column in record and row[column] != record[column]:
                    raise Invalid('Saved record details disagree; no action can proceed.')
            if table == 'action_requests':
                if row['confirmed'] not in (0, 1):
                    raise Invalid('Saved request confirmation is unreadable; no action can proceed.')
                if bool(record['attempt_id']) != (record['started_at'] is not None):
                    raise Invalid('Saved request attempt details are incomplete; no action can proceed.')
                if record['started_at'] is not None and not (instant(record['created_at']) <= instant(record['started_at']) <= instant(record['updated_at'])):
                    raise Invalid('Saved request attempt times disagree; no action can proceed.')
                if record['execution_state'] in ('queued', 'running', 'needs_verification', 'blocked') and record['status'] != 'pending':
                    raise Invalid('Saved request lifecycle disagrees; no action can proceed.')
                record['_confirmed'] = bool(row['confirmed'])
            records.append(record)
        return records

    def profile_snapshot(self, account_id=None):
        """Read review setup in one transaction; never load mail or normalize work."""
        if account_id is not None:
            identifier(account_id)
        if not self.read_only:
            return Store(self.directory, read_only=True).profile_snapshot(account_id)
        with self.connect() as db:
            db.execute('BEGIN')
            if db.execute('PRAGMA user_version').fetchone()[0] != len(MIGRATIONS):
                raise Invalid('This database needs the matching app version before its profile can be read.')
            accounts = self.readonly_records(db, 'accounts', account_record)
            selected = [account for account in accounts if account_id is None or account['id'] == account_id]
            if account_id is not None and not selected:
                raise Invalid('The requested profile account does not exist.')
            brand_row = db.execute('SELECT data FROM communication_brand WHERE id=1').fetchone()
            if brand_row is None:
                raise Invalid('The saved brand profile is missing.')
            brand = brand_record(strict_json(brand_row[0].encode('utf-8')))
            social = {account['id']: account for account in selected
                      if account['platform'] not in ('Gmail', 'Outlook')}
            kept, changed = [], []
            if social:
                placeholders = ','.join('?' for _ in social)
                def records(table, validator):
                    rows = db.execute(f'SELECT * FROM {table} WHERE account_id IN ({placeholders})', tuple(social))
                    result = []
                    for row in rows:
                        value = validator(strict_json(row['data'].encode('utf-8')))
                        if any(row[key] != value[key] for key in row.keys() if key in value):
                            raise Invalid('Saved profile decisions disagree with their indexed records.')
                        result.append(value)
                    return result
                reviews = records('reviews', review_record)
                review_map = {review['id']: review for review in reviews}
                for table, validator in (('action_requests', action_request_record), ('attestations', attestation_record)):
                    for record in records(table, validator):
                        if record['execution_state'] != 'verified':
                            continue
                        review = review_map.get(record['review_id'])
                        destination = kept if record['action'] == 'keep' else changed
                        destination.append(dict(account_id=record['account_id'], action=record['action'],
                                            title=review['title'] if review else 'Saved change',
                                            excerpt=record['original_excerpt'][:500], source_url=record['source_url'],
                                            decided_at=record.get('verified_at') or record.get('completed_at') or record.get('updated_at') or record['done_at']))
            latest = lambda values: sorted(values, key=lambda value: instant(value['decided_at']), reverse=True)[:30]
            keys = ('id', 'platform', 'handle', 'display_name', 'url', 'identity_mode', 'daring', 'sfw_only')
            return {'accounts': [{key: account[key] for key in keys} for account in selected],
                    'brand': brand, 'precedents': {'kept': latest(kept), 'changed': latest(changed)},
                    'learning': {'available': False, 'detail': 'Accepted learned lines and muted-rule preferences are not stored by this edition. --learn is unavailable.'},
                    'scope_note': 'Saved settings and historical social choices only. No live account access or new action is authorized. Mail and private messages were not read.'}

    def request_snapshot(self, request_id):
        """One read transaction for the final look; never normalize or retire work."""
        identifier(request_id)
        if not self.read_only:
            return Store(self.directory, read_only=True).request_snapshot(request_id)
        try:
            with self.connect() as db:
                db.execute('BEGIN')
                if db.execute('PRAGMA user_version').fetchone()[0] != len(MIGRATIONS):
                    raise Invalid('This database needs the matching app version before a request can be read.')
                accounts = self.readonly_records(db, 'accounts', account_record)
                account_ids = {account['id'] for account in accounts}
                reviews = self.readonly_records(db, 'reviews', review_record, 'rowid DESC')
                decisions = self.readonly_records(db, 'policy_decisions', decision_record)
                requests = self.readonly_records(db, 'action_requests', action_request_record, 'rowid DESC')
                attestations = self.readonly_records(db, 'attestations', attestation_record, 'rowid DESC')
                source_decisions = self.readonly_records(db, 'source_decisions', source_decision_record, 'rowid DESC')
                flags = self.readonly_records(db, 'flag_evidence', flag_evidence_record)
                for record in reviews + requests + attestations + source_decisions + flags:
                    if record['account_id'] not in account_ids:
                        raise Invalid('Saved evidence references an unknown account; no action can proceed.')
                request = next((record for record in requests if record['id'] == request_id), None)
                if request is None:
                    raise LookupError('Action request not found.')
                runs = self.readonly_records(db, 'monitor_runs', lambda value: monitor_run_record(value, account_ids),
                                             'completed_epoch DESC,rowid DESC')
                configs = self.readonly_records(db, 'monitor_config', monitor_config_record)
                if len(configs) != 1:
                    raise Invalid('Saved monitoring settings are unreadable; no action can proceed.')
                _, by_key = self.assemble_source_items(db, accounts, reviews, decisions, requests, attestations, runs)
                item = by_key.get((request['account_id'], request['source_key']))
                current = bool(item and item['precise_source'] and request['evidence_fingerprint'] and
                               item['evidence_fingerprint'] == request['evidence_fingerprint'])
                started_at = instant(request['started_at']) if request['started_at'] is not None else None
                user_completed = any(
                    attestation['account_id'] == request['account_id'] and
                    attestation['source_key'] == request['source_key'] and
                    (attestation['execution_state'] in ('needs_verification', 'blocked') or
                     (started_at is not None and
                      (instant(attestation['created_at']) >= started_at or instant(attestation['done_at']) >= started_at)))
                    for attestation in attestations)
                active = [record for record in requests if record['account_id'] == request['account_id'] and
                          record['source_key'] == request['source_key'] and record['status'] == 'pending' and
                          record['execution_state'] in ('queued', 'running', 'needs_verification', 'blocked')]
                raw = dict(request)
                confirmed = raw.pop('_confirmed')
                scheduled_for = (workflow.next_scheduled_check(configs[0])
                                 if raw['status'] == 'pending' and raw['execution_state'] == 'queued' else None)
                presented = self.present_action_request(raw, confirmed, scheduled_for,
                                                        item['evidence_fingerprint'] if item else None,
                                                        bool(item and item['precise_source']), len(active) > 1, self.identity_preflight(db, request),
                                                        item.get('edit_availability') if item else None)
                return {'action_request': presented, 'evidence_current': current, 'user_completed': user_completed,
                        'identity_current': presented['identity_preflight']['status'] == 'matched',
                        'edit_current': presented['edit_preflight']['status'] in ('available', 'not_required')}
        except (KeyError, TypeError, IndexError, ValueError) as error:
            if isinstance(error, Invalid):
                raise
            raise Invalid('Saved request evidence is unreadable; no action can proceed.') from None

    @staticmethod
    def communication(db, accounts):
        account_platforms = {account['id']: account['platform'] for account in accounts}
        return {
            'brand': brand_record(json.loads(db.execute('SELECT data FROM communication_brand WHERE id=1').fetchone()[0])),
            'access': [access_record(json.loads(row[0])) for row in db.execute('SELECT data FROM account_access ORDER BY rowid')],
            'setup_requests': [setup_request_record(json.loads(row[0])) for row in db.execute('SELECT data FROM setup_requests ORDER BY rowid DESC')],
            'mail_syncs': [mail_sync_record(json.loads(row[0])) for row in db.execute('SELECT data FROM mail_syncs ORDER BY checked_epoch DESC,rowid DESC')],
            'messages': [sanitize_message(message_record(json.loads(row[0]), account_platforms)) for row in db.execute('SELECT data FROM messages ORDER BY rowid DESC')],
            'drafts': [draft_record(json.loads(row[0])) for row in db.execute('SELECT data FROM mail_drafts ORDER BY rowid DESC')],
        }

    @staticmethod
    def monitoring(db, limit=30):
        query = 'SELECT data FROM monitor_runs ORDER BY completed_epoch DESC, rowid DESC'
        rows = db.execute(query if limit is None else query + ' LIMIT ?', () if limit is None else (limit,))
        account_ids = {value[0] for value in db.execute('SELECT id FROM accounts')}
        runs = []
        for row in rows:
            record = json.loads(row[0])
            monitor_run_record(record, account_ids)
            runs.append(record)
        return {'config': monitor_config_record(json.loads(db.execute('SELECT data FROM monitor_config WHERE id=1').fetchone()[0])),
                'runs': runs}

    @staticmethod
    def monitor_conflict(db, run):
        row = db.execute('SELECT data FROM monitor_runs WHERE id=?', (run['id'],)).fetchone()
        if row is not None:
            saved = json.loads(row[0])
            saved.setdefault('requests', [])
            candidate = dict(run); candidate.setdefault('requests', [])
            for outcome in saved['requests']:
                outcome.setdefault('reconcile', False)
            for outcome in candidate['requests']:
                outcome.setdefault('reconcile', False)
            if canonical_json(saved) != canonical_json(candidate):
                raise Invalid('A different monitoring report already uses this run ID.')
        return row is not None

    @staticmethod
    def put_monitor_run(db, run):
        db.execute('INSERT OR IGNORE INTO monitor_runs VALUES(?,?,?)',
                   (run['id'], instant(run['completed_at']).timestamp(), canonical_json(run)))

    def set_source_review_status(self, db, item, status, changed_at, snoozed_until=None):
        accounts = [account_record(json.loads(row[0])) for row in db.execute('SELECT data FROM accounts')]
        reviews = [review_record(json.loads(row[0])) for row in db.execute('SELECT data FROM reviews')]
        current_flags = self.flags(accounts, reviews, [decision_record(json.loads(row[0])) for row in db.execute('SELECT data FROM policy_decisions')])
        self.materialize_flag_evidence(db, accounts, reviews)
        for review in reviews:
            if review['id'] not in item['review_ids']:
                continue
            review.update(status=status, snoozed_until=snoozed_until if status == 'snoozed' else None, updated_at=changed_at)
            self.put(db, 'reviews', review_record(review))
        for flag in current_flags:
            if flag['review_id'] in item['review_ids']:
                decision = dict(id=flag['id'], status=status,
                                snoozed_until=snoozed_until if status == 'snoozed' else None,
                                updated_at=changed_at)
                db.execute('INSERT INTO policy_decisions VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                           (decision['id'], canonical_json(decision_record(decision))))

    def validate_outcomes(self, db, outcomes):
        if not outcomes:
            return []
        items, by_key, _, _, _ = self.source_snapshot(db)
        del items
        validated = []
        for outcome in outcomes:
            if outcome['target_type'] == 'request':
                row = db.execute('SELECT data,confirmed FROM action_requests WHERE id=?', (outcome['id'],)).fetchone()
                if row is None:
                    raise Invalid('Monitoring outcome references an unknown action request.')
                target = action_request_record(json.loads(row['data']))
                state = target['execution_state']
                if target['status'] == 'cancelled' or state in ('cancelled', 'reopened', 'verified'):
                    raise Invalid('Monitoring outcome targets stale or retired action work.')
                allowed = {
                    'verified': {'running', 'needs_verification', 'blocked'},
                    'blocked': {'queued', 'running', 'needs_verification', 'blocked'},
                    'reopened': {'running', 'needs_verification', 'blocked'},
                    'unknown': {'running', 'needs_verification'},
                }[outcome['outcome']]
                if outcome['outcome'] == 'verified' and target['attempt_id'] is None:
                    raise Invalid('A request cannot be verified without a recorded provider-action attempt.')
                if outcome['outcome'] == 'reopened' and target['attempt_id'] is None:
                    raise Invalid('A request cannot be reopened by verification without a recorded attempt.')
            else:
                row = db.execute('SELECT data FROM attestations WHERE id=?', (outcome['id'],)).fetchone()
                if row is None:
                    raise Invalid('Monitoring outcome references an unknown user attestation.')
                target = attestation_record(json.loads(row['data']))
                state = target['execution_state']
                allowed = {
                    'verified': {'needs_verification', 'blocked'},
                    'blocked': {'needs_verification', 'blocked'},
                    'reopened': {'needs_verification', 'blocked'},
                    'unknown': {'needs_verification', 'blocked'},
                }[outcome['outcome']]
            if state not in allowed:
                raise Invalid('Monitoring outcome does not match the target lifecycle state.')
            if (target['account_id'] != outcome['account_id'] or target['source_key'] != outcome['source_key'] or
                    target['evidence_fingerprint'] != outcome['evidence_fingerprint']):
                raise Invalid('Monitoring outcome identity does not match its saved target.')
            item = by_key.get((target['account_id'], target['source_key']))
            stale = item is None or not item['precise_source'] or item['evidence_fingerprint'] != target['evidence_fingerprint']
            if stale and not outcome['reconcile']:
                raise Invalid('Monitoring outcome is stale because the source evidence changed.')
            if outcome['reconcile'] and outcome['target_type'] == 'request' and target['attempt_id'] is None:
                raise Invalid('Only a recorded provider-action attempt can use request reconciliation.')
            validated.append((outcome, target, item))
        return validated

    def apply_outcomes(self, db, validated, changed_at):
        summary = {'verified': 0, 'blocked': 0, 'reopened': 0, 'unknown': 0}
        for outcome, target, item in validated:
            result = outcome['outcome']; summary[result] += 1
            if outcome['target_type'] == 'request':
                if result == 'verified':
                    target.update(status='completed', execution_state='verified', completed_at=changed_at,
                                  updated_at=changed_at, receipt_url=outcome['receipt_url'],
                                  receipt_note=outcome['receipt_note'], blocker_reason='', next_move='none')
                    if not outcome['reconcile']:
                        self.set_source_review_status(db, item, 'resolved', changed_at)
                elif result == 'blocked':
                    target.update(execution_state='blocked', updated_at=changed_at,
                                  blocker_reason=outcome['reason'], next_move=outcome['next_move'])
                elif result == 'reopened':
                    target.update(status='cancelled', execution_state='reopened', updated_at=changed_at,
                                  blocker_reason=outcome['reason'], next_move=outcome['next_move'],
                                  receipt_url=outcome['receipt_url'], receipt_note=outcome['receipt_note'])
                    if not outcome['reconcile']:
                        self.set_source_review_status(db, item, 'open', changed_at)
                else:
                    target.update(execution_state='needs_verification', updated_at=changed_at,
                                  blocker_reason=outcome['reason'], next_move='none')
                self.put_action_request(db, action_request_record(target))
            else:
                if result == 'verified':
                    target.update(execution_state='verified', verified_at=changed_at,
                                  receipt_url=outcome['receipt_url'], receipt_note=outcome['receipt_note'],
                                  blocker_reason='', next_move='none')
                    if not outcome['reconcile']:
                        self.set_source_review_status(db, item, 'resolved', changed_at)
                elif result == 'blocked':
                    target.update(execution_state='blocked', blocker_reason=outcome['reason'], next_move=outcome['next_move'])
                elif result == 'reopened':
                    target.update(execution_state='reopened', blocker_reason=outcome['reason'], next_move=outcome['next_move'],
                                  verified_at=changed_at if outcome['receipt_url'] else None,
                                  receipt_url=outcome['receipt_url'], receipt_note=outcome['receipt_note'])
                    if not outcome['reconcile']:
                        self.set_source_review_status(db, item, 'open', changed_at)
                else:
                    target.update(execution_state='needs_verification', blocker_reason=outcome['reason'], next_move='none')
                self.put_attestation(db, attestation_record(target))
        return summary

    @staticmethod
    def request_matches_review(request, review, historical=False):
        sources = {workflow.normalize_source(workflow.review_source_url(review))}
        if historical:
            sources.add(workflow.normalize_source(review['source_url']))
        return (request['review_id'] == review['id'] and
                request['account_id'] == review['account_id'] and
                request['original_excerpt'] == review['excerpt'] and
                workflow.normalize_source(request['source_url']) in sources)

    @staticmethod
    def snapshot_source_keys(account, review, source_url, historical=False):
        identities = [workflow.review_source_identity(account, review)]
        if historical and 'source_url_correction' in review:
            original = {key: value for key, value in review.items() if key not in REVIEW_CORRECTION_FIELDS}
            identities.append(workflow.review_source_identity(account, original))
        return {identity['source_key'] for identity in identities
                if workflow.normalize_source(identity['source_url']) == workflow.normalize_source(source_url)}

    def resolve_action_review(self, db, request, changed_at):
        review = review_record(self.get(db, 'reviews', request['review_id']))
        if not self.request_matches_review(request, review):
            raise Invalid('The saved account, excerpt, or source URL has changed. Create a fresh action request from the current review.')
        accounts = [account_record(json.loads(row[0])) for row in db.execute('SELECT data FROM accounts')]
        reviews = [review_record(json.loads(row[0])) for row in db.execute('SELECT data FROM reviews')]
        for flag in policy.account_flags(accounts, reviews):
            if flag['review_id'] == review['id']:
                decision = dict(id=flag['id'], status='resolved', snoozed_until=None, updated_at=changed_at)
                db.execute('INSERT INTO policy_decisions VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                           (decision['id'], canonical_json(decision)))
        review.update(status='resolved', snoozed_until=None, updated_at=changed_at)
        self.put(db, 'reviews', review_record(review))
        return review

    def mutate(self, method, path, body):
        if not getattr(self, 'is_sample', False) and samples.has_sample_data(body):
            raise Invalid('Sample records cannot be saved with your own data.')
        if path == '/api/data-folder/open' and method == 'POST':
            fields(body, set())
            return 200, self.open_data_folder()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if path == '/api/brand' and method == 'PATCH':
                fields(body, {'description', 'audience', 'voice', 'avoid'})
                brand_record(body, partial=True)
                brand = brand_record(json.loads(db.execute('SELECT data FROM communication_brand WHERE id=1').fetchone()[0]))
                brand.update(body)
                brand['updated_at'] = now()
                db.execute('UPDATE communication_brand SET data=? WHERE id=1', (canonical_json(brand_record(brand)),))
                return 200, {'brand': brand}
            if path == '/api/access-checks' and method == 'POST':
                fields(body, {'account_id'}, {'account_id'})
                identifier(body['account_id'])
                account_record(self.get(db, 'accounts', body['account_id']))
                row = db.execute('SELECT data FROM setup_requests WHERE account_id=? AND status=?',
                                 (body['account_id'], 'pending')).fetchone()
                if row is not None:
                    return 200, {'setup_request': setup_request_record(json.loads(row[0]))}
                changed_at = now()
                request = setup_request_record(dict(id=uid(), account_id=body['account_id'], status='pending',
                                                    created_at=changed_at, updated_at=changed_at, note=''))
                self.put_setup_request(db, request)
                self.log(db, 'Queued a local account access check for the assistant.')
                return 201, {'setup_request': request}
            if re.fullmatch(r'/api/access-checks/[A-Za-z0-9_-]{1,100}', path) and method == 'PATCH':
                fields(body, {'status', 'note'}, {'status'})
                status = choice(body['status'], ('completed', 'cancelled'), 'setup request status')
                request = setup_request_record(self.get(db, 'setup_requests', path.rsplit('/', 1)[1]))
                if request['status'] != 'pending':
                    raise Invalid('Completed and cancelled access checks are immutable.')
                note = body.get('note', '')
                string(note, 'Setup request note', 4000, required=status == 'completed')
                request.update(status=status, note=note, updated_at=now())
                self.put_setup_request(db, setup_request_record(request))
                self.log(db, 'Recorded an account access-check result.' if status == 'completed' else 'Cancelled a pending account access check.')
                return 200, {'setup_request': request}
            if path == '/api/account-access' and method == 'POST':
                access_record(body)
                account_record(self.get(db, 'accounts', body['account_id']))
                self.put_access(db, body)
                self.log(db, 'Saved an assistant-produced account access snapshot.')
                return 200, {'access': body}
            if path == '/api/mail-sync' and method == 'POST':
                fields(body, {'account_id', 'checked_at', 'scope', 'has_more', 'total_count', 'unread_count', 'messages'},
                       {'account_id', 'checked_at', 'scope', 'has_more', 'total_count', 'unread_count', 'messages'})
                identifier(body['account_id'])
                account = account_record(self.get(db, 'accounts', body['account_id']))
                if account['platform'] not in ('Gmail', 'Outlook'):
                    raise Invalid('Mail sync requires a saved Gmail or Outlook account.')
                if type(body['messages']) is not list or len(body['messages']) > 1000:
                    raise Invalid('Mail sync messages must be a list of at most 1000 items.')
                sync = mail_sync_record(dict(account_id=account['id'], checked_at=body['checked_at'], scope=body['scope'],
                                             imported_count=len(body['messages']), has_more=body['has_more'],
                                             total_count=body['total_count'], unread_count=body['unread_count']))
                previous = db.execute('SELECT data FROM mail_syncs WHERE account_id=?', (account['id'],)).fetchone()
                if previous is not None and instant(sync['checked_at']) < instant(mail_sync_record(json.loads(previous[0]))['checked_at']):
                    raise Invalid('Mail sync time cannot move backwards.')
                account_platforms = {account['id']: account['platform']}
                imported_at = now()
                messages, provider_ids = [], set()
                input_fields = {'provider_id', 'thread_id', 'subject', 'sender', 'received_at', 'source_url', 'preview', 'is_read'}
                required = {'provider_id', 'subject', 'sender', 'received_at', 'source_url', 'preview', 'is_read'}
                for item in body['messages']:
                    fields(item, input_fields, required)
                    string(item['provider_id'], 'Provider message ID', 1000, True)
                    if item['provider_id'] in provider_ids:
                        raise Invalid('A mail sync cannot contain the same provider message twice.')
                    provider_ids.add(item['provider_id'])
                    stable_id = message_id(account['id'], item['provider_id'])
                    existing = db.execute('SELECT data FROM messages WHERE id=?', (stable_id,)).fetchone()
                    first_imported = imported_at
                    if existing is not None:
                        saved = message_record(json.loads(existing[0]), account_platforms)
                        if saved['account_id'] != account['id'] or saved['provider_id'] != item['provider_id']:
                            raise Invalid('A saved message ID belongs to another account or provider message.')
                        first_imported = saved['imported_at']
                    message = message_record(dict(id=stable_id, account_id=account['id'], provider_id=item['provider_id'],
                                                  thread_id=item.get('thread_id', ''), subject=item['subject'], sender=item['sender'],
                                                  received_at=item['received_at'], source_url=item['source_url'], preview=item['preview'],
                                                  is_read=item['is_read'], imported_at=first_imported), account_platforms)
                    messages.append(sanitize_message(message))
                for message in messages:
                    self.put_message(db, message)
                self.put_mail_sync(db, sync)
                self.log(db, 'Imported a bounded mailbox preview sync; no provider state was changed.')
                return 201, {'sync': sync, 'imported_count': len(messages)}
            if path == '/api/mail-drafts' and method == 'POST':
                fields(body, {'message_id', 'text'}, {'message_id', 'text'})
                identifier(body['message_id'])
                string(body['text'], 'Draft', 20000, True)
                message = self.get(db, 'messages', body['message_id'])
                row = db.execute('SELECT data FROM mail_drafts WHERE message_id=?', (body['message_id'],)).fetchone()
                changed_at = now()
                if row is None:
                    draft = dict(id=uid(), message_id=message['id'], account_id=message['account_id'], text=body['text'],
                                 created_at=changed_at, updated_at=changed_at)
                    code = 201
                else:
                    draft = draft_record(json.loads(row[0]))
                    if draft['account_id'] != message['account_id']:
                        raise Invalid('The saved draft does not belong to this message account.')
                    draft.update(text=body['text'], updated_at=changed_at)
                    code = 200
                self.put_draft(db, draft_record(draft))
                self.log(db, 'Saved a local reply draft; no message was sent or provider draft created.')
                return code, {'draft': draft}
            if path == '/api/monitoring' and method == 'PATCH':
                body = monitor_config_record(body, partial=True)
                config = monitor_config_record(json.loads(db.execute('SELECT data FROM monitor_config WHERE id=1').fetchone()[0]))
                config.update(body)
                config['updated_at'] = now()
                config = monitor_config_record(config)
                db.execute('UPDATE monitor_config SET data=? WHERE id=1', (canonical_json(config),))
                return 200, {'config': config}
            if path == '/api/monitor-runs' and method == 'POST':
                account_ids = {row[0] for row in db.execute('SELECT id FROM accounts')}
                report = monitor_run_record(body, account_ids)
                replay = self.monitor_conflict(db, report)
                if not replay:
                    if {a['account_id'] for a in report['accounts']} != account_ids:
                        raise Invalid('Include every saved account, using blocked for accounts that could not be checked.')
                    validated = self.validate_outcomes(db, report['requests'])
                    # A later report can revoke an edit check, never revive it.
                    # Keep the attempt and observation history for reconciliation.
                    for account_report in report['accounts']:
                        edits = [finding['edit_availability'] for finding in account_report['findings']
                                 if 'edit_availability' in finding]
                        if not edits:
                            continue
                        for row in db.execute('SELECT data FROM action_requests WHERE account_id=?', (account_report['account_id'],)):
                            request = action_request_record(json.loads(row[0]))
                            saved = (request.get('identity_check') or {}).get('edit_availability')
                            if request['action'] != 'edit' or saved is None:
                                continue
                            if any(workflow.normalize_source(edit['source_url']) == request['source_key'] and
                                   instant(edit['checked_at']) >= instant(saved['checked_at']) and
                                   any(edit[key] != saved[key] for key in ('status', 'available_until')) for edit in edits):
                                db.execute('DELETE FROM action_identity_checks WHERE request_id=?', (request['id'],))
                    self.put_monitor_run(db, body)
                    self.materialize_saved_report_reviews(db, [body])
                    outcome_summary = self.apply_outcomes(db, validated, now())
                    self.log(db, 'Saved a browser-assisted monitoring report.')
                else:
                    outcome_summary = {key: 0 for key in ('verified', 'blocked', 'reopened', 'unknown')}
                return (200 if replay else 201), {'run': body, 'created': not replay,
                                                   'outcomes': outcome_summary}
            if path == '/api/excerpts' and method == 'POST':
                fields(body, {'account_id', 'kind', 'text', 'source_url', 'title'}, {'account_id', 'kind', 'text'})
                identifier(body['account_id'])
                account = account_record(self.get(db, 'accounts', body['account_id']))
                choice(body['kind'], ('post', 'comment', 'reply'), 'evidence type')
                string(body['text'], 'Excerpt', 20000, True)
                signals = policy.check(body['text'], account)
                default_title = 'A saved ' + body['kind'] + ' to review'
                finding = ('Local checks matched: ' + '; '.join(s['title'] for s in signals) + '. These are possible issues for you to review in context.' if signals else 'No local boundary rules matched this excerpt. The wording still needs your judgment; this is not an SFW certification or a full contextual review.')
                review = review_record(dict(id=uid(), account_id=account['id'], kind=body['kind'], title=body.get('title') or default_title, excerpt=body['text'], source_url=body.get('source_url', ''), observed_at=now(), reviewed_at=now(), sample_count=1, severity='attention' if any(s['category'] in {'sfw', 'risk'} for s in signals) else 'strength', finding=finding, suggestion='Open the original and check the surrounding conversation and any images or video. Decide whether the wording fits this account.' if signals else '', origin='local', status='open' if signals else 'resolved', snoozed_until=None, updated_at=now()))
                self.put(db, 'reviews', review)
                self.log(db, 'Added an excerpt for account-boundary review.')
                return 201, {'review': review, 'flag_count': len(signals)}
            if re.fullmatch(r'/api/policy-flags/[a-f0-9]{32}', path) and method == 'PATCH':
                fields(body, {'status'}, {'status'})
                status = choice(body['status'], ('open', 'resolved', 'snoozed'), 'status')
                accounts = [account_record(json.loads(r[0])) for r in db.execute('SELECT data FROM accounts')]
                reviews = [json.loads(r[0]) for r in db.execute('SELECT data FROM reviews')]
                flag_id = path.rsplit('/', 1)[1]
                flag = next((f for f in policy.account_flags(accounts, reviews) if f['id'] == flag_id), None)
                if flag is None:
                    raise LookupError('The excerpt or boundary has changed. Refresh to see current flags.')
                items, _, by_review, requests, _ = self.source_snapshot(db)
                del items
                item = by_review[flag['review_id']]
                active = [value for value in requests if value['account_id'] == item['account_id'] and
                          value['source_key'] == item['source_key'] and value['status'] == 'pending' and
                          value['execution_state'] in ('queued', 'running', 'needs_verification', 'blocked')]
                if status == 'resolved' and active:
                    raise Invalid('This source has mutation or verification work. Use Keep or open the current request.')
                changed_at = now()
                if status == 'resolved':
                    self.set_source_review_status(db, item, 'resolved', changed_at)
                else:
                    wake = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(timespec='seconds').replace('+00:00', 'Z') if status == 'snoozed' else None
                    source = source_decision_record(dict(
                        id=uid(), account_id=item['account_id'], source_key=item['source_key'], evidence_fingerprint=item['evidence_fingerprint'],
                        status=status, snoozed_until=wake,
                        created_at=changed_at, updated_at=changed_at))
                    self.put_source_decision(db, source)
                    self.set_source_review_status(db, item, status, changed_at, wake)
                flag.update(status=status, snoozed_until=(datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(timespec='seconds').replace('+00:00', 'Z') if status == 'snoozed' else None, updated_at=changed_at)
                self.log(db, {'open': 'Reopened a boundary flag.', 'resolved': 'Marked a boundary flag reviewed; original content unchanged.', 'snoozed': 'Snoozed a boundary flag for 24 hours.'}[status])
                return 200, {'flag': flag}
            if path == '/api/attestations' and method == 'POST':
                fields(body, {'review_id', 'action', 'note', 'proposed_text', 'result_url'},
                       {'review_id', 'action', 'note'})
                identifier(body['review_id'])
                action = choice(body['action'], ('edit', 'delete'), 'attestation action')
                note = string(body['note'], 'Attestation note', 5000, True)
                proposed_text = string(body.get('proposed_text', ''), 'Proposed text', 20000)
                result_url = url(body.get('result_url', ''))
                if action == 'delete' and proposed_text:
                    raise Invalid('Proposed text is only used for an attested edit.')
                review = review_record(self.get(db, 'reviews', body['review_id']))
                _, _, by_review, source_requests, attestations = self.source_snapshot(db)
                item = by_review.get(review['id'])
                if item is None or not item['precise_source']:
                    raise Invalid(item['identity_note'] if item else 'An exact source is required for an attestation.')
                exact = next((value for value in attestations
                              if value['account_id'] == item['account_id'] and value['source_key'] == item['source_key'] and
                              value['evidence_fingerprint'] == item['evidence_fingerprint'] and
                              value['execution_state'] in ('needs_verification', 'blocked') and
                              value['action'] == action and value['note'] == note and
                              value['proposed_text'] == proposed_text and value['result_url'] == result_url), None)
                if exact is not None:
                    return 200, {'attestation': exact, 'created': False,
                                 'cancelled_request_ids': [],
                                 'running_request_ids': [value['id'] for value in source_requests
                                                         if value['account_id'] == item['account_id'] and value['source_key'] == item['source_key'] and
                                                         value['execution_state'] in ('running', 'needs_verification')]}
                if any(value['account_id'] == item['account_id'] and
                       (value['source_key'] == item['source_key'] or value['review_id'] in item['review_ids']) and
                       value['execution_state'] in ('needs_verification', 'blocked') for value in attestations):
                    raise Invalid('This source already has a user attestation awaiting verification.')
                changed_at = now(); cancelled, running = [], []
                for request in source_requests:
                    if request['account_id'] != item['account_id'] or (request['source_key'] != item['source_key'] and
                            request['review_id'] not in item['review_ids']) or request['status'] != 'pending':
                        continue
                    if request['execution_state'] == 'queued' or (request['execution_state'] == 'blocked' and request['attempt_id'] is None):
                        cancelled.append(request['id'])
                        request.update(status='cancelled', execution_state='cancelled', updated_at=changed_at)
                        self.put_action_request(db, action_request_record(durable_request(request)))
                    elif request['execution_state'] in ('running', 'needs_verification'):
                        running.append(request['id'])
                attestation = attestation_record(dict(
                    id=uid(), review_id=review['id'], account_id=review['account_id'], source_key=item['source_key'],
                    evidence_fingerprint=item['evidence_fingerprint'], action=action, note=note,
                    proposed_text=proposed_text, result_url=result_url, original_excerpt=review['excerpt'],
                    source_url=item['source_url'], created_at=changed_at, done_at=changed_at,
                    execution_state='needs_verification', blocker_reason='', next_move='none',
                    verified_at=None, receipt_url='', receipt_note=''))
                self.put_attestation(db, attestation)
                self.set_source_review_status(db, item, 'resolved', changed_at)
                self.log(db, 'Recorded work done by the user and queued source verification; no provider action was performed.')
                return 201, {'attestation': attestation, 'created': True,
                             'cancelled_request_ids': cancelled, 'running_request_ids': running}
            if re.fullmatch(r'/api/source-items/[A-Za-z0-9_-]{1,100}/link', path) and method == 'POST':
                fields(body, {'source_url', 'evidence_fingerprint'}, {'source_url', 'evidence_fingerprint'})
                source_url = url(body['source_url'])
                string(body['evidence_fingerprint'], 'Evidence fingerprint', 64, True)
                items, by_key, _, requests, attestations = self.source_snapshot(db)
                item = next((value for value in items if value['id'] == path.split('/')[-2]), None)
                if item is None:
                    raise LookupError('Source item not found. Refresh to see its current link.')
                if item['evidence_fingerprint'] != body['evidence_fingerprint']:
                    raise Invalid('The saved evidence changed. Refresh before adding its exact link.')
                if item['precise_source'] or len(item['review_ids']) != 1:
                    raise Invalid('Add a link only to one saved item that has no exact source.')
                review = dict(review_record(self.get(db, 'reviews', item['review_ids'][0])))
                if 'source_url_correction' in review:
                    raise Invalid('This evidence already has a saved source correction.')
                account = account_record(self.get(db, 'accounts', item['account_id']))
                review.update(source_url_correction=source_url, source_url_corrected_at=now())
                review_record(review)
                validate_source_correction(account, review)
                corrected = workflow.review_source_identity(account, review)
                if (item['account_id'], corrected['source_key']) in by_key:
                    raise Invalid('That exact link already belongs to another source item. Review it there.')
                related = lambda value: (value['account_id'] == item['account_id'] and
                                        (value['source_key'] == item['source_key'] or value['review_id'] in item['review_ids']))
                if any(related(value) and (value['status'] == 'pending' or
                        value['execution_state'] in ('queued', 'running', 'needs_verification', 'blocked')) for value in requests):
                    raise Invalid('Cancel or reconcile this source’s pending work before adding its exact link.')
                if any(related(value) and value['execution_state'] in ('needs_verification', 'blocked') for value in attestations):
                    raise Invalid('Verify or reconcile this source’s user-completed work before adding its exact link.')
                review.update(status='open', snoozed_until=None, updated_at=review['source_url_corrected_at'])
                self.put(db, 'reviews', review)
                self.log(db, 'Added an exact source link while preserving the original evidence; no provider change was made.')
                refreshed = self.source_snapshot(db)[1][(item['account_id'], corrected['source_key'])]
                return 200, {'source_item': refreshed}
            if re.fullmatch(r'/api/source-items/[A-Za-z0-9_-]{1,100}', path) and method == 'PATCH':
                fields(body, {'status'}, {'status'})
                requested = choice(body['status'], ('snoozed', 'open'), 'source status')
                items, _, _, _, _ = self.source_snapshot(db)
                item = next((value for value in items if value['id'] == path.rsplit('/', 1)[1]), None)
                if item is None:
                    raise LookupError('Source item not found.')
                changed_at = now()
                decision = source_decision_record(dict(
                    id=uid(), account_id=item['account_id'], source_key=item['source_key'],
                    evidence_fingerprint=item['evidence_fingerprint'], status=requested,
                    snoozed_until=(datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(timespec='seconds').replace('+00:00', 'Z') if requested == 'snoozed' else None,
                    created_at=changed_at, updated_at=changed_at))
                self.put_source_decision(db, decision)
                self.set_source_review_status(db, item, requested, changed_at, decision['snoozed_until'])
                self.log(db, 'Snoozed a source item for 24 hours.' if requested == 'snoozed' else 'Reopened a source item.')
                refreshed = next(value for value in self.source_snapshot(db)[0] if value['id'] == item['id'])
                return 200, {'source_item': refreshed}
            if re.fullmatch(r'/api/action-requests/[A-Za-z0-9_-]{1,100}/identity-check', path) and method == 'POST':
                observation = identity_observation_record(body)
                request_id = path.split('/')[-2]
                row = db.execute('SELECT data,confirmed FROM action_requests WHERE id=?', (request_id,)).fetchone()
                if row is None:
                    raise LookupError('Action request not found.')
                request = action_request_record(json.loads(row['data']))
                if request['status'] != 'pending' or request['execution_state'] != 'queued' or not row['confirmed']:
                    raise Invalid('Only a locally confirmed queued request can receive a new identity check.')
                _, by_key, _, source_requests, attestations = self.source_snapshot(db)
                item = by_key.get((request['account_id'], request['source_key']))
                if not item or not item['precise_source'] or item['evidence_fingerprint'] != request['evidence_fingerprint']:
                    raise Invalid('The source or evidence changed. A fresh user choice is required.')
                if any(value['id'] != request['id'] and value['account_id'] == request['account_id'] and
                       value['source_key'] == request['source_key'] and value['status'] == 'pending' and
                       value['execution_state'] in ('queued', 'running', 'needs_verification', 'blocked') for value in source_requests):
                    raise Invalid('Conflicting action history must be reconciled before checking identity.')
                if any(value['account_id'] == request['account_id'] and value['source_key'] == request['source_key'] and
                       value['execution_state'] in ('needs_verification', 'blocked') for value in attestations):
                    raise Invalid('User-completed work must be verified before preparing an assistant action.')
                if any(observation[key] != request[key] for key in ('account_id', 'source_key', 'evidence_fingerprint')):
                    raise Invalid('Copy the exact request account, source key and evidence fingerprint before checking identity.')
                if workflow.normalize_source(observation['source_url']) != request['source_key']:
                    raise Invalid('The identity check must inspect the exact requested source.')
                changed_at = now()
                if request.get('identity_check') and instant(observation['checked_at']) < instant(request['identity_check']['checked_at']):
                    raise Invalid('A newer identity observation is already saved. Inspect the active account again.')
                if instant(observation['checked_at']) > instant(changed_at) or instant(observation['checked_at']) < instant(request['created_at']):
                    raise Invalid('Observe identity after this request was created, with no future check date.')
                latest_edit = item['edit_availability']['observation']
                if request['action'] == 'edit' and 'edit_availability' in observation and latest_edit and instant(observation['checked_at']) < instant(latest_edit['checked_at']):
                    raise Invalid('A newer edit observation is already saved. Inspect the post again.')
                account = account_record(self.get(db, 'accounts', request['account_id']))
                expected = {key: account[key] for key in IDENTITY_ACCOUNT_FIELDS}
                matched = False
                if observation['status'] != 'signed_in':
                    reason = 'The account is signed out or its identity could not be inspected. Sign in or repair the saved account, then check again.'
                elif observation['platform'] == account['platform'] and not identity_observation_comparable(account, observation):
                    reason = 'A handle or supported profile link is missing or cannot be compared. Inspect the active account and source author, or explicitly repair the saved profile. No replacement account is assumed.'
                elif observation['platform'] != account['platform'] or not workflow.observed_identity_matches(account, observation['handle'], observation['profile_url']):
                    reason = 'The observed active account does not match the saved account. Select the intended account or explicitly repair its saved details.'
                elif not workflow.observed_identity_matches(account, observation['author_handle'], observation['author_profile_url']) or not workflow.source_author_matches(account, request['source_url']):
                    reason = 'The observed source author does not match the saved account. Do not edit or remove this item.'
                else:
                    matched = True
                    reason = 'Active account and source author match the saved identity.'
                check = identity_check_record(dict(observation, id=uid(), recorded_at=changed_at,
                                                   expected=expected, matched=matched, reason=reason))
                request.update(identity_check=check, updated_at=changed_at)
                self.put_action_request(db, request)
                db.execute('INSERT INTO action_identity_checks VALUES(?,?,?) ON CONFLICT(request_id) DO UPDATE SET check_id=excluded.check_id,binding=excluded.binding',
                           (request_id, check['id'], self.identity_binding(request, account)))
                self.log(db, 'Recorded an assistant-reported account and source-author comparison; no provider action performed.')
                _, refreshed, _, _, _ = self.source_snapshot(db)
                return 200, {'action_request': self.present_action_request(request, True, None, item['evidence_fingerprint'], True, False,
                                                                           self.identity_preflight(db, request),
                                                                           refreshed[(request['account_id'], request['source_key'])]['edit_availability'])}
            if re.fullmatch(r'/api/action-requests/[A-Za-z0-9_-]{1,100}/start', path) and method == 'POST':
                fields(body, {'identity_check_id'}, {'identity_check_id'})
                identifier(body['identity_check_id'])
                request_id = path.split('/')[-2]
                row = db.execute('SELECT data,confirmed FROM action_requests WHERE id=?', (request_id,)).fetchone()
                if row is None:
                    raise LookupError('Action request not found.')
                request = action_request_record(json.loads(row['data']))
                if request['status'] != 'pending' or request['execution_state'] != 'queued':
                    raise Invalid('This request is not safe queued work and cannot be started again.')
                if not row['confirmed']:
                    raise Invalid('Confirm this imported request in Resonance before starting it.')
                _, by_key, _, source_requests, attestations = self.source_snapshot(db)
                item = by_key.get((request['account_id'], request['source_key']))
                if item is None or not item['precise_source'] or request['evidence_fingerprint'] != item['evidence_fingerprint']:
                    raise Invalid('The source or evidence changed. A fresh user choice is required.')
                if any(value['account_id'] == request['account_id'] and value['source_key'] == request['source_key'] and
                       value['execution_state'] in ('needs_verification', 'blocked') for value in attestations):
                    raise Invalid('User-completed work must be verified or reopened before an assistant action can start.')
                conflicts = [value for value in source_requests if value['id'] != request['id'] and
                             value['account_id'] == request['account_id'] and value['source_key'] == request['source_key'] and
                             value['status'] == 'pending' and value['execution_state'] in ('queued', 'running', 'needs_verification', 'blocked')]
                if conflicts:
                    raise Invalid('Conflicting action history must be reconciled before execution.')
                identity = self.identity_preflight(db, request)
                if identity['status'] != 'matched' or request['identity_check']['id'] != body['identity_check_id']:
                    raise Invalid(identity['reason'] if identity['status'] != 'matched' else 'Use the current identity check ID; an earlier check cannot start this request.')
                edit = request_edit_preflight(request, identity, item['edit_availability'])
                if edit['status'] not in ('available', 'not_required'):
                    raise Invalid(edit['reason'])
                changed_at = now()
                request.update(execution_state='running', started_at=changed_at, attempt_id=uid(), updated_at=changed_at)
                self.put_action_request(db, action_request_record(durable_request(request)))
                self.log(db, 'Recorded the durable start of a provider-action attempt.')
                return 200, {'action_request': self.present_action_request(request, True, None,
                                                                            item['evidence_fingerprint'], True, False, self.identity_preflight(db, request))}
            if path == '/api/action-requests' and method == 'POST':
                fields(body, {'review_id', 'action', 'proposed_text'}, {'review_id', 'action'})
                identifier(body['review_id'])
                action = choice(body['action'], ('keep', 'edit', 'delete'), 'action')
                proposed_text = body.get('proposed_text', '')
                string(proposed_text, 'Proposed text', 20000)
                review = review_record(self.get(db, 'reviews', body['review_id']))
                _, _, by_review, source_requests, attestations = self.source_snapshot(db)
                item = by_review.get(review['id'])
                if item is None:
                    raise Invalid('The saved evidence has no current source item.')
                if action in ('edit', 'delete') and not item['precise_source']:
                    raise Invalid(item['identity_note'])
                if action == 'edit' and item['edit_availability']['status'] == 'unavailable':
                    raise Invalid('Editing is unavailable: ' + item['edit_availability']['reason'] +
                                  ' Keep the original or prepare a separately reviewed follow-up draft.')
                if action in ('edit', 'delete') and any(value['account_id'] == item['account_id'] and
                        (value['source_key'] == item['source_key'] or value['review_id'] in item['review_ids']) and
                        value['execution_state'] in ('needs_verification', 'blocked')
                        for value in attestations):
                    raise Invalid('This source has user-completed work awaiting verification or reconciliation.')
                changed_at = now()
                candidate = action_request_record(dict(
                    id=uid(), review_id=review['id'], account_id=review['account_id'],
                    action=action, status='completed' if action == 'keep' else 'pending',
                    original_excerpt=review['excerpt'], source_url=item['source_url'],
                    proposed_text=proposed_text, created_at=changed_at, updated_at=changed_at,
                    completed_at=changed_at if action == 'keep' else None,
                    receipt_url='', receipt_note='', source_key=item['source_key'],
                    evidence_fingerprint=item['evidence_fingerprint'],
                    execution_state='verified' if action == 'keep' else 'queued',
                    started_at=None, attempt_id=None, blocker_reason='', next_move='none'))
                requests = [request for request in source_requests if request['account_id'] == review['account_id'] and
                            (request['source_key'] == item['source_key'] or request['review_id'] in item['review_ids'])]
                active = [request for request in requests if request['status'] == 'pending' and
                          request['execution_state'] in ('queued', 'running', 'needs_verification', 'blocked')]
                if action == 'keep':
                    cancelled_ids = [value['id'] for value in active
                                     if value['execution_state'] == 'queued' or
                                     (value['execution_state'] == 'blocked' and value['attempt_id'] is None)]
                    for pending in active:
                        if pending['execution_state'] == 'queued' or (pending['execution_state'] == 'blocked' and pending['attempt_id'] is None):
                            pending.update(status='cancelled', execution_state='cancelled', updated_at=changed_at)
                            self.put_action_request(db, action_request_record(durable_request(pending)))
                    exact = next((request for request in requests if request['action'] == 'keep' and
                                  request['evidence_fingerprint'] == item['evidence_fingerprint']), None)
                    if exact is not None and not cancelled_ids:
                        public_exact = durable_request(exact)
                        return 200, {'action_request': self.present_action_request(public_exact, True, None,
                                                                                   item['evidence_fingerprint'], item['precise_source'], False),
                                     'created': False, 'cancelled_request_ids': []}
                    decision = source_decision_record(dict(
                        id='decision_' + workflow.digest([item['account_id'], item['source_key'], item['evidence_fingerprint'], 'kept'])[:24],
                        account_id=item['account_id'], source_key=item['source_key'], evidence_fingerprint=item['evidence_fingerprint'],
                        status='kept', snoozed_until=None, created_at=changed_at, updated_at=changed_at))
                    self.put_source_decision(db, decision)
                    if exact is not None:
                        self.set_source_review_status(db, item, 'resolved', changed_at)
                        public_exact = durable_request(exact)
                        return 200, {'action_request': self.present_action_request(public_exact, True, None,
                                                                                   item['evidence_fingerprint'], item['precise_source'], False), 'created': False,
                                     'cancelled_request_ids': cancelled_ids}
                else:
                    if len(active) > 1:
                        raise Invalid('Multiple requests target this source. Cancel or reconcile the extras before confirming work.')
                    exact = next((request for request in active if (request['execution_state'] == 'queued' or
                                  (request['execution_state'] == 'blocked' and request['attempt_id'] is None)) and
                                  request['action'] == action and request['proposed_text'] == proposed_text and
                                  (not request['evidence_fingerprint'] or request['evidence_fingerprint'] == item['evidence_fingerprint'])), None)
                    if exact is not None:
                        scheduled = workflow.next_scheduled_check(monitor_config_record(json.loads(db.execute('SELECT data FROM monitor_config WHERE id=1').fetchone()[0])))
                        if exact['execution_state'] == 'queued' and exact['evidence_fingerprint'] == item['evidence_fingerprint'] and exact.get('_confirmed'):
                            return 200, {'action_request': self.present_action_request(durable_request(exact), True, scheduled,
                                                                                       item['evidence_fingerprint'], item['precise_source'], False, self.identity_preflight(db, exact)),
                                         'created': False}
                        exact.update(source_key=item['source_key'], evidence_fingerprint=item['evidence_fingerprint'],
                                     execution_state='queued', blocker_reason='', next_move='none', updated_at=changed_at)
                        db.execute('DELETE FROM action_identity_checks WHERE request_id=?', (exact['id'],))
                        self.put_action_request(db, durable_request(exact), confirmed=True)
                        db.execute('UPDATE action_requests SET confirmed=1 WHERE id=?', (exact['id'],))
                        return 200, {'action_request': self.present_action_request(durable_request(exact), True, scheduled,
                                                                                   item['evidence_fingerprint'], item['precise_source'], False, self.identity_preflight(db, exact)), 'created': False}
                    if active:
                        raise Invalid('This source already has action work. Open the current request before choosing another action.')
                self.put_action_request(db, candidate)
                if action == 'keep':
                    self.set_source_review_status(db, item, 'resolved', changed_at)
                    self.log(db, 'Kept the saved social content and resolved its source evidence; no provider change was made.')
                else:
                    self.log(db, 'Queued a provider action for separate verification; no provider change was made.')
                scheduled = workflow.next_scheduled_check(monitor_config_record(json.loads(db.execute('SELECT data FROM monitor_config WHERE id=1').fetchone()[0]))) if action != 'keep' else None
                response = {'action_request': self.present_action_request(candidate, True, scheduled,
                                                                           item['evidence_fingerprint'], item['precise_source'], False, self.identity_preflight(db, candidate)), 'created': True}
                if action == 'keep':
                    response['cancelled_request_ids'] = cancelled_ids
                return 201, response
            if re.fullmatch(r'/api/action-requests/[A-Za-z0-9_-]{1,100}', path) and method == 'PATCH':
                fields(body, {'status', 'receipt_url', 'receipt_note'}, {'status'})
                status = choice(body['status'], ('completed', 'cancelled'), 'action status')
                row = db.execute('SELECT data,confirmed FROM action_requests WHERE id=?', (path.rsplit('/', 1)[1],)).fetchone()
                if row is None:
                    raise LookupError('Record not found.')
                request = action_request_record(json.loads(row['data']))
                if request['status'] != 'pending':
                    raise Invalid('Completed and cancelled action requests are immutable.')
                changed_at = now()
                if status == 'cancelled':
                    if set(body) != {'status'}:
                        raise Invalid('Cancellation accepts only the cancelled status.')
                    if request['execution_state'] != 'queued' and not (request['execution_state'] == 'blocked' and request['attempt_id'] is None):
                        raise Invalid('A started or unknown attempt cannot be cancelled until its result is reconciled.')
                    request.update(status='cancelled', execution_state='cancelled', updated_at=changed_at)
                    self.put_action_request(db, action_request_record(durable_request(request)))
                    self.log(db, 'Cancelled a pending provider action; no provider change was made.')
                else:
                    fields(body, {'status', 'receipt_url', 'receipt_note'}, {'status', 'receipt_url', 'receipt_note'})
                    if request['action'] == 'keep':
                        raise Invalid('Keep decisions complete locally when they are created.')
                    if not row['confirmed']:
                        raise Invalid('Choose Keep, Edit, or Delete again after importing the review.')
                    if request['attempt_id'] is None or request['execution_state'] not in ('running', 'needs_verification', 'blocked'):
                        raise Invalid('Record a durable request start before saving a provider verification receipt.')
                    receipt_url = url(body['receipt_url'])
                    receipt_note = string(body['receipt_note'], 'Receipt note', 5000, True)
                    if not receipt_url:
                        raise Invalid('A receipt URL is required to verify completion.')
                    _, by_key, _, _, _ = self.source_snapshot(db)
                    item = by_key.get((request['account_id'], request['source_key']))
                    if item is None or not item['precise_source'] or request['evidence_fingerprint'] != item['evidence_fingerprint']:
                        raise Invalid('The saved source or evidence changed. Create a fresh action request from the current source item.')
                    request.update(status='completed', execution_state='verified', updated_at=changed_at, completed_at=changed_at,
                                   receipt_url=receipt_url, receipt_note=receipt_note)
                    self.put_action_request(db, action_request_record(durable_request(request)))
                    self.set_source_review_status(db, item, 'resolved', changed_at)
                    self.log(db, 'Recorded a separately verified provider action receipt and resolved its source evidence.')
                return 200, {'action_request': self.present_action_request(request, bool(row['confirmed']), identity=self.identity_preflight(db, request))}
            if path == '/api/accounts' and method == 'POST':
                fields(body, {'platform', 'handle', 'display_name', 'url', 'identity_mode', 'daring', 'sfw_only'}, {'platform', 'handle'})
                account = account_record(dict(id=uid(), platform=body['platform'], handle=body['handle'], display_name=body.get('display_name', ''), url=body.get('url', ''), access_checked_at=None, access_note='', created_at=now(), identity_mode=body.get('identity_mode', 'unclassified'), daring=body.get('daring', 'careful'), sfw_only=body.get('sfw_only', True)))
                self.put(db, 'accounts', account)
                self.log(db, 'Added an account.')
                return 201, {'account': account}
            if re.fullmatch(r'/api/accounts/[A-Za-z0-9_-]{1,100}', path) and method == 'PATCH':
                fields(body, {'handle', 'display_name', 'url', 'access_checked_at', 'access_note', 'identity_mode', 'daring', 'sfw_only'})
                account = account_record(self.get(db, 'accounts', path.rsplit('/', 1)[1]))
                self.materialize_flag_evidence(db)
                if 'handle' in body:
                    string(body['handle'], 'Handle', 200, True)
                material_handle_change = ('handle' in body and body['handle'].casefold() != account['handle'].casefold())
                if account['platform'] in ('Gmail', 'Outlook') and material_handle_change and db.execute('SELECT 1 FROM messages WHERE account_id=? LIMIT 1', (account['id'],)).fetchone():
                    raise Invalid('Add a separate mailbox to keep its messages separate.')
                identity_changed = material_handle_change or any(key in body and body[key] != account[key] for key in ('display_name', 'url'))
                account.update(body)
                if account.get('identity_mode', 'unclassified') != 'alias' and 'sfw_only' not in body:
                    account['sfw_only'] = True
                account = account_record(account)
                self.put(db, 'accounts', account)
                if identity_changed:
                    self.invalidate_access(db, account['id'])
                self.log(db, 'Updated an account snapshot.')
                return 200, {'account': account}
            if path == '/api/reviews' and method == 'POST':
                fields(body, {'account_id', 'kind', 'title', 'excerpt', 'source_url', 'observed_at', 'sample_count', 'severity', 'finding', 'suggestion'}, {'account_id', 'kind', 'title', 'severity', 'finding'})
                review = review_record(dict(id=uid(), account_id=body['account_id'], kind=body['kind'], title=body['title'], excerpt=body.get('excerpt', ''), source_url=body.get('source_url', ''), observed_at=body.get('observed_at', now()), reviewed_at=now(), sample_count=body.get('sample_count', 1), severity=body['severity'], finding=body['finding'], suggestion=body.get('suggestion', ''), origin='manual', status='open', snoozed_until=None, updated_at=now()))
                if not db.execute('SELECT 1 FROM accounts WHERE id=?', (review['account_id'],)).fetchone():
                    raise Invalid('Choose an existing account.')
                self.put(db, 'reviews', review)
                self.log(db, 'Added review evidence.')
                return 201, {'review': review}
            if re.fullmatch(r'/api/reviews/[A-Za-z0-9_-]{1,100}', path) and method == 'PATCH':
                fields(body, {'status'}, {'status'})
                status = choice(body['status'], ('open', 'resolved', 'snoozed'), 'status')
                review = self.get(db, 'reviews', path.rsplit('/', 1)[1])
                _, _, by_review, requests, _ = self.source_snapshot(db)
                item = by_review[review['id']]
                active = [value for value in requests if value['account_id'] == item['account_id'] and
                          value['source_key'] == item['source_key'] and value['status'] == 'pending' and
                          value['execution_state'] in ('queued', 'running', 'needs_verification', 'blocked')]
                if status == 'resolved' and active:
                    raise Invalid('This source has mutation or verification work. Use Keep or open the current request.')
                changed_at = now()
                if status == 'resolved':
                    self.set_source_review_status(db, item, 'resolved', changed_at)
                else:
                    wake = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(timespec='seconds').replace('+00:00', 'Z') if status == 'snoozed' else None
                    source = source_decision_record(dict(
                        id=uid(), account_id=item['account_id'], source_key=item['source_key'], evidence_fingerprint=item['evidence_fingerprint'],
                        status=status, snoozed_until=wake,
                        created_at=changed_at, updated_at=changed_at))
                    self.put_source_decision(db, source)
                    self.set_source_review_status(db, item, status, changed_at, wake)
                review.update(status=status, snoozed_until=source['snoozed_until'] if status == 'snoozed' else None, updated_at=changed_at)
                self.log(db, {'open': 'Reopened a review.', 'resolved': 'Marked a review handled.', 'snoozed': 'Snoozed a review for 24 hours.'}[status])
                return 200, {'review': review}
            if path == '/api/settings' and method == 'PATCH':
                settings_record(body, partial=True)
                settings = json.loads(db.execute('SELECT data FROM settings WHERE id=1').fetchone()[0])
                settings.update(body)
                db.execute('UPDATE settings SET data=? WHERE id=1', (json.dumps(settings),))
                return 200, {'settings': settings}
            if path == '/api/draft-check' and method == 'POST':
                fields(body, {'text', 'account_id'}, {'text'})
                result = draft_check(body['text'])
                account = {'identity_mode': 'unclassified', 'daring': 'careful', 'sfw_only': True}
                if 'account_id' in body:
                    identifier(body['account_id'])
                    account = account_record(self.get(db, 'accounts', body['account_id']))
                result['signals'] = policy.check(body['text'], account)
                result['signals'].sort(key=lambda item: (item['start'], item['end']))
                result['summary'] = 'These limited local checks found no flags for this account’s boundaries. This is not an SFW certification or a prediction of audience response.'
                if result['signals']:
                    result['summary'] = 'Local writing and account-policy checks found wording to consider. Context and intent still matter.'
                return 200, result
            if path == '/api/import' and method == 'POST':
                return 200, self.import_backup(db, body)
            raise LookupError('Endpoint not found.')

    def import_backup(self, db, body):
        if samples.has_sample_data(body):
            raise Invalid('Sample records cannot be imported with your own data.')
        self.materialize_flag_evidence(db)
        fields(body, {'schema_version', 'settings', 'accounts', 'reviews', 'policy_decisions', 'flag_evidence', 'action_requests', 'attestations', 'source_decisions', 'monitoring', 'communication'}, {'schema_version', 'settings', 'accounts', 'reviews'})
        if type(body['schema_version']) is not int or body['schema_version'] not in (1, 2):
            raise Invalid('Unsupported backup version.')
        settings_record(body['settings'])
        if type(body['accounts']) is not list or type(body['reviews']) is not list or len(body['accounts']) > 10000 or len(body['reviews']) > 10000:
            raise Invalid('Backup accounts and reviews must be lists of at most 10000 records.')
        account_ids, review_ids = set(), set()
        normalized_accounts = [account_record(account) for account in body['accounts']]
        for account in normalized_accounts:
            if account['id'] in account_ids:
                raise Invalid('Duplicate account identifiers in backup.')
            account_ids.add(account['id'])
        existing_accounts = {record['id']: account_record(record) for record in
                             (json.loads(row[0]) for row in db.execute('SELECT data FROM accounts'))}
        existing_ids = set(existing_accounts)
        resulting_accounts = dict(existing_accounts)
        resulting_accounts.update((account['id'], account) for account in normalized_accounts)
        account_platforms = {account_id: account['platform'] for account_id, account in resulting_accounts.items()}
        accounts_with_mail = {row[0] for row in db.execute('SELECT DISTINCT account_id FROM messages')}
        for account in normalized_accounts:
            previous = existing_accounts.get(account['id'])
            if (previous is not None and account['id'] in accounts_with_mail and
                    (account['platform'] != previous['platform'] or
                     account['handle'].casefold() != previous['handle'].casefold())):
                raise Invalid('Add a separate mailbox to keep its messages separate.')
        changed_identities = {account['id'] for account in normalized_accounts
                              if account['id'] in existing_accounts and
                              (account['platform'] != existing_accounts[account['id']]['platform'] or
                               account['handle'].casefold() != existing_accounts[account['id']]['handle'].casefold() or
                               any(account[key] != existing_accounts[account['id']][key]
                                   for key in ('display_name', 'url')))}
        existing_reviews = {review['id']: review_record(review) for review in
                            (json.loads(row[0]) for row in db.execute('SELECT data FROM reviews'))}
        normalized_reviews = []
        for value in body['reviews']:
            review = dict(review_record(value))
            if review['id'] in review_ids:
                raise Invalid('Duplicate review identifiers in backup.')
            if review['account_id'] not in account_ids | existing_ids:
                raise Invalid('Backup review references an unknown account.')
            if review['id'] in existing_reviews:
                mutable = {'status', 'snoozed_until', 'updated_at'}
                if any(review[key] != existing_reviews[review['id']][key] for key in REVIEW_FIELDS - mutable):
                    raise Invalid('Existing review evidence is immutable; add a new review for changed evidence.')
                previous = existing_reviews[review['id']]
                if 'source_url_correction' in review and any(review.get(key) != previous.get(key) for key in REVIEW_CORRECTION_FIELDS):
                    raise Invalid('A different source correction already uses this review ID. Add an exact link from the source card.')
                if 'source_url_correction' in previous:
                    review.update((key, previous[key]) for key in REVIEW_CORRECTION_FIELDS)
            validate_source_correction(resulting_accounts[review['account_id']], review)
            normalized_reviews.append(review)
            review_ids.add(review['id'])
        decisions = body.get('policy_decisions', [])
        if type(decisions) is not list or len(decisions) > 10000:
            raise Invalid('Backup boundary decisions must be a list of at most 10000 records.')
        decision_ids = set()
        for decision in decisions:
            decision_record(decision)
            if decision['id'] in decision_ids:
                raise Invalid('Duplicate boundary flag identifiers in backup.')
            decision_ids.add(decision['id'])
        incoming_requests = body.get('action_requests', [])
        if type(incoming_requests) is not list or len(incoming_requests) > 10000:
            raise Invalid('Backup action requests must be a list of at most 10000 records.')
        existing_requests = {request['id']: action_request_record(request) for request in
                             (json.loads(row[0]) for row in db.execute('SELECT data FROM action_requests'))}
        request_ids = set()
        merged_requests = dict(existing_requests)
        normalized_requests, legacy_request_ids = [], set()
        for value in incoming_requests:
            if type(value) is dict and 'source_key' not in value:
                legacy_request_ids.add(value.get('id'))
            request = action_request_record(value)
            if request['id'] in request_ids:
                raise Invalid('Duplicate action request identifiers in backup.')
            request_ids.add(request['id'])
            if request['id'] in existing_requests and canonical_json(request) != canonical_json(existing_requests[request['id']]):
                raise Invalid('A different action request already uses this request ID.')
            if request['id'] not in existing_requests and request['status'] == 'pending' and request['execution_state'] == 'running':
                request.update(execution_state='needs_verification')
            merged_requests[request['id']] = request
            normalized_requests.append(request)
        resulting_reviews = dict(existing_reviews)
        resulting_reviews.update((review['id'], review) for review in normalized_reviews)
        incoming_flag_evidence = body.get('flag_evidence', [])
        if type(incoming_flag_evidence) is not list or len(incoming_flag_evidence) > 100000:
            raise Invalid('Backup flag evidence must be a list of at most 100000 records.')
        existing_flag_evidence = {value['id']: flag_evidence_record(value) for value in
                                  (json.loads(row[0]) for row in db.execute('SELECT data FROM flag_evidence'))}
        flag_evidence_ids, normalized_flag_evidence = set(), []
        for value in incoming_flag_evidence:
            record = flag_evidence_record(value)
            if record['id'] in flag_evidence_ids:
                raise Invalid('Duplicate durable flag evidence identifiers in backup.')
            flag_evidence_ids.add(record['id'])
            review = resulting_reviews.get(record['review_id'])
            if review is None or review['account_id'] != record['account_id']:
                raise Invalid('Durable flag evidence references an unknown review or wrong account.')
            if (record['excerpt'] != review['excerpt'] or
                    workflow.normalize_source(record['source_url']) != workflow.normalize_source(review['source_url'])):
                raise Invalid('Durable flag evidence snapshot does not match its immutable review evidence.')
            if record['id'] in existing_flag_evidence and canonical_json(record) != canonical_json(existing_flag_evidence[record['id']]):
                raise Invalid('Different durable flag evidence already uses this identifier.')
            normalized_flag_evidence.append(record)
        for request in merged_requests.values():
            review = resulting_reviews.get(request['review_id'])
            if review is None or request['account_id'] not in account_ids | existing_ids:
                raise Invalid('Action request references an unknown review or account.')
            if not self.request_matches_review(request, review, historical=request['status'] != 'pending'):
                raise Invalid('Action request snapshot does not match its saved review evidence.')
            if request['id'] in {value['id'] for value in normalized_requests}:
                expected_sources = self.snapshot_source_keys(resulting_accounts[request['account_id']], review,
                                                             request['source_url'], historical=request['status'] != 'pending')
                if request['id'] in legacy_request_ids and len(expected_sources) == 1:
                    request['source_key'] = next(iter(expected_sources))
                elif request['source_key'] not in expected_sources:
                    raise Invalid('Action request source identity does not match its saved review.')
        incoming_attestations = body.get('attestations', [])
        if type(incoming_attestations) is not list or len(incoming_attestations) > 10000:
            raise Invalid('Backup attestations must be a list of at most 10000 records.')
        existing_attestations = {value['id']: attestation_record(value) for value in
                                 (json.loads(row[0]) for row in db.execute('SELECT data FROM attestations'))}
        attestation_ids, normalized_attestations = set(), []
        for value in incoming_attestations:
            record = attestation_record(value)
            if record['id'] in attestation_ids:
                raise Invalid('Duplicate attestation identifiers in backup.')
            attestation_ids.add(record['id'])
            if record['id'] in existing_attestations and canonical_json(record) != canonical_json(existing_attestations[record['id']]):
                raise Invalid('A different attestation already uses this identifier.')
            review = resulting_reviews.get(record['review_id'])
            if review is None or review['account_id'] != record['account_id']:
                raise Invalid('Attestation references an unknown review or wrong account.')
            historical = record['execution_state'] in ('verified', 'reopened')
            if not self.request_matches_review(record, review, historical=historical):
                raise Invalid('Attestation snapshot does not match its immutable review evidence.')
            if record['source_key'] not in self.snapshot_source_keys(resulting_accounts[record['account_id']], review, record['source_url'], historical=historical):
                raise Invalid('Attestation source identity does not match its saved review.')
            normalized_attestations.append(record)
        incoming_source_decisions = body.get('source_decisions', [])
        if type(incoming_source_decisions) is not list or len(incoming_source_decisions) > 10000:
            raise Invalid('Backup source decisions must be a list of at most 10000 records.')
        existing_source_decisions = {value['id']: source_decision_record(value) for value in
                                     (json.loads(row[0]) for row in db.execute('SELECT data FROM source_decisions'))}
        source_decision_ids, normalized_source_decisions = set(), []
        for value in incoming_source_decisions:
            record = source_decision_record(value)
            if record['id'] in source_decision_ids:
                raise Invalid('Duplicate source decision identifiers in backup.')
            source_decision_ids.add(record['id'])
            if record['account_id'] not in account_ids | existing_ids:
                raise Invalid('Source decision references an unknown account.')
            if record['id'] in existing_source_decisions and canonical_json(record) != canonical_json(existing_source_decisions[record['id']]):
                raise Invalid('A different source decision already uses this identifier.')
            normalized_source_decisions.append(record)
        monitoring = body.get('monitoring')
        if 'monitoring' in body:
            fields(monitoring, {'config', 'runs'}, {'config', 'runs'})
            monitoring = dict(monitoring)
            monitoring['config'] = monitor_config_record(monitoring['config'])
            if type(monitoring['runs']) is not list or len(monitoring['runs']) > 10000:
                raise Invalid('Backup monitoring runs must be a list of at most 10000 reports.')
            run_ids = set()
            for run in monitoring['runs']:
                monitor_run_record(run, account_ids | existing_ids)
                if run['id'] in run_ids:
                    raise Invalid('Duplicate monitoring run identifiers in backup.')
                run_ids.add(run['id'])
                self.monitor_conflict(db, run)
        communication = None
        if 'communication' in body:
            communication = self.validate_communication_import(db, body['communication'], account_platforms)
        # All validation completes before the first write; the context commits once.
        for account in normalized_accounts:
            self.put(db, 'accounts', account)
        for review in normalized_reviews:
            self.put(db, 'reviews', review)
        for decision in decisions:
            db.execute('INSERT INTO policy_decisions VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data', (decision['id'], json.dumps(decision)))
        for record in normalized_flag_evidence:
            db.execute('INSERT OR IGNORE INTO flag_evidence(id,review_id,account_id,data) VALUES(?,?,?,?)',
                       (record['id'], record['review_id'], record['account_id'], canonical_json(record)))
        for request in normalized_requests:
            db.execute('DELETE FROM action_identity_checks WHERE request_id=?', (request['id'],))
            self.put_action_request(db, request, confirmed=False if request['status'] == 'pending' and request['id'] not in existing_requests else None)
        for record in normalized_attestations:
            self.put_attestation(db, record)
        for record in normalized_source_decisions:
            self.put_source_decision(db, record)
        if monitoring is not None:
            db.execute('UPDATE monitor_config SET data=? WHERE id=1', (canonical_json(monitoring['config']),))
            for run in monitoring['runs']:
                self.put_monitor_run(db, run)
            self.materialize_saved_report_reviews(db, monitoring['runs'])
        if communication is not None:
            self.apply_communication_import(db, communication)
        for account_id in changed_identities:
            self.invalidate_access(db, account_id)
        db.execute('UPDATE settings SET data=? WHERE id=1', (json.dumps(body['settings']),))
        self.log(db, 'Imported a Resonance backup.')
        return {'accounts': len(account_ids), 'reviews': len(review_ids)}


def markdown_brief(state):
    def escape(value):
        return re.sub(r'([\\`*_{}\[\]<>()#!|])', r'\\\1', value).replace('\r', '')
    accounts = {a['id']: a for a in state['accounts']}
    reviews = [r for r in state['reviews'] if r['status'] == 'open']
    lines = ['# Resonance', '', f'Prepared: {now()}', '', 'Scope: Manually recorded evidence and imported Resonance records only. This is wording feedback, not measured audience sentiment or a reputation score. Account access dates are verification snapshots, not live integrations. No social content is changed.', '', f'{len(accounts)} accounts · {len(reviews)} open review records', '']
    flags = [f for f in state.get('policy_flags', []) if f['status'] == 'open']
    lines += ['## Account boundaries', '', 'Named accounts must remain SFW regardless of daring. Local rules cover saved text excerpts only, not images, video or a whole account history.', '']
    for a in accounts.values():
        lines += [f"- {escape(a['platform'])} · {escape(a['handle'])}: {a['identity_mode']}, {a['daring']}, {'SFW required' if a['sfw_only'] else 'SFW relaxed for alias'}"]
    lines += ['', f'## Boundary flags awaiting review ({len(flags)})', '']
    for flag in flags:
        a = accounts[flag['account_id']]
        lines += [f"### {escape(flag['title'])}", '', f"{escape(a['platform'])} · {escape(a['handle'])}", '', escape(flag['detail']), '', 'Excerpt: ' + escape(flag['excerpt']), '', 'Source: ' + (escape(flag['source_url']) if flag['source_url'] else 'No source URL recorded.'), '']
    if not reviews:
        lines += ['No open review evidence. This does not establish that unreviewed content is clear.', '']
    for r in reviews:
        a = accounts[r['account_id']]
        lines += [f"## {escape(r['title'])}", '', f"{escape(a['platform'])} · {escape(a['handle'])} · {r['severity']} · {r['kind']}", '', f"Observed: {r['observed_at']} · Sample count: {r['sample_count']}", '', escape(r['finding']), '']
        if r['excerpt']:
            lines += ['Evidence: ' + escape(r['excerpt']), '']
        if r['suggestion']:
            lines += ['Suggested next step: ' + escape(r['suggestion']), '']
        lines += ['Source: ' + (escape(r['source_url']) if r['source_url'] else 'No source URL recorded.'), '']
    return '\n'.join(lines)


def strict_json(raw):
    def unique(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise Invalid('Duplicate JSON fields are not allowed.')
            obj[key] = value
        return obj
    def no_constant(value):
        raise Invalid('JSON numbers must be finite.')
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=unique, parse_constant=no_constant)
    except (UnicodeError, ValueError, RecursionError):
        raise Invalid('Invalid JSON document.') from None


class Handler(BaseHTTPRequestHandler):
    server_version = 'Resonance'
    sys_version = ''

    def log_message(self, *args):
        pass  # Never log request paths, headers, bodies, or user content.

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def send_content(self, code, value, content_type='application/json', download=None):
        body = json.dumps(value, ensure_ascii=False).encode('utf-8') if content_type == 'application/json' else value if isinstance(value, bytes) else value.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', content_type + '; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store' if self.path.startswith('/api/') else 'no-cache')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
        self.send_header('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
        if download:
            self.send_header('Content-Disposition', 'attachment; filename="' + download + '"')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def protected(self, mutation=False):
        hosts = self.headers.get_all('Host', [])
        port = self.server.server_address[1]
        allowed = {f'127.0.0.1:{port}', f'localhost:{port}'}
        if port == 80:
            allowed |= {'127.0.0.1', 'localhost'}
        if len(hosts) != 1 or hosts[0] not in allowed:
            self.send_content(403, {'error': 'This local app requires its loopback address and port.'})
            return False
        if mutation:
            origins = self.headers.get_all('Origin', [])
            if origins != ['http://' + hosts[0]] or self.headers.get('Sec-Fetch-Site') not in (None, 'same-origin'):
                self.send_content(403, {'error': 'Changes must come from the Resonance page on this local server.'})
                return False
        return True

    def workspace_token(self):
        headers = self.headers.get_all('X-Resonance-Workspace', [])
        query = parse_qs(urlsplit(self.path).query).get('workspace', [])
        if len(headers) > 1 or len(query) > 1 or headers and query and headers[0] != query[0]:
            raise Invalid('Use the workspace shown on this page; refresh before trying again.')
        return (headers or query or [None])[0]

    def do_GET(self):
        if not self.protected():
            return
        path = urlsplit(self.path).path
        try:
            if path == '/api/state':
                self.send_content(200, self.server.store.state())
            elif path == '/api/export':
                self.send_content(200, self.server.store.backup(self.workspace_token()), download='resonance-backup.json')
            elif path == '/api/data-folder':
                self.send_content(200, self.server.store.data_folder_details())
            elif path == '/api/assistant':
                self.send_content(200, {'prompt': f'Read ASSISTANT.md in {ROOT} and run my Resonance check.'})
            elif path == '/api/brief':
                self.send_content(200, self.server.store.brief(self.workspace_token()), 'text/markdown', 'resonance-brief.md')
            elif path in STATIC:
                filename, mime = STATIC[path]
                file = ROOT / 'static' / filename
                if file.is_symlink() or not file.is_file():
                    raise LookupError()
                self.send_content(200, file.read_bytes(), mime)
            else:
                raise LookupError()
        except LookupError:
            self.send_content(404, {'error': 'Not found.'})
        except (Invalid, samples.SampleError) as error:
            self.send_content(400, {'error': str(error)})
        except (sqlite3.Error, OSError):
            self.send_content(500, {'error': 'Local data could not be read. Check the data folder and try again.'})

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        self.mutation()

    def do_PATCH(self):
        self.mutation()

    def mutation(self):
        self.close_connection = True
        if not self.protected(mutation=True):
            return
        if self.headers.get_content_type() != 'application/json':
            self.send_content(415, {'error': 'Send JSON with Content-Type application/json.'})
            return
        lengths = self.headers.get_all('Content-Length', [])
        if self.headers.get('Transfer-Encoding') or len(lengths) != 1 or not re.fullmatch(r'\d+', lengths[0]):
            self.send_content(400, {'error': 'A single valid Content-Length is required.'})
            return
        is_import = urlsplit(self.path).path == '/api/import'
        limit = MAX_IMPORT_BODY if is_import else MAX_BODY
        if len(lengths[0]) > 10 or int(lengths[0]) > limit:
            self.send_content(413, {'error': 'This request exceeds the ' + ('32' if is_import else '2') + ' MB limit.'})
            return
        length = int(lengths[0])
        try:
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise Invalid('The request body is incomplete.')
            body = strict_json(raw)
            path = urlsplit(self.path).path
            if path == '/api/data-folder/open' and self.command == 'POST':
                fields(body, set())
                code, response = 200, self.server.store.open_data_folder(self.workspace_token())
            else:
                code, response = self.server.store.mutate(self.command, path, body, self.workspace_token())
            self.send_content(code, response)
        except (Invalid, samples.SampleError) as error:
            self.send_content(400, {'error': str(error)})
        except LookupError:
            self.send_content(404, {'error': 'Record or endpoint not found.'})
        except (sqlite3.Error, OSError):
            self.send_content(500, {'error': 'Your change could not be saved. Check local storage and try again.'})


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    # Windows lets a second listener share an address under SO_REUSEADDR, so an
    # occupied port would not refuse there; hold the address exclusively instead.
    exclusive_option = getattr(socket, 'SO_EXCLUSIVEADDRUSE', None)
    allow_reuse_address = exclusive_option is None

    def server_bind(self):
        if self.exclusive_option is not None:
            self.socket.setsockopt(socket.SOL_SOCKET, self.exclusive_option, 1)
        # HTTPServer resolves a display hostname here. A loopback-only app needs
        # no reverse DNS lookup; it can stall startup on an offline resolver.
        TCPServer.server_bind(self)
        self.server_name = '127.0.0.1'
        self.server_port = self.server_address[1]


def make_server(directory=None, port=4317):
    server = LocalServer(('127.0.0.1', port), Handler)
    try:
        # Resolve and upgrade data only after owning this app's listening address.
        server.store = samples.Workspace(directory if directory is not None else data_directory(), Store)
    except Exception:
        server.server_close()
        raise
    return server


def main():
    parser = argparse.ArgumentParser(description='Resonance — private local edition. Listens only on this computer.')
    parser.add_argument('--port', type=int, default=4317)
    parser.add_argument('--open', action='store_true', help='Open the app in your default browser')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('Port must be between 1 and 65535.')
    try:
        server = make_server(port=args.port)
    except (OSError, sqlite3.Error, Invalid, samples.SampleError) as error:
        print(f'Resonance could not start: {error}', file=sys.stderr)
        return 1
    address = f'http://127.0.0.1:{args.port}'
    print(f'Resonance is ready: {address}\nData stays in: {server.store.directory}\nKeep this window open. Press Ctrl+C to stop.', flush=True)
    if args.open:
        threading.Timer(0.3, lambda: webbrowser.open(address)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nResonance stopped. Your saved data is kept.')
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
