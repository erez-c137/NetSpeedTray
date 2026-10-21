"""
Windows System Event Hook Utility.

Provides a threaded listener for system-wide WinEvents, allowing the main
application to react to events like foreground window changes without polling.
"""

import logging
import threading
import ctypes
from ctypes import wintypes, windll, byref
from typing import Optional

import win32process

from PyQt6.QtCore import QObject, pyqtSignal, QTimer

logger = logging.getLogger("NetSpeedTray.WinEventHook")

# Ctypes definitions for the Windows API
WINEVENTPROC = ctypes.WINFUNCTYPE(
    None,
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.HWND,
    wintypes.LONG,
    wintypes.LONG,
    wintypes.DWORD,
    wintypes.DWORD
)

# WinEvent Constants
EVENT_SYSTEM_FOREGROUND = 0x0003
EVENT_OBJECT_LOCATIONCHANGE = 0x800B
# A window's children changed Z-order. For top-level windows the source is the desktop
# window (#32769): explorer fires it right before it raises the taskbar over the widget.
EVENT_OBJECT_REORDER = 0x8004
EVENT_SYSTEM_MOVESIZEEND = 0x000B # Fired when a window finishes moving or resizing
WINEVENT_OUTOFCONTEXT = 0x0000
# Drop events raised by our own process (e.g. the widget's own re-seat echoing back as a
# reorder). The caller's periodic safety net still covers anything our own UI does.
WINEVENT_SKIPOWNPROCESS = 0x0002
_WM_QUIT = 0x0012
_PM_NOREMOVE = 0x0000
# How long stop() waits for a just-started hook thread to install its hook and message
# queue, so it can always be told to unhook (a SYSTEM-WIDE hook must never leak).
_THREAD_READY_TIMEOUT_S = 2.0

# A private user32 handle with explicit prototypes: HWINEVENTHOOK is pointer-sized, so the
# default int restype would truncate it on 64-bit Python. Private, so the global
# windll.user32 prototypes other modules rely on are untouched.
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_SetWinEventHook = _user32.SetWinEventHook
_SetWinEventHook.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, WINEVENTPROC,
                             wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
_SetWinEventHook.restype = wintypes.HANDLE
_UnhookWinEvent = _user32.UnhookWinEvent
_UnhookWinEvent.argtypes = [wintypes.HANDLE]
_UnhookWinEvent.restype = wintypes.BOOL
_PeekMessageW = _user32.PeekMessageW
_PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT,
                          wintypes.UINT, wintypes.UINT]
_PeekMessageW.restype = wintypes.BOOL
_PostThreadMessageW = _user32.PostThreadMessageW
_PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_PostThreadMessageW.restype = wintypes.BOOL


class WinEventHook(QObject, threading.Thread):
    """
    Listens for a specific WinEvent in a separate thread and emits signals.
    This implementation is thread-safe, using signals and slots to communicate
    from the hook's worker thread back to the main GUI thread.
    """
    event_triggered = pyqtSignal(int)
    event_triggered_debounced = pyqtSignal(int)

    # Internal signal for safe cross-thread communication
    _internal_event_received = pyqtSignal(int)

    def __init__(self, event_to_watch: int, hwnd_to_watch: int = 0, debounce_ms: Optional[int] = None,
                 parent=None, source_hwnd_filter: Optional[int] = None,
                 skip_own_process: bool = False):
        """
        Initializes the hook.

        source_hwnd_filter: when set, events whose source HWND differs are dropped on the hook
        thread, before they cross into Qt - for a system-wide hook (hwnd_to_watch=0) on a
        frequent event where only one source matters (EVENT_OBJECT_REORDER on the desktop).
        skip_own_process: add WINEVENT_SKIPOWNPROCESS (ignore events our own process raises).
        """
        super().__init__(parent)
        threading.Thread.__init__(self)
        self.daemon = True
        self._hook = None
        self._thread_id = None
        self._is_running = False
        # Set by stop(); checked by run() before and after it installs the hook, so a stop()
        # that lands while the thread is still starting can never be lost.
        self._stop_requested = False
        # Set by run() once the hook + message queue exist (or it gave up): stop() waits on it.
        self._ready = threading.Event()
        self._flags = WINEVENT_OUTOFCONTEXT | (WINEVENT_SKIPOWNPROCESS if skip_own_process else 0)

        self.event_to_watch = event_to_watch
        self.hwnd_to_watch = hwnd_to_watch
        self.source_hwnd_filter = source_hwnd_filter
        self.c_callback = WINEVENTPROC(self.callback)

        self._debounced_timer = QTimer()
        self._debounced_timer.setSingleShot(True)
        self._last_hwnd = 0
        if debounce_ms is not None:
            self._debounced_timer.setInterval(debounce_ms)
            self._debounced_timer.timeout.connect(self._emit_debounced_signal)

        # Connect the internal signal to the main-thread handler slot
        self._internal_event_received.connect(self._handle_event_on_main_thread)

    def run(self):
        """The main loop for the hook thread."""
        try:
            self._run_hook()
        finally:
            self._is_running = False
            self._ready.set()   # never leave stop() waiting

    def _run_hook(self):
        process_id = 0
        thread_id = 0
        if self.hwnd_to_watch != 0:
            try:
                thread_id, process_id = win32process.GetWindowThreadProcessId(self.hwnd_to_watch)
            except Exception as e:
                logger.error(f"Could not get process/thread for HWND {self.hwnd_to_watch}: {e}")
                return
        if self._stop_requested:
            return
        self._hook = _SetWinEventHook(
            self.event_to_watch, self.event_to_watch, None, self.c_callback,
            process_id, thread_id, self._flags
        )
        if not self._hook:
            logger.error(f"SetWinEventHook failed for event {self.event_to_watch}.")
            return
        try:
            # Force this thread's message queue into existence BEFORE publishing the thread
            # id, so stop()'s WM_QUIT can never be posted to a thread without a queue (and
            # silently dropped, leaving the hook installed).
            msg = wintypes.MSG()
            _PeekMessageW(byref(msg), None, 0, 0, _PM_NOREMOVE)
            self._thread_id = windll.kernel32.GetCurrentThreadId()
            self._is_running = True
            self._ready.set()
            logger.debug("WinEventHook started successfully for event %s in thread %d.", self.event_to_watch, self._thread_id)
            while not self._stop_requested and windll.user32.GetMessageW(byref(msg), 0, 0, 0) > 0:
                windll.user32.TranslateMessage(byref(msg))
                windll.user32.DispatchMessageW(byref(msg))
        finally:
            _UnhookWinEvent(self._hook)
            self._hook = None
            logger.debug("WinEventHook stopped and unhooked for event %s.", self.event_to_watch)

    def callback(self, hWinEventHook, event, hwnd, idObject, idChild, dwEventThread, dwmsEventTime):
        """
        The C-compatible callback. Runs on the worker thread.
        Its ONLY job is to emit the internal signal to safely cross the thread boundary
        (after the optional source filter).
        """
        if self.source_hwnd_filter is not None and (hwnd or 0) != self.source_hwnd_filter:
            return
        self._internal_event_received.emit(hwnd or 0)

    def _handle_event_on_main_thread(self, hwnd: int):
        """
        This slot runs on the main GUI thread, thanks to the signal/slot connection.
        It is now safe to interact with QTimer and emit public signals.
        """
        # Emit the raw, immediate signal safely.
        self.event_triggered.emit(hwnd)

        # If debouncing is enabled, store the latest HWND and restart the timer safely.
        if self._debounced_timer.interval() > 0:
            self._last_hwnd = hwnd
            self._debounced_timer.start()

    def _emit_debounced_signal(self):
        """Fires on the main thread after the debounce timer finishes."""
        self.event_triggered_debounced.emit(self._last_hwnd)

    def stop(self):
        """
        Stops the event listener thread, and with it the hook. Safe at any point: when the
        thread was started but has not finished installing the hook yet, this waits (bounded)
        for it, so a start() followed at once by stop() still unhooks - leaking a system-wide
        hook would keep a thread and a cross-process event stream alive for the whole session.
        """
        self._stop_requested = True
        if self._debounced_timer.isActive():
            self._debounced_timer.stop()
        if self.ident is None:
            return   # never started: nothing to unhook
        if self._thread_id is None:
            self._ready.wait(_THREAD_READY_TIMEOUT_S)
        if self._thread_id is not None and self.is_alive():
            _PostThreadMessageW(self._thread_id, _WM_QUIT, 0, 0)
        self._is_running = False
