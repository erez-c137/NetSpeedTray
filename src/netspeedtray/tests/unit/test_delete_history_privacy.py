"""
Privacy review 2.2 (finding 1) and data-integrity review (A, B): what "Delete all history" leaves behind.

  * Backups. The v8 upgrade keeps the whole 2.1 file as ``speed_history.db.bak.v7_<ts>`` and nothing
    ever removed it - Delete all history emptied the live file and left every pre-upgrade row (and
    every adapter name) next to it. Now the delete zeroes and removes every ``speed_history.db.bak*``
    file (sidecars included); a backup it cannot remove yet (locked) is retried like a busy checkpoint.
    Harness: v7 fixture -> migrate -> delete -> no .bak file and no adapter name in ANY file's bytes.
  * Backups also age out with "Keep history for": maintenance removes one older than the retention
    window, never sooner than BACKUP_MIN_KEEP_DAYS after it was made; "Keep everything" keeps them.
  * The odometer's counter snapshot (``usage_counter_snapshot``: adapter names + NIC counters) goes
    with the usage reset, in the delete's own transaction - the next same-boot launch then simply
    starts fresh (no phantom bytes).
  * A: a FAILED delete never resets the data-cap counter (it used to zero it in memory and on disk
    straight after queueing the delete, without waiting for the answer).
  * B: a usage reset while the file is still opening drops the polls buffered before it (they used to
    be replayed into the fresh counter: 5 x 100 MB -> 500000001 after the first poll).
"""
import os
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest
from PyQt6.QtCore import QThread

from netspeedtray import constants
from netspeedtray.core import usage_snapshot
from netspeedtray.core.database import DatabaseWorker
from netspeedtray.core.db import migrate_v8 as M
from netspeedtray.core.db import schema_v8 as S8
from netspeedtray.core.db import writer_v8
from netspeedtray.core.widget_state import WidgetState
from netspeedtray.tests.integration import v7_fixture as F
from netspeedtray.tests.legacy_v7_worker import LegacyV7Worker
from netspeedtray.tests.state_helpers import prepare_v8

DB = "speed_history.db"
D = constants.data
SNAP = constants.network.usage.SNAPSHOT_METADATA_KEY
ADAPTER_NAMES = [n.encode("utf-8") for n in F.IFACES]


@pytest.fixture
def no_retry_sleep(monkeypatch):
    monkeypatch.setattr(constants.timeouts, "DB_CHECKPOINT_RETRY_SLEEP", 0.0)


def _make_v7(d: Path, usage=None) -> Path:
    db = d / DB
    w = LegacyV7Worker(db)
    w._initialize_connection()
    w._check_and_create_schema()
    w._ensure_indexes()
    F.populate(w.conn)
    if usage:
        w.conn.execute("INSERT OR REPLACE INTO usage_counter (id, cumulative_up, cumulative_down, anchor_up, "
                       "anchor_down, period_key, updated_ts) VALUES (1, ?, ?, ?, ?, '2026-10-01', 0)", usage)
        w.conn.commit()
    w._close_connection()
    return db


def _migrated_worker(d: Path) -> DatabaseWorker:
    _make_v7(d)
    w = DatabaseWorker(d / DB)
    w._prepare_storage()            # recover -> migrate (renames the v7 file to .bak.v7_<ts>) -> open
    assert w.migration_result is not None and w.migration_result.status == "migrated"
    assert list(d.glob(f"{DB}.bak.v7_*")), "the fixture must start with the upgrade's backup"
    return w


def _bak_files(d: Path):
    return sorted(p.name for p in d.glob(f"{DB}.bak*"))


def _names_on_disk(d: Path):
    """(file, adapter name) for every adapter name found in the raw bytes of ANY file in the folder."""
    hits = []
    for p in d.iterdir():
        if p.is_file():
            data = p.read_bytes()
            hits += [(p.name, n.decode()) for n in ADAPTER_NAMES if n in data]
    return hits


# --- finding 1: the backup goes with the history ---------------------------------------------------------

def test_delete_with_usage_reset_leaves_no_backup_and_no_adapter_name_anywhere(tmp_path, no_retry_sleep):
    w = _migrated_worker(tmp_path)
    assert _names_on_disk(tmp_path), "the fixture must carry adapter names before the delete"
    got = []
    w.history_deleted.connect(got.append)
    w._execute_task("delete_history", {"include_usage": True})
    assert got == [DatabaseWorker.DELETE_DONE]
    assert _bak_files(tmp_path) == [], "Delete all history left a database backup behind"
    w._close_connection()
    assert _names_on_disk(tmp_path) == []


def test_delete_without_usage_reset_still_removes_the_backups(tmp_path, no_retry_sleep):
    w = _migrated_worker(tmp_path)
    # A backup someone opened in a SQLite tool has sidecars; they hold its pages too.
    bak = next(tmp_path.glob(f"{DB}.bak.v7_*"))
    Path(str(bak) + "-wal").write_bytes(b"Tailscale" * 100)
    Path(str(bak) + "-shm").write_bytes(b"\0" * 64)
    w._execute_task("delete_history", {"include_usage": False})
    assert _bak_files(tmp_path) == []
    # Kept by design (D5): the odometer and the counter snapshot paired with it.
    assert w.conn.execute("SELECT 1 FROM metadata WHERE key = ?", (SNAP,)).fetchone() is not None
    w._close_connection()


def test_a_locked_backup_is_reported_pending_and_removed_on_the_idle_retry(tmp_path, no_retry_sleep):
    w = _migrated_worker(tmp_path)
    bak = next(tmp_path.glob(f"{DB}.bak.v7_*"))
    got = []
    w.history_deleted.connect(got.append)
    with open(bak, "rb"):           # Windows: an open handle without FILE_SHARE_DELETE blocks the delete
        w._execute_task("delete_history", {"include_usage": False})
        assert bak.exists(), "the open handle should have blocked the removal"
        assert got == [DatabaseWorker.DELETE_COMPACT_PENDING], "a left-behind backup is not 'done'"
        assert w._compact_pending and w._backups_pending
    w._retry_pending_compaction(force=True)
    assert _bak_files(tmp_path) == []
    assert not w._compact_pending
    w._close_connection()


def test_a_locked_backup_is_removed_at_the_next_start_after_a_quit(tmp_path, no_retry_sleep):
    w = _migrated_worker(tmp_path)
    bak = next(tmp_path.glob(f"{DB}.bak.v7_*"))
    with open(bak, "rb"):
        w._execute_task("delete_history", {"include_usage": False})
    w.conn.close()                  # killed before the idle retry
    w.conn = None
    w2 = DatabaseWorker(tmp_path / DB)
    w2._prepare_storage()
    assert _bak_files(tmp_path) == []
    assert w2.conn.execute("SELECT 1 FROM metadata WHERE key = ?",
                           (DatabaseWorker._COMPACT_MARKER_KEY,)).fetchone() is None
    w2._close_connection()


def test_a_2_1_compaction_marker_never_removes_the_upgrade_backup(tmp_path):
    """2.1.x writes pending_history_compaction = '1' and the migration copies it over; resuming that
    compaction at the first 2.2 start must not take the brand-new v7 backup with it."""
    _make_v7(tmp_path)
    c = sqlite3.connect(tmp_path / DB)
    assert c.execute("SELECT value FROM metadata WHERE key = 'pending_history_compaction'").fetchone() == ("1",)
    c.close()
    w = DatabaseWorker(tmp_path / DB)
    w._prepare_storage()
    assert len(list(tmp_path.glob(f"{DB}.bak.v7_*"))) == 1
    w._close_connection()


def test_backups_are_kept_while_a_migration_journal_exists(tmp_path, no_retry_sleep):
    w = _migrated_worker(tmp_path)
    before = _bak_files(tmp_path)
    M.journal_path_for(tmp_path / DB).write_text("{}")
    w._execute_task("delete_history", {"include_usage": False})
    assert _bak_files(tmp_path) == before, "recover() needs the v7 backup while a journal exists"
    w._close_connection()


# --- backups age out with "Keep history for" -------------------------------------------------------------

def _fake_bak(d: Path, version: int, when: datetime) -> Path:
    p = d / f"{DB}.bak.v{version}_{when:%Y%m%d_%H%M%S}"
    p.write_bytes(b"x")
    return p


def test_backups_age_out_with_the_retention_window(tmp_path):
    w = DatabaseWorker(tmp_path / DB)
    w._prepare_storage()
    now = datetime(2026, 10, 20, 12, 0, 0)
    old = _fake_bak(tmp_path, 7, now - timedelta(days=400))
    Path(str(old) + "-wal").write_bytes(b"x")
    young = _fake_bak(tmp_path, 6, now - timedelta(days=100))
    w._age_out_backups(365, now)
    assert not old.exists() and not Path(str(old) + "-wal").exists()
    assert young.exists()
    w._close_connection()


def test_a_fresh_backup_survives_a_short_retention_for_the_rollback_window(tmp_path):
    w = DatabaseWorker(tmp_path / DB)
    w._prepare_storage()
    now = datetime(2026, 10, 20, 12, 0, 0)
    recent = _fake_bak(tmp_path, 7, now - timedelta(days=D.retention.BACKUP_MIN_KEEP_DAYS - 5))
    stale = _fake_bak(tmp_path, 6, now - timedelta(days=D.retention.BACKUP_MIN_KEEP_DAYS + 5))
    w._age_out_backups(1, now)      # a hand-edited 1-day retention
    assert recent.exists(), "never removed sooner than BACKUP_MIN_KEEP_DAYS after the upgrade"
    assert not stale.exists()
    w._close_connection()


def test_keep_everything_keeps_every_backup(tmp_path):
    w = DatabaseWorker(tmp_path / DB)
    w._prepare_storage()
    now = datetime(2026, 10, 20, 12, 0, 0)
    ancient = _fake_bak(tmp_path, 7, now - timedelta(days=3000))
    w._age_out_backups(D.retention.UNLIMITED_DAYS, now)
    assert ancient.exists()
    w._close_connection()


def test_the_hourly_maintenance_ages_backups_out(tmp_path):
    w = DatabaseWorker(tmp_path / DB)
    w._prepare_storage()
    now = datetime.now()
    old = _fake_bak(tmp_path, 7, now - timedelta(days=500))
    w._run_maintenance({"keep_data": 365}, now=now)
    assert not old.exists()
    w._close_connection()


# --- the counter snapshot goes with the usage reset --------------------------------------------------------

def test_usage_reset_clears_the_counter_snapshot_and_the_next_launch_recovers_nothing(tmp_path, no_retry_sleep):
    w = _migrated_worker(tmp_path)
    boot = 1_789_000_000.0
    snap = usage_snapshot.encode_snapshot({"Wi-Fi 3": NS(bytes_sent=100, bytes_recv=200)}, boot + 10, boot)
    w.conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)", (SNAP, snap))
    w._execute_task("delete_history", {"include_usage": True})
    assert w.conn.execute("SELECT 1 FROM metadata WHERE key = ?", (SNAP,)).fetchone() is None
    assert w.conn.execute(f"SELECT COUNT(*) FROM {D.USAGE_COUNTER_TABLE}").fetchone()[0] == 0
    w._close_connection()
    # The next same-boot launch: counters moved 5 GB since that snapshot; none of it may be re-added.
    c = sqlite3.connect(f"file:{(tmp_path / DB).as_posix()}?mode=ro", uri=True)
    try:
        row = c.execute("SELECT value FROM metadata WHERE key = ?", (SNAP,)).fetchone()
    finally:
        c.close()
    stored = usage_snapshot.decode_snapshot(row[0] if row else None)
    deltas, reason = usage_snapshot.reconcile_deltas(
        stored, {"Wi-Fi 3": NS(bytes_sent=5_000_000_100, bytes_recv=5_000_000_200)}, boot, boot + 3600)
    assert deltas == {} and reason == "no snapshot"


# --- A / B: the odometer reset follows the delete's answer -------------------------------------------------

def _state(d: Path) -> WidgetState:
    cfg = constants.config.defaults.DEFAULT_CONFIG.copy()
    with patch.object(QThread, "start", lambda self: None), \
            patch("netspeedtray.core.widget_state.get_app_data_path", return_value=d):
        s = WidgetState(cfg)
    s.batch_persist_timer.stop()
    s.maintenance_timer.stop()
    return s


def _drain(s: WidgetState) -> None:
    """Run every queued worker task on this thread (where prepare_v8 bound the writer)."""
    q = s.db_worker._queue
    while not q.empty():
        item = q.get_nowait()
        if item is not None:
            s.db_worker._execute_task(*item)


def _usage_on_disk(d: Path):
    c = sqlite3.connect(f"file:{(d / DB).as_posix()}?mode=ro", uri=True)
    try:
        row = c.execute("SELECT cumulative_up, cumulative_down FROM usage_counter").fetchone()
        snap = c.execute("SELECT value FROM metadata WHERE key = ?", (SNAP,)).fetchone()
        return (tuple(row) if row else None), (snap[0] if snap else None)
    finally:
        c.close()


def test_a_failed_delete_leaves_the_data_cap_counter_untouched(tmp_path, q_app, no_retry_sleep):
    """Harness h1 / T2: delete_history raising 'database is locked' -> failed, history intact, and the
    odometer must still read 5e9 / 9e9 (it used to be zeroed on disk and in memory)."""
    _make_v7(tmp_path, usage=(5e9, 9e9, 1e9, 2e9))
    s = _state(tmp_path)
    try:
        prepare_v8(s)
        _drain(s)
        assert s.get_usage_this_period() == (4e9, 7e9)
        got = []
        s.history_deleted.connect(got.append)

        def boom(self, include_usage=False, now_ts=None):
            raise sqlite3.OperationalError("database is locked")
        with patch.object(writer_v8.WriterV8, "delete_history", boom):
            s.delete_all_history(include_usage=True)
            s.add_usage_bytes(10.0, 20.0)                       # a poll while the delete is queued
            _drain(s)
        assert got == [DatabaseWorker.DELETE_FAILED]
        assert s.get_usage_this_period() == (4e9 + 10.0, 7e9 + 20.0), "a failed delete reset the counter"
        _drain(s)                                               # whatever was persisted afterwards
        assert _usage_on_disk(tmp_path)[0] == (5e9 + 10.0, 9e9 + 20.0)
        assert s.db_worker.conn.execute(f"SELECT COUNT(*) FROM {S8.NET_HOUR}").fetchone()[0] > 0
    finally:
        s.db_worker._close_connection()
        s.cleanup()


def test_a_successful_delete_resets_the_counter_and_keeps_traffic_seen_since(tmp_path, q_app, no_retry_sleep):
    _make_v7(tmp_path, usage=(5e9, 9e9, 1e9, 2e9))
    s = _state(tmp_path)
    try:
        prepare_v8(s)
        _drain(s)
        assert s.get_usage_this_period() == (4e9, 7e9)
        s.delete_all_history(include_usage=True)
        assert s.get_usage_this_period() == (4e9, 7e9), "unchanged until the delete has answered"
        s.add_usage_bytes(10.0, 20.0)
        _drain(s)
        assert s.get_usage_this_period() == (10.0, 20.0)
        _drain(s)
        assert _usage_on_disk(tmp_path)[0] == (10.0, 20.0)
    finally:
        s.db_worker._close_connection()
        s.cleanup()


def test_a_reset_while_the_file_is_opening_drops_the_buffered_polls(tmp_path, q_app, no_retry_sleep):
    """Harness h3: 5 x 100 MB polls buffered before the odometer could load, then the user resets.
    The first post-reset poll must read 1 byte, not 500000001 - and the old counter snapshot must not
    survive (a crash would re-add pre-reset traffic on the next same-boot launch)."""
    db = _make_v7(tmp_path, usage=(5e9, 9e9, 1e9, 2e9))
    s = _state(tmp_path)
    try:
        c = lambda n: {"Wi-Fi": NS(bytes_sent=n, bytes_recv=n)}
        t = time.time()
        for i in range(5):           # the worker is still "migrating": nothing loaded, polls buffered
            s.add_usage_bytes(1e8, 1e8, counters=c(i), sampled_at=t + i, window_start=t + i - 1)
        assert not s._usage_loaded and len(s._usage_pending) == 5
        s.delete_all_history(include_usage=True)
        prepare_v8(s, db)            # the upgrade finishes; the queued delete runs
        _drain(s)
        assert s.get_usage_this_period() == (0.0, 0.0)
        assert _usage_on_disk(tmp_path)[1] is None, "the pre-reset counter snapshot survived the reset"
        s.add_usage_bytes(1.0, 1.0, counters=c(9), sampled_at=t + 9, window_start=t + 8)
        assert s.get_usage_this_period() == (1.0, 1.0)
    finally:
        s.db_worker._close_connection()
        s.cleanup()


def test_a_failed_reset_while_opening_keeps_the_buffered_polls(tmp_path, q_app, no_retry_sleep):
    db = _make_v7(tmp_path, usage=(5e9, 9e9, 1e9, 2e9))
    s = _state(tmp_path)
    try:
        for _ in range(3):
            s.add_usage_bytes(1e8, 1e8)
        s.delete_all_history(include_usage=True)

        def boom(self, include_usage=False, now_ts=None):
            raise sqlite3.OperationalError("database is locked")
        with patch.object(writer_v8.WriterV8, "delete_history", boom):
            prepare_v8(s, db)
            _drain(s)
        s.add_usage_bytes(1.0, 1.0)
        assert s.get_usage_this_period() == (4e9 + 3e8 + 1.0, 7e9 + 3e8 + 1.0)
    finally:
        s.db_worker._close_connection()
        s.cleanup()


def test_settings_re_arms_cap_alerts_only_after_a_successful_delete():
    from netspeedtray.views.settings.dialog import SettingsDialog
    calls = []
    fake = NS(
        _delete_timeout=NS(stop=lambda: None),
        advanced_page=NS(delete_history_btn=NS(setEnabled=lambda v: None)),
        parent_widget=NS(update_config=lambda u: calls.append(u), invalidate_usage_cache=lambda: None),
        config={"usage_alert_state": "2026-10-01:80"}, original_config={"usage_alert_state": "2026-10-01:80"},
        i18n=NS(SUCCESS_TITLE="", DELETE_HISTORY_DONE="", DELETE_HISTORY_DONE_COMPACTING="",
                ERROR_TITLE="", DELETE_HISTORY_FAILED=""),
        logger=NS(debug=lambda *a, **k: None),
        _delete_include_usage=True,
    )
    with patch("netspeedtray.views.settings.dialog.QMessageBox"):
        SettingsDialog._on_history_deleted(fake, DatabaseWorker.DELETE_FAILED)
        assert fake.config["usage_alert_state"] == "2026-10-01:80" and calls == []
        fake._delete_include_usage = True
        SettingsDialog._on_history_deleted(fake, DatabaseWorker.DELETE_DONE)
    assert fake.config["usage_alert_state"] == "" and fake.original_config["usage_alert_state"] == ""
    assert calls == [{"usage_alert_state": ""}]
