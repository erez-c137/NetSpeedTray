"""
Windows System Event Hook Utility.

Provides a threaded listener for system-wide WinEvents, allowing the main
application to react to events like foreground window changes without polling.
"""

import logging
import threading
import ctypes
from ctypes import wintypes, windll, byref

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
EVENT_SYSTEM_FOREGROUND = 0x0003
WINEVENT_OUTOFCONTEXT = 0x0000

class WinEventHook(QObject, threading.Thread):
    """
    Listens for foreground window changes in a separate thread and emits a signal.
    """
    foreground_window_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        threading.Thread.__init__(self)
        self.daemon = True  # Ensure thread exits when main program does
        self._hook = None
        self._thread_id = None
        self._is_running = False

    def run(self):
        """The main loop for the hook thread."""
        self._is_running = True
        
        # We must store the callback in an instance variable so it's not garbage collected
        self.c_callback = WINEVENTPROC(self.callback)
        
        # Set the hook. This must be done from the thread that will process the events.
        self._hook = windll.user32.SetWinEventHook(
            EVENT_SYSTEM_FOREGROUND,
            EVENT_SYSTEM_FOREGROUND,
            0,
            self.c_callback,
            0,
            0,
            WINEVENT_OUTOFCONTEXT
        )
        
        if self._hook == 0:
            logger.error("SetWinEventHook failed.")
            self._is_running = False
            return

        # Store the Win32 thread ID for posting the quit message
        self._thread_id = windll.kernel32.GetCurrentThreadId()
        logger.info("WinEventHook started successfully in thread %d.", self._thread_id)
        
        # This is the standard Windows message loop.
        msg = wintypes.MSG()
        while self._is_running and windll.user32.GetMessageW(byref(msg), 0, 0, 0) != 0:
            windll.user32.TranslateMessage(byref(msg))
            windll.user32.DispatchMessageW(byref(msg))

        windll.user32.UnhookWinEvent(self._hook)
        logger.info("WinEventHook stopped and unhooked.")

    def callback(self, hWinEventHook, event, hwnd, idObject, idChild, dwEventThread, dwmsEventTime):
        """The C-compatible callback that receives events from Windows."""
        # This function runs in the hook's thread, so we emit a signal
        # to communicate safely with the main GUI thread.
        self.foreground_window_changed.emit()

    def stop(self):
        """Stops the event listener thread."""
        if not self._is_running or self._thread_id is None:
            return
        self._is_running = False
        # Post a WM_QUIT message to the thread's message queue to unblock GetMessageW
        windll.user32.PostThreadMessageW(self._thread_id, wintypes.UINT(0x0012), 0, 0)