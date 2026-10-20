"""#322 - reproduced live: Win11's native taskbar size switched Default (48 px) -> Small (32 px) on a
TOP bar while the app ran. The widget was repositioned but never re-sized, so it kept 48 px and its
second row hung 16 px onto the desktop. After a restart it was 34 px - the #221 two-row floor - 2 px
over the bar's edge.

1. A taskbar change re-runs the sizing when the dock geometry it depends on changed (edge, strip
   thickness, DPI, small mode) - and only then: a plain move or an auto-hide slide resizes nothing.
2. On a strip thinner than the font's two line boxes, the widget takes the strip's height when the
   INK of both rows fits in it (it does for the default 9 pt font in 32 px); 48 px is unchanged.
"""
import json
import os
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtWidgets import QApplication

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils.taskbar_utils import TaskbarInfo
from netspeedtray.utils.widget_renderer import WidgetRenderer
from netspeedtray.views.widget import main as widget_main
from netspeedtray.views.widget.layout import WidgetLayoutManager


@pytest.fixture(scope="session", autouse=True)
def qt_app():
    yield QApplication.instance() or QApplication([])


SRC_DIR = Path(__file__).resolve().parents[3]          # .../src
FONT_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
SCREEN_W, SCREEN_H = 1920, 1080
TOP, BOTTOM, LEFT = constants.TaskbarEdge.TOP, constants.TaskbarEdge.BOTTOM, constants.TaskbarEdge.LEFT


def _taskbar(edge, thickness: int, *, slide: int = 0, tray_x: int = 1700) -> MagicMock:
    """A docked taskbar ``thickness`` px thick on ``edge``, work area inset to match. ``slide`` shifts
    the window rect only (an auto-hide bar mid-animation), never the inset."""
    full = QRect(0, 0, SCREEN_W, SCREEN_H)
    if edge == TOP:
        avail, rect = QRect(0, thickness, SCREEN_W, SCREEN_H - thickness), (0, -slide, SCREEN_W, thickness - slide)
    elif edge == BOTTOM:
        avail = QRect(0, 0, SCREEN_W, SCREEN_H - thickness)
        rect = (0, SCREEN_H - thickness + slide, SCREEN_W, SCREEN_H + slide)
    else:
        avail, rect = QRect(thickness, 0, SCREEN_W - thickness, SCREEN_H), (0, 0, thickness, SCREEN_H)
    screen = MagicMock()
    screen.geometry.return_value = full
    screen.availableGeometry.return_value = avail
    tb = MagicMock(spec=TaskbarInfo)
    tb.rect, tb.hwnd, tb.dpi_scale = rect, 12345, 1.0
    tb.height = rect[3] - rect[1] if edge in (TOP, BOTTOM) else rect[2] - rect[0]
    tb.get_edge_position.return_value = edge
    tb.get_screen.return_value = screen
    tb.get_tray_rect.return_value = (tray_x, rect[1], tray_x + 200, rect[3])
    return tb


def _fake_widget():
    """Just enough of NetworkSpeedWidget to drive its real update_position()/taskbar slot."""
    w = types.SimpleNamespace(
        config={}, logger=MagicMock(), layout_manager=MagicMock(), position_manager=MagicMock(),
        _dock_sig_seen=None, _refresh_cached_layout_mode=lambda: None)
    w.position_manager.is_free_float_active.return_value = False
    w.update_position = types.MethodType(widget_main.NetworkSpeedWidget.update_position, w)
    resize_check = getattr(widget_main.NetworkSpeedWidget, "_resize_if_dock_changed", None)
    if resize_check is not None:
        w._resize_if_dock_changed = types.MethodType(resize_check, w)
    return w


def _resizes_for(sequence) -> list:
    """Feed taskbar states through the taskbar-changed slot; True per step that re-sized."""
    w = _fake_widget()
    out = []
    for tb in sequence:
        before = w.layout_manager.resize_widget_for_font.call_count
        with patch.object(widget_main, "get_taskbar_info", return_value=tb):
            w.update_position()
        out.append(w.layout_manager.resize_widget_for_font.call_count > before)
    return out


# --- 1. Live re-size on a dock-geometry change ---------------------------------------------------

@pytest.mark.parametrize("edge", [TOP, BOTTOM])
@pytest.mark.parametrize("first,second", [(48, 32), (32, 48)])
def test_thickness_change_resizes(edge, first, second):
    assert _resizes_for([_taskbar(edge, first), _taskbar(edge, second)]) == [False, True]


@pytest.mark.parametrize("edge", [TOP, BOTTOM])
def test_same_size_move_does_not_resize(edge):
    # Tray moved (x changes) and an auto-hide slide (rect shifts): same strip, so reposition only.
    seq = [_taskbar(edge, 48), _taskbar(edge, 48, tray_x=1500), _taskbar(edge, 48, slide=20)]
    w = _fake_widget()
    for tb in seq:
        with patch.object(widget_main, "get_taskbar_info", return_value=tb):
            w.update_position()
    assert w.layout_manager.resize_widget_for_font.call_count == 0
    assert w.position_manager.update_position.call_count == 3   # still repositioned every time


def test_edge_change_bottom_to_left_resizes():
    assert _resizes_for([_taskbar(BOTTOM, 48), _taskbar(LEFT, 48)]) == [False, True]


def test_resize_is_not_followed_by_a_second_reposition_or_a_loop():
    # The resize ends in widget.update_position() itself (layout.py); that nested call sees the new
    # signature and only repositions - no second resize.
    w = _fake_widget()
    w.layout_manager.resize_widget_for_font.side_effect = lambda: w.update_position()
    tb48, tb32 = _taskbar(TOP, 48), _taskbar(TOP, 32)
    with patch.object(widget_main, "get_taskbar_info", return_value=tb48):
        w.update_position()
    with patch.object(widget_main, "get_taskbar_info", return_value=tb32):
        w.update_position()
    assert w.layout_manager.resize_widget_for_font.call_count == 1
    assert w.position_manager.update_position.call_count == 2   # startup + the resize's own


def test_fallback_taskbar_never_resizes():
    fallback = _taskbar(TOP, 48)
    fallback.hwnd = 0
    assert _resizes_for([_taskbar(TOP, 48), fallback, _taskbar(TOP, 48)]) == [False, False, False]


# --- 2. Fit a strip thinner than the font's two line boxes ----------------------------------------

def _renderer_and_layout(**overrides):
    """The shipped defaults (font included) through the REAL layout font path and the real renderer."""
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG, **overrides)
    i18n = I18nStrings("en_US")
    r = WidgetRenderer(cfg, i18n)
    widget = MagicMock()
    widget.config, widget.i18n = cfg, i18n
    lm = WidgetLayoutManager(widget)
    lm.set_font(resize=False)
    assert lm.metrics.height() == r.metrics.height()   # layout and paint measure the same font
    return r, lm


def _ink_rows(r: WidgetRenderer, height: int, width: int = 360, pad: int = 20):
    """Paint the docked readout (network rows + CPU/GPU with RAM/VRAM, side by side) into a
    ``height`` px band inside a taller canvas; return (first, last) painted row relative to the band."""
    cfg = r.config
    cfg.default_color = "#FFFFFF"
    img = QImage(width, height + 2 * pad, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.translate(0, pad)
    r.draw_network_speeds(p, 123456.0, 9876543.0, 160, height, cfg, layout_mode="horizontal")
    r.draw_hardware_stats(p, 7.0, 2.0, 200, height, cfg, cpu_temp=37.0, gpu_temp=37.0,
                          ram_info=(11.0, 31.9), vram_info=(1.7, 6.0), layout_mode="horizontal",
                          x_offset=165)
    p.end()
    rows = [y for y in range(img.height())
            if any((img.pixel(x, y) >> 24) & 0xFF for x in range(width))]
    return rows[0] - pad, rows[-1] - pad


def _measure(strip: int, edge=TOP):
    """(old #221 floor, docked height for ``strip``, first ink row, last ink row) with the defaults."""
    r, lm = _renderer_and_layout()
    h = int(lm._horizontal_dock_height(edge, _taskbar(edge, strip), 1.0))
    first, last = _ink_rows(r, h)
    return lm.metrics.height() * 2 + 2, h, first, last


# In-process: the suite's offscreen platform has no font directory, so these run on Qt's fallback
# font - the claims below hold for any font.

@pytest.mark.parametrize("below", [1, 2, 3, 4, 6])
def test_thin_strip_never_crops_and_never_exceeds_the_221_floor(below):
    r, lm = _renderer_and_layout()
    old = lm.metrics.height() * 2 + 2
    strip = old - below
    h = int(lm._horizontal_dock_height(TOP, _taskbar(TOP, strip), 1.0))
    assert strip <= h <= old
    first, last = _ink_rows(r, h)
    assert 0 <= first and last <= h - 1, (strip, h, first, last)


def test_strip_too_thin_for_the_ink_keeps_the_221_floor():
    _, lm = _renderer_and_layout()
    assert lm._horizontal_dock_height(TOP, _taskbar(TOP, 10), 1.0) == lm.metrics.height() * 2 + 2


@pytest.mark.parametrize("edge", [TOP, BOTTOM])
def test_default_48px_strip_unchanged(edge):
    _, lm = _renderer_and_layout()
    assert lm._horizontal_dock_height(edge, _taskbar(edge, 48), 1.0) == 48


# Real Windows fonts (subprocess, like test_settings_no_hscroll): the reported 32 px Small bar.

_PROBE = r"""
import importlib, json, sys
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)
t = importlib.import_module("netspeedtray.tests.unit.test_dock_resize_322")
rows = [[e.name, s, *t._measure(s, e)] for e in (t.TOP, t.BOTTOM) for s in (32, 48)]
print("RESULT" + json.dumps(rows), flush=True)
"""


@pytest.mark.skipif(sys.platform != "win32" or not FONT_DIR.is_dir(),
                    reason="needs the Windows font directory for real text metrics")
def test_small_32px_bar_fits_with_the_real_default_font(tmp_path):
    env = dict(os.environ)
    env.update({"QT_QPA_PLATFORM": "offscreen", "QT_QPA_FONTDIR": str(FONT_DIR), "QT_SCALE_FACTOR": "1",
                "APPDATA": str(tmp_path), "PYTHONPATH": str(SRC_DIR), "PYTHONIOENCODING": "utf-8"})
    proc = subprocess.run([sys.executable, "-c", _PROBE], env=env, capture_output=True, text=True,
                          encoding="utf-8", timeout=120)
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT")]
    assert lines, f"probe failed ({proc.returncode}):\n{proc.stdout[-2000:]}\n{proc.stderr[-4000:]}"
    for edge, strip, old_floor, h, first, last in json.loads(lines[-1][len("RESULT"):]):
        if strip == 32:
            assert old_floor > 32, "precondition: #221's floor overhung the 32 px bar (34 px live)"
            assert h == 32, (edge, h)
        else:
            assert h == 48, (edge, h)                   # Default bar: pixel-identical
        assert 0 <= first and last <= h - 1, (edge, strip, h, first, last)   # no glyph cropped
