"""
System Event Handler Module.

This module centralizes low-level Windows system event handling, including
WinEventHooks for foreground window changes and object location changes (taskbar).
It abstracts the raw win32 API calls and exposes clean Qt signals to the application.
"""

import logging
import time
import win32api
import win32gui
from typing import Optional
import ctypes # Needed for MSG structure

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, QAbstractNativeEventFilter
from PyQt6.QtWidgets import QApplication

from netspeedtray.constants import timeouts
from netspeedtray.utils.taskbar_utils import (
    get_taskbar_info, is_taskbar_obstructed, TaskbarInfo
)
from netspeedtray.utils.win_event_hook import (
    EVENT_SYSTEM_FOREGROUND,
    EVENT_SYSTEM_MOVESIZEEND,
    WinEventHook
)

class ThemeChangeFilter(QAbstractNativeEventFilter):
    """
    Native event filter to capture WM_SETTINGCHANGE messages.
    """
    WM_SETTINGCHANGE = 0x001A

    def __init__(self, handler):
        super().__init__()
        self.handler = handler

    def nativeEventFilter(self, eventType, message):
        if eventType == "windows_generic_MSG":
            msg = ctypes.wintypes.MSG.from_address(int(message))
            if msg.message == self.WM_SETTINGCHANGE:
                # Emit signal directly from the filter
                self.handler.theme_changed.emit()
        return False, 0

class SystemEventHandler(QObject):
    """
    Manages system-wide events and taskbar validity monitoring.
    
    Signals:
        foreground_app_changed (int): Emitted when the foreground window changes (debounced).
        immediate_hide_requested (void): Emitted when a fullscreen app is detected (immediate).
        taskbar_changed (void): Emitted when the taskbar moves or resizes.
        taskbar_restarted (void): Emitted when explorer.exe restart is detected.
        events_paused (bool): Emitted when event monitoring is paused/resumed.
    """
    
    foreground_app_changed = pyqtSignal(int)
    immediate_hide_requested = pyqtSignal()
    taskbar_changed = pyqtSignal()
    taskbar_restarted = pyqtSignal()
    events_paused = pyqtSignal(bool)
    theme_changed = pyqtSignal() # New signal

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.logger = logging.getLogger("NetSpeedTray.SystemEventHandler")
        
        # Hooks
        self.foreground_hook: Optional[WinEventHook] = None
        self.movesize_hook: Optional[WinEventHook] = None
        
        # Timers
        self._taskbar_validity_timer = QTimer(self)
        
        # State
        self._last_immediate_hide_time: float = 0.0
        self._is_paused = False
        
        # Native Filter
        self.theme_filter: Optional[ThemeChangeFilter] = None

    def start(self) -> None:
        """Starts all hooks and monitoring timers."""
        self.logger.debug("Starting SystemEventHandler...")
        self._setup_hooks()
        self._setup_timers()
        self._install_native_filter()
        self.logger.debug("SystemEventHandler started.")

    def stop(self) -> None:
        """Stops all hooks and timers."""
        self.logger.debug("Stopping SystemEventHandler...")
        if self.foreground_hook:
            self.foreground_hook.stop()
        if self.movesize_hook:
            self.movesize_hook.stop()
        self._taskbar_validity_timer.stop()
        self._remove_native_filter()

    def _setup_hooks(self) -> None:
        """Initializes and starts WinEventHooks."""
        try:
            # 1. Foreground hook
            self.foreground_hook = WinEventHook(EVENT_SYSTEM_FOREGROUND, debounce_ms=250)
            self.foreground_hook.event_triggered.connect(self._on_foreground_change_immediate)
            self.foreground_hook.event_triggered_debounced.connect(self.foreground_app_changed)
            self.foreground_hook.start()
            
            # 2. Taskbar move/size hook
            taskbar_info = get_taskbar_info()
            self.movesize_hook = WinEventHook(EVENT_SYSTEM_MOVESIZEEND, hwnd_to_watch=taskbar_info.hwnd)
            self.movesize_hook.event_triggered.connect(self._on_taskbar_moved_or_sized)
            self.movesize_hook.start()
            
        except Exception as e:
            self.logger.error("Error setting up hooks: %s", e, exc_info=True)

    def _setup_timers(self) -> None:
        """Sets up the taskbar validity timer."""
        self._taskbar_validity_timer.timeout.connect(self._check_taskbar_validity)
        self._taskbar_validity_timer.start(timeouts.TASKBAR_VALIDITY_CHECK_INTERVAL_MS)

    def _on_foreground_change_immediate(self, hwnd: int) -> None:
        """
        Handles the raw event for an "emergency hide" on unambiguous fullscreen windows.
        """
        if self._is_paused:
            return

        try:
            if not hwnd or not win32gui.IsWindow(hwnd):
                return

            taskbar_info = get_taskbar_info()
            if is_taskbar_obstructed(taskbar_info, hwnd):
                window_rect = win32gui.GetWindowRect(hwnd)
                monitor_info = win32api.GetMonitorInfo(win32api.MonitorFromWindow(hwnd))
                monitor_rect = monitor_info.get('Monitor')
                
                if window_rect == monitor_rect:
                    self.logger.debug("Immediate check: Fullscreen detected (HWND: %s). Requesting hide.", hwnd)
                    self.immediate_hide_requested.emit()
                    self._last_immediate_hide_time = time.monotonic()

        except (win32gui.error, AttributeError):
            pass 
        except Exception as e:
            self.logger.error(f"Error in immediate foreground handler: {e}", exc_info=True)

    def _check_taskbar_validity(self) -> None:
        """
        Checks if the taskbar handle is still valid. If not, emits taskbar_restarted.
        """
        if self._is_paused:
            return

        try:
            taskbar_info = get_taskbar_info()
            taskbar_hwnd = taskbar_info.hwnd
            
            # Note: We rely on taskbar_info.hwnd for the check. If get_taskbar_info returns
            # a new HWND, then we might not detect invalidity of the OLD one unless we track it.
            # However, the original logic checked the stored `position_manager.taskbar_info.hwnd`.
            # Here, we will trust get_taskbar_info() but we should probably re-init hooks if 
            # the HWND changes.
            
            # A better check for 'explorer restarted' is if our hook's target HWND is no longer valid.
            if self.movesize_hook and self.movesize_hook.hwnd_to_watch != 0:
                if not win32gui.IsWindow(self.movesize_hook.hwnd_to_watch):
                     self.logger.warning("Watched taskbar handle invalid. Explorer likely restarted.")
                     self.taskbar_restarted.emit()
                     # Restart hooks to attach to the new taskbar
                     self.stop() 
                     self.start()

        except Exception as e:
            self.logger.error("Error checking taskbar validity: %s", e, exc_info=True)

    def _on_taskbar_moved_or_sized(self, hwnd: int) -> None:
        """Fires when the taskbar is moved or resized."""
        if not self._is_paused:
            self.taskbar_changed.emit()

    def pause(self) -> None:
        """Pauses event processing."""
        self._is_paused = True
        self.events_paused.emit(True)

    def resume(self) -> None:
        """Resumes event processing."""
        self._is_paused = False
        self.events_paused.emit(False)

    def _install_native_filter(self) -> None:
        """Installs the native event filter on the QApplication."""
        if not self.theme_filter:
            self.theme_filter = ThemeChangeFilter(self)
            app = QApplication.instance()
            if app:
                app.installNativeEventFilter(self.theme_filter)
                self.logger.debug("Native event filter installed for theme changes.")

    def _remove_native_filter(self) -> None:
        """Removes the native event filter."""
        if self.theme_filter:
            app = QApplication.instance()
            if app:
                app.removeNativeEventFilter(self.theme_filter)
            self.theme_filter = None
            self.logger.debug("Native event filter removed.")
