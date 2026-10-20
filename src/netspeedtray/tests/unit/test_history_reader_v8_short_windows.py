"""
Short windows (<= 24 h) on a v8 file whose recent hours are MINUTE rows below the raw watermark - the
shape the v7 -> v8 upgrade leaves for its first day (the network raw tier is folded into minutes).

2.2.0-beta.1 live smoke test on the owner's migrated history: the 12 h Overview graph drew an 87 Mbps
spike while the same window's summary said "Peak 2.3 Mbps", and the Stats sheet "1,005 samples, 2%".
Both summaries had a "<= 24 h reads raw only" shortcut, so they saw only the seconds recorded since the
upgrade. The tier answering a range is the watermarks' call (schema_v8 contract), not the window's
length. Same file: "busiest hour" must be an hour's AVERAGE, never the graph's per-bucket peaks.
"""
from datetime import datetime, timedelta

import pytest

from netspeedtray.core.db import schema_v8 as S8
from netspeedtray.tests.unit import v8_twin_fixture as F
from netspeedtray.utils import summaries as S

NOW = F.fixed_now()
NOW_TS = int(NOW.timestamp())
BASE = NOW_TS // 3600 * 3600          # hour-aligned: the raw watermark sits one hour before it
WM_RAW = BASE - 3600
SPIKE_TS = BASE - 10 * 3600           # an hour of bursts: 88 Mbps peaks, a 1 kB/s average
BUSY_HOUR = BASE - 5 * 3600           # an hour of sustained 500 kB/s (4 Mbps) - the real busiest hour
SPIKE_MAX = 11_000_000.0
MB = 1_000_000.0


def _history() -> F.History:
    h = F.History(now_ts=NOW_TS, wm_raw=WM_RAW, wm_minute=BASE - 40 * 86400)
    for ts in range(BASE - 11 * 3600, WM_RAW, 60):
        avg, mx = (500_000.0, 600_000.0) if BUSY_HOUR <= ts < BUSY_HOUR + 3600 else (1000.0, 2000.0)
        if SPIKE_TS <= ts < SPIKE_TS + 3600:
            mx = SPIKE_MAX
        # migrated shape: bytes NULL, sample_count = the v7 per-second sample count
        h.minute.append((ts, "Ethernet", 100.0, avg, 200.0, mx, 60, None, None))
        h.hw_minute.append((ts, "cpu", 20.0, 95.0 if ts == SPIKE_TS else 30.0, 60))
    for ts in range(WM_RAW, NOW_TS + 1, 10):
        h.raw.append((ts, "Ethernet", 100, 2000))
        h.hw_raw.append((ts, "cpu", 10.0))
    return h


@pytest.fixture(scope="module")
def reader(tmp_path_factory):
    d = tmp_path_factory.mktemp("short")
    F.write_v8(d / "speed_history.db", _history()).close()
    _, r8, conns = F.readers(d / "speed_history.db", d / "speed_history.db", now=NOW)
    yield r8
    for c in conns:
        c.close()


def _window(hours):
    return NOW - timedelta(hours=hours), NOW


@pytest.mark.parametrize("hours", [12, 24])
def test_short_window_summary_reads_the_folded_minutes(reader, hours):
    s, e = _window(hours)
    summ = reader.summarize_network("download", s, e)
    graph_peak = max(p[2] for p in reader.speed_history(s, e, None, True, 'auto'))
    assert summ.max == SPIKE_MAX == graph_peak          # summary peak == the graph's peak
    assert summ.tier == "minute" and not summ.exact     # honest: rollup rows -> no percentiles
    # Coverage: 600 folded minutes x 60 s + the raw seconds (1 s each, sampled every 10 s), not 2%.
    raw_s = len(range(WM_RAW, NOW_TS + 1, 10))
    assert summ.coverage_pct == pytest.approx(round((600 * 60 + raw_s) / (hours * 3600) * 100, 1), abs=0.1)


def test_short_window_hardware_summary_reads_the_minutes_too(reader):
    s, e = _window(12)
    summ = reader.summarize_hardware("cpu", s, e)
    assert summ.max == 95.0 and summ.tier == "minute"
    graph = reader.hardware_history("cpu", s, e)
    assert max(v for _, v in graph) == 20.0   # the graph plots minute AVERAGES for hardware


def test_a_window_inside_the_raw_tier_stays_exact(reader):
    """Steady state (no rollup row in the window): the exact raw summary, exactly as before."""
    s, e = NOW - timedelta(minutes=30), NOW
    summ = reader.summarize_network("download", s, e)
    assert summ.tier == "raw" and summ.exact and summ.max == 2000.0 and summ.p95 == 2000.0
    hw = reader.summarize_hardware("cpu", s, e)
    assert hw.tier == "raw" and hw.exact and hw.max == 10.0


def test_a_raw_resolution_window_over_folded_minutes_is_drawn_at_minute_resolution(reader):
    """<= 6 h windows plot raw resolution, which reads only the raw tier: the hours just before the
    upgrade drew EMPTY while their summary showed traffic. Drawn from the minute tier instead."""
    s, e = _window(6)
    pts = reader.speed_history(s, e, None, True, 'auto')
    folded = [p for p in pts if s.timestamp() <= p[0] < WM_RAW]
    assert folded and max(p[2] for p in folded) == 600_000.0
    assert max(p[2] for p in pts) == reader.summarize_network("download", s, e).max
    # An explicit 'raw' request still means the raw tier only.
    assert all(p[0] >= WM_RAW or p[2] == 0 for p in reader.speed_history(s, e, None, True, 'raw'))


def test_busiest_hour_is_the_highest_average_not_the_highest_peak(reader):
    s, e = _window(12)
    prof = reader.network_hourly_profile("download", s, e)
    po = S.peak_offpeak_from_profile(prof)
    busy = datetime.fromtimestamp(BUSY_HOUR).hour
    assert po["peak_hour"] == busy and po["peak_avg"] == pytest.approx(500_000.0)
    assert po["peak_avg"] < SPIKE_MAX / 10            # never an hour's peak
    spike = datetime.fromtimestamp(SPIKE_TS).hour
    assert prof[spike] == pytest.approx(1000.0)
    # The old source (graph peaks) names the spike hour - the bug.
    old = S.peak_offpeak([(datetime.fromtimestamp(p[0]), p[2])
                          for p in reader.speed_history(s, e, None, True, 'auto')])
    assert old["peak_hour"] == spike


def test_hourly_profile_averages_back_to_the_summary(reader):
    """Same samples and weights as summarize_network: the hours, weighted, ARE the window average."""
    s, e = _window(24)
    summ = reader.summarize_network("download", s, e)
    prof = reader.network_hourly_profile("download", s, e)
    w = {}
    for ts in range(BASE - 11 * 3600, WM_RAW, 60):
        w[datetime.fromtimestamp(ts).hour] = w.get(datetime.fromtimestamp(ts).hour, 0) + 60
    for ts in range(WM_RAW, NOW_TS + 1, 10):
        w[datetime.fromtimestamp(ts).hour] = w.get(datetime.fromtimestamp(ts).hour, 0) + 1
    assert set(prof) == set(w)
    assert sum(prof[h] * w[h] for h in w) / sum(w.values()) == pytest.approx(summ.avg)


def test_hourly_profile_per_interface_and_empty(reader):
    s, e = _window(12)
    assert reader.network_hourly_profile("upload", s, e, "Ethernet")
    assert reader.network_hourly_profile("upload", s, e, "Wi-Fi 3") == {}
    assert reader.network_hourly_profile("upload", NOW - timedelta(days=60), NOW - timedelta(days=59)) == {}
