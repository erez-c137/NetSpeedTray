"""
Windows System Event Hook Utility.

Provides a threaded listener for system-wide WinEvents, allowing the main
application to react to events like foreground window changes without polling.
"""

import logging
import threading
import ctypes
from ctypes import wintypes, windll, byref

import win32process

from PyQt6.QtCore import QObject, pyqtSignal

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
WINEVENT_OUTOFCONTEXT = 0x0000


class WinEventHook(QObject, threading.Thread):
    """
    Listens for a specific WinEvent in a separate thread and emits a signal.
    Can be configured for global events or events from a specific window.
    """
    event_triggered = pyqtSignal(int)  # Emits the HWND of the window that triggered the event

    def __init__(self, event_to_watch: int, hwnd_to_watch: int = 0, parent=None):
        """
        Initializes the hook.

        Args:
            event_to_watch: The WinEvent constant to listen for (e.g., EVENT_SYSTEM_FOREGROUND).
            hwnd_to_watch: Optional HWND. If provided, the hook only listens to this window.
                           If 0 (default), it's a global hook.
        """
        super().__init__(parent)
        threading.Thread.__init__(self)
        self.daemon = True
        self._hook = None
        self._thread_id = None
        self._is_running = False
        
        self.event_to_watch = event_to_watch
        self.hwnd_to_watch = hwnd_to_watch

        # Store the callback in an instance variable so it's not garbage collected
        self.c_callback = WINEVENTPROC(self.callback)

    def run(self):
        """The main loop for the hook thread."""
        self._is_running = True
        
        process_id = 0
        thread_id = 0
        
        # If we are watching a specific window, get its process and thread ID
        if self.hwnd_to_watch != 0:
            try:
                thread_id, process_id = win32process.GetWindowThreadProcessId(self.hwnd_to_watch)
            except Exception as e:
                logger.error(f"Could not get process/thread for HWND {self.hwnd_to_watch}: {e}")
                self._is_running = False
                return

        self._hook = windll.user32.SetWinEventHook(
            self.event_to_watch,
            self.event_to_watch,
            0,
            self.c_callback,
            process_id,
            thread_id,
            WINEVENT_OUTOFCONTEXT
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
        """The C-compatible callback that receives events from Windows."""
        # Emit the HWND that triggered the event for context.
        self.event_triggered.emit(hwnd)

    def stop(self):
        """Stops the event listener thread."""
        if not self._is_running or self._thread_id is None:
            return
        self._is_running = False
        # Post a WM_QUIT message to the thread's message queue to unblock GetMessageW
        WM_QUIT = 0x0012
        windll.user32.PostThreadMessageW(self._thread_id, wintypes.UINT(WM_QUIT), 0, 0)