"""
rc.2 batch S-B - the Monitor network graph says only what was measured (graph-plan section 1).

* S1: the hover reports MEASURED samples, not the spline / smoothing the line is drawn with (3 of every
  4 points on the Week line were interpolated, and the hover read them).
* S2: the speed axes carry their unit and follow ``unit_type`` (they were bare Mbps numbers under a
  "Download" label while the hover, one inch away, said MB/s).
* S3: past 6 h each point is the PEAK of a minute / hour / day bucket; a caption says so.
* S4: time nobody measured (app off, PC asleep, before the first recorded session) is a gap, not a
  dashed zero line; measured idle (the app ran, nothing moved - v8 stores no all-zero rows) is zero.
  The reader no longer pads windows with synthetic zero rows.

Real GraphRenderer code on real matplotlib axes (no Qt canvas), as test_graph_series_labels; the reader
parts on a real v8 file (v8_twin_fixture).
"""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import matplotlib.dates as mdates
import numpy as np
import pytest
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.core.db import schema_v8 as S8
from netspeedtray.tests.unit import v8_twin_fixture as F
from netspeedtray.tests.unit.test_graph_series_labels import _bare_init
from netspeedtray.views.graph.renderer import GraphRenderer
from netspeedtray.views.monitor.graph_hover import GraphHoverTooltip

T0 = datetime(2026, 7, 1, 0, 0, 0)
EN = I18nStrings("en_US")


@pytest.fixture
def renderer(monkeypatch):
    monkeypatch.setattr(GraphRenderer, "_init_matplotlib", _bare_init)
    return GraphRenderer(MagicMock(), EN)


def _agg_init(self):
    """A real Agg figure (render() rebuilds its own axes on it), as test_value_axis_ticks."""
    self.figure = Figure(figsize=(9.3, 4.3), dpi=100)
    self.canvas = FigureCanvasAgg(self.figure)
    self._setup_standard_axes()


@pytest.fixture
def agg_renderer(monkeypatch):
    monkeypatch.setattr(GraphRenderer, "_init_matplotlib", _agg_init)
    return GraphRenderer(MagicMock(), EN)


def speed_axis_unit(*a, **k):
    from netspeedtray.views.graph.value_axis import speed_axis_unit as f
    return f(*a, **k)


def _hover(ax, when: datetime):
    rows, at = GraphHoverTooltip(SimpleNamespace())._rows_at(ax, mdates.date2num(when))
    return {lbl: y for lbl, y, _c in rows}, at


def _bps(mbps):
    return mbps * 1e6 / 8


# ============================================================================ S1 - hover reads samples

def test_hover_reports_a_measured_sample_not_the_spline(renderer):
    """48 hourly samples alternating 50 / 10 Mbps: the spline is on (<= 600 points), so the line has
    three interpolated points between each pair. Hovering 20 minutes past a sample sits on an
    interpolated point (within the cursor's pixel column); the readout must still be a sample."""
    dts = [T0 + timedelta(hours=i) for i in range(48)]
    down = np.array([50.0 if i % 2 == 0 else 10.0 for i in range(48)])
    renderer._plot_high_res(np.array(dts), down / 10, down)
    ax = renderer.ax_download
    ax.set_xlim(mdates.date2num(dts[0]), mdates.date2num(dts[-1]))
    line = ax.get_lines()[0]
    assert len(line.get_xdata()) > len(dts), "precondition: the line is the interpolated curve"
    plotted = set(np.round(np.asarray(line.get_ydata(), dtype=float), 6))
    assert plotted - {50.0, 10.0}, "precondition: the curve has values nobody measured"
    sample_x = {round(mdates.date2num(d), 9) for d in dts}
    for i in range(0, 46, 3):
        vals, at = _hover(ax, dts[i] + timedelta(minutes=20))
        assert vals[EN.DOWNLOAD_LABEL] in (50.0, 10.0)
        assert round(at, 9) in sample_x


def test_hover_on_a_smoothed_hardware_line_reports_the_sample(renderer):
    """Smooth averages 0/100 alternation to ~50 on the drawn line; the hover says what was measured."""
    data = [(T0 + timedelta(seconds=10 * i), 100.0 if i % 2 == 0 else 0.0) for i in range(120)]
    renderer._render_hwsingle(data, T0, data[-1][0], "TIMELINE_30_MIN", "cpu",
                              hw_styles={"smoothing": True, "smooth_window": 9})
    ax = renderer.ax_download
    drawn = np.asarray(ax.get_lines()[0].get_ydata(), dtype=float)
    assert drawn[10:-10].max() < 90, "precondition: the drawn line is smoothed"
    for i in (30, 31, 60, 61):
        vals, _ = _hover(ax, data[i][0])
        assert vals[renderer._hw_role_label("cpu")] == data[i][1]


# ============================================================================ S2 - axis unit

@pytest.mark.parametrize("top, unit_type, force, scale, label", [
    (0.1, "bits_decimal", False, 1000.0, "Kbps"),             # the 0.1 Mbps floor is 100 Kbps
    (150.0, "bits_decimal", False, 1.0, "Mbps"),
    (5000.0, "bits_decimal", False, 0.001, "Gbps"),
    (150.0, "bytes_decimal", False, 0.125, "MB/s"),
    (150.0, "bytes_binary", False, 1e6 / 8 / 1024 ** 2, "MiB/s"),
    (0.5, "bytes_decimal", False, 125.0, "KB/s"),
    (0.1, "bits_decimal", True, 1.0, "Mbps"),                  # "always Mbps" keeps the mega floor
    (0.1, "nonsense", False, 1000.0, "Kbps"),                  # unknown setting -> bits_decimal
])
def test_speed_axis_unit(top, unit_type, force, scale, label):
    got_scale, got_label = speed_axis_unit(top, EN, unit_type, force)
    assert got_label == getattr(EN, {"Kbps": "KBITS_LABEL", "Mbps": "MBITS_LABEL", "Gbps": "GBITS_LABEL",
                                     "MB/s": "MBPS_LABEL", "MiB/s": "MIBPS_LABEL",
                                     "KB/s": "KBPS_LABEL"}[label])
    assert got_scale == pytest.approx(scale)


def test_scaled_locator_ticks_on_round_numbers_of_the_shown_unit():
    from netspeedtray.views.graph.value_axis import ScaledMaxNLocator
    loc = ScaledMaxNLocator(0.125, nbins=5, steps=[1, 2, 2.5, 5, 10], min_n_ticks=3)
    shown = np.asarray(loc.tick_values(0, 150)) * 0.125              # 0-150 Mbps as MB/s
    assert np.allclose(shown, np.round(shown)), shown                  # 0, 5, 10 ... not 3.125, 6.25


def _net_rows(mbps_down, mbps_up=None, start=T0, step_s=60):
    up = mbps_up if mbps_up is not None else [v / 10 for v in mbps_down]
    return [((start + timedelta(seconds=step_s * i)).timestamp(), _bps(u), _bps(d))
            for i, (u, d) in enumerate(zip(up, mbps_down))]


def _tick_texts(r, ax):
    r.figure.canvas.draw()
    lo, hi = ax.get_ylim()
    return [(t.get_position()[1], t.get_text()) for t in ax.get_yticklabels()
            if lo - 1e-9 <= t.get_position()[1] <= hi + 1e-9 and t.get_text()]


@pytest.mark.parametrize("unit_type, label", [("bits_decimal", "Mbps"), ("bytes_decimal", "MB/s"),
                                              ("bytes_binary", "MiB/s")])
def test_network_axes_carry_the_users_unit(agg_renderer, unit_type, label):
    r = agg_renderer
    rows = _net_rows([20.0 + (i % 7) * 15 for i in range(120)], [1.0 + (i % 5) for i in range(120)])
    end = T0 + timedelta(hours=2)
    r.render(rows, T0, end, "TIMELINE_4_HOURS", stat_type="network",
             hw_styles={"unit_type": unit_type}, force_rebuild=True)
    assert r.ax_download.get_ylabel() == f"{EN.DOWNLOAD_LABEL} ({label})"
    assert r.ax_upload.get_ylabel().startswith(f"{EN.UPLOAD_LABEL} (")
    scale, _ = speed_axis_unit(r.ax_download.get_ylim()[1], EN, unit_type)
    ticks = _tick_texts(r, r.ax_download)
    assert len(ticks) >= 3
    for pos, text in ticks:
        assert float(text) == pytest.approx(pos * scale, abs=1e-6)     # the label IS the tick, converted
        assert float(text) == pytest.approx(round(float(text)))         # on round numbers of that unit


def test_clipped_axis_peak_is_stated_in_the_axis_unit(agg_renderer):
    r = agg_renderer
    vals = [5.0 + (i % 9) for i in range(400)]
    vals[200] = 800.0                                                # one speed test in a week
    rows = _net_rows(vals, start=T0, step_s=1500)
    end = datetime.fromtimestamp(rows[-1][0])
    r.render(rows, end - timedelta(days=7), end, "TIMELINE_WEEK", stat_type="network",
             hw_styles={"unit_type": "bytes_decimal"}, force_rebuild=True)
    peaks = [t.get_text() for t in r.ax_download.texts if t.get_gid() == "value_axis_clip_peak"]
    assert peaks and peaks[0].endswith(EN.MBPS_LABEL) and "100" in peaks[0]   # 800 Mbps = 100 MB/s


# ============================================================================ S3 - caption

def _caption(r):
    return [t.get_text() for t in r.ax_download.texts if t.get_gid() == "net_graph_stat_caption"]


@pytest.mark.parametrize("resolution, key", [("minute", "GRAPH_CAPTION_PEAK_PER_MINUTE"),
                                             ("hour", "GRAPH_CAPTION_PEAK_PER_HOUR"),
                                             ("day", "GRAPH_CAPTION_PEAK_PER_DAY")])
def test_rollup_resolutions_name_the_statistic(agg_renderer, resolution, key):
    r = agg_renderer
    rows = _net_rows([10.0] * 50)
    r.render(rows, T0, T0 + timedelta(hours=24), "TIMELINE_24_HOURS", stat_type="network",
             hw_styles={"resolution": resolution}, force_rebuild=True)
    assert _caption(r) == [getattr(EN, key)]
    # ... and a later raw-resolution render of the same axes takes it down again.
    r.render(rows, T0, T0 + timedelta(hours=1), "TIMELINE_1_HOUR", stat_type="network",
             hw_styles={"resolution": "raw"}, force_rebuild=False)
    assert _caption(r) == []


def test_measured_rate_and_session_carry_no_caption(agg_renderer):
    r = agg_renderer
    rows = _net_rows([10.0] * 50, step_s=1)
    r.render(rows, T0, T0 + timedelta(minutes=1), "TIMELINE_30_MIN", stat_type="network",
             hw_styles={"resolution": "raw"}, force_rebuild=True)
    assert _caption(r) == []
    r.render(rows, T0, T0 + timedelta(minutes=1), "TIMELINE_SESSION", stat_type="network",
             hw_styles={"resolution": "minute"}, force_rebuild=True)
    assert _caption(r) == []


def test_every_caption_key_is_a_real_string_in_every_locale():
    for lang in I18nStrings.LANGUAGE_MAP:
        i18n = I18nStrings(lang)
        for key in constants.graph.NET_GRAPH_STAT_CAPTION_KEYS.values():
            text = getattr(i18n, key)
            assert text and text != key, (lang, key)


# ============================================================================ S4 - gaps, not zeros

def _ts(*secs):
    return np.array(secs, dtype=float)


def test_unmeasured_silence_breaks_the_line():
    ts = _ts(0, 1, 2, 3, 1000, 1001, 1002)
    segs = GraphRenderer._net_segments(ts, np.ones(7), np.ones(7), measured=None, bin_seconds=1)
    assert [list(s[0]) for s in segs] == [[0, 1, 2, 3], [1000, 1001, 1002]]


def test_measured_silence_is_zero_and_keeps_the_line():
    ts = _ts(0, 1, 2, 3, 1000, 1001, 1002)
    segs = GraphRenderer._net_segments(ts, np.ones(7), np.ones(7), measured=[(-50, 5000)], bin_seconds=1)
    assert len(segs) == 1
    t, up, down = segs[0]
    assert list(t) == [0, 1, 2, 3, 4, 999, 1000, 1001, 1002]
    assert list(down[4:6]) == [0.0, 0.0] and list(up[4:6]) == [0.0, 0.0]


def test_partly_measured_silence_is_zero_then_a_gap():
    """The app closed at t=500 and a new session began at t=900: zero until 500, gap, zero from 900."""
    ts = _ts(0, 1, 2, 3, 1000, 1001)
    segs = GraphRenderer._net_segments(ts, np.ones(6), np.ones(6), measured=[(-5, 500), (900, 2000)],
                                       bin_seconds=1)
    assert [list(s[0]) for s in segs] == [[0, 1, 2, 3, 4, 500], [900, 999, 1000, 1001]]
    assert segs[0][2][-1] == 0.0 and segs[1][2][0] == 0.0


def test_window_edges_are_zero_only_while_the_app_ran():
    """Leading / trailing silences: drawn from the session start and to the session end, nothing outside."""
    ts = _ts(1000, 1001, 1002)
    segs = GraphRenderer._net_segments(ts, np.ones(3), np.ones(3), measured=[(600, 1500)],
                                       start_ts=0, end_ts=3000, bin_seconds=1)
    assert [list(s[0]) for s in segs] == [[600, 999, 1000, 1001, 1002, 1003, 1500]]
    # unmeasured edges are simply not drawn (the old reader padding put zero rows there)
    segs = GraphRenderer._net_segments(ts, np.ones(3), np.ones(3), measured=(), start_ts=0, end_ts=3000,
                                       bin_seconds=1)
    assert [list(s[0]) for s in segs] == [[1000, 1001, 1002]]


def test_no_zero_line_is_drawn_across_unmeasured_time(renderer):
    """The old renderer drew a dashed zero bridge across every gap (and on to "now"). Nothing may
    plot a point inside an unmeasured gap any more, labelled or not."""
    a = [T0 + timedelta(seconds=i) for i in range(30)]
    b = [T0 + timedelta(minutes=20, seconds=i) for i in range(30)]
    end = T0 + timedelta(minutes=60)
    renderer._plot_high_res(np.array(a + b), np.ones(60), np.full(60, 10.0), target_end_time=end,
                            measured=None, start_time=T0 - timedelta(minutes=10), bin_seconds=1)
    gap_lo, gap_hi = mdates.date2num(a[-1]), mdates.date2num(b[0])
    for ax in (renderer.ax_download, renderer.ax_upload):
        for line in ax.get_lines():
            xs = np.asarray(line.get_xdata(orig=False), dtype=float)
            assert not ((xs > gap_lo + 1e-9) & (xs < gap_hi - 1e-9)).any()
            assert xs.max() <= mdates.date2num(b[-1]) + 1e-9          # no trailing bridge to "now"
            assert not str(line.get_label()).startswith("_")            # no unlabelled helper lines


def test_a_lone_sample_is_drawn_as_a_point(renderer):
    renderer._plot_high_res(np.array([T0]), np.array([1.0]), np.array([5.0]))
    line = renderer.ax_download.get_lines()[0]
    assert line.get_marker() == "o"


def test_a_coarser_resolution_than_the_rows_never_misplaces_a_zero_run():
    """The resolution is read just after the rows; a roll-up in between could name 'day' for raw rows.
    The zero run still starts within the data's own gap threshold of its neighbour."""
    ts = _ts(0, 1, 2, 3, 1000, 1001)
    segs = GraphRenderer._net_segments(ts, np.ones(6), np.ones(6), measured=[(0, 2000)], bin_seconds=86400)
    assert len(segs) == 1
    t = list(segs[0][0])
    assert t[:4] == [0, 1, 2, 3] and 3 < t[4] <= 13 and 988 <= t[5] < 1000 and t[-2:] == [1000, 1001]


def test_an_empty_window_keeps_its_time_range_and_units(agg_renderer):
    """A fresh install / a range before history / the first hour after boot: the 'no traffic' note
    sits on the real frame (requested time range, time ticks, unit labels), not a bare 0-1 grid."""
    r = agg_renderer
    start, end = T0, T0 + timedelta(hours=1)
    r.render([], start, end, "TIMELINE_1_HOUR", stat_type="network",
             hw_styles={"unit_type": "bytes_decimal", "earliest": None}, force_rebuild=True)
    for ax in (r.ax_download, r.ax_upload):
        lo, hi = ax.get_xlim()
        assert (lo, hi) == pytest.approx((mdates.date2num(start), mdates.date2num(end)))
        assert "(" in ax.get_ylabel() and ax.get_ylabel().endswith(")")
    assert r.ax_download.get_ylabel() == f"{EN.DOWNLOAD_LABEL} ({EN.KBPS_LABEL})"   # 0.1 Mbps floor = 12.5 KB/s
    r.figure.canvas.draw()
    assert [t.get_text() for t in r.ax_upload.get_xticklabels() if t.get_text()], "time axis labelled"
    assert any(t.get_text() == EN.NET_GRAPH_NO_HISTORY_YET for t in r.ax_download.texts)
    # Data arriving afterwards draws normally (the cleared lines are not reused).
    r.render(_net_rows([10.0] * 30, step_s=60), start, end, "TIMELINE_1_HOUR", stat_type="network",
             hw_styles={"unit_type": "bytes_decimal"})
    assert any(line.axes is r.ax_download and len(line.get_xdata()) >= 30 for line in r.ax_download.get_lines())


@pytest.mark.parametrize("unit_type", ["bits_decimal", "bits_binary", "bytes_decimal", "bytes_binary"])
@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("top", [0.1, 0.2, 0.8, 3.0, 150.0])
def test_every_tick_label_is_its_tick(unit_type, force, top):
    """"Always Mbps" + bytes at the idle 0.1 floor put ticks on 0.0025 MB/s and printed "0.003"."""
    from netspeedtray.views.graph.value_axis import ScaledMaxNLocator, ValueAxisFormatter
    g = constants.graph
    scale, _ = speed_axis_unit(top, EN, unit_type, force)
    ticks = ScaledMaxNLocator(scale, nbins=g.VALUE_AXIS_LOCATOR_BINS, steps=list(g.VALUE_AXIS_LOCATOR_STEPS),
                              min_n_ticks=3).tick_values(0, top)
    labels = ValueAxisFormatter(EN, scale=scale).format_ticks(list(ticks))
    assert len(ticks) >= 3
    for tick, label in zip(ticks, labels):
        assert float(label) == pytest.approx(tick * scale, abs=1e-12), (labels, list(ticks * scale))


def test_thinning_keeps_every_spike_as_a_stored_row():
    """4 h of per-second rows thinned for drawing: a stride sample halved a 45 Mbps spike to 22 and
    lost a 3.7 Mbps upload spike. Per-bucket peaks keep both, as the rows they were stored as."""
    from netspeedtray.views.graph.worker import GraphDataWorker
    rng = np.random.default_rng(7)
    n = 14_400
    data = [(1_800_000_000.0 + i, float(rng.uniform(0, 20_000)), float(rng.uniform(0, 100_000)))
            for i in range(n)]
    data[5003] = (data[5003][0], data[5003][1], _bps(45.0))      # download spike
    data[5004] = (data[5004][0], data[5004][1], _bps(22.0))      # its shoulder
    data[9001] = (data[9001][0], _bps(3.7), data[9001][2])       # upload spike
    for i in range(0, 14_000, 700):                              # smaller spikes all over
        data[i + 350] = (data[i + 350][0], data[i + 350][1], _bps(10.0))
    out = GraphDataWorker._thin_keep_peaks(data, GraphDataWorker.MAX_DATA_POINTS)
    assert len(out) <= GraphDataWorker.MAX_DATA_POINTS
    assert data[5003] in out and data[9001] in out
    assert all(data[i + 350] in out for i in range(0, 14_000, 700))
    assert set(out) <= set(data) and out == sorted(out)
    assert GraphDataWorker._thin_keep_peaks(data[:100], 2000) == data[:100]


def test_subtract_spans():
    from netspeedtray.core.db.history_reader import subtract_spans
    assert subtract_spans(((0, 100),), [(10, 20), (50, 60)]) == ((0, 10), (20, 50), (60, 100))
    assert subtract_spans(((0, 100), (200, 300)), [(90, 210)]) == ((0, 90), (210, 300))
    assert subtract_spans(((0, 100),), [(-5, 200)]) == ()
    assert subtract_spans(((0, 100),), []) == ((0, 100),)


# --- reader (v8 file) -------------------------------------------------------------------------------

NOW = F.fixed_now()
NOW_TS = int(NOW.timestamp())


def _v8(tmp_path, sessions=(), raw=(), minute=()):
    """sessions: (start, last_seen, ended, clean); raw: (ts, up, down, dt_ms, flags);
    minute: (ts, up_max, down_max, flags)."""
    w = F.V8Writer(tmp_path / "v8.db")
    w.conn.executemany(f"INSERT INTO {S8.APP_SESSIONS} (started_ts, last_seen_ts, ended_ts, version, "
                       "poll_ms, clean_exit) VALUES (?, ?, ?, '2.2.0', 1000, ?)", list(sessions))
    iid = w.iface("Ethernet")
    w.conn.executemany(f"INSERT INTO {S8.NET_RAW} (ts, iface_id, up_bytes, down_bytes, dt_ms, flags) "
                       "VALUES (?, ?, ?, ?, ?, ?)", [(t, iid, u, d, dt, fl) for t, u, d, dt, fl in raw])
    w.conn.executemany(f"INSERT INTO {S8.NET_MINUTE} (ts, iface_id, up_avg, down_avg, up_max, down_max, "
                       "sample_count, up_bytes, down_bytes, covered_ms, flags) "
                       "VALUES (?, ?, ?, ?, ?, ?, 60, ?, ?, 60000, ?)",
                       [(t, iid, u, d, u, d, u * 60, d * 60, fl) for t, u, d, fl in minute])
    w.watermarks(raw=NOW_TS - 86400, minute=NOW_TS - 30 * 86400)
    w.close()
    _, r8, conns = F.readers(tmp_path / "v8.db", tmp_path / "v8.db")
    return r8, conns


def _close(conns):
    for c in conns:
        c.close()


def test_reader_no_longer_pads_windows_with_zero_rows(tmp_path):
    t = NOW_TS - 3000
    r8, conns = _v8(tmp_path, raw=[(t, 1000, 5000, 1000, 0)])
    try:
        assert r8.speed_history(NOW - timedelta(hours=2), NOW, None, True, "auto") == [(t, 1000.0, 5000.0)]
        empty = r8.speed_history(NOW - timedelta(hours=6), NOW - timedelta(hours=5), None, True, "auto")
        assert empty == []       # was a flat baseline of 10+ synthetic zero rows
    finally:
        _close(conns)


def test_v7_reader_no_longer_pads_either(tmp_path):
    from netspeedtray.tests.legacy_v7_worker import LegacyV7Worker
    w = LegacyV7Worker(tmp_path / "v7.db")
    w._initialize_connection()
    w._check_and_create_schema()
    w.conn.commit()
    w.conn.close()
    r7, _r8, conns = F.readers(tmp_path / "v7.db", tmp_path / "v7.db")
    try:
        assert r7.speed_history(NOW - timedelta(hours=6), NOW, None, True, "auto") == []
        assert r7.measured_spans(NOW - timedelta(hours=6), NOW) is None      # v7 records no sessions
        assert r7.speed_resolution(NOW - timedelta(hours=24), NOW, None) == "minute"
    finally:
        conns[0].close()


def test_measured_spans_are_sessions_minus_recorded_sleep(tmp_path):
    T = NOW_TS - 10000
    wake = T + 5000
    r8, conns = _v8(tmp_path,
                    sessions=[(T, T + 9000, T + 9000, 1), (T + 9500, NOW_TS - 5, None, 0)],
                    raw=[(T + 100, 10, 10, 1000, 0),
                         (wake, 0, 0, 3_000_000, S8.FLAG_GAP)])     # the poll that ended 3000 s of standby
    try:
        spans = r8.measured_spans(datetime.fromtimestamp(T - 500), NOW)
        assert spans == ((T, T + 2000), (wake, T + 9000), (T + 9500, NOW_TS - 5))
    finally:
        _close(conns)


def test_measured_spans_cut_a_flagged_rollup_bucket_back_to_the_previous_one(tmp_path):
    T = (NOW_TS - 5 * 86400) // 60 * 60
    r8, conns = _v8(tmp_path, sessions=[(T - 600, T + 20000, T + 20000, 1)],
                    minute=[(T, 100, 100, 0), (T + 9000, 100, 100, S8.FLAG_GAP)])
    try:
        spans = r8.measured_spans(datetime.fromtimestamp(T - 3600), datetime.fromtimestamp(T + 30000))
        assert spans == ((T - 600, T + 60), (T + 9000, T + 20000))
    finally:
        _close(conns)


def test_measured_spans_empty_before_any_session(tmp_path):
    r8, conns = _v8(tmp_path)
    try:
        assert r8.measured_spans(NOW - timedelta(days=1), NOW) == ()
        assert r8.speed_resolution(NOW - timedelta(days=1), NOW, None) == "minute"
        assert r8.speed_resolution(NOW - timedelta(hours=1), NOW, None) == "raw"
        assert r8.speed_resolution(NOW - timedelta(days=7), NOW, None) == "hour"
    finally:
        _close(conns)


# --- worker + host ----------------------------------------------------------------------------------

def test_measured_spans_read_each_tier_only_inside_its_watermarks(tmp_path):
    """A flagged raw row below the raw watermark was already rolled up (a stray): it is not read again."""
    T = NOW_TS - 3 * 86400
    r8, conns = _v8(tmp_path, sessions=[(T, NOW_TS - 5, None, 0)],
                    raw=[(T + 5000, 0, 0, 3_000_000, S8.FLAG_GAP)])     # older than the raw watermark
    try:
        assert r8.measured_spans(datetime.fromtimestamp(T), NOW) == ((T, NOW_TS - 5),)
    finally:
        _close(conns)


def _db_request(seq=1):
    from netspeedtray.views.graph.request import DataRequest
    return DataRequest(start_time=NOW - timedelta(hours=24), end_time=NOW, interface_name=None,
                       is_session_view=False, sequence_id=seq, stat_type="network")


def test_worker_sends_coverage_ahead_of_the_rows_and_keeps_a_lone_row(q_app):
    from netspeedtray.views.graph.worker import GraphDataWorker
    ws = MagicMock()
    ws.get_speed_history.return_value = [(NOW_TS - 600, 10.0, 20.0)]           # ONE stored row
    ws.get_total_bandwidth_for_period.return_value = (1.0, 2.0)
    ws.get_history_volume_for_period.side_effect = Exception("no provenance")
    cov = {"resolution": "minute", "measured": ((NOW_TS - 3600, NOW_TS),)}
    ws.get_graph_coverage.return_value = cov
    w = GraphDataWorker(ws)
    events = []
    w.graph_coverage.connect(lambda seq, c: events.append(("coverage", seq, c)))
    w.data_ready.connect(lambda data, up, down, seq: events.append(("data", seq, list(data))))
    w.process_data(_db_request(7))
    assert events[0] == ("coverage", 7, cov)
    assert events[1] == ("data", 7, [(NOW_TS - 600, 10.0, 20.0)])


def test_session_memory_path_claims_no_coverage(q_app):
    from netspeedtray.views.graph.request import DataRequest
    from netspeedtray.views.graph.worker import GraphDataWorker
    ws = MagicMock()
    ws.get_aggregated_speed_history.return_value = []
    w = GraphDataWorker(ws)
    seen = []
    w.graph_coverage.connect(lambda *a: seen.append(a))
    w.process_data(DataRequest(start_time=NOW - timedelta(hours=1), end_time=NOW, interface_name=None,
                               is_session_view=True, sequence_id=1, stat_type="network"))
    assert seen == [] and not ws.get_graph_coverage.called


def test_host_hands_units_and_coverage_to_the_renderer():
    from netspeedtray.views.monitor.graph_host import GraphHost
    host = SimpleNamespace(config={"unit_type": "bytes_binary", "speed_display_mode": "always_mbps"},
                           _graph_coverage={}, _hw_styles=lambda: {"earliest": None})
    GraphHost._on_graph_coverage(host, 4, {"resolution": "hour", "measured": ((1, 2),)})
    styles = GraphHost._net_styles(host, 4)
    assert styles["unit_type"] == "bytes_binary" and styles["force_mega"] is True
    assert styles["resolution"] == "hour" and styles["measured"] == ((1, 2),)
    assert "earliest" in styles and host._graph_coverage == {}          # consumed once
    # A reply without coverage (the in-memory Session) claims nothing.
    styles = GraphHost._net_styles(host, 5)
    assert styles["resolution"] is None and styles["measured"] is None
