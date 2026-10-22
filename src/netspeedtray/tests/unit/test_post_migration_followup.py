"""
F8 (2.2 RC checkup): the post-migration maintenance pass runs BEFORE the first v8 poll is flushed, so
it cannot merge the polls that land in the folded minute (below the raw watermark the migrator stamps
at the end of the minute it ran in). Readers do not see raw rows below that watermark, so the upgrade
minute's traffic stayed hidden until the next hourly pass - up to an hour. The worker now runs one
extra roll-up once that minute is over and flushed.
"""
import time
from datetime import datetime
from pathlib import Path

from netspeedtray.core.database import DatabaseWorker
from netspeedtray.core.db import schema_v8 as S
from netspeedtray.core.db.writer_v8 import NetSample


def _worker(tmp_path: Path) -> DatabaseWorker:
    w = DatabaseWorker(tmp_path / "speed_history.db")
    w._prepare_storage()
    w._writer.start_session()
    return w


def _raw_rows_below_wm(w) -> int:
    wm = w._writer._meta_int(S.WATERMARK_RAW_KEY)
    return w.conn.execute(f"SELECT COUNT(*) FROM {S.NET_RAW} WHERE ts < ?", (wm,)).fetchone()[0]


def test_the_upgrade_minutes_polls_are_rolled_up_soon_after_the_first_flush(tmp_path):
    w = _worker(tmp_path)
    now = int(time.time())
    # What the migrator leaves: a raw watermark at the end of the current minute.
    wm = now // 60 * 60 + 60
    w.conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)", (S.WATERMARK_RAW_KEY, str(wm)))
    w.conn.commit()
    w._post_migration_followup_armed = True      # set by _open_storage after the post-upgrade pass
    w._persist_v8(([NetSample(ts=wm - 5, dt_ms=1000, byte_deltas={"Ethernet": (10, 1000)})], []))
    assert _raw_rows_below_wm(w) == 1             # hidden from readers: below the raw watermark
    due = w._post_migration_followup_at
    assert due is not None and due >= now + DatabaseWorker._POST_MIGRATION_FOLLOWUP_DELAY_S - 1
    assert due >= wm + DatabaseWorker._POST_MIGRATION_FOLLOWUP_WM_MARGIN_S
    w._persist_v8(([], []))                       # not due yet: nothing happens
    assert _raw_rows_below_wm(w) == 1
    w._post_migration_followup_at = time.time() - 1
    w._persist_v8(([], []))                       # the first flush at/after the due time
    assert _raw_rows_below_wm(w) == 0
    assert w.conn.execute(f"SELECT SUM(down_bytes) FROM {S.NET_MINUTE}").fetchone()[0] == 1000
    assert w._post_migration_followup_at is None  # once only
    w._close_connection()


def test_no_follow_up_without_a_migration(tmp_path, monkeypatch):
    w = _worker(tmp_path)
    calls = []
    monkeypatch.setattr(w._writer, "run_maintenance", lambda **kw: calls.append(kw) or {})
    for _ in range(3):
        w._persist_v8(([], []))
    assert calls == [] and w._post_migration_followup_at is None
    w._close_connection()


def test_a_real_v7_upgrade_arms_the_follow_up_and_a_fresh_file_does_not(tmp_path):
    from netspeedtray.tests.unit.test_db_switchover import DB, _make_v7
    v7 = tmp_path / "v7"
    v7.mkdir()
    _make_v7(v7)
    w = DatabaseWorker(v7 / DB)
    w._prepare_storage()
    try:
        assert w._post_migration_followup_armed
    finally:
        w._close_connection()
    fresh = DatabaseWorker(tmp_path / DB)
    fresh._prepare_storage()
    try:
        assert not fresh._post_migration_followup_armed
    finally:
        fresh._close_connection()
