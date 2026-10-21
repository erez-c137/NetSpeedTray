"""
Burial recovery (#314, #238, PR #327): is_buried_behind_taskbar / reseat_above_taskbar and the
WS_EX_TOPMOST restore in ensure_topmost().

Complements test_topmost_zorder.py, which locks in the #200 rule that the steady-state cadence
must NOT reorder Z. These cover the deliberate exception - the widget provably BELOW its taskbar
in Z-order - and lock in the insertion point that makes the exception safe: the widget is seated
directly above the taskbar (hWndInsertAfter = the window right above it), never at the top of the
topmost band, so anything already above the taskbar (menus, jump lists, tooltips, flyouts) stays
above the widget.

The Win32 Z-order is simulated by `_ZOrder`, a top-to-bottom list whose SetWindowPos really moves
the window, so the tests can assert what ends up above what.
"""
from contextlib import contextmanager
import logging
from unittest.mock import MagicMock, patch

import win32con

from netspeedtray.core import position_manager as pm_mod
from netspeedtray.core.position_manager import PositionManager, WindowState


TB = 0xABC          # the taskbar (Shell_TrayWnd)
TB2 = 0xABD         # a secondary taskbar (Shell_SecondaryTrayWnd)
W = 0x1000          # our widget
MENU = 0x2000       # an open taskbar context menu / jump list (above the taskbar)
TIP = 0x2001        # a hidden taskbar tooltip owned by the taskbar
APP = 0x3000        # an ordinary application window
OTHER_TOP = 0x4000  # some other topmost window

EXPECTED_FLAGS = (win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE
                  | 0x0200)  # SWP_NOOWNERZORDER


class _Clock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def monotonic(self):
        return self.t


class _ZOrder:
    """A fake top-level Z-order: index 0 is the top. SetWindowPos really reorders it."""

    CLASSES = {TB: "Shell_TrayWnd", TB2: "Shell_SecondaryTrayWnd", MENU: "#32768",
               TIP: "tooltips_class32", W: "Qt6QWindow", APP: "Notepad", OTHER_TOP: "Other"}

    def __init__(self, order, topmost=(TB, TB2, W, MENU, TIP, OTHER_TOP)):
        self.order = list(order)
        self.topmost = set(topmost)
        self.set_calls = []

    def GetWindow(self, hwnd, cmd):
        assert cmd == 3, "only GW_HWNDPREV is expected"
        i = self.order.index(hwnd)
        return self.order[i - 1] if i > 0 else 0

    def GetWindowLong(self, hwnd, index):
        assert index == win32con.GWL_EXSTYLE
        return win32con.WS_EX_TOPMOST if hwnd in self.topmost else 0

    def GetClassName(self, hwnd):
        return self.CLASSES.get(hwnd, "Other")

    def IsWindow(self, hwnd):
        return hwnd in self.order

    def SetWindowPos(self, hwnd, after, x, y, cx, cy, flags):
        self.set_calls.append((hwnd, after, flags))
        self.order.remove(hwnd)
        if after == win32con.HWND_TOPMOST:
            self.order.insert(0, hwnd)
        else:
            self.order.insert(self.order.index(after) + 1, hwnd)
        if after == win32con.HWND_TOPMOST or after in self.topmost:
            self.topmost.add(hwnd)   # placed above a topmost window -> topmost

    def above(self, a, b):
        return self.order.index(a) < self.order.index(b)


def _manager(visible=True, tb=TB):
    widget = MagicMock()
    widget.winId.return_value = W
    widget.isVisible.return_value = visible
    tb_info = MagicMock()
    tb_info.hwnd = tb
    return PositionManager(WindowState(config={}, widget=widget, taskbar_info=tb_info))


@contextmanager
def _env(order, owner=TB, visible=True, bands=None, topmost=None, clock=None):
    """Patch the win32 surface onto a _ZOrder. `bands` maps hwnd -> Z-band (None = API missing)."""
    z = _ZOrder(order) if topmost is None else _ZOrder(order, topmost)
    m = _manager(visible=visible)   # the detected taskbar is always TB
    win32 = MagicMock()
    for name in ("GetWindow", "GetWindowLong", "GetClassName", "IsWindow", "SetWindowPos"):
        setattr(win32, name, MagicMock(side_effect=getattr(z, name)))
    band = (lambda h: None) if bands is None else (lambda h: bands.get(h))
    clock = clock or _Clock()
    with patch.object(pm_mod, "win32gui", win32), \
         patch.object(pm_mod, "_GetWindowLongPtr", return_value=owner), \
         patch.object(pm_mod, "_SetWindowLongPtr"), \
         patch.object(pm_mod, "_window_band", side_effect=band), \
         patch.object(pm_mod.time, "monotonic", side_effect=clock.monotonic):
        yield m, z, win32, clock


# --- Detection: a pure Z-order comparison against the docked taskbar -------------------


def test_widget_above_taskbar_is_not_buried():
    with _env([MENU, W, TIP, TB, APP]) as (m, *_):
        assert m.is_buried_behind_taskbar() is False


def test_widget_below_taskbar_is_buried():
    with _env([MENU, TIP, TB, W, APP]) as (m, *_):
        assert m.is_buried_behind_taskbar() is True


def test_menu_covering_a_widget_that_is_above_the_taskbar_is_not_burial():
    """A menu above the widget is the #200 case: the widget is fine, nothing to do."""
    with _env([MENU, W, TB]) as (m, *_):
        assert m.is_buried_behind_taskbar() is False


def test_buried_widget_is_detected_even_while_a_menu_covers_it():
    """The old centre hit-test saw the menu and missed the burial; Z-order sees both."""
    with _env([MENU, TB, W]) as (m, *_):
        assert m.is_buried_behind_taskbar() is True


def test_hidden_widget_is_never_buried_and_walks_nothing():
    with _env([TB, W], visible=False) as (m, _z, win32, _c):
        assert m.is_buried_behind_taskbar() is False
        win32.GetWindow.assert_not_called()


def test_secondary_taskbar_owner_is_the_reference():
    """Docked to a secondary taskbar: compare against THAT taskbar, not the primary."""
    with _env([TB, W, TB2], owner=TB2) as (m, *_):
        assert m.is_buried_behind_taskbar() is False      # above its own taskbar
    with _env([TB2, W, TB], owner=TB2) as (m, *_):
        assert m.is_buried_behind_taskbar() is True       # below its own taskbar


def test_non_taskbar_owner_falls_back_to_detected_taskbar():
    with _env([TB, W], owner=APP) as (m, *_):
        assert m.is_buried_behind_taskbar() is True


def test_runaway_zorder_walk_is_not_treated_as_burial():
    """If the walk cannot finish it must not guess 'buried' (and so never re-seat on a guess)."""
    with _env([TB, W]) as (m, _z, win32, _c):
        win32.GetWindow.side_effect = lambda h, c: OTHER_TOP   # a cycle that never ends
        assert m.is_buried_behind_taskbar() is False


def test_detection_failure_is_swallowed():
    with _env([TB, W]) as (m, _z, win32, _c):
        win32.GetWindow.side_effect = RuntimeError("boom")
        assert m.is_buried_behind_taskbar() is False


# --- The re-seat: directly above the taskbar, never focus, never the owner cluster -----


def test_reseat_inserts_directly_above_the_taskbar_not_topmost():
    """pasharm's insertion point: hWndInsertAfter = GetWindow(taskbar, GW_HWNDPREV)."""
    with _env([OTHER_TOP, MENU, TIP, TB, W, APP]) as (m, z, win32, _c):
        assert m.reseat_above_taskbar() is True
        hwnd, after, flags = z.set_calls[0]
        assert (hwnd, after) == (W, TIP)
        assert after != win32con.HWND_TOPMOST


def test_reseat_flags_never_activate_move_size_or_drag_the_owner():
    with _env([MENU, TB, W]) as (m, z, *_):
        m.reseat_above_taskbar()
        flags = z.set_calls[0][2]
        assert flags == EXPECTED_FLAGS
        assert flags & win32con.SWP_NOACTIVATE
        assert flags & 0x0200          # SWP_NOOWNERZORDER: the taskbar is not dragged (#200)


def test_reseat_keeps_open_menus_and_flyouts_above_the_widget():
    """Everything above the taskbar before the re-seat is still above the widget after it."""
    with _env([OTHER_TOP, MENU, TIP, TB, APP, W]) as (m, z, *_):
        m.reseat_above_taskbar()
        assert z.above(W, TB)
        for w in (OTHER_TOP, MENU, TIP):
            assert z.above(w, W)
        assert m.is_buried_behind_taskbar() is False


def test_reseat_uses_hwnd_topmost_only_when_taskbar_is_first():
    with _env([TB, W, APP]) as (m, z, *_):
        m.reseat_above_taskbar()
        assert z.set_calls[0][1] == win32con.HWND_TOPMOST
        assert z.order[:2] == [W, TB]


def test_reseat_is_a_noop_when_already_directly_above():
    with _env([MENU, W, TB]) as (m, z, *_):
        assert m.reseat_above_taskbar() is False
        assert z.set_calls == []


def test_reseat_refuses_when_taskbar_is_not_topmost():
    """A fullscreen app took the taskbar out of the topmost band: never follow it there."""
    with _env([APP, TB, W], topmost=(W,)) as (m, z, *_):
        assert m.reseat_above_taskbar() is False
        assert z.set_calls == []


def test_reseat_refuses_when_taskbar_is_in_another_zband():
    """Win11 with Start open: the taskbar is in a higher band; don't fight it."""
    with _env([TB, W], bands={TB: 6, W: 1}) as (m, z, *_):
        assert m.reseat_above_taskbar() is False
        assert z.set_calls == []


def test_reseat_proceeds_when_bands_match_or_are_unknown():
    with _env([TB, W], bands={TB: 1, W: 1}) as (m, z, *_):
        assert m.reseat_above_taskbar() is True
    with _env([TB, W], bands={TB: 1}) as (m, z, *_):       # widget band unknown
        assert m.reseat_above_taskbar() is True


def test_reseat_without_a_taskbar_does_nothing():
    with _env([W, APP], owner=0) as (m, z, *_):
        assert m.reseat_above_taskbar() is False
        assert z.set_calls == []


def test_reseat_failure_is_logged_not_raised(caplog):
    with _env([TB, W]) as (m, _z, win32, _c):
        win32.SetWindowPos.side_effect = RuntimeError("access denied")
        with caplog.at_level(logging.ERROR, logger="NetSpeedTray.Core.PositionManager"):
            assert m.reseat_above_taskbar() is False


# --- ensure_topmost(): style restore goes through the safe seat --------------------------


def test_ensure_topmost_restores_lost_topmost_by_seating_above_taskbar():
    """Owner intact, WS_EX_TOPMOST lost -> seat directly above the taskbar (not top of band)."""
    with _env([OTHER_TOP, MENU, TB, APP, W], topmost=(TB, MENU, OTHER_TOP)) as (m, z, *_):
        m.ensure_topmost()
        assert z.set_calls and z.set_calls[0][:2] == (W, MENU)
        assert W in z.topmost
        assert z.above(MENU, W) and z.above(W, TB)


def test_ensure_topmost_leaves_intact_topmost_alone():
    """Steady state (owner + WS_EX_TOPMOST) -> no SetWindowPos at all (#200)."""
    with _env([TB, W]) as (m, z, *_):
        m.ensure_topmost()
        assert z.set_calls == []


# --- Backoff: the safety net against a shell that fights back ---------------------------


def _bury(z):
    z.order.remove(W)
    z.order.insert(z.order.index(TB) + 1, W)


def test_backoff_first_immediate_then_doubling():
    with _env([TB, W]) as (m, z, _w, clock):
        assert m.reseat_above_taskbar() is True        # first: immediate
        _bury(z)
        clock.t += 0.5
        assert m.reseat_above_taskbar() is False       # inside the 1 s window
        clock.t += 0.6
        assert m.reseat_above_taskbar() is True        # 1.1 s later
        _bury(z)
        clock.t += 1.5
        assert m.reseat_above_taskbar() is False       # 2 s window now
        clock.t += 0.6
        assert m.reseat_above_taskbar() is True


def test_backoff_saturates_at_cap():
    m = _manager()
    m._last_reseat_ts = 1.0
    m._reseat_attempts = pm_mod._RESEAT_BACKOFF_EXP_CAP + 1
    assert m._reseat_delay() == pm_mod._RESEAT_BACKOFF_MAX_S
    m._reseat_attempts = 50
    assert m._reseat_delay() == pm_mod._RESEAT_BACKOFF_MAX_S


def test_single_on_top_tick_does_not_retire_backoff():
    with _env([TB, W]) as (m, z, _w, clock):
        m.reseat_above_taskbar()
        clock.t += 0.2
        assert m.is_buried_behind_taskbar() is False   # on top - starts the stretch only
        assert m._reseat_attempts == 1 and m._last_reseat_ts


def test_sustained_on_top_stretch_retires_backoff():
    """A re-seat that held for _RESEAT_HEALTHY_S retires the escalation: the next burial
    (pasharm's maximize/restore repro) recovers at once again."""
    with _env([TB, W]) as (m, z, _w, clock):
        m.reseat_above_taskbar()
        m._reseat_attempts = 5                          # pretend it escalated
        clock.t += 1
        m.is_buried_behind_taskbar()
        clock.t += pm_mod._RESEAT_HEALTHY_S
        m.is_buried_behind_taskbar()
        assert m._reseat_attempts == 0 and not m._last_reseat_ts
        _bury(z)
        clock.t += 0.1
        assert m.is_buried_behind_taskbar() is True
        assert m.reseat_above_taskbar() is True         # immediate, no backoff left


def test_on_top_stretch_broken_by_burial_restarts():
    with _env([TB, W]) as (m, z, _w, clock):
        m.reseat_above_taskbar()
        clock.t += 1
        m.is_buried_behind_taskbar()                    # stretch starts
        clock.t += pm_mod._RESEAT_HEALTHY_S - 2
        _bury(z)
        assert m.is_buried_behind_taskbar() is True     # breaks the stretch
        z.order = [W, TB]
        clock.t += 3
        m.is_buried_behind_taskbar()                    # new stretch begins here
        assert m._reseat_attempts == 1                  # not retired


def test_reset_reseat_state_clears_everything():
    m = _manager()
    m._reseat_attempts, m._last_reseat_ts, m._first_recovery_logged = 4, 9.0, True
    m._on_top_since = 3.0
    m.reset_reseat_state()
    assert (m._reseat_attempts, m._last_reseat_ts, m._first_recovery_logged, m._on_top_since) == \
        (0, 0.0, False, None)


# --- Logging: INFO once per episode, deferrals once per window ---------------------------


def test_info_logged_once_per_episode(caplog):
    with _env([TB, W]) as (m, z, _w, clock):
        with caplog.at_level(logging.DEBUG, logger="NetSpeedTray.Core.PositionManager"):
            m.reseat_above_taskbar()
            _bury(z)
            clock.t += 5
            m.reseat_above_taskbar()
        infos = [r for r in caplog.records if r.levelno == logging.INFO and "recovered" in r.message]
        assert len(infos) == 1


def test_backoff_deferral_logs_once_per_window_not_per_tick(caplog):
    """234 'deferred: backoff' lines in 4 min on the owner's live run - now one per window."""
    with _env([TB, W]) as (m, z, _w, clock):
        m.reseat_above_taskbar()
        _bury(z)
        with caplog.at_level(logging.DEBUG, logger="NetSpeedTray.Core.PositionManager"):
            for _ in range(10):
                clock.t += 0.05
                m.reseat_above_taskbar()
        deferred = [r for r in caplog.records if "deferred: backoff" in r.message]
        assert len(deferred) == 1


def test_repeated_user_burials_each_recover_at_once():
    """pasharm's Win10 repro: every maximize/restore buries the widget (6 in 60 s). With 1 Hz
    safety-net ticks in between, each burial must recover on its first check - the backoff is
    for a shell that fights back, not for a user who keeps restoring windows."""
    with _env([TB, W]) as (m, z, _w, clock):
        for _ in range(6):
            _bury(z)
            assert m.is_buried_behind_taskbar() is True
            assert m.reseat_above_taskbar() is True
            for _tick in range(12):             # 12 s on top, checked once a second
                clock.t += 1
                assert m.is_buried_behind_taskbar() is False


# --- _window_band helper ------------------------------------------------------------------


def test_window_band_none_when_api_missing():
    with patch.object(pm_mod, "_GetWindowBandRaw", None):
        assert pm_mod._window_band(W) is None


def test_window_band_none_on_failure_or_zero_hwnd():
    with patch.object(pm_mod, "_GetWindowBandRaw", MagicMock(return_value=0)):
        assert pm_mod._window_band(W) is None
    assert pm_mod._window_band(0) is None
