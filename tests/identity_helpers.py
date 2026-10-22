"""Synthetic identity observations shared by workflow fixtures; no provider access."""
import server


def observation(account, request, **changes):
    profile = account['url'] or {'X': 'https://x.com/', 'YouTube': 'https://www.youtube.com/@'}[account['platform']] + account['handle'].lstrip('@')
    value = {key: request[key] for key in ('account_id', 'source_key', 'evidence_fingerprint')}
    value.update(checked_at=server.now(), status='signed_in', platform=account['platform'],
                 handle=account['handle'], profile_url=profile, author_handle=account['handle'],
                 author_profile_url=profile, source_url=request['source_url'],
                 detail='Synthetic account switcher and source author inspected in a temporary fixture.')
    value.update(changes)
    return value


def start_checked(store, request_id):
    state = store.state()
    request = next(value for value in state['action_requests'] if value['id'] == request_id)
    account = next(value for value in state['accounts'] if value['id'] == request['account_id'])
    checked = store.mutate('POST', '/api/action-requests/' + request_id + '/identity-check', observation(account, request))[1]['action_request']
    return store.mutate('POST', '/api/action-requests/' + request_id + '/start', {'identity_check_id': checked['identity_check']['id']})
