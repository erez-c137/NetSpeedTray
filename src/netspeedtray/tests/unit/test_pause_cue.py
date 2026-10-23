"""
Pause froze the numbers with no visual cue, so a paused widget looked hung - the very failure the
non-persisted pause was meant to avoid. A paused frame is now drawn dimmed, and a two-bar pause glyph
takes the place of the up/down arrow column (or sits in a free left margin when no arrows are drawn).

The glyph must never overlap text, in any display mode, on either theme, on a side taskbar's compact
layout or with split graphs - checked on real pixels: the paused frame is painted once with the glyph
suppressed, and no text pixel may fall inside any glyph rect. The widget's rect never changes.
"""
import inspect

import numpy as np
import pytest
from PyQt6.QtCore import QRect
from PyQt6.QtGui import QColor, QImage, QPainter

from netspeedtray import constants
from netspeedtray.tests.color_helpers import themed
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils import widget_paint as wp
from netspeedtray.utils.widget_renderer import WidgetRenderer
from netspeedtray.utils.widget_paint import WidgetMetrics, render_widget, font_from_config
from netspeedtray.tests.unit import test_side_taskbar_compact as side

HW = dict(monitor_cpu_enabled=True, monitor_gpu_enabled=True)
HORIZONTAL = {   # name: (config overrides, cycle phase, arrows expected)
    "network_only": (dict(widget_display_mode="network_only"), "network_only", True),
    "network_graph": (dict(widget_display_mode="network_only", graph_enabled=True), "network_only", True),
    "split_graphs": (dict(widget_display_mode="network_only", graph_enabled=True, graph_split_directions=True),
                     "network_only", True),
    "combined": (dict(widget_display_mode="combined", **HW), "network_only", False),   # CPU+GPU only
    "side_by_side": (dict(widget_display_mode="side_by_side", **HW), "network_only", True),
    "cycle_net": (dict(widget_display_mode="cycle", **HW), "network_only", True),
    "cycle_cpu": (dict(widget_display_mode="cycle", **HW), "cpu_only", False),
    "cpu_only": (dict(widget_display_mode="cpu_only", **HW), "network_only", False),
    "gpu_only": (dict(widget_display_mode="gpu_only", **HW), "network_only", False),
    "hide_arrows": (dict(widget_display_mode="network_only", hide_arrows=True), "network_only", False),
    "hide_arrows_sbs": (dict(widget_display_mode="side_by_side", hide_arrows=True, **HW), "network_only", False),
}
THEMES = {"dark": "#FFFFFF", "light": "#1A1A1A"}
METRICS = WidgetMetrics(upload_mbps=1.4, download_mbps=48.7, cpu_usage=37.0, gpu_usage=58.0)


def _cfg(overrides, text_color):
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    cfg.update({"graph_enabled": False, "background_opacity": 0, "default_color": text_color})
    cfg.update(overrides)
    return themed(cfg)


def _paint(cfg, w, h, *, layout_mode="horizontal", cycle_mode="network_only", paused=True, glyph=True,
           monkeypatch=None):
    """Paint on a canvas 2x the widget, so anything drawn outside the widget's rect is visible."""
    img = QImage(w * 2, h * 2, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    renderer = WidgetRenderer(cfg, I18nStrings("en_US"))
    if not glyph:
        # The text mask: no glyph, and no graph layer (lines, fills, split divider) - the glyph may sit
        # over the graph like the arrows it replaces; only text counts. Same layout either way.
        monkeypatch.setattr(wp, "_draw_pause_glyphs", lambda *a: None)
        monkeypatch.setattr(WidgetRenderer, "draw_mini_graph", lambda *a, **k: None)
        monkeypatch.setattr(WidgetRenderer, "draw_split_divider", staticmethod(lambda *a, **k: None))
    p = QPainter(img)
    try:
        render_widget(p, QRect(0, 0, w, h), renderer, renderer.config, METRICS, layout_mode=layout_mode,
                      cycle_mode=cycle_mode, font=font_from_config(cfg), paused=paused)
    finally:
        p.end()
    if not glyph:
        monkeypatch.undo()
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes())
    return np.frombuffer(ptr, dtype=np.uint8).reshape((h * 2, w * 2, 4)).copy(), renderer.pause_glyph_rects


def _assert_no_overlap(cfg, w, h, monkeypatch, label, **kw):
    full, rects = _paint(cfg, w, h, **kw)
    text, rects_again = _paint(cfg, w, h, glyph=False, monkeypatch=monkeypatch, **kw)
    assert [r.getRect() for r in rects] == [r.getRect() for r in rects_again]
    for r in rects:
        x0, y0 = int(r.left()), int(r.top())
        x1, y1 = int(np.ceil(r.right())), int(np.ceil(r.bottom()))
        assert 0 <= x0 and x1 <= w and 0 <= y0 and y1 <= h, f"{label}: glyph outside the widget"
        assert not (text[y0:y1, x0:x1, 3] > 2).any(), f"{label}: pause glyph overlaps text at {r.getRect()}"
    # The cue adds nothing outside the widget's rect (whatever the unpaused frame already does there).
    live, _ = _paint(cfg, w, h, paused=False, **kw)
    beyond = np.ones((h * 2, w * 2), dtype=bool)
    beyond[:h, :w] = False
    assert not ((full[..., 3] > 0) & ~(live[..., 3] > 0) & beyond).any(), f"{label}: drew outside the widget"
    return rects


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("mode", HORIZONTAL)
@pytest.mark.parametrize("height", [48, 32])
def test_glyph_never_overlaps_text_on_a_horizontal_taskbar(q_app, monkeypatch, mode, theme, height):
    overrides, phase, arrows = HORIZONTAL[mode]
    rects = _assert_no_overlap(_cfg(overrides, THEMES[theme]), 260, height, monkeypatch, mode,
                               cycle_mode=phase)
    if arrows:
        assert rects, f"{mode}: the arrow column should carry the pause glyph"


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("mode", side.MODES)
@pytest.mark.parametrize("thickness", [48, 32])
def test_glyph_never_overlaps_text_on_a_side_taskbar(q_app, monkeypatch, mode, theme, thickness):
    cfg = side._config(**side.MODES[mode])
    cfg.update(themed({"default_color": THEMES[theme]}))
    w, h, _fit = side._size(cfg, side._taskbar(side.LEFT, thickness))
    _assert_no_overlap(cfg, w, h, monkeypatch, f"side {mode}", layout_mode="vertical")


def test_split_panels_in_one_column_get_a_glyph_each_off_the_divider(q_app):
    """Split graphs stacked on a side bar: both rows' arrow cells share a column but sit either side of
    the divider (on the real-font platform: cells at y 4-20 and 32-48 in a 48 x 72 widget). One glyph
    per row - never a merged one straddling the divider. A stacked readout's adjacent rows still merge.
    Geometry-only, so it holds whatever fonts the test platform has."""
    from PyQt6.QtCore import QRectF
    r = WidgetRenderer(_cfg({}, "#FFFFFF"), I18nStrings("en_US"))
    r._pause_slots = [QRectF(9, 4, 8, 16), QRectF(9, 32, 8, 16)]      # split panels, divider at ~26
    rects = wp._pause_glyph_rects(QRect(0, 0, 48, 72), r)
    assert len(rects) == 2
    for g, cell in zip(rects, r._pause_slots):
        assert cell.contains(g)
    gap = constants.renderer.TWO_ROW_GAP_PX
    r._pause_slots = [QRectF(9, 4, 8, 16), QRectF(9, 20 + gap, 8, 16)]   # one stacked readout
    rects = wp._pause_glyph_rects(QRect(0, 0, 48, 72), r)
    assert len(rects) == 1 and abs(rects[0].center().y() - (4 + 36 + gap) / 2) < 0.01


def test_glyph_replaces_the_arrows_centered_across_both_rows(q_app):
    cfg = _cfg(dict(widget_display_mode="network_only"), "#FFFFFF")
    live, none = _paint(cfg, 200, 48, paused=False)
    paused, rects = _paint(cfg, 200, 48)
    assert none == [] and len(rects) == 1
    g = rects[0]
    assert abs(g.center().y() - 24) <= 1.5            # centered across the two rows
    # The arrows are gone: inside the glyph's column nothing but the glyph is painted, at full opacity.
    assert (paused[..., 3] >= 250).sum() < (live[..., 3] >= 250).sum() * 0.5   # values dimmed
    assert (paused[int(g.top()):int(g.bottom()), int(g.left()):int(np.ceil(g.right())), 3] >= 250).any()


def test_dimmed_values_stay_readable():
    assert 0.5 <= constants.renderer.PAUSED_CONTENT_OPACITY <= 0.6


def test_unpaused_is_unchanged(q_app):
    cfg = _cfg(dict(widget_display_mode="network_only"), "#FFFFFF")
    a, ra = _paint(cfg, 200, 48, paused=False)
    b, rb = _paint(cfg, 200, 48, paused=False)
    assert (a == b).all() and ra == rb == []


def test_live_widget_passes_its_pause_state():
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    assert "paused=self.is_paused" in inspect.getsource(NetworkSpeedWidget.paintEvent)
