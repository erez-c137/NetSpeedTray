
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
        """Creates the legacy v2 schema (before any v3 changes)."""
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
        cursor.execute("INSERT INTO metadata (key, value) VALUES ('db_version', '2')")
        cursor.execute("CREATE TABLE speed_history_raw (timestamp INTEGER, interface_name TEXT, upload_bytes_sec REAL, download_bytes_sec REAL)")
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
        self.worker._migrate_schema(2)
        
        # Assertions
        self.worker._migrate_v2_to_v3.assert_called_once()
        
        # Check DB version updated to 3
        cursor = self.worker.conn.cursor()
        cursor.execute("SELECT value FROM metadata WHERE key='db_version'")
        new_ver = int(cursor.fetchone()[0])
        self.assertEqual(new_ver, 3)
        
    def test_no_migration_needed(self):
        """Ensure migration is skipped if versions match."""
        # Current _DB_VERSION is 3
        self.worker._initialize_connection()
        
        # Create metadata with current target version
        cursor = self.worker.conn.cursor()
        cursor.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
        cursor.execute(f"INSERT INTO metadata (key, value) VALUES ('db_version', '{self.worker._DB_VERSION}')")
        self.worker.conn.commit()
        
        with patch.object(self.worker, '_migrate_schema') as mock_migrate:
            self.worker._check_and_create_schema()
            mock_migrate.assert_not_called()

    def test_v3_migration_execution(self):
        """Test that the actual v3 migration method executes without error."""
        # Setup: Ensure we are starting with v2
        self.worker._initialize_connection()
        self._create_v2_schema(self.worker.conn)
        
        # Override the target version to 3 for this test instance
        self.worker._DB_VERSION = 3
        
        # We generally DO NOT mock the migration method here because we want to test
        # the *actual* implementation of _migrate_v2_to_v3 (even if it's just logging).
        
        # Act: Run the full schema check which triggers migration
        self.worker._check_and_create_schema()
        
        # Assert: Check DB version is now 3
        cursor = self.worker.conn.cursor()
        cursor.execute("SELECT value FROM metadata WHERE key='db_version'")
        new_ver = int(cursor.fetchone()[0])
        self.assertEqual(new_ver, 3)

if __name__ == '__main__':
    unittest.main()
