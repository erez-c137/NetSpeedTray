"""
The widget's mini graph draws without numpy (memory plan 2.4: numpy cost the widget +9 MB USS and
+40 MB commit at first paint, only for the Trend smoothing and the spline).

Pins, against numpy reference implementations kept HERE (main's code, verbatim math):
- trend_smooth (pure Python) equals main's numpy smoother to ~1e-15 on random, constant, spiky,
  stepped, short (len < window), NaN and None series, for any window / edge window;
- calculate_monotone_cubic_interpolation is bit-identical to main's numpy version;
- trend_smooth_sampled (the long-history fast path) draws the same indices as before and stays within
  a small fraction of a pixel of the full smoothing once mapped onto the widget's graph rect, auto-fit
  included;
- rendering every graph style (and the hardware / split / sparse variants) and the hover card's battery
  average (summarize_hardware -> utils.summaries) leave 'numpy' out of sys.modules, in a clean
  interpreter.
"""
import math
import os
import random
import subprocess
import sys
import zlib
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

import netspeedtray
from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.core.widget_state import AggregatedSpeedData
from netspeedtray.tests.color_helpers import themed
from netspeedtray.utils.helpers import calculate_monotone_cubic_interpolation
from netspeedtray.utils.widget_renderer import (RenderConfig, WidgetRenderer, trend_scale_values, trend_smooth,
                                                trend_smooth_sampled, trend_window)

R = constants.renderer
SRC = Path(netspeedtray.__file__).resolve().parents[1]


# --- numpy references: main's code (2.2.0-rc.6), kept in the test only -----------------------------------

def _np_gaussian_local_linear(y, window):
    n = len(y)
    half = min(int(window) // 2, (n - 1) // 2)
    if half < 1:
        return y.copy()
    d = np.arange(-half, half + 1, dtype=float)
    sigma = max(half / 2.0, 0.5)
    g = np.exp(-0.5 * (d / sigma) ** 2)
    ones = np.ones(n)

    def corr(a, k):
        return np.convolve(a, k[::-1], mode="same")

    s0, s1 = corr(ones, g), corr(ones, g * d)
    s2 = corr(ones, g * d * d) + R.TREND_EDGE_SLOPE_DAMPING * float((g * d * d).sum())
    t0, t1 = corr(y, g), corr(y, g * d)
    return (s2 * t0 - s1 * t1) / (s0 * s2 - s1 * s1)


def np_trend_smooth(values, window, edge_window=None):
    y = np.asarray(values, dtype=float)
    n = len(y)
    out = _np_gaussian_local_linear(y, window)
    edge = R.TREND_EDGE_WINDOW if edge_window is None else int(edge_window)
    blend = min(int(window) // 2, n - 1)
    if edge < window and blend > 0:
        short = _np_gaussian_local_linear(y, edge)
        k = np.arange(n - 1, -1, -1, dtype=float)
        w = np.clip(1.0 - k / blend, 0.0, 1.0)
        out = (1.0 - w) * out + w * short
    return out.tolist()


def np_monotone_cubic(x_coords, y_coords, density=10):
    x = np.array(x_coords, dtype=float)
    y = np.array(y_coords, dtype=float)
    n = len(x)
    if n < 2:
        return list(x), list(y)
    dx = np.diff(x)
    dy = np.diff(y)
    dx[dx == 0] = 1e-9
    secants = dy / dx
    tangents = np.zeros(n)
    for i in range(1, n - 1):
        m_prev = secants[i - 1]
        m_next = secants[i]
        if m_prev * m_next <= 0:
            tangents[i] = 0.0
        else:
            tangents[i] = (3 * m_prev * m_next) / (max(m_next, m_prev) + 2 * min(m_next, m_prev))
    tangents[0] = secants[0]
    tangents[-1] = secants[-1]
    t = np.linspace(0, 1, density + 1)[:-1]
    t2 = t * t
    t3 = t * t * t
    h00 = 2 * t3 - 3 * t2 + 1
    h10 = t3 - 2 * t2 + t
    h01 = -2 * t3 + 3 * t2
    h11 = t3 - t2
    x_start, x_end = x[:-1, np.newaxis], x[1:, np.newaxis]
    y_start, y_end = y[:-1, np.newaxis], y[1:, np.newaxis]
    m_start, m_end = tangents[:-1, np.newaxis], tangents[1:, np.newaxis]
    seg_dx = x_end - x_start
    seg_y = (y_start * h00) + (seg_dx * m_start * h10) + (y_end * h01) + (seg_dx * m_end * h11)
    seg_x = x_start + t * seg_dx
    interp_x = seg_x.flatten().tolist()
    interp_y = seg_y.flatten().tolist()
    interp_x.append(x[-1])
    interp_y.append(y[-1])
    return interp_x, interp_y


# --- series ---------------------------------------------------------------------------------------------

KINDS = ("noise", "spikes", "steps", "burst", "recent_spike", "zeros", "const")


def _series(kind, n, rng):
    if kind == "noise":
        return [abs(rng.gauss(1e6, 4e5)) for _ in range(n)]
    if kind == "spikes":
        y = [abs(rng.gauss(2e5, 5e4)) for _ in range(n)]
        for _ in range(rng.randint(1, 6)):
            y[rng.randrange(n)] += rng.uniform(1e6, 5e7)
        return y
    if kind == "steps":
        y, v = [], 0.0
        for _ in range(n):
            if rng.random() < 0.01:
                v = rng.choice([0.0, rng.uniform(1e5, 1e8)])
            y.append(v + abs(rng.gauss(0, 1e4)))
        return y
    if kind == "burst":
        y = [abs(rng.gauss(1e5, 1e4)) for _ in range(n)]
        a, length = rng.randrange(n), rng.randint(1, max(2, n // 10))
        for i in range(a, min(n, a + length)):
            y[i] += 2e7
        return y
    if kind == "recent_spike":
        y = [abs(rng.gauss(2e5, 5e4)) for _ in range(n)]
        y[n - 1 - rng.randrange(min(n, 200))] += rng.uniform(1e6, 5e7)
        return y
    if kind == "zeros":
        return [0.0] * n
    return [3.5e5] * n


def _assert_close(got, ref):
    assert len(got) == len(ref)
    scale = max([1.0] + [abs(v) for v in ref if math.isfinite(v)])
    for a, b in zip(got, ref):
        if math.isnan(b):
            assert math.isnan(a)
        else:
            assert abs(a - b) <= 1e-9 * scale, (a, b)


# --- trend_smooth == main's numpy smoother --------------------------------------------------------------

@pytest.mark.parametrize("seed", range(6))
def test_trend_smooth_matches_numpy_on_random_series(seed):
    rng = random.Random(seed)
    for _ in range(60):
        n = rng.choice([3, 4, 5, 7, 10, 11, 12, 20, 50, 179, 180, 181, 300, 601])
        y = _series(rng.choice(KINDS), n, rng)
        window = rng.choice([trend_window(n), 3, 4, 11, 12, 40, 200])
        edge = rng.choice([None, 1, 5, 11, 30])
        _assert_close(trend_smooth(y, window, edge), np_trend_smooth(y, window, edge))


@pytest.mark.parametrize("n", [0, 1, 2, 3, 4, 10])
@pytest.mark.parametrize("window", [3, 11, 50])
def test_trend_smooth_short_series_match_numpy(n, window):
    """Shorter than the window (the kernel shrinks to fit) and the degenerate lengths."""
    y = [float(i * i % 7) for i in range(n)]
    _assert_close(trend_smooth(y, window), np_trend_smooth(y, window))


def test_trend_smooth_constant_and_spike_match_numpy():
    _assert_close(trend_smooth([5.0] * 300, 18), np_trend_smooth([5.0] * 300, 18))
    spike = [0.0] * 300
    spike[150] = 1e9
    _assert_close(trend_smooth(spike, 18), np_trend_smooth(spike, 18))
    spike[-1] = 1e9   # a spike at "now"
    _assert_close(trend_smooth(spike, 18), np_trend_smooth(spike, 18))


def test_trend_smooth_nan_and_none_match_numpy():
    """numpy read None as NaN and let a NaN spread over its kernel; the pure version does the same."""
    y = [1.0 + (i % 5) for i in range(200)]
    y[50] = math.nan
    _assert_close(trend_smooth(y, 12), np_trend_smooth(y, 12))
    y[50], y[120] = None, None
    _assert_close(trend_smooth(y, 12), np_trend_smooth(y, 12))
    _assert_close(trend_smooth([1.0, None], 11), np_trend_smooth([1.0, None], 11))


def test_trend_smooth_accepts_any_iterable_of_numbers():
    ref = np_trend_smooth([1, 2, 3, 10, 2, 1, 0], 4)
    _assert_close(trend_smooth((1, 2, 3, 10, 2, 1, 0), 4), ref)
    _assert_close(trend_smooth(iter([1, 2, 3, 10, 2, 1, 0]), 4), ref)


# --- the spline is bit-identical ------------------------------------------------------------------------

@pytest.mark.parametrize("seed", range(4))
def test_monotone_cubic_is_bit_identical_to_numpy(seed):
    rng = random.Random(seed)
    for _ in range(40):
        n = rng.choice([0, 1, 2, 3, 5, 40, 181])
        x = sorted(rng.uniform(0, 500) for _ in range(n))
        if n > 3 and rng.random() < 0.3:
            x[2] = x[1]                       # a zero step (the 1e-9 guard)
        y = [rng.choice([0.0, rng.uniform(-5, 1e7)]) for _ in range(n)]
        density = rng.choice([1, 2, 5, 10])
        gx, gy = calculate_monotone_cubic_interpolation(x, y, density=density)
        ex, ey = np_monotone_cubic(x, y, density=density)
        assert [float(v) for v in gx] == [float(v) for v in ex]
        assert [float(v) for v in gy] == [float(v) for v in ey]


def test_monotone_cubic_reads_none_as_nan_like_numpy():
    gx, gy = calculate_monotone_cubic_interpolation([0.0, 1.0, 2.0], [1.0, None, 3.0], density=2)
    ex, ey = np_monotone_cubic([0.0, 1.0, 2.0], [1.0, None, 3.0], density=2)
    assert gx == ex and len(gy) == len(ey)
    assert all((math.isnan(a) and math.isnan(b)) or a == b for a, b in zip(gy, ey))


# --- the long-history fast path: same points, within a fraction of a pixel ------------------------------

def _px(values, scale_lists, graph_h):
    """The trend paint path's y mapping (auto-fit, no outlier clip, capped at the top)."""
    top = max(max(s) for s in scale_lists if s)
    max_y = max(top * R.GRAPH_Y_AXIS_PADDING_FACTOR, R.MIN_Y_SCALE)
    h = float(max(1, graph_h - 1))
    return [(min(max(0.0, v), max_y) / max_y) * h for v in values]


# Graph rects of the widget's sizes (60x22 .. 120x40 minus the margins) and a wide one.
GRAPH_RECTS = [(56, 18), (80, 26), (116, 36), (200, 36)]
# What the fast path guarantees on the widget's graph rects (worst measured 0.135 px; 1 device pixel at
# 200% scaling is 0.5 logical px).
MAX_PX_ERROR = 0.15


@pytest.mark.parametrize("graph_w,graph_h", GRAPH_RECTS)
def test_trend_sampled_matches_full_within_a_pixel(graph_w, graph_h):
    rng = random.Random(graph_w * 1000 + graph_h)
    worst = 0.0
    for _ in range(18):
        n = min(5000, rng.choice([5, 8, 15, 30, 60, 83, 120]) * 60)
        stride = max(1, n // (2 * graph_w))
        window = trend_window(n)
        ups, downs = (_series(rng.choice(KINDS), n, rng) for _ in range(2))
        full_u, full_d = trend_smooth(ups, window), trend_smooth(downs, window)
        idx = list(range(0, n, stride))
        if idx[-1] != n - 1:
            idx.append(n - 1)
        old = [_px([f[i] for i in idx], [trend_scale_values(full_u), trend_scale_values(full_d)], graph_h)
               for f in (full_u, full_d)]
        (iu, du, su), (idd, dd, sd) = (trend_smooth_sampled(s, window, stride) for s in (ups, downs))
        assert iu == idx and idd == idx
        new = [_px(v, [su, sd], graph_h) for v in (du, dd)]
        worst = max([worst] + [abs(a - b) for o, nw in zip(old, new) for a, b in zip(o, nw)])
    assert worst <= MAX_PX_ERROR, worst


@pytest.mark.parametrize("kind", ["spikes", "recent_spike", "steps", "burst"])
def test_trend_sampled_auto_fit_finds_the_max_between_drawn_points(kind):
    """The axis is sized to the max over EVERY smoothed sample. Between two drawn points that max can
    sit on a smoothed peak, in the "now" blend zone, or on the edge where a big sample crosses the
    kernel's cut (a jump of e^-2 * y / s0) - all within 0.5%."""
    # zlib.crc32, not hash(): str hashes are salted per process (PYTHONHASHSEED), so hash(kind) drew
    # different series on every run and ~3 in 1,000 runs hit a case over the 0.5% bound.
    rng = random.Random(zlib.crc32(kind.encode()) % 1000)
    for _ in range(25):
        n = rng.choice([1800, 3600, 5000])
        stride = n // (2 * 116)
        y = _series(kind, n, rng)
        window = trend_window(n)
        want = max(trend_scale_values(trend_smooth(y, window)))
        got = max(trend_smooth_sampled(y, window, stride)[2])
        assert abs(got - want) <= 0.005 * want, (n, (got - want) / want)


def _auto_fit_error(y, stride):
    window = trend_window(len(y))
    want = max(trend_scale_values(trend_smooth(y, window)))
    return (max(trend_smooth_sampled(y, window, stride)[2]) - want) / want


@pytest.mark.parametrize("seed", [4, 31, 171])
def test_auto_fit_sees_a_spike_crossing_the_kernel_cut(seed):
    """Found by search: series whose smoothed max sits where a big sample enters / leaves the kernel
    (a jump of e^-2 * y / s0), between two drawn points. A parabola through the smoothing itself read
    them 1.0-1.3% low; the search splits off the cut's step (the box sum) and computes the top exactly."""
    n = (1800, 3600, 5000)[seed % 3]
    rng = random.Random(seed)
    y = [abs(rng.gauss(2e5, 5e4)) for _ in range(n)]
    for _ in range(rng.randint(2, 6)):
        y[rng.randrange(n)] += rng.uniform(1e6, 5e7)
    assert abs(_auto_fit_error(y, n // (2 * 116))) <= 0.002


@pytest.mark.parametrize("n,stride", [(1800, 7), (1800, 16), (5000, 21), (5000, 44)])
def test_auto_fit_sees_a_peak_at_the_start_of_the_now_zone(n, stride):
    """A lone spike whose smoothed peak lands just before or inside the blend zone (the newest
    window // 2 samples): the search reaches across the zone's start, and the zone's peaks are
    recomputed exactly (its interpolated long window was up to 0.6% low, the unsearched boundary 1.4%)."""
    w = trend_window(n)
    for back in range(w // 2 - 20, w // 2 + 20):
        y = [2e5] * n
        y[n - 1 - back] = 1e7
        assert abs(_auto_fit_error(y, stride)) <= 0.004, back


# Every position of one big spike, both edges included: the oldest edge's local-linear fit (a spike
# half a window in leaves its peak between lattice points 0 and 1), the interior, the "now" zone.
SWEEPS = [(600, 56), (1800, 56), (1800, 116), (3600, 56), (5000, 56), (5000, 116)]


@pytest.mark.parametrize("n,graph_w", SWEEPS)
def test_a_single_spike_anywhere_draws_within_0_08_px(n, graph_w):
    stride = n // (2 * graph_w)
    window = trend_window(n)
    worst = 0.0
    for pos in range(0, n, 1 if n <= 1800 else 2):
        y = [0.0] * n
        y[pos] = 1e8
        ref = np_trend_smooth(y, window)
        top = max(trend_scale_values(ref))
        idx, drawn, scale = trend_smooth_sampled(y, window, stride)
        worst = max(worst, abs(max(scale) - top) / top,
                    max(abs(v - ref[i]) for i, v in zip(idx, drawn)) / top)
    assert worst * 35 <= 0.08, (worst * 35, "px of a 36 px graph")   # measured worst 0.068 (56 px wide)


def test_the_oldest_edge_spike_from_the_review_is_fitted():
    """Reviewer's cases (#488): a spike ~half a window from the oldest sample put the max between
    lattice points 0 and 1; the parabola never fitted m = 0, so the axis was 2.6% low (0.91 px)."""
    for n, stride, pos in [(3600, 32, 39), (5000, 44, 54), (1800, 16, 19)]:
        y = [0.0] * n
        y[pos] = 1e8
        assert abs(_auto_fit_error(y, stride)) <= 0.001, (n, stride, pos)


@pytest.mark.parametrize("n", list(range(20, 41)) + [706, 800, 1000])
def test_any_stride_stays_within_a_quarter_pixel(n):
    """The public contract, beyond the widget's sizes: strides up to a quarter of the series, short
    windows (3) and the default. Past 2 * stride > half the lattice could skip a sample's whole bump
    (n = 20-40, stride 5, window 3 read an axis of 0.0 for a 7.9e7 spike), so that falls back to the
    full computation; below it the binned fit stays within 0.25 px even at a ~30 px graph."""
    rng = random.Random(n)
    for stride in range(2, n // 4 + 1):
        for window in (3, trend_window(n)):
            for _ in range(6):
                y = [0.0] * n
                y[rng.randrange(n)] = 7.9e7
                ref = np_trend_smooth(y, window)
                top = max(trend_scale_values(ref))
                idx, drawn, scale = trend_smooth_sampled(y, window, stride)
                assert idx == list(range(0, n, stride)) + ([n - 1] if (n - 1) % stride else [])
                assert abs(max(scale) - top) / top * 35 <= 0.25
                assert max(abs(v - ref[i]) for i, v in zip(idx, drawn)) / top * 35 <= 0.25


@pytest.mark.parametrize("stride", [2, 5, 7])
def test_trend_sampled_small_strides_draw_exact_values(stride):
    """Below _TREND_MIN_BIN the lattice sums the raw kernel: the drawn values ARE the full smoothing's."""
    rng = random.Random(stride)
    y = _series("spikes", 900, rng)
    window = trend_window(900)
    full = trend_smooth(y, window)
    idx, drawn, _scale = trend_smooth_sampled(y, window, stride)
    _assert_close(drawn, [full[i] for i in idx])


def test_trend_sampled_stride_one_is_the_full_smoothing():
    y = _series("noise", 300, random.Random(1))
    full = trend_smooth(y, trend_window(300))
    idx, drawn, scale = trend_smooth_sampled(y, trend_window(300), 1)
    assert idx == list(range(300)) and drawn == full and scale == trend_scale_values(full)


def test_trend_sampled_is_exact_for_a_constant_series():
    idx, drawn, scale = trend_smooth_sampled([4.0e5] * 5000, trend_window(5000), 21)
    assert all(abs(v - 4.0e5) <= 1e-6 for v in drawn + scale)


MBPS = 125_000.0
T0 = datetime(2026, 1, 1)


def _cfg(**overrides):
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    cfg.update(graph_enabled=True, default_color="#FFFFFF", graph_upload_color=constants.graph.UPLOAD_LINE_COLOR,
               graph_download_color=constants.graph.DOWNLOAD_LINE_COLOR, history_minutes=120)
    cfg.update(overrides)
    return themed(cfg)


@pytest.mark.parametrize("w,h", [(120, 40), (60, 22)])
def test_trend_paint_path_on_a_long_history_matches_the_full_smoothing(q_app, w, h):
    """End to end through the renderer: 2 h of 1 s samples, every cached polyline point within the
    pixel budget of what main's algorithm (full smoothing, then every stride-th sample) plots."""
    from PyQt6.QtGui import QColor, QImage, QPainter
    rng = random.Random(9)
    n = 5000
    hist = [AggregatedSpeedData(upload=abs(rng.gauss(2.0, 1.0)) * MBPS + (40 * MBPS if i == n - 20 else 0),
                                download=(abs(rng.gauss(10.0, 5.0)) + (60.0 if 2000 <= i < 2600 else 0.0)) * MBPS,
                                timestamp=T0 + timedelta(seconds=i)) for i in range(n)]
    r = WidgetRenderer(_cfg(graph_style="trend"), I18nStrings("en_US"))
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    r.draw_mini_graph(p, w, h, r.config, hist)
    p.end()
    _size, _key, up, down, _ceiling = r._graph_cache[(False, "both", "trend")]
    graph_w, graph_h = w - 2 * R.GRAPH_LEFT_PADDING, h - (R.GRAPH_MARGIN + R.GRAPH_BOTTOM_PADDING)
    stride = n // (2 * graph_w)
    assert stride >= 8        # the binned path, not the exact one
    window = trend_window(n)
    full_u = trend_smooth([d.upload for d in hist], window)
    full_d = trend_smooth([d.download for d in hist], window)
    idx = list(range(0, n, stride)) + ([n - 1] if (n - 1) % stride else [])
    base = float(R.GRAPH_MARGIN + graph_h - 1)
    expect = [_px([f[i] for i in idx], [trend_scale_values(full_u), trend_scale_values(full_d)], graph_h)
              for f in (full_u, full_d)]
    for pts, exp in ((up, expect[0]), (down, expect[1])):
        assert len(pts) == len(idx)
        assert max(abs((base - pt.y()) - e) for pt, e in zip(pts, exp)) <= MAX_PX_ERROR


# --- a NaN / inf sample never takes the graph down (#488 regression) ------------------------------------

@pytest.mark.parametrize("y", [
    [1.0, math.nan, 1.0, 1.0],              # the reported case: max(0, nan) + 2 * min(0, nan) == 0
    [math.nan, 1.0, 1.0, 1.0, 2.0],
    [1.0, 2.0, 2.0, math.nan],
    [1.0, 1.0, math.nan, math.nan, 1.0, 1.0],
    [0.0, math.inf, 0.0, 0.0, 1.0],
    [1.0, -math.inf, 1.0, 1.0],
    [5.0, math.inf, math.nan, 5.0, 5.0, 5.0],
])
def test_monotone_cubic_survives_nan_and_inf(y):
    """The numpy spline returned NaN for a tangent beside a NaN sample and kept drawing; the pure one
    divided 0 by 0. A NaN secant now counts as an extremum (a flat tangent), so only the segments
    touching the bad sample are non-finite - the rest of the curve is drawn."""
    x = [float(i) for i in range(len(y))]
    cx, cy = calculate_monotone_cubic_interpolation(x, y, density=5)
    assert len(cx) == len(cy) == (len(y) - 1) * 5 + 1
    bad = {i for i, v in enumerate(y) if not math.isfinite(v)}
    for k, v in enumerate(cy[:-1]):
        seg = k // 5
        if not ({seg - 1, seg, seg + 1, seg + 2} & bad):   # a segment and its tangents' secants
            assert math.isfinite(v), (k, v)


def test_monotone_cubic_finite_input_is_unchanged_by_the_nan_guard():
    """Equal secants, zero secants, sign changes: the flat-tangent rule only adds NaN to it."""
    rng = random.Random(7)
    for _ in range(50):
        n = rng.randint(2, 30)
        y = [rng.choice([0.0, 1.0, rng.uniform(-3, 1e6)]) for _ in range(n)]
        x = [float(i) for i in range(n)]
        gx, gy = calculate_monotone_cubic_interpolation(x, y, density=5)
        ex, ey = np_monotone_cubic(x, y, density=5)
        assert gx == ex and gy == ey


@pytest.mark.parametrize("style", ["detailed", "trend"])
@pytest.mark.parametrize("n", [12, 300])                 # sparse (the spline) and dense
@pytest.mark.parametrize("where", [0, 1, 5, -2, -1])    # the oldest sample, inside, "now"
@pytest.mark.parametrize("bad", [math.nan, math.inf])
def test_a_nan_or_inf_sample_still_draws_the_graph(q_app, caplog, style, n, where, bad):
    """It vanished (ZeroDivisionError caught, an ERROR every paint) until the sample scrolled out. A
    NaN at the oldest edge also made the auto-fit axis NaN (max() keeps a NaN it starts on)."""
    from PyQt6.QtGui import QColor, QImage, QPainter
    hist = [AggregatedSpeedData(upload=1.0 * MBPS + (i % 3) * 1e4, download=4.0 * MBPS + (i % 5) * 1e4,
                                timestamp=T0 + timedelta(seconds=i)) for i in range(n)]
    hist[where] = AggregatedSpeedData(upload=bad, download=bad, timestamp=hist[where].timestamp)
    r = WidgetRenderer(_cfg(graph_style=style), I18nStrings("en_US"))
    img = QImage(120, 40, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    with caplog.at_level("ERROR"):
        r.draw_mini_graph(p, 120, 40, r.config, hist)
    p.end()
    assert not [rec for rec in caplog.records if rec.levelname == "ERROR"]
    _size, _key, up, down, _ceiling = r._graph_cache[(False, "both", style)]
    assert up and down
    for pt in up + down:
        assert math.isfinite(pt.x()) and math.isfinite(pt.y())
        assert R.GRAPH_MARGIN - 1 <= pt.y() <= 40, pt.y()   # inside the graph, not off to -inf


# --- narrow graphs on a long history: the points exactly, the cost of the lattice -----------------------

def _narrow_cases(rng, count):
    """(series, window, stride) with 2 * stride > half: graphs of 6-45 px on 600-7,200 samples."""
    out = []
    while len(out) < count:
        n = rng.choice([600, 1000, 1800, 3600, 5000, 7200])
        graph_w = rng.randint(6, 45)
        stride, window = max(1, n // (2 * graph_w)), trend_window(n)
        if stride > 1 and n >= 4 * stride and 2 * stride > min(window // 2, (n - 1) // 2):
            out.append((_series(rng.choice(KINDS), n, rng), window, stride))
    return out


def _same(a, b):
    return len(a) == len(b) and all(x == z or (math.isnan(x) and math.isnan(z)) for x, z in zip(a, b))


def test_narrow_graph_draws_the_full_smoothings_own_values():
    """2 * stride > half used to run the whole O(n * window) smoothing (30-50 ms a series per sample at
    5,000-7,200 samples). The drawn points are now computed alone - bit-identical to it - and the
    auto-fit comes from the lattice at a stride well inside its pinned range (measured within 1e-4)."""
    rng = random.Random(488)
    worst = 0.0
    for y, window, stride in _narrow_cases(rng, 120):
        n = len(y)
        full = trend_smooth(y, window)
        idx, drawn, scale = trend_smooth_sampled(y, window, stride)
        assert idx == list(range(0, n, stride)) + ([n - 1] if (n - 1) % stride else [])
        assert _same(drawn, [full[i] for i in idx]), (n, stride)
        want = max(trend_scale_values(full))
        if want > 0:
            worst = max(worst, abs(max(scale) - want) / want)
    assert worst <= 5e-4, worst


def test_narrow_graph_nan_series_draws_the_same_points():
    y = _series("noise", 5000, random.Random(3))
    y[100] = y[2600] = math.nan
    y[-3] = None
    window = trend_window(5000)
    full = trend_smooth(y, window)
    idx, drawn, _scale = trend_smooth_sampled(y, window, 5000 // 40)
    assert _same(drawn, [full[i] for i in idx])


def test_narrow_graph_on_a_long_history_stays_cheap():
    """7,200 samples (2 h) on a 20 px graph: was ~45 ms a series; now ~1-2 ms. Generous bound."""
    import time
    n = 7200
    y = _series("noise", n, random.Random(11))
    window, stride = trend_window(n), n // (2 * 20)
    assert 2 * stride > window // 2                  # the narrow path, not the lattice
    best = math.inf
    for _ in range(3):
        t = time.perf_counter()
        trend_smooth_sampled(y, window, stride)
        best = min(best, time.perf_counter() - t)
    assert best < 0.015, f"{best * 1000:.1f} ms"


# --- numpy is never loaded by the widget's graph --------------------------------------------------------

_CHILD = r"""
import os, sys, random
from datetime import datetime, timedelta
from PyQt6.QtGui import QColor, QImage, QPainter, QGuiApplication
app = QGuiApplication([])
from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.core.widget_state import AggregatedSpeedData, HardwareStatSnapshot
from netspeedtray.utils.widget_renderer import WidgetRenderer
import netspeedtray.views.widget.main  # the widget module itself
rng = random.Random(1)
T0 = datetime(2026, 1, 1)
for style in ("trend", "detailed"):
    for n in (5, 40, 180, 1200, 5000):          # sparse (spline), every sample, exact and binned lattice
        hist = [AggregatedSpeedData(upload=rng.uniform(0, 1e6), download=rng.uniform(0, 1e7),
                                    timestamp=T0 + timedelta(seconds=i)) for i in range(n)]
        hw = [HardwareStatSnapshot(value=rng.uniform(0, 100), timestamp=T0 + timedelta(seconds=i)) for i in range(n)]
        for split in (False, True):
            cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
            cfg.update(graph_enabled=True, graph_style=style, history_minutes=120, graph_split_directions=split)
            r = WidgetRenderer(cfg, I18nStrings("en_US"))
            for w, h in ((120, 40), (60, 22)):
                img = QImage(w, h, QImage.Format.Format_ARGB32)
                img.fill(QColor(0, 0, 0, 0))
                p = QPainter(img)
                for series in (("upload", "download") if split else ("both",)):
                    r.draw_mini_graph(p, w, h, r.config, hist, series=series)
                r.draw_mini_graph(p, w, h, r.config, hw, is_hardware=True, hardware_color="#76B900")
                p.end()
            assert r._graph_cache, (style, n)
# The hover card's battery average: NetworkSpeedWidget._hover_battery_today_avg -> WidgetState.summarize_hardware
# -> V8HistoryReader -> utils.summaries, on a real v8 file with today's battery_drain samples.
import logging, sqlite3, tempfile, time
from pathlib import Path
from types import SimpleNamespace
from netspeedtray.core.db.schema_v8 import create_v8_schema
from netspeedtray.core.db.history_reader import V8HistoryReader
from netspeedtray.core.widget_state import WidgetState
from netspeedtray.views.widget.main import NetworkSpeedWidget
path = Path(tempfile.mkdtemp()) / "history.db"
conn = sqlite3.connect(str(path), isolation_level=None)
create_v8_schema(conn)
conn.execute("INSERT INTO metrics (metric_id, name, entity, unit) VALUES (1, 'battery_drain', '', 'watt')")
now = int(time.time())
for k in range(120):
    conn.execute("INSERT INTO hw_raw (ts, metric_id, value) VALUES (?, 1, ?)", (now - 120 + k, 5.0 + (k % 7) * 0.5))
state = SimpleNamespace(db_worker=object(), history_reader=V8HistoryReader(lambda: conn, lambda: path))
widget = SimpleNamespace(
    widget_state=SimpleNamespace(summarize_hardware=lambda *a, **k: WidgetState.summarize_hardware(state, *a, **k)),
    config={"update_rate": 1.0}, logger=logging.getLogger("t"), _hover_battery_avg_cache=None,
    _hover_battery_avg_cache_ts=0.0, _HOVER_USAGE_TTL_SEC=30.0)
avg = NetworkSpeedWidget._hover_battery_today_avg(widget)
assert avg is not None and 5.0 <= avg <= 8.0, avg
print("NUMPY_LOADED" if "numpy" in sys.modules else "NUMPY_ABSENT")
sys.stdout.flush()
os._exit(0)   # skip interpreter teardown (Qt + an open sqlite connection)
"""


def test_the_widget_never_imports_numpy_drawing_graphs_or_the_hover_card(tmp_path):
    env = dict(os.environ, PYTHONPATH=str(SRC), QT_QPA_PLATFORM="offscreen",
               APPDATA=str(tmp_path / "Roaming"), LOCALAPPDATA=str(tmp_path / "Local"))
    proc = subprocess.run([sys.executable, "-c", _CHILD], env=env, timeout=300, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[-3000:]
    assert proc.stdout.strip().splitlines()[-1] == "NUMPY_ABSENT", proc.stdout + proc.stderr[-2000:]
