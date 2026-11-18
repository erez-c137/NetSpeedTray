"""
2.2.2 D3: a second launch that is the USER asking for the app opens the "NetSpeedTray is running"
panel (step 2), after the existing widget recovery (step 1). This file pins who counts as the user:

- the SECOND process decides and says so in the activate message's wParam (shutdown_channel):
  an updater relaunch (``--relaunch``) -> ACTIVATE_WPARAM_RELAUNCH; a launch from a shortcut that is
  not in a Startup folder (Start menu, taskbar pin, desktop) -> ACTIVATE_WPARAM_USER, even right after
  sign-in; otherwise the #373 sign-in rule -> ACTIVATE_WPARAM_SIGN_IN_DUPLICATE or USER;
- before posting a USER request it calls AllowSetForegroundWindow(<running pid>), or Windows keeps
  the panel behind the window the user was in;
- the running widget does step 1 for every kind and opens the panel only for a user request.
"""
import ctypes
import types
from unittest.mock import MagicMock, call

import pytest

from netspeedtray import constants
from netspeedtray.core import shutdown_channel as sc

APP = constants.app


@pytest.fixture(autouse=True)
def _quiet_desktop(monkeypatch):
    """One running instance (hwnd 0x20, pid 200) of this user; signed in long ago; no .lnk; no real
    AllowSetForegroundWindow."""
    monkeypatch.setattr(sc, "_enum_top_level_windows", lambda: [0x20])
    monkeypatch.setattr(sc, "_has_target_prop", lambda hwnd: True)
    monkeypatch.setattr(sc, "_window_pid", lambda hwnd: 200)
    monkeypatch.setattr(sc.os, "getpid", lambda: 999)
    monkeypatch.setattr(sc, "_other_user_pids", lambda: set())
    monkeypatch.setattr(sc, "_session_seconds_since_logon", lambda: 10_000.0)
    monkeypatch.setattr(sc, "_startup_link_name", lambda: None)
    monkeypatch.setattr(sc, "_startup_folders", lambda: [r"C:\Users\Bob\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup",
                                                         r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs\StartUp"])


@pytest.fixture
def wire(monkeypatch):
    """Records AllowSetForegroundWindow and PostMessage calls, in order."""
    events = []
    monkeypatch.setattr(sc, "_allow_foreground", lambda pid: events.append(("allow", pid)) or True)
    monkeypatch.setattr(sc, "_post", lambda hwnd, msg, wparam=0: events.append(("post", hwnd, wparam)) or True)
    return events


# --- the sender's verdict -------------------------------------------------------------------------

def test_a_plain_user_launch_allows_the_foreground_then_posts_user(wire):
    assert sc.request_activate(argv=[]) is True
    assert wire == [("allow", 200), ("post", 0x20, APP.ACTIVATE_WPARAM_USER)]


def test_every_instance_of_this_user_is_allowed_before_its_post(monkeypatch, wire):
    monkeypatch.setattr(sc, "_enum_top_level_windows", lambda: [0x20, 0x40])
    monkeypatch.setattr(sc, "_window_pid", lambda hwnd: {0x20: 200, 0x40: 400}[hwnd])
    assert sc.request_activate(argv=[]) is True
    assert wire == [("allow", 200), ("post", 0x20, APP.ACTIVATE_WPARAM_USER),
                    ("allow", 400), ("post", 0x40, APP.ACTIVATE_WPARAM_USER)]


def test_a_duplicate_start_at_sign_in_posts_the_flag_and_allows_nothing(monkeypatch, wire):
    monkeypatch.setattr(sc, "_session_seconds_since_logon", lambda: 15.0)
    assert sc.request_activate(argv=[]) is True
    assert wire == [("post", 0x20, APP.ACTIVATE_WPARAM_SIGN_IN_DUPLICATE)]


def test_a_start_menu_launch_right_after_sign_in_is_still_the_user(monkeypatch, wire):
    """The #373 rule must never swallow an explicit launch: a shortcut outside the Startup folders
    (Start menu, taskbar pin, desktop) is the user even inside the sign-in window."""
    monkeypatch.setattr(sc, "_session_seconds_since_logon", lambda: 15.0)
    monkeypatch.setattr(sc, "_startup_link_name", lambda: (
        r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs\NetSpeedTray.lnk"))
    assert sc.request_activate(argv=[]) is True
    assert wire == [("allow", 200), ("post", 0x20, APP.ACTIVATE_WPARAM_USER)]


@pytest.mark.parametrize("link", [
    r"C:\Users\Bob\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\NetSpeedTray.lnk",
    r"c:\programdata\microsoft\windows\start menu\programs\startup\NetSpeedTray.lnk",   # any case
    r"D:\Roaming\Elsewhere\Startup\NetSpeedTray.lnk",   # folder lookup failed: the name still counts
])
def test_a_startup_folder_shortcut_at_sign_in_is_a_duplicate(monkeypatch, wire, link):
    monkeypatch.setattr(sc, "_session_seconds_since_logon", lambda: 15.0)
    monkeypatch.setattr(sc, "_startup_link_name", lambda: link)
    assert sc.request_activate(argv=[]) is True
    assert wire == [("post", 0x20, APP.ACTIVATE_WPARAM_SIGN_IN_DUPLICATE)]


def test_an_updater_relaunch_brings_the_widget_back_without_the_panel(monkeypatch, wire):
    """Normally the old instance is gone before the updater relaunches; if it is not, the relaunch is
    still not the user asking for a window."""
    monkeypatch.setattr(sc, "_startup_link_name", lambda: r"C:\Somewhere\NetSpeedTray.lnk")
    assert sc.request_activate(argv=[sc.RELAUNCH_FLAG]) is True
    assert wire == [("post", 0x20, APP.ACTIVATE_WPARAM_RELAUNCH)]


def test_request_activate_reads_the_process_argv_by_default(monkeypatch, wire):
    monkeypatch.setattr(sc.sys, "argv", ["NetSpeedTray.exe", sc.RELAUNCH_FLAG])
    assert sc.request_activate() is True
    assert wire == [("post", 0x20, APP.ACTIVATE_WPARAM_RELAUNCH)]


def test_a_failed_allow_still_posts(monkeypatch):
    posts = []
    monkeypatch.setattr(sc, "_allow_foreground", MagicMock(side_effect=OSError("denied")))
    monkeypatch.setattr(sc, "_post", lambda hwnd, msg, wparam=0: posts.append(wparam) or True)
    assert sc.request_activate(argv=[]) is True
    assert posts == [APP.ACTIVATE_WPARAM_USER]


def test_wparam_values_are_distinct_and_the_old_ones_unchanged():
    # 0 and 1 are a wire protocol with every 2.2.x build - never renumber them.
    assert APP.ACTIVATE_WPARAM_USER == 0
    assert APP.ACTIVATE_WPARAM_SIGN_IN_DUPLICATE == 1
    assert APP.ACTIVATE_WPARAM_RELAUNCH not in (0, 1)


def test_allow_foreground_calls_user32_with_the_pid(monkeypatch):
    fake = MagicMock(return_value=1)
    monkeypatch.setattr(sc, "_AllowSetForegroundWindow", fake)
    assert sc._allow_foreground(4321) is True
    fake.assert_called_once_with(4321)


def test_the_real_startup_info_reads_without_error():
    """Read-only: whatever started pytest, the lookups return a str or None and never raise."""
    link = sc._startup_link_name()
    assert link is None or isinstance(link, str)
    folders = sc._startup_folders()
    assert isinstance(folders, list) and all(isinstance(f, str) for f in folders)


# --- the updater's relaunch says what it is ---------------------------------------------------------

def test_the_updater_relaunch_passes_the_relaunch_flag(monkeypatch, tmp_path):
    from netspeedtray.core import update_applier as ua
    popen = MagicMock()
    monkeypatch.setattr(ua.subprocess, "Popen", popen)
    exe = str(tmp_path / "NetSpeedTray.exe")
    assert ua.relaunch(exe) is True
    args = popen.call_args[0][0]
    assert args == [exe, sc.RELAUNCH_FLAG]


# --- the receiver: nativeEvent -> handle_activate_request -> step 1, then the panel -------------------

def _msg(message: int, wparam: int = 0):
    from netspeedtray.views.widget.main import _NativeMSG
    m = _NativeMSG()
    m.message = message
    m.wParam = wparam
    return m, ctypes.addressof(m)


@pytest.mark.parametrize("wparam", [0, 1, 2, 7])
def test_native_event_hands_the_wparam_to_the_activate_handler(monkeypatch, wparam):
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    scheduled = []
    monkeypatch.setattr("netspeedtray.views.widget.main.QTimer.singleShot",
                        lambda _ms, fn: scheduled.append(fn))
    fake = types.SimpleNamespace(logger=MagicMock(), fully_exit_application=MagicMock(),
                                 handle_activate_request=MagicMock())
    ref, addr = _msg(sc.activate_message_id(), wparam)
    assert NetworkSpeedWidget.nativeEvent(fake, b"windows_generic_MSG", addr) == (True, 0)
    assert len(scheduled) == 1
    scheduled[0]()
    fake.handle_activate_request.assert_called_once_with(wparam)


def _receiver(quitting=False):
    order = []
    fake = types.SimpleNamespace(
        logger=MagicMock(),
        show_from_second_launch=MagicMock(side_effect=lambda **kw: order.append(("step1", kw))),
        show_running_panel=MagicMock(side_effect=lambda: order.append(("panel",))),
        _app_is_quitting=MagicMock(return_value=quitting))
    return fake, order


@pytest.mark.parametrize("wparam,step1_dup,panel", [
    (APP.ACTIVATE_WPARAM_USER, False, True),
    (APP.ACTIVATE_WPARAM_SIGN_IN_DUPLICATE, True, False),
    (APP.ACTIVATE_WPARAM_RELAUNCH, False, False),
    (7, False, True),    # a newer build's request this one doesn't know: still the user asking
])
def test_activate_request_routing(wparam, step1_dup, panel):
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    fake, order = _receiver()
    NetworkSpeedWidget.handle_activate_request(fake, wparam)
    expected = [("step1", {"sign_in_duplicate": step1_dup})]
    if panel:
        expected.append(("panel",))
    assert order == expected


def test_no_panel_while_the_app_is_quitting():
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    fake, order = _receiver(quitting=True)
    NetworkSpeedWidget.handle_activate_request(fake, APP.ACTIVATE_WPARAM_USER)
    assert order == [("step1", {"sign_in_duplicate": False})]


def test_a_failing_step_one_still_opens_the_panel():
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    fake, order = _receiver()
    fake.show_from_second_launch.side_effect = RuntimeError("boom")
    NetworkSpeedWidget.handle_activate_request(fake, APP.ACTIVATE_WPARAM_USER)
    assert order == [("panel",)]
