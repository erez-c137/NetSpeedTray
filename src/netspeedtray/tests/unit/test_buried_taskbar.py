"""
Tests for the buried-behind-taskbar recovery (is_buried_behind_taskbar / reseat_topmost)
and the WS_EX_TOPMOST style restore in ensure_topmost().

Complements test_topmost_zorder.py, which locks in the #200 rule that the steady-state
cadence must NOT reorder Z. These cover the deliberate exceptions: the widget provably
buried behind the taskbar itself, or dropped out of the topmost band entirely.

The hit-test must run in PHYSICAL pixels: it reads the widget rect via native
GetWindowRect (the widget's own HWND), never via Qt's mapToGlobal(), whose
device-independent coordinates land ~25% off at 125% scaling and hit-test the wrong
window (live-observed on a 1920x1200/125% machine: the widget sat behind the taskbar
while the logical-space check reported "not buried").
"""
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from netspeedtray.core.position_manager import PositionManager, WindowState

import win32con


TB_HWND = 0xABC
WIDGET_HWND = 0x1000
MENU_HWND = 0x2000      # a shell menu / foreign window
# Physical-pixel rect over the taskbar (matches a live Win11 125% capture)
WIDGET_RECT = (1167, 912, 1252, 960)


def _manager(visible=True):
    widget = MagicMock()
    widget.winId.return_value = WIDGET_HWND
    widget.isVisible.return_value = visible
    tb_info = MagicMock()
    tb_info.hwnd = TB_HWND
    state = WindowState(config={}, widget=widget, taskbar_info=tb_info)
    return PositionManager(state)


def _fake_class_name(class_name):
    def fake(hwnd, buf, n):
        buf.value = class_name
        return len(class_name)
    return fake


@contextmanager
def _hit_env(hit, root, class_name="", visible=True):
    """Open the full patch surface for is_buried_behind_taskbar."""
    m = _manager(visible=visible)
    with patch("netspeedtray.core.position_manager.win32gui") as win32, \
         patch("netspeedtray.core.position_manager._WindowFromPoint", return_value=hit) as from_point, \
         patch("netspeedtray.core.position_manager._GetAncestor", return_value=root), \
         patch("netspeedtray.core.position_manager._GetClassNameW",
               side_effect=_fake_class_name(class_name)):
        win32.IsWindow.return_value = True
        win32.GetWindowRect.return_value = WIDGET_RECT
        yield m, win32, from_point


def test_on_top_is_not_buried():
    """The hit-test returns the widget itself -> not buried."""
    with _hit_env(WIDGET_HWND, WIDGET_HWND) as (m, win32, _):
        assert m.is_buried_behind_taskbar() is False


def test_buried_behind_taskbar_detected():
    """Hit lands on the taskbar (root Shell_TrayWnd) while we are visible -> buried."""
    with _hit_env(0x50, TB_HWND, "Shell_TrayWnd") as (m, win32, _):
        assert m.is_buried_behind_taskbar() is True


def test_hit_test_uses_native_physical_rect():
    """The hit point must come from GetWindowRect(hwnd), not Qt mapToGlobal()."""
    with _hit_env(0x50, TB_HWND, "Shell_TrayWnd") as (m, _, _):
        m.is_buried_behind_taskbar()
        m._state.widget.mapToGlobal.assert_not_called()


def test_secondary_taskbar_counts_as_burial():
    with _hit_env(0x50, 0xBEEF, "Shell_SecondaryTrayWnd") as (m, win32, _):
        assert m.is_buried_behind_taskbar() is True


def test_shell_menu_is_not_burial():
    """A #32768 menu covering the widget is the #200 case - must NOT count as burial."""
    with _hit_env(MENU_HWND, MENU_HWND, "#32768") as (m, win32, _):
        assert m.is_buried_behind_taskbar() is False


def test_win11_flyout_is_not_burial():
    with _hit_env(0x60, 0x60, "XamlExplorerHostIslandWindow") as (m, win32, _):
        assert m.is_buried_behind_taskbar() is False


def test_hidden_widget_skips_hit_test():
    """Hidden widgets are never 'buried', and the hit-test must not even run."""
    with _hit_env(0x50, TB_HWND, "Shell_TrayWnd", visible=False) as (m, _, from_point):
        assert m.is_buried_behind_taskbar() is False
        from_point.assert_not_called()


def test_degenerate_rect_is_not_buried():
    """A zero-area rect cannot be hit-tested -> False, no crash."""
    with _hit_env(0x50, TB_HWND, "Shell_TrayWnd") as (m, win32, from_point):
        win32.GetWindowRect.return_value = (100, 100, 100, 100)
        assert m.is_buried_behind_taskbar() is False
        from_point.assert_not_called()


def test_reseat_topmost_uses_topmost_insert():
    m = _manager()
    with patch("netspeedtray.core.position_manager.win32gui") as win32:
        win32.IsWindow.return_value = True
        m.reseat_topmost()
        assert win32.SetWindowPos.call_count == 1
        args = win32.SetWindowPos.call_args[0]
        assert args[0] == WIDGET_HWND
        assert args[1] == win32con.HWND_TOPMOST
        assert args[6] == (win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)


def test_ensure_topmost_restores_lost_topmost_style():
    """Steady-state owner + missing WS_EX_TOPMOST -> restore the band (the new elif)."""
    m = _manager()
    with patch("netspeedtray.core.position_manager.win32gui") as win32, \
         patch("netspeedtray.core.position_manager._GetWindowLongPtr", return_value=TB_HWND), \
         patch("netspeedtray.core.position_manager._SetWindowLongPtr"):
        win32.IsWindow.return_value = True
        win32.GetWindowLong.return_value = 0   # style bit gone
        m.ensure_topmost()
        assert win32.SetWindowPos.call_count == 1
        assert win32.SetWindowPos.call_args[0][1] == win32con.HWND_TOPMOST


def test_ensure_topmost_leaves_intact_topmost_alone():
    """Steady-state owner + WS_EX_TOPMOST present -> no SetWindowPos at all (#200)."""
    m = _manager()
    with patch("netspeedtray.core.position_manager.win32gui") as win32, \
         patch("netspeedtray.core.position_manager._GetWindowLongPtr", return_value=TB_HWND), \
         patch("netspeedtray.core.position_manager._SetWindowLongPtr"):
        win32.IsWindow.return_value = True
        win32.GetWindowLong.return_value = win32con.WS_EX_TOPMOST
        m.ensure_topmost()
        win32.SetWindowPos.assert_not_called()
