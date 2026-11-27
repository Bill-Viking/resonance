"""Replay the photo-sharing workflow against a source checkout, using synthetic provider events."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


PROFILES = {
    'Instagram': 'https://instagram.com/synthetic_studio',
    'Facebook': 'https://facebook.com/synthetic_studio',
}
AUDIENCES = {'Instagram': 'Followers', 'Facebook': 'Friends'}
LINKS = {
    'Instagram': 'https://instagram.com/p/zSynthetic/',
    'Facebook': 'https://facebook.com/synthetic_studio/posts/203',
}
CAPTION = 'Fog over the paper garden.'
GIF = bytes.fromhex('47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b')


def measure(source, workflow, case, folder):
    sys.path.insert(0, str(source))
    from server import Store

    workspace = folder / 'workspace'
    Store(workspace)
    photo = folder / 'paper.gif'
    photo.write_bytes(GIF)
    plan = folder / 'plan.json'
    evidence = folder / 'observation.json'
    counts = Counter()
    commands = []
    receipts = {}

    def run(script, *args):
        command = [sys.executable, '-B', str(source / script), '--data-dir', str(workspace), *args]
        output = subprocess.check_output(command, cwd=source, env={**os.environ, 'RESONANCE_DATA_DIR': str(workspace)})
        commands.append(args[0])
        return json.loads(output)

    def write_plan(destinations):
        entries = {}
        for platform in destinations:
            entry = {'profile': PROFILES[platform], 'caption': CAPTION}
            if workflow == 'revised':
                entry['audience'] = AUDIENCES[platform]
            entries[platform] = entry
        plan.write_text(json.dumps(entries), encoding='utf-8')

    # Replay the old instruction's omission. A separate, newly requested
    # Facebook post avoids editing a private record on a helper without revise.
    separate = workflow == 'baseline' and case == 'ordinary'
    start = time.perf_counter()
    for primary in (['Instagram', 'Facebook'] if separate else ['Instagram']):
        linked = primary == 'Instagram' and not separate
        job = 'garden' if primary == 'Instagram' else 'additional-facebook'
        if primary == 'Facebook':
            counts['follow_up_requests'] += 1
        run('monitor.py', '--profile')
        counts['setup_passes'] += 1
        destinations = [primary, 'Facebook'] if linked else [primary]
        discovered = workflow == 'revised' and case == 'ordinary'
        write_plan([primary] if discovered else destinations)
        state = run('image_post.py', 'start', job, '--image', str(photo), '--plan', str(plan))

        def act(operation, *args):
            nonlocal state
            state = run('image_post.py', operation, job, '--version', str(state['version']), *args)

        image_hash = state['selected'].split('.')[0]
        composer = [primary, '--profile', PROFILES[primary], '--sha256', image_hash]
        act('attach', *composer)
        assert Path(state['selected_path']).read_bytes() == photo.read_bytes()
        counts['uploads'] += 1
        if discovered:
            write_plan(['Facebook'])
            act('revise', '--plan', str(plan))
        if separate and primary == 'Instagram':
            counts['switch_changes'] += 1
        counts['preview_approval_prompts'] += 1
        flags = []
        if workflow == 'baseline':
            act('preview', *composer)
        else:
            flags.extend(['--confirm-preview', '--audience', AUDIENCES[primary]])
        if linked:
            flags.extend(['--facebook-profile', PROFILES['Facebook']])
            if workflow == 'revised':
                flags.extend(['--facebook-audience', AUDIENCES['Facebook']])
        elif primary == 'Instagram':
            flags.append('--facebook-off')
        act('submit', *composer, *flags)
        counts['publish_clicks'] += 1
        for platform in destinations:
            assert state['destinations'][platform]['submissions'] == 1
            evidence.write_text(json.dumps({
                'profile': PROFILES[platform], 'checked_at': datetime.now(timezone.utc).isoformat(),
                'result': 'published', 'detail': 'Matched the simulated destination post.',
                'author_profile': PROFILES[platform], 'permalink': LINKS[platform],
                'caption': CAPTION, 'matched_image_sha256': image_hash,
            }), encoding='utf-8')
            act('observe', platform, '--evidence', str(evidence))
            receipts[platform] = state['destinations'][platform]['permalink']
    assert receipts == LINKS
    return {'local_seconds': round(time.perf_counter() - start, 3), 'cli_calls': len(commands),
            'simulated': dict(counts), 'receipts': len(receipts), 'commands': commands}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workflow', choices=['baseline', 'revised'])
    parser.add_argument('case', choices=['ordinary', 'planned'])
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1],
                        help='Checkout containing the helper version to measure')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        print(json.dumps(measure(args.source.resolve(), args.workflow, args.case, Path(tmp).resolve())))
