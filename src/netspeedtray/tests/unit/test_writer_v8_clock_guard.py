"""
The live-history clock guard (2.2 RC checkup F3 + F4; harness h1_clock).

F3: one maintenance pass with the wall clock far AHEAD (a bad RTC / BIOS, a VM restore) rolled every
tier up and pruned the hour tier at (fake now - retention): the whole history was gone (also in 2.1.7).
F4: one pass with the clock BEHIND (a dead CMOS -> 2020) rewrote the watermarks at the fake time, which
hid everything older than 24 h until the next pass.

WriterV8._observe_clock now gates roll-ups (watermark moves) and the prune on a clock continuous with
the last trusted pass, and accepts a new clock only after passes have agreed with it for
CLOCK_ACCEPT_SECONDS (so a legit correction cannot block maintenance for good); the prune further
waits BACKUP_CLOCK_CONTINUITY_DAYS after an acceptance.
"""
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from netspeedtray import constants
from netspeedtray.core.db import schema_v8 as S
from netspeedtray.core.db import writer_v8 as W
from netspeedtray.core.db.history_reader import V8HistoryReader

DAY = 86400
NOW = int(datetime(2026, 10, 21, 12, 0).timestamp())
R = constants.data.retention
WM_KEYS = (S.WATERMARK_RAW_KEY, S.WATERMARK_MINUTE_KEY, S.HW_WATERMARK_RAW_KEY, S.HW_WATERMARK_MINUTE_KEY)


def _build(path: Path, now: int = NOW, days: int = 60, established: bool = True):
    """h1's fixture: ``days`` of traffic, one 1 MB sample every 10 minutes, one trusted pass at ``now``.
    ``established``: the file has been maintained on a steady clock for long (no continuity week
    pending, as on a fresh file's first stamp)."""
    c = sqlite3.connect(path)
    S.create_v8_schema(c)
    c.execute("PRAGMA journal_mode = WAL")
    c.execute("INSERT INTO metadata (key, value) VALUES ('db_version', '8')")
    c.commit()
    w = W.WriterV8(c, version="t", poll_ms=1000)
    t = now - days * DAY
    while t < now:
        w.add_net_sample(W.NetSample(ts=t, dt_ms=1000, byte_deltas={"Ethernet": (0, 1_000_000)}))
        t += 600
        if len(w._net_rows) > 5000:
            w.flush(now)
    w.flush(now)
    out = w.run_maintenance(now_ts=now, retention_days=365)
    assert "clock_untrusted" not in out
    if established:
        c.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, '0')",
                  (W.WriterV8.CLOCK_CONTINUOUS_SINCE_KEY,))
        c.commit()
    return c, w


def _volume_mb(path: Path, now: int = NOW) -> float:
    r = V8HistoryReader(lambda: sqlite3.connect(path), lambda: path, clock=lambda: now)
    v = r.volume(datetime.fromtimestamp(now - 400 * DAY), datetime.fromtimestamp(now), None, 1.0)
    return round(v.down_bytes / 1e6)


def _counts(c):
    return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("net_raw", "net_minute", "net_hour")}


def _wms(c):
    return dict(c.execute(f"SELECT key, value FROM metadata WHERE key IN ({','.join('?' * 4)})", WM_KEYS))


FULL_MB = 60 * 144


def test_one_pass_with_the_clock_two_years_ahead_prunes_nothing(tmp_path):
    p = tmp_path / "a.db"
    c, w = _build(p)
    before, wms = _counts(c), _wms(c)
    out = w.run_maintenance(now_ts=NOW + 2 * 365 * DAY, retention_days=365)
    assert out.get("clock_untrusted") == 1 and "pruned" not in out
    assert _counts(c) == before and _wms(c) == wms
    assert _volume_mb(p) == FULL_MB                                 # was 0: every row pruned


def test_one_pass_with_the_clock_behind_never_lowers_a_watermark(tmp_path):
    p = tmp_path / "b.db"
    c, w = _build(p)
    wms = _wms(c)
    w.run_maintenance(now_ts=NOW - 6 * 365 * DAY, retention_days=365)
    assert _wms(c) == wms                                           # was rewritten at 2020
    assert _volume_mb(p) == FULL_MB                                 # was 144 MB (the last 24 h)
    out = w.run_maintenance(now_ts=NOW + 60, retention_days=365)    # the corrected clock: trusted again
    assert "clock_untrusted" not in out
    assert _volume_mb(p, NOW + 60) == FULL_MB


def test_in_session_a_wall_clock_behind_the_monotonic_clock_is_caught(tmp_path):
    """Within a session the monotonic clock says the pass came an hour after the last one; a wall
    clock that went BACK a day is a jump (the date rule alone would let 1 h - 1 day through as drift)."""
    c, w = _build(tmp_path / "c.db")
    w.run_maintenance(now_ts=NOW + 3600, retention_days=365, mono=1000.0)          # trusted, in-session
    wms = _wms(c)
    out = w.run_maintenance(now_ts=NOW + 3600 - DAY // 2, retention_days=365, mono=1000.0 + 3600)
    assert out.get("clock_untrusted") == 1 and _wms(c) == wms


@pytest.mark.parametrize("asleep_h", [8, 13 * 24])
def test_sleep_the_monotonic_clock_did_not_count_is_like_app_off_time(tmp_path, asleep_h):
    """r3 B / D: a monotonic clock that stops across sleep (or a VM guest the host suspended) leaves the
    wall clock AHEAD of it - not a jump. It used to pause roll-ups for 6 h and restart the prune's
    continuity week every night, so the prune never ran."""
    c, w = _build(tmp_path / "s.db")
    mono, wall = 1000.0, NOW + 3600
    w.run_maintenance(now_ts=wall, retention_days=365, mono=mono)
    since = c.execute("SELECT value FROM metadata WHERE key = ?", (W.WriterV8.CLOCK_CONTINUOUS_SINCE_KEY,)).fetchone()
    out = w.run_maintenance(now_ts=wall + 3600 + asleep_h * 3600, retention_days=365, mono=mono + 3600)
    assert "clock_untrusted" not in out and "pruned" in out
    assert c.execute("SELECT value FROM metadata WHERE key = ?",
                     (W.WriterV8.CLOCK_CONTINUOUS_SINCE_KEY,)).fetchone() == since


def test_a_month_of_nightly_sleep_with_a_sleep_blind_monotonic_clock_keeps_pruning(tmp_path):
    """r3 case B, end to end: 30 days, 16 h awake + 8 h asleep, monotonic excluding sleep."""
    c, w = _build(tmp_path / "m.db", days=40)
    mono, wall, untrusted, pruned_passes = 1000.0, NOW + 3600, 0, 0
    for day in range(30):
        for h in range(16):
            out = w.run_maintenance(now_ts=wall, retention_days=31, mono=mono)
            untrusted += out.get("clock_untrusted", 0)
            pruned_passes += "pruned" in out
            wall += 3600
            mono += 3600
        wall += 8 * 3600
    assert untrusted == 0 and pruned_passes == 30 * 16


def test_a_fresh_stamp_starts_the_prunes_continuity_week(tmp_path):
    """S4: the first stamp on a file (every 2.2 beta file, every fresh migration) does not prove the
    clock it was written on: the prune waits BACKUP_CLOCK_CONTINUITY_DAYS of steady passes."""
    c, w = _build(tmp_path / "w.db", days=40, established=False)
    t, mono = NOW, 0.0
    out = w.run_maintenance(now_ts=t + 3600, retention_days=31, mono=mono)
    assert "clock_untrusted" not in out and out["pruned"] == 0       # rolls up, prunes nothing yet
    while t < NOW + R.BACKUP_CLOCK_CONTINUITY_DAYS * DAY:
        t, mono = t + 3600, mono + 3600
        out = w.run_maintenance(now_ts=t + 3600, retention_days=31, mono=mono)
    assert out["pruned"] > 0


def test_steady_hourly_passes_keep_rolling_up_and_pruning(tmp_path):
    p = tmp_path / "d.db"
    c, w = _build(p, days=40)
    mono = 5000.0
    for h in range(1, 4):
        out = w.run_maintenance(now_ts=NOW + h * 3600, retention_days=31, mono=mono + h * 3600)
        assert "clock_untrusted" not in out
    assert out["pruned"] > 0                                        # retention still applies
    assert int(_wms(c)[S.WATERMARK_RAW_KEY]) >= NOW + 3 * 3600 - DAY - 60


def test_a_legit_large_correction_is_accepted_after_steady_passes(tmp_path):
    """The clock really moved (it had been wrong for weeks, or the PC was off for a month): roll-ups
    resume once the new clock has held for CLOCK_ACCEPT_SECONDS; the prune waits the continuity week."""
    p = tmp_path / "e.db"
    c, w = _build(p, days=40)
    new = NOW + 40 * DAY
    hours = R.CLOCK_ACCEPT_SECONDS // 3600
    outs = [w.run_maintenance(now_ts=new + h * 3600, retention_days=31) for h in range(hours + 1)]
    assert all(o.get("clock_untrusted") == 1 for o in outs[:hours])
    assert "clock_untrusted" not in outs[hours]
    assert outs[hours]["pruned"] == 0                                # accepted, not yet continuous
    assert int(_wms(c)[S.WATERMARK_RAW_KEY]) > NOW                  # roll-ups moved on the new clock
    t = new + hours * 3600
    while t < new + R.BACKUP_CLOCK_CONTINUITY_DAYS * DAY:
        t += 3600
        out = w.run_maintenance(now_ts=t, retention_days=31)
    assert out["pruned"] > 0


def test_acceptance_survives_restarts(tmp_path):
    """Short sessions: the clock being confirmed is persisted, so a new writer (next app start) carries
    on with it instead of starting over - maintenance cannot be blocked for good."""
    p = tmp_path / "f.db"
    c, w = _build(p)
    new = NOW - 300 * DAY                                           # corrected backward, a lot
    for h in range(0, R.CLOCK_ACCEPT_SECONDS // 3600):
        w = W.WriterV8(c, version="t", poll_ms=1000)                # a fresh session every hour
        assert w.run_maintenance(now_ts=new + h * 3600).get("clock_untrusted") == 1
    w = W.WriterV8(c, version="t", poll_ms=1000)
    assert "clock_untrusted" not in w.run_maintenance(now_ts=new + R.CLOCK_ACCEPT_SECONDS)
    assert c.execute("SELECT value FROM metadata WHERE key = ?", (W.WriterV8.CLOCK_CANDIDATE_KEY,)).fetchone() is None


def test_a_clock_that_keeps_jumping_is_never_accepted(tmp_path):
    c, w = _build(tmp_path / "g.db")
    wms = _wms(c)
    for i in range(12):                                             # every pass a different wrong year
        w.run_maintenance(now_ts=NOW + (i % 2 * 2 - 1) * (i + 2) * 365 * DAY, retention_days=365)
    assert _wms(c) == wms


def test_the_untrusted_pass_is_logged_once(tmp_path, caplog):
    c, w = _build(tmp_path / "h.db")
    with caplog.at_level(logging.WARNING):
        for h in range(3):
            w.run_maintenance(now_ts=NOW + 2 * 365 * DAY + h * 60)
    assert sum("paused" in r.message for r in caplog.records) == 1


def test_first_pass_on_a_file_with_no_trusted_stamp_checks_the_newest_row(tmp_path):
    """An existing 2.2 beta / freshly migrated file has no clock_trusted_ts yet: its newest row stands
    in for the stamp, in both directions (r4: the first pass at +2 y used to prune all 60 days)."""
    p = tmp_path / "i.db"
    c, w = _build(p)
    wms = _wms(c)
    c.execute("DELETE FROM metadata WHERE key = ?", (W.WriterV8.CLOCK_TRUSTED_KEY,))
    c.commit()
    w2 = W.WriterV8(c, version="t", poll_ms=1000)
    assert w2.run_maintenance(now_ts=NOW - 6 * 365 * DAY, retention_days=365).get("clock_untrusted") == 1
    assert _wms(c) == wms and _volume_mb(p) == FULL_MB
    c.execute("DELETE FROM metadata WHERE key IN (?, ?)",
              (W.WriterV8.CLOCK_TRUSTED_KEY, W.WriterV8.CLOCK_CANDIDATE_KEY))
    c.commit()
    w3 = W.WriterV8(c, version="t", poll_ms=1000)
    assert w3.run_maintenance(now_ts=NOW + 2 * 365 * DAY, retention_days=365).get("clock_untrusted") == 1
    assert _wms(c) == wms and _volume_mb(p) == FULL_MB
    w4 = W.WriterV8(c, version="t", poll_ms=1000)
    c.execute("DELETE FROM metadata WHERE key IN (?, ?)",
              (W.WriterV8.CLOCK_TRUSTED_KEY, W.WriterV8.CLOCK_CANDIDATE_KEY))
    c.commit()
    assert "clock_untrusted" not in w4.run_maintenance(now_ts=NOW + 5 * DAY)   # app off 5 days: fine


def test_an_accepted_wrong_clock_then_corrected_never_hides_rolled_up_rows(tmp_path):
    """r4 / S5: two years ahead for 8 h (accepted after 6 h - everything rolled up at the fake time),
    then corrected (accepted again). Lowering the watermarks back to the real clock shadowed the rows
    the wrong clock had rolled up: 8640 -> 4410 MB, the last 7 days read 0."""
    p = tmp_path / "r4.db"
    c, w = _build(p)
    ahead = 730 * DAY
    for h in range(8):
        w.run_maintenance(now_ts=NOW + ahead + h * 3600, retention_days=365)
    real = NOW + 8 * 3600
    for h in range(8):
        out = w.run_maintenance(now_ts=real + h * 3600, retention_days=365)
    assert "clock_untrusted" not in out
    end = real + 7 * 3600
    assert _volume_mb(p, end) == FULL_MB
    r = V8HistoryReader(lambda: sqlite3.connect(p), lambda: p, clock=lambda: end)
    last7 = r.volume(datetime.fromtimestamp(end - 7 * DAY), datetime.fromtimestamp(end), None, 1.0).down_bytes
    assert round(last7 / 1e6) == 7 * 144 - 15 * 6                   # the data stops at NOW; 15 h later


def test_database_worker_hands_the_writer_the_monotonic_clock(tmp_path, monkeypatch):
    from netspeedtray.core.database import DatabaseWorker
    w = DatabaseWorker(tmp_path / "speed_history.db")
    w._prepare_storage()
    seen = {}
    real = w._writer.run_maintenance

    def spy(**kw):
        seen.update(kw)
        return real(**kw)
    monkeypatch.setattr(w._writer, "run_maintenance", spy)
    w._run_maintenance({"keep_data": 365})
    assert isinstance(seen.get("mono"), float)
    w._close_connection()
