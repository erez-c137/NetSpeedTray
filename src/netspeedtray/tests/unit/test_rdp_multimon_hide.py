"""
#132 (rc.2 F2): with Remote Desktop's "Use all my monitors", mstsc's TscShellContainerClass window is
ONE caption-less window spanning every monitor. The fullscreen test (window rect == its monitor's
rect) never matched it, so the local widget showed through the remote desktop. Such a window whose
rect contains the whole monitor of our taskbar now counts as a fullscreen obstruction - with "Keep
visible in fullscreen" as the opt-out, exactly like any other fullscreen app. A windowed session
(caption on, even maximized) and every other window class behave as before.
"""
from unittest.mock import MagicMock, patch

import pytest
import win32con

from netspeedtray.utils import taskbar_utils as tu

MON1 = (0, 0, 1920, 1080)          # the taskbar's monitor
MON2 = (1920, 0, 4480, 1440)
SPAN = (0, 0, 4480, 1440)          # the multi-monitor RDP window
RDP = "TscShellContainerClass"
FULLSCREEN_STYLE = win32con.WS_POPUP | win32con.WS_VISIBLE
WINDOWED_STYLE = win32con.WS_OVERLAPPEDWINDOW | win32con.WS_VISIBLE


def _desktop(window_rect, class_name=RDP, style=FULLSCREEN_STYLE, fg_monitor=2, process="mstsc.exe"):
    """Patch taskbar_utils' Win32 surface: our taskbar is on monitor 1 (MON1)."""
    gui = MagicMock()
    gui.IsWindow.return_value = True
    gui.GetClassName.return_value = class_name
    gui.GetWindowRect.return_value = window_rect
    gui.GetWindowLong.side_effect = lambda h, i: style if i == win32con.GWL_STYLE else 0
    gui.error = Exception
    api = MagicMock()
    api.MonitorFromWindow.side_effect = lambda h, flags=0: 1 if h == 0xABC else fg_monitor
    api.GetMonitorInfo.side_effect = lambda m: {"Monitor": MON1 if m == 1 else MON2}
    proc = MagicMock()
    proc.GetWindowThreadProcessId.return_value = (1, 4321)
    return patch.multiple(tu, win32gui=gui, win32api=api, win32process=proc,
                          get_process_name_from_hwnd=MagicMock(return_value=process))


def _taskbar():
    return MagicMock(hwnd=0xABC)


def test_multimonitor_rdp_session_obstructs_the_taskbar_even_from_another_monitor():
    with _desktop(SPAN):
        assert tu.is_taskbar_obstructed(_taskbar(), 0x555) is True


@pytest.mark.parametrize("kwargs", [
    {"style": WINDOWED_STYLE},                             # windowed (caption) - even maximized
    {"window_rect": (1920, 0, 4480, 1440)},                # covers only the OTHER monitor
    {"class_name": "Chrome_WidgetWin_1"},                  # any other spanning window
    {"window_rect": (-8, -8, 1928, 1040), "style": WINDOWED_STYLE, "fg_monitor": 1},  # maximized RDP
])
def test_everything_else_is_unchanged(kwargs):
    kwargs.setdefault("window_rect", SPAN)
    with _desktop(**kwargs):
        assert tu.is_taskbar_obstructed(_taskbar(), 0x555) is False


def test_plain_fullscreen_still_obstructs():
    with _desktop(MON1, class_name="UnrealWindow", fg_monitor=1, process="game.exe"):
        assert tu.is_taskbar_obstructed(_taskbar(), 0x555) is True


def test_fullscreen_check_does_not_short_circuit_a_spanning_rdp_window(q_app):
    """The event handler's cheap pre-check (window == its monitor) must let the RDP window through to
    the taskbar check, and still short-circuit everything else."""
    from netspeedtray.core import system_events as se
    with patch("netspeedtray.core.system_events.WinEventHook"):
        handler = se.SystemEventHandler()
    try:
        for cls, expected_calls in ((RDP, 1), ("Notepad", 0)):
            with patch("win32gui.IsWindow", return_value=True), \
                    patch("win32gui.GetWindowRect", return_value=SPAN), \
                    patch("win32gui.GetClassName", return_value=cls), \
                    patch("win32api.MonitorFromWindow", return_value=2), \
                    patch("win32api.GetMonitorInfo", return_value={"Monitor": MON2}), \
                    patch.object(se, "get_taskbar_info", return_value=_taskbar()), \
                    patch.object(se, "is_taskbar_obstructed", return_value=True) as obstructed:
                assert handler._is_fullscreen_obstruction(0x555) is bool(expected_calls)
                assert obstructed.call_count == expected_calls
    finally:
        handler.stop()
