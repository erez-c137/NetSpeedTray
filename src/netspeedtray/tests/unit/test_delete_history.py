"""
"Delete all history" (2.2 scope item C, privacy headline).

What it must do, pinned here:
  * the DatabaseWorker task empties EVERY history tier (speed raw/minute/hour, hardware raw/minute/hour,
    the legacy per-interface totals) and VACUUMs, so the deleted rows are really gone from the file;
  * the data-usage odometer (data-cap counter, D5: authoritative for billing) is untouched unless the
    user opts in, and schema metadata (db_version) always survives;
  * WidgetState clears its in-memory series AND its not-yet-flushed batches (a pending batch flushed
    after the delete would resurrect pre-delete samples), resets the odometer only when opted;
  * the live app keeps recording: the next sample after the delete is written.
"""
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from netspeedtray import constants
from netspeedtray.core.database import DatabaseWorker
from netspeedtray.core.widget_state import WidgetState

D = constants.data
SPEED_TIERS = (D.SPEED_TABLE_RAW, D.SPEED_TABLE_MINUTE, D.SPEED_TABLE_HOUR)
HW_TIERS = (D.HARDWARE_STATS_TABLE_RAW, D.HARDWARE_STATS_TABLE_MINUTE, D.HARDWARE_STATS_TABLE_HOUR)


def _fresh_worker(tmp_path: Path) -> DatabaseWorker:
    w = DatabaseWorker(tmp_path / "speed_history.db")
    w._initialize_connection()
    w._check_and_create_schema()
    return w


def _seed(w: DatabaseWorker, n: int = 2000) -> None:
    """Rows in every tier + the odometer. Enough rows that the delete leaves real free pages."""
    c = w.conn.cursor()
    base = 1_700_000_000
    c.executemany(f"INSERT INTO {D.SPEED_TABLE_RAW} VALUES (?, 'Ethernet', 1000, 2000)",
                  [(base + i,) for i in range(n)])
    for t in (D.SPEED_TABLE_MINUTE, D.SPEED_TABLE_HOUR):
        c.executemany(f"INSERT INTO {t} VALUES (?, 'Ethernet', 1, 2, 3, 4, 5)",
                      [(base + 60 * i,) for i in range(n)])
    c.executemany(f"INSERT INTO {D.HARDWARE_STATS_TABLE_RAW} VALUES (?, 'cpu', 12.5)",
                  [(base + i,) for i in range(n)])
    for t in (D.HARDWARE_STATS_TABLE_MINUTE, D.HARDWARE_STATS_TABLE_HOUR):
        c.executemany(f"INSERT INTO {t} VALUES (?, 'cpu', 10, 20, 5)",
                      [(base + 60 * i,) for i in range(n)])
    c.execute(f"INSERT INTO {D.BANDWIDTH_TABLE} VALUES ('Ethernet', 10, 20)")
    c.execute(f"INSERT INTO {D.USAGE_COUNTER_TABLE} VALUES (1, 111, 222, 11, 22, '2026-10-01', 1)")
    w.conn.commit()


def _count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# --- DatabaseWorker ---------------------------------------------------------------------------

def test_delete_history_empties_every_tier_and_keeps_the_odometer(tmp_path):
    w = _fresh_worker(tmp_path)
    _seed(w)
    w._execute_task("delete_history", {"include_usage": False})

    for t in SPEED_TIERS + HW_TIERS + (D.BANDWIDTH_TABLE,):
        assert _count(w.conn, t) == 0, f"{t} still has rows after Delete all history"
    # D5: the odometer is the billing source of truth - untouched unless the user opts in.
    row = w.conn.execute(f"SELECT cumulative_up, cumulative_down FROM {D.USAGE_COUNTER_TABLE}").fetchone()
    assert tuple(row) == (111.0, 222.0)
    # Schema bookkeeping survives (a missing db_version would read as a fresh/unknown file).
    assert w.conn.execute("SELECT value FROM metadata WHERE key='db_version'").fetchone()[0] == str(
        DatabaseWorker._DB_VERSION)


def test_delete_history_vacuums_so_the_rows_leave_the_file(tmp_path):
    w = _fresh_worker(tmp_path)
    _seed(w)
    w._execute_task("delete_history", {"include_usage": False})
    # A plain DELETE only moves pages to the freelist - the old samples stay readable in the file.
    assert w.conn.execute("PRAGMA freelist_count").fetchone()[0] == 0
    assert w.conn.execute("SELECT value FROM metadata WHERE key='last_vacuum_at'").fetchone() is not None


def test_delete_history_with_usage_opt_in_clears_the_odometer(tmp_path):
    w = _fresh_worker(tmp_path)
    _seed(w)
    w._execute_task("delete_history", {"include_usage": True})
    assert _count(w.conn, D.USAGE_COUNTER_TABLE) == 0


def test_delete_history_emits_completion(tmp_path):
    w = _fresh_worker(tmp_path)
    _seed(w)
    got = []
    w.history_deleted.connect(got.append)   # same thread -> direct delivery
    w._execute_task("delete_history", {"include_usage": False})
    assert got == [DatabaseWorker.DELETE_DONE]


def test_next_samples_are_written_after_delete(tmp_path):
    w = _fresh_worker(tmp_path)
    _seed(w)
    w._execute_task("delete_history", {"include_usage": False})
    w._execute_task("persist_speed", [(1_800_000_000, "Ethernet", 5.0, 6.0)])
    w._execute_task("persist_hardware", [(1_800_000_000, "cpu", 42.0)])
    assert _count(w.conn, D.SPEED_TABLE_RAW) == 1
    assert _count(w.conn, D.HARDWARE_STATS_TABLE_RAW) == 1


def test_delete_history_refused_on_a_newer_schema(tmp_path):
    """The downgrade guard covers this task like every other write."""
    w = _fresh_worker(tmp_path)
    _seed(w)
    w._schema_incompatible = True
    w._execute_task("delete_history", {"include_usage": True})
    assert _count(w.conn, D.SPEED_TABLE_RAW) > 0
    assert _count(w.conn, D.USAGE_COUNTER_TABLE) == 1


# --- Honest completion: the bytes must actually leave the files -----------------------------------

MARKER = "SECRETNIC7f3a"


def _seed_marker(w: DatabaseWorker, n: int = 3000) -> None:
    w.conn.executemany(f"INSERT INTO {D.SPEED_TABLE_RAW} VALUES (?, '{MARKER}', 1, 2)",
                       [(1_700_000_000 + i,) for i in range(n)])
    w.conn.commit()
    w.conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")   # the marker now lives in the .db file


def _marker_on_disk(tmp_path: Path) -> bool:
    found = False
    for name in ("speed_history.db", "speed_history.db-wal"):
        f = tmp_path / name
        if f.exists() and MARKER.encode() in f.read_bytes():
            found = True
    return found


@pytest.fixture
def no_retry_sleep(monkeypatch):
    monkeypatch.setattr(constants.timeouts, "DB_CHECKPOINT_RETRY_SLEEP", 0.0)


def test_clean_delete_leaves_no_trace_in_db_or_wal(tmp_path, no_retry_sleep):
    w = _fresh_worker(tmp_path)
    _seed_marker(w)
    assert _marker_on_disk(tmp_path)
    got = []
    w.history_deleted.connect(got.append)
    w._execute_task("delete_history", {"include_usage": False})
    assert got == [DatabaseWorker.DELETE_DONE]
    assert not _marker_on_disk(tmp_path)


def test_busy_checkpoint_is_reported_pending_then_compacted_when_idle(tmp_path, no_retry_sleep, caplog):
    """A reader mid-query (the Monitor's graph worker) makes wal_checkpoint(TRUNCATE) return busy -
    the old pages then still sit in the .db / -wal. That must not be reported as done."""
    w = _fresh_worker(tmp_path)
    _seed_marker(w)
    reader = sqlite3.connect(tmp_path / "speed_history.db")
    cur = reader.execute(f"SELECT * FROM {D.SPEED_TABLE_RAW}")
    cur.fetchone()                                   # read transaction held open
    got = []
    w.history_deleted.connect(got.append)
    with caplog.at_level("WARNING"):
        w._execute_task("delete_history", {"include_usage": False})
    assert got == [DatabaseWorker.DELETE_COMPACT_PENDING]
    assert any(r.levelname == "WARNING" for r in caplog.records)
    assert _count(w.conn, D.SPEED_TABLE_RAW) == 0     # the rows are deleted either way
    assert w._compact_pending
    # The short checkpoint busy_timeout must not leak into normal writes.
    assert w.conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000

    cur.close()
    reader.close()
    w._retry_pending_compaction(force=True)          # what the idle tick does
    assert not w._compact_pending
    assert not _marker_on_disk(tmp_path)


class _VacuumFails:
    """Connection proxy whose VACUUM raises (sqlite3.Connection attributes are read-only)."""
    def __init__(self, conn):
        self._c = conn

    def execute(self, sql, *a):
        if sql.strip().upper().startswith("VACUUM"):
            raise sqlite3.OperationalError("database is locked")
        return self._c.execute(sql, *a)

    def __getattr__(self, name):
        return getattr(self._c, name)


def test_failed_vacuum_is_not_full_success(tmp_path, no_retry_sleep, caplog):
    w = _fresh_worker(tmp_path)
    _seed_marker(w)
    real = w.conn
    w.conn = _VacuumFails(real)
    got = []
    w.history_deleted.connect(got.append)
    with caplog.at_level("WARNING"):
        w._execute_task("delete_history", {"include_usage": False})
    assert got == [DatabaseWorker.DELETE_COMPACT_PENDING]
    assert any("VACUUM" in r.getMessage() and r.levelname == "WARNING" for r in caplog.records)
    assert w._compact_pending

    w.conn = real
    w._retry_pending_compaction(force=True)
    assert not w._compact_pending
    assert w.conn.execute("PRAGMA freelist_count").fetchone()[0] == 0
    assert not _marker_on_disk(tmp_path)


def test_pending_compaction_survives_a_quit(tmp_path, no_retry_sleep):
    """Quit before the idle retry lands -> the next start finishes the job. (A busy checkpoint alone
    heals on a clean quit - the last connection's close checkpoints the WAL - but a failed VACUUM
    leaves the deleted rows on the freelist, readable in the file, across restarts.)"""
    w = _fresh_worker(tmp_path)
    _seed_marker(w)
    real = w.conn
    w.conn = _VacuumFails(real)
    w._execute_task("delete_history", {"include_usage": False})
    assert w._compact_pending
    real.close()                                     # the app quits
    assert _marker_on_disk(tmp_path)                 # the old rows are still in the file

    w2 = _fresh_worker(tmp_path)                     # next start
    w2._resume_pending_compaction()
    assert not w2._compact_pending
    assert w2.conn.execute("SELECT 1 FROM metadata WHERE key=?",
                           (DatabaseWorker._COMPACT_MARKER_KEY,)).fetchone() is None
    assert not _marker_on_disk(tmp_path)


def test_idle_retry_is_throttled(tmp_path, no_retry_sleep):
    w = _fresh_worker(tmp_path)
    w._compact_pending = True
    w._vacuum_pending = False
    w._last_compact_attempt = time.monotonic()        # just tried
    w._retry_pending_compaction()                     # not forced -> waits for the interval
    assert w._compact_pending
    w._retry_pending_compaction(force=True)
    assert not w._compact_pending


def test_widget_state_can_delete_history_reflects_worker_state(sync_state):
    # The worker thread isn't started in this fixture - Delete must say so up front instead of
    # leaving the button disabled forever waiting for an answer that never comes.
    assert sync_state.can_delete_history() is False


# --- WidgetState (synchronous: worker thread not started) ---------------------------------------

@pytest.fixture
def sync_state(tmp_path, q_app):
    from PyQt6.QtCore import QThread
    cfg = constants.config.defaults.DEFAULT_CONFIG.copy()
    with patch.object(QThread, "start", lambda self: None), \
            patch("netspeedtray.core.widget_state.get_app_data_path", return_value=tmp_path):
        state = WidgetState(cfg)
    state.batch_persist_timer.stop()
    state.maintenance_timer.stop()
    state.db_worker._queue.queue.clear()   # drop the startup maintenance task
    yield state
    state.cleanup()


def test_widget_state_delete_clears_series_and_pending_batches(sync_state):
    s = sync_state
    now = datetime.now()
    s.add_speed_data({"Ethernet": (1000.0, 2000.0)}, now=now)
    for k in ("cpu", "gpu", "ram"):
        s.add_hardware_stat(k, 50.0, now=now)
    assert s.aggregated_history and s.cpu_history and s._db_batch and s._hw_batch

    s.delete_all_history()

    assert not s.in_memory_history and not s.aggregated_history
    assert not s.cpu_history and not s.gpu_history and not s.ram_history
    assert s._db_batch == [] and s._hw_batch == [], "a pending batch would resurrect deleted samples"
    tasks = [t for t, _ in list(s.db_worker._queue.queue)]
    assert tasks == ["delete_history"]


def test_widget_state_delete_keeps_odometer_unless_opted(sync_state):
    s = sync_state
    s._usage = {"cumulative_up": 500.0, "cumulative_down": 900.0,
                "anchor_up": 100.0, "anchor_down": 100.0, "period_key": "2026-10-01"}
    s._usage_loaded = True
    s.delete_all_history(include_usage=False)
    assert s._usage["cumulative_up"] == 500.0
    _task, payload = list(s.db_worker._queue.queue)[-1]
    assert payload == {"include_usage": False}


def test_widget_state_delete_with_usage_resets_odometer(sync_state):
    s = sync_state
    s._usage = {"cumulative_up": 500.0, "cumulative_down": 900.0,
                "anchor_up": 100.0, "anchor_down": 100.0, "period_key": "2026-10-01"}
    s._usage_loaded = True
    s.delete_all_history(include_usage=True)
    assert s.get_usage_this_period() == (0.0, 0.0)
    tasks = [t for t, _ in list(s.db_worker._queue.queue)]
    # delete first, then the zeroed odometer is persisted (FIFO), so it can't be resurrected.
    assert tasks[0] == "delete_history" and tasks[-1] == "persist_usage"


# --- End to end with the real write thread --------------------------------------------------------

def test_end_to_end_delete_then_keep_recording(tmp_path, q_app):
    cfg = constants.config.defaults.DEFAULT_CONFIG.copy()
    with patch("netspeedtray.core.widget_state.get_app_data_path", return_value=tmp_path):
        state = WidgetState(cfg)
    try:
        state.batch_persist_timer.stop()
        old = datetime.fromtimestamp(time.time() - 120)
        state.add_speed_data({"Ethernet": (1000.0, 2000.0)}, now=old)
        state.add_hardware_stat("cpu", 30.0, now=old)
        state.flush_and_wait(timeout=5.0)
        # An unflushed sample taken just before the delete must not survive it either.
        state.add_speed_data({"Ethernet": (3000.0, 4000.0)}, now=datetime.fromtimestamp(time.time() - 60))

        state.delete_all_history()
        state.add_speed_data({"Ethernet": (7000.0, 8000.0)})
        state.add_hardware_stat("cpu", 70.0)
        state.flush_and_wait(timeout=10.0)

        db = tmp_path / "speed_history.db"
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            speeds = conn.execute(f"SELECT upload_bytes_sec FROM {D.SPEED_TABLE_RAW}").fetchall()
            hw = conn.execute(f"SELECT value FROM {D.HARDWARE_STATS_TABLE_RAW}").fetchall()
        finally:
            conn.close()
        assert speeds == [(7000.0,)]
        assert hw == [(70.0,)]
        assert len(state.aggregated_history) == 1 and len(state.cpu_history) == 1
    finally:
        state.cleanup()
