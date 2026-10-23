"""
#234 (rc.2 W14 / F1): a second launch used to exit silently, so a hidden or off-screen widget could
only be recovered through Task Manager. Now the second process posts ACTIVATE_MESSAGE_NAME to the
running instance's marked window (the shutdown channel's window property) and exits 0; the widget
shows itself, re-docks, re-seats above the taskbar and, if it was off-screen, goes back to its docked
spot. The per-user instance mutex is respected: only this user's instance is asked.
"""
import ctypes
import os
import subprocess
import sys
import textwrap
import time
import types
from unittest.mock import MagicMock

import pytest

from netspeedtray import constants
from netspeedtray.core import shutdown_channel as sc


def _fake_desktop(monkeypatch, windows, own_pid=999):
    monkeypatch.setattr(sc, "_enum_top_level_windows", lambda: [w[0] for w in windows])
    props = {w[0]: w[2] for w in windows}
    pids = {w[0]: w[1] for w in windows}
    monkeypatch.setattr(sc, "_has_target_prop", lambda hwnd: props[hwnd])
    monkeypatch.setattr(sc, "_window_pid", lambda hwnd: pids[hwnd])
    monkeypatch.setattr(sc.os, "getpid", lambda: own_pid)


def test_activate_message_is_its_own_registered_atom():
    act = sc.activate_message_id()
    assert 0xC000 <= act <= 0xFFFF
    assert act != sc.shutdown_message_id()
    assert act == int(ctypes.windll.user32.RegisterWindowMessageW(constants.app.ACTIVATE_MESSAGE_NAME))


def test_second_launch_signals_this_users_instance_only(monkeypatch):
    _fake_desktop(monkeypatch, [(0x20, 200, True), (0x30, 300, True), (0x40, 400, False)])
    monkeypatch.setattr(sc, "_other_user_pids", lambda: {300})          # pid 300 is another user's
    post = MagicMock(return_value=True)
    monkeypatch.setattr(sc, "_post", post)
    assert sc.request_activate() is True
    post.assert_called_once_with(0x20, sc.activate_message_id())


def test_unknown_owner_is_still_asked(monkeypatch):
    """When processes can't be listed at all, the marked window is asked anyway: all it can do is
    become visible."""
    _fake_desktop(monkeypatch, [(0x20, 200, True)])
    monkeypatch.setattr(sc, "_other_user_pids", lambda: None)
    post = MagicMock(return_value=True)
    monkeypatch.setattr(sc, "_post", post)
    assert sc.request_activate() is True
    post.assert_called_once()


def test_nothing_to_activate(monkeypatch):
    _fake_desktop(monkeypatch, [(0x20, 300, True)])
    monkeypatch.setattr(sc, "_other_user_pids", lambda: {300})
    post = MagicMock(return_value=True)
    monkeypatch.setattr(sc, "_post", post)
    assert sc.request_activate() is False
    post.assert_not_called()


def test_other_user_pids_never_contains_this_process():
    pids = sc._other_user_pids()
    assert pids is not None and os.getpid() not in pids


def test_elevated_instance_with_no_reported_sid_is_still_signalled(monkeypatch):
    """#440 review M1: from an unelevated second launch WTSEnumerateProcesses reports NO SID for an
    elevated instance (its token can't be opened). Only a known DIFFERENT SID excludes a process."""
    import win32api
    import win32con
    import win32security
    import win32ts
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        mine = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        token.Close()
    other = win32security.ConvertStringSidToSid("S-1-5-21-1-2-3-1001")
    monkeypatch.setattr(win32ts, "WTSEnumerateProcesses", lambda *a: [
        (1, 200, "NetSpeedTray.exe", None),       # elevated instance of ours: no SID visible
        (1, 300, "NetSpeedTray.exe", other),      # another user's copy on this desktop
        (1, 400, "NetSpeedTray.exe", mine),
    ])
    assert sc._other_user_pids() == {300}
    _fake_desktop(monkeypatch, [(0x20, 200, True), (0x30, 300, True), (0x40, 400, True)])
    post = MagicMock(return_value=True)
    monkeypatch.setattr(sc, "_post", post)
    assert sc.request_activate() is True
    assert [c.args[0] for c in post.call_args_list] == [0x20, 0x40]


def test_arming_also_opens_uipi_for_the_activate_message(monkeypatch):
    calls = []
    monkeypatch.setattr(sc, "_set_prop", lambda hwnd: True)
    monkeypatch.setattr(sc, "_allow_through_uipi", lambda hwnd, msg: calls.append(msg) or True)
    assert sc.arm_window(0x55) is True
    assert calls == [sc.shutdown_message_id(), sc.activate_message_id()]


_CHILD = textwrap.dedent(r"""
    import sys, time
    sys.path.insert(0, sys.argv[1])
    import win32gui
    from netspeedtray.core import shutdown_channel as sc
    hwnd = win32gui.CreateWindowEx(0, "STATIC", "nst-activate-test", 0, 0, 0, 1, 1, 0, 0, 0, None)
    assert sc.arm_window(hwnd)
    open(sys.argv[2], "w").write("ready:%d" % hwnd)
    want = sc.activate_message_id()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        ok, msg = win32gui.PeekMessage(hwnd, 0, 0, 1)   # PM_REMOVE
        if ok and msg[1] == want:
            open(sys.argv[2], "w").write("activated")
            sys.exit(0)
        time.sleep(0.02)
    sys.exit(3)
""")


def test_real_cross_process_round_trip(tmp_path, monkeypatch):
    """End to end on real Win32: another process arms a window; request_activate() from this one finds
    its mark and owner (same user, real WTS listing) and the posted message arrives there.

    The enumeration is narrowed to the child's window: a real NetSpeedTray running on this desktop
    (the developer's own) carries the same mark and must never receive a test's message."""
    src = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    script = tmp_path / "child.py"
    script.write_text(_CHILD)
    flag = tmp_path / "flag.txt"
    child = subprocess.Popen([sys.executable, str(script), src, str(flag)])
    try:
        deadline = time.monotonic() + 20
        while not (flag.exists() and flag.read_text().startswith("ready:")):
            assert child.poll() is None, "child exited early"
            assert time.monotonic() < deadline, "child never armed its window"
            time.sleep(0.05)
        hwnd = int(flag.read_text().split(":")[1])
        real_enum = sc._enum_top_level_windows
        assert hwnd in real_enum()
        monkeypatch.setattr(sc, "_enum_top_level_windows", lambda: [h for h in real_enum() if h == hwnd])
        (target,) = sc.find_running_instances()
        assert target[0] == hwnd and target[1] not in sc._other_user_pids()
        assert sc.request_activate() is True
        assert child.wait(timeout=20) == 0
        assert flag.read_text() == "activated"
    finally:
        if child.poll() is None:
            child.kill()


# --- the receiver: widget.nativeEvent + show_from_second_launch ------------------------------------

def _msg(message: int):
    from netspeedtray.views.widget.main import _NativeMSG
    m = _NativeMSG()
    m.message = message
    return m, ctypes.addressof(m)


def test_widget_routes_the_activate_message(monkeypatch):
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    scheduled = []
    monkeypatch.setattr("netspeedtray.views.widget.main.QTimer.singleShot",
                        lambda _ms, fn: scheduled.append(fn))
    fake = types.SimpleNamespace(logger=MagicMock(), fully_exit_application=MagicMock(),
                                 show_from_second_launch=MagicMock())
    _ref, addr = _msg(sc.activate_message_id())
    assert NetworkSpeedWidget.nativeEvent(fake, b"windows_generic_MSG", addr) == (True, 0)
    assert scheduled == [fake.show_from_second_launch]
    fake.fully_exit_application.assert_not_called()


def _show_fake(*, visible, off_screen, buried=True, fullscreen=False, keep_visible=False):
    pm = MagicMock()
    pm.is_buried_behind_taskbar.return_value = buried
    handler = MagicMock()
    handler.is_fullscreen_active.return_value = fullscreen
    fake = types.SimpleNamespace(
        logger=MagicMock(), position_manager=pm, system_event_handler=handler,
        config={"keep_visible_fullscreen": keep_visible}, _taskbar_lost_count=7,
        isVisible=MagicMock(return_value=visible), setVisible=MagicMock(),
        _is_off_screen=MagicMock(return_value=off_screen), reset_to_default_position=MagicMock(),
        _execute_refresh=MagicMock())
    return fake, pm


@pytest.fixture
def no_timer(monkeypatch):
    monkeypatch.setattr("netspeedtray.views.widget.main.QTimer.singleShot", lambda _ms, fn: None)


def test_hidden_off_screen_widget_comes_back_to_its_docked_spot(no_timer):
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    fake, pm = _show_fake(visible=False, off_screen=True)
    NetworkSpeedWidget.show_from_second_launch(fake)
    # #440 review L3: back for THIS session only - Reset position (which saves) is never used.
    fake.reset_to_default_position.assert_not_called()
    pm.move_to_default_for_session.assert_called_once_with()
    fake.setVisible.assert_called_once_with(True)
    pm.reset_reseat_state.assert_called_once_with()      # no backoff left to defer the re-seat
    pm.update_position.assert_called_once_with()
    pm.ensure_topmost.assert_called_once_with()
    pm.reseat_above_taskbar.assert_called_once_with()
    assert fake._taskbar_lost_count == 0


def test_visible_widget_on_screen_keeps_its_spot(no_timer):
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    fake, pm = _show_fake(visible=True, off_screen=False, buried=False)
    NetworkSpeedWidget.show_from_second_launch(fake)
    fake.reset_to_default_position.assert_not_called()
    pm.move_to_default_for_session.assert_not_called()
    fake.setVisible.assert_not_called()
    pm.reseat_above_taskbar.assert_not_called()
    pm.ensure_topmost.assert_called_once_with()


def test_session_move_keeps_every_saved_position_value():
    """#440 review L3: the duplicate start at sign-in can come before the slow monitor that holds the
    saved Free Move spot connects. The widget moves to the docked spot, but position_x/y and the drag
    offsets stay exactly as saved, so the spot is back once that monitor is."""
    from PyQt6.QtCore import QPoint, QRect
    from netspeedtray.core.position_manager import PositionManager, WindowState
    from netspeedtray.utils.taskbar_utils import TaskbarInfo
    screen = MagicMock()
    screen.name.return_value = "DISPLAY1"
    screen.geometry.return_value = QRect(0, 0, 1920, 1080)
    screen.availableGeometry.return_value = QRect(0, 0, 1920, 1032)
    tb = MagicMock(spec=TaskbarInfo)
    tb.hwnd, tb.rect, tb.dpi_scale = 0x10, (0, 1032, 1920, 1080), 1.0
    tb.tasklist_rect = tb.widgets_rect = tb.toolbars_rect = None
    tb.get_tray_rect.return_value = (1694, 1032, 1920, 1080)
    tb.get_edge_position.return_value = constants.taskbar.edge.BOTTOM
    tb.get_screen.return_value = screen
    widget = MagicMock()
    widget.width.return_value, widget.height.return_value = 190, 48
    widget.pos.return_value = QPoint(5000, 2000)
    cfg = {"free_move": True, "position_x": 5000, "position_y": 2000, "tray_offset_x": 40,
           "tray_offset_y": 9}
    saved = dict(cfg)
    pm = PositionManager(WindowState(config=cfg, widget=widget, taskbar_info=tb))
    assert pm.move_to_default_for_session() is True
    x, y = widget.move.call_args[0]
    assert x == 1694 - 190 - constants.layout.TRAY_EDGE_MIN_GAP_PX and 1032 <= y < 1080
    assert cfg == saved


def test_fullscreen_app_keeps_it_hidden_unless_keep_visible(no_timer):
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    fake, pm = _show_fake(visible=False, off_screen=False, fullscreen=True)
    NetworkSpeedWidget.show_from_second_launch(fake)
    fake.setVisible.assert_not_called()
    pm.update_position.assert_not_called()
    fake, pm = _show_fake(visible=False, off_screen=False, fullscreen=True, keep_visible=True)
    NetworkSpeedWidget.show_from_second_launch(fake)
    fake.setVisible.assert_called_once_with(True)


# --- monitor.py: the duplicate launch signals, then exits 0 -----------------------------------------

def test_second_launch_signals_then_exits_zero(monkeypatch):
    import monitor
    monkeypatch.setattr(monitor.sys, "argv", ["NetSpeedTray.exe"])
    monkeypatch.setattr(monitor, "set_app_working_directory", lambda: None)
    monkeypatch.setattr(monitor.ConfigManager, "setup_logging", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(monitor, "_install_exception_hooks", lambda logger: None)
    monkeypatch.setattr("netspeedtray.core.update_installer.sweep_stale_update_dirs", lambda: None)
    monkeypatch.setattr(monitor, "QApplication", MagicMock())

    def already_running():
        raise monitor.AlreadyRunningError("running")

    monkeypatch.setattr(monitor, "SingleInstanceChecker", already_running)
    activate = MagicMock(return_value=True)
    monkeypatch.setattr(sc, "request_activate", activate)
    assert monitor.main() == 0
    activate.assert_called_once_with()
