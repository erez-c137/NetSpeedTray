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
EVENT_SYSTEM_MOVESIZEEND = 0x000B # Fired when a window finishes moving or resizing
WINEVENT_OUTOFCONTEXT = 0x0000


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

    def __init__(self, event_to_watch: int, hwnd_to_watch: int = 0, debounce_ms: Optional[int] = None, parent=None):
        """
        Initializes the hook.
        """
        super().__init__(parent)
        threading.Thread.__init__(self)
        self.daemon = True
        self._hook = None
        self._thread_id = None
        self._is_running = False
        
        self.event_to_watch = event_to_watch
        self.hwnd_to_watch = hwnd_to_watch
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
        self._is_running = True
        process_id = 0
        thread_id = 0
        if self.hwnd_to_watch != 0:
            try:
                thread_id, process_id = win32process.GetWindowThreadProcessId(self.hwnd_to_watch)
            except Exception as e:
                logger.error(f"Could not get process/thread for HWND {self.hwnd_to_watch}: {e}")
                self._is_running = False
                return
        self._hook = windll.user32.SetWinEventHook(
            self.event_to_watch, self.event_to_watch, 0, self.c_callback,
            process_id, thread_id, WINEVENT_OUTOFCONTEXT
        )
        if self._hook == 0:
            logger.error(f"SetWinEventHook failed for event {self.event_to_watch}.")
            self._is_running = False
            return
        self._thread_id = windll.kernel32.GetCurrentThreadId()
        logger.info("WinEventHook started successfully for event %s in thread %d.", self.event_to_watch, self._thread_id)
        msg = wintypes.MSG()
        while self._is_running and windll.user32.GetMessageW(byref(msg), 0, 0, 0) != 0:
            windll.user32.TranslateMessage(byref(msg))
            windll.user32.DispatchMessageW(byref(msg))
        windll.user32.UnhookWinEvent(self._hook)
        logger.info("WinEventHook stopped and unhooked for event %s.", self.event_to_watch)

    def callback(self, hWinEventHook, event, hwnd, idObject, idChild, dwEventThread, dwmsEventTime):
        """
        The C-compatible callback. Runs on the worker thread.
        Its ONLY job is to emit the internal signal to safely cross the thread boundary.
        """
        self._internal_event_received.emit(hwnd)

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
        """Stops the event listener thread."""
        if not self._is_running or self._thread_id is None:
            return
        self._is_running = False
        if self._debounced_timer.isActive():
            self._debounced_timer.stop()
        WM_QUIT = 0x0012
        windll.user32.PostThreadMessageW(self._thread_id, wintypes.UINT(WM_QUIT), 0, 0)