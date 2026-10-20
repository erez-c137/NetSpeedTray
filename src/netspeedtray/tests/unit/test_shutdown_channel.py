"""
The graceful-shutdown channel: `NetSpeedTray.exe --shutdown` (sender) and the running widget (receiver).

Before this, `--shutdown` called ``win32gui.RegisterWindowMessageW`` (no such function - it raised and
returned 1) and nothing in the app listened anyway, while setup.iss looked for a window titled
``NetSpeedTrayHidden`` that 2.x never creates. So every installer upgrade ended in ``taskkill /F``.

Sender tests mock the Win32 seams in ``core.shutdown_channel``; the receiver tests drive the real
``NetworkSpeedWidget.nativeEvent`` with a synthesised MSG, the pattern from test_native_event_resume.py.
The live proof (built EXE, both elevation directions) is in the PR description.
"""
import ctypes
import types
from unittest.mock import MagicMock

import pytest

from netspeedtray import constants
from netspeedtray.core import shutdown_channel as sc


# --- message registration ------------------------------------------------------------------------

def test_message_id_is_a_real_registered_atom():
    """RegisterWindowMessageW returns 0xC000-0xFFFF for a string; the same string gives the same atom
    in every process - which is what lets a separate `--shutdown` process address the running app."""
    mid = sc.shutdown_message_id()
    assert 0xC000 <= mid <= 0xFFFF
    assert mid == int(ctypes.windll.user32.RegisterWindowMessageW(constants.app.SHUTDOWN_MESSAGE_NAME))


# --- CLI parsing ---------------------------------------------------------------------------------

def test_cli_returns_none_without_flag(monkeypatch):
    called = MagicMock()
    monkeypatch.setattr(sc, "request_shutdown", called)
    assert sc.run_shutdown_cli(["--export-csv"]) is None
    called.assert_not_called()


def test_cli_passes_default_and_explicit_timeout(monkeypatch):
    seen = []
    monkeypatch.setattr(sc, "request_shutdown", lambda timeout: seen.append(timeout) or 0)
    assert sc.run_shutdown_cli(["--shutdown"]) == 0
    assert sc.run_shutdown_cli(["--shutdown", "--timeout", "4"]) == 0
    assert seen == [constants.app.SHUTDOWN_DEFAULT_TIMEOUT_S, 4.0]


def test_cli_bad_timeout_falls_back_to_default(monkeypatch):
    seen = []
    monkeypatch.setattr(sc, "request_shutdown", lambda timeout: seen.append(timeout) or 0)
    sc.run_shutdown_cli(["--shutdown", "--timeout", "soon"])
    sc.run_shutdown_cli(["--shutdown", "--timeout", "-3"])
    assert seen == [constants.app.SHUTDOWN_DEFAULT_TIMEOUT_S] * 2


# --- finding the running instance ----------------------------------------------------------------

def _fake_desktop(monkeypatch, windows, own_pid=999):
    """windows: list of (hwnd, pid, has_prop)."""
    monkeypatch.setattr(sc, "_enum_top_level_windows", lambda: [w[0] for w in windows])
    props = {w[0]: w[2] for w in windows}
    pids = {w[0]: w[1] for w in windows}
    monkeypatch.setattr(sc, "_has_target_prop", lambda hwnd: props[hwnd])
    monkeypatch.setattr(sc, "_window_pid", lambda hwnd: pids[hwnd])
    monkeypatch.setattr(sc.os, "getpid", lambda: own_pid)


def test_finds_only_the_marked_window(monkeypatch):
    _fake_desktop(monkeypatch, [(0x10, 100, False), (0x20, 200, True), (0x30, 300, False)])
    assert sc.find_running_instances() == [(0x20, 200)]


def test_never_targets_its_own_process(monkeypatch):
    _fake_desktop(monkeypatch, [(0x20, 999, True)], own_pid=999)
    assert sc.find_running_instances() == []


# --- request_shutdown: the whole sender ----------------------------------------------------------

def test_nothing_running_is_success_without_posting(monkeypatch):
    _fake_desktop(monkeypatch, [(0x10, 100, False)])
    post = MagicMock(return_value=True)
    monkeypatch.setattr(sc, "_post", post)
    assert sc.request_shutdown(timeout=1) == sc.EXIT_OK
    post.assert_not_called()


def test_running_instance_is_signalled_and_waited_for(monkeypatch):
    _fake_desktop(monkeypatch, [(0x20, 200, True)])
    post = MagicMock(return_value=True)
    waits = []
    monkeypatch.setattr(sc, "_post", post)
    monkeypatch.setattr(sc, "_wait_for_exit", lambda pid, hwnd, t: waits.append((pid, hwnd, t)) or True)
    assert sc.request_shutdown(timeout=7) == sc.EXIT_OK
    post.assert_called_once_with(0x20, sc.shutdown_message_id())
    assert waits == [(200, 0x20, 7)]


def test_instance_that_does_not_exit_is_a_timeout(monkeypatch):
    _fake_desktop(monkeypatch, [(0x20, 200, True)])
    monkeypatch.setattr(sc, "_post", MagicMock(return_value=True))
    monkeypatch.setattr(sc, "_wait_for_exit", lambda pid, hwnd, t: False)
    assert sc.request_shutdown(timeout=1) == sc.EXIT_TIMEOUT


def test_failed_post_is_reported_not_waited_out(monkeypatch):
    """A refused PostMessage (UIPI with no filter on an old receiver) must not burn the full timeout."""
    _fake_desktop(monkeypatch, [(0x20, 200, True)])
    monkeypatch.setattr(sc, "_post", MagicMock(return_value=False))
    wait = MagicMock(return_value=True)
    monkeypatch.setattr(sc, "_wait_for_exit", wait)
    assert sc.request_shutdown(timeout=1) == sc.EXIT_TIMEOUT
    wait.assert_not_called()


def test_wait_for_exit_real_process_handle():
    """_wait_for_exit on our own (live) PID times out promptly; on a dead PID it returns True."""
    import os, subprocess, sys
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    assert sc._wait_for_exit(p.pid, 0, 1.0) is True
    assert sc._wait_for_exit(os.getpid(), 0, 0.2) is False


# --- arming the receiver window ------------------------------------------------------------------

def test_arm_window_marks_and_opens_uipi(monkeypatch):
    calls = []
    monkeypatch.setattr(sc, "_set_prop", lambda hwnd: calls.append(("prop", hwnd)) or True)
    monkeypatch.setattr(sc, "_allow_through_uipi", lambda hwnd, msg: calls.append(("uipi", hwnd, msg)) or True)
    assert sc.arm_window(0x55) is True
    assert calls == [("prop", 0x55), ("uipi", 0x55, sc.shutdown_message_id())]


def test_arm_window_rejects_null_hwnd(monkeypatch):
    monkeypatch.setattr(sc, "_set_prop", MagicMock())
    assert sc.arm_window(0) is False
    sc._set_prop.assert_not_called()


def test_marked_window_is_found_by_a_real_enumeration():
    """End to end on real Win32: a native window this process marks is enumerated and carries the prop.
    (find_running_instances() then drops it only because it is our own PID.)"""
    import win32gui
    hwnd = win32gui.CreateWindowEx(0, "STATIC", "nst-shutdown-test", 0, 0, 0, 1, 1, 0, 0, 0, None)
    try:
        assert sc.arm_window(hwnd) is True
        assert hwnd in sc._enum_top_level_windows()
        assert sc._has_target_prop(hwnd) is True
        assert sc._window_pid(hwnd) == __import__("os").getpid()
    finally:
        win32gui.DestroyWindow(hwnd)


# --- the receiver: widget.nativeEvent ------------------------------------------------------------

def _msg(message: int):
    from netspeedtray.views.widget.main import _NativeMSG
    m = _NativeMSG()
    m.message = message
    return m, ctypes.addressof(m)


def test_widget_handles_shutdown_message_via_clean_exit(monkeypatch):
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    scheduled = []
    monkeypatch.setattr("netspeedtray.views.widget.main.QTimer.singleShot",
                        lambda _ms, fn: scheduled.append(fn))
    fake = types.SimpleNamespace(logger=MagicMock(), fully_exit_application=MagicMock(),
                                 _on_environment_changed=MagicMock())
    _ref, addr = _msg(sc.shutdown_message_id())
    result = NetworkSpeedWidget.nativeEvent(fake, b"windows_generic_MSG", addr)
    assert result == (True, 0)
    # Deferred out of the native dispatch, then the SAME path as tray Exit.
    fake.fully_exit_application.assert_not_called()
    assert len(scheduled) == 1
    scheduled[0]()
    fake.fully_exit_application.assert_called_once_with()


def test_widget_ignores_unrelated_registered_messages(monkeypatch):
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    fake = types.SimpleNamespace(logger=MagicMock(), fully_exit_application=MagicMock(),
                                 _on_environment_changed=MagicMock())
    other = int(ctypes.windll.user32.RegisterWindowMessageW("NetSpeedTray_SomethingElse_test"))
    _ref, addr = _msg(other)
    assert NetworkSpeedWidget.nativeEvent(fake, b"windows_generic_MSG", addr) == (False, 0)
    fake.fully_exit_application.assert_not_called()


def test_widget_arms_its_native_window_once_per_hwnd(monkeypatch):
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    armed = []
    monkeypatch.setattr("netspeedtray.views.widget.main.shutdown_channel.arm_window",
                        lambda hwnd: armed.append(hwnd) or True)
    fake = types.SimpleNamespace(logger=MagicMock(), winId=MagicMock(return_value=0x101),
                                 _shutdown_armed_hwnd=0)
    NetworkSpeedWidget._arm_shutdown_channel(fake)
    NetworkSpeedWidget._arm_shutdown_channel(fake)
    fake.winId.return_value = 0x202          # Qt recreated the native window
    NetworkSpeedWidget._arm_shutdown_channel(fake)
    assert armed == [0x101, 0x202]


# --- monitor.py routes --shutdown before any GUI / mutex / update sweep ---------------------------

def test_monitor_main_routes_shutdown_headless(monkeypatch):
    import monitor
    monkeypatch.setattr(monitor.sys, "argv", ["NetSpeedTray.exe", "--shutdown"])
    monkeypatch.setattr(monitor, "set_app_working_directory", lambda: None)
    monkeypatch.setattr(monitor.ConfigManager, "setup_logging", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(sc, "request_shutdown", lambda timeout: 0)
    boom = MagicMock(side_effect=AssertionError("must not reach the GUI / mutex path"))
    monkeypatch.setattr(monitor, "QApplication", boom)
    monkeypatch.setattr(monitor, "SingleInstanceChecker", boom)
    sweep = MagicMock()
    monkeypatch.setattr("netspeedtray.core.update_installer.sweep_stale_update_dirs", sweep)
    assert monitor.main() == 0
    sweep.assert_not_called()   # the installer runs us mid-update: never sweep its temp dir


# --- the quit path runs its (bounded, possibly long) cleanup once ---------------------------------

def test_widget_cleanup_runs_once():
    """closeEvent and aboutToQuit both call cleanup(); the second call must be a no-op, or a quit whose
    DB thread outlived DB_SHUTDOWN_WAIT_MS would wait that long twice and overrun --shutdown's timeout."""
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    fake = types.SimpleNamespace(logger=MagicMock(), _hover_card_timer=MagicMock(),
                                 _hide_usage_hover_card=MagicMock())
    # Stop right after the first real step; we only count how often cleanup starts.
    fake._hide_usage_hover_card.side_effect = RuntimeError("stop here")
    NetworkSpeedWidget.cleanup(fake)
    NetworkSpeedWidget.cleanup(fake)
    assert fake._hover_card_timer.stop.call_count == 1


def test_shutdown_timeout_outlasts_the_db_quit_wait():
    """The app may legitimately take DB_SHUTDOWN_WAIT_MS (+ the 1.5 s latency-probe join) to quit
    mid-migration; --shutdown must wait longer than that, or Setup taskkills a clean quit."""
    wait_ms = getattr(constants.timeouts, "DB_SHUTDOWN_WAIT_MS", 2000)
    assert constants.app.SHUTDOWN_DEFAULT_TIMEOUT_S * 1000 >= wait_ms + 1500 + 5000


def _setup_iss() -> str:
    from pathlib import Path
    return (Path(__file__).resolve().parents[4] / "build" / "setup.iss").read_text(encoding="utf-8")


def _iss_code_section(iss: str) -> str:
    return iss[iss.index("[Code]"):]


def test_installer_waits_the_same_timeout():
    """setup.iss waits ShutdownTimeoutSec for the app to exit; it must not drift from the app's constant."""
    import re
    iss = _setup_iss()
    m = re.search(r'#define ShutdownTimeoutSec "(\d+)"', iss)
    assert m and float(m.group(1)) == constants.app.SHUTDOWN_DEFAULT_TIMEOUT_S
    assert "{#ShutdownTimeoutSec} * 1000" in _iss_code_section(iss)


def test_installer_speaks_the_same_wire_protocol():
    """Setup posts the shutdown message itself, so its copies of the two channel names must match
    the app's - a drift silently turns every upgrade back into taskkill /F."""
    import re
    iss = _setup_iss()
    msg = re.search(r'#define ShutdownMessageName "([^"]+)"', iss)
    prop = re.search(r'#define ShutdownWindowProp "([^"]+)"', iss)
    assert msg and msg.group(1) == constants.app.SHUTDOWN_MESSAGE_NAME
    assert prop and prop.group(1) == constants.app.SHUTDOWN_WINDOW_PROP
    code = _iss_code_section(iss)
    assert "WinRegisterWindowMessage('{#ShutdownMessageName}')" in code
    assert "WinGetProp(Wnd, '{#ShutdownWindowProp}')" in code


def test_installer_never_executes_the_installed_exe():
    """Setup runs elevated and the install folder is only as safe as its ACL (a custom folder under C:\\
    inherits Authenticated Users Modify), so Setup / the uninstaller must never run {app}'s exe or a
    path read from the registry. The only Exec allowed in [Code] is the system taskkill fallback."""
    import re
    code = _iss_code_section(_setup_iss())
    code = "\n".join(line for line in code.splitlines() if not line.lstrip().startswith("//"))
    assert "--shutdown" not in code
    assert "App Path" not in code
    execs = re.findall(r"\bExec(?:AsOriginalUser)?\s*\(([^,]+),", code)
    assert execs, "expected the taskkill fallback Exec"
    assert all(arg.strip() == "ExpandConstant('{sys}\\taskkill.exe')" for arg in execs), execs


# --- review fixes ---------------------------------------------------------------------------------

def _cleanup_fake():
    fake = types.SimpleNamespace(
        logger=MagicMock(), _hover_card_timer=MagicMock(), _hide_usage_hover_card=MagicMock(),
        system_event_handler=MagicMock(), position_manager=MagicMock(), _state_watcher_timer=MagicMock(),
        monitor_thread=MagicMock(), latency_probe=MagicMock(), metrics_endpoint=MagicMock(),
        controller=MagicMock(), widget_state=MagicMock(), monitor_window=None,
        config={"free_move": False, "free_float": True}, config_manager=MagicMock(),
        update_config=MagicMock(), pos=MagicMock())
    fake.position_manager.is_floating.return_value = False
    return fake


def test_cleanup_early_failure_still_flushes_db_and_saves_config():
    """A teardown step that raises (here the event hooks) must not skip the history flush or the
    final config save - and the aboutToQuit retry must not be what rescues them."""
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    fake = _cleanup_fake()
    fake.system_event_handler.stop.side_effect = RuntimeError("hook will not unhook")
    NetworkSpeedWidget.cleanup(fake)
    fake.widget_state.cleanup.assert_called_once_with()
    fake.config_manager.save.assert_called_once_with(fake.config)
    fake.monitor_thread.stop.assert_called_once_with()
    fake.metrics_endpoint.stop.assert_called_once_with()
    NetworkSpeedWidget.cleanup(fake)              # the aboutToQuit pass
    fake.widget_state.cleanup.assert_called_once_with()
    fake.config_manager.save.assert_called_once_with(fake.config)


def test_widget_rearms_on_winid_change_without_creating_a_window(monkeypatch):
    """WinIdChange (create / recreate, e.g. #327's re-dock) re-arms on the new HWND via
    internalWinId(), which never creates a window - so a teardown (id 0) is skipped."""
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    armed = []
    monkeypatch.setattr("netspeedtray.views.widget.main.shutdown_channel.arm_window",
                        lambda hwnd: armed.append(hwnd) or True)
    fake = types.SimpleNamespace(logger=MagicMock(), winId=MagicMock(side_effect=AssertionError("creates")),
                                 internalWinId=MagicMock(return_value=0x303), _shutdown_armed_hwnd=0)
    NetworkSpeedWidget._arm_shutdown_channel(fake, create=False)
    fake.internalWinId.return_value = 0           # being destroyed
    NetworkSpeedWidget._arm_shutdown_channel(fake, create=False)
    assert armed == [0x303]


def test_widget_event_routes_winid_change_to_arming():
    from PyQt6.QtCore import QEvent
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    calls = []
    fake = types.SimpleNamespace(_arm_shutdown_channel=lambda create=True: calls.append(create))
    with pytest.raises(TypeError):
        # super().event() on a non-QWidget fake raises after our branch has run - fine for this check.
        NetworkSpeedWidget.event(fake, QEvent(QEvent.Type.WinIdChange))
    assert calls == [False]


def test_widget_arms_before_first_show():
    """A starting instance must be reachable before its (500 ms delayed) first show: __init__ arms
    the channel itself, ahead of scheduling the show."""
    import inspect
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    src = inspect.getsource(NetworkSpeedWidget.__init__)
    assert "self._arm_shutdown_channel()" in src
    assert src.index("self._arm_shutdown_channel()") < src.index("_delayed_initial_show")


def test_shutdown_hard_deadline_is_armed_first_and_bounded():
    import inspect
    import monitor
    fired = []
    t = monitor._arm_shutdown_deadline(["--shutdown", "--timeout", "4"], exit_fn=fired.append)
    try:
        assert t.interval == 4 + monitor._SHUTDOWN_DEADLINE_GRACE_S
        assert t.daemon and t.is_alive()
    finally:
        t.cancel()
    t2 = monitor._arm_shutdown_deadline(["--shutdown"], exit_fn=fired.append)
    t2.cancel()
    assert t2.interval == constants.app.SHUTDOWN_DEFAULT_TIMEOUT_S + monitor._SHUTDOWN_DEADLINE_GRACE_S
    assert monitor._arm_shutdown_deadline(["--export-csv"]) is None
    assert fired == []
    # Armed at import, before PyQt6 / pywin32 / the app load.
    src = inspect.getsource(monitor)
    call = src.index("_shutdown_deadline = _arm_shutdown_deadline(")
    assert call < src.index("from PyQt6") and call < src.index("import win32api")


def test_shutdown_hard_deadline_fires_exit_1(monkeypatch):
    import time
    import monitor
    fired = []
    monkeypatch.setattr(monitor, "_SHUTDOWN_DEADLINE_GRACE_S", 0.0)
    monitor._arm_shutdown_deadline(["--shutdown", "--timeout", "0.05"], exit_fn=fired.append)
    time.sleep(0.4)
    assert fired == [1]
