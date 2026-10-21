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
        for _ in range(20):                                      # a burst inside the window
            clock.t += 0.002
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
