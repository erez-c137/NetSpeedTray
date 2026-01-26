# In src/netspeedtray/utils/visibility_manager.py

import logging
from typing import TYPE_CHECKING, Set

import win32gui
from PyQt6.QtCore import QObject, pyqtSignal, QTimer

from netspeedtray.utils.taskbar_utils import (
    get_process_name_from_hwnd, get_taskbar_info, is_taskbar_obstructed,
    is_taskbar_visible
)
from netspeedtray.utils.win_event_hook import (
    EVENT_SYSTEM_FOREGROUND,
    EVENT_SYSTEM_MOVESIZEEND,
    WinEventHook,
)

if TYPE_CHECKING:
    from netspeedtray.views.widget import NetworkSpeedWidget

logger = logging.getLogger("NetSpeedTray.VisibilityManager")

WATCHED_PROCESSES: Set[str] = {
    "vlc.exe", "mpc-hc64.exe", "chrome.exe", "firefox.exe",
    "msedge.exe", "brave.exe", "opera.exe"
}
SLOW_INTERVAL = 1000
FAST_INTERVAL = 200


class VisibilityManager(QObject):
    """
    Manages widget visibility using a single, stateful refresh mechanism
    driven by both system events and a dynamic-interval timer.
    """
    visibility_should_change = pyqtSignal(bool)


    def __init__(self, widget: "NetworkSpeedWidget", parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._widget = widget
        self._foreground_hook = WinEventHook(EVENT_SYSTEM_FOREGROUND)
        self._movesize_hook = WinEventHook(EVENT_SYSTEM_MOVESIZEEND)
        self._watcher_timer = QTimer(self)
        self._last_known_visibility: bool = True

        self._connect_signals()
        self.start()
        logger.debug("VisibilityManager initialized with dynamic timer logic.")


    def _connect_signals(self) -> None:
        self._foreground_hook.event_triggered.connect(self._handle_system_event)
        self._movesize_hook.event_triggered.connect(self._handle_system_event)
        self._watcher_timer.timeout.connect(self.refresh_visibility)


    def _handle_system_event(self, hwnd: int) -> None:
        process_name = get_process_name_from_hwnd(hwnd)
        target_interval = FAST_INTERVAL if process_name and process_name in WATCHED_PROCESSES else SLOW_INTERVAL

        if self._watcher_timer.interval() != target_interval:
            self._watcher_timer.setInterval(target_interval)
            logger.debug(f"Focus changed. Switched to {'FAST' if target_interval == FAST_INTERVAL else 'SLOW'} polling.")
        
        self.refresh_visibility()


    def refresh_visibility(self) -> None:
        """
        The single, authoritative function that determines if the widget
        should be visible. It is "flicker-proof" as it only emits a signal
        if the required state has actually changed.
        """
        try:
            taskbar_info = get_taskbar_info()
            
            # Get the current foreground window handle to check against.
            hwnd = win32gui.GetForegroundWindow()
            
            # Call is_taskbar_obstructed with BOTH required arguments.
            should_be_visible = is_taskbar_visible(taskbar_info) and not is_taskbar_obstructed(taskbar_info, hwnd)

            # Only emit a signal if the state is different from what we last set.
            if should_be_visible != self._last_known_visibility:
                self._last_known_visibility = should_be_visible
                self.visibility_should_change.emit(should_be_visible)
        except Exception as e:
            logger.error(f"Error in refresh_visibility: {e}", exc_info=True)


    def start(self) -> None:
        self._watcher_timer.setInterval(SLOW_INTERVAL)
        self._watcher_timer.start()
        self._foreground_hook.start()
        self._movesize_hook.start()


    def stop(self) -> None:
        self._watcher_timer.stop()
        self._foreground_hook.stop()
        self._movesize_hook.stop()
        logger.debug("VisibilityManager stopped.")