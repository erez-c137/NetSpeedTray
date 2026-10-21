"""
"Show hardware only when hovering the widget" (``hardware_in_hover_card``; side-taskbar design phase C).

Owner request (2026-10-21): offer the side-bar hover card on TOP/BOTTOM bars too, so the taskbar widget
can stay a clean speed readout and CPU/GPU/memory are one hover away. One opt-in switch (default off):

- While it is ACTIVE (on + CPU or GPU monitoring on - RAM/VRAM ride on those lines) the widget sizes
  and paints exactly as its network-only form for whatever Display Mode is saved, on every edge. One
  helper decides it (``helpers.effective_display_mode``), read by RenderConfig (paint + Settings
  preview), the layout manager (sizing) and the Auto-Cycle timer.
- The saved ``widget_display_mode`` is never rewritten: turning the switch off brings the old layout back.
- The hover card's hardware section opens on any edge (``_hover_hardware_wanted``) and lists what the
  user's OWN Display Mode would show (side_by_side keeps its order and "None" slots; network_only /
  Auto-Cycle list every enabled monitor), with temps/power per their switches.
- Collection is untouched: it is gated by monitor_cpu/gpu_enabled only, never by the display mode.
"""
import hashlib
import json
import os
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QRect
from PyQt6.QtGui import QColor, QFontMetrics, QImage, QPainter

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils.helpers import effective_display_mode, hardware_in_hover_card_active
from netspeedtray.utils.taskbar_utils import TaskbarInfo
from netspeedtray.utils.widget_paint import WidgetMetrics, font_from_config, render_widget
from netspeedtray.utils.widget_renderer import RenderConfig, WidgetRenderer
from netspeedtray.views.widget.layout import WidgetLayoutManager

EN = I18nStrings("en_US")
TOP, BOTTOM = constants.TaskbarEdge.TOP, constants.TaskbarEdge.BOTTOM
LEFT, RIGHT = constants.TaskbarEdge.LEFT, constants.TaskbarEdge.RIGHT
SRC_DIR = Path(__file__).resolve().parents[3]
REPO_DIR = SRC_DIR.parent

METRICS = WidgetMetrics(upload_mbps=12.4, download_mbps=88.6, cpu_usage=37.0, gpu_usage=52.0,
                        cpu_temp=58.0, gpu_temp=64.0, cpu_power=35.0, gpu_power=120.0,
                        ram_used=9.2, ram_total=16.0, vram_used=3.1, vram_total=8.0)

HW = dict(monitor_cpu_enabled=True, monitor_gpu_enabled=True, monitor_ram_enabled=True,
          monitor_vram_enabled=True, show_hardware_temps=True, show_hardware_power=True)

# Every saved Display Mode (and the internal Auto-Cycle phases a widget can be painting), with the
# graph options that change the network-only form (mini graph, separate upload/download graphs).
MODES = {
    "side_by_side": dict(widget_display_mode="side_by_side", graph_enabled=True),
    "side_by_stack": dict(widget_display_mode="side_by_side", stack_hardware_stats=True),
    "side_by_side_split": dict(widget_display_mode="side_by_side", graph_enabled=True,
                               graph_split_directions=True),
    "cycle": dict(widget_display_mode="cycle", graph_enabled=True),
    "cycle_split": dict(widget_display_mode="cycle", graph_enabled=True, graph_split_directions=True),
    "cpu_only": dict(widget_display_mode="cpu_only"),
    "gpu_only": dict(widget_display_mode="gpu_only"),
    "combined": dict(widget_display_mode="combined"),
    "network_only": dict(widget_display_mode="network_only", graph_enabled=True),
}
CYCLE_PHASES = ("network_only", "cpu_only", "gpu_only", "network_upload", "network_download")


def _config(**overrides):
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    cfg.update({"free_float": False, "graph_enabled": False, "background_opacity": 0,
                "hardware_label_style": "icons"})
    cfg.update(overrides)
    return cfg


def _taskbar(edge, thickness=48, *, screen=QRect(0, 0, 1920, 1080)):
    """A docked taskbar ``thickness`` logical px thick on ``edge`` of ``screen`` (100% DPI)."""
    x, y, w, h = screen.x(), screen.y(), screen.width(), screen.height()
    if edge == BOTTOM:
        avail, rect = QRect(x, y, w, h - thickness), (x, y + h - thickness, x + w, y + h)
    elif edge == TOP:
        avail, rect = QRect(x, y + thickness, w, h - thickness), (x, y, x + w, y + thickness)
    elif edge == RIGHT:
        avail, rect = QRect(x, y, w - thickness, h), (x + w - thickness, y, x + w, y + h)
    else:
        avail, rect = QRect(x + thickness, y, w - thickness, h), (x, y, x + thickness, y + h)
    scr = MagicMock()
    scr.geometry.return_value = screen
    scr.availableGeometry.return_value = avail
    tb = MagicMock(spec=TaskbarInfo)
    tb.rect, tb.hwnd, tb.dpi_scale = rect, 12345, 1.0
    tb.height = thickness
    tb.tasklist_rect = None
    tb.get_edge_position.return_value = edge
    tb.get_screen.return_value = scr
    tb.get_tray_rect.return_value = (rect[2] - 200, rect[1], rect[2], rect[3])
    return tb


def _size(cfg, tb):
    """(width, height, network width, side fit) the real WidgetLayoutManager gives the widget."""
    widget = MagicMock()
    widget.config = cfg
    widget.i18n = EN
    widget._identity_reserve_px.return_value = 0
    widget.ram_total, widget.vram_total = 16.0, 8.0
    widget.ram_used, widget.vram_used = 9.2, 3.1
    lm = WidgetLayoutManager(widget)
    lm.metrics = QFontMetrics(font_from_config(cfg))
    lm.arrow_metrics = lm.metrics
    with patch("netspeedtray.views.widget.layout.get_taskbar_info", return_value=tb), \
            patch("netspeedtray.views.widget.layout.is_small_taskbar", return_value=False), \
            patch("netspeedtray.utils.taskbar_utils.get_free_float_screen", return_value=None):
        lm.resize_widget_for_font()
    (w, h), _ = widget.setFixedSize.call_args
    return w, h, lm._network_width, lm._side_fit


def _render(cfg, tb, cycle_mode="network_only") -> QImage:
    """The widget exactly as the live paintEvent draws it at the size the layout gave it."""
    w, h, net_w, _fit = _size(cfg, tb)
    layout = "vertical" if tb.get_edge_position() in (LEFT, RIGHT) else "horizontal"
    renderer = WidgetRenderer(cfg, EN)
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    render_widget(p, QRect(0, 0, w, h), renderer, renderer.config, METRICS, layout_mode=layout,
                  cycle_mode=cycle_mode, network_width=net_w, font=font_from_config(cfg))
    p.end()
    return img


EDGES = [(BOTTOM, 48), (TOP, 48), (RIGHT, 48), (LEFT, 32)]


# --- 1. Config -------------------------------------------------------------------------------------

def test_default_is_off_and_validated_as_a_bool(tmp_path):
    from netspeedtray.utils.config import ConfigManager
    d = constants.config.defaults
    assert d.DEFAULT_HARDWARE_IN_HOVER_CARD is False
    assert d.DEFAULT_CONFIG["hardware_in_hover_card"] is False
    assert d.VALIDATION_SCHEMA["hardware_in_hover_card"] == {"type": bool, "default": False}
    cm = ConfigManager(tmp_path / "c.json")
    with patch.object(cm.logger, "warning"):
        assert cm._validate_config({"hardware_in_hover_card": "yes"})["hardware_in_hover_card"] is False
    assert cm._validate_config({"hardware_in_hover_card": True})["hardware_in_hover_card"] is True
    assert cm._validate_config({})["hardware_in_hover_card"] is False


def test_effective_mode_collapses_only_while_active():
    for mode in ("side_by_side", "cycle", "network_only", "cpu_only", "gpu_only", "combined"):
        assert effective_display_mode({"widget_display_mode": mode}) == mode
        assert effective_display_mode({"widget_display_mode": mode, "hardware_in_hover_card": False,
                                       **HW}) == mode
        assert effective_display_mode({"widget_display_mode": mode, "hardware_in_hover_card": True,
                                       "monitor_gpu_enabled": True}) == "network_only"
        # On, but no hardware to move: inert - the widget keeps its Display Mode.
        assert effective_display_mode({"widget_display_mode": mode, "hardware_in_hover_card": True,
                                       "monitor_ram_enabled": True, "monitor_vram_enabled": True}) == mode
    assert hardware_in_hover_card_active({"hardware_in_hover_card": True, "monitor_cpu_enabled": True})
    assert not hardware_in_hover_card_active({"hardware_in_hover_card": True})
    assert not hardware_in_hover_card_active(None)
    # The saved mode is the user's: RenderConfig paints network_only, the dict keeps side_by_side.
    cfg = _config(hardware_in_hover_card=True, widget_display_mode="side_by_side", **HW)
    assert RenderConfig.from_dict(cfg).widget_display_mode == "network_only"
    assert cfg["widget_display_mode"] == "side_by_side"


# --- 2. With the switch on, the widget IS the network-only widget ----------------------------------

@pytest.mark.parametrize("edge,thickness", EDGES)
@pytest.mark.parametrize("mode", list(MODES))
def test_switch_on_sizes_and_paints_as_the_network_only_widget(q_app, mode, edge, thickness):
    over = MODES[mode]
    on = _config(hardware_in_hover_card=True, **HW, **over)
    network_only = _config(**HW, **{**over, "widget_display_mode": "network_only"})
    tb = _taskbar(edge, thickness)
    assert _size(on, tb)[:3] == _size(network_only, tb)[:3]
    phases = CYCLE_PHASES if over["widget_display_mode"] == "cycle" else ("network_only",)
    for phase in phases:            # a cycle phase left over from before the switch paints nothing new
        assert _render(on, tb, phase) == _render(network_only, tb, "network_only"), (mode, phase)


@pytest.mark.parametrize("edge,thickness", [(BOTTOM, 48), (TOP, 48)])
def test_switch_on_actually_drops_the_hardware_from_a_horizontal_bar(q_app, edge, thickness):
    """Guards the test above against comparing two identical things: off, side_by_side is wider and
    paints differently; on, it is the clean readout."""
    tb = _taskbar(edge, thickness)
    off = _config(**HW, **MODES["side_by_side"])
    on = dict(off, hardware_in_hover_card=True)
    assert _size(on, tb)[0] < _size(off, tb)[0]
    assert _render(on, tb) != _render(off, tb)


# --- 3. The hover card ----------------------------------------------------------------------------

def _hover_fake(layout="horizontal", fit=None, **config):
    from netspeedtray.views.widget.main import NetworkSpeedWidget as W
    cfg = {"show_usage_on_hover": False, "show_hover_tips": False, **config}
    fake = types.SimpleNamespace(
        config=cfg, _cached_layout_mode=layout, layout_manager=types.SimpleNamespace(_side_fit=fit),
        cpu_usage=37.0, gpu_usage=52.0, cpu_temp=58.0, gpu_temp=64.0, cpu_power=35.0, gpu_power=120.0,
        ram_used=9.2, ram_total=16.0, vram_used=3.1, vram_total=8.0, network_identity=None,
        logger=MagicMock(), _hover_card=None, is_paused=False, update=MagicMock(),
        _HOVER_HW_REFRESH_SEC=W._HOVER_HW_REFRESH_SEC)
    for name in ("_side_bar_fit", "_hover_hardware_wanted", "_hover_hardware_snapshot",
                 "_identity_presentation", "_identity_ssid_text", "_refresh_hover_hardware",
                 "_bar_shows", "update_cpu_usage", "update_gpu_usage", "update_ram_info",
                 "update_vram_info", "update_cpu_temp", "update_gpu_power"):
        setattr(fake, name, types.MethodType(getattr(W, name), fake))
    return fake


@pytest.mark.parametrize("layout", ["horizontal", "vertical"])
def test_hover_gate_opens_on_any_edge_with_the_switch_and_hardware_on(q_app, layout):
    assert _hover_fake(layout, hardware_in_hover_card=True, monitor_cpu_enabled=True)._hover_hardware_wanted()
    assert _hover_fake(layout, hardware_in_hover_card=True, monitor_gpu_enabled=True)._hover_hardware_wanted()
    # No CPU/GPU monitoring: nothing to show, the gate stays shut (RAM/VRAM need their host line).
    assert not _hover_fake(layout, hardware_in_hover_card=True, monitor_ram_enabled=True,
                           monitor_vram_enabled=True)._hover_hardware_wanted()
    # Switch off: a horizontal bar never opens it (the hardware is on the widget).
    assert not _hover_fake("horizontal", hardware_in_hover_card=False, **HW)._hover_hardware_wanted()


def test_card_carries_everything_enabled_and_honours_the_detail_switches(q_app):
    full = _hover_fake(hardware_in_hover_card=True, widget_display_mode="side_by_side", **HW)
    snap = full._hover_hardware_snapshot()
    assert snap == {"cpu": {"usage": 37.0, "temp": 58.0, "power": 35.0},
                    "gpu": {"usage": 52.0, "temp": 64.0, "power": 120.0},
                    "ram": (9.2, 16.0), "vram": (3.1, 8.0)}

    bare = _hover_fake(hardware_in_hover_card=True, widget_display_mode="network_only",
                       **{**HW, "show_hardware_temps": False, "show_hardware_power": False,
                          "monitor_ram_enabled": False, "monitor_vram_enabled": False})
    assert bare._hover_hardware_snapshot() == {"cpu": {"usage": 37.0}, "gpu": {"usage": 52.0}}

    # RAM rides on CPU, VRAM on GPU: GPU off drops the VRAM row even with its switch on.
    cpu_only = _hover_fake(hardware_in_hover_card=True, widget_display_mode="cycle",
                           **{**HW, "monitor_gpu_enabled": False, "show_hardware_power": False})
    assert cpu_only._hover_hardware_snapshot() == {"cpu": {"usage": 37.0, "temp": 58.0},
                                                   "ram": (9.2, 16.0)}

    # side_by_side keeps the user's Display order - a "None" slot stays out of the card too.
    order = _hover_fake(hardware_in_hover_card=True, widget_display_mode="side_by_side",
                        widget_display_order=["network", "none", "gpu"], **HW)
    assert set(order._hover_hardware_snapshot()) == {"gpu", "vram"}


def test_card_is_built_with_the_hardware_section(q_app):
    from PyQt6.QtWidgets import QLabel
    from netspeedtray.views.usage_flyout import UsageFlyout
    snap = _hover_fake(hardware_in_hover_card=True, widget_display_mode="side_by_side",
                       **HW)._hover_hardware_snapshot()
    card = UsageFlyout(EN, None, None, hardware=snap)
    try:
        texts = [lbl.text() for lbl in card.findChildren(QLabel)]
        for t in ("CPU", "GPU", "RAM", "VRAM", "37%", "52%", "58°C", "64°C"):
            assert t in texts, (t, texts)
        assert card.has_hardware
    finally:
        card.deleteLater()


def test_open_card_closes_when_the_switch_is_turned_off(q_app):
    fake = _hover_fake(hardware_in_hover_card=True, widget_display_mode="side_by_side", **HW)
    card = MagicMock(has_hardware=True)
    fake._hover_card, fake._hover_hw_refreshed = card, 0.0
    fake._hide_usage_hover_card = MagicMock()
    fake._refresh_hover_hardware()
    card.update_hardware.assert_called_once()
    fake._hover_hw_refreshed = 0.0
    fake.config["hardware_in_hover_card"] = False
    fake._refresh_hover_hardware()
    fake._hide_usage_hover_card.assert_called_once()


def test_hover_arms_the_card_on_a_horizontal_bar_with_usage_and_tips_off(q_app):
    from PyQt6.QtCore import QPointF, QTimer
    from PyQt6.QtGui import QEnterEvent
    from PyQt6.QtWidgets import QWidget
    from netspeedtray.views.widget.main import NetworkSpeedWidget

    class _Armable(NetworkSpeedWidget):
        def __init__(self, on):
            QWidget.__init__(self)
            self.config = {"show_usage_on_hover": False, "show_hover_tips": False, **HW,
                           "widget_display_mode": "side_by_side", "hardware_in_hover_card": on}
            self._cached_layout_mode = "horizontal"
            self.layout_manager = types.SimpleNamespace(_side_fit=None)
            self._hover_card = None
            self._hover_card_timer = QTimer()
            self._hover_card_timer.setSingleShot(True)
            self.logger = MagicMock()

    event = QEnterEvent(QPointF(1, 1), QPointF(1, 1), QPointF(1, 1))
    on, off = _Armable(True), _Armable(False)
    try:
        on.enterEvent(event)
        off.enterEvent(event)
        assert on._hover_card_timer.isActive()
        assert on._hover_card_timer.interval() == NetworkSpeedWidget._HOVER_CARD_DELAY_MS   # 350 ms rest
        assert not off._hover_card_timer.isActive()
    finally:
        for w in (on, off):
            w._hover_card_timer.stop()
            w.deleteLater()


SECOND = QRect(1920, -200, 2560, 1440)       # a monitor right of the primary, top above it
LEFT_OF = QRect(-1920, 0, 1920, 1080)        # a monitor left of the primary (negative x)


@pytest.mark.parametrize("screen", [QRect(0, 0, 1920, 1080), SECOND, LEFT_OF])
@pytest.mark.parametrize("edge", [BOTTOM, TOP])
@pytest.mark.parametrize("where", ["left_end", "right_end", "middle"])
def test_card_on_a_horizontal_bar_clamps_inside_the_widgets_work_area(q_app, screen, edge, where):
    """Above a bottom bar, below a top bar; a widget at either end of the bar keeps the card on its own
    monitor's work area (never onto the neighbour monitor, never over the taskbar)."""
    from netspeedtray.views.usage_flyout import UsageFlyout
    tb = _taskbar(edge, 48, screen=screen)
    avail = tb.get_screen().availableGeometry()
    bar_y = tb.rect[1]
    x = {"left_end": screen.left(), "right_end": screen.right() - 120, "middle": screen.center().x()}[where]
    widget = QRect(x, bar_y, 121, 48)
    snap = _hover_fake(hardware_in_hover_card=True, widget_display_mode="side_by_side",
                       **HW)._hover_hardware_snapshot()
    card = UsageFlyout(EN, (1e9, 2e9), (3e9, 4e9), hardware=snap)
    try:
        card.show_for(widget, avail, edge=edge)
        g = card.geometry()
        assert avail.contains(g), (g, avail)
        assert not g.intersects(widget)
        if edge == BOTTOM:
            assert g.bottom() < widget.top()
        else:
            assert g.top() > widget.bottom()
        if where == "middle":
            assert abs(g.center().x() - widget.center().x()) <= 1
    finally:
        card.hide()
        card.deleteLater()


# --- 4. Collection keeps running ------------------------------------------------------------------

def test_hardware_collection_ignores_the_switch_and_the_display_mode():
    from netspeedtray.core.monitor_thread import hardware_collection_gates
    for mode in ("network_only", "side_by_side", "cycle"):
        cfg = {"widget_display_mode": mode, "hardware_in_hover_card": True, **HW}
        assert hardware_collection_gates(cfg, False) == (True, True)


def test_widget_keeps_every_reading_but_stops_repainting_for_hardware_it_hides(q_app):
    on = _hover_fake(hardware_in_hover_card=True, widget_display_mode="side_by_side", **HW)
    on._refresh_hover_hardware = MagicMock()
    on.update_cpu_usage(81.0)
    on.update_gpu_usage(12.0)
    on.update_ram_info(10.0, 16.0)
    on.update_vram_info(4.0, 8.0)
    on.update_cpu_temp(70.0)
    on.update_gpu_power(99.0)
    assert (on.cpu_usage, on.gpu_usage, on.ram_used, on.vram_used, on.cpu_temp, on.gpu_power) == \
        (81.0, 12.0, 10.0, 4.0, 70.0, 99.0)
    assert on._refresh_hover_hardware.call_count == 3           # the open card follows CPU/GPU/RAM
    on.update.assert_not_called()                                # ... but the widget has nothing new
    off = _hover_fake(hardware_in_hover_card=False, widget_display_mode="side_by_side", **HW)
    off._refresh_hover_hardware = MagicMock()
    off.update_cpu_usage(81.0)
    off.update.assert_called_once()


def _controller_widget(cfg):
    w = MagicMock()
    w.config = cfg
    w._cycle_timer.isActive.return_value = True
    return w


def test_settings_apply_resizes_live_and_stops_the_cycle_timer(q_app):
    from netspeedtray.core.config_controller import ConfigController
    w = _controller_widget(_config(widget_display_mode="cycle", hardware_in_hover_card=True, **HW))
    cc = ConfigController(w, MagicMock())
    cc._apply_keep_on_top = MagicMock()
    cc.apply_all_settings()
    w.layout_manager.set_font.assert_called_once_with(resize=True)   # live resize, no restart
    w._cycle_timer.stop.assert_called_once()
    w2 = _controller_widget(_config(widget_display_mode="cycle", hardware_in_hover_card=False, **HW))
    w2._cycle_timer.isActive.return_value = False
    cc2 = ConfigController(w2, MagicMock())
    cc2._apply_keep_on_top = MagicMock()
    cc2.apply_all_settings()
    w2._cycle_timer.start.assert_called_once()                       # switch off: Auto-Cycle is back


# --- 5. Switch off = today's widget ---------------------------------------------------------------

@pytest.mark.parametrize("edge,thickness", EDGES)
def test_switch_off_is_structurally_todays_widget(q_app, edge, thickness):
    """Portable half of the 'off is pixel-identical to main' check: with the switch off (or the key
    missing, as in a pre-2.2 config) the paint config and every size are what the mode alone gives."""
    tb = _taskbar(edge, thickness)
    for mode, over in MODES.items():
        off = _config(hardware_in_hover_card=False, **HW, **over)
        legacy = {k: v for k, v in off.items() if k != "hardware_in_hover_card"}
        assert RenderConfig.from_dict(off) == RenderConfig.from_dict(legacy)
        assert RenderConfig.from_dict(off).widget_display_mode == over["widget_display_mode"]
        assert _size(off, tb)[:3] == _size(legacy, tb)[:3], mode
        assert _render(off, tb) == _render(legacy, tb), mode


_HASH_SCRIPT = r'''
import hashlib, json, sys
sys.path.insert(0, sys.argv[1])
from unittest.mock import MagicMock, patch
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QRect
from PyQt6.QtGui import QColor, QFontMetrics, QImage, QPainter
app = QApplication([])
from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils.taskbar_utils import TaskbarInfo
from netspeedtray.utils.widget_paint import WidgetMetrics, font_from_config, render_widget
from netspeedtray.utils.widget_renderer import WidgetRenderer
from netspeedtray.views.widget.layout import WidgetLayoutManager
E = constants.TaskbarEdge
EN = I18nStrings("en_US")
M = WidgetMetrics(upload_mbps=12.4, download_mbps=88.6, cpu_usage=37.0, gpu_usage=52.0, cpu_temp=58.0,
                  gpu_temp=64.0, cpu_power=35.0, gpu_power=120.0, ram_used=9.2, ram_total=16.0,
                  vram_used=3.1, vram_total=8.0)
modes, hw, edges = json.loads(sys.argv[2])
def taskbar(edge, th):
    if edge == "BOTTOM": avail, rect = QRect(0, 0, 1920, 1080 - th), (0, 1080 - th, 1920, 1080)
    elif edge == "TOP": avail, rect = QRect(0, th, 1920, 1080 - th), (0, 0, 1920, th)
    elif edge == "RIGHT": avail, rect = QRect(0, 0, 1920 - th, 1080), (1920 - th, 0, 1920, 1080)
    else: avail, rect = QRect(th, 0, 1920 - th, 1080), (0, 0, th, 1080)
    scr = MagicMock(); scr.geometry.return_value = QRect(0, 0, 1920, 1080)
    scr.availableGeometry.return_value = avail
    tb = MagicMock(spec=TaskbarInfo); tb.rect, tb.hwnd, tb.dpi_scale, tb.height = rect, 1, 1.0, th
    tb.tasklist_rect = None; tb.get_edge_position.return_value = getattr(E, edge)
    tb.get_screen.return_value = scr; tb.get_tray_rect.return_value = (rect[2] - 200, rect[1], rect[2], rect[3])
    return tb
out = {}
for name, over in modes.items():
    for edge, th in edges:
        cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
        cfg.update({"free_float": False, "graph_enabled": False, "background_opacity": 0,
                    "hardware_label_style": "icons"}, **hw, **over)
        w = MagicMock(); w.config = cfg; w.i18n = EN; w._identity_reserve_px.return_value = 0
        w.ram_total, w.vram_total, w.ram_used, w.vram_used = 16.0, 8.0, 9.2, 3.1
        lm = WidgetLayoutManager(w); lm.metrics = QFontMetrics(font_from_config(cfg)); lm.arrow_metrics = lm.metrics
        tb = taskbar(edge, th)
        with patch("netspeedtray.views.widget.layout.get_taskbar_info", return_value=tb), \
                patch("netspeedtray.views.widget.layout.is_small_taskbar", return_value=False), \
                patch("netspeedtray.utils.taskbar_utils.get_free_float_screen", return_value=None):
            lm.resize_widget_for_font()
        (ww, hh), _ = w.setFixedSize.call_args
        for phase in ("network_only", "cpu_only", "gpu_only"):
            r = WidgetRenderer(cfg, EN)
            img = QImage(ww, hh, QImage.Format.Format_ARGB32); img.fill(QColor(0, 0, 0, 0))
            p = QPainter(img)
            render_widget(p, QRect(0, 0, ww, hh), r, r.config, M,
                          layout_mode="vertical" if edge in ("LEFT", "RIGHT") else "horizontal",
                          cycle_mode=phase, network_width=lm._network_width, font=font_from_config(cfg))
            p.end()
            out[f"{name}/{edge}{th}/{phase}"] = [ww, hh, hashlib.sha256(bytes(img.constBits().asstring(img.sizeInBytes()))).hexdigest()]
print(json.dumps(out))
'''


@pytest.mark.skipif(os.environ.get("NST_PIXEL_HASH") != "1",
                    reason="local only: raster hashes depend on the machine's fonts (set NST_PIXEL_HASH=1)")
def test_switch_off_is_pixel_identical_to_main(tmp_path):
    """Renders every mode x edge x cycle phase with the switch OFF on this branch and on origin/main
    (a git-archive export), in two subprocesses on the same machine, and compares size + raster hash."""
    main_dir = tmp_path / "main"
    main_dir.mkdir()
    archive = subprocess.run(["git", "-C", str(REPO_DIR), "archive", "origin/main", "src"],
                             capture_output=True, check=True).stdout
    tar = tmp_path / "main.tar"
    tar.write_bytes(archive)
    subprocess.run(["tar", "-xf", str(tar), "-C", str(main_dir)], check=True)
    args = json.dumps([MODES, HW, [[e.name, th] for e, th in EDGES]])
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}

    def hashes(src):
        res = subprocess.run([sys.executable, "-I", "-c", _HASH_SCRIPT, str(src), args],
                             capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=300)
        assert res.returncode == 0, res.stderr[-3000:]
        return json.loads(res.stdout.strip().splitlines()[-1])

    main, branch = hashes(main_dir / "src"), hashes(SRC_DIR)
    assert set(main) == set(branch) and len(main) == len(MODES) * len(EDGES) * 3
    diff = {k: (main[k], branch[k]) for k in main if main[k] != branch[k]}
    assert not diff, diff


# --- 6. Settings ----------------------------------------------------------------------------------

@pytest.fixture
def page(q_app):
    from netspeedtray.views.settings.pages.hardware import HardwarePage
    p = HardwarePage(EN, lambda: None)
    yield p
    p.deleteLater()


def test_settings_toggle_round_trips(page):
    page.load_settings({})
    assert page.get_settings()["hardware_in_hover_card"] is False
    page.load_settings({"hardware_in_hover_card": True, "monitor_cpu_enabled": True})
    assert page.get_settings()["hardware_in_hover_card"] is True
    page.load_settings({"hardware_in_hover_card": False, "monitor_cpu_enabled": True})
    assert page.get_settings()["hardware_in_hover_card"] is False


def test_settings_toggle_is_greyed_without_cpu_or_gpu(page):
    page.load_settings({"monitor_cpu_enabled": False, "monitor_gpu_enabled": False,
                        "monitor_ram_enabled": True})
    assert not page._hover_card_card.isEnabled()
    page.monitor_gpu.setChecked(True)
    assert page._hover_card_card.isEnabled()
    page.monitor_gpu.setChecked(False)
    page.monitor_cpu.setChecked(True)
    assert page._hover_card_card.isEnabled()
    page.monitor_cpu.setChecked(False)
    assert not page._hover_card_card.isEnabled()


def test_settings_toggle_reports_a_change(q_app):
    from netspeedtray.views.settings.pages.hardware import HardwarePage
    changes = []
    p = HardwarePage(EN, lambda: changes.append(1))
    try:
        p.load_settings({"monitor_cpu_enabled": True})
        changes.clear()
        p.hardware_in_hover_card.setChecked(True)
        assert changes                       # feeds the dialog's throttled live apply + preview
    finally:
        p.deleteLater()


@pytest.mark.parametrize("bar_width", [None, 400, 48])
def test_settings_preview_shows_the_clean_widget(q_app, bar_width):
    from netspeedtray.views.widget.preview import PreviewWidget
    over = MODES["side_by_side"]
    previews = {}
    for name, cfg in (("on", _config(hardware_in_hover_card=True, **HW, **over)),
                      ("network_only", _config(**HW, **{**over, "widget_display_mode": "network_only"})),
                      ("off", _config(**HW, **over))):
        pv = PreviewWidget(_config(), EN, width=120, height=40)
        pv.set_bar_width(bar_width)
        pv.set_config(cfg)
        previews[name] = pv
    try:
        on, net, off = (previews[k] for k in ("on", "network_only", "off"))
        assert on.size() == net.size()
        assert on.grab().toImage() == net.grab().toImage()
        if bar_width != 48:     # a native 48 px side bar already moves side_by_side's hardware to the card
            assert on.size() != off.size()      # the toggle really changes what the preview shows
    finally:
        for pv in previews.values():
            pv.deleteLater()


# --- 7. Auto-Cycle stays off; the note under Display Mode -----------------------------------------

def _timer_fake(config):
    from netspeedtray.views.widget.main import NetworkSpeedWidget as W
    fake = types.SimpleNamespace(config=config, i18n=EN, logger=MagicMock(), position_manager=MagicMock(),
                                 _state_watcher_timer=MagicMock(), _cycle_timer=MagicMock(),
                                 widget_state=MagicMock(), update_config=MagicMock(),
                                 _show_usage_alert=MagicMock(), _on_update_available=MagicMock(),
                                 _execute_refresh=MagicMock())
    with patch("netspeedtray.views.widget.main.UpdateChecker") as uc:
        uc.return_value.should_check.return_value = False
        W._setup_timers(fake)
    return fake


def test_startup_never_starts_the_cycle_timer_with_the_switch_on(q_app):
    on = _timer_fake(_config(widget_display_mode="cycle", hardware_in_hover_card=True, **HW))
    on._cycle_timer.start.assert_not_called()
    off = _timer_fake(_config(widget_display_mode="cycle", hardware_in_hover_card=False, **HW))
    off._cycle_timer.start.assert_called_once()


def _wheel_widget(on):
    from PyQt6.QtWidgets import QWidget
    from netspeedtray.views.widget.main import NetworkSpeedWidget

    class _Wheel(NetworkSpeedWidget):
        def __init__(self):            # only the state wheelEvent / _step_cycle read
            QWidget.__init__(self)
            self.config = _config(widget_display_mode="cycle", hardware_in_hover_card=on, **HW)
            self._cycle_timer = MagicMock()
            self._cycle_index, self._current_cycle_mode = 0, "network_only"
            self.logger = MagicMock()
    return _Wheel()


def _wheel_event():
    from PyQt6.QtCore import QPoint, QPointF, Qt
    from PyQt6.QtGui import QWheelEvent
    return QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120),
                       Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                       Qt.ScrollPhase.NoScrollPhase, False)


def test_scrolling_does_not_step_auto_cycle_with_the_switch_on(q_app):
    on, off = _wheel_widget(True), _wheel_widget(False)
    try:
        ev = _wheel_event()
        on.wheelEvent(ev)
        assert on._current_cycle_mode == "network_only" and on._cycle_index == 0
        assert not ev.isAccepted()                     # passed through, like any non-cycle mode
        ev = _wheel_event()
        off.wheelEvent(ev)
        assert off._current_cycle_mode == "cpu_only" and ev.isAccepted()
    finally:
        on.deleteLater()
        off.deleteLater()


@pytest.fixture
def dialog(qtbot, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    from netspeedtray.views.settings.dialog import SettingsDialog
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: None)

    def make(side_bar_px=None):
        m = MagicMock()
        m.config, m.config_manager = {}, MagicMock()
        m._cached_layout_mode = "vertical" if side_bar_px else "horizontal"
        m.width.return_value = side_bar_px or 0
        d = SettingsDialog(main_widget=m, config=_config(), version="2.2.0", i18n=EN,
                           available_interfaces=["Ethernet"], is_startup_enabled=False)
        qtbot.addWidget(d)
        return d
    return make


def _note(d):
    n = d.widget_page.display_mode_note
    return n.text() if not n.isHidden() else None


@pytest.mark.parametrize("side_bar_px", [None, 48])
def test_display_mode_note_follows_the_switch_live_on_any_edge(dialog, side_bar_px):
    d = dialog(side_bar_px)
    hw = d.hardware_page
    hw.monitor_cpu.setChecked(True)
    combo = d.widget_page.display_mode_combo
    combo.setCurrentIndex(combo.findData("side_by_side"))
    d._emit_settings_changed_throttled()               # the live-preview path every change takes
    assert _note(d) == (EN.SIDE_TASKBAR_HOVER_NOTE if side_bar_px else None)
    hw.hardware_in_hover_card.setChecked(True)
    d._emit_settings_changed_throttled()
    assert _note(d) == EN.HOVER_CARD_DISPLAY_MODE_NOTE   # one note - the switch's, on every edge
    assert combo.isEnabled()                             # Display Mode stays editable
    hw.hardware_in_hover_card.setChecked(False)          # switch off: back to the edge's own note
    d._emit_settings_changed_throttled()
    assert _note(d) == (EN.SIDE_TASKBAR_HOVER_NOTE if side_bar_px else None)
    hw.hardware_in_hover_card.setChecked(True)
    hw.monitor_cpu.setChecked(False)                     # no hardware: the switch is inert, no note
    d._emit_settings_changed_throttled()
    assert _note(d) is None


def test_side_bar_with_the_switch_on_explains_where_the_hardware_went(dialog):
    """On a side bar the switch makes the preview fit network-only (hides_hardware False), so the
    note has to come from the switch, not from the fit."""
    d = dialog(48)
    d.hardware_page.monitor_gpu.setChecked(True)
    d.hardware_page.hardware_in_hover_card.setChecked(True)
    d._emit_settings_changed_throttled()
    assert d.preview_widget.side_fit is not None and not d.preview_widget.side_fit.hides_hardware
    assert _note(d) == EN.HOVER_CARD_DISPLAY_MODE_NOTE
