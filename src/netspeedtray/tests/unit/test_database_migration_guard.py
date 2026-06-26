"""
Regression tests for the schema migration data-loss guard (M3).

A failed migration on an EXISTING database must NEVER fall through to the destructive
fresh-build (DROP TABLE) path — that would wipe a user's history. These exercise
_check_and_create_schema / _has_existing_data via the real (unbound) methods bound to a
lightweight fake holding a real in-memory sqlite connection.
"""
import sqlite3
import types
import logging
from unittest.mock import MagicMock

from netspeedtray import constants
from netspeedtray.core.database import DatabaseWorker

RAW = constants.data.SPEED_TABLE_RAW


def _fake_db(version: int, with_data: bool):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO metadata (key, value) VALUES ('db_version', ?)", (str(version),))
    conn.execute(f"CREATE TABLE {RAW} (timestamp INTEGER, interface_name TEXT, upload REAL, download REAL)")
    if with_data:
        conn.execute(f"INSERT INTO {RAW} VALUES (1, 'Ethernet', 10.0, 20.0)")
    conn.commit()

    fake = types.SimpleNamespace()
    fake.conn = conn
    fake._DB_VERSION = 6
    fake.logger = logging.getLogger("test.db")
    # Bind the real methods under test to the fake.
    fake._has_existing_data = DatabaseWorker._has_existing_data.__get__(fake)
    fake._get_current_db_version = DatabaseWorker._get_current_db_version.__get__(fake)
    return fake, conn


def _row_count(conn):
    return conn.execute(f"SELECT COUNT(*) FROM {RAW}").fetchone()[0]


def test_failed_migration_preserves_existing_data():
    fake, conn = _fake_db(version=3, with_data=True)
    # Migration blows up.
    fake._migrate_schema = MagicMock(side_effect=RuntimeError("boom"))
    DatabaseWorker._check_and_create_schema(fake)
    # The row (and the metadata version) must survive — NO fresh build / DROP.
    assert _row_count(conn) == 1
    assert conn.execute("SELECT value FROM metadata WHERE key='db_version'").fetchone()[0] == "3"


def test_fresh_build_refused_when_data_present_but_version_reads_zero():
    fake, conn = _fake_db(version=3, with_data=True)
    # Simulate the version-read glitch: it reports a brand-new DB (0)...
    fake._get_current_db_version = lambda: 0
    fake._migrate_schema = MagicMock()
    DatabaseWorker._check_and_create_schema(fake)
    # ...but the safety net sees real data and refuses to rebuild.
    assert _row_count(conn) == 1
    fake._migrate_schema.assert_not_called()


def test_has_existing_data_true_with_rows_false_when_empty():
    fake_data, _ = _fake_db(version=6, with_data=True)
    assert fake_data._has_existing_data() is True
    fake_empty, _ = _fake_db(version=6, with_data=False)
    assert fake_empty._has_existing_data() is False
