"""Verify the verbatim desktop notices without downloading upstream files."""
import hashlib
import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
RULE = b'=' * 72 + b'\n'
HEADER = re.compile(rb'^' + RULE + rb'([^\n]+)\nSource: ([^\n]+)\n'
                    rb'SHA-256: ([0-9a-f]{64})\nBytes: ([1-9][0-9]*)\n' + RULE + rb'\n', re.MULTILINE)


class NoticeTests(unittest.TestCase):
    def test_all_27_notice_sections_match_the_catalog_and_exact_upstream_hashes(self):
        raw = (ROOT / 'THIRD-PARTY-NOTICES.txt').read_bytes()
        catalog = json.loads((ROOT / 'desktop/third-party-sources.json').read_bytes())
        self.assertEqual(catalog['format'], 1)
        entries = catalog['sources']
        headers = list(HEADER.finditer(raw))
        self.assertEqual(len(headers), 27)
        self.assertEqual(len(entries), len(headers))
        self.assertEqual(len({entry['file'] for entry in entries}), len(entries))
        for index, (header, entry) in enumerate(zip(headers, entries)):
            name, url, digest, size = header.groups()
            with self.subTest(notice=entry['file']):
                self.assertEqual(entry, {'file': name.decode('ascii'), 'url': url.decode('ascii'),
                                         'sha256': digest.decode('ascii'), 'size': int(size)})
                self.assertIs(type(entry['size']), int)
                end = header.end() + entry['size']
                body = raw[header.end():end]
                self.assertEqual(len(body), entry['size'])
                self.assertEqual(hashlib.sha256(body).hexdigest(), entry['sha256'])
                boundary = headers[index + 1].start() if index + 1 < len(headers) else len(raw)
                # Section framing may contain blank lines after the exact upstream bytes.
                self.assertRegex(raw[end:boundary], rb'\A\n{1,3}\Z')
                if name == b'SQLite-LICENSE.md':
                    self.assertRegex(url, rb'\Ahttps://raw\.githubusercontent\.com/sqlite/sqlite/'
                                          rb'[0-9a-f]{40}/LICENSE\.md\Z')
