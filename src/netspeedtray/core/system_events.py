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

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication

from netspeedtray.constants import timeouts
from netspeedtray.utils.taskbar_utils import (
    get_taskbar_info, is_taskbar_obstructed, TaskbarInfo
)
from netspeedtray.utils.win_event_hook import (
    EVENT_SYSTEM_FOREGROUND,
    EVENT_SYSTEM_MOVESIZEEND,
    WinEventHook
)

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
        self._theme_signal_connected = False

    def start(self) -> None:
        """Starts all hooks and monitoring timers."""
        self.logger.debug("Starting SystemEventHandler...")
        self._setup_hooks()
        self._setup_timers()
        self._connect_color_scheme_signal()
        self.logger.debug("SystemEventHandler started.")

    def stop(self) -> None:
        """Stops all hooks and timers."""
        self.logger.debug("Stopping SystemEventHandler...")
        if self.foreground_hook:
            self.foreground_hook.stop()
        if self.movesize_hook:
            self.movesize_hook.stop()
        self._taskbar_validity_timer.stop()
        self._disconnect_color_scheme_signal()

    def _setup_hooks(self) -> None:
        """Initializes and starts WinEventHooks."""
        try:
            # 1. Foreground hook
            self.foreground_hook = WinEventHook(EVENT_SYSTEM_FOREGROUND, debounce_ms=250)
            self.foreground_hook.event_triggered.connect(self._on_foreground_change_immediate)
            self.foreground_hook.event_triggered_debounced.connect(self.foreground_app_changed)
            self.foreground_hook.start()
            
            # 2. Taskbar move/size hook — only when a real taskbar handle exists
            # (see _attach_movesize_hook for why a 0 handle must be skipped).
            self._attach_movesize_hook()

        except Exception as e:
            self.logger.error("Error setting up hooks: %s", e, exc_info=True)

    def _attach_movesize_hook(self) -> bool:
        """(Re)attach the MOVESIZEEND hook to the current taskbar handle.

        Skips attaching when the taskbar handle is 0 (e.g. explorer mid-restart at
        startup). A hwnd_to_watch of 0 installs a SYSTEM-WIDE move/size hook that
        fires for every window on the desktop, and it permanently disables the
        explorer-restart self-heal in _check_taskbar_validity (which is gated on
        hwnd_to_watch != 0). The validity timer retries until a real handle appears.
        Returns True if a hook was attached.
        """
        hwnd = get_taskbar_info().hwnd
        if not hwnd:
            self.logger.debug("Taskbar handle unavailable (0); deferring move/size hook.")
            return False
        # Replace any prior (possibly stale or system-wide) hook before re-attaching.
        if self.movesize_hook is not None:
            try:
                self.movesize_hook.stop()
            except Exception:
                pass
        self.movesize_hook = WinEventHook(EVENT_SYSTEM_MOVESIZEEND, hwnd_to_watch=hwnd)
        self.movesize_hook.event_triggered.connect(self._on_taskbar_moved_or_sized)
        self.movesize_hook.start()
        self.logger.debug("Move/size hook attached to taskbar HWND %s.", hwnd)
        return True

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
            else:
                # No move/size hook attached yet — we started while the taskbar handle
                # was 0 (explorer mid-restart). Keep trying until a real handle appears.
                self._attach_movesize_hook()

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

    def _connect_color_scheme_signal(self) -> None:
        """Connect to Qt's native colorSchemeChanged signal.

        Qt 6.5+ exposes a debounced, OS-agnostic signal for color-scheme
        changes. Using it here replaces the previous WM_SETTINGCHANGE
        native event filter, which fired on every system setting change
        (mouse, language, accessibility, etc.) — not just theme.
        """
        if self._theme_signal_connected:
            return
        app = QGuiApplication.instance()
        if app is None:
            self.logger.warning("No QGuiApplication instance — cannot connect theme signal.")
            return
        try:
            app.styleHints().colorSchemeChanged.connect(self._on_color_scheme_changed)
            self._theme_signal_connected = True
            self.logger.debug("Connected to QStyleHints.colorSchemeChanged.")
        except Exception as e:
            self.logger.warning("Failed to connect colorSchemeChanged: %s", e)

    def _disconnect_color_scheme_signal(self) -> None:
        if not self._theme_signal_connected:
            return
        app = QGuiApplication.instance()
        if app is None:
            return
        try:
            app.styleHints().colorSchemeChanged.disconnect(self._on_color_scheme_changed)
        except (TypeError, RuntimeError):
            pass
        self._theme_signal_connected = False

    def _on_color_scheme_changed(self, _scheme) -> None:
        """Re-emit Qt's signal as our existing theme_changed signal."""
        if self._is_paused:
            return
        self.logger.info("Color scheme changed; emitting theme_changed.")
        self.theme_changed.emit()
