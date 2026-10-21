"""
Mini graph style: "detailed" (every sample, filled - the classic look) vs "trend" (each series smoothed
by widget_renderer.trend_smooth and drawn as a clean line).

Pins: the config key (default, validation, RenderConfig sanitising), the smoother (calmer than the
raw series, a step still reaches its level, the right edge does not lag a trailing average), the
trend paint path (smoother plotted y, auto-fit sized to the smoothed data, fixed scale untouched),
the polyline cache keyed by style, Detailed's pixels unchanged from before the option existed, and
the Settings control.
"""
import hashlib
import math
import os
import random
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PyQt6.QtCore import QT_VERSION_STR
from PyQt6.QtGui import QColor, QImage, QPainter

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.core.widget_state import AggregatedSpeedData, HardwareStatSnapshot
from netspeedtray.utils.config import ConfigManager
from netspeedtray.utils.widget_renderer import RenderConfig, WidgetRenderer, trend_smooth, trend_window

MBPS = 125_000.0
T0 = datetime(2026, 1, 1)
W, H = 120, 40


def _cfg(**overrides):
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    cfg.update(graph_enabled=True, default_color="#FFFFFF")
    cfg.update(overrides)
    return cfg


def _noisy_history(n=180, seed=3):
    rng = random.Random(seed)
    return [AggregatedSpeedData(upload=abs(rng.gauss(2.0, 1.0)) * MBPS,
                                download=(abs(rng.gauss(10.0, 5.0)) + (60.0 if 80 <= i < 120 else 0.0)) * MBPS,
                                timestamp=T0 + timedelta(seconds=i)) for i in range(n)]


def _draw(renderer, history, w=W, h=H, **kw):
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.draw_mini_graph(p, w, h, renderer.config, history, **kw)
    p.end()
    return img


def _points(renderer, key):
    """The cached (upload, download) polylines of a graph kind, as y arrays."""
    _size, _hash, up, down, _ceiling = renderer._graph_cache[key]
    return np.array([pt.y() for pt in up]), np.array([pt.y() for pt in down])


# --- config ------------------------------------------------------------------------------------------

def test_graph_style_default_and_schema():
    d = constants.config.defaults
    assert d.DEFAULT_GRAPH_STYLE == "trend"   # owner decision after the renders (#395)
    assert d.DEFAULT_CONFIG["graph_style"] == d.DEFAULT_GRAPH_STYLE
    rules = constants.config.defaults.VALIDATION_SCHEMA["graph_style"]
    assert rules["type"] is str and set(rules["choices"]) == {"detailed", "trend"}


@pytest.mark.parametrize("raw,expected", [("trend", "trend"), ("TREND", "trend"), ("detailed", "detailed"),
                                          ("sparkline", "trend"), (3, "trend")])
def test_config_manager_validates_graph_style(tmp_path, raw, expected):
    cm = ConfigManager(tmp_path / "nst.conf")
    with patch.object(cm.logger, "warning"):
        assert cm._validate_config({"graph_style": raw})["graph_style"] == expected


@pytest.mark.parametrize("raw,expected", [(None, "trend"), ("trend", "trend"), ("detailed", "detailed"), ("bogus", "trend")])
def test_render_config_sanitises_graph_style(raw, expected):
    cfg = _cfg()
    if raw is None:
        cfg.pop("graph_style", None)
    else:
        cfg["graph_style"] = raw
    assert RenderConfig.from_dict(cfg).graph_style == expected


# --- the smoother --------------------------------------------------------------------------------------

def test_trend_window_is_a_fraction_of_the_visible_samples_with_a_floor():
    assert trend_window(180) == round(180 * constants.renderer.TREND_WINDOW_FRACTION)
    assert trend_window(1800) > trend_window(180)
    assert trend_window(10) == constants.renderer.TREND_MIN_WINDOW


def test_trend_smooth_calms_noise_and_keeps_length():
    rng = np.random.default_rng(0)
    y = rng.normal(10.0, 3.0, 180)
    s = np.array(trend_smooth(y.tolist(), trend_window(180)))
    assert len(s) == len(y)
    assert np.std(np.diff(s)) < 0.3 * np.std(np.diff(y))   # far less second-to-second jitter
    assert abs(s.mean() - y.mean()) < 0.5                   # same level


def test_trend_smooth_step_reaches_its_new_level_within_the_window():
    n, w = 180, trend_window(180)
    y = [0.0] * 100 + [1.0] * 80
    s = trend_smooth(y, w)
    assert s[100 + w // 2] > 0.95          # interior: there within half a window of the step
    assert max(s) <= 1.0 + 1e-9 and min(s) >= -1e-9   # and no overshoot / undershoot


def test_trend_smooth_right_edge_beats_a_trailing_average():
    """The right edge is "now": a step 2 samples old must already show most of its height - more than
    a trailing mean over the same half-window would - without overshooting."""
    n, w = 180, trend_window(180)
    y = [0.0] * (n - 3) + [1.0] * 3
    last = trend_smooth(y, w)[-1]
    trailing = float(np.mean(y[-(w // 2 + 1):]))
    assert last > 0.8 and last > trailing + 0.2 and last <= 1.0


def test_trend_smooth_short_series_is_safe():
    assert trend_smooth([1.0, 2.0], 11) == [1.0, 2.0]
    assert len(trend_smooth([1.0, 5.0, 2.0], 11)) == 3


# --- the paint path ------------------------------------------------------------------------------------

def test_trend_plots_a_smoother_line_than_detailed(q_app):
    hist = _noisy_history()
    det = WidgetRenderer(_cfg(graph_style="detailed"), I18nStrings("en_US"))
    tr = WidgetRenderer(_cfg(graph_style="trend"), I18nStrings("en_US"))
    _draw(det, hist)
    _draw(tr, hist)
    _, det_down = _points(det, (False, "both", "detailed"))
    _, tr_down = _points(tr, (False, "both", "trend"))
    # Compare at the samples themselves: Detailed's spline has 5 points per sample (its knots are every
    # 5th point); Trend keeps one point per sample here (180 samples over 116 px).
    det_knots = det_down[::5]
    assert len(tr_down) == len(det_knots) == len(hist)
    # Sample-to-sample jitter of the plotted y: the variance of its first difference.
    assert np.var(np.diff(tr_down)) * 10 < np.var(np.diff(det_knots))


def test_trend_auto_fit_uses_the_smoothed_data(q_app):
    """Trend sizes the axis to what it draws: a lone 200 Mbps second smooths to a much lower peak, and
    that peak (not the raw 200) reaches the top of the graph instead of sitting at a fraction of it."""
    hist = [AggregatedSpeedData(upload=1 * MBPS, download=(10.0 + (i % 2)) * MBPS,
                                timestamp=T0 + timedelta(seconds=i)) for i in range(180)]
    hist[90] = AggregatedSpeedData(upload=1 * MBPS, download=200 * MBPS, timestamp=hist[90].timestamp)
    tr = WidgetRenderer(_cfg(graph_style="trend"), I18nStrings("en_US"))
    _draw(tr, hist)
    _, tr_down = _points(tr, (False, "both", "trend"))
    # y grows downward: the trend's highest point reaches near the top of the graph.
    assert tr_down.min() < constants.renderer.GRAPH_MARGIN + 0.25 * H


GRAPH_TOP = constants.renderer.GRAPH_MARGIN
GRAPH_BOTTOM = H - constants.renderer.GRAPH_BOTTOM_PADDING - 1   # QRect.bottom() of the graph rect


def _idle_with(n, burst_at, burst_len, level=50.0, idle=1.15):
    """n samples of idle with a `burst_len`-second burst starting `burst_at` samples before "now"."""
    vals = [idle] * n
    for i in range(n - 1 - burst_at, min(n, n - 1 - burst_at + burst_len)):
        vals[i] = level
    return [AggregatedSpeedData(upload=0.1 * MBPS, download=v * MBPS, timestamp=T0 + timedelta(seconds=i))
            for i, v in enumerate(vals)]


SHAPES = {
    **{f"spike{b}s@{age}": (b, age) for b in (1, 2, 3, 5, 8) for age in (0, 1, 3, 12, 90)},
    "burst40s@60": (40, 60), "step@20": (21, 20), "step@0": (1, 0),
}


@pytest.mark.parametrize("fixed", [False, True], ids=["auto", "fixed"])
@pytest.mark.parametrize("shape", sorted(SHAPES) + ["zero"])
def test_trend_line_never_leaves_the_graph_rect(q_app, shape, fixed):
    """Review finding: the 95th-percentile outlier rule fed with the smoothed series set the axis
    at 1.39 Mbps under a smoothed 50 Mbps spike, and with no cap in auto-fit the line was drawn 200+
    px above the widget. Every Trend point must sit inside the graph rect, auto or fixed scale."""
    if shape == "zero":
        hist = [AggregatedSpeedData(upload=0.0, download=0.0, timestamp=T0 + timedelta(seconds=i))
                for i in range(180)]
    else:
        hist = _idle_with(180, SHAPES[shape][1], SHAPES[shape][0])
    extra = dict(graph_fixed_scale=True, plan_down_mbps=20, plan_up_mbps=5) if fixed else {}
    tr = WidgetRenderer(_cfg(graph_style="trend", **extra), I18nStrings("en_US"))
    _draw(tr, hist)
    up, down = _points(tr, (False, "both", "trend"))
    ys = np.concatenate([up, down])
    assert ys.min() >= GRAPH_TOP - 1e-6 and ys.max() <= GRAPH_BOTTOM + 1e-6


@pytest.mark.parametrize("minutes", [3, 10, 30])
def test_trend_edge_follows_a_drop_at_every_timespan(minutes):
    """Review finding: the window is 6% of the samples on screen, so on 30 min the right edge still
    showed 59% of a finished download 10 s later while the text read 0. The edge window is fixed."""
    n = minutes * 60
    w = trend_window(n)
    for k, most in ((5, 15.0), (10, 1.0)):
        y = [100.0] * (n - k) + [0.0] * k
        assert trend_smooth(y, w)[-1] <= most, (minutes, k)


def test_trend_auto_fit_does_not_chase_a_fresh_spike(q_app):
    """Review finding: a lone 1 s spike is drawn high at the edge and settles lower as it scrolls in;
    sizing the axis to the edge peak re-scaled the whole graph 2.2x over 3 s. Measured by the idle
    baseline's height (far from the spike) as the spike ages 0..9 s: it must stay within 1.4x."""
    heights = []
    for age in range(10):
        tr = WidgetRenderer(_cfg(graph_style="trend"), I18nStrings("en_US"))
        _draw(tr, _idle_with(180, age, 1))
        _, down = _points(tr, (False, "both", "trend"))
        heights.append(GRAPH_BOTTOM - down[20])
    assert max(heights) / min(heights) <= 1.4, heights


def test_trend_fixed_scale_is_unchanged(q_app):
    """With a fixed scale both styles share the plan-speed axis and the plan line row (within the one
    row Trend gives back by mapping max_y onto the graph's top row instead of the row above it)."""
    flat = [AggregatedSpeedData(upload=10 * MBPS, download=50 * MBPS, timestamp=T0 + timedelta(seconds=i))
            for i in range(60)]
    fixed = dict(graph_fixed_scale=True, plan_down_mbps=100, plan_up_mbps=20, graph_scale_headroom=10)
    det = WidgetRenderer(_cfg(graph_style="detailed", **fixed), I18nStrings("en_US"))
    tr = WidgetRenderer(_cfg(graph_style="trend", **fixed), I18nStrings("en_US"))
    _draw(det, flat)
    _draw(tr, flat)
    assert abs(det._graph_cache[(False, "both", "detailed")][4] - tr._graph_cache[(False, "both", "trend")][4]) <= 1
    _, d = _points(det, (False, "both", "detailed"))
    _, t = _points(tr, (False, "both", "trend"))
    assert t[-1] == pytest.approx(d[-1], abs=1.0)   # a flat 50 Mbps sits on the same row


def test_trend_split_panel_and_hardware_graph_paint(q_app):
    tr = WidgetRenderer(_cfg(graph_style="trend", graph_split_directions=True), I18nStrings("en_US"))
    img = _draw(tr, _noisy_history(), series="upload")
    up, down = _points(tr, (False, "upload", "trend"))
    assert len(up) and not len(down)
    hw = [HardwareStatSnapshot(value=50 + 20 * math.sin(i / 5), timestamp=T0 + timedelta(seconds=i))
          for i in range(120)]
    img = _draw(tr, hw, is_hardware=True, hardware_color="#00FF00")
    up, _ = _points(tr, (True, "both", "trend"))
    assert len(up)
    assert any(QColor(img.pixel(x, y)).green() > 100 for x in range(W) for y in range(H))


def test_graph_cache_key_includes_the_style(q_app):
    """One renderer drawing both styles (e.g. a preview built from another config) must not hand one
    style the other's cached polyline."""
    hist = _noisy_history()
    r = WidgetRenderer(_cfg(graph_style="detailed"), I18nStrings("en_US"))
    _draw(r, hist)
    trend_cfg = RenderConfig.from_dict(_cfg(graph_style="trend"))
    img = QImage(W, H, QImage.Format.Format_ARGB32)
    p = QPainter(img)
    r.draw_mini_graph(p, W, H, trend_cfg, hist)
    p.end()
    assert {(False, "both", "detailed"), (False, "both", "trend")} <= set(r._graph_cache)
    assert len(r._graph_cache[(False, "both", "detailed")][3]) != len(r._graph_cache[(False, "both", "trend")][3])


# --- Detailed is pixel-identical to before the option existed -------------------------------------------

# Two checks. (1) Portable, runs everywhere: the Detailed path builds the SAME polylines / plan-line row
# as main's algorithm (recomputed independently below) and issues the SAME draw calls (Plus-composited
# alpha-120 gradient fill, 1.5 px round-join pens). (2) Exact pixels, maintainer-only: sha256 prefixes
# of graph-only renders made on origin/main @ 498d4d5 (before graph_style existed). Antialiased raster
# output differs between machines even on one Qt build (the GitHub windows runner disagrees with the
# maintainer's PC), so (2) runs only with NST_PIXEL_HASH=1 on the Qt it was captured with.
PINNED_QT = "6.10.0"
PINNED = {
    ("auto", 120, 40): "b030072dfcdd7d9a", ("auto", 60, 22): "f0fe2d2a0cb48538",
    ("fixed", 120, 40): "ad561a91cd0326b9", ("fixed", 60, 22): "fa4cd7f679a33f36",
    ("op40", 120, 40): "58396c6726861d23", ("op40", 60, 22): "f17419913f80fb68",
    ("split_up", 120, 40): "dfbcde39febc92de", ("split_up", 60, 22): "e7dcc87fe20eacce",
    ("hw", 120, 40): "b59656ada9df6d1b", ("hw", 60, 22): "5a25a77b8a13251b",
}


def _pinned_data():
    rng = random.Random(5)
    hist = [AggregatedSpeedData(upload=abs(rng.gauss(2e5, 8e4)) + (3e6 if 60 < i < 70 else 0),
                                download=abs(rng.gauss(1e6, 4e5)) + (8e6 if 100 < i < 150 else 0),
                                timestamp=T0 + timedelta(seconds=i)) for i in range(180)]
    hw = [HardwareStatSnapshot(value=30 + 20 * math.sin(i / 7) + rng.uniform(-10, 10),
                               timestamp=T0 + timedelta(seconds=i)) for i in range(180)]
    return hist, hw


@pytest.mark.skipif(os.environ.get("NST_PIXEL_HASH") != "1",
                    reason="exact pixel hashes are machine-specific; set NST_PIXEL_HASH=1 to run")
@pytest.mark.skipif(QT_VERSION_STR != PINNED_QT, reason=f"hashes captured on Qt {PINNED_QT}")
@pytest.mark.parametrize("name,w,h", sorted(PINNED))
def test_detailed_pixels_match_main(q_app, name, w, h):
    hist, hw = _pinned_data()
    overrides = {"fixed": dict(graph_fixed_scale=True, plan_down_mbps=50, plan_up_mbps=20, graph_scale_headroom=10),
                 "op40": dict(graph_opacity=40), "split_up": dict(graph_split_directions=True)}.get(name, {})
    cfg = _cfg(**overrides)
    cfg["graph_style"] = "detailed"   # Trend is the default; Detailed is the opt-in being pinned
    r = WidgetRenderer(cfg, I18nStrings("en_US"))
    if name == "hw":
        img = _draw(r, hw, w, h, is_hardware=True, hardware_color="#76B900")
    elif name == "split_up":
        img = _draw(r, hist, w, h, series="upload")
    else:
        img = _draw(r, hist, w, h)
    bits = img.constBits()
    bits.setsize(img.sizeInBytes())
    assert hashlib.sha256(bytes(bits)).hexdigest()[:16] == PINNED[(name, w, h)]


def _main_scale(history, cfg, series):
    """main @ 498d4d5's _network_graph_scale, restated here so a change to the renderer's copy shows."""
    ceiling = {"upload": cfg.graph_scale_ceiling_up,
               "download": cfg.graph_scale_ceiling_down}.get(series, cfg.graph_scale_ceiling)
    if cfg.graph_fixed_scale and ceiling > 0:
        return max(ceiling * (1.0 + cfg.graph_scale_headroom), 12500), ceiling
    uploads = [d.upload for d in history] if series in ("both", "upload") else []
    downloads = [d.download for d in history] if series in ("both", "download") else []
    top = max(uploads + downloads)
    if len(history) > 10:
        ordered = sorted(uploads + downloads)
        p95 = ordered[int(len(ordered) * 0.95)]
        if p95 > 0 and top > p95 * 3.0:
            top = p95
    return max(top * 1.15, 12500), None


def _main_polylines(history, cfg, w, h, series="both", is_hardware=False):
    """main's Detailed polylines and plan-line row, recomputed independently of the renderer."""
    from netspeedtray.utils.helpers import calculate_monotone_cubic_interpolation
    if cfg.max_samples and len(history) > cfg.max_samples:
        history = history[-cfg.max_samples:]
    left, top, bottom_pad = 2, 3, 1          # GRAPH_LEFT_PADDING, GRAPH_MARGIN, GRAPH_BOTTOM_PADDING
    rw, rh = w - 2 * left, h - (top + bottom_pad)
    max_y, ceiling = (100.0, None) if is_hardware else _main_scale(history, cfg, series)
    y_cap = max_y if ceiling is not None else math.inf
    n = len(history)
    step = rw / (n - 1)
    right, base = float(left + rw - 1), float(top + rh - 1)
    xs = [right - (n - 1 - i) * step for i in range(n)]

    def line(acc):
        cx, cy = calculate_monotone_cubic_interpolation(xs, [acc(d) for d in history], density=5)
        return [(x, base - (min(max(0, y), y_cap) / max_y) * rh) for x, y in zip(cx, cy)]

    if ceiling is not None and cfg.graph_show_ceiling and cfg.graph_scale_headroom > 0:
        ceiling_y = math.floor(base - (ceiling / max_y) * rh) + 0.5
    else:
        ceiling_y = None
    if is_hardware:
        return line(lambda d: d.value), [], ceiling_y
    up = line(lambda d: d.upload) if series in ("both", "upload") else []
    down = line(lambda d: d.download) if series in ("both", "download") else []
    return up, down, ceiling_y


DETAILED_CASES = {
    "auto": ({}, "both", False), "fixed": (dict(graph_fixed_scale=True, plan_down_mbps=50, plan_up_mbps=20,
                                                 graph_scale_headroom=10), "both", False),
    "split_up": (dict(graph_split_directions=True), "upload", False),
    "split_down": (dict(graph_split_directions=True), "download", False),
    "hw": ({}, "both", True),
}


@pytest.mark.parametrize("w,h", [(120, 40), (60, 22)])
@pytest.mark.parametrize("case", sorted(DETAILED_CASES))
def test_detailed_polylines_match_mains_algorithm(q_app, case, w, h):
    """Portable half of "Detailed is unchanged": the cached polylines and plan-line row equal main's
    algorithm point for point (spline density 5, accessors, y mapping, outlier-clipped auto-fit)."""
    overrides, series, is_hw = DETAILED_CASES[case]
    hist, hw = _pinned_data()
    cfg = _cfg(**overrides)
    cfg["graph_style"] = "detailed"   # Trend is the default; Detailed is the opt-in being pinned
    r = WidgetRenderer(cfg, I18nStrings("en_US"))
    data = hw if is_hw else hist
    kw = dict(is_hardware=True, hardware_color="#76B900") if is_hw else dict(series=series)
    _draw(r, data, w, h, **kw)
    _size, _key, up, down, ceiling_y = r._graph_cache[(is_hw, series, "detailed")]
    exp_up, exp_down, exp_ceiling = _main_polylines(data, r.config, w, h, series, is_hw)
    assert ceiling_y == exp_ceiling
    for got, exp in ((up, exp_up), (down, exp_down)):
        assert len(got) == len(exp)
        assert np.allclose([(p.x(), p.y()) for p in got], exp, rtol=0, atol=1e-9) if exp else not got


@pytest.mark.parametrize("is_hw", [False, True])
def test_detailed_issues_mains_draw_calls(q_app, is_hw):
    """Portable half, the painting: a recording painter must see main's sequence - opacity, Plus
    composition for an alpha-120 -> 0 vertical gradient fill per series, then SourceOver and 1.5 px
    round-join pens in the series colours (upload before download)."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QPainter as QP
    hist, hw = _pinned_data()
    r = WidgetRenderer(_cfg(graph_style="detailed", graph_opacity=40), I18nStrings("en_US"))
    painter = MagicMock()
    if is_hw:
        r.draw_mini_graph(painter, W, H, r.config, hw, is_hardware=True, hardware_color="#76B900")
        colors = ["#76B900"]
    else:
        r.draw_mini_graph(painter, W, H, r.config, hist)
        colors = [constants.graph.UPLOAD_LINE_COLOR, constants.graph.DOWNLOAD_LINE_COLOR]
    names = [c[0] for c in painter.method_calls]
    painter.setOpacity.assert_called_once_with(pytest.approx(0.40))
    modes = [c.args[0] for c in painter.setCompositionMode.call_args_list]
    assert modes == [QP.CompositionMode.CompositionMode_Plus, QP.CompositionMode.CompositionMode_SourceOver]
    # Fills: one gradient polygon per series, drawn between the two composition-mode switches.
    plus_at, over_at = [i for i, n in enumerate(names) if n == "setCompositionMode"]
    assert [i for i, n in enumerate(names) if n == "drawPolygon"] and \
        all(plus_at < i < over_at for i, n in enumerate(names) if n == "drawPolygon")
    assert painter.drawPolygon.call_count == len(colors)
    brushes = [c.args[0] for c in painter.setBrush.call_args_list if hasattr(c.args[0], "gradient")
               and c.args[0].gradient() is not None]
    assert len(brushes) == len(colors)
    for brush, color in zip(brushes, colors):
        (p0, c0), (p1, c1) = brush.gradient().stops()
        assert (p0, p1) == (0.0, 1.0) and (c0.alpha(), c1.alpha()) == (120, 0)
        assert c0.name().lower() == color.lower()
    # Lines: drawn after SourceOver, one 1.5 px round-join pen per series, then restore.
    assert painter.drawPolyline.call_count == len(colors)
    assert all(i > over_at for i, n in enumerate(names) if n == "drawPolyline")
    pens = [c.args[0] for c in painter.setPen.call_args_list if hasattr(c.args[0], "widthF")]
    assert [(p.widthF(), p.joinStyle(), p.color().name().lower()) for p in pens] == \
        [(1.5, Qt.PenJoinStyle.RoundJoin, c.lower()) for c in colors]
    assert names[0] == "save" and names[-1] == "restore"


# --- Settings ----------------------------------------------------------------------------------------

@pytest.fixture
def section(q_app):
    from netspeedtray.views.settings.pages.mini_graph_section import MiniGraphSection
    return MiniGraphSection(I18nStrings("en_US"), MagicMock())


def test_settings_graph_style_round_trip(section):
    section.load_settings({"graph_enabled": True})
    assert section.get_settings()["graph_style"] == "trend"   # the default
    section.load_settings({"graph_enabled": True, "graph_style": "trend"})
    assert section.graph_style.currentData() == "trend"
    assert section.get_settings()["graph_style"] == "trend"
    section.load_settings({"graph_enabled": True, "graph_style": "bogus"})
    assert section.get_settings()["graph_style"] == "trend"
    section.load_settings({"graph_enabled": True, "graph_style": "detailed"})
    assert section.get_settings()["graph_style"] == "detailed"


def test_settings_graph_style_change_fires_on_change(section):
    section.load_settings({"graph_enabled": True, "graph_style": "detailed"})
    section.on_change.reset_mock()
    section.graph_style.setCurrentIndex(section.graph_style.findData("trend"))
    assert section.on_change.called


def test_settings_graph_style_grays_out_with_the_graph_off(section):
    section.load_settings({"graph_enabled": False, "graph_style": "trend"})
    assert section._style_card.isEnabled() is False
    assert section.get_settings()["graph_style"] == "trend"   # the stored choice survives
    section.enable_graph.setChecked(True)
    assert section._style_card.isEnabled() is True
