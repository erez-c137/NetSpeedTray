"""#609: no temperature / power source for a device -> no "(N/A)" suffix on the taskbar, ever.

With the defaults (show_hardware_temps / show_hardware_power on), an Intel / AMD GPU without
LibreHardwareMonitor showed "GPU 3% (N/A)" forever: NetSpeedTray reads GPU temperatures itself only
through nvidia-smi, so there was nothing that could ever fill the parentheses.

The monitor thread now says, per device, whether a source exists that can supply a reading
(``cpu_sensor_source`` / ``gpu_sensor_source``): a reading this poll, or an attached source - nvidia-smi
for an NVIDIA GPU, RAPL for power, a connected LHM/OHM WMI namespace for the CPU. A source that worked
is held for constants.lhm.SENSOR_SOURCE_HOLD_SEC, so an LHM blip keeps "(N/A)" instead of making the
suffix (and the widget width) flap. ``False`` drops the suffix and its reserved column; ``None`` (not
known yet - before the first poll) keeps today's behaviour.
"""
import time as _time
import types
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PyQt6.QtCore import QRect
from PyQt6.QtGui import QColor, QImage, QPainter

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.core import monitor_thread as mt
from netspeedtray.core.monitor_thread import StatsMonitorThread
from netspeedtray.utils import helpers
from netspeedtray.utils.taskbar_utils import TaskbarInfo
from netspeedtray.utils.widget_paint import WidgetMetrics, font_from_config, render_widget
from netspeedtray.utils.widget_renderer import WidgetRenderer
from netspeedtray.views.widget.layout import WidgetLayoutManager

EN = I18nStrings("en_US")


# --- the suffix itself -------------------------------------------------------------------------

def test_reference_is_empty_without_a_source():
    assert helpers.hw_suffix_reference(True, True, "celsius", False) == ""
    assert helpers.hw_suffix_reference(True, True, "celsius", None) == helpers.hw_suffix_reference(
        True, True, "celsius")
    assert helpers.hw_suffix_reference(True, False, "celsius", True) == helpers.hw_suffix_reference(
        True, False, "celsius")


def test_no_source_means_no_suffix_not_na(q_app):
    r = WidgetRenderer(dict(constants.config.defaults.DEFAULT_CONFIG), EN)
    assert r._build_hw_suffix(None, None, True, True, source_available=False) == ""


def test_a_source_with_a_missing_sample_keeps_na(q_app):
    r = WidgetRenderer(dict(constants.config.defaults.DEFAULT_CONFIG), EN)
    na = f"({EN.DEFAULT_TEXT})"
    assert r._build_hw_suffix(None, None, True, True, source_available=True) == na
    assert r._build_hw_suffix(None, None, True, True) == na          # not known yet: as before


def test_a_reading_always_shows(q_app):
    r = WidgetRenderer(dict(constants.config.defaults.DEFAULT_CONFIG), EN)
    assert r._build_hw_suffix(40.0, None, True, True, source_available=False) == "(40°C)"


# --- the monitor thread's verdict ----------------------------------------------------------------

class _Clock:
    t = 1000.0

    def monotonic(self):
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(mt, "time", types.SimpleNamespace(monotonic=c.monotonic, sleep=_time.sleep,
                                                          time=_time.time))
    return c


def _thread(q_app, **over):
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    cfg.update(monitor_cpu_enabled=True, monitor_gpu_enabled=True, **over)
    t = StatsMonitorThread(interval=0.1, config=cfg)
    t.logger = MagicMock()
    t._wmi_ohm = None
    t._power_pkg_counter = None
    t._gpu_source_serves = (False, False)
    return t


def test_intel_gpu_without_lhm_has_no_source(q_app, clock):
    t = _thread(q_app)
    stats = {"gpu": 3.0, "gpu_temp": None, "gpu_power": None, "cpu": 5.0, "cpu_temp": None,
             "cpu_power": None}
    t._update_sensor_sources(stats)
    assert stats["gpu_sensor_source"] is False
    assert stats["cpu_sensor_source"] is False


def test_nvidia_smi_is_a_source_before_its_first_reading(q_app, clock):
    t = _thread(q_app)
    t._gpu_source_serves = (True, False)
    stats = {"gpu": 3.0, "gpu_temp": None, "gpu_power": None}
    t._update_sensor_sources(stats)
    assert stats["gpu_sensor_source"] is True


def test_rapl_pp1_counts_only_for_power(q_app, clock):
    t = _thread(q_app, show_hardware_power=False)
    t._gpu_source_serves = (False, True)
    stats = {"gpu": 3.0, "gpu_temp": None}
    t._update_sensor_sources(stats)
    assert stats["gpu_sensor_source"] is False
    t = _thread(q_app, show_hardware_power=True)
    t._gpu_source_serves = (False, True)
    stats = {"gpu": 3.0, "gpu_temp": None, "gpu_power": None}
    t._update_sensor_sources(stats)
    assert stats["gpu_sensor_source"] is True


def test_cpu_sources(q_app, clock):
    t = _thread(q_app)
    t._wmi_ohm = object()                    # LHM v0.9.4 / OHM connected
    stats = {"cpu": 5.0, "cpu_temp": None, "cpu_power": None}
    t._update_sensor_sources(stats)
    assert stats["cpu_sensor_source"] is True

    t = _thread(q_app, show_hardware_power=False)
    t._power_pkg_counter = 7                 # RAPL PKG: power only
    stats = {"cpu": 5.0, "cpu_temp": None}
    t._update_sensor_sources(stats)
    assert stats["cpu_sensor_source"] is False

    t = _thread(q_app)
    stats = {"cpu": 5.0, "cpu_temp": 45.0, "cpu_power": None}   # an ACPI zone / LHM HTTP reading
    t._update_sensor_sources(stats)
    assert stats["cpu_sensor_source"] is True


def test_a_lost_source_is_held_then_dropped_and_comes_back(q_app, clock):
    """LHM closed: "(N/A)" for the hold (a blip must not resize the widget), then no suffix; LHM
    started again: the suffix is back on the next reading, no restart."""
    t = _thread(q_app)
    stats = {"gpu": 3.0, "gpu_temp": 50.0, "gpu_power": None}
    t._update_sensor_sources(stats)
    assert stats["gpu_sensor_source"] is True
    clock.t += constants.lhm.SENSOR_SOURCE_HOLD_SEC - 1
    stats = {"gpu": 3.0, "gpu_temp": None, "gpu_power": None}
    t._update_sensor_sources(stats)
    assert stats["gpu_sensor_source"] is True
    clock.t += 2
    stats = {"gpu": 3.0, "gpu_temp": None, "gpu_power": None}
    t._update_sensor_sources(stats)
    assert stats["gpu_sensor_source"] is False
    clock.t += 100
    stats = {"gpu": 3.0, "gpu_temp": 51.0, "gpu_power": None}
    t._update_sensor_sources(stats)
    assert stats["gpu_sensor_source"] is True


def test_no_verdict_when_temps_and_power_are_off_or_not_polled(q_app, clock):
    t = _thread(q_app, show_hardware_temps=False, show_hardware_power=False)
    stats = {"gpu": 3.0, "gpu_temp": None, "cpu": 5.0, "cpu_temp": None}   # Monitor-forced poll
    t._update_sensor_sources(stats)
    assert "gpu_sensor_source" not in stats and "cpu_sensor_source" not in stats
    t = _thread(q_app)
    stats = {"gpu": 3.0}                    # GPU poll failed: no keys, keep what the widget has
    t._update_sensor_sources(stats)
    assert "gpu_sensor_source" not in stats


def test_gpu_poll_reports_what_can_serve_it(q_app):
    """_poll_gpu_hybrid records whether nvidia-smi / RAPL PP1 can serve the shown GPU."""
    t = StatsMonitorThread(interval=0.1)
    t.logger = MagicMock()
    t._gpu_query = None
    t._gpu_query_retry_after = float("inf")
    t._lhm_http = MagicMock()
    t._lhm_http.get_snapshot.return_value = None
    t._wmi_ohm = None
    t._nvidia_smi_path = None
    t._power_pp1_counter = None
    with patch.object(t, "_init_ohm_wmi"), patch.object(t, "_init_power_query"):
        t._poll_gpu_hybrid(include_temp=True, include_power=True)
    assert t._gpu_source_serves == (False, False)

    t._power_pp1_counter = 9
    t._power_query = None                    # no query: no reading, but the counter is there
    with patch.object(t, "_init_ohm_wmi"), patch.object(t, "_init_power_query"):
        t._poll_gpu_hybrid(include_temp=True, include_power=True)
    assert t._gpu_source_serves == (False, True)

    t._power_pp1_counter = None
    t._nvidia_smi_path = "nvidia-smi.exe"
    with patch.object(t, "_init_ohm_wmi"), patch.object(t, "_init_power_query"), \
            patch.object(t, "_is_hybrid_laptop", return_value=False), \
            patch.object(t, "_query_nvidia_smi", side_effect=OSError("no")):
        t._poll_gpu_hybrid(include_temp=True, include_power=True)
    assert t._gpu_source_serves[0] is True


# --- controller -> widget ------------------------------------------------------------------------

def test_controller_forwards_the_verdict_before_the_readings(q_app):
    from netspeedtray.core.controller import StatsController
    ctl = StatsController({"record_hardware_history": False}, MagicMock())
    order = []
    ctl.view = types.SimpleNamespace(
        update_sensor_source=lambda dev, ok: order.append(("source", dev, ok)))
    ctl.gpu_temp_updated.connect(lambda v: order.append(("temp", v)))
    ctl.handle_stats({"gpu": 3.0, "gpu_temp": None, "gpu_sensor_source": False})
    assert order == [("source", "gpu", False), ("temp", None)]
    order.clear()
    ctl.handle_stats({"gpu": 3.0})            # no verdict this poll: nothing forwarded
    assert order == []


def test_widget_resizes_only_when_the_suffix_appears_or_vanishes():
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    w = types.SimpleNamespace(is_paused=False, cpu_sensor_source=None, gpu_sensor_source=None,
                              layout_manager=MagicMock(), update=MagicMock())
    upd = NetworkSpeedWidget.update_sensor_source
    upd(w, "gpu", True)                        # None -> True: same reserved width
    assert w.gpu_sensor_source is True
    w.layout_manager.resize_widget_for_font.assert_not_called()
    upd(w, "gpu", True)
    w.layout_manager.resize_widget_for_font.assert_not_called()
    upd(w, "gpu", False)                       # the suffix goes: the widget gets tighter, once
    assert w.layout_manager.resize_widget_for_font.call_count == 1
    upd(w, "gpu", False)
    assert w.layout_manager.resize_widget_for_font.call_count == 1
    upd(w, "gpu", True)                        # LHM started: back, once
    assert w.layout_manager.resize_widget_for_font.call_count == 2


# --- width: no dead suffix column ---------------------------------------------------------------

def _config(**over):
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    cfg.update({"free_float": False, "background_opacity": 0, "graph_enabled": False,
                "monitor_cpu_enabled": True, "monitor_gpu_enabled": True,
                "show_hardware_temps": True, "show_hardware_power": True})
    cfg.update(over)
    return cfg


def _bottom_taskbar():
    screen = MagicMock()
    screen.geometry.return_value = QRect(0, 0, 1920, 1080)
    screen.availableGeometry.return_value = QRect(0, 0, 1920, 1032)
    tb = MagicMock(spec=TaskbarInfo)
    tb.rect, tb.hwnd, tb.dpi_scale, tb.height = (0, 1032, 1920, 1080), 1, 1.0, 48
    tb.tasklist_rect = tb.widgets_rect = None
    tb.get_edge_position.return_value = constants.TaskbarEdge.BOTTOM
    tb.get_screen.return_value = screen
    tb.get_tray_rect.return_value = (1700, 1032, 1900, 1080)
    return tb


def _width(cfg, cpu_src, gpu_src):
    widget = MagicMock()
    widget.config = cfg
    widget.i18n = EN
    widget._identity_reserve_px.return_value = 0
    widget._hover_card = None
    widget.ram_total = widget.vram_total = 0.0
    widget.ram_used = widget.vram_used = None
    widget.cpu_sensor_source, widget.gpu_sensor_source = cpu_src, gpu_src
    lm = WidgetLayoutManager(widget)
    with patch("netspeedtray.views.widget.layout.get_taskbar_info", return_value=_bottom_taskbar()), \
            patch("netspeedtray.utils.taskbar_utils.get_free_float_screen", return_value=None):
        lm.set_font(resize=False)
        lm.resize_widget_for_font()
    (w, h), _ = widget.setFixedSize.call_args
    return w, h, lm._network_width


@pytest.mark.parametrize("mode", ["gpu_only", "cycle", "side_by_side"])
def test_no_gpu_source_frees_the_suffix_width(q_app, mode):
    cfg = _config(widget_display_mode=mode, monitor_cpu_enabled=(mode != "gpu_only"),
                  stack_hardware_stats=False)
    if mode == "cycle":
        cfg["monitor_cpu_enabled"] = False
    with_suffix = _width(cfg, None, None)[0]
    assert _width(cfg, None, True)[0] == with_suffix
    assert _width(cfg, None, False)[0] < with_suffix


@pytest.mark.parametrize("over", [dict(widget_display_mode="combined"),
                                  dict(widget_display_mode="side_by_side", stack_hardware_stats=True)],
                         ids=["combined", "side_by_side_stacked"])
def test_stacked_rows_keep_the_column_while_one_device_has_a_source(q_app, over):
    """The default layout (side_by_side, stacked): CPU and GPU share one suffix column."""
    cfg = _config(**over)
    both = _width(cfg, None, None)[0]
    assert _width(cfg, False, True)[0] == both        # the GPU still needs the column
    assert _width(cfg, None, False)[0] == both
    assert _width(cfg, False, False)[0] < both


def _ink_right(cfg, w, h, nw, metrics):
    renderer = WidgetRenderer(cfg, EN)
    img = QImage(3 * w, 3 * h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    p.translate(w, h)
    with patch.object(QPainter, "setClipRect", lambda *a, **k: None):
        render_widget(p, QRect(0, 0, w, h), renderer, renderer.config, metrics, layout_mode="horizontal",
                      cycle_mode="network_only", network_width=nw, font=font_from_config(cfg))
    p.end()
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = img.constBits()
    ptr.setsize(img.height() * img.width() * 4)
    a = np.frombuffer(ptr, dtype=np.uint8).reshape((img.height(), img.width(), 4))[:, :, 3].copy()
    xs = np.nonzero(a > 24)[1]
    return int(xs.min()) - w, int(xs.max()) - w


def test_gpu_only_without_a_source_paints_inside_its_tighter_widget(q_app):
    cfg = _config(widget_display_mode="gpu_only", monitor_cpu_enabled=False)
    w, h, nw = _width(cfg, None, False)
    live = WidgetMetrics(upload_mbps=1.0, download_mbps=1.0, gpu_usage=100.0,
                         gpu_sensor_source=False)
    left, right = _ink_right(cfg, w, h, nw, live)
    assert left >= 0 and right < w
    # ...and nothing painted where "(N/A)" used to be: the ink ends within a few px of "100%".
    with_na = WidgetMetrics(upload_mbps=1.0, download_mbps=1.0, gpu_usage=100.0, gpu_sensor_source=None)
    w2, h2, nw2 = _width(cfg, None, None)
    assert _ink_right(cfg, w2, h2, nw2, with_na)[1] > right


@pytest.mark.parametrize("sources", [(False, False), (False, True), (True, False)], ids=str)
@pytest.mark.parametrize("over", [dict(widget_display_mode="combined", monitor_ram_enabled=True),
                                  dict(widget_display_mode="side_by_side", stack_hardware_stats=True,
                                       monitor_ram_enabled=True),
                                  dict(widget_display_mode="side_by_side", stack_hardware_stats=False),
                                  dict(widget_display_mode="cpu_only")],
                         ids=["combined_ram", "sbs_stacked_ram", "sbs", "cpu_only"])
def test_a_tighter_widget_still_fits_the_worst_case(q_app, over, sources):
    """The width given back is exactly the column nothing paints in: worst-case readings from the
    device that HAS a source still land inside the widget."""
    cfg = _config(**over)
    cpu_src, gpu_src = sources
    w, h, nw = _width(cfg, cpu_src, gpu_src)
    live = WidgetMetrics(upload_mbps=12.4, download_mbps=88.6, cpu_usage=100.0, gpu_usage=100.0,
                         cpu_temp=99.0 if cpu_src else None, gpu_temp=99.0 if gpu_src else None,
                         cpu_power=250.0 if cpu_src else None, gpu_power=250.0 if gpu_src else None,
                         ram_used=15.9, ram_total=16.0,
                         cpu_sensor_source=cpu_src, gpu_sensor_source=gpu_src)
    left, right = _ink_right(cfg, w, h, nw, live)
    assert left >= 0 and right < w, (left, right, w)

