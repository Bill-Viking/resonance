"""Data location resolution and compatibility, entirely inside temporary folders."""
import importlib.util
from contextlib import ExitStack, closing
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import server


class DataDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.dict(os.environ, {'HOME': str(self.root), 'USERPROFILE': str(self.root)}, clear=True))
        stack.enter_context(patch.object(Path, 'home', return_value=self.root))

    def test_five_step_order_on_each_platform(self):
        for platform, parent in (
            ('darwin', self.root / 'Library' / 'Application Support'),
            ('win32', self.root / 'AppData' / 'Local'),
            ('linux', self.root / '.local' / 'share'),
        ):
            with self.subTest(platform=platform), patch.object(server.sys, 'platform', platform):
                modern, legacy = parent / 'Resonance', parent / 'Social Brief'
                self.assertEqual(server.data_directory(), modern)
                self.assertFalse(modern.exists())
                legacy.mkdir(parents=True)
                marker = legacy / 'untouched'
                marker.write_bytes(b'legacy contents')
                self.assertEqual(server.data_directory(), legacy)
                self.assertEqual(marker.read_bytes(), b'legacy contents')
                self.assertFalse(modern.exists())
                modern.mkdir()
                self.assertEqual(server.data_directory(), modern)
                with patch.dict(os.environ, {'SOCIAL_BRIEF_DATA_DIR': str(self.root / 'old-env')}):
                    self.assertEqual(server.data_directory(), self.root / 'old-env')
                    with patch.dict(os.environ, {'RESONANCE_DATA_DIR': str(self.root / 'new-env')}):
                        self.assertEqual(server.data_directory(), self.root / 'new-env')
                with patch.dict(os.environ, {'RESONANCE_DATA_DIR': '~/explicit'}):
                    self.assertEqual(server.data_directory(), self.root / 'explicit')

    def test_platform_parent_overrides_and_linux_legacy(self):
        for platform, variable in (('win32', 'LOCALAPPDATA'), ('linux', 'XDG_DATA_HOME')):
            parent = self.root / platform
            with patch.object(server.sys, 'platform', platform), patch.dict(os.environ, {variable: str(parent)}):
                self.assertEqual(server.data_directory(), parent / 'Resonance')
                if platform == 'linux':
                    (parent / 'social-brief').mkdir(parents=True)
                    self.assertEqual(server.data_directory(), parent / 'social-brief')

    def test_legacy_database_is_used_in_place(self):
        with patch.object(server.sys, 'platform', 'darwin'):
            folder = self.root / 'Library' / 'Application Support' / 'Social Brief'
            folder.mkdir(parents=True)
            old = folder / 'social-brief.sqlite3'
            # Close the handle too: Windows cannot remove a temporary folder holding an open file.
            with closing(sqlite3.connect(old)) as db, db:
                db.execute('CREATE TABLE settings (id INTEGER PRIMARY KEY, data TEXT NOT NULL)')
                db.execute('INSERT INTO settings VALUES (1, ?)', ('{"display_name":"Example","theme":"dark"}',))
            inode = old.stat().st_ino
            store = server.Store(server.data_directory())
            self.assertEqual(store.directory, folder)
            self.assertEqual(store.path, old)
            self.assertEqual(old.stat().st_ino, inode)
            self.assertEqual(store.state()['settings']['display_name'], 'Example')
            self.assertFalse((folder / 'resonance.sqlite3').exists())
            self.assertFalse((folder.parent / 'Resonance').exists())

    def test_new_database_name_and_ambiguous_pair_refused(self):
        store = server.Store(self.root / 'new')
        self.assertEqual(store.path.name, 'resonance.sqlite3')
        (store.directory / 'social-brief.sqlite3').touch()
        before = store.path.read_bytes()
        with self.assertRaisesRegex(server.Invalid, 'Two databases'):
            server.Store(store.directory)
        self.assertEqual(store.path.read_bytes(), before)

    def test_cloud_git_and_symlink_guards(self):
        for name in ('iCloud', 'Mobile Documents', 'CloudStorage', 'Dropbox', 'Google Drive', 'GoogleDrive', 'OneDrive - Example', '.git'):
            with self.subTest(name=name), self.assertRaises(server.Invalid):
                server.safe_data_directory(self.root / name / 'data')
        repo = self.root / 'repository'
        repo.mkdir()
        (repo / '.git').write_text('gitdir: elsewhere')
        with self.assertRaises(server.Invalid):
            server.safe_data_directory(repo / 'data')
        if os.name != 'nt':
            cloud = self.root / 'Dropbox'
            cloud.mkdir()
            link = self.root / 'alias'
            link.symlink_to(cloud, target_is_directory=True)
            with self.assertRaises(server.Invalid):
                server.safe_data_directory(link / 'data')

    def test_optional_personal_exclusions_protect_descendants(self):
        excluded = self.root / 'my-notes'
        self.assertEqual(server.safe_data_directory(excluded), excluded)
        (self.root / 'never-store-here.txt').write_text('# One directory per line\n\nmy-notes\n' + str(self.root / 'private-sync') + '\n')
        for path in (excluded, excluded / 'reports' / 'nested', self.root / 'private-sync' / 'data'):
            with self.assertRaisesRegex(server.Invalid, 'never-store-here'):
                server.safe_data_directory(path)
        self.assertEqual(server.safe_data_directory(self.root / 'my-notes-extra'), self.root / 'my-notes-extra')

    def test_folder_holding_a_database_wins_over_an_empty_folder_of_the_new_name(self):
        # The Mac launcher keeps runtimes and logs in a folder of the new name beside the legacy data.
        with patch.object(server.sys, 'platform', 'darwin'):
            parent = self.root / 'Library' / 'Application Support'
            state, legacy = parent / 'Resonance', parent / 'Social Brief'
            (state / 'runtime' / 'abc1234').mkdir(parents=True)
            (state / 'launcher.log').write_text('started\n')
            legacy.mkdir()
            old = legacy / 'social-brief.sqlite3'
            with closing(sqlite3.connect(old)) as db, db:
                db.execute('CREATE TABLE settings (id INTEGER PRIMARY KEY, data TEXT NOT NULL)')
                db.execute('INSERT INTO settings VALUES (1, ?)', ('{"display_name":"Example","theme":"dark"}',))
            self.assertEqual(server.data_directory(), legacy)
            store = server.Store(server.data_directory())
            self.assertEqual(store.path, old)
            self.assertEqual(store.state()['settings']['display_name'], 'Example')
            self.assertFalse((state / 'resonance.sqlite3').exists())
            self.assertFalse((state / 'social-brief.sqlite3').exists())
            # A database of either name in the new folder keeps the designed order.
            (state / 'social-brief.sqlite3').touch()
            self.assertEqual(server.data_directory(), state)
            (state / 'social-brief.sqlite3').unlink()
            (state / 'resonance.sqlite3').touch()
            self.assertEqual(server.data_directory(), state)
        with patch.object(server.sys, 'platform', 'linux'):
            parent = self.root / '.local' / 'share'
            (parent / 'Resonance').mkdir(parents=True)
            (parent / 'social-brief').mkdir()
            (parent / 'social-brief' / 'social-brief.sqlite3').touch()
            self.assertEqual(server.data_directory(), parent / 'social-brief')

    @unittest.skipIf(os.name == 'nt', 'POSIX permissions')
    def test_unreadable_exclusion_file_is_one_plain_refusal(self):
        config = self.root / 'never-store-here.txt'
        config.write_text('my-notes\n')
        config.chmod(0)
        self.addCleanup(config.chmod, 0o600)
        with self.assertRaisesRegex(server.Invalid, 'could not be read'):
            server.safe_data_directory(self.root / 'anywhere')

    def test_mac_builder_imports_shared_resolver(self):
        spec = importlib.util.spec_from_file_location('launcher_builder', server.ROOT / 'mac-launcher' / 'build.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        self.assertIs(builder.data_directory, server.data_directory)
        config = self.root / 'release.json'
        self.assertEqual(builder.bundle_identifier(config), 'io.github.resonance-project.resonance')
        config.write_text('{"github_owner":"Example-Owner"}')
        self.assertEqual(builder.bundle_identifier(config), 'io.github.example-owner.resonance')
        config.write_text('{"github_owner":"../invalid"}')
        with self.assertRaises(ValueError):
            builder.bundle_identifier(config)

    def test_data_folder_details_are_local_and_excluded_from_backups(self):
        store = server.Store(self.root / 'workspace')
        other = self.root / 'outside-private-file'
        other.write_bytes(b'x' * 100000)
        if os.name != 'nt':
            (store.directory / 'outside-link').symlink_to(other)
        details = store.data_folder_details()
        actual = sum(p.stat().st_size for p in store.directory.rglob('*') if p.is_file() and not p.is_symlink())
        self.assertEqual(details['size_bytes'], actual)
        self.assertEqual(details['path'], str(store.directory))
        self.assertIsNotNone(details['last_automatic_backup'])
        self.assertNotIn(str(store.directory), str(store.backup()))

    def test_open_folder_uses_only_fixed_local_target(self):
        store = server.Store(self.root / 'workspace')
        for platform, expected in (('darwin', '/usr/bin/open'), ('linux', 'xdg-open')):
            with patch.object(server.sys, 'platform', platform), patch.object(server.subprocess, 'run') as run:
                self.assertEqual(store.open_data_folder(), {'opened': True})
                self.assertEqual(run.call_args[0][0], [expected, str(store.directory)])
        with patch.object(server.sys, 'platform', 'win32'), patch.object(server.os, 'startfile', create=True) as start:
            store.open_data_folder()
            start.assert_called_once_with(str(store.directory))
        with patch.object(server.sys, 'platform', 'linux'), patch.object(server.subprocess, 'run', side_effect=OSError('missing')):
            with self.assertRaisesRegex(server.Invalid, 'copy its path'):
                store.open_data_folder()


if __name__ == '__main__':
    unittest.main()
