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


class _NetIO:
    def __init__(self, bytes_sent: int, bytes_recv: int):
        self.bytes_sent = bytes_sent
        self.bytes_recv = bytes_recv


# --------------------------------------------------------------------------------------------
# 1. The spike filter is display-only; history stores the measured rate
# --------------------------------------------------------------------------------------------

@pytest.fixture
def real_state(tmp_path):
    """A real WidgetState + DatabaseWorker on a temp DB, driven synchronously (thread not started)."""
    config = constants.config.defaults.DEFAULT_CONFIG.copy()
    with patch.object(QThread, "start", lambda self: None):
        with patch("netspeedtray.core.widget_state.get_app_data_path", return_value=tmp_path):
            state = WidgetState(config)
        worker = state.db_worker
        worker.db_path = tmp_path / "speed_history.db"
        worker._initialize_connection()
        worker._check_and_create_schema()
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
    t0 = time.monotonic()
    controller.last_check_time = t0
    controller.last_interface_counters = {name: _NetIO(0, 0)}

    # One isolated poll at 12 MB/s - 6x the baseline, so the filter fires and caps it at 2x.
    with patch("time.monotonic", return_value=t0 + 1.0):
        controller._handle_network_counters({name: _NetIO(12_000_000, 12_000_000)})

    # The display is smoothed: 2x baseline = 4 MB/s = 32 Mbps, not the measured 96 Mbps.
    up_mbps, down_mbps = view.update_display_speeds.call_args[0]
    assert up_mbps == pytest.approx(32.0) and down_mbps == pytest.approx(32.0)

    # The batch bound for disk carries the measurement.
    batch = [row for row in state._db_batch if row[1] == name]
    assert len(batch) == 1
    assert batch[0][2] == 12_000_000 and batch[0][3] == 12_000_000, (
        "the spike filter's substitute value (2x baseline) was persisted instead of the measured rate")

    # ...and it lands in speed_history_raw as measured, through the real DatabaseWorker.
    worker._persist_speed_batch(list(state._db_batch))
    row = worker.conn.execute(
        "SELECT upload_bytes_sec, download_bytes_sec FROM speed_history_raw WHERE interface_name=?",
        (name,)).fetchone()
    assert row == (12_000_000.0, 12_000_000.0)


def test_unfiltered_persist_still_honours_the_ceiling_and_the_noise_floor(real_state):
    """The measured path keeps the two guards add_speed_data always applied to the batch."""
    state, _worker, _config = real_state
    state.add_speed_data({"Wi-Fi": (100.0, 100.0)},
                         persist_data={"Wi-Fi": (100.0, 100.0), "idle": (0.0, 0.0)})
    names = [row[1] for row in state._db_batch]
    assert names == ["Wi-Fi"]   # 'idle' is under MIN_RECORDABLE_SPEED_BPS -> not recorded


# --------------------------------------------------------------------------------------------
# 2 + 3. Backup verification and the migration abort
# --------------------------------------------------------------------------------------------

def _make_v6_db(path: Path, drop_table: str = "") -> None:
    """A real v6 database (the v7 schema minus usage_counter), with data in it."""
    w = DatabaseWorker(path)
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
    w._initialize_connection()
    monkeypatch.setattr(w, "_backup_database", lambda: False)
    w._check_and_create_schema()
    w._close_connection()

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

    w = DatabaseWorker(db)
    w._initialize_connection()

    def _boom(*_a, **_k):
        raise sqlite3.OperationalError("database or disk is full")
    monkeypatch.setattr(w, "_verify_backup", _boom)
    w._check_and_create_schema()
    version = w._get_current_db_version()
    w._close_connection()

    assert version == 6
    assert list(tmp_path.glob("*.bak.*")) == []


def test_verified_backup_lets_the_migration_run(tmp_path):
    """The control: the same v6 file migrates when the backup verifies."""
    db = tmp_path / "speed_history.db"
    _make_v6_db(db)

    w = DatabaseWorker(db)
    w._initialize_connection()
    w._check_and_create_schema()
    version = w._get_current_db_version()
    tables = _tables(w.conn)
    w._close_connection()

    assert version == DatabaseWorker._DB_VERSION
    assert constants.data.USAGE_COUNTER_TABLE in tables
    assert len(list(tmp_path.glob("speed_history.db.bak.v6_*"))) == 1


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
    w._initialize_connection()
    w._check_and_create_schema()
    version = w._get_current_db_version()
    w._close_connection()
    assert version == 6


def test_expected_tables_matches_the_fresh_schema():
    """EXPECTED_TABLES[_DB_VERSION] is static - pin it to what a fresh build actually creates,
    so a future schema bump that forgets to extend it fails here, not in a user's migration."""
    w = DatabaseWorker(Path(":memory:"))
    w.conn = sqlite3.connect(":memory:")
    try:
        w._check_and_create_schema()
        assert _tables(w.conn) == set(DatabaseWorker.EXPECTED_TABLES[DatabaseWorker._DB_VERSION])
    finally:
        w.conn.close()


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


def test_rollback_still_finds_the_v7_backup_after_v8_pruning(tmp_path):
    """_find_newest_backup (the downgrade flyout's restore target) must name the survivor."""
    w = DatabaseWorker(tmp_path / "speed_history.db")
    v7 = _fake_bak(tmp_path, 7, "20261101_000000", age_s=4000)
    for i in (2, 3, 4):
        _fake_bak(tmp_path, 8, f"2026110{i}_000000", age_s=3000 - i * 100)
    w._prune_old_backups()
    assert w._find_newest_backup() == v7
