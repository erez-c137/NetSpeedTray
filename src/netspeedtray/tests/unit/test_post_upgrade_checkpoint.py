"""
rc.2 D4 - the post-upgrade write burst.

beta.1's perf run saw one minute ~4.5 min after the v7 -> v8 upgrade with ~2.6k small writes. Traced on
a scratch copy of the owner's v7 file: the post-upgrade maintenance pass (inside _prepare_storage, before
any reader may open the file) moves a day of raw hardware rows into the minute tier and leaves ~3 MB of
frames in the WAL. Nothing checkpointed them reliably - the startup maintenance's TRUNCATE checkpoint
races the GUI's first reads - so the WAL sat just under SQLite's 1000-page auto-checkpoint until a few
minutes of polls tipped it over, and that one commit copied the pages back. The upgrade now checkpoints
right after that pass, while the readers are still held off: the WAL starts the session empty.
"""
from netspeedtray.core.database import DatabaseWorker
from netspeedtray.tests.integration import v7_fixture as F
from netspeedtray.tests.legacy_v7_worker import LegacyV7Worker

DB = "speed_history.db"


def _make_v7(d):
    w = LegacyV7Worker(d / DB)
    w._initialize_connection()
    w._check_and_create_schema()
    w._ensure_indexes()
    F.populate(w.conn)
    w._close_connection()


def test_the_upgrade_leaves_no_post_maintenance_frames_in_the_wal(tmp_path, monkeypatch):
    from netspeedtray.core.db import writer_v8
    real = writer_v8.WriterV8.run_maintenance

    def heavy_pass(self, *a, **k):
        # The owner's file: that pass moved 107k hardware rows. Stand in for it with ~600 pages of
        # writes - well under the 1000-page auto-checkpoint, as on the owner's machine.
        out = real(self, *a, **k)
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('d4_test', ?)",
                              ("x" * 2_400_000,))
            self.conn.execute("DELETE FROM metadata WHERE key = 'd4_test'")
        return out
    monkeypatch.setattr(writer_v8.WriterV8, "run_maintenance", heavy_pass)
    # The fixture's v7 file carries a 2.1.x "compaction owed" marker, whose resume checkpoints too; a
    # real upgrade usually has none (the owner's did not), so nothing else may empty the WAL here.
    monkeypatch.setattr(DatabaseWorker, "_resume_pending_compaction", lambda self: None)
    _make_v7(tmp_path)
    w = DatabaseWorker(tmp_path / DB)
    try:
        w._prepare_storage()
        assert w.migration_result is not None and w.migration_result.status == "migrated"
        wal = tmp_path / f"{DB}-wal"
        # start_session() writes one small transaction after the checkpoint; the post-upgrade pass's
        # frames must be gone (before the fix the WAL held every page that pass rewrote).
        frames = w.conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()[1]
        assert frames <= 8, f"{frames} WAL frames left by the post-upgrade maintenance"
        assert not wal.exists() or wal.stat().st_size <= 8 * 4096 + 4096
    finally:
        w._close_connection()


def test_an_hourly_pass_never_stalls_writes_while_the_monitor_reads(tmp_path):
    """Review (b): the hourly pass ran a raw wal_checkpoint(TRUNCATE) on the 5 s busy timeout, so a
    Monitor read holding its snapshot stalled every queued write ~5 s each hour. It now tries once on the
    short timeout and leaves a busy checkpoint to the idle retry - with no restart marker written."""
    import sqlite3
    import time
    from netspeedtray import constants
    w = DatabaseWorker(tmp_path / DB)
    try:
        w._prepare_storage()                                         # a fresh v8 file
        reader = sqlite3.connect((tmp_path / DB).as_uri() + "?mode=ro", uri=True, isolation_level=None)
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM metadata").fetchone()   # the Monitor's snapshot
        with w.conn:
            w.conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('d4b', 'x')")
        t0 = time.perf_counter()
        w._execute_task("maintenance", constants.config.defaults.DEFAULT_CONFIG.copy())
        took = time.perf_counter() - t0
        assert took < 2.0, f"the hourly pass blocked {took:.1f} s on a reader"
        assert w._wal_pending and w._compact_pending
        assert w.conn.execute("SELECT value FROM metadata WHERE key = ?",
                              (w._COMPACT_MARKER_KEY,)).fetchone() is None
        reader.execute("COMMIT")
        reader.close()
        w._retry_pending_compaction(force=True)                      # the idle tick, reader gone
        assert not w._wal_pending and not w._compact_pending
    finally:
        w._close_connection()
