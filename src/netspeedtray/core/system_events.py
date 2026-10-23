"""
System Event Handler Module.

This module centralizes low-level Windows system event handling, including
WinEventHooks for foreground window changes and object location changes (taskbar).
It abstracts the raw win32 API calls and exposes clean Qt signals to the application.
"""

import ctypes
import logging
import time
import win32api
import win32gui
from ctypes import wintypes
from typing import Optional

from PyQt6.QtCore import QAbstractNativeEventFilter, QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication

from netspeedtray.constants import timeouts
from netspeedtray.constants.shell import shell as shell_constants
from netspeedtray.utils.taskbar_utils import (
    get_taskbar_info, is_taskbar_obstructed, TaskbarInfo
)
from netspeedtray.utils.styles import read_system_uses_light_theme
from netspeedtray.utils.win_event_hook import (
    EVENT_OBJECT_REORDER,
    EVENT_SYSTEM_FOREGROUND,
    EVENT_SYSTEM_MOVESIZEEND,
    WinEventHook
)

_WM_SETTINGCHANGE = 0x001A
# lParam of the WM_SETTINGCHANGE Windows broadcasts when the Light/Dark (or accent) theme changes.
_IMMERSIVE_COLOR_SET = "ImmersiveColorSet"
# Taskbar icon alignment (Settings > Personalization > Taskbar > Taskbar behaviors): 0 left/top,
# 1 center (Win11's default when the value is absent).
_EXPLORER_ADVANCED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"


def read_taskbar_alignment() -> Optional[int]:
    """``TaskbarAl`` under HKCU's Explorer "Advanced" key: 0 = left/top, 1 = center (also when
    absent - the Win11 default), None when the key itself can't be read."""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _EXPLORER_ADVANCED_KEY)
        try:
            value, _ = winreg.QueryValueEx(key, "TaskbarAl")
            return int(value)
        except FileNotFoundError:
            return 1
        finally:
            winreg.CloseKey(key)
    except (OSError, ValueError, TypeError):
        return None


class _NativeMSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND), ("message", wintypes.UINT),
        ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_ssize_t),
        ("time", wintypes.DWORD), ("pt_x", wintypes.LONG), ("pt_y", wintypes.LONG),
    ]


class _ImmersiveColorSetFilter(QAbstractNativeEventFilter):
    """App-wide native filter: calls ``on_change`` for a WM_SETTINGCHANGE "ImmersiveColorSet" seen by
    any of the app's windows. Observe only - never consumes a message. (The pre-#62 filter compared
    the event type to the str "windows_generic_MSG"; PyQt6 passes bytes, so it never matched.)"""

    def __init__(self, on_change, on_any_setting=None) -> None:
        super().__init__()
        self._on_change = on_change
        # Called for EVERY WM_SETTINGCHANGE (the taskbar-alignment check: Explorer's broadcast for a
        # taskbar setting carries no documented lParam, so the callee compares one registry value).
        self._on_any_setting = on_any_setting

    def nativeEventFilter(self, eventType, message):
        try:
            if eventType in (b"windows_generic_MSG", "windows_generic_MSG") and message is not None:
                msg = _NativeMSG.from_address(int(message))
                if msg.message == _WM_SETTINGCHANGE:
                    if msg.lParam and ctypes.wstring_at(msg.lParam, len(_IMMERSIVE_COLOR_SET) + 1) == _IMMERSIVE_COLOR_SET + "\0":
                        self._on_change()
                    if self._on_any_setting is not None:
                        self._on_any_setting()
        except Exception:
            pass
        return False, 0


class SystemEventHandler(QObject):
    """
    Manages system-wide events and taskbar validity monitoring.
    
    Signals:
        foreground_app_changed (int): Emitted when the foreground window changes (debounced).
        immediate_hide_requested (void): Emitted when a fullscreen app is detected (immediate).
        taskbar_focused (void): Emitted immediately (non-debounced) when the taskbar/shell
            gains focus, so the widget can re-assert topmost before the activating taskbar
            visibly occludes it.
        taskbar_changed (void): Emitted when the taskbar moves or resizes.
        zorder_changed (void): Top-level Z-order may have changed (EVENT_OBJECT_REORDER on the
            desktop, or a foreground change) - the widget may have been buried under its
            taskbar. Coalesced: leading edge at once, then at most one per
            ZORDER_RECHECK_MIN_INTERVAL_MS, with a trailing emit so the last change is seen.
        taskbar_restarted (void): Emitted when explorer.exe restart is detected.
        events_paused (bool): Emitted when event monitoring is paused/resumed.
    """

    foreground_app_changed = pyqtSignal(int)
    immediate_hide_requested = pyqtSignal()
    taskbar_focused = pyqtSignal()
    taskbar_changed = pyqtSignal()
    zorder_changed = pyqtSignal()
    taskbar_restarted = pyqtSignal()
    events_paused = pyqtSignal(bool)
    theme_changed = pyqtSignal() # New signal
    # The taskbar's icon alignment (TaskbarAl) flipped - on a side bar that moves the Widgets button
    # and the app group, so the widget's side-bar placement must be decided again (rc.1 B3).
    taskbar_alignment_changed = pyqtSignal()

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.logger = logging.getLogger("NetSpeedTray.SystemEventHandler")
        
        # Hooks
        self.foreground_hook: Optional[WinEventHook] = None
        self.movesize_hook: Optional[WinEventHook] = None
        self.reorder_hook: Optional[WinEventHook] = None
        
        # Timers - connect the timeout slots ONCE here (not in _setup_timers), so the repeated
        # start()/stop() cycles on every Explorer restart can't accumulate duplicate connections that
        # multiply the per-tick work and the taskbar-restart fan-out (#11/#12).
        self._taskbar_validity_timer = QTimer(self)
        self._taskbar_validity_timer.timeout.connect(self._check_taskbar_validity)
        self._fullscreen_poll_timer = QTimer(self)
        self._fullscreen_poll_timer.timeout.connect(self._poll_fullscreen)
        # Trailing edge of the zorder_changed coalescer (see _note_zorder_change).
        self._zorder_trailing_timer = QTimer(self)
        self._zorder_trailing_timer.setSingleShot(True)
        self._zorder_trailing_timer.timeout.connect(self._emit_zorder_changed)
        self._last_zorder_emit: float = 0.0

        # State
        self._is_paused = False
        self._theme_signal_connected = False
        self._was_fullscreen = False  # last polled fullscreen state (edge detection)
        self._last_shell_light: Optional[int] = None  # last seen SystemUsesLightTheme (edge detection)
        self._theme_filter: Optional[_ImmersiveColorSetFilter] = None
        self._last_taskbar_alignment: Optional[int] = None  # last seen TaskbarAl (edge detection)

    def start(self) -> None:
        """Starts all hooks and monitoring timers."""
        self.logger.debug("Starting SystemEventHandler...")
        self._setup_hooks()
        self._setup_timers()
        self._connect_color_scheme_signal()
        if self._last_shell_light is None:   # the baseline survives Explorer-restart stop()/start()
            self._last_shell_light = read_system_uses_light_theme()
        if self._last_taskbar_alignment is None:
            self._last_taskbar_alignment = read_taskbar_alignment()
        self._install_theme_filter()
        self.logger.debug("SystemEventHandler started.")

    def stop(self) -> None:
        """Stops all hooks and timers."""
        self.logger.debug("Stopping SystemEventHandler...")
        if self.foreground_hook:
            self.foreground_hook.stop()
        if self.movesize_hook:
            self.movesize_hook.stop()
        if self.reorder_hook:
            self.reorder_hook.stop()
        self._zorder_trailing_timer.stop()
        self._taskbar_validity_timer.stop()
        self._fullscreen_poll_timer.stop()
        self._disconnect_color_scheme_signal()
        self._remove_theme_filter()

    def _setup_hooks(self) -> None:
        """Initializes and starts WinEventHooks."""
        try:
            # 1. Foreground hook
            self.foreground_hook = WinEventHook(EVENT_SYSTEM_FOREGROUND, debounce_ms=250)
            self.foreground_hook.event_triggered.connect(self._on_foreground_change_immediate)
            self.foreground_hook.event_triggered_debounced.connect(self.foreground_app_changed)
            self.foreground_hook.start()
            
            # 2. Taskbar move/size hook - only when a real taskbar handle exists
            # (see _attach_movesize_hook for why a 0 handle must be skipped).
            self._attach_movesize_hook()

            # 3. Top-level Z-order hook (#314, #238). System-wide on purpose: pasharm traced
            # the burial to explorer firing EVENT_OBJECT_REORDER on the desktop window, and
            # not from the taskbar's thread, so a per-thread hook misses it. Only desktop-
            # sourced events cross into Qt (filtered on the hook thread), and the widget's
            # handler is a short Z-order walk that re-seats only when actually buried.
            self._attach_reorder_hook()

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

    def _attach_reorder_hook(self) -> None:
        """(Re)create the desktop EVENT_OBJECT_REORDER hook. A failure only costs speed: the
        widget's 1 s safety-net tick still catches a burial."""
        if self.reorder_hook is not None:
            try:
                self.reorder_hook.stop()
            except Exception:
                pass
            self.reorder_hook = None
        try:
            desktop = int(win32gui.GetDesktopWindow() or 0)
            if not desktop:
                return
            # skip_own_process: our own re-seat echoes back as a reorder; dropping it saves a
            # pointless check (anything our own UI does is still caught by the 1 s tick).
            self.reorder_hook = WinEventHook(EVENT_OBJECT_REORDER, source_hwnd_filter=desktop,
                                             skip_own_process=True)
            self.reorder_hook.event_triggered.connect(self._on_zorder_event)
            self.reorder_hook.start()
        except Exception as e:
            self.logger.warning("Z-order hook unavailable (%s); burial recovery falls back to the 1 s check.", e)
            self.reorder_hook = None

    def _on_zorder_event(self, _hwnd: int = 0) -> None:
        """Raw EVENT_OBJECT_REORDER on the desktop."""
        self._note_zorder_change()

    def _note_zorder_change(self) -> None:
        """
        Coalesce Z-order change notifications into zorder_changed. The first change after a
        quiet spell emits at once (a burial is undone within the same frame or so); changes
        inside the next ZORDER_RECHECK_MIN_INTERVAL_MS collapse into one trailing emit at the
        end of the window, so a burst - a window animating, a flyout opening - costs at most
        one check a frame and the final Z-order is always checked. The window is one frame
        (16 ms) because a Win10 maximize / restore buries the widget 10-20 ms into its burst,
        after the leading-edge check (PR #424, pasharm).
        """
        if self._is_paused:
            return
        interval_ms = timeouts.ZORDER_RECHECK_MIN_INTERVAL_MS
        elapsed_ms = (time.monotonic() - self._last_zorder_emit) * 1000.0
        if elapsed_ms >= interval_ms:
            self._zorder_trailing_timer.stop()
            self._emit_zorder_changed()
        elif not self._zorder_trailing_timer.isActive():
            self._zorder_trailing_timer.start(max(1, int(interval_ms - elapsed_ms)))

    def _emit_zorder_changed(self) -> None:
        self._last_zorder_emit = time.monotonic()
        if not self._is_paused:
            self.zorder_changed.emit()

    def _setup_timers(self) -> None:
        """Start the taskbar-validity + fast fullscreen-hide timers. The timeout slots are connected
        once in __init__, so this only (re)starts them - repeated start()/stop() can't accumulate
        duplicate connections (#11/#12). (Fullscreen poll catches an app going fullscreen while already
        focused, when no foreground event fires, so the widget hides promptly.)"""
        self._taskbar_validity_timer.start(timeouts.TASKBAR_VALIDITY_CHECK_INTERVAL_MS)
        self._fullscreen_poll_timer.start(timeouts.FULLSCREEN_POLL_INTERVAL_MS)

    def _poll_fullscreen(self) -> None:
        """
        Fast detection of fullscreen ENTER (hide) and EXIT (show) for the case where an
        app toggles fullscreen while already focused -- which fires no foreground event,
        so without this the transition waits for the 1s state-watcher tick. Acts only on
        a state CHANGE (edge), so it is quiet during steady state.
        """
        if self._is_paused:
            return
        try:
            hwnd = win32gui.GetForegroundWindow()
            is_fs = self._is_fullscreen_obstruction(hwnd)
            if is_fs == self._was_fullscreen:
                return
            self._was_fullscreen = is_fs
            if is_fs:
                self.immediate_hide_requested.emit()
            else:
                # Fullscreen ended with no foreground event: re-evaluate visibility now so
                # the widget returns promptly instead of waiting for the 1s state-watcher.
                self.foreground_app_changed.emit(hwnd)
        except Exception as e:
            self.logger.error(f"Error in fullscreen poll: {e}", exc_info=True)

    def _preferred_monitor(self) -> Optional[str]:
        """
        The user's preferred_monitor (a QScreen.name()) read live from the parent
        widget's config, so fullscreen-obstruction checks the widget's OWN taskbar on
        multi-monitor setups instead of always the primary. None when unset / no parent.
        """
        cfg = getattr(self.parent(), "config", None)
        if cfg is not None and hasattr(cfg, "get"):
            return cfg.get("preferred_monitor")
        return None

    def _is_fullscreen_obstruction(self, hwnd: int) -> bool:
        """
        True if `hwnd` is a true-fullscreen window that obstructs our taskbar. Shared by
        the foreground hook and the fast poll.

        Cheap by design: the window_rect == monitor_rect test short-circuits before any
        taskbar query, so the common (non-fullscreen) case costs only a couple of
        GetWindowRect/MonitorFromWindow calls.
        """
        try:
            if not hwnd or not win32gui.IsWindow(hwnd):
                return False
            window_rect = win32gui.GetWindowRect(hwnd)
            monitor_info = win32api.GetMonitorInfo(win32api.MonitorFromWindow(hwnd))
            if window_rect != monitor_info.get('Monitor') and \
                    win32gui.GetClassName(hwnd) not in shell_constants.shell.RDP_CLIENT_WINDOW_CLASSES:
                # Not fullscreen on its monitor - no taskbar query needed. The exception is a
                # multi-monitor Remote Desktop session (#132): one window spanning every monitor,
                # judged against our taskbar's monitor by is_taskbar_obstructed below.
                return False
            # Check obstruction against the widget's OWN taskbar (preferred monitor), not
            # always the primary - otherwise a fullscreen app on another monitor would
            # wrongly hide the widget.
            taskbar_info = get_taskbar_info(preferred_screen_name=self._preferred_monitor())
            return bool(is_taskbar_obstructed(taskbar_info, hwnd))
        except (win32gui.error, AttributeError):
            return False
        except Exception as e:
            self.logger.error(f"Error in fullscreen check: {e}", exc_info=True)
            return False

    def _on_foreground_change_immediate(self, hwnd: int) -> None:
        """
        Handles the raw (non-debounced) foreground event:

        - When the taskbar/shell itself gains focus (e.g. the user clicks the
          taskbar), immediately re-assert the widget's topmost status. The widget
          is a separate top-level window sitting over the taskbar; when the taskbar
          activates it can land above the widget in Z-order, and the ~250ms-debounced
          _execute_refresh recovers it only after a visible gap (the "hide and
          return"). Re-asserting here, in the same event, closes that gap. This
          restores a410aae's _handle_taskbar_focus, dropped in a later refactor.
        - When an unambiguous fullscreen window appears, request an emergency hide.
        """
        if self._is_paused:
            return

        try:
            if not hwnd or not win32gui.IsWindow(hwnd):
                return

            # Any activation can reorder the topmost band (and #238's grouped-preview hover
            # raises the taskbar): let the widget check it is still above its taskbar.
            self._note_zorder_change()

            if win32gui.GetClassName(hwnd) in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
                self.taskbar_focused.emit()
                return

            if self._is_fullscreen_obstruction(hwnd):
                self.immediate_hide_requested.emit()

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
                # No move/size hook attached yet - we started while the taskbar handle
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
        """Resumes event processing. A theme flip while paused fires now."""
        self._is_paused = False
        self.events_paused.emit(False)
        self.check_shell_theme()
        self.check_taskbar_alignment()

    def is_fullscreen_active(self) -> bool:
        """True when the most recent poll saw a fullscreen obstruction. Lets the widget suppress
        hover affordances (the usage card) over a game/video even when the user opted to keep the
        widget itself visible (keep_visible_fullscreen)."""
        return self._was_fullscreen

    def _connect_color_scheme_signal(self) -> None:
        """Connect to Qt's native colorSchemeChanged signal.

        Qt 6.5+ exposes a debounced, OS-agnostic signal for color-scheme
        changes. Using it here replaces the previous WM_SETTINGCHANGE
        native event filter, which fired on every system setting change
        (mouse, language, accessibility, etc.) - not just theme.
        """
        if self._theme_signal_connected:
            return
        app = QGuiApplication.instance()
        if app is None:
            self.logger.warning("No QGuiApplication instance - cannot connect theme signal.")
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

    def _install_theme_filter(self) -> None:
        if self._theme_filter is not None:
            return
        app = QGuiApplication.instance()
        if app is None:
            return
        try:
            self._theme_filter = _ImmersiveColorSetFilter(self.check_shell_theme,
                                                          self.check_taskbar_alignment)
            app.installNativeEventFilter(self._theme_filter)
        except Exception as e:
            self._theme_filter = None
            self.logger.warning("Failed to install the theme-change filter: %s", e)

    def _remove_theme_filter(self) -> None:
        if self._theme_filter is None:
            return
        app = QGuiApplication.instance()
        try:
            if app is not None:
                app.removeNativeEventFilter(self._theme_filter)
        except Exception:
            pass
        self._theme_filter = None

    def check_shell_theme(self) -> None:
        """Emit theme_changed when the WINDOWS-mode theme (SystemUsesLightTheme) has flipped.

        Qt's colorSchemeChanged follows the APP mode (AppsUseLightTheme), but the widget's automatic
        text colour follows the Windows mode the taskbar is painted in. In Win11's "Custom" mode a
        user can flip only the Windows mode, which emits nothing from Qt - the widget kept white text
        on a now-light taskbar until a restart. Windows broadcasts WM_SETTINGCHANGE "ImmersiveColorSet"
        for either flip; the app-wide native filter calls this then, and only then (no polling).

        ONE registry read; emits only when the value differs from the last one seen, so one flip is
        one theme_changed however many windows got the broadcast and whichever path (this or Qt's
        signal) saw it first. While paused the last value is left alone; resume() re-checks.
        """
        if self._is_paused:
            return
        value = read_system_uses_light_theme()
        if value is None or value == self._last_shell_light:
            return
        first = self._last_shell_light is None
        self._last_shell_light = value
        if first:
            return
        self.logger.info("Windows-mode theme changed (SystemUsesLightTheme=%s); emitting theme_changed.", value)
        self.theme_changed.emit()

    def check_taskbar_alignment(self) -> None:
        """Emit taskbar_alignment_changed when TaskbarAl differs from the last value seen. Called for
        every WM_SETTINGCHANGE (one registry read; they are rare), deduped like check_shell_theme so
        the broadcast reaching several of our windows emits once."""
        if self._is_paused:
            return
        value = read_taskbar_alignment()
        if value is None or value == self._last_taskbar_alignment:
            return
        first = self._last_taskbar_alignment is None
        self._last_taskbar_alignment = value
        if first:
            return
        self.logger.info("Taskbar icon alignment changed (TaskbarAl=%s); emitting taskbar_alignment_changed.",
                         value)
        self.taskbar_alignment_changed.emit()

    def _on_color_scheme_changed(self, _scheme) -> None:
        """Qt saw a colour-scheme change: re-check the Windows-mode theme the widget follows (the
        same deduped check as the native path, so a flip both see emits theme_changed once)."""
        if self._is_paused:
            return
        self.logger.info("Color scheme changed; re-checking the Windows-mode theme.")
        self.check_shell_theme()
