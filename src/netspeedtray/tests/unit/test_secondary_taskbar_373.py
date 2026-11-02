"""Secondary-taskbar placement on mixed-DPI laptops + extended monitors (#373, relu616; analysis by
@emgeka in the same thread).

R2a - the preferred extended monitor read as taskbar-less, so the widget free-floated ABOVE its bar at
the desktop's bottom-right. Root cause: the taskbar-to-screen match converted Qt's screen geometry to
physical px by multiplying the ORIGIN by the scale, but Qt 6 on Windows keeps each screen's native
origin and scales only its size. A 150 % 4K monitor above a 150 % laptop panel is Qt
QRect(0, -2160, 2560, 1440); origin * 1.5 put it at y -3240..-1080, so its bar (0, -72, 3840, 0)
overlapped no screen and the FIRST screen (the primary) won. The match now uses the bar window's
monitor device name first (MonitorFromWindow -> GetMonitorInfo "Device" == QScreen.name()), then the
corrected physical rect (origin unscaled, size scaled).

R2b - on Shell_SecondaryTrayWnd get_tray_rect() is always None, and the step left of a Widgets/weather
button beside the clock was gated on a tray rect, so it never ran there; the clock was a fixed 75 px
reserve though a clock with seconds is wider; and nothing probed a mirror's bar for its Widgets box /
clock, while the one-taskbar layout cache flipped between bars with "Show on all taskbars" on.
"""
import types
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QRect
from PyQt6.QtWidgets import QApplication

from netspeedtray import constants
from netspeedtray.core.position_manager import PositionCalculator, PositionManager, WindowState
from netspeedtray.utils import taskbar_utils as tbu

E = constants.taskbar.edge
GAP = constants.layout.WIDGETS_BUTTON_MIN_GAP_PX
TRAY_GAP = constants.layout.TRAY_EDGE_MIN_GAP_PX
RESERVE = constants.config.defaults.DEFAULT_SECONDARY_CLOCK_RESERVE_PX
W, H = 85, 48
CFG = {"tray_offset_x": 0, "tray_offset_y": 3}
# The real function, captured before conftest's autouse no-op replaces the module attribute
# (getattr: the module must still import on a tree without it, to show these tests fail there).
REAL_BACKGROUND_PROBE = getattr(tbu, "request_background_layout_probe", None)

D1, D2 = r"\\.\DISPLAY1", r"\\.\DISPLAY2"
PRIMARY_HWND, SECONDARY_HWND, TRAY_HWND = 0x10010, 0x20020, 0x10011


@pytest.fixture(scope="module", autouse=True)
def q_app():
    return QApplication.instance() or QApplication([])


def _screen(x, y, w, h, name, dpr):
    s = MagicMock()
    s.geometry.return_value = QRect(x, y, w, h)
    s.availableGeometry.return_value = QRect(x, y, w, h - 48)
    s.devicePixelRatio.return_value = dpr
    s.name.return_value = name
    return s


# --- R2a: which screen a taskbar is on ---------------------------------------------------------------

# relu616-style layout: laptop panel 1920x1080 at 150 % (Qt 1280x720 at the origin) and a 4K monitor
# at 150 % ABOVE it, physical (0, -2160)..(3840, 0) - Qt reports QRect(0, -2160, 2560, 1440).
PRIMARY_PHYS = (0, 0, 1920, 1080)
SECONDARY_PHYS = (0, -2160, 3840, 0)
PRIMARY_BAR = (0, 1008, 1920, 1080)
SECONDARY_BAR = (0, -72, 3840, 0)


def _mixed_dpi_screens(monkeypatch):
    primary = _screen(0, 0, 1280, 720, D1, 1.5)
    secondary = _screen(0, -2160, 2560, 1440, D2, 1.5)
    monkeypatch.setattr(tbu.QApplication, "screens", staticmethod(lambda: [primary, secondary]))
    monkeypatch.setattr(tbu.QApplication, "primaryScreen", staticmethod(lambda: primary))
    return primary, secondary


def _fake_win32(monkeypatch, *, secondary_bar=SECONDARY_BAR, device_by_hwnd=None):
    """win32gui / win32api for a primary + a secondary bottom bar. ``device_by_hwnd``: what
    MonitorFromWindow -> GetMonitorInfo says each bar's monitor is (None: no answer)."""
    rects = {PRIMARY_HWND: PRIMARY_BAR, SECONDARY_HWND: secondary_bar}
    classes = {PRIMARY_HWND: "Shell_TrayWnd", SECONDARY_HWND: "Shell_SecondaryTrayWnd"}
    gui = MagicMock()
    gui.error = type("FakeWin32Error", (Exception,), {"winerror": 0})
    gui.FindWindow.return_value = PRIMARY_HWND
    gui.IsWindow.return_value = True
    gui.IsWindowVisible.return_value = True
    gui.GetClassName.side_effect = lambda h: classes.get(h, "")
    gui.GetWindowRect.side_effect = lambda h: rects.get(h, (0, 0, 0, 0))
    gui.FindWindowEx.side_effect = (
        lambda parent, after, cls, name: TRAY_HWND if (parent == PRIMARY_HWND and cls == "TrayNotifyWnd") else 0)
    gui.EnumWindows.side_effect = lambda cb, extra: [cb(h, extra) for h in (PRIMARY_HWND, SECONDARY_HWND)]
    monkeypatch.setattr(tbu, "win32gui", gui)

    infos = {1: {"Work": (0, 0, 1920, 1008), "Monitor": PRIMARY_PHYS, "Device": D1},
             2: {"Work": (0, -2160, 3840, -72), "Monitor": SECONDARY_PHYS, "Device": D2}}
    by_device = {D1: 101, D2: 102}
    device_by_hwnd = device_by_hwnd or {}
    api = MagicMock()
    api.MonitorFromPoint.side_effect = lambda pt, flags=0: 2 if pt[1] < 0 else 1
    api.MonitorFromWindow.side_effect = (
        lambda h, flags=0: by_device.get(device_by_hwnd.get(h)) if device_by_hwnd.get(h) else None)

    def monitor_info(m):
        if m in infos:
            return infos[m]
        device = next(d for d, handle in by_device.items() if handle == m)
        return {"Device": device}
    api.GetMonitorInfo.side_effect = monitor_info
    monkeypatch.setattr(tbu, "win32api", api)
    monkeypatch.setattr(tbu, "get_dpi_for_monitor", lambda m, hwnd=None: 1.5)
    monkeypatch.setattr(tbu, "find_tasklist_rect", lambda h: None)
    monkeypatch.setattr(tbu, "find_toolbar_bands_rect", lambda h: None)
    monkeypatch.setattr(tbu, "taskbar_layout_for", lambda h: (None, None, None, False))
    monkeypatch.setattr(tbu, "get_widgets_rect", lambda h: None)


def _bars_by_hwnd():
    return {tb.hwnd: tb for tb in tbu.get_all_taskbar_info()}


def test_mixed_dpi_secondary_bar_resolves_to_its_own_screen(monkeypatch):
    """The issue's layout, geometry only (no device-name answer): before the fix the secondary bar
    overlapped no screen and was labelled with the primary."""
    _primary, secondary = _mixed_dpi_screens(monkeypatch)
    _fake_win32(monkeypatch)
    bars = _bars_by_hwnd()
    assert bars[SECONDARY_HWND].screen_name == D2
    assert bars[SECONDARY_HWND].is_primary is False
    assert bars[PRIMARY_HWND].screen_name == D1
    assert bars[SECONDARY_HWND].get_screen() is secondary


def test_the_preferred_extended_monitor_docks_instead_of_free_floating(monkeypatch):
    """R2a's symptom: preferred = the extended monitor, which HAS a taskbar - it must dock, never
    free-float above that bar at the desktop's bottom-right."""
    _mixed_dpi_screens(monkeypatch)
    _fake_win32(monkeypatch)
    assert tbu.get_free_float_screen(D2) is None
    assert tbu.get_taskbar_info(preferred_screen_name=D2).hwnd == SECONDARY_HWND


def test_device_name_wins_over_geometry(monkeypatch):
    """The bar window's own monitor (MonitorFromWindow -> GetMonitorInfo Device) decides, whatever the
    coordinate maths say: here the secondary's rect geometrically overlaps the primary panel."""
    _mixed_dpi_screens(monkeypatch)
    _fake_win32(monkeypatch, secondary_bar=(0, 1008, 1920, 1080),
                device_by_hwnd={PRIMARY_HWND: D1, SECONDARY_HWND: D2})
    assert _bars_by_hwnd()[SECONDARY_HWND].screen_name == D2


def test_get_screen_intersection_layer_uses_the_physical_rect(monkeypatch):
    """TaskbarInfo.get_screen()'s fallback (stored geometry stale, no device answer) had the same
    origin * scale bug and returned the primary."""
    _primary, secondary = _mixed_dpi_screens(monkeypatch)
    monkeypatch.setattr(tbu, "monitor_device_name_for_hwnd", lambda h: None, raising=False)
    tb = tbu.TaskbarInfo(hwnd=999, tray_hwnd=None, tasklist_rect=None, rect=SECONDARY_BAR,
                         screen_name=D2, screen_geometry=(0, -2160, 2561, 1440),   # stale by 1 px
                         work_area=(0, -2160, 3840, -72), dpi_scale=1.5, is_primary=False, height=48)
    assert tb.get_screen() is secondary


def test_qt_screen_native_rect_keeps_the_origin_and_scales_the_size():
    s = _screen(0, -2160, 2560, 1440, D2, 1.5)
    assert tbu.qt_screen_native_rect(s) == SECONDARY_PHYS
    s = _screen(1920, 0, 2048, 1152, D2, 1.25)          # right of a 100 % 1080p primary
    assert tbu.qt_screen_native_rect(s) == (1920, 0, 1920 + 2560, 1440)
    assert tbu.physical_to_logical(1920 + 2560, 1920, 1.25) == 1920 + 2048
    assert tbu.physical_to_logical(300, 0, 1.5) == 300 / 1.5    # origin 0: plain division


# --- R2b: the default spot on a tray-less (secondary) bottom bar --------------------------------------

def _secondary_bar(*, x0=0, width=1920, dpi=1.0, widgets=None, hwnd=4343, thick=48):
    """A Win11 SECONDARY bottom bar (no TrayNotifyWnd, no classic task list) on a screen whose Qt
    geometry is (x0, 0, width, 1080) at ``dpi`` - Qt's native-origin convention. ``widgets``: the
    Widgets button's LOGICAL x span (left, right), converted to physical around the origin."""
    def px(v):            # a logical x on this screen -> physical
        return int(round(x0 + (v - x0) * dpi))

    def py(v):
        return int(round(v * dpi))

    tb = MagicMock()
    tb.hwnd, tb.dpi_scale, tb.height = hwnd, dpi, py(thick)
    tb.rect = (px(x0), py(1080 - thick), px(x0 + width), py(1080))
    tb.tasklist_rect = tb.toolbars_rect = tb.apps_extent = tb.widgets_rect = None
    tb.widgets_extent = (px(widgets[0]), py(1080 - thick), px(widgets[1]), py(1080)) if widgets else None
    tb.get_edge_position.return_value = E.BOTTOM
    tb.get_tray_rect.return_value = None
    s = MagicMock()
    s.geometry.return_value = QRect(x0, 0, width, 1080)
    s.availableGeometry.return_value = QRect(x0, 0, width, 1080 - thick)
    s.name.return_value = D2
    tb.get_screen.return_value = s
    return tb


def _x(tb, config=CFG):
    return PositionCalculator().calculate_position(tb, (W, H), config).x


@pytest.fixture
def probed_tray():
    tbu._probed_tray.clear()
    yield tbu._probed_tray
    tbu._probed_tray.clear()


def test_secondary_bar_steps_left_of_a_widgets_button_beside_its_clock(probed_tray):
    """tray_rect is None on every Shell_SecondaryTrayWnd: the step must run there too."""
    widgets = (1690, 1838)                         # right beside the 75 px clock reserve (1845)
    tb = _secondary_bar(widgets=widgets)
    x = _x(tb)
    assert x + W + GAP <= widgets[0]
    assert x == widgets[0] - GAP - W


@pytest.mark.parametrize("dpi", [1.0, 1.25, 1.5, 2.0])
def test_emgeka_synthetic_weather_case(dpi, probed_tray):
    """@emgeka's case: a 1920-logical bar, the 85 px widget, the 75 px reserve, weather at
    x 1700-1840 - no overlap at any scale."""
    tb = _secondary_bar(dpi=dpi, widgets=(1700, 1840))
    x = _x(tb)
    left, right = tb.widgets_extent[0] / dpi, tb.widgets_extent[2] / dpi
    assert x + W + GAP <= left or x >= right + GAP
    assert x + W <= (1920 - RESERVE)                # and still clear of the clock reserve


def test_a_button_reaching_into_the_clock_reserve_is_still_stepped_past(probed_tray):
    """The reserve under-estimates a wide clock and the button ends past it: it is still the tray-end
    button, so the widget goes left of it instead of sitting on both."""
    widgets = (1720, 1852)                          # ends past 1920 - 75 = 1845
    x = _x(_secondary_bar(widgets=widgets))
    assert x + W + GAP <= widgets[0]


def test_mixed_dpi_offset_screen_converts_around_its_origin(probed_tray):
    """A 150 % 4K monitor right of a 100 % 1080p panel: Qt (1920, 0, 2560, 1440); its button's physical
    rect converts around x 1920, not as physical / 1.5."""
    x0, width = 1920, 2560
    widgets = (x0 + 2300, x0 + 2440)                 # logical, beside the reserve (x0 + 2485)
    tb = _secondary_bar(x0=x0, width=width, dpi=1.5, widgets=widgets)
    x = _x(tb)
    assert x + W + GAP <= widgets[0]
    assert x == widgets[0] - GAP - W


def test_centred_secondary_bar_button_at_the_far_left_changes_nothing(probed_tray):
    tb = _secondary_bar(widgets=(6, 158))
    assert _x(tb) == 1920 - RESERVE - W


def test_a_users_drag_on_a_secondary_bar_is_never_overridden(probed_tray):
    tb = _secondary_bar(widgets=(1690, 1838))
    assert _x(tb, {"tray_offset_x": 50, "tray_offset_y": 3}) == 1920 - RESERVE - W - 50


def test_a_probed_clock_wider_than_the_reserve_moves_the_right_boundary(probed_tray):
    """A clock with seconds: UIA saw it from 1810 (110 px) - the widget hugs it with the #161 gap."""
    tb = _secondary_bar()
    probed_tray[tb.hwnd] = (1810, 1032, 1920, 1080)
    assert _x(tb) == 1810 - TRAY_GAP - W


def test_a_probed_clock_and_a_weather_button_beside_it(probed_tray):
    tb = _secondary_bar(widgets=(1660, 1800))
    probed_tray[tb.hwnd] = (1810, 1032, 1920, 1080)
    x = _x(tb)
    assert x + W + GAP <= 1660


@pytest.mark.parametrize("rect", [
    (5000, 1032, 5110, 1080),    # outside this bar (another layout of the same HWND)
    (100, 1032, 210, 1080),      # in the bar's left half: not the clock
])
def test_a_probed_rect_that_cannot_be_the_clock_is_ignored(rect, probed_tray):
    tb = _secondary_bar()
    probed_tray[tb.hwnd] = rect
    assert _x(tb) == 1920 - RESERVE - W


# --- the layout probe per taskbar HWND ---------------------------------------------------------------

FRESH = {"hwnd": 0, "apps": None, "widgets": None, "group": None, "overflow": None,
         "widgets_content": None, "bar": None, "pending": False, "done": False, "gen": 0,
         "running": False, "started": 0.0, "landed": 0}
A, B, C = 0xA0, 0xB0, 0xC0
APPS = {A: (0, 1032, 500, 1080), B: (1920, 1032, 2400, 1080), C: (3840, 1032, 4300, 1080)}
WIDGETS = {A: (1600, 1032, 1750, 1080), B: (3500, 1032, 3650, 1080), C: (5400, 1032, 5550, 1080)}
CLOCKS = {A: (1800, 1032, 1920, 1080), B: (3720, 1032, 3840, 1080), C: (5640, 1032, 5760, 1080)}


@pytest.fixture
def layout():
    tbu._layout_state.update(FRESH)
    tbu._probed_tray.clear()
    with patch.object(tbu, "_start_layout_worker"), \
            patch.object(tbu, "_query_taskbar_layout_blocking",
                         side_effect=lambda h: (APPS[h], WIDGETS[h], None, None, CLOCKS[h])):
        yield
    tbu._layout_state.update(FRESH)
    tbu._probed_tray.clear()


def _probe(hwnd, keep_last=False):
    tbu.request_taskbar_layout_probe(hwnd, keep_last=keep_last)
    tbu._layout_worker()


def test_another_bars_probe_does_not_flip_this_bars_layout(layout):
    """With "Show on all taskbars" every bar probes its own HWND; the one-taskbar cache used to answer
    (None, None) for every bar but the last one probed."""
    _probe(A)
    _probe(B)
    assert tbu.taskbar_layout_snapshot(A) == (APPS[A], WIDGETS[A], False)
    assert tbu.taskbar_layout_snapshot(B) == (APPS[B], WIDGETS[B], False)
    assert tbu.taskbar_layout_for(A)[2] == WIDGETS[A]
    assert tbu.taskbar_layout_snapshot(C) == (None, None, False)     # never another bar's


def test_a_keep_last_recheck_after_another_bars_probe_keeps_its_own_result(layout):
    _probe(A)
    _probe(B)
    tbu.request_taskbar_layout_probe(A, keep_last=True)          # in flight, not landed yet
    apps, widgets, pending = tbu.taskbar_layout_snapshot(A)
    assert (apps, widgets, pending) == (APPS[A], WIDGETS[A], True)
    tbu._layout_worker()
    assert tbu.taskbar_layout_snapshot(A) == (APPS[A], WIDGETS[A], False)


def test_a_fresh_probe_still_drops_that_bars_cached_layout(layout):
    _probe(A)
    _probe(B)
    tbu.request_taskbar_layout_probe(A)                          # not keep_last: a new dock
    assert tbu.taskbar_layout_snapshot(A) == (None, None, True)


def test_a_background_probe_never_takes_the_slot(layout):
    _probe(A)
    gen, landed = tbu._layout_state["gen"], tbu._layout_state["landed"]
    REAL_BACKGROUND_PROBE(C)
    REAL_BACKGROUND_PROBE(C)                                     # queued once
    assert tbu._layout_background == [C]
    tbu._layout_worker()
    assert tbu._layout_state["hwnd"] == A
    assert (tbu._layout_state["gen"], tbu._layout_state["landed"]) == (gen, landed)
    assert tbu.taskbar_layout_snapshot(C) == (APPS[C], WIDGETS[C], False)
    assert tbu.probed_tray_rect(C) == CLOCKS[C]
    assert tbu.taskbar_layout_snapshot(A) == (APPS[A], WIDGETS[A], False)
    assert not tbu._layout_state["running"] and tbu._layout_background == []


def test_a_background_probe_of_the_slots_own_bar_is_a_keep_last_recheck(layout):
    _probe(A)
    landed = tbu._layout_state["landed"]
    REAL_BACKGROUND_PROBE(A)
    assert tbu._layout_state["pending"] and tbu._layout_background == []
    assert tbu.taskbar_layout_snapshot(A)[:2] == (APPS[A], WIDGETS[A])   # kept meanwhile
    tbu._layout_worker()
    assert tbu._layout_state["landed"] == landed + 1


def test_a_slot_request_takes_a_queued_bar_out_of_the_background_queue(layout):
    REAL_BACKGROUND_PROBE(C)
    tbu.request_taskbar_layout_probe(C)
    assert tbu._layout_background == []


# --- who asks for that probe ---------------------------------------------------------------------------

def _manager(tb, monkeypatch):
    calls = []
    monkeypatch.setattr(tbu, "request_background_layout_probe", lambda h: calls.append(h))
    widget = MagicMock()
    widget.width.return_value, widget.height.return_value = W, H
    pm = PositionManager(WindowState(config=dict(CFG), widget=widget, taskbar_info=tb),
                         side_watcher_enabled=False, app_yield_enabled=False)   # as a mirror is built
    return pm, calls


def test_a_mirror_on_a_trayless_bar_probes_its_own_bar_then_rate_limits(monkeypatch, probed_tray):
    tb = _secondary_bar(widgets=(1690, 1838))
    pm, calls = _manager(tb, monkeypatch)
    pos = pm.get_calculated_position()
    assert calls == [tb.hwnd]
    assert pos.x + W + GAP <= 1690                 # mirrors get the same weather avoidance
    pm.get_calculated_position()
    assert calls == [tb.hwnd]                      # not every tick
    pm._trayless_probe_at -= constants.timeouts.TRAYLESS_BAR_LAYOUT_REFRESH_S + 1
    pm.get_calculated_position()
    assert calls == [tb.hwnd, tb.hwnd]


def test_the_primary_bar_with_a_tray_is_never_background_probed(monkeypatch):
    tb = _secondary_bar()
    tb.get_tray_rect.return_value = (1770, 1032, 1920, 1080)
    pm, calls = _manager(tb, monkeypatch)
    pm.get_calculated_position()
    assert calls == []
