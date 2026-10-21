"""
v8 reader backend (database v8 step 3, dormant): Gate 5 and coverage.

**Gate 5** (release-plan, D2): the loss series keep one meaning across old 0/1 rows and new fractional
rows (0.25 steps). Both shapes - in the same hour and across tiers - are written by the real v7 write path
and roll-up, then carried untouched into a v8 file (as the migrator will: hw values unchanged, dimension
ids assigned). Every reader the gate names must give the SAME answer on both backends, and the right one:
the summary avg -> ``summaries.loss_pct``, ``outage_summary`` (0.25 is not a drop, 0.5 is), the exporter's
files (byte-identical), and the ``DATABASE.md`` recipe (through the v7-named compatibility views).

**Coverage**: whether the app was running is a fact from ``app_sessions`` (heartbeat-bounded), never
inferred from traffic rows and never backfilled - time before the first recorded session is UNKNOWN,
not "not running".
"""
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from netspeedtray import constants
from netspeedtray.core.db import schema_v8 as S8
from netspeedtray.core.db.sqlite_uri import sqlite_ro_uri
from netspeedtray.tests.unit import v8_twin_fixture as F
from netspeedtray.utils import summaries as S

LAT = constants.network.latency
_REPO_ROOT = Path(__file__).resolve().parents[4]
# One clock for the data, the windows and the readers (v8_twin_fixture.fixed_now). The DATABASE.md
# recipe reads SQLite's own 'now' (-7 days), so an NST_TEST_NOW override must stay within ~3 days.
NOW = F.fixed_now()


def _build_v7(path: Path) -> None:
    """Same shape as test_latency_bursts: each hour, 10 single-echo cycles (one lost) then 10 bursts
    (two at 0.5, and on the anchor one 0.25 and one 0.75). Router loss 10%, internet loss 15%."""
    from netspeedtray.tests.legacy_v7_worker import LegacyV7Worker
    w = LegacyV7Worker(path)
    w._initialize_connection()
    w._check_and_create_schema()
    now = int(NOW.timestamp())
    batch = []
    for h in (((now - 3 * 86400) // 3600) * 3600, ((now - 2 * 3600) // 3600) * 3600):
        for i in range(20):
            ts = h + 60 + 3 * i
            if i < 10:
                g = a = 1.0 if i == 0 else 0.0
            else:
                g = 0.5 if i in (10, 11) else 0.0
                a = {10: 0.5, 11: 0.5, 12: 0.25, 13: 0.75}.get(i, 0.0)
            if g < 1.0:
                batch.append((ts, "latency_gw", 4.0 + i % 3))
            if a < 1.0:
                batch.append((ts, "latency_anchor", 20.0 + i % 5))
            batch.append((ts, LAT.STAT_GW_TIMEOUT, g))
            batch.append((ts, LAT.STAT_ANCHOR_TIMEOUT, a))
    w._persist_hardware_batch(batch)
    w._aggregate_hardware_raw_to_minute(w.conn.cursor(), NOW)
    w.conn.commit()
    w._close_connection()


def _carry_to_v8(v7: Path, v8: Path) -> None:
    """The migration's hardware half: every v7 row, values untouched, into the v8 tables."""
    src = sqlite3.connect(sqlite_ro_uri(v7), uri=True)
    w = F.V8Writer(v8)
    w.hw_raw(src.execute(f"SELECT timestamp, stat_type, value FROM {constants.data.HARDWARE_STATS_TABLE_RAW}").fetchall())
    for v7t, v8t in ((constants.data.HARDWARE_STATS_TABLE_MINUTE, S8.HW_MINUTE),
                     (constants.data.HARDWARE_STATS_TABLE_HOUR, S8.HW_HOUR)):
        w.hw_tier(v8t, src.execute(f"SELECT timestamp, stat_type, avg_value, max_value, sample_count FROM {v7t}").fetchall())
    # Hardware raw rows are not folded at migration: only the hw pair describes them. The network pair is
    # set somewhere that would hide the last day if a hardware reader ever read it (#359 review).
    now = int(NOW.timestamp())
    w.watermarks(now // 60 * 60, now // 3600 * 3600,
                 hw_raw=(now - 24 * 3600) // 60 * 60, hw_minute=(now - 30 * 86400) // 3600 * 3600)
    w.close()
    src.close()
    chk = sqlite3.connect(sqlite_ro_uri(v8), uri=True)
    assert chk.execute(f"SELECT COUNT(*) FROM {S8.HW_MINUTE}").fetchone()[0] > 0, "no rows reached the minute tier"
    chk.close()


@pytest.fixture(scope="module")
def pair(tmp_path_factory, q_app):
    d = tmp_path_factory.mktemp("gate5")
    _build_v7(d / "v7.db")
    _carry_to_v8(d / "v7.db", d / "v8.db")
    r7, r8, conns = F.readers(d / "v7.db", d / "v8.db")
    yield d, r7, r8
    for c in conns:
        c.close()


def _loss(reader, series, start, end):
    s = reader.summarize_hardware(series, start, end, 3.0)
    return S.loss_pct((s.avg or 0.0) * s.count, s.count), s


@pytest.mark.parametrize("hours, gw, anchor", [(6, 10.0, 15.0), (24 * 4, 10.0, 15.0)])
def test_loss_pct_is_the_same_rate_on_both_backends(pair, hours, gw, anchor):
    """6 h = the new hour, raw (exact path); 4 days = both hours, one of them rolled into minute rows
    (rollup path) - the mixed shapes average to the same per-cycle rate either way."""
    _, r7, r8 = pair
    now = NOW
    start = now - timedelta(hours=hours)
    for series, want in ((LAT.STAT_GW_TIMEOUT, gw), (LAT.STAT_ANCHOR_TIMEOUT, anchor)):
        l7, s7 = _loss(r7, series, start, now)
        l8, s8 = _loss(r8, series, start, now)
        assert s7 == s8
        assert l7 == l8 == pytest.approx(want), series


def test_outage_summary_is_the_same_and_0_25_is_not_a_drop(pair):
    _, r7, r8 = pair
    now = NOW
    start = now - timedelta(hours=6)
    for series, want in ((LAT.STAT_GW_TIMEOUT, 2), (LAT.STAT_ANCHOR_TIMEOUT, 3)):
        o7 = S.outage_summary(r7.hardware_history(series, start, now))
        o8 = S.outage_summary(r8.hardware_history(series, start, now))
        assert o7 == o8
        assert o8["count"] == want, f"{series}: the old 0/1 drop, the 0.5 pair (and on the anchor the lone 0.75) - not 0.25"
    # Over 4 days the old hour reads from its minute row (one 0-1 rate): same on both backends.
    start = now - timedelta(days=4)
    assert S.outage_summary(r7.hardware_history(LAT.STAT_GW_TIMEOUT, start, now)) == \
        S.outage_summary(r8.hardware_history(LAT.STAT_GW_TIMEOUT, start, now))


class _WS:
    """The slice of WidgetState the exporter calls, over one reader."""

    def __init__(self, r):
        self.r = r

    def summarize_network(self, direction, s, e, iface=None, poll=1.0):
        return self.r.summarize_network(direction, s, e, iface, poll)

    def summarize_hardware(self, key, s, e, poll=1.0):
        return self.r.summarize_hardware(key, s, e, poll)

    def get_speed_history(self, s, e, iface=None, resolution="auto"):
        return self.r.speed_history(s, e, iface, False, resolution)

    def get_hardware_history(self, key, s, e):
        return self.r.hardware_history(key, s, e)


@pytest.mark.parametrize("days", [0.25, 4])
def test_exporter_files_are_byte_identical(pair, tmp_path, days):
    from netspeedtray.utils.stats_exporter import export_window
    _, r7, r8 = pair
    end = NOW
    start = end - timedelta(days=days)
    p7 = export_window(_WS(r7), start, end, "w", str(tmp_path / "v7"), "x", poll_interval=3.0)
    p8 = export_window(_WS(r8), start, end, "w", str(tmp_path / "v8"), "x", poll_interval=3.0)
    for k in ("summary_csv", "raw_csv", "summary_json"):
        assert Path(p7[k]).read_bytes() == Path(p8[k]).read_bytes(), k
    text = Path(p8["summary_csv"]).read_text(encoding="utf-8")
    row = next(l for l in text.splitlines() if l.split(",")[3] == "latency_gw")
    assert ",10.0," in row, row


def _documented_loss_query() -> str:
    text = (_REPO_ROOT / "DATABASE.md").read_text(encoding="utf-8")
    m = re.search(r"\*\*Internet latency and packet loss[^\n]*\*\*\s*```sql\n(.*?)```", text, re.S)
    assert m, "DATABASE.md lost its latency/loss recipe"
    return m.group(1)


# The 2.1.x DATABASE.md recipe, frozen: since step 7 the document's recipe reads the v8 tables (with the
# watermarks), and it must return exactly what this one returned on the v7 file - and this one must keep
# working on v8 through the v7-named compatibility views (the promise to a rolled-back 2.1.x and to
# anyone's saved query).
_V7_LOSS_QUERY = """
WITH samples AS (
    SELECT timestamp, stat_type, value AS v, 1 AS n FROM hardware_stats_raw
    UNION ALL
    SELECT timestamp, stat_type, avg_value, sample_count FROM hardware_stats_minute
)
SELECT datetime(timestamp / 3600 * 3600, 'unixepoch', 'localtime') AS hour,
       ROUND(SUM(CASE WHEN stat_type='latency_anchor' THEN v * n END)
           / SUM(CASE WHEN stat_type='latency_anchor' THEN n END), 1) AS internet_ms,
       ROUND(100.0 * SUM(CASE WHEN stat_type='latency_gw_timeout' THEN v * n END)
           / SUM(CASE WHEN stat_type='latency_gw_timeout' THEN n END), 1) AS router_loss_pct,
       ROUND(100.0 * SUM(CASE WHEN stat_type='latency_anchor_timeout' THEN v * n END)
           / SUM(CASE WHEN stat_type='latency_anchor_timeout' THEN n END), 1) AS internet_loss_pct
FROM samples
WHERE timestamp > strftime('%s','now','-7 days')
  AND stat_type IN ('latency_anchor', 'latency_gw_timeout', 'latency_anchor_timeout')
GROUP BY timestamp / 3600
ORDER BY timestamp / 3600;
"""


def test_database_md_recipe_returns_the_same_rows_on_both_files(pair):
    """The documented (v8) recipe on the v8 file == the 2.1 recipe on the v7 file == the 2.1 recipe on
    the v8 file through the compatibility views."""
    d, _, _ = pair

    def run(name, q):
        c = sqlite3.connect(sqlite_ro_uri(d / name), uri=True)
        try:
            return c.execute(q).fetchall()
        finally:
            c.close()
    out = [run("v7.db", _V7_LOSS_QUERY), run("v8.db", _documented_loss_query()),
           run("v8.db", _V7_LOSS_QUERY)]
    assert out[0] == out[1] == out[2] and len(out[1]) == 2
    for _, _, router, internet in out[1]:
        assert (router, internet) == (pytest.approx(10.0), pytest.approx(15.0))


# --- coverage from app_sessions ------------------------------------------------------------------

def _sessions_db(tmp_path, sessions):
    w = F.V8Writer(tmp_path / "v8.db")
    w.conn.executemany(f"INSERT INTO {S8.APP_SESSIONS} (started_ts, last_seen_ts, ended_ts, version, poll_ms, "
                       "clean_exit) VALUES (?, ?, ?, '2.2.0', 1000, ?)", sessions)
    w.close()
    _, r8, conns = F.readers(tmp_path / "v8.db", tmp_path / "v8.db")
    return r8, conns


def test_coverage_is_a_fact_from_sessions_never_backfilled(tmp_path):
    now = int(NOW.timestamp())
    T = now - 10 * 3600
    r8, conns = _sessions_db(tmp_path, [
        (T, T + 3595, T + 3600, 1),            # clean exit: ended_ts wins
        (T + 7200, T + 9000, None, 0),          # crash / taskkill: bounded by the last heartbeat
        (T + 8000, T + 9990, T + 10000, 1),     # a second instance overlapping it (portable beside installed)
    ])
    cov = r8.coverage(datetime.fromtimestamp(T - 3600), datetime.fromtimestamp(T + 4 * 3600))
    assert cov.unknown_seconds == 3600, "before the first session there is no record either way"
    assert cov.running_seconds == 3600 + (T + 10000 - (T + 7200))
    assert cov.not_running_seconds == 5 * 3600 - 3600 - cov.running_seconds
    assert cov.sessions == ((T, T + 3600), (T + 7200, T + 10000))
    # a window inside a session, one in a known gap, one wholly before tracking began
    assert r8.coverage(datetime.fromtimestamp(T + 60), datetime.fromtimestamp(T + 120)).running_seconds == 60
    gap = r8.coverage(datetime.fromtimestamp(T + 4000), datetime.fromtimestamp(T + 7000))
    assert (gap.running_seconds, gap.not_running_seconds, gap.unknown_seconds) == (0, 3000, 0)
    before = r8.coverage(datetime.fromtimestamp(T - 7200), datetime.fromtimestamp(T - 3600))
    assert (before.running_seconds, before.not_running_seconds, before.unknown_seconds) == (0, 0, 3600)
    # the future is not "not running": the window is clipped at now
    fut = r8.coverage(datetime.fromtimestamp(now - 60), datetime.fromtimestamp(now + 3600))
    assert fut.end_ts <= now + 1 and fut.running_seconds + fut.not_running_seconds + fut.unknown_seconds == \
        fut.end_ts - fut.start_ts
    for c in conns:
        c.close()


def test_coverage_with_no_sessions_is_all_unknown_and_v7_has_none(tmp_path, q_app):
    r8, conns = _sessions_db(tmp_path, [])
    now = NOW
    cov = r8.coverage(now - timedelta(days=1), now)
    assert (cov.running_seconds, cov.not_running_seconds) == (0, 0) and cov.unknown_seconds > 86000
    F.write_v7(tmp_path / "v7.db", F.History(now_ts=int(now.timestamp())))
    r7, _, c7 = F.readers(tmp_path / "v7.db", tmp_path / "v8.db")
    assert r7.coverage(now - timedelta(days=1), now) is None, "v7 records no sessions: coverage is unknown"
    for c in conns + c7:
        c.close()


def test_coverage_ignores_a_garbled_session(tmp_path):
    """last_seen before start (a clock step mid-session) contributes nothing rather than negative time."""
    now = int(NOW.timestamp())
    r8, conns = _sessions_db(tmp_path, [(now - 7200, now - 7200, None, 0), (now - 3600, now - 4000, None, 0)])
    cov = r8.coverage(datetime.fromtimestamp(now - 7200), datetime.fromtimestamp(now))
    assert cov.running_seconds == 0 and cov.not_running_seconds == 7200
    for c in conns:
        c.close()
