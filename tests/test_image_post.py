"""Exercise reconnects and uncertain platform results using disposable local files."""
import contextlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

import image_post
from tools import make_release
import assistant_kit


def png(rgb):
    def chunk(kind, body):
        return (struct.pack('>I', len(body)) + kind + body
                + struct.pack('>I', zlib.crc32(kind + body) & 0xffffffff))
    header = struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header)
            + chunk(b'IDAT', zlib.compress(b'\0' + bytes(rgb))) + chunk(b'IEND', b''))


class ImageRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.base = ['--data-dir', str(self.root / 'workspace')]
        self.script = str(Path(image_post.__file__).resolve())
        self.image = self.root / 'picture.png'
        self.image.write_bytes(png((40, 20, 10)))
        self.plan = {k: {'profile': 'https://' + site + '/synthetic_user', 'caption': k + ' caption'}
                     for k, site in [('X', 'x.com'), ('Instagram', 'www.instagram.com'), ('Facebook', 'facebook.com')]}
        self.plan['Facebook']['caption'] = self.plan['Instagram']['caption']
        self.start(self.plan)

    def plan_file(self, plan):
        path = self.root / 'plan.json'
        path.write_text(json.dumps(plan))
        return str(path)

    def start(self, plan, job='demo'):
        self.job = job
        self.state = self.call('start', job, '--image', str(self.image), '--plan', self.plan_file(plan))

    def saved_record(self):
        return (self.root / 'workspace' / 'image-posts' / ('post-' + self.job) / 'state.json').read_bytes()

    def call(self, *args, error=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = image_post.main(self.base + list(args))
        if error is not None:
            self.assertEqual(code, 1)
            self.assertIn(error, err.getvalue())
            self.assertFalse(out.getvalue())
            return
        self.assertEqual(code, 0, err.getvalue())
        return json.loads(out.getvalue())

    def act(self, op, *args, error=None):
        previous = self.saved_record() if error is not None else None
        res = self.call(op, self.job, *args, '--version', str(self.state['version']), error=error)
        if error is None:
            self.state = res
        else:
            self.assertEqual(self.saved_record(), previous)

    def browser(self, op, platform, *args, profile=None, error=None):
        self.act(op, platform, '--profile', profile or self.plan[platform]['profile'], '--sha256',
                 self.state['selected'].split('.')[0], *args, error=error)

    def revise(self, plan=None, *, image=None, remove=(), error=None):
        args = []
        if plan is not None:
            args += ['--plan', self.plan_file(plan)]
        if image is not None:
            args += ['--image', str(image)]
        if remove:
            args += ['--remove', *remove]
        self.act('revise', *args, error=error)

    def observe(self, platform, outcome, *, error=None, **fields):
        data = dict(profile=self.plan[platform]['profile'], result=outcome, detail='Synthetic browser observation',
                    checked_at=datetime.now(timezone.utc).isoformat())
        data.update(fields)
        path = self.root / 'evidence.json'
        path.write_text(json.dumps(data))
        self.act('observe', platform, '--evidence', str(path), error=error)

    def published(self, platform, link, **fields):
        values = dict(author_profile=self.plan[platform]['profile'], caption=self.plan[platform]['caption'],
                      matched_image_sha256=self.state['selected'].split('.')[0], permalink=link)
        self.observe(platform, 'published', **{**values, **fields})

    def test_latest_image_survives_source_loss_and_fresh_process(self):
        old_version = self.state['version']
        old = self.state['selected']
        newest = png((10, 80, 60))
        self.image.write_bytes(newest)
        self.act('select', '--image', str(self.image))
        self.assertNotEqual(self.state['selected'], old)
        self.image.unlink()
        raw = subprocess.check_output([sys.executable, '-B', self.script, *self.base, 'status', 'demo'])
        self.assertEqual(Path(json.loads(raw)['selected_path']).read_bytes(), newest)
        self.assertEqual(self.call('list'), ['demo'])
        self.call('resume', 'demo', '--version', str(old_version), error='changed')
        self.assertEqual(self.call('status', 'demo')['version'], self.state['version'])
        Path(self.state['selected_path']).write_bytes(b'corrupt')
        self.call('status', 'demo', error='missing or changed')

    def test_cross_post_partial_delivery_blocks_blind_retries(self):
        self.browser('attach', 'Instagram')
        self.browser('preview', 'Instagram')
        self.browser('submit', 'Instagram', error='cross-post switch')
        self.browser('submit', 'Instagram', '--facebook-profile', self.plan['Facebook']['profile'])
        self.assertIsNone(self.state['destinations']['Facebook']['preview'])
        self.act('resume')
        self.published('Instagram', 'https://www.instagram.com/p/synthetic_photo/')
        receipt = self.state['destinations']['Instagram']
        self.observe('Facebook', 'unknown')
        self.browser('attach', 'Facebook', error='before another attempt')
        self.browser('submit', 'Instagram', '--facebook-off', error='verified permalink')
        self.observe('Facebook', 'absent', prior_action_settled=True, composer='empty',
                     recent_posts_checked=True, error='confirms the submission failed')
        self.observe('X', 'absent', prior_action_settled=True, composer='empty', recent_posts_checked=True)
        self.browser('attach', 'X')
        self.published('Facebook', 'https://facebook.com/synthetic_user/posts/synthetic_post')
        self.assertEqual(receipt, self.state['destinations']['Instagram'])
        self.assertEqual(self.state['destinations']['Facebook']['submissions'], 1)
        self.assertEqual(self.state['destinations']['X']['phase'], 'attaching')

    def test_add_facebook_friends_to_attached_instagram_and_verify_each_post(self):
        self.start({'Instagram': dict(self.plan['Instagram'], audience='Public')}, 'shared')
        self.browser('attach', 'Instagram')
        self.browser('preview', 'Instagram')
        self.act('approve', 'Instagram')
        attached = self.state['destinations']['Instagram']
        self.revise({'Facebook': dict(self.plan['Facebook'], caption='Different caption', audience='Friends')})
        self.assertEqual(self.state['destinations']['Instagram'], attached)
        facebook = self.plan['Facebook']['profile']
        preview = ('--confirm-preview', '--audience', 'Public')
        linked = ('--facebook-profile', facebook, '--facebook-audience', 'Friends')
        self.browser('submit', 'Instagram', *preview, *linked, error='caption')
        self.revise({'Facebook': {'caption': self.plan['Facebook']['caption']}})
        self.assertEqual(self.state['destinations']['Facebook']['audience'], 'Friends')
        self.act('approve', 'Instagram', 'Facebook')
        for flags, observed_profile, reason in (
            (preview, None, 'cross-post switch'),
            ((*preview, '--facebook-profile', facebook), None, 'audience'),
            ((*preview, '--facebook-profile', facebook, '--facebook-audience', 'Public'), None, 'audience'),
            ((*preview, '--facebook-profile', 'https://facebook.com/another_user', '--facebook-audience', 'Friends'),
             None, 'account'),
            ((*preview, *linked), 'https://www.instagram.com/another_user', 'account'),
            (('--confirm-preview', '--audience', 'Close Friends', *linked), None, 'audience'),
        ):
            with self.subTest(flags=flags, observed_profile=observed_profile):
                self.browser('submit', 'Instagram', *flags, profile=observed_profile, error=reason)
        self.browser('submit', 'Instagram', *preview, *linked)
        for platform in ('Instagram', 'Facebook'):
            target = self.state['destinations'][platform]
            self.assertEqual((target['phase'], target['submissions']), ('awaiting_post', 1))
            self.assertEqual(target['attempt_image'], self.state['selected'])
        self.act('resume')
        self.published('Instagram', 'https://www.instagram.com/p/synthetic_photo/')
        self.assertIsNone(self.state['destinations']['Facebook']['permalink'])
        self.browser('attach', 'Facebook', error='before another attempt')
        link = 'https://facebook.com/synthetic_user/posts/synthetic_post'
        self.published('Facebook', link, audience='Public', error='published audience')
        self.published('Facebook', link, audience='Friends')
        self.assertEqual(self.state['destinations']['Facebook']['published_audience'], 'Friends')
        self.assertEqual(self.state['destinations']['Facebook']['permalink'], link)

    def test_instagram_only_confirmation_and_later_independent_destinations(self):
        self.start({'Instagram': dict(self.plan['Instagram'], audience='Public')}, 'separate')
        self.act('approve', 'Instagram')
        approved = self.state['destinations']['Instagram']
        self.revise({'X': dict(self.plan['X'], audience='Public')})
        self.assertEqual(self.state['destinations']['Instagram'], approved)
        self.browser('attach', 'X')
        self.browser('preview', 'X')
        self.revise(remove=('X',))
        self.assertEqual(set(self.state['destinations']), {'Instagram'})
        self.browser('attach', 'Instagram')
        self.act('resume')
        self.assertEqual(self.state['destinations']['Instagram']['approval'], approved['approval'])
        self.observe('Instagram', 'absent', prior_action_settled=True, composer='selected_image',
                     recent_posts_checked=True, matched_image_sha256=self.state['selected'].split('.')[0])
        self.browser('submit', 'Instagram', '--confirm-preview', '--audience', 'Public', error='cross-post switch')
        self.browser('submit', 'Instagram', '--confirm-preview', '--facebook-off', error='audience')
        self.browser('submit', 'Instagram', '--confirm-preview', '--audience', 'Public', '--facebook-off')
        self.published('Instagram', 'https://www.instagram.com/p/synthetic_photo/')
        receipt = self.state['destinations']['Instagram']
        later = {'X': dict(self.plan['X'], audience='Public'),
                 'Facebook': dict(self.plan['Facebook'], audience='Friends')}
        plan = self.plan_file(later)
        previous = self.saved_record()
        self.call('revise', self.job, '--version', str(self.state['version'] - 1), '--plan', plan, error='changed')
        self.assertEqual(self.saved_record(), previous)
        self.revise({**later, 'Instagram': {'caption': 'Changed after delivery'}}, error='verified permalink')
        self.revise({**later, 'Instagram': {'profile': 'https://www.instagram.com/another_user'}},
                    error='revised profile must match')
        self.revise(later, remove=('Instagram',), error='never been submitted')
        self.revise(later)
        self.assertEqual(self.state['destinations']['Instagram'], receipt)
        self.browser('attach', 'Facebook')
        self.browser('submit', 'Facebook', '--confirm-preview', '--audience', 'Friends')
        self.assertEqual(self.state['destinations']['Facebook']['submissions'], 1)
        self.assertEqual(self.state['destinations']['X']['submissions'], 0)

    def test_caption_revision_and_image_replacement_keep_unresolved_attempt_exact(self):
        self.revise({'X': {'audience': 'Public'}, 'Instagram': {'audience': 'Public'},
                     'Facebook': {'audience': 'Friends'}})
        self.act('approve', 'X', 'Instagram')
        old_image = self.state['selected']
        old_approval = self.state['destinations']['X']['approval']
        instagram = self.state['destinations']['Instagram']
        self.browser('attach', 'X')
        self.browser('preview', 'X')
        caption = 'Revised synthetic caption'
        self.revise({'X': {'caption': caption}})
        target = self.state['destinations']['X']
        self.assertEqual((target['phase'], target['preview'], target['audience']), ('attached', old_image, 'Public'))
        self.assertIsNone(target.get('approval'))
        self.assertEqual(self.state['destinations']['Instagram'], instagram)
        self.browser('submit', 'X', '--audience', 'Public', error='approval')
        self.act('approve', 'X')
        self.assertNotEqual(self.state['destinations']['X']['approval'], old_approval)
        self.assertFalse(self.state['destinations']['X'].get('approval_required'))
        self.browser('submit', 'X', '--confirm-preview', '--audience', 'Public')
        sent = self.state['destinations']['X']
        self.browser('attach', 'Facebook')
        self.browser('submit', 'Facebook', '--confirm-preview', '--audience', 'Friends')
        facebook_approval = self.state['destinations']['Facebook']['approval']
        self.image.write_bytes(png((5, 50, 75)))
        media = set(Path(self.state['selected_path']).parent.glob('*.png'))
        self.revise({'X': {'caption': 'Changed during delivery'}, 'Instagram': {'caption': 'Also changed'}},
                    image=self.image, error='pending submission')
        self.assertEqual(set(Path(self.state['selected_path']).parent.glob('*.png')), media)
        self.revise(remove=('X',), error='never been submitted')
        self.revise(image=self.image)
        self.assertNotEqual(self.state['selected'], old_image)
        self.assertIsNone(self.state['destinations']['Instagram'].get('approval'))
        self.act('resume')
        target = self.state['destinations']['X']
        self.assertEqual((target['attempt_image'], target['caption'], target['submissions']), (old_image, caption, 1))
        self.assertEqual(target['approval'], sent['approval'])
        self.assertEqual(self.state['destinations']['Facebook']['approval'], facebook_approval)
        self.browser('submit', 'X', '--confirm-preview', '--audience', 'Public', error='before another attempt')
        self.published('X', 'https://x.com/synthetic_user/status/123', caption=caption, error='image and caption')
        self.published('X', 'https://x.com/synthetic_user/status/123', caption=sent['caption'],
                       matched_image_sha256=old_image.split('.')[0])
        verified = self.state['destinations']['X']
        self.assertEqual(verified['published_image'], old_image)
        self.assertEqual(verified['approval'], sent['approval'])
        self.observe('Facebook', 'absent', prior_action_settled=True, composer='empty', recent_posts_checked=True,
                     submission_failure='Platform explicitly reported failure')
        facebook = self.state['destinations']['Facebook']
        self.assertEqual(facebook['phase'], 'ready')
        self.assertIsNone(facebook['attempt_image'])
        self.assertIsNone(facebook.get('approval'))

    def test_late_success_matches_previous_image_and_preserves_latest_selection(self):
        submitted = self.state['selected']
        for op in ('attach', 'preview', 'submit'):
            self.browser(op, 'X')
        self.image.write_bytes(png((5, 50, 75)))
        self.act('select', '--image', str(self.image))
        self.published('X', 'https://x.com/synthetic_user/status/123', error='image and caption')
        self.published('X', 'https://x.com/synthetic_user/status/123', matched_image_sha256=submitted.split('.')[0])
        self.assertEqual(self.state['destinations']['X']['published_image'], submitted)
        self.assertNotEqual(self.state['selected'], submitted)
        self.assertEqual(Path(self.state['selected_path']).read_bytes(), self.image.read_bytes())

    def test_failed_submit_allows_only_the_step_the_composer_needs(self):
        for op in ('attach', 'preview', 'submit'):
            self.browser(op, 'X')
        self.observe('X', 'absent', prior_action_settled=False, composer='selected_image',
                     recent_posts_checked=True, error='settled composer')
        self.observe('X', 'absent', prior_action_settled=True, composer='selected_image', recent_posts_checked=True,
                     submission_failure='Platform explicitly reported failure', matched_image_sha256=self.state['selected'].split('.')[0])
        self.browser('attach', 'X', error='before another attempt')
        self.browser('submit', 'X')
        self.assertEqual(self.state['destinations']['X']['submissions'], 2)

    def test_wrong_account_link_image_time_or_caption_cannot_verify(self):
        self.browser('attach', 'X')
        self.observe('X', 'unknown', profile='https://x.com/someone_else', error='does not match')
        self.observe('X', 'unknown', checked_at='2020-01-01T00:00:00Z', error='fresh observation')
        for link in ('https://x.com/someone_else/status/123', 'https://www.instagram.com/p/example/',
                     'https://x.com/synthetic_user', 'https://example.test/posts/123'):
            self.published('X', link, error='post')
        self.published('X', 'https://x.com/synthetic_user/status/123', caption='something else', error='image and caption')
        self.published('X', 'https://x.com/synthetic_user/status/123', author_profile='https://x.com/elsewhere', error='author')
        self.assertIsNone(self.call('status', 'demo')['destinations']['X']['permalink'])

    def test_reconnect_requires_inspection_of_old_composer_and_independent_progress(self):
        self.browser('attach', 'X')
        with patch.object(image_post.time, 'sleep') as pause:
            pending = self.call('watch', 'demo', 'X')
            pause.assert_called_once()
        self.assertTrue(pending['waiting_for_platform'])
        self.assertIsNone(pending['preview'])
        self.act('resume')
        self.browser('preview', 'X', error='before another attempt')
        self.observe('X', 'absent', prior_action_settled=True, composer='selected_image', recent_posts_checked=True,
                     matched_image_sha256='0'*64, error='latest selected')
        self.observe('X', 'absent', prior_action_settled=True, composer='selected_image', recent_posts_checked=True,
                     matched_image_sha256=self.state['selected'].split('.')[0])
        self.browser('submit', 'X')

    def test_concurrent_submit_grants_exactly_one_attempt(self):
        self.revise({'X': {'audience': 'Public'}})
        self.browser('attach', 'X')
        argv = [sys.executable, '-B', self.script, *self.base, 'submit', 'demo', 'X', '--profile', self.plan['X']['profile'],
                '--version', str(self.state['version']), '--sha256', self.state['selected'].split('.')[0],
                '--confirm-preview', '--audience', 'Public']
        with ThreadPoolExecutor(max_workers=2) as pool:
            attempts = list(pool.map(lambda _: subprocess.run(argv, capture_output=True), range(2)))
        self.assertEqual(sorted(r.returncode for r in attempts), [0, 1])
        current = self.call('status', 'demo')
        saved = current['destinations']['X']
        self.assertEqual((saved['phase'], saved['submissions']), ('awaiting_post', 1))
        self.state = current
        self.browser('submit', 'X', '--confirm-preview', '--audience', 'Public', error='before another attempt')

    def test_reconnect_discards_prior_observations_and_contradictory_absence(self):
        inspected = datetime.now(timezone.utc).isoformat()
        self.act('resume')
        self.observe('X', 'absent', checked_at=inspected, prior_action_settled=True,
                     composer='empty', recent_posts_checked=True, error='fresh observation')
        self.observe('X', 'absent', prior_action_settled=True, recent_posts_checked=True,
                     composer='empty', permalink='https://x.com/synthetic_user/status/123', error='evidence fields')
        self.assertEqual(self.call('status', 'demo')['destinations']['X']['phase'], 'check_destination')

    def test_retry_requires_a_fresh_destination_check_even_in_the_same_session(self):
        self.act('resume')
        self.observe('X', 'absent', prior_action_settled=True, recent_posts_checked=True, composer='empty')
        with patch.object(image_post.time, 'time', return_value=image_post.time.time() + 601):
            self.browser('attach', 'X', error='retry check expired')
        self.assertEqual(self.call('status', 'demo')['destinations']['X']['phase'], 'ready')

    def test_invalid_destination_plan_does_not_copy_private_media(self):
        plan = self.root / 'bad-plan.json'
        plan.write_text(json.dumps({'unknown': {}}))
        self.call('start', 'invalid', '--image', str(self.image), '--plan', str(plan), error='destination')
        folder = self.root / 'workspace' / 'image-posts' / 'post-invalid'
        self.assertFalse(list(folder.iterdir()))
        self.assertEqual(self.call('list'), ['demo'])

    def test_facebook_receipt_checks_numeric_account_and_original_post(self):
        account = {'profile': 'https://facebook.com/profile.php?id=1001'}
        for wrong in ('https://facebook.com/1002/posts/111', 'https://facebook.com/groups/1001/posts/111',
                      'https://facebook.com/story.php?story_fbid=111&id=1002',
                      'https://facebook.com/photo/?fbid=111&comment_id=222'):
            with self.subTest(wrong=wrong), self.assertRaises(image_post.Invalid):
                image_post.checked_link('Facebook', account, wrong)
        for right in ('https://facebook.com/1001/posts/111', 'https://facebook.com/photo/?fbid=111',
                      'https://facebook.com/story.php?story_fbid=111&id=1001'):
            self.assertEqual(image_post.checked_link('Facebook', account, right), right)

    def test_kit_and_export_include_the_executable_helper(self):
        self.assertIn('image_post.py', assistant_kit.REQUIRED)
        self.assertIn('image_post.py', make_release.REQUIRED_PUBLIC)
        self.assertTrue(make_release.public_path('image_post.py'))
        self.assertFalse(make_release.public_path('image-posts/post-demo/state.json'))
