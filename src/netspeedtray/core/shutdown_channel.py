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

A second message on the same marked window, ``ACTIVATE_MESSAGE_NAME`` (#234): a second launch of
the app posts it (``request_activate``) so the running widget shows itself again, then exits 0.

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
# Added by the updater's relaunch (update_applier.relaunch): this start is not the user asking for a
# window. Ignored by a first instance (and by builds before 2.2.2, which ignore unknown arguments).
RELAUNCH_FLAG = "--relaunch"

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
_AllowSetForegroundWindow = _user32.AllowSetForegroundWindow
_AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
_AllowSetForegroundWindow.restype = wintypes.BOOL

_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_kernel32.WaitForSingleObject.restype = wintypes.DWORD
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.GetStartupInfoW.argtypes = [ctypes.c_void_p]
_kernel32.GetStartupInfoW.restype = None

_message_id: Optional[int] = None
_activate_id: Optional[int] = None


def shutdown_message_id() -> int:
    """The system-wide atom for SHUTDOWN_MESSAGE_NAME (same value in every process). 0 on failure."""
    global _message_id
    if not _message_id:
        _message_id = int(_user32.RegisterWindowMessageW(constants.app.SHUTDOWN_MESSAGE_NAME))
    return _message_id


def activate_message_id() -> int:
    """The system-wide atom for ACTIVATE_MESSAGE_NAME ("show yourself", #234). 0 on failure."""
    global _activate_id
    if not _activate_id:
        _activate_id = int(_user32.RegisterWindowMessageW(constants.app.ACTIVATE_MESSAGE_NAME))
    return _activate_id


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


def _post(hwnd: int, message: int, wparam: int = 0, lparam: int = 0) -> bool:
    return bool(_user32.PostMessageW(hwnd, message, wparam, lparam))


def _allow_foreground(pid: int) -> bool:
    """Let ``pid`` take the foreground (D3). Only a process that may set the foreground itself can pass
    the right on - a launch from Start or Explorer can; without it the running app's panel would open
    behind the window the user was in, with a flashing taskbar button instead of focus."""
    return bool(_AllowSetForegroundWindow(pid))


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR), ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD), ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD), ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_void_p), ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE),
    ]


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]


_STARTF_USESTDHANDLES = 0x00000100
_STARTF_TITLEISLINKNAME = 0x00000800
_MONITOR_DEFAULTTONULL = 0
_user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
_user32.GetMonitorInfoW.restype = wintypes.BOOL
_user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
_user32.MonitorFromPoint.restype = wintypes.HANDLE


def _startup_info() -> _STARTUPINFOW:
    si = _STARTUPINFOW()
    si.cb = ctypes.sizeof(_STARTUPINFOW)
    _kernel32.GetStartupInfoW(ctypes.byref(si))
    return si


def _startup_link_name() -> Optional[str]:
    """The shortcut (.lnk) this process was started from, or None. The shell says so in STARTUPINFO
    (STARTF_TITLEISLINKNAME, lpTitle = the .lnk path) for a Start-menu entry, a taskbar pin, a desktop
    or Startup-folder shortcut; a Run-key start, a scheduled task or a double-clicked exe has none
    (proven with a real .lnk launched through Explorer, in and outside a folder named Startup)."""
    si = _startup_info()
    if not (si.dwFlags & _STARTF_TITLEISLINKNAME) or not si.lpTitle:
        return None
    return str(si.lpTitle)


def _monitor_info(hmonitor) -> Optional[_MONITORINFOEXW]:
    mi = _MONITORINFOEXW()
    mi.cbSize = ctypes.sizeof(_MONITORINFOEXW)
    if not hmonitor or not _user32.GetMonitorInfoW(hmonitor, ctypes.byref(mi)):
        return None
    return mi


def _startup_monitor_center() -> Optional[Tuple[int, int]]:
    """The centre (physical px) of the monitor the shell launched us on, or None. ShellExecuteEx passes
    its hMonitor in STARTUPINFO.hStdOutput when no std handles are set (SEE_MASK_HMONITOR - what the
    Start menu and the taskbar use); the handle is trusted only once GetMonitorInfoW accepts it."""
    si = _startup_info()
    if si.dwFlags & _STARTF_USESTDHANDLES or not si.hStdOutput:
        return None
    mi = _monitor_info(si.hStdOutput)
    if mi is None:
        return None
    r = mi.rcMonitor
    return (r.left + r.right) // 2, (r.top + r.bottom) // 2


def pack_point(x: int, y: int) -> int:
    """A screen point as an activate lParam (signed 16-bit halves, like MAKELPARAM). 0 = no hint - no
    monitor is ever centred on (0, 0)."""
    return ((int(y) & 0xFFFF) << 16) | (int(x) & 0xFFFF)


def unpack_point(lparam: int) -> Optional[Tuple[int, int]]:
    lparam = int(lparam or 0) & 0xFFFFFFFF
    if not lparam:
        return None
    x, y = lparam & 0xFFFF, (lparam >> 16) & 0xFFFF
    return (x - 0x10000 if x & 0x8000 else x), (y - 0x10000 if y & 0x8000 else y)


def monitor_device_at(x: int, y: int) -> Optional[str]:
    """The device name of the monitor at a physical screen point (what QScreen.name() reports on
    Windows, e.g. \\\\.\\DISPLAY2), or None."""
    try:
        hmon = _user32.MonitorFromPoint(wintypes.POINT(int(x), int(y)), _MONITOR_DEFAULTTONULL)
        mi = _monitor_info(hmon)
        return str(mi.szDevice) if mi is not None else None
    except Exception as e:
        logger.debug("MonitorFromPoint(%s, %s) failed: %s", x, y, e)
        return None


def _startup_folders() -> List[str]:
    """Exactly this user's and the all-users Startup folders (FOLDERID_Startup /
    FOLDERID_CommonStartup); a folder that can't be read is left out."""
    out: List[str] = []
    try:
        from win32com.shell import shell, shellcon
    except Exception as e:
        logger.debug("win32com.shell unavailable: %s", e)
        return out
    for fid in (shellcon.FOLDERID_Startup, shellcon.FOLDERID_CommonStartup):
        try:
            path = shell.SHGetKnownFolderPath(fid)
            if path:
                out.append(str(path))
        except Exception as e:
            logger.debug("Startup known folder unreadable: %s", e)
    return out


def _link_target(link: str) -> Optional[str]:
    """The path a .lnk points at (IShellLink, in-process, no network), or None when it can't be read."""
    try:
        import pythoncom
        from win32com.shell import shell
        try:
            pythoncom.CoInitialize()
        except Exception:
            pass
        sl = pythoncom.CoCreateInstance(shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER,
                                        shell.IID_IShellLink)
        sl.QueryInterface(pythoncom.IID_IPersistFile).Load(link)
        target = sl.GetPath(shell.SLGP_RAWPATH)[0]
        return str(target) if target else None
    except Exception as e:
        logger.debug("Could not read the shortcut's target: %s", e)
        return None


def _is_this_app(target: str) -> bool:
    """The shortcut starts NetSpeedTray: this very exe, or any NetSpeedTray.exe (a portable copy reached
    through an older Start entry is still the user asking for the app)."""
    target = os.path.expandvars(target)
    try:
        if os.path.exists(target) and os.path.samefile(target, sys.executable):
            return True
    except OSError:
        pass
    return os.path.basename(target).lower() == APP_EXE_NAME.lower()


APP_EXE_NAME = "NetSpeedTray.exe"


def launched_from_user_shortcut() -> bool:
    """True when this process was started from a shortcut to NetSpeedTray OUTSIDE the Startup folders -
    the Start menu, a taskbar pin, the desktop: the user asked, whenever that was (the #373 sign-in rule
    must never swallow it). A shortcut in exactly this user's or the all-users Startup folder is an
    autostart, not a request. False on any doubt - the Startup folders unreadable, a target that can't
    be read or is another program (a launcher's shortcut) - and the sign-in rule then decides."""
    try:
        link = _startup_link_name()
        if not link or not link.lower().endswith(".lnk"):
            return False
        startup = {os.path.normcase(os.path.normpath(f)) for f in _startup_folders() if f}
        if not startup:
            return False
        folder = os.path.normcase(os.path.normpath(os.path.dirname(link)))
        if folder in startup:
            return False
        target = _link_target(link)
        return bool(target) and _is_this_app(target)
    except Exception as e:
        logger.debug("Could not read how this process was started: %s", e)
        return False


def activation_kind(argv: Optional[Sequence[str]] = None) -> int:
    """The wParam a second launch sends (constants.app.ACTIVATE_WPARAM_*): the updater's relaunch, an
    explicit launch from a shortcut, a duplicate autostart at sign-in (#373), or a plain user launch."""
    args = list(sys.argv[1:] if argv is None else argv)
    if RELAUNCH_FLAG in args:
        return constants.app.ACTIVATE_WPARAM_RELAUNCH
    if launched_from_user_shortcut():
        return constants.app.ACTIVATE_WPARAM_USER
    if is_sign_in_duplicate():
        return constants.app.ACTIVATE_WPARAM_SIGN_IN_DUPLICATE
    return constants.app.ACTIVATE_WPARAM_USER


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
    # The second launch's "show yourself" (#234): an unelevated second launch must reach an elevated
    # instance too. It can only make the widget visible - nothing a same-user process can't ask for.
    act = activate_message_id()
    act_allowed = _allow_through_uipi(hwnd, act) if act else False
    logger.debug("Shutdown channel armed on HWND %s (marked=%s, uipi_allowed=%s, activate_allowed=%s).",
                 hwnd, marked, allowed, act_allowed)
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


def _other_user_pids() -> Optional[set]:
    """PIDs of processes that provably belong to ANOTHER user: WTSEnumerateProcesses reported a SID and
    it is not ours. A process it reports with NO SID (a token it could not open - an ELEVATED instance
    of this very user, seen from an unelevated second launch) is not in the set: dropping those made
    the second launch miss an elevated widget (#440 review M1). None when nothing can be listed."""
    try:
        import win32api
        import win32con
        import win32security
        import win32ts
        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        try:
            me = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        finally:
            token.Close()
        my_sid = win32security.ConvertSidToStringSid(me)
        return {int(pid) for _session, pid, _name, sid in win32ts.WTSEnumerateProcesses(
                    win32ts.WTS_CURRENT_SERVER_HANDLE, 1, 0)
                if sid is not None and win32security.ConvertSidToStringSid(sid) != my_sid}
    except Exception as e:
        logger.debug("Could not list this user's processes: %s", e)
        return None


class _WTSINFOW(ctypes.Structure):
    """wtsapi32.h WTSINFOW (WTSQuerySessionInformationW, WTSSessionInfo). The times are FILETIMEs."""
    _fields_ = [
        ("State", ctypes.c_int),
        ("SessionId", wintypes.DWORD),
        ("IncomingBytes", wintypes.DWORD),
        ("OutgoingBytes", wintypes.DWORD),
        ("IncomingFrames", wintypes.DWORD),
        ("OutgoingFrames", wintypes.DWORD),
        ("IncomingCompressedBytes", wintypes.DWORD),
        ("OutgoingCompressedBytes", wintypes.DWORD),
        ("WinStationName", wintypes.WCHAR * 32),    # WINSTATIONNAME_LENGTH
        ("Domain", wintypes.WCHAR * 17),            # DOMAIN_LENGTH
        ("UserName", wintypes.WCHAR * 21),          # USERNAME_LENGTH + 1
        ("ConnectTime", ctypes.c_longlong),
        ("DisconnectTime", ctypes.c_longlong),
        ("LastInputTime", ctypes.c_longlong),
        ("LogonTime", ctypes.c_longlong),
        ("CurrentTime", ctypes.c_longlong),
    ]


_WTS_CURRENT_SERVER_HANDLE = None
_WTS_CURRENT_SESSION = 0xFFFFFFFF
_WTS_SESSION_INFO = 24          # WTS_INFO_CLASS.WTSSessionInfo
_FILETIME_TICKS_PER_S = 10_000_000


_wts_api: Optional[Tuple[object, object]] = None


def _wts_functions():
    """(WTSQuerySessionInformationW, WTSFreeMemory), loaded and typed once."""
    global _wts_api
    if _wts_api is None:
        wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)
        query = wtsapi32.WTSQuerySessionInformationW
        query.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_int,
                          ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
        query.restype = wintypes.BOOL
        free = wtsapi32.WTSFreeMemory
        free.argtypes = [ctypes.c_void_p]
        free.restype = None
        _wts_api = (query, free)
    return _wts_api


def _session_seconds_since_logon() -> Optional[float]:
    """Seconds since this session signed in, from ONE WTSSessionInfo query (CurrentTime - LogonTime, so
    no clock of ours is involved). None when it can't be told (any failure, a zero LogonTime)."""
    query, free = _wts_functions()
    buf = ctypes.c_void_p()
    size = wintypes.DWORD()
    if not query(_WTS_CURRENT_SERVER_HANDLE, _WTS_CURRENT_SESSION, _WTS_SESSION_INFO,
                 ctypes.byref(buf), ctypes.byref(size)) or not buf.value:
        return None
    try:
        if size.value < ctypes.sizeof(_WTSINFOW):
            return None
        info = _WTSINFOW.from_address(buf.value)
        if info.LogonTime <= 0 or info.CurrentTime <= 0:
            return None
        return (info.CurrentTime - info.LogonTime) / _FILETIME_TICKS_PER_S
    finally:
        free(buf)


def is_sign_in_duplicate() -> bool:
    """A second launch's own verdict (#373): True when this session signed in less than
    SIGN_IN_DUPLICATE_WINDOW_S ago - a duplicate autostart, not the user asking for the widget. Any
    failure (or a clock set back since logon) is a normal user launch: False."""
    try:
        since = _session_seconds_since_logon()
        if since is None or since < 0:
            return False
        return since < constants.app.SIGN_IN_DUPLICATE_WINDOW_S
    except Exception as e:
        logger.debug("Could not read the session's logon time: %s", e)
        return False


def request_activate(argv: Optional[Sequence[str]] = None) -> bool:
    """
    A second launch (the per-user instance mutex is taken, #234): ask THIS user's running widget to show
    itself again - un-hide, re-dock, re-seat above the taskbar, back to its docked spot if off-screen -
    instead of exiting silently. EnumWindows only sees our own desktop, and a marked window of a process
    known to be another user's (a "Run as different user" copy) is skipped, matching the per-user
    mutex; one whose owner can't be told (an elevated instance of ours, or no listing at all) is
    asked anyway - it can only become visible.

    The wParam says who is asking (``activation_kind``): ACTIVATE_WPARAM_RELAUNCH for the updater's
    relaunch (``--relaunch`` in ``argv``, default sys.argv); ACTIVATE_WPARAM_USER for a launch from a
    shortcut outside the Startup folders (D3: the user asked, even right after sign-in);
    ACTIVATE_WPARAM_SIGN_IN_DUPLICATE when this process starts within SIGN_IN_DUPLICATE_WINDOW_S of the
    session's sign-in (#373, ``is_sign_in_duplicate``); else ACTIVATE_WPARAM_USER. A USER request opens
    the "running" panel, so the running process is first allowed to take the foreground
    (``_allow_foreground``) - a failure there only costs focus, never the post.

    True if a running instance was signalled. Never waits: the second process exits right after.
    """
    msg = activate_message_id()
    if not msg:
        logger.warning("RegisterWindowMessageW(%s) failed.", constants.app.ACTIVATE_MESSAGE_NAME)
        return False
    targets = find_running_instances()
    if targets:
        others = _other_user_pids()
        if others:
            targets = [(hwnd, pid) for hwnd, pid in targets if pid not in others]
    if not targets:
        logger.info("Second launch: no running widget of this user on this desktop to bring back.")
        return False
    wparam = activation_kind(argv)
    why = {constants.app.ACTIVATE_WPARAM_SIGN_IN_DUPLICATE: " (a duplicate start at sign-in)",
           constants.app.ACTIVATE_WPARAM_RELAUNCH: " (the updater's relaunch)"}.get(wparam, "")
    # lParam: the centre of the monitor the shell launched us on (a Start menu or a pin on a secondary
    # taskbar), so the panel opens there; 0 = no hint. Only a user request opens a window.
    lparam = 0
    if wparam == constants.app.ACTIVATE_WPARAM_USER:
        try:
            centre = _startup_monitor_center()
            if centre is not None:
                lparam = pack_point(*centre)
        except Exception as e:
            logger.debug("No launch monitor hint: %s", e)
    sent = False
    for hwnd, pid in targets:
        if wparam == constants.app.ACTIVATE_WPARAM_USER:
            try:
                if not _allow_foreground(pid):
                    logger.debug("AllowSetForegroundWindow(%s) refused (err %s).", pid,
                                 ctypes.get_last_error())
            except Exception as e:
                logger.debug("AllowSetForegroundWindow(%s) failed: %s", pid, e)
        posted = _post(hwnd, msg, wparam, lparam) if lparam else _post(hwnd, msg, wparam)
        if posted:
            logger.info("Second launch: asked the running widget (pid %s) to show itself%s.", pid, why)
            sent = True
        else:
            logger.warning("Second launch: PostMessage to HWND %s (pid %s) failed (err %s).",
                           hwnd, pid, ctypes.get_last_error())
    return sent


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
