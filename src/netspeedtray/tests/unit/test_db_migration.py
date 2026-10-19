
import os
import shutil
import sqlite3
import tempfile
import unittest
import sys
from pathlib import Path

# Add src to path
sys.path.append(os.path.abspath("src"))
from unittest.mock import MagicMock, patch

from netspeedtray.core.widget_state import DatabaseWorker

class TestDatabaseMigration(unittest.TestCase):
    def setUp(self):
        # Create a temp dir
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / "test_speed_history.db"
        # DatabaseWorker instance
        self.worker = DatabaseWorker(self.db_path)

    def tearDown(self):
        self.worker.stop()
        if self.worker.conn:
            self.worker.conn.close()
        shutil.rmtree(self.temp_dir)

    def _create_v2_schema(self, conn):
        """Creates the legacy v2 schema (before any v3 changes).

        A real v2 DB has all three speed tiers - the v2->v3 migration builds covering
        indexes on the minute/hour tables, so they must exist for it to run. (Previously
        only raw was created, so the migration failed and the old code silently
        fresh-built over it - the exact data-loss bug now guarded against.)
        """
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
        cursor.execute("INSERT INTO metadata (key, value) VALUES ('db_version', '2')")
        cursor.execute("CREATE TABLE speed_history_raw (timestamp INTEGER, interface_name TEXT, upload_bytes_sec REAL, download_bytes_sec REAL)")
        cursor.execute("CREATE TABLE speed_history_minute (timestamp INTEGER, interface_name TEXT, upload_avg REAL, download_avg REAL)")
        cursor.execute("CREATE TABLE speed_history_hour (timestamp INTEGER, interface_name TEXT, upload_avg REAL, download_avg REAL)")
        conn.commit()

    def test_backup_database_success(self):
        # Setup DB
        self.worker._initialize_connection()
        self._create_v2_schema(self.worker.conn)
        
        # Run backup
        success = self.worker._backup_database()
        self.assertTrue(success)
        
        # Check if file exists
        backups = list(Path(self.temp_dir).glob("*.bak.*"))
        self.assertEqual(len(backups), 1)
        self.assertTrue("v2" in backups[0].name)

    def test_get_current_db_version(self):
        self.worker._initialize_connection()
        self._create_v2_schema(self.worker.conn)
        
        ver = self.worker._get_current_db_version()
        self.assertEqual(ver, 2)

    def test_migrate_schema_call(self):
        """Test that _migrate_schema calls the correct migration methods."""
        # Setup: Current DB is v2. Target in class is currently v2, so we override it for test.
        self.worker._DB_VERSION = 3 
        self.worker._migrate_v2_to_v3 = MagicMock() # Mock the specific step
        
        self.worker._initialize_connection()
        self._create_v2_schema(self.worker.conn)
        
        # Run Migration framework
        self.worker._migrate_schema(2, 3)
        
        # Assertions
        self.worker._migrate_v2_to_v3.assert_called_once()
        
        # Check DB version updated to 3
        cursor = self.worker.conn.cursor()
        cursor.execute("SELECT value FROM metadata WHERE key='db_version'")
        new_ver = int(cursor.fetchone()[0])
        self.assertEqual(new_ver, 3)
        
    def test_no_migration_needed(self):
        """A file already at this build's version (v8) is opened as is - no migration."""
        self.worker._prepare_storage()            # fresh install -> v8
        self.worker._close_connection()
        w2 = DatabaseWorker(self.db_path)
        with patch("netspeedtray.core.db.migrate_v8.migrate") as mock_migrate,              patch.object(w2, '_migrate_schema') as mock_chain:
            w2._prepare_storage()
            mock_migrate.assert_not_called()
            mock_chain.assert_not_called()
        self.assertEqual(w2.reader_version, 8)
        w2._close_connection()

    def test_v3_migration_execution(self):
        """The actual v2 -> v3 step runs without error (the legacy chain's own connection)."""
        conn = sqlite3.connect(self.db_path)
        self._create_v2_schema(conn)
        conn.close()
        self.worker.conn = sqlite3.connect(self.db_path)
        self.worker._migrate_schema(2, 3)
        cursor = self.worker.conn.cursor()
        cursor.execute("SELECT value FROM metadata WHERE key='db_version'")
        self.assertEqual(int(cursor.fetchone()[0]), 3)

if __name__ == '__main__':
    unittest.main()
