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


def test_lost_topmost_is_burial_and_the_safe_seat_restores_it():
    """Owner intact, WS_EX_TOPMOST lost: the widget is below the topmost taskbar, so the guarded
    recovery path sees burial and the seat above the taskbar makes it topmost again."""
    with _env([OTHER_TOP, MENU, TB, APP, W], topmost=(TB, MENU, OTHER_TOP)) as (m, z, *_):
        assert m.is_buried_behind_taskbar() is True
        assert m.reseat_above_taskbar() is True
        assert z.set_calls[0][:2] == (W, MENU)
        assert W in z.topmost
        assert z.above(MENU, W) and z.above(W, TB)


def test_ensure_topmost_never_reseats_a_lost_topmost_widget_itself(caplog):
    """S1/S2 (#327 review): ensure_topmost() runs at 1 Hz and on taskbar focus; it must neither
    re-seat (that bypassed the menu/drag/fullscreen guards) nor log on every call while the
    re-seat is held back. Recovery belongs to the widget's guarded _recover_if_buried."""
    with _env([OTHER_TOP, MENU, TB, APP, W], topmost=(TB, MENU, OTHER_TOP)) as (m, z, *_):
        with caplog.at_level(logging.DEBUG, logger="NetSpeedTray.Core.PositionManager"):
            for _ in range(5):
                m.ensure_topmost()
        assert z.set_calls == []
        assert not [r for r in caplog.records if "WS_EX_TOPMOST" in r.message]


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
    # Fight-window rule: the re-burial must be FOUND within BURIAL_FIGHT_WINDOW_MS of our
    # re-seat to be charged (it used to be found 0.5 s / 1.5 s later here, which is now a fresh
    # burial). The later checks of the same burial stay inside the backoff window.
    with _env([TB, W]) as (m, z, _w, clock):
        assert m.reseat_above_taskbar() is True        # first: immediate
        _bury(z)
        clock.t += 0.1
        assert m.reseat_above_taskbar() is False       # a fight: inside the 1 s window
        clock.t += 0.4
        assert m.reseat_above_taskbar() is False       # same burial, still inside
        clock.t += 0.6
        assert m.reseat_above_taskbar() is True        # 1.1 s later
        _bury(z)
        clock.t += 0.1
        assert m.reseat_above_taskbar() is False       # 2 s window now
        clock.t += 1.4
        assert m.reseat_above_taskbar() is False
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
        for _ in range(int(pm_mod._RESEAT_HEALTHY_S) + 1):   # observed once a second
            clock.t += 1
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


# --- S3 (#327 review): unobserved / hidden time never counts as "held on top" -----------


def test_hidden_check_breaks_the_on_top_stretch():
    with _env([TB, W]) as (m, z, _w, clock):
        m.reseat_above_taskbar()
        clock.t += 1
        m.is_buried_behind_taskbar()
        assert m._on_top_since is not None
        m._state.widget.isVisible.return_value = False
        m.is_buried_behind_taskbar()
        assert m._on_top_since is None


def test_hide_show_mid_fight_does_not_retire_the_backoff():
    """Hidden for longer than the healthy stretch (a fullscreen video, say) with no checks
    running: the first on-top tick after it must start a NEW stretch, not retire the backoff."""
    with _env([TB, W]) as (m, z, _w, clock):
        m.reseat_above_taskbar()
        m._reseat_attempts = 4
        clock.t += 1
        m.is_buried_behind_taskbar()                    # stretch starts
        clock.t += pm_mod._RESEAT_HEALTHY_S + 30        # unobserved (hidden, no ticks)
        m.is_buried_behind_taskbar()
        assert m._reseat_attempts == 4                  # not retired by unobserved time


def test_note_on_top_unverified_clears_the_stretch():
    m = _manager()
    m._on_top_since = 5.0
    m.note_on_top_unverified()
    assert m._on_top_since is None


# --- the insert-after window vanishing mid-call ---------------------------------------------


def test_insert_after_destroyed_mid_call_is_debug_and_free(caplog):
    with _env([MENU, TIP, TB, W]) as (m, z, win32, _c):
        def vanish(*_a):
            z.order.remove(TIP)
            raise RuntimeError("invalid window handle")
        win32.SetWindowPos.side_effect = vanish
        with caplog.at_level(logging.DEBUG, logger="NetSpeedTray.Core.PositionManager"):
            assert m.reseat_above_taskbar() is False
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert m._reseat_attempts == 0 and not m._last_reseat_ts   # no backoff charged
        win32.SetWindowPos.side_effect = z.SetWindowPos
        assert m.reseat_above_taskbar() is True                    # retry hits the new neighbour
        assert z.set_calls[-1][:2] == (W, MENU)


# --- B1 (PR #327 live test): Start / Task View lifts the taskbar - and the widget - into a ---
# higher Z-band. The walk from the taskbar then misses the widget although nothing is wrong.


def test_raised_taskbar_band_is_not_reported_as_burial():
    """Task View open: taskbar AND widget in band 6, the walk cannot find the widget above."""
    with _env([TB, W], bands={TB: 6, W: 6}) as (m, *_):
        assert m.is_buried_behind_taskbar() is False


def test_raised_taskbar_band_never_reseats_even_when_bands_match():
    """The old guard only compared the two bands - always equal here, since the widget is owned."""
    with _env([TB, W], bands={TB: 6, W: 6}) as (m, z, *_):
        assert m.reseat_above_taskbar() is False
        assert z.set_calls == []
        assert m._reseat_attempts == 0 and not m._last_reseat_ts


def test_raised_taskbar_band_breaks_the_on_top_stretch():
    with _env([W, TB], bands={TB: 1, W: 1}) as (m, z, _w, clock):
        m.is_buried_behind_taskbar()
        assert m._on_top_since is not None
    with _env([W, TB], bands={TB: 6, W: 6}, clock=clock) as (m2, *_):
        m2._on_top_since = m._on_top_since
        m2.is_buried_behind_taskbar()
        assert m2._on_top_since is None


def test_task_view_repro_does_not_escalate_backoff_and_real_burial_recovers_at_once():
    """Live repro: Win+Tab for 20 s (checks every event + 1 s tick), then Esc. Before the fix
    every check reported burial, re-seated and doubled the backoff, so the real burial left
    behind after Esc waited out an escalated window (11-12 s on the owner's PC)."""
    bands = {TB: 6, W: 6}
    with _env([TB, W], bands=bands) as (m, z, _w, clock):
        for _ in range(40):                      # 20 s of checks at 2 Hz
            clock.t += 0.5
            if m.is_buried_behind_taskbar():
                m.reseat_above_taskbar()
            z.order = [TB, W]                    # the walk keeps missing the widget
        assert z.set_calls == []
        assert m._reseat_attempts == 0
        bands[TB] = bands[W] = 1                 # Esc: back to the desktop band, widget buried
        clock.t += 0.1
        assert m.is_buried_behind_taskbar() is True
        assert m.reseat_above_taskbar() is True  # immediate - no backoff was charged
        assert z.above(W, TB)


def test_deferral_for_raised_band_logs_once(caplog):
    with _env([TB, W], bands={TB: 6, W: 6}) as (m, _z, _w, clock):
        with caplog.at_level(logging.DEBUG, logger=pm_mod.logger.name):
            for _ in range(10):
                clock.t += 0.5
                m.is_buried_behind_taskbar()
                m.reseat_above_taskbar()
    lines = [r for r in caplog.records if "Z-band 6" in r.getMessage()]
    assert len(lines) == 1


# --- M1: quick repeated Task View. Every close is a REAL burial (the taskbar drops back to ---
# band 1 with the widget under it); those re-seats are the shell's doing, not a fight.


def _task_view_cycle(m, z, clock, bands, open_s=1.0, gap_s=2.0):
    bands[TB] = bands[W] = 6                     # Win+Tab
    for _ in range(int(open_s / 0.5)):
        clock.t += 0.5
        m.is_buried_behind_taskbar()
    bands[TB] = bands[W] = 1                     # Esc: widget left under the taskbar
    _bury(z)
    clock.t += 0.28
    if m.is_buried_behind_taskbar():
        m.reseat_above_taskbar()
    for _ in range(int(gap_s)):
        clock.t += 1
        m.is_buried_behind_taskbar()


def test_quick_repeated_task_view_does_not_escalate_backoff():
    """Live: 8 x (Win+Tab 1 s, Esc) ~3 s apart reached attempt 6 / a 16 s window, and the
    widget stayed buried 11.8 s after the last Esc."""
    bands = {TB: 1, W: 1}
    with _env([W, TB], bands=bands) as (m, z, _w, clock):
        for _ in range(8):
            _task_view_cycle(m, z, clock, bands)
            assert z.above(W, TB)                # every close recovered at once
        assert m._reseat_attempts == 0 and not m._last_reseat_ts


def test_band_return_reseat_is_free_even_mid_backoff():
    bands = {TB: 1, W: 1}
    with _env([TB, W], bands=bands) as (m, z, _w, clock):
        m.reseat_above_taskbar()
        m._reseat_attempts = 5                   # a fight escalated to a 16 s window
        _task_view_cycle(m, z, clock, bands)
        assert z.above(W, TB)
        assert m._reseat_attempts == 5           # not charged


def test_real_fight_without_band_change_still_escalates():
    bands = {TB: 1, W: 1}
    with _env([TB, W], bands=bands) as (m, z, _w, clock):
        assert m.reseat_above_taskbar() is True
        _bury(z)
        clock.t += 0.1                            # within the fight window (was 0.3 s: now fresh)
        assert m.is_buried_behind_taskbar() is True
        assert m.reseat_above_taskbar() is False  # inside the 1 s window


def test_band_return_grace_expires():
    bands = {TB: 6, W: 6}
    with _env([TB, W], bands=bands) as (m, z, _w, clock):
        m._last_reseat_ts, m._reseat_attempts = clock.t, 5
        m.is_buried_behind_taskbar()             # raised seen
        bands[TB] = bands[W] = 1
        clock.t += pm_mod._BAND_RETURN_GRACE_S + 1
        assert m.is_buried_behind_taskbar() is True
        assert m.reseat_above_taskbar() is False  # an old band return buys nothing


# --- L1: only bands ABOVE 1 are "raised"; a taskbar that stays raised falls back ---------


def test_band_zero_taskbar_is_still_checked_and_reseated():
    with _env([TB, W], bands={TB: 0, W: 0}) as (m, z, *_):
        assert m.is_buried_behind_taskbar() is True
        assert m.reseat_above_taskbar() is True
        assert z.above(W, TB)


def test_taskbar_that_stays_raised_falls_back_to_checking(caplog):
    with _env([TB, W], bands={TB: 6, W: 6}) as (m, z, _w, clock):
        with caplog.at_level(logging.DEBUG, logger=pm_mod.logger.name):
            for _ in range(int(pm_mod._BAND_RAISED_FALLBACK_S)):
                clock.t += 1
                assert m.is_buried_behind_taskbar() is False
            clock.t += 2
            assert m.is_buried_behind_taskbar() is True   # verifiable again
            clock.t += 1
            m.is_buried_behind_taskbar()
    infos = [r for r in caplog.records if r.levelno == logging.INFO and "Z-band 6" in r.getMessage()]
    assert len(infos) == 1


def test_raised_stretch_with_a_gap_restarts_the_fallback_clock():
    with _env([TB, W], bands={TB: 6, W: 6}) as (m, z, _w, clock):
        for _ in range(40):
            clock.t += 1
            m.is_buried_behind_taskbar()
        clock.t += pm_mod._ON_TOP_MAX_GAP_S + 1  # unobserved (hidden): not continuous
        for _ in range(30):
            clock.t += 1
            assert m.is_buried_behind_taskbar() is False


# --- The fight window (pasharm, PR #327 rc.1 report): only a burial that lands within -------
# BURIAL_FIGHT_WINDOW_MS of OUR re-seat is the shell fighting back. On Win10 every maximize AND
# restore buries the widget; charging those grew the backoff 1 -> 2 -> 4 s and the widget
# blinked out for 0.6-2.8 s. His shortest re-seat -> user-burial gap was ~420 ms.

_FIGHT_S = pm_mod.constants.timeouts.BURIAL_FIGHT_WINDOW_MS / 1000.0


def test_fight_window_constant_sits_between_the_coalescer_and_a_user_action():
    """It must outlast the Z-order coalescer (a 1-frame re-burial is checked at its trailing
    edge, one coalescer interval after the shell's reorder) plus the rc.1 0.2 s live probe, and
    stay under pasharm's shortest user gap: ~420 ms on rc.1, 0.30 s with the 16 ms coalescer
    while toggling deliberately fast (PR #424)."""
    coalescer_s = pm_mod.constants.timeouts.ZORDER_RECHECK_MIN_INTERVAL_MS / 1000.0
    assert coalescer_s <= 0.017                     # one frame at 60 Hz (PR #424)
    assert 0.033 + coalescer_s + 0.05 < _FIGHT_S    # a 2-frame fight, checked late, is still a fight
    assert 0.2 < _FIGHT_S < 0.30


def test_pasharm_timeline_user_burials_after_the_fight_window_are_immediate_and_free():
    """Re-seat, then the user buries it again at +420 ms, +650 ms and +2.5 s: each one is a fresh
    burial - re-seated on its first check, no attempt charged, no backoff window waited out."""
    with _env([TB, W]) as (m, z, _w, clock):
        _bury(z)
        assert m.reseat_above_taskbar() is True          # the first burial ever: attempt 1
        assert m._reseat_attempts == 1
        for gap in (0.42, 0.65, 2.5):
            clock.t += gap
            _bury(z)
            assert m.is_buried_behind_taskbar() is True
            assert m.reseat_above_taskbar() is True, gap  # immediate
            assert z.above(W, TB)
            assert m._reseat_attempts == 1, gap          # not charged


def test_pasharm_maximize_restore_series_never_leaves_the_widget_buried():
    """His debug log: re-seats ~0.5-2.5 s apart. Old rule: deferred 0.6 s, 2.5 s, 2.5 s.
    New rule: every one recovers on the check that found it."""
    with _env([TB, W]) as (m, z, _w, clock):
        _bury(z)
        m.reseat_above_taskbar()
        for gap in (0.654, 0.616, 0.461, 2.548, 1.985, 2.503):
            clock.t += gap
            _bury(z)
            assert m.is_buried_behind_taskbar() is True
            assert m.reseat_above_taskbar() is True, gap


def _run_fight(m, z, clock, rebury_after_s, check_every_s, duration_s):
    """A shell that re-buries the widget `rebury_after_s` after every re-seat of ours; our
    checks run every `check_every_s` (Z-order events / the 1 s tick). Returns re-seat times."""
    reseats, pending_bury_at = [], clock.t
    end = clock.t + duration_s
    while clock.t < end:
        if pending_bury_at is not None and clock.t >= pending_bury_at - 1e-9:
            _bury(z)
            pending_bury_at = None
        if m.is_buried_behind_taskbar() and m.reseat_above_taskbar():
            reseats.append(clock.t)
            pending_bury_at = clock.t + rebury_after_s
        clock.t = round(clock.t + check_every_s, 6)
    return reseats


def _gaps(start, reseats):
    return [round(b - a, 1) for a, b in zip([start] + reseats, reseats)]


def test_one_frame_fight_still_escalates_1_2_4_8_16():
    """A real fight re-buries within a frame; our check sees it at the coalescer's trailing edge."""
    with _env([TB, W]) as (m, z, _w, clock):
        start = clock.t
        _bury(z)
        reseats = _run_fight(m, z, clock, rebury_after_s=0.016, check_every_s=0.1, duration_s=40)
        assert _gaps(start, reseats)[:6] == [0.0, 1.0, 2.0, 4.0, 8.0, 16.0], _gaps(start, reseats)


def test_one_frame_fight_checked_every_frame_still_escalates():
    """PR #424 follow-up: with the 16 ms coalescer the shell's 1-frame re-burial is checked a
    frame later, not ~100 ms later - still deep inside the fight window, so it still escalates
    and the backoff still bounds it."""
    frame = pm_mod.constants.timeouts.ZORDER_RECHECK_MIN_INTERVAL_MS / 1000.0
    with _env([TB, W]) as (m, z, _w, clock):
        start = clock.t
        _bury(z)
        reseats = _run_fight(m, z, clock, rebury_after_s=0.016, check_every_s=frame, duration_s=40)
        assert _gaps(start, reseats)[:6] == [0.0, 1.0, 2.0, 4.0, 8.0, 16.0], _gaps(start, reseats)


def test_slow_periodic_reburier_checked_every_frame_still_decays():
    """The rate cap does not depend on how often we check: a 260-400 ms re-burier seen by
    frame-rate checks is capped and decays exactly as with 50 ms checks."""
    frame = pm_mod.constants.timeouts.ZORDER_RECHECK_MIN_INTERVAL_MS / 1000.0
    for rebury in (0.26, 0.3, 0.4):
        with _env([TB, W]) as (m, z, _w, clock):
            _bury(z)
            reseats = _run_fight(m, z, clock, rebury_after_s=rebury, check_every_s=frame,
                                 duration_s=120)
            assert len(reseats) <= 25, (rebury, len(reseats))
            assert reseats[-1] - reseats[-2] >= 16 - 0.1, (rebury, _gaps(reseats[0], reseats))


def test_rc1_buryloop_probe_still_counts_as_a_fight():
    """The rc.1 live probe re-buries on its next 0.2 s poll: ~200 ms after our re-seat. That is
    inside the window, so the owner's Win11 live result (1/2/4/8/16 s) still holds."""
    with _env([TB, W]) as (m, z, _w, clock):
        start = clock.t
        _bury(z)
        reseats = _run_fight(m, z, clock, rebury_after_s=0.2, check_every_s=0.2, duration_s=40)
        assert _gaps(start, reseats)[:6] == [0.0, 1.0, 2.0, 4.0, 8.0, 16.0], _gaps(start, reseats)


def test_every_50ms_fight_is_bounded():
    with _env([TB, W]) as (m, z, _w, clock):
        _bury(z)
        reseats = _run_fight(m, z, clock, rebury_after_s=0.05, check_every_s=0.05, duration_s=300)
        # 0, 1, 3, 7, 15, 31, 63, 123, 183, 243: decays to one re-seat a minute.
        assert len(reseats) <= 10, len(reseats)
        assert reseats[-1] - reseats[-2] >= pm_mod._RESEAT_BACKOFF_MAX_S - 0.1


def test_burial_that_persists_through_a_backoff_window_stays_a_fight():
    """While we wait out the backoff we are not re-seating, so every later check of the SAME
    burial is far from our last re-seat - it must not be mistaken for a fresh one."""
    with _env([TB, W]) as (m, z, _w, clock):
        _bury(z)
        assert m.reseat_above_taskbar() is True
        clock.t += 0.1
        _bury(z)                                         # the shell fights back at once
        assert m.reseat_above_taskbar() is False         # charged: 1 s window
        for _ in range(8):                               # same burial, checked at 0.2..0.9 s
            clock.t += 0.1
            assert m.is_buried_behind_taskbar() is True
            assert m.reseat_above_taskbar() is False
        clock.t += 0.15
        assert m.reseat_above_taskbar() is True          # 1 s after the last re-seat
        assert m._reseat_attempts == 2


def test_fresh_burial_after_a_fight_went_quiet_is_not_blocked_by_its_backoff():
    """A fight escalated to an 8 s window, then the widget came back on top (the shell let go).
    A burial seconds later is a new one: immediate, not held for the rest of that window."""
    with _env([TB, W]) as (m, z, _w, clock):
        _bury(z)
        m.reseat_above_taskbar()
        m._reseat_attempts = 4                           # an 8 s window
        clock.t += 0.1
        _bury(z)
        assert m.reseat_above_taskbar() is False         # the fight: deferred
        z.order = [W, TB]                                # the shell put it back itself
        clock.t += 0.9
        assert m.is_buried_behind_taskbar() is False     # verified on top: that burial is over
        clock.t += 1.0
        _bury(z)                                         # the user maximizes a window
        assert m.is_buried_behind_taskbar() is True
        assert m.reseat_above_taskbar() is True          # 2 s into the 8 s window, still immediate
        assert m._reseat_attempts == 4


def test_fight_right_after_a_fresh_reseat_is_still_charged():
    with _env([TB, W]) as (m, z, _w, clock):
        _bury(z)
        m.reseat_above_taskbar()
        clock.t += 5
        _bury(z)
        assert m.reseat_above_taskbar() is True          # fresh, free
        assert m._reseat_attempts == 1
        clock.t += 0.1
        _bury(z)                                         # ... and the shell fights it at once
        assert m.reseat_above_taskbar() is True          # 5 s since the last CHARGED one: allowed
        assert m._reseat_attempts == 2
        clock.t += 0.1
        _bury(z)
        assert m.reseat_above_taskbar() is False         # now inside the 2 s window


def test_slow_periodic_reburier_outside_the_fight_window_still_decays():
    """#424 review: something that re-buries 260-400 ms after every re-seat is never inside the
    fight window - 300-400 free re-seats in 120 s, ~3/s forever. The fresh-burial rate cap turns
    it into a fight, which decays like any other."""
    for rebury in (0.26, 0.3, 0.4):
        with _env([TB, W]) as (m, z, _w, clock):
            _bury(z)
            reseats = _run_fight(m, z, clock, rebury_after_s=rebury, check_every_s=0.05,
                                 duration_s=120)
            assert len(reseats) <= 25, (rebury, len(reseats))
            assert reseats[-1] - reseats[-2] >= 16 - 0.1, (rebury, _gaps(reseats[0], reseats))


def test_one_frame_fight_seen_only_by_the_1s_tick_still_decays():
    """No Z-order hook (it failed to attach, or our own process did the burying): every check is
    ~1 s after our re-seat, so every burial looked fresh - 120 re-seats in 120 s, forever."""
    with _env([TB, W]) as (m, z, _w, clock):
        _bury(z)
        reseats = _run_fight(m, z, clock, rebury_after_s=0.016, check_every_s=1.0, duration_s=120)
        assert len(reseats) <= 25, len(reseats)
        assert reseats[-1] - reseats[-2] >= 16 - 0.1, _gaps(reseats[0], reseats)


def test_rate_capped_fight_stays_capped_until_the_widget_holds():
    """Once capped, the next burials are charged until the widget stays on top for the healthy
    stretch - then a user's burial is free again."""
    with _env([TB, W]) as (m, z, _w, clock):
        _bury(z)
        _run_fight(m, z, clock, rebury_after_s=0.3, check_every_s=0.05, duration_s=30)
        assert m._fresh_rate_capped
        z.order = [W, TB]                                # the re-burier stopped
        for _ in range(int(pm_mod._RESEAT_HEALTHY_S) + 2):
            clock.t += 1
            assert m.is_buried_behind_taskbar() is False
        assert not m._fresh_rate_capped
        _bury(z)
        assert m.reseat_above_taskbar() is True
        clock.t += 0.5
        _bury(z)
        assert m.reseat_above_taskbar() is True          # fresh and free again
        assert m._reseat_attempts == 1


def test_pasharm_pace_never_hits_the_rate_cap():
    """His rc.1 runs: up to 10 burials in 90 s, bursts like 4 in 1.7 s (the debug log). A user
    toggling maximize/restore every ~1.4 s for a whole cap window is still free."""
    with _env([TB, W]) as (m, z, _w, clock):
        _bury(z)
        m.reseat_above_taskbar()
        gaps = [0.654, 0.616, 0.461] + [9.0] * 9 + [1.4] * 10
        for gap in gaps:
            clock.t += gap
            _bury(z)
            assert m.is_buried_behind_taskbar() is True
            assert m.reseat_above_taskbar() is True, gap
            assert m._reseat_attempts == 1
        assert not m._fresh_rate_capped


def test_first_burial_ever_still_recovers_at_once():
    """Win11 forced burial (rc.1 live: back in 2 ms) - nothing to compare against, immediate."""
    with _env([TB, W]) as (m, z, *_):
        _bury(z)
        assert m.reseat_above_taskbar() is True
        assert m._reseat_attempts == 1


# --- Access denied (pasharm, Win10): SetWindowPos refused the insert-after window 4x in 1.6 s --
# Hypothesis: the window above the taskbar (a hidden explorer tooltip) sits in another Z-band.

class _AccessDenied(Exception):
    def __init__(self):
        super().__init__(5, "SetWindowPos", "Access is denied.")
        self.winerror = 5


TIP16 = 0x2002      # a hidden explorer tooltip in band 16, directly above the taskbar


def _deny_after(z, win32, *refused):
    def setpos(hwnd, after, *rest):
        if after in refused:
            z.set_calls.append((hwnd, after, "DENIED"))
            raise _AccessDenied()
        return z.SetWindowPos(hwnd, after, *rest)
    win32.SetWindowPos.side_effect = setpos


def test_access_denied_falls_back_to_the_first_window_in_the_taskbars_band(caplog):
    """MENU (band 1) above a band-16 tooltip above the taskbar: inserting after the tooltip is
    refused, so the widget goes in after MENU - still under the menu (#200), above the taskbar."""
    bands = {MENU: 1, TIP16: 16, TB: 1, W: 1}
    with _env([MENU, TIP16, TB, W], bands=bands) as (m, z, win32, _c):
        z.CLASSES = {**z.CLASSES, TIP16: "tooltips_class32"}
        _deny_after(z, win32, TIP16)
        with caplog.at_level(logging.DEBUG, logger=pm_mod.logger.name):
            assert m.reseat_above_taskbar() is True
        assert z.above(MENU, W) and z.above(W, TB)
        assert z.set_calls[-1][:2] == (W, MENU)
        infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO
                 and "denied" in r.getMessage().lower()]
        assert len(infos) == 1 and "tooltips_class32" in infos[0] and "16" in infos[0], infos
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_access_denied_with_no_same_band_window_above_uses_topmost():
    bands = {TIP16: 16, TB: 1, W: 1}
    with _env([TIP16, TB, W], bands=bands) as (m, z, win32, _c):
        _deny_after(z, win32, TIP16)
        assert m.reseat_above_taskbar() is True
        assert z.set_calls[-1][:2] == (W, win32con.HWND_TOPMOST)
        assert z.above(W, TB)


def test_access_denied_without_band_info_does_not_guess_and_logs_once(caplog):
    """No band API: the fallback cannot tell which neighbour is safe, so it leaves the order
    alone (no charge), logs the neighbour once for the episode and lets the next check retry."""
    with _env([MENU, TIP, TB, W]) as (m, z, win32, clock):
        _deny_after(z, win32, TIP)
        with caplog.at_level(logging.DEBUG, logger=pm_mod.logger.name):
            for _ in range(4):
                clock.t += 0.4
                assert m.reseat_above_taskbar() is False
        assert m._reseat_attempts == 0 and not m._last_reseat_ts
        infos = [r for r in caplog.records if r.levelno == logging.INFO
                 and "denied" in r.getMessage().lower()]
        assert len(infos) == 1 and "tooltips_class32" in infos[0].getMessage()
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
        win32.SetWindowPos.side_effect = z.SetWindowPos   # the neighbour went away
        assert m.reseat_above_taskbar() is True


def test_access_denied_fallback_also_refused_is_not_charged():
    bands = {MENU: 1, TIP16: 16, TB: 1, W: 1}
    with _env([MENU, TIP16, TB, W], bands=bands) as (m, z, win32, _c):
        _deny_after(z, win32, TIP16, MENU)
        assert m.reseat_above_taskbar() is False
        assert m._reseat_attempts == 0 and not m._last_reseat_ts
        assert z.above(TB, W)


def test_other_setwindowpos_errors_still_log_an_error(caplog):
    with _env([MENU, TB, W]) as (m, z, win32, _c):
        win32.SetWindowPos.side_effect = RuntimeError("boom")
        with caplog.at_level(logging.DEBUG, logger=pm_mod.logger.name):
            assert m.reseat_above_taskbar() is False
        assert [r for r in caplog.records if r.levelno == logging.ERROR]


# --- rc.2 W8: the one INFO line is kept for a REAL burial -----------------------------------------


def _recovery_infos(caplog):
    return [r for r in caplog.records if r.levelno == logging.INFO and "recovered" in r.getMessage()]


def _set_tray(m, tray):
    """A docked bottom taskbar whose tray rect reads ``tray``; runs the ~1 s refresh's tray check."""
    info = MagicMock()
    info.hwnd = TB
    info.rect = (0, 1032, 1920, 1080)
    info.get_tray_rect.return_value = tray
    info.get_screen.return_value = None
    m._state.taskbar_info = info
    assert m._tray_rect_ready() is True


def test_burial_right_after_docking_does_not_use_up_the_info_line(caplog):
    """Startup: the widget docks (owner set), the shell buries it once while it lays the taskbar out.
    That recovery is DEBUG; the first real burial later still gets the INFO line."""
    with _env([TB, W], owner=0) as (m, z, _w, clock):
        with caplog.at_level(logging.DEBUG, logger=pm_mod.logger.name):
            m.ensure_topmost()                       # first dock: owner set -> settle window
            _bury(z)
            clock.t += 1
            assert m.reseat_above_taskbar() is True
            assert _recovery_infos(caplog) == []
            clock.t += 60                            # much later: a real burial
            _bury(z)
            assert m.reseat_above_taskbar() is True
        assert len(_recovery_infos(caplog)) == 1


def test_owner_reset_to_the_same_taskbar_is_not_a_new_dock(caplog):
    """#440 review L4: Qt drops the owner across show / flag changes (after a fullscreen app) and it is
    re-set to the SAME taskbar. That is no layout change: a burial right after it is real and logs
    INFO. Only a dock to a different taskbar HWND starts the settle window."""
    with _env([TB, W], owner=0) as (m, z, _w, clock):
        with caplog.at_level(logging.DEBUG, logger=pm_mod.logger.name):
            m.ensure_topmost()                       # first dock to TB: settle
            clock.t += 60
            m.ensure_topmost()                       # owner re-set to TB again (fullscreen exit)
            _bury(z)
            clock.t += 1
            assert m.reseat_above_taskbar() is True
        assert len(_recovery_infos(caplog)) == 1


def test_tray_icon_arrival_hands_the_info_line_back(caplog):
    """A tray icon arrives: the widget is buried first, the tray rect changes on the next refresh.
    The INFO the arrival used is handed back, so the real burial after it logs at INFO too."""
    with _env([TB, W]) as (m, z, _w, clock):
        with caplog.at_level(logging.DEBUG, logger=pm_mod.logger.name):
            _set_tray(m, (1694, 1032, 1920, 1080))
            clock.t += 30
            _bury(z)
            assert m.reseat_above_taskbar() is True           # the arrival's burial: INFO
            clock.t += 0.8
            _set_tray(m, (1670, 1032, 1920, 1080))            # ...and its wider tray
            clock.t += 0.5
            _bury(z)
            assert m.reseat_above_taskbar() is True           # still settling: DEBUG
            clock.t += 120
            _bury(z)
            assert m.reseat_above_taskbar() is True           # the real one: INFO again
        assert len(_recovery_infos(caplog)) == 2


def test_unchanged_tray_never_reopens_the_info_line(caplog):
    """The settle window needs a real change: the 1 s refresh re-reading the same tray rect is not
    one, so a fight keeps its single INFO line (#327)."""
    with _env([TB, W]) as (m, z, _w, clock):
        with caplog.at_level(logging.DEBUG, logger=pm_mod.logger.name):
            _set_tray(m, (1694, 1032, 1920, 1080))
            clock.t += 30
            for _ in range(4):
                _bury(z)
                m.reseat_above_taskbar()
                clock.t += 1
                _set_tray(m, (1694, 1032, 1920, 1080))
                clock.t += 20
        assert len(_recovery_infos(caplog)) == 1
