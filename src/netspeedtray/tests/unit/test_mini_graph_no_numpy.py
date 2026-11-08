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
- rendering every graph style (and the hardware / split / sparse variants) leaves 'numpy' out of
  sys.modules, in a clean interpreter.
"""
import math
import os
import random
import subprocess
import sys
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
# 1 device pixel at 200% scaling is 0.5 logical px; stay well inside it.
MAX_PX_ERROR = 0.25


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
    sit on a smoothed peak (parabola), in the "now" blend zone (quadratic long window there), or on the
    edge where a big sample crosses the kernel's cut (a jump of e^-2 * y / s0) - all within 0.5%."""
    rng = random.Random(hash(kind) % 1000)
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
    (a jump of e^-2 * y / s0), between two drawn points. Without the jump check they are 1.0-1.3% low."""
    n = (1800, 3600, 5000)[seed % 3]
    rng = random.Random(seed)
    y = [abs(rng.gauss(2e5, 5e4)) for _ in range(n)]
    for _ in range(rng.randint(2, 6)):
        y[rng.randrange(n)] += rng.uniform(1e6, 5e7)
    assert abs(_auto_fit_error(y, n // (2 * 116))) <= 0.002


@pytest.mark.parametrize("n,stride", [(1800, 7), (1800, 16), (5000, 21), (5000, 44)])
def test_auto_fit_sees_a_peak_at_the_start_of_the_now_zone(n, stride):
    """A lone spike whose smoothed peak lands just before or inside the blend zone (the newest
    window // 2 samples): the parabola reaches across the zone's start and the zone's long window is
    quadratic, not linear (linear was up to 0.6% low, the unfitted boundary 1.4%)."""
    w = trend_window(n)
    for back in range(w // 2 - 20, w // 2 + 20):
        y = [2e5] * n
        y[n - 1 - back] = 1e7
        assert abs(_auto_fit_error(y, stride)) <= 0.004, back


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
print("NUMPY_LOADED" if "numpy" in sys.modules else "NUMPY_ABSENT")
"""


def test_drawing_every_mini_graph_style_never_imports_numpy(tmp_path):
    env = dict(os.environ, PYTHONPATH=str(SRC), QT_QPA_PLATFORM="offscreen",
               APPDATA=str(tmp_path / "Roaming"), LOCALAPPDATA=str(tmp_path / "Local"))
    proc = subprocess.run([sys.executable, "-c", _CHILD], env=env, timeout=300, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[-3000:]
    assert proc.stdout.strip().splitlines()[-1] == "NUMPY_ABSENT", proc.stdout + proc.stderr[-2000:]
