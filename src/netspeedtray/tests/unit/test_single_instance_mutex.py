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
    with create, get_last, mock.patch.object(monitor.win32api, "CloseHandle") as close:
        with SingleInstanceChecker() as checker:
            assert checker.mutex is mock.sentinel.mutex_handle
        close.assert_called_once_with(mock.sentinel.mutex_handle)


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
