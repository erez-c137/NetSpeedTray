"""
#200 regression: ensure_topmost() must NOT re-insert the taskbar-owned widget at the top of
the topmost Z-band on the steady-state cadence, because that drags the taskbar's owner-cluster
above open shell menus/flyouts and clips their overlapping rows. It may reorder Z ONLY when it
actually (re)establishes the taskbar ownership (first dock / explorer restart), when no such
menu can be up.
"""
from unittest.mock import MagicMock, patch

import pytest

from netspeedtray.core.position_manager import PositionManager, WindowState


TB_HWND = 0xABC        # the taskbar
WIDGET_HWND = 0x1000   # our widget


def _manager(owner_returns):
    """Build a PositionManager whose widget winId() is WIDGET_HWND and whose taskbar is TB_HWND.
    `owner_returns` is what _GetWindowLongPtr(GWLP_HWNDPARENT) reports (the current owner)."""
    widget = MagicMock()
    widget.winId.return_value = WIDGET_HWND
    tb_info = MagicMock()
    tb_info.hwnd = TB_HWND
    state = WindowState(config={}, widget=widget, taskbar_info=tb_info)
    return PositionManager(state)


def _patches(owner_value):
    """Patch the win32 surface ensure_topmost touches. IsWindow is always True."""
    m = _manager(owner_value)
    p_win32 = patch("netspeedtray.core.position_manager.win32gui")
    p_get = patch("netspeedtray.core.position_manager._GetWindowLongPtr", return_value=owner_value)
    p_set = patch("netspeedtray.core.position_manager._SetWindowLongPtr")
    return m, p_win32, p_get, p_set


def test_steady_state_does_not_reorder_zorder():
    """Owner already the taskbar -> no SetWindowLongPtr, no SetWindowPos (menus undisturbed)."""
    m, p_win32, p_get, p_set = _patches(owner_value=TB_HWND)
    with p_win32 as win32, p_get, p_set as set_owner:
        win32.IsWindow.return_value = True
        m.ensure_topmost()
        set_owner.assert_not_called()
        win32.SetWindowPos.assert_not_called()


def test_first_dock_reorders_once():
    """No owner yet -> establish ownership and a single HWND_TOPMOST assert to seat the band."""
    m, p_win32, p_get, p_set = _patches(owner_value=0)
    with p_win32 as win32, p_get, p_set as set_owner:
        import win32con
        win32.IsWindow.return_value = True
        m.ensure_topmost()
        set_owner.assert_called_once()               # owner (re)established
        assert win32.SetWindowPos.call_count == 1     # exactly one topmost assert
        args = win32.SetWindowPos.call_args[0]
        assert args[0] == WIDGET_HWND
        assert args[1] == win32con.HWND_TOPMOST


def test_explorer_restart_reowns_and_reasserts():
    """Owner points at a stale taskbar handle -> re-own to the fresh one and re-seat once."""
    m, p_win32, p_get, p_set = _patches(owner_value=0xDEAD)   # stale != TB_HWND
    with p_win32 as win32, p_get, p_set as set_owner:
        win32.IsWindow.return_value = True
        m.ensure_topmost()
        set_owner.assert_called_once()
        assert win32.SetWindowPos.call_count == 1


def test_ensure_taskbar_owner_return_contract():
    """_ensure_taskbar_owner returns True only when it just (re)set the owner."""
    # already owned -> False
    m, p_win32, p_get, p_set = _patches(owner_value=TB_HWND)
    with p_win32 as win32, p_get, p_set:
        win32.IsWindow.return_value = True
        assert m._ensure_taskbar_owner(WIDGET_HWND) is False
    # not owned -> True
    m2, p_win32b, p_getb, p_setb = _patches(owner_value=0)
    with p_win32b as win32b, p_getb, p_setb:
        win32b.IsWindow.return_value = True
        assert m2._ensure_taskbar_owner(WIDGET_HWND) is True


def test_no_taskbar_is_a_noop():
    """If the taskbar handle is invalid, do nothing (no owner set, no reorder)."""
    m, p_win32, p_get, p_set = _patches(owner_value=0)
    with p_win32 as win32, p_get, p_set as set_owner:
        # widget hwnd is a valid window, but the taskbar hwnd is not
        win32.IsWindow.side_effect = lambda h: h == WIDGET_HWND
        m.ensure_topmost()
        set_owner.assert_not_called()
        win32.SetWindowPos.assert_not_called()
