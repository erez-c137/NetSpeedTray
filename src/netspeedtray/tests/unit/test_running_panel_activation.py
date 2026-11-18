"""
2.2.2 D3: a second launch that is the USER asking for the app opens the "NetSpeedTray is running"
panel (step 2), after the existing widget recovery (step 1). This file pins who counts as the user:

- the SECOND process decides and says so in the activate message's wParam (shutdown_channel):
  an updater relaunch (``--relaunch``) -> ACTIVATE_WPARAM_RELAUNCH; a launch from a shortcut to
  NetSpeedTray that is not in exactly the user's / all-users Startup folder (Start menu, taskbar pin,
  desktop) -> ACTIVATE_WPARAM_USER, even right after sign-in; a Startup-folder shortcut or the Run
  key's start (``--autostart``) -> ACTIVATE_WPARAM_SIGN_IN_DUPLICATE whenever it happens; otherwise
  the #373 sign-in rule -> SIGN_IN_DUPLICATE; after it, USER only with positive evidence (Explorer
  started this process: a double-clicked exe) and ACTIVATE_WPARAM_BACKGROUND (step 1 only) for
  everything else (a scheduled task, a script, a launcher) - combined review C2;
- before posting a USER request it calls AllowSetForegroundWindow(<running pid>), or Windows keeps
  the panel behind the window the user was in, and sends the launch monitor in lParam;
- the running widget does step 1 for every kind and opens the panel only for wParam USER.
"""
import ctypes
import types
from unittest.mock import MagicMock

import pytest

from netspeedtray import constants
from netspeedtray.core import shutdown_channel as sc

APP = constants.app
USER_STARTUP = r"C:\Users\Bob\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup"
COMMON_STARTUP = r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs\StartUp"
NST_EXE = r"C:\Program Files\NetSpeedTray\NetSpeedTray.exe"


@pytest.fixture(autouse=True)
def _quiet_desktop(monkeypatch):
    """One running instance (hwnd 0x20, pid 200) of this user; signed in long ago; no .lnk; no monitor
    hint; shortcuts point at NetSpeedTray; no real AllowSetForegroundWindow."""
    monkeypatch.setattr(sc, "_enum_top_level_windows", lambda: [0x20])
    monkeypatch.setattr(sc, "_has_target_prop", lambda hwnd: True)
    monkeypatch.setattr(sc, "_window_pid", lambda hwnd: 200)
    monkeypatch.setattr(sc.os, "getpid", lambda: 999)
    monkeypatch.setattr(sc, "_other_user_pids", lambda: set())
    monkeypatch.setattr(sc, "_session_seconds_since_logon", lambda: 10_000.0)
    monkeypatch.setattr(sc, "_startup_link_name", lambda: None)
    monkeypatch.setattr(sc, "_startup_monitor_center", lambda: None)
    monkeypatch.setattr(sc, "_link_target", lambda link: NST_EXE)
    monkeypatch.setattr(sc, "_startup_folders", lambda: [USER_STARTUP, COMMON_STARTUP])
    monkeypatch.setattr(sc, "_launched_by_explorer", lambda: True)


@pytest.fixture
def wire(monkeypatch):
    """Records AllowSetForegroundWindow and PostMessage calls, in order."""
    events = []
    monkeypatch.setattr(sc, "_allow_foreground", lambda pid: events.append(("allow", pid)) or True)

    def post(hwnd, msg, wparam=0, lparam=None):
        events.append(("post", hwnd, wparam) if lparam is None else ("post", hwnd, wparam, lparam))
        return True

    monkeypatch.setattr(sc, "_post", post)
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
    """The #373 rule must never swallow an explicit launch: a shortcut to NetSpeedTray outside the
    Startup folders (Start menu, taskbar pin, desktop) is the user even inside the sign-in window."""
    monkeypatch.setattr(sc, "_session_seconds_since_logon", lambda: 15.0)
    monkeypatch.setattr(sc, "_startup_link_name", lambda: (
        r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs\NetSpeedTray.lnk"))
    assert sc.request_activate(argv=[]) is True
    assert wire == [("allow", 200), ("post", 0x20, APP.ACTIVATE_WPARAM_USER)]


@pytest.mark.parametrize("link", [
    USER_STARTUP + r"\NetSpeedTray.lnk",
    COMMON_STARTUP.lower() + r"\NetSpeedTray.lnk",   # any case
])
def test_a_startup_folder_shortcut_at_sign_in_is_a_duplicate(monkeypatch, wire, link):
    monkeypatch.setattr(sc, "_session_seconds_since_logon", lambda: 15.0)
    monkeypatch.setattr(sc, "_startup_link_name", lambda: link)
    assert sc.request_activate(argv=[]) is True
    assert wire == [("post", 0x20, APP.ACTIVATE_WPARAM_SIGN_IN_DUPLICATE)]


def test_a_folder_merely_named_startup_is_not_an_autostart(monkeypatch, wire):
    """Only exactly FOLDERID_Startup / FOLDERID_CommonStartup count - not any folder called Startup."""
    monkeypatch.setattr(sc, "_session_seconds_since_logon", lambda: 15.0)
    monkeypatch.setattr(sc, "_startup_link_name", lambda: r"D:\Tools\Startup\NetSpeedTray.lnk")
    assert sc.request_activate(argv=[]) is True
    assert wire == [("allow", 200), ("post", 0x20, APP.ACTIVATE_WPARAM_USER)]


@pytest.mark.parametrize("target,user", [
    (NST_EXE, True),
    (r"D:\Portable\NetSpeedTray\netspeedtray.exe", True),        # any case, any copy
    (r"C:\Tools\launcher.exe", False),                           # someone else's shortcut
    (None, False),                                               # an unreadable .lnk
])
def test_the_shortcut_must_start_netspeedtray(monkeypatch, wire, target, user):
    monkeypatch.setattr(sc, "_session_seconds_since_logon", lambda: 15.0)
    monkeypatch.setattr(sc, "_startup_link_name", lambda: r"C:\Somewhere\Thing.lnk")
    monkeypatch.setattr(sc, "_link_target", lambda link: target)
    assert sc.request_activate(argv=[]) is True
    want = APP.ACTIVATE_WPARAM_USER if user else APP.ACTIVATE_WPARAM_SIGN_IN_DUPLICATE
    assert wire[-1] == ("post", 0x20, want)


# --- combined review C2: the panel only on positive evidence ---------------------------------------

def test_a_startup_folder_shortcut_long_after_sign_in_is_still_an_autostart(monkeypatch, wire):
    """A Startup-folder .lnk is an autostart whenever it runs (a slow disk, 3 minutes in) - no panel."""
    monkeypatch.setattr(sc, "_session_seconds_since_logon", lambda: 180.0)
    monkeypatch.setattr(sc, "_startup_link_name", lambda: USER_STARTUP + r"\NetSpeedTray.lnk")
    assert sc.request_activate(argv=[]) is True
    assert wire == [("post", 0x20, APP.ACTIVATE_WPARAM_SIGN_IN_DUPLICATE)]


def test_a_run_key_start_long_after_sign_in_is_an_autostart(monkeypatch, wire):
    """The Run key's start (Explorer starts it, so its parent is explorer.exe as for a double-click) is
    told apart by its own --autostart argument: no panel, whenever it runs."""
    monkeypatch.setattr(sc, "_session_seconds_since_logon", lambda: 180.0)
    assert sc.request_activate(argv=[sc.AUTOSTART_FLAG]) is True
    assert wire == [("post", 0x20, APP.ACTIVATE_WPARAM_SIGN_IN_DUPLICATE)]


def test_a_scheduled_task_like_start_brings_the_widget_back_without_the_panel(monkeypatch, wire):
    """No shortcut and not started by Explorer (Task Scheduler, a script, a launcher): no evidence the
    user asked for a window - step 1 only."""
    monkeypatch.setattr(sc, "_session_seconds_since_logon", lambda: 180.0)
    monkeypatch.setattr(sc, "_launched_by_explorer", lambda: False)
    assert sc.request_activate(argv=[]) is True
    assert wire == [("post", 0x20, APP.ACTIVATE_WPARAM_BACKGROUND)]


def test_a_start_menu_shortcut_opens_the_panel_whatever_the_parent(monkeypatch, wire):
    monkeypatch.setattr(sc, "_session_seconds_since_logon", lambda: 180.0)
    monkeypatch.setattr(sc, "_launched_by_explorer", lambda: False)
    monkeypatch.setattr(sc, "_startup_link_name", lambda: (
        r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs\NetSpeedTray.lnk"))
    assert sc.request_activate(argv=[]) is True
    assert wire == [("allow", 200), ("post", 0x20, APP.ACTIVATE_WPARAM_USER)]


def test_the_real_parent_check_runs_without_error():
    assert sc._launched_by_explorer() in (True, False)


def test_the_run_key_value_carries_the_autostart_flag(monkeypatch):
    from netspeedtray.core import startup_manager as sm
    monkeypatch.setattr(sm.sys, "frozen", True, raising=False)
    monkeypatch.setattr(sm.sys, "executable", NST_EXE)
    value = sm.StartupManager._get_executable_path(sm.StartupManager.__new__(sm.StartupManager))
    assert value == f'"{NST_EXE}" {sc.AUTOSTART_FLAG}'


def test_unreadable_startup_folders_never_make_a_user_launch(monkeypatch, wire):
    monkeypatch.setattr(sc, "_session_seconds_since_logon", lambda: 15.0)
    monkeypatch.setattr(sc, "_startup_link_name", lambda: r"C:\Somewhere\NetSpeedTray.lnk")
    monkeypatch.setattr(sc, "_startup_folders", lambda: [])
    assert sc.request_activate(argv=[]) is True
    assert wire == [("post", 0x20, APP.ACTIVATE_WPARAM_SIGN_IN_DUPLICATE)]


def test_an_updater_relaunch_brings_the_widget_back_without_the_panel(monkeypatch, wire):
    """Normally the old instance is gone before the updater relaunches; if it is not, the relaunch is
    still not the user asking for a window."""
    monkeypatch.setattr(sc, "_startup_link_name", lambda: r"C:\Somewhere\NetSpeedTray.lnk")
    monkeypatch.setattr(sc, "_startup_monitor_center", lambda: (2880, 540))
    assert sc.request_activate(argv=[sc.RELAUNCH_FLAG]) is True
    assert wire == [("post", 0x20, APP.ACTIVATE_WPARAM_RELAUNCH)]   # no allow, no monitor hint


def test_request_activate_reads_the_process_argv_by_default(monkeypatch, wire):
    monkeypatch.setattr(sc.sys, "argv", ["NetSpeedTray.exe", sc.RELAUNCH_FLAG])
    assert sc.request_activate() is True
    assert wire == [("post", 0x20, APP.ACTIVATE_WPARAM_RELAUNCH)]


def test_a_failed_allow_still_posts(monkeypatch):
    posts = []
    monkeypatch.setattr(sc, "_allow_foreground", MagicMock(side_effect=OSError("denied")))
    monkeypatch.setattr(sc, "_post", lambda hwnd, msg, wparam=0, lparam=0: posts.append(wparam) or True)
    assert sc.request_activate(argv=[]) is True
    assert posts == [APP.ACTIVATE_WPARAM_USER]


def test_a_user_launch_carries_the_launch_monitor_in_lparam(monkeypatch, wire):
    monkeypatch.setattr(sc, "_startup_monitor_center", lambda: (2880, 540))
    assert sc.request_activate(argv=[]) is True
    assert wire == [("allow", 200), ("post", 0x20, APP.ACTIVATE_WPARAM_USER, sc.pack_point(2880, 540))]


@pytest.mark.parametrize("pt", [(960, 540), (-960, 540), (2880, -300), (1, 1), (-32768, 32767)])
def test_point_packing_round_trips(pt):
    assert sc.unpack_point(sc.pack_point(*pt)) == pt
    assert sc.unpack_point(0) is None


def test_wparam_values_are_distinct_and_the_old_ones_unchanged():
    # 0 and 1 are a wire protocol with every 2.2.x build - never renumber them.
    assert APP.ACTIVATE_WPARAM_USER == 0
    assert APP.ACTIVATE_WPARAM_SIGN_IN_DUPLICATE == 1
    assert APP.ACTIVATE_WPARAM_RELAUNCH not in (0, 1)
    assert len({APP.ACTIVATE_WPARAM_USER, APP.ACTIVATE_WPARAM_SIGN_IN_DUPLICATE,
                APP.ACTIVATE_WPARAM_RELAUNCH, APP.ACTIVATE_WPARAM_BACKGROUND}) == 4


def test_allow_foreground_calls_user32_with_the_pid(monkeypatch):
    fake = MagicMock(return_value=1)
    monkeypatch.setattr(sc, "_AllowSetForegroundWindow", fake)
    assert sc._allow_foreground(4321) is True
    fake.assert_called_once_with(4321)


# --- the real Win32/COM reads (read-only) ----------------------------------------------------------

def test_the_real_startup_info_reads_without_error():
    """Whatever started pytest, the STARTUPINFO read works (the autouse fixture patches only the
    functions built on it)."""
    si = sc._startup_info()
    assert si.cb == ctypes.sizeof(sc._STARTUPINFOW)


def test_the_real_known_startup_folders_and_link_reader(tmp_path, monkeypatch):
    monkeypatch.undo()   # the real functions
    folders = sc._startup_folders()
    assert len(folders) == 2 and all(f.lower().endswith("startup") for f in folders)
    assert sc._link_target(str(tmp_path / "missing.lnk")) is None
    center = sc._startup_monitor_center()
    assert center is None or (isinstance(center, tuple) and len(center) == 2)


def test_the_real_monitor_lookup_names_a_display():
    user32 = ctypes.windll.user32
    w, h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    name = sc.monitor_device_at(w // 2, h // 2)
    assert name is None or "DISPLAY" in name.upper()
    assert sc.monitor_device_at(-30000, -30000) is None


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

def _msg(message: int, wparam: int = 0, lparam: int = 0):
    from netspeedtray.views.widget.main import _NativeMSG
    m = _NativeMSG()
    m.message = message
    m.wParam = wparam
    m.lParam = lparam
    return m, ctypes.addressof(m)


@pytest.mark.parametrize("wparam,lparam", [(0, 0), (1, 0), (2, 0), (7, 0), (0, 0x021C0B40)])
def test_native_event_hands_wparam_and_lparam_to_the_activate_handler(monkeypatch, wparam, lparam):
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    scheduled = []
    monkeypatch.setattr("netspeedtray.views.widget.main.QTimer.singleShot",
                        lambda _ms, fn: scheduled.append(fn))
    fake = types.SimpleNamespace(logger=MagicMock(), fully_exit_application=MagicMock(),
                                 handle_activate_request=MagicMock())
    _ref, addr = _msg(sc.activate_message_id(), wparam, lparam)
    assert NetworkSpeedWidget.nativeEvent(fake, b"windows_generic_MSG", addr) == (True, 0)
    assert len(scheduled) == 1
    scheduled[0]()
    fake.handle_activate_request.assert_called_once_with(wparam, lparam)


def _receiver(quitting=False):
    order = []
    fake = types.SimpleNamespace(
        logger=MagicMock(),
        show_from_second_launch=MagicMock(side_effect=lambda **kw: order.append(("step1", kw))),
        show_running_panel=MagicMock(side_effect=lambda lparam=0: order.append(("panel", lparam))),
        _app_is_quitting=MagicMock(return_value=quitting))
    return fake, order


@pytest.mark.parametrize("wparam,step1_dup,panel", [
    (APP.ACTIVATE_WPARAM_USER, False, True),
    (APP.ACTIVATE_WPARAM_SIGN_IN_DUPLICATE, True, False),
    (APP.ACTIVATE_WPARAM_RELAUNCH, False, False),
    (3, False, False),   # ACTIVATE_WPARAM_BACKGROUND (C2): no evidence the user asked
    (7, False, False),   # a value this build doesn't know: step 1 only
])
def test_activate_request_routing(wparam, step1_dup, panel):
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    fake, order = _receiver()
    NetworkSpeedWidget.handle_activate_request(fake, wparam, 1234)
    expected = [("step1", {"sign_in_duplicate": step1_dup})]
    if panel:
        expected.append(("panel", 1234))     # the launch monitor goes along
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
    assert order == [("panel", 0)]
