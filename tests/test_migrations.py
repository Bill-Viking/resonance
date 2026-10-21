"""Upgrade and recovery proofs use synthetic SQLite databases in temp folders."""
from contextlib import closing
import copy
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import server


def snapshot(path):
    with closing(sqlite3.connect(path)) as db:
        return (db.execute('PRAGMA user_version').fetchone()[0], tuple(db.iterdump()))


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name).resolve()

    def legacy(self):
        path = self.folder / 'social-brief.sqlite3'
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE settings (id INTEGER PRIMARY KEY, data TEXT NOT NULL)')
            db.execute('INSERT INTO settings VALUES (1, ?)', (json.dumps({'display_name': 'Example person', 'theme': 'dark'}),))
        return path

    def backups(self):
        return sorted((self.folder / 'backups' / 'auto').glob('*-before-v*.sqlite3'))

    def test_unversioned_database_upgrades_once_and_keeps_exact_snapshot(self):
        path = self.legacy()
        before = snapshot(path)
        store = server.Store(self.folder)
        self.assertEqual(snapshot(path)[0], len(server.MIGRATIONS))
        self.assertEqual(snapshot(self.backups()[0]), before)
        self.assertEqual(store.state()['settings']['display_name'], 'Example person')
        count = len(self.backups())
        self.assertEqual(server.Store(self.folder).backup(), store.backup())
        self.assertEqual(len(self.backups()), count)
        if os.name != 'nt':
            for backup in self.backups():
                self.assertEqual(backup.stat().st_mode & 0o777, 0o600)

    def test_failed_migration_restores_data_schema_and_version(self):
        path = self.legacy()
        before = snapshot(path)

        def broken(store, db):
            db.execute('UPDATE settings SET data=?', ('{"lost":true}',))
            db.execute('CREATE TABLE partial (id INTEGER)')
            db.execute('PRAGMA user_version=17')
            # Even a badly written migration which commits before failing recovers.
            db.commit()
            raise RuntimeError('synthetic failure')

        with patch.object(server, 'MIGRATIONS', (broken,)):
            with self.assertRaisesRegex(server.Invalid, 'restored its backup'):
                server.Store(self.folder)
        self.assertEqual(snapshot(path), before)
        self.assertEqual(snapshot(self.backups()[0]), before)
        self.assertEqual(server.Store(self.folder).state()['settings']['display_name'], 'Example person')

    def test_backup_includes_wal_records_from_an_open_connection(self):
        path = self.legacy()
        with closing(sqlite3.connect(path)) as live:
            live.execute('PRAGMA journal_mode=WAL')
            live.execute('UPDATE settings SET data=?', ('{"display_name":"Latest WAL value","theme":"light"}',))
            live.commit()
            self.assertTrue(Path(str(path) + '-wal').exists())
            before = snapshot(path)
            store = server.Store(self.folder)
            self.assertEqual(snapshot(self.backups()[0]), before)
            self.assertEqual(store.state()['settings']['display_name'], 'Latest WAL value')

    def test_newer_database_is_refused_without_touching_bytes(self):
        path = self.legacy()
        with closing(sqlite3.connect(path)) as db:
            db.execute('PRAGMA user_version=999')
        before, stat = path.read_bytes(), path.stat()
        with self.assertRaisesRegex(server.Invalid, 'newer version'):
            server.Store(self.folder)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(path.stat().st_mtime_ns, stat.st_mtime_ns)
        self.assertEqual(path.stat().st_mode, stat.st_mode)
        self.assertFalse((self.folder / 'backups').exists())

    def test_backup_failure_refuses_before_running_migration(self):
        path = self.legacy()
        before = snapshot(path)
        with patch.object(server.Store, 'migration_backup', side_effect=OSError('full')):
            with self.assertRaisesRegex(server.Invalid, 'could not save an upgrade backup'):
                server.Store(self.folder)
        self.assertEqual(snapshot(path), before)

    def test_ordered_migrations_and_retention_keep_five(self):
        observed = []

        def step(number):
            def migrate(store, db):
                observed.append(db.execute('PRAGMA user_version').fetchone()[0])
                db.execute('INSERT INTO sequence VALUES (?)', (number,))
            return migrate

        path = self.legacy()
        with closing(sqlite3.connect(path)) as db:
            db.execute('CREATE TABLE sequence (number INTEGER)')
        migrations = tuple(step(number) for number in range(1, 8))
        with patch.object(server, 'MIGRATIONS', migrations):
            server.Store(self.folder)
        self.assertEqual(observed, list(range(7)))
        self.assertEqual(len(self.backups()), 5)
        self.assertEqual([snapshot(p)[0] for p in self.backups()], list(range(2, 7)))
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute('SELECT number FROM sequence').fetchall(), [(n,) for n in range(1, 8)])

    def test_backup_versions_one_and_two_import_without_clearing_omitted_collections(self):
        store = server.Store(self.folder)
        exported = store.backup()
        self.assertEqual(exported['schema_version'], 2)
        store.mutate('PATCH', '/api/monitoring', {'scope': 'Synthetic retained scope'})
        for version in (1, 2):
            body = copy.deepcopy(exported)
            body['schema_version'] = version
            body.pop('monitoring')
            body.pop('communication')
            self.assertEqual(store.mutate('POST', '/api/import', body)[0], 200)
            self.assertEqual(store.backup()['monitoring']['config']['scope'], 'Synthetic retained scope')
        for version in (True, 0, 3, '2'):
            with self.assertRaisesRegex(server.Invalid, 'Unsupported backup version'):
                store.mutate('POST', '/api/import', dict(exported, schema_version=version))

    def test_action_scope_migrates_and_old_api_and_backup_key_remain_accepted(self):
        store = server.Store(self.folder)
        config = store.backup()['monitoring']['config']
        config['youtube_action'] = 'Synthetic authorized scope'
        config.pop('action_scope')
        with store.connect() as db:
            db.execute('UPDATE monitor_config SET data=? WHERE id=1', (json.dumps(config),))
            db.execute('PRAGMA user_version=1')
        store = server.Store(self.folder)
        with store.connect() as db:
            saved = json.loads(db.execute('SELECT data FROM monitor_config WHERE id=1').fetchone()[0])
        self.assertNotIn('youtube_action', saved)
        self.assertEqual(saved['action_scope'], 'Synthetic authorized scope')
        updated = store.mutate('PATCH', '/api/monitoring', {'youtube_action': 'Legacy API scope'})[1]['config']
        self.assertEqual(updated['action_scope'], 'Legacy API scope')
        self.assertNotIn('youtube_action', updated)
        with self.assertRaisesRegex(server.Invalid, 'disagree'):
            store.mutate('PATCH', '/api/monitoring', {'youtube_action': 'A', 'action_scope': 'B'})
        body = store.backup()
        body['schema_version'] = 1
        body['monitoring']['config']['youtube_action'] = body['monitoring']['config'].pop('action_scope')
        self.assertEqual(store.mutate('POST', '/api/import', body)[0], 200)
        self.assertEqual(store.backup()['monitoring']['config']['action_scope'], 'Legacy API scope')


if __name__ == '__main__':
    unittest.main()
