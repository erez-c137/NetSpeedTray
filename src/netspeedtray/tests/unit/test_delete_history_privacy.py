"""
Privacy review 2.2 (finding 1) and data-integrity review (A, B): what "Delete all history" leaves behind.

  * Backups. The v8 upgrade keeps the whole 2.1 file as ``speed_history.db.bak.v7_<ts>`` and nothing
    ever removed it - Delete all history emptied the live file and left every pre-upgrade row (and
    every adapter name) next to it. Now the delete zeroes and removes every ``speed_history.db.bak*``
    file (sidecars included); a backup it cannot remove yet (locked) is retried like a busy checkpoint.
    Harness: v7 fixture -> migrate -> delete -> no .bak file and no adapter name in ANY file's bytes.
  * The same goes for ``speed_history.db.stray_*`` (+ sidecars): files migrate_v8.recover() moved off
    the live name, which can be a whole 2.1.x-recorded history file.
  * Backups (and strays) also age out with "Keep history for": maintenance removes one older than the retention
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
import stat
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
    """Every file that can hold an old history copy: backups AND recover()'s moved-aside strays."""
    return sorted(p.name for p in list(d.glob(f"{DB}.bak*")) + list(d.glob(f"{DB}{M.STRAY_INFIX}*")))


def _add_stray(d: Path, stamp: str = "20261019_120000") -> Path:
    """What migrate_v8.recover() leaves when it moves a file off the live name (_move_aside): here a
    2.1.x-recorded history file (a copy of the v7 backup) with a hot -wal beside it."""
    bak = next(d.glob(f"{DB}.bak.v7_*"))
    stray = d / f"{DB}{M.STRAY_INFIX}{stamp}"
    stray.write_bytes(bak.read_bytes())
    Path(str(stray) + "-wal").write_bytes(b"Tailscale" * 50)
    return stray


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
    _add_stray(tmp_path)
    assert _names_on_disk(tmp_path), "the fixture must carry adapter names before the delete"
    got = []
    w.history_deleted.connect(got.append)
    w._execute_task("delete_history", {"include_usage": True})
    assert got == [DatabaseWorker.DELETE_DONE]
    assert _bak_files(tmp_path) == [], "Delete all history left a database backup / stray file behind"
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


def test_a_locked_stray_file_is_retried_like_a_backup(tmp_path, no_retry_sleep):
    w = _migrated_worker(tmp_path)
    stray = _add_stray(tmp_path)
    got = []
    w.history_deleted.connect(got.append)
    with open(stray, "rb"):
        w._execute_task("delete_history", {"include_usage": False})
        assert stray.exists() and got == [DatabaseWorker.DELETE_COMPACT_PENDING]
    w._retry_pending_compaction(force=True)
    assert _bak_files(tmp_path) == []
    w._close_connection()


def test_backups_are_kept_while_a_migration_journal_exists(tmp_path, no_retry_sleep):
    w = _migrated_worker(tmp_path)
    _add_stray(tmp_path)
    before = _bak_files(tmp_path)
    M.journal_path_for(tmp_path / DB).write_text("{}")
    w._execute_task("delete_history", {"include_usage": False})
    assert _bak_files(tmp_path) == before, "recover() needs the v7 backup while a journal exists"
    w._close_connection()


# --- backups age out with "Keep history for" -------------------------------------------------------------

def _touch(p: Path, when: datetime, data: bytes = b"x") -> Path:
    p.write_bytes(data)
    os.utime(p, (when.timestamp(), when.timestamp()))
    return p


def _fake_bak(d: Path, version: int, when: datetime) -> Path:
    return _touch(d / f"{DB}.bak.v{version}_{when:%Y%m%d_%H%M%S}", when)


def _fake_stray(d: Path, when: datetime, suffix: str = "") -> Path:
    return _touch(d / f"{DB}{M.STRAY_INFIX}{when:%Y%m%d_%H%M%S}{suffix}", when)


def _aging_worker(d: Path, now: datetime, migrated: bool = True) -> DatabaseWorker:
    """A v8 worker whose clock has run steadily (so age-out is armed) and - unless ``migrated`` is
    False - whose live file is a completed v7 -> v8 migration."""
    w = DatabaseWorker(d / DB)
    w._prepare_storage()
    c = w.conn
    c.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('backup_clock_continuous_since', ?)",
              (str(int(now.timestamp()) - 30 * 86400),))
    if migrated:
        c.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                  (DatabaseWorker.MIGRATED_FROM_V7_KEY, str(int(now.timestamp()) - 400 * 86400)))
    return w


def _mark_seen(w: DatabaseWorker, d: Path, days: float = 60) -> None:
    """Every backup present has been OBSERVED by the app for ``days`` of steady time."""
    for p in w._history_backup_files():
        w.conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                       (DatabaseWorker._SEEN_PREFIX + p.name.lower(), str(int(days * 86400))))


def _age(w: DatabaseWorker, retention, now, prev, mono=None) -> None:
    """One age-out pass over files the app has long observed (the 30-day floor met by observed time),
    so what is under test is the date / clock / migration rule."""
    _mark_seen(w, w.db_path.parent)
    w._age_out_backups(retention, now, prev, mono)


def _steady(now: datetime) -> int:
    """The previous maintenance pass, an hour before ``now``."""
    return int(now.timestamp()) - 3600


def test_backups_age_out_with_the_retention_window(tmp_path):
    now = datetime(2026, 10, 20, 12, 0, 0)
    w = _aging_worker(tmp_path, now)
    old = _fake_bak(tmp_path, 7, now - timedelta(days=400))
    _touch(Path(str(old) + "-wal"), now - timedelta(days=400))
    young = _fake_bak(tmp_path, 6, now - timedelta(days=100))
    old_stray = _fake_stray(tmp_path, now - timedelta(days=400))
    _touch(Path(str(old_stray) + "-shm"), now - timedelta(days=400))
    young_stray = _fake_stray(tmp_path, now - timedelta(days=100), "_1")
    legacy = _touch(tmp_path / f"{DB}.bak", now - timedelta(days=400))      # 2.0-era bare .bak
    _age(w, 365, now, _steady(now))
    assert not old.exists() and not Path(str(old) + "-wal").exists()
    assert young.exists()
    assert not old_stray.exists() and not Path(str(old_stray) + "-shm").exists(), "strays age out too"
    assert young_stray.exists()
    assert not legacy.exists(), "the legacy bare .bak ages out too"
    w._close_connection()


def test_a_fresh_backup_survives_a_short_retention_for_the_rollback_window(tmp_path):
    now = datetime(2026, 10, 20, 12, 0, 0)
    w = _aging_worker(tmp_path, now)
    recent = _fake_bak(tmp_path, 7, now - timedelta(days=D.retention.BACKUP_MIN_KEEP_DAYS - 5))
    stale = _fake_bak(tmp_path, 6, now - timedelta(days=D.retention.BACKUP_MIN_KEEP_DAYS + 5))
    _age(w, 1, now, _steady(now))      # a hand-edited 1-day retention
    assert recent.exists(), "never removed sooner than BACKUP_MIN_KEEP_DAYS after the upgrade"
    assert not stale.exists()
    w._close_connection()


def test_keep_everything_keeps_every_backup(tmp_path):
    now = datetime(2026, 10, 20, 12, 0, 0)
    w = _aging_worker(tmp_path, now)
    ancient = _fake_bak(tmp_path, 7, now - timedelta(days=3000))
    stray = _fake_stray(tmp_path, now - timedelta(days=3000))
    _age(w, D.retention.UNLIMITED_DAYS, now, _steady(now))
    assert ancient.exists() and stray.exists()
    w._close_connection()


def test_the_hourly_maintenance_ages_backups_out(tmp_path):
    now = datetime.now()
    w = _aging_worker(tmp_path, now)
    w.conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('last_maintenance_at', ?)",
                   (str(_steady(now)),))
    old = _fake_bak(tmp_path, 7, now - timedelta(days=500))
    _mark_seen(w, tmp_path)
    w._run_maintenance({"keep_data": 365}, now=now)
    assert not old.exists()
    w._close_connection()


# --- review #376 item 1: a wrong clock / a stuck upgrade never costs the only copy -----------------------

def test_a_clock_jump_to_2030_keeps_a_day_old_backup(tmp_path, no_retry_sleep):
    """Harness rr376/scope.py AGE365_CLOCK2030: the pass that prunes the live rows with a clock set to
    2030 used to scrub a 1-day-old .bak.v7 too - the one copy left."""
    w = _migrated_worker(tmp_path)
    real = datetime.now()
    w.conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('last_maintenance_at', ?)",
                   (str(int(real.timestamp())),))
    # The fixture keeps everything; a 1-year retention already in force (no 48 h grace in the way).
    w.conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('current_retention_days', '365')")
    w.conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('backup_clock_continuous_since', ?)",
                   (str(int(real.timestamp()) - 30 * 86400),))   # it HAD been running steadily
    bak = next(tmp_path.glob(f"{DB}.bak.v7_*"))
    stray = _fake_stray(tmp_path, real - timedelta(days=1))
    _mark_seen(w, tmp_path)             # observed long enough: only the clock guard stands in the way
    wrong = datetime(2030, 1, 1, 12, 0, 0)
    w._run_maintenance({"keep_data": 365}, now=wrong)
    for h in range(1, 48):              # two days of hourly passes on the wrong clock
        w._run_maintenance({"keep_data": 365}, now=wrong + timedelta(hours=h))
    assert bak.exists() and stray.exists(), "a clock jump aged out a day-old backup"
    w._close_connection()


def test_age_out_resumes_once_the_clock_has_run_steadily(tmp_path):
    now = datetime(2026, 10, 20, 12, 0, 0)
    w = DatabaseWorker(tmp_path / DB)
    w._prepare_storage()
    w.conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, '1')", (DatabaseWorker.MIGRATED_FROM_V7_KEY,))
    old = _fake_bak(tmp_path, 7, now - timedelta(days=400))
    _mark_seen(w, tmp_path)
    w._age_out_backups(365, now, None)                       # first pass ever: not armed yet
    assert old.exists()
    ts = int(now.timestamp())          # stepped in epoch seconds: a naive +1 h skips an hour at DST
    for _ in range(D.retention.BACKUP_CLOCK_CONTINUITY_DAYS * 24 - 1):
        w._age_out_backups(365, datetime.fromtimestamp(ts + 3600), ts)
        ts += 3600
    assert old.exists(), "armed only after BACKUP_CLOCK_CONTINUITY_DAYS of steady running"
    w._age_out_backups(365, datetime.fromtimestamp(ts + 7200), ts)
    assert not old.exists()
    w._close_connection()


def test_a_backup_dated_in_the_future_is_kept(tmp_path):
    now = datetime(2026, 10, 20, 12, 0, 0)
    w = _aging_worker(tmp_path, now)
    future = _fake_bak(tmp_path, 7, now + timedelta(days=30))
    _age(w, 31, now + timedelta(days=0), _steady(now))
    assert future.exists()
    w._close_connection()


def test_age_is_from_the_later_of_name_date_and_mtime(tmp_path):
    now = datetime(2026, 10, 20, 12, 0, 0)
    w = _aging_worker(tmp_path, now)
    p = tmp_path / f"{DB}.bak.v7_{now - timedelta(days=400):%Y%m%d_%H%M%S}"
    _touch(p, now - timedelta(days=2))            # old name, recent file: not old
    q = _touch(tmp_path / f"{DB}.bak.v6_{now - timedelta(days=2):%Y%m%d_%H%M%S}", now - timedelta(days=400))
    _age(w, 365, now, _steady(now))
    assert p.exists() and q.exists()
    w._close_connection()


def test_a_stuck_upgrade_keeps_its_v7_backup(tmp_path):
    """Reviewer's case: the journal was deleted by hand, a FRESH v8 file was created, and the
    .bak.v7 is the only full history. Not a completed migration -> never aged out."""
    now = datetime(2026, 10, 20, 12, 0, 0)
    w = _aging_worker(tmp_path, now, migrated=False)
    bak = _fake_bak(tmp_path, 7, now - timedelta(days=400))
    stray = _fake_stray(tmp_path, now - timedelta(days=400))
    legacy = _touch(tmp_path / f"{DB}.bak", now - timedelta(days=400))
    _age(w, 31, now, _steady(now))
    assert bak.exists() and stray.exists() and legacy.exists()
    w._close_connection()


def test_a_completed_migration_is_stamped(tmp_path):
    w = _migrated_worker(tmp_path)
    rows = dict(w.conn.execute("SELECT key, value FROM metadata WHERE key IN (?, ?)",
                               (DatabaseWorker.MIGRATED_FROM_V7_KEY, DatabaseWorker.MIGRATED_FROM_V7_BACKUP_KEY)))
    assert rows[DatabaseWorker.MIGRATED_FROM_V7_KEY].isdigit()
    assert rows[DatabaseWorker.MIGRATED_FROM_V7_BACKUP_KEY] == next(tmp_path.glob(f"{DB}.bak.v7_*")).name
    w._close_connection()
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    w2 = DatabaseWorker(fresh / DB)
    w2._prepare_storage()
    assert w2.conn.execute("SELECT 1 FROM metadata WHERE key = ?",
                           (DatabaseWorker.MIGRATED_FROM_V7_KEY,)).fetchone() is None
    w2._close_connection()


def test_a_bad_file_does_not_stop_the_pass_or_get_logged_as_removed(tmp_path, caplog):
    now = datetime(2026, 10, 20, 12, 0, 0)
    w = _aging_worker(tmp_path, now)
    d = tmp_path / f"{DB}.bak.v7_{now - timedelta(days=500):%Y%m%d_%H%M%S}_9"
    d.mkdir()                                     # a directory with an app-like name
    old = _fake_bak(tmp_path, 6, now - timedelta(days=500))
    held = _fake_bak(tmp_path, 5, now - timedelta(days=500))
    with open(held, "rb"), caplog.at_level("INFO"):
        _age(w, 365, now, _steady(now))
    assert d.is_dir() and not old.exists() and held.exists()
    removed = [r.getMessage() for r in caplog.records if "removed." in r.getMessage()]
    assert len(removed) == 1 and old.name in removed[0]
    w._close_connection()


# --- review #376 item 5: only the app's own file names ----------------------------------------------------

USER_FILES = ["speed_history.db.backup", "speed_history.db.bak_mine.zip", "speed_history.db.bak.handmade",
              "speed_history (copy).db", "speed_history.db.bak.v7_20261019_120000_copy", "speed_history.db.bak.v7_x", "speed_history.db.bak.v7_20261019_120000.zip",
              "netspeedtray.conf", "migrate_v8.journal.keep"]
APP_FILES = ["speed_history.db.bak", "speed_history.db.bak-wal",
             "speed_history.db.bak.v7_20261019_120000", "speed_history.db.bak.v7_20261019_120000-wal",
             "speed_history.db.bak.v7_20261019_120000-shm", "speed_history.db.bak.v7_20261019_120000_1",
             "speed_history.db.bak.v6_20250101_000000-journal", "speed_history.db.stray_20260101_000000",
             "speed_history.db.stray_20260101_000000_2-wal",
             # NTFS is case-insensitive: these ARE the app's files (review #376 item 4)
             "SPEED_HISTORY.DB.BAK.V7_20200101_000000", "Speed_History.db.Stray_20200101_000000",
             "speed_history.db.BAK.v6_20200101_000000-WAL"]


def test_delete_all_history_removes_only_the_apps_own_backup_names(tmp_path):
    for n in USER_FILES + APP_FILES:
        (tmp_path / n).write_bytes(b"DATA" * 100)
    w = DatabaseWorker(tmp_path / DB)
    assert sorted(p.name for p in w._history_backup_files()) == sorted(APP_FILES)
    assert w._remove_history_backups() == "ok"
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(USER_FILES)
    assert all((tmp_path / n).read_bytes() == b"DATA" * 100 for n in USER_FILES), "a user file was zeroed"


# --- review #376 item 3: never write through a link -------------------------------------------------------

def test_a_hard_linked_backup_is_unlinked_but_its_target_never_zeroed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    precious = outside / "precious.txt"
    precious.write_bytes(b"PRECIOUS" * 10)
    data = tmp_path / "data"
    data.mkdir()
    link = data / f"{DB}.bak.v7_20261019_120000"
    try:
        os.link(precious, link)
    except OSError as e:
        pytest.skip(f"no hard links here: {e}")
    w = DatabaseWorker(data / DB)
    assert w._remove_history_backups() == "ok"
    assert not link.exists()
    assert precious.read_bytes() == b"PRECIOUS" * 10


def test_a_symlinked_backup_is_unlinked_but_its_target_never_zeroed(tmp_path):
    precious = tmp_path / "precious.txt"
    precious.write_bytes(b"PRECIOUS" * 10)
    data = tmp_path / "data"
    data.mkdir()
    link = data / f"{DB}.stray_20261019_120000"
    try:
        os.symlink(precious, link)
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"no symlinks without privilege here: {e}")
    w = DatabaseWorker(data / DB)
    assert w._remove_history_backups() == "ok"
    assert not os.path.lexists(link)
    assert precious.read_bytes() == b"PRECIOUS" * 10


# --- review #376 item 2: an unremovable backup is retried with backoff, never re-zeroed ------------------

def test_a_read_only_backup_is_removed(tmp_path):
    p = tmp_path / f"{DB}.bak.v7_20261019_120000"
    p.write_bytes(b"Tailscale" * 100)
    os.chmod(p, stat.S_IREAD)
    w = DatabaseWorker(tmp_path / DB)
    assert w._remove_history_backups() == "ok"
    assert not p.exists()


def test_a_held_backup_backs_off_warns_once_never_rezeroes_and_still_checkpoints(tmp_path, no_retry_sleep,
                                                                                    monkeypatch, caplog):
    """Harness rr376/stuck.py: a held backup used to be re-zeroed + fsynced every 5 s (~720 WARNINGs
    an hour), and the early return meant a busy WAL checkpoint was never retried either."""
    import netspeedtray.core.database as dbm
    w = _migrated_worker(tmp_path)
    bak = next(tmp_path.glob(f"{DB}.bak.v7_*"))
    writes = []
    real_open = open

    def spy_open(f, mode="r", *a, **k):
        if "+" in mode:
            writes.append(str(f))
        return real_open(f, mode, *a, **k)
    monkeypatch.setattr(dbm, "open", spy_open, raising=False)
    clock = [1000.0]
    monkeypatch.setattr(dbm.time, "monotonic", lambda: clock[0])
    reader = sqlite3.connect(tmp_path / DB)
    cur = reader.execute(f"SELECT * FROM {S8.NET_MINUTE}")
    cur.fetchone()                                   # the WAL checkpoint is busy too
    T = constants.timeouts
    held = real_open(bak, "rb")
    try:
        with caplog.at_level("DEBUG"):
            w._execute_task("delete_history", {"include_usage": False})
            assert w._backups_pending and w._wal_pending
            cur.close()
            reader.close()                           # the reader goes away; the backup stays held
            attempts_at = []
            for _ in range(2000):                    # ~2.8 h of 5 s idle ticks
                clock[0] += T.DB_COMPACT_RETRY_INTERVAL_SEC
                before = w._backup_attempts
                w._retry_pending_compaction()
                if w._backup_attempts != before:
                    attempts_at.append(clock[0])
        assert not w._wal_pending, "the WAL checkpoint must be retried even while a backup is held"
        assert w._backup_attempts == T.DB_BACKUP_RETRY_MAX_ATTEMPTS
        gaps = [b - a for a, b in zip(attempts_at, attempts_at[1:])]
        assert gaps == sorted(gaps) and max(gaps) <= T.DB_BACKUP_RETRY_MAX_SEC + T.DB_COMPACT_RETRY_INTERVAL_SEC
        assert not w._compact_pending, "given up for the session: no more idle ticks"
        assert writes.count(str(bak)) == 1, f"backup re-zeroed {writes.count(str(bak))} times"
        warns = [r for r in caplog.records if r.levelname == "WARNING" and bak.name in r.getMessage()]
        assert len(warns) == 1, [r.getMessage() for r in warns]
        assert w.conn.execute("SELECT value FROM metadata WHERE key = ?",
                              (DatabaseWorker._COMPACT_MARKER_KEY,)).fetchone() == ("2",)
    finally:
        held.close()
    w._close_connection()
    w2 = DatabaseWorker(tmp_path / DB)               # the next start finishes it
    w2._prepare_storage()
    assert _bak_files(tmp_path) == []
    w2._close_connection()


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


@pytest.mark.parametrize("path", ["quit", "logoff"])
@pytest.mark.parametrize("fails", [False, True])
def test_quitting_with_a_reset_unanswered_persists_the_matching_odometer(tmp_path, q_app, no_retry_sleep, fails,
                                                                          path):
    """Review #376 item 6: at session end the GUI may not have heard the delete's answer yet, and the
    pending reset made the final persist a no-op - the traffic since the request was lost. Now the
    write thread (which ran the delete first) persists the outcome that matches."""
    _make_v7(tmp_path, usage=(5e9, 9e9, 1e9, 2e9))
    s = _state(tmp_path)
    try:
        prepare_v8(s)
        _drain(s)
        assert s.get_usage_this_period() == (4e9, 7e9)
        s.db_worker.history_deleted.disconnect(s._on_worker_history_deleted)   # the answer never lands
        s.delete_all_history(include_usage=True)
        s.add_usage_bytes(10.0, 20.0)
        if path == "quit":
            s.persist_usage_final()                   # what cleanup() calls at quit
        else:
            s.persist_essentials(timeout=0.1)         # Windows ending the session (#377's logoff path)

        def boom(self, include_usage=False, now_ts=None):
            raise sqlite3.OperationalError("database is locked")
        if fails:
            with patch.object(writer_v8.WriterV8, "delete_history", boom):
                _drain(s)
        else:
            _drain(s)
        want = (5e9 + 10.0, 9e9 + 20.0) if fails else (10.0, 20.0)
        assert _usage_on_disk(tmp_path)[0] == want
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


# --- second review of #376 --------------------------------------------------------------------------------

def test_a_vacuum_failure_after_the_commit_is_a_done_delete_not_a_failed_one(tmp_path, no_retry_sleep):
    """Harness rr376/h4.py [vac]: the post-commit incremental_vacuum raising used to report FAILED -
    the user was told nothing was deleted, the backups were kept, and the logoff save then wrote the
    OLD 5e9 total back over the (already deleted) odometer."""
    w = _migrated_worker(tmp_path)
    w.conn.execute("INSERT OR REPLACE INTO usage_counter (id, cumulative_up, cumulative_down, anchor_up, "
                   "anchor_down, period_key, updated_ts) VALUES (1, 5e9, 5e9, 0, 0, '2026-10-01', 0)")
    got = []
    w.history_deleted.connect(got.append)
    with patch.object(writer_v8.WriterV8, "incremental_vacuum",
                      side_effect=sqlite3.OperationalError("database or disk is full")):
        w._execute_task("delete_history", {"include_usage": True})
        assert got == [DatabaseWorker.DELETE_COMPACT_PENDING], got
        assert w._last_delete_outcome == "usage_reset"
        assert _bak_files(tmp_path) == [], "a done delete removes the backups"
        assert w._vacuum_pending
        w._persist_usage_after_delete({"if_reset": (1.0, 1.0, 0, 0, "2026-10-01", 0, None),
                                       "if_failed": (5e9, 5e9, 0, 0, "2026-10-01", 0, None)})
        assert w.conn.execute("SELECT cumulative_up FROM usage_counter").fetchall() == [(1.0,)]
        w._retry_pending_compaction(force=True)          # the vacuum keeps failing...
        assert not w._wal_pending, "...and never blocks the checkpoint"
    w._retry_pending_compaction(force=True)              # ...until it works
    assert not w._vacuum_pending and not w._compact_pending
    w._close_connection()


def _seen_s(w, name):
    row = w.conn.execute("SELECT value FROM metadata WHERE key = ?",
                         (DatabaseWorker._SEEN_PREFIX + name.lower(),)).fetchone()
    return int(row[0]) if row else 0


def _run_hours(w, start_ts: int, hours: int, keep: int = 31) -> int:
    """The REAL maintenance pass, hourly, on a simulated clock (no monotonic: the date rules only)."""
    for h in range(hours):
        w._run_maintenance({"keep_data": keep}, now=datetime.fromtimestamp(start_ts + h * 3600))
    return start_ts + hours * 3600


def _clock_worker(tmp_path, keep=31):
    w = DatabaseWorker(tmp_path / DB)
    w._prepare_storage()
    w.conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, '1')",
                   (DatabaseWorker.MIGRATED_FROM_V7_KEY,))
    w.conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('current_retention_days', ?)", (str(keep),))
    return w


REAL = datetime(2026, 10, 20, 12, 0, 0)


def test_a_forward_jump_under_the_gap_rule_never_beats_the_observed_30_day_floor(tmp_path):
    """Harness h4 A3: a backup 20 real days old, 12 days observed, then the clock jumps +13 days (under
    the 14-day gap rule, so it looks like app-off time): it used to be deleted below the 30-day floor."""
    w = _clock_worker(tmp_path)
    b = _fake_bak(tmp_path, 7, REAL - timedelta(days=20))
    _run_hours(w, int((REAL - timedelta(days=12)).timestamp()), 24 * 12)
    _run_hours(w, int((REAL + timedelta(days=13)).timestamp()), 3)
    assert b.exists()
    assert _seen_s(w, b.name) < D.retention.BACKUP_MIN_KEEP_DAYS * 86400
    w._close_connection()


def test_two_forward_jumps_an_hour_apart_keep_an_8_day_old_backup(tmp_path):
    """Harness h4 A4."""
    w = _clock_worker(tmp_path)
    b = _fake_bak(tmp_path, 7, REAL - timedelta(days=8))
    _run_hours(w, int((REAL - timedelta(days=8)).timestamp()), 24 * 8)
    _run_hours(w, int((REAL + timedelta(days=13)).timestamp()), 1)
    _run_hours(w, int((REAL + timedelta(days=26)).timestamp()), 1)
    assert b.exists()
    w._close_connection()


def test_a_clock_corrected_forward_keeps_a_10_day_old_backup(tmp_path):
    """Harness h4 A2: the clock started in 2020 (backup stamped 2020), then was corrected to 2026; the
    backup is really ~10 days old and used to go 7 days after the correction."""
    w = _clock_worker(tmp_path)
    b = _fake_bak(tmp_path, 7, datetime(2020, 1, 1))
    _run_hours(w, int(datetime(2020, 1, 1, 1).timestamp()), 24)
    _run_hours(w, int(REAL.timestamp()), 24 * 9)
    assert b.exists()
    w._close_connection()


def test_steady_observed_time_past_the_floor_does_age_a_backup_out(tmp_path):
    """The control: a correct clock, the backup 60 days old by its dates and observed for 31 steady days."""
    w = _clock_worker(tmp_path)
    b = _fake_bak(tmp_path, 7, REAL - timedelta(days=60))
    end = _run_hours(w, int((REAL - timedelta(days=31)).timestamp()), 24 * 30)
    assert b.exists(), "30 observed days: not yet"
    _run_hours(w, end, 25)
    assert not b.exists()
    w._close_connection()


def test_a_backup_whose_unlink_failed_once_is_retried_on_the_next_pass(tmp_path, monkeypatch):
    """Zeroing stamps the file 'modified now'; if the unlink then fails (a scanner holding the fresh
    write), the retry must still see the backup's own dates - not keep a zeroed file a whole period."""
    w = _clock_worker(tmp_path)
    b = _fake_bak(tmp_path, 7, REAL - timedelta(days=60))
    b.write_bytes(b"history")
    old = REAL - timedelta(days=60)
    os.utime(b, (old.timestamp(), old.timestamp()))
    real_unlink, failed = os.unlink, []

    def flaky_unlink(path, *a, **k):
        if Path(path) == b and not failed:
            failed.append(1)
            raise PermissionError(32, "held by a scanner")
        return real_unlink(path, *a, **k)

    monkeypatch.setattr(os, "unlink", flaky_unlink)
    end = _run_hours(w, int((REAL - timedelta(days=31)).timestamp()), 24 * 30)
    _run_hours(w, end, 26)
    assert failed, "the first eligible pass must have tried (and failed) to unlink it"
    assert not b.exists(), "the next pass retries it instead of waiting another retention period"
    w._close_connection()


def test_in_a_session_a_wall_clock_jump_against_monotonic_resets_the_steady_count(tmp_path):
    """Within a session the gap rule can't tell a +13 d jump from app-off time - monotonic time can."""
    w = _clock_worker(tmp_path)
    now = int(REAL.timestamp())
    w.conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('backup_clock_continuous_since', ?)",
                   (str(now - 30 * 86400),))
    mono = 5000.0
    w._age_out_backups(31, datetime.fromtimestamp(now), now - 3600, mono)
    credit, continuous = w._observe_clock(now + 3600, now, mono + 3600)          # a steady hour
    assert credit == 3600 and continuous
    credit, continuous = w._observe_clock(now + 3600 + 13 * 86400, now + 3600, mono + 7200)   # +13 d / 1 h
    assert credit == 0 and not continuous, "a wall jump the monotonic clock did not see is a jump"
    w._close_connection()


def test_backup_names_match_case_insensitively_everywhere(tmp_path):
    w = DatabaseWorker(tmp_path / DB)
    upper = tmp_path / "SPEED_HISTORY.DB.BAK.V7_20261019_120000"
    upper.write_bytes(b"x")
    assert w._backup_files("v*") == [upper]
    assert w._find_newest_backup() == upper, "the restore hint and Delete must agree"
    assert w._remove_history_backups() == "ok" and not upper.exists()


def test_prune_of_old_backups_never_touches_user_files(tmp_path):
    """The v2-v6 in-place chain's _prune_old_backups used a broad glob: .bak.handmade went too."""
    w = DatabaseWorker(tmp_path / DB)
    mine = tmp_path / f"{DB}.bak.handmade"
    mine.write_bytes(b"MINE")
    for i in range(4):
        _fake_bak(tmp_path, 6, REAL - timedelta(days=i + 1))
    w._prune_old_backups()
    assert mine.read_bytes() == b"MINE"
    assert len(list(tmp_path.glob(f"{DB}.bak.v6_*"))) == w._BACKUP_RETENTION


def test_a_read_only_hard_linked_backup_is_given_up_on_and_said_so(tmp_path, no_retry_sleep, caplog):
    """Harness h4b: a read-only, hard-linked backup can never be unlinked without changing the other
    name's file; it used to be retried 8 times every session forever."""
    w = _migrated_worker(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "ro_precious.txt"
    target.write_bytes(b"ROPRECIO" * 10)
    link = tmp_path / f"{DB}.bak.v4_20250101_000000"
    try:
        os.link(target, link)
    except OSError as e:
        pytest.skip(f"no hard links here: {e}")
    os.chmod(target, stat.S_IREAD)
    try:
        got = []
        w.history_deleted.connect(got.append)
        with caplog.at_level("INFO"):
            w._execute_task("delete_history", {"include_usage": False})
            assert got == [DatabaseWorker.DELETE_BACKUP_KEPT]
            assert link.exists() and target.read_bytes() == b"ROPRECIO" * 10
            assert not os.stat(target).st_mode & stat.S_IWRITE, "the shared file's attribute is untouched"
            assert not w._compact_pending, "nothing left to retry"
            assert w._history_backup_files() == [link]          # the v7 backup itself is gone
            w._compact_pending = w._backups_pending = True
            w._retry_pending_compaction(force=True)
            w._retry_pending_compaction(force=True)
        warns = [r for r in caplog.records if r.levelname == "WARNING" and link.name in r.getMessage()]
        assert len(warns) == 1
        assert link.name.lower() in w.conn.execute("SELECT value FROM metadata WHERE key = ?",
                                                    (DatabaseWorker._KEPT_KEY,)).fetchone()[0]
    finally:
        os.chmod(target, stat.S_IREAD | stat.S_IWRITE)
    w._close_connection()


def test_a_journal_present_delete_says_the_backup_is_kept_until_the_upgrade_finishes(tmp_path, no_retry_sleep):
    w = _migrated_worker(tmp_path)
    jp = M.journal_path_for(tmp_path / DB)
    jp.write_text("{}")
    got = []
    w.history_deleted.connect(got.append)
    w._execute_task("delete_history", {"include_usage": False})
    assert got == [DatabaseWorker.DELETE_BACKUP_KEPT_UPGRADE]
    assert _bak_files(tmp_path), "kept while the journal exists"
    assert w._backup_attempts >= constants.timeouts.DB_BACKUP_RETRY_MAX_ATTEMPTS, "not retried this session"
    assert w.conn.execute("SELECT value FROM metadata WHERE key = ?",
                          (DatabaseWorker._COMPACT_MARKER_KEY,)).fetchone() == ("2",)
    w._close_connection()
    jp.unlink()                                       # the upgrade has finished / been undone
    w2 = DatabaseWorker(tmp_path / DB)
    w2._prepare_storage()
    assert _bak_files(tmp_path) == [], "removed at a later start"
    w2._close_connection()


@pytest.mark.parametrize("status,key,box", [
    ("backup_kept_upgrade", "DELETE_HISTORY_DONE_BACKUP_UPGRADE", "information"),
    ("backup_kept", "DELETE_HISTORY_DONE_BACKUP_KEPT", "warning")])
def test_settings_tells_the_user_a_backup_was_kept(status, key, box):
    from netspeedtray.views.settings.dialog import SettingsDialog
    shown = []
    fake = NS(_delete_timeout=NS(stop=lambda: None),
              advanced_page=NS(delete_history_btn=NS(setEnabled=lambda v: None)),
              parent_widget=NS(update_config=lambda u: None, invalidate_usage_cache=lambda: None),
              config={}, original_config={}, logger=NS(debug=lambda *a, **k: None),
              i18n=NS(SUCCESS_TITLE="ok", DELETE_HISTORY_DONE_BACKUP_UPGRADE="UPG", DELETE_HISTORY_DONE_BACKUP_KEPT="KEPT"),
              _delete_include_usage=False)
    with patch("netspeedtray.views.settings.dialog.QMessageBox") as mb:
        getattr(mb, box).side_effect = lambda *a: shown.append(a[2])
        SettingsDialog._on_history_deleted(fake, status)
    assert shown == [getattr(fake.i18n, key)]
