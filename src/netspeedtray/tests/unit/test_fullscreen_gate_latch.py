"""
#503: the widget flashed over a fullscreen app (and blinked out on a plain desktop) every ~24 s.

The fullscreen hide was re-decided from ONE foreground reading on every refresh, and anything that was
not a covering window read as "not covered": a NULL foreground during an activation hand-off, the
stale hwnd a debounced foreground event carried (possibly closed by then), and an invisible or
DWM-cloaked monitor-sized window counted as a real fullscreen cover. These pin the latch:

- a foreground that is not on screen is no information - the decision holds;
- the refresh judges the LIVE foreground, not the stored hwnd;
- an invisible / cloaked monitor-sized window hides nothing;
- leaving fullscreen needs two agreeing readings FULLSCREEN_EXIT_CONFIRM_MS apart (the desktop in the
  foreground is a deliberate exit and shows at once); a closed fullscreen app with nothing taking the
  foreground still releases;
- that hold is bounded: a cover that still looks drawn but has left the foreground (a shell surface
  during Show desktop) cannot keep the widget hidden past FULLSCREEN_NO_FOREGROUND_HOLD_MS.
"""
import types
from unittest.mock import MagicMock, patch

import pytest

from netspeedtray import constants
from netspeedtray.core.fullscreen_gate import FullscreenLatch
from netspeedtray.views.widget import main as widget_main

TB_HWND = 0x1000
JAVA = 0x4F0C86        # the fullscreen SunAwtFrame
NORMAL = 0x2222        # an ordinary visible window
INVISIBLE_FS = 0x3333  # a monitor-sized window that is not drawn (invisible / cloaked)
DESKTOP = 0x4444       # WorkerW
DEAD = 0xDEAD          # a window already destroyed

CONFIRM_S = constants.timeouts.FULLSCREEN_EXIT_CONFIRM_MS / 1000.0
HOLD_S = constants.timeouts.FULLSCREEN_NO_FOREGROUND_HOLD_MS / 1000.0


# --- the widget's visibility gate ------------------------------------------------------------------

@pytest.fixture
def gate(monkeypatch):
    """Drive NetworkSpeedWidget._execute_refresh against a scripted world. ``world`` holds the live
    foreground and which windows are drawn; ``obstructs`` mimics the pre-#503 is_taskbar_obstructed
    (rect == monitor only, no visibility test), so a test can show the gate itself copes."""
    world = {"fg": 0, "on_screen": {JAVA, NORMAL, DESKTOP}, "covering": {JAVA, INVISIBLE_FS}}
    monkeypatch.setattr(widget_main, "get_taskbar_info",
                        lambda preferred_screen_name=None: types.SimpleNamespace(hwnd=TB_HWND))
    monkeypatch.setattr(widget_main, "is_taskbar_visible", lambda tb: True)
    monkeypatch.setattr(widget_main, "is_taskbar_obstructed", lambda tb, h: h in world["covering"])
    monkeypatch.setattr(widget_main, "is_window_on_screen", lambda h: h in world["on_screen"],
                        raising=False)
    monkeypatch.setattr(widget_main.win32gui, "GetForegroundWindow", lambda: world["fg"])
    monkeypatch.setattr(widget_main.win32gui, "GetClassName",
                        lambda h: "WorkerW" if h == DESKTOP else "SunAwtFrame")
    timer = MagicMock()
    monkeypatch.setattr(widget_main, "QTimer", timer)
    clock = {"t": 1000.0}   # the latch's monotonic clock, frozen: no test depends on wall time

    def make(visible):
        w = MagicMock()
        w._fs_latch = FullscreenLatch(clock=lambda: clock["t"])
        w.config = {"preferred_monitor": None, "free_move": True, "keep_visible_fullscreen": False}
        w._is_context_menu_visible = False
        w._dragging = False
        w._taskbar_lost_count = 0
        shown = {"v": visible}
        w.isVisible.side_effect = lambda: shown["v"]
        w.setVisible.side_effect = lambda v: shown.update(v=v)
        w.position_manager.refresh_float_state.return_value = None
        w.position_manager.is_yielding_to_apps.return_value = False
        w.position_manager.is_free_float_active.return_value = False
        w.shown = shown
        return w

    def refresh(w, fg, stored_hwnd=0):
        world["fg"] = fg
        widget_main.NetworkSpeedWidget._execute_refresh(w, stored_hwnd)

    def advance(seconds):
        clock["t"] += seconds

    return types.SimpleNamespace(world=world, make=make, refresh=refresh, timer=timer, advance=advance)


def _shows(w):
    return [c for c in w.setVisible.call_args_list if c.args == (True,)]


def _hides(w):
    return [c for c in w.setVisible.call_args_list if c.args == (False,)]


def test_null_foreground_between_fullscreen_readings_does_not_show(gate):
    """Java -> NULL -> Java: the NULL hand-off is no information, the widget stays hidden."""
    w = gate.make(visible=True)
    gate.refresh(w, JAVA)
    assert w.shown["v"] is False
    for fg in (0, JAVA, 0, 0, JAVA):
        gate.refresh(w, fg)
    assert _shows(w) == []


def test_refresh_judges_the_live_foreground_not_a_closed_stored_hwnd(gate):
    """The debounced event hands over the hwnd that was foreground 250 ms ago - already closed. The
    live foreground is still the fullscreen app, so nothing may show."""
    w = gate.make(visible=True)
    gate.refresh(w, JAVA)
    gate.refresh(w, JAVA, stored_hwnd=DEAD)
    gate.refresh(w, JAVA, stored_hwnd=NORMAL)   # stale but alive: still the live one that counts
    assert _shows(w) == []
    assert w.shown["v"] is False


def test_invisible_monitor_sized_foreground_does_not_hide_on_the_desktop(gate):
    """No fullscreen app: normal -> an invisible monitor-sized window -> normal never hides."""
    w = gate.make(visible=True)
    for fg in (NORMAL, INVISIBLE_FS, NORMAL, INVISIBLE_FS, 0, NORMAL):
        gate.refresh(w, fg)
    assert _hides(w) == []
    assert w.shown["v"] is True


def test_one_normal_reading_does_not_show_two_apart_do(gate):
    """Leaving fullscreen for a real window: the first reading only arms a re-check; the second,
    FULLSCREEN_EXIT_CONFIRM_MS later, shows the widget."""
    w = gate.make(visible=True)
    gate.refresh(w, JAVA)
    gate.refresh(w, NORMAL)
    assert _shows(w) == []                                 # a single blip cannot show it
    gate.timer.singleShot.assert_called_once()             # ...but the confirmation is scheduled
    assert gate.timer.singleShot.call_args.args[0] == constants.timeouts.FULLSCREEN_EXIT_CONFIRM_MS
    gate.advance(CONFIRM_S - 0.010)
    gate.refresh(w, NORMAL)                                 # too soon: still unconfirmed
    assert _shows(w) == []
    gate.advance(0.010)
    gate.advance(CONFIRM_S)
    gate.refresh(w, NORMAL)
    assert len(_shows(w)) == 1
    assert w.shown["v"] is True


def test_a_confirming_timer_a_hair_early_still_confirms(gate):
    """The precise re-check timer can fire a millisecond early against time.monotonic: that must not
    cost a second 250 ms round."""
    w = gate.make(visible=True)
    gate.refresh(w, JAVA)
    gate.refresh(w, NORMAL)
    gate.advance(CONFIRM_S - 0.001)
    gate.refresh(w, NORMAL)
    assert w.shown["v"] is True


def test_a_hold_on_no_information_asks_for_a_prompt_recheck(gate):
    """While the latch holds on a NULL foreground it schedules the re-check timer, so the hold releases
    when it runs out instead of at the next 1 s tick; a steady fullscreen app asks for nothing."""
    w = gate.make(visible=True)
    gate.refresh(w, JAVA)
    gate.refresh(w, JAVA)
    gate.timer.singleShot.assert_not_called()
    gate.refresh(w, 0)
    assert w._fs_latch.recheck_wanted is True
    gate.timer.singleShot.assert_called_once()


def test_forget_fullscreen_runs_even_while_yielding_to_app_buttons(gate):
    """The hold gives up while #24 hides the widget for app buttons: the poll must still forget the
    cover, or burial recovery and the hover card stay off after the yield ends."""
    w = gate.make(visible=True)
    gate.refresh(w, JAVA)
    w.position_manager.is_yielding_to_apps.return_value = True
    gate.refresh(w, 0)
    gate.advance(HOLD_S + 0.1)
    gate.refresh(w, 0)
    assert w._fs_latch.reason == "no-foreground-timeout"
    w.system_event_handler.forget_fullscreen.assert_called_once()


def test_a_fullscreen_reading_in_between_restarts_the_confirmation(gate):
    w = gate.make(visible=True)
    gate.refresh(w, JAVA)
    gate.refresh(w, NORMAL)
    gate.refresh(w, JAVA)
    gate.advance(CONFIRM_S)
    gate.refresh(w, NORMAL)      # a first reading again, not a confirmation
    assert _shows(w) == []


def test_desktop_in_the_foreground_shows_at_once(gate):
    """Win+D / Show desktop: a deliberate exit, no confirmation wait."""
    w = gate.make(visible=True)
    gate.refresh(w, JAVA)
    gate.refresh(w, DESKTOP)
    assert len(_shows(w)) == 1


def test_fullscreen_app_closing_with_a_null_foreground_still_shows(gate):
    """Nothing takes the foreground after the fullscreen app closes: the latch re-judges the window
    that covered, finds it gone, and releases after the usual confirmation."""
    w = gate.make(visible=True)
    gate.refresh(w, JAVA)
    gate.world["on_screen"].discard(JAVA)
    gate.world["covering"].discard(JAVA)
    gate.refresh(w, 0)
    assert _shows(w) == []
    gate.advance(CONFIRM_S)
    gate.refresh(w, 0)
    assert len(_shows(w)) == 1


def test_a_cover_that_left_the_foreground_cannot_hold_the_widget_hidden(gate):
    """Show desktop from the taskbar (owner's smoke test, 2026-11-10): a monitor-sized surface took the
    foreground, then the foreground went NULL with that surface still drawn and monitor-sized. Without
    a bound the latch kept re-judging it as covering and the widget stayed hidden until a desktop click.
    Short NULL hand-offs still hold (the #503 flash); a long one releases."""
    w = gate.make(visible=True)
    gate.refresh(w, JAVA)                 # the surface (any drawn monitor-sized window) covers
    assert w.shown["v"] is False
    for _ in range(4):                    # NULL for under the hold, ticking: still hidden
        gate.refresh(w, 0)
        gate.advance(HOLD_S / 5)
    assert _shows(w) == []
    gate.advance(HOLD_S)                  # NULL past the hold: the 1 s tick shows the widget
    gate.refresh(w, 0)
    assert w.shown["v"] is True
    assert w._fs_latch.reason == "no-foreground-timeout"
    # ...and it reaches the INFO file log (DEBUG never does), naming the window's class, not its title.
    info = [c.args for c in w.logger.info.call_args_list if "Fullscreen gate" in c.args[0]]
    assert len(info) == 1 and info[0][1].startswith("class=")
    # ...and the poll forgets the cover too, or burial recovery and the hover card would stand down
    # for a fullscreen app that is no longer there (#504 review M1).
    w.system_event_handler.forget_fullscreen.assert_called_once()


def test_a_gap_in_readings_restarts_the_hold(gate):
    """Readings far apart (the gate was not consulted: taskbar hidden, a menu open) must not count
    the gap toward the hold - one NULL before the gap and one after is not 1.5 s of NULL."""
    w = gate.make(visible=True)
    gate.refresh(w, JAVA)
    gate.refresh(w, 0)
    gate.advance(FullscreenLatch.NO_INFO_MAX_GAP_S + 1)
    gate.refresh(w, 0)                    # first reading after the gap: the hold starts here
    assert _shows(w) == []
    gate.advance(HOLD_S / 2)
    gate.refresh(w, 0)
    assert _shows(w) == []


def test_a_real_foreground_reading_restarts_the_hold(gate):
    """Java -> NULL (most of the hold) -> Java -> NULL (most of it again): never released - the
    hold counts one unbroken run of no-information readings, not their total."""
    w = gate.make(visible=True)
    gate.refresh(w, JAVA)
    for _ in range(3):
        gate.refresh(w, 0)
        gate.advance(HOLD_S * 0.9)
        gate.refresh(w, JAVA)
    assert _shows(w) == []


def test_immediate_hide_latches_so_a_null_reading_cannot_undo_it(gate):
    """The hook's immediate hide, then a refresh that only sees a NULL foreground: still hidden."""
    w = gate.make(visible=True)
    gate.world["fg"] = JAVA
    widget_main.NetworkSpeedWidget._on_immediate_hide_requested(w)
    assert w.shown["v"] is False
    gate.refresh(w, 0)
    assert _shows(w) == []


def test_fullscreen_visibility_changes_are_logged_and_nothing_else(gate):
    """A DEBUG line for each fullscreen hide / show (reason, class, pid - never a title); an
    ordinary show that the fullscreen gate did not cause is not logged as one."""
    def gate_lines(w):
        return [c.args for c in w.logger.debug.call_args_list if "Fullscreen gate" in c.args[0]]
    w = gate.make(visible=False)
    gate.refresh(w, NORMAL)                       # first show at startup: not a fullscreen event
    assert w.shown["v"] is True and gate_lines(w) == []
    gate.refresh(w, JAVA)
    gate.refresh(w, DESKTOP)
    lines = gate_lines(w)
    assert [l[1] for l in lines] == ["hiding", "showing"]
    assert [l[2] for l in lines] == ["covered", "desktop-foreground"]
    assert all(l[3].startswith("class=") and "pid=" in l[3] for l in lines)


def test_keep_visible_fullscreen_still_wins(gate):
    w = gate.make(visible=True)
    w.config["keep_visible_fullscreen"] = True
    gate.refresh(w, JAVA)
    assert _hides(w) == []


# --- the event handler (hook + 250 ms poll) --------------------------------------------------------

@pytest.fixture
def handler(q_app):
    from netspeedtray.core import system_events as se
    with patch("netspeedtray.core.system_events.WinEventHook"):
        h = se.SystemEventHandler()
    yield h
    h.stop()


def _win32_world(fg, visible, cloaked=False):
    """win32 patches for the handler: every window is monitor-sized on the taskbar's monitor."""
    return [
        patch("win32gui.GetForegroundWindow", return_value=fg),
        patch("win32gui.IsWindow", side_effect=lambda h: bool(h) and h != DEAD),
        patch("win32gui.IsWindowVisible", side_effect=lambda h: h in visible),
        patch("netspeedtray.utils.dwm.is_cloaked", return_value=cloaked, create=True),
        patch("win32gui.GetClassName", return_value="SunAwtFrame"),
        patch("win32gui.GetWindowRect", return_value=(0, 0, 1920, 1200)),
        patch("win32api.MonitorFromWindow", return_value=1),
        patch("win32api.GetMonitorInfo", return_value={"Monitor": (0, 0, 1920, 1200)}),
        patch("netspeedtray.core.system_events.get_taskbar_info"),
        # The pre-#503 obstruction test: rect == monitor, nothing about visibility.
        patch("netspeedtray.core.system_events.is_taskbar_obstructed", side_effect=lambda tb, h: bool(h)),
    ]


def _run(patches, fn):
    for p in patches:
        p.start()
    try:
        fn()
    finally:
        for p in reversed(patches):
            p.stop()


def test_poll_null_foreground_keeps_the_fullscreen_state(handler):
    """Java -> NULL -> Java on the 250 ms poll: no exit edge (which would refresh the widget)."""
    hide, refresh = MagicMock(), MagicMock()
    handler.immediate_hide_requested.connect(hide)
    handler.foreground_app_changed.connect(refresh)
    for fg in (JAVA, 0, JAVA, 0):
        _run(_win32_world(fg, visible={JAVA}), handler._poll_fullscreen)
    hide.assert_called_once()          # the one real enter edge
    refresh.assert_not_called()
    assert handler.is_fullscreen_active() is True


def test_poll_exit_when_the_fullscreen_window_closes_under_a_null_foreground(handler):
    refresh = MagicMock()
    handler.foreground_app_changed.connect(refresh)
    _run(_win32_world(JAVA, visible={JAVA}), handler._poll_fullscreen)
    _run(_win32_world(0, visible=set()), handler._poll_fullscreen)    # Java gone, nothing foreground
    refresh.assert_called_once()
    assert handler.is_fullscreen_active() is False


def test_poll_after_forget_fullscreen_is_quiet_on_null_and_hides_on_the_cover_returning(handler):
    """After the widget's latch released on a no-foreground timeout: the poll stays quiet while the
    foreground is NULL, reports nothing fullscreen, and the cover coming back is a fresh edge that
    hides at once (rc.7's ~250 ms), not after the 1 s tick."""
    hide, refresh = MagicMock(), MagicMock()
    handler.immediate_hide_requested.connect(hide)
    handler.foreground_app_changed.connect(refresh)
    _run(_win32_world(JAVA, visible={JAVA}), handler._poll_fullscreen)
    _run(_win32_world(0, visible={JAVA}), handler._poll_fullscreen)
    handler.forget_fullscreen()
    assert handler.is_fullscreen_active() is False
    _run(_win32_world(0, visible={JAVA}), handler._poll_fullscreen)
    refresh.assert_not_called()
    assert hide.call_count == 1
    _run(_win32_world(JAVA, visible={JAVA}), handler._poll_fullscreen)
    assert hide.call_count == 2 and handler.is_fullscreen_active() is True


@pytest.mark.parametrize("visible,cloaked", [(set(), False), ({INVISIBLE_FS}, True)],
                         ids=["invisible", "cloaked"])
def test_invisible_or_cloaked_monitor_sized_foreground_hides_nothing(handler, visible, cloaked):
    hide = MagicMock()
    handler.immediate_hide_requested.connect(hide)
    _run(_win32_world(INVISIBLE_FS, visible=visible, cloaked=cloaked),
         lambda: handler._on_foreground_change_immediate(INVISIBLE_FS))
    _run(_win32_world(INVISIBLE_FS, visible=visible, cloaked=cloaked), handler._poll_fullscreen)
    hide.assert_not_called()


# --- is_taskbar_obstructed and the mirrors ---------------------------------------------------------

@pytest.mark.parametrize("visible,cloaked", [(False, False), (True, True)], ids=["invisible", "cloaked"])
def test_is_taskbar_obstructed_ignores_windows_that_are_not_drawn(visible, cloaked):
    from netspeedtray.utils import taskbar_utils as tu
    tb = types.SimpleNamespace(hwnd=TB_HWND)
    with patch("win32gui.IsWindow", return_value=True), \
            patch("win32gui.IsWindowVisible", return_value=visible), \
            patch("netspeedtray.utils.dwm.is_cloaked", return_value=cloaked, create=True), \
            patch("win32process.GetWindowThreadProcessId", return_value=(1, 999999)), \
            patch("win32gui.GetClassName", return_value="SunAwtFrame"), \
            patch.object(tu, "get_process_name_from_hwnd", return_value="java.exe"), \
            patch("win32api.MonitorFromWindow", return_value=1), \
            patch("win32gui.GetWindowRect", return_value=(0, 0, 1920, 1200)), \
            patch("win32api.GetMonitorInfo", return_value={"Monitor": (0, 0, 1920, 1200)}):
        assert tu.is_taskbar_obstructed(tb, INVISIBLE_FS) is False


def test_is_taskbar_obstructed_still_sees_a_real_fullscreen_window():
    from netspeedtray.utils import taskbar_utils as tu
    tb = types.SimpleNamespace(hwnd=TB_HWND)
    with patch("win32gui.IsWindow", return_value=True), \
            patch("win32gui.IsWindowVisible", return_value=True), \
            patch("netspeedtray.utils.dwm.is_cloaked", return_value=False, create=True), \
            patch("win32process.GetWindowThreadProcessId", return_value=(1, 999999)), \
            patch("win32gui.GetClassName", return_value="SunAwtFrame"), \
            patch.object(tu, "get_process_name_from_hwnd", return_value="java.exe"), \
            patch("win32api.MonitorFromWindow", return_value=1), \
            patch("win32gui.GetWindowRect", return_value=(0, 0, 1920, 1200)), \
            patch("win32api.GetMonitorInfo", return_value={"Monitor": (0, 0, 1920, 1200)}):
        assert tu.is_taskbar_obstructed(tb, JAVA) is True


@pytest.fixture
def mirror_host(monkeypatch):
    """A stand-in ``self`` for MirrorWidget.refresh with a frozen-clock latch."""
    from netspeedtray.views.widget import mirror as mirror_mod
    world = {"covering": {JAVA}, "on_screen": {JAVA, NORMAL, DESKTOP}}
    monkeypatch.setattr(mirror_mod, "is_taskbar_visible", lambda tb: True)
    monkeypatch.setattr(mirror_mod, "is_taskbar_obstructed", lambda tb, h: h in world["covering"])
    monkeypatch.setattr(mirror_mod, "is_window_on_screen", lambda h: h in world["on_screen"], raising=False)
    monkeypatch.setattr(mirror_mod, "_window_class", lambda h: "WorkerW" if h == DESKTOP else "SunAwtFrame")
    clock = {"t": 1000.0}
    m = MagicMock()
    m._fs_latch = FullscreenLatch(clock=lambda: clock["t"])
    m.dead = False
    m.native_alive.return_value = True
    m.owner_lost.return_value = False
    m._main = types.SimpleNamespace(config={"keep_visible_fullscreen": False}, _is_context_menu_visible=False)
    shown = {"v": True}
    m.isVisible.side_effect = lambda: shown["v"]
    m.setVisible.side_effect = lambda v: shown.update(v=v)
    m.position_manager.is_yielding_to_apps.return_value = False
    m._obstructed = False
    tb = types.SimpleNamespace(hwnd=TB_HWND)

    def refresh(fg):
        mirror_mod.MirrorWidget.refresh(m, tb, fg)

    def advance(seconds):
        clock["t"] += seconds
    return types.SimpleNamespace(m=m, shown=shown, world=world, refresh=refresh, advance=advance)


def test_mirror_keeps_its_fullscreen_hide_on_a_null_foreground(mirror_host):
    h = mirror_host
    h.refresh(JAVA)
    assert h.shown["v"] is False
    h.refresh(0)                                    # NULL hand-off: no information
    assert h.shown["v"] is False
    h.world["covering"].discard(JAVA)               # the fullscreen app closed
    h.world["on_screen"].discard(JAVA)
    h.refresh(0)
    assert h.shown["v"] is False and h.m.fs_recheck_wanted is True
    h.advance(CONFIRM_S)
    h.refresh(0)
    assert h.shown["v"] is True


def test_mirror_drops_its_recheck_while_the_menu_is_open(mirror_host):
    """Mid exit-confirmation when the main context menu opens: the mirror must not keep asking for the
    250 ms re-check (a stale flag re-armed it four times a second until the menu closed)."""
    h = mirror_host
    h.refresh(JAVA)
    h.refresh(NORMAL)
    assert h.m.fs_recheck_wanted is True
    h.m._main._is_context_menu_visible = True
    h.refresh(NORMAL)
    assert h.m.fs_recheck_wanted is False


def test_mirror_exit_needs_two_readings_apart(mirror_host):
    """A mirror, like the main widget, does not show on one not-covered reading."""
    h = mirror_host
    h.refresh(JAVA)
    h.refresh(NORMAL)
    assert h.shown["v"] is False                    # a single blip cannot show it
    assert h.m.fs_recheck_wanted is True            # ...the manager re-ticks to confirm
    h.refresh(JAVA)                                 # covered again: the exit starts over
    h.advance(CONFIRM_S)
    h.refresh(NORMAL)
    assert h.shown["v"] is False
    h.advance(CONFIRM_S)
    h.refresh(NORMAL)
    assert h.shown["v"] is True and h.m.fs_recheck_wanted is False


def test_mirror_desktop_foreground_shows_at_once(mirror_host):
    h = mirror_host
    h.refresh(JAVA)
    h.refresh(DESKTOP)
    assert h.shown["v"] is True


def test_note_covered_with_no_window_is_ignored():
    """An immediate hide with no known window must not latch 'covered by nothing': that could never
    be re-checked and would hold the widget hidden until some real foreground came along."""
    latch = FullscreenLatch()
    latch.note_covered(0)
    assert latch.covered is False and latch.cover_hwnd == 0
    on = {NORMAL}
    assert latch.judge(0, on_screen=lambda x: x in on, obstructs=lambda x: False,
                       is_desktop=lambda x: False) is False
    latch.note_covered(JAVA)                        # a real window still latches...
    latch.note_covered(0)                           # ...and a later 0 keeps that window
    assert latch.covered is True and latch.cover_hwnd == JAVA
