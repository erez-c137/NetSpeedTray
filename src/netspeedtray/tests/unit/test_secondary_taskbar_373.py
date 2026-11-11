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
REAL_LAYOUT_FOR = tbu.taskbar_layout_for
REAL_TOGGLE = getattr(tbu, "_widgets_toggle_on", None)

D1, D2 = r"\\.\DISPLAY1", r"\\.\DISPLAY2"
PRIMARY_HWND, SECONDARY_HWND, TRAY_HWND = 0x10010, 0x20020, 0x10011


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
         "running": False, "started": 0.0}
A, B, C = 0xA0, 0xB0, 0xC0
APPS = {A: (0, 1032, 500, 1080), B: (1920, 1032, 2400, 1080), C: (3840, 1032, 4300, 1080)}
WIDGETS = {A: (1600, 1032, 1750, 1080), B: (3500, 1032, 3650, 1080), C: (5400, 1032, 5550, 1080)}
CLOCKS = {A: (1800, 1032, 1920, 1080), B: (3720, 1032, 3840, 1080), C: (5640, 1032, 5760, 1080)}


@pytest.fixture
def layout():
    tbu._layout_state.update(FRESH)
    tbu._probed_tray.clear()
    # The Widgets toggle (TaskbarDa) is pinned on: cached reads drop the box while it is off, and the
    # suite must not depend on the machine's setting. Tests of the toggle patch it themselves.
    with patch.object(tbu, "_start_layout_worker"), \
            patch.object(tbu, "_widgets_toggle_on", return_value=True), \
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
    gen, landed = tbu._layout_state["gen"], tbu.taskbar_layout_landed_count(A)
    REAL_BACKGROUND_PROBE(C)
    REAL_BACKGROUND_PROBE(C)                                     # queued once
    assert tbu._layout_background == [C]
    tbu._layout_worker()
    assert tbu._layout_state["hwnd"] == A
    assert (tbu._layout_state["gen"], tbu.taskbar_layout_landed_count(A)) == (gen, landed)
    assert tbu.taskbar_layout_snapshot(C) == (APPS[C], WIDGETS[C], False)
    assert tbu.probed_tray_rect(C) == CLOCKS[C]
    assert tbu.taskbar_layout_snapshot(A) == (APPS[A], WIDGETS[A], False)
    assert not tbu._layout_state["running"] and tbu._layout_background == []


def test_a_background_probe_of_the_slots_own_bar_is_a_keep_last_recheck(layout):
    _probe(A)
    landed = tbu.taskbar_layout_landed_count(A)
    REAL_BACKGROUND_PROBE(A)
    assert tbu._layout_state["pending"] and tbu._layout_background == []
    assert tbu.taskbar_layout_snapshot(A)[:2] == (APPS[A], WIDGETS[A])   # kept meanwhile
    tbu._layout_worker()
    assert tbu.taskbar_layout_landed_count(A) > landed


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


# --- review round (PR #473) ------------------------------------------------------------------------

class _Clock:
    """A settable time.monotonic for the layout cache's ages and timeouts."""
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    c = _Clock()
    with patch.object(tbu.time, "monotonic", c):
        yield c


@pytest.fixture
def widgets_on():
    with patch.object(tbu, "_widgets_toggle_on", return_value=True):
        yield


def test_a_reseeded_recheck_that_times_out_is_not_a_landing(layout, clock, widgets_on):
    """Review 1: a keep_last re-check of a bar out of the slot reseeds the slot from the cache. When
    that probe then times out, the caller (the #24 yield, the side-bar watcher) compares the landed
    count with the one it read BEFORE the request - a cached layout must never count as fresh."""
    _probe(A)
    _probe(B)
    before = tbu.taskbar_layout_landed_count(A)
    tbu.request_taskbar_layout_probe(A, keep_last=True)       # reseeded, never probed
    clock.now += tbu.LAYOUT_PROBE_TIMEOUT_S + 1
    assert tbu.taskbar_layout_snapshot(A)[2] is False          # the re-check timed out
    assert tbu.taskbar_layout_landed_count(A) == before        # ... and did not "land"
    _probe(A, keep_last=True)                                  # a real landing does move it
    assert tbu.taskbar_layout_landed_count(A) > before


def test_a_landing_for_another_bar_does_not_move_this_bars_count(layout, clock, widgets_on):
    _probe(A)
    before = tbu.taskbar_layout_landed_count(A)
    _probe(B)
    REAL_BACKGROUND_PROBE(C)
    tbu._layout_worker()
    assert tbu.taskbar_layout_landed_count(A) == before


def test_a_cached_layout_ages_out(layout, clock, widgets_on):
    """Review 2 / #473 VM: past LAYOUT_CACHE_MAX_AGE_S a bar out of the slot no longer answers for its
    app buttons, but keeps its Widgets box (turning Widgets off drops it at once - next test)."""
    _probe(A)
    _probe(B)
    clock.now += tbu.LAYOUT_CACHE_MAX_AGE_S - 1
    assert tbu.taskbar_layout_snapshot(A)[:2] == (APPS[A], WIDGETS[A])
    clock.now += 2
    # The app buttons answer "unknown" (as before #373); the Widgets box is KEPT (#473 VM: dropping it
    # moved a side-bar mirror onto the weather button 90 s after launch).
    assert tbu.taskbar_layout_snapshot(A) == (None, WIDGETS[A], False)
    assert tbu.taskbar_layout_for(A)[2] == WIDGETS[A]
    assert tbu.taskbar_layout_group(A) is None
    tbu.request_taskbar_layout_probe(A, keep_last=True)              # reseeded: the box only
    assert tbu.taskbar_layout_snapshot(A) == (None, WIDGETS[A], True)


def test_a_widgets_button_turned_off_drops_its_cached_box_at_once(layout, clock):
    with patch.object(tbu, "_widgets_toggle_on", return_value=True):
        _probe(A)
        _probe(B)
    with patch.object(tbu, "_widgets_toggle_on", return_value=False):
        assert tbu.taskbar_layout_snapshot(A) == (APPS[A], None, False)
        assert tbu.taskbar_layout_for(A)[2] is None


def test_a_slot_request_waiting_on_a_background_probe_gets_its_full_timeout(layout, clock, widgets_on):
    """Review 3: a slot request made while a background probe runs waits for it; its timeout counts
    from when its own probe starts."""
    seen = {}

    def query(h):
        if h == C:                                   # the background probe takes 3 s ...
            tbu.request_taskbar_layout_probe(A)      # ... and a slot request arrives meanwhile
            clock.now += 3.0
        else:
            seen["started_at_probe"] = tbu._layout_state["started"]
            clock.now += 3.0                         # 6 s after the request, 3 s into its own probe
            seen["pending"] = tbu.taskbar_layout_snapshot(A)[2]
        return APPS[h], WIDGETS[h], None, None, CLOCKS[h]

    with patch.object(tbu, "_query_taskbar_layout_blocking", side_effect=query):
        REAL_BACKGROUND_PROBE(C)
        tbu._layout_worker()
    assert seen["started_at_probe"] == 1003.0
    assert seen["pending"] is True                   # not timed out: the probe lands
    assert tbu.taskbar_layout_snapshot(A) == (APPS[A], WIDGETS[A], False)


def test_a_hung_background_probe_does_not_extend_the_slots_timeout(layout, clock, widgets_on):
    """Review 3's exception: a background probe that outlasts LAYOUT_PROBE_TIMEOUT_S (a hung Explorer)
    does not restart the waiting slot request's clock - the request times out as it always did, its
    late result is dropped, and nothing counts as landed."""
    seen = {}

    def query(h):
        if h == C:
            tbu.request_taskbar_layout_probe(A)              # requested at 1000 ...
            seen["before"] = tbu.taskbar_layout_landed_count(A)
            clock.now += tbu.LAYOUT_PROBE_TIMEOUT_S + 1      # ... the background probe hangs 6 s
        else:
            seen["started"] = tbu._layout_state["started"]
            seen["snapshot"] = tbu.taskbar_layout_snapshot(A)
        return APPS[h], WIDGETS[h], None, None, CLOCKS[h]

    with patch.object(tbu, "_query_taskbar_layout_blocking", side_effect=query):
        REAL_BACKGROUND_PROBE(C)
        tbu._layout_worker()
    assert seen["started"] == 1000.0                          # not re-timed
    assert seen["snapshot"] == (None, None, False)            # timed out before its probe ran
    assert tbu.taskbar_layout_snapshot(A) == (None, None, False)   # the late result is dropped
    assert tbu.taskbar_layout_landed_count(A) == seen["before"]


def test_the_per_hwnd_cache_evicts_the_least_recently_landed(layout, clock):
    """Review 4: a bar that lands again moves to the back; the oldest landing goes first."""
    with tbu._layout_lock:
        for h in range(1, tbu._LAYOUT_BY_HWND_MAX + 1):
            tbu._store_landed_layout(h, {"apps": APPS[A]})
        tbu._store_landed_layout(1, {"apps": APPS[A]})          # bar 1 lands again
        tbu._store_landed_layout(999, {"apps": APPS[A]})        # one too many
    assert 1 in tbu._layout_by_hwnd and 2 not in tbu._layout_by_hwnd
    assert len(tbu._layout_by_hwnd) == tbu._LAYOUT_BY_HWND_MAX


def test_a_primary_bar_without_a_tray_window_is_never_background_probed(monkeypatch, probed_tray):
    """Review 5: ExplorerPatcher / StartAllBack builds can give the PRIMARY bar no TrayNotifyWnd."""
    tb = _secondary_bar()
    tb.is_primary = True
    pm, calls = _manager(tb, monkeypatch)
    pm.get_calculated_position()
    assert calls == []


def test_the_task_list_safe_zone_converts_around_the_screen_origin(probed_tray):
    """Review 6: a 150 % monitor LEFT of the primary, native x -3840 (Qt (-3840, 0, 2560, 1440)), with a
    classic task list ending at physical -840 = logical -1840. tasklist / scale put the safe zone at -560
    and snapped the widget onto it."""
    tb = _secondary_bar(x0=-3840, width=2560, dpi=1.5)
    tb.tasklist_rect = (-3840, 1548, -840, 1620)
    x = _x(tb)
    assert x == -3840 + 2560 - RESERVE - W                       # the reserve spot, no snap
    assert x > -1840


# Side bars: a RIGHT bar on a 150 % monitor ABOVE the primary - Qt (0, -2160, 2560, 1440), the bar
# physical (3768, -2160, 3840, 0).
def _offset_side_bar(apps=None):
    tb = MagicMock()
    tb.hwnd, tb.dpi_scale, tb.height = 7070, 1.5, 2160
    tb.rect = (3768, -2160, 3840, 0)
    tb.tasklist_rect = tb.widgets_extent = tb.widgets_rect = None
    tb.apps_extent = apps
    tb.get_tray_rect.return_value = None
    tb.get_edge_position.return_value = E.RIGHT
    s = MagicMock()
    s.geometry.return_value = QRect(0, -2160, 2560, 1440)
    s.availableGeometry.return_value = QRect(0, -2160, 2512, 1440)
    tb.get_screen.return_value = s
    return tb


def test_side_bar_geometry_converts_around_the_screen_origin(probed_tray):
    """Review 7: bar_top read -1440 (= -2160 / 1.5) instead of the screen's top, -2160."""
    from netspeedtray.core.position_manager import side_bar_geometry
    geo = side_bar_geometry(_offset_side_bar(apps=(3768, -1860, 3840, -1500)), 1.5)
    assert geo.bar_top == -2160
    assert geo.apps_top == -2160 + 300 / 1.5 and geo.apps_bottom == -2160 + 660 / 1.5
    assert geo.tray_top == -720 - constants.layout.SECONDARY_SIDE_CLOCK_RESERVE_PX


def test_a_probed_side_clock_converts_around_the_screen_origin(probed_tray):
    from netspeedtray.core.position_manager import side_bar_geometry
    tb = _offset_side_bar()
    probed_tray[tb.hwnd] = (3768, -66, 3840, -12)
    assert side_bar_geometry(tb, 1.5).tray_top == -2160 + (2160 - 66) / 1.5


def test_vertical_strip_x_from_the_window_converts_around_the_screen_origin():
    """Review 7: another appbar shares the edge, so the strip's x comes from the bar window - on a
    150 % monitor at native x -3840 that was rect / scale (-2542) instead of -3822."""
    tb = MagicMock()
    tb.rect = (-3813, 0, -3741, 2160)                 # 72 px wide, 27 px in from the screen's left
    s = MagicMock()
    s.geometry.return_value = QRect(-3840, 0, 2560, 1440)
    s.availableGeometry.return_value = QRect(-3840 + 60, 0, 2500, 1440)     # 60 px reserved
    tb.get_screen.return_value = s
    x_origin, thickness = tbu.vertical_taskbar_strip(tb, E.LEFT, 1.5)
    assert x_origin == -3840 + 27 / 1.5 and thickness == 48


def test_the_side_bar_watcher_converts_around_the_screen_origin():
    from netspeedtray.core import side_bar_watcher as sbw
    w = sbw.SideBarWatcher.__new__(sbw.SideBarWatcher)
    w._start_hwnd = 1
    ctx = sbw.SideWatchContext(hwnd=7070, dpi=1.5, widget=(0.0, 1.0), user_placed=False, y_origin=-2160.0)
    with patch.object(sbw, "_window_rect", return_value=(3768, -2094, 3840, -2028)):
        assert w._start_top(ctx) == -2160 + 66 / 1.5


def test_the_yield_group_span_converts_around_the_screen_origin():
    """Review 8: the #24 yield's group span divided by the scale."""
    from netspeedtray.core.app_overlap_yield import group_span
    assert group_span((5370, 0, 5580, 10), 1.5, 1920.0) == (4220.0, 4360.0)
    assert group_span((300, 0, 600, 10), 1.5) == (200.0, 400.0)          # the primary: rect / dpi


def test_the_yields_tray_spot_check_converts_around_the_screen_origin(probed_tray):
    """Review 8: tray_spot_clear compared the weather text at physical / scale. On a 150 % screen at
    x 1920 the text at logical 4250..4330 read as 3667..3720 - "clear" of the tray spot at 4320."""
    x0 = 1920
    tb = _secondary_bar(x0=x0, width=2560, dpi=1.5, widgets=(x0 + 2300, x0 + 2440))
    tb.is_primary = False
    content = (int(x0 + 2330 * 1.5), 1548, int(x0 + 2410 * 1.5), 1620)   # logical 4250..4330
    widget = MagicMock()
    widget.width.return_value, widget.height.return_value = W, H
    widget.pos.return_value = types.SimpleNamespace(x=lambda: 4127, y=lambda: 1032)
    widget._dragging = widget._is_context_menu_visible = False
    pm = PositionManager(WindowState(config=dict(CFG, hide_when_apps_cover=True), widget=widget,
                                     taskbar_info=tb), side_watcher_enabled=False)
    pm._app_overlap_info = tb
    with patch.object(tbu, "taskbar_layout_widgets_content", return_value=content), \
            patch.object(tbu, "current_dpi_for_hwnd", return_value=None), \
            patch.object(tbu, "widgets_button_enabled", return_value=True), \
            patch("netspeedtray.core.system_events.read_taskbar_alignment", return_value=0):
        ctx = pm._app_overlap_context()
    assert ctx.stepped == (4127.0, 4127.0 + W) and ctx.tray_spot == (4320.0, 4320.0 + W)
    assert ctx.tray_spot_clear is False
    assert ctx.x_origin == x0


# --- re-review round (PR #473) ---------------------------------------------------------------------

def test_edge_fallback_without_a_monitor_rect_converts_around_the_screen_origin(monkeypatch):
    """get_edge_position's last resort (no monitor_rect): a TOP bar on the 150 % monitor above the
    primary, (0, -2160, 3840, -2088), read as top -1440 against the screen top -2160 and came out BOTTOM."""
    _mixed_dpi_screens(monkeypatch)
    monkeypatch.setattr(tbu, "monitor_device_name_for_hwnd", lambda h: None)
    tb = tbu.TaskbarInfo(hwnd=999, tray_hwnd=None, tasklist_rect=None, rect=(0, -2160, 3840, -2088),
                         screen_name=D2, screen_geometry=(0, -2160, 2560, 1440),
                         work_area=(0, -2088, 3840, 0), dpi_scale=1.5, is_primary=False, height=48)
    assert tb.get_edge_position() == E.TOP


def test_horizontal_path_rect_fallback_converts_around_the_screen_origin(probed_tray):
    """_calculate_horizontal_position's non-TOP/BOTTOM fallback took y from rect / scale."""
    tb = _offset_side_bar()
    tb.rect = (0, -2160, 3840, -2088)
    _x_, y = PositionCalculator()._calculate_horizontal_position(tb, (W, H), CFG, 1.5)
    assert y == -2160


def test_horizontal_drag_limit_task_list_converts_around_the_screen_origin(probed_tray):
    """constrain_drag_position: a classic task list ending at physical -840 on a 150 % screen at
    native x -3840 ends at logical -1840 (tasklist / scale said -560)."""
    from PyQt6.QtCore import QPoint, QSize
    tb = _secondary_bar(x0=-3840, width=2560, dpi=1.5)
    tb.tasklist_rect = (-3840, 1548, -840, 1620)
    d = PositionCalculator().constrain_drag_position(QPoint(-3000, 0), tb, QSize(W, H))
    assert d.x() == -1840 + constants.layout.DEFAULT_PADDING


def test_vertical_drag_limit_task_list_converts_around_the_screen_origin(probed_tray):
    """The side-bar twin: a classic (ExplorerPatcher-style) RIGHT bar on the 150 % monitor above the
    primary, task list ending at physical -1500 = logical -2160 + 660 / 1.5 = -1720 (not -1000)."""
    from PyQt6.QtCore import QPoint, QSize
    tb = _offset_side_bar()
    tb.tasklist_rect = (3768, -2160, 3840, -1500)
    d = PositionCalculator().constrain_drag_position(QPoint(2520, -2100), tb, QSize(48, 40))
    assert d.y() == -1720 + constants.layout.DEFAULT_PADDING


# --- VM run (#473): DISPLAY2 at 150 % above-left, RIGHT bars, weather on, mirrors on --------------------

def test_a_mirror_on_a_trayless_side_bar_keeps_its_layout_fresh(monkeypatch, probed_tray):
    """The DISPLAY2 mirror sat above the weather button, then 90 s after launch dropped onto it: nothing
    re-probed its SIDE bar (the background refresh was TOP/BOTTOM only), so the cached layout aged out."""
    tb = _offset_side_bar()
    tb.is_primary = False
    pm, calls = _manager(tb, monkeypatch)
    pm.get_calculated_position()
    assert calls == [tb.hwnd]
    pm._trayless_probe_at -= constants.timeouts.TRAYLESS_BAR_LAYOUT_REFRESH_S + 1
    pm.get_calculated_position()
    assert calls == [tb.hwnd, tb.hwnd]


def test_an_aged_out_side_bar_layout_keeps_the_widget_above_the_weather(layout, clock):
    """Even unrefreshed, an aged cache must never put the widget onto the weather button: the side
    bar's tray end stays the Widgets box's top."""
    from netspeedtray.core.position_manager import side_bar_geometry
    hwnd = 0xD2
    widgets = (3768, -400, 3840, -250)                       # "Top" alignment: just above the tray
    with patch.object(tbu, "_query_taskbar_layout_blocking",
                      side_effect=lambda h: ((3768, -1900, 3840, -1600), widgets, None, None, None)
                      if h == hwnd else (APPS[h], WIDGETS[h], None, None, CLOCKS[h])):
        _probe(hwnd)
        _probe(A)                                            # another bar takes the slot
    clock.now += tbu.LAYOUT_CACHE_MAX_AGE_S + 60
    tb = _offset_side_bar()
    tb.hwnd = hwnd
    with patch.object(tbu, "find_apps_rect", return_value=None):
        _apps, _src, tb.widgets_extent, _pending = tbu.taskbar_layout_for(hwnd)
    geo = side_bar_geometry(tb, 1.5)
    assert geo.end == -2160 + (2160 - 400) / 1.5             # the Widgets box's top, not the clock


def test_widgets_turned_off_drops_the_slot_bars_box_at_once(layout):
    """The primary main widget took ~15 s to move back by the clock after Widgets was turned off (mirrors
    ~1 s): the bar IN the slot kept answering its probed box until the next re-probe."""
    _probe(A)
    with patch.object(tbu, "_widgets_toggle_on", return_value=False):
        assert tbu.taskbar_layout_snapshot(A) == (APPS[A], None, False)
        assert tbu.taskbar_layout_for(A)[2] is None
    assert tbu.taskbar_layout_snapshot(A) == (APPS[A], WIDGETS[A], False)   # back on: as probed


def test_widgets_turned_off_drops_the_200_probes_cached_rect_at_once(monkeypatch):
    """... and the #200 probe's 30 s cache (widgets_rect, the fallback the primary steps by)."""
    monkeypatch.setattr(tbu, "_widgets_state", {"rect": (1606, 1032, 1758, 1080), "ts": 1e12,
                                                "querying": False, "hwnd": 0x1})
    monkeypatch.setattr(tbu, "_widgets_probe_enabled", True)
    assert tbu.get_widgets_rect(0x1) == (1606, 1032, 1758, 1080)
    monkeypatch.setattr(tbu, "_widgets_toggle_on", lambda: False, raising=False)
    assert tbu.get_widgets_rect(0x1) is None


# --- VM re-check (#473): Widgets turned back ON -------------------------------------------------------

def _note_toggle():
    """What a docked tray-less bar's PositionManager does each tick: read the Widgets toggle."""
    read = getattr(tbu, "widgets_toggle_generation", None)
    if read is not None:
        read()


class _Toggle:
    def __init__(self):
        self.on = True

    def __call__(self):
        return self.on


def test_widgets_back_on_answers_the_last_seen_box_until_a_probe_sees_it(layout, clock):
    """Turned back on, the mirrors sat on the weather text for ~17 s: the probes made while Widgets was
    off had found no button, and that answered until the next 30 s refresh."""
    toggle = _Toggle()
    seen = {"widgets": WIDGETS[A]}

    def query(h):
        return APPS[h], (seen["widgets"] if h == A else WIDGETS[h]), None, None, CLOCKS[h]

    with patch.object(tbu, "_widgets_toggle_on", toggle), \
            patch.object(tbu, "_query_taskbar_layout_blocking", side_effect=query):
        _note_toggle()
        _probe(A)
        _probe(B)                                        # A is out of the slot, like a mirror's bar
        toggle.on, seen["widgets"] = False, None         # turned off: the button is gone
        _note_toggle()
        REAL_BACKGROUND_PROBE(A)
        tbu._layout_worker()
        assert tbu.taskbar_layout_snapshot(A)[1] is None
        toggle.on = True                                 # turned back on
        _note_toggle()
        assert tbu.taskbar_layout_snapshot(A)[1] == WIDGETS[A]      # the last seen box, at once
        REAL_BACKGROUND_PROBE(A)                         # Explorer has not drawn it yet
        tbu._layout_worker()
        assert tbu.taskbar_layout_snapshot(A)[1] == WIDGETS[A]
        clock.now += constants.timeouts.WIDGETS_REENABLE_GRACE_S + 1
        moved = (1500, 1032, 1640, 1080)
        seen["widgets"] = moved                          # drawn, a little to the left this time
        REAL_BACKGROUND_PROBE(A)
        tbu._layout_worker()
        assert tbu.taskbar_layout_snapshot(A)[1] == moved
        seen["widgets"] = None                           # on, past the grace, really no button
        REAL_BACKGROUND_PROBE(A)
        tbu._layout_worker()
        assert tbu.taskbar_layout_snapshot(A)[1] is None


def test_widgets_back_on_reprobes_every_trayless_bar_at_once_and_after_the_grace(monkeypatch, probed_tray):
    toggle = _Toggle()
    monkeypatch.setattr(tbu, "_widgets_toggle_on", toggle)
    tb = _secondary_bar(widgets=(1690, 1838))
    tb.is_primary = False
    pm, calls = _manager(tb, monkeypatch)
    pm.get_calculated_position()
    assert calls == [tb.hwnd]
    toggle.on = False
    pm.get_calculated_position()                         # turned off: re-probed at once
    assert len(calls) == 2
    toggle.on = True
    pm.get_calculated_position()                         # turned back on: at once ...
    assert len(calls) == 3
    pm.get_calculated_position()
    assert len(calls) == 3                               # ... not every tick ...
    tbu._widgets_flip["on_at"] -= constants.timeouts.WIDGETS_REENABLE_GRACE_S + 1
    pm._trayless_probe_at -= constants.timeouts.WIDGETS_REENABLE_GRACE_S + 1
    pm.get_calculated_position()                         # ... and once more after the grace
    assert len(calls) == 4
    pm.get_calculated_position()
    assert len(calls) == 4



# --- review of cd740cce ------------------------------------------------------------------------------

def test_an_aged_primary_box_yields_to_the_fresher_200_probe(monkeypatch, clock):
    """Review 1: the aged-box rule kept the primary's old Widgets box for good (the primary gets no
    background refresh), and widgets_button_span_x preferred it over the #200 probe's 30 s rect - a
    left-aligned primary with the yield off could sit on longer weather text. Tray-less bars keep it."""
    _mixed_dpi_screens(monkeypatch)
    _fake_win32(monkeypatch)
    monkeypatch.setattr(tbu, "taskbar_layout_for", REAL_LAYOUT_FOR)
    monkeypatch.setattr(tbu, "find_apps_rect", lambda h: None)
    fresh = (1500, 1008, 1660, 1080)
    monkeypatch.setattr(tbu, "get_widgets_rect", lambda h: fresh)
    old_box = (1606, 1008, 1758, 1080)
    sec_box = (3000, -72, 3150, 0)
    tbu._layout_state.update(FRESH)
    with tbu._layout_lock:
        tbu._store_landed_layout(PRIMARY_HWND, {"widgets": old_box})
        tbu._store_landed_layout(SECONDARY_HWND, {"widgets": sec_box})
    tbu._layout_state["hwnd"] = 0xBEEF                    # a mirror's bar holds the slot
    clock.now += tbu.LAYOUT_CACHE_MAX_AGE_S + 1
    bars = _bars_by_hwnd()
    assert bars[PRIMARY_HWND].widgets_extent is None and bars[PRIMARY_HWND].widgets_rect == fresh
    assert bars[SECONDARY_HWND].widgets_extent == sec_box  # the tray-less bar keeps its aged box
    tbu._layout_state.update(FRESH)


def test_a_stand_in_box_expires_when_no_probe_confirms_it(layout, clock):
    """Review 2: the last seen box stands in after Widgets is turned back on, but not for ever - a
    button that moved while it was off (alignment changed) must not linger."""
    toggle = _Toggle()
    with patch.object(tbu, "_widgets_toggle_on", toggle):
        _note_toggle()
        _probe(A)
        _probe(B)
        toggle.on = False
        _note_toggle()
        with patch.object(tbu, "_query_taskbar_layout_blocking",
                          side_effect=lambda h: (APPS[h], None, None, None, CLOCKS[h])):
            REAL_BACKGROUND_PROBE(A)
            tbu._layout_worker()
        toggle.on = True
        _note_toggle()
        assert tbu.taskbar_layout_snapshot(A)[1] == WIDGETS[A]
        clock.now += getattr(constants.timeouts, "WIDGETS_INTERIM_MAX_S", 10.0) + 1
        assert tbu.taskbar_layout_snapshot(A)[1] is None


def test_the_primary_gets_one_follow_up_probe_for_a_stand_in_box(monkeypatch, layout, clock):
    """Review 2: the primary (no periodic refresh) holding a stand-in box at the grace's end gets one
    probe to confirm or replace it."""
    toggle = _Toggle()
    monkeypatch.setattr(tbu, "_widgets_toggle_on", toggle)
    tb = _secondary_bar(widgets=(1690, 1838))
    tb.is_primary = True
    pm, calls = _manager(tb, monkeypatch)
    _note_toggle()
    with patch.object(tbu, "_query_taskbar_layout_blocking",
                      side_effect=lambda h: (APPS[A], WIDGETS[A], None, None, CLOCKS[A])):
        _probe(tb.hwnd)
    _probe(B)
    toggle.on = False
    _note_toggle()
    with patch.object(tbu, "_query_taskbar_layout_blocking",
                      side_effect=lambda h: (APPS[A], None, None, None, CLOCKS[A])):
        _probe(tb.hwnd, keep_last=True)                  # probed while off: no button
    _probe(B)
    toggle.on = True
    pm.get_calculated_position()
    assert calls == []                                   # inside the grace: nothing yet
    clock.now += constants.timeouts.WIDGETS_REENABLE_GRACE_S + 0.5
    pm.get_calculated_position()
    assert calls == [tb.hwnd]
    pm.get_calculated_position()
    assert calls == [tb.hwnd]                            # once


def test_the_widgets_toggle_read_is_memoised(clock, monkeypatch):
    """Review 3: read 10-30 times per bar per tick, often under _layout_lock."""
    reads = []

    def reader():
        reads.append(1)
        return True

    memo = getattr(tbu, "_widgets_toggle_memo", None)
    if isinstance(memo, dict):
        memo.update(at=None, on=True)
    elif memo is not None:
        monkeypatch.setattr(tbu, "_widgets_toggle_memo", (None, True))
    with patch.object(tbu, "_widgets_button_enabled", side_effect=reader):
        for _ in range(5):
            assert REAL_TOGGLE() is True
        assert len(reads) == 1
        clock.now += 0.3
        REAL_TOGGLE()
        assert len(reads) == 2



# --- VM confirm of cd740cce: Taskbar position bottom -> Right while running -------------------------------

def test_slot_requests_from_several_bars_in_one_tick_all_get_probed(layout):
    """Every widget and mirror re-docks in the same tick; each asked the slot for its new bar, and every
    request but the last was superseded before its probe started - the DISPLAY2 mirror had no layout
    (on the weather button) until the next 30 s refresh."""
    tbu.request_taskbar_layout_probe(A)
    tbu.request_taskbar_layout_probe(B)
    tbu.request_taskbar_layout_probe(C)
    tbu._layout_worker()
    for h in (A, B, C):
        assert tbu.taskbar_layout_snapshot(h)[:2] == (APPS[h], WIDGETS[h]), hex(h)


def test_a_request_superseded_mid_probe_is_not_probed_twice(layout):
    calls = []

    def query(h):
        calls.append(h)
        if h == A and len(calls) == 1:
            tbu.request_taskbar_layout_probe(B)         # arrives while A's probe runs
        return APPS[h], WIDGETS[h], None, None, CLOCKS[h]

    with patch.object(tbu, "_query_taskbar_layout_blocking", side_effect=query):
        tbu.request_taskbar_layout_probe(A)
        tbu._layout_worker()
    assert calls == [A, B]
    assert tbu.taskbar_layout_snapshot(A)[:2] == (APPS[A], WIDGETS[A])


def test_a_layout_probed_on_another_edge_is_not_served(monkeypatch, clock):
    """bottom -> Right: the box cached for the bottom bar must not place the mirror on the right bar."""
    _mixed_dpi_screens(monkeypatch)
    _fake_win32(monkeypatch, secondary_bar=(3768, -2160, 3840, 0))     # the bar is on the RIGHT now
    monkeypatch.setattr(tbu, "taskbar_layout_for", REAL_LAYOUT_FOR)
    monkeypatch.setattr(tbu, "find_apps_rect", lambda h: None)
    tbu._layout_state.update(FRESH)
    with tbu._layout_lock:
        tbu._store_landed_layout(SECONDARY_HWND, {"widgets": (3000, -72, 3150, 0),
                                                   "apps": (100, -72, 900, 0), "bar": SECONDARY_BAR})
    tbu._layout_state["hwnd"] = 0xBEEF
    bar = _bars_by_hwnd()[SECONDARY_HWND]
    assert bar.widgets_extent is None and bar.apps_extent is None
    with tbu._layout_lock:                                               # same edge: served
        tbu._store_landed_layout(SECONDARY_HWND, {"widgets": (3768, -400, 3840, -250),
                                                   "bar": (3768, -2160, 3840, 0)})
    assert _bars_by_hwnd()[SECONDARY_HWND].widgets_extent == (3768, -400, 3840, -250)
    tbu._layout_state.update(FRESH)



# --- review of a8e03adb ------------------------------------------------------------------------------

def _primary_and_secondary_cache(monkeypatch, secondary_bar, stored_primary, stored_secondary):
    """The issue's two bars, each with a cached layout probed on ``stored_*`` (the primary's window is
    PRIMARY_BAR, 72 px; the secondary's ``secondary_bar``), out of the slot."""
    _mixed_dpi_screens(monkeypatch)
    _fake_win32(monkeypatch, secondary_bar=secondary_bar)
    monkeypatch.setattr(tbu, "taskbar_layout_for", REAL_LAYOUT_FOR)
    monkeypatch.setattr(tbu, "find_apps_rect", lambda h: None)
    tbu._layout_state.update(FRESH)
    with tbu._layout_lock:
        tbu._store_landed_layout(PRIMARY_HWND, {"widgets": (1606, 1008, 1758, 1080), "bar": stored_primary})
        tbu._store_landed_layout(SECONDARY_HWND, {"widgets": (3000, -72, 3150, 0), "bar": stored_secondary})
    tbu._layout_state["hwnd"] = 0xBEEF                   # another bar holds the slot


def test_the_primary_keeps_its_box_when_its_bar_grew_or_slid(monkeypatch):
    """Review 1: containment read a Small -> Default (or a mid-slide auto-hide) primary as another
    layout and dropped its Widgets box - the primary's placement must not change."""
    _primary_and_secondary_cache(monkeypatch, SECONDARY_BAR,
                                 stored_primary=(0, 1032, 1920, 1080),        # probed at 48 px
                                 stored_secondary=SECONDARY_BAR)
    bars = _bars_by_hwnd()                                 # the primary is 72 px now
    assert bars[PRIMARY_HWND].widgets_extent == (1606, 1008, 1758, 1080)
    assert bars[SECONDARY_HWND].widgets_extent == (3000, -72, 3150, 0)
    tbu._layout_state.update(FRESH)


def test_a_trayless_bar_that_shrank_does_not_serve_the_old_layout(monkeypatch):
    """Review 1: a bar that SHRANK (Default -> Small) still matched by containment."""
    _primary_and_secondary_cache(monkeypatch, (0, -48, 3840, 0),
                                 stored_primary=PRIMARY_BAR, stored_secondary=SECONDARY_BAR)
    assert _bars_by_hwnd()[SECONDARY_HWND].widgets_extent is None
    tbu._layout_state.update(FRESH)


def test_a_superseded_request_keeps_its_caller_waiting_and_then_lands(layout):
    """Review 2: a yield / side-bar watcher request moved to the background by another bar's request
    read "not pending, nothing landed" at once - its caller gave up on a probe that was still coming."""
    before = tbu.taskbar_layout_landed_count(A)
    tbu.request_taskbar_layout_probe(A, keep_last=True)
    tbu.request_taskbar_layout_probe(B)                  # supersedes A before its probe started
    assert tbu.taskbar_layout_snapshot(A)[2] is True     # still pending for its caller
    tbu._layout_worker()
    assert tbu.taskbar_layout_snapshot(A)[2] is False
    assert tbu.taskbar_layout_landed_count(A) > before   # ... and it counts as landed
    assert tbu.taskbar_layout_snapshot(A)[:2] == (APPS[A], WIDGETS[A])


def test_a_superseded_request_that_never_lands_times_out(layout, clock):
    tbu.request_taskbar_layout_probe(A)
    tbu.request_taskbar_layout_probe(B)
    assert tbu.taskbar_layout_snapshot(A)[2] is True
    clock.now += tbu.LAYOUT_PROBE_TIMEOUT_S + 1
    assert tbu.taskbar_layout_snapshot(A)[2] is False
