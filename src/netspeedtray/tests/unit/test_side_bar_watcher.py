"""Side-taskbar re-placement after startup (rc.2 W1-W4, PR #406).

#406 decided the side-bar placement only at startup, so apps opened later grew into the widget (rc.1
live test), and the live alignment change relied on a WM_SETTINGCHANGE broadcast that never arrives
on build 26200. ``core/side_bar_watcher.py`` fixes both:

- centred icons: a LOCATIONCHANGE hook on the legacy ``Start`` / task-switch windows. ``Start`` moves
  to its final spot 15-30 ms before UI Automation (scratchpad ``p406live/b2_right_center.txt``); the
  new app group is predicted from that edge and only a predicted collision runs ONE UIA probe;
- Top alignment (Start never moves): task-window moves and foreground changes count one slot each,
  and the 1 s tick re-probes every 3 s only while the group is near the widget;
- alignment: TaskbarAl is read on the hook burst and the tick; a flip re-decides once settled;
- the probe keeps the last layout until the new one lands, waits out the animation (Start vs UIA),
  and PositionManager moves the widget only on a real collision; a user's drag always wins.
"""
import types
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QPoint, QRect, QSize, Qt, QTimer
from PyQt6.QtWidgets import QApplication

from netspeedtray import constants
from netspeedtray.core import side_bar_watcher as sbw
from netspeedtray.core.position_manager import (
    PositionCalculator, PositionManager, SidePlacement, WindowState, side_bar_geometry,
    side_bar_y_limits,
)
from netspeedtray.core.side_bar_watcher import SideBarWatcher, SideWatchContext, predict_group, span_gap
from netspeedtray.utils import taskbar_utils, uia_widgets
from netspeedtray.utils.taskbar_utils import TaskbarInfo

T = constants.timeouts
RIGHT = constants.taskbar.edge.RIGHT
BOTTOM = constants.taskbar.edge.BOTTOM
HWND = 4242
START, REBAR, TASKSW = 11, 12, 13
SLOT = 45                                  # the legacy Start window's height on the owner's right bar
X0, X1 = 1872, 1920


@pytest.fixture(scope="module", autouse=True)
def q_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def layout():
    """The REAL layout cache with a synchronous, controllable worker."""
    ctl = types.SimpleNamespace(result=None, workers=[], requests=[])

    def start():
        ctl.workers.append(taskbar_utils._layout_worker)

    def land(apps=None, group=None, widgets=None):
        ctl.result = (_r(apps), _r(widgets), _r(group or apps))
        while ctl.workers:
            ctl.workers.pop(0)()

    real_request = taskbar_utils.request_taskbar_layout_probe

    def request(h, keep_last=False):
        ctl.requests.append(keep_last)
        real_request(h, keep_last=keep_last)

    ctl.land = land
    fresh = {"hwnd": 0, "apps": None, "widgets": None, "group": None, "pending": False, "done": False,
             "gen": 0, "running": False, "started": 0.0, "landed": 0}
    taskbar_utils._layout_state.update(fresh)
    with patch.object(taskbar_utils, "_start_layout_worker", side_effect=start), \
            patch.object(taskbar_utils, "_query_taskbar_layout_blocking", side_effect=lambda h: ctl.result), \
            patch.object(taskbar_utils, "request_taskbar_layout_probe", side_effect=request):
        yield ctl
    taskbar_utils._layout_state.update(fresh)


def _r(span):
    return (X0, span[0], X1, span[1]) if span else None


class Rig:
    """A watcher on a fake right bar: Start window rect, TaskbarAl, widget span, a fake hook."""

    def __init__(self, layout, *, model=(300, 900), start_top=300, widget=(840, 896), align=1,
                 user_placed=False):
        self.layout = layout
        self.start_top = start_top
        self.align = align
        self.widget = widget
        self.user_placed = user_placed
        self.now = 1000.0
        self.rechecks = []
        self.hook_args = None
        patches = [
            patch.object(taskbar_utils, "find_classic_layout_hwnds", return_value=(START, REBAR, TASKSW)),
            patch.object(sbw, "_window_rect", side_effect=self._rect),
        ]
        for p in patches:
            p.start()
        self._patches = patches
        self.w = SideBarWatcher(self._ctx, self.rechecks.append, hook_factory=self._hook,
                                read_alignment=lambda: self.align, clock=lambda: self.now)
        if model is not None:   # the startup probe already landed
            taskbar_utils.request_taskbar_layout_probe(HWND)
            layout.land(model)
            layout.requests.clear()
        self.w.attach(HWND)

    def _hook(self, hwnd, watched, on_event):
        self.hook_args = (hwnd, watched)
        self.on_event = on_event
        return MagicMock()

    def _rect(self, hwnd):
        if hwnd == START and self.start_top is not None:
            return (X0, self.start_top, X1, self.start_top + SLOT)
        return None

    def _ctx(self):
        return SideWatchContext(hwnd=HWND, dpi=1.0, widget=self.widget, user_placed=self.user_placed)

    def burst(self, *hwnds):
        for h in hwnds:
            self.on_event(h)
        assert self.w._burst_timer.isActive()
        self.w._burst_timer.stop()
        self.w._on_burst()

    def fire_probe(self):
        assert self.w._probe_timer.isActive()
        self.w._probe_timer.stop()
        self.w._run_probe()

    def close(self):
        self.w.detach()
        for p in self._patches:
            p.stop()


@pytest.fixture
def rig(layout):
    made = []

    def make(**kw):
        r = Rig(layout, **kw)
        made.append(r)
        return r
    yield make
    for r in made:
        r.close()


# --- pure prediction ---------------------------------------------------------------------------

def test_span_gap_is_negative_on_overlap():
    assert span_gap((100, 150), (200, 300)) == 50
    assert span_gap((350, 400), (200, 300)) == 50
    assert span_gap((250, 400), (200, 300)) < 0


def test_prediction_takes_starts_edge_and_assumes_the_same_growth_at_the_far_end():
    assert predict_group((300, 900), 300, 0) == (300, 900)
    assert predict_group((300, 900), 255, 0) == (255, 945)          # grew 45 at Start's end
    assert predict_group((300, 900), 345, 0) == (300, 900)          # shrank: never predicts less
    assert predict_group((0, 700), 0, 90) == (0, 790)               # Top: assumed slots at the far end
    assert predict_group((300, 900), None, 0) == (300, 900)


# --- the hook ------------------------------------------------------------------------------------

def test_hook_watches_explorers_legacy_start_and_task_windows(rig):
    r = rig()
    assert r.hook_args == (HWND, frozenset({START, REBAR, TASKSW}))


def test_real_hook_is_out_of_context_process_scoped_and_filtered_on_its_thread(q_app):
    """The callback drops other windows and non-window objects BEFORE crossing into Qt."""
    from netspeedtray.utils import win_event_hook as weh
    seen = []
    with patch.object(weh.WinEventHook, "start"):
        hook = sbw._default_hook_factory(HWND, frozenset({START, TASKSW}), seen.append)
    assert hook.event_to_watch == weh.EVENT_OBJECT_LOCATIONCHANGE
    assert hook.process_scope and hook.hwnd_to_watch == HWND
    assert hook._flags == weh.WINEVENT_OUTOFCONTEXT | weh.WINEVENT_SKIPOWNPROCESS
    hook.callback(None, 0x800B, 999, 0, 0, 0, 0)                 # another Explorer window
    hook.callback(None, 0x800B, START, -9, 0, 0, 0)              # OBJID_CURSOR-like, not the window
    hook.callback(None, 0x800B, START, 0, 0, 0, 0)
    hook.callback(None, 0x800B, TASKSW, 0, 0, 0, 0)
    assert seen == [START, TASKSW]


def test_win_event_hook_process_scope_hooks_every_thread_of_the_process():
    from netspeedtray.utils import win_event_hook as weh
    hook = weh.WinEventHook(weh.EVENT_OBJECT_LOCATIONCHANGE, hwnd_to_watch=HWND, process_scope=True)
    with patch.object(weh.win32process, "GetWindowThreadProcessId", return_value=(77, 4321)), \
            patch.object(weh, "_SetWinEventHook", return_value=0) as setter:
        hook._run_hook()
    args = setter.call_args.args
    assert (args[4], args[5]) == (4321, 0)                       # pid, ALL threads


# --- centred icons: Start's early edge ------------------------------------------------------------

def test_start_moving_into_a_widget_in_the_top_gap_runs_one_settled_probe(rig, layout):
    r = rig(model=(300, 900), start_top=300, widget=(150, 206))
    r.start_top = 255                                           # one more app: 49 px clear - no probe
    r.burst(START, TASKSW, REBAR)
    assert not r.w._probe_timer.isActive()
    r.start_top = 210                                           # another: Start 4 px under the widget
    r.burst(START, TASKSW, REBAR)
    assert r.w._probe_timer.interval() == T.SIDE_BAR_SETTLE_MS  # waits for the UIA animation
    r.fire_probe()
    assert layout.requests == [True]                            # keeps the last layout meanwhile
    layout.land((210, 900))
    r.w._poll_probe()
    assert r.rechecks == [False]                                # a collision re-check, not a full one
    assert r.w._model == (210, 900)


def test_start_moving_far_from_the_widget_never_probes(rig, layout):
    r = rig(model=(300, 600), start_top=300, widget=(840, 896))
    r.start_top = 255
    r.burst(START, REBAR)
    assert not r.w._probe_timer.isActive() and layout.requests == []


def test_a_second_burst_pushes_the_probe_back_one_probe_per_burst(rig, layout):
    r = rig(model=(300, 900), start_top=300, widget=(150, 206))
    r.start_top = 210
    r.burst(START)
    r.start_top = 210
    r.burst(START)
    r.fire_probe()
    assert layout.requests == [True]


def test_a_mid_animation_probe_is_repeated_until_uia_matches_start(rig, layout):
    """Live: UIA reports Start at its old spot and animates for ~300-550 ms after the legacy window
    already moved - a probe then would under-report the group."""
    r = rig(model=(300, 900), start_top=300, widget=(150, 206))
    r.start_top = 210
    r.burst(START)
    r.fire_probe()
    retries = []
    with patch.object(QTimer, "singleShot", side_effect=lambda ms, fn: retries.append((ms, fn))):
        layout.land((250, 900))                                 # still animating
        r.w._poll_probe()
    assert r.rechecks == [] and retries and retries[0][0] == T.SIDE_BAR_RETRY_MS
    retries[0][1]()
    assert layout.requests == [True, True]
    layout.land((210, 900))
    r.w._poll_probe()
    assert r.rechecks == [False]


def test_retries_are_bounded(rig, layout):
    r = rig(model=(300, 900), start_top=300, widget=(150, 206))
    r.start_top = 210
    r.burst(START)
    r.fire_probe()
    with patch.object(QTimer, "singleShot", side_effect=lambda ms, fn: fn()):
        for _ in range(T.SIDE_BAR_MAX_RETRIES + 1):
            layout.land((250, 900))
            r.w._poll_probe()
    assert r.rechecks == [False]
    assert len(layout.requests) == T.SIDE_BAR_MAX_RETRIES + 1


def test_a_timed_out_recheck_keeps_the_layout_and_moves_nothing(rig, layout):
    r = rig(model=(300, 900), start_top=300, widget=(150, 206))
    r.start_top = 210
    r.burst(START)
    r.fire_probe()
    taskbar_utils._layout_state["started"] -= taskbar_utils.LAYOUT_PROBE_TIMEOUT_S + 1
    r.w._poll_probe()
    assert r.rechecks == [] and not r.w._inflight
    apps, _w, pending = taskbar_utils.taskbar_layout_snapshot(HWND)
    assert apps == _r((300, 900)) and not pending


def test_probes_are_rate_limited(rig, layout):
    r = rig(model=(300, 900), start_top=300, widget=(150, 206))
    r.w._last_probe_at = r.now - 0.2
    r.w._schedule(full=False, delay_ms=0)
    assert r.w._probe_timer.interval() == pytest.approx(T.SIDE_BAR_MIN_PROBE_INTERVAL_MS - 200, abs=2)


# --- Top alignment: Start never moves -------------------------------------------------------------

def test_top_alignment_counts_task_window_moves_as_slots_and_probes_only_when_near(rig, layout):
    r = rig(model=(0, 700), start_top=0, widget=(840, 896), align=0)
    r.burst(TASKSW)                       # 745: 95 px away
    r.burst(REBAR, TASKSW)                # 790: 50 px away
    assert not r.w._probe_timer.isActive()
    r.burst(TASKSW)                       # 835: inside the margin
    assert r.w._probe_timer.isActive()
    r.fire_probe()
    layout.land((0, 830))
    r.w._poll_probe()
    assert r.rechecks == [False] and r.w._assumed_growth == 0 and r.w._model == (0, 830)


def test_foreground_change_rechecks_near_groups_now_and_800_ms_later(rig, layout):
    r = rig(model=(0, 760), start_top=0, widget=(840, 896), align=0)
    r.w.note_foreground_change()
    assert r.w._probe_timer.isActive() and r.w._followup_timer.isActive()
    assert r.w._followup_timer.interval() == T.SIDE_BAR_FOREGROUND_FOLLOWUP_MS


def test_foreground_change_far_away_only_counts_a_slot(rig, layout):
    r = rig(model=(0, 300), start_top=0, widget=(840, 896), align=0)
    r.w.note_foreground_change()
    assert not r.w._probe_timer.isActive() and r.w._assumed_growth == SLOT


def test_tick_rechecks_every_3_s_only_while_near(rig, layout):
    r = rig(model=(0, 760), start_top=0, widget=(840, 896), align=0)
    r.w._last_probe_at = r.now - 1.0
    r.w.on_tick()
    assert not r.w._probe_timer.isActive()
    r.now += 2.5
    r.w.on_tick()
    assert r.w._probe_timer.isActive()
    far = rig(model=(0, 300), start_top=0, widget=(840, 896), align=0)
    far.now += 60
    far.w.on_tick()
    assert not far.w._probe_timer.isActive()


# --- alignment flip (W2) ------------------------------------------------------------------------

@pytest.mark.parametrize("via", ["tick", "burst", "settingchange"])
def test_alignment_flip_re_decides_fully_once_the_bar_settled(rig, layout, via):
    r = rig(model=(300, 900), start_top=300, widget=(840, 896), align=1)
    r.align = 0
    r.start_top = 0                               # Start jumps to the top of the bar
    if via == "tick":
        r.w.on_tick()
    elif via == "burst":
        r.burst(START)
    else:
        r.w.note_alignment_maybe_changed()
    assert r.w._probe_timer.interval() >= T.SIDE_BAR_ALIGNMENT_SETTLE_MS
    r.fire_probe()
    layout.land((0, 700))
    r.w._poll_probe()
    assert r.rechecks == [True]
    r.w.on_tick()                                 # same value again: nothing more
    assert not r.w._probe_timer.isActive()


def test_alignment_flip_re_clamps_even_a_user_placed_widget(rig, layout):
    r = rig(model=(300, 900), start_top=300, widget=(500, 556), user_placed=True)
    r.align = 0
    r.start_top = 0
    r.w.on_tick()
    r.fire_probe()
    layout.land((0, 700))
    r.w._poll_probe()
    assert r.rechecks == [True]


def test_a_user_placed_widget_is_never_moved_for_apps(rig, layout):
    r = rig(model=(300, 900), start_top=300, widget=(150, 206), user_placed=True)
    r.start_top = 160
    r.burst(START)
    r.w.note_foreground_change()
    r.now += 60
    r.w.on_tick()
    assert not r.w._probe_timer.isActive() and layout.requests == []


def test_detach_unhooks_and_stops_everything(rig, layout):
    r = rig(model=(300, 900), start_top=300, widget=(150, 206))
    hook = r.w._hook
    r.start_top = 210
    r.burst(START)
    r.w.detach()
    hook.stop.assert_called_once_with()
    assert not r.w._probe_timer.isActive() and r.w.attached_hwnd == 0


# --- keep_last + W3 drag clamp during a re-check ------------------------------------------------

def test_a_keep_last_recheck_answers_from_the_last_layout_until_it_lands(layout):
    taskbar_utils.request_taskbar_layout_probe(HWND)
    layout.land((300, 900), widgets=(0, 120))
    taskbar_utils.request_taskbar_layout_probe(HWND, keep_last=True)
    apps, source, widgets, pending = taskbar_utils.taskbar_layout_for(HWND)
    assert (apps, source, widgets, pending) == (_r((300, 900)), "uia", _r((0, 120)), True)
    layout.land((255, 900), widgets=(0, 120))
    assert taskbar_utils.taskbar_layout_for(HWND)[0] == _r((255, 900))
    assert taskbar_utils.taskbar_layout_group(HWND) == _r((255, 900))


def test_drag_stays_below_the_widgets_button_while_the_layout_is_re_checked(layout):
    """W3: the drag limit comes from the Widgets button's box. A fresh (non-keep_last) request drops
    it while pending, so a drag in that window reached the bar top; the runtime re-checks keep it."""
    taskbar_utils.request_taskbar_layout_probe(HWND)
    layout.land((300, 900), widgets=(0, 120))
    taskbar_utils.request_taskbar_layout_probe(HWND, keep_last=True)
    apps, source, widgets, _p = taskbar_utils.taskbar_layout_for(HWND)
    tb = MagicMock(spec=TaskbarInfo)
    tb.hwnd, tb.dpi_scale, tb.rect, tb.height, tb.tasklist_rect = HWND, 1.0, (X0, 0, X1, 1080), 48, None
    tb.apps_extent, tb.apps_extent_source, tb.widgets_extent, tb.widgets_rect = apps, source, widgets, None
    tb.get_edge_position.return_value = RIGHT
    screen = MagicMock()
    screen.geometry.return_value = QRect(0, 0, 1920, 1080)
    screen.availableGeometry.return_value = QRect(0, 0, 1872, 1080)
    tb.get_screen.return_value = screen
    tb.get_tray_rect.return_value = (X0, 908, X1, 1080)
    pos = PositionCalculator().constrain_drag_position(QPoint(1872, 0), tb, QSize(48, 56))
    assert pos.y() == 120


# --- PositionManager: what a re-check may move -----------------------------------------------------

def _pm(widget_top, config=None):
    tb = MagicMock(spec=TaskbarInfo)
    tb.hwnd, tb.dpi_scale, tb.rect, tb.height, tb.tasklist_rect = HWND, 1.0, (X0, 0, X1, 1080), 48, None
    tb.apps_extent = tb.apps_extent_source = tb.widgets_extent = tb.widgets_rect = None
    tb.get_edge_position.return_value = RIGHT
    screen = MagicMock()
    screen.geometry.return_value = QRect(0, 0, 1920, 1080)
    screen.availableGeometry.return_value = QRect(0, 0, 1872, 1080)
    tb.get_screen.return_value = screen
    tb.get_tray_rect.return_value = (X0, 908, X1, 1080)
    widget = MagicMock()
    widget.width.return_value, widget.height.return_value = 48, 56
    widget.pos.return_value = QPoint(X0, widget_top)
    widget.isVisible.return_value = True
    widget._dragging = False
    cfg = {"tray_offset_y": constants.config.defaults.DEFAULT_TRAY_OFFSET_Y} if config is None else config
    pm = PositionManager(WindowState(config=cfg, widget=widget, taskbar_info=tb))
    return pm, tb, widget


def test_recheck_moves_the_widget_only_when_the_fresh_apps_reach_it(layout):
    pm, tb, _w = _pm(widget_top=150)              # in the top gap, 150..206
    pm._side_placement = SidePlacement("top", 150)
    taskbar_utils.request_taskbar_layout_probe(HWND)
    layout.land((300, 900), widgets=(0, 120))
    assert pm._recheck_side_placement(tb, 1.0, 56, "collision") is pm._side_placement   # clear: stays
    taskbar_utils.request_taskbar_layout_probe(HWND, keep_last=True)
    layout.land((200, 900), widgets=(0, 120))     # Start grew up into the widget
    with patch.object(pm, "_decide_side_placement", return_value=SidePlacement("tray_no_room")) as d:
        assert pm._recheck_side_placement(tb, 1.0, 56, "collision").mode == "tray_no_room"
    d.assert_called_once()


def test_recheck_full_always_re_decides_and_a_drag_offset_blocks_collision_moves(layout):
    pm, tb, _w = _pm(widget_top=150, config={"tray_offset_y": 700})
    pm._side_placement = SidePlacement("tray")
    taskbar_utils.request_taskbar_layout_probe(HWND)
    layout.land((100, 900), widgets=(0, 60))
    with patch.object(pm, "_decide_side_placement", return_value=SidePlacement("tray")) as d:
        pm._recheck_side_placement(tb, 1.0, 56, "collision")
        d.assert_not_called()                     # the user's drag wins
        pm._recheck_side_placement(tb, 1.0, 56, "full")
        d.assert_called_once()


def test_watcher_follows_the_native_side_bar_and_hands_its_result_back(layout):
    pm, tb, widget = _pm(widget_top=840)
    with patch.object(pm._side_watcher, "attach") as attach, \
            patch.object(pm._side_watcher, "detach") as detach:
        pm._side_placement_for(tb, 56)
        attach.assert_called_with(HWND)
        tb.get_edge_position.return_value = BOTTOM
        pm._side_placement_for(tb, 56)
        detach.assert_called()
    tb.get_edge_position.return_value = RIGHT
    pm._side_placement_for(tb, 56)
    ctx = pm._side_watch_context()
    assert ctx.hwnd == HWND and ctx.widget == (840.0, 896.0) and not ctx.user_placed
    widget._dragging = True
    assert pm._side_watch_context() is None       # never re-place under a drag
    widget._dragging = False
    with patch.object(pm, "update_position") as upd:
        pm._apply_side_recheck(False)
    upd.assert_called_once_with()
    assert pm._side_recheck == "collision"
    with patch.object(pm, "_recheck_side_placement", return_value=None) as rc:
        pm._side_placement_for(tb, 56)
    assert rc.call_args.args[3] == "collision" and pm._side_recheck is None
    pm.stop_side_watcher()


# --- the contiguous app group (#24 reuses it) ------------------------------------------------------

D24_BOTTOM_CENTERED = [   # owner's bottom bar, 2026-10-22 (scratchpad d24/bottom_centered_probe.txt)
    ("Taskbar.TaskbarFrameAutomationPeer", "TaskbarFrame", (0, 1032, 1920, 1080)),
    ("ToggleButton", "WidgetsButton", (6, 1032, 158, 1080)),
    ("NamedContainerAutomationPeer", "", (16, 1036, 152, 1076)),
    ("ToggleButton", "StartButton", (674, 1032, 719, 1080)),
    ("ToggleButton", "SearchButton", (719, 1032, 763, 1080)),
    ("Microsoft.UI.Xaml.Controls.AnimatedVisualPlayer", "Icon", (729, 1044, 753, 1068)),
] + [("Taskbar.TaskListButtonAutomationPeer", "Appid: x%d" % i, (763 + 44 * i, 1032, 807 + 44 * i, 1080))
     for i in range(11)] + [
    ("SystemTray.NormalButton", "SystemTrayIcon", (1694, 1032, 1726, 1080)),
    ("SystemTray.ShowDesktopButton", "SystemTrayIcon", (1908, 1032, 1920, 1080)),
]


def test_app_group_on_the_live_bottom_bar():
    assert uia_widgets.app_group_from_elements(D24_BOTTOM_CENTERED, vertical=False) == (674, 1032, 1247, 1080)


def test_app_group_keeps_the_overflow_button_but_not_a_far_button():
    els = D24_BOTTOM_CENTERED + [("ToggleButton", "TaskbarOverflowButton", (1247, 1032, 1291, 1080)),
                                 ("Button", "SomeFutureShellButton", (1600, 1032, 1644, 1080))]
    assert uia_widgets.app_group_from_elements(els, vertical=False) == (674, 1032, 1291, 1080)
    union, _ = uia_widgets.taskbar_layout_from_elements(els)
    assert union[2] == 1644                       # the union (side-bar policy) still counts it


def test_app_group_on_a_side_bar_and_without_start():
    els = [("ToggleButton", "WidgetsButton", (X0, 10, X1, 54)),
           ("ToggleButton", "StartButton", (X0, 145, X1, 190)),
           ("Taskbar.TaskListButtonAutomationPeer", "a", (X0, 190, X1, 234)),
           ("Taskbar.TaskListButtonAutomationPeer", "b", (X0, 234, X1, 278))]
    assert uia_widgets.app_group_from_elements(els, vertical=True) == (X0, 145, X1, 278)
    assert uia_widgets.app_group_from_elements(els[2:], vertical=True) is None


def test_full_query_infers_the_axis_and_returns_the_group():
    els = [("ToggleButton", "StartButton", (X0, 145, X1, 190)),
           ("Taskbar.TaskListButtonAutomationPeer", "a", (X0, 190, X1, 234))]
    apps, widgets, group = uia_widgets._layout_from_elements_full(els, None)
    assert apps == group == (X0, 145, X1, 234) and widgets is None


def test_worker_stores_a_two_value_result_from_older_callers(layout):
    layout.result = (_r((300, 900)), _r((0, 120)))
    taskbar_utils.request_taskbar_layout_probe(HWND)
    while layout.workers:
        layout.workers.pop(0)()
    assert taskbar_utils.taskbar_layout_for(HWND)[0] == _r((300, 900))
    assert taskbar_utils.taskbar_layout_group(HWND) is None


# --- W4: the no-room notice never takes the keyboard ---------------------------------------------

def test_no_room_notice_is_a_non_activating_flyout_not_a_message_box(q_app):
    from netspeedtray.views.widget import main as main_mod
    stub = types.SimpleNamespace(config={}, logger=MagicMock(), update_config=MagicMock(),
                                 i18n=types.SimpleNamespace(SIDE_BAR_NO_ROOM_TITLE="t", SIDE_BAR_NO_ROOM_BODY="b"),
                                 _show_notice_flyout=MagicMock())
    with patch.object(main_mod.QTimer, "singleShot", side_effect=lambda ms, fn: fn()), \
            patch.object(main_mod.QMessageBox, "information") as box:
        main_mod.NetworkSpeedWidget._maybe_show_side_no_room_notice(stub)
        main_mod.NetworkSpeedWidget._maybe_show_side_no_room_notice(stub)   # once ever
    box.assert_not_called()
    stub._show_notice_flyout.assert_called_once_with("t", "b")
    stub.update_config.assert_called_once_with({"side_bar_no_room_notice_shown": True})


def test_flyout_never_accepts_focus(q_app):
    from netspeedtray.views.flyout import Flyout
    f = Flyout("t", "b", auto_dismiss_ms=0)
    try:
        assert f.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
        assert f.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    finally:
        f.close()


def test_widget_forwards_foreground_changes_to_the_watcher(q_app):
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    stub = types.SimpleNamespace(config={}, logger=MagicMock(), position_manager=MagicMock())
    NetworkSpeedWidget._on_foreground_for_side_bar(stub, 123)
    stub.position_manager.note_foreground_change.assert_called_once_with()
    stub.config["free_move"] = True
    NetworkSpeedWidget._on_foreground_for_side_bar(stub, 123)
    assert stub.position_manager.note_foreground_change.call_count == 1
