"""
Event-driven burial recovery (#314, #238, PR #327): the desktop EVENT_OBJECT_REORDER hook
(pasharm's lead), the zorder_changed coalescer in SystemEventHandler, the WinEventHook source
filter, and the widget's _recover_if_buried guards (hidden / own menu / drag / fullscreen).
"""
import types
from unittest.mock import MagicMock, patch

import pytest

from netspeedtray import constants
from netspeedtray.core import system_events as se_mod
from netspeedtray.core.system_events import SystemEventHandler
from netspeedtray.utils.win_event_hook import EVENT_OBJECT_REORDER, WinEventHook
from netspeedtray.views.widget.main import NetworkSpeedWidget


class _Clock:
    def __init__(self, t=100.0):
        self.t = t

    def monotonic(self):
        return self.t


@pytest.fixture
def handler(q_app):
    with patch.object(se_mod, "WinEventHook") as hook_cls:
        h = SystemEventHandler()
        h._hook_cls = hook_cls
        yield h
        h.stop()


# --- the hook ---------------------------------------------------------------------------


def test_reorder_hook_is_system_wide_and_filtered_to_the_desktop(handler):
    with patch.object(se_mod.win32gui, "GetDesktopWindow", return_value=0x10010), \
         patch.object(se_mod, "get_taskbar_info") as tb:
        tb.return_value.hwnd = 0x123
        handler.start()
    calls = [c for c in handler._hook_cls.call_args_list if c.args and c.args[0] == EVENT_OBJECT_REORDER]
    assert len(calls) == 1
    assert calls[0].kwargs.get("source_hwnd_filter") == 0x10010
    assert calls[0].kwargs.get("hwnd_to_watch", 0) == 0          # process-wide, not per-thread
    assert calls[0].kwargs.get("skip_own_process") is True       # our own re-seat echoes dropped
    assert handler.reorder_hook is not None


def test_reorder_hook_failure_degrades_to_the_safety_net(handler):
    with patch.object(se_mod.win32gui, "GetDesktopWindow", side_effect=RuntimeError("no desktop")):
        handler._attach_reorder_hook()
    assert handler.reorder_hook is None


def test_stop_stops_the_reorder_hook(handler):
    hook = MagicMock()
    handler.reorder_hook = hook
    handler.stop()
    hook.stop.assert_called_once()


def test_reorder_hook_is_rebuilt_not_stacked(handler):
    """start()/stop() cycles on every explorer restart must not leave stale hooks running."""
    old = MagicMock()
    handler.reorder_hook = old
    with patch.object(se_mod.win32gui, "GetDesktopWindow", return_value=0x10010):
        handler._attach_reorder_hook()
    old.stop.assert_called_once()


# --- the coalescer ----------------------------------------------------------------------


def test_first_change_emits_at_once_burst_collapses_to_one_trailing(handler):
    clock = _Clock()
    slot = MagicMock()
    handler.zorder_changed.connect(slot)
    with patch.object(se_mod.time, "monotonic", side_effect=clock.monotonic):
        handler._on_zorder_event(0x10010)
        assert slot.call_count == 1                              # leading edge: immediate
        step = constants.timeouts.ZORDER_RECHECK_MIN_INTERVAL_MS / 1000.0 / 25
        for _ in range(20):                                      # a burst inside the window
            clock.t += step
            handler._on_zorder_event(0x10010)
        assert slot.call_count == 1
        assert handler._zorder_trailing_timer.isActive()
        handler._zorder_trailing_timer.stop()
        handler._emit_zorder_changed()                           # the trailing edge fires
        assert slot.call_count == 2


def test_change_after_quiet_spell_emits_at_once(handler):
    clock = _Clock()
    slot = MagicMock()
    handler.zorder_changed.connect(slot)
    with patch.object(se_mod.time, "monotonic", side_effect=clock.monotonic):
        handler._note_zorder_change()
        clock.t += constants.timeouts.ZORDER_RECHECK_MIN_INTERVAL_MS / 1000.0 + 0.01
        handler._note_zorder_change()
    assert slot.call_count == 2
    assert not handler._zorder_trailing_timer.isActive()


def _run_burst(handler, clock, event_offsets_ms, until_ms):
    """Feed reorder events at the given offsets (ms) through the real coalescer on a 1 ms
    simulated clock; the trailing QTimer is fired by hand when its interval has elapsed.
    Returns the offsets (ms) at which zorder_changed was emitted."""
    emits = []
    handler.zorder_changed.connect(lambda: emits.append(round((clock.t - start) * 1000)))
    start = clock.t
    due = None
    pending = sorted(event_offsets_ms)
    for ms in range(until_ms + 1):
        clock.t = start + ms / 1000.0
        if due is not None and clock.t >= due - 1e-9:
            handler._zorder_trailing_timer.stop()
            handler._emit_zorder_changed()
            due = None
        while pending and pending[0] <= ms:
            pending.pop(0)
            handler._on_zorder_event(0x10010)
            if due is None and handler._zorder_trailing_timer.isActive():
                due = clock.t + handler._zorder_trailing_timer.interval() / 1000.0
    handler._zorder_trailing_timer.stop()
    return emits


def test_burial_15ms_into_a_burst_is_rechecked_within_a_frame(handler):
    """pasharm (PR #424, Win10): a maximize / restore fires 6-15 desktop reorders; the first is
    checked at once, but the burial itself lands 10-20 ms in. At a 100 ms coalescer that burial
    waited for the trailing edge (~80 ms visible blink). It must be rechecked within ~one frame."""
    clock = _Clock()
    burial_ms = 15
    burst = [0, 3, 6, 9, 12, burial_ms, 18, 22, 27, 33]
    with patch.object(se_mod.time, "monotonic", side_effect=clock.monotonic):
        emits = _run_burst(handler, clock, burst, until_ms=300)
    assert emits[0] == 0                                         # leading edge: immediate
    after = [e for e in emits if e >= burial_ms]
    assert after, emits
    assert after[0] - burial_ms <= 16 + 2, emits                 # the burial is seen within a frame
    assert emits[-1] >= burst[-1]                                # the final Z-order is checked
    assert len(emits) <= 1 + (burst[-1] // 16) + 1, emits        # still at most one check a frame


def test_paused_handler_emits_nothing(handler):
    slot = MagicMock()
    handler.zorder_changed.connect(slot)
    handler.pause()
    handler._note_zorder_change()
    handler._emit_zorder_changed()
    slot.assert_not_called()


def test_foreground_change_also_triggers_a_zorder_check(handler):
    """#238: hovering grouped previews / any activation can raise the taskbar."""
    slot = MagicMock()
    handler.zorder_changed.connect(slot)
    with patch("win32gui.IsWindow", return_value=True), \
         patch("win32gui.GetClassName", return_value="Shell_TrayWnd"):
        handler._on_foreground_change_immediate(hwnd=777)
    slot.assert_called_once()


# --- WinEventHook source filter -------------------------------------------------------


def test_win_event_hook_source_filter_drops_other_sources(q_app):
    hook = WinEventHook(EVENT_OBJECT_REORDER, source_hwnd_filter=0x10010)
    slot = MagicMock()
    hook.event_triggered.connect(slot)
    hook.callback(None, EVENT_OBJECT_REORDER, 0x55, -4, 0, 0, 0)     # a child reorder elsewhere
    slot.assert_not_called()
    hook.callback(None, EVENT_OBJECT_REORDER, 0x10010, -4, 0, 0, 0)  # the desktop
    slot.assert_called_once_with(0x10010)


def test_win_event_hook_without_filter_passes_everything_and_null_hwnd(q_app):
    hook = WinEventHook(EVENT_OBJECT_REORDER)
    slot = MagicMock()
    hook.event_triggered.connect(slot)
    hook.callback(None, EVENT_OBJECT_REORDER, None, 0, 0, 0, 0)
    slot.assert_called_once_with(0)


# --- the widget's guards ----------------------------------------------------------------


def _fake_widget(visible=True, menu=False, dragging=False, fullscreen=False, buried=True):
    pm = MagicMock()
    pm.is_buried_behind_taskbar.return_value = buried
    handler = MagicMock()
    handler.is_fullscreen_active.return_value = fullscreen
    return types.SimpleNamespace(
        isVisible=lambda: visible, _is_context_menu_visible=menu, _dragging=dragging,
        system_event_handler=handler, position_manager=pm, logger=MagicMock())


def test_buried_widget_is_reseated():
    fake = _fake_widget()
    NetworkSpeedWidget._recover_if_buried(fake)
    fake.position_manager.reseat_above_taskbar.assert_called_once_with()


def test_widget_on_top_is_left_alone():
    fake = _fake_widget(buried=False)
    NetworkSpeedWidget._recover_if_buried(fake)
    fake.position_manager.reseat_above_taskbar.assert_not_called()


@pytest.mark.parametrize("kwargs", [
    {"visible": False}, {"menu": True}, {"dragging": True}, {"fullscreen": True},
])
def test_guards_skip_the_check_entirely(kwargs):
    """Hidden, our own menu open, mid-drag, or a fullscreen app (Keep visible in fullscreen
    keeps its old behaviour): no Z-order work at all."""
    fake = _fake_widget(**kwargs)
    NetworkSpeedWidget._recover_if_buried(fake)
    fake.position_manager.is_buried_behind_taskbar.assert_not_called()
    fake.position_manager.reseat_above_taskbar.assert_not_called()
    # S3 (#327 review): an unverified tick breaks the on-top stretch that retires the backoff.
    fake.position_manager.note_on_top_unverified.assert_called_once_with()


def test_recovery_errors_are_swallowed():
    fake = _fake_widget()
    fake.position_manager.is_buried_behind_taskbar.side_effect = RuntimeError("boom")
    NetworkSpeedWidget._recover_if_buried(fake)   # must not raise


def test_explorer_restart_resets_the_backoff(monkeypatch):
    monkeypatch.setattr("netspeedtray.views.widget.main.QTimer.singleShot", lambda _ms, _fn: None)
    fake = types.SimpleNamespace(logger=MagicMock(), _execute_refresh=MagicMock(),
                                 position_manager=MagicMock())
    NetworkSpeedWidget._recover_taskbar_restarted(fake)
    fake.position_manager.reset_reseat_state.assert_called_once_with()


# --- S1/S2 (#327 review): the topmost cadence recovers only through the guarded path ------


def _fake_topmost(visible=True, **kw):
    fake = _fake_widget(visible=visible, **kw)
    fake._allow_taskbar_created = MagicMock()
    fake._recover_if_buried = MagicMock()
    return fake


def test_topmost_cadence_recovers_through_the_guarded_path():
    fake = _fake_topmost()
    NetworkSpeedWidget._ensure_win32_topmost(fake)
    fake.position_manager.ensure_topmost.assert_called_once_with()
    fake._recover_if_buried.assert_called_once_with()
    fake.position_manager.reseat_above_taskbar.assert_not_called()   # never directly


def test_topmost_cadence_with_our_menu_open_does_not_reseat():
    """The guarded path itself, end to end: a lost-topmost/buried widget with our own context
    menu open is left alone (the old ensure_topmost elif re-seated regardless)."""
    fake = _fake_widget(menu=True)
    fake._allow_taskbar_created = MagicMock()
    fake._recover_if_buried = lambda: NetworkSpeedWidget._recover_if_buried(fake)
    NetworkSpeedWidget._ensure_win32_topmost(fake)
    fake.position_manager.reseat_above_taskbar.assert_not_called()


def test_hidden_topmost_cadence_breaks_the_stretch():
    fake = _fake_topmost(visible=False)
    NetworkSpeedWidget._ensure_win32_topmost(fake)
    fake.position_manager.ensure_topmost.assert_not_called()
    fake.position_manager.note_on_top_unverified.assert_called_once_with()


# --- WinEventHook lifecycle: a system-wide hook must always be unhooked -------------------


def test_hook_prototypes_are_pointer_sized():
    import ctypes
    from ctypes import wintypes
    from netspeedtray.utils import win_event_hook as weh
    assert weh._SetWinEventHook.restype is wintypes.HANDLE
    assert weh._UnhookWinEvent.argtypes == [wintypes.HANDLE]
    assert ctypes.sizeof(weh._SetWinEventHook.restype) == ctypes.sizeof(ctypes.c_void_p)


def test_skip_own_process_flag(q_app):
    from netspeedtray.utils import win_event_hook as weh
    assert WinEventHook(EVENT_OBJECT_REORDER)._flags == weh.WINEVENT_OUTOFCONTEXT
    assert WinEventHook(EVENT_OBJECT_REORDER, skip_own_process=True)._flags ==         weh.WINEVENT_OUTOFCONTEXT | weh.WINEVENT_SKIPOWNPROCESS


def test_stop_right_after_start_still_unhooks(q_app):
    """start() then stop() within milliseconds - before run() published its thread id - used to
    return early and leak the thread and its SYSTEM-WIDE hook."""
    import time as _time
    from netspeedtray.utils import win_event_hook as weh
    real_peek = weh._PeekMessageW

    def slow_peek(*a):
        _time.sleep(0.2)            # widen the start-up window stop() lands in
        return real_peek(*a)

    unhook = MagicMock(return_value=True)
    with patch.object(weh, "_SetWinEventHook", return_value=0x5150) as sethook,          patch.object(weh, "_UnhookWinEvent", unhook),          patch.object(weh, "_PeekMessageW", side_effect=slow_peek):
        hook = WinEventHook(EVENT_OBJECT_REORDER)
        hook.start()
        hook.stop()                 # lands while run() is still starting up
        hook.join(3.0)
    assert not hook.is_alive()
    sethook.assert_called_once()
    unhook.assert_called_once_with(0x5150)


def test_stop_before_hook_install_never_installs(q_app):
    from netspeedtray.utils import win_event_hook as weh
    with patch.object(weh, "_SetWinEventHook", return_value=0x5150) as sethook,          patch.object(weh, "_UnhookWinEvent") as unhook:
        hook = WinEventHook(EVENT_OBJECT_REORDER)
        hook._stop_requested = True     # stop() won the race outright
        hook.run()
    sethook.assert_not_called()
    unhook.assert_not_called()
    assert hook._ready.is_set()


def test_stop_on_a_never_started_hook_is_a_noop(q_app):
    hook = WinEventHook(EVENT_OBJECT_REORDER)
    hook.stop()                         # must not wait or raise
