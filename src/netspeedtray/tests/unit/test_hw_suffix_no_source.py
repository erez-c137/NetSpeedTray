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


GRACE = constants.lhm.NOT_DETECTED_GRACE_SEC
HOLD = constants.lhm.SENSOR_SOURCE_HOLD_SEC


def _thread(q_app, **over):
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    cfg.update(monitor_cpu_enabled=True, monitor_gpu_enabled=True, **over)
    t = StatsMonitorThread(interval=0.1, config=cfg)
    t.logger = MagicMock()
    t._wmi_ohm = None
    t._power_pkg_counter = None
    t._gpu_source_serves = (False, False)
    return t


def _verdicts(t, clock, stats, polls=1, step=1.0):
    """``polls`` polls ``step`` s apart with the same stats; the verdict keys of each."""
    out = []
    for _ in range(polls):
        s = dict(stats)
        t._update_sensor_sources(s)
        out.append((s.get("cpu_sensor_source", "-"), s.get("gpu_sensor_source", "-")))
        clock.t += step
    return out


NO_READINGS = {"gpu": 3.0, "gpu_temp": None, "gpu_power": None, "cpu": 5.0, "cpu_temp": None,
               "cpu_power": None}


def test_intel_gpu_without_lhm_has_no_source_after_the_grace(q_app, clock):
    t = _thread(q_app)
    seen = _verdicts(t, clock, NO_READINGS, polls=int(GRACE) + 2)
    assert all(v == ("-", "-") for v in seen[:int(GRACE)])     # no verdict yet: the widget keeps its own
    assert seen[-1] == (False, False)


def test_a_reading_says_true_at_once_even_in_the_grace(q_app, clock):
    t = _thread(q_app)
    assert _verdicts(t, clock, dict(NO_READINGS, gpu_temp=50.0)) == [("-", True)]


def test_nvidia_smi_is_a_source_before_its_first_reading(q_app, clock):
    t = _thread(q_app)
    t._gpu_source_serves = (True, False)
    assert _verdicts(t, clock, NO_READINGS)[0][1] is True


def test_rapl_pp1_counts_only_for_power(q_app, clock):
    t = _thread(q_app, show_hardware_power=False)
    t._gpu_source_serves = (False, True)
    assert _verdicts(t, clock, {"gpu": 3.0, "gpu_temp": None}, polls=int(GRACE) + 2)[-1][1] is False
    t = _thread(q_app, show_hardware_power=True)
    t._gpu_source_serves = (False, True)
    assert _verdicts(t, clock, {"gpu": 3.0, "gpu_temp": None, "gpu_power": None})[0][1] is True


def test_cpu_sources(q_app, clock):
    t = _thread(q_app)
    t._wmi_ohm = object()                    # LHM v0.9.4 / OHM connected
    assert _verdicts(t, clock, {"cpu": 5.0, "cpu_temp": None, "cpu_power": None})[0][0] is True

    t = _thread(q_app, show_hardware_power=False)
    t._power_pkg_counter, t._rapl_pkg_read_ok = 7, True    # RAPL PKG: power only
    assert _verdicts(t, clock, {"cpu": 5.0, "cpu_temp": None}, polls=int(GRACE) + 2)[-1][0] is False

    t = _thread(q_app)
    assert _verdicts(t, clock, {"cpu": 5.0, "cpu_temp": 45.0, "cpu_power": None})[0][0] is True


def test_a_rapl_counter_that_never_read_is_no_source(q_app, clock):
    """Review M1: the RAPL PKG counter exists on most Intel CPUs; one that reads 0 is not a source."""
    t = _thread(q_app)
    t._power_pkg_counter = 7
    assert _verdicts(t, clock, {"cpu": 5.0, "cpu_temp": None, "cpu_power": None},
                     polls=int(GRACE) + 2)[-1][0] is False
    t = _thread(q_app)
    t._power_query, t._power_pkg_counter, t._wmi_ohm = 123, 7, False
    t._lhm_http = MagicMock()
    t._lhm_http.get_snapshot.return_value = None
    with patch("win32pdh.CollectQueryData"), \
            patch("win32pdh.GetFormattedCounterValue", return_value=(0, 0.0)), \
            patch.object(t, "_init_ohm_wmi"):
        assert t._poll_cpu_power() is None
    assert t._rapl_pkg_read_ok is False
    with patch("win32pdh.CollectQueryData"), \
            patch("win32pdh.GetFormattedCounterValue", return_value=(0, 15000.0)):
        assert t._poll_cpu_power() == pytest.approx(15.0)
    assert t._rapl_pkg_read_ok is True
    with patch("win32pdh.CloseQuery"):
        t._cleanup_power_query()             # a rebuilt query must prove itself again
    assert t._rapl_pkg_read_ok is False


def test_a_lost_source_is_held_then_dropped_and_comes_back(q_app, clock):
    """LHM closed: "(N/A)" for the hold (a blip must not resize the widget), then no suffix; LHM
    started again: the suffix is back on the next reading, no restart."""
    t = _thread(q_app)
    assert _verdicts(t, clock, dict(NO_READINGS, gpu_temp=50.0))[0][1] is True
    clock.t += HOLD - 3
    assert _verdicts(t, clock, NO_READINGS)[0][1] is True
    clock.t += 2
    assert _verdicts(t, clock, NO_READINGS)[0][1] is False
    clock.t += 100
    assert _verdicts(t, clock, dict(NO_READINGS, gpu_temp=51.0))[0][1] is True


def test_a_source_remembered_from_last_session_gets_the_whole_hold(q_app, clock):
    """An LHM user whose NetSpeedTray starts first: "(N/A)" until LHM answers - no shrink, no grow."""
    t = _thread(q_app, gpu_sensor_source_last=True)
    seen = _verdicts(t, clock, NO_READINGS, polls=int(HOLD) - 2)
    assert all(g in ("-", True) for _c, g in seen)
    assert _verdicts(t, clock, dict(NO_READINGS, gpu_temp=50.0))[0][1] is True     # LHM answered
    # Not remembered (or remembered False): the plain grace.
    t = _thread(q_app, gpu_sensor_source_last=False)
    assert _verdicts(t, clock, NO_READINGS, polls=int(GRACE) + 2)[-1][1] is False


def _dirty_poll(t):
    """What the poll loop does with a pending settings / environment change, without the PDH work."""
    t._apply_source_hold_changes(mt.time.monotonic())


def test_an_unrelated_apply_or_a_resume_does_not_reset_the_verdict(q_app, clock):
    t = _thread(q_app)
    assert _verdicts(t, clock, dict(NO_READINGS, gpu_temp=50.0))[0][1] is True
    clock.t += 3600                                       # asleep for an hour
    t.invalidate_hardware_queries()                        # resume
    _dirty_poll(t)
    assert _verdicts(t, clock, NO_READINGS)[0][1] is True     # a fresh hold, not False at once
    t.update_config(dict(t.config, default_color_dark="#123456"))   # a colour: no fresh look
    assert t._sources_reset_pending is False
    _dirty_poll(t)
    assert _verdicts(t, clock, NO_READINGS)[0][1] is True


@pytest.mark.parametrize("key,value", [("show_hardware_power", True), ("lhm_port", 18085),
                                       ("lhm_username", "me"), ("gpu_adapter", "Intel(R) UHD")])
def test_a_source_setting_change_takes_a_fresh_look_with_a_grace(q_app, clock, key, value):
    t = _thread(q_app)
    assert _verdicts(t, clock, dict(NO_READINGS, gpu_temp=50.0))[0][1] is True
    t.update_config(dict(t.config, **{key: value}))
    assert t._sources_reset_pending is True
    _dirty_poll(t)
    seen = _verdicts(t, clock, {"gpu": 3.0, "gpu_temp": None}, polls=int(GRACE) + 2)
    assert all(g == "-" for _c, g in seen[:int(GRACE)])   # no False inside the grace
    assert seen[-1][1] is False                              # the old sighting does not count


def test_no_verdict_when_temps_and_power_are_off_or_not_polled(q_app, clock):
    t = _thread(q_app, show_hardware_temps=False, show_hardware_power=False)
    assert _verdicts(t, clock, NO_READINGS, polls=int(GRACE) + 2)[-1] == ("-", "-")   # Monitor-forced
    t = _thread(q_app)
    assert _verdicts(t, clock, {"gpu": 3.0}, polls=int(GRACE) + 2)[-1] == ("-", "-")  # GPU poll failed


def _gpu_thread(adapters=None):
    """A thread whose GPU poll makes no system call: no PDH query, no DXGI refresh, no nvidia-smi."""
    t = StatsMonitorThread(interval=0.1)
    t.logger = MagicMock()
    t._gpu_query = None
    t._gpu_query_retry_after = float("inf")
    t._gpu_adapters = adapters
    t._lhm_http = MagicMock()
    t._lhm_http.get_snapshot.return_value = None
    t._wmi_ohm = None
    t._nvidia_smi_path = None
    t._power_pp1_counter = None
    return t


def _gpu_poll(t):
    with patch.object(t, "_init_ohm_wmi"), patch.object(t, "_init_power_query"), \
            patch.object(t, "_refresh_adapters_on_luid_change"), \
            patch.object(t, "_selected_gpu_adapter", return_value=None), \
            patch.object(t, "_is_hybrid_laptop", return_value=False), \
            patch.object(t, "_query_nvidia_smi", side_effect=OSError("no")), \
            patch.object(t, "_dxgi_vram_total", return_value=None):
        t._poll_gpu_hybrid(include_temp=True, include_power=True)
    return t._gpu_source_serves


def _adapter(vendor_id):
    from netspeedtray.core import gpu_adapters
    return gpu_adapters.AdapterInfo(name="GPU", vendor_id=vendor_id, luid=(0, vendor_id),
                                    dedicated_video_bytes=1 << 30, shared_system_bytes=1 << 30,
                                    software=False)


def test_gpu_poll_reports_what_can_serve_it(q_app):
    """_poll_gpu_hybrid records whether nvidia-smi / RAPL PP1 can serve the shown GPU."""
    t = _gpu_thread()
    assert _gpu_poll(t) == (False, False)

    t._power_pp1_counter = 9
    t._power_query = None                    # a counter, but it never read: not a source (review M1)
    assert _gpu_poll(t) == (False, False)
    t._rapl_pp1_read_ok = True
    assert _gpu_poll(t) == (False, True)

    t = _gpu_thread(adapters=[_adapter(constants.gpu.VENDOR_NVIDIA)])
    t._nvidia_smi_path = "nvidia-smi.exe"
    assert _gpu_poll(t)[0] is True


def test_a_leftover_nvidia_smi_without_an_nvidia_card_is_no_source(q_app):
    t = _gpu_thread(adapters=[_adapter(constants.gpu.VENDOR_INTEL)])
    t._nvidia_smi_path = "nvidia-smi.exe"
    assert _gpu_poll(t)[0] is False


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


def _fake_widget(mode="gpu_only", **cfg):
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    config = dict(widget_display_mode=mode, **cfg)
    w = types.SimpleNamespace(is_paused=False, cpu_sensor_source=None, gpu_sensor_source=None,
                              layout_manager=MagicMock(), update=MagicMock(), config=config,
                              logger=MagicMock())
    w.save_config_keys = lambda updates: config.update(updates)
    w._bar_shows = types.MethodType(NetworkSpeedWidget._bar_shows, w)
    return w


def test_widget_resizes_only_when_the_suffix_appears_or_vanishes():
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    w = _fake_widget()
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


def test_widget_remembers_the_verdict_and_leaves_a_bar_without_hardware_alone():
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    w = _fake_widget(mode="network_only")
    NetworkSpeedWidget.update_sensor_source(w, "gpu", False)
    assert w.gpu_sensor_source is False
    assert w.config["gpu_sensor_source_last"] is False          # sized right at the next launch
    w.layout_manager.resize_widget_for_font.assert_not_called()  # the bar shows no GPU
    w.update.assert_not_called()
    w.save_config_keys = MagicMock()
    NetworkSpeedWidget.update_sensor_source(w, "gpu", False)
    w.save_config_keys.assert_not_called()                       # written on a change only


def test_widget_is_seeded_from_the_remembered_verdict():
    from netspeedtray.views.widget.main import _remembered_source
    assert _remembered_source({"gpu_sensor_source_last": False}, "gpu") is False
    assert _remembered_source({"cpu_sensor_source_last": True}, "cpu") is True
    assert _remembered_source({}, "cpu") is None
    assert _remembered_source({"cpu_sensor_source_last": "yes"}, "cpu") is None


def test_the_remembered_verdicts_are_valid_config_app_state():
    schema = constants.config.defaults.VALIDATION_SCHEMA
    from netspeedtray.views.settings.dialog import SettingsDialog
    for key in ("cpu_sensor_source_last", "gpu_sensor_source_last"):
        assert constants.config.defaults.DEFAULT_CONFIG[key] is None
        assert schema[key]["default"] is None
        assert key in SettingsDialog.RESET_ALL_KEEPS      # app state: Reset all keeps it


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



# --- mirrors (other taskbars) follow the main widget's verdict (review M2) -----------------------

def test_a_mirror_gives_the_column_back_and_takes_it_again(qt_owned, monkeypatch):
    from netspeedtray.tests.unit.test_mirror_widgets import _real_main, _secondary_tb
    from netspeedtray.views.widget import layout as layout_mod
    from netspeedtray.views.widget.mirror import MirrorWidget
    from netspeedtray.utils import taskbar_utils
    from netspeedtray.core import shutdown_channel
    tb = _secondary_tb()
    monkeypatch.setattr(layout_mod, "get_taskbar_info", lambda preferred_screen_name=None: tb)
    monkeypatch.setattr(taskbar_utils, "get_free_float_screen", lambda name: None)
    monkeypatch.setattr(shutdown_channel, "arm_window", lambda hwnd: True)
    main = _real_main(qt_owned)
    main.config.update(_config(widget_display_mode="gpu_only", monitor_cpu_enabled=False))
    main.cpu_sensor_source = main.gpu_sensor_source = None
    m = qt_owned(MirrorWidget(main, tb))
    wide = m.width()
    assert m.gpu_sensor_source is None
    assert m._resize_if_needed(tb) is False                 # nothing changed: no re-size
    main.gpu_sensor_source = False
    assert m._resize_if_needed(tb) is True
    assert m.width() < wide                                  # no dead "(N/A)" column
    assert m._resize_if_needed(tb) is False                  # once
    main.gpu_sensor_source = True                            # LHM started
    assert m._resize_if_needed(tb) is True
    assert m.width() == wide                                 # the suffix fits again


# --- Settings > Hardware teaches in place (UX M2) -------------------------------------------------

@pytest.fixture
def hw_page(qtbot):
    from netspeedtray.views.settings.pages.hardware import HardwarePage
    state = {"lhm": None, "sources": (None, None)}
    calls = []
    p = HardwarePage(EN, lambda: None, lhm_status_provider=lambda: state["lhm"],
                     temp_setup_callback=calls.append, sensor_source_provider=lambda: state["sources"])
    qtbot.addWidget(p)
    p.load_settings(dict(constants.config.defaults.DEFAULT_CONFIG, show_hardware_temps=True,
                         monitor_cpu_enabled=True, monitor_gpu_enabled=True))
    p.state, p.setup_calls = state, calls
    p.show()
    return p


def test_no_source_bar_shows_while_a_monitored_device_has_none(hw_page):
    p = hw_page
    p.refresh_lhm_status()
    assert not p.no_source_notice.isVisible()
    assert p.temps_note.isVisible() and p.temps_setup_link.isVisible()
    p.state["sources"] = (True, False)                       # Intel / AMD GPU, no LHM
    p.refresh_lhm_status()
    bar = p.no_source_notice
    assert bar.isVisible()
    assert bar.text() == EN.HARDWARE_TEMPS_LIMITATION_NOTE
    assert bar.action_text() == EN.HARDWARE_TEMPS_SETUP_ACTION
    assert not p.temps_note.isVisible() and not p.temps_setup_link.isVisible()
    bar.click()
    assert p.setup_calls == [1]
    p.state["sources"] = (True, True)                        # LHM started
    p.refresh_lhm_status()
    assert not bar.isVisible()
    assert p.temps_note.isVisible() and p.temps_setup_link.isVisible()


def test_no_source_bar_needs_readings_on_and_the_device_monitored(hw_page):
    p = hw_page
    p.state["sources"] = (None, False)
    p.monitor_gpu.setChecked(False)
    p.refresh_lhm_status()
    assert not p.no_source_notice.isVisible()
    p.monitor_gpu.setChecked(True)
    p.show_temps.setChecked(False)
    p.show_power.setChecked(False)
    p.refresh_lhm_status()
    assert not p.no_source_notice.isVisible()
    p.show_power.setChecked(True)
    p.refresh_lhm_status()
    assert p.no_source_notice.isVisible()


def test_the_server_off_bar_wins(hw_page):
    p = hw_page
    p.state["sources"] = (False, False)
    p.state["lhm"] = (constants.lhm.STATUS_SERVER_OFF, constants.lhm.HTTP_PORT)
    p.refresh_lhm_status()
    assert p.lhm_server_off_notice.isVisible()
    assert not p.no_source_notice.isVisible()
    assert p.temps_note.isVisible() and not p.temps_setup_link.isVisible()   # as before


def test_settings_reads_the_widgets_verdicts(q_app):
    from netspeedtray.views.settings.dialog import SettingsDialog
    dlg = types.SimpleNamespace(parent_widget=types.SimpleNamespace(cpu_sensor_source=True,
                                                                    gpu_sensor_source=False))
    assert SettingsDialog._sensor_sources(dlg) == (True, False)
    dlg.parent_widget = MagicMock()                           # not a bool: unknown
    assert SettingsDialog._sensor_sources(dlg) == (None, None)


def test_an_intermittent_rapl_pp1_keeps_the_suffix_as_na(q_app, clock):
    """#609 reporter (Arc B390 iGPU, Panther Lake, no LHM): GPU power "sometimes" shows, temperature
    never. Once PP1 has read > 0 it IS a source: its 0-readings show "(N/A)", the suffix never vanishes."""
    t = _gpu_thread()
    t.config = dict(constants.config.defaults.DEFAULT_CONFIG, monitor_gpu_enabled=True,
                    show_hardware_temps=True, show_hardware_power=True)
    t._power_query, t._power_pp1_counter = 123, 9
    readings = iter([0.0, 0.0, 5200.0] + [0.0] * int(HOLD + GRACE + 10))
    verdicts = []
    with patch("win32pdh.CollectQueryData"), \
            patch("win32pdh.GetFormattedCounterValue", side_effect=lambda *a: (0, next(readings))):
        for _ in range(int(HOLD + GRACE + 10)):
            serves = _gpu_poll(t)
            stats = {"gpu": 3.0, "gpu_temp": None, "gpu_power": None}
            t._update_sensor_sources(stats)
            verdicts.append(stats.get("gpu_sensor_source", "-"))
            clock.t += 1.0
    assert serves == (False, True)                          # latched: still serving at 0 W
    assert True in verdicts
    assert all(v is True for v in verdicts[verdicts.index(True):])   # never False after the first read
