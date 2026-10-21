"""Check shell handoff without starting an app or accessing real user data."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import server


@unittest.skipIf(os.name == 'nt', 'POSIX shell launcher')
class ShellLauncherTests(unittest.TestCase):
    def test_launch_from_other_folder_preserves_arguments_and_exit_status(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            app = root / 'app with spaces'
            app.mkdir()
            launcher = app / 'resonance'
            shutil.copy2(server.ROOT / 'resonance', launcher)
            binary = root / 'bin'
            binary.mkdir()
            fake = binary / 'python3'
            fake.write_text('#!/bin/sh\npwd\nprintf "%s\\n" "$@"\nexit 23\n')
            fake.chmod(0o700)
            environment = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ.get('PATH', ''))
            result = subprocess.run([str(launcher), '--port', '12345', 'two words'], cwd=root,
                                    env=environment, capture_output=True, text=True)
            self.assertEqual(result.returncode, 23)
            self.assertEqual(result.stdout.splitlines(), [str(app), 'server.py', '--open', '--port', '12345', 'two words'])

    def test_missing_python_explains_requirement(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            launcher = root / 'resonance'
            shutil.copy2(server.ROOT / 'resonance', launcher)
            (root / 'dirname').symlink_to(shutil.which('dirname'))
            result = subprocess.run([str(launcher)], cwd=root, env=dict(os.environ, PATH=str(root)),
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 127)
            self.assertIn('Python 3.9 or newer', result.stderr)


if __name__ == '__main__':
    unittest.main()
