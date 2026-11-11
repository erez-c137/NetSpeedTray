"""
Line spacing: the gap between the widget's upload and download rows, -4..+4 px (Settings > Appearance >
Interface layout). The default, 1, is the fixed ``TWO_ROW_GAP_PX`` the widget always painted with, so
an untouched config renders exactly as before.

Every place that sizes or paints the two-row network readout must agree on the gap: the renderer
(``draw_network_speeds``), the docked-height model (``_row_baselines`` / ``_two_row_floor``), the
side-taskbar heights (``side_classic_height``, ``stacked_segment_height``) and the pause glyph, which
merges one readout's two arrow cells into a single glyph.
"""
import inspect
import json
import os
import subprocess
import sys
from unittest.mock import MagicMock

import numpy as np
import pytest
from PyQt6.QtCore import QRect, QRectF
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils import widget_paint as wp
from netspeedtray.utils.helpers import line_spacing_px
from netspeedtray.utils.widget_paint import font_from_config
from netspeedtray.utils.widget_renderer import RenderConfig, WidgetRenderer
from netspeedtray.views.widget.layout import WidgetLayoutManager
from netspeedtray.tests.unit import test_dock_strip_322 as dock
from netspeedtray.tests.unit import test_pause_cue as pause
from netspeedtray.tests.unit import test_side_taskbar_compact as side

DEFAULT = constants.config.defaults.DEFAULT_LINE_SPACING


# --- The value ------------------------------------------------------------------------------------

def test_default_is_the_old_fixed_gap():
    assert DEFAULT == constants.renderer.TWO_ROW_GAP_PX == 1
    assert constants.config.defaults.DEFAULT_CONFIG["line_spacing"] == DEFAULT
    schema = constants.config.defaults.VALIDATION_SCHEMA["line_spacing"]
    assert (schema["min"], schema["max"], schema["default"]) == (-4, 4, DEFAULT)


@pytest.mark.parametrize("raw,expected", [(-4, -4), (0, 0), (4, 4), (None, DEFAULT), (True, DEFAULT),
                                          (False, DEFAULT), ("3", DEFAULT), (2.5, DEFAULT)])
def test_only_a_plain_int_is_taken(raw, expected):
    """A bool is an int in Python; a hand-edited ``true`` must not become a 1 px gap in the renderer
    while the layout reads the default - both go through ``line_spacing_px``."""
    assert line_spacing_px(raw) == expected
    assert RenderConfig.from_dict({"line_spacing": raw}).line_spacing == expected


def test_missing_key_is_the_default():
    assert RenderConfig.from_dict({}).line_spacing == DEFAULT


# --- The renderer: the rows move apart by exactly the spacing ---------------------------------------

def _ink_rows(cfg, w=220, h=48):
    img, _ = pause._paint(cfg, w, h, paused=False)
    rows = np.nonzero((img[:h, :w, 3] > 2).any(axis=1))[0]     # text only, not the near-clear backdrop
    return int(rows[0]), int(rows[-1])


@pytest.mark.parametrize("spacing", [-4, -1, 0, 2, 4])
def test_rows_painted_spacing_apart(q_app, spacing):
    base = pause._cfg(dict(widget_display_mode="network_only"), "#FFFFFF")
    top0, bottom0 = _ink_rows(base)
    top, bottom = _ink_rows({**base, "line_spacing": spacing})
    assert (bottom - top) - (bottom0 - top0) == spacing - DEFAULT
    assert abs((top + bottom) - (top0 + bottom0)) <= 1          # still centred


# --- The docked-height model --------------------------------------------------------------------

def _lm(spacing=None, font_size=9) -> WidgetLayoutManager:
    widget = MagicMock()
    widget.config = {} if spacing is None else {"line_spacing": spacing}
    lm = WidgetLayoutManager(widget)
    lm.metrics = QFontMetrics(QFont("Segoe UI", font_size))
    return lm


@pytest.mark.parametrize("spacing", [-4, 0, 1, 4])
def test_layout_baselines_follow_the_spacing(q_app, spacing):
    lm, line_h = _lm(spacing), _lm().metrics.height()
    net_top, net_bottom, hw_top, hw_bottom = lm._row_baselines(48)
    assert net_bottom - net_top == line_h + spacing
    assert lm._two_row_height() == 2 * line_h + spacing
    assert (hw_top, hw_bottom) == _lm()._row_baselines(48)[2:]   # CPU/GPU rows have no gap


def test_layout_reads_junk_as_the_default(q_app):
    assert _lm(True)._row_gap() == _lm("4")._row_gap() == _lm()._row_gap() == DEFAULT
    assert WidgetLayoutManager(MagicMock())._row_gap() == DEFAULT     # config is not even a dict


@pytest.mark.parametrize("spacing,extra", [(None, 2), (1, 2), (4, 5), (-4, 2)])
def test_two_row_floor(q_app, spacing, extra):
    """The #221 floor (``2*line_height + 2`` by default) grows with a wider spacing, and a negative one
    never lowers it: the hardware rows are painted without a gap and would crop."""
    lm = _lm(spacing)
    assert lm._two_row_floor(200) == 2 * lm.metrics.height() + extra


# --- A docked horizontal taskbar: the gap gives way, the widget doesn't outgrow the bar -------------

def _docked(spacing, font_size, strip, edge=dock.BOTTOM):
    lm = _lm(spacing, font_size)
    h = lm._horizontal_dock_height(edge, dock._taskbar(edge, inset=strip, window_h=strip), 1.0)
    return lm, h


def _tightest_strip(font_size):
    """The thinnest bar the default gap fits for this platform's font - the Small-taskbar situation,
    whatever font the test platform falls back to."""
    return _lm(None, font_size)._two_row_floor(1)


@pytest.mark.parametrize("edge", [dock.TOP, dock.BOTTOM])
@pytest.mark.parametrize("spacing", [2, 3, 4])
@pytest.mark.parametrize("font_size,strip", [(9, None), (11, None), (9, 32), (11, 40)])
def test_wide_spacing_never_outgrows_a_horizontal_strip(q_app, font_size, strip, spacing, edge):
    """#455 review: +3/+4 used to floor a 9 pt widget to 34-35 px on the 32 px Small taskbar - the #322
    overhang back through a setting. The widget is now exactly as tall as with the default gap, and the
    rows are painted with the narrower gap that height was sized for. (Real Segoe UI on the 32/40 px
    bars: ``test_wide_spacing_on_real_small_taskbars``.)"""
    tight = strip is None
    strip = _tightest_strip(font_size) if tight else strip
    lm, h = _docked(spacing, font_size, strip, edge)
    _lm_default, h_default = _docked(None, font_size, strip, edge)
    overhangs = lm._two_row_floor(strip, spacing) > strip
    if tight and spacing == 4:
        assert overhangs, "precondition: the user's gap alone would overhang the bar"
    assert h == h_default <= strip
    # Narrowed only as far as needed: kept whole when it fits, never below the default.
    assert (DEFAULT <= lm.docked_row_gap < spacing) if overhangs else lm.docked_row_gap == spacing
    assert lm._two_row_floor(strip, lm.docked_row_gap) <= strip
    if lm.docked_row_gap + 1 <= spacing:
        assert lm._two_row_floor(strip, lm.docked_row_gap + 1) > strip   # the widest gap that fits
    painted = lm.paint_config(RenderConfig.from_dict({"line_spacing": spacing}))
    assert painted.line_spacing == lm.docked_row_gap


@pytest.mark.parametrize("painted", [False, True])
@pytest.mark.parametrize("font_size", [8, 9, 11, 14])
def test_the_one_height_check_agrees_with_the_full_floor(q_app, font_size, painted):
    """``_docked_rows`` checks each wider gap at the one height it must fit; that must give the same
    answer as the full ``_two_row_floor`` scan, on every strip from far too thin to roomy."""
    lm = _lm(4, font_size)
    if painted:
        lm.widget.i18n = I18nStrings("en_US")
        lm._font = QFont("Segoe UI", font_size)
    for strip in range(16, 50, 3):
        limit = max(strip, lm._two_row_floor(strip, DEFAULT))
        for gap in (2, 3, 4):
            assert lm._floor_within(limit, gap) == (lm._two_row_floor(strip, gap) <= limit), (strip, gap)


@pytest.mark.parametrize("spacing", [-4, -1, 0, 1, 2, 4])
def test_a_roomy_strip_keeps_the_users_spacing(q_app, spacing):
    lm, h = _docked(spacing, 9, 48)
    assert (h, lm.docked_row_gap) == (48, spacing)
    config = RenderConfig.from_dict({"line_spacing": spacing})
    assert lm.paint_config(config) is config           # nothing to narrow: the same object


@pytest.mark.parametrize("spacing", [-4, -1, 0])
def test_a_tight_spacing_is_never_narrowed(q_app, spacing):
    lm, _h = _docked(spacing, 9, 24)                    # a strip thinner than even the default fits
    assert lm.docked_row_gap == spacing


def test_paint_config_untouched_when_not_docked(q_app):
    lm = _lm(4)
    assert lm.docked_row_gap is None
    config = RenderConfig.from_dict({"line_spacing": 4})
    assert lm.paint_config(config) is config


@pytest.mark.parametrize("edge", [dock.TOP, dock.BOTTOM])
def test_narrowed_rows_paint_inside_the_small_taskbar(q_app, edge):
    """The painted proof: on the thinnest bar the default fits, +4 alone would overhang; the layout
    narrows the gap, and the frame painted the way ``paintEvent`` does (``paint_config``) keeps every
    text pixel inside the widget - above and below."""
    cfg = pause._cfg({"widget_display_mode": "network_only", "line_spacing": 4}, "#FFFFFF")
    font = font_from_config(cfg)
    widget = MagicMock()
    widget.config, widget.i18n = {"line_spacing": 4}, I18nStrings("en_US")
    lm = WidgetLayoutManager(widget)
    lm.metrics, lm._font = QFontMetrics(font), font                 # sized by painting, like the app
    strip = lm._two_row_floor(1, DEFAULT)
    assert lm._two_row_floor(strip, 4) > strip, "precondition: +4 alone would overhang the bar"
    h = int(lm._horizontal_dock_height(edge, dock._taskbar(edge, inset=strip, window_h=strip), 1.0))
    assert h == strip and DEFAULT <= lm.docked_row_gap < 4

    renderer, w = WidgetRenderer(cfg, I18nStrings("en_US")), 260
    painted = lm.paint_config(renderer.config)
    assert painted.line_spacing == lm.docked_row_gap
    img = QImage(w, h * 3, QImage.Format.Format_ARGB32)              # the widget in the middle third
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    try:
        p.translate(0, h)
        wp.render_widget(p, QRect(0, 0, w, h), renderer, painted, pause.METRICS,
                         layout_mode="horizontal", cycle_mode="network_only", font=font, paused=False)
    finally:
        p.end()
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes())
    alpha = np.frombuffer(ptr, np.uint8).reshape(h * 3, img.bytesPerLine() // 4, 4)[:, :w, 3]
    ink = np.nonzero((alpha > 2).any(axis=1))[0]
    assert ink.size, "nothing painted"
    assert h <= ink[0] and ink[-1] < 2 * h, f"text outside the {h} px widget: rows {ink[0] - h}..{ink[-1] - h}"


def test_paint_config_never_widens_the_gap(q_app):
    """A stale ``docked_row_gap`` (sized before the user narrowed the spacing) must not paint wider."""
    lm = _lm(1)
    lm.docked_row_gap = 3
    config = RenderConfig.from_dict({"line_spacing": 1})
    assert lm.paint_config(config) is config


@pytest.mark.parametrize("where", ["side", "free_float"])
def test_docked_gap_is_dropped_when_no_longer_on_a_horizontal_bar(q_app, monkeypatch, where):
    """Re-sized for a side bar or free float, the widget paints the user's own gap again."""
    from PyQt6.QtGui import QGuiApplication
    from netspeedtray.utils import taskbar_utils
    from netspeedtray.views.widget import layout as layout_mod

    lm = _lm(4)
    lm.widget.config = {**constants.config.defaults.DEFAULT_CONFIG, "line_spacing": 4,
                        "free_float": where == "free_float"}
    lm.widget.renderer = WidgetRenderer(lm.widget.config, I18nStrings("en_US"))
    lm.widget.devicePixelRatioF.return_value = 1.0
    lm.docked_row_gap = 2                             # left over from a thin horizontal bar
    monkeypatch.setattr(layout_mod, "get_taskbar_info", lambda *a, **k: side._taskbar(side.LEFT, 48))
    monkeypatch.setattr(taskbar_utils, "get_free_float_screen",
                        lambda *a, **k: QGuiApplication.primaryScreen() if where == "free_float" else None)
    lm.resize_widget_for_font()
    assert lm.docked_row_gap is None
    config = RenderConfig.from_dict({"line_spacing": 4})
    assert lm.paint_config(config) is config


def test_both_paint_events_paint_with_the_layout_gap():
    """``paintEvent`` of the main widget and of the secondary-taskbar mirrors size and paint alike."""
    from netspeedtray.views.widget import main as widget_main, mirror
    for cls in (widget_main.NetworkSpeedWidget, mirror.MirrorWidget):
        assert "layout_manager.paint_config(" in inspect.getsource(cls.paintEvent), cls.__name__


# --- Side taskbar ------------------------------------------------------------------------------

def test_stacked_network_segment_height():
    assert wp.stacked_segment_height("network", 16, False, False) == 33               # unchanged default
    assert wp.stacked_segment_height("network", 16, False, False, 4) == 36
    assert wp.stacked_segment_height("network", 16, False, False, -4) == 28
    assert wp.stacked_segment_height("cpu", 16, True, False, 4) == 32                 # no gap there


@pytest.mark.parametrize("mode", ["network_only", "split_graphs", "side_by_side"])
@pytest.mark.parametrize("spacing", [-4, 4])
def test_classic_side_height_moves_with_the_spacing(spacing, mode):
    cfg = side._config(**side.MODES[mode])
    base = RenderConfig.from_dict(cfg)
    moved = RenderConfig.from_dict({**cfg, "line_spacing": spacing})
    panels = 2 if mode == "split_graphs" else 1
    assert (wp.side_classic_height(moved, 16) - wp.side_classic_height(base, 16)
            == panels * (spacing - DEFAULT))


@pytest.mark.parametrize("mode", ["cpu_only", "combined"])
@pytest.mark.parametrize("spacing", [-4, 4])
def test_hardware_only_heights_ignore_the_spacing(q_app, mode, spacing):
    """CPU/GPU rows have no gap, so a hardware-only widget keeps its height (side bar and free float)."""
    cfg = side._config(**side.MODES[mode])
    moved = {**cfg, "line_spacing": spacing}
    assert (wp.side_classic_height(RenderConfig.from_dict(moved), 16)
            == wp.side_classic_height(RenderConfig.from_dict(cfg), 16))
    assert side._size(moved, side._taskbar(side.LEFT, 300))[1] == side._size(cfg, side._taskbar(side.LEFT, 300))[1]


@pytest.mark.parametrize("mode", ["network_only", "network_graph", "split_graphs", "side_by_side"])
@pytest.mark.parametrize("spacing", [-4, 4])
@pytest.mark.parametrize("thickness", [48, 32, 300])
def test_side_bar_never_paints_outside_the_widget(q_app, mode, spacing, thickness):
    """A guard, not the regression test for the side-bar heights (the 8 px padding hides a few px):
    48/32 px bars use the compact ladder (its own fixed gap); a 300 px one (wide enough with the test
    platform's fallback font) the classic two-row readout, sized by ``side_classic_height``."""
    cfg = side._config(**side.MODES[mode], line_spacing=spacing)
    w, h, fit = side._size(cfg, side._taskbar(side.LEFT, thickness))
    if thickness == 300:
        assert fit is None or fit.classic, "precondition: the classic two-row readout"
    ys, xs = side._outside(side._paint(cfg, w, h), w, h)
    assert len(ys) == 0, f"{mode} @ {spacing:+d}: ink outside the {w}x{h} widget"


# --- Pause glyph --------------------------------------------------------------------------------

def test_pause_glyph_merges_rows_at_the_widest_spacing(q_app):
    """At +4 one readout's two arrow cells are 4 px apart: still one glyph across both rows. Split
    panels on a side bar (cells ~12 px apart, a divider between them) still get one each."""
    r = WidgetRenderer(pause._cfg({}, "#FFFFFF"), I18nStrings("en_US"))
    r._pause_slots = [QRectF(9, 4, 8, 16), QRectF(9, 24, 8, 16)]
    assert len(wp._pause_glyph_rects(QRect(0, 0, 48, 72), r, 4)) == 1
    r._pause_slots = [QRectF(9, 4, 8, 16), QRectF(9, 32, 8, 16)]
    assert len(wp._pause_glyph_rects(QRect(0, 0, 48, 72), r, 4)) == 2


@pytest.mark.parametrize("spacing", [-4, 4])
@pytest.mark.parametrize("mode", ["network_only", "side_by_side", "split_graphs"])
def test_pause_glyph_never_overlaps_text(q_app, monkeypatch, mode, spacing):
    overrides, phase, _arrows = pause.HORIZONTAL[mode]
    cfg = pause._cfg({**overrides, "line_spacing": spacing}, "#FFFFFF")
    rects = pause._assert_no_overlap(cfg, 260, 48, monkeypatch, f"{mode} @ {spacing:+d}", cycle_mode=phase)
    panels = 2 if mode == "split_graphs" else 1      # split graphs: one readout per panel, side by side
    assert len(rects) == panels, f"{mode} @ {spacing:+d}: one glyph across both rows of each readout"


# --- Real fonts: the review's numbers ---------------------------------------------------------------

def _native_small_taskbar_rows():
    """Run in a subprocess on the native Windows platform: Segoe UI with the real paint-fit check (the
    widget's own ``_font``), on Win11's 32 px Small bar at 9 pt and a 40 px bar at 11 pt."""
    rows = []
    for font_size, strip in ((9, 32), (11, 40)):
        for spacing in (None, 3, 4):
            lm = _lm(spacing, font_size)
            lm._font = QFont("Segoe UI", font_size)
            lm.widget.i18n = I18nStrings("en_US")
            h = lm._horizontal_dock_height(dock.BOTTOM, dock._taskbar(dock.BOTTOM, inset=strip, window_h=strip), 1.0)
            rows.append([font_size, strip, spacing, lm.metrics.height(), h, lm.docked_row_gap,
                         lm._two_row_floor(strip, line_spacing_px(spacing))])
    return rows


_NATIVE_PROBE = r"""
import json, sys
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)
from netspeedtray.tests.unit import test_line_spacing as t
print("RESULT" + json.dumps(t._native_small_taskbar_rows()), flush=True)
"""


@pytest.mark.skipif(sys.platform != "win32" or not side.FONT_DIR.is_dir(),
                    reason="needs Windows and its fonts for real text metrics")
def test_wide_spacing_on_real_small_taskbars(tmp_path):
    """#455 review, measured with real Segoe UI (line height 16 at 9 pt): +3/+4 alone would floor the
    widget past the 32 px Small taskbar (and 11 pt past a 40 px one); with the gap giving way it stays
    exactly as tall as with the default spacing."""
    env = {k: v for k, v in os.environ.items() if k not in ("QT_QPA_PLATFORM", "QT_QPA_FONTDIR")}
    env.update({"QT_QPA_PLATFORM": "windows", "QT_SCALE_FACTOR": "1", "APPDATA": str(tmp_path),
                "PYTHONPATH": str(side.SRC_DIR), "PYTHONIOENCODING": "utf-8"})
    proc = subprocess.run([sys.executable, "-c", _NATIVE_PROBE], env=env, capture_output=True, text=True,
                          encoding="utf-8", timeout=180)
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT")]
    assert lines, f"probe failed ({proc.returncode}):\n{proc.stdout[-2000:]}\n{proc.stderr[-4000:]}"
    rows = json.loads(lines[-1][len("RESULT"):])
    default_h = {(size, strip): h for size, strip, spacing, _lh, h, _gap, _floor in rows if spacing is None}
    for size, strip, spacing, _line_h, h, gap, user_floor in rows:
        where = (size, strip, spacing, h, gap, user_floor)
        assert h == default_h[(size, strip)] <= strip, where       # never taller than the bar
        if spacing is not None:
            assert user_floor > strip, where                       # precondition: it would have overhung
            assert DEFAULT <= gap < spacing, where
