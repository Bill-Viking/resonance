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
        plan = self.root / 'plan.json'
        plan.write_text(json.dumps(self.plan))
        self.state = self.call('start', 'demo', '--image', str(self.image), '--plan', str(plan))

    def call(self, *args, error=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = image_post.main(self.base + list(args))
        if error:
            self.assertEqual(code, 1)
            self.assertIn(error, err.getvalue())
            self.assertFalse(out.getvalue())
            return
        self.assertEqual(code, 0, err.getvalue())
        return json.loads(out.getvalue())

    def act(self, op, *args, error=None):
        res = self.call(op, 'demo', *args, '--version', str(self.state['version']), error=error)
        if error is None:
            self.state = res

    def browser(self, op, platform, *args, error=None):
        self.act(op, platform, '--profile', self.plan[platform]['profile'], '--sha256',
                 self.state['selected'].split('.')[0], *args, error=error)

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
        for op in ('attach', 'preview'):
            self.browser(op, 'X')
        argv = [sys.executable, '-B', self.script, *self.base, 'submit', 'demo', 'X', '--profile', self.plan['X']['profile'],
                '--version', str(self.state['version']), '--sha256', self.state['selected'].split('.')[0]]
        with ThreadPoolExecutor(max_workers=2) as pool:
            attempts = list(pool.map(lambda _: subprocess.run(argv, capture_output=True), range(2)))
        self.assertEqual(sorted(r.returncode for r in attempts), [0, 1])
        saved = self.call('status', 'demo')['destinations']['X']
        self.assertEqual((saved['phase'], saved['submissions']), ('awaiting_post', 1))

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
