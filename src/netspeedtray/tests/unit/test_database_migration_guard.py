"""
Regression tests for the schema migration data-loss guard (M3), on the 2.2 (v8) startup path.

A failed migration on an EXISTING database must NEVER fall through to the destructive fresh-build
path - that would wipe a user's history. Since step 7 the decision is made in
``DatabaseWorker._prepare_storage`` before any write connection exists; a failure leaves the session
read-only and the file byte-for-byte as it was.
"""
import hashlib
import sqlite3
import types
import logging
from unittest.mock import MagicMock, patch

from netspeedtray import constants
from netspeedtray.core.database import DatabaseWorker

RAW = constants.data.SPEED_TABLE_RAW


def _sha(p) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _file_db(path, version, with_data: bool = True):
    conn = sqlite3.connect(path)
    if version is not None:
        conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO metadata (key, value) VALUES ('db_version', ?)", (str(version),))
    conn.execute(f"CREATE TABLE {RAW} (timestamp INTEGER, interface_name TEXT, upload_bytes_sec REAL, "
                 "download_bytes_sec REAL)")
    if with_data:
        conn.execute(f"INSERT INTO {RAW} VALUES (1, 'Ethernet', 10.0, 20.0)")
    conn.commit()
    conn.close()


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
    fake.logger = logging.getLogger("test.db")
    fake._has_existing_data = DatabaseWorker._has_existing_data.__get__(fake)
    fake._get_current_db_version = DatabaseWorker._get_current_db_version.__get__(fake)
    return fake, conn


def test_failed_migration_preserves_existing_data(tmp_path, q_app):
    """The v3 -> v7 chain blows up: read-only, nothing rebuilt, the file untouched."""
    db = tmp_path / "speed_history.db"
    _file_db(db, 3)
    before = _sha(db)
    w = DatabaseWorker(db)
    with patch.object(DatabaseWorker, "_run_legacy_chain", side_effect=RuntimeError("boom")):
        w._prepare_storage()
    assert w._schema_incompatible and w._migration_deferred == DatabaseWorker.MIGRATION_FAILED
    assert w.conn is None and _sha(db) == before


def test_failed_v8_migration_preserves_existing_data(tmp_path, q_app):
    db = tmp_path / "speed_history.db"
    _file_db(db, 7)
    before = _sha(db)
    w = DatabaseWorker(db)
    with patch("netspeedtray.core.db.migrate_v8.migrate", side_effect=RuntimeError("boom")):
        w._prepare_storage()
    assert w._schema_incompatible and w._migration_deferred == DatabaseWorker.MIGRATION_FAILED
    assert w.reader_version == 7 and w.readers_read_only
    assert w.conn is None and _sha(db) == before


def test_fresh_build_refused_when_data_present_but_version_reads_zero(tmp_path, q_app):
    """History rows but no schema version: never a fresh build, never moved aside - read-only."""
    db = tmp_path / "speed_history.db"
    _file_db(db, None)
    before = _sha(db)
    w = DatabaseWorker(db)
    with patch("netspeedtray.core.db.migrate_v8.migrate") as mig:
        w._prepare_storage()
    mig.assert_not_called()
    assert w._schema_incompatible and w._migration_deferred == DatabaseWorker.MIGRATION_UNREADABLE
    assert _sha(db) == before and sorted(p.name for p in tmp_path.iterdir()) == ["speed_history.db"]


def test_an_empty_unversioned_file_is_moved_aside_not_deleted(tmp_path, q_app):
    db = tmp_path / "speed_history.db"
    _file_db(db, None, with_data=False)
    before = _sha(db)
    w = DatabaseWorker(db)
    w._prepare_storage()
    try:
        assert not w._schema_incompatible and w._get_current_db_version() == 8
        aside = [p for p in tmp_path.iterdir() if ".stray_" in p.name and not p.name.endswith(("-wal", "-shm"))]
        assert len(aside) == 1 and _sha(aside[0]) == before
    finally:
        w._close_connection()


def test_has_existing_data_true_with_rows_false_when_empty():
    fake_data, _ = _fake_db(version=6, with_data=True)
    assert fake_data._has_existing_data() is True
    fake_empty, _ = _fake_db(version=6, with_data=False)
    assert fake_empty._has_existing_data() is False
