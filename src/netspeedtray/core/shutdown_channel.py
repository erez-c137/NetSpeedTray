"""
Graceful-shutdown channel between the installer and the running app.

Receiver: the widget marks its native window with a property (``SHUTDOWN_WINDOW_PROP``), lets the
registered ``SHUTDOWN_MESSAGE_NAME`` message through UIPI, and on receipt runs the same clean quit as
tray Exit (stop threads, flush the DB, save config) - see ``NetworkSpeedWidget.nativeEvent``.

Senders: setup.iss's [Code] speaks the protocol itself (RegisterWindowMessageW, EnumWindows +
GetPropW, PostMessageW, wait for the process) before falling back to ``taskkill /F`` - it deliberately
does NOT run the installed exe: Setup is elevated and a custom install folder may be user-writable,
so executing ``{app}\\NetSpeedTray.exe`` there would hand admin to whoever replaced it. Keep its
``ShutdownMessageName`` / ``ShutdownWindowProp`` defines in step with the constants (a test pins them).
``NetSpeedTray.exe --shutdown [--timeout N]`` stays for manual and scripted use - headless, no
QApplication, no mutex. It enumerates top-level windows for the marked one (targeted, never
HWND_BROADCAST), posts the message and waits for that process to exit.

Exit codes: 0 = nothing running, or every instance exited in time; 1 = an instance is still running
(post refused or timed out - the caller should fall back); 2 = unexpected error.

Elevation (UIPI): an elevated sender can post to a medium-integrity window, but a medium sender's post
to an elevated window is dropped unless the receiver allows the message with
ChangeWindowMessageFilterEx - which ``arm_window`` does. Letting a same-user, lower-integrity process
ask the app to exit cleanly grants nothing it could not already do to an unelevated instance.
"""
from __future__ import annotations

import ctypes
import logging
import os
import sys
import time
from ctypes import wintypes
from typing import List, Optional, Sequence, Tuple

from netspeedtray import constants

logger = logging.getLogger("NetSpeedTray.ShutdownChannel")

EXIT_OK = 0
EXIT_TIMEOUT = 1
EXIT_ERROR = 2

SHUTDOWN_FLAG = "--shutdown"
TIMEOUT_FLAG = "--timeout"

_MSGFLT_ALLOW = 1
_SYNCHRONIZE = 0x00100000
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_WAIT_OBJECT_0 = 0

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
_user32.RegisterWindowMessageW.restype = wintypes.UINT
_user32.SetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.HANDLE]
_user32.SetPropW.restype = wintypes.BOOL
_user32.GetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
_user32.GetPropW.restype = wintypes.HANDLE
_user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.PostMessageW.restype = wintypes.BOOL
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.IsWindow.argtypes = [wintypes.HWND]
_user32.IsWindow.restype = wintypes.BOOL
_ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_user32.EnumWindows.argtypes = [_ENUMPROC, wintypes.LPARAM]
_user32.EnumWindows.restype = wintypes.BOOL
try:
    _ChangeWindowMessageFilterEx = _user32.ChangeWindowMessageFilterEx
    _ChangeWindowMessageFilterEx.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.DWORD, ctypes.c_void_p]
    _ChangeWindowMessageFilterEx.restype = wintypes.BOOL
except AttributeError:  # pre-Windows 7
    _ChangeWindowMessageFilterEx = None

_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_kernel32.WaitForSingleObject.restype = wintypes.DWORD
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL

_message_id: Optional[int] = None


def shutdown_message_id() -> int:
    """The system-wide atom for SHUTDOWN_MESSAGE_NAME (same value in every process). 0 on failure."""
    global _message_id
    if not _message_id:
        _message_id = int(_user32.RegisterWindowMessageW(constants.app.SHUTDOWN_MESSAGE_NAME))
    return _message_id


# --- Win32 seams (patched in unit tests) -----------------------------------------------------------

def _set_prop(hwnd: int) -> bool:
    return bool(_user32.SetPropW(hwnd, constants.app.SHUTDOWN_WINDOW_PROP, wintypes.HANDLE(1)))


def _has_target_prop(hwnd: int) -> bool:
    return bool(_user32.GetPropW(hwnd, constants.app.SHUTDOWN_WINDOW_PROP))


def _allow_through_uipi(hwnd: int, message: int) -> bool:
    if _ChangeWindowMessageFilterEx is None:
        return False
    return bool(_ChangeWindowMessageFilterEx(hwnd, message, _MSGFLT_ALLOW, None))


def _enum_top_level_windows() -> List[int]:
    found: List[int] = []

    def _cb(hwnd, _lparam):
        found.append(int(hwnd))
        return True

    _user32.EnumWindows(_ENUMPROC(_cb), 0)
    return found


def _window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD(0)
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def _post(hwnd: int, message: int) -> bool:
    return bool(_user32.PostMessageW(hwnd, message, 0, 0))


def _wait_for_exit(pid: int, hwnd: int, timeout: float) -> bool:
    """True once ``pid`` has exited (or ``hwnd`` is gone when the process cannot be opened)."""
    deadline = time.monotonic() + max(0.0, timeout)
    handle = _kernel32.OpenProcess(_SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        handle = _kernel32.OpenProcess(_SYNCHRONIZE, False, pid)
    if handle:
        try:
            ms = int(max(0.0, timeout) * 1000)
            return _kernel32.WaitForSingleObject(handle, ms) == _WAIT_OBJECT_0
        finally:
            _kernel32.CloseHandle(handle)
    # Could not open the process (exited already, or access denied): a process that has exited no
    # longer owns windows, so the window disappearing is the exit signal.
    logger.debug("OpenProcess(%s) failed (err %s); polling the window instead.", pid, ctypes.get_last_error())
    while True:
        if not hwnd or not _user32.IsWindow(hwnd):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


# --- receiver ------------------------------------------------------------------------------------

def arm_window(hwnd: int) -> bool:
    """Mark ``hwnd`` as the shutdown target and let the message through UIPI. True if marked."""
    if not hwnd:
        return False
    marked = _set_prop(hwnd)
    msg = shutdown_message_id()
    allowed = _allow_through_uipi(hwnd, msg) if msg else False
    logger.debug("Shutdown channel armed on HWND %s (marked=%s, uipi_allowed=%s).", hwnd, marked, allowed)
    return marked


# --- sender --------------------------------------------------------------------------------------

def find_running_instances() -> List[Tuple[int, int]]:
    """(hwnd, pid) of every marked window that belongs to another process."""
    own = os.getpid()
    out = []
    for hwnd in _enum_top_level_windows():
        try:
            if not _has_target_prop(hwnd):
                continue
            pid = _window_pid(hwnd)
        except Exception:
            continue
        if pid and pid != own:
            out.append((hwnd, pid))
    return out


def request_shutdown(timeout: float) -> int:
    msg = shutdown_message_id()
    if not msg:
        logger.error("RegisterWindowMessageW(%s) failed.", constants.app.SHUTDOWN_MESSAGE_NAME)
        return EXIT_ERROR
    targets = find_running_instances()
    if not targets:
        logger.info("Shutdown requested: no running NetSpeedTray instance found.")
        return EXIT_OK
    result = EXIT_OK
    for hwnd, pid in targets:
        if not _post(hwnd, msg):
            logger.warning("Shutdown: PostMessage to HWND %s (pid %s) failed (err %s).",
                           hwnd, pid, ctypes.get_last_error())
            result = EXIT_TIMEOUT
            continue
        logger.info("Shutdown: asked pid %s (HWND %s) to quit; waiting up to %.0f s.", pid, hwnd, timeout)
        started = time.monotonic()
        if _wait_for_exit(pid, hwnd, timeout):
            logger.info("Shutdown: pid %s exited cleanly after %.1f s.", pid, time.monotonic() - started)
        else:
            logger.warning("Shutdown: pid %s still running after %.0f s.", pid, timeout)
            result = EXIT_TIMEOUT
    return result


def _parse_timeout(argv: Sequence[str]) -> float:
    default = float(constants.app.SHUTDOWN_DEFAULT_TIMEOUT_S)
    if TIMEOUT_FLAG in argv:
        i = list(argv).index(TIMEOUT_FLAG)
        try:
            value = float(argv[i + 1])
            if value > 0:
                return value
        except (IndexError, ValueError):
            pass
    return default


def run_shutdown_cli(argv: Optional[Sequence[str]] = None) -> Optional[int]:
    """If ``--shutdown`` is present, signal the running app and return an exit code; else None."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if SHUTDOWN_FLAG not in argv:
        return None
    try:
        return request_shutdown(_parse_timeout(argv))
    except Exception as e:
        logger.error("Shutdown command failed: %s", e, exc_info=True)
        return EXIT_ERROR
