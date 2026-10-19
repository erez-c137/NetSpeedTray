"""
v8 step 7 - the switch-over (Docs/reference/database-v8-implementation-steps.md, "Step 7 notes").

What these pin, each against the REAL worker startup (``DatabaseWorker._prepare_storage`` / the thread):

* a fresh install creates v8; a v7 file is migrated on start, maintenance runs right after, and every
  reader returns what the v7 backend returned for history older than 24 h (notes 1, 4, 5);
* ``migrate_v8.recover()`` runs on every launch BEFORE any connection to the live name (note 3);
* every refusal - InsufficientSpace, VerificationFailed, SwapDeferred, SwapIncomplete, RecoveryError,
  any other migration error - leaves the session read-only, fires ``migration_deferred`` (the flyout),
  never opens the live name read-write, never writes to it (bytes unchanged), and the next launch
  retries (note 2);
* odometer / metadata writes never interleave with WriterV8's transaction (note 6), and every writer
  call is on the worker thread (note 7);
* the V8 reader weighs raw rows by their measured seconds in rollup summaries (note 8);
* end to end: 10 minutes of synthetic polls at the 2 s and 10 s presets -> stored volume == the sum of
  the byte deltas, exactly, before and after the raw tier is rolled up;
* the exporter (a read-only WidgetState) and the odometer read a v8 file as before.
"""
import hashlib
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QThread

from netspeedtray import constants
from netspeedtray.core.database import DatabaseWorker
from netspeedtray.core.db import migrate_v8 as M
from netspeedtray.core.db import schema_v8 as S8
from netspeedtray.core.db.history_reader import V7HistoryReader, V8HistoryReader
from netspeedtray.core.widget_state import WidgetState
from netspeedtray.tests.integration import v7_fixture as F
from netspeedtray.tests.legacy_v7_worker import LegacyV7Worker
from netspeedtray.tests.state_helpers import prepare_v8

DB = "speed_history.db"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _snapshot(d: Path) -> dict:
    """Every file in the folder -> its hash (sidecars included)."""
    return {p.name: _sha(p) for p in sorted(d.iterdir()) if p.is_file()}


def _make_v7(d: Path) -> Path:
    db = d / DB
    w = LegacyV7Worker(db)
    w._initialize_connection()
    w._check_and_create_schema()
    w._ensure_indexes()
    F.populate(w.conn)
    w._close_connection()
    return db


def _unstarted_state(d: Path) -> WidgetState:
    cfg = constants.config.defaults.DEFAULT_CONFIG.copy()
    with patch.object(QThread, "start", lambda self: None), \
            patch("netspeedtray.core.widget_state.get_app_data_path", return_value=d):
        return WidgetState(cfg)


def _started_state(d: Path, config=None) -> WidgetState:
    cfg = dict(config or constants.config.defaults.DEFAULT_CONFIG)
    with patch("netspeedtray.core.widget_state.get_app_data_path", return_value=d):
        s = WidgetState(cfg)
    assert s.db_worker.ready.wait(30), "the worker never finished starting"
    return s


# ------------------------------------------------------------------------------------------------
# fresh install / migration on start
# ------------------------------------------------------------------------------------------------

def test_fresh_install_creates_v8_on_the_worker_thread(tmp_path, q_app):
    s = _started_state(tmp_path)
    try:
        w = s.db_worker
        assert w.reader_version == 8 and not w.readers_read_only and not w._schema_incompatible
        assert w._writer is not None and w._writer._owner != threading.get_ident()   # built on ITS thread
        assert s.history_schema_version() == 8
        assert isinstance(s.history_reader, V8HistoryReader)
    finally:
        s.cleanup()
    c = sqlite3.connect(sqlite_ro(tmp_path / DB), uri=True)
    try:
        assert c.execute("SELECT value FROM metadata WHERE key='db_version'").fetchone()[0] == "8"
        assert c.execute("PRAGMA auto_vacuum").fetchone()[0] == 2
        # A clean exit closes the session it opened.
        assert c.execute("SELECT COUNT(*) FROM app_sessions WHERE clean_exit = 1").fetchone()[0] == 1
    finally:
        c.close()


def sqlite_ro(p: Path) -> str:
    from netspeedtray.core.db.sqlite_uri import sqlite_ro_uri
    return sqlite_ro_uri(p)


def _v7_reads(reader, windows):
    out = {}
    for s, e in windows:
        for res in ("minute", "hour", "day"):
            out[("speed", s, res)] = reader.speed_history(s, e, None, True, res)
            out[("speed-wifi", s, res)] = reader.speed_history(s, e, "Wi-Fi 3", True, res)
        for st in F.STATS:
            out[("hw", st, s)] = reader.hardware_history(st, s, e)
    out["ifaces"] = reader.distinct_interfaces()
    out["earliest"] = reader.earliest_timestamp()
    out["usage"] = reader.load_usage()
    return out


def test_a_v7_file_is_migrated_on_start_and_reads_the_same(tmp_path, q_app):
    """The real thread: recover -> migrate (no connection of ours open) -> maintenance -> open. The
    v7 file stays, unchanged, as the backup; graphs / hardware / interfaces / odometer read the same
    for history older than 24 h (all of the fixture's)."""
    db = _make_v7(tmp_path)
    end = datetime.fromtimestamp(F.RAW_START + 4000)
    windows = [(end - timedelta(days=d), end) for d in (2, 7, 40)]
    before_hash = _sha(db)
    c7 = sqlite3.connect(sqlite_ro(db), uri=True)
    try:
        expected = _v7_reads(V7HistoryReader(lambda: c7, lambda: db), windows)
    finally:
        c7.close()

    calls = []
    real_maint = M.migrate

    def spy_migrate(path, *a, **k):
        calls.append(("migrate", Path(path).name))
        return real_maint(path, *a, **k)
    with patch("netspeedtray.core.db.migrate_v8.migrate", side_effect=spy_migrate), \
            patch("netspeedtray.core.db.writer_v8.WriterV8.run_maintenance", autospec=True,
                  side_effect=lambda self, *a, **k: calls.append(("maintenance", k.get("retention_days")))
                  or {"net_raw_moved": 0}), \
            patch("netspeedtray.core.db.writer_v8.WriterV8.start_session", autospec=True,
                  side_effect=lambda self, *a, **k: calls.append(("session",)) or setattr(self, "session_id", 1)):
        s = _started_state(tmp_path)
    try:
        w = s.db_worker
        assert w.migration_result.status == "migrated"
        # note 5: maintenance right after migrate(), before the session's first write.
        assert calls[:3] == [("migrate", DB), ("maintenance", None), ("session",)]
        # note 4: no _backup_database copy for this step - the renamed v7 file is the only backup.
        baks = sorted(p.name for p in tmp_path.glob("*.bak.*"))
        assert len(baks) == 1 and ".bak.v7_" in baks[0]
        assert _sha(tmp_path / baks[0]) == before_hash
        got = _v7_reads(s.history_reader, windows)
    finally:
        s.cleanup()
    assert got.keys() == expected.keys()
    for k in expected:
        if k[0] == "hw":   # hardware rows are copied untouched; a UNION order can move the last ulp of an AVG
            assert [t for t, _ in got[k]] == [t for t, _ in expected[k]], k
            assert [v for _, v in got[k]] == pytest.approx([v for _, v in expected[k]], rel=1e-12), k
        else:
            assert got[k] == expected[k], k


def test_maintenance_really_runs_after_the_migration(tmp_path, q_app):
    """Without the spy: the post-migration pass runs for real (the hourly maintenance stamp exists
    before any hourly tick could have fired)."""
    _make_v7(tmp_path)
    w = DatabaseWorker(tmp_path / DB)
    with patch("netspeedtray.core.db.writer_v8.WriterV8.run_maintenance", autospec=True,
               wraps=None) as rm:
        rm.side_effect = lambda self, *a, **k: {"net_raw_moved": 0}
        w._prepare_storage()
    try:
        assert rm.call_count == 1
    finally:
        w._close_connection()


# ------------------------------------------------------------------------------------------------
# note 3: recover() before ANY connect
# ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("make", ["fresh", "v7", "v8"])
def test_recover_runs_before_any_connection_to_the_live_name(tmp_path, q_app, make):
    if make == "v7":
        _make_v7(tmp_path)
    elif make == "v8":
        w0 = DatabaseWorker(tmp_path / DB)
        w0._prepare_storage()
        w0._close_connection()
    live = str(tmp_path / DB)
    events = []
    real_connect = sqlite3.connect
    real_recover = M.recover

    def spy_connect(target, *a, **k):
        if live in str(target) or live.replace("\\", "/") in str(target):
            events.append(("connect", str(target)))
        return real_connect(target, *a, **k)

    def spy_recover(p):
        events.append(("recover", str(p)))
        return real_recover(p)
    w = DatabaseWorker(tmp_path / DB)
    with patch("netspeedtray.core.database.sqlite3.connect", side_effect=spy_connect), \
            patch("netspeedtray.core.db.migrate_v8.recover", side_effect=spy_recover):
        w._prepare_storage()
    try:
        assert events and events[0][0] == "recover", events[:3]
        assert any(e[0] == "connect" for e in events)
    finally:
        w._close_connection()


# ------------------------------------------------------------------------------------------------
# note 2: every refusal path
# ------------------------------------------------------------------------------------------------

def _enospc(*_a, **_k):
    return shutil._ntuple_diskusage(10 ** 12, 10 ** 12, 1)


class _Fault:
    """Each refusal, injected where the real one happens."""

    @staticmethod
    def no_space():
        return patch("netspeedtray.core.db.migrate_v8.shutil.disk_usage", side_effect=_enospc)

    @staticmethod
    def verification():
        return patch("netspeedtray.core.db.migrate_v8.verify",
                     side_effect=M.VerificationFailed("injected: counts differ"))

    @staticmethod
    def swap_deferred():
        real = M._try_rename

        def fail_first(src, dst):
            if Path(dst).name.startswith(DB + M.BAK_INFIX):
                return 32           # ERROR_SHARING_VIOLATION on live -> backup
            return real(src, dst)
        return patch.multiple("netspeedtray.core.db.migrate_v8", _try_rename=fail_first,
                              RENAME_RETRY_SECONDS=0.05)

    @staticmethod
    def swap_incomplete():
        real = M._try_rename

        def fail_second(src, dst):
            if Path(src).name.endswith(M.TMP_SUFFIX):
                return 5            # ERROR_ACCESS_DENIED on .v8tmp -> live
            return real(src, dst)
        return patch.multiple("netspeedtray.core.db.migrate_v8", _try_rename=fail_second,
                              RENAME_RETRY_SECONDS=0.05)

    @staticmethod
    def other():
        return patch("netspeedtray.core.db.migrate_v8._build", side_effect=sqlite3.OperationalError("injected"))


REFUSALS = [
    ("no_space", DatabaseWorker.MIGRATION_NO_SPACE, 7),
    ("verification", DatabaseWorker.MIGRATION_FAILED, 7),
    ("swap_deferred", DatabaseWorker.MIGRATION_FILE_IN_USE, 7),
    ("swap_incomplete", DatabaseWorker.MIGRATION_SWAP_INCOMPLETE, None),
    ("other", DatabaseWorker.MIGRATION_FAILED, 7),
]


@pytest.mark.parametrize("fault,reason,readable", REFUSALS)
def test_every_refusal_is_read_only_with_a_flyout_and_writes_nothing(tmp_path, q_app, fault, reason, readable):
    db = _make_v7(tmp_path)
    live_before = _sha(db)
    s = _unstarted_state(tmp_path)
    got = []
    s.db_worker.migration_deferred.connect(got.append)
    w = s.db_worker
    with getattr(_Fault, fault)():
        w._prepare_storage()
    w.ready.set()
    try:
        assert got == [reason], "the flyout signal must fire exactly once, with the reason"
        assert w._schema_incompatible and w.conn is None and w._writer is None
        assert w.reader_version == readable and w.readers_read_only
        assert not s.can_delete_history()

        # The session keeps polling: every write task is dropped, nothing reaches the file.
        s.add_net_sample(time.time(), 1000, {"Wi-Fi 3": (1, 2)})
        s.add_hardware_stat("cpu", 5.0)
        s.flush_batch()
        s.trigger_maintenance()
        s.db_worker.enqueue_task("persist_usage", (1.0, 2.0, 0.0, 0.0, "2026-10-01", 1))
        while not w._queue.empty():
            item = w._queue.get_nowait()
            if item is not None:
                w._execute_task(*item)

        # Readers: the untouched v7 file read-only - or nothing at all when the live name is absent.
        pts = s.get_speed_history(datetime.fromtimestamp(F.RAW_START - 86400),
                                  datetime.fromtimestamp(F.RAW_START + 4000), resolution="hour",
                                  wait_for_flush=False)
        if readable == 7:
            assert isinstance(s.history_reader, V7HistoryReader)
            assert any(up > 0 for _t, up, _d in pts), "deferred session must still SHOW the v7 history"
        s.cleanup()   # closes the read connections (the place a read-write handle would checkpoint)

        if fault == "swap_incomplete":
            # The v7 file is already the backup and the live name is EMPTY: nothing may create it.
            assert not db.exists(), "a plain connect created an empty database under the live name"
            assert not Path(str(db) + "-wal").exists()
            bak = next(tmp_path.glob(f"{DB}{M.BAK_INFIX}*"))
            assert _sha(bak) == live_before
        else:
            assert _sha(db) == live_before, "the live v7 file changed during a refused upgrade"
    finally:
        if w.conn is not None:
            w.conn.close()

    # Retried at the next launch, with the fault gone: the upgrade completes.
    w2 = DatabaseWorker(db)
    w2._prepare_storage()
    try:
        assert not w2._schema_incompatible and w2.reader_version == 8
        assert w2._get_current_db_version() == 8
    finally:
        w2._close_connection()


def test_a_recovery_error_is_read_only_and_touches_nothing(tmp_path, q_app):
    """J5 with a valid v7 file under the live name (a rolled-back 2.1.x recording into a fresh file):
    recover() refuses to guess - the session must not open anything read-write."""
    db = _make_v7(tmp_path)
    bak = tmp_path / f"{DB}{M.BAK_INFIX}20261001_000000"
    shutil.copyfile(db, bak)
    tmp = M.tmp_path_for(db)
    w0 = DatabaseWorker(tmp)       # any valid v8 file as the "verified build"
    w0._prepare_storage()
    w0._close_connection()
    M._write_journal(M.journal_path_for(db), {"format": 1, "state": "swapping", "live": db.name,
                                               "tmp": tmp.name, "backup": bak.name, "written_at": 1,
                                               **M._fingerprint(db)})
    before = _snapshot(tmp_path)
    w = DatabaseWorker(db)
    got = []
    w.migration_deferred.connect(got.append)
    w._prepare_storage()
    assert got == [DatabaseWorker.MIGRATION_RECOVERY]
    assert w._schema_incompatible and w.conn is None and w.reader_version == 7
    after = _snapshot(tmp_path)
    assert {k: v for k, v in after.items() if not k.endswith(("-shm", "-wal"))} == \
           {k: v for k, v in before.items() if not k.endswith(("-shm", "-wal"))}


def test_widget_state_reemits_and_the_widget_shows_the_flyout(tmp_path, q_app):
    import types
    from netspeedtray.constants.i18n import I18nStrings
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    s = _unstarted_state(tmp_path)
    try:
        got = []
        s.db_migration_deferred.connect(got.append)
        s.db_worker.migration_deferred.emit(DatabaseWorker.MIGRATION_NO_SPACE)
        assert got == [DatabaseWorker.MIGRATION_NO_SPACE]
    finally:
        s.cleanup()
    for reason, key in ((DatabaseWorker.MIGRATION_NO_SPACE, "DB_UPGRADE_PAUSED_NO_SPACE_FLYOUT_BODY"),
                        (DatabaseWorker.MIGRATION_FILE_IN_USE, "DB_UPGRADE_PAUSED_FLYOUT_BODY"),
                        (DatabaseWorker.MIGRATION_SWAP_INCOMPLETE, "DB_UPGRADE_PAUSED_FLYOUT_BODY")):
        f = types.SimpleNamespace(i18n=I18nStrings("en_US"), _show_usage_alert=MagicMock(), logger=MagicMock())
        NetworkSpeedWidget._on_db_migration_deferred(f, reason)
        f._show_usage_alert.assert_called_once_with(f.i18n.DB_READONLY_FLYOUT_TITLE, getattr(f.i18n, key))
    assert I18nStrings("en_US").DB_UPGRADE_PAUSED_FLYOUT_BODY != I18nStrings("en_US").DB_READONLY_FLYOUT_BODY


def test_readers_wait_for_the_worker_and_never_create_the_live_name(tmp_path, q_app):
    """Before the worker has decided, no read connection may open the live name: in a half-swapped
    state a plain connect would create an empty database there (and an open handle blocks the rename)."""
    s = _unstarted_state(tmp_path)
    try:
        assert not s.db_worker.ready.is_set()
        # Readers come back empty (logged), exactly like any read error - never a connection.
        assert s.history_reader.speed_history(datetime.now() - timedelta(hours=1), datetime.now(),
                                              None, True, "minute") == []
        assert s.get_distinct_interfaces() == [] and s.get_usage_lifetime() is None
        assert not (tmp_path / DB).exists()
    finally:
        s.cleanup()


# ------------------------------------------------------------------------------------------------
# notes 6 + 7: transactions and threads
# ------------------------------------------------------------------------------------------------

def test_odometer_writes_never_fold_into_the_writers_transaction(tmp_path, q_app):
    w = DatabaseWorker(tmp_path / DB)
    w._prepare_storage()
    try:
        assert w.conn.isolation_level is None, "the worker connection must be autocommit"
        now = time.time()
        w._writer.add_net_sample(M_sample(now, {"Ethernet": (10, 20)}))   # buffered, not in the DB
        w._execute_task("persist_usage", (5.0, 6.0, 0.0, 0.0, "2026-10-01", 1, None))
        assert not w.conn.in_transaction
        assert w.conn.execute("SELECT COUNT(*) FROM net_raw").fetchone()[0] == 0   # writer's rows not committed by it
        assert w.conn.execute("SELECT cumulative_up FROM usage_counter").fetchone()[0] == 5.0

        # A failing odometer write rolls back alone; the writer's next flush is unaffected.
        with patch("netspeedtray.core.database.constants.data.USAGE_COUNTER_TABLE", "no_such_table"):
            w._execute_task("persist_usage", (7.0, 8.0, 0.0, 0.0, "2026-10-01", 2, "{}"))
        assert not w.conn.in_transaction
        assert w._writer.flush(now + 1)
        assert w.conn.execute("SELECT up_bytes FROM net_raw").fetchone()[0] == 10
        assert w.conn.execute("SELECT cumulative_up FROM usage_counter").fetchone()[0] == 5.0

        # A transaction somebody left open is committed (warned), never folded into the odometer unit.
        w.conn.execute("BEGIN")
        w.conn.execute("INSERT INTO metadata (key, value) VALUES ('stray', '1')")
        w._execute_task("persist_usage", (9.0, 9.0, 0.0, 0.0, "2026-10-01", 3, None))
        assert not w.conn.in_transaction
        assert w.conn.execute("SELECT value FROM metadata WHERE key='stray'").fetchone()[0] == "1"
    finally:
        w._close_connection()


def M_sample(ts, deltas, dt_ms=1000):
    from netspeedtray.core.db.writer_v8 import NetSample
    return NetSample(ts=ts, dt_ms=dt_ms, byte_deltas=deltas)


def test_every_writer_call_is_on_the_worker_thread(tmp_path, q_app):
    """The GUI side only buffers; the writer refuses any call off the thread that built it."""
    s = _started_state(tmp_path)
    try:
        with pytest.raises(RuntimeError):
            s.db_worker._writer.flush()           # this (GUI / test) thread is not the owner
        s.add_net_sample(time.time(), 1000, {"Ethernet": (3, 4)})
        s.flush_and_wait(timeout=5)
    finally:
        s.cleanup()
    c = sqlite3.connect(sqlite_ro(tmp_path / DB), uri=True)
    try:
        assert c.execute("SELECT up_bytes, down_bytes FROM net_raw").fetchall() == [(3, 4)]
    finally:
        c.close()


# ------------------------------------------------------------------------------------------------
# note 8: raw rows weigh their measured seconds in rollup summaries
# ------------------------------------------------------------------------------------------------

def test_summary_weights_raw_rows_by_dt_ms_seconds(tmp_path, q_app):
    """One legacy minute at 100 B/s (60 s) + six 10-second raw rows at 1000 B/s (60 s): the honest mean
    over the 120 measured seconds is 550 B/s. Weighting each raw row as ONE (the step-3 reader) gives
    (100*60 + 1000*6) / 66 = 181.8 B/s - the most recent minute under-weighted tenfold."""
    s = _unstarted_state(tmp_path)
    w = prepare_v8(s, tmp_path / DB)
    try:
        now = int(time.time())
        old = (now - 3 * 86400) // 60 * 60
        for i in range(6):
            w._writer.add_net_sample(M_sample(now - 600 + 10 * i, {"Ethernet": (10_000, 10_000)}, dt_ms=10_000))
        w._writer.flush(now)
        iid = w.conn.execute("SELECT iface_id FROM interfaces").fetchone()[0]
        w.conn.execute("INSERT INTO net_minute (ts, iface_id, up_avg, down_avg, up_max, down_max, sample_count) "
                       "VALUES (?, ?, 100, 100, 100, 100, 60)", (old, iid))
        # No watermarks yet (a fresh file before its first pass): the reader's fallback rule applies.
        summ = s.summarize_network("upload", datetime.fromtimestamp(old - 60), datetime.fromtimestamp(now + 1), None, 10.0)
        assert summ.tier == "minute"
        assert summ.avg == pytest.approx(550.0)
        assert summ.count == 120
        assert summ.coverage_pct == pytest.approx(round(120 / (now + 1 - old + 60) * 100, 1), abs=0.11)
    finally:
        w._close_connection()
        s.cleanup()


# ------------------------------------------------------------------------------------------------
# end to end: exact volume at the 2 s and 10 s presets
# ------------------------------------------------------------------------------------------------

def _feed(s, start: int, poll_s: float, salt: int):
    """10 minutes of polls ending at start + 600, flushed on the 10 s tick. Returns the exact sums."""
    n = int(600 / poll_s)
    per_flush = max(1, int(10 / poll_s))
    tot_up = tot_down = 0
    for i in range(n):
        up, down = 1_000 + 37 * i + salt, 5_000 + 101 * (i % 7)   # odd, uneven numbers
        tot_up += up
        tot_down += down
        s.add_net_sample(start + (i + 1) * poll_s, poll_s * 1000, {"Wi-Fi": (up, down), "Idle": (0, 0)})
        if (i + 1) % per_flush == 0:
            s.flush_batch()                                   # the 10 s persist tick
    s.flush_and_wait(timeout=10)
    return n, tot_up, tot_down


@pytest.mark.parametrize("poll_s", [2.0, 10.0])
def test_ten_minutes_of_polls_store_exactly_the_byte_deltas(tmp_path, q_app, poll_s):
    """Through the real path (WidgetState buffer -> 10 s persist task -> worker thread -> WriterV8) at
    a preset: the V8 reader's volume equals the sum of the byte deltas EXACTLY, whatever poll rate the
    reader is asked about - for 10 minutes still in the raw tier, and for 10 minutes the real
    maintenance pass rolls into minute rows (written 26 h ago: below the raw watermark the startup pass
    stamped, so invisible until that roll-up - the writer's contract)."""
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    cfg["update_rate"] = poll_s
    s = _started_state(tmp_path, cfg)
    try:
        s.batch_persist_timer.stop()
        now = int(time.time())
        recent = (now - 2 * 3600) // 60 * 60
        old = (now - 26 * 3600) // 60 * 60
        n, r_up, r_down = _feed(s, recent, poll_s, 0)
        _, o_up, o_down = _feed(s, old, poll_s, 3)
        w_recent = (datetime.fromtimestamp(recent), datetime.fromtimestamp(recent + 601))
        w_old = (datetime.fromtimestamp(old), datetime.fromtimestamp(old + 601))

        for p in (1.0, 2.0, 10.0):                           # the reader's poll interval is irrelevant
            v = s.history_reader.volume(w_recent[0], w_recent[1], None, p)
            assert (v.up_bytes, v.down_bytes, v.exact_up_bytes, v.reconstructed) == (r_up, r_down, r_up, False)
        assert s.get_total_bandwidth_for_period(*w_recent) == (r_up, r_down)
        c = sqlite3.connect(sqlite_ro(tmp_path / DB), uri=True)
        try:
            assert c.execute("SELECT COUNT(*), MIN(dt_ms), MAX(dt_ms), SUM(flags & 2) FROM net_raw").fetchone() == \
                   (2 * n, poll_s * 1000, poll_s * 1000, 0)          # every poll stored, idle adapter not, no gaps
            assert c.execute("SELECT SUM(up_bytes), SUM(down_bytes) FROM net_raw WHERE ts <= ?",
                             (old + 601,)).fetchone() == (o_up, o_down)
        finally:
            c.close()

        s.trigger_maintenance()                               # the hourly pass: old raw -> minute
        s.flush_and_wait(timeout=10)
        c = sqlite3.connect(sqlite_ro(tmp_path / DB), uri=True)
        try:
            assert c.execute("SELECT COUNT(*) FROM net_raw WHERE ts <= ?", (old + 601,)).fetchone()[0] == 0
            assert c.execute("SELECT SUM(up_bytes), SUM(down_bytes), SUM(sample_count) FROM net_minute"
                             ).fetchone() == (o_up, o_down, 600)
        finally:
            c.close()
        for win, want in ((w_old, (o_up, o_down)), (w_recent, (r_up, r_down))):
            assert s.get_total_bandwidth_for_period(*win) == want
            assert s.get_history_volume_for_period(*win).reconstructed is False
    finally:
        s.cleanup()


# ------------------------------------------------------------------------------------------------
# exporter / odometer on v8
# ------------------------------------------------------------------------------------------------

def test_the_read_only_exporter_and_the_odometer_read_a_v8_file(tmp_path, q_app):
    s = _started_state(tmp_path)
    now = time.time()
    try:
        assert s._try_load_usage()
        s.add_usage_bytes(1_000.0, 2_000.0, window_start=now - 1)
        s._persist_usage_now()
        for i in range(30):
            s.add_net_sample(now - 30 + i, 1000, {"Wi-Fi": (100, 200)})
            s.add_hardware_stat("cpu", 40.0 + i, now=datetime.fromtimestamp(now - 30 + i))
        s.flush_and_wait(timeout=5)
    finally:
        s.cleanup()
    before = _sha(tmp_path / DB)
    with patch("netspeedtray.core.widget_state.get_app_data_path", return_value=tmp_path):
        ro = WidgetState(dict(constants.config.defaults.DEFAULT_CONFIG), read_only=True)
    try:
        assert ro.history_schema_version() == 8 and isinstance(ro.history_reader, V8HistoryReader)
        assert ro.get_usage_lifetime() == (1_000.0, 2_000.0)
        win = (datetime.fromtimestamp(now - 60), datetime.fromtimestamp(now + 1))
        assert ro.get_total_bandwidth_for_period(*win) == (3_000.0, 6_000.0)
        hw = ro.get_hardware_history("cpu", *win)
        assert len(hw) == 30
        from netspeedtray.utils.stats_exporter import export_window
        paths = export_window(ro, win[0], win[1], "w", str(tmp_path / "out"), "x", poll_interval=1.0)
        text = Path(paths["summary_csv"]).read_text(encoding="utf-8")
        assert "cpu" in text
    finally:
        ro.cleanup()
    assert _sha(tmp_path / DB) == before, "the read-only exporter wrote to the database"
