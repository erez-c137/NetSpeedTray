"""
PR #366 review fixes (v8 step 7). Each test was written red first.

1. An existing v8 file that cannot be opened for writing (read-only file, a held write lock, an OSError
   on the way) must end READ-ONLY with the flyout - history still shown when the file reads - never a
   half-built writer with no connection, never an exception out of run(), and in bounded time.
2. Raw rows flagged FLAG_GAP (one interval spanning standby) carry real bytes but are not samples:
   they must not inflate a summary's coverage or outweigh minute rows; a row's weight is clipped to
   the window.
4. A file held by another program (WinError 32 / a busy checkpoint) is "file in use", not a failure.
5. Quitting while the worker is still migrating / running the first maintenance must never destroy a
   running QThread: quit waits (bounded) for a safe point; past the bound the worker is detached and
   the process exits without destroying it.
"""
import os
import sqlite3
import stat
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from PyQt6.QtCore import QThread

from netspeedtray import constants
from netspeedtray.core import database as dbmod
from netspeedtray.core.database import DatabaseWorker
from netspeedtray.core.db import migrate_v8 as M
from netspeedtray.core.db import schema_v8 as S8
from netspeedtray.core.db.writer_v8 import NetSample
from netspeedtray.core.widget_state import WidgetState
from netspeedtray.tests.legacy_v7_worker import LegacyV7Worker
from netspeedtray.tests.integration import v7_fixture as F
from netspeedtray.tests.state_helpers import prepare_v8

DB = "speed_history.db"


def _v8_with_history(d: Path) -> Path:
    w = DatabaseWorker(d / DB)
    w._prepare_storage()
    now = time.time()
    for i in range(30):
        w._writer.add_net_sample(NetSample(ts=now - 60 + i, dt_ms=1000, byte_deltas={"Wi-Fi": (100, 200)}))
    w._writer.flush(now)
    w._close_connection()
    return d / DB


def _unstarted_state(d: Path) -> WidgetState:
    cfg = constants.config.defaults.DEFAULT_CONFIG.copy()
    with patch.object(QThread, "start", lambda self: None), \
            patch("netspeedtray.core.widget_state.get_app_data_path", return_value=d):
        return WidgetState(cfg)


@pytest.fixture
def fast_retry(monkeypatch):
    monkeypatch.setattr(constants.timers, "MINIMUM_INTERVAL_MS", 1, raising=False)


# --- 1. an existing file that will not open for writing --------------------------------------------

def test_a_read_only_file_ends_read_only_with_history_and_a_flyout(tmp_path, q_app, fast_retry):
    db = _v8_with_history(tmp_path)
    os.chmod(db, stat.S_IREAD)
    try:
        s = _unstarted_state(tmp_path)
        w = s.db_worker
        got = []
        w.migration_deferred.connect(got.append)
        assert w._start_storage() is False
        w.ready.set()
        assert got == [DatabaseWorker.DB_UNAVAILABLE]
        assert w._writer is None and w.conn is None and w._schema_incompatible
        assert w.reader_version == 8 and w.readers_read_only
        now = datetime.now()
        v = s.history_reader.volume(now - timedelta(minutes=5), now, None, 1.0)
        assert v.up_bytes == 3000, "a read-only session must still show the history"
        assert s.get_usage_lifetime() is not None, "the odometer must load (read-only)"
        s.cleanup()
    finally:
        os.chmod(db, stat.S_IREAD | stat.S_IWRITE)


def test_a_held_write_lock_is_read_only_in_bounded_time(tmp_path, q_app):
    db = _v8_with_history(tmp_path)
    holder = sqlite3.connect(db, isolation_level=None)
    holder.execute("PRAGMA journal_mode = WAL")
    holder.execute("BEGIN EXCLUSIVE")                 # another process holding the write lock
    try:
        w = DatabaseWorker(db)
        got = []
        w.migration_deferred.connect(got.append)
        t = time.monotonic()
        assert w._start_storage() is False
        took = time.monotonic() - t
        assert took < 20, f"opening a locked file took {took:.1f} s (the review measured 28.9 s)"
        assert got == [DatabaseWorker.DB_UNAVAILABLE]
        assert w._writer is None and w.conn is None and w.reader_version == 8
    finally:
        holder.execute("ROLLBACK")
        holder.close()


def test_an_oserror_never_escapes_and_ends_read_only(tmp_path, q_app, fast_retry):
    w = DatabaseWorker(tmp_path / "sub" / DB)
    got = []
    w.migration_deferred.connect(got.append)
    with patch.object(Path, "mkdir", side_effect=PermissionError(13, "Access is denied")):
        assert w._start_storage() is False            # must not raise
    assert got == [DatabaseWorker.DB_UNAVAILABLE]
    assert w._writer is None and w.conn is None and w.reader_version is None


def test_a_failure_after_the_writer_was_built_leaves_no_half_built_writer(tmp_path, q_app, fast_retry):
    with patch("netspeedtray.core.db.writer_v8.WriterV8.start_session",
               side_effect=sqlite3.OperationalError("attempt to write a readonly database")):
        w = DatabaseWorker(tmp_path / DB)
        assert w._start_storage() is False
    assert w._writer is None and w.conn is None
    w._execute_task("persist_v8", ([], []))           # dropped, not an AttributeError on conn=None


# --- 2. gap rows ------------------------------------------------------------------------------------

def test_a_gap_row_does_not_fake_coverage_or_outweigh_minutes(tmp_path, q_app):
    s = _unstarted_state(tmp_path)
    w = prepare_v8(s, tmp_path / DB)
    try:
        now = int(time.time())
        # 10 one-second polls, then one interval that spans 8 h of standby (FLAG_GAP, real bytes).
        for i in range(10):
            w._writer.add_net_sample(NetSample(ts=now - 300 + i, dt_ms=1000, byte_deltas={"Wi-Fi": (100, 100)}))
        w._writer.add_net_sample(NetSample(ts=now - 100, dt_ms=8 * 3600 * 1000,
                                           byte_deltas={"Wi-Fi": (9_000_000, 9_000_000)}))
        w._writer.flush(now)
        assert w.conn.execute("SELECT COUNT(*) FROM net_raw WHERE flags & 2").fetchone()[0] == 1
        st, et = datetime.fromtimestamp(now - 3600), datetime.fromtimestamp(now)
        summ = s.summarize_network("upload", st, et, None, 1.0)
        assert summ.coverage_pct == pytest.approx(10 / 3600 * 100, abs=0.1), "the gap row faked coverage"
        assert summ.count == 10 and summ.avg == pytest.approx(100.0)
        # > 24 h: a legacy minute (60 s) and the gap row - the gap must not outweigh the minute.
        old = (now - 3 * 86400) // 60 * 60
        iid = w.conn.execute("SELECT iface_id FROM interfaces").fetchone()[0]
        w.conn.execute("INSERT INTO net_minute (ts, iface_id, up_avg, down_avg, up_max, down_max, sample_count) "
                       "VALUES (?, ?, 1000, 1000, 1000, 1000, 60)", (old, iid))
        summ2 = s.summarize_network("upload", datetime.fromtimestamp(old - 60), et, None, 1.0)
        assert summ2.avg == pytest.approx((1000 * 60 + 100 * 10) / 70)
        # The bytes are not lost: volume still counts the gap row.
        assert s.get_total_bandwidth_for_period(st, et)[0] == 1000 + 9_000_000
    finally:
        w._close_connection()
        s.cleanup()


def test_a_raw_rows_weight_is_clipped_to_the_window(tmp_path, q_app):
    s = _unstarted_state(tmp_path)
    w = prepare_v8(s, tmp_path / DB)
    try:
        now = int(time.time())
        # A 10 s interval ending 4 s after the window starts: only 4 of its seconds are in the window.
        w._writer.add_net_sample(NetSample(ts=now - 600 + 4, dt_ms=10_000, byte_deltas={"Wi-Fi": (1000, 1000)}))
        w._writer.flush(now)
        summ = s.summarize_network("upload", datetime.fromtimestamp(now - 600), datetime.fromtimestamp(now), None, 10.0)
        assert summ.coverage_pct == pytest.approx(4 / 600 * 100, abs=0.06)
    finally:
        w._close_connection()
        s.cleanup()


# --- 4. a held file is "in use" ---------------------------------------------------------------------

def _make_v7(d: Path) -> Path:
    db = d / DB
    w = LegacyV7Worker(db)
    w._initialize_connection()
    w._check_and_create_schema()
    F.populate(w.conn)
    w._close_connection()
    return db


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing semantics")
def test_a_held_wal_is_file_in_use(tmp_path, q_app):
    db = _make_v7(tmp_path)
    wal = Path(str(db) + "-wal")
    wal.write_bytes(b"")
    with open(wal, "rb"):                              # another program holding the -wal (WinError 32)
        w = DatabaseWorker(db)
        got = []
        w.migration_deferred.connect(got.append)
        w._prepare_storage()
    assert got == [DatabaseWorker.MIGRATION_FILE_IN_USE]
    assert w.reader_version == 7
    w2 = DatabaseWorker(db)                            # released: the next launch finishes the swap
    w2._prepare_storage()
    try:
        assert w2.reader_version == 8
    finally:
        w2._close_connection()


def test_a_busy_checkpoint_is_file_in_use(tmp_path, q_app):
    db = _make_v7(tmp_path)
    other = sqlite3.connect(db)                        # another connection mid-read holds the WAL back
    other.execute("PRAGMA journal_mode = WAL")
    other.execute("INSERT INTO metadata (key, value) VALUES ('x', '1')")
    other.commit()
    cur = other.execute("SELECT * FROM speed_history_raw")
    cur.fetchone()
    try:
        w = DatabaseWorker(db)
        got = []
        w.migration_deferred.connect(got.append)
        w._prepare_storage()
        assert got == [DatabaseWorker.MIGRATION_FILE_IN_USE]
    finally:
        cur.close()
        other.close()


# --- 5. quitting during a long startup --------------------------------------------------------------

def test_stop_requested_skips_the_post_migration_maintenance(tmp_path, q_app):
    _make_v7(tmp_path)
    w = DatabaseWorker(tmp_path / DB)
    w._stop_event.set()
    with patch("netspeedtray.core.db.writer_v8.WriterV8.run_maintenance") as rm:
        w._prepare_storage()
    try:
        rm.assert_not_called()                         # the next start's hourly pass does it
        assert w.reader_version == 8
    finally:
        w._close_connection()


def _slow_prepare(seconds):
    real = DatabaseWorker._prepare_storage

    def slow(self):
        time.sleep(seconds)                            # stands in for a migration of a large file
        return real(self)
    return slow


def test_quit_waits_for_a_slow_startup_to_finish(tmp_path, q_app):
    with patch.object(DatabaseWorker, "_prepare_storage", _slow_prepare(3.0)), \
            patch("netspeedtray.core.widget_state.get_app_data_path", return_value=tmp_path):
        s = WidgetState(constants.config.defaults.DEFAULT_CONFIG.copy())
        time.sleep(0.2)
        s.cleanup()                                    # quit while the worker is still "migrating"
    assert not s.db_worker.isRunning(), "cleanup returned with the worker still running"
    assert not dbmod.detached_workers_running()


def test_past_the_bound_the_worker_is_detached_not_destroyed(tmp_path, q_app, monkeypatch):
    monkeypatch.setattr(constants.timeouts, "DB_SHUTDOWN_WAIT_MS", 300, raising=False)
    with patch.object(DatabaseWorker, "_prepare_storage", _slow_prepare(2.0)), \
            patch("netspeedtray.core.widget_state.get_app_data_path", return_value=tmp_path):
        s = WidgetState(constants.config.defaults.DEFAULT_CONFIG.copy())
        time.sleep(0.1)
        t = time.monotonic()
        s.cleanup()
        assert time.monotonic() - t < 1.5
        assert dbmod.detached_workers_running(), "a still-running worker must be kept alive, not destroyed"
        assert s.db_worker in dbmod._DETACHED_WORKERS
        assert s.db_worker.wait(10_000)                # it finishes on its own
    assert not dbmod.detached_workers_running()
    dbmod._DETACHED_WORKERS.clear()


def test_monitor_hard_exits_only_while_a_worker_is_detached(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "nst_monitor_main", Path(__file__).resolve().parents[3] / "monitor.py")
    mon = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mon)
    exits = []
    monkeypatch.setattr(mon.os, "_exit", lambda code: exits.append(code))
    monkeypatch.setattr(dbmod, "detached_workers_running", lambda: False)
    assert mon._finish(3) == 3 and exits == []
    monkeypatch.setattr(dbmod, "detached_workers_running", lambda: True)
    mon._finish(4)
    assert exits == [4]
