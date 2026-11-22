"""Check the checkout attributes through a synthetic public export and checkout."""
import os
from pathlib import Path
import tempfile
import unittest

from tools import make_release

ATTRIBUTE_NAME = '.gitattributes'
NOTICE_NAME = 'THIRD-PARTY-NOTICES.txt'
IMAGE_NAME = 'browser-reader/icon-16.png'
NOTICE = b'Synthetic notice with trailing spaces\x20\x20\nSecond line\n'
ORDINARY_TEXT = b'Synthetic README\n'
BINARY = b'\x89PNG\r\n\x1a\n\x00\x00\r\n\x00synthetic\r\n\x00'
BASELINE_ATTRIBUTES = (NOTICE_NAME + ' -text\n').encode('ascii')
VERSION = '0.1.0-preview.1'
git = make_release.release_git


class NoticeCheckoutTests(unittest.TestCase):
    def export_and_clone(self, base, attributes):
        """Export a synthetic tree carrying these attributes, then clone it with autocrlf=true."""
        source = base / 'source'
        source.mkdir()
        output = base / 'public'
        checkout = base / 'checkout'
        git(source, 'init', '-b', 'main', '--template=')
        for name in make_release.REQUIRED_PUBLIC - {ATTRIBUTE_NAME}:
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'Synthetic public file\n')
        (source / 'VERSION').write_bytes((VERSION + '\n').encode('ascii'))
        (source / NOTICE_NAME).write_bytes(NOTICE)
        (source / 'README.md').write_bytes(ORDINARY_TEXT)
        (source / IMAGE_NAME).parent.mkdir(parents=True, exist_ok=True)
        (source / IMAGE_NAME).write_bytes(BINARY)
        git(source, 'add', '.')
        git(source, 'commit', '-m', 'Synthetic source without checkout attributes')
        with self.assertRaisesRegex(ValueError, r'^Missing public files: \.gitattributes$'):
            make_release.export(source, output, VERSION)
        self.assertFalse(output.exists())

        (source / ATTRIBUTE_NAME).write_bytes(attributes)
        git(source, 'add', ATTRIBUTE_NAME)
        git(source, 'commit', '-m', 'Add the checkout attributes')
        output, expected = make_release._export(source, output, VERSION)
        self.assertEqual((output / ATTRIBUTE_NAME).read_bytes(), attributes)
        make_release.commit_export(output, VERSION, expected)

        git(base, 'clone', '--template=', '-c', 'core.autocrlf=true', str(output), str(checkout))
        return checkout

    def isolated_status(self, checkout):
        # Touch the files so Git must compare bytes rather than trust its cached stat data.
        for name in ('README.md', NOTICE_NAME, IMAGE_NAME):
            stat = (checkout / name).stat()
            os.utime(checkout / name, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000_000))
        return git(checkout, '-c', 'core.autocrlf=false', 'status', '--porcelain', '--untracked-files=all')

    def test_public_export_requires_attributes_and_checkout_pins_bytes(self):
        attributes = (Path(__file__).resolve().parents[1] / ATTRIBUTE_NAME).read_bytes()
        with tempfile.TemporaryDirectory() as folder:
            checkout = self.export_and_clone(Path(folder), attributes)
            # Ordinary text keeps its committed LF bytes even under a user's autocrlf=true.
            self.assertEqual((checkout / 'README.md').read_bytes(), ORDINARY_TEXT)
            self.assertEqual((checkout / NOTICE_NAME).read_bytes(), NOTICE)
            self.assertEqual((checkout / IMAGE_NAME).read_bytes(), BINARY)
            self.assertEqual(self.isolated_status(checkout), b'')

    def test_baseline_attributes_convert_ordinary_text(self):
        # The previous attributes, held in memory, show the LF assertion is meaningful.
        with tempfile.TemporaryDirectory() as folder:
            checkout = self.export_and_clone(Path(folder), BASELINE_ATTRIBUTES)
            self.assertEqual((checkout / 'README.md').read_bytes(), b'Synthetic README\r\n')
            self.assertNotEqual((checkout / 'README.md').read_bytes(), ORDINARY_TEXT)
            self.assertEqual((checkout / NOTICE_NAME).read_bytes(), NOTICE)
            self.assertEqual((checkout / IMAGE_NAME).read_bytes(), BINARY)
            self.assertIn(b' M README.md', self.isolated_status(checkout))
