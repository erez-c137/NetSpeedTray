"""
"Delete all history" (2.2 scope item C, privacy headline) - on the v8 schema (#347 through WriterV8).

What it must do, pinned here:
  * the DatabaseWorker task empties EVERY v8 history table (constants.data.HISTORY_TABLES: network and
    hardware tiers, sessions, link events AND the interfaces / metrics dimensions - adapter friendly
    names are identifying) with secure_delete on, returns the free pages and checkpoints the WAL, so
    the deleted rows are really gone from both files;
  * the data-usage odometer (data-cap counter, D5: authoritative for billing) is untouched unless the
    user opts in, and schema metadata (db_version, watermarks) always survives;
  * WidgetState clears its in-memory series AND its not-yet-flushed batches (a pending batch flushed
    after the delete would resurrect pre-delete samples), resets the odometer only when opted;
  * the live app keeps recording: the next sample after the delete is written, as a new session.
"""
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from netspeedtray import constants
from netspeedtray.core.database import DatabaseWorker
from netspeedtray.core.db import schema_v8 as S8
from netspeedtray.core.db.writer_v8 import NetSample
from netspeedtray.core.widget_state import WidgetState

D = constants.data
MARKER = "SECRETNIC7f3a"   # an adapter friendly name: identifying, must leave the file


def _fresh_worker(tmp_path: Path) -> DatabaseWorker:
    w = DatabaseWorker(tmp_path / "speed_history.db")
    w._prepare_storage()          # fresh install -> v8, writer bound to this thread
    return w


def _seed(w: DatabaseWorker, n: int = 2000, nic: str = "Ethernet") -> None:
    """Rows in every v8 history table + the odometer. Enough rows that the delete frees real pages."""
    now = int(time.time())
    for i in range(n):
        w._writer.add_net_sample(NetSample(ts=now - n + i, dt_ms=1000, byte_deltas={nic: (1000, 2000)}))
        w._writer.add_hw_sample(now - n + i, "cpu", 12.5)
    w._writer.flush(now)
    c = w.conn
    iid = c.execute("SELECT iface_id FROM interfaces WHERE name = ?", (nic,)).fetchone()[0]
    mid = c.execute("SELECT metric_id FROM metrics WHERE name = 'cpu'").fetchone()[0]
    base = 1_700_000_000
    c.execute("BEGIN")
    for t in (S8.NET_MINUTE, S8.NET_HOUR):
        c.executemany(f"INSERT INTO {t} (ts, iface_id, up_avg, down_avg, up_max, down_max, sample_count) "
                      "VALUES (?, ?, 1, 2, 3, 4, 5)", [(base + 60 * i, iid) for i in range(n)])
    for t in (S8.HW_MINUTE, S8.HW_HOUR):
        c.executemany(f"INSERT INTO {t} (ts, metric_id, avg_value, max_value, sample_count) VALUES (?, ?, 10, 20, 5)",
                      [(base + 60 * i, mid) for i in range(n)])
    c.execute(f"INSERT INTO {S8.LINK_EVENTS} VALUES (?, ?, 1)", (iid, base))
    c.execute(f"INSERT INTO {D.USAGE_COUNTER_TABLE} VALUES (1, 111, 222, 11, 22, '2026-10-01', 1)")
    c.execute("COMMIT")


def _count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_history_tables_are_the_v8_set():
    assert tuple(D.HISTORY_TABLES) == tuple(S8.HISTORY_TABLES_V8)
    assert D.USAGE_COUNTER_TABLE not in D.HISTORY_TABLES and "metadata" not in D.HISTORY_TABLES


# --- DatabaseWorker ---------------------------------------------------------------------------

def test_delete_history_empties_every_table_and_keeps_the_odometer(tmp_path):
    w = _fresh_worker(tmp_path)
    _seed(w)
    w._execute_task("delete_history", {"include_usage": False})

    for t in D.HISTORY_TABLES:
        n = _count(w.conn, t)
        # The running app becomes a new session inside the delete's own transaction.
        assert n == (1 if t == S8.APP_SESSIONS else 0), f"{t} still has rows after Delete all history"
    # D5: the odometer is the billing source of truth - untouched unless the user opts in.
    row = w.conn.execute(f"SELECT cumulative_up, cumulative_down FROM {D.USAGE_COUNTER_TABLE}").fetchone()
    assert tuple(row) == (111.0, 222.0)
    # Schema bookkeeping survives (a missing db_version would read as a fresh/unknown file).
    assert w.conn.execute("SELECT value FROM metadata WHERE key='db_version'").fetchone()[0] == "8"
    w._close_connection()


def test_delete_history_returns_the_free_pages(tmp_path):
    """auto_vacuum=INCREMENTAL: no full VACUUM - the freed pages go back via incremental_vacuum."""
    w = _fresh_worker(tmp_path)
    _seed(w)
    pages_before = w.conn.execute("PRAGMA page_count").fetchone()[0]
    w._execute_task("delete_history", {"include_usage": False})
    assert w.conn.execute("PRAGMA freelist_count").fetchone()[0] == 0
    assert w.conn.execute("PRAGMA page_count").fetchone()[0] < pages_before
    w._close_connection()


def test_delete_history_with_usage_opt_in_clears_the_odometer(tmp_path):
    w = _fresh_worker(tmp_path)
    _seed(w)
    w._execute_task("delete_history", {"include_usage": True})
    assert _count(w.conn, D.USAGE_COUNTER_TABLE) == 0
    w._close_connection()


def test_delete_history_emits_completion(tmp_path):
    w = _fresh_worker(tmp_path)
    _seed(w)
    got = []
    w.history_deleted.connect(got.append)   # same thread -> direct delivery
    w._execute_task("delete_history", {"include_usage": False})
    assert got == [DatabaseWorker.DELETE_DONE]
    w._close_connection()


def test_next_samples_are_written_after_delete(tmp_path):
    """The writer's cached ids are dropped by the delete; the next sample re-creates its dimension rows."""
    w = _fresh_worker(tmp_path)
    _seed(w)
    w._execute_task("delete_history", {"include_usage": False})
    now = time.time()
    w._execute_task("persist_v8", ([NetSample(ts=now, dt_ms=1000, byte_deltas={"Ethernet": (5, 6)})],
                                   [(int(now), "cpu", 42.0)]))
    assert _count(w.conn, S8.NET_RAW) == 1 and _count(w.conn, S8.HW_RAW) == 1
    assert w.conn.execute("SELECT up_bytes, down_bytes FROM net_raw").fetchone() == (5, 6)
    assert _count(w.conn, S8.INTERFACES) == 1
    w._close_connection()


def test_delete_history_refused_on_a_newer_schema(tmp_path):
    """The read-only guard covers this task like every other write."""
    w = _fresh_worker(tmp_path)
    _seed(w)
    w._schema_incompatible = True
    got = []
    w.history_deleted.connect(got.append)
    w._execute_task("delete_history", {"include_usage": True})
    assert got == [DatabaseWorker.DELETE_FAILED]
    assert _count(w.conn, S8.NET_RAW) > 0
    assert _count(w.conn, D.USAGE_COUNTER_TABLE) == 1
    w._close_connection()


# --- Honest completion: the bytes must actually leave the files -----------------------------------

def _seed_marker(w: DatabaseWorker, n: int = 3000) -> None:
    _seed(w, n=n, nic=MARKER)
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
    """The adapter's friendly name lives once, in `interfaces`; secure_delete + the checkpoint must
    scrub it from both files."""
    w = _fresh_worker(tmp_path)
    _seed_marker(w)
    assert _marker_on_disk(tmp_path)
    got = []
    w.history_deleted.connect(got.append)
    w._execute_task("delete_history", {"include_usage": False})
    assert got == [DatabaseWorker.DELETE_DONE]
    assert not _marker_on_disk(tmp_path)
    w._close_connection()


def test_busy_checkpoint_is_reported_pending_then_compacted_when_idle(tmp_path, no_retry_sleep, caplog):
    """A reader mid-query (the Monitor's graph worker) makes wal_checkpoint(TRUNCATE) return busy -
    the old page images then still sit in -wal. That must not be reported as done."""
    w = _fresh_worker(tmp_path)
    _seed_marker(w)
    reader = sqlite3.connect(tmp_path / "speed_history.db")
    cur = reader.execute(f"SELECT * FROM {S8.NET_MINUTE}")
    cur.fetchone()                                   # read transaction held open
    got = []
    w.history_deleted.connect(got.append)
    with caplog.at_level("WARNING"):
        w._execute_task("delete_history", {"include_usage": False})
    assert got == [DatabaseWorker.DELETE_COMPACT_PENDING]
    assert any(r.levelname == "WARNING" for r in caplog.records)
    assert _count(w.conn, S8.NET_MINUTE) == 0        # the rows are deleted either way
    assert w._compact_pending
    # The short checkpoint busy_timeout must not leak into normal writes.
    assert w.conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000

    cur.close()
    reader.close()
    w._retry_pending_compaction(force=True)          # what the idle tick does
    assert not w._compact_pending
    assert not _marker_on_disk(tmp_path)
    w._close_connection()


def test_pending_compaction_survives_a_quit(tmp_path, no_retry_sleep):
    """Quit (killed) before the idle retry lands -> the next start finishes the job."""
    w = _fresh_worker(tmp_path)
    _seed_marker(w)
    reader = sqlite3.connect(tmp_path / "speed_history.db")
    cur = reader.execute(f"SELECT * FROM {S8.NET_MINUTE}")
    cur.fetchone()
    w._execute_task("delete_history", {"include_usage": False})
    assert w._compact_pending
    cur.close()
    reader.close()
    w.conn.close()                                   # the app is killed: no session end, no retry
    w.conn = None

    w2 = _fresh_worker(tmp_path)                     # next start resumes it (_prepare_storage)
    assert not w2._compact_pending
    assert w2.conn.execute("SELECT 1 FROM metadata WHERE key=?",
                           (DatabaseWorker._COMPACT_MARKER_KEY,)).fetchone() is None
    assert not _marker_on_disk(tmp_path)
    w2._close_connection()


def test_idle_retry_is_throttled(tmp_path, no_retry_sleep):
    w = _fresh_worker(tmp_path)
    w._compact_pending = True
    w._vacuum_pending = False
    w._last_compact_attempt = time.monotonic()        # just tried
    w._retry_pending_compaction()                     # not forced -> waits for the interval
    assert w._compact_pending
    w._retry_pending_compaction(force=True)
    assert not w._compact_pending
    w._close_connection()


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
    s.add_net_sample(now.timestamp(), 1000, {"Ethernet": (1000.0, 2000.0)})
    for k in ("cpu", "gpu", "ram"):
        s.add_hardware_stat(k, 50.0, now=now)
    assert s.aggregated_history and s.cpu_history and s._net_batch and s._hw_batch

    s.delete_all_history()

    assert not s.in_memory_history and not s.aggregated_history
    assert not s.cpu_history and not s.gpu_history and not s.ram_history
    assert s._net_batch == [] and s._hw_batch == [], "a pending batch would resurrect deleted samples"
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
        t0 = time.time()
        state.add_net_sample(t0 - 120, 1000, {"Ethernet": (1000.0, 2000.0)})
        state.add_hardware_stat("cpu", 30.0, now=datetime.fromtimestamp(t0 - 120))
        state.flush_and_wait(timeout=5.0)
        # An unflushed sample taken just before the delete must not survive it either.
        state.add_net_sample(t0 - 60, 1000, {"Ethernet": (3000.0, 4000.0)})

        state.delete_all_history()
        state.add_net_sample(t0, 1000, {"Ethernet": (7000.0, 8000.0)})
        state.add_hardware_stat("cpu", 70.0)
        state.flush_and_wait(timeout=10.0)

        db = tmp_path / "speed_history.db"
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            ups = conn.execute(f"SELECT up_bytes FROM {S8.NET_RAW}").fetchall()
            hw = conn.execute(f"SELECT value FROM {S8.HW_RAW}").fetchall()
        finally:
            conn.close()
        assert ups == [(7000,)]
        assert hw == [(70.0,)]
    finally:
        state.cleanup()
