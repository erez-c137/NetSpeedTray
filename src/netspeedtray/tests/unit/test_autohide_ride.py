"""Auto-hide taskbar ride-along (#135): the state machine in core/autohide_ride.py, and the widget's
wiring of it (_on_taskbar_moved, the hook's attach / detach, the refresh tick's stuck-slide way out,
the fullscreen hide). Fake bar rects throughout: a 48 px bottom bar on a 1920x1080 monitor."""
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from netspeedtray import constants
from netspeedtray.core.autohide_ride import (
    MODE_HIDE_ONLY, MODE_OFF, MODE_RIDE, NOTHING, AutohideRide, RideAction,
)
from netspeedtray.views.widget import main as widget_main

E = constants.taskbar.edge
MON = (0, 0, 1920, 1080)
DOCK = (1700, 1036)


def bar(slid: int):
    """A bottom bar slid ``slid`` px down from flush (0 = fully out, 46 = hidden with 2 px showing)."""
    return (0, 1032 + slid, 1920, 1080 + slid)


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def _ride():
    clock = Clock()
    return AutohideRide(clock=clock), clock


def _ev(ride, slid, *, visible, pos=DOCK, mode=MODE_RIDE, allowed=True, dpi=1.0):
    return ride.bar_moved(bar(slid), MON, E.BOTTOM, dpi, visible=visible, pos=pos, mode=mode,
                          ride_in_allowed=lambda: allowed)


# --- the state machine ------------------------------------------------------------------------------


def test_slide_out_rides_along_then_hides_and_parks_on_the_dock():
    ride, _ = _ride()
    assert _ev(ride, 10, visible=True) == RideAction(move_to=(1700, 1046))
    assert ride.sliding
    # The widget is now at the offset spot; the dock is still the spot the slide started from.
    assert _ev(ride, 30, visible=True, pos=(1700, 1046)) == RideAction(move_to=(1700, 1066))
    assert _ev(ride, 46, visible=True, pos=(1700, 1066)) == RideAction(hide=True, move_to=DOCK)
    assert not ride.sliding and ride.hid_widget


def test_slide_in_rides_a_bar_hidden_widget_back_and_runs_the_full_gate_once_out():
    ride, _ = _ride()
    ride.hid_widget = True
    assert _ev(ride, 40, visible=False) == RideAction(move_to=(1700, 1076), show=True)
    assert _ev(ride, 20, visible=True, pos=(1700, 1076)) == RideAction(move_to=(1700, 1056))
    assert _ev(ride, 0, visible=True, pos=(1700, 1056)) == RideAction(move_to=DOCK, refresh=True)
    assert not ride.sliding


def test_no_ride_in_when_the_fullscreen_or_yield_check_says_no():
    """Point 2: bar hidden, a borderless game starts, the edge is poked - the widget stays hidden."""
    ride, _ = _ride()
    ride.hid_widget = True
    assert _ev(ride, 40, visible=False, allowed=False) is NOTHING
    assert not ride.sliding
    # Fully out: the full gate (which has the fullscreen latch) decides, nothing is shown here.
    assert _ev(ride, 0, visible=False) == RideAction(refresh=True)


def test_no_ride_in_for_a_widget_not_hidden_by_its_bar():
    ride, _ = _ride()
    assert ride.hid_widget is False
    assert _ev(ride, 40, visible=False) is NOTHING


def test_cancel_drops_the_ride_in_claim_and_returns_a_riding_widget_to_its_dock():
    ride, _ = _ride()
    _ev(ride, 10, visible=True)
    ride.hid_widget = True
    assert ride.cancel() == RideAction(move_to=DOCK)
    assert not ride.sliding and not ride.hid_widget
    assert ride.cancel() is NOTHING


def test_free_float_ignores_the_bar_entirely():
    """Point 1: free-float follows nothing on the primary bar - no hide, no shift."""
    ride, _ = _ride()
    for slid in (0, 10, 30, 46, 20, 0):
        assert _ev(ride, slid, visible=True, mode=MODE_OFF) is NOTHING
    assert not ride.sliding


def test_free_move_is_never_shifted_only_hidden_and_re_gated():
    """Point 1: a Free Move widget parked by the user hides with its bar but never moves."""
    ride, _ = _ride()
    assert _ev(ride, 10, visible=True, mode=MODE_HIDE_ONLY) is NOTHING
    assert _ev(ride, 46, visible=True, mode=MODE_HIDE_ONLY) == RideAction(hide=True)
    ride.hid_widget = True
    assert _ev(ride, 20, visible=False, mode=MODE_HIDE_ONLY) is NOTHING
    assert _ev(ride, 0, visible=False, mode=MODE_HIDE_ONLY) == RideAction(refresh=True)


def test_a_slide_whose_last_event_never_comes_turns_stuck():
    """Point 3: the refresh tick's way out."""
    ride, clock = _ride()
    _ev(ride, 10, visible=True)
    assert not ride.stuck()
    clock.t += (constants.timeouts.AUTOHIDE_SLIDE_STUCK_MS - 50) / 1000.0
    assert not ride.stuck()
    _ev(ride, 20, visible=True)                     # a fresh event restarts the wait
    clock.t += (constants.timeouts.AUTOHIDE_SLIDE_STUCK_MS - 50) / 1000.0
    assert not ride.stuck()
    clock.t += 0.1
    assert ride.stuck()


def test_the_dock_is_wherever_the_widget_is_when_a_slide_starts():
    """Point 4: a drag / alignment change / resize just before the slide is never undone."""
    ride, _ = _ride()
    _ev(ride, 10, visible=True, pos=DOCK)
    _ev(ride, 46, visible=True)
    _ev(ride, 0, visible=False)
    moved = (1500, 1036)                            # the user dragged it between two slides
    assert _ev(ride, 10, visible=True, pos=moved) == RideAction(move_to=(1500, 1046))
    assert _ev(ride, 46, visible=True)              == RideAction(hide=True, move_to=moved)


def test_a_stationary_bar_does_nothing():
    ride, _ = _ride()
    assert _ev(ride, 0, visible=True) is NOTHING


@pytest.mark.parametrize("edge, slid_rect, expect", [
    (E.TOP, (0, -30, 1920, 18), (1700, 970)),
    (E.LEFT, (-30, 0, 18, 1080), (1670, 1000)),
    (E.RIGHT, (1902, 0, 1950, 1080), (1730, 1000)),
])
def test_every_edge_rides_outward(edge, slid_rect, expect):
    ride, _ = _ride()
    act = ride.bar_moved(slid_rect, MON, edge, 1.0, visible=True, pos=(1700, 1000), mode=MODE_RIDE,
                         ride_in_allowed=lambda: True)
    assert act.move_to == expect


def test_the_slide_is_scaled_to_logical_px():
    ride, _ = _ride()
    assert _ev(ride, 30, visible=True, dpi=1.5).move_to == (1700, 1056)


# --- the widget's wiring ----------------------------------------------------------------------------


def _widget(visible=True, free_float=False, free_move=False):
    w = MagicMock()
    w.config = {"free_move": free_move, "keep_visible_fullscreen": False, "preferred_monitor": None}
    w._is_context_menu_visible = False
    w._dragging = False
    w._visible = visible
    w.isVisible.side_effect = lambda: w._visible
    w.setVisible.side_effect = lambda v: setattr(w, "_visible", v)
    w.x.return_value, w.y.return_value = DOCK
    w.position_manager.is_free_float_active.return_value = free_float
    w.position_manager.is_yielding_to_apps.return_value = False
    w._autohide_ride_state = AutohideRide(clock=Clock())
    w._fs_latch = widget_main.FullscreenLatch()
    w._autohide_taskbar_info = SimpleNamespace(hwnd=4242, monitor_rect=MON, dpi_scale=1.0,
                                               get_edge_position=lambda: E.BOTTOM)
    for name in ("_on_taskbar_moved", "_apply_autohide_action", "_cancel_autohide_ride",
                 "_autohide_ride_in_allowed"):
        setattr(w, name, types.MethodType(getattr(widget_main.NetworkSpeedWidget, name), w))
    return w


def _slide(monkeypatch, w, *steps):
    for slid in steps:
        monkeypatch.setattr(widget_main.win32gui, "GetWindowRect", lambda h, s=slid: bar(s))
        w._on_taskbar_moved(4242)


def test_widget_rides_out_and_hides(monkeypatch):
    w = _widget()
    _slide(monkeypatch, w, 10, 30, 46)
    moves = [c.args[0] for c in w.move.call_args_list]
    assert [(p.x(), p.y()) for p in moves] == [(1700, 1046), (1700, 1066), DOCK]
    assert w._visible is False


def test_free_floating_widget_never_hides_or_moves_with_the_primary_bar(monkeypatch):
    w = _widget(free_float=True)
    _slide(monkeypatch, w, 10, 30, 46, 20, 0)
    w.move.assert_not_called()
    w.setVisible.assert_not_called()


def test_free_move_widget_hides_but_is_never_shifted(monkeypatch):
    w = _widget(free_move=True)
    _slide(monkeypatch, w, 10, 30, 46)
    w.move.assert_not_called()
    assert w._visible is False


def test_no_ride_in_over_a_fullscreen_window_started_while_the_bar_was_hidden(monkeypatch):
    w = _widget()
    _slide(monkeypatch, w, 10, 46)                  # bar hides: widget hidden by its bar
    monkeypatch.setattr(widget_main.win32gui, "GetForegroundWindow", lambda: 777)
    monkeypatch.setattr(widget_main, "is_taskbar_obstructed", lambda tb, hwnd: hwnd == 777)
    w.move.reset_mock()
    _slide(monkeypatch, w, 40, 20)                  # edge poked over the game
    assert w._visible is False
    w.move.assert_not_called()


def test_no_ride_in_while_the_fullscreen_latch_is_covered(monkeypatch):
    w = _widget()
    _slide(monkeypatch, w, 10, 46)
    w._fs_latch.note_covered(777)
    monkeypatch.setattr(widget_main, "is_taskbar_obstructed", lambda tb, hwnd: False)
    _slide(monkeypatch, w, 40)
    assert w._visible is False


def test_immediate_fullscreen_hide_drops_the_ride_in_claim(monkeypatch):
    w = _widget()
    _slide(monkeypatch, w, 10, 46)
    assert w._autohide_ride_state.hid_widget
    monkeypatch.setattr(widget_main.win32gui, "GetForegroundWindow", lambda: 777)
    monkeypatch.setattr(widget_main, "is_window_on_screen", lambda h: True)
    widget_main.NetworkSpeedWidget._on_immediate_hide_requested(w)
    assert not w._autohide_ride_state.hid_widget


def test_missing_monitor_rect_cancels_a_slide(monkeypatch):
    w = _widget()
    _slide(monkeypatch, w, 10)
    w._autohide_taskbar_info.monitor_rect = None
    _slide(monkeypatch, w, 20)
    assert not w._autohide_ride_state.sliding
    assert (w.move.call_args.args[0].x(), w.move.call_args.args[0].y()) == DOCK


def _refresh_world(monkeypatch):
    monkeypatch.setattr(widget_main, "get_taskbar_info",
                        lambda preferred_screen_name=None: SimpleNamespace(hwnd=4242))
    monkeypatch.setattr(widget_main, "is_taskbar_visible", lambda tb: True)
    monkeypatch.setattr(widget_main, "_judge_fullscreen", lambda *a, **k: False)
    monkeypatch.setattr(widget_main.win32gui, "GetForegroundWindow", lambda: 0)


def _refresh_self(w):
    w._taskbar_lost_count = 0
    w.position_manager.refresh_float_state.return_value = None
    w.position_manager.holding_for_taskbar.return_value = False
    return w


def test_refresh_tick_leaves_a_live_slide_alone(monkeypatch):
    _refresh_world(monkeypatch)
    w = _refresh_self(_widget())
    _slide(monkeypatch, w, 10)
    w.position_manager.update_position.reset_mock()
    widget_main.NetworkSpeedWidget._execute_refresh(w)
    w.position_manager.update_position.assert_not_called()


def test_refresh_tick_recovers_a_stuck_slide(monkeypatch):
    """Point 3: the last event never came - back to the dock, then the normal refresh."""
    _refresh_world(monkeypatch)
    w = _refresh_self(_widget())
    _slide(monkeypatch, w, 10)
    w._autohide_ride_state._clock.t += constants.timeouts.AUTOHIDE_SLIDE_STUCK_MS / 1000.0 + 0.1
    widget_main.NetworkSpeedWidget._execute_refresh(w)
    assert not w._autohide_ride_state.sliding
    last = w.move.call_args.args[0]
    assert (last.x(), last.y()) == DOCK
    w.position_manager.update_position.assert_called_once()


# --- the hook only while auto-hide is on (point 5) -----------------------------------------------------


class FakeHook:
    instances = []

    def __init__(self, *a, hwnd_to_watch=0, **k):
        self.hwnd = hwnd_to_watch
        self.started = self.stopped = False
        self.event_triggered = SimpleNamespace(connect=lambda cb: None)
        FakeHook.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


@pytest.fixture
def hooks(monkeypatch):
    import netspeedtray.utils.win_event_hook as weh
    FakeHook.instances = []
    monkeypatch.setattr(weh, "WinEventHook", FakeHook)
    return FakeHook.instances


def _hook_self():
    w = _widget()
    w._autohide_hook = None
    w._autohide_hook_hwnd = 0
    for name in ("_sync_autohide_hook", "_stop_autohide_hook"):
        setattr(w, name, types.MethodType(getattr(widget_main.NetworkSpeedWidget, name), w))
    return w


def test_hook_attaches_only_while_auto_hide_is_on_and_detaches_when_it_turns_off(monkeypatch, hooks):
    state = {"on": False}
    monkeypatch.setattr(widget_main, "is_autohide_enabled", lambda h: state["on"])
    w = _hook_self()
    tb = SimpleNamespace(hwnd=4242)
    w._sync_autohide_hook(tb)
    assert hooks == [] and w._autohide_taskbar_info is None
    state["on"] = True
    w._sync_autohide_hook(tb)
    w._sync_autohide_hook(tb)                       # unchanged: no second hook
    assert len(hooks) == 1 and hooks[0].started and hooks[0].hwnd == 4242
    state["on"] = False
    w._sync_autohide_hook(tb)
    assert hooks[0].stopped and w._autohide_hook is None


def test_hook_is_not_attached_for_a_free_floating_widget(monkeypatch, hooks):
    monkeypatch.setattr(widget_main, "is_autohide_enabled", lambda h: True)
    w = _hook_self()
    w._sync_autohide_hook(SimpleNamespace(hwnd=4242), docked=False)
    assert hooks == []


def test_reattaching_to_a_new_bar_cancels_a_slide(monkeypatch, hooks):
    monkeypatch.setattr(widget_main, "is_autohide_enabled", lambda h: True)
    w = _hook_self()
    w._sync_autohide_hook(w._autohide_taskbar_info)
    _slide(monkeypatch, w, 10)
    assert w._autohide_ride_state.sliding
    w._sync_autohide_hook(SimpleNamespace(hwnd=5151))   # Explorer restarted: a new bar window
    assert hooks[0].stopped and hooks[1].hwnd == 5151
    assert not w._autohide_ride_state.sliding
