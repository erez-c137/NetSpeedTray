"""Side-taskbar Phase B: keep the widget clear of the app buttons on a native Win11 LEFT/RIGHT bar.

Design: Docs/reference/side-taskbar-design.md §3. Live findings (owner's PC, 2026-10-20): on a native
side bar the widget sat above the tray and covered app buttons, because Win11 has no ToolbarWindow32
task list - ``tasklist_rect`` is None and nothing knew where the apps were.

1. ``TaskbarInfo.apps_extent``: the UIA union of Start/Search/app buttons (probed on demand on a
   background thread), else the MSTaskSwWClass rect. Probed live on this machine's bottom bar
   (2026-10-21): UIA 652..1269 vs the HWND 651..1136 - the HWND rect lags behind the real buttons.
2. Placement: tray end if the gap above the tray fits; else the top gap (Widgets/bar top .. first
   app); else tray end + a one-time heads-up. Decided at startup / dock change / reset only.
3. Drag limits: y in [Widgets bottom (or bar top), tray top - #161 gap]; a stale tray_offset_y is
   clamped into the same range.
4. Reset position: clears the drag offsets too (it used to clear only position_x/y, and nothing
   called it), and is wired to a right-click menu item shown once the widget has been moved.

ExplorerPatcher-style bars (a real ToolbarWindow32 task list) keep today's behaviour exactly.
"""
import threading
import time
import types
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QPoint, QRect, QSize
from PyQt6.QtWidgets import QApplication

from netspeedtray import constants
from netspeedtray.core import position_manager as pm_mod
from netspeedtray.core.position_manager import (
    PositionCalculator, PositionManager, SidePlacement, WindowState, choose_side_placement,
    side_bar_geometry, side_bar_y_limits, user_placed_side_offset,
)
from netspeedtray.utils import taskbar_utils, uia_widgets
from netspeedtray.utils.taskbar_utils import TaskbarInfo

LEFT, RIGHT = constants.taskbar.edge.LEFT, constants.taskbar.edge.RIGHT
BOTTOM = constants.taskbar.edge.BOTTOM
GAP = constants.layout.TRAY_EDGE_MIN_GAP_PX
DEFAULT_Y = constants.config.defaults.DEFAULT_TRAY_OFFSET_Y
H = 56                       # a compact side-bar widget (3 rows + margins)
TRAY_TOP = 908               # the owner's tray on a 1080 px bar


@pytest.fixture(scope="module", autouse=True)
def q_app():
    return QApplication.instance() or QApplication([])


def _x0(edge, thickness):
    return 1920 - thickness if edge == RIGHT else 0


def _screen(edge, thickness):
    s = MagicMock()
    s.geometry.return_value = QRect(0, 0, 1920, 1080)
    if edge == RIGHT:
        s.availableGeometry.return_value = QRect(0, 0, 1920 - thickness, 1080)
    else:
        s.availableGeometry.return_value = QRect(thickness, 0, 1920 - thickness, 1080)
    return s


def _span(edge, thickness, top, bottom):
    x = _x0(edge, thickness)
    return (x, top, x + thickness, bottom)


def _taskbar(edge=RIGHT, thickness=48, *, apps=None, widgets=(0, 120), tasklist=None, source="uia"):
    """A mocked native side taskbar. ``apps`` / ``widgets`` are (top, bottom) spans along the bar."""
    tb = MagicMock(spec=TaskbarInfo)
    tb.hwnd = 4242
    tb.dpi_scale = 1.0
    tb.rect = _span(edge, thickness, 0, 1080)
    tb.height = thickness
    tb.tasklist_rect = tasklist
    tb.apps_extent = _span(edge, thickness, *apps) if apps else None
    tb.apps_extent_source = source if apps else None
    tb.widgets_extent = _span(edge, thickness, *widgets) if widgets else None
    tb.widgets_rect = None
    tb.get_edge_position.return_value = edge
    tb.get_screen.return_value = _screen(edge, thickness)
    tb.get_tray_rect.return_value = _span(edge, thickness, TRAY_TOP, 1080)
    return tb


def _place(tb, config=None, h=H):
    """(placement, y) the calculator gives, with the placement decided from the taskbar as given."""
    config = {"tray_offset_y": DEFAULT_Y} if config is None else config
    geo = side_bar_geometry(tb, 1.0)
    placement = choose_side_placement(geo, h) if geo is not None else None
    pos = PositionCalculator().calculate_position(tb, (tb.height, h), config, side_placement=placement)
    return placement, pos


# --- 1. apps_extent -------------------------------------------------------------------------------

LIVE_BOTTOM_BAR = [   # this machine's bottom bar, 2026-10-21 (names/app ids not recorded)
    ("NetworkSpeedWidget", "QApplication.NetworkSpeedWidget", (1496, 1032, 1686, 1080)),
    ("Taskbar.TaskbarFrameAutomationPeer", "TaskbarFrame", (0, 1032, 1920, 1080)),
    ("ToggleButton", "WidgetsButton", (6, 1032, 158, 1080)),
    ("TextBlock", "WidgetsOverlayText", (84, 1041, 99, 1054)),
    ("ToggleButton", "StartButton", (652, 1032, 697, 1080)),
    ("ToggleButton", "SearchButton", (697, 1032, 741, 1080)),
    ("Microsoft.UI.Xaml.Controls.AnimatedVisualPlayer", "Icon", (707, 1044, 731, 1068)),
] + [("Taskbar.TaskListButtonAutomationPeer", "Appid:x", (741 + 44 * i, 1032, 785 + 44 * i, 1080))
     for i in range(12)] + [
    ("SystemTray.NormalButton", "SystemTrayIcon", (1694, 1032, 1726, 1080)),
    ("SystemTray.ShowDesktopButton", "SystemTrayIcon", (1908, 1032, 1920, 1080)),
    ("TrayNotifyWnd", "", (1694, 1032, 1920, 1080)),
    ("MSTaskSwWClass", "", (651, 1032, 1136, 1080)),
    ("Windows.UI.Input.InputSite.WindowClass", "", (0, 1032, 0, 1032)),
]


def test_live_probe_elements_give_the_real_app_span_and_the_widgets_button():
    apps, widgets = uia_widgets.taskbar_layout_from_elements(LIVE_BOTTOM_BAR)
    assert apps == (652, 1032, 1269, 1080)    # Start .. 12th app; not our widget, not the tray
    assert widgets == (6, 1032, 158, 1080)


def test_side_bar_elements_union_vertically_and_skip_empty_buttons():
    els = [("ToggleButton", "WidgetsButton", (1872, 0, 1920, 110)),
           ("ToggleButton", "StartButton", (1872, 300, 1920, 344)),
           ("Taskbar.TaskListButtonAutomationPeer", "a", (1872, 344, 1920, 388)),
           ("Taskbar.TaskListButtonAutomationPeer", "b", (1872, 700, 1920, 700)),   # collapsed
           ("Taskbar.TaskListButtonAutomationPeer", "c", (1872, 388, 1920, 432))]
    assert uia_widgets.taskbar_layout_from_elements(els) == ((1872, 300, 1920, 432), (1872, 0, 1920, 110))
    assert uia_widgets.taskbar_layout_from_elements([]) == (None, None)


def test_hwnd_fallback_prefers_mstaskswwclass_then_rebar():
    rects = {11: (651, 1032, 1136, 1080), 10: (600, 1032, 1200, 1080)}
    finds = {("ReBarWindow32",): 10, ("MSTaskSwWClass",): 11}
    with patch.object(taskbar_utils.win32gui, "FindWindowEx",
                      side_effect=lambda parent, after, cls, name: finds.get((cls,), 0)), \
            patch.object(taskbar_utils.win32gui, "GetWindowRect", side_effect=lambda h: rects[h]):
        assert taskbar_utils.find_apps_rect(1) == (651, 1032, 1136, 1080)
        finds[("MSTaskSwWClass",)] = 0
        assert taskbar_utils.find_apps_rect(1) == (600, 1032, 1200, 1080)
        finds[("ReBarWindow32",)] = 0
        assert taskbar_utils.find_apps_rect(1) is None
    with patch.object(taskbar_utils.win32gui, "FindWindowEx", side_effect=RuntimeError("gone")):
        assert taskbar_utils.find_apps_rect(1) is None


def test_valid_rect_rejects_mocks_empties_and_bools():
    assert taskbar_utils.valid_rect((1, 2, 3, 4)) == (1, 2, 3, 4)
    for bad in (None, MagicMock(), (1, 2, 1, 4), (1, 2, 3), (True, 0, 5, 5), "abcd"):
        assert taskbar_utils.valid_rect(bad) is None


def test_layout_probe_runs_in_the_background_and_feeds_get_apps_extent():
    done = []
    gate = threading.Event()

    def fake_query(hwnd):
        gate.wait(2)
        done.append(hwnd)
        return ((1872, 300, 1920, 900), (1872, 0, 1920, 120))

    with patch.object(taskbar_utils, "_query_taskbar_layout_blocking", side_effect=fake_query), \
            patch.object(taskbar_utils, "find_apps_rect", return_value=(1872, 300, 1920, 500)):
        taskbar_utils.request_taskbar_layout_probe(777)
        assert taskbar_utils.taskbar_layout_probe_pending(777)        # the caller never waited
        assert taskbar_utils.get_apps_extent(777) == ((1872, 300, 1920, 500), "hwnd")
        gate.set()
        for _ in range(200):
            if not taskbar_utils.taskbar_layout_probe_pending(777):
                break
            time.sleep(0.01)
        assert not taskbar_utils.taskbar_layout_probe_pending(777)
        assert taskbar_utils.get_apps_extent(777) == ((1872, 300, 1920, 900), "uia")
        assert taskbar_utils.cached_taskbar_layout(777)[1] == (1872, 0, 1920, 120)
        assert taskbar_utils.cached_taskbar_layout(778) == (None, None)    # another taskbar
        assert taskbar_utils.get_apps_extent(0) == (None, None)


def test_an_older_probe_cannot_overwrite_a_newer_request():
    entered, first_gate = threading.Event(), threading.Event()

    def fake_query(hwnd):
        if not entered.is_set():
            entered.set()
            first_gate.wait(2)            # the first probe lands AFTER the second request's
            return ((0, 1, 2, 3), None)
        return ((0, 10, 2, 30), None)

    with patch.object(taskbar_utils, "_query_taskbar_layout_blocking", side_effect=fake_query):
        taskbar_utils.request_taskbar_layout_probe(55)
        assert entered.wait(2)
        taskbar_utils.request_taskbar_layout_probe(55)
        for _ in range(200):
            if not taskbar_utils.taskbar_layout_probe_pending(55):
                break
            time.sleep(0.01)
        first_gate.set()
        time.sleep(0.05)
    assert taskbar_utils.cached_taskbar_layout(55)[0] == (0, 10, 2, 30)


def test_taskbar_info_apps_fields_default_to_none():
    tb = TaskbarInfo(hwnd=0, tray_hwnd=None, tasklist_rect=None, rect=(0, 0, 0, 0), screen_name="x",
                     screen_geometry=(0, 0, 1, 1), work_area=(0, 0, 1, 1), dpi_scale=1.0,
                     is_primary=True, height=48)
    assert (tb.apps_extent, tb.apps_extent_source, tb.widgets_extent) == (None, None, None)


# --- 2. Placement policy matrix -------------------------------------------------------------------

@pytest.mark.parametrize("edge", [LEFT, RIGHT])
@pytest.mark.parametrize("thickness", [48, 32])
def test_gap_above_the_tray_fits_keeps_the_tray_end(edge, thickness):
    placement, pos = _place(_taskbar(edge, thickness, apps=(200, 700)))
    assert placement.mode == "tray"
    assert pos.y == TRAY_TOP - GAP - H                      # the #161 gap, not the old 3 px


@pytest.mark.parametrize("edge", [LEFT, RIGHT])
@pytest.mark.parametrize("thickness", [48, 32])
def test_apps_reaching_the_tray_move_it_into_the_top_gap(edge, thickness):
    placement, pos = _place(_taskbar(edge, thickness, apps=(300, 900)))
    assert placement.mode == "top"
    assert 120 <= pos.y and pos.y + H <= 300                 # below the Widgets button, above Start
    assert pos.y == round(120 + (300 - 120 - H) / 2)        # centred in the gap
    x0 = _x0(edge, thickness)
    assert x0 <= pos.x and pos.x + thickness <= x0 + thickness


@pytest.mark.parametrize("edge", [LEFT, RIGHT])
def test_no_room_anywhere_stays_at_the_tray_and_flags_it(edge):
    placement, pos = _place(_taskbar(edge, 48, apps=(150, 900)))
    assert placement.mode == "tray_no_room"
    assert pos.y == TRAY_TOP - GAP - H


def test_without_a_widgets_button_the_top_gap_starts_at_the_bar_top():
    placement, pos = _place(_taskbar(apps=(100, 900), widgets=None))
    assert placement.mode == "top" and pos.y == round((100 - H) / 2)


def test_unknown_apps_extent_keeps_todays_tray_end():
    placement, pos = _place(_taskbar(apps=None))
    assert placement.mode == "tray" and pos.y == TRAY_TOP - GAP - H


def test_a_users_drag_wins_over_the_placement():
    tb = _taskbar(apps=(300, 900))
    placement, pos = _place(tb, {"tray_offset_y": 400})
    assert placement.mode == "top"                          # what auto-placement would do ...
    assert pos.y == TRAY_TOP - H - 400                      # ... but the drag is honoured
    assert user_placed_side_offset({"tray_offset_y": 400})
    assert not user_placed_side_offset({"tray_offset_y": DEFAULT_Y}) and not user_placed_side_offset({})


def test_horizontal_bars_are_untouched():
    tb = MagicMock(spec=TaskbarInfo)
    tb.hwnd, tb.dpi_scale, tb.rect, tb.height, tb.tasklist_rect = 1, 1.0, (0, 1032, 1920, 1080), 48, None
    tb.apps_extent = (652, 1032, 1269, 1080)
    tb.get_edge_position.return_value = BOTTOM
    s = MagicMock()
    s.geometry.return_value = QRect(0, 0, 1920, 1080)
    s.availableGeometry.return_value = QRect(0, 0, 1920, 1032)
    tb.get_screen.return_value = s
    tb.get_tray_rect.return_value = (1694, 1032, 1920, 1080)
    pos = PositionCalculator().calculate_position(tb, (190, 48), {"tray_offset_x": 0})
    assert pos.x == 1694 - 190 - GAP                       # no app-icon snap from apps_extent
    pm = _manager(tb)
    with patch.object(taskbar_utils, "request_taskbar_layout_probe") as probe:
        assert pm._side_placement_for(tb, 48) is None
    probe.assert_not_called()                              # a bottom bar never runs the UIA probe


# --- 3. The decision lifecycle: rarely, never per app launch --------------------------------------

def _manager(tb, config=None):
    widget = MagicMock()
    widget.width.return_value = tb.height
    widget.height.return_value = H
    widget.pos.return_value = QPoint(-1, -1)
    state = WindowState(config={"tray_offset_y": DEFAULT_Y} if config is None else config,
                        widget=widget, taskbar_info=tb)
    return PositionManager(state)


@contextmanager
def _layout(*, hwnd=None, uia=None, widgets=(0, 120), sync=False, edge=RIGHT, thickness=48):
    """Drive the REAL layout-probe cache with a controllable worker.

    ``hwnd`` / ``uia`` are (top, bottom) spans: what MSTaskSwWClass reports and what the UIA probe
    finds. The worker runs synchronously on start (``sync``) or when the test calls ``ctl.land()``.
    ``ctl.uia = None`` with ``ctl.fail = True`` makes the probe find nothing."""
    ctl = types.SimpleNamespace(hwnd=hwnd, uia=uia, widgets=widgets, fail=False, workers=[], requests=0)

    def span(s):
        return _span(edge, thickness, *s) if s else None

    def query(_h):
        return None if ctl.fail else (span(ctl.uia), span(ctl.widgets))

    def start():
        if sync:
            taskbar_utils._layout_worker()
        else:
            ctl.workers.append(taskbar_utils._layout_worker)

    def land():
        while ctl.workers:
            ctl.workers.pop(0)()

    real_request = taskbar_utils.request_taskbar_layout_probe

    def request(h, keep_last=False):
        ctl.requests += 1
        real_request(h, keep_last=keep_last)

    ctl.land = land
    fresh = {"hwnd": 0, "apps": None, "widgets": None, "pending": False, "done": False, "gen": 0,
             "running": False, "started": 0.0}
    taskbar_utils._layout_state.update(fresh)
    try:
        with patch.object(taskbar_utils, "_start_layout_worker", side_effect=start), \
                patch.object(taskbar_utils, "_query_taskbar_layout_blocking", side_effect=query), \
                patch.object(taskbar_utils, "find_apps_rect", side_effect=lambda h: span(ctl.hwnd)), \
                patch.object(taskbar_utils, "request_taskbar_layout_probe", side_effect=request):
            yield ctl
    finally:
        taskbar_utils._layout_state.update(fresh)


def test_a_probe_that_already_landed_is_used_directly():
    """The reviewer's race.py set-up with the probe thread run synchronously: UIA says the apps reach
    the tray (the HWND rect lags and says there is room) - the decision must be the UIA one, final."""
    tb = _taskbar()
    pm = _manager(tb)
    with _layout(hwnd=(300, 600), uia=(300, 905), widgets=None, sync=True):
        placement = pm._side_placement_for(tb, H)
    assert placement.mode == "top" and pm._side_placement_source == "uia"
    assert not pm._side_placement_provisional


def test_a_probe_landing_mid_decision_cannot_make_a_lagging_decision_final():
    """The race: the probe lands after the decision read the HWND rect but before it read 'pending'.
    The decision taken from the lagging rect must stay provisional and be redone on the next tick."""
    tb = _taskbar()
    pm = _manager(tb)
    with _layout(hwnd=(300, 600), uia=(150, 905)) as ctl:
        def lagging_rect_then_land(_h):
            ctl.land()                                       # the probe lands right now
            return _span(RIGHT, 48, 300, 600)
        with patch.object(taskbar_utils, "find_apps_rect", side_effect=lagging_rect_then_land):
            first = pm._side_placement_for(tb, H)
        assert first.mode == "tray" and pm._side_placement_provisional
        second = pm._side_placement_for(tb, H)
    assert second.mode == "tray_no_room" and pm._side_placement_source == "uia"


def test_placement_is_decided_once_and_not_redone_when_apps_come_and_go():
    tb = _taskbar()
    pm = _manager(tb)
    with _layout(uia=(300, 900)) as ctl:
        pm.get_calculated_position()                          # provisional (probe in flight)
        ctl.land()
        first = pm.get_calculated_position()                  # the UIA decision
        ctl.uia = (100, 1000)                                 # 20 more apps launched ...
        tb.apps_extent = _span(RIGHT, 48, 100, 1000)          # ... and every tick's TaskbarInfo says so
        again = pm.get_calculated_position()
    assert ctl.requests == 1                                  # probed once, at the first sight
    assert first == again                                     # no jump per app launch (#200 jitter)
    assert first.y == round(120 + (300 - 120 - H) / 2)


def test_dock_change_re_decides_and_re_probes():
    tb = _taskbar()
    pm = _manager(tb)
    with _layout(uia=(300, 900), sync=True) as ctl:
        pm.get_calculated_position()
        ctl.uia = (200, 600)
        tb.height = 32                                         # live switch to Small
        tb.rect = _span(RIGHT, 32, 0, 1080)
        tb.get_screen.return_value = _screen(RIGHT, 32)
        pm._state.widget.width.return_value = 32
        pos = pm.get_calculated_position()
    assert ctl.requests == 2
    assert pos.y == TRAY_TOP - GAP - H                        # the new layout leaves room by the tray


def test_leaving_the_side_bar_and_coming_back_probes_afresh():
    tb = _taskbar()
    pm = _manager(tb)
    with _layout(uia=(300, 900), sync=True) as ctl:
        pm._side_placement_for(tb, H)
        tb.get_edge_position.return_value = BOTTOM             # moved to the bottom ...
        assert pm._side_placement_for(tb, H) is None
        tb.get_edge_position.return_value = RIGHT              # ... and back, same dock signature
        pm._side_placement_for(tb, H)
    assert ctl.requests == 2


def test_a_provisional_decision_is_redone_once_when_the_uia_probe_lands():
    tb = _taskbar()
    pm = _manager(tb)
    seen = []
    pm.on_side_no_room = lambda: seen.append(1)
    with _layout(hwnd=(200, 600), uia=(150, 900)) as ctl:    # the lagging HWND rect says "room"
        assert pm._side_placement_for(tb, H).mode == "tray"
        assert pm._side_placement_provisional
        assert pm._side_placement_for(tb, H).mode == "tray"   # still in flight: unchanged
        ctl.land()
        assert pm._side_placement_for(tb, H).mode == "tray_no_room"
        assert not pm._side_placement_provisional
        pm._side_placement_for(tb, H)
    assert seen == [1]                                         # heads-up once, on the final decision


def test_a_failed_probe_makes_the_hwnd_decision_final_without_looping():
    tb = _taskbar()
    pm = _manager(tb)
    seen = []
    pm.on_side_no_room = lambda: seen.append(1)
    with _layout(hwnd=(40, 900)) as ctl, patch.object(pm, "_decide_side_placement",
                                                       wraps=pm._decide_side_placement) as decide:
        ctl.fail = True
        pm._side_placement_for(tb, H)
        ctl.land()
        for _ in range(3):
            pm._side_placement_for(tb, H)
    assert decide.call_count == 2                              # first sight + once when it finished
    assert pm._side_placement.mode == "tray_no_room" and not pm._side_placement_provisional
    assert seen == [1]


def test_a_probe_that_hangs_times_out_and_its_late_result_is_dropped():
    with _layout(uia=(300, 900)) as ctl:
        taskbar_utils.request_taskbar_layout_probe(4242)
        assert taskbar_utils.taskbar_layout_snapshot(4242)[2] is True
        with patch.object(taskbar_utils, "LAYOUT_PROBE_TIMEOUT_S", -1.0):
            assert taskbar_utils.taskbar_layout_snapshot(4242) == (None, None, False)
        ctl.land()                                             # the hung call finally returns
        assert taskbar_utils.cached_taskbar_layout(4242) == (None, None)


def test_many_requests_share_one_worker_and_the_newest_wins():
    with _layout(uia=(300, 900)) as ctl:
        for _ in range(20):
            taskbar_utils.request_taskbar_layout_probe(4242)
        assert len(ctl.workers) == 1                            # one thread, not twenty
        ctl.land()
        assert taskbar_utils.cached_taskbar_layout(4242)[0] == _span(RIGHT, 48, 300, 900)
        assert not taskbar_utils.taskbar_layout_probe_pending(4242)


def test_apps_and_widgets_come_from_one_snapshot():
    with _layout(hwnd=(300, 600), uia=(300, 905), widgets=(0, 110), sync=True):
        taskbar_utils.request_taskbar_layout_probe(4242)
        apps, source, widgets, pending = taskbar_utils.taskbar_layout_for(4242)
    assert (apps, source, widgets, pending) == (_span(RIGHT, 48, 300, 905), "uia",
                                                _span(RIGHT, 48, 0, 110), False)


def test_no_heads_up_while_the_users_own_drag_is_in_force():
    tb = _taskbar()
    pm = _manager(tb, {"tray_offset_y": 200})
    seen = []
    pm.on_side_no_room = lambda: seen.append(1)
    with _layout(uia=(150, 900), sync=True):
        pm.get_calculated_position()
    assert seen == []


def test_explorer_patcher_style_bars_keep_todays_behaviour():
    tb = _taskbar(apps=(150, 900), tasklist=_span(RIGHT, 48, 0, 400))
    assert side_bar_geometry(tb, 1.0) is None
    pm = _manager(tb, {"tray_offset_y": 709})
    with patch.object(taskbar_utils, "request_taskbar_layout_probe") as req:
        assert pm._side_placement_for(tb, H) is None
        pos = pm.get_calculated_position()
    req.assert_not_called()
    # Today's rule verbatim: tray_top - h - offset, snapped below the task list when above it.
    assert pos.y == 400 + constants.layout.DEFAULT_PADDING
    pos = PositionCalculator().calculate_position(tb, (48, H), {"tray_offset_y": 3})
    assert pos.y == TRAY_TOP - H - 3
    drag = PositionCalculator().constrain_drag_position(QPoint(0, 2000), tb, QSize(48, H))
    assert drag.y() == TRAY_TOP - H - constants.layout.DEFAULT_PADDING


# --- 4. Drag limits and the stale offset -----------------------------------------------------------

@pytest.mark.parametrize("edge", [LEFT, RIGHT])
@pytest.mark.parametrize("thickness", [48, 32])
def test_drag_stays_between_the_widgets_button_and_the_tray(edge, thickness):
    tb = _taskbar(edge, thickness, apps=(300, 900))
    calc = PositionCalculator()
    top = calc.constrain_drag_position(QPoint(900, -500), tb, QSize(thickness, H))
    bottom = calc.constrain_drag_position(QPoint(900, 5000), tb, QSize(thickness, H))
    mid = calc.constrain_drag_position(QPoint(900, 500), tb, QSize(thickness, H))
    assert top.y() == 120                                    # not onto the Widgets button
    assert bottom.y() == TRAY_TOP - GAP - H                  # not onto the tray / its chevron
    assert mid.y() == 500                                    # free in between (over apps: user's call)
    assert top.x() == bottom.x() == mid.x() == _x0(edge, thickness)   # x from the strip


def test_drag_limit_is_the_bar_top_without_a_widgets_button():
    tb = _taskbar(apps=(300, 900), widgets=None)
    assert PositionCalculator().constrain_drag_position(QPoint(0, -50), tb, QSize(48, H)).y() == 0


@pytest.mark.parametrize("offset,expect", [
    (709, 143),                                # the owner's config: 908 - 56 - 709 = 143 - inside
    (850, 120),                                # would land on the Widgets button -> below it
    (5000, 120),                               # far above the bar -> clamped onto it
    (-400, TRAY_TOP - GAP - H),                # below the tray -> tray end
])
def test_a_stale_tray_offset_y_is_clamped_onto_the_bar(offset, expect):
    _, pos = _place(_taskbar(apps=(300, 900)), {"tray_offset_y": offset})
    assert pos.y == expect


def test_limits_collapse_to_the_tray_end_when_the_widget_cannot_fit():
    geo = side_bar_geometry(_taskbar(apps=(300, 900)), 1.0)
    assert side_bar_y_limits(geo, 2000) == (0, 0)            # never above the bar top


# --- 5. Reset position -----------------------------------------------------------------------------

def test_position_manager_reset_clears_offsets_and_re_decides():
    tb = _taskbar(apps=(300, 900))
    cfg = {"tray_offset_y": 709, "tray_offset_x": 250, "position_x": 5, "position_y": 6}
    pm = _manager(tb, cfg)
    with _layout(uia=(300, 900), sync=True) as ctl, patch.object(pm, "ensure_topmost"), \
            patch.object(pm_mod, "get_taskbar_info", return_value=tb):
        pm.get_calculated_position()
        pm.reset_to_default()
    assert cfg == {"tray_offset_y": DEFAULT_Y, "tray_offset_x": constants.config.defaults.DEFAULT_TRAY_OFFSET_X,
                   "position_x": None, "position_y": None}
    assert ctl.requests == 2                                 # decided afresh after the reset
    y = pm._state.widget.move.call_args[0][1]
    assert y == round(120 + (300 - 120 - H) / 2)             # back to the automatic (top-gap) spot


def _reset_fake(config):
    from netspeedtray.views.widget.main import NetworkSpeedWidget as W
    fake = types.SimpleNamespace(config=config, logger=MagicMock(), position_manager=MagicMock(),
                                 update_config=MagicMock(), _hide_usage_hover_card=MagicMock())
    fake.reset_to_default_position = types.MethodType(W.reset_to_default_position, fake)
    fake.has_custom_position = types.MethodType(W.has_custom_position, fake)
    return fake


def test_widget_reset_persists_every_cleared_key():
    fake = _reset_fake({"tray_offset_y": 709})
    fake.reset_to_default_position()
    fake.position_manager.reset_to_default.assert_called_once()
    fake._hide_usage_hover_card.assert_called_once()
    (updates,), _ = fake.update_config.call_args
    assert updates == {"position_x": None, "position_y": None,
                       "tray_offset_x": constants.config.defaults.DEFAULT_TRAY_OFFSET_X,
                       "tray_offset_y": DEFAULT_Y}


@pytest.mark.parametrize("config,moved", [
    ({}, False),
    ({"tray_offset_y": DEFAULT_Y, "tray_offset_x": 0, "position_x": None}, False),
    ({"tray_offset_y": 709}, True),
    ({"tray_offset_x": 120}, True),
    ({"position_x": 10, "position_y": 20}, True),
])
def test_has_custom_position(config, moved):
    assert _reset_fake(config).has_custom_position() is moved


def test_reset_menu_item_shows_once_moved_and_triggers_the_reset():
    from PyQt6.QtWidgets import QWidget
    from netspeedtray.core.tray_manager import TrayIconManager
    from netspeedtray.constants.i18n import I18nStrings

    class W(QWidget):
        def __init__(self):
            super().__init__()
            self.config = {}
            self.moved = False
            self.reset_to_default_position = MagicMock()

        def has_custom_position(self):
            return self.moved

    w = W()
    m = TrayIconManager(w, I18nStrings("en_US"))
    m._load_and_set_icon = lambda: None
    m.initialize()
    assert m.reset_position_action.text() == "Reset P&osition"
    m._refresh_dynamic_items()
    assert not m.reset_position_action.isVisible() and not m.pause_separator.isVisible()
    w.moved = True
    m._refresh_dynamic_items()
    assert m.reset_position_action.isVisible() and m.pause_separator.isVisible()
    m.reset_position_action.trigger()
    w.reset_to_default_position.assert_called_once()


def test_no_room_heads_up_shows_once_ever():
    from netspeedtray.views.widget.main import NetworkSpeedWidget as W
    from netspeedtray.constants.i18n import I18nStrings
    cfg = {}
    fake = types.SimpleNamespace(config=cfg, logger=MagicMock(), i18n=I18nStrings("en_US"))
    fake.update_config = lambda u: cfg.update(u)
    with patch("netspeedtray.views.widget.main.QTimer.singleShot") as shot:
        W._maybe_show_side_no_room_notice(fake)
        W._maybe_show_side_no_room_notice(fake)
        fresh = types.SimpleNamespace(config=dict(cfg), logger=MagicMock(), i18n=fake.i18n,
                                      update_config=MagicMock())
        W._maybe_show_side_no_room_notice(fresh)              # next launch: the saved flag holds
    assert shot.call_count == 1 and cfg["side_bar_no_room_notice_shown"] is True
    fresh.update_config.assert_not_called()


# --- B4 (rc.1 live test, 2026-10-22): every taskbar button is occupied space ----------------------
# Owner's PC, side bar with "Top" icon alignment: Win11 moves the Widgets button down to just above
# the tray (y 852-896, tray from ~900) and the widget was placed at 842-900, covering it - the probe's
# app span left out the Widgets button and the "..." overflow button.

WIDGETS_AT_TRAY = (860, 904)                   # just above TRAY_TOP (908), like the owner's 852-896


@pytest.mark.parametrize("edge", [LEFT, RIGHT])
@pytest.mark.parametrize("thickness", [48, 32])
def test_top_alignment_puts_the_widget_above_the_widgets_button_not_on_it(edge, thickness):
    placement, pos = _place(_taskbar(edge, thickness, apps=(0, 400), widgets=WIDGETS_AT_TRAY))
    assert placement.mode == "tray"
    assert pos.y == WIDGETS_AT_TRAY[0] - GAP - H
    assert pos.y + H <= WIDGETS_AT_TRAY[0]                 # clear of the Widgets button ...
    assert pos.y >= 400                                    # ... and of the apps


def test_top_alignment_with_apps_down_to_the_widgets_button_still_never_covers_it():
    placement, pos = _place(_taskbar(apps=(0, 850), widgets=WIDGETS_AT_TRAY))
    assert placement.mode == "tray_no_room"                # no top gap: apps start at the bar top
    assert pos.y + H <= WIDGETS_AT_TRAY[0]


def test_top_alignment_drag_and_stale_offset_stop_above_the_widgets_button():
    tb = _taskbar(apps=(0, 400), widgets=WIDGETS_AT_TRAY)
    calc = PositionCalculator()
    assert calc.constrain_drag_position(QPoint(0, 5000), tb, QSize(48, H)).y() == WIDGETS_AT_TRAY[0] - GAP - H
    assert calc.constrain_drag_position(QPoint(0, -50), tb, QSize(48, H)).y() == 0   # bar top is free
    # a drag offset saved on the old (Center) layout, measured from the tray: clamped above Widgets
    pos = PositionCalculator().calculate_position(tb, (48, H), {"tray_offset_y": 10})
    assert pos.y == WIDGETS_AT_TRAY[0] - GAP - H


def test_center_alignment_is_unchanged_widgets_on_top_limits_the_top_gap():
    geo = side_bar_geometry(_taskbar(apps=(300, 900), widgets=(0, 120)), 1.0)
    assert geo.limit_top == 120 and geo.end == TRAY_TOP and geo.end_top is None


def test_widgets_position_without_a_known_app_span_uses_the_bars_halves():
    low = side_bar_geometry(_taskbar(apps=None, widgets=WIDGETS_AT_TRAY), 1.0)
    assert low.end == WIDGETS_AT_TRAY[0] and low.limit_top == 0
    high = side_bar_geometry(_taskbar(apps=None, widgets=(0, 120)), 1.0)
    assert high.end == TRAY_TOP and high.limit_top == 120
    placement, pos = _place(_taskbar(apps=None, widgets=WIDGETS_AT_TRAY))
    assert placement.mode == "tray" and pos.y + H <= WIDGETS_AT_TRAY[0]


def test_overflow_and_other_bar_buttons_count_as_apps_but_tray_and_our_widget_do_not():
    x0, x1 = 1872, 1920
    els = [("ToggleButton", "StartButton", (x0, 0, x1, 44)),
           ("Taskbar.TaskListButtonAutomationPeer", "a", (x0, 44, x1, 88)),
           ("Taskbar.TaskListButtonAutomationPeer", "b", (x0, 88, x1, 132)),
           # the "..." overflow button (class/id not probed live yet: matched generically)
           ("Taskbar.TaskbarOverflowButtonAutomationPeer", "TaskbarOverflowButton", (x0, 132, x1, 176)),
           ("Button", "SomeFutureShellButton", (x0, 176, x1, 220)),
           ("ToggleButton", "WidgetsButton", (x0, 852, x1, 896)),
           ("SystemTray.NormalButton", "SystemTrayIcon", (x0, 900, x1, 940)),
           ("SystemTray.ShowDesktopButton", "SystemTrayIcon", (x0, 1070, x1, 1080)),
           ("QPushButton", "QApplication.NetworkSpeedWidget.button", (x0, 600, x1, 650)),
           ("Taskbar.TaskbarFrameAutomationPeer", "TaskbarFrame", (x0, 0, x1, 1080)),
           ("Image", "BadgeAnchorLargeTicker", (x0, 860, x1, 880))]
    apps, widgets = uia_widgets.taskbar_layout_from_elements(els)
    assert apps == (x0, 0, x1, 220)
    assert widgets == (x0, 852, x1, 896)


def test_live_bottom_bar_probe_2026_10_22_is_unchanged_by_the_wider_match():
    # This machine's bottom bar, re-probed 2026-10-22 (Center alignment, no overflow button).
    els = [("Taskbar.TaskbarFrameAutomationPeer", "TaskbarFrame", (0, 1032, 1920, 1080)),
           ("ToggleButton", "WidgetsButton", (6, 1032, 158, 1080)),
           ("NamedContainerAutomationPeer", "", (16, 1036, 152, 1076)),
           ("Image", "BadgeAnchorLargeTicker", (19, 1043, 43, 1067)),
           ("ToggleButton", "StartButton", (652, 1032, 697, 1080)),
           ("ToggleButton", "SearchButton", (697, 1032, 741, 1080)),
           ("Microsoft.UI.Xaml.Controls.AnimatedVisualPlayer", "Icon", (707, 1044, 731, 1068))] + [
        ("Taskbar.TaskListButtonAutomationPeer", "Appid:x", (741 + 44 * i, 1032, 785 + 44 * i, 1080))
        for i in range(12)] + [
        ("SystemTray.NormalButton", "SystemTrayIcon", (1694, 1032, 1726, 1080)),
        ("SystemTray.AccentButton", "SystemTrayIcon", (1774, 1032, 1798, 1080)),
        ("SystemTray.OmniButtonRight", "SystemTrayIcon", (1802, 1032, 1826, 1080)),
        ("SystemTray.OmniButton", "SystemTrayIcon", (1834, 1032, 1904, 1080)),
        ("SystemTray.ShowDesktopButton", "SystemTrayIcon", (1908, 1032, 1920, 1080))]
    assert uia_widgets.taskbar_layout_from_elements(els) == ((652, 1032, 1269, 1080), (6, 1032, 158, 1080))


# --- B3 (rc.1 live test, 2026-10-22): a live Center <-> Top alignment flip re-decides ------------

def _settingchange_msg(text=None):
    """A real MSG struct (WM_SETTINGCHANGE) in memory, and keep-alive refs, for nativeEventFilter."""
    import ctypes
    from netspeedtray.core import system_events as se
    buf = ctypes.create_unicode_buffer(text) if text is not None else None
    msg = se._NativeMSG()
    msg.message = se._WM_SETTINGCHANGE
    msg.lParam = ctypes.addressof(buf) if buf is not None else 0
    return msg, buf


@pytest.mark.parametrize("text", [None, "TraySettings", "Policy", "ImmersiveColorSet"])
def test_every_settingchange_reaches_the_alignment_check(text):
    import ctypes
    from netspeedtray.core import system_events as se
    theme, any_setting = [], []
    flt = se._ImmersiveColorSetFilter(lambda: theme.append(1), lambda: any_setting.append(1))
    msg, _buf = _settingchange_msg(text)
    assert flt.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(msg)) == (False, 0)  # observe only
    assert any_setting == [1]
    assert theme == ([1] if text == "ImmersiveColorSet" else [])


def test_alignment_flip_emits_once_and_only_on_a_change():
    from netspeedtray.core import system_events as se
    handler = se.SystemEventHandler()
    seen = []
    handler.taskbar_alignment_changed.connect(lambda: seen.append(1))
    values = iter([1, 1, 0, 0, 0, 1])
    with patch.object(se, "read_taskbar_alignment", side_effect=lambda: next(values)):
        handler.check_taskbar_alignment()        # baseline (start-up): no emit
        handler.check_taskbar_alignment()        # same value (another of our windows got it)
        handler.check_taskbar_alignment()        # Center -> Top
        handler.check_taskbar_alignment()
        handler._is_paused = True
        handler.check_taskbar_alignment()        # paused: not consumed ...
        handler._is_paused = False
        handler.check_taskbar_alignment()        # ... value 0 again, no change
        handler.check_taskbar_alignment()        # Top -> Center
    assert seen == [1, 1]


def test_read_taskbar_alignment_defaults_to_center_when_the_value_is_absent():
    import winreg
    from netspeedtray.core import system_events as se
    with patch.object(winreg, "OpenKey", return_value=MagicMock()), \
            patch.object(winreg, "CloseKey"), \
            patch.object(winreg, "QueryValueEx", side_effect=FileNotFoundError()):
        assert se.read_taskbar_alignment() == 1
    with patch.object(winreg, "OpenKey", side_effect=OSError("no key")):
        assert se.read_taskbar_alignment() is None
    with patch.object(winreg, "OpenKey", return_value=MagicMock()), \
            patch.object(winreg, "CloseKey"), \
            patch.object(winreg, "QueryValueEx", return_value=(0, winreg.REG_DWORD)):
        assert se.read_taskbar_alignment() == 0


def test_widget_forwards_an_alignment_signal_to_the_side_bar_watcher(q_app):
    """The WM_SETTINGCHANGE path (dead on 26200, kept as a hint) no longer re-decides on a blind 1.5 s
    timer: the side-bar watcher compares TaskbarAl and waits for the re-laid bar (rc.2 W2)."""
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    stub = types.SimpleNamespace(logger=MagicMock(), position_manager=MagicMock())
    NetworkSpeedWidget._on_taskbar_alignment_changed(stub)
    stub.position_manager.note_taskbar_alignment_hint.assert_called_once_with()
