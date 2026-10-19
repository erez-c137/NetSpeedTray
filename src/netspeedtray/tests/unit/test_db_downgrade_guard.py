"""
Downgrade guard: what happens when an OLDER build opens a NEWER database.

This is the 2.2.0-beta rollback path. A beta tester migrates their database to a
newer schema, dislikes the beta, and reinstalls the previous release - which is
exactly what beta testers do, and GitHub/WinGet keep every prior version
downloadable forever.

Before the guard, that produced two silent failures:

  1. `_migrate_schema` ran `_backup_database()` unconditionally, but the loop
     `range(current_version, _DB_VERSION)` is EMPTY on a downgrade. So a full copy
     of the database was written to a new `.bak` on EVERY launch, and nothing in
     the codebase ever deletes one.
  2. Reads kept working - a newer schema may expose compatibility views - while
     every write raised `OperationalError`. That is a `sqlite3.Error` subclass,
     and every handler in the worker swallows it. The app looked completely
     healthy and recorded nothing, permanently, with no user-visible signal.

Since 2.2 (schema v8) the guard runs inside ``_prepare_storage``, BEFORE any write connection
exists: a newer file is never opened read-write at all.

These tests assert the guard *actually holds*, not merely that it logged. Note
the sibling lesson in `test_db_migration.test_backup_database_success`, which
asserts a backup file exists but never opens it - the same blind spot that let
`WinError 5` through on the portable updater.
"""
import sqlite3
import sys
import os
from pathlib import Path

import pytest

sys.path.append(os.path.abspath("src"))
import hashlib

from netspeedtray.core.widget_state import DatabaseWorker


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_newer_db(path: Path, version: int) -> None:
    """A database claiming a schema version this build does not understand."""
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
    cur.execute("INSERT INTO metadata (key, value) VALUES ('db_version', ?)", (str(version),))
    # A real future schema; the shape does not matter, only that this build
    # cannot write it. A view stands in for a v8 compatibility view.
    cur.execute("CREATE TABLE speed_raw (ts INTEGER, iface_id INTEGER, up_bytes INTEGER)")
    cur.execute("INSERT INTO speed_raw VALUES (1787000000, 1, 4242)")
    cur.execute(
        "CREATE VIEW speed_history_raw AS "
        "SELECT ts AS timestamp, 'Wi-Fi' AS interface_name, "
        "up_bytes AS upload_bytes_sec, up_bytes AS download_bytes_sec FROM speed_raw"
    )
    conn.commit()
    conn.close()


@pytest.fixture
def worker(tmp_path):
    db = tmp_path / "speed_history.db"
    w = DatabaseWorker(db)
    yield w
    if w.conn:
        w.conn.close()


def test_newer_db_is_detected_and_flagged(worker, tmp_path):
    _make_newer_db(worker.db_path, version=worker._DB_VERSION + 1)
    worker._prepare_storage()
    assert worker._schema_incompatible is True


def test_newer_db_writes_no_backup(worker, tmp_path):
    """The bug that filled disks: a full .bak on every single launch."""
    _make_newer_db(worker.db_path, version=worker._DB_VERSION + 1)
    before = _sha(worker.db_path)
    for _ in range(5):                      # five launches
        worker._prepare_storage()
        assert worker.conn is None, "a newer file must never get a write connection"
    assert list(tmp_path.glob("*.bak*")) == []
    assert _sha(worker.db_path) == before


def test_newer_db_refuses_every_write_task(worker):
    """Reads may still work; writes must be refused, not left to fail silently."""
    _make_newer_db(worker.db_path, version=worker._DB_VERSION + 1)
    worker._prepare_storage()

    before = _sha(worker.db_path)
    for task, data in (("persist_v8", ([], [(1787000001, "cpu", 1.0)])),
                       ("persist_usage", (1.0, 2.0, 0.0, 0.0, "2026-10-01", 1)),
                       ("maintenance", {}), ("set_poll_ms", 2000)):
        # Must not raise, and must not reach a handler.
        worker._execute_task(task, data)
    assert _sha(worker.db_path) == before

    # The user's data is untouched: no new rows, and the original row survives.
    conn = sqlite3.connect(f"file:{worker.db_path}?mode=ro", uri=True)
    assert conn.execute("SELECT COUNT(*) FROM speed_raw").fetchone()[0] == 1
    conn.close()


def test_flush_barrier_still_completes_on_newer_db(worker):
    """A reader waiting on the flush barrier must not hang forever."""
    import threading

    _make_newer_db(worker.db_path, version=worker._DB_VERSION + 1)
    worker._prepare_storage()

    event = threading.Event()
    worker._execute_task("__signal__", event)
    assert event.is_set(), "the flush barrier must be honoured even when writes are refused"


def test_matching_version_is_untouched(worker):
    """The guard must not fire on the version this build actually understands."""
    worker._prepare_storage()          # builds a fresh, current schema
    assert worker._schema_incompatible is False
    assert worker.reader_version == worker._DB_VERSION == 8 and worker.readers_read_only is False
    worker._close_connection()


def test_older_db_still_migrates(worker, tmp_path):
    """Guarding downgrades must not break the upgrade path it sits next to: a v2 file goes through the
    in-place chain to v7 (verified backup first) and then the v8 rebuild - one launch, both backups."""
    conn = sqlite3.connect(worker.db_path)
    cur = conn.cursor()
    cur.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
    cur.execute("INSERT INTO metadata (key, value) VALUES ('db_version', '2')")
    cur.execute("CREATE TABLE speed_history_raw (timestamp INTEGER NOT NULL, interface_name TEXT NOT NULL, "
                "upload_bytes_sec REAL NOT NULL, download_bytes_sec REAL NOT NULL, "
                "PRIMARY KEY (timestamp, interface_name))")
    for tier in ("minute", "hour"):
        cur.execute(
            f"CREATE TABLE speed_history_{tier} (timestamp INTEGER NOT NULL, interface_name TEXT NOT NULL, "
            "upload_avg REAL NOT NULL, download_avg REAL NOT NULL, upload_max REAL NOT NULL, "
            "download_max REAL NOT NULL, PRIMARY KEY (timestamp, interface_name))")
    cur.execute("INSERT INTO speed_history_hour VALUES (1699999200, 'Wi-Fi', 10.0, 20.0, 30.0, 40.0)")
    conn.commit()
    conn.close()

    worker._prepare_storage()

    assert worker._schema_incompatible is False
    assert worker._get_current_db_version() == worker._DB_VERSION == 8
    assert worker.migration_result is not None and worker.migration_result.status == "migrated"
    assert sorted(p.name.split("_")[1].split(".")[-1] for p in tmp_path.glob("*.bak.v*")) == ["v2", "v7"]
    assert worker.conn.execute("SELECT COUNT(*) FROM net_hour").fetchone()[0] == 1
    worker._close_connection()
