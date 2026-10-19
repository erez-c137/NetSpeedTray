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
import logging
from unittest.mock import MagicMock, patch

from netspeedtray.core.position_manager import PositionManager, WindowState

import win32con


TB_HWND = 0xABC
WIDGET_HWND = 0x1000
MENU_HWND = 0x2000      # a shell menu / foreign window
# Physical-pixel rect over the taskbar (matches a live Win11 125% capture)
WIDGET_RECT = (1167, 912, 1252, 960)
MON_A = 0xE001
MON_B = 0xE002


class _Clock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def monotonic(self):
        return self.t


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


# --- Re-seat guards (#327 review): backoff + shell-menu monitor check --------


def test_reseat_deferred_for_menu_on_same_monitor():
    """A visible #32768 anywhere on the widget's monitor defers, not just one over the center."""
    m = _manager()
    with patch("netspeedtray.core.position_manager.win32gui") as win32, \
         patch("netspeedtray.core.position_manager._MonitorFromWindow", side_effect=[MON_A, MON_A]):
        win32.IsWindow.return_value = True
        win32.IsWindowVisible.side_effect = lambda h: h == MENU_HWND
        win32.GetClassName.side_effect = lambda h: {MENU_HWND: "#32768"}.get(h, "Other")
        win32.EnumWindows.side_effect = lambda cb, ctx: cb(MENU_HWND, ctx) or cb(0x777, ctx)
        m.reseat_topmost()
        win32.SetWindowPos.assert_not_called()


def test_reseat_proceeds_when_menu_on_other_monitor():
    """A #32768 on another monitor must not hold the widget buried on this one."""
    m = _manager()
    with patch("netspeedtray.core.position_manager.win32gui") as win32, \
         patch("netspeedtray.core.position_manager._MonitorFromWindow", side_effect=[MON_A, MON_B]):
        win32.IsWindow.return_value = True
        win32.IsWindowVisible.side_effect = lambda h: h == MENU_HWND
        win32.GetClassName.side_effect = lambda h: {MENU_HWND: "#32768"}.get(h, "Other")
        win32.EnumWindows.side_effect = lambda cb, ctx: cb(MENU_HWND, ctx) or cb(0x777, ctx)
        m.reseat_topmost()
        assert win32.SetWindowPos.call_count == 1


def test_reseat_defers_when_menu_scan_fails():
    """A broken scan fails safe (defer). It can never turn recovery into the #200 re-assert."""
    m = _manager()
    with patch("netspeedtray.core.position_manager.win32gui") as win32, \
         patch("netspeedtray.core.position_manager._MonitorFromWindow", return_value=MON_A):
        win32.IsWindow.return_value = True
        win32.EnumWindows.side_effect = RuntimeError("enum blew up")
        m.reseat_topmost()
        win32.SetWindowPos.assert_not_called()


def test_reseat_backoff_escalates_when_reburying():
    """First recovery immediate, then 2 s -> 4 s windows; a persistent burial cannot ride 1 Hz."""
    m = _manager()
    clock = _Clock()
    with patch("netspeedtray.core.position_manager.win32gui") as win32, \
         patch("netspeedtray.core.position_manager._MonitorFromWindow", return_value=MON_A), \
         patch("netspeedtray.core.position_manager.time", clock):
        win32.IsWindow.return_value = True
        m.reseat_topmost()
        first = win32.SetWindowPos.call_count
        clock.t += 0.9              # inside the first (2 s) window -> deferred
        m.reseat_topmost()
        assert win32.SetWindowPos.call_count == first
        clock.t += 5.0              # past that window -> acts again
        m.reseat_topmost()
        assert win32.SetWindowPos.call_count == first + 1
        clock.t += 3.9              # inside the next (4 s) window -> deferred
        m.reseat_topmost()
        assert win32.SetWindowPos.call_count == first + 1
        clock.t += 2.0
        m.reseat_topmost()
        assert win32.SetWindowPos.call_count == first + 2


def test_reseat_delay_saturates_at_cap():
    m = _manager()
    assert m._reseat_delay() == 0.0
    m._last_reseat_ts = 1000.0
    m._reseat_attempts = 999
    assert m._reseat_delay() == 60.0


def test_on_top_single_tick_does_not_retire_backoff():
    """A single on-top tick is NOT proof of recovery (#327 review): backoff must survive it."""
    with _hit_env(WIDGET_HWND, WIDGET_HWND) as (m, _, _):
        m._reseat_attempts = 5
        m._last_reseat_ts = 900.0
        m._first_recovery_logged = True
        assert m.is_buried_behind_taskbar() is False
        assert m._reseat_attempts == 5 and m._last_reseat_ts == 900.0
        assert m._first_recovery_logged is True


def test_on_top_sustained_stretch_retires_backoff():
    """~60 s CONTINUOUSLY on top retires the escalation for the next episode."""
    clock = _Clock()
    with patch("netspeedtray.core.position_manager.time", clock):
        with _hit_env(WIDGET_HWND, WIDGET_HWND) as (m, _, _):
            m._reseat_attempts = 5
            m._last_reseat_ts = 900.0
            m._first_recovery_logged = True
            m.is_buried_behind_taskbar()          # arm the stretch at t=1000
            clock.t += 59.0
            m.is_buried_behind_taskbar()          # still inside the stretch
            assert m._reseat_attempts == 5
            clock.t += 2.0                        # past _RESEAT_HEALTHY_S
            m.is_buried_behind_taskbar()
            assert (m._reseat_attempts, m._last_reseat_ts, m._first_recovery_logged) == (0, 0.0, False)


def test_on_top_stretch_broken_by_burial_restarts():
    """A burial voids the continuous stretch; the clock restarts from the next on-top tick."""
    clock = _Clock()
    with patch("netspeedtray.core.position_manager.time", clock):
        with _hit_env(WIDGET_HWND, WIDGET_HWND) as (m, win32, from_point):
            m._reseat_attempts = 5
            m._last_reseat_ts = 900.0
            m.is_buried_behind_taskbar()          # verified on top: arm the stretch
            clock.t += 30.0
            from_point.return_value = 0x50        # flip to a burial tick
            with patch("netspeedtray.core.position_manager._GetAncestor", return_value=TB_HWND), \
                 patch("netspeedtray.core.position_manager._GetClassNameW",
                       side_effect=_fake_class_name("Shell_TrayWnd")):
                assert m.is_buried_behind_taskbar() is True
            assert m._on_top_since is None        # stretch broken
            from_point.return_value = WIDGET_HWND
            clock.t += 200.0                      # long on-top time, fresh stretch
            m.is_buried_behind_taskbar()
            assert m._reseat_attempts == 5        # stretched far, but stretch is young: no retire
            clock.t += 61.0
            m.is_buried_behind_taskbar()
            assert (m._reseat_attempts, m._last_reseat_ts, m._first_recovery_logged) == (0, 0.0, False)


def test_backoff_defers_before_menu_scan():
    """While a backoff window is open the EnumWindows scan must not run every tick."""
    m = _manager()
    clock = _Clock()
    with patch("netspeedtray.core.position_manager.win32gui") as win32, \
         patch("netspeedtray.core.position_manager.time", clock):
        win32.IsWindow.return_value = True
        m.reseat_topmost()                        # acts; scan ran once, backoff started
        assert win32.SetWindowPos.call_count == 1
        assert win32.EnumWindows.call_count == 1
        clock.t += 0.5                            # inside the 2 s window -> deferred
        m.reseat_topmost()
        assert win32.SetWindowPos.call_count == 1
        assert win32.EnumWindows.call_count == 1  # scan skipped inside the backoff


def test_menu_scan_logs_census_once_per_class(caplog):
    """Visible non-guard classes are logged once each, at DEBUG (the #327 research hook)."""
    caplog.set_level(logging.DEBUG)
    m = _manager()
    with patch("netspeedtray.core.position_manager.win32gui") as win32, \
         patch("netspeedtray.core.position_manager._MonitorFromWindow", return_value=MON_A):
        win32.IsWindow.return_value = True
        win32.IsWindowVisible.return_value = True
        win32.GetClassName.side_effect = lambda h: {
            0x77A: "ApplicationFrameWindow", 0x77B: "CabinetWClass"}.get(h, "#32768")
        win32.EnumWindows.side_effect = lambda cb, ctx: all(
            [cb(h, ctx) for h in (0x77A, 0x77B, WIDGET_HWND)])
        m.reseat_topmost()
        census = [r.getMessage() for r in caplog.records
                  if r.getMessage().startswith("Shell-menu scan census")]
        assert len(census) == 2                   # one line per foreign class
        m.reseat_topmost()                        # repeat scan: memoized, no new lines
        census2 = [r.getMessage() for r in caplog.records
                   if r.getMessage().startswith("Shell-menu scan census")]
        assert census2 == census


def test_reseat_deferred_for_win11_xaml_popup_on_same_monitor():
    """#327 review: on Win11 the shell's popup host is Xaml_WindowedPopupClass, not #32768."""
    m = _manager()
    with patch("netspeedtray.core.position_manager.win32gui") as win32, \
         patch("netspeedtray.core.position_manager._MonitorFromWindow", return_value=MON_A):
        win32.IsWindow.return_value = True
        win32.IsWindowVisible.side_effect = lambda h: h == MENU_HWND
        win32.GetClassName.side_effect = lambda h: {
            MENU_HWND: "Xaml_WindowedPopupClass"}.get(h, "Other")
        win32.EnumWindows.side_effect = lambda cb, ctx: cb(MENU_HWND, ctx)
        m.reseat_topmost()
        win32.SetWindowPos.assert_not_called()


def test_covering_window_class_is_censused(caplog):
    """#327 review: name what covers the widget and whether the guard matches it."""
    caplog.set_level(logging.DEBUG)
    with _hit_env(MENU_HWND, MENU_HWND, "XamlExplorerHostIslandWindow") as (m, _, _):
        assert m.is_buried_behind_taskbar() is False
    lines = [r.getMessage() for r in caplog.records if "covered by visible class" in r.getMessage()]
    assert len(lines) == 1
    assert "XamlExplorerHostIslandWindow" in lines[0]
    assert "guard match: False" in lines[0]


def test_burial_keeps_backoff_running():
    """A persistent burial must not reset its own backoff (that is the 1 Hz re-assert again)."""
    with _hit_env(0x50, TB_HWND, "Shell_TrayWnd") as (m, _, _):
        m._reseat_attempts = 5
        m._last_reseat_ts = 900.0
        m._first_recovery_logged = True
        assert m.is_buried_behind_taskbar() is True
        assert m._reseat_attempts == 5 and m._first_recovery_logged is True


def test_reseat_logs_info_once_per_episode(caplog):
    caplog.set_level(logging.DEBUG)
    m = _manager()
    clock = _Clock()
    with patch("netspeedtray.core.position_manager.win32gui") as win32, \
         patch("netspeedtray.core.position_manager._MonitorFromWindow", return_value=MON_A), \
         patch("netspeedtray.core.position_manager.time", clock):
        win32.IsWindow.return_value = True
        m.reseat_topmost()
        clock.t += 60.0
        m.reseat_topmost()
        clock.t += 60.0
        m.reseat_topmost()
        recovery_logs = [r.levelno for r in caplog.records
                         if "re-seated" in r.getMessage() or "recovered" in r.getMessage()]
        assert recovery_logs == [logging.INFO, logging.DEBUG, logging.DEBUG]


def test_style_restore_defers_for_open_menu():
    """The WS_EX_TOPMOST restore path rides the same menu guard."""
    m = _manager()
    with patch("netspeedtray.core.position_manager.win32gui") as win32, \
         patch("netspeedtray.core.position_manager._GetWindowLongPtr", return_value=TB_HWND), \
         patch("netspeedtray.core.position_manager._SetWindowLongPtr"), \
         patch("netspeedtray.core.position_manager._MonitorFromWindow", side_effect=[MON_A, MON_A]):
        win32.IsWindow.return_value = True
        win32.GetWindowLong.return_value = 0          # WS_EX_TOPMOST gone
        win32.IsWindowVisible.side_effect = lambda h: h == MENU_HWND
        win32.GetClassName.side_effect = lambda h: {MENU_HWND: "#32768"}.get(h, "Other")
        win32.EnumWindows.side_effect = lambda cb, ctx: cb(MENU_HWND, ctx)
        m.ensure_topmost()
        win32.SetWindowPos.assert_not_called()
