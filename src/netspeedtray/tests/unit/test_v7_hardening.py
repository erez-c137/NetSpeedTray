"""
v8 step 0 - "v7 hardening": four defects that make any later migration dangerous, fixed on the
v7 schema so they ship whether or not v8 does (database-v8-implementation-steps.md, step 0;
database-v8-final.md section 0.1; release-plan.md Gate 2 items 3-5).

1. History must hold MEASUREMENTS. The spike filter used to write its substitute value
   (int(recent_avg * 2.0), a number never measured) into speed_history_raw while the real one
   was discarded. Display smoothing stays; the DB gets what the counters said.
2. A failed or unverified pre-migration backup ABORTS the migration - the file stays on its old
   version with no schema change applied, and the next launch retries.
3. _verify_backup checks a STATIC per-version table set (EXPECTED_TABLES), never the live
   source's own table list - a damaged source missing a table would otherwise "verify" a backup
   that is missing the same table.
4. .bak retention is version-aware: the newest backup per schema version survives, so a v7
   backup (the rollback target) outlives any number of v8-era backups.

Everything runs against temp dirs and a real DatabaseWorker; nothing touches %APPDATA%.

Since the v8 switch-over (2.2): (1) is stronger - history stores the exact BYTE deltas the odometer
counts, before the filter; (2)-(3) guard the in-place chain a v2-v6 file still takes to v7 before the
v8 rebuild; (4) is unchanged. v6 / v7 fixture files are written by 2.1.x's worker (LegacyV7Worker).
"""
import os
import sqlite3
import time
from collections import deque
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QThread

from netspeedtray import constants
from netspeedtray.core.controller import StatsController
from netspeedtray.core.database import DatabaseWorker
from netspeedtray.core.widget_state import WidgetState
from netspeedtray.tests.legacy_v7_worker import LegacyV7Worker
from netspeedtray.tests.state_helpers import prepare_v8


class _NetIO:
    def __init__(self, bytes_sent: int, bytes_recv: int):
        self.bytes_sent = bytes_sent
        self.bytes_recv = bytes_recv


# --------------------------------------------------------------------------------------------
# 1. The spike filter is display-only; history stores the measured rate
# --------------------------------------------------------------------------------------------

def _drain(worker) -> None:
    """Run every queued task in order, as the worker thread would (the startup maintenance first)."""
    while not worker._queue.empty():
        item = worker._queue.get_nowait()
        if item is not None:
            worker._execute_task(*item)


@pytest.fixture
def real_state(tmp_path):
    """A real WidgetState + DatabaseWorker on a temp v8 DB, driven synchronously (thread not started)."""
    config = constants.config.defaults.DEFAULT_CONFIG.copy()
    with patch.object(QThread, "start", lambda self: None):
        with patch("netspeedtray.core.widget_state.get_app_data_path", return_value=tmp_path):
            state = WidgetState(config)
        worker = prepare_v8(state, tmp_path / "speed_history.db")
        yield state, worker, config
        worker._close_connection()
        state.cleanup()


def test_spike_reaches_the_db_unfiltered_while_the_display_is_filtered(real_state):
    state, worker, config = real_state
    config["interface_mode"] = "all_physical"
    controller = StatsController(config=config, widget_state=state)
    controller.primary_interface = None
    view = MagicMock()
    controller.set_view(view)

    name = "Ethernet"
    controller.recent_speeds[name] = deque([(2_000_000, 2_000_000)] * 10, maxlen=20)  # 2 MB/s baseline
    t0 = 1000.0  # fixed clock: (t0 + 1.0) - t0 must be exactly 1.0 (a real monotonic near 2**k rounds it)
    w0 = time.time()
    controller.last_check_time = t0
    controller.last_interface_counters = {name: _NetIO(0, 0)}
    controller._last_sample_time = (t0, w0)

    # One isolated poll at 12 MB/s - 6x the baseline, so the filter fires and caps it at 2x.
    with patch("time.monotonic", return_value=t0 + 1.0):
        controller._handle_network_counters({name: _NetIO(12_000_000, 12_000_000)}, (t0 + 1.0, w0 + 1.0))

    # The display is smoothed: 2x baseline = 4 MB/s = 32 Mbps, not the measured 96 Mbps.
    up_mbps, down_mbps = view.update_display_speeds.call_args[0]
    assert up_mbps == pytest.approx(32.0) and down_mbps == pytest.approx(32.0)

    # The sample bound for disk carries the measurement: the exact byte delta over the measured 1 s.
    assert len(state._net_batch) == 1
    sample = state._net_batch[0]
    assert sample.byte_deltas[name] == (12_000_000, 12_000_000) and sample.dt_ms == 1000, (
        "the spike filter's substitute value (2x baseline) reached the history instead of the measurement")

    # ...and it lands in net_raw as measured bytes, through the real DatabaseWorker.
    state.flush_batch()
    _drain(worker)
    row = worker.conn.execute(
        "SELECT up_bytes, down_bytes, dt_ms FROM net_raw n JOIN interfaces i USING (iface_id) "
        "WHERE i.name = ?", (name,)).fetchone()
    assert row == (12_000_000, 12_000_000, 1000)


def test_unfiltered_persist_still_honours_the_ceiling_and_the_noise_floor(real_state):
    """An idle adapter writes no row (coverage comes from app_sessions), and a counter jump past the
    100 Gbps ceiling is clipped exactly as the odometer clips it - the same deltas feed both."""
    state, worker, config = real_state
    controller = StatsController(config=config, widget_state=state)
    controller.set_view(MagicMock())
    t0, w0 = 1000.0, time.time()  # fixed clock: (t0 + 1.0) - t0 must be exactly 1.0 (a real monotonic near 2**k rounds it)
    first = {"Wi-Fi": _NetIO(0, 0), "idle": _NetIO(5, 5), "glitch": _NetIO(0, 0)}
    with patch("time.monotonic", return_value=t0):
        controller._handle_network_counters(first, (t0, w0))              # primes
    with patch("time.monotonic", return_value=t0 + 1.0):
        controller._handle_network_counters(
            {"Wi-Fi": _NetIO(100, 100), "idle": _NetIO(5, 5), "glitch": _NetIO(10 ** 15, 0)},
            (t0 + 1.0, w0 + 1.0))
    state.flush_batch()
    _drain(worker)
    rows = dict(worker.conn.execute(
        "SELECT i.name, n.up_bytes FROM net_raw n JOIN interfaces i USING (iface_id)").fetchall())
    ceiling = constants.network.interface.MAX_REASONABLE_SPEED_BPS * 1.0
    assert rows.keys() == {"Wi-Fi", "glitch"}            # 'idle' moved nothing -> no row
    assert rows["Wi-Fi"] == 100 and rows["glitch"] <= ceiling * 1.01


# --------------------------------------------------------------------------------------------
# 2 + 3. Backup verification and the migration abort
# --------------------------------------------------------------------------------------------

def _make_v6_db(path: Path, drop_table: str = "") -> None:
    """A real v6 database (the v7 schema minus usage_counter), with data in it - written by 2.1.x."""
    w = LegacyV7Worker(path)
    w._initialize_connection()
    w._check_and_create_schema()                      # fresh v7
    w.conn.execute(f"DROP TABLE {constants.data.USAGE_COUNTER_TABLE}")
    w.conn.execute("UPDATE metadata SET value='6' WHERE key='db_version'")
    w.conn.executemany(
        "INSERT INTO speed_history_raw (timestamp, interface_name, upload_bytes_sec, download_bytes_sec) "
        "VALUES (?,?,?,?)", [(1787000000 + i, "Wi-Fi", 1.0e6, 2.0e6) for i in range(300)])
    if drop_table:
        w.conn.execute(f"DROP TABLE {drop_table}")
    w.conn.commit()
    w._close_connection()


def _tables(conn) -> set:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_forced_backup_failure_aborts_the_migration(tmp_path, monkeypatch):
    db = tmp_path / "speed_history.db"
    _make_v6_db(db)

    w = DatabaseWorker(db)
    monkeypatch.setattr(DatabaseWorker, "_backup_database", lambda self: False)
    w._prepare_storage()
    assert w._schema_incompatible and w.conn is None      # read-only, no write connection

    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as ro:
        version = ro.execute("SELECT value FROM metadata WHERE key='db_version'").fetchone()[0]
        tables = _tables(ro)
        rows = ro.execute("SELECT COUNT(*) FROM speed_history_raw").fetchone()[0]
    assert version == "6", "a migration ran without a verified backup"
    assert constants.data.USAGE_COUNTER_TABLE not in tables, "the v6->v7 schema change was applied"
    assert rows == 300


def test_backup_raising_also_aborts_the_migration(tmp_path, monkeypatch):
    """A disk-full / lock failure inside VACUUM INTO surfaces as False from _backup_database."""
    db = tmp_path / "speed_history.db"
    _make_v6_db(db)

    def _boom(*_a, **_k):
        raise sqlite3.OperationalError("database or disk is full")
    monkeypatch.setattr(DatabaseWorker, "_verify_backup", _boom)
    w = DatabaseWorker(db)
    w._prepare_storage()

    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as ro:
        assert ro.execute("SELECT value FROM metadata WHERE key='db_version'").fetchone()[0] == "6"
    assert w._schema_incompatible
    assert list(tmp_path.glob("*.bak.*")) == []


def test_verified_backup_lets_the_migration_run(tmp_path):
    """The control: the same v6 file migrates when the backup verifies - to v7 in place (a verified
    v6 copy first), then rebuilt as v8 (the v7 file itself kept as the backup)."""
    db = tmp_path / "speed_history.db"
    _make_v6_db(db)

    w = DatabaseWorker(db)
    w._prepare_storage()
    version = w._get_current_db_version()
    tables = _tables(w.conn)
    w._close_connection()

    assert version == DatabaseWorker._DB_VERSION == 8
    assert constants.data.USAGE_COUNTER_TABLE in tables
    assert len(list(tmp_path.glob("speed_history.db.bak.v6_*"))) == 1
    assert len(list(tmp_path.glob("speed_history.db.bak.v7_*"))) == 1


def test_a_source_missing_a_table_fails_verification(tmp_path):
    """A damaged source missing a history table must not 'verify' a backup missing it too."""
    db = tmp_path / "speed_history.db"
    _make_v6_db(db, drop_table=constants.data.HARDWARE_STATS_TABLE_HOUR)

    w = DatabaseWorker(db)
    w._initialize_connection()
    try:
        assert w._backup_database() is False
        assert w._get_current_db_version() == 6
    finally:
        w._close_connection()
    assert list(tmp_path.glob("*.bak.*")) == [], "an unverified backup was left on disk"


def test_a_source_missing_a_table_does_not_migrate(tmp_path):
    db = tmp_path / "speed_history.db"
    _make_v6_db(db, drop_table=constants.data.HARDWARE_STATS_TABLE_HOUR)

    w = DatabaseWorker(db)
    w._prepare_storage()
    assert w._schema_incompatible
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as ro:
        assert ro.execute("SELECT value FROM metadata WHERE key='db_version'").fetchone()[0] == "6"


def test_expected_tables_matches_the_fresh_schema(tmp_path):
    """EXPECTED_TABLES is static - pin each entry to what a fresh build of that version actually
    creates, so a future schema bump that forgets to extend it fails here, not in a user's migration."""
    w = DatabaseWorker(tmp_path / "v8.db")
    w._prepare_storage()
    try:
        assert _tables(w.conn) == set(DatabaseWorker.EXPECTED_TABLES[DatabaseWorker._DB_VERSION])
    finally:
        w._close_connection()
    legacy = LegacyV7Worker(Path(":memory:"))
    legacy.conn = sqlite3.connect(":memory:")
    try:
        legacy._check_and_create_schema()
        assert _tables(legacy.conn) == set(DatabaseWorker.EXPECTED_TABLES[7])
    finally:
        legacy.conn.close()


def test_unknown_version_fails_verification(tmp_path):
    db = tmp_path / "speed_history.db"
    _make_v6_db(db)
    w = DatabaseWorker(db)
    w._initialize_connection()
    try:
        w.conn.execute("UPDATE metadata SET value='99' WHERE key='db_version'")
        w.conn.commit()
        assert w._backup_database() is False   # no table set is known for v99
    finally:
        w._close_connection()


# --------------------------------------------------------------------------------------------
# 4. Version-aware retention
# --------------------------------------------------------------------------------------------

def _fake_bak(folder: Path, version: int, stamp: str, age_s: float) -> Path:
    p = folder / f"speed_history.db.bak.v{version}_{stamp}"
    p.write_bytes(b"x")
    t = time.time() - age_s
    os.utime(p, (t, t))
    return p


def test_v7_backup_survives_three_v8_backups(tmp_path):
    w = DatabaseWorker(tmp_path / "speed_history.db")
    v7 = _fake_bak(tmp_path, 7, "20261101_000000", age_s=4000)
    v8 = [_fake_bak(tmp_path, 8, f"2026110{i}_000000", age_s=3000 - i * 1000) for i in (2, 3, 4)]

    w._prune_old_backups()

    left = set(tmp_path.glob("*.bak.*"))
    assert v7 in left, "the v7 rollback target was pruned by v8-era backups"
    assert v8[2] in left and v8[1] in left      # newest 2 overall
    assert v8[0] not in left                    # an older same-version backup still goes
    assert len(left) == 3


def test_retention_keeps_newest_per_version_and_newest_two(tmp_path):
    w = DatabaseWorker(tmp_path / "speed_history.db")
    old6 = _fake_bak(tmp_path, 6, "20260101_000000", age_s=9000)
    new6 = _fake_bak(tmp_path, 6, "20260201_000000", age_s=8000)
    old7 = _fake_bak(tmp_path, 7, "20260301_000000", age_s=7000)
    new7 = _fake_bak(tmp_path, 7, "20260401_000000", age_s=6000)
    a8 = _fake_bak(tmp_path, 8, "20260501_000000", age_s=3000)
    b8 = _fake_bak(tmp_path, 8, "20260601_000000", age_s=2000)
    c8 = _fake_bak(tmp_path, 8, "20260701_000000", age_s=1000)

    w._prune_old_backups()

    assert set(tmp_path.glob("*.bak.*")) == {new6, new7, b8, c8}
    assert not any(p.exists() for p in (old6, old7, a8))


def test_a_backups_sidecars_are_never_mistaken_for_backups(tmp_path):
    """Found live (step 7): anything that opens a backup - DB Browser, a read-only check - leaves
    `<backup>-wal` / `-shm` beside it, and both match `.db.bak.v{N}_`. 2.1.7 then named the -shm as the
    file to restore. Pruning must likewise never keep a sidecar in place of its backup, and must take a
    pruned backup's sidecars with it."""
    w = DatabaseWorker(tmp_path / "speed_history.db")
    v7 = _fake_bak(tmp_path, 7, "20261101_000000", age_s=5000)
    for side in ("-wal", "-shm"):
        p = tmp_path / (v7.name + side)
        p.write_bytes(b"s")                         # NEWER than the backup, as an open leaves them
    old6 = _fake_bak(tmp_path, 6, "20260101_000000", age_s=9000)
    new6 = _fake_bak(tmp_path, 6, "20260201_000000", age_s=8000)
    (tmp_path / (old6.name + "-shm")).write_bytes(b"s")
    assert w._find_newest_backup() == v7
    w._prune_old_backups()
    left = {p.name for p in tmp_path.glob("*.bak.*")}
    assert v7.name in left and new6.name in left and old6.name not in left
    assert old6.name + "-shm" not in left, "a pruned backup's sidecar was left behind"
    assert w._find_newest_backup() == v7


def test_rollback_still_finds_the_v7_backup_after_v8_pruning(tmp_path):
    """_find_newest_backup of a ROLLED-BACK 2.1.x (the downgrade flyout's restore target) must name the
    survivor after 2.2-era pruning."""
    w = DatabaseWorker(tmp_path / "speed_history.db")
    v7 = _fake_bak(tmp_path, 7, "20261101_000000", age_s=4000)
    for i in (2, 3, 4):
        _fake_bak(tmp_path, 8, f"2026110{i}_000000", age_s=3000 - i * 100)
    w._prune_old_backups()
    assert LegacyV7Worker(tmp_path / "speed_history.db")._find_newest_backup() == v7
