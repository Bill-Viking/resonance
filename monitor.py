#!/usr/bin/env python3
"""Save externally produced check reports. No scheduling or provider access occurs here."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
import samples

from server import (Invalid, MAX_BODY, Store, data_directory, identifier, safe_data_directory,
                    strict_json, validate_monitor_document)


def read_document(path, private=True):
    source = Path(path).expanduser().resolve()
    if private:
        safe_data_directory(source.parent)
    with source.open('rb') as file:
        raw = file.read(MAX_BODY + 1)
    if len(raw) > MAX_BODY:
        raise Invalid('The document exceeds the 2 MB limit.')
    document = strict_json(raw)
    if samples.has_sample_data(document):
        raise Invalid('Sample records cannot be used by the command-line tool.')
    return document


def main(argv=None):
    parser = argparse.ArgumentParser(description='Save local Resonance reports and schedule metadata. This command does not check accounts, schedule work, or perform provider actions.')
    parser.add_argument('--data-dir', type=Path, help='Private local app-data directory; defaults to the app’s data directory')
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument('--report', type=Path, help='Insert one complete monitoring report from a private JSON file')
    operation.add_argument('--config', type=Path, help='Update monitoring schedule metadata from a private JSON file')
    operation.add_argument('--status', action='store_true', help='Print saved schedule metadata and the latest report summary')
    operation.add_argument('--work', action='store_true', help='Print safe queued work and verification-only reconciliation work')
    operation.add_argument('--validate', type=Path, help='Check a report or learned-list JSON file by shape only, without opening the data store')
    operation.add_argument('--request', metavar='ID', help='Read one request and its current evidence without changing the data store')
    operation.add_argument('--check-identity', metavar='ID', help='Record an assistant observation of active account and source author for one queued request')
    operation.add_argument('--start-request', metavar='ID', help='Durably start one safe queued request before any provider action')
    operation.add_argument('--profile', action='store_true', help='Read saved account boundaries, brand and past social choices without changing data')
    operation.add_argument('--review-context', action='store_true', help='Read one social account’s saved coverage, exact sources and work history without changing data')
    operation.add_argument('--res-intake', type=Path, metavar='FILE', help='Save assistant-read public comments addressed to RES as local review requests from a private JSON file; nothing is run or published')
    operation.add_argument('--res-inbox', action='store_true', help='Read saved RES-addressed requests without changing data')
    operation.add_argument('--res-update', type=Path, metavar='FILE', help='Record a local evaluation, proposal or state change for one saved RES request from a private JSON file')
    parser.add_argument('--account', metavar='ID', help='Select one account for --profile, --review-context or --res-inbox')
    parser.add_argument('--state', choices=('pending', 'needs_review', 'prepared', 'done', 'dismissed', 'all'), default=None, help='Requests to list with --res-inbox (default: pending, meaning needs_review and prepared)')
    parser.add_argument('--observation', type=Path, help='Private identity observation JSON; required with --check-identity')
    parser.add_argument('--identity-check-id', metavar='ID', help='Current matching check ID; required with --start-request')
    args = parser.parse_args(argv)
    if bool(args.check_identity) != bool(args.observation):
        parser.error('--check-identity requires --observation FILE; --observation is only for that command')
    if bool(args.start_request) != bool(args.identity_check_id):
        parser.error('--start-request requires --identity-check-id ID; that flag is only for a start')
    if args.account is not None and not (args.profile or args.review_context or args.res_inbox):
        parser.error('--account is available only with --profile, --review-context or --res-inbox')
    if args.state is not None and not args.res_inbox:
        parser.error('--state is available only with --res-inbox')
    if args.review_context and not args.account:
        parser.error('--review-context requires --account ID')
    try:
        if args.validate:
            print(json.dumps(validate_monitor_document(read_document(args.validate, private=False)), ensure_ascii=False))
            return 0
        source_file = args.report or args.config or args.observation or args.res_intake or args.res_update
        document = read_document(source_file) if source_file else None
        directory = safe_data_directory(args.data_dir) if args.data_dir is not None else data_directory()
        read_only = args.request is not None or args.profile or args.review_context or args.res_inbox
        guard = samples.read_only_real_workspace if read_only else samples.real_workspace
        with guard(directory):
            store = Store(directory, read_only=read_only)
            if args.res_inbox:
                result_json = json.dumps(store.inbox_snapshot(args.account, args.state or 'pending'), ensure_ascii=False)
            elif args.res_intake:
                _, result = store.mutate('POST', '/api/request-inbox/intake', document)
                result_json = json.dumps(result, ensure_ascii=False)
            elif args.res_update:
                if type(document) is not dict:
                    raise Invalid('Expected a JSON object.')
                _, result = store.mutate('PATCH', '/api/request-inbox/' + identifier(document.get('id')), {key: value for key, value in document.items() if key != 'id'})
                result_json = json.dumps(result, ensure_ascii=False)
            elif args.review_context:
                result_json = json.dumps(store.review_context(args.account), ensure_ascii=False)
            elif args.profile:
                result_json = json.dumps(store.profile_snapshot(args.account), ensure_ascii=False)
            elif args.report:
                _, result = store.mutate('POST', '/api/monitor-runs', document)
                result_json = json.dumps({'id': result['run']['id'], 'created': result['created'], 'accounts': len(result['run']['accounts']), 'summary': result['run']['summary']}, ensure_ascii=False)
            elif args.config:
                _, result = store.mutate('PATCH', '/api/monitoring', document)
                result_json = json.dumps(result, ensure_ascii=False)
            elif args.work:
                result_json = json.dumps(store.work(), ensure_ascii=False)
            elif args.check_identity:
                _, result = store.mutate('POST', '/api/action-requests/' + args.check_identity + '/identity-check', document)
                result_json = json.dumps(result, ensure_ascii=False)
            elif args.start_request:
                _, result = store.mutate('POST', '/api/action-requests/' + args.start_request + '/start', {'identity_check_id': args.identity_check_id})
                result_json = json.dumps(result, ensure_ascii=False)
            elif args.request:
                result_json = json.dumps(store.request_snapshot(args.request), ensure_ascii=False)
            else:
                with store.connect() as db:
                    monitoring = store.monitoring(db, limit=1)
                    total = db.execute('SELECT count(*) FROM monitor_runs').fetchone()[0]
                latest = monitoring['runs'][0] if monitoring['runs'] else None
                result_json = json.dumps({'config': monitoring['config'], 'run_count': total,
                                  'latest': {key: latest[key] for key in ('id', 'started_at', 'completed_at', 'summary')} if latest else None,
                                  'outcomes': ({key: sum(1 for value in latest.get('requests', []) if value['outcome'] == key)
                                                for key in ('verified', 'blocked', 'reopened', 'unknown')} if latest else None),
                                  'scope_note': 'Saved browser-assisted reports only. This app does not run a scheduler or access social accounts.'}, ensure_ascii=False)
        print(result_json)
        return 0
    except (Invalid, samples.SampleError, LookupError, OSError, sqlite3.Error) as error:
        print('Resonance monitoring: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
