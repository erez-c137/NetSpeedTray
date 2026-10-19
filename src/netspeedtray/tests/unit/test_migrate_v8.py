"""
The v7 -> v8 migrator (database v8 step 4 - dormant: nothing in the app calls it yet).

In-process tests of what the migration produces, what it refuses, and every state of the journal
state machine in ``recover``. The kill test (real processes, hot WAL, TerminateProcess at every
kill point) is ``tests/integration/test_migration_kill.py``.
"""
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.append(os.path.abspath("src"))
from netspeedtray.core.database import DatabaseWorker
from netspeedtray.core.db import migrate_v8 as M
from netspeedtray.core.db import schema_v8 as S
from netspeedtray.core.db.sqlite_uri import sqlite_ro_uri
from netspeedtray.tests.integration import v7_fixture as F

DB = "speed_history.db"
NOW = datetime(2026, 10, 19, 12, 0, 0)


def _make_v7(directory: Path) -> Path:
    db = directory / DB
    w = DatabaseWorker(db)
    w._initialize_connection()
    w._check_and_create_schema()
    w._ensure_indexes()
    F.populate(w.conn)
    w._close_connection()
    return db


@pytest.fixture
def v7(tmp_path):
    return _make_v7(tmp_path)


def _rows(c, table):
    return sorted(tuple(r) for r in c.execute(f"SELECT * FROM {table}"))


def _files(d: Path):
    """Directory listing, ignoring EMPTY -wal/-shm left by a read-only open (content-free)."""
    return sorted(p.name for p in d.iterdir()
                  if not (p.name.endswith(("-wal", "-shm")) and p.name.startswith(DB + "-")
                          and (p.name.endswith("-shm") or p.stat().st_size == 0)))


# --------------------------------------------------------------------------------------------
# what a migration produces
# --------------------------------------------------------------------------------------------

def test_migrates_build_aside_and_swaps(v7, tmp_path):
    res = M.migrate(v7, now=NOW)
    assert res.status == "migrated"
    bak = tmp_path / f"{DB}.bak.v7_20261019_120000"
    assert Path(res.backup_path) == bak
    assert _files(tmp_path) == sorted([DB, bak.name]), "no tmp, journal or sidecar may be left"

    c = sqlite3.connect(v7)
    try:
        assert c.execute("SELECT value FROM metadata WHERE key='db_version'").fetchone()[0] == "8"
        assert c.execute("PRAGMA auto_vacuum").fetchone()[0] == 2           # INCREMENTAL
        assert c.execute("PRAGMA page_size").fetchone()[0] == S.PAGE_SIZE
        assert c.execute("PRAGMA freelist_count").fetchone()[0] == 0
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert set(S.EXPECTED_TABLES_V8) <= tables
        assert "bandwidth_history" not in tables, "bandwidth_history is dropped consciously"
        assert "sqlite_stat1" in tables, "ANALYZE runs at the end of the migration"
    finally:
        c.close()

    # The backup IS the untouched v7 file.
    b = sqlite3.connect(sqlite_ro_uri(bak), uri=True)
    try:
        for table, want in F.manifest().items():
            assert _rows(b, table) == want, table
    finally:
        b.close()


def test_every_v7_reader_shape_survives_through_the_views(v7):
    M.migrate(v7, now=NOW)
    exp = F.expected_v8_view_rows()
    merged = F.merged_minute_keys()
    c = sqlite3.connect(v7)
    try:
        for table, want in exp.items():
            got = _rows(c, table)
            assert len(got) == len(want), table
            for g, w in zip(got, want):
                if table == F.SPEED_MIN and (w[0], w[1]) in merged:
                    assert g[:2] == w[:2] and g[4:] == w[4:]
                    assert g[2:4] == pytest.approx(w[2:4], rel=1e-12)
                else:
                    assert g == w, (table, g, w)
    finally:
        c.close()


def test_fractional_loss_series_are_carried_exactly(v7):
    """Gate 5: 0/1-per-probe and fraction-per-burst rows, side by side, bit for bit."""
    M.migrate(v7, now=NOW)
    c = sqlite3.connect(v7)
    try:
        for tier in (S.HW_RAW, S.HW_MINUTE, S.HW_HOUR):
            col = "value" if tier == S.HW_RAW else "avg_value"
            vals = {r[0] for r in c.execute(
                f"SELECT DISTINCT {col} FROM {tier} h JOIN metrics m USING (metric_id) "
                "WHERE m.name LIKE '%_timeout'")}
            assert vals == set(F.LOSS_VALUES), tier
        assert c.execute("SELECT COUNT(*) FROM hw_minute WHERE min_value IS NOT NULL").fetchone()[0] == 0
    finally:
        c.close()


def test_raw_tier_is_folded_into_minutes_without_inventing_bytes(v7):
    M.migrate(v7, now=NOW)
    c = sqlite3.connect(v7)
    try:
        assert c.execute("SELECT COUNT(*) FROM net_raw").fetchone()[0] == 0
        for t in ("net_minute", "net_hour"):
            assert c.execute(f"SELECT COUNT(*) FROM {t} WHERE up_bytes IS NOT NULL OR down_bytes "
                             "IS NOT NULL OR covered_ms IS NOT NULL OR flags != 0").fetchone()[0] == 0
        # every raw sample is accounted for: samples and rate-sums are conserved
        man = F.manifest()
        n_samples = c.execute("SELECT SUM(sample_count) FROM net_minute").fetchone()[0]
        assert n_samples == sum(r[6] for r in man[F.SPEED_MIN]) + len(man[F.SPEED_RAW])
    finally:
        c.close()


def test_metadata_is_byte_identical_and_only_db_version_changes(v7):
    M.migrate(v7, now=NOW)
    c = sqlite3.connect(v7)
    try:
        got = dict(c.execute("SELECT key, value FROM metadata"))
    finally:
        c.close()
    want = dict(F.manifest()["metadata"])
    want["db_version"] = "8"
    want.update({k: str(v) for k, v in F.expected_watermarks().items()})
    assert got == want
    assert got["usage_counter_snapshot"] == want["usage_counter_snapshot"]       # #348
    assert got["pending_history_compaction"] == "1"                              # #347


def test_tier_watermarks_are_stamped_with_no_gap_and_no_overlap(v7):
    res = M.migrate(v7, now=NOW)
    wm = F.expected_watermarks()
    c = sqlite3.connect(v7)
    try:
        got = {k: int(v) for k, v in c.execute(
            "SELECT key, value FROM metadata WHERE key IN (?, ?, ?, ?)", S.WATERMARK_KEYS)}
        assert got == wm
        # exactly one tier answers every stored row
        for fine, coarse, bucket, k in (("net_minute", "net_hour", 3600, S.WATERMARK_MINUTE_KEY),
                                        ("hw_minute", "hw_hour", 3600, S.HW_WATERMARK_MINUTE_KEY),
                                        ("hw_raw", "hw_minute", 60, S.HW_WATERMARK_RAW_KEY),
                                        ("net_raw", "net_minute", 60, S.WATERMARK_RAW_KEY)):
            assert c.execute(f"SELECT COUNT(*) FROM {fine} WHERE ts < ?", (wm[k],)).fetchone()[0] == 0, fine
            assert c.execute(f"SELECT COUNT(*) FROM {coarse} WHERE ts + ? > ?",
                             (bucket, wm[k])).fetchone()[0] == 0, coarse
        # nothing between the last folded minute and the raw watermark is lost: it IS the fold's end
        assert c.execute("SELECT MAX(ts) + 60 FROM net_minute").fetchone()[0] == wm[S.WATERMARK_RAW_KEY]
    finally:
        c.close()
    assert res.counts["net_minute_shadowed"] == res.counts["hw_minute_shadowed"] ==         res.counts["hw_raw_shadowed"] == 0


@pytest.mark.parametrize("args, want", [
    ((None, None, None, None, 1_000_000_123), (1_000_000_080, 999_997_200)),   # empty -> now, floored
    ((7200, 9000, 12000, None, 10**9), (12060, 10800)),                             # hour end; minute end
    ((None, 9000, 12000, 20000, 10**9), (12060, 7200)),                             # no hour tier
    ((None, None, None, 20000, 10**9), (19980, 18000)),                             # raw only
    ((36000, None, None, 1000, 10**9), (39600, 39600)),
    ((4 * 10**9, None, 5 * 10**9, None, 10**9), (1_000_000_320, 999_997_200)),  # clamped to the clock                             # raw clamped >= minute
])
def test_watermark_rule_edges(args, want):
    assert M._wm_rule(*args) == want


def test_overlapping_v7_tiers_shadow_the_finer_rows_instead_of_double_counting(v7):
    """A v7 late insert can leave a minute row inside an hour that is already rolled up. The coarser
    tier owns that hour; the minute row is counted as shadowed and the migration still verifies."""
    man = F.manifest()
    hour_ts = max(r[0] for r in man[F.SPEED_HOUR])
    c = sqlite3.connect(v7)
    c.execute("INSERT INTO speed_history_minute VALUES (?, 'Wi-Fi 3', 1, 1, 1, 1, 1)", (hour_ts + 60,))
    c.commit()
    c.close()
    res = M.migrate(v7, now=NOW)
    assert res.counts["net_minute_shadowed"] == 1
    assert res.counts[S.WATERMARK_MINUTE_KEY] == hour_ts + 3600


def test_interfaces_and_metrics_dimensions(v7):
    M.migrate(v7, now=NOW)
    c = sqlite3.connect(v7)
    try:
        ifaces = {r[0]: r[1:] for r in c.execute(
            "SELECT name, stable_key, is_physical, alias_of FROM interfaces")}
        assert set(ifaces) == set(F.IFACES)
        assert ifaces["Tailscale"] == ("Tailscale", 0, None)
        assert ifaces["Wi-Fi 3"] == ("Wi-Fi 3", 1, None)
        assert {r[0] for r in c.execute("SELECT name FROM metrics WHERE entity = ''")} == set(F.STATS)
    finally:
        c.close()


def test_is_physical_guess_agrees_with_the_classifier():
    from netspeedtray.utils.interface_classifier import classify_interface
    from netspeedtray import constants
    for name in ("Wi-Fi 3", "Ethernet", "Tailscale", "vEthernet (WSL (Hyper-V firewall))",
                 "Loopback Pseudo-Interface 1", "Teredo Tunneling", "OpenVPN TAP", "NordLynx",
                 "Local Area Connection* 10", "Bluetooth Network Connection", "ZeroTier One"):
        want = 1 if classify_interface(name, None) == constants.network.interface.KIND_PHYSICAL else 0
        assert M._guess_is_physical(name) == want, name


def test_expected_v7_tables_match_the_worker_registry():
    assert set(M.EXPECTED_TABLES_V7) == set(DatabaseWorker.EXPECTED_TABLES[7])


# --------------------------------------------------------------------------------------------
# refusals: the v7 file stays live and untouched
# --------------------------------------------------------------------------------------------

def _digest(p: Path) -> bytes:
    return p.read_bytes()


def test_refuses_without_free_space(v7, tmp_path, monkeypatch):
    before = _digest(v7)
    usage = shutil.disk_usage(tmp_path)
    monkeypatch.setattr(M.shutil, "disk_usage", lambda p: usage._replace(free=v7.stat().st_size))
    with pytest.raises(M.InsufficientSpace):
        M.migrate(v7, now=NOW)
    assert _files(tmp_path) == [DB] and _digest(v7) == before


def test_verification_failure_deletes_the_build_and_keeps_v7(v7, tmp_path, monkeypatch):
    before = _digest(v7)
    real = M._build

    def lossy(c, *a):
        out = real(c, *a)
        c.execute("DELETE FROM main.hw_raw WHERE ts = (SELECT MIN(ts) FROM main.hw_raw)")
        return out
    monkeypatch.setattr(M, "_build", lossy)
    with pytest.raises(M.VerificationFailed, match="hardware_stats_raw"):
        M.migrate(v7, now=NOW)
    assert _files(tmp_path) == [DB] and _digest(v7) == before


@pytest.mark.parametrize("sabotage, match", [
    ("UPDATE main.metadata SET value = value || ' ' WHERE key = 'usage_counter_snapshot'", "metadata"),
    ("UPDATE main.hw_hour SET avg_value = avg_value + 1e-9 WHERE ts = (SELECT MIN(ts) FROM main.hw_hour)",
     "hardware_stats_hour"),
    ("DELETE FROM main.net_minute WHERE ts = (SELECT MAX(ts) FROM main.net_minute)", "net_minute"),
    ("UPDATE main.usage_counter SET anchor_up = anchor_up + 1", "usage_counter"),
    ("UPDATE main.metadata SET value = value + 60 WHERE key = 'tier_watermark_raw'", "watermarks"),
    ("UPDATE main.metadata SET value = value - 3600 WHERE key = 'hw_tier_watermark_minute'", "watermarks"),
])
def test_verification_catches_each_kind_of_damage(v7, monkeypatch, sabotage, match):
    real = M._build
    monkeypatch.setattr(M, "_build", lambda c, *a: (real(c, *a), c.execute(sabotage))[0])
    with pytest.raises(M.VerificationFailed, match=match):
        M.migrate(v7, now=NOW)


def test_refuses_while_another_connection_holds_the_wal(v7, tmp_path):
    """The checkpoint must empty the v7 WAL before the rename; an open reader prevents that."""
    hold = sqlite3.connect(v7)
    hold.execute("PRAGMA wal_autocheckpoint = 0")
    hold.execute("INSERT INTO metadata VALUES ('held', '1')")
    hold.commit()
    hold.execute("BEGIN")
    hold.execute("SELECT COUNT(*) FROM metadata").fetchone()      # an open read transaction
    try:
        with pytest.raises(M.MigrationError):
            M.migrate(v7, now=NOW)
    finally:
        hold.rollback()
        hold.close()
    assert not M.tmp_path_for(v7).exists() and not M.journal_path_for(v7).exists()
    assert sqlite3.connect(v7).execute("SELECT value FROM metadata WHERE key='db_version'").fetchone()[0] == "7"


def test_refuses_a_v7_file_missing_a_table(v7, tmp_path):
    c = sqlite3.connect(v7)
    c.execute("DROP TABLE usage_counter")
    c.commit()
    c.close()
    with pytest.raises(M.MigrationError, match="usage_counter"):
        M.migrate(v7, now=NOW)
    assert not M.tmp_path_for(v7).exists()


def test_statuses_for_nothing_to_do(tmp_path, v7):
    assert M.migrate(tmp_path / "absent" / DB).status == "no_database"
    assert M.migrate(v7, now=NOW).status == "migrated"
    assert M.migrate(v7, now=NOW).status == "already_v8"
    c = sqlite3.connect(v7)
    c.execute("UPDATE metadata SET value='6' WHERE key='db_version'")
    c.commit()
    c.close()
    assert M.migrate(v7, now=NOW).status == "not_v7"


def test_backup_name_collision_gets_a_suffix(v7, tmp_path):
    (tmp_path / f"{DB}.bak.v7_20261019_120000").write_bytes(b"older")
    res = M.migrate(v7, now=NOW)
    assert Path(res.backup_path).name == f"{DB}.bak.v7_20261019_120000_1"
    assert (tmp_path / f"{DB}.bak.v7_20261019_120000").read_bytes() == b"older"


# --------------------------------------------------------------------------------------------
# step 0 interplay: the backup is the v7 backup to the worker's retention and restore lookup
# --------------------------------------------------------------------------------------------

def test_backup_matches_the_worker_backup_pattern(v7, tmp_path, monkeypatch):
    bak = Path(M.migrate(v7, now=NOW).backup_path)
    assert re.search(r"\.db\.bak\.v(\d+)_", bak.name).group(1) == "7"
    w = DatabaseWorker(v7)
    assert w._find_newest_backup() == bak                       # a 2.1.x build (_DB_VERSION 7)
    monkeypatch.setattr(DatabaseWorker, "_DB_VERSION", 8)
    assert w._find_newest_backup() == bak                       # the 2.2 build
    for i in range(4):
        p = tmp_path / f"{DB}.bak.v8_2099010{i}_000000"
        p.write_bytes(b"v8")
        os.utime(p, (2_000_000_000 + i, 2_000_000_000 + i))
    w._prune_old_backups()
    assert bak.exists(), "version-aware retention must keep the newest v7 backup"


# --------------------------------------------------------------------------------------------
# recover(): every state of the journal state machine
# --------------------------------------------------------------------------------------------

@pytest.fixture
def built(tmp_path):
    """A v7 live file and a verified .v8tmp beside it - the moment the journal is written."""
    src = tmp_path / "src"
    src.mkdir()
    db = _make_v7(src)
    M.migrate(db, now=NOW)                    # produces a v8 live + v7 bak in src
    d = tmp_path / "d"
    d.mkdir()
    shutil.copy2(src / f"{DB}.bak.v7_20261019_120000", d / DB)          # live = v7
    shutil.copy2(src / DB, d / (DB + M.TMP_SUFFIX))                      # tmp  = v8
    return d


def _journal(d: Path, backup=f"{DB}.bak.v7_20261019_120000"):
    st = (d / DB).stat()
    M._write_journal(M.journal_path_for(d / DB), {"format": 1, "state": "swapping", "live": DB,
                                                   "tmp": DB + M.TMP_SUFFIX, "backup": backup,
                                                   "live_size": st.st_size,
                                                   "live_mtime_ns": st.st_mtime_ns})


def _ver(p: Path):
    return M._read_version(p)


BAK = f"{DB}.bak.v7_20261019_120000"


def test_recover_N0_nothing(tmp_path):
    assert M.recover(tmp_path / DB) == ""


def test_recover_N1_discards_an_unjournaled_tmp(built):
    (built / (DB + M.TMP_SUFFIX + "-journal")).write_bytes(b"hot rollback journal")
    assert M.recover(built / DB) == "discarded_tmp"
    assert _files(built) == [DB] and _ver(built / DB) == 7


def test_recover_J1_rolls_back_when_the_live_file_changed_since_the_journal(built):
    _journal(built)
    c = sqlite3.connect(built / DB)
    c.execute("PRAGMA journal_mode = DELETE")         # a write that reaches the main file directly
    c.execute("INSERT INTO metadata VALUES ('written_after', '1')")
    c.commit()
    c.close()
    assert M.recover(built / DB) == "rolled_back"
    assert _ver(built / DB) == 7 and not (built / (DB + M.TMP_SUFFIX)).exists()


def test_recover_J1_rolls_forward(built):
    _journal(built)
    (built / (DB + "-shm")).write_bytes(b"\0" * 32768)        # stale, zero-content sidecars
    (built / (DB + "-wal")).write_bytes(b"")
    assert M.recover(built / DB) == "completed"
    assert _files(built) == sorted([DB, BAK])
    assert _ver(built / DB) == 8 and _ver(built / BAK) == 7
    assert not (built / (DB + "-wal")).exists() and not (built / (DB + "-shm")).exists()


def test_recover_J1_rolls_back_when_the_live_wal_has_content(built):
    _journal(built)
    (built / (DB + "-wal")).write_bytes(b"x" * 100)
    assert M.recover(built / DB) == "rolled_back"
    assert not (built / (DB + M.TMP_SUFFIX)).exists() and not M.journal_path_for(built / DB).exists()


def test_recover_J2_finishes_between_renames(built):
    _journal(built)
    os.rename(built / DB, built / BAK)
    (built / (DB + "-wal")).write_bytes(b"x" * 100)            # must NOT follow the v8 file
    assert M.recover(built / DB) == "completed"
    assert not (built / (DB + "-wal")).exists(), "a v7 WAL left under the live name replays into v8"
    assert _files(built) == sorted([DB, BAK]) and _ver(built / DB) == 8


def test_recover_J3_only_deletes_the_journal(built):
    _journal(built)
    os.rename(built / DB, built / BAK)
    os.rename(built / (DB + M.TMP_SUFFIX), built / DB)
    assert M.recover(built / DB) == "completed"
    assert _files(built) == sorted([DB, BAK]) and _ver(built / DB) == 8


def test_recover_J4_restores_the_v7_backup(built):
    _journal(built)
    os.rename(built / DB, built / BAK)
    (built / (DB + M.TMP_SUFFIX)).unlink()
    assert M.recover(built / DB) == "restored_v7"
    assert _files(built) == [DB] and _ver(built / DB) == 7


def _j2(built):
    """Journal state J2: the first rename happened, the second did not."""
    _journal(built)
    os.rename(built / DB, built / BAK)


def test_recover_J5_finishes_when_a_plain_open_recreated_an_empty_live_file(built):
    """Review #359 bug 1: the 2nd rename failed (J2), then something plain-connected the live name
    and SQLite created an EMPTY file there. B exists, which proves our first rename happened (its
    name was unique when the journal was written) - so the verified v8 build must be swapped in,
    never deleted, and the empty file moved aside, not deleted."""
    _j2(built)
    c = sqlite3.connect(built / DB)                                   # a plain open ...
    c.execute("SELECT 1").fetchone()
    c.close()                                                         # ... leaves an empty live file
    assert (built / DB).exists()
    assert M.recover(built / DB) == "completed"
    assert _ver(built / DB) == 8 and _ver(built / BAK) == 7
    assert not (built / (DB + M.TMP_SUFFIX)).exists() and not M.journal_path_for(built / DB).exists()
    aside = [p for p in built.iterdir() if p.name.startswith(DB + M.STRAY_INFIX)]
    assert len(aside) == 1, _files(built)


def test_recover_J5_refuses_to_guess_when_the_live_file_holds_data(built):
    _j2(built)
    c = sqlite3.connect(built / DB)                                   # a real database appeared
    c.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    c.execute("INSERT INTO metadata VALUES ('db_version', '7')")
    c.commit()
    c.close()
    with pytest.raises(M.RecoveryError):
        M.recover(built / DB)
    assert (built / (DB + M.TMP_SUFFIX)).exists(), "the verified v8 build must never be deleted"
    assert M.journal_path_for(built / DB).exists() and (built / BAK).exists()


def test_recover_J6_renames_the_tmp_in_when_live_vanished(built):
    _journal(built)
    (built / DB).unlink()
    assert M.recover(built / DB) == "completed"
    assert _files(built) == [DB] and _ver(built / DB) == 8


def test_recover_J7_stale_journal(built):
    _journal(built)
    (built / (DB + M.TMP_SUFFIX)).unlink()
    assert M.recover(built / DB) == "stale_journal"
    assert _files(built) == [DB]


def test_recover_J8_keeps_the_journal_and_raises(built):
    _journal(built)
    (built / DB).unlink()
    (built / (DB + M.TMP_SUFFIX)).unlink()
    with pytest.raises(M.RecoveryError):
        M.recover(built / DB)
    assert M.journal_path_for(built / DB).exists()


def test_recover_refuses_a_foreign_journal(built):
    M.journal_path_for(built / DB).write_text(json.dumps({"live": "other.db", "tmp": "x", "backup": "y"}))
    with pytest.raises(M.RecoveryError):
        M.recover(built / DB)
    assert sorted(_files(built)) == sorted([DB, DB + M.TMP_SUFFIX, M.JOURNAL_NAME])


def test_recover_discards_a_torn_journal_write(built):
    (built / (M.JOURNAL_NAME + ".tmp")).write_text('{"form')
    assert M.recover(built / DB) == "discarded_tmp"
    assert _files(built) == [DB]


# --------------------------------------------------------------------------------------------
# hygiene
# --------------------------------------------------------------------------------------------

def test_killpoint_is_inert_without_the_test_hooks_switch(monkeypatch):
    monkeypatch.delenv("NST_TEST_HOOKS", raising=False)
    monkeypatch.setenv("NST_KILLPOINT", "before_commit")
    M._killpoint("before_commit")             # would end this process if it fired


def test_migrator_never_uses_executescript():
    src = Path(M.__file__).read_text(encoding="utf-8")
    assert "executescript(" not in src


def test_dormant_nothing_in_the_app_calls_the_migrator():
    assert DatabaseWorker._DB_VERSION == 7
    root = Path(M.__file__).resolve().parents[2]          # src/netspeedtray
    callers = [p for p in root.rglob("*.py")
               if "tests" not in p.parts and p.name != "migrate_v8.py"
               and "migrate_v8" in p.read_text(encoding="utf-8", errors="ignore")]
    assert callers == []


# --------------------------------------------------------------------------------------------
# review #359: renames that fail, future-dated rows, awkward paths
# --------------------------------------------------------------------------------------------

class _Hold:
    """An open handle WITHOUT FILE_SHARE_DELETE (what Python's open() makes): MoveFileEx on the
    file then fails with ERROR_SHARING_VIOLATION (32), as it does under an AV scanner."""
    def __init__(self, path):
        self.fh = open(path, "rb")

    def release(self):
        if self.fh:
            self.fh.close()
            self.fh = None


def test_a_briefly_held_live_file_is_retried_not_fatal(v7, monkeypatch):
    import threading
    monkeypatch.setattr(M, "RENAME_RETRY_SECONDS", 5.0)
    held = {}

    def hook(name):
        if name == "after_journal_pre_rename":
            held["h"] = _Hold(v7)
            threading.Timer(0.6, held["h"].release).start()
    monkeypatch.setattr(M, "_killpoint", hook)
    res = M.migrate(v7, now=NOW)
    assert res.status == "migrated" and _ver(v7) == 8


def test_a_held_live_file_defers_the_swap_and_keeps_the_verified_build(v7, tmp_path, monkeypatch):
    monkeypatch.setattr(M, "RENAME_RETRY_SECONDS", 0.5)
    held = {}
    monkeypatch.setattr(M, "_killpoint",
                        lambda n: held.setdefault("h", _Hold(v7)) if n == "after_journal_pre_rename" else None)
    try:
        with pytest.raises(M.SwapDeferred) as ei:
            M.migrate(v7, now=NOW)
        assert isinstance(ei.value, M.MigrationError) and ei.value.winerror == 32
        assert _ver(v7) == 7, "v7 stays live"
        assert (tmp_path / (DB + M.TMP_SUFFIX)).exists() and M.journal_path_for(v7).exists()
    finally:
        held["h"].release()
    assert M.recover(v7) == "completed"                       # the next launch finishes the swap
    assert _ver(v7) == 8


def test_a_held_build_file_leaves_J2_and_the_next_launch_finishes(v7, tmp_path, monkeypatch):
    monkeypatch.setattr(M, "RENAME_RETRY_SECONDS", 0.5)
    tmp = M.tmp_path_for(v7)
    held = {}
    monkeypatch.setattr(M, "_killpoint",
                        lambda n: held.setdefault("h", _Hold(tmp)) if n == "after_bak_rename_pre_swap" else None)
    try:
        with pytest.raises(M.SwapIncomplete) as ei:
            M.migrate(v7, now=NOW)
        assert isinstance(ei.value, M.MigrationError)
        assert not v7.exists() and tmp.exists() and M.journal_path_for(v7).exists()
    finally:
        held["h"].release()
    assert M.recover(v7) == "completed"
    assert _ver(v7) == 8


@pytest.mark.parametrize("ts_offset", [4 * 365 * 86400, -1])     # 4 years ahead / 2000-01-01
def test_watermarks_ignore_rows_dated_outside_now(v7, ts_offset):
    """Review #359 bug 3: one raw row from a wrong clock must not push tier_watermark_raw years
    ahead (every new v8 raw row would be invisible until then), nor drag anything to 2000."""
    now_ts = int(NOW.timestamp())
    ts = now_ts + ts_offset if ts_offset > 0 else 946_684_800
    c = sqlite3.connect(v7)
    c.execute("INSERT INTO speed_history_raw VALUES (?, 'Wi-Fi 3', 1.0, 1.0)", (ts,))
    c.execute("INSERT INTO hardware_stats_raw VALUES (?, 'cpu', 1.0)", (ts,))
    c.execute("INSERT INTO hardware_stats_hour VALUES (?, 'cpu', 1.0, 1.0, 1)", (ts,))
    c.commit()
    c.close()
    res = M.migrate(v7, now=NOW)
    assert res.status == "migrated"
    for k in S.WATERMARK_KEYS:
        assert res.counts[k] <= now_ts + M.FUTURE_SLACK_SECONDS, (k, res.counts[k])
    if ts_offset > 0:
        assert res.counts == {**res.counts, **{k: v for k, v in F.expected_watermarks().items()}}
        assert res.counts["future_rows"] == 3
    else:
        assert res.counts[S.WATERMARK_RAW_KEY] == F.expected_watermarks()[S.WATERMARK_RAW_KEY]


@pytest.mark.parametrize("name", ["Bob#2", "pct%41x", "q a#b%20c"])
def test_migrates_under_an_awkward_profile_path(tmp_path, name):
    d = tmp_path / name
    d.mkdir()
    db = _make_v7(d)
    assert M.migrate(db, now=NOW).status == "migrated"
    assert _ver(db) == 8
    assert sorted(p.name for p in tmp_path.iterdir()) == [name], "a truncated URI created a stray file"
