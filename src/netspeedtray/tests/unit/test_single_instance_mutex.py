"""SingleInstanceChecker: a second launch must exit quietly whenever another instance holds the mutex.

Regression: with an ELEVATED instance running, an unelevated launch could not open the mutex the
elevated process created (its default DACL admits only Administrators/SYSTEM, and its High integrity
label blocks Medium-IL callers), so CreateMutexW failed with ERROR_ACCESS_DENIED. That surfaced as a
"critical error" dialog instead of the quiet already-running exit. ACCESS_DENIED on a named mutex only
happens when the object already exists, so it means the same thing as ERROR_ALREADY_EXISTS.

Since 2.2.0 the rule is one instance per USER: the mutex name carries the user's SID (in Global\\,
so the same user's second session sees it), and the old shared Global\\ name is kept for the
transition - claimed when free, and when held, a launch gives way only to the SAME user's process.
"""
import os
import uuid
from unittest import mock

import pytest
import pywintypes
import winerror

import monitor
from monitor import AlreadyRunningError, SingleInstanceChecker

MY_SID = "S-1-5-21-1-2-3-1001"
OTHER_SID = "S-1-5-21-1-2-3-1002"


def _patch_create(*, side_effect=None, last_error=0):
    handle = mock.sentinel.mutex_handle
    create = mock.patch.object(monitor.win32event, "CreateMutex",
                               side_effect=side_effect, return_value=handle)
    get_last = mock.patch.object(monitor.win32api, "GetLastError", return_value=last_error)
    return create, get_last


def _unique(tag: str) -> str:
    # Unique per call: other pytest sessions on this machine share the kernel namespace.
    return f"Local\\NetSpeedTray_test_{tag}_{os.getpid()}_{uuid.uuid4().hex}"


def test_first_instance_acquires_mutex():
    create, get_last = _patch_create(last_error=0)
    with create as created, get_last, mock.patch.object(monitor.win32api, "CloseHandle") as close:
        with SingleInstanceChecker() as checker:
            assert checker.mutex is mock.sentinel.mutex_handle
            assert checker.legacy_mutex is mock.sentinel.mutex_handle, "free legacy name not claimed"
        assert close.call_count == 2
    names = [c.args[2] for c in created.call_args_list]
    assert names == [monitor._instance_mutex_name(), monitor.constants.app.LEGACY_MUTEX_NAME]


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


def test_real_mutex_denied_by_dacl_is_quiet(monkeypatch):
    """End to end on the real kernel object: a mutex created with a DACL that denies everyone (the
    shape an elevated owner presents to an unelevated launch) must read as 'already running'."""
    import win32event
    import win32security

    name = _unique("denied")
    sa = win32security.SECURITY_ATTRIBUTES()
    sa.SECURITY_DESCRIPTOR = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        "D:(D;;GA;;;WD)", win32security.SDDL_REVISION_1)
    owner = win32event.CreateMutex(sa, False, name)
    try:
        monkeypatch.setattr(monitor, "_instance_mutex_name", lambda: name)
        with pytest.raises(AlreadyRunningError):
            SingleInstanceChecker()
    finally:
        monitor.win32api.CloseHandle(owner)


# --- one instance per user (2.2.0) -------------------------------------------------------------

def test_the_instance_mutex_is_global_and_per_user():
    name = monitor._instance_mutex_name()
    assert name == monitor.constants.app.MUTEX_NAME_PREFIX + monitor._current_user_sid()
    assert name.startswith("Global\\NetSpeedTray_Instance_S-1-")
    assert monitor.constants.app.LEGACY_MUTEX_NAME == "Global\\NetSpeedTray_SingleInstanceMutex"


@pytest.fixture
def names(monkeypatch):
    """Real kernel objects under unique test names (never the app's real ones)."""
    instance, legacy = _unique("i"), _unique("g")
    monkeypatch.setattr(monitor, "_instance_mutex_name", lambda: instance)
    monkeypatch.setattr(monitor.constants.app, "LEGACY_MUTEX_NAME", legacy)
    monkeypatch.setattr(monitor, "_LEGACY_RECHECK_DELAY_S", 0.0)
    return instance, legacy


def _hold(name):
    import win32event
    return win32event.CreateMutex(None, False, name)


def test_same_user_second_session_is_blocked_by_the_per_user_name(names):
    """Same user, two sessions: the second launch sees the first's (Global\\, per-user) mutex."""
    instance, _legacy = names
    first = _hold(instance)
    try:
        with pytest.raises(AlreadyRunningError):
            SingleInstanceChecker()
    finally:
        monitor.win32api.CloseHandle(first)


def test_two_users_get_two_names():
    with mock.patch.object(monitor, "_current_user_sid", return_value=MY_SID):
        a = monitor._instance_mutex_name()
    with mock.patch.object(monitor, "_current_user_sid", return_value=OTHER_SID):
        b = monitor._instance_mutex_name()
    assert a != b and a.endswith(MY_SID) and b.endswith(OTHER_SID)


def test_legacy_name_held_by_another_users_old_instance_does_not_block(names, monkeypatch):
    _instance, legacy = names
    other = _hold(legacy)
    monkeypatch.setattr(monitor, "_legacy_instance_of_this_user", lambda: False)
    try:
        with SingleInstanceChecker() as checker:
            assert checker.mutex and checker.legacy_mutex is None
    finally:
        monitor.win32api.CloseHandle(other)


def test_this_users_old_instance_blocks_and_releases_ours(names, monkeypatch):
    instance, legacy = names
    old = _hold(legacy)
    monkeypatch.setattr(monitor, "_legacy_instance_of_this_user", lambda: True)
    try:
        with pytest.raises(AlreadyRunningError):
            SingleInstanceChecker()
        probe = _hold(instance)                             # ours was released on the way out
        assert monitor.win32api.GetLastError() != winerror.ERROR_ALREADY_EXISTS
        monitor.win32api.CloseHandle(probe)
    finally:
        monitor.win32api.CloseHandle(old)


def test_an_old_copy_started_later_sees_the_legacy_name_held(names):
    """A 2.1.x copy only checks the shared Global\\ name: the new instance must be holding it."""
    _instance, legacy = names
    with SingleInstanceChecker():
        h = _hold(legacy)
        assert monitor.win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS
        monitor.win32api.CloseHandle(h)


# --- the same-user process scan ------------------------------------------------------------------

class _FakeSid:
    def __init__(self, s):
        self.s = s


@pytest.fixture
def fake_wts(monkeypatch):
    import win32security
    import win32ts
    monkeypatch.setattr(monitor, "_current_user_sid", lambda: MY_SID)
    monkeypatch.setattr(monitor, "_LEGACY_RECHECK_DELAY_S", 0.0)
    monkeypatch.setattr(monitor, "_LEGACY_RECHECK_WINDOW_S", 0.0)
    monkeypatch.setattr(monitor, "_any_established", lambda pids: False)   # fake PIDs may be real
    monkeypatch.setattr(win32security, "ConvertSidToStringSid", lambda sid: sid.s)
    state = {"procs": [], "cmdlines": {}}
    monkeypatch.setattr(win32ts, "WTSEnumerateProcesses",
                        lambda *a: [(sess, pid, name, _FakeSid(sid) if sid else None)
                                    for sess, pid, name, sid in state["procs"]])

    class _P:
        def __init__(self, pid):
            self.pid = pid

        def cmdline(self):
            if self.pid not in state["cmdlines"]:
                raise PermissionError("elevated")
            return state["cmdlines"][self.pid]
    import psutil
    monkeypatch.setattr(psutil, "Process", _P)
    return state


def test_the_same_users_instance_in_another_session_counts(fake_wts):
    fake_wts["procs"] = [(2, 50, "NetSpeedTray.exe", MY_SID)]
    assert monitor._legacy_instance_of_this_user() is True


def test_another_users_instance_in_this_session_does_not_count(fake_wts):
    fake_wts["procs"] = [(1, 51, "NetSpeedTray.exe", OTHER_SID), (0, 52, "NetSpeedTray.exe", None),
                         (1, os.getpid(), "NetSpeedTray.exe", MY_SID), (1, 53, "notepad.exe", MY_SID)]
    assert monitor._legacy_instance_of_this_user() is False


def test_headless_runs_are_ignored_but_unreadable_ones_count(fake_wts):
    fake_wts["procs"] = [(1, 70, "NetSpeedTray.exe", MY_SID)]
    fake_wts["cmdlines"] = {70: ["NetSpeedTray.exe", "--apply-update", "C:\\x"]}
    assert monitor._legacy_instance_of_this_user() is False
    fake_wts["procs"] = [(1, 71, "NETSPEEDTRAY.EXE", MY_SID)]           # elevated: unreadable
    assert monitor._legacy_instance_of_this_user() is True


def test_an_old_copy_that_exits_during_the_recheck_does_not_block(fake_wts, monkeypatch):
    """Old + new launched together while another user holds the legacy name: the old one exits on
    seeing it held; the new one must not exit too because it saw the old one for an instant. At a
    cold logon that exit can take seconds, so it is polled, not checked once."""
    polls = []

    def sleep(s):
        polls.append(s)
        if len(polls) == 4:
            fake_wts["procs"] = []                                     # gone on the 4th poll (2 s)
    clock = [0.0]
    monkeypatch.setattr(monitor, "_LEGACY_RECHECK_DELAY_S", 0.5)
    monkeypatch.setattr(monitor, "_LEGACY_RECHECK_WINDOW_S", 5.0)
    monkeypatch.setattr(monitor.time, "sleep", lambda s: (sleep(s), clock.__setitem__(0, clock[0] + s)))
    monkeypatch.setattr(monitor.time, "monotonic", lambda: clock[0])
    fake_wts["procs"] = [(1, 80, "NetSpeedTray.exe", MY_SID)]
    assert monitor._legacy_instance_of_this_user() is False
    assert polls == [0.5] * 4


def test_a_copy_still_there_after_the_window_blocks(fake_wts, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(monitor, "_LEGACY_RECHECK_DELAY_S", 0.5)
    monkeypatch.setattr(monitor, "_LEGACY_RECHECK_WINDOW_S", 5.0)
    monkeypatch.setattr(monitor.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    monkeypatch.setattr(monitor.time, "monotonic", lambda: clock[0])
    fake_wts["procs"] = [(1, 81, "NetSpeedTray.exe", MY_SID)]
    assert monitor._legacy_instance_of_this_user() is True
    assert 5.0 <= clock[0] <= 5.5, "bounded: about the window, no longer"


def test_an_established_old_copy_blocks_without_waiting(fake_wts, monkeypatch):
    monkeypatch.setattr(monitor, "_any_established", lambda pids: True)
    monkeypatch.setattr(monitor.time, "sleep", lambda s: pytest.fail("waited for an established copy"))
    fake_wts["procs"] = [(1, 82, "NetSpeedTray.exe", MY_SID)]
    assert monitor._legacy_instance_of_this_user() is True


def test_established_means_started_well_before_us():
    import psutil
    parent = os.getppid()
    try:
        older = psutil.Process(parent).create_time() <= psutil.Process(os.getpid()).create_time() - 10
    except psutil.Error:
        pytest.skip("parent process not readable")
    assert monitor._any_established([parent]) is older
    assert monitor._any_established([os.getpid()]) is False


def test_without_wts_the_psutil_scan_answers(monkeypatch):
    monkeypatch.setattr(monitor, "_current_user_sid", lambda: MY_SID)
    monkeypatch.setattr(monitor, "_same_user_instances_wts",
                        mock.Mock(side_effect=ImportError("No module named win32ts")))
    monkeypatch.setattr(monitor, "_same_user_instances_psutil", lambda sid: [])
    assert monitor._legacy_instance_of_this_user() is False


def test_the_psutil_scan_matches_by_user_then_by_session(monkeypatch):
    import psutil
    import win32security
    monkeypatch.setattr(win32security, "ConvertStringSidToSid", lambda s: s)
    monkeypatch.setattr(win32security, "LookupAccountSid", lambda _h, _sid: ("me", "DOM", 1))
    monkeypatch.setattr(monitor, "_is_app_process",
                        lambda pid, name: pid != os.getpid() and name == "NetSpeedTray.exe")

    class _Proc:
        def __init__(self, pid, name, user):
            self.info = {"pid": pid, "name": name}
            self._user = user

        def username(self):
            if self._user is None:
                raise psutil.AccessDenied(self.info["pid"])
            return self._user
    procs = [_Proc(90, "NetSpeedTray.exe", "DOM\\me"),          # ours, other session: counted
             _Proc(91, "NetSpeedTray.exe", "DOM\\someone"),     # another user's: not counted
             _Proc(0x7FFFFFF0, "NetSpeedTray.exe", None),       # unreadable, session unknown: not
             _Proc(os.getpid(), "NetSpeedTray.exe", "DOM\\me")]  # this process
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter(procs))
    assert monitor._same_user_instances_psutil(MY_SID) == [90]
    # An unreadable process in OUR session counts (an elevated old instance of ours).
    unreadable_here = _Proc(os.getppid(), "NetSpeedTray.exe", None)
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter([unreadable_here]))
    assert monitor._same_user_instances_psutil(MY_SID) == [os.getppid()]


def test_only_when_no_scan_works_is_an_instance_assumed(monkeypatch):
    monkeypatch.setattr(monitor, "_current_user_sid", lambda: MY_SID)
    boom = mock.Mock(side_effect=OSError("no"))
    monkeypatch.setattr(monitor, "_same_user_instances_wts", boom)
    monkeypatch.setattr(monitor, "_same_user_instances_psutil", boom)
    assert monitor._legacy_instance_of_this_user() is True


def test_the_spec_bundles_the_scan_modules():
    from pathlib import Path
    spec = (Path(monitor.__file__).resolve().parents[1] / "build" / "netspeedtray.spec").read_text(
        encoding="utf-8")
    assert "'win32ts'" in spec and "'win32security'" in spec


def test_setup_finds_both_old_and_new_instances():
    from pathlib import Path
    iss = (Path(monitor.__file__).resolve().parents[1] / "build" / "setup.iss").read_text(
        encoding="utf-8")
    assert '#define MyLegacyAppMutex "Global\\NetSpeedTray_SingleInstanceMutex"' in iss
    assert "AppMutex={#MyLegacyAppMutex}" in iss
    body = iss[iss.index("function IsAppRunning(): Boolean;"):]
    body = body[:body.index("\nend;")]
    assert "{#MyLegacyAppMutex}" in body and "AnyAppProcess()" in body
