"""Keep private recovery state for an assistant carrying out an authorized image post."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time
from urllib.parse import parse_qs, urlsplit

if __name__ == '__main__':
    sys.dont_write_bytecode = True  # Keep the verified desktop assistant kit intact.

import samples
import workflow
from server import Invalid, data_directory, safe_data_directory, strict_json, timestamp, url


PLATFORMS = ('Instagram', 'X', 'Facebook')
IMAGE_LIMIT = 64 * 1024 * 1024
RECORD_LIMIT = 1024 * 1024
OBSERVATION_LIFETIME = 600
IMAGE_NAME = re.compile(r'[0-9a-f]{64}\.(?:jpg|png|gif|webp|heic|heif|avif|tif|bmp)')


def read_file(path, limit):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 1024
            or info.st_size > limit):
        raise Invalid('Expected a regular file within the size limit.')
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise Invalid('The file exceeds the size limit.')
    return data


def atomic_write(path, data):
    temp = tempfile.NamedTemporaryFile(dir=path.parent, delete=False)
    name = Path(temp.name)
    try:
        with temp:
            temp.write(data)
            temp.flush()
            os.fsync(temp.fileno())
        os.replace(name, path)
        if os.name == 'posix':
            fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        name.unlink(missing_ok=True)


def image_extension(raw):
    for signature, extension in ((b'\xff\xd8\xff', 'jpg'), (b'\x89PNG\r\n\x1a\n', 'png'),
                                 (b'GIF87a', 'gif'), (b'GIF89a', 'gif'), (b'II*\0', 'tif'),
                                 (b'MM\0*', 'tif'), (b'BM', 'bmp')):
        if raw.startswith(signature):
            return extension
    if raw[:4] == b'RIFF' and raw[8:12] == b'WEBP':
        return 'webp'
    if raw[4:8] == b'ftyp':
        for brands, extension in (((b'avif', b'avis'), 'avif'), ((b'heic', b'heix', b'hevc', b'hevx'), 'heic'),
                                  ((b'mif1', b'msf1'), 'heif')):
            if raw[8:12] in brands:
                return extension
    raise Invalid('Choose a supported image file (JPEG, PNG, GIF, WebP, HEIF, AVIF, TIFF or BMP).')


def save_image(folder, source):
    raw = read_file(source, IMAGE_LIMIT)
    name = hashlib.sha256(raw).hexdigest() + '.' + image_extension(raw)
    target = folder / name
    if target.exists():
        if read_file(target, IMAGE_LIMIT) != raw:
            raise Invalid('The saved image has changed. Inspect the private recovery folder.')
    else:
        atomic_write(target, raw)
    return name


def private_folder(path, create=False):
    if path.is_symlink() or path.resolve() != path:
        raise Invalid('Recovery folders must be private local directories without links.')
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    return path


def local_json(path):
    return strict_json(read_file(path, RECORD_LIMIT))


def profile(platform, value):
    url(value)
    if not isinstance(value, str) or not value.startswith('https://'):
        raise Invalid('Inspect the signed-in destination account and provide its HTTPS profile URL.')
    key = workflow.profile_key(platform, value)
    if key is None or key[2] in ('home', 'me', 'explore', 'settings', 'notifications', 'accounts', 'i'):
        raise Invalid('Use the exact account profile URL for this destination.')
    return key


def new_record(plan, now):
    if not isinstance(plan, dict) or not plan or not set(plan) <= set(PLATFORMS):
        raise Invalid('Choose an authorized Instagram, X or Facebook destination.')
    targets = {}
    for platform, item in plan.items():
        if not isinstance(item, dict) or set(item) != {'profile', 'caption'}:
            raise Invalid('Each destination needs its profile URL and exact caption.')
        profile(platform, item['profile'])
        if not isinstance(item['caption'], str) or len(item['caption']) > 20000:
            raise Invalid('Use a text caption of at most 20,000 characters.')
        targets[platform] = dict(item, phase='ready', preview=None, attempt_image=None, submissions=0,
                                 permalink=None, changed_at=now, observation=None)
    return dict(format=1, version=1, destinations=targets)


def require_phase(target, *allowed):
    if target['phase'] not in allowed:
        raise Invalid('This destination is ' + target['phase'] + '. Inspect its current state before another attempt.')


def checked_link(platform, target, link):
    url(link)
    parts = urlsplit(link)
    hosts = {'X': {'x.com', 'twitter.com'}, 'Instagram': {'instagram.com'},
             'Facebook': {'facebook.com', 'm.facebook.com'}}
    hostname = (parts.hostname or '').lower().removeprefix('www.')
    if parts.scheme != 'https' or hostname not in hosts[platform] or parts.port is not None or parts.fragment:
        raise Invalid('Open the exact published post on the requested platform.')
    query = parse_qs(parts.query, keep_blank_values=True)
    if set(query) & {'comment_id', 'reply_comment_id'}:
        raise Invalid('Use the post permalink, without comment selectors.')
    exact = workflow.precise_source(link, platform, 'post')
    if platform == 'Facebook' and parts.path in ('/photo', '/photo/', '/photo.php'):
        exact = len(query.get('fbid', [])) == 1 and query['fbid'][0].isdigit()
    author_url = target['profile']
    if platform == 'Facebook':
        key = profile(platform, author_url)
        if key[1] == 'id':
            author_url = 'https://facebook.com/' + key[2]
        if parts.path in ('/permalink.php', '/story.php'):
            exact = (len(query.get('story_fbid', [])) == 1 and bool(query['story_fbid'][0])
                     and len(query.get('id', [])) == 1 and query['id'][0].isdigit()
                     and (key[1] != 'id' or query['id'] == [key[2]]))
        if parts.path.startswith('/groups/'):
            exact = False
    if platform == 'X' and not parts.path.rstrip('/').rsplit('/', 1)[-1].isdigit():
        exact = False
    if not exact or not workflow.source_author_matches({'platform': platform, 'url': author_url}, link):
        raise Invalid('The observed link does not identify the expected account’s post.')
    return workflow.normalize_source(link)


def check_saved_image(folder, name):
    if (not isinstance(name, str) or not IMAGE_NAME.fullmatch(name)
            or hashlib.sha256(read_file(folder / name, IMAGE_LIMIT)).hexdigest() != name.split('.')[0]):
        raise Invalid('The saved image is missing or changed. Check the recovery folder before using it.')


def observe(record, platform, target, evidence, now, folder):
    if not isinstance(evidence, dict):
        raise Invalid('Supply a destination observation from the live browser.')
    if profile(platform, evidence.get('profile')) != profile(platform, target['profile']):
        raise Invalid('The observed account does not match this destination.')
    timestamp(evidence.get('checked_at'))
    checked = datetime.fromisoformat(evidence['checked_at'].replace('Z', '+00:00')).timestamp()
    if (checked < max(target['changed_at'], target.get('check_after', 0))
            or checked > now + 1 or now - checked > OBSERVATION_LIFETIME):
        raise Invalid('Inspect the current destination again and supply its fresh observation time.')
    outcome = evidence.get('result')
    common = {'profile', 'checked_at', 'result', 'detail'}
    allowed = {
        'published': {'author_profile', 'permalink', 'caption', 'matched_image_sha256'},
        'absent': {'composer', 'prior_action_settled', 'recent_posts_checked', 'matched_image_sha256', 'submission_failure'},
        'unknown': set(),
    }
    if outcome not in allowed:
        raise Invalid('Use the observed result published, absent or unknown.')
    if evidence.keys() - (common | allowed[outcome]):
        raise Invalid('Use only the evidence fields for this observed result.')
    note = evidence.get('detail')
    if not isinstance(note, str) or not note.strip() or len(note) > 1000:
        raise Invalid('Describe what the destination actually showed, in at most 1,000 characters.')
    if outcome == 'published':
        if profile(platform, evidence.get('author_profile')) != profile(platform, target['profile']):
            raise Invalid('The published author does not match this destination.')
        image = target['attempt_image'] or target['preview'] or record['selected']
        if image != record['selected']:
            check_saved_image(folder, image)
        if (evidence.get('matched_image_sha256') != image.split('.')[0]
                or evidence.get('caption') != target['caption']):
            raise Invalid('The observed image and caption must match the exact post.')
        link = checked_link(platform, target, evidence.get('permalink'))
        target.update(phase='verified', permalink=link, published_image=image)
    elif outcome == 'absent':
        if (evidence.get('recent_posts_checked') is not True or evidence.get('prior_action_settled') is not True
                or evidence.get('composer') not in ('empty', 'selected_image')):
            raise Invalid('Inspect the settled composer and recent posts for this account before retrying.')
        if target['attempt_image']:
            failure = evidence.get('submission_failure')
            if not isinstance(failure, str) or not failure.strip() or len(failure) > 1000:
                raise Invalid('Delivery is unresolved. Record unknown until the platform confirms the submission failed.')
        preview = None
        if evidence['composer'] == 'selected_image':
            if evidence.get('matched_image_sha256') != record['selected'].split('.')[0]:
                raise Invalid('The composer preview must match the latest selected image.')
            preview = record['selected']
        elif 'matched_image_sha256' in evidence:
            raise Invalid('An empty composer cannot have a matching image preview.')
        target.update(phase='attached' if preview else 'ready', preview=preview, attempt_image=None)
    else:
        target['phase'] = 'check_destination'
    target['observation'] = {'result': outcome, 'checked_at': evidence['checked_at'], 'detail': note}
    if outcome == 'absent' and evidence.get('submission_failure'):
        target['observation']['submission_failure'] = evidence['submission_failure']
    target['changed_at'] = now


def require_current_retry_check(target, now):
    previous = target['observation']
    if previous and previous['result'] == 'absent':
        checked = datetime.fromisoformat(previous['checked_at'].replace('Z', '+00:00')).timestamp()
        if now - checked > OBSERVATION_LIFETIME:
            raise Invalid('The retry check expired. Inspect the live destination and composer again.')


def update(record, args, folder, now):
    targets = record['destinations']
    if args.operation == 'select':
        selected = save_image(folder, args.image)
        if record['selected'] != selected:
            record['selected'] = selected
            for target in targets.values():
                if target['phase'] not in ('ready', 'verified'):
                    target.update(phase='check_destination', changed_at=now)
        return
    if args.operation == 'resume':
        for target in targets.values():
            if target['phase'] != 'verified':
                target['phase'] = 'check_destination'
                target['check_after'] = now
        return
    platform = args.platform
    target = targets.get(platform)
    if target is None:
        raise Invalid('This platform is outside the saved post destinations.')
    if target['phase'] == 'verified':
        raise Invalid('This destination already has a verified permalink.')
    if args.operation == 'observe':
        observe(record, platform, target, local_json(args.evidence), now, folder)
        return
    if profile(platform, args.profile) != profile(platform, target['profile']):
        raise Invalid('The active account must match the destination.')
    if args.sha256 != record['selected'].split('.')[0]:
        raise Invalid('The image selection has changed. Inspect the latest image.')
    if args.operation in ('attach', 'submit'):
        require_current_retry_check(target, now)
    if args.operation == 'attach':
        require_phase(target, 'ready')
        target.update(phase='attaching', preview=None)
    elif args.operation == 'preview':
        require_phase(target, 'attaching')
        target.update(phase='attached', preview=record['selected'])
    elif args.operation == 'submit':
        require_phase(target, 'attached')
        if target['preview'] != record['selected']:
            raise Invalid('Check the latest selected image in the live composer.')
        group = [target]
        if platform == 'Instagram' and not (args.facebook_profile or args.facebook_off):
            raise Invalid('Inspect the Instagram cross-post switch and specify its Facebook profile or --facebook-off.')
        if platform != 'Instagram' and args.facebook_off:
            raise Invalid('--facebook-off applies only to the Instagram composer.')
        if args.facebook_profile:
            facebook = targets.get('Facebook')
            if (platform != 'Instagram' or facebook is None
                    or profile('Facebook', args.facebook_profile) != profile('Facebook', facebook['profile'])):
                raise Invalid('The Facebook cross-post destination must match the authorized account.')
            require_phase(facebook, 'ready')
            require_current_retry_check(facebook, now)
            group.append(facebook)
        for item in group:
            item.update(phase='awaiting_post', attempt_image=record['selected'],
                        submissions=item['submissions'] + 1, changed_at=now)
    target['changed_at'] = now


def result(record, job, folder, now):
    image = record['selected']
    check_saved_image(folder, image)
    return dict(record, job=job, selected_path=str(folder / image),
                elapsed_seconds={p: max(0, int(now - t['changed_at'])) for p, t in record['destinations'].items()})


def progress(record, platform, now):
    target = record['destinations'].get(platform)
    if target is None:
        raise Invalid('This platform is outside the saved post destinations.')
    return {'platform': platform, 'phase': target['phase'], 'version': record['version'],
            'elapsed_seconds': max(0, int(now - target['changed_at'])),
            'preview': target['preview'], 'submissions': target['submissions'], 'permalink': target['permalink']}


def run(args):
    root = safe_data_directory(args.data_dir) if args.data_dir else data_directory()
    read_only = args.operation in ('list', 'status', 'watch')
    guard = samples.read_only_real_workspace if read_only else samples.real_workspace
    with guard(root):
        base = private_folder(root / 'image-posts', create=not read_only)
        if args.operation == 'list':
            return sorted(p.name[5:] for p in base.glob('post-*') if (p / 'state.json').is_file())
        if not re.fullmatch('[a-z0-9][a-z0-9_-]{0,63}', args.job):
            raise Invalid('Use a short lowercase local post name with letters, digits, underscores or hyphens.')
        folder = private_folder(base / ('post-' + args.job), create=args.operation == 'start')
        path = folder / 'state.json'
        now = time.time()
        if args.operation == 'start':
            if path.exists():
                raise Invalid('This post already exists. Resume it to keep its delivery history.')
            record = new_record(local_json(args.plan), now)
            record['selected'] = save_image(folder, args.image)
        else:
            record = local_json(path)
            if not isinstance(record, dict) or record.get('format') != 1:
                raise Invalid('Unsupported recovery record. Keep it for inspection.')
            if args.operation == 'watch':
                return progress(record, args.platform, now)
            if args.operation != 'status':
                if args.version != record['version']:
                    raise Invalid('Recovery state changed. Read status again before acting.')
                update(record, args, folder, now)
                record['version'] += 1
        response = result(record, args.job, folder, now)
        if not read_only:
            atomic_write(path, json.dumps(record, ensure_ascii=False).encode('utf-8'))
        return response


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, help='Private local RES data folder')
    commands = parser.add_subparsers(dest='operation', required=True)
    commands.add_parser('list', help='Find existing local post names')
    watch = commands.add_parser('watch', help='Report a stalled browser action independently of its requested timeout')
    watch.add_argument('job')
    watch.add_argument('platform', choices=PLATFORMS)
    for operation in ('start', 'status', 'select', 'resume', 'attach', 'preview', 'submit', 'observe'):
        cmd = commands.add_parser(operation)
        cmd.add_argument('job')
        if operation in ('start', 'select'):
            cmd.add_argument('--image', type=Path, required=True)
        if operation == 'start':
            cmd.add_argument('--plan', type=Path, required=True)
        if operation not in ('start', 'status'):
            cmd.add_argument('--version', type=int, required=True)
        if operation in ('attach', 'preview', 'submit', 'observe'):
            cmd.add_argument('platform', choices=PLATFORMS)
        if operation in ('attach', 'preview', 'submit'):
            cmd.add_argument('--profile', required=True)
            cmd.add_argument('--sha256', required=True)
        if operation == 'submit':
            cross_post = cmd.add_mutually_exclusive_group()
            cross_post.add_argument('--facebook-profile', help='Facebook profile shown by the authorized Instagram cross-post switch')
            cross_post.add_argument('--facebook-off', action='store_true', help='The Instagram composer shows Facebook sharing off')
        if operation == 'observe':
            cmd.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        response = run(args)
        if args.operation == 'watch' and response['phase'] in ('attaching', 'awaiting_post'):
            delay = max(0, 15 - response['elapsed_seconds'])
            if delay:
                time.sleep(delay)  # The workspace lock was released by run().
                response = run(args)
            response['waiting_for_platform'] = response['phase'] in ('attaching', 'awaiting_post')
        print(json.dumps(response, ensure_ascii=False))
    except (ValueError, OSError, LookupError, TypeError, samples.SampleError) as error:
        print('Image post recovery: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
