"""SingleInstanceChecker: a second launch must exit quietly whenever another instance holds the mutex.

Regression: with an ELEVATED instance running, an unelevated launch could not open the mutex the
elevated process created (its default DACL admits only Administrators/SYSTEM, and its High integrity
label blocks Medium-IL callers), so CreateMutexW failed with ERROR_ACCESS_DENIED. That surfaced as a
"critical error" dialog instead of the quiet already-running exit. ACCESS_DENIED on a named mutex only
happens when the object already exists, so it means the same thing as ERROR_ALREADY_EXISTS.
"""
from unittest import mock

import pytest
import pywintypes
import winerror

import monitor
from monitor import AlreadyRunningError, SingleInstanceChecker


def _patch_create(*, side_effect=None, last_error=0):
    handle = mock.sentinel.mutex_handle
    create = mock.patch.object(monitor.win32event, "CreateMutex",
                               side_effect=side_effect, return_value=handle)
    get_last = mock.patch.object(monitor.win32api, "GetLastError", return_value=last_error)
    return create, get_last


def test_first_instance_acquires_mutex():
    create, get_last = _patch_create(last_error=0)
    with create as created, get_last, mock.patch.object(monitor.win32api, "CloseHandle") as close:
        with SingleInstanceChecker() as checker:
            assert checker.mutex is mock.sentinel.mutex_handle
            assert checker.legacy_mutex is mock.sentinel.mutex_handle, "free legacy name not claimed"
        assert close.call_count == 2
    names = [c.args[2] for c in created.call_args_list]
    assert names == [monitor.constants.app.MUTEX_NAME, monitor.constants.app.LEGACY_MUTEX_NAME]


def test_already_exists_is_quiet_second_launch():
    create, get_last = _patch_create(last_error=winerror.ERROR_ALREADY_EXISTS)
    with create, get_last, mock.patch.object(monitor.win32api, "CloseHandle"):
        with pytest.raises(AlreadyRunningError):
            SingleInstanceChecker()


def test_access_denied_from_elevated_owner_is_quiet_second_launch():
    err = pywintypes.error(winerror.ERROR_ACCESS_DENIED, "CreateMutex", "Access is denied.")
    create, get_last = _patch_create(side_effect=err)
    with create, get_last:
        with pytest.raises(AlreadyRunningError):
            SingleInstanceChecker()


def test_other_create_failures_still_raise_runtime_error():
    err = pywintypes.error(winerror.ERROR_INVALID_HANDLE, "CreateMutex", "The handle is invalid.")
    create, get_last = _patch_create(side_effect=err)
    with create, get_last:
        with pytest.raises(RuntimeError) as exc_info:
            SingleInstanceChecker()
    assert not isinstance(exc_info.value, AlreadyRunningError)


def test_real_mutex_denied_by_dacl_is_quiet(tmp_path):
    """End to end on the real kernel object: a mutex created with a DACL that denies everyone (the
    shape an elevated owner presents to an unelevated launch) must read as 'already running'."""
    import win32event
    import win32security

    name = f"Local\\NetSpeedTray_test_denied_{id(tmp_path)}"
    sa = win32security.SECURITY_ATTRIBUTES()
    sa.SECURITY_DESCRIPTOR = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        "D:(D;;GA;;;WD)", win32security.SDDL_REVISION_1)
    owner = win32event.CreateMutex(sa, False, name)
    try:
        with mock.patch.object(monitor.constants.app, "MUTEX_NAME", name):
            with pytest.raises(AlreadyRunningError):
                SingleInstanceChecker()
    finally:
        monitor.win32api.CloseHandle(owner)


# --- per-session instance (2.2.0) and the Global\ transition ----------------------------------
#
# The mutex was Global\, so the first signed-in user's instance silently blocked every other
# user's. It is Local\ now; the old Global\ name is still claimed when free and, when held, blocks
# this launch only if an old instance runs in THIS session (it shares our config and database).

def test_the_instance_mutex_is_per_session():
    assert monitor.constants.app.MUTEX_NAME.startswith("Local\\")
    assert monitor.constants.app.LEGACY_MUTEX_NAME == "Global\\NetSpeedTray_SingleInstanceMutex"


@pytest.fixture
def names(monkeypatch, tmp_path):
    """Real kernel objects under unique test names (never the app's real ones)."""
    base = f"NetSpeedTray_test_{id(tmp_path)}"
    session, legacy = f"Local\\{base}_s", f"Local\\{base}_g"
    monkeypatch.setattr(monitor.constants.app, "MUTEX_NAME", session)
    monkeypatch.setattr(monitor.constants.app, "LEGACY_MUTEX_NAME", legacy)
    return session, legacy


def _hold(name):
    import win32event
    return win32event.CreateMutex(None, False, name)


def test_a_legacy_mutex_held_in_another_session_does_not_block(names, monkeypatch):
    _session, legacy = names
    other = _hold(legacy)                                   # "another user's old instance"
    monkeypatch.setattr(monitor, "_legacy_instance_in_this_session", lambda: False)
    try:
        with SingleInstanceChecker() as checker:
            assert checker.mutex and checker.legacy_mutex is None
    finally:
        monitor.win32api.CloseHandle(other)


def test_an_old_instance_in_this_session_blocks_and_releases_ours(names, monkeypatch):
    session, legacy = names
    old = _hold(legacy)
    monkeypatch.setattr(monitor, "_legacy_instance_in_this_session", lambda: True)
    try:
        with pytest.raises(AlreadyRunningError):
            SingleInstanceChecker()
        probe = _hold(session)                              # ours was released on the way out
        assert monitor.win32api.GetLastError() != winerror.ERROR_ALREADY_EXISTS
        monitor.win32api.CloseHandle(probe)
    finally:
        monitor.win32api.CloseHandle(old)


def test_an_old_copy_started_later_sees_the_legacy_name_held(names):
    """A 2.1.x copy only checks Global\\: the new instance must be holding it."""
    _session, legacy = names
    with SingleInstanceChecker():
        h = _hold(legacy)
        assert monitor.win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS
        monitor.win32api.CloseHandle(h)


def _fake_processes(monkeypatch, procs, cmdlines=None):
    import win32ts
    monkeypatch.setattr(win32ts, "ProcessIdToSessionId", lambda pid: 1)
    monkeypatch.setattr(win32ts, "WTSEnumerateProcesses",
                        lambda *a: [(s, p, n, None) for s, p, n in procs])
    cmdlines = cmdlines or {}

    class _P:
        def __init__(self, pid):
            self.pid = pid

        def cmdline(self):
            if self.pid not in cmdlines:
                raise PermissionError("elevated")
            return cmdlines[self.pid]
    import psutil
    monkeypatch.setattr(psutil, "Process", _P)


def test_session_scan_counts_only_this_sessions_app_instances(monkeypatch):
    import os
    me = os.getpid()
    _fake_processes(monkeypatch, [(1, me, "NetSpeedTray.exe"), (2, 50, "NetSpeedTray.exe"),
                                  (1, 60, "notepad.exe")])
    assert monitor._legacy_instance_in_this_session() is False


def test_session_scan_ignores_headless_runs_but_counts_unreadable_ones(monkeypatch):
    _fake_processes(monkeypatch, [(1, 70, "NetSpeedTray.exe")],
                    {70: ["NetSpeedTray.exe", "--apply-update", "C:\\x"]})
    assert monitor._legacy_instance_in_this_session() is False
    _fake_processes(monkeypatch, [(1, 71, "NETSPEEDTRAY.EXE")])          # elevated: unreadable
    assert monitor._legacy_instance_in_this_session() is True


def test_session_scan_failure_assumes_an_instance(monkeypatch):
    import win32ts

    def boom(*a):
        raise OSError("no WTS")
    monkeypatch.setattr(win32ts, "WTSEnumerateProcesses", boom)
    assert monitor._legacy_instance_in_this_session() is True


def test_setup_checks_both_mutex_names():
    from pathlib import Path
    iss = (Path(monitor.__file__).resolve().parents[1] / "build" / "setup.iss").read_text(
        encoding="utf-8")
    assert '#define MyAppMutex "Local\\NetSpeedTray_SingleInstanceMutex"' in iss
    assert '#define MyLegacyAppMutex "Global\\NetSpeedTray_SingleInstanceMutex"' in iss
    assert "AppMutex={#MyAppMutex},{#MyLegacyAppMutex}" in iss
    body = iss[iss.index("function IsAppRunning(): Boolean;"):]
    body = body[:body.index("\nend;")]
    assert "{#MyAppMutex}" in body and "{#MyLegacyAppMutex}" in body and "AnyAppProcess" in body
