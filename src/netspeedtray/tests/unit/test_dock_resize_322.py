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
from netspeedtray.utils import taskbar_utils
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



def _fake_widget(sized_from):
    """Just enough of NetworkSpeedWidget to drive its real update_position()/taskbar slot.

    ``sized_from``: the taskbar the startup layout sized against (None = a fallback taskbar or a
    free-float). The fake layout records ``dock_signature`` of the taskbar it re-sizes against, as
    ``resize_widget_for_font`` does.
    """
    w = types.SimpleNamespace(
        config={}, logger=MagicMock(), layout_manager=MagicMock(), position_manager=MagicMock(),
        _dock_sig_seen=None, _refresh_cached_layout_mode=lambda: None, current=None)
    w.position_manager.is_free_float_active.return_value = False
    w.update_position = types.MethodType(widget_main.NetworkSpeedWidget.update_position, w)
    resize_check = getattr(widget_main.NetworkSpeedWidget, "_resize_if_dock_changed", None)
    if resize_check is not None:
        w._resize_if_dock_changed = types.MethodType(resize_check, w)
    sig = getattr(taskbar_utils, "dock_signature", lambda tb: None)
    w._dock_sig_seen = sig(sized_from) if sized_from is not None else None

    def _layout_resize():
        w._dock_sig_seen = sig(w.current)
    w.layout_manager.resize_widget_for_font.side_effect = _layout_resize
    return w


def _feed(w, tb) -> bool:
    """One taskbar-changed event with ``tb`` live; True if it re-sized."""
    before = w.layout_manager.resize_widget_for_font.call_count
    w.current = tb
    with patch.object(widget_main, "get_taskbar_info", return_value=tb):
        w.update_position()
    return w.layout_manager.resize_widget_for_font.call_count > before


def _resizes_for(sequence, sized_from="first") -> list:
    """Feed taskbar states through the taskbar-changed slot; True per step that re-sized. By default
    the startup layout sized against the first state."""
    w = _fake_widget(sequence[0] if sized_from == "first" else sized_from)
    return [_feed(w, tb) for tb in sequence]


def _fallback_taskbar() -> MagicMock:
    tb = _taskbar(BOTTOM, 48)
    tb.hwnd, tb.rect = 0, (0, 0, 0, 0)
    return tb


# --- 1. Live re-size on a dock-geometry change ---------------------------------------------------

@pytest.mark.parametrize("edge", [TOP, BOTTOM])
@pytest.mark.parametrize("first,second", [(48, 32), (32, 48)])
def test_thickness_change_resizes(edge, first, second):
    assert _resizes_for([_taskbar(edge, first), _taskbar(edge, second)]) == [False, True]


@pytest.mark.parametrize("edge", [TOP, BOTTOM])
def test_same_size_move_does_not_resize(edge):
    # Tray moved (x changes) and an auto-hide slide (rect shifts): same strip, so reposition only.
    seq = [_taskbar(edge, 48), _taskbar(edge, 48, tray_x=1500), _taskbar(edge, 48, slide=20)]
    w = _fake_widget(seq[0])
    assert [_feed(w, tb) for tb in seq] == [False, False, False]
    assert w.position_manager.update_position.call_count == 3   # still repositioned every time


def test_edge_change_bottom_to_left_resizes():
    assert _resizes_for([_taskbar(BOTTOM, 48), _taskbar(LEFT, 48)]) == [False, True]


def test_resize_is_not_followed_by_a_second_reposition_or_a_loop():
    # The resize ends in widget.update_position() itself (layout.py); that nested call sees the new
    # signature and only repositions - no second resize.
    tb48, tb32 = _taskbar(TOP, 48), _taskbar(TOP, 32)
    w = _fake_widget(tb48)
    record = w.layout_manager.resize_widget_for_font.side_effect

    def _resize_then_reposition():
        record()
        w.update_position()
    w.layout_manager.resize_widget_for_font.side_effect = _resize_then_reposition
    assert [_feed(w, tb48), _feed(w, tb32), _feed(w, tb32)] == [False, True, False]
    assert w.layout_manager.resize_widget_for_font.call_count == 1
    assert w.position_manager.update_position.call_count == 3   # 48, the resize's own, 32 again


def test_a_fallback_taskbar_mid_run_does_not_resize():
    real = _taskbar(TOP, 48)
    assert _resizes_for([real, _fallback_taskbar(), _taskbar(TOP, 48)]) == [False, False, False]


@pytest.mark.parametrize("edge,thickness", [(BOTTOM, 48), (BOTTOM, 32), (TOP, 32)])
def test_sized_from_a_fallback_taskbar_resizes_when_the_real_one_appears(edge, thickness):
    # Started before Explorer's taskbar was ready (logon autostart / explorer restart): the layout
    # sized against the fallback (rect 0,0,0,0 -> read as a LEFT bar, a 50 px two-line widget).
    seq = [_fallback_taskbar(), _taskbar(edge, thickness), _taskbar(edge, thickness)]
    assert _resizes_for(seq, sized_from=None) == [False, True, False]


def test_free_float_off_resizes_to_the_taskbar():
    # Sized while free-floating (#188: no signature recorded); float turns off with the same bar.
    tb = _taskbar(BOTTOM, 48)
    w = _fake_widget(None)
    w.position_manager.is_free_float_active.return_value = True
    assert _feed(w, tb) is False                    # floating: never sized to a taskbar
    w.position_manager.is_free_float_active.return_value = False
    assert [_feed(w, tb), _feed(w, tb)] == [True, False]


def test_layout_sizes_from_the_preferred_monitor_taskbar_and_records_it():
    # Both sides must read the same taskbar, or a secondary preferred monitor re-sizes every tick.
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG, preferred_monitor="\\\\.\\DISPLAY2",
               free_float=False)
    widget = MagicMock()
    widget.config, widget.i18n = cfg, I18nStrings("en_US")
    widget._identity_reserve_px = lambda metrics: 0
    lm = WidgetLayoutManager(widget)
    lm.set_font(resize=False)
    tb = _taskbar(BOTTOM, 32)
    with patch("netspeedtray.views.widget.layout.get_taskbar_info", return_value=tb) as get:
        lm.resize_widget_for_font()
    assert get.call_args.kwargs.get("preferred_screen_name") == "\\\\.\\DISPLAY2"
    assert widget._dock_sig_seen == taskbar_utils.dock_signature(tb)


def test_layout_records_none_while_free_floating():
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG, preferred_monitor="ACCESSORY", free_float=True)
    widget = MagicMock()
    widget.config, widget.i18n = cfg, I18nStrings("en_US")
    widget._identity_reserve_px = lambda metrics: 0
    widget._dock_sig_seen = ("stale",)
    lm = WidgetLayoutManager(widget)
    lm.set_font(resize=False)
    with patch("netspeedtray.views.widget.layout.get_taskbar_info", return_value=_taskbar(BOTTOM, 48)), \
            patch("netspeedtray.utils.taskbar_utils.get_free_float_screen", return_value=MagicMock()):
        lm.resize_widget_for_font()
    assert widget._dock_sig_seen is None


# --- 2. Fit a strip thinner than the font's two line boxes ----------------------------------------

def _renderer_and_layout(lang: str = "en_US", **overrides):
    """The shipped defaults (font included) through the REAL layout font path and the real renderer."""
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG, **overrides)
    i18n = I18nStrings(lang)
    r = WidgetRenderer(cfg, i18n)
    widget = MagicMock()
    widget.config, widget.i18n = cfg, i18n
    lm = WidgetLayoutManager(widget)
    lm.set_font(resize=False)
    assert lm.metrics.height() == r.metrics.height()   # layout and paint measure the same font
    return r, lm


def _ink_rows(r: WidgetRenderer, height: int, dpr: float = 1.0, width: int = 460, pad: int = 20):
    """Paint the docked readout (network rows + CPU/GPU with temps, power, RAM/VRAM, side by side)
    into a ``height`` px band of a taller canvas at device pixel ratio ``dpr``; return (first, last)
    painted DEVICE row relative to the band, and the band's last device row."""
    cfg = r.config
    cfg.default_color = "#FFFFFF"
    img = QImage(round(width * dpr), round((height + 2 * pad) * dpr), QImage.Format.Format_ARGB32)
    img.setDevicePixelRatio(dpr)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.translate(0, pad)
    r.draw_network_speeds(p, 123456.0, 9876543.0, 160, height, cfg, layout_mode="horizontal")
    # What the owner's 34 px screenshot showed: a CPU row with temp + power + RAM, a GPU row "(N/A)".
    r.draw_hardware_stats(p, 11.0, 12.0, 260, height, cfg, cpu_temp=59.0, gpu_temp=None,
                          ram_info=(9.2, 15.7), vram_info=(1.7, 6.0), layout_mode="horizontal",
                          x_offset=165, cpu_power=16.1)
    p.end()
    rows = [y for y in range(img.height())
            if any((img.pixel(x, y) >> 24) & 0xFF for x in range(img.width()))]
    top = round(pad * dpr)
    return rows[0] - top, rows[-1] - top, int(height * dpr) - 1


# The owner's config from the live #322 test: separate DemiBold arrow font, side_by_side with CPU/GPU,
# RAM/VRAM + labels, temps and power, monochrome icons, always-Mbps long labels.
OWNER_CONFIG = dict(
    font_family="Segoe UI", font_size=9, font_weight=400, use_separate_arrow_font=True,
    arrow_font_family="Segoe UI", arrow_font_size=9, arrow_font_weight=600,
    widget_display_mode="side_by_side", widget_display_order=["network", "cpu", "gpu"],
    monitor_cpu_enabled=True, monitor_gpu_enabled=True, monitor_ram_enabled=True,
    monitor_vram_enabled=True, show_memory_labels=True, hardware_label_style="icons_monochrome",
    show_hardware_temps=True, show_hardware_power=True, graph_enabled=True,
    speed_display_mode="always_mbps", short_unit_labels=False, show_network_identity=False)

# Review cases (#322): a small font whose CPU/GPU icon is the tallest thing painted, and Korean byte
# units ("초" from a fallback font) in a bold 11 at 125%.
SMALL_ICON_FONTS = [dict(font_family="Calibri", font_size=8), dict(font_family="Cascadia Mono", font_size=8)]
KO_BOLD_11 = dict(font_family="Calibri", font_size=11, font_weight=700, unit_type="bytes_decimal")


def _measure(strip: int, edge=TOP, overrides=None, lang: str = "en_US", dpr: float = 1.0):
    """(old #221 floor, docked height for ``strip``, first ink row, last ink row, band's last row)."""
    r, lm = _renderer_and_layout(lang, **(overrides or {}))
    h = int(lm._horizontal_dock_height(edge, _taskbar(edge, strip), 1.0))
    return (lm.metrics.height() * 2 + 2, h, *_ink_rows(r, h, dpr))


# In-process: the suite's offscreen platform has no font directory, so these run on Qt's fallback
# font - the claims below hold for any font.

@pytest.mark.parametrize("overrides", [{}, OWNER_CONFIG], ids=["defaults", "owner"])
@pytest.mark.parametrize("below", [1, 2, 3, 4, 6])
def test_thin_strip_never_crops_and_never_exceeds_the_221_floor(below, overrides):
    r, lm = _renderer_and_layout(**overrides)
    old = lm.metrics.height() * 2 + 2
    strip = old - below
    h = int(lm._horizontal_dock_height(TOP, _taskbar(TOP, strip), 1.0))
    assert strip <= h <= old
    for dpr in (1.0, 1.25, 1.5):
        first, last, limit = _ink_rows(r, h, dpr)
        assert 0 <= first and last <= limit, (strip, h, dpr, first, last, limit)


def test_strip_too_thin_for_the_ink_keeps_the_221_floor():
    _, lm = _renderer_and_layout()
    assert lm._horizontal_dock_height(TOP, _taskbar(TOP, 10), 1.0) == lm.metrics.height() * 2 + 2


@pytest.mark.parametrize("edge", [TOP, BOTTOM])
def test_default_48px_strip_unchanged(edge):
    _, lm = _renderer_and_layout()
    assert lm._horizontal_dock_height(edge, _taskbar(edge, 48), 1.0) == 48


def test_metrics_model_counts_the_full_icon_height():
    # The fallback model (no font object): the icon's antialiased stroke reaches HW_ICON_SIZE_PX
    # above the baseline, not one less.
    from PyQt6.QtGui import QFont, QFontMetrics
    lm = WidgetLayoutManager(MagicMock())
    lm.metrics = lm.arrow_metrics = QFontMetrics(QFont("Segoe UI", 6))   # text shorter than the icon
    lm.widget.config = {}
    assert -lm.metrics.tightBoundingRect("0123456789").top() < constants.renderer.HW_ICON_SIZE_PX
    assert lm._ink_extents()[0] >= constants.renderer.HW_ICON_SIZE_PX


# Real Windows fonts, in a subprocess on Qt's NATIVE "windows" platform - the DirectWrite engine the
# app paints with. (The offscreen platform pointed at C:\Windows\Fonts uses FreeType and measured some
# glyph runs 1 px shallower, so the 34 px the owner saw live passed there.) Only QImages are painted;
# no window is shown.

_PROBE = r"""
import importlib, json, sys
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)
t = importlib.import_module("netspeedtray.tests.unit.test_dock_resize_322")
rows = []
for name, cfg in (("defaults", {}), ("owner", t.OWNER_CONFIG)):
    for e in (t.TOP, t.BOTTOM):
        for s in (32, 48):
            for dpr in (1.0, 1.25, 1.5, 2.0):
                rows.append([name, e.name, s, dpr, *t._measure(s, e, cfg, dpr=dpr)])
for cfg in t.SMALL_ICON_FONTS:
    for dpr in (1.0, 1.25, 1.5):
        rows.append([cfg["font_family"] + " 8", "TOP", 26, dpr, *t._measure(26, t.TOP, cfg, dpr=dpr)])
for dpr in (1.0, 1.25, 1.5):
    rows.append(["ko_KR bold 11", "TOP", 32, dpr, *t._measure(32, t.TOP, t.KO_BOLD_11, "ko_KR", dpr)])
print("RESULT" + json.dumps(rows), flush=True)
"""


@pytest.mark.skipif(sys.platform != "win32" or not FONT_DIR.is_dir(),
                    reason="needs Windows and its fonts for real text metrics")
def test_thin_bars_fit_with_real_fonts(tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in ("QT_QPA_PLATFORM", "QT_QPA_FONTDIR")}
    env.update({"QT_QPA_PLATFORM": "windows", "QT_SCALE_FACTOR": "1", "APPDATA": str(tmp_path),
                "PYTHONPATH": str(SRC_DIR), "PYTHONIOENCODING": "utf-8"})
    proc = subprocess.run([sys.executable, "-c", _PROBE], env=env, capture_output=True, text=True,
                          encoding="utf-8", timeout=180)
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT")]
    assert lines, f"probe failed ({proc.returncode}):\n{proc.stdout[-2000:]}\n{proc.stderr[-4000:]}"
    for case, edge, strip, dpr, old_floor, h, first, last, limit in json.loads(lines[-1][len("RESULT"):]):
        where = (case, edge, strip, dpr, h, first, last, limit)
        if case in ("defaults", "owner"):
            if strip == 32:
                assert old_floor > 32, "precondition: #221's floor overhung the 32 px bar (34 px live)"
                assert h == 32, where                   # the owner's Small bar: fits exactly
            else:
                assert h == 48, where                   # Default bar: pixel-identical
        assert h <= max(strip, old_floor), where
        assert 0 <= first and last <= limit, where      # nothing cropped, at any scale
